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
"""
import contextlib
import io
import json
import os
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
		"relevancy": [], "links": [], "context": [], "suggestions": []},
	{"id": "brew:ssh-copy-id",
		"headliners": [_hl("security", "notable", "Same source tarball: CVE-2026-53789")],
		"relevancy": [], "links": [], "context": [], "suggestions": []},
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
			self.assertEqual(set(tool["security"]),
				{"cve_ids", "cve_count", "cve_claimed_count", "has_security", "security_only", "impact"})

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

	def test_zero_dot_x_major_is_caught(self):
		uv = next(t for t in self.report["tools"] if t["id"] == "mise:uv")
		self.assertEqual(uv["version_delta"], "major")
		self.assertEqual(uv["risk_level"], "elevated")
		self.assertFalse(uv["suggestions"][0]["pre_accept"])


if __name__ == "__main__":
	unittest.main(verbosity=2)
