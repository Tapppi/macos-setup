#!/usr/bin/env python3
"""
test_assemble.py — the test matrix for assemble.py's derived fields.
Usage: python3 test_assemble.py [-v]

Stdlib `unittest` only (no pytest, no fixtures directory, no network) so it
runs on the same bare python3 assemble.py itself targets.

Every fixture here is written in the **item model** (`references/item-schema.md`):
one `items[]` array carrying tags, one severity, and two optional blocks
(`change` upstream, `local` about this setup). The four parallel arrays
`headliners[]`/`relevancy[]`/`context[]`/`security.notable[]` are gone, and so
are the tests that only existed to reconcile them with each other.

Groups, in the order references/assembly.md documents the computations:

1. Version classification — the worked-example table that motivated
   compute_version_delta(). Every row is a real current→latest pair seen in a
   live run; the point of writing them down is that the next person changing
   the classifier does not have to re-derive "is `7.99 → 7.991` a minor?" from
   scratch. Unchanged by the item model: a version is a version.
2. Semantic classification — minimal Tool fixtures run through the whole
   pipeline (validator → build_tool → finalize_tool), asserting the
   security → risk_level → review_bucket → pre_accept chain end to end rather
   than each function in isolation, since the bugs live in their interaction.
3. The CVE rollup — the id scan's sources under the item model, the claim
   pattern's two real-world false positives, and the structural
   `security.cve_id`/`anchor` path that replaced `security.cve_severities[]`.
4. Shape drift — research is agent-written free-form JSON, so every array it
   supplies is fed back in the wrong shape sooner or later. One report is
   assembled from ~22 files covering ~77 tools, so the contract is that a
   drifted file costs one reported-about tool, never the whole run.
5. Report-level invariants — a synthetic session dir assembled through main(),
   so the ordering constraints (finalize_tool before the id-uniqueness pass,
   build_highlights after it) are exercised, not just asserted in prose.
6. The severity rollup — `sum(severity_counts) == cve_count` holds because the
   rollup iterates the ids rather than the ratings.
7. The assembly ↔ page contract — the tag→group map and the CVE ordering rank
   exist on both sides, and the page must not read a legacy array. These read
   `assets/report-template.html` because prose agreement between the two sides
   has already drifted once without a single test failing.
8. Degradation — per file, per entry, per tool. Nothing malformed may cost the
   run.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble  # noqa: E402
import items as model  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets",
	"report-template.html")


# ── 1. Version classification (references/assembly.md §Version Delta) ────────
# (label, current, latest, delta, scheme, note)
VERSION_MATRIX = [
	("brew revision stripped before compare", "6.0.1_1", "6.1.0", "minor", "semver", "index 1"),
	("ffmpeg major", "8.1.2_1", "9.0.1", "major", "semver", "index 0"),
	("homebrew rebuild only", "13.55", "13.55_1", "revision", "semver", "packaging revision only"),
	("revision to revision", "0.41.0_6", "0.41.0_8", "revision", "semver", "packaging revision only"),
	("nmap decimal scheme", "7.99", "7.991", "minor", "semver", "index 1"),
	("tmux letter release", "3.7b", "3.7c", "patch", "semver", "suffix change at index 1"),
	("short side padded with 0", "2.46.1", "2.47", "minor", "semver", "index 1"),
	("gnu parallel date version", "20260622", "20260722", "minor", "date", "date-versioned release"),
	("v-prefixed date version", "v20260622", "v20260722", "minor", "date", "date-versioned release"),
	("yt-dlp calver month bump", "2026.7.4", "2026.8.19", "minor", "calver", "calver index 1"),
	("cask tuple, left half decides", "1.22209.3,babe1157", "1.34493.1,255293a4", "minor", "semver", "index 1"),
	("datagrip calver bugfix", "2026.2,262.8665.272", "2026.2.4,262.10315.24", "patch", "calver", "calver index 2"),
	("openssh portable suffix", "10.4p1", "10.5p1", "minor", "semver", "index 1"),
	("poppler 2-digit year is not calver", "26.07.0", "26.08.0", "minor", "semver", "index 1"),
	("chrome rolling major", "150.0.7871.129", "151.0.7922.174", "major", "semver", "index 0"),
	("libpq postgres minor", "18.4", "18.6", "minor", "semver", "index 1"),
	("kdiff3 patch", "1.12.4", "1.12.6", "patch", "semver", "index 2"),
	("yq patch", "4.53.3", "4.53.6", "patch", "semver", "index 2"),
	("bun minor", "1.3.14", "1.4.0", "minor", "semver", "index 1"),
	("imagemagick patch level", "7.1.2-27", "7.1.2-29", "patch", "semver", "index 3"),
	("teams build numbers", "26163.407.4839.8659", "26213.1006.5011.1671", "unknown", "opaque", "build-number scheme, not interpretable"),
	("pre-release to final", "1.0.0-rc1", "1.0.0", "patch", "semver", "suffix change at index 3"),
	("cursor cask tuple", "3.12.17,0fb76205", "3.17.8,2fdd31c9", "minor", "semver", "index 1"),
	("cask build-half-only bump", "1.2.3", "1.2.3,999", "revision", "semver", "packaging revision only"),
	("missing versions", None, None, "unknown", "none", "missing version"),
	("codex 0.x minor is major", "0.144.6", "0.149.0", "major", "semver", "index 1 (0.x rule)"),
	("uv 0.x minor is major", "0.11.29", "0.12.5", "major", "semver", "index 1 (0.x rule)"),
	("claudebar 0.x patch rounds up", "0.4.73", "0.4.81", "minor", "semver", "index 2 (0.x rule)"),
	("fzf 0.x patch rounds up", "0.74.1", "0.74.3", "minor", "semver", "index 2 (0.x rule)"),
]


class VersionDeltaTests(unittest.TestCase):
	def test_matrix(self):
		for label, cur, lat, delta, scheme, note in VERSION_MATRIX:
			with self.subTest(label, current=cur, latest=lat):
				self.assertEqual(assemble.compute_version_delta(cur, lat, "brew"), (delta, scheme, note))

	def test_non_version_sources_short_circuit(self):
		# A brew-health or skill-drift finding has no versions at all, and a
		# macos candidate's current_version is the running OS version rather than
		# that update's (references/schemas.md §1.3) — a delta from any of them
		# would be fiction.
		for source in ("brew-health", "skill-drift", "macos"):
			with self.subTest(source=source):
				self.assertEqual(
					assemble.compute_version_delta("15.6", "15.7", source),
					("unknown", "none", "no version delta for this source"))

	def test_no_numeric_component(self):
		self.assertEqual(assemble.compute_version_delta("stable", "latest", "brew"),
			("unknown", "none", "no numeric component"))

	def test_equal_versions(self):
		with contextlib.redirect_stderr(io.StringIO()):
			delta, scheme, note = assemble.compute_version_delta("1.2.3", "1.2.3", "brew")
		self.assertEqual((delta, note), ("unknown", "versions compare equal"))
		self.assertEqual(scheme, "semver")

	def test_leading_v_is_stripped(self):
		self.assertEqual(assemble.compute_version_delta("v1.2.3", "1.3.0", "brew")[0], "minor")

	def test_ambiguity_never_rounds_down_to_patch(self):
		# The dangerous failure is a false "patch" — it reads as "nothing to
		# think about" and feeds the auto-approve path. Nothing unparseable may
		# land there.
		for cur, lat in (("26163.407", "26213.1006"), (None, "1.2.3"), ("", "1.2.3"), ("x", "y")):
			with self.subTest(current=cur, latest=lat):
				self.assertNotEqual(assemble.compute_version_delta(cur, lat, "brew")[0], "patch")


class VersionHelperTests(unittest.TestCase):
	def test_split_cask_tuple_splits_on_first_comma_only(self):
		self.assertEqual(assemble.split_cask_tuple("1.2,3,4"), ("1.2", "3,4"))
		self.assertEqual(assemble.split_cask_tuple("1.2"), ("1.2", None))

	def test_split_brew_revision(self):
		self.assertEqual(assemble.split_brew_revision("13.55_1"), ("13.55", "1"))
		self.assertEqual(assemble.split_brew_revision("13.55"), ("13.55", None))
		self.assertEqual(assemble.split_brew_revision("1.2_beta"), ("1.2_beta", None))

	def test_parse_version_components(self):
		self.assertEqual(assemble.parse_version_components("10.4p1"), [(10, ""), (4, "p1")])
		self.assertEqual(assemble.parse_version_components("3.7c"), [(3, ""), (7, "c")])
		self.assertEqual(assemble.parse_version_components("v2.0"), [(2, ""), (0, "")])
		self.assertEqual(assemble.parse_version_components("rc1"), [])

	def test_looks_like_date8(self):
		self.assertTrue(assemble.looks_like_date8("20260622"))
		self.assertFalse(assemble.looks_like_date8("20261322"))  # month 13
		self.assertFalse(assemble.looks_like_date8("18900101"))  # year < 1990
		self.assertFalse(assemble.looks_like_date8("2026062"))

	def test_first_difference(self):
		a = assemble.parse_version_components("1.2.3")
		self.assertEqual(assemble.first_difference(a, assemble.parse_version_components("1.2.3")), (None, None))
		self.assertEqual(assemble.first_difference(a, assemble.parse_version_components("1.3.3")), (1, "numeric"))
		self.assertEqual(assemble.first_difference(a, assemble.parse_version_components("1.2.3b")), (2, "suffix"))
		# A missing trailing component compares as (0, "").
		self.assertEqual(assemble.first_difference(a, assemble.parse_version_components("1.2")), (2, "numeric"))


# ── the item-model fixture kit ──────────────────────────────────────────────
def _item(slug, *, title="A changelog fact.", body=None, tags=("fix",), severity="info",
		change=True, local=None, security=None, anchor=None, **extra):
	"""One conforming item. `slug` only has to be unique within its tool — the
	validator derives the id from the anchor, checkers never write one."""
	item = {
		"anchor": anchor if anchor is not None else {"kind": "release", "value": "1.0.1/" + slug},
		"title": title,
		"tags": list(tags),
		"severity": severity,
	}
	if body is not None:
		item["body"] = body
	if change:
		item["change"] = {"version": "1.0.1", "citation": "upstream release note for " + slug}
	if local is not None:
		item["local"] = local
	if security is not None:
		item["security"] = security
	item.update(extra)
	return item


def _local(direction="unclear", effect="none", statement="Reasoning about this setup.",
		evidence=(), citations=()):
	return {"direction": direction, "effect": effect, "statement": statement,
		"evidence": list(evidence), "citations": list(citations)}


def _sec(cve_id=None, rating="unknown", basis="unrated", wild=False, advisory=None):
	return {"cve_id": cve_id, "advisory_id": advisory, "rating": rating,
		"rating_basis": basis, "exploited_in_wild": wild}


def _cand(tool_id, name, source, current, latest, **extra):
	cand = {"id": tool_id, "name": name, "source": source,
		"current_version": current, "latest_version": latest, "pinned": False}
	cand.update(extra)
	return cand


def assemble_session(collect, research_entries, extra_files=None):
	"""Write a throwaway session dir, run it through main(), and return
	(report, stderr). Going through main() rather than build_tool() is the
	point: it is the only way to exercise validation, the two ordering
	constraints and the id-uniqueness pass together.

	`extra_files` maps a research/ filename to raw bytes or text, for the
	degradation cases where the file itself is the hostile input."""
	with tempfile.TemporaryDirectory() as tmp:
		session = os.path.join(tmp, "tool-update-review-20260822T113344Z")
		os.makedirs(os.path.join(session, "research"))
		with open(os.path.join(session, "collect.json"), "w", encoding="utf-8") as fh:
			json.dump(collect, fh)
		if research_entries is not None:
			with open(os.path.join(session, "research", "01-all.json"), "w", encoding="utf-8") as fh:
				json.dump(research_entries, fh)
		for name, blob in (extra_files or {}).items():
			mode = "wb" if isinstance(blob, bytes) else "w"
			kwargs = {} if isinstance(blob, bytes) else {"encoding": "utf-8"}
			with open(os.path.join(session, "research", name), mode, **kwargs) as fh:
				fh.write(blob)
		argv, err = sys.argv, io.StringIO()
		sys.argv = ["assemble.py", session, "--macos-setup-root", tmp,
			"--dotfiles-root", tmp, "--systems-root", tmp]
		try:
			with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
				assemble.main()
		finally:
			sys.argv = argv
		with open(os.path.join(session, "report.json"), "r", encoding="utf-8") as fh:
			report = json.load(fh)
		with open(os.path.join(session, "assemble.warn"), "r", encoding="utf-8") as fh:
			report["_warn"] = fh.read()
		with open(os.path.join(session, "assemble.log"), "r", encoding="utf-8") as fh:
			report["_log"] = fh.read()
		return report, err.getvalue()


def build_one(candidate, research, **collect_extra):
	"""One candidate through the whole path → its Tool object."""
	key = {"brew-health": "brew_health", "skill-drift": "skill_drift"}.get(candidate["source"])
	collect = {"generated_at": "2026-08-22T11:33:44Z", "machine": {}}
	if key:
		collect[key] = {"findings": [candidate], "suppressed": []}
	else:
		collect[{"mise": "mise", "standalone": "standalone", "macos": "macos"}
			.get(candidate["source"], "brew")] = [candidate]
	collect.update(collect_extra)
	report, _ = assemble_session(collect, [research] if research is not None else [])
	return report["tools"][0]


# ── 2. Semantic classification ──────────────────────────────────────────────
# Every key a fixture expectation dict may carry. Asserted as an allowlist so
# a typo'd key fails loudly instead of being silently never checked — the
# failure mode that makes a green matrix meaningless.
EXPECT_KEYS = {
	"has_security", "security_only", "impact", "cve_count", "cve_claimed_count",
	"review_bucket", "risk_level", "version_delta", "version_scheme",
	"suggestions", "pre_accept", "needs_sudo", "display_item_ids",
}


def _fixtures():
	return [
		(
			"S1 security-only patch, nothing reaches here → security_auto, pre-accepted",
			_cand("brew:s1", "s1", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s1", "links": [], "items": [
				_item("cve", tags=["security"], severity="notable",
					title="Fixes CVE-2026-53789 in the agent forwarding path",
					security=_sec("CVE-2026-53789", "high", "vendor")),
				_item("chore", tags=["chore"], severity="info"),
			]},
			{"has_security": True, "security_only": True, "impact": "none",
				"review_bucket": "security_auto", "risk_level": "low",
				"pre_accept": True, "cve_count": 1},
		),
		(
			"S2 a feature alongside the fix disqualifies security_only",
			_cand("brew:s2", "s2", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s2", "links": [], "items": [
				_item("cve", tags=["security"], severity="notable", security=_sec("CVE-2026-1111")),
				_item("feat", tags=["feature"], severity="notable"),
			]},
			{"has_security": True, "security_only": False,
				"review_bucket": "security_mixed", "pre_accept": True},
		),
		(
			"S3 a `breaking` item at warning is impact, and never security_only",
			_cand("brew:s3", "s3", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s3", "links": [], "items": [
				_item("cve", tags=["security"], severity="notable", security=_sec("CVE-2026-2222")),
				_item("brk", tags=["breaking"], severity="warning",
					local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}])),
			]},
			{"has_security": True, "security_only": False, "impact": "possible",
				"review_bucket": "security_mixed", "risk_level": "elevated",
				"pre_accept": False},
		),
		(
			"S4 pinned is impact and elevated",
			_cand("brew:s4", "s4", "brew", "1.0.0", "1.0.1", pinned=True),
			{"id": "brew:s4", "links": [], "items": [_item("a", tags=["fix"], severity="info")]},
			{"impact": "possible", "risk_level": "elevated",
				"review_bucket": "attention", "pre_accept": False},
		),
		(
			"S5 research that told us nothing is never security_only",
			_cand("brew:s5", "s5", "brew", "1.0.0", "1.0.1"),
			None,
			{"has_security": False, "security_only": False, "impact": "unknown",
				"risk_level": "elevated", "review_bucket": "attention", "pre_accept": False},
		),
		(
			"S6 config needs_attention with an edit suggestion → attention, impact possible",
			_cand("brew:s6", "s6", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s6", "links": [], "items": [_item("a", tags=["fix"], severity="info")],
				"config_status": {"state": "needs_attention", "detail": "The Brewfile pin is stale.",
					"evidence": [{"path": "Brewfile"}], "citations": []},
				"suggestions": [{"id": "brew:s6:edit", "kind": "edit", "title": "Update the pin",
					"target_files": [{"path": "Brewfile", "description": "pin"}],
					"rationale": "", "motivating_link": None, "diff_preview": None}]},
			{"impact": "possible", "review_bucket": "attention", "pre_accept": False,
				"suggestions": 2},
		),
		(
			"S7 a major bump is elevated on its own",
			_cand("brew:s7", "s7", "brew", "1.0.0", "2.0.0"),
			{"id": "brew:s7", "links": [], "items": [_item("a", tags=["feature"], severity="info")]},
			{"version_delta": "major", "risk_level": "elevated",
				"review_bucket": "attention", "pre_accept": False},
		),
		(
			"S8 an expected brew-health note needs no decision",
			{"id": "brew-health:path_note:gnubin", "name": "GNU coreutils on PATH",
				"source": "brew-health", "category": "path_note", "severity": "info",
				"detail": "GNU coreutils shadow the system ones — intentional here.",
				"remediation": None, "expected": True},
			None,
			{"has_security": False, "impact": "none", "risk_level": "low",
				"review_bucket": "routine", "pre_accept": False, "suggestions": 0},
		),
		(
			"S9 a real brew-health finding is attention, and its remediation never pre-accepts",
			{"id": "brew-health:unlinked_keg:tree-sitter", "name": "Unlinked keg: tree-sitter",
				"source": "brew-health", "category": "unlinked_keg", "severity": "warning",
				"detail": "Keg `tree-sitter` is unlinked in the Cellar.",
				"remediation": {"command": "brew link tree-sitter", "auto_runnable": True,
					"needs_sudo": False, "label": "Relink tree-sitter"},
				"expected": False},
			None,
			{"has_security": False, "impact": "possible", "risk_level": "elevated",
				"review_bucket": "attention", "pre_accept": False, "suggestions": 1},
		),
		(
			"S10 a skill drift the user must resolve is attention; `:sync` never pre-accepts",
			{"id": "skill-drift:anthropics/pptx", "name": "pptx (anthropics)",
				"source": "skill-drift", "drift_state": "upstream_ahead", "severity": "notable",
				"detail": "Upstream moved since the last sync.",
				"vendor": "anthropics", "skill": "pptx",
				"remediation": {"command": "bash config/agent-skills/sync-upstream.sh",
					"auto_runnable": True, "needs_sudo": False, "label": "Sync anthropics"},
				"expected": False},
			None,
			{"review_bucket": "attention", "pre_accept": False, "suggestions": 1},
		),
		(
			"S11 a locally-patched skill needs no decision",
			{"id": "skill-drift:tapppi/jira", "name": "jira (tapppi)",
				"source": "skill-drift", "drift_state": "local_only", "severity": "info",
				"detail": "The local copy is deliberately patched.",
				"vendor": "tapppi", "skill": "jira", "remediation": None, "expected": True},
			None,
			{"review_bucket": "routine", "pre_accept": False, "suggestions": 0},
		),
		(
			"S12 a macOS security update cannot be run, so it never reaches security_auto",
			_cand("macos:Safari", "Safari", "macos", "26.0", "26.1"),
			{"id": "macos:Safari", "links": [], "items": [
				_item("cve", tags=["security"], severity="notable", security=_sec("CVE-2026-3333"))]},
			{"has_security": True, "security_only": True,
				"review_bucket": "security_mixed", "version_delta": "unknown",
				"pre_accept": False, "needs_sudo": True},
		),
		(
			"S13 vendor-silent security with no security item still lands in a security bucket",
			# THE REGRESSION TEST. `vendor_silent_categories: ["security"]` is
			# research saying "this release has security content the vendor
			# refused to detail". Read only the tag-derived has_security flag
			# and the tool computes: not security (no security tag), and NOT
			# elevated either — because compute_risk_level's "no items" clause
			# is suppressed by the non-empty vendor_silent list. It lands in
			# `routine`, out of the security section entirely, and **arrives
			# pre-accepted**: a field whose entire purpose is "look at this"
			# would guarantee nobody does.
			#
			# Both halves are asserted. `has_security` is what makes it visible;
			# `risk_level` is what stops it being auto-approved on the way past,
			# because `apply_pre_accept` accepts on `risk_level == "low"` and
			# widening `has_security` alone leaves that untouched.
			_cand("brew:s13", "s13", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s13", "links": [], "vendor_silent_categories": ["security"],
				"items": [_item("feat", tags=["feature"], severity="notable")]},
			{"has_security": True, "security_only": False, "risk_level": "elevated",
				"review_bucket": "security_mixed", "pre_accept": False},
		),
		(
			"S14 an item whose local effect is a risk at notable is impact",
			_cand("brew:s14", "s14", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s14", "links": [], "items": [
				_item("a", tags=["fix"], severity="notable",
					local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}]))]},
			# `review_bucket` deliberately does NOT read impact outside the
			# security_auto clause, so this stays routine — impact and bucket
			# are different axes and always have been.
			{"impact": "possible", "review_bucket": "routine"},
		),
		(
			"S15 an info-level local finding is not impact on its own",
			_cand("brew:s15", "s15", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s15", "links": [], "items": [
				_item("a", tags=["fix"], severity="info",
					local=_local("does_not_reach", "benefit"))]},
			{"impact": "none", "risk_level": "low", "review_bucket": "routine",
				"pre_accept": True},
		),
		(
			"S16 a security item that reaches this machine is shown inline; one that does not is not",
			_cand("brew:s16", "s16", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:s16", "links": [], "items": [
				_item("reaches", tags=["security"], severity="notable",
					security=_sec("CVE-2026-4444", "medium", "nvd"),
					local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}])),
				_item("misses", tags=["security"], severity="info",
					security=_sec("CVE-2026-5555"),
					local=_local("does_not_reach", "benefit")),
			]},
			{"has_security": True, "cve_count": 2,
				"display_item_ids": ["brew:s16#release:1.0.1%2Freaches"]},
		),
	]


def _fixture(prefix):
	"""Look a fixture up by label prefix (S1…S16). A positional index silently
	retargets the moment a row is inserted above it."""
	matches = [f for f in _fixtures() if f[0].startswith(prefix + " ")]
	assert len(matches) == 1, f"{prefix}: expected 1 fixture, found {len(matches)}"
	return matches[0]


class SemanticClassificationTests(unittest.TestCase):
	def test_fixtures(self):
		for label, candidate, research, expect in _fixtures():
			with self.subTest(label):
				self.assertEqual(set(expect) - EXPECT_KEYS, set(), f"{label}: unknown expectation key")
				tool = build_one(candidate, research)
				sec = tool["security"]
				for key in ("has_security", "security_only", "impact", "cve_count",
						"cve_claimed_count", "display_item_ids"):
					if key in expect:
						got = sec["display_item_ids"] if key == "display_item_ids" else sec[key]
						self.assertEqual(got, expect[key], f"{label}: security.{key}")
				for key in ("review_bucket", "risk_level", "version_delta", "version_scheme"):
					if key in expect:
						self.assertEqual(tool[key], expect[key], f"{label}: {key}")
				if "suggestions" in expect:
					self.assertEqual(len(tool["suggestions"]), expect["suggestions"], f"{label}: suggestions")
				if "pre_accept" in expect:
					baseline = assemble.baseline_upgrade(tool)
					if baseline is not None:
						self.assertEqual(baseline["pre_accept"], expect["pre_accept"], f"{label}: pre_accept")
					else:
						# No baseline at all (a finding source) — nothing on the
						# tool may pre-accept.
						self.assertFalse(expect["pre_accept"], f"{label}: fixture expects a baseline that isn't there")
						self.assertFalse(any(s["pre_accept"] for s in tool["suggestions"]), f"{label}: pre_accept")
				if "needs_sudo" in expect:
					self.assertEqual(assemble.baseline_upgrade(tool)["needs_sudo"], expect["needs_sudo"], label)
				# Invariants that hold for every tool, no exceptions.
				self.assertEqual(sec["cve_count"], len(sec["cve_ids"]), f"{label}: cve_count is id-backed")
				self.assertEqual(sec["cve_ids"], sorted(set(sec["cve_ids"]), key=assemble.cve_sort_key), label)
				self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"], label)
				for sug in tool["suggestions"]:
					self.assertIn("pre_accept", sug, f"{label}: every suggestion carries pre_accept")

	def test_the_bucket_is_explained_by_its_own_recorded_inputs(self):
		"""`bucket_inputs` is what convergence reads instead of re-deriving the
		bucket. A bucket its own inputs cannot explain is exactly the opacity
		§C3 exists to remove — so the two must never be computed from different
		values of `has_security`."""
		for label, candidate, research, _ in _fixtures():
			with self.subTest(label):
				tool = build_one(candidate, research)
				inputs = tool["bucket_inputs"]
				self.assertEqual(tool["security"]["has_security"], inputs["has_security"], label)
				self.assertEqual(tool["security"]["security_only"], inputs["security_only"], label)
				self.assertEqual(tool["security"]["impact"], inputs["impact"], label)
				self.assertEqual(tool["version_delta"], inputs["version_delta"], label)
				if tool["review_bucket"] == "security_mixed":
					self.assertTrue(inputs["has_security"], label)
				if tool["review_bucket"] == "security_auto":
					self.assertTrue(inputs["has_security"] and inputs["security_only"], label)
					self.assertEqual(inputs["impact"], "none", label)

	def test_vendor_silent_security_is_never_quietly_pre_accepted(self):
		"""The named regression, asserted as its consequence rather than its
		mechanism: a tool whose vendor admitted undetailed security content must
		be visible as security work AND must not be auto-approved on the way
		past. Both halves are needed, and the second is easy to miss — moving
		the tool into a security bucket makes it visible, but `pre_accept`
		reads `risk_level`, so without the risk limb it arrives already
		accepted in the section it was just made visible in.

		How far the risk limb reaches is set by the bucket precedence, not by
		it: `security_auto` returns from clause 2 and `risk_level` is not read
		until clause 4, and `pre_accept` is `risk_level == "low"` OR
		`review_bucket == "security_auto"`. So elevated risk is no bar for a
		tool that reaches `security_auto`; this fixture misses clause 2 because
		its `feature` item makes `security_only` false, which is the shape that
		motivated the fix. `test_security_only_silence_still_reaches_auto`
		pins the other side."""
		_, candidate, research, _ = _fixture("S13")
		tool = build_one(candidate, research)
		self.assertIn(tool["review_bucket"], ("security_auto", "security_mixed"))
		self.assertTrue(tool["security"]["has_security"])
		self.assertTrue(tool["bucket_inputs"]["has_security"])
		self.assertFalse(assemble.baseline_upgrade(tool)["pre_accept"])

	def test_security_only_silence_still_reaches_auto(self):
		"""The other side of the clause, pinned so nobody reads the risk limb as
		a blanket bar. A vendor-silent-security tool whose READABLE items are
		all security-only still reaches `security_auto` and is still
		pre-accepted, `risk_level: elevated` notwithstanding — `security_auto`
		returns from clause 2, `risk_level` is not consulted until clause 4, and
		`pre_accept` accepts on either. That is the designed path (the content
		we could read is security-only, so taking the update is the safe
		action), and it is a judgement about precedence rather than a
		consequence of the risk clause."""
		tool = build_one(_cand("brew:vs", "vs", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:vs", "links": [], "vendor_silent_categories": ["security"],
				"items": [_item("a", tags=["chore"], severity="info")]})
		self.assertEqual(tool["risk_level"], "elevated")
		self.assertEqual(tool["review_bucket"], "security_auto")
		self.assertTrue(assemble.baseline_upgrade(tool)["pre_accept"])

	def test_health_suggestions_never_pre_accept(self):
		# brew link tree-sitter IS auto_runnable — it is excluded because a
		# health remediation is not a `:upgrade`-suffixed baseline, not because
		# it could not be run.
		_, candidate, research, _ = _fixture("S9")
		tool = build_one(candidate, research)
		self.assertTrue(tool["suggestions"][0]["auto_runnable"])
		self.assertFalse(tool["suggestions"][0]["pre_accept"])
		self.assertIsNone(assemble.baseline_upgrade(tool))

	def test_drift_suggestions_never_pre_accept(self):
		_, candidate, research, _ = _fixture("S10")
		tool = build_one(candidate, research)
		self.assertTrue(tool["suggestions"][0]["id"].endswith(":sync"))
		self.assertFalse(tool["suggestions"][0]["pre_accept"])
		self.assertIsNone(assemble.baseline_upgrade(tool))

	def test_a_finding_item_is_synthesized_with_a_local_block_and_no_change(self):
		"""A health finding is not an upstream change at all — it is a statement
		about this install, so the synthesized item carries `local` and
		`change: null`. The page's severity filters read `local`, so a finding
		with none would vanish from "relevant only"."""
		_, candidate, research, _ = _fixture("S9")
		tool = build_one(candidate, research)
		self.assertEqual(len(tool["items"]), 1)
		item = tool["items"][0]
		self.assertIsNone(item["change"])
		self.assertEqual(item["local"]["direction"], "reaches")
		self.assertEqual(item["local"]["effect"], "risk")
		self.assertIn("tree-sitter", item["title"])
		self.assertEqual(model.primary_group(item), "fixes")

	def test_an_expected_finding_is_synthesized_with_no_risk(self):
		_, candidate, research, _ = _fixture("S8")
		tool = build_one(candidate, research)
		self.assertEqual(tool["items"][0]["local"]["effect"], "none")
		self.assertEqual(model.primary_group(tool["items"][0]), "notes")

	def test_a_health_finding_never_reports_security_content(self):
		"""An untrusted tap tags `security` so it files under the security
		group, but the security *section* is about patches the user can take —
		counting it would make the section's count disagree with its cards."""
		tool = build_one({"id": "brew-health:untrusted_tap:x", "name": "Untrusted tap: x",
			"source": "brew-health", "category": "untrusted_tap", "severity": "warning",
			"detail": "Tap x is not in the trusted list.", "remediation": None,
			"expected": False}, None)
		self.assertEqual(model.primary_group(tool["items"][0]), "security")
		self.assertFalse(tool["security"]["has_security"])
		self.assertEqual(tool["security"]["cve_ids"], [])

	def test_auto_runnable_false_blocks_pre_accept(self):
		tool = build_one(_cand("standalone:x", "x", "standalone", "1.0.0", "1.0.1"),
			{"id": "standalone:x", "links": [], "items": [_item("a", severity="info")]})
		baseline = assemble.baseline_upgrade(tool)
		self.assertFalse(baseline["auto_runnable"])
		self.assertFalse(baseline["pre_accept"])
		self.assertEqual(tool["review_bucket"], "attention")

	def test_items_arrive_in_the_contracts_canonical_order(self):
		"""Assembly never sorts items itself — the order is the contract's, and
		the page renders it verbatim. Re-sorting on either side is how two
		copies of one corpus stopped being byte-identical."""
		tool = build_one(_cand("brew:ord", "ord", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:ord", "links": [], "items": [
				_item("c", tags=["chore"], severity="info"),
				_item("a", tags=["security"], severity="warning",
					security=_sec(), local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}])),
				_item("b", tags=["feature"], severity="notable"),
			]})
		self.assertEqual(tool["items"], model.order_items(tool["items"]))
		self.assertEqual([model.primary_group(i) for i in tool["items"]],
			["security", "features", "notes"])


