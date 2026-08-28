#!/usr/bin/env python3
"""
test_assemble.py — the test matrix for assemble.py's derived fields.
Usage: python3 test_assemble.py [-v]

Stdlib `unittest` only (no pytest, no fixtures directory, no network) so it
runs on the same bare python3 assemble.py itself targets. Four groups, in the
order references/assembly.md documents the computations:

1. Version classification — the worked-example table that motivated
   compute_version_delta(). Every row is a real current→latest pair seen in a
   live run; the point of writing them down is that the next person changing
   the classifier does not have to re-derive "is `7.99 → 7.991` a minor?" from
   scratch.
2. Semantic classification — eleven minimal Tool fixtures (headliner/relevancy
   category+severity shapes distilled from real research/*.json entries) run
   through build_tool()/build_health_tool(), asserting the whole
   security → risk_level → review_bucket → pre_accept chain end to end rather
   than each function in isolation, since the bugs live in their interaction.
3. Regex cases — the CVE-id and CVE-claim patterns, including the two
   real-world false positives their bounds exist to reject.
4. Shape drift — research is agent-written free-form JSON, so every array it
   supplies is fed back in the wrong shape sooner or later. One report is
   assembled from ~22 files covering ~77 tools, so the contract is that a
   drifted file costs one warned-about tool, never the whole run.
5. Report-level invariants — a synthetic session dir assembled through main(),
   so the ordering constraints (finalize_tool before the id-uniqueness pass,
   build_highlights after it) are exercised, not just asserted in prose.
6. CVE severity, `notable` security items, and the noise floor — the security
   block research supplies and assembly validates. Its two load-bearing
   properties are structural: `sum(severity_counts) == cve_count` holds because
   the rollup iterates the ids rather than the ratings, and no decision
   (`review_bucket`, `pre_accept`) can move as a result of the noise floor
   deleting items.
7. The assembly ↔ page contract for `security.notable[]` — the one place where
   the report's producer and its only consumer both sort the same array, and
   where `[]` and an absent key have to keep meaning two different things.
   These read `assets/report-template.html` because prose agreement between
   the two sides has already drifted once without a single test failing.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble  # noqa: E402


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
		# A brew-health finding has no versions at all, and a macos candidate's
		# current_version is the running OS version rather than that update's
		# (references/schemas.md §1.3) — a delta from either would be fiction.
		for source in ("brew-health", "macos"):
			with self.subTest(source=source):
				self.assertEqual(
					assemble.compute_version_delta("15.6", "15.7", source),
					("unknown", "none", "no version delta for this source"))

	def test_no_numeric_component(self):
		self.assertEqual(assemble.compute_version_delta("stable", "latest", "brew"),
			("unknown", "none", "no numeric component"))

	def test_equal_versions(self):
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


# ── 2. Semantic classification (references/assembly.md §Security Extraction,
#      §Review Buckets and Pre-Accept) ────────────────────────────────────────
def _hl(category, severity, text="A changelog fact."):
	return {"text": text, "category": category, "severity": severity}


def _rel(category, severity, summary="Affects this setup.", detail=""):
	return {
		"category": category,
		"severity": severity,
		"summary": summary,
		"detail": detail,
		"evidence": [],
		"motivating_change": "release notes entry",
	}


def _cand(tool_id, name, source, current, latest, **extra):
	cand = {"id": tool_id, "name": name, "source": source,
		"current_version": current, "latest_version": latest, "pinned": False}
	cand.update(extra)
	return cand


def build(candidate, research):
	"""build_tool() with stderr silenced — several fixtures deliberately trip
	the needs_attention/equal-version warnings."""
	with contextlib.redirect_stderr(io.StringIO()):
		return assemble.build_tool(candidate, research)


# Every key a fixture expectation dict may carry. Asserted as an allowlist so
# a typo'd key fails loudly instead of being silently never checked — the
# failure mode that makes a green matrix meaningless.
EXPECT_KEYS = {
	"has_security", "security_only", "impact", "cve_count", "cve_claimed_count",
	"review_bucket", "risk_level", "version_delta", "version_scheme",
	"suggestions", "pre_accept", "needs_sudo",
}


# Each fixture: (label, candidate, research_obj, expectations)
def _fixtures():
	return [
		(
			"S1 brew:duckdb — security-only, nothing touching this setup",
			_cand("brew:duckdb", "duckdb", "brew", "1.4.2", "1.4.3"),
			{
				"headliners": [_hl("security", "notable"), _hl("security", "info")] +
					[_hl("fixes", "notable")] * 4,
				"relevancy": [_rel("security", "notable")],
				"config_status": {"state": "up_to_date", "detail": "Reviewed at 1.4.2.", "evidence": []},
			},
			{"has_security": True, "security_only": True, "impact": "none",
				"review_bucket": "security_auto", "risk_level": "low", "pre_accept": True},
		),
		(
			"S2 brew:libpq — security_auto overriding an elevated risk_level",
			_cand("brew:libpq", "libpq", "brew", "18.4", "18.6"),
			{
				"headliners": [_hl("security", "warning"), _hl("security", "notable"),
					_hl("security", "notable"), _hl("fixes", "notable"),
					_hl("fixes", "info"), _hl("notes", "info")],
				"relevancy": [_rel("security", "warning")],
			},
			{"has_security": True, "security_only": True, "impact": "none",
				"review_bucket": "security_auto", "risk_level": "elevated", "pre_accept": True},
		),
		(
			"S3 cask:wireshark-app — security_auto cask, needs_sudo, claim without ids",
			_cand("cask:wireshark-app", "wireshark-app", "cask", "4.6.5", "4.6.6"),
			{
				"headliners": [_hl("security", "warning", "28 security advisories fixed (wnpa-sec-2026-64)"),
					_hl("notes", "info"), _hl("notes", "info"), _hl("fixes", "info")],
				"relevancy": [_rel("security", "warning")],
			},
			{"has_security": True, "security_only": True, "impact": "none",
				"review_bucket": "security_auto", "pre_accept": True,
				"needs_sudo": True, "cve_count": 0, "cve_claimed_count": 28},
		),
		(
			"S4 brew:rsync — security plus notes/notable and a watch-item",
			_cand("brew:rsync", "rsync", "brew", "3.4.1", "3.4.2"),
			{
				"headliners": [
					_hl("security", "warning", "Fixes 33 CVEs including CVE-2026-53789 and CVE-2026-53790"),
					_hl("security", "warning", "CVE-2026-53791, CVE-2026-53792"),
					_hl("security", "notable", "CVE-2026-53793 CVE-2026-53794 CVE-2026-53795"),
					_hl("notes", "notable"), _hl("notes", "notable"), _hl("fixes", "info"),
				],
				"relevancy": [_rel("security", "notable"), _rel("security", "warning")],
				"suggestions": [{
					"id": "brew:rsync:watch-protocol", "kind": "watch-item",
					"title": "Watch: protocol negotiation", "target_files": [], "command": None,
					"auto_runnable": False, "needs_sudo": False, "rationale": "",
					"motivating_link": None, "diff_preview": None,
					"watch_topic": "protocol negotiation", "watch_note": "…",
				}],
			},
			{"has_security": True, "security_only": False, "impact": "possible",
				"review_bucket": "security_mixed", "pre_accept": False,
				"cve_count": 7, "cve_claimed_count": 33},
		),
		(
			"S5 mise:node — security plus features, pre-accepts via risk low",
			_cand("mise:node", "node", "mise", "24.5.0", "24.6.0"),
			{
				"headliners": [
					_hl("security", "warning", "Fixes 11 CVEs: CVE-2026-1001, CVE-2026-1002, CVE-2026-1003"),
					_hl("security", "notable", "CVE-2026-1004 CVE-2026-1005 CVE-2026-1006 CVE-2026-1007"),
					_hl("features", "notable"), _hl("features", "notable"),
					_hl("features", "info", "CVE-2026-1008, CVE-2026-1009, CVE-2026-1010"),
					_hl("notes", "info"),
				],
				"relevancy": [],
			},
			{"has_security": True, "security_only": False, "impact": "none",
				"review_bucket": "security_mixed", "risk_level": "low", "pre_accept": True,
				"cve_count": 10, "cve_claimed_count": 11},
		),
		(
			"S6 cask:slack — vendor silent about security",
			_cand("cask:slack", "slack", "cask", "4.45.0", "4.46.0"),
			{"headliners": [], "vendor_silent_categories": ["security"]},
			{"has_security": True, "security_only": False, "impact": "unknown",
				"review_bucket": "security_mixed", "risk_level": "low", "pre_accept": True},
		),
		(
			"S7 cask:claudebar — vendor publishes nothing, stays routine",
			_cand("cask:claudebar", "claudebar", "cask", "0.4.73", "0.4.81"),
			{"headliners": [], "vendor_silent_categories": ["fixes"]},
			{"has_security": False, "security_only": False, "impact": "unknown",
				"review_bucket": "routine", "risk_level": "low", "pre_accept": True},
		),
		(
			"S8 research failed — never pre-accepted",
			_cand("brew:ripgrep", "ripgrep", "brew", "14.1.0", "14.1.1"),
			None,
			{"has_security": False, "security_only": False, "impact": "unknown",
				"review_bucket": "attention", "risk_level": "elevated", "pre_accept": False},
		),
		(
			"S9 cask:codex — 0.x major with a breaking note",
			_cand("cask:codex", "codex", "cask", "0.144.6", "0.149.0"),
			{
				"headliners": [_hl("notes", "notable", "`codex exec --full-auto` was removed"),
					_hl("security", "notable"), _hl("security", "notable"),
					_hl("features", "notable"), _hl("features", "info"), _hl("features", "info")],
				"relevancy": [_rel("security", "notable"), _rel("features", "notable")],
			},
			{"has_security": True, "security_only": False, "impact": "possible",
				"review_bucket": "security_mixed", "risk_level": "elevated", "pre_accept": False,
				"version_delta": "major"},
		),
		(
			"S10 brew-health missing_dependency — structural finding",
			_cand("brew-health:missing_dependency:dtc", "Missing dependency: dtc", "brew-health", None, None,
				category="missing_dependency", severity="notable", expected=False,
				detail="An installed formula/cask is missing dependency `dtc`.",
				remediation={"command": "brew install dtc", "auto_runnable": True,
				"needs_sudo": False, "label": "Install dtc"}),
			{
				"headliners": [_hl("fixes", "warning")] + [_hl("fixes", "notable")] * 4 + [_hl("notes", "info")],
				"relevancy": [_rel("fixes", "warning")],
				"config_status": {"state": "needs_attention", "detail": "Brewfile lacks dtc.", "evidence": []},
				"suggestions": [{
					"id": "brew-health:missing_dependency:dtc:add-brewfile-line", "kind": "edit",
					"title": "Add dtc to the Brewfile", "target_files": [{"path": "Brewfile", "description": "add dtc"}],
					"rationale": "", "motivating_link": None, "diff_preview": None,
				}],
			},
			{"has_security": False, "security_only": False, "impact": "possible",
				"review_bucket": "attention", "risk_level": "elevated", "pre_accept": False,
				"version_delta": "unknown", "version_scheme": "none"},
		),
		(
			"S11 brew-health path_note — expected, nothing to decide",
			_cand("brew-health:path_note:gnu-utils-path", "GNU utils in PATH (intentional)", "brew-health", None, None,
				category="path_note", severity="info", expected=True,
				detail="Non-prefixed GNU utilities are earlier in PATH than the macOS defaults.",
				remediation=None),
			None,
			{"has_security": False, "security_only": False, "impact": "none",
				"review_bucket": "routine", "risk_level": "low", "suggestions": 0},
		),
	]


def _fixture(prefix):
	"""Look a fixture up by label prefix (S1…S11). A positional index silently
	retargets the moment a row is inserted above it."""
	matches = [f for f in _fixtures() if f[0].startswith(prefix + " ")]
	assert len(matches) == 1, f"{prefix}: expected 1 fixture, found {len(matches)}"
	return matches[0]


class SemanticClassificationTests(unittest.TestCase):
	def test_fixtures(self):
		for label, candidate, research, expect in _fixtures():
			with self.subTest(label):
				self.assertEqual(set(expect) - EXPECT_KEYS, set(), f"{label}: unknown expectation key")
				tool = build(candidate, research)
				sec = tool["security"]
				for key in ("has_security", "security_only", "impact"):
					if key in expect:
						self.assertEqual(sec[key], expect[key], f"{label}: security.{key}")
				for key in ("cve_count", "cve_claimed_count"):
					if key in expect:
						self.assertEqual(sec[key], expect[key], f"{label}: security.{key}")
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
						# No baseline at all (a brew-health finding) — nothing on
						# the tool may pre-accept.
						self.assertFalse(expect["pre_accept"], f"{label}: fixture expects a baseline that isn't there")
						self.assertFalse(any(s["pre_accept"] for s in tool["suggestions"]), f"{label}: pre_accept")
				if "needs_sudo" in expect:
					self.assertEqual(assemble.baseline_upgrade(tool)["needs_sudo"], expect["needs_sudo"], label)
				# Invariants that hold for every tool, no exceptions.
				self.assertEqual(sec["cve_count"], len(sec["cve_ids"]), f"{label}: cve_count is id-backed")
				self.assertEqual(sec["cve_ids"], sorted(set(sec["cve_ids"]), key=assemble.cve_sort_key), label)
				for sug in tool["suggestions"]:
					self.assertIn("pre_accept", sug, f"{label}: every suggestion carries pre_accept")

	def test_health_suggestions_never_pre_accept(self):
		# brew install dtc IS auto_runnable — it is excluded because a health
		# remediation is not a `:upgrade`-suffixed baseline, not because it
		# could not be run.
		_, candidate, research, _ = _fixture("S10")
		research = dict(research)
		research.pop("suggestions")
		tool = build(candidate, research)
		self.assertEqual([s["id"] for s in tool["suggestions"]], ["brew-health:missing_dependency:dtc:remediate"])
		self.assertTrue(tool["suggestions"][0]["auto_runnable"])
		self.assertFalse(tool["suggestions"][0]["pre_accept"])
		self.assertIsNone(assemble.baseline_upgrade(tool))

	def test_auto_runnable_false_blocks_pre_accept(self):
		# A macos/standalone baseline has no command the skill can run, so
		# "accepted" would claim a decision about something it cannot execute.
		tool = build(_cand("macos:Safari", "Safari", "macos", "15.6", "15.6"), {"headliners": [_hl("fixes", "info")]})
		baseline = assemble.baseline_upgrade(tool)
		self.assertFalse(baseline["auto_runnable"])
		self.assertFalse(baseline["pre_accept"])
		self.assertEqual(tool["review_bucket"], "attention")

	def test_security_only_macos_lands_in_mixed_not_auto(self):
		tool = build(_cand("macos:Safari", "Safari", "macos", "15.6", "15.6"),
			{"headliners": [_hl("security", "notable")]})
		self.assertTrue(tool["security"]["security_only"])
		self.assertEqual(tool["review_bucket"], "security_mixed")
		self.assertFalse(assemble.baseline_upgrade(tool)["pre_accept"])

	def test_research_error_never_reads_as_security_only(self):
		# "We know nothing" must never be reported as "nothing but security
		# fixes" — even with a CVE id somewhere in the shell of an object.
		tool = build(_cand("brew:foo", "foo", "brew", "1.0.0", "1.0.1"),
			{"headliners": [], "context": [{"title": "t", "detail": "CVE-2026-1111", "evidence": []}]})
		self.assertTrue(tool["security"]["has_security"])
		self.assertFalse(tool["security"]["security_only"])
		self.assertEqual(tool["security"]["impact"], "unknown")

	def test_pinned_is_impact_and_elevated(self):
		tool = build(_cand("brew:podman", "podman", "brew", "5.5.1", "5.5.2", pinned=True),
			{"headliners": [_hl("fixes", "info")]})
		self.assertEqual(tool["security"]["impact"], "possible")
		self.assertEqual(tool["risk_level"], "elevated")
		self.assertEqual(tool["review_bucket"], "attention")

	def test_non_security_headliner_warning_is_impact(self):
		# mise:rust in the live run: three fixes/warning headliners, no
		# relevancy — impact-shaped whether or not a subagent wrote it up.
		tool = build(_cand("mise:rust", "rust", "mise", "1.90.0", "1.91.0"),
			{"headliners": [_hl("fixes", "warning")], "relevancy": []})
		self.assertEqual(tool["security"]["impact"], "possible")

	def test_info_relevancy_alone_is_not_impact(self):
		tool = build(_cand("brew:jq", "jq", "brew", "1.8.0", "1.8.1"),
			{"headliners": [_hl("fixes", "info")], "relevancy": [_rel("fixes", "info")]})
		self.assertEqual(tool["security"]["impact"], "none")
		self.assertEqual(tool["review_bucket"], "routine")


# ── 3. Regexes (references/assembly.md §Security Extraction) ─────────────────
class RegexTests(unittest.TestCase):
	def test_cve_ids(self):
		cases = [
			("Fixes CVE-2026-12345 and CVE-2026-9", ["CVE-2026-12345"]),
			("cve-2025-0001", ["CVE-2025-0001"]),
			("CVE-2026-1234567890", ["CVE-2026-1234567890"]),
			("NOTCVE-2026-1234", []),
			("CVE-3026-1234", []),
			("CVE-2026-123", []),
			("https://nvd.nist.gov/vuln/detail/CVE-2026-4242", ["CVE-2026-4242"]),
		]
		for text, expected in cases:
			with self.subTest(text):
				self.assertEqual([m.upper() for m in assemble._CVE_RE.findall(text)], expected)

	def test_cve_claims(self):
		cases = [
			("Both 5.80 CVEs need a running service", []),
			("33 CVEs fixed in one release", ["33"]),
			("28 security advisories fixed (wnpa-sec-2026-64)", ["28"]),
			("fixes 1234 CVEs", []),
			("resolves twelve CVEs", []),
			("50 security vulnerabilities and 3 security fixes", ["50", "3"]),
		]
		for text, expected in cases:
			with self.subTest(text):
				self.assertEqual(assemble._CVE_CLAIM_RE.findall(text), expected)

	def test_claim_takes_the_max_never_the_sum(self):
		# Chrome: a 370-fix headliner plus a relevancy detail about a 68-fix
		# subset. Summing double-counts the subset.
		tool = build(_cand("cask:google-chrome", "google-chrome", "cask", "150.0.7871.129", "151.0.7922.174"),
			{"headliners": [_hl("security", "warning", "Shipped 370 security fixes")],
			"relevancy": [_rel("security", "warning", "68 CVEs affect the renderer", "")]})
		self.assertEqual(tool["security"]["cve_claimed_count"], 370)

	def test_claims_are_not_scanned_in_context(self):
		# stunnel's real context note: "Both 5.80 CVEs need a running service".
		# The lookbehind rejects it; scoping the claim scan away from context[]
		# is the second, independent guard.
		tool = build(_cand("brew:stunnel", "stunnel", "brew", "5.79", "5.80"),
			{"headliners": [_hl("security", "notable")],
			"context": [{"title": "Not a running service here",
			"detail": "Both 80 CVEs need a running service", "evidence": []}]})
		self.assertIsNone(tool["security"]["cve_claimed_count"])

	def test_cve_ids_are_scanned_in_context_but_not_links(self):
		tool = build(_cand("brew:rsync", "rsync", "brew", "3.4.1", "3.4.2"),
			{"headliners": [_hl("security", "notable")],
			"context": [{"title": "In range", "detail": "CVE-2026-53789 is in this range", "evidence": []}],
			"links": [{"type": "changelog", "label": "CVE-2026-99999", "url": None,
			"embedded_content": "CVE-2026-88888 from an out-of-range release"}]})
		self.assertEqual(tool["security"]["cve_ids"], ["CVE-2026-53789"])

	def test_cve_ids_sort_numerically_not_lexically(self):
		tool = build(_cand("brew:x", "x", "brew", "1.0.0", "1.0.1"),
			{"headliners": [_hl("security", "notable", "CVE-2026-12143 CVE-2026-9595 CVE-2025-9999")]})
		self.assertEqual(tool["security"]["cve_ids"], ["CVE-2025-9999", "CVE-2026-9595", "CVE-2026-12143"])


class EvidenceCitationTests(unittest.TestCase):
	"""_COMMIT_LIKE decides whether an evidence string is a checkable path or a
	commit citation with nothing to check — so a path it wrongly claims is a
	citation is never validated at all."""

	def test_a_path_segment_named_commit_is_still_checked(self):
		for path in ("dotfiles/commit-hooks/prepare-msg.sh", "config/commit/template"):
			with self.subTest(path):
				self.assertIs(assemble.evidence_exists(path, [os.sep + "nonexistent"]), False)

	def test_real_commit_citations_are_still_skipped(self):
		# Verbatim shapes from one live run — anchoring the pattern to the start
		# of the string instead would have turned all 19 of these into spurious
		# "evidence not found" warnings.
		for citation in (
			'macos-setup commit 5f25045 — "Add gh auth login to manual steps"',
			'dotfiles commit 1a55cef — "Add gh cli configs"',
			"git log -- Brewfile intel.Brewfile — no commit subject references azcopy",
			"a1b2c3d4e5f",
		):
			with self.subTest(citation):
				self.assertIsNone(assemble.evidence_exists(citation, [os.sep + "nonexistent"]))


# ── 4. Shape drift at the research boundary (references/assembly.md §Loading and Merging) ──
# Every shape below was either observed to abort the run outright or is the
# same class of drift. The assertion is never "we understood it" — only that
# one bad array degrades to one warned-about tool with a usable card.
DRIFT_SHAPES = [
	("headliners as a bare string", {"headliners": "no notable changes"}),
	("headliners as bare strings", {"headliners": ["a change", "another"]}),
	("headliners as a single object", {"headliners": {"text": "x", "category": "notes", "severity": "info"}}),
	("headliners null", {"headliners": None}),
	("relevancy null", {"relevancy": None}),
	("relevancy as a bare string", {"relevancy": "none found"}),
	("relevancy as bare strings", {"relevancy": ["nothing here"]}),
	("context as bare strings", {"context": ["some note"]}),
	("suggestions null", {"suggestions": None}),
	("links null", {"links": None}),
	("release_inventory as bare strings", {"release_inventory": ["1.0.0", "1.0.1"]}),
	("vendor_silent_categories as a bare string", {"vendor_silent_categories": "security"}),
	("vendor_silent_categories of objects", {"vendor_silent_categories": [{"category": "security"}]}),
	("mixed good and bad members", {"headliners": [_hl("fixes", "info"), "junk", None, 42]}),
]

# field → the member type the schema declares for it
_ARRAY_FIELDS = {
	"headliners": dict, "links": dict, "relevancy": dict, "context": dict,
	"release_inventory": dict, "suggestions": dict, "vendor_silent_categories": str,
}


class ShapeDriftTests(unittest.TestCase):
	def _assert_well_shaped(self, tool, label):
		for field, member in _ARRAY_FIELDS.items():
			self.assertIsInstance(tool[field], list, f"{label}: {field}")
			for item in tool[field]:
				self.assertIsInstance(item, member, f"{label}: {field} member")

	def test_drifted_shapes_still_build_a_usable_tool(self):
		for label, research in DRIFT_SHAPES:
			with self.subTest(label):
				tool = build(_cand("brew:drift", "drift", "brew", "1.0.0", "1.0.1"), dict(research))
				self._assert_well_shaped(tool, label)
				# Still fully classified, and still carrying a decidable baseline.
				self.assertIn(tool["review_bucket"], ("security_auto", "security_mixed", "attention", "routine"))
				baseline = assemble.baseline_upgrade(tool)
				self.assertIsNotNone(baseline, label)
				self.assertIn("pre_accept", baseline)
				assemble.score_tool(tool)   # the post-rename reader must not choke either

	def test_health_path_survives_the_same_drift(self):
		_, candidate, _, _ = _fixture("S10")
		for label, research in DRIFT_SHAPES:
			with self.subTest(label):
				tool = build(candidate, dict(research))
				self._assert_well_shaped(tool, label)
				self.assertEqual(tool["review_bucket"], "attention", label)
				# A drifted headliners array still leaves the finding's own detail
				# on the card, through the synthesized fallback.
				self.assertTrue(tool["headliners"], label)

	def test_good_members_survive_next_to_bad_ones(self):
		tool = build(_cand("brew:drift", "drift", "brew", "1.0.0", "1.0.1"),
			{"headliners": [_hl("security", "notable", "Fixes CVE-2026-4242"), "junk", None]})
		self.assertEqual(len(tool["headliners"]), 1)
		self.assertTrue(tool["security"]["has_security"])
		self.assertEqual(tool["security"]["cve_ids"], ["CVE-2026-4242"])

	def test_drift_is_warned_about_not_swallowed(self):
		buf = io.StringIO()
		with contextlib.redirect_stderr(buf):
			assemble.build_tool(_cand("brew:drift", "drift", "brew", "1.0.0", "1.0.1"),
				{"headliners": "no notable changes", "relevancy": [{"severity": "info"}, "junk"]})
		warnings = buf.getvalue()
		self.assertIn("headliners was str", warnings)
		self.assertIn("relevancy entry was str", warnings)

	def test_one_drifted_file_costs_one_tool_not_the_run(self):
		# The actual failure mode: 22 files in, one of them drifted, and the
		# report for all the other tools has to survive it.
		research = [dict(entry) for entry in RESEARCH]
		research[0] = {"id": "brew:openssh", "headliners": "no notable changes",
			"relevancy": None, "suggestions": None, "context": ["a note"]}
		report, stderr = assemble_session(COLLECT, research)
		self.assertEqual(len(report["tools"]), 6)
		drifted = next(t for t in report["tools"] if t["id"] == "brew:openssh")
		self.assertEqual(drifted["headliners"], [])
		self.assertEqual(drifted["review_bucket"], "attention")   # no content ⇒ never quietly routine
		self.assertFalse(drifted["suggestions"][0]["pre_accept"])
		self.assertIn("brew:openssh: headliners was str", stderr)
		# …and every other tool is untouched.
		podman = next(t for t in report["tools"] if t["id"] == "brew:podman")
		self.assertEqual(podman["version_delta"], "major")


# ── 5. Highlights and report-level invariants ───────────────────────────────
class HighlightScoringTests(unittest.TestCase):
	def test_bare_major_does_not_clear_the_threshold(self):
		tool = build(_cand("cask:google-chrome", "google-chrome", "cask", "150.0.7871.129", "151.0.7922.174"),
			{"headliners": [_hl("features", "info")]})
		score, reasons = assemble.score_tool(tool)
		self.assertEqual(reasons, ["major_bump"])
		self.assertEqual(score, 25)
		self.assertEqual(assemble.build_highlights([tool]), [])

	def test_watch_item_hit_phrase_scores(self):
		tool = build(_cand("cask:cursor", "cursor", "cask", "3.12.17", "3.17.8"),
			{"headliners": [_hl("features", "notable")],
			"relevancy": [_rel("features", "notable", "⚠ Watch item hit: shell integration changed again")]})
		score, reasons = assemble.score_tool(tool)
		self.assertIn("watch_item_hit", reasons)
		self.assertGreaterEqual(score, 70)

	def test_highlight_object_shape_and_ordering(self):
		low = build(_cand("brew:aaa", "aaa", "brew", "1.0.0", "2.0.0"),
			{"headliners": [_hl("features", "warning")]})           # 25 + 20 = 45
		high = build(_cand("brew:zzz", "zzz", "brew", "1.0.0", "1.0.1"),
			{"headliners": [_hl("fixes", "info")],
			"relevancy": [_rel("fixes", "incompatible", "Breaks the wrapper script here")]})  # 100 + 45
		highlights = assemble.build_highlights([low, high])
		self.assertEqual([h["tool_id"] for h in highlights], ["brew:zzz", "brew:aaa"])
		top = highlights[0]
		self.assertEqual(top["title"], "zzz 1.0.0 → 1.0.1")
		self.assertEqual(top["why"], "Breaks the wrapper script here")
		self.assertEqual(top["severity"], "incompatible")
		self.assertEqual(top["suggestion_ids"], ["brew:zzz:upgrade"])
		self.assertEqual(top["reasons"], ["incompatible_finding"])
		self.assertEqual(top["score"], 100)

	def test_why_truncates_on_a_word_boundary(self):
		summary = "word " * 80
		tool = build(_cand("brew:verbose", "verbose", "brew", "1.0.0", "1.0.1"),
			{"headliners": [_hl("fixes", "info")],
			"relevancy": [_rel("fixes", "incompatible", summary.strip())]})
		why = assemble.build_highlights([tool])[0]["why"]
		self.assertLessEqual(len(why), 220)
		self.assertTrue(why.endswith("…"))
		self.assertFalse(why[:-1].endswith(" "))

	def test_cap_is_eight(self):
		tools = []
		for i in range(12):
			tools.append(build(_cand(f"brew:t{i:02d}", f"t{i:02d}", "brew", "1.0.0", "1.0.1"),
				{"headliners": [_hl("fixes", "info")],
				"relevancy": [_rel("fixes", "incompatible", f"Breaks thing {i}")]}))
		self.assertEqual(len(assemble.build_highlights(tools)), 8)


# A synthetic session, small enough to reason about and shaped to exercise the
# things prose invariants cannot: a CVE shared by two tools (union < sum), a
# duplicate suggestion id (the rename pass runs between finalize_tool and
# build_highlights), and one health finding.
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
}

RESEARCH = [
	{"id": "brew:openssh",
		"headliners": [_hl("security", "notable", "Fixes CVE-2026-53789 in the agent forwarding path")],
		"relevancy": [_rel("security", "notable", "Agent forwarding is how this machine reaches its remotes.")],
		"links": [], "context": [], "suggestions": [],
		"security": {
			"cve_severities": [{"cve_id": "CVE-2026-53789", "severity": "high", "basis": "vendor"}],
			"notable": [{"cve_id": "CVE-2026-53789", "advisory_id": None, "severity": "high",
				"summary": "Agent forwarding is how this machine reaches its remotes.", "affects_me": True}]}},
	{"id": "brew:ssh-copy-id",
		"headliners": [_hl("security", "notable", "Same source tarball: CVE-2026-53789")],
		"relevancy": [], "links": [], "context": [], "suggestions": [],
		# The same advisory rated differently by a second source — the report-wide
		# union must take the worse of the two, never the last one seen.
		"security": {"cve_severities": [{"cve_id": "CVE-2026-53789", "severity": "medium", "basis": "nvd"}]}},
	{"id": "brew:podman",
		"headliners": [_hl("fixes", "warning", "libkrun required")],
		"relevancy": [_rel("fixes", "incompatible", "Intel Mac not supported in v5+")],
		"suggestions": [
			{"id": "brew:podman:upgrade", "kind": "edit",  # collides with the baseline id
			"title": "Annotate the Brewfile pin", "target_files": [{"path": "Brewfile", "description": "note"}],
			"rationale": "", "motivating_link": None, "diff_preview": None},
			{"kind": "edit", "title": "No id at all", "target_files": [],  # id omitted entirely
			"rationale": "", "motivating_link": None, "diff_preview": None},
		]},
	{"id": "brew:parallel", "headliners": [_hl("notes", "info", "Monthly snapshot")], "relevancy": []},
	{"id": "mise:uv", "headliners": [_hl("features", "notable", "New resolver")], "relevancy": []},
	{"id": "brew-health:unlinked_keg:tree-sitter", "headliners": [], "relevancy": []},
]


def assemble_session(collect, research_entries):
	"""Write a throwaway session dir, run it through main(), and return
	(report, stderr). Going through main() rather than build_tool() is the
	point: it is the only way to exercise the two ordering constraints and
	the id-uniqueness pass together."""
	with tempfile.TemporaryDirectory() as tmp:
		session = os.path.join(tmp, "tool-update-review-20260822T113344Z")
		os.makedirs(os.path.join(session, "research"))
		with open(os.path.join(session, "collect.json"), "w", encoding="utf-8") as fh:
			json.dump(collect, fh)
		with open(os.path.join(session, "research", "01-all.json"), "w", encoding="utf-8") as fh:
			json.dump(research_entries, fh)
		argv, err = sys.argv, io.StringIO()
		sys.argv = ["assemble.py", session, "--macos-setup-root", tmp,
			"--dotfiles-root", tmp, "--systems-root", tmp]
		try:
			with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
				assemble.main()
		finally:
			sys.argv = argv
		with open(os.path.join(session, "report.json"), "r", encoding="utf-8") as fh:
			return json.load(fh), err.getvalue()


class ReportInvariantTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.report, cls.stderr = assemble_session(COLLECT, RESEARCH)

	def test_schema_version_unchanged(self):
		self.assertEqual(self.report["schema_version"], 1)

	def test_by_delta_sums_to_total_outdated(self):
		summary = self.report["summary"]
		self.assertEqual(sum(summary["by_delta"].values()), summary["total_outdated"])
		self.assertEqual(set(summary["by_delta"]), {"major", "minor", "patch", "revision", "unknown"})

	def test_by_bucket_sums_to_every_tool(self):
		# Deliberately a different denominator from by_delta (which excludes
		# brew-health): review_bucket is defined for every Tool object and the
		# page renders health cards inside the bucket lists, so this must sum to
		# total_outdated + health_count, not to total_outdated. Anything mixing
		# the two into one percentage is comparing 77 against 74.
		summary = self.report["summary"]
		self.assertEqual(sum(summary["by_bucket"].values()), len(self.report["tools"]))
		self.assertEqual(len(self.report["tools"]), summary["total_outdated"] + summary["health_count"])

	def test_security_bucket_counts_fit_inside_tools_with_security(self):
		sec = self.report["summary"]["security"]
		self.assertLessEqual(sec["auto_count"] + sec["mixed_count"], sec["tools_with_security"])

	def test_report_cve_count_is_a_union(self):
		sec = self.report["summary"]["security"]
		per_tool_sum = sum(t["security"]["cve_count"] for t in self.report["tools"])
		self.assertLessEqual(sec["cve_count"], per_tool_sum)
		# openssh and ssh-copy-id ship the same advisory — union must be
		# strictly smaller here, or the dedupe is not happening at all.
		self.assertLess(sec["cve_count"], per_tool_sum)

	def test_highlights_are_capped_and_resolvable(self):
		tool_ids = {t["id"] for t in self.report["tools"]}
		suggestion_ids = {s["id"] for t in self.report["tools"] for s in t["suggestions"]}
		self.assertLessEqual(len(self.report["highlights"]), 8)
		for h in self.report["highlights"]:
			self.assertIn(h["tool_id"], tool_ids)
			for sid in h["suggestion_ids"]:
				self.assertIn(sid, suggestion_ids)

	def test_highlight_ids_are_post_rename(self):
		# The renamed id ("…:upgrade-2") must reach highlights[], which is only
		# true if build_highlights() runs after the uniqueness pass.
		podman = next(t for t in self.report["tools"] if t["id"] == "brew:podman")
		self.assertEqual([s["id"] for s in podman["suggestions"]],
			["brew:podman:upgrade", "brew:podman:upgrade-2", "brew:podman:sug-1"])
		highlight = next(h for h in self.report["highlights"] if h["tool_id"] == "brew:podman")
		self.assertEqual(highlight["suggestion_ids"],
			["brew:podman:upgrade", "brew:podman:upgrade-2", "brew:podman:sug-1"])

	def test_id_less_suggestion_gets_one_synthesized(self):
		# write_status.py indexes by sug["id"] and KeyErrors on a missing one,
		# long after the user has already decided about it.
		ids = [s["id"] for t in self.report["tools"] for s in t["suggestions"]]
		self.assertEqual(len(ids), len(set(ids)))
		self.assertTrue(all(ids))
		self.assertIn("brew:podman: suggestion with no id", self.stderr)

	def test_baseline_detection_survives_the_rename(self):
		# finalize_tool() runs before the rename, so the baseline is still the
		# one identified by position + kind + ":upgrade" suffix.
		podman = next(t for t in self.report["tools"] if t["id"] == "brew:podman")
		self.assertFalse(podman["suggestions"][0]["pre_accept"])  # pinned + edit ⇒ elevated
		self.assertTrue(podman["suggestions"][0]["kind"] == "upgrade")

	def test_every_tool_carries_the_new_fields(self):
		for tool in self.report["tools"]:
			self.assertIn(tool["version_delta"], ("major", "minor", "patch", "revision", "unknown"))
			self.assertIn(tool["version_scheme"], ("semver", "calver", "date", "opaque", "none"))
			self.assertIsInstance(tool["version_delta_note"], str)
			self.assertIn(tool["review_bucket"], ("security_auto", "security_mixed", "attention", "routine"))
			# Eight keys always; `notable` is the ninth and is emitted only when
			# assembly has an answer to give (§7 below), so a tool may carry no
			# `notable` key at all — but never a `notable` that is not a list.
			self.assertEqual(set(tool["security"]) - {"notable"},
				{"cve_ids", "cve_count", "cve_claimed_count", "has_security", "security_only", "impact",
				"severity_counts", "cve_severities"})
			if "notable" in tool["security"]:
				self.assertIsInstance(tool["security"]["notable"], list, tool["id"])

	def test_health_tool_is_excluded_from_by_delta_and_security(self):
		health = next(t for t in self.report["tools"] if t["source"] == "brew-health")
		self.assertEqual(health["version_delta"], "unknown")
		self.assertEqual(health["version_scheme"], "none")
		self.assertFalse(health["security"]["has_security"])
		self.assertEqual(self.report["summary"]["by_delta"]["unknown"], 0)

	def test_date_version_is_no_longer_a_major_bump(self):
		# The old leading-integer rule called parallel's monthly snapshot major
		# and elevated it; the shared classifier calls it minor.
		parallel = next(t for t in self.report["tools"] if t["id"] == "brew:parallel")
		self.assertEqual(parallel["version_delta"], "minor")
		self.assertEqual(parallel["risk_level"], "low")

	def test_severity_counts_sum_to_cve_count_on_every_tool(self):
		# Structural, not asserted: the rollup iterates cve_ids, so an id nobody
		# rated becomes `unknown` rather than a broken sum.
		for tool in self.report["tools"]:
			sec = tool["security"]
			self.assertEqual(set(sec["severity_counts"]), set(assemble._CVE_SEVERITIES), tool["id"])
			self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"], tool["id"])

	def test_summary_severity_counts_is_a_union_taking_the_worse(self):
		sec = self.report["summary"]["security"]
		self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"])
		# openssh rates the shared advisory `high`, ssh-copy-id `medium`; the
		# union counts it once, at the worse of the two.
		self.assertEqual(sec["severity_counts"]["high"], 1)
		self.assertEqual(sec["severity_counts"]["medium"], 0)
		self.assertLessEqual(sec["severity_counts"]["high"],
			sum(t["security"]["severity_counts"]["high"] for t in self.report["tools"]))

	def test_notable_is_capped_and_every_id_it_names_resolves(self):
		for tool in self.report["tools"]:
			sec = tool["security"]
			self.assertLessEqual(len(sec.get("notable") or []), 3, tool["id"])
			for entry in sec.get("notable") or []:
				self.assertTrue(entry["cve_id"] is None or entry["cve_id"] in sec["cve_ids"], tool["id"])
			for entry in sec["cve_severities"]:
				self.assertIn(entry["cve_id"], sec["cve_ids"], tool["id"])
			if tool["source"] == "brew-health" or tool["research_error"]:
				self.assertEqual(sec["notable"], [], tool["id"])

	def test_highlights_carry_their_provenance(self):
		for h in self.report["highlights"]:
			self.assertIn(h["why_source"], (
				"relevancy_security", "relevancy_other", "config_status", "research_error",
				"headliner_security", "headliner_other", "major_bump", "none"))
			self.assertTrue(h["why_ref"] is None or h["why_ref"].split(":")[0] in ("rel", "hl"))

	def test_no_highlight_restates_its_tools_security_card(self):
		by_id = {t["id"]: t for t in self.report["tools"]}
		for h in self.report["highlights"]:
			refs = {n["source_ref"] for n in by_id[h["tool_id"]]["security"].get("notable") or []
				if n["source_ref"]}
			self.assertNotIn(h["why_ref"], refs, h["tool_id"])

	def test_zero_dot_x_major_is_caught(self):
		uv = next(t for t in self.report["tools"] if t["id"] == "mise:uv")
		self.assertEqual(uv["version_delta"], "major")
		self.assertEqual(uv["risk_level"], "elevated")
		self.assertFalse(uv["suggestions"][0]["pre_accept"])



# ── 6. CVE severity, notable security items, and the noise floor ────────────
#      (references/assembly.md §Severity Rollup and the Sum Invariant,
#       §Validating Research's `notable`; references/research.md §The Noise Floor)
def _sev(cve_id, severity, basis="vendor"):
	return {"cve_id": cve_id, "severity": severity, "basis": basis}


def _not(summary, severity="high", cve_id=None, advisory_id=None, affects_me=False):
	return {"cve_id": cve_id, "advisory_id": advisory_id, "severity": severity,
		"summary": summary, "affects_me": affects_me}


def _sec_research(**kw):
	"""A research object whose security content is a security headliner plus
	whatever security block the case under test needs."""
	research = {"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001 and CVE-2026-1002.")],
		"relevancy": [], "context": []}
	research.update(kw)
	return research


class SeverityRollupTests(unittest.TestCase):
	def _sec(self, research, tool_id="brew:sev"):
		return build(_cand(tool_id, tool_id.split(":")[1], "brew", "1.0.0", "1.0.1"), research)["security"]

	def test_counts_have_the_whole_vocabulary_and_sum_to_cve_count(self):
		sec = self._sec(_sec_research(security={"cve_severities": [_sev("CVE-2026-1001", "critical")]}))
		self.assertEqual(set(sec["severity_counts"]), set(assemble._CVE_SEVERITIES))
		self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"])
		self.assertEqual(sec["severity_counts"]["critical"], 1)
		# The un-rated id is not an error and not a gap — it is `unknown`.
		self.assertEqual(sec["severity_counts"]["unknown"], 1)

	def test_the_sum_holds_when_research_rates_nothing_at_all(self):
		# The expected steady state (R2): most ids are never graded.
		sec = self._sec(_sec_research())
		self.assertEqual(sec["severity_counts"]["unknown"], 2)
		self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"])
		self.assertEqual(sec["cve_severities"], [])

	def test_a_rating_for_an_id_this_range_does_not_contain_is_dropped(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			tool = assemble.build_tool(_cand("brew:sev", "sev", "brew", "1.0.0", "1.0.1"),
				_sec_research(security={"cve_severities": [_sev("CVE-2019-4242", "critical")]}))
		sec = tool["security"]
		self.assertEqual(sec["severity_counts"]["critical"], 0)
		self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"])
		self.assertIn("CVE-2019-4242", err.getvalue())

	def test_a_rating_with_no_basis_is_unknown_never_a_guess(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			tool = assemble.build_tool(_cand("brew:sev", "sev", "brew", "1.0.0", "1.0.1"),
				_sec_research(security={"cve_severities": [
					{"cve_id": "CVE-2026-1001", "severity": "critical"},
					{"cve_id": "CVE-2026-1002", "severity": "apocalyptic", "basis": "vendor"}]}))
		sec = tool["security"]
		self.assertEqual(sec["severity_counts"], dict(critical=0, high=0, medium=0, low=0, unknown=2))
		self.assertEqual(sec["cve_severities"], [])
		self.assertIn("basis", err.getvalue())

	def test_conflicting_ratings_for_one_id_keep_the_worse(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			tool = assemble.build_tool(_cand("brew:sev", "sev", "brew", "1.0.0", "1.0.1"),
				_sec_research(security={"cve_severities": [
					_sev("CVE-2026-1001", "low", "nvd"), _sev("CVE-2026-1001", "critical", "vendor")]}))
		self.assertEqual(tool["security"]["severity_counts"]["critical"], 1)
		self.assertEqual(tool["security"]["severity_counts"]["low"], 0)
		self.assertIn("CVE-2026-1001", err.getvalue())

	def test_the_report_wide_worse_wins_resolution_is_not_silent(self):
		# Its per-tool twin in resolve_cve_severities() warns on exactly this
		# disagreement; the report-wide one used to resolve it in silence, so a
		# header reading "1 critical" could come from one tool's page
		# contradicting another's with nothing said about it.
		a = self._tool_rating("brew:a", "low", "nvd")
		b = self._tool_rating("brew:b", "critical", "vendor")
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			summary = assemble.summarize_security([a, b])
		self.assertEqual(summary["severity_counts"]["critical"], 1)
		self.assertEqual(summary["severity_counts"]["low"], 0)
		self.assertIn("CVE-2026-1001", err.getvalue())
		self.assertIn("keeping the worse", err.getvalue())
		# Agreement stays quiet — the warning has to mean something.
		quiet = io.StringIO()
		with contextlib.redirect_stderr(quiet):
			assemble.summarize_security([a, self._tool_rating("brew:c", "low", "vendor")])
		self.assertNotIn("keeping the worse", quiet.getvalue())

	def _tool_rating(self, tool_id, severity, basis):
		return build(_cand(tool_id, tool_id.split(":")[1], "brew", "1.0.0", "1.0.1"),
			{"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001.")],
				"security": {"cve_severities": [_sev("CVE-2026-1001", severity, basis)]}})

	def test_emitted_cve_severities_are_the_resolved_graded_subset(self):
		sec = self._sec(_sec_research(security={"cve_severities": [
			_sev("CVE-2026-1002", "medium", "nvd"), _sev("CVE-2026-1001", "high", "vendor")]}))
		# Ordered by (year, sequence) like cve_ids, one entry per graded id, and
		# every id resolvable in cve_ids — so summarize_security() can rebuild
		# the union map from report.json alone.
		self.assertEqual(sec["cve_severities"],
			[_sev("CVE-2026-1001", "high", "vendor"), _sev("CVE-2026-1002", "medium", "nvd")])
		for entry in sec["cve_severities"]:
			self.assertIn(entry["cve_id"], sec["cve_ids"])

	def test_health_tool_carries_a_zeroed_block(self):
		_, candidate, research, _ = _fixture("S10")
		with contextlib.redirect_stderr(io.StringIO()):
			tool = assemble.build_health_tool(candidate, research)
		sec = tool["security"]
		self.assertEqual(sec["severity_counts"], dict(critical=0, high=0, medium=0, low=0, unknown=0))
		self.assertEqual(sec["notable"], [])
		self.assertEqual(sec["cve_severities"], [])


class NotableValidationTests(unittest.TestCase):
	def _build(self, research, tool_id="brew:nota", capture=False):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			tool = assemble.build_tool(
				_cand(tool_id, tool_id.split(":")[1], "brew", "1.0.0", "1.0.1"), research)
		return (tool, err.getvalue()) if capture else tool

	def test_an_over_cap_list_keeps_the_worst_three(self):
		tool, err = self._build(_sec_research(security={"notable": [
			_not("low one", "low"), _not("critical one", "critical"),
			_not("medium one", "medium"), _not("high one", "high")]}), capture=True)
		self.assertEqual([n["summary"] for n in tool["security"]["notable"]],
			["critical one", "high one", "medium one"])
		self.assertIn("notable", err)

	def test_affects_me_breaks_ties_between_equal_severities(self):
		tool = self._build(_sec_research(
			relevancy=[_rel("security", "notable", "Reaches this machine's sshd config.")],
			security={"notable": [
				_not("second", "high"), _not("first", "high", affects_me=True), _not("third", "medium")]}))
		self.assertEqual([n["summary"] for n in tool["security"]["notable"]], ["first", "second", "third"])

	def test_a_touchpoint_outranks_higher_rated_items_that_miss_this_machine(self):
		"""R5 clause 3's whole purpose: an id-less, ungraded flaw that lands on
		something this machine runs. With severity ordered first it sorted last
		and the cap evicted it — three `low` CVEs nobody here can reach beat a
		reproduced command injection (`brew:iproute2mac`, the recorded run)."""
		lows = [_not(f"low {i}", "low", cve_id=f"CVE-2026-100{i}") for i in (1, 2, 3)]
		mine = _not("Command injection in the wrapper this machine runs.", "unknown", affects_me=True)
		tool = self._build({
			"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001, CVE-2026-1002, CVE-2026-1003.")],
			"relevancy": [_rel("security", "notable", "The wrapper this machine runs takes the injected argv.")],
			"security": {
				"cve_severities": [_sev(f"CVE-2026-100{i}", "low") for i in (1, 2, 3)],
				"notable": lows + [mine]}})
		summaries = [n["summary"] for n in tool["security"]["notable"]]
		self.assertEqual(summaries[0], "Command injection in the wrapper this machine runs.")
		self.assertIn("low 1", summaries)   # … and it evicted the weakest, not the first

	def test_unknown_is_not_ranked_below_low(self):
		# `unknown` is 0 in _CVE_SEVERITY_RANK because there it means "no rating
		# recorded" and must never beat one. On a notable[] entry it means
		# "research selected this and nobody graded it", which is not evidence
		# of a small flaw — the two maps are deliberately different.
		self.assertGreater(assemble._NOTABLE_SEVERITY_RANK["unknown"],
			assemble._NOTABLE_SEVERITY_RANK["low"])
		self.assertLess(assemble._NOTABLE_SEVERITY_RANK["unknown"],
			assemble._NOTABLE_SEVERITY_RANK["medium"])
		self.assertEqual(set(assemble._NOTABLE_SEVERITY_RANK), set(assemble._CVE_SEVERITIES))
		tool = self._build(_sec_research(security={"notable": [
			_not("graded low", "low"), _not("nobody graded it", "unknown")]}))
		self.assertEqual([n["summary"] for n in tool["security"]["notable"]],
			["nobody graded it", "graded low"])

	def test_ordering_strictly_precedes_the_cap(self):
		"""The cap keeps the *worst* three, never the first three — so research
		writing its strongest item last costs nothing."""
		tool, err = self._build(_sec_research(security={"notable": [
			_not("u1", "unknown"), _not("u2", "unknown"), _not("u3", "unknown"),
			_not("the critical one, written last", "critical")]}), capture=True)
		self.assertEqual([n["summary"] for n in tool["security"]["notable"]][0],
			"the critical one, written last")
		self.assertEqual(len(tool["security"]["notable"]), assemble._NOTABLE_CAP)
		self.assertIn("dropping 1", err)

	def test_a_grade_only_on_the_notable_warns_that_the_rollup_never_sees_it(self):
		# The card would read `critical` while severity_counts buckets the same
		# id as `unknown` — the disagreement the map-wins rule exists to stop,
		# reached by omission rather than by contradiction. The grade is kept
		# (it is the only one research found) and the gap is warned about.
		tool, err = self._build(_sec_research(security={
			"notable": [_not("x", "critical", cve_id="CVE-2026-1001")]}), capture=True)
		self.assertEqual(tool["security"]["notable"][0]["severity"], "critical")
		self.assertEqual(tool["security"]["severity_counts"]["critical"], 0)
		self.assertEqual(tool["security"]["severity_counts"]["unknown"], 2)
		self.assertIn("cve_severities has no entry for it", err)
		# … and an ungraded entry is the normal case, so it stays silent.
		_, quiet = self._build(_sec_research(security={
			"notable": [_not("x", "unknown", cve_id="CVE-2026-1001")]}), capture=True)
		self.assertNotIn("cve_severities has no entry", quiet)

	def test_a_non_string_summary_is_dropped_not_stringified(self):
		# _norm_text() would render {"text": …} as its repr and 42 as "42", and
		# the page's `typeof === 'string'` guard passes both by then — the drift
		# has to be caught on this side or not at all.
		tool, err = self._build(_sec_research(security={"notable": [
			{"cve_id": None, "advisory_id": None, "severity": "high",
				"summary": {"text": "wrapped"}, "affects_me": False},
			{"cve_id": None, "advisory_id": None, "severity": "high",
				"summary": 42, "affects_me": False},
			_not("a real line")]}), capture=True)
		self.assertEqual([n["summary"] for n in tool["security"]["notable"]], ["a real line"])
		self.assertEqual(err.count("not a string"), 2)

	def test_source_ref_picks_the_max_severity_relevancy_match(self):
		# Two relevancy items naming one CVE. _highlight_why_parts() takes the
		# max-severity one, so this must too — otherwise the two refs disagree
		# and the R6 dedupe below silently misses.
		tool = self._build({
			"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001.")],
			"relevancy": [
				_rel("security", "notable", "CVE-2026-1001 is bundled in the vendored copy."),
				_rel("security", "incompatible", "CVE-2026-1001 lands on the gh commands this machine pre-approves.")],
			"security": {"notable": [_not("x", "critical", cve_id="CVE-2026-1001", affects_me=True)]}})
		self.assertEqual(tool["security"]["notable"][0]["source_ref"], "rel:1")
		self.assertEqual(assemble._highlight_why_parts(tool)[2], "rel:1")

	def test_an_entry_with_no_summary_is_dropped(self):
		tool, err = self._build(_sec_research(security={"notable": [
			_not(""), {"cve_id": "CVE-2026-1001", "severity": "high"}, _not("real one")]}), capture=True)
		self.assertEqual([n["summary"] for n in tool["security"]["notable"]], ["real one"])
		self.assertIn("summary", err)

	def test_an_unknown_severity_is_coerced_not_guessed(self):
		tool, err = self._build(_sec_research(security={"notable": [_not("x", "catastrophic")]}), capture=True)
		self.assertEqual(tool["security"]["notable"][0]["severity"], "unknown")
		self.assertIn("catastrophic", err)

	def test_the_severity_map_wins_over_the_entrys_own_rating(self):
		# One source of truth: severity_counts and the card must agree.
		tool, err = self._build(_sec_research(security={
			"cve_severities": [_sev("CVE-2026-1001", "critical")],
			"notable": [_not("x", "low", cve_id="CVE-2026-1001")]}), capture=True)
		self.assertEqual(tool["security"]["notable"][0]["severity"], "critical")
		self.assertIn("CVE-2026-1001", err)

	def test_a_notable_id_reaches_cve_ids_but_never_the_claim_count(self):
		tool = self._build({"headliners": [_hl("security", "notable", "Security release.")],
			"security": {"notable": [_not("Only 1 of 28 advisories lands here.",
				cve_id="CVE-2026-4242")]}})
		self.assertIn("CVE-2026-4242", tool["security"]["cve_ids"])
		# The claim scan is deliberately NOT extended: "1 of 28 advisories" in a
		# notable summary must not become a vendor claim of 28.
		self.assertIsNone(tool["security"]["cve_claimed_count"])

	def test_an_unresolvable_id_is_nulled_rather_than_emitted(self):
		tool, err = self._build(_sec_research(security={"notable": [
			_not("malformed id", cve_id="CVE-26-1")]}), capture=True)
		self.assertIsNone(tool["security"]["notable"][0]["cve_id"])
		self.assertIn("CVE-26-1", err)

	def test_affects_me_with_no_security_relevancy_warns_but_is_never_forced(self):
		# R1: direction is research's call. Assembly warns, never rewrites — a
		# negative-direction finding ("the fix does not reach this machine") is
		# affects_me: false and stays that way.
		tool, err = self._build(_sec_research(security={"notable": [_not("x", affects_me=True)]}), capture=True)
		self.assertTrue(tool["security"]["notable"][0]["affects_me"])
		self.assertIn("affects_me", err)
		quiet = self._build(_sec_research(
			relevancy=[_rel("security", "info", "The sshd fix reaches Apple's sshd, not Homebrew's.")],
			security={"notable": [_not("x", affects_me=False)]}))
		self.assertFalse(quiet["security"]["notable"][0]["affects_me"])

	def test_an_empty_notable_is_the_normal_correct_result(self):
		# Research answered — it wrote a security block — and nothing in this
		# release qualified. That is the common case, and it is a real answer,
		# distinct from research that never addressed the question at all (§7,
		# NotableAbsentVersusEmptyTests).
		tool = self._build(_sec_research(security={"notable": []}))
		self.assertEqual(tool["security"]["notable"], [])
		self.assertTrue(tool["security"]["has_security"])   # the strip still renders

	def test_research_that_told_us_nothing_never_carries_a_notable(self):
		tool, err = self._build({"headliners": [], "security": {"notable": [_not("x")]}}, capture=True)
		self.assertEqual(tool["security"]["notable"], [])
		self.assertIn("notable", err)

	def test_a_notable_is_security_content_on_its_own(self):
		tool = self._build({"headliners": [_hl("fixes", "info", "A plain fix.")],
			"security": {"notable": [_not("A vendor advisory with no CVE id.", advisory_id="TS-2026-011")]}})
		self.assertTrue(tool["security"]["has_security"])
		self.assertEqual(tool["security"]["notable"][0]["advisory_id"], "TS-2026-011")
		self.assertIsNone(tool["security"]["notable"][0]["cve_id"])

	def test_source_ref_points_at_the_item_the_notable_restates(self):
		tool = self._build({
			"headliners": [_hl("notes", "info", "Unrelated."), _hl("security", "notable", "Fixes CVE-2026-1001.")],
			"relevancy": [_rel("security", "notable", "CVE-2026-1002 lands on gh commands this machine pre-approves.")],
			"security": {"notable": [
				_not("Reachable here", cve_id="CVE-2026-1002"),
				_not("Not reachable", cve_id="CVE-2026-1001"),
				_not("No id at all, restating the note", advisory_id=None)]}})
		refs = {n["summary"]: n["source_ref"] for n in tool["security"]["notable"]}
		self.assertEqual(refs["Reachable here"], "rel:0")
		self.assertEqual(refs["Not reachable"], "hl:1")
		self.assertIsNone(refs["No id at all, restating the note"])


class SecurityBlockDriftTests(unittest.TestCase):
	"""Research files are model-written; the security block will arrive drifted
	sooner or later, and a drifted block must cost one warned-about tool, never
	the run (§4's doctrine applied to the newest arrays)."""
	SHAPES = [
		("security as a bare string", {"security": "none"}),
		("security null", {"security": None}),
		("security as a list", {"security": [{"notable": []}]}),
		("notable as a bare string", {"security": {"notable": "no notable items"}}),
		("notable of bare strings", {"security": {"notable": ["CVE-2026-1001 is bad"]}}),
		("notable null", {"security": {"notable": None}}),
		("cve_severities as a dict", {"security": {"cve_severities": {"CVE-2026-1001": "high"}}}),
		("cve_severities of bare strings", {"security": {"cve_severities": ["CVE-2026-1001 high"]}}),
		("severity as an int", {"security": {"notable": [_not("x", 9)]}}),
		("affects_me as a string", {"security": {"notable": [_not("x", affects_me="yes")]}}),
		("cve_id as an int", {"security": {"notable": [_not("x", cve_id=2026)]}}),
		("advisory_id as a dict", {"security": {"notable": [_not("x", advisory_id={"id": "TS-1"})]}}),
		("rating entry with no id", {"security": {"cve_severities": [{"severity": "high", "basis": "nvd"}]}}),
	]

	def test_a_drifted_security_block_costs_one_tool_not_the_run(self):
		for label, extra in self.SHAPES:
			with self.subTest(label):
				research = _sec_research(**extra)
				with contextlib.redirect_stderr(io.StringIO()):
					tool = assemble.build_tool(_cand("brew:drift", "drift", "brew", "1.0.0", "1.0.1"), research)
				sec = tool["security"]
				# A block drifted at its *root* (a bare string, a list, null) is not
				# an answer to "what is notable here", so the key is omitted and the
				# page derives the column — §7's NotableAbsentVersusEmptyTests pins
				# that. A block whose root reads and whose members drifted still
				# answers, and the answer is always a well-shaped list.
				notable = sec["notable"] if "notable" in sec else []
				self.assertIsInstance(notable, list, label)
				self.assertLessEqual(len(notable), 3, label)
				self.assertIsInstance(sec["cve_severities"], list, label)
				self.assertEqual(set(sec["severity_counts"]), set(assemble._CVE_SEVERITIES), label)
				self.assertEqual(sum(sec["severity_counts"].values()), sec["cve_count"], label)
				for entry in notable:
					self.assertIsInstance(entry["summary"], str, label)
					self.assertIn(entry["severity"], assemble._CVE_SEVERITIES, label)
					self.assertIsInstance(entry["affects_me"], bool, label)
					self.assertTrue(entry["cve_id"] is None or entry["cve_id"] in sec["cve_ids"], label)
					self.assertTrue(entry["advisory_id"] is None or isinstance(entry["advisory_id"], str), label)
				# Still fully classified — the card is usable either way.
				self.assertIn(tool["review_bucket"], ("security_auto", "security_mixed", "attention", "routine"))


# ── The noise floor's decision-invariance (R3) ──────────────────────────────
# `noise_suppressible()` is the machine-checkable statement of
# references/research.md §The Noise Floor: the bounded set of items a research
# subagent may delete outright. The property that matters is that deleting all
# of them changes no decision — a presentation rule must never approve, or
# un-approve, an update.
def _decision(tool):
	return (tool["security"]["has_security"], tool["security"]["security_only"],
		tool["security"]["impact"], tool["risk_level"], tool["review_bucket"],
		tuple(s.get("pre_accept") for s in tool["suggestions"]))


def _suppress(research, tool):
	"""Return a copy of `research` with every noise-suppressible item removed."""
	out = dict(research)
	for field, items in (("headliners", tool.get("headliners")), ("relevancy", tool.get("relevancy"))):
		if isinstance(research.get(field), list):
			out[field] = [i for i in items if not assemble.noise_suppressible(tool, i, field)]
	return out


class NoiseFloorTests(unittest.TestCase):
	def test_suppressing_noise_changes_no_decision(self):
		for label, candidate, research, _ in _fixtures():
			if not isinstance(research, dict):
				continue
			with self.subTest(label):
				before = build(candidate, research)
				after = build(candidate, _suppress(research, before))
				self.assertEqual(_decision(before), _decision(after), f"{label}: noise suppression moved a decision")

	def test_a_features_item_is_never_suppressible_and_here_is_why(self):
		# The counter-example that makes the boundary load-bearing rather than
		# arbitrary: `features` at any severity is what security_only reads as
		# "this release is more than patches". Delete the last one and the tool
		# walks from security_mixed into security_auto and pre-accepts itself.
		research = {"headliners": [_hl("security", "notable"), _hl("features", "info", "One fewer round-trip.")]}
		tool = build(_cand("brew:op", "op", "brew", "1.0.0", "1.0.1"), research)
		self.assertFalse(assemble.noise_suppressible(tool, research["headliners"][1], "headliners"))
		self.assertEqual(tool["review_bucket"], "security_mixed")
		deleted = build(_cand("brew:op", "op", "brew", "1.0.0", "1.0.1"),
			{"headliners": [_hl("security", "notable")]})
		self.assertEqual(deleted["review_bucket"], "security_auto")
		self.assertTrue(deleted["suggestions"][0]["pre_accept"])

	def test_a_security_item_is_never_suppressible(self):
		# R3: the noise classes apply to fixes/features/notes only. Removing the
		# last security item would take has_security with it.
		research = {"headliners": [_hl("notes", "info"), _hl("security", "info", "A small hardening change.")],
			"relevancy": [_rel("security", "info", "Reaches a config here.")]}
		tool = build(_cand("brew:s", "s", "brew", "1.0.0", "1.0.1"), research)
		self.assertFalse(assemble.noise_suppressible(tool, research["headliners"][1], "headliners"))
		self.assertFalse(assemble.noise_suppressible(tool, research["relevancy"][0], "relevancy"))

	def test_the_last_headliner_is_never_suppressible(self):
		research = {"headliners": [_hl("notes", "info", "The only thing we know.")]}
		tool = build(_cand("brew:one", "one", "brew", "1.0.0", "1.0.1"), research)
		self.assertFalse(assemble.noise_suppressible(tool, research["headliners"][0], "headliners"))

	def test_text_carrying_an_id_a_claim_or_a_watch_hit_is_never_suppressible(self):
		research = {"headliners": [
			_hl("notes", "info", "Routine."),
			_hl("notes", "info", "Linux-only CVE-2026-19042 does not affect the macOS builds."),
			_hl("fixes", "info", "The release fixes 33 CVEs upstream.")],
			"relevancy": [_rel("notes", "info", "⚠ Watch item hit: bash 5 stays the login shell.")]}
		tool = build(_cand("brew:w", "w", "brew", "1.0.0", "1.0.1"), research)
		self.assertTrue(assemble.noise_suppressible(tool, research["headliners"][0], "headliners"))
		for item in research["headliners"][1:]:
			self.assertFalse(assemble.noise_suppressible(tool, item, "headliners"))
		self.assertFalse(assemble.noise_suppressible(tool, research["relevancy"][0], "relevancy"))

	def test_the_suppressible_set_is_exactly_the_inert_pairs(self):
		research = {"headliners": [_hl("notes", "info"), _hl("notes", "info")]}
		tool = build(_cand("brew:m", "m", "brew", "1.0.0", "1.0.1"), research)
		expected = {
			("headliners", "fixes", "info"): True,
			("headliners", "fixes", "notable"): True,
			("headliners", "notes", "info"): True,
			("headliners", "notes", "notable"): False,   # notes above info disqualifies security_only
			("headliners", "fixes", "warning"): False,   # a non-security warning headliner is impact
			("headliners", "features", "info"): False,   # features at any severity disqualifies security_only
			("relevancy", "fixes", "info"): True,
			("relevancy", "notes", "info"): True,
			("relevancy", "fixes", "notable"): False,    # a non-security notable relevancy is impact
			("relevancy", "notes", "warning"): False,    # a warning relevancy elevates risk_level
		}
		for (field, category, severity), want in expected.items():
			with self.subTest(field=field, category=category, severity=severity):
				item = (_hl(category, severity) if field == "headliners" else _rel(category, severity))
				self.assertEqual(assemble.noise_suppressible(tool, item, field), want)


# ── Highlights: provenance, and the security de-duplication (R6) ────────────
class HighlightProvenanceTests(unittest.TestCase):
	def _tool(self, tool_id, research):
		return build(_cand(tool_id, tool_id.split(":")[1], "brew", "1.0.0", "1.0.1"), research)

	def test_why_source_and_ref_are_recorded(self):
		tool = self._tool("brew:a", {"headliners": [_hl("fixes", "info")],
			"relevancy": [_rel("fixes", "info", "Low."), _rel("security", "incompatible", "Breaks the pinned path.")]})
		h = assemble.build_highlights([tool])[0]
		self.assertEqual((h["why_source"], h["why_ref"]), ("relevancy_security", "rel:1"))
		self.assertEqual(h["why"], "Breaks the pinned path.")

	def test_a_security_bucket_prefers_a_non_security_headliner(self):
		# Step 4 used to guarantee the duplication the user complained about:
		# with no relevancy it returned the first security headliner, which is
		# the same line the card's security column renders.
		tool = self._tool("brew:b", {"headliners": [
			_hl("security", "notable", "Fixes CVE-2026-1001."),
			_hl("notes", "warning", "The config file moved to ~/.config/b.")],
			"relevancy": []})
		self.assertEqual(tool["review_bucket"], "security_mixed")
		why, source, ref = assemble._highlight_why_parts(tool)
		self.assertEqual((why, source, ref), ("The config file moved to ~/.config/b.", "headliner_other", "hl:1"))

	def test_a_security_headliner_is_still_the_fallback_when_nothing_else_exists(self):
		tool = self._tool("brew:c", {"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001.")]})
		why, source, ref = assemble._highlight_why_parts(tool)
		self.assertEqual((why, source, ref), ("Fixes CVE-2026-1001.", "headliner_security", "hl:0"))

	def _dup_corpus(self):
		"""One top-ranked tool whose `why` restates a notable item, plus nine
		ordinary candidates to backfill from."""
		dup = self._tool("brew:dup", {
			"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001.")],
			"relevancy": [_rel("security", "incompatible",
				"CVE-2026-1001 lands on gh commands this machine pre-approves for agents.")],
			"security": {"notable": [_not("CVE-2026-1001 lands on gh commands this machine pre-approves for agents.",
				"critical", cve_id="CVE-2026-1001", affects_me=True)]}})
		others = [self._tool(f"brew:t{i}", {"headliners": [_hl("fixes", "info")],
			"relevancy": [_rel("fixes", "warning", f"Touches config {i}.")]}) for i in range(9)]
		return dup, others

	def test_a_duplicating_highlight_yields_and_the_slot_is_backfilled(self):
		dup, others = self._dup_corpus()
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			highlights = assemble.build_highlights([dup] + others)
		ids = [h["tool_id"] for h in highlights]
		self.assertNotIn("brew:dup", ids)               # the highlight yields …
		self.assertEqual(len(ids), 8)                   # … and the section still carries 8
		self.assertEqual(len(set(ids)), 8)              # … all distinct
		self.assertEqual(ids, [f"brew:t{i}" for i in range(8)])
		self.assertIn("brew:dup", err.getvalue())
		# The security card keeps the sentence — nothing was removed there.
		self.assertEqual(len(dup["security"]["notable"]), 1)

	def test_without_the_duplicate_the_top_ranked_tool_still_leads(self):
		# Control for the test above: same corpus, notable emptied, so the drop
		# is attributable to the duplication and not to the ranking.
		dup, others = self._dup_corpus()
		dup["security"]["notable"] = []
		with contextlib.redirect_stderr(io.StringIO()):
			ids = [h["tool_id"] for h in assemble.build_highlights([dup] + others)]
		self.assertEqual(ids[0], "brew:dup")
		self.assertEqual(len(ids), 8)

	def test_two_relevancy_items_naming_one_cve_still_dedupe(self):
		# Latent until one of them outranks the other: source_ref resolved to
		# the *first* naming item while `why` came from the max-severity one, so
		# the refs never matched and the highlight restated the security card.
		dup = self._tool("brew:two", {
			"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001.")],
			"relevancy": [
				_rel("security", "notable", "CVE-2026-1001 is bundled in the vendored copy."),
				_rel("security", "incompatible", "CVE-2026-1001 lands on the gh commands this machine pre-approves.")],
			"security": {"notable": [_not("CVE-2026-1001 lands on the gh commands this machine pre-approves.",
				"critical", cve_id="CVE-2026-1001", affects_me=True)]}})
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			highlights = assemble.build_highlights([dup])
		self.assertEqual(highlights, [])
		self.assertIn("brew:two", err.getvalue())
		self.assertEqual(len(dup["security"]["notable"]), 1)   # the card keeps the sentence

	def test_why_sources_lists_every_branch_that_can_produce_one(self):
		"""`_WHY_SOURCES` is documentation unless something checks it. Every
		name in it must still appear in the two functions that emit one, so
		renaming a branch fails here instead of drifting `report.json`."""
		import inspect
		emitted = (inspect.getsource(assemble._highlight_why_parts)
			+ inspect.getsource(assemble._headliner_source))
		for source in assemble._WHY_SOURCES:
			self.assertIn(f'"{source}"', emitted)
		self.assertEqual(len(set(assemble._WHY_SOURCES)), len(assemble._WHY_SOURCES))

	def test_a_highlight_whose_why_is_not_a_security_item_is_kept(self):
		tool = self._tool("brew:keep", {
			"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001.")],
			"relevancy": [_rel("fixes", "incompatible", "The new default breaks this machine's wrapper.")],
			"security": {"notable": [_not("An unrelated advisory.", "high", cve_id="CVE-2026-1001")]}})
		with contextlib.redirect_stderr(io.StringIO()):
			highlights = assemble.build_highlights([tool])
		self.assertEqual([h["tool_id"] for h in highlights], ["brew:keep"])
		self.assertFalse(highlights[0]["why_ref"] in
			{n["source_ref"] for n in tool["security"]["notable"]})