# ── 3. The CVE rollup ───────────────────────────────────────────────────────
class CveScanTests(unittest.TestCase):
	def test_cve_ids(self):
		for text, want in [
			("Fixes CVE-2026-53789 in the parser", ["CVE-2026-53789"]),
			("cve-2026-1234 lowercase", ["CVE-2026-1234"]),
			("NOTCVE-2026-1234 is not one", []),
			("CVE-3026-1234 has an impossible year", []),
			("CVE-2026-123 is too short", []),
			("CVE-2026-1234567 seven digits is fine", ["CVE-2026-1234567"]),
		]:
			with self.subTest(text):
				tool = {"items": [_item("a", title=text)]}
				self.assertEqual(assemble.extract_cve_ids(tool), want)

	def test_cve_ids_sort_numerically_not_lexically(self):
		tool = {"items": [_item("a", title="CVE-2026-12143 and CVE-2026-9595")]}
		self.assertEqual(assemble.extract_cve_ids(tool),
			["CVE-2026-9595", "CVE-2026-12143"])

	def test_a_structural_cve_id_is_counted_without_being_written_in_prose(self):
		"""The point of the item model: the id is a field, not a sentence."""
		tool = {"items": [_item("a", tags=["security"], security=_sec("CVE-2026-7777"))]}
		self.assertEqual(assemble.extract_cve_ids(tool), ["CVE-2026-7777"])

	def test_a_cve_anchor_is_counted_too(self):
		tool = {"items": [_item("a", tags=["security"], security=_sec(),
			anchor={"kind": "cve", "value": "CVE-2026-8888"})]}
		self.assertEqual(assemble.extract_cve_ids(tool), ["CVE-2026-8888"])

	def test_a_malformed_structural_id_is_not_counted(self):
		"""I-5 reports it; counting it would put an unresolvable chip on the
		card, and `cve_count` is defined as the size of a list the page can
		link every member of."""
		tool = {"items": [_item("a", tags=["security"], security=_sec("CVE-26-1"))]}
		self.assertEqual(assemble.extract_cve_ids(tool), [])

	def test_the_id_scan_reads_the_local_statement_but_not_its_citations(self):
		"""A `prior_review` citation legitimately names an advisory from
		outside this current→latest range; the statement is about this range by
		construction."""
		tool = {"items": [_item("a",
			local=_local(statement="CVE-2026-1111 is the one that matters here",
				citations=[{"kind": "prior_review", "text": "CVE-2020-9999 last time", "url": None}]))]}
		self.assertEqual(assemble.extract_cve_ids(tool), ["CVE-2026-1111"])

	def test_cve_claims(self):
		for text, want in [
			("fixes 33 CVEs", 33),
			("addresses 12 security issues", 12),
			("resolves 4 vulnerabilities", 4),
			("Both 5.80 CVEs need a running service", None),  # the stunnel false positive
			("1234 CVEs", None),  # \d{1,3} plus the lookbehind
			("no numbers here", None),
		]:
			with self.subTest(text):
				tool = {"items": [_item("a", title=text)]}
				self.assertEqual(assemble.extract_cve_claim(tool), want)

	def test_claim_takes_the_max_never_the_sum(self):
		tool = {"items": [_item("a", title="fixes 50 CVEs"), _item("b", title="fixes 47 CVEs")]}
		self.assertEqual(assemble.extract_cve_claim(tool), 50)

	def test_a_claim_is_never_read_out_of_our_own_analysis(self):
		"""`local.statement` is what WE concluded. "the 5 CVEs above do not
		reach us" is not the vendor claiming five fixes."""
		tool = {"items": [_item("a", local=_local(statement="the 5 CVEs above do not reach us"))]}
		self.assertIsNone(assemble.extract_cve_claim(tool))


class SeverityRollupTests(unittest.TestCase):
	def _tool(self, items):
		return build_one(_cand("brew:r", "r", "brew", "1.0.0", "1.0.1"),
			{"id": "brew:r", "links": [], "items": items})

	def test_counts_have_the_whole_vocabulary_and_sum_to_cve_count(self):
		tool = self._tool([
			_item("a", tags=["security"], security=_sec("CVE-2026-1111", "high", "vendor")),
			_item("b", tags=["security"], security=_sec("CVE-2026-2222")),
		])
		sec = tool["security"]
		self.assertEqual(set(sec["severity_counts"]), set(model.CVE_RATINGS))
		self.assertEqual(sec["severity_counts"]["high"], 1)
		self.assertEqual(sec["severity_counts"]["unknown"], 1)
		self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"])

	def test_the_sum_holds_when_nothing_is_rated_at_all(self):
		tool = self._tool([_item("a", title="Fixes CVE-2026-1111 and CVE-2026-2222")])
		sec = tool["security"]
		self.assertEqual(sec["cve_count"], 2)
		self.assertEqual(sec["severity_counts"]["unknown"], 2)
		self.assertEqual(sum(sec["severity_counts"].values()), 2)

	def test_a_rating_with_no_basis_is_unknown_never_a_guess(self):
		"""Research is forbidden from deriving a severity from how a
		description reads; `rating_basis` is how assembly can tell a fetched
		rating from an impression. I-6 reports it too."""
		tool = self._tool([_item("a", tags=["security"],
			security=_sec("CVE-2026-1111", "critical", "unrated"))])
		self.assertEqual(tool["security"]["severity_counts"]["critical"], 0)
		self.assertEqual(tool["security"]["severity_counts"]["unknown"], 1)

	def test_two_items_rating_one_id_keep_the_worse(self):
		tool = self._tool([
			_item("a", tags=["security"], security=_sec("CVE-2026-1111", "medium", "nvd")),
			_item("b", tags=["security"], security=_sec("CVE-2026-1111", "high", "vendor")),
		])
		self.assertEqual(tool["security"]["severity_counts"]["high"], 1)
		self.assertEqual(tool["security"]["severity_counts"]["medium"], 0)

	def test_a_finding_source_carries_a_zeroed_block(self):
		_, candidate, research, _ = _fixture("S9")
		sec = build_one(candidate, research)["security"]
		self.assertEqual(sec["cve_ids"], [])
		self.assertEqual(sec["cve_count"], 0)
		self.assertFalse(sec["has_security"])
		self.assertEqual(set(sec["severity_counts"]), set(model.CVE_RATINGS))
		self.assertEqual(sum(sec["severity_counts"].values()), 0)