# ── 7. The assembly ↔ page contract for `security.notable[]` ────────────────
#      (references/schemas.md §1.9 Ordering; references/rendering-report.md
#       §Group (b) — `security_mixed`)
#
# assemble.py orders `notable[]` *and evicts by that order*, then the page
# re-sorts what survived. So the two rankings are one contract with two
# implementations, and they have already disagreed silently: the page led with
# severity and ranked `unknown` below `low`, which rendered the id-less,
# ungraded, machine-touching item — the class R5 clause 3 exists for — last on
# the card, in the faintest ink it has.
_TEMPLATE_PATH = os.path.join(
	os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "report-template.html")


def _template():
	with open(_TEMPLATE_PATH, encoding="utf-8") as fh:
		return fh.read()


def _brace_block(text: str, start: int) -> str:
	"""The `{...}` beginning at or after `start`, brace-matched with string and
	comment literals skipped — so a `{` inside a comment or a quoted string
	cannot run the scan off the end of the file."""
	i = text.index("{", start)
	depth, j = 0, i
	while j < len(text):
		c = text[j]
		if c in "'\"`":
			quote, j = c, j + 1
			while j < len(text) and text[j] != quote:
				j += 2 if text[j] == "\\" else 1
		elif text.startswith("/*", j):
			j = text.index("*/", j) + 1
		elif text.startswith("//", j):
			j = text.index("\n", j)
		elif c == "{":
			depth += 1
		elif c == "}":
			depth -= 1
			if depth == 0:
				return text[i:j + 1]
		j += 1
	raise AssertionError("unbalanced braces from offset %d" % start)