# ── 4. Shape drift at the research boundary ─────────────────────────────────
DRIFTED = {
	"id": "brew:drift",
	"items": "no notable changes",          # a string where an array belongs
	"links": None,                          # present-but-null
	"config_status": "up to date",          # a string where an object belongs
	"suggestions": [{"kind": "edit", "title": "ok", "id": "brew:drift:e1"}, "not an object"],
	"vendor_silent_categories": {"security": True},
	"release_inventory": "none",
}


class ShapeDriftTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.report, cls.stderr = assemble_session(
			{"generated_at": "2026-08-22T11:33:44Z", "machine": {},
				"brew": [_cand("brew:drift", "drift", "brew", "1.0.0", "1.0.1"),
					_cand("brew:fine", "fine", "brew", "1.0.0", "1.0.1")]},
			[DRIFTED, {"id": "brew:fine", "links": [], "items": [_item("a")]}])

	def test_a_drifted_file_costs_no_tool(self):
		self.assertEqual([t["id"] for t in self.report["tools"]], ["brew:drift", "brew:fine"])

	def test_every_array_reads_as_a_list_downstream(self):
		tool = self.report["tools"][0]
		for key in ("items", "links", "suggestions", "vendor_silent_categories",
				"release_inventory", "quarantine", "spec_violations"):
			self.assertIsInstance(tool[key], list, key)
		self.assertIsInstance(tool["config_status"], dict)

	def test_a_wrong_typed_member_is_quarantined_not_dropped(self):
		"""Dropping is deletion, and a human would have read it
		(references/item-schema.md §0)."""
		tool = self.report["tools"][0]
		self.assertIn("not an object", json.dumps(tool["quarantine"]))

	def test_the_drift_is_reported_in_the_conformance_channel(self):
		self.assertTrue(self.report["_warn"].strip())
		for line in self.report["_warn"].splitlines():
			self.assertRegex(line, r"^[EW]-[A-Z0-9-]+ ")
		self.assertIn("brew:drift", self.report["_warn"])
		self.assertNotIn("brew:fine", self.report["_warn"])

	def test_the_good_tool_is_untouched(self):
		fine = self.report["tools"][1]
		self.assertEqual(fine["spec_violations"], [])
		self.assertEqual(len(fine["items"]), 1)

	def test_both_finding_builders_survive_the_same_drift(self):
		"""`build_health_tool` and `build_drift_tool` take the same `_tool_base`
		path as the version builder, so a shape that would break one breaks all
		three. Assert the pair explicitly rather than trusting the shared path."""
		for candidate in (
				{"id": "brew-health:other:x", "name": "x", "source": "brew-health",
					"category": "other", "severity": "notable", "detail": "d",
					"remediation": None, "expected": False},
				{"id": "skill-drift:v/s", "name": "s (v)", "source": "skill-drift",
					"drift_state": "upstream_ahead", "severity": "notable", "detail": "d",
					"vendor": "v", "skill": "s", "remediation": None, "expected": False}):
			with self.subTest(candidate["source"]):
				tool = build_one(candidate, dict(DRIFTED, id=candidate["id"]))
				self.assertEqual(len(tool["items"]), 1)
				self.assertIsInstance(tool["config_status"], dict)
				self.assertFalse(any(s["pre_accept"] for s in tool["suggestions"]))

	def test_a_drifted_tool_can_never_be_pre_accepted(self):
		"""A tool the validator could not read is the last thing that should be
		auto-approved."""
		tool = self.report["tools"][0]
		self.assertFalse(any(s["pre_accept"] for s in tool["suggestions"]))


# ── 5. Report-level invariants ──────────────────────────────────────────────
# A synthetic session, small enough to reason about and shaped to exercise the
# things prose invariants cannot: a CVE shared by two tools (union < sum), a
# duplicate suggestion id (the rename pass runs between finalize_tool and
# build_highlights), one health finding and one skill-drift finding — the two
# non-version sources, which is what makes the summary's three denominators
# actually differ from one another here.
COLLECT = {
	"generated_at": "2026-08-22T11:33:44Z",
	"machine": {"arch": "arm64", "os": "macOS 26.0", "hostname": "test"},
	"brew": [
		{"id": "brew:openssh", "name": "openssh", "source": "brew",
			"current_version": "10.4p1", "latest_version": "10.5p1", "pinned": False},
		{"id": "brew:ssh-copy-id", "name": "ssh-copy-id", "source": "brew",
			"current_version": "10.4p1", "latest_version": "10.5p1", "pinned": False},
		{"id": "brew:podman", "name": "podman", "source": "brew",
			"current_version": "4.9.3", "latest_version": "5.5.1", "pinned": True},
		{"id": "brew:parallel", "name": "parallel", "source": "brew",
			"current_version": "20260622", "latest_version": "20260722", "pinned": False},
	],
	"mise": [
		{"id": "mise:uv", "name": "uv", "source": "mise",
			"current_version": "0.11.29", "latest_version": "0.12.5", "pinned": False},
	],
	"standalone": [],
	"macos": [],
	"brew_health": {
		"findings": [{
			"id": "brew-health:unlinked_keg:tree-sitter", "name": "Unlinked keg: tree-sitter",
			"source": "brew-health", "category": "unlinked_keg", "severity": "warning",
			"detail": "Keg `tree-sitter` is unlinked in the Cellar.", "affected": ["tree-sitter"],
			"remediation": {"command": "brew link tree-sitter", "auto_runnable": False,
				"needs_sudo": False, "label": "Relink tree-sitter"},
			"expected": False, "pinned": False, "current_version": None, "latest_version": None,
		}],
		"suppressed": [],
	},
	"skill_drift": {
		"findings": [{
			"id": "skill-drift:anthropics/pptx", "name": "pptx (anthropics)",
			"source": "skill-drift", "drift_state": "upstream_ahead", "severity": "notable",
			"detail": "Upstream moved since the last sync; the vendored tree is unmodified.",
			"vendor": "anthropics", "skill": "pptx", "vendor_kind": "subtree",
			"remediation": {"command": "bash config/agent-skills/sync-upstream.sh",
				"auto_runnable": False, "needs_sudo": False,
				"label": "Sync anthropics from upstream"},
			"expected": False, "pinned": False, "current_version": None, "latest_version": None,
		}],
		"suppressed": ["anthropics/docx: in sync with upstream"],
	},
}

RESEARCH = [
	{"id": "brew:openssh", "links": [], "items": [
		_item("cve", tags=["security"], severity="notable",
			title="Fixes CVE-2026-53789 in the agent forwarding path",
			security=_sec("CVE-2026-53789", "high", "vendor"),
			local=_local("reaches", "risk",
				statement="Agent forwarding is how this machine reaches its remotes.",
				evidence=[{"path": "Brewfile"}])),
	]},
	# The same advisory rated differently by a second source — the report-wide
	# union must take the worse of the two, never the last one seen.
	{"id": "brew:ssh-copy-id", "links": [], "items": [
		_item("cve", tags=["security"], severity="info",
			title="Same source tarball: CVE-2026-53789",
			security=_sec("CVE-2026-53789", "medium", "nvd")),
	]},
	{"id": "brew:podman", "links": [], "items": [
		_item("krun", tags=["breaking"], severity="incompatible",
			title="Intel Mac not supported in v5+",
			local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}])),
	], "suggestions": [
		{"id": "brew:podman:upgrade", "kind": "edit",  # collides with the baseline id
			"title": "Annotate the Brewfile pin", "target_files": [{"path": "Brewfile"}],
			"rationale": "", "motivating_link": None, "diff_preview": None},
		{"kind": "edit", "title": "No id at all", "target_files": [],  # id omitted entirely
			"rationale": "", "motivating_link": None, "diff_preview": None},
	]},
	{"id": "brew:parallel", "links": [], "items": [_item("snap", tags=["chore"], severity="info")]},
	{"id": "mise:uv", "links": [], "items": [_item("res", tags=["feature"], severity="notable")]},
]


class ReportInvariantTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.report, cls.stderr = assemble_session(COLLECT, RESEARCH)

	def test_schema_version_is_two(self):
		"""`tools[].items[]` replaced four arrays, so a schema-1 consumer
		cannot read this report and must not try."""
		self.assertEqual(self.report["schema_version"], 2)
		self.assertEqual(self.report["contract_version"], model.CONTRACT_VERSION)

	def test_by_delta_sums_to_total_outdated(self):
		summary = self.report["summary"]
		self.assertEqual(sum(summary["by_delta"].values()), summary["total_outdated"])
		self.assertEqual(set(summary["by_delta"]), {"major", "minor", "patch", "revision", "unknown"})

	def test_by_bucket_sums_to_every_tool(self):
		summary = self.report["summary"]
		self.assertEqual(sum(summary["by_bucket"].values()), len(self.report["tools"]))
		self.assertEqual(sum(summary["by_bucket"].values()),
			summary["total_outdated"] + summary["health_count"] + summary["skill_drift_count"])

	def test_report_cve_count_is_a_union(self):
		"""One advisory on two tools counts once report-wide and once per
		tool, so the union is strictly smaller than the sum."""
		per_tool = sum(t["security"]["cve_count"] for t in self.report["tools"])
		self.assertEqual(per_tool, 2)
		self.assertEqual(self.report["summary"]["security"]["cve_count"], 1)

	def test_summary_severity_counts_is_a_union_taking_the_worse(self):
		counts = self.report["summary"]["security"]["severity_counts"]
		self.assertEqual(sum(counts.values()), self.report["summary"]["security"]["cve_count"])
		self.assertEqual(counts["high"], 1)
		self.assertEqual(counts["medium"], 0)
		self.assertIn("keeping the worse", self.report["_log"])

	def test_severity_counts_sum_to_cve_count_on_every_tool(self):
		for tool in self.report["tools"]:
			with self.subTest(tool["id"]):
				sec = tool["security"]
				self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"])

	def test_id_less_suggestion_gets_one_synthesized(self):
		podman = next(t for t in self.report["tools"] if t["id"] == "brew:podman")
		ids = [s["id"] for s in podman["suggestions"]]
		self.assertTrue(all(ids))
		self.assertEqual(len(set(ids)), len(ids))

	def test_baseline_detection_survives_the_rename(self):
		"""The baseline is suggestions[0] and claims its id first, so a
		colliding research suggestion is what gets renamed."""
		podman = next(t for t in self.report["tools"] if t["id"] == "brew:podman")
		self.assertEqual(podman["suggestions"][0]["id"], "brew:podman:upgrade")
		self.assertIn("brew:podman:upgrade-2", [s["id"] for s in podman["suggestions"]])

	def test_highlights_are_capped_and_resolvable(self):
		by_id = {t["id"]: t for t in self.report["tools"]}
		self.assertLessEqual(len(self.report["highlights"]), 8)
		for h in self.report["highlights"]:
			self.assertIn(h["tool_id"], by_id)
			self.assertIn(h["why_source"], assemble._WHY_SOURCES)
			for sid in h["suggestion_ids"]:
				self.assertIn(sid, [s["id"] for s in by_id[h["tool_id"]]["suggestions"]])

	def test_a_highlight_why_ref_is_an_item_id(self):
		"""`content_ref()`'s `"rel:0"` / `"hl:2"` slots are gone — there is one
		array, and its members carry validator-assigned ids."""
		by_id = {t["id"]: t for t in self.report["tools"]}
		for h in self.report["highlights"]:
			if h["why_ref"] is None:
				continue
			item_ids = [i["id"] for i in by_id[h["tool_id"]]["items"]]
			self.assertIn(h["why_ref"], item_ids)

	def test_every_tool_carries_the_new_fields(self):
		for tool in self.report["tools"]:
			with self.subTest(tool["id"]):
				for key in ("items", "quarantine", "spec_violations", "bucket_inputs",
						"version_delta", "version_scheme", "version_delta_note",
						"risk_level", "review_bucket", "security"):
					self.assertIn(key, tool)
				for gone in ("headliners", "relevancy", "context"):
					self.assertNotIn(gone, tool)
				self.assertNotIn("notable", tool["security"])
				self.assertNotIn("cve_severities", tool["security"])

	def test_security_bucket_counts_fit_inside_tools_with_security(self):
		"""Both security buckets are subsets of the tools that carry security
		content, so their sum can never exceed it."""
		sec = self.report["summary"]["security"]
		self.assertLessEqual(sec["auto_count"] + sec["mixed_count"], sec["tools_with_security"])
		self.assertEqual(sec["auto_count"], self.report["summary"]["by_bucket"]["security_auto"])
		self.assertEqual(sec["mixed_count"], self.report["summary"]["by_bucket"]["security_mixed"])

	def test_finding_sources_are_excluded_from_by_delta_and_from_security(self):
		"""A brew-health finding is an environment issue and a skill-drift
		finding is a vendoring issue — neither is an update, so neither inflates
		the delta boxes or the security totals. Each is counted in its own."""
		self.assertEqual(self.report["summary"]["health_count"], 1)
		self.assertEqual(self.report["summary"]["skill_drift_count"], 1)
		self.assertEqual(sum(self.report["summary"]["by_delta"].values()),
			self.report["summary"]["total_outdated"])
		for tool in self.report["tools"]:
			if tool["source"] in assemble.NON_VERSION_SOURCES:
				self.assertFalse(tool["security"]["has_security"], tool["id"])
				self.assertEqual(tool["security"]["cve_ids"], [], tool["id"])

	def test_a_finding_source_cannot_contribute_a_grade_it_does_not_count(self):
		"""`compute_security` forces a non-version source's whole block empty,
		so the report-wide rollup must read the tool's emitted `cve_ids` rather
		than re-scanning its items — otherwise an enriched health finding
		carrying a CVE item grades an id the report deliberately does not
		count, and can fire a "rated differently on two tools" note about it."""
		report, _ = assemble_session(
			{"generated_at": "t", "machine": {},
				"brew_health": {"findings": [{"id": "brew-health:untrusted_tap:x",
					"name": "x", "source": "brew-health", "category": "untrusted_tap",
					"severity": "warning", "detail": "d", "remediation": None,
					"expected": False}], "suppressed": []}},
			[{"id": "brew-health:untrusted_tap:x", "links": [], "items": [
				_item("cve", tags=["security"], severity="warning",
					title="Fixes CVE-2026-9999 upstream",
					security=_sec("CVE-2026-9999", "critical", "vendor"))]}])
		sec = report["summary"]["security"]
		self.assertEqual(sec["cve_count"], 0)
		self.assertEqual(sum(sec["severity_counts"].values()), 0)
		self.assertEqual(sec["tools_with_security"], 0)
		self.assertNotIn("CVE-2026-9999", report["_log"])

	def test_a_within_tool_rating_disagreement_is_logged_once(self):
		"""`tool_cve_ratings` runs twice per version tool — once for the tool's
		own rollup, once for the report-wide union — so the second caller has to
		ask for the values without a second announcement. The array this
		replaced (`security.cve_severities[]`) made that second call
		unnecessary by existing; deleting it is what created the second
		reader."""
		report, _ = assemble_session(
			{"generated_at": "t", "machine": {},
				"brew": [_cand("brew:x", "x", "brew", "1.0.0", "1.0.1")]},
			[{"id": "brew:x", "links": [], "items": [
				_item("a", tags=["security"], severity="warning",
					security=_sec("CVE-2026-9999", "critical", "vendor")),
				_item("b", tags=["security"], severity="info",
					security=_sec("CVE-2026-9999", "low", "nvd"))]}])
		lines = [ln for ln in report["_log"].splitlines() if "rated both" in ln]
		self.assertEqual(len(lines), 1, lines)
		# The resolution itself is unaffected: the worse still wins.
		self.assertEqual(report["tools"][0]["security"]["severity_counts"]["critical"], 1)

	def test_a_finding_source_never_notes_a_rating_for_an_id_it_does_not_count(self):
		"""`tool_cve_ratings` notes a within-tool disagreement as it resolves
		one, so the report-wide rollup has to skip a non-counting tool BEFORE
		calling it — otherwise `assemble.log` carries "rated both critical and
		low" about an id the report counts nowhere."""
		report, _ = assemble_session(
			{"generated_at": "t", "machine": {},
				"brew_health": {"findings": [{"id": "brew-health:untrusted_tap:x",
					"name": "x", "source": "brew-health", "category": "untrusted_tap",
					"severity": "warning", "detail": "d", "remediation": None,
					"expected": False}], "suppressed": []}},
			[{"id": "brew-health:untrusted_tap:x", "links": [], "items": [
				_item("a", tags=["security"], severity="warning",
					security=_sec("CVE-2026-9999", "critical", "vendor")),
				_item("b", tags=["security"], severity="info",
					security=_sec("CVE-2026-9999", "low", "nvd"))]}])
		self.assertEqual(report["summary"]["security"]["cve_count"], 0)
		self.assertNotIn("CVE-2026-9999", report["_log"])

	def test_the_report_states_whether_the_corpus_validated(self):
		self.assertIn("validation", self.report)
		self.assertIsInstance(self.report["validation"]["clean"], bool)

	def test_local_findings_drive_the_two_summary_counters(self):
		"""An item with no local block is a statement about the release, not
		about this machine — the two boxes say "how many findings land on me"."""
		self.assertEqual(self.report["summary"]["incompatible_count"], 1)
		# The brew-health finding: `warning` severity, and its synthesized item
		# carries a local block, so it counts. It could not before — a finding
		# had no relevancy entry to be counted through — and counting it is the
		# honest answer to "how many findings land on me".
		self.assertEqual(self.report["summary"]["warning_count"], 1)

	def test_the_two_channels_stay_apart(self):
		"""assemble.warn is spec conformance and nothing else; the operational
		notes are in assemble.log. One stream at a 1:272 signal ratio is what
		made the single real defect invisible."""
		self.assertIn("suppressed", self.report["_log"])
		self.assertNotIn("suppressed", self.report["_warn"])
		for line in self.report["_warn"].splitlines():
			self.assertRegex(line, r"^[EW]-[A-Z0-9-]+ ")

	def test_a_clean_corpus_leaves_the_warn_file_empty(self):
		report, _ = assemble_session(
			{"generated_at": "2026-08-22T11:33:44Z", "machine": {},
				"brew": [_cand("brew:clean", "clean", "brew", "1.0.0", "1.0.1")]},
			[{"id": "brew:clean", "links": [], "items": [_item("a")]}])
		self.assertEqual(report["_warn"], "")
		self.assertTrue(report["validation"]["clean"])