def _js_const(name: str, text: str) -> str:
	m = re.search(r"\bconst %s\s*=\s*" % re.escape(name), text)
	assert m, "%s is gone from the template" % name
	return "const %s = %s;" % (name, _brace_block(text, m.end()))


def _js_function(name: str, text: str) -> str:
	m = re.search(r"\bfunction %s\s*\(" % re.escape(name), text)
	assert m, "%s() is gone from the template" % name
	close = text.index(")", m.end())
	return "function %s%s %s" % (name, text[m.end() - 1:close + 1], _brace_block(text, close))


def _rank_table(name: str) -> dict:
	"""`{key: int}` out of one of the page's rank objects."""
	body = _js_const(name, _template())
	return {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", body)}


def _sev_table(name: str) -> dict:
	body = _js_const(name, _template())
	return dict(re.findall(r"(\w+)\s*:\s*'(\w+)'", body))


def _run_page(snippet: str) -> object:
	"""Evaluate the page's own notable[] functions, lifted verbatim out of the
	template, under node. Only these functions — no DOM, no report — so the
	test pins the code that shipped rather than a paraphrase of it."""
	tmpl = _template()
	prelude = "\n".join([
		_js_const("NOTABLE_SEV", tmpl),
		_js_const("NOTABLE_RANK", tmpl),
		_js_function("normNotable", tmpl),
		_js_function("notableItems", tmpl),
		# The fallback path notableItems() takes when `notable` is absent. A
		# distinctive value, so "it fell back" is observable rather than
		# inferred from an empty array.
		"function buildContentGroups() { return { security: "
		"[{ title: 'DERIVED', severity: 'warning' }] }; }",
	])
	with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
		fh.write(prelude + "\n" + snippet + "\n")
		path = fh.name
	try:
		out = subprocess.run([shutil.which("node"), path], capture_output=True, text=True, timeout=30)
		assert out.returncode == 0, out.stderr
		return json.loads(out.stdout)
	finally:
		os.unlink(path)


class PageNotableOrderingTests(unittest.TestCase):
	"""Two implementations of one ranking. The table comparison always runs;
	the behavioural half needs node and skips without it."""

	def _emit(self, research, tool_id="brew:order"):
		with contextlib.redirect_stderr(io.StringIO()):
			tool = assemble.build_tool(
				_cand(tool_id, tool_id.split(":")[1], "brew", "1.0.0", "1.0.1"), research)
		return tool["security"]["notable"]

	def test_the_page_rank_table_agrees_with_assembly_tier_for_tier(self):
		"""`NOTABLE_RANK` ascends where `_NOTABLE_SEVERITY_RANK` descends, over
		the same five-value vocabulary. This is the assertion whose absence let
		the page keep `unknown` below `low` after assembly moved it above."""
		page = _rank_table("NOTABLE_RANK")
		for sev in assemble._CVE_SEVERITIES:
			self.assertIn(sev, page, "the page's rank table lost %r" % sev)
		by_assembly = sorted(assemble._CVE_SEVERITIES,
			key=lambda s: -assemble._NOTABLE_SEVERITY_RANK[s])
		by_page = sorted(assemble._CVE_SEVERITIES, key=lambda s: page[s])
		self.assertEqual(by_page, by_assembly)
		# …and the page's own item vocabulary rides the same scale, so a
		# notable promoted out of a relevancy item interleaves correctly.
		for cve_sev, item_sev in (("critical", "incompatible"), ("high", "warning"),
				("medium", "notable"), ("low", "info")):
			self.assertEqual(page[cve_sev], page[item_sev], cve_sev)

	def test_an_ungraded_notable_is_not_painted_the_faintest_class(self):
		"""`unknown` means "nobody published a grade", never "a small flaw" —
		and every route into notable[] that leaves the grade absent already
		cleared `notable`+ on this machine's own scale (R5 clauses 3 and 4).
		Painting it `info` put it in the lightest ink the card has."""
		sev, rank = _sev_table("NOTABLE_SEV"), _rank_table("NOTABLE_RANK")
		self.assertNotEqual(sev["unknown"], sev["low"])
		self.assertLess(rank[sev["unknown"]], rank[sev["low"]])

	@unittest.skipUnless(shutil.which("node"), "node not available")
	def test_the_page_renders_the_order_assembly_emitted(self):
		"""End to end across the seam: whatever order assembly emits is the
		order the page's own notableItems() puts on the card."""
		cases = {
			# R5 clause 3's item — id-less, ungraded, lands on this machine —
			# against three `low` CVEs that do not. Assembly ranks it first and
			# the cap keeps it; the page must not sort it back down.
			"touchpoint beats higher-rated misses": {
				"headliners": [_hl("security", "notable",
					"Fixes CVE-2026-1001, CVE-2026-1002, CVE-2026-1003.")],
				"relevancy": [_rel("security", "notable",
					"The wrapper this machine runs takes the injected argv.")],
				"security": {
					"cve_severities": [_sev("CVE-2026-100%d" % i, "low") for i in (1, 2, 3)],
					"notable": [_not("low %d" % i, "low", cve_id="CVE-2026-100%d" % i)
						for i in (1, 2, 3)]
						+ [_not("Command injection in the wrapper this machine runs.",
							"unknown", affects_me=True)]}},
			# No affects_me anywhere: the tier order alone has to match.
			"unknown outranks low on severity alone": _sec_research(security={"notable": [
				_not("graded low", "low"), _not("nobody graded it", "unknown"),
				_not("graded medium", "medium")]}),
		}
		for label, research in cases.items():
			with self.subTest(label):
				emitted = self._emit(research)
				rendered = _run_page(
					"console.log(JSON.stringify(notableItems({ notable: normNotable(%s) }, {})));"
					% json.dumps(emitted))
				self.assertEqual([r["text"] for r in rendered], [n["summary"] for n in emitted])
		# …and the item the whole ranking exists to protect does not arrive in
		# the faintest class the card has.
		emitted = self._emit(cases["touchpoint beats higher-rated misses"])
		rendered = _run_page(
			"console.log(JSON.stringify(notableItems({ notable: normNotable(%s) }, {})));"
			% json.dumps(emitted))
		self.assertTrue(rendered[0]["mine"])
		self.assertNotEqual(rendered[0]["severity"], "info")
		self.assertEqual(rendered[-1]["severity"], "info")   # the graded `low` CVEs still are


class NotableAbsentVersusEmptyTests(unittest.TestCase):
	"""`notable: []` and no `notable` key are two different answers. Empty says
	the selection ran and nothing qualified — the single-column card. Absent
	says the question was never put, and the page derives the column the way it
	did before the field existed. Emitting `[]` for research that predates the
	field asserted "nothing here is notable" about 77 of the 78 tools in the
	recorded run and deleted the security column from every one of them."""

	def _sec(self, research, tool_id="brew:empt"):
		with contextlib.redirect_stderr(io.StringIO()):
			tool = assemble.build_tool(
				_cand(tool_id, tool_id.split(":")[1], "brew", "1.0.0", "1.0.1"), research)
		return tool["security"]

	def test_research_that_never_mentions_security_omits_the_key(self):
		sec = self._sec({"headliners": [_hl("security", "notable", "Fixes CVE-2026-1001.")],
			"relevancy": [_rel("security", "warning", "Reaches this machine's sshd config.")]})
		self.assertNotIn("notable", sec)
		self.assertTrue(sec["has_security"])   # the tool is still a security card

	def test_a_security_block_with_nothing_notable_emits_the_empty_answer(self):
		sec = self._sec(_sec_research(security={"cve_severities": [_sev("CVE-2026-1001", "high")]}))
		self.assertEqual(sec["notable"], [])
		sec = self._sec(_sec_research(security={"notable": []}))
		self.assertEqual(sec["notable"], [])

	def test_assembly_answers_for_itself_where_it_forces_the_list_empty(self):
		"""The two cases where assembly, not research, decides: a brew-health
		tool and research that told us nothing. Both are real answers, so both
		carry the key."""
		_, candidate, research, _ = _fixture("S10")
		with contextlib.redirect_stderr(io.StringIO()):
			health = assemble.build_health_tool(candidate, research)
		self.assertEqual(health["security"]["notable"], [])
		self.assertEqual(self._sec({"headliners": [], "research_error": "subagent timed out"},
			tool_id="brew:err")["notable"], [])

	def test_a_block_too_drifted_to_read_is_not_an_answer(self):
		for shape in ("none", None, [{"notable": []}]):
			with self.subTest(repr(shape)):
				self.assertNotIn("notable", self._sec(_sec_research(security=shape)))

	@unittest.skipUnless(shutil.which("node"), "node not available")
	def test_the_page_keeps_the_two_answers_apart(self):
		self.assertIsNone(_run_page("console.log(JSON.stringify(normNotable(undefined)));"))
		self.assertEqual(_run_page("console.log(JSON.stringify(normNotable([])));"), [])
		# [] → nothing to show, which is what collapses the card to one column.
		self.assertEqual(_run_page("console.log(JSON.stringify(notableItems({ notable: [] }, {})));"), [])
		# absent → the derivation, reached through the stubbed buildContentGroups().
		self.assertEqual(
			[i["text"] for i in _run_page(
				"console.log(JSON.stringify(notableItems({ notable: null }, {})));")],
			["DERIVED"])


if __name__ == "__main__":
	unittest.main(verbosity=2)