class HighlightScoringTests(unittest.TestCase):
	def _tool(self, items, **research):
		research.setdefault("links", [])
		research["items"] = items
		research["id"] = "brew:h"
		return build_one(_cand("brew:h", "h", "brew", "1.0.0", "1.0.1"), research)

	def test_bare_major_does_not_clear_the_threshold(self):
		tool = build_one(_cand("brew:h", "h", "brew", "1.0.0", "2.0.0"),
			{"id": "brew:h", "links": [], "items": [_item("a", severity="info")]})
		score, reasons = assemble.score_tool(tool)
		self.assertIn("major_bump", reasons)
		self.assertLess(score, assemble._HIGHLIGHT_THRESHOLD)

	def test_a_local_incompatible_finding_outscores_a_release_level_one(self):
		"""The old pair was relevancy-incompatible (100) vs
		headliner-incompatible (40). The distinction survives the merge as
		"does this item carry a local block", which is what it always meant."""
		local = self._tool([_item("a", tags=["fix"], severity="incompatible",
			local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}]))])
		release = self._tool([_item("a", tags=["breaking"], severity="incompatible")])
		self.assertIn("incompatible_finding", assemble.score_tool(local)[1])
		self.assertNotIn("incompatible_finding", assemble.score_tool(release)[1])
		self.assertIn("breaking_change", assemble.score_tool(release)[1])
		self.assertGreater(assemble.score_tool(local)[0], assemble.score_tool(release)[0])

	def test_no_watch_item_hit_signal_survives(self):
		"""§I4 retires the literal-string channel outright: a paraphrase could
		silently cost 70 points, and the replacement is a structured field on
		the checker's output that does not exist yet. The signal is gone, not
		reimplemented against prose."""
		tool = self._tool([_item("a", title="Watch item hit: the quarantine flag moved",
			local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}]))])
		self.assertNotIn("watch_item_hit", assemble.score_tool(tool)[1])
		self.assertNotIn("watch_item_hit", assemble._WHY_SOURCES)
		with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assemble.py"),
				encoding="utf-8") as fh:
			source = fh.read()
		self.assertNotIn("_WATCH_HIT_RE", source)

	def test_a_structural_suggestion_scores_like_an_edit(self):
		tool = self._tool([_item("a")], suggestions=[{"id": "brew:h:s", "kind": "structural",
			"title": "Add the quarantine task", "target_files": [],
			"structural": {"op": "task_add", "subjects": [{"type": "cask", "name": "codex"}],
				"manifest": None, "from": None,
				"to": {"type": "task", "name": "setup.sh:quarantine"},
				"anchor": {"file": "setup.sh"}}}])
		self.assertIn("proposed_edit", assemble.score_tool(tool)[1])

	def test_why_truncates_on_a_word_boundary(self):
		long = "word " * 80
		tool = self._tool([_item("a", title=long.strip(), severity="warning",
			local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}]))])
		why, source, ref = assemble._highlight_why_parts(tool)
		self.assertLessEqual(len(why), assemble._WHY_MAX)
		self.assertTrue(why.endswith("…"))
		self.assertEqual(source, "item_local_other")
		self.assertEqual(ref, tool["items"][0]["id"])

	def test_a_security_bucket_prefers_a_non_security_item(self):
		"""A highlight slot spent restating the card's own security column is
		a slot wasted."""
		tool = self._tool([
			_item("cve", tags=["security"], severity="notable",
				title="A security line", security=_sec("CVE-2026-1111")),
			_item("other", tags=["feature"], severity="notable", title="A feature line"),
		])
		self.assertEqual(tool["review_bucket"], "security_mixed")
		why, source, _ = assemble._highlight_why_parts(tool)
		self.assertEqual(why, "A feature line")
		self.assertEqual(source, "item_other")

	def test_a_security_item_is_still_the_fallback(self):
		tool = self._tool([_item("cve", tags=["security"], severity="notable",
			title="A security line", security=_sec("CVE-2026-1111"))])
		why, source, _ = assemble._highlight_why_parts(tool)
		self.assertEqual(why, "A security line")
		self.assertEqual(source, "item_security")

	def test_a_highlight_restating_the_security_column_yields_its_slot(self):
		"""The de-duplication is on the item id now, never on prose — the `why`
		has already been truncated at 220 chars, so a text comparison silently
		fails on a longer title."""
		tools = []
		for i in range(3):
			tools.append(build_one(
				_cand(f"brew:d{i}", f"d{i}", "brew", "1.0.0", "2.0.0"),
				{"id": f"brew:d{i}", "links": [], "items": [
					_item("cve", tags=["security"], severity="warning",
						title="The one line that matters " + "x" * 300,
						security=_sec(f"CVE-2026-111{i}", "critical", "vendor"),
						local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}])),
				]}))
		for tool in tools:
			self.assertTrue(tool["security"]["display_item_ids"])
		with contextlib.redirect_stderr(io.StringIO()) as err:
			highlights = assemble.build_highlights(tools)
		self.assertEqual(highlights, [])
		self.assertIn("restates security item", err.getvalue())

	def test_a_memory_proposal_is_not_an_authored_action(self):
		"""`REDESIGN.md` §O: a memory proposal changes what we REMEMBER, an
		action proposal changes the user's system. Only the second scores
		`proposed_edit`, and only the second may push a tool toward a decision.
		`watch_item_proposed` keeps its own 25 points and gets no `method-note`
		twin — the same rule, expressed in the highlight surface."""
		for kind, want_action in (("edit", True), ("structural", True),
				("watch-item", False), ("method-note", False),
				("upgrade", False), ("edits", True)):
			with self.subTest(kind=kind):
				self.assertEqual(assemble.is_action_suggestion({"kind": kind}), want_action)

	def test_an_unrecognized_suggestion_kind_fails_closed(self):
		"""The predicate is a negation on purpose. The positive form ("is the
		kind one of edit/structural") fails OPEN: a typo'd `"edits"` carrying a
		real config edit falls through and scores nothing."""
		tool = self._tool([_item("a")], suggestions=[{"id": "brew:h:s", "kind": "edits",
			"title": "A real config edit with a typo'd kind", "target_files": []}])
		self.assertIn("proposed_edit", assemble.score_tool(tool)[1])

	def test_why_sources_lists_every_branch_that_can_produce_one(self):
		"""`_WHY_SOURCES` is the published vocabulary of `highlights[].why_source`
		(references/schemas.md §1.11). A branch that can fire and is not listed
		ships a value no consumer was told about."""
		listed = set(assemble._WHY_SOURCES)
		import inspect
		body = inspect.getsource(assemble._highlight_why_parts) \
			+ inspect.getsource(assemble._item_source)
		produced = set(re.findall(r'"(item_local_\w+|item_\w+|config_status|research_error|major_bump|none)"', body))
		produced |= {f"item_local_{k}" for k in ("security", "other")}
		produced |= {f"item_{k}" for k in ("security", "other")}
		self.assertEqual(produced - listed, set(), "a branch produces an unlisted why_source")
		self.assertEqual(listed - produced, set(), "a listed why_source no branch can produce")

	def test_a_repr_is_never_rendered_as_a_highlight_line(self):
		"""A title a checker nested one level too deep used to raise
		AttributeError out of build_highlights() and abort the report for all
		78 tools. Coercing it with str() is not the fix either — that renders
		the repr on the card."""
		tool = self._tool([_item("a", severity="warning",
			local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}]),
			title={"text": "nested one level too deep"})])
		with contextlib.redirect_stderr(io.StringIO()) as err:
			why, source, ref = assemble._highlight_why_parts(tool)
		self.assertNotIn("nested one level too deep", why)
		self.assertNotIn("{", why)
		self.assertIn("not a string", err.getvalue())

	def test_a_malformed_title_costs_one_item_not_the_whole_branch(self):
		"""The worst local item's title being unreadable used to drop the tool
		out of the local-finding branch entirely, down to `major_bump` — with
		three good findings sitting behind it."""
		tool = self._tool([
			_item("bad", severity="incompatible", title={"nested": "too deep"},
				local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}])),
			_item("good", severity="warning", title="The next-worst thing we know",
				local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}])),
		])
		with contextlib.redirect_stderr(io.StringIO()):
			why, source, ref = assemble._highlight_why_parts(tool)
		self.assertEqual(why, "The next-worst thing we know")
		self.assertEqual(source, "item_local_other")
		self.assertEqual(ref, [i["id"] for i in tool["items"] if i["title"] == why][0])

	def test_the_cap_is_eight(self):
		tools = []
		for i in range(12):
			tools.append(build_one(_cand(f"brew:t{i:02d}", f"t{i:02d}", "brew", "1.0.0", "1.0.1"),
				{"id": f"brew:t{i:02d}", "links": [], "items": [
					_item("a", tags=["fix"], severity="incompatible",
						title=f"Breaks thing {i}",
						local=_local("reaches", "risk", evidence=[{"path": "Brewfile"}]))]}))
		with contextlib.redirect_stderr(io.StringIO()):
			self.assertEqual(len(assemble.build_highlights(tools)), 8)


# ── 6. The assembly ↔ page contract ─────────────────────────────────────────
class PageContractTests(unittest.TestCase):
	"""The report's producer and its only consumer both speak the item model.
	Prose agreement between the two sides has already drifted once without a
	single test failing."""

	@classmethod
	def setUpClass(cls):
		with open(TEMPLATE, encoding="utf-8") as fh:
			cls.template = fh.read()

	def _js_object(self, name):
		match = re.search(r"const " + name + r" = \{(.*?)\n\t\t\};", self.template, re.S)
		self.assertIsNotNone(match, f"{name} not found in the template")
		body = match.group(1)
		out = {}
		for key, value in re.findall(r"([A-Za-z_]+)\s*:\s*'([a-z_]+)'", body):
			out[key] = value
		for key, value in re.findall(r"([A-Za-z_]+)\s*:\s*(\d+)", body):
			out[key] = int(value)
		return out

	def test_the_tag_group_map_agrees_with_the_contract(self):
		self.assertEqual(self._js_object("GROUP_OF_TAG"), model.GROUP_OF_TAG)

	def test_the_group_precedence_agrees_with_the_contract(self):
		match = re.search(r"const CATEGORY_ORDER = \[(.*?)\];", self.template)
		order = tuple(re.findall(r"'([a-z]+)'", match.group(1)))
		self.assertEqual(order, model.GROUP_PRECEDENCE)

	def test_the_cve_ordering_rank_agrees_tier_for_tier(self):
		"""`unknown` outranks `low` here and loses to it when resolving — the
		two tables are not interchangeable (items.py). The page renders a list
		the contract already ordered, so a disagreement silently reshuffles
		it."""
		rank = self._js_object("NOTABLE_RANK")
		by_tier = {}
		for name, value in rank.items():
			by_tier.setdefault(value, set()).add(name)
		# The page's table covers both vocabularies; project it back onto the
		# CVE ratings and it must be the contract's order, worst first.
		cve_order = sorted(model.CVE_ORDER_RANK, key=lambda k: -model.CVE_ORDER_RANK[k])
		page_order = [r for _, names in sorted(by_tier.items())
			for r in cve_order if r in names]
		self.assertEqual(page_order, cve_order)

	def test_the_page_reads_no_legacy_array(self):
		"""Every one of these would render as `undefined` against a schema-2
		report — silently, and looking entirely correct."""
		for gone in ("tool.headliners", "tool.relevancy", "tool.context",
				"sec.notable", "n.affects_me", "r.motivating_change", "source_ref"):
			self.assertNotIn(gone, self.template, gone)

	def test_the_page_reads_the_body_field_not_detail(self):
		"""WP1 renamed the overflow field to `body` and pinned it in
		contract.json precisely so this could not be got wrong silently."""
		self.assertIn("item.body", self.template)
		self.assertIn("display_item_ids", self.template)

	def test_a_finding_sources_items_are_all_local_to_the_page(self):
		"""`maxSeverity()` drives `data-max-severity`, the "relevant only"
		filter and the severity dropdown. A brew-health or skill-drift card is a
		statement about THIS install in its entirety, so every item on it counts
		— including a checker's own items on an ENRICHED finding, which need no
		`local` block. Without the exemption a warning-severity health finding
		with `local: null` gets an empty `data-max-severity`, vanishes from both
		filters, and floors to `info` on its Overview band."""
		fn = re.search(r"function maxSeverity\(tool\) \{(.*?)\n\t\t\}", self.template, re.S)
		self.assertIsNotNone(fn)
		self.assertIn("isNonVersion(tool)", fn.group(1),
			"maxSeverity() lost its finding-source exemption")

	def test_the_tag_line_shows_a_same_group_pair_and_a_lone_unknown_tag(self):
		"""Two ways to render nothing where something was needed.

		Filtering by GROUP hides a second tag that maps to the same one —
		`["fix", "breaking"]` renders no `breaking` anywhere, and `breaking` is
		the most decision-relevant tag in the set. And an item whose only tag is
		unrecognised must still say so: the validator kept that tag deliberately
		(E-TAG-UNKNOWN reports and keeps), the item lands in Notes, and nothing
		else on the page surfaces it."""
		block = re.search(
			r"const tags = tagsOf\(item\);\n(.*?)const tagsHtml", self.template, re.S)
		self.assertIsNotNone(block, "the tag line stopped reading tagsOf(item)")
		body = block.group(1)
		self.assertNotIn("GROUP_OF_TAG[t] !== cat", body,
			"the tag line is filtered by group again — a same-group second tag is hidden")
		self.assertIn("tags.length > 1", body,
			"a multi-tag item must render its tags")
		self.assertIn("!GROUP_OF_TAG[t]", body,
			"a lone unrecognised tag must still render — nothing else surfaces E-TAG-UNKNOWN")

	def test_the_renderer_refuses_a_schema_one_report(self):
		with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "render.py"),
				encoding="utf-8") as fh:
			self.assertIn('report.get("schema_version") != 2', fh.read())


# ── 7. Degradation: per file, per entry, per tool ───────────────────────────
class LoadResearchDegradationTests(unittest.TestCase):
	"""`load_research` reads ~22 agent-written files. One hostile shape in one
	of them must cost that file or that entry — never the run."""

	HOSTILE_ENTRIES = [
		"a bare string",
		42,
		None,
		True,
		[],
		{},                                   # no id
		{"id": None},
		{"id": 42},
		{"id": ""},
		{"id": ["brew:list-id"]},
	]

	def _load(self, entries):
		with tempfile.TemporaryDirectory() as td:
			with open(os.path.join(td, "01.json"), "w", encoding="utf-8") as fh:
				json.dump(entries, fh)
			with contextlib.redirect_stderr(io.StringIO()) as err:
				return assemble.load_research(td), err.getvalue()

	def test_one_hostile_entry_never_costs_the_good_ones(self):
		for hostile in self.HOSTILE_ENTRIES:
			with self.subTest(repr(hostile)):
				by_id, _ = self._load([hostile, {"id": "brew:good", "items": []}])
				self.assertEqual(list(by_id), ["brew:good"])

	def test_a_file_of_nothing_but_garbage_is_empty_not_fatal(self):
		by_id, _ = self._load(self.HOSTILE_ENTRIES)
		self.assertEqual(by_id, {})

	def test_the_warning_names_the_file_so_it_is_actionable(self):
		with tempfile.TemporaryDirectory() as td:
			with open(os.path.join(td, "07-networking.json"), "w", encoding="utf-8") as fh:
				json.dump(["a bare string"], fh)
			with contextlib.redirect_stderr(io.StringIO()) as err:
				assemble.load_research(td)
			self.assertIn("07-networking.json", err.getvalue())

	def test_an_unreadable_file_costs_that_file_and_no_other(self):
		with tempfile.TemporaryDirectory() as td:
			with open(os.path.join(td, "01-bad.json"), "wb") as fh:
				fh.write(b'[{"id": "brew:x", "title": "caf\xc3')   # truncated UTF-8
			with open(os.path.join(td, "02-good.json"), "w", encoding="utf-8") as fh:
				json.dump([{"id": "brew:good", "items": []}], fh)
			with contextlib.redirect_stderr(io.StringIO()) as err:
				by_id = assemble.load_research(td)
			self.assertEqual(list(by_id), ["brew:good"])
			self.assertIn("01-bad.json", err.getvalue())

	def test_a_missing_research_dir_is_a_warning_not_a_crash(self):
		with contextlib.redirect_stderr(io.StringIO()) as err:
			self.assertEqual(assemble.load_research("/no/such/dir"), {})
		self.assertIn("research_error", err.getvalue())


class ItemAssemblyDegradationTests(unittest.TestCase):
	"""The item-model twin of LoadResearchDegradationTests: one hostile shape
	*inside* an entry's `items[]` must cost that item's checks, never the tool
	and never the run. The item itself is always kept — deleting it is the one
	thing this layer may not do (criterion 1)."""

	HOSTILE_ITEMS = [
		"a bare string",
		42,
		None,
		[],
		{},                                                  # nothing at all
		{"anchor": None},
		{"anchor": "not-an-object"},
		{"anchor": {"kind": "cve", "value": "CVE-26-1"}},     # malformed id
		{"anchor": {"kind": "nonsense", "value": "x"}},
		{"title": 42, "anchor": {"kind": "none", "slug": "a"}},
		{"title": "t", "tags": "security", "anchor": {"kind": "none", "slug": "b"}},
		{"title": "t", "tags": [["security"]], "anchor": {"kind": "none", "slug": "c"}},
		{"title": "t", "severity": ["warning"], "anchor": {"kind": "none", "slug": "d"}},
		{"title": "t", "local": "not-an-object", "anchor": {"kind": "none", "slug": "e"}},
		{"title": "t", "change": 7, "anchor": {"kind": "none", "slug": "f"}},
		{"title": "t", "security": "not-an-object", "tags": ["security"],
			"anchor": {"kind": "none", "slug": "g"}},
		{"title": "t", "local": {"evidence": "Brewfile"}, "anchor": {"kind": "none", "slug": "h"}},
		{"title": "t", "local": {"evidence": [{"path": 7}]}, "anchor": {"kind": "none", "slug": "i"}},
	]

	def _run(self, items):
		return assemble_session(
			{"generated_at": "2026-08-22T11:33:44Z", "machine": {},
				"brew": [_cand("brew:hostile", "hostile", "brew", "1.0.0", "1.0.1"),
					_cand("brew:fine", "fine", "brew", "1.0.0", "1.0.1")]},
			[{"id": "brew:hostile", "links": [], "items": items},
				{"id": "brew:fine", "links": [], "items": [_item("a")]}])

	def test_one_hostile_item_never_costs_the_tool_or_the_run(self):
		for hostile in self.HOSTILE_ITEMS:
			with self.subTest(repr(hostile)[:60]):
				report, _ = self._run([hostile, _item("good")])
				ids = [t["id"] for t in report["tools"]]
				self.assertEqual(ids, ["brew:hostile", "brew:fine"])
				fine = report["tools"][1]
				self.assertEqual(len(fine["items"]), 1)

	def test_a_hostile_item_is_kept_somewhere_it_can_be_read(self):
		"""Either normalized onto items[] with an assigned id, or quarantined
		verbatim. Never gone: a human would have read it, and convergence has
		to be able to address it."""
		for hostile in self.HOSTILE_ITEMS:
			with self.subTest(repr(hostile)[:60]):
				report, _ = self._run([hostile])
				tool = report["tools"][0]
				blob = json.dumps(tool["items"]) + json.dumps(tool["quarantine"])
				self.assertTrue(tool["items"] or tool["quarantine"], repr(hostile))
				if isinstance(hostile, dict) and isinstance(hostile.get("title"), str):
					self.assertIn(hostile["title"], blob)

	def test_a_whole_corpus_of_hostile_items_still_produces_a_report(self):
		report, _ = self._run(list(self.HOSTILE_ITEMS))
		self.assertEqual(len(report["tools"]), 2)
		self.assertEqual(report["summary"]["total_outdated"], 2)
		self.assertTrue(report["_warn"].strip())

	def test_a_hostile_item_never_promotes_its_tool(self):
		"""The `brew:libpq` shape by another route: a tool whose corpus could
		not be read is the last thing that should be auto-approved."""
		for hostile in self.HOSTILE_ITEMS:
			with self.subTest(repr(hostile)[:60]):
				report, _ = self._run([hostile])
				tool = report["tools"][0]
				if tool["validator_error"]:
					self.assertEqual(tool["security"]["impact"], "unknown")
					self.assertFalse(any(s["pre_accept"] for s in tool["suggestions"]))

	def test_every_hostile_item_is_reported_by_code(self):
		for hostile in self.HOSTILE_ITEMS:
			with self.subTest(repr(hostile)[:60]):
				report, _ = self._run([hostile])
				self.assertTrue(report["_warn"].strip(), repr(hostile))
				self.assertTrue(report["tools"][0]["spec_violations"], repr(hostile))


class RunBoundaryDegradationTests(unittest.TestCase):
	"""One bad unit must never cost the report."""

	def test_a_malformed_section_costs_that_section_only(self):
		collect = dict(COLLECT)
		collect["mise"] = "not an array"
		report, err = assemble_session(collect, RESEARCH)
		self.assertNotIn("mise:uv", [t["id"] for t in report["tools"]])
		self.assertTrue(any(t["id"] == "brew:openssh" for t in report["tools"]))
		self.assertIn("mise", report["_log"])

	def test_a_candidate_with_no_identity_costs_one_card(self):
		collect = json.loads(json.dumps(COLLECT))
		collect["brew"].append({"name": "no-id", "source": "brew"})
		collect["brew"].append({"id": "brew:no-source", "name": "x"})
		report, _ = assemble_session(collect, RESEARCH)
		self.assertEqual(len([t for t in report["tools"] if t["source"] == "brew"]), 4)

	def test_a_malformed_finding_block_costs_the_findings_not_the_run(self):
		collect = json.loads(json.dumps(COLLECT))
		collect["skill_drift"] = "not an object"
		report, _ = assemble_session(collect, RESEARCH)
		self.assertEqual(report["summary"]["skill_drift_count"], 0)
		self.assertEqual(report["summary"]["total_outdated"], 5)

	def test_an_absent_finding_key_still_reports_a_zero_count(self):
		collect = json.loads(json.dumps(COLLECT))
		del collect["skill_drift"]
		report, _ = assemble_session(collect, RESEARCH)
		self.assertEqual(report["summary"]["skill_drift_count"], 0)
		self.assertEqual(report["summary"]["by_bucket"]["attention"]
			+ report["summary"]["by_bucket"]["routine"]
			+ report["summary"]["by_bucket"]["security_auto"]
			+ report["summary"]["by_bucket"]["security_mixed"], len(report["tools"]))

	def test_a_collect_json_that_is_not_an_object_exits_saying_so(self):
		with tempfile.TemporaryDirectory() as tmp:
			session = os.path.join(tmp, "tool-update-review-x")
			os.makedirs(session)
			with open(os.path.join(session, "collect.json"), "w", encoding="utf-8") as fh:
				json.dump(["not", "an", "object"], fh)
			argv = sys.argv
			sys.argv = ["assemble.py", session]
			try:
				with contextlib.redirect_stderr(io.StringIO()) as err:
					with self.assertRaises(SystemExit) as ctx:
						assemble.main()
			finally:
				sys.argv = argv
			self.assertEqual(ctx.exception.code, 1)
			self.assertIn("not a JSON object", err.getvalue())

	def test_an_unreadable_repo_context_falls_back_to_the_placeholder(self):
		report, _ = assemble_session(COLLECT, RESEARCH)
		self.assertEqual(set(report["repo_context"]), {"macos_setup", "dotfiles"})

	def test_the_python39_interpreter_can_run_it(self):
		"""assemble.py targets the bare `python3` on a freshly imaged Mac."""
		if not os.path.exists("/usr/bin/python3"):
			self.skipTest("no /usr/bin/python3")
		here = os.path.dirname(os.path.abspath(__file__))
		proc = subprocess.run(["/usr/bin/python3", "-c",
			f"import sys; sys.path.insert(0, {here!r}); import assemble, validate_items"],
			capture_output=True, text=True)
		self.assertEqual(proc.returncode, 0, proc.stderr)


# ── 8. Pinning the reviewed version (WP5/I2 — references/apply.md §Executing
#      Upgrade Suggestions, references/schemas.md §1.6) ─────────────────────
# The defect this closes: `command` used to be a bare `brew upgrade
# {name}`/`mise upgrade {name}` with no reference to what was actually
# reviewed, so running it later installed whatever the package manager
# considered "latest" *at apply time* — not the version a human approved.
# mise can pin an exact version as a CLI argument (confirmed against
# upstream docs: `mise upgrade tiny@3.0.1`); brew/cask cannot do this
# generically for an arbitrary formula/cask, so they stay unpinned in
# `command` and rely on scripts/check_pin.py's preflight/verify instead
# (tested separately in test_check_pin.py).
class PinnedVersionTests(unittest.TestCase):
	def test_mise_command_pins_the_reviewed_version(self):
		command, auto_runnable, manual_reason, version_pinned = assemble.upgrade_command_and_runnable(
			"mise", "node", "24.6.0")
		self.assertEqual(command, "mise upgrade node@24.6.0")
		self.assertTrue(auto_runnable)
		self.assertIsNone(manual_reason)
		self.assertTrue(version_pinned)

	def test_mise_without_a_version_falls_back_to_the_old_unpinned_command(self):
		# validate_items.py (pre-assembly) calls this with no version at all —
		# omitting it must keep working exactly as before, never raise.
		command, auto_runnable, manual_reason, version_pinned = assemble.upgrade_command_and_runnable(
			"mise", "node")
		self.assertEqual(command, "mise upgrade node")
		self.assertTrue(auto_runnable)
		self.assertFalse(version_pinned)

	def test_mise_name_already_carrying_an_at_sign_refuses_to_pin(self):
		# A pin that would produce "mise upgrade node@20@24.6.0" is worse than
		# no pin: version_pinned=True on a command not established to be
		# well-formed is a guarantee that reads as one without being one.
		command, auto_runnable, manual_reason, version_pinned = assemble.upgrade_command_and_runnable(
			"mise", "node@20", "24.6.0")
		self.assertEqual(command, "mise upgrade node@20")
		self.assertNotIn("24.6.0", command)
		self.assertTrue(auto_runnable)
		self.assertFalse(version_pinned)

	def test_mise_backend_qualified_name_pins_normally(self):
		# mise's own qualifier syntax uses ":" (npm:prettier), never "@" —
		# confirmed against upstream docs (`mise use -g npm:prettier@3`) — so
		# this shape is safe to pin exactly like a bare tool name.
		command, auto_runnable, manual_reason, version_pinned = assemble.upgrade_command_and_runnable(
			"mise", "npm:prettier", "3.3.1")
		self.assertEqual(command, "mise upgrade npm:prettier@3.3.1")
		self.assertTrue(version_pinned)

	def test_missing_latest_version_refuses_to_synthesize_a_runnable_baseline(self):
		# A verify step compared against a null target_version can never pass
		# — worse than no check, because it would permanently report a
		# correctly-landed upgrade as failed. Refuse to run or pin at all.
		tool = build(_cand("mise:ghost", "ghost", "mise", "1.0.0", None), {"headliners": []})
		baseline = assemble.baseline_upgrade(tool)
		self.assertIsNone(baseline["command"])
		self.assertFalse(baseline["auto_runnable"])
		self.assertFalse(baseline["version_pinned"])
		self.assertIsNone(baseline["target_version"])
		self.assertIn("latest_version", baseline["manual_reason"])

	def test_brew_and_cask_cannot_pin_a_version_in_the_command(self):
		# Homebrew has no general `brew install name@version` for an arbitrary
		# formula/cask — passing a version must never be silently swallowed
		# into looking pinned when it structurally cannot be.
		for source, name, expected_command in (
			("brew", "podman", "brew upgrade podman"),
			("cask", "wireshark-app", "brew upgrade --cask wireshark-app"),
		):
			with self.subTest(source):
				command, auto_runnable, manual_reason, version_pinned = assemble.upgrade_command_and_runnable(
					source, name, "5.5.1")
				self.assertEqual(command, expected_command)
				self.assertTrue(auto_runnable)
				self.assertFalse(version_pinned)
				self.assertNotIn("5.5.1", command)

	def test_baseline_suggestion_carries_target_version_always(self):
		# Every kind:"upgrade" baseline records the reviewed version on the
		# suggestion itself — apply must never have to reach back into a
		# different part of report.json to know what it is pinning to.
		tool = build(_cand("mise:node", "node", "mise", "24.5.0", "24.6.0"), {"headliners": []})
		baseline = assemble.baseline_upgrade(tool)
		self.assertEqual(baseline["target_version"], "24.6.0")
		self.assertEqual(baseline["command"], "mise upgrade node@24.6.0")
		self.assertTrue(baseline["version_pinned"])

	def test_baseline_suggestion_records_unpinned_for_brew_and_cask(self):
		for source, name in (("brew", "podman"), ("cask", "wireshark-app")):
			with self.subTest(source):
				tool = build(_cand(f"{source}:{name}", name, source, "1.0.0", "2.0.0"), {"headliners": []})
				baseline = assemble.baseline_upgrade(tool)
				self.assertEqual(baseline["target_version"], "2.0.0")
				self.assertFalse(baseline["version_pinned"])
				self.assertNotIn("2.0.0", baseline["command"])

	def test_manual_only_baseline_still_carries_target_version(self):
		# macos/standalone never get a runnable command, but the reviewed
		# version is still recorded — the manual polling step in
		# references/apply.md compares against it.
		tool = build(_cand("macos:Safari", "Safari", "macos", "15.6", "15.7"), {"headliners": []})
		baseline = assemble.baseline_upgrade(tool)
		self.assertEqual(baseline["target_version"], "15.7")
		self.assertFalse(baseline["version_pinned"])
		self.assertIsNone(baseline["command"])


if __name__ == "__main__":
	unittest.main()
