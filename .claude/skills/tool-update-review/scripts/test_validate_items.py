#!/usr/bin/env python3
"""
test_validate_items.py — the six validator stages, the eighteen invariants,
every normalization, and the degradation contract.
Usage: python3 test_validate_items.py [-v]

Stdlib `unittest` only, same constraint as `test_assemble.py`.

Seven groups:

1. **The constraint.** Property tests asserting the validator changes nothing
   it is not licensed to change: no item is removed, no severity re-rated, no
   title truncated, no unknown tag dropped, no non-path moved out of
   `evidence[]`. This is acceptance criterion 1, and it is the group to read
   first — the measured defect is a rule-driven trim that moved `brew:libpq`
   into `security_auto`, pre-accepted, with 10 CVEs.
2. The eighteen invariants, one test each, by their code.
3. The normalizations §5.3 licenses, and only those.
4. Evidence resolution: the three outcomes, and why `W-EVID-ROOT` is a
   configuration finding rather than a checker defect.
5. The structural outlet and its preconditions.
6. Impact and initial bucketing, including the two heuristics that were
   removed and the third that §D row 4 dropped.
7. **Degradation.** Same doctrine as `LoadResearchDegradationTests`: a
   research file is subagent output, so any member can be any shape, and one
   malformed entry must cost that entry — never the run. The fuzz baseline is
   0 of 346 aborting cases and this group is how it stays there.
"""
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items as model  # noqa: E402
import validate_items as V  # noqa: E402


FIXTURE_SESSION, FIXTURE_ROOTS, FIXTURE_UNCONFIGURED = V.fixture_session()
MANIFEST_ROOT = FIXTURE_ROOTS[0]


def _candidate(**kw):
	base = {"id": "brew:x", "name": "x", "source": "brew",
		"current_version": "1.0.0", "latest_version": "1.0.1", "pinned": False}
	base.update(kw)
	return base


def _item(**kw):
	"""A minimal conforming item. Override one field per test so the finding a
	test asserts is the only thing that could have produced it."""
	base = {
		"anchor": {"kind": "issue", "value": "org/repo#1"},
		"title": "A conforming title",
		"tags": ["fix"],
		"severity": "info",
		"change": {"version": "1.0.1", "citation": "upstream said this", "link_index": None},
	}
	base.update(kw)
	return base


def validate_one(research=None, candidate=None, roots=None, unconfigured=None,
		manifest_root=None, watch_topics=None):
	"""→ (view, findings). One tool, straight through V2–V6, with the fixture's
	own roots so nothing depends on what sits beside the repo."""
	findings = V.Findings()
	resolver = V.RootResolver(roots if roots is not None else FIXTURE_ROOTS,
		unconfigured_roots=FIXTURE_UNCONFIGURED if unconfigured is None else unconfigured)
	manifest = V.Manifest(manifest_root or MANIFEST_ROOT)
	view = V.validate_tool(candidate or _candidate(), research, findings, resolver, manifest,
		watch_topics=watch_topics)
	return view, findings


def codes(research=None, **kw):
	return sorted({f["code"] for f in validate_one(research, **kw)[1].entries})


def session_without_snapshot():
	"""A copy of the published fixture session with watch-items.json removed —
	the W-WATCH-UNCHECKED route. A copy, because the fixture is read-only."""
	td = tempfile.mkdtemp()
	session = os.path.join(td, "session")
	shutil.copytree(FIXTURE_SESSION, session)
	os.remove(os.path.join(session, "watch-items.json"))
	return td, session


def session_with(research_files, collect=None):
	"""A throwaway session dir. Returns the validation document."""
	td = tempfile.mkdtemp()
	try:
		os.makedirs(os.path.join(td, "research"))
		with open(os.path.join(td, "collect.json"), "w", encoding="utf-8") as fh:
			json.dump(collect if collect is not None else {
				"generated_at": "2026-09-07T00:00:00Z", "machine": {},
				"brew": [_candidate()]}, fh)
		for name, payload in research_files.items():
			path = os.path.join(td, "research", name)
			with open(path, "w", encoding="utf-8") as fh:
				if isinstance(payload, str):
					fh.write(payload)
				else:
					json.dump(payload, fh)
		# collect.json shape warnings are assemble.py's stderr channel and are
		# tested there; swallow them so a degradation sweep does not bury the
		# assertion output.
		with contextlib.redirect_stderr(io.StringIO()):
			return V.validate_session(td, FIXTURE_ROOTS, manifest_root=MANIFEST_ROOT,
				unconfigured_roots=FIXTURE_UNCONFIGURED)
	finally:
		shutil.rmtree(td, ignore_errors=True)


# ── 1. The constraint: it reports, it does not judge ────────────────────────
class NeverMutatesTests(unittest.TestCase):
	"""REDESIGN.md §A / §C3 / criterion 1. The deterministic layer validates,
	normalizes, counts, buckets and calculates impact. It never deletes, trims
	or re-rates an item on a regex or heuristic rule."""

	HOSTILE_ITEMS = [
		{"anchor": {"kind": "cve", "value": "CVE-26-1"}, "title": "x" * 400,
			"tags": ["securty", "hardening"], "severity": "critical"},
		{"anchor": {"kind": "none", "slug": "s"}, "title": "t", "tags": [],
			"severity": "warning"},
		_item(severity="incompatible", local={"direction": "does_not_reach",
			"effect": "benefit", "statement": "s", "evidence": []}),
		_item(anchor={"kind": "issue", "value": "org/repo#9"},
			local={"direction": "reaches", "effect": "risk", "statement": "s",
				"evidence": ["this is prose, not a path", "no/such/file.txt"]}),
	]

	def setUp(self):
		self.view, self.findings = validate_one({"id": "brew:x", "links": [],
			"items": [dict(i) for i in self.HOSTILE_ITEMS]})

	def test_every_item_survives(self):
		self.assertEqual(len(self.view["items"]), len(self.HOSTILE_ITEMS))

	def test_no_severity_is_re_rated(self):
		self.assertEqual(sorted(i["severity"] for i in self.view["items"]),
			sorted(i["severity"] for i in self.HOSTILE_ITEMS))

	def test_no_title_is_truncated(self):
		"""The 578-character title is a defect and it is reported as one. It is
		not shortened: truncating hides a data defect behind a clamp."""
		self.assertIn(400, [len(i["title"]) for i in self.view["items"]])
		self.assertIn("W-TITLE-LONG", {f["code"] for f in self.findings.entries})

	def test_an_unknown_tag_is_kept_verbatim_on_the_item(self):
		tags = [t for i in self.view["items"] for t in i["tags"]]
		self.assertIn("securty", tags)
		self.assertIn("hardening", tags)

	def test_a_non_path_is_never_moved_out_of_evidence(self):
		"""Auto-moving is a regex deciding what a field means — the class §C3
		forbids — and it would paper over a broken path that reads like prose."""
		reaching = [i for i in self.view["items"]
			if (i.get("local") or {}).get("direction") == "reaches"][0]
		self.assertIn("this is prose, not a path", reaching["local"]["evidence"])
		self.assertEqual(reaching["local"]["citations"], [])

	def test_every_hostile_shape_is_reported_rather_than_silently_absorbed(self):
		self.assertTrue(self.findings.entries)
		for entry in self.findings.entries:
			self.assertIn(entry["code"], model.FINDING_CODES)
			self.assertTrue(entry["message"])

	def test_a_duplicate_id_is_kept_and_suffixed_never_merged(self):
		"""Merging is deletion plus a severity choice — the `brew:sops` defect,
		where the dedup rule's survivor silently downgrades the card."""
		view, findings = validate_one({"id": "brew:sops", "links": [], "items": [
			_item(anchor={"kind": "issue", "value": "getsops/sops#2245"},
				title="the headliner", severity="warning",
				local={"direction": "unclear", "effect": "none", "statement": "s",
					"evidence": []}),
			_item(anchor={"kind": "issue", "value": "getsops/sops#2245"},
				title="the relevancy item", severity="notable"),
		]}, candidate=_candidate(id="brew:sops", name="sops"))
		self.assertEqual(len(view["items"]), 2)
		self.assertEqual({i["id"] for i in view["items"]},
			{"brew:sops#issue:getsops%2Fsops%232245",
			 "brew:sops#issue:getsops%2Fsops%232245~2"})
		self.assertIn("E-ITEM-DUP-ANCHOR", {f["code"] for f in findings.entries})
		self.assertEqual(sorted(i["severity"] for i in view["items"]),
			["notable", "warning"])

	def test_a_wrong_typed_member_is_quarantined_not_dropped(self):
		"""Today's `as_item_list` drops it. Under §C3 nothing deterministic
		deletes content a human would have seen."""
		view, findings = validate_one({"id": "brew:x", "links": [],
			"items": ["a bare string", _item()]})
		self.assertEqual(len(view["items"]), 1)
		self.assertEqual([q["value"] for q in view["quarantine"]], ["a bare string"])
		self.assertIn("W-MEMBER-QUARANTINED", {f["code"] for f in findings.entries})

	def test_a_quarantined_member_from_any_array_lands_on_the_tool(self):
		view, findings = validate_one({"id": "brew:x", "links": [],
			"items": ["a bare item", _item()],
			"suggestions": ["a bare suggestion", {"id": "brew:x:e", "kind": "edit",
				"target_files": []}]})
		self.assertEqual(sorted(q["field"] for q in view["quarantine"]),
			["items", "suggestions"])
		self.assertEqual(len(view["items"]), 1)
		self.assertEqual(len(view["suggestions"]), 1)

	def test_the_bucket_is_labelled_initial_and_carries_its_inputs(self):
		"""A baseline for convergence to review, not a decision."""
		self.assertIn("initial_review_bucket", self.view)
		self.assertNotIn("review_bucket", self.view)
		self.assertEqual(set(self.view["bucket_inputs"]),
			{"has_security", "security_only", "impact", "version_delta", "runnable"})


# ── 2. The eighteen invariants ──────────────────────────────────────────────
class InvariantTests(unittest.TestCase):
	def assertCode(self, code, research, **kw):
		got = codes(research, **kw)
		self.assertIn(code, got, "expected {} in {}".format(code, got))

	def assertNoCode(self, code, research, **kw):
		self.assertNotIn(code, codes(research, **kw))

	def test_i1_an_item_with_neither_change_nor_local_says_nothing(self):
		self.assertCode("E-ITEM-EMPTY", {"id": "brew:x", "links": [],
			"items": [_item(change=None)]})
		self.assertNoCode("E-ITEM-EMPTY", {"id": "brew:x", "links": [], "items": [_item()]})

	def test_i2_incompatible_requires_reaches_and_risk(self):
		research = {"id": "brew:x", "links": [], "items": [_item(severity="incompatible",
			local={"direction": "does_not_reach", "effect": "risk", "statement": "s",
				"evidence": []})]}
		self.assertCode("E-SEV-INCOMPAT-UNGROUNDED", research)
		grounded = {"id": "brew:x", "links": [], "items": [_item(severity="incompatible",
			local={"direction": "reaches", "effect": "risk", "statement": "s",
				"evidence": [{"path": "Brewfile"}]})]}
		self.assertNoCode("E-SEV-INCOMPAT-UNGROUNDED", grounded)

	def test_i3_warning_requires_a_local_block(self):
		self.assertCode("E-SEV-WARNING-UNGROUNDED", {"id": "brew:x", "links": [],
			"items": [_item(severity="warning")]})

	def test_i4_the_security_block_and_the_security_tag_travel_together(self):
		self.assertCode("E-SEC-BLOCK-MISSING", {"id": "brew:x", "links": [],
			"items": [_item(tags=["security"])]})
		self.assertCode("E-SEC-BLOCK-ORPHAN", {"id": "brew:x", "links": [],
			"items": [_item(security={"rating": "low", "rating_basis": "nvd",
				"exploited_in_wild": False})]})

	def test_i5_a_cve_id_matches_the_cve_grammar(self):
		self.assertCode("E-CVE-MALFORMED", {"id": "brew:x", "links": [],
			"items": [_item(tags=["security"], security={"cve_id": "CVE-1",
				"rating": "low", "rating_basis": "nvd", "exploited_in_wild": False})]})

	def test_i6_a_rating_needs_a_basis(self):
		self.assertCode("E-SEC-RATING-UNBASED", {"id": "brew:x", "links": [],
			"items": [_item(tags=["security"], security={"cve_id": None,
				"rating": "high", "rating_basis": "unrated", "exploited_in_wild": False})]})
		self.assertNoCode("E-SEC-RATING-UNBASED", {"id": "brew:x", "links": [],
			"items": [_item(tags=["security"], security={"cve_id": None,
				"rating": "unknown", "rating_basis": "unrated", "exploited_in_wild": False})]})

	def test_i7_change_present_means_a_verbatim_citation(self):
		for hollow in ("", "   ", None):
			with self.subTest(repr(hollow)):
				self.assertCode("E-CHANGE-UNCITED", {"id": "brew:x", "links": [],
					"items": [_item(change={"version": "1.0.1", "citation": hollow})]})

	def test_i8_link_index_is_in_range(self):
		self.assertCode("E-LINK-INDEX", {"id": "brew:x",
			"links": [{"type": "changelog", "url": "https://example.com"}],
			"items": [_item(change={"version": "1", "citation": "c", "link_index": 1})]})
		self.assertNoCode("E-LINK-INDEX", {"id": "brew:x",
			"links": [{"type": "changelog", "url": "https://example.com"}],
			"items": [_item(change={"version": "1", "citation": "c", "link_index": 0})]})

	def test_i9_the_anchor_matches_the_grammar_for_its_kind(self):
		self.assertCode("E-ANCHOR-MALFORMED", {"id": "brew:x", "links": [],
			"items": [_item(anchor={"kind": "cve", "value": "CVE-26-1"})]})

	def test_i10_two_items_deriving_one_id(self):
		self.assertCode("E-ITEM-DUP-ANCHOR", {"id": "brew:x", "links": [],
			"items": [_item(), _item()]})

	def test_i11_unknown_and_absent_tags(self):
		self.assertCode("E-TAG-UNKNOWN", {"id": "brew:x", "links": [],
			"items": [_item(tags=["hardening", "fix"])]})
		self.assertCode("E-TAG-NONE", {"id": "brew:x", "links": [],
			"items": [_item(tags=["hardening"])]})

	def test_i12_the_three_evidence_outcomes(self):
		def local(evidence):
			return {"id": "brew:x", "links": [], "items": [_item(
				local={"direction": "unclear", "effect": "none", "statement": "s",
					"evidence": evidence})]}
		self.assertCode("E-EVID-MALFORMED", local(["a phrase, not a path"]))
		self.assertCode("E-EVID-404", local(["no/such/file.txt"]))
		self.assertCode("W-EVID-ROOT", local(["tieto/konehuone/sysmi/notes.md"]))
		self.assertEqual(codes(local(["Brewfile:6"])), [])

	def test_i13_a_checker_flag_that_disagrees_with_the_items(self):
		self.assertCode("E-FLAG-DISAGREE", {"id": "brew:x", "links": [],
			"flags": {"has_security": True}, "items": [_item()]})
		self.assertNoCode("E-FLAG-DISAGREE", {"id": "brew:x", "links": [],
			"flags": {"has_security": False}, "items": [_item()]})

	def test_i13_the_validators_value_wins_on_disagreement(self):
		view, _ = validate_one({"id": "brew:x", "links": [],
			"flags": {"has_security": True, "worst_severity": "incompatible"},
			"items": [_item()]})
		self.assertFalse(view["flags"]["has_security"])
		self.assertEqual(view["flags"]["worst_severity"], "info")

	def test_i14_reaches_needs_evidence(self):
		self.assertCode("E-REACHES-UNEVIDENCED", {"id": "brew:x", "links": [],
			"items": [_item(local={"direction": "reaches", "effect": "risk",
				"statement": "s", "evidence": []})]})

	def test_i15_needs_attention_needs_a_non_upgrade_suggestion(self):
		self.assertCode("W-ATTENTION-NOSUG", {"id": "brew:x", "links": [],
			"items": [_item()],
			"config_status": {"state": "needs_attention", "detail": "d", "evidence": []},
			"suggestions": [{"id": "brew:x:upgrade", "kind": "upgrade"}]})
		self.assertNoCode("W-ATTENTION-NOSUG", {"id": "brew:x", "links": [],
			"items": [_item()],
			"config_status": {"state": "needs_attention", "detail": "d", "evidence": []},
			"suggestions": [{"id": "brew:x:upgrade", "kind": "upgrade"},
				{"id": "brew:x:edit", "kind": "edit", "target_files": []}]})

	def test_i16_a_structural_precondition_that_does_not_hold(self):
		self.assertCode("E-STRUCT-PRECOND", {"id": "brew:x", "links": [], "items": [_item()],
			"suggestions": [{"id": "brew:x:s", "kind": "structural", "structural": {
				"op": "manifest_remove", "subjects": [{"type": "formula", "name": "absent"}],
				"manifest": "Brewfile", "from": {"type": "formula", "name": "absent"}}}]})

	def test_i17_intel_brewfile_in_a_manifest_and_in_a_target_file(self):
		self.assertCode("E-INTEL-BREWFILE", {"id": "brew:x", "links": [], "items": [_item()],
			"suggestions": [{"id": "brew:x:s", "kind": "structural", "structural": {
				"op": "manifest_remove", "subjects": [{"type": "formula", "name": "sops"}],
				"manifest": "intel.Brewfile", "from": {"type": "formula", "name": "sops"}}}]})
		self.assertCode("E-INTEL-BREWFILE", {"id": "brew:x", "links": [], "items": [_item()],
			"suggestions": [{"id": "brew:x:e", "kind": "edit",
				"target_files": [{"path": "intel.Brewfile"}]}]})

	def test_i17_matches_a_path_that_merely_ends_in_the_forbidden_manifest(self):
		"""A string-equality test waves `./intel.Brewfile` through."""
		for path in ("intel.Brewfile", "./intel.Brewfile", "macos-setup/intel.Brewfile"):
			with self.subTest(path):
				self.assertCode("E-INTEL-BREWFILE", {"id": "brew:x", "links": [],
					"items": [_item()], "suggestions": [{"id": "brew:x:e", "kind": "edit",
						"target_files": [{"path": path}]}]})
		self.assertNoCode("E-INTEL-BREWFILE", {"id": "brew:x", "links": [],
			"items": [_item()], "suggestions": [{"id": "brew:x:e", "kind": "edit",
				"target_files": [{"path": "Brewfile"}]}]})

	def test_i18_suggestion_ids_are_unique_across_the_whole_report(self):
		document = session_with({"g.json": [
			{"id": "brew:x", "links": [], "items": [_item()],
				"suggestions": [{"id": "shared:id", "kind": "edit", "target_files": []}]},
			{"id": "brew:y", "links": [], "items": [_item()],
				"suggestions": [{"id": "shared:id", "kind": "edit", "target_files": []}]},
		]}, collect={"generated_at": "2026-09-07T00:00:00Z", "machine": {},
			"brew": [_candidate(), _candidate(id="brew:y", name="y")]})
		self.assertIn("W-SUG-DUP-ID", document["counts"]["by_code"])

	def test_criterion_2_a_checker_may_not_emit_a_bucket_or_an_auto_approval(self):
		for flag in model.VALIDATOR_ONLY_FLAGS:
			with self.subTest(flag):
				self.assertCode("E-FLAG-FORBIDDEN",
					{"id": "brew:x", "links": [], "items": [_item()], flag: "anything"})
				self.assertCode("E-FLAG-FORBIDDEN", {"id": "brew:x", "links": [],
					"items": [_item()], "flags": {flag: "anything"}})

	def test_l2_the_title_bar_is_reported_at_the_documented_length(self):
		self.assertNoCode("W-TITLE-LONG", {"id": "brew:x", "links": [],
			"items": [_item(title="x" * model.TITLE_MAX_CHARS)]})
		self.assertCode("W-TITLE-LONG", {"id": "brew:x", "links": [],
			"items": [_item(title="x" * (model.TITLE_MAX_CHARS + 1))]})
		self.assertCode("W-TITLE-LONG", {"id": "brew:x", "links": [],
			"items": [_item(title="one line\nand another")]})

	def test_l2_body_is_the_field_the_overflow_belongs_in(self):
		view, _ = validate_one({"id": "brew:x", "links": [],
			"items": [_item(body="the paragraph that used to be crammed into title")]})
		self.assertEqual(view["items"][0]["body"],
			"the paragraph that used to be crammed into title")

	def test_citations_are_checked_for_shape_and_never_fetched(self):
		research = {"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": [], "citations": [
					{"kind": "blogpost", "text": "", "url": "not a url"}]})]}
		got = codes(research)
		for code in ("E-CITE-KIND", "E-CITE-EMPTY", "E-CITE-URL"):
			self.assertIn(code, got)

	def test_an_upstream_source_citation_is_never_path_checked(self):
		"""`extensions/podman/.../rosetta.ts` is an upstream path that will
		never resolve locally and is excellent evidence. Today it warns."""
		research = {"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": [], "citations": [{"kind": "upstream_source",
					"text": "extensions/podman/packages/extension/src/utils/rosetta.ts",
					"url": None}]})]}
		self.assertEqual(codes(research), [])


# ── 3. The licensed normalizations, and only those ──────────────────────────
class NormalizationTests(unittest.TestCase):
	def test_a_null_array_and_an_absent_one_mean_the_same_and_say_nothing(self):
		view, findings = validate_one({"id": "brew:x", "links": None, "items": None,
			"suggestions": None})
		self.assertEqual(view["items"], [])
		self.assertEqual([f["code"] for f in findings.entries], [])

	def test_a_non_list_where_a_list_belongs_is_coerced_and_named(self):
		view, findings = validate_one({"id": "brew:x", "links": [],
			"items": "no notable changes"})
		self.assertEqual(view["items"], [])
		entry = [f for f in findings.entries if f["code"] == "W-SHAPE-COERCED"][0]
		self.assertIn("str", entry["message"])

	def test_evidence_shorthand_becomes_the_object_form(self):
		view, _ = validate_one({"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": ["Brewfile:6", "Brewfile:5-7", "Brewfile"]})]})
		self.assertEqual(view["items"][0]["local"]["evidence"], [
			{"path": "Brewfile", "lines": [6]},
			{"path": "Brewfile", "lines": [[5, 7]]},
			{"path": "Brewfile"}])

	def test_the_note_field_survives_and_is_never_load_bearing(self):
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": [{"path": "Brewfile", "note": "the sops line"}]})]})
		self.assertEqual(view["items"][0]["local"]["evidence"][0]["note"], "the sops line")
		self.assertEqual([f["code"] for f in findings.entries], [])

	def test_an_empty_lines_list_is_the_same_as_no_lines(self):
		view, _ = validate_one({"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": [{"path": "Brewfile", "lines": []}]})]})
		self.assertEqual(view["items"][0]["local"]["evidence"], [{"path": "Brewfile"}])

	def test_a_bad_line_locator_is_reported_and_left_exactly_as_written(self):
		"""The normalization is licensed only when it succeeds. Half-rewriting a
		locator list would lose the part it could not read."""
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": [{"path": "Brewfile", "lines": ["six", True, [1, 2, 3]]}]})]})
		self.assertEqual(view["items"][0]["local"]["evidence"],
			[{"path": "Brewfile", "lines": ["six", True, [1, 2, 3]]}])
		self.assertEqual({f["code"] for f in findings.entries}, {"E-FIELD-TYPE"})

	def test_an_evidence_object_keeps_keys_the_schema_did_not_anticipate(self):
		"""Rebuilding the object from known keys drops a checker's extra field
		silently, which is content a human would have read."""
		view, _ = validate_one({"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": [{"path": "Brewfile", "lines": [6], "note": "n",
					"why": "THIS TEXT IS LOAD-BEARING"}]})]})
		self.assertEqual(view["items"][0]["local"]["evidence"][0],
			{"path": "Brewfile", "lines": [6], "note": "n", "why": "THIS TEXT IS LOAD-BEARING"})

	def test_a_wrong_typed_block_is_reported_and_left_on_the_item(self):
		"""V2's rule: the item survives with the offending field as written. The
		finding's `value` is bounded for readability, so nulling the field would
		make the only surviving copy the truncated one."""
		authored = "upstream said: " + "x" * 400
		for field in ("change", "local", "security"):
			with self.subTest(field):
				view, findings = validate_one({"id": "brew:x", "links": [],
					"items": [_item(**{field: authored})]})
				got = {f["code"] for f in findings.entries}
				self.assertEqual(view["items"][0][field], authored)
				self.assertIn("E-FIELD-TYPE", got)
				# Keeping the field means every later read of it has to guard:
				# a truthy non-dict reaching `(x or {}).get(...)` raises, and
				# the tool loses its derived axes to a crash.
				self.assertNotIn("E-VALIDATOR-CRASH", got)
				self.assertIsNone(view["validator_error"])

	def test_a_quarantined_member_is_stored_whole_not_as_a_truncated_repr(self):
		member = ["deeply", {"nested": ["x" * 500]}]
		view, _ = validate_one({"id": "brew:x", "links": [], "items": [member, _item()]})
		self.assertEqual(view["quarantine"][0]["value"], member)

	def test_a_config_status_citation_quarantine_reaches_the_tool(self):
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [_item()],
			"config_status": {"state": "up_to_date", "detail": "d", "evidence": [],
				"citations": ["a bare string"]}})
		self.assertEqual([q["field"] for q in view["quarantine"]],
			["config_status.citations"])
		self.assertIn("W-MEMBER-QUARANTINED", {f["code"] for f in findings.entries})

	def test_config_status_gets_the_same_evidence_split(self):
		"""133 of the recorded run's 272 warnings came from this one field."""
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [_item()],
			"config_status": {"state": "up_to_date", "detail": None,
				"evidence": ["changelog.md — 2026-07-06 entry"],
				"citations": [{"kind": "prior_review", "text": "the 2026-07-06 entry",
					"url": None}]}})
		self.assertEqual(view["config_status"]["detail"], "")
		self.assertEqual({f["code"] for f in findings.entries}, {"E-EVID-MALFORMED"})
		self.assertEqual(len(view["config_status"]["citations"]), 1)

	def test_ordering_is_applied_to_the_emitted_items(self):
		view, _ = validate_one({"id": "brew:x", "links": [], "items": [
			_item(anchor={"kind": "issue", "value": "org/repo#2"}, tags=["chore"]),
			_item(anchor={"kind": "issue", "value": "org/repo#1"}, tags=["security"],
				security={"cve_id": None, "rating": "unknown", "rating_basis": "unrated",
					"exploited_in_wild": False}),
		]})
		self.assertEqual([model.primary_group(i) for i in view["items"]],
			["security", "notes"])


# ── 4. Evidence resolution ──────────────────────────────────────────────────
class ManifestTests(unittest.TestCase):
	def _manifest(self, brewfile):
		td = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, td, True)
		with open(os.path.join(td, "Brewfile"), "w", encoding="utf-8") as fh:
			fh.write(brewfile)
		return V.Manifest(td)

	def test_a_comment_after_a_header_block_is_not_a_section(self):
		"""A header is rule-title-rule, so the CLOSING rule must not promote the
		next `##` line. The real Brewfile survives this only because a blank line
		follows each header, which is luck rather than a guarantee."""
		manifest = self._manifest(
			'## ====\n## CORE\n## ====\n## Install mac app store cli\nbrew "mas"\n')
		self.assertEqual(manifest.section_of("formula", "mas"), "CORE")

	def test_a_header_block_still_opens_its_section(self):
		manifest = self._manifest(
			'## ====\n## CORE\n## ====\n\nbrew "mas"\n\n'
			'## ====\n## NETWORK\n## ====\n\nbrew "nmap"\n')
		self.assertEqual(manifest.section_of("formula", "mas"), "CORE")
		self.assertEqual(manifest.section_of("formula", "nmap"), "NETWORK")

	def test_a_formula_ref_is_looked_up_under_the_brewfile_keyword(self):
		"""`Ref.type` says `formula`; the Brewfile line says `brew`. A missed
		translation reads as "not in the manifest"."""
		manifest = self._manifest('## ====\n## CORE\n## ====\n\nbrew "sops"\n')
		self.assertTrue(manifest.has("formula", "sops"))
		self.assertEqual(manifest.section_of("formula", "sops"), "CORE")

	def test_an_absent_manifest_is_unreadable_not_empty(self):
		with tempfile.TemporaryDirectory() as td:
			manifest = V.Manifest(td)
			self.assertFalse(manifest.readable)
			self.assertFalse(manifest.tasks_readable)

	def test_an_undecodable_manifest_is_unreadable_never_an_abort(self):
		"""One non-UTF-8 byte in the Brewfile (an em dash truncated
		mid-character, reproduced on the real repo file) used to raise
		UnicodeDecodeError out of Manifest.__init__ and abort the whole run
		before a single tool was built — no report.json, no validation.json,
		no assemble.warn. The designed degradation (`readable=False` →
		W-STRUCT-UNCHECKED) existed and was unreachable behind the
		OSError-only handler."""
		td = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, td, True)
		with open(os.path.join(td, "Brewfile"), "wb") as fh:
			fh.write(b'brew "sops"\n# GNU coreutils \xe2\x80 the good ones\n')
		with open(os.path.join(td, "setup.sh"), "wb") as fh:
			fh.write(b'[[ "${1}" = "install" ]]\n# \xe2\x80\n')
		manifest = V.Manifest(td)
		self.assertFalse(manifest.readable)
		self.assertFalse(manifest.tasks_readable)
		# And the degradation is reported, not silent: a structural op against
		# the undecodable manifest is "unchecked", never "passing" or a crash.
		research = {"id": "brew:x", "links": [], "items": [_item()],
			"suggestions": [{"id": "brew:x:s", "kind": "structural", "target_files": [],
				"structural": {"op": "manifest_remove", "manifest": "Brewfile",
					"subjects": [{"type": "formula", "name": "sops"}],
					"from": {"type": "formula", "name": "sops"}}}]}
		found = codes(research, manifest_root=td)
		self.assertIn("W-STRUCT-UNCHECKED", found)
		self.assertNotIn("E-STRUCT-PRECOND", found)


class RootResolverTests(unittest.TestCase):
	def test_a_path_under_a_configured_root_resolves(self):
		resolver = V.RootResolver(FIXTURE_ROOTS, unconfigured_roots=[])
		self.assertEqual(resolver.resolve("Brewfile"), ("ok", None))
		self.assertEqual(resolver.resolve("dotfiles/config/git/config"), ("ok", None))

	def test_a_leading_repo_directory_name_is_stripped_and_retried(self):
		resolver = V.RootResolver(FIXTURE_ROOTS, unconfigured_roots=[])
		self.assertEqual(resolver.resolve("macos-setup/Brewfile"), ("ok", None))

	def test_an_unconfigured_repo_is_a_configuration_finding_not_a_defect(self):
		"""7 of the recorded run's 9 bare-path failures were `tieto/…` — a real
		repo the checker legitimately read, that is simply not in the root list.
		Conflating that with a broken path is how the one real defect stayed
		buried among 272 warnings."""
		resolver = V.RootResolver(FIXTURE_ROOTS, unconfigured_roots=FIXTURE_UNCONFIGURED)
		self.assertEqual(resolver.resolve("tieto/konehuone/sysmi/notes.md"),
			("unconfigured", "tieto"))

	def test_a_path_that_resolves_nowhere_is_the_real_defect(self):
		resolver = V.RootResolver(FIXTURE_ROOTS, unconfigured_roots=FIXTURE_UNCONFIGURED)
		self.assertEqual(resolver.resolve("dotfiles/config/bash/.path"), ("missing", None))

	def test_discovery_finds_a_sibling_repo_and_ignores_a_plain_directory(self):
		with tempfile.TemporaryDirectory() as td:
			for name in ("macos-setup", "a-repo", "not-a-repo"):
				os.makedirs(os.path.join(td, name))
			os.makedirs(os.path.join(td, "a-repo", ".git"))
			with open(os.path.join(td, "a-repo", "file.md"), "w", encoding="utf-8") as fh:
				fh.write("x")
			with open(os.path.join(td, "not-a-repo", "file.md"), "w", encoding="utf-8") as fh:
				fh.write("x")
			resolver = V.RootResolver([os.path.join(td, "macos-setup")])
			self.assertEqual(resolver.resolve("a-repo/file.md"), ("unconfigured", "a-repo"))
			self.assertEqual(resolver.resolve("not-a-repo/file.md"), ("missing", None))

	def test_discovery_survives_an_unreadable_parent(self):
		resolver = V.RootResolver(["/nonexistent/root"])
		self.assertEqual(resolver.resolve("anything"), ("missing", None))

	def test_an_absolute_path_is_checked_directly(self):
		resolver = V.RootResolver(FIXTURE_ROOTS, unconfigured_roots=[])
		self.assertEqual(resolver.resolve(os.path.join(MANIFEST_ROOT, "Brewfile")),
			("ok", None))
		self.assertEqual(resolver.resolve("/nonexistent/thing"), ("missing", None))


# ── 5. The structural outlet ────────────────────────────────────────────────
class StructuralTests(unittest.TestCase):
	def _sug(self, block, sid="brew:x:s"):
		return {"id": "brew:x", "links": [], "items": [_item()], "suggestions": [
			{"id": sid, "kind": "structural", "structural": block, "target_files": []}]}

	def test_a_clean_task_add_passes_every_precondition(self):
		self.assertEqual(codes(self._sug({
			"op": "task_add",
			"subjects": [{"type": "cask", "name": "codex"}],
			"manifest": None, "from": None,
			"to": {"type": "task", "name": "setup.sh:quarantine"},
			"anchor": {"file": "setup.sh"}})), [])

	def test_a_task_add_for_a_token_already_dispatched_fails(self):
		self.assertIn("E-STRUCT-PRECOND", codes(self._sug({
			"op": "task_add",
			"subjects": [{"type": "cask", "name": "codex"}],
			"manifest": None, "from": None,
			"to": {"type": "task", "name": "setup.sh:install"},
			"anchor": {"file": "setup.sh"}})))

	def test_manifest_move_to_the_section_it_is_already_in_fails(self):
		"""B3's opening case, made a two-line check: does the section exist,
		and is the entry currently somewhere else."""
		self.assertIn("E-STRUCT-PRECOND", codes(self._sug({
			"op": "manifest_move", "subjects": [{"type": "formula", "name": "sops"}],
			"manifest": "Brewfile", "from": {"type": "formula", "name": "sops"},
			"anchor": {"section": "CORE"}})))
		self.assertEqual(codes(self._sug({
			"op": "manifest_move", "subjects": [{"type": "formula", "name": "sops"}],
			"manifest": "Brewfile", "from": {"type": "formula", "name": "sops"},
			"anchor": {"section": "FILE TOOLS"}})), ["E-STRUCT-PRECOND"])

	def test_manifest_add_checks_both_limbs(self):
		self.assertIn("E-STRUCT-PRECOND", codes(self._sug({
			"op": "manifest_add", "subjects": [{"type": "formula", "name": "sops"}],
			"manifest": "Brewfile", "to": {"type": "formula", "name": "sops"},
			"anchor": {"section": "CORE"}})))
		self.assertEqual(codes(self._sug({
			"op": "manifest_add", "subjects": [{"type": "formula", "name": "ripgrep"}],
			"manifest": "Brewfile", "to": {"type": "formula", "name": "ripgrep"},
			"anchor": {"section": "CORE"}})), [])

	def test_tap_ops_read_the_tap_lines(self):
		self.assertEqual(codes(self._sug({
			"op": "tap_remove", "subjects": [{"type": "tap", "name": "slp/krun"}],
			"manifest": "Brewfile", "from": {"type": "tap", "name": "slp/krun"}})), [])
		self.assertIn("E-STRUCT-PRECOND", codes(self._sug({
			"op": "tap_add", "subjects": [{"type": "tap", "name": "slp/krun"}],
			"manifest": "Brewfile", "to": {"type": "tap", "name": "slp/krun"}})))

	def test_install_method_change_needs_distinct_types(self):
		self.assertIn("E-STRUCT-PRECOND", codes(self._sug({
			"op": "install_method_change", "subjects": [{"type": "formula", "name": "sops"}],
			"from": {"type": "formula", "name": "sops"},
			"to": {"type": "formula", "name": "sops"}})))
		self.assertEqual(codes(self._sug({
			"op": "install_method_change", "subjects": [{"type": "formula", "name": "sops"}],
			"from": {"type": "formula", "name": "sops"},
			"to": {"type": "runtime", "name": "mise:sops"}})), [])

	def test_a_missing_required_field_is_named_before_a_precondition_is_claimed(self):
		got = codes(self._sug({"op": "manifest_move",
			"subjects": [{"type": "formula", "name": "sops"}], "manifest": "Brewfile",
			"from": {"type": "formula", "name": "sops"}}))
		self.assertEqual(got, ["E-FIELD-MISSING"])

	def test_a_null_anchor_never_suppresses_a_precondition_that_ignores_it(self):
		"""Five of the nine ops never read an anchor. A checker emitting a
		uniform block with `"anchor": null` must not thereby skip I-16 with no
		finding at all — that is the silent skip this outlet exists to
		prevent."""
		self.assertEqual(codes(self._sug({
			"op": "manifest_remove", "subjects": [{"type": "formula", "name": "absent"}],
			"manifest": "Brewfile", "from": {"type": "formula", "name": "absent"},
			"anchor": None})), ["E-STRUCT-PRECOND"])

	def test_an_anchor_reading_op_with_a_bad_anchor_reports_and_stops(self):
		self.assertEqual(codes(self._sug({
			"op": "manifest_move", "subjects": [{"type": "formula", "name": "sops"}],
			"manifest": "Brewfile", "from": {"type": "formula", "name": "sops"},
			"anchor": {"section": ["CORE"]}})), ["E-FIELD-TYPE"])

	def test_i17_first_limb_also_reads_the_path(self):
		self.assertEqual(codes(self._sug({
			"op": "manifest_remove", "subjects": [{"type": "formula", "name": "sops"}],
			"manifest": "./intel.Brewfile",
			"from": {"type": "formula", "name": "sops"}})),
			["E-INTEL-BREWFILE", "W-STRUCT-UNCHECKED"])

	def test_an_unreadable_manifest_is_unchecked_never_satisfied(self):
		"""A silent skip is how a structural fix covering the wrong subject set
		stays invisible."""
		with tempfile.TemporaryDirectory() as td:
			got = codes(self._sug({
				"op": "manifest_remove", "subjects": [{"type": "formula", "name": "sops"}],
				"manifest": "Brewfile", "from": {"type": "formula", "name": "sops"}}),
				manifest_root=td)
			self.assertEqual(got, ["W-STRUCT-UNCHECKED"])

	def test_subjects_are_required_because_target_files_cannot_see_the_collision(self):
		"""Three suggestions edit CLAUDE.md/AGENTS.md and the fourth adds a
		setup.sh dispatch covering two of the four casks the others document.
		Zero file overlap: the collision is semantic."""
		self.assertIn("E-FIELD-MISSING", codes(self._sug({
			"op": "task_add", "subjects": [], "manifest": None,
			"to": {"type": "task", "name": "setup.sh:quarantine"},
			"anchor": {"file": "setup.sh"}})))

	def test_the_subject_index_is_built_across_tools(self):
		document = session_with({"g.json": [
			{"id": "brew:x", "links": [], "items": [_item()], "suggestions": [{
				"id": "brew:x:s", "kind": "structural", "target_files": [], "structural": {
					"op": "task_add",
					"subjects": [{"type": "cask", "name": "codex"},
						{"type": "cask", "name": "cursor-cli"}],
					"manifest": None, "to": {"type": "task", "name": "setup.sh:quarantine"},
					"anchor": {"file": "setup.sh"}}}]}]})
		self.assertEqual(document["subject_index"],
			{"cask:codex": ["brew:x:s"], "cask:cursor-cli": ["brew:x:s"]})

	MALFORMED_REFS = [
		("manifest_add", {"op": "manifest_add", "subjects": [{"type": "cask", "name": "c"}],
			"manifest": "Brewfile", "to": {"type": "formula"},
			"anchor": {"section": "CORE"}}),
		("manifest_add, to is a string", {"op": "manifest_add",
			"subjects": [{"type": "cask", "name": "c"}], "manifest": "Brewfile",
			"to": "formula z", "anchor": {"section": "CORE"}}),
		("tap_add, to is a string", {"op": "tap_add",
			"subjects": [{"type": "tap", "name": "a/b"}], "manifest": "Brewfile",
			"to": "a/b"}),
		("manifest_remove, from is a string", {"op": "manifest_remove",
			"subjects": [{"type": "formula", "name": "z"}], "manifest": "Brewfile",
			"from": "z"}),
		("install_method_change, from is a string", {"op": "install_method_change",
			"subjects": [{"type": "formula", "name": "z"}], "from": "formula z",
			"to": {"type": "runtime", "name": "mise:z"}}),
		("install_method_change, from has no name", {"op": "install_method_change",
			"subjects": [{"type": "formula", "name": "z"}], "from": {"type": "formula"},
			"to": {"type": "runtime", "name": "mise:z"}}),
		("task_add, anchor.file is an int", {"op": "task_add",
			"subjects": [{"type": "cask", "name": "c"}], "manifest": None,
			"to": {"type": "task", "name": "setup.sh:q"}, "anchor": {"file": 123}}),
		("anchor is a string", {"op": "manifest_move",
			"subjects": [{"type": "formula", "name": "sops"}], "manifest": "Brewfile",
			"from": {"type": "formula", "name": "sops"}, "anchor": "CORE"}),
	]

	def test_a_malformed_ref_never_reaches_a_precondition(self):
		"""A precondition is a claim about a well-formed op. Checking one
		against a Ref with no `name` used to raise KeyError out of the whole
		stage, trading one reported field for a degraded tool."""
		for label, block in self.MALFORMED_REFS:
			with self.subTest(label):
				got = codes(self._sug(block))
				self.assertNotIn("E-VALIDATOR-CRASH", got)
				self.assertTrue(got, "the shape defect must still be reported")
				self.assertFalse({"E-STRUCT-PRECOND"} & set(got),
					"a precondition must not be claimed against a malformed ref")

	def test_the_block_is_present_iff_the_kind_is_structural(self):
		self.assertIn("E-FIELD-MISSING", codes({"id": "brew:x", "links": [],
			"items": [_item()],
			"suggestions": [{"id": "brew:x:s", "kind": "structural", "target_files": []}]}))
		self.assertIn("E-FIELD-TYPE", codes({"id": "brew:x", "links": [],
			"items": [_item()],
			"suggestions": [{"id": "brew:x:e", "kind": "edit", "target_files": [],
				"structural": {"op": "task_add", "subjects": [],
					"to": {"type": "task", "name": "t"}, "anchor": {"file": "setup.sh"}}}]}))


# ── 6. Impact and initial bucketing ─────────────────────────────────────────
class ImpactAndBucketTests(unittest.TestCase):
	def _view(self, **kw):
		research = {"id": "brew:x", "links": [], "items": [_item()]}
		research.update(kw)
		return validate_one(research)[0]

	def test_a_security_item_that_benefits_us_is_not_impact(self):
		"""The removed heuristic: `category != "security"` was a proxy for
		`effect != "risk"`, and a poor one — a security change CAN be a risk
		here. With `effect` explicit the proxy disappears."""
		view = self._view(items=[_item(tags=["security"], severity="warning",
			security={"cve_id": None, "rating": "high", "rating_basis": "nvd",
				"exploited_in_wild": False},
			local={"direction": "reaches", "effect": "benefit", "statement": "s",
				"evidence": [{"path": "Brewfile"}]})])
		self.assertEqual(view["impact"], "none")

	def test_a_security_item_that_is_a_risk_here_is_impact(self):
		view = self._view(items=[_item(tags=["security"], severity="warning",
			security={"cve_id": None, "rating": "high", "rating_basis": "nvd",
				"exploited_in_wild": False},
			local={"direction": "reaches", "effect": "risk", "statement": "s",
				"evidence": [{"path": "Brewfile"}]})])
		self.assertEqual(view["impact"], "possible")

	def test_a_breaking_item_at_warning_is_impact_without_a_local_block(self):
		"""mise:rust — three fixes/warning headliners and no relevancy — is now
		`breaking`-tagged items at warning, which this clause covers."""
		view = self._view(items=[_item(tags=["breaking"], severity="warning",
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": []})])
		self.assertEqual(view["impact"], "possible")

	def test_a_watch_item_suggestion_is_no_longer_impact(self):
		"""REDESIGN.md §D row 4. `item-schema.md` §5.5 flags its own draft as
		wrong against that row; this is the row landing."""
		view = self._view(suggestions=[{"id": "brew:x:w", "kind": "watch-item"}])
		self.assertEqual(view["impact"], "none")
		view = self._view(suggestions=[{"id": "brew:x:e", "kind": "edit",
			"target_files": []}])
		self.assertEqual(view["impact"], "possible")

	def test_a_structural_suggestion_is_impact_by_construction(self):
		view = self._view(suggestions=[{"id": "brew:x:s", "kind": "structural",
			"target_files": [], "structural": {"op": "task_add",
				"subjects": [{"type": "cask", "name": "codex"}], "manifest": None,
				"to": {"type": "task", "name": "setup.sh:quarantine"},
				"anchor": {"file": "setup.sh"}}}])
		self.assertEqual(view["impact"], "possible")

	def test_research_that_told_us_nothing_is_unknown_never_none(self):
		""""unknown" can never reach security_auto, so it never renders as
		auto-approved."""
		self.assertEqual(self._view(items=[])["impact"], "unknown")
		self.assertEqual(self._view(research_error="timed out")["impact"], "unknown")

	def test_the_security_auto_path(self):
		view = self._view(items=[_item(tags=["security"], severity="notable",
			security={"cve_id": "CVE-2026-18408", "rating": "high",
				"rating_basis": "nvd", "exploited_in_wild": False})])
		self.assertEqual(view["initial_review_bucket"], "security_auto")
		self.assertEqual(view["bucket_inputs"], {"has_security": True, "security_only": True,
			"impact": "none", "version_delta": "patch", "runnable": True})

	def test_a_breaking_tag_disqualifies_security_only_on_its_tag(self):
		"""Codex's breaking change disqualifies on its tag rather than on the
		accident of having been filed notes/notable."""
		view = self._view(items=[
			_item(tags=["security"], severity="notable",
				security={"cve_id": None, "rating": "unknown", "rating_basis": "unrated",
					"exploited_in_wild": False}),
			_item(anchor={"kind": "issue", "value": "org/repo#2"}, tags=["breaking"],
				severity="info"),
		])
		self.assertFalse(view["bucket_inputs"]["security_only"])
		self.assertEqual(view["initial_review_bucket"], "security_mixed")

	def test_a_feature_at_any_severity_disqualifies_security_only(self):
		view = self._view(items=[
			_item(tags=["security"], severity="notable",
				security={"cve_id": None, "rating": "unknown", "rating_basis": "unrated",
					"exploited_in_wild": False}),
			_item(anchor={"kind": "issue", "value": "org/repo#2"}, tags=["feature"],
				severity="info"),
		])
		self.assertFalse(view["bucket_inputs"]["security_only"])

	def test_a_vendor_silent_about_a_non_security_category_blocks_security_only(self):
		view = self._view(vendor_silent_categories=["features"], items=[
			_item(tags=["security"], severity="notable",
				security={"cve_id": None, "rating": "unknown", "rating_basis": "unrated",
					"exploited_in_wild": False})])
		self.assertFalse(view["bucket_inputs"]["security_only"])

	def test_a_source_with_no_runnable_command_never_reaches_security_auto(self):
		research = {"id": "macos:safari", "links": [], "items": [_item(tags=["security"],
			severity="notable", security={"cve_id": None, "rating": "unknown",
				"rating_basis": "unrated", "exploited_in_wild": False})]}
		view, _ = validate_one(research, candidate=_candidate(id="macos:safari",
			name="Safari", source="macos"))
		self.assertFalse(view["bucket_inputs"]["runnable"])
		self.assertEqual(view["initial_review_bucket"], "security_mixed")

	def test_a_non_version_finding_source_short_circuits(self):
		for source, flag in (("brew-health", True), ("skill-drift", True)):
			with self.subTest(source):
				candidate = _candidate(id=source + ":f", name="f", source=source,
					current_version=None, latest_version=None, expected=flag)
				view, _ = validate_one(None, candidate=candidate)
				self.assertEqual(view["impact"], "none")
				self.assertEqual(view["initial_review_bucket"], "routine")
				self.assertEqual(view["risk_level"], "low")

	def test_an_unexpected_finding_needs_attention(self):
		candidate = _candidate(id="brew-health:f", name="f", source="brew-health",
			current_version=None, latest_version=None, expected=False)
		view, _ = validate_one(None, candidate=candidate)
		self.assertEqual(view["initial_review_bucket"], "attention")
		self.assertEqual(view["risk_level"], "elevated")

	def test_the_security_display_ids_are_carried_for_the_page(self):
		view = self._view(items=[_item(tags=["security"], severity="warning",
			security={"cve_id": None, "rating": "critical", "rating_basis": "vendor",
				"exploited_in_wild": False},
			local={"direction": "reaches", "effect": "risk", "statement": "s",
				"evidence": [{"path": "Brewfile"}]})])
		self.assertEqual(view["security_display_item_ids"], ["brew:x#issue:org%2Frepo%231"])


# ── 6b. Memory proposals (REDESIGN.md L1, L7) ───────────────────────────────
def _memory(kind="method-note", **kw):
	base = {"id": "brew:x:" + kind, "kind": kind, "title": "A memory proposal",
		"target_files": [], "command": None, "auto_runnable": False,
		"rationale": "why this is worth remembering"}
	for field in model.MEMORY_PAYLOAD_FIELDS[kind]:
		base[field] = "some " + field
	base.update(kw)
	return base


def _action(kind="edit", **kw):
	base = {"id": "brew:x:" + str(kind), "kind": kind, "title": "An action proposal",
		"target_files": [], "rationale": "why the user should change something"}
	if kind == "structural":
		base["structural"] = {"op": "tap_add", "subjects": [{"type": "tap", "name": "a/b"}],
			"manifest": "Brewfile", "from": None, "to": {"type": "tap", "name": "a/b"},
			"anchor": {"section": "TAPS"}}
	base.update(kw)
	return {k: v for k, v in base.items() if not (k == "structural" and v is None)}


class MemoryProposalTests(unittest.TestCase):
	"""`REDESIGN.md` §L1 expects **many** per-tool method notes and watch items,
	and §L7 makes the self-test tag load-bearing — convergence keys its review
	off it. Both facts are why these are validated rather than waved through as
	free-floating extra fields: an unvalidated channel is how `Watch item hit:`
	broke, and a misspelt `self_test_failed` would fail the same silent way."""

	def _view(self, suggestions, **kw):
		research = {"id": "brew:x", "links": [], "items": [_item()],
			"suggestions": suggestions}
		research.update(kw)
		return validate_one(research)

	# — the bucket rule —
	def test_a_memory_proposal_alone_leaves_a_tool_routine(self):
		"""The ruling, in one assertion. A method note or a watch item proposes
		a change to what we remember; only `edit`/`structural` propose a change
		to the user's system, and only those mean "a human has to look"."""
		for kind in model.MEMORY_SUGGESTION_KINDS:
			with self.subTest(kind):
				view, _ = self._view([_memory(kind)])
				self.assertEqual(view["initial_review_bucket"], "routine")
				self.assertEqual(view["risk_level"], "low")
				self.assertEqual(view["impact"], "none")

	def test_an_action_proposal_still_lands_in_attention(self):
		for kind in model.ACTION_SUGGESTION_KINDS:
			with self.subTest(kind):
				view, _ = self._view([_action(kind)])
				self.assertEqual(view["initial_review_bucket"], "attention")

	def test_a_memory_proposal_beside_an_action_one_does_not_hide_it(self):
		view, _ = self._view([_memory("watch-item"), _action("edit")])
		self.assertEqual(view["initial_review_bucket"], "attention")

	def test_a_memory_proposal_does_not_answer_needs_attention(self):
		"""I-15 reads the same tuple as the bucket clause. A note about how to
		research this tool next time is not an answer to "the config may be
		stale — what do I do about it", so it must not silence the warning any
		more than it may raise the bucket."""
		status = {"state": "needs_attention", "detail": "stale", "evidence": [],
			"citations": []}
		_, findings = self._view([_memory("method-note")], config_status=status)
		self.assertIn("W-ATTENTION-NOSUG", {f["code"] for f in findings.entries})
		_, findings = self._view([_action("edit")], config_status=status)
		self.assertNotIn("W-ATTENTION-NOSUG", {f["code"] for f in findings.entries})

	# — the payload —
	def test_a_memory_proposal_without_its_payload_is_a_missing_field(self):
		for kind, fields in sorted(model.MEMORY_PAYLOAD_FIELDS.items()):
			for field in fields:
				with self.subTest(kind + "." + field):
					_, findings = self._view([_memory(kind, **{field: "   "})])
					missing = {f["field"] for f in findings.entries
						if f["code"] == "E-FIELD-MISSING"}
					self.assertEqual(missing, {field})

	def test_the_two_payloads_do_not_share_a_topic_or_note_field(self):
		"""A watch item's topic and a method note's topic are different stores.
		Sharing a field name is how they got conflated in the first place.
		`rationale` is deliberately common to both — it is the same obligation
		in both stores, and the field convergence reads."""
		watch = set(model.MEMORY_PAYLOAD_FIELDS["watch-item"])
		method = set(model.MEMORY_PAYLOAD_FIELDS["method-note"])
		self.assertEqual(watch & method, {"rationale"})

	def test_both_memory_kinds_owe_a_rationale(self):
		"""The measured failure was never a missing topic — it was rationales
		reciting the bar's own escape phrase. The self-test writes its answers
		into this field and convergence promotes on it, so an empty one is a
		proposal nobody downstream can review."""
		for kind in model.MEMORY_SUGGESTION_KINDS:
			with self.subTest(kind):
				self.assertIn("rationale", model.MEMORY_PAYLOAD_FIELDS[kind])
				_, findings = self._view([_memory(kind, rationale="   ")])
				missing = [f for f in findings.entries if f["code"] == "E-FIELD-MISSING"]
				self.assertEqual([f["field"] for f in missing], ["rationale"])

	# — the self-test tag —
	def test_a_well_formed_tag_is_accepted_and_exported(self):
		for limb in model.SELF_TEST_LIMBS:
			with self.subTest(limb):
				view, findings = self._view([_memory("watch-item", self_test_failed={
					"limb": limb, "reason": "config_status re-verified this exact delta"})])
				self.assertEqual(findings.entries, [])
				self.assertEqual(view["self_test_tagged_suggestion_ids"],
					["brew:x:watch-item"])

	def test_an_untagged_proposal_is_not_in_the_exported_set(self):
		view, _ = self._view([_memory("watch-item")])
		self.assertEqual(view["self_test_tagged_suggestion_ids"], [])

	def test_a_tag_with_no_reason_is_a_drop_with_extra_steps(self):
		"""Criterion 17 exists to stop a self-test deleting a proposal. A tag
		naming no reason gives convergence nothing to review it against, which
		is a deletion wearing a tag."""
		for reason in (None, "", "   ", 7):
			with self.subTest(repr(reason)):
				tag = {"limb": "scope"}
				if reason is not None:
					tag["reason"] = reason
				self.assertIn("E-SELFTEST-NOREASON",
					codes({"id": "brew:x", "links": [], "items": [_item()],
						"suggestions": [_memory("watch-item", self_test_failed=tag)]}))

	def test_a_limb_outside_the_vocabulary_is_reported(self):
		_, findings = self._view([_memory("watch-item", self_test_failed={
			"limb": "no-single-delta-to-re-check", "reason": "the rule's own words"})])
		bad = [f for f in findings.entries if f["code"] == "E-ENUM-INVALID"]
		self.assertEqual([f["field"] for f in bad], ["self_test_failed.limb"])

	def test_a_tagged_proposal_is_still_kept_whole(self):
		"""§L7's whole point: the agent writes the proposal it failed. Nothing
		here removes it, empties it, or lowers anything on it."""
		proposal = _memory("watch-item", self_test_failed={
			"limb": "scope", "reason": "config_status caught it"})
		view, _ = self._view([copy.deepcopy(proposal)])
		self.assertEqual(view["suggestions"], [proposal])

	def test_the_tag_belongs_only_on_a_memory_kind(self):
		for kind in ("edit", "upgrade"):
			with self.subTest(kind):
				view, findings = self._view([_action(kind, self_test_failed={
					"limb": "limb", "reason": "an action proposal has no self-test"})])
				self.assertIn("E-FIELD-TYPE", {f["code"] for f in findings.entries})
				# ...and it is NOT offered to convergence as a droppable note.
				self.assertEqual(view["self_test_tagged_suggestion_ids"], [])

	def test_a_non_object_tag_is_reported_not_crashed_on(self):
		for tag in ("scope", ["scope"], 3):
			with self.subTest(repr(tag)):
				self.assertIn("E-FIELD-TYPE",
					codes({"id": "brew:x", "links": [], "items": [_item()],
						"suggestions": [_memory("watch-item", self_test_failed=tag)]}))

	# — failing safe —
	def test_an_unrecognized_kind_still_forces_a_decision(self):
		"""The rule is "memory kinds do not force attention", not "only two
		kinds do". Spelled the second way, a typo'd `"edits"` carrying a real
		config edit reads as a memory proposal and the tool stays `routine`,
		with E-ENUM-INVALID raised and feeding nothing."""
		for kind in ("edits", "watchitem", "method_note", "future-kind"):
			with self.subTest(kind):
				view, findings = self._view([_action(kind, structural=None)])
				self.assertEqual(view["initial_review_bucket"], "attention")
				self.assertIn("E-ENUM-INVALID", {f["code"] for f in findings.entries})

	def test_an_unrecognized_kind_never_reaches_the_pre_accepting_bucket(self):
		"""`compute_initial_bucket` tests `security_auto` **before** its own
		suggestion clause, and that clause's inputs are `impact` and
		`security_only`. So the bucket clause's negation is not the guarantee —
		`compute_impact` and `compute_risk_level` are.

		No fixture anywhere crossed a security item with a suggestion, which is
		why a 133-test suite stayed green over a route straight to
		`security_auto`, pre-accepted, carrying an unreviewed proposed edit to
		the user's system. That is the `brew:libpq` defect, exactly."""
		security = _item(tags=["security"], severity="notable",
			security={"cve_id": "CVE-2026-1000", "rating": "high",
				"rating_basis": "nvd", "exploited_in_wild": False})
		for kind in ("edit", "edits", "future-kind"):
			with self.subTest(kind):
				research = {"id": "brew:x", "links": [], "items": [security],
					"suggestions": [_action(kind, structural=None)]}
				view, _ = validate_one(research)
				self.assertNotEqual(view["initial_review_bucket"], "security_auto")
				self.assertEqual(view["initial_review_bucket"], "security_mixed")
				self.assertEqual(view["impact"], "possible")
				self.assertEqual(view["risk_level"], "elevated")

	def test_a_memory_proposal_beside_security_content_still_auto_accepts(self):
		"""The other side of the same clause: the negation must not over-fire.
		A method note is not a reason to hold back a security-only upgrade."""
		security = _item(tags=["security"], severity="notable",
			security={"cve_id": "CVE-2026-1000", "rating": "high",
				"rating_basis": "nvd", "exploited_in_wild": False})
		research = {"id": "brew:x", "links": [], "items": [security],
			"suggestions": [_memory("method-note")]}
		view, _ = validate_one(research)
		self.assertEqual(view["impact"], "none")
		self.assertEqual(view["risk_level"], "low")
		self.assertEqual(view["initial_review_bucket"], "security_auto")

	def test_an_unrecognized_kind_does_not_silence_the_attention_warning(self):
		status = {"state": "needs_attention", "detail": "stale", "evidence": [],
			"citations": []}
		_, findings = self._view([_action("edits", structural=None)], config_status=status)
		self.assertNotIn("W-ATTENTION-NOSUG", {f["code"] for f in findings.entries})

	def test_an_unhashable_kind_costs_the_tool_no_suggestions(self):
		"""`assemble.suggestion_kind` returns what the checker wrote, so a
		drifted `"kind": ["edit"]` arrives unhashable. A dict lookup on it would
		raise out of the whole stage and take this tool's real edit proposal
		with it — deletion, over a shape the rest of this file survives."""
		for kind in (["edit"], {"a": 1}, {"edit"}):
			with self.subTest(repr(kind)):
				real = _action("edit", id="brew:x:real-edit")
				view, findings = self._view([{"id": "brew:x:drifted", "kind": kind,
					"title": "A suggestion whose kind is not even a string"}, real])
				codes_seen = {f["code"] for f in findings.entries}
				self.assertNotIn("E-VALIDATOR-CRASH", codes_seen)
				self.assertIn("E-ENUM-INVALID", codes_seen)
				self.assertIn(real, view["suggestions"])
				self.assertEqual(len(view["suggestions"]), 2)

	def test_an_unhashable_kind_carrying_a_tag_is_reported_by_type(self):
		view, findings = self._view([{"id": "brew:x:drifted", "kind": ["watch-item"],
			"title": "t", "self_test_failed": {"limb": "scope", "reason": "r"}}])
		messages = [f["message"] for f in findings.entries if f["code"] == "E-FIELD-TYPE"]
		self.assertTrue(any("\"list\"" in m for m in messages), messages)
		self.assertEqual(view["self_test_tagged_suggestion_ids"], [])

	def test_the_tagged_list_survives_a_crash_inside_the_axis_derivation(self):
		"""The default in the view literal is only ever *read* when
		`_derive_axes` raises before assigning the key — so a test that lets
		`_derive_axes` finish pins nothing, and passes with the default
		removed. Inject the crash.

		It also makes the ordering point visible rather than assumed: with the
		crash injected before the tail, `initial_review_bucket` still reads
		`attention`, so a failed stage cannot promote this tool."""
		with mock.patch.object(V, "compute_impact", side_effect=RuntimeError("boom")):
			view, _ = validate_one(None)
		self.assertIn("self_test_tagged_suggestion_ids", view)
		self.assertEqual(view["self_test_tagged_suggestion_ids"], [])
		self.assertTrue(view["validator_error"])
		self.assertEqual(view["initial_review_bucket"], "attention")

	def test_a_tagged_proposal_missing_its_id_is_still_listed(self):
		"""Absent from the list is the one thing it must never be — convergence
		works from this list, and a proposal it cannot see is one it cannot
		restore."""
		proposal = _memory("watch-item", self_test_failed={
			"limb": "limb", "reason": "neither half answered"})
		del proposal["id"]
		view, _ = self._view([proposal])
		self.assertEqual(view["self_test_tagged_suggestion_ids"], ["brew:x:<no id>"])


# ── 7. Degradation: per tool, loud, never fatal ─────────────────────────────
class DegradationTests(unittest.TestCase):
	"""Same doctrine as `LoadResearchDegradationTests`. A research file is
	subagent output, so any member can be any shape; one report is validated
	from ~22 files covering ~80 tools, so a single drifted file must never
	destroy the run after the expensive part of the session is spent."""

	HOSTILE = [None, "a string", 42, True, [], {}, ["x"], [None], [42], [[1]],
		{"k": "v"}, [{"k": ["v"]}], [{"id": []}], "", [""], {"a": {"b": {"c": 1}}}]
	RESEARCH_KEYS = ["items", "links", "suggestions", "config_status", "flags",
		"vendor_silent_categories", "research_error", "notes"]

	def test_every_hostile_shape_at_every_key_costs_at_most_one_tool(self):
		for key in self.RESEARCH_KEYS:
			for shape in self.HOSTILE:
				with self.subTest(key=key, shape=repr(shape)):
					research = {"id": "brew:x", "links": [], "items": [_item()]}
					research[key] = shape
					view, _ = validate_one(research)
					self.assertEqual(view["id"], "brew:x")
					self.assertIn(view["impact"], ("none", "possible", "unknown"))

	def test_a_hostile_item_member_never_raises(self):
		for shape in self.HOSTILE:
			with self.subTest(repr(shape)):
				view, _ = validate_one({"id": "brew:x", "links": [], "items": [shape]})
				self.assertIsInstance(view["items"], list)

	def test_a_hostile_anchor_never_raises(self):
		for shape in self.HOSTILE:
			with self.subTest(repr(shape)):
				validate_one({"id": "brew:x", "links": [], "items": [_item(anchor=shape)]})

	def test_a_hostile_local_block_never_raises(self):
		for shape in self.HOSTILE:
			with self.subTest(repr(shape)):
				validate_one({"id": "brew:x", "links": [], "items": [_item(local=shape)]})

	def test_a_hostile_security_block_never_raises(self):
		for shape in self.HOSTILE:
			with self.subTest(repr(shape)):
				validate_one({"id": "brew:x", "links": [],
					"items": [_item(tags=["security"], security=shape)]})

	def test_a_hostile_structural_block_never_raises(self):
		for shape in self.HOSTILE:
			with self.subTest(repr(shape)):
				validate_one({"id": "brew:x", "links": [], "items": [_item()],
					"suggestions": [{"id": "s", "kind": "structural", "structural": shape}]})

	def test_an_unreadable_file_costs_that_file_and_no_other(self):
		document = session_with({"a-bad.json": "{ not json", "b-good.json": [
			{"id": "brew:x", "links": [], "items": [_item()]}]})
		self.assertIn("E-RESEARCH-UNREADABLE", document["counts"]["by_code"])
		self.assertEqual([t["id"] for t in document["tools"]], ["brew:x"])
		self.assertEqual(document["tools"][0]["items"][0]["title"], "A conforming title")

	def test_a_file_that_is_not_an_array_costs_that_file(self):
		document = session_with({"a.json": {"id": "brew:x"}, "b.json": [
			{"id": "brew:x", "links": [], "items": [_item()]}]})
		self.assertIn("E-RESEARCH-NOTARRAY", document["counts"]["by_code"])
		self.assertEqual(len(document["tools"][0]["items"]), 1)

	def test_an_entry_that_is_not_an_object_is_an_orphan_not_a_crash(self):
		document = session_with({"a.json": ["just a string", 42, None,
			{"id": "brew:x", "links": [], "items": [_item()]}]})
		self.assertEqual(len(document["orphans"]), 3)
		self.assertIn("E-ENTRY-NOTOBJECT", document["counts"]["by_code"])
		self.assertEqual(len(document["tools"][0]["items"]), 1)

	def test_an_entry_with_no_usable_id_is_an_orphan(self):
		document = session_with({"a.json": [{"id": []}, {"id": ""}, {"items": []}]})
		self.assertEqual(len(document["orphans"]), 3)
		self.assertIn("E-ENTRY-NOID", document["counts"]["by_code"])

	def test_two_files_claiming_one_tool_is_reported(self):
		document = session_with({
			"a.json": [{"id": "brew:x", "links": [], "items": [_item()]}],
			"b.json": [{"id": "brew:x", "links": [], "items": [_item()]}]})
		self.assertIn("W-ENTRY-DUPLICATE", document["counts"]["by_code"])

	def test_an_entry_naming_no_candidate_is_kept_as_unmatched(self):
		document = session_with({"a.json": [{"id": "brew:ghost", "links": [], "items": []}]})
		self.assertEqual(document["unmatched"], ["brew:ghost"])
		self.assertIn("W-ENTRY-UNMATCHED", document["counts"]["by_code"])

	def test_a_missing_research_directory_is_reported_not_fatal(self):
		td = tempfile.mkdtemp()
		try:
			with open(os.path.join(td, "collect.json"), "w", encoding="utf-8") as fh:
				json.dump({"generated_at": "t", "machine": {}, "brew": [_candidate()]}, fh)
			document = V.validate_session(td, FIXTURE_ROOTS, manifest_root=MANIFEST_ROOT,
				unconfigured_roots=[])
			self.assertIn("E-RESEARCH-UNREADABLE", document["counts"]["by_code"])
			self.assertEqual(len(document["tools"]), 1)
		finally:
			shutil.rmtree(td, ignore_errors=True)

	def _with_exploding(self, name, work):
		original = getattr(V, name)

		def explode(*args, **kwargs):
			raise RuntimeError("synthetic")

		setattr(V, name, explode)
		try:
			return work()
		finally:
			setattr(V, name, original)

	def test_a_crashing_item_check_costs_the_check_never_the_item(self):
		"""The next unknown shape — the one no test anticipated — must cost as
		little as possible and say so. Losing the item would be deletion, which
		this layer is forbidden to do."""
		document = self._with_exploding("validate_item", lambda: session_with({"a.json": [
			{"id": "brew:x", "links": [], "items": [_item(title="AUTHORED TEXT")]},
			{"id": "brew:y", "links": [], "items": []}]},
			collect={"generated_at": "t", "machine": {},
				"brew": [_candidate(), _candidate(id="brew:y", name="y")]}))
		self.assertIn("E-VALIDATOR-CRASH", document["counts"]["by_code"])
		self.assertEqual([t["id"] for t in document["tools"]], ["brew:x", "brew:y"])
		kept = document["tools"][0]["items"]
		self.assertEqual([i["title"] for i in kept], ["AUTHORED TEXT"])
		self.assertEqual(kept[0]["id"], "brew:x#issue:org%2Frepo%231")
		self.assertEqual(document["tools"][1]["items"], [])

	def test_an_unhashable_anchor_kind_costs_no_item(self):
		"""Ids are assigned before V2 reports on the anchor, so `anchor.kind`
		reaches the derivation unvalidated. A dict-membership test on an
		unhashable one used to raise out of the whole stage and take every
		later item and suggestion with it."""
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [
			_item(title="FIRST"),
			_item(anchor={"kind": ["issue"], "value": "org/repo#9"}, title="POISON"),
			_item(anchor={"kind": "issue", "value": "org/repo#3"}, title="THIRD"),
		], "suggestions": [{"id": "brew:x:e", "kind": "edit", "target_files": []}]})
		self.assertEqual(sorted(i["title"] for i in view["items"]),
			["FIRST", "POISON", "THIRD"])
		self.assertEqual(len(view["suggestions"]), 1)
		got = {f["code"] for f in findings.entries}
		self.assertNotIn("E-VALIDATOR-CRASH", got)
		self.assertIn("E-ENUM-INVALID", got)

	def test_an_item_kept_through_a_crash_still_carries_its_assigned_fields(self):
		"""The same "a consumer must not KeyError on precisely the item
		degradation was meant to keep usable" argument as the view's defaults."""
		document = self._with_exploding("validate_item", lambda: session_with(
			{"a.json": [{"id": "brew:x", "links": [], "items": [_item()]}]}))
		item = document["tools"][0]["items"][0]
		self.assertEqual(item["id"], "brew:x#issue:org%2Frepo%231")
		self.assertEqual(item["id_stability"], "anchored")

	def test_a_crashing_stage_keeps_everything_that_conformed_before_it(self):
		"""`_validate_suggestions` runs after the items are validated, so a
		failure there must not take the items with it."""
		document = self._with_exploding("_validate_suggestions",
			lambda: session_with({"a.json": [
				{"id": "brew:x", "links": [], "items": [_item(title="AUTHORED TEXT")]}]}))
		self.assertIn("E-VALIDATOR-CRASH", document["counts"]["by_code"])
		view = document["tools"][0]
		self.assertEqual([i["title"] for i in view["items"]], ["AUTHORED TEXT"])
		self.assertEqual(view["impact"], "unknown")
		self.assertEqual(view["initial_review_bucket"], "attention")

	def test_a_partial_stage_failure_can_never_promote_a_tool(self):
		"""The `brew:libpq` defect by another route. A stage that fails halfway
		leaves the view missing exactly what it had not reached yet, so a bucket
		computed from what survived can pre-accept a tool *because* the thing
		holding it back is the thing that went missing."""
		security_item = _item(tags=["security"], severity="notable",
			security={"cve_id": "CVE-2026-18408", "rating": "high",
				"rating_basis": "nvd", "exploited_in_wild": False})
		structural = {"id": "brew:x:s", "kind": "structural", "target_files": [],
			"structural": {"op": "task_add", "subjects": [{"type": "cask", "name": "c"}],
				"manifest": None, "to": {"type": "task", "name": "setup.sh:q"},
				"anchor": {"file": "setup.sh"}}}

		# The suggestion is what holds this tool out of the auto bucket…
		alone, _ = validate_one({"id": "brew:x", "links": [], "items": [security_item]})
		self.assertEqual(alone["initial_review_bucket"], "security_auto")
		healthy, _ = validate_one({"id": "brew:x", "links": [], "items": [security_item],
			"suggestions": [structural]})
		self.assertEqual(healthy["initial_review_bucket"], "security_mixed")

		# …so losing it to a crashed stage must not hand the tool the auto path.
		document = self._with_exploding("_validate_suggestions",
			lambda: session_with({"a.json": [{"id": "brew:x", "links": [],
				"items": [security_item], "suggestions": [structural]}]}))
		view = document["tools"][0]
		self.assertIn("E-VALIDATOR-CRASH", document["counts"]["by_code"])
		self.assertTrue(view["validator_error"])
		self.assertEqual(view["suggestions"], [])
		self.assertEqual(view["impact"], "unknown")
		self.assertEqual(view["risk_level"], "elevated")
		self.assertNotEqual(view["initial_review_bucket"], "security_auto")
		self.assertFalse(view["bucket_inputs"]["security_only"])

	def test_a_degraded_tool_with_no_security_content_lands_in_attention(self):
		document = self._with_exploding("_validate_suggestions",
			lambda: session_with({"a.json": [{"id": "brew:x", "links": [],
				"items": [_item()]}]}))
		view = document["tools"][0]
		self.assertEqual(view["initial_review_bucket"], "attention")

	def test_a_degraded_tool_still_carries_every_key_a_consumer_reads(self):
		"""A KeyError on precisely the tool degradation was supposed to keep
		usable is the failure this defends against."""
		document = self._with_exploding("_read_research",
			lambda: session_with({"a.json": [{"id": "brew:x", "links": [],
				"items": [_item()]}]}))
		healthy = model.load_fixture("expected_validation.json")["tools"][0]
		view = document["tools"][0]
		self.assertEqual(set(healthy) - set(view), set())
		self.assertEqual(view["impact"], "unknown")
		self.assertEqual(view["initial_review_bucket"], "attention")
		self.assertFalse(view["bucket_inputs"]["security_only"])

	COLLECT_KEYS = ["brew", "mise", "standalone", "macos", "brew_health", "skill_drift",
		"machine", "generated_at"]

	def test_the_whole_session_survives_every_hostile_shape_at_every_key(self):
		"""The session-level twin of `scratch/spof/repro/fuzz.py`, which holds
		`assemble.main()` at 0 of 346 aborting cases. Same matrix, same rule:
		only a message-bearing NoCandidateSet is an acceptable stop, and only
		for collect.json — nothing else may abort."""
		aborted = []
		for key in self.RESEARCH_KEYS + ["id"]:
			for shape in self.HOSTILE:
				entry = {"id": "brew:x", "links": [], "items": [_item()]}
				entry[key] = shape
				try:
					session_with({"g.json": [entry]})
				except Exception as exc:  # noqa: BLE001 — the point is to catch everything
					aborted.append(("research.{}={!r}".format(key, shape), repr(exc)))
		for key in self.COLLECT_KEYS:
			for shape in self.HOSTILE:
				collect = {"generated_at": "t", "machine": {}, "brew": [_candidate()]}
				collect[key] = shape
				try:
					session_with({"g.json": [
						{"id": "brew:x", "links": [], "items": [_item()]}]}, collect=collect)
				except V.NoCandidateSet:
					pass
				except Exception as exc:  # noqa: BLE001
					aborted.append(("collect.{}={!r}".format(key, shape), repr(exc)))
		for shape in [None, "x", 42, [], ["a"]]:
			try:
				session_with({"g.json": shape})
			except Exception as exc:  # noqa: BLE001
				aborted.append(("research={!r}".format(shape), repr(exc)))
			try:
				session_with({"g.json": []}, collect=shape)
			except V.NoCandidateSet:
				pass
			except Exception as exc:  # noqa: BLE001
				aborted.append(("collect={!r}".format(shape), repr(exc)))
		self.assertEqual(aborted, [])

	def test_a_candidate_with_no_identity_costs_one_card(self):
		document = session_with({"a.json": []}, collect={
			"generated_at": "t", "machine": {},
			"brew": [{"name": "x", "source": "brew"}, _candidate()]})
		self.assertEqual([t["id"] for t in document["tools"]], ["brew:x"])
		self.assertIn("E-ENTRY-NOID", document["counts"]["by_code"])

	def test_a_collect_json_that_is_not_an_object_says_so_and_stops(self):
		"""collect.json IS the candidate set; nothing can be salvaged from one
		that is not an object. It stops saying which file and what shape,
		which a traceback does not."""
		with tempfile.TemporaryDirectory() as td:
			with open(os.path.join(td, "collect.json"), "w", encoding="utf-8") as fh:
				fh.write("[1, 2, 3]")
			with self.assertRaises(V.NoCandidateSet) as caught:
				V.validate_session(td, FIXTURE_ROOTS)
			self.assertIn("no candidate set", str(caught.exception))


# ── the CLI and its exit codes ──────────────────────────────────────────────
class CliTests(unittest.TestCase):
	def _run(self, research):
		td = tempfile.mkdtemp()
		os.makedirs(os.path.join(td, "research"))
		with open(os.path.join(td, "collect.json"), "w", encoding="utf-8") as fh:
			json.dump({"generated_at": "2026-09-07T00:00:00Z", "machine": {},
				"brew": [_candidate()]}, fh)
		with open(os.path.join(td, "research", "g.json"), "w", encoding="utf-8") as fh:
			json.dump(research, fh)
		with contextlib.redirect_stdout(io.StringIO()):
			code = V.main([td, "--macos-setup-root", MANIFEST_ROOT,
				"--dotfiles-root", os.path.join(MANIFEST_ROOT, "dotfiles"),
				"--systems-root", os.path.join(MANIFEST_ROOT, "no-such-systems")])
		return td, code

	def test_a_clean_run_exits_zero_and_writes_an_empty_warn_file(self):
		td, code = self._run([{"id": "brew:x", "links": [], "items": [_item()]}])
		try:
			self.assertEqual(code, 0)
			with open(os.path.join(td, "validation.json"), encoding="utf-8") as fh:
				document = json.load(fh)
			self.assertTrue(document["clean"])
			self.assertEqual(document["findings"], [])
			with open(os.path.join(td, "assemble.warn"), encoding="utf-8") as fh:
				self.assertEqual(fh.read(), "")
		finally:
			shutil.rmtree(td, ignore_errors=True)

	def test_a_degraded_run_exits_three_and_still_writes_everything(self):
		"""3 is not a failure — everything downstream still runs. The workflow
		surfaces it; it never aborts."""
		td, code = self._run([{"id": "brew:x", "links": [],
			"items": [_item(tags=["hardening"])]}])
		try:
			self.assertEqual(code, 3)
			with open(os.path.join(td, "validation.json"), encoding="utf-8") as fh:
				document = json.load(fh)
			self.assertFalse(document["clean"])
			with open(os.path.join(td, "assemble.warn"), encoding="utf-8") as fh:
				lines = fh.read().splitlines()
			self.assertTrue(lines)
			for line in lines:
				self.assertRegex(line, r"^[EW]-[A-Z0-9-]+ ")
		finally:
			shutil.rmtree(td, ignore_errors=True)

	def test_an_unreadable_collect_json_exits_above_three(self):
		with tempfile.TemporaryDirectory() as td:
			with contextlib.redirect_stderr(io.StringIO()) as err:
				code = V.main([td, "--macos-setup-root", MANIFEST_ROOT])
			self.assertEqual(code, 4)
			self.assertIn("collect.json", err.getvalue())

	def test_the_warn_tail_carries_the_same_findings_in_the_same_order(self):
		document = model.load_fixture("expected_validation.json")
		lines = V.warn_lines(document)
		self.assertEqual(len(lines), len(document["findings"]))
		self.assertEqual([line.split(" ", 1)[0] for line in lines],
			[f["code"] for f in document["findings"]])


class EffectiveHasSecurityTests(unittest.TestCase):
	"""`has_security` is wider than the tag, and the width is safety.

	`recompute_flags` is tag-only — it is what `E-FLAG-DISAGREE` compares a
	checker's claim against, so widening it would make a checker that correctly
	reported `has_security: false` from its own items read as disagreeing. The
	widening therefore happens at the point of use, ONCE, and the same value
	feeds `security_only`, the bucket and `bucket_inputs`."""

	def _view(self, **research):
		research.setdefault("id", "brew:x")
		research.setdefault("links", [])
		return validate_one(research)[0]

	def test_the_tag_alone_is_still_enough(self):
		view = self._view(items=[_item(tags=["security"], severity="info",
			security={"cve_id": None, "advisory_id": None, "rating": "unknown",
				"rating_basis": "unrated", "exploited_in_wild": False})])
		self.assertTrue(view["bucket_inputs"]["has_security"])
		self.assertEqual(view["initial_review_bucket"], "security_auto")

	def test_a_vendor_silent_on_security_reaches_a_security_bucket(self):
		"""The regression this closes. `vendor_silent_categories: ["security"]`
		is research's explicit statement "this release has security content the
		vendor refused to detail". Tag-only, the tool computes: not security (no
		security tag), and NOT elevated either — `compute_risk_level`'s "no
		items" clause is suppressed by the non-empty vendor_silent list. It
		lands in `routine`, out of the security section entirely, from a field
		whose whole purpose is "look at this"."""
		view = self._view(vendor_silent_categories=["security"],
			items=[_item(tags=["feature"], severity="notable")])
		self.assertTrue(view["bucket_inputs"]["has_security"])
		self.assertEqual(view["initial_review_bucket"], "security_mixed")
		# The tag-only flag is unchanged, and must be: it is the comparison
		# basis for E-FLAG-DISAGREE.
		self.assertFalse(view["flags"]["has_security"])

	def test_a_security_block_on_an_untagged_item_counts(self):
		"""I-4 reports the missing tag (E-SEC-BLOCK-ORPHAN) — and reporting it
		while treating the tool as non-security is how a CVE-carrying tool
		would reach a bucket that pre-accepts. A degraded run still renders and
		still applies, so a finding is not a gate; erring toward "security" is."""
		view, findings = validate_one({"id": "brew:x", "links": [],
			"items": [_item(tags=["fix"], severity="info",
				security={"cve_id": "CVE-2026-1111", "advisory_id": None,
					"rating": "unknown", "rating_basis": "unrated",
					"exploited_in_wild": False})]})
		self.assertIn("E-SEC-BLOCK-ORPHAN", {f["code"] for f in findings.entries})
		self.assertTrue(view["bucket_inputs"]["has_security"])
		self.assertIn(view["initial_review_bucket"], ("security_auto", "security_mixed"))

	def test_the_bucket_is_explained_by_its_own_recorded_inputs(self):
		"""A value that is "security" for bucketing and "not security" for the
		security-only test is its own auto-accept route, and a bucket its own
		recorded inputs cannot explain is exactly the opacity §C3 removes."""
		view = self._view(vendor_silent_categories=["security"],
			items=[_item(tags=["chore"], severity="info")])
		inputs = view["bucket_inputs"]
		self.assertTrue(inputs["has_security"])
		# chore/info is allowed for security_only, and nothing disqualifies —
		# so the recorded inputs alone explain security_auto.
		self.assertTrue(inputs["security_only"])
		self.assertEqual(inputs["impact"], "none")
		self.assertEqual(view["initial_review_bucket"], "security_auto")

	def test_nothing_security_shaped_stays_out_of_a_security_bucket(self):
		view = self._view(vendor_silent_categories=["features"],
			items=[_item(tags=["feature"], severity="notable")])
		self.assertFalse(view["bucket_inputs"]["has_security"])
		self.assertNotIn(view["initial_review_bucket"], ("security_auto", "security_mixed"))


# ── D1: the degradation channel and the fail-closed clause ──────────────────
class DegradationChannelTests(unittest.TestCase):
	"""Content-losing input fails closed: `attention`, `elevated`, and never
	pre-accepted. Every OTHER finding is a marker on the card and moves
	nothing — that half is asserted here too, because a fail-closed rule that
	quietly widens is how the whole fleet lands on the "needs you" list."""

	def test_a_quarantined_member_forces_attention_and_elevated(self):
		"""The measured brew:quarantined route: security-only plus one bare
		string in items[] came out security_auto/low/pre-accepted, with
		W-MEMBER-QUARANTINED the only trace."""
		view, _ = validate_one({"id": "brew:x", "links": [], "items": [
			_item(tags=["security"], severity="info",
				security={"cve_id": None, "rating": "medium", "rating_basis": "nvd",
					"exploited_in_wild": False}),
			"a bare string member"]})
		self.assertEqual(view["degradation"]["content_losing"], ["quarantined-content"])
		self.assertEqual(view["risk_level"], "elevated")
		self.assertEqual(view["initial_review_bucket"], "attention")

	def test_a_coerced_items_container_forces_attention_and_elevated(self):
		"""The measured brew:coerced route: items as an object map plus a
		non-empty vendor_silent_categories came out routine/low/pre-accepted —
		the members were not even quarantined."""
		view, _ = validate_one({"id": "brew:x", "links": [],
			"items": {"0": _item()}, "vendor_silent_categories": ["deprecation"]})
		self.assertEqual(view["degradation"]["content_losing"], ["shape-coerced"])
		self.assertEqual(view["risk_level"], "elevated")
		self.assertEqual(view["initial_review_bucket"], "attention")

	def test_clause_zero_outranks_the_source_clause(self):
		"""An `expected` brew-health finding is the one class of tool whose
		findings are routinely waved through — its degradation must not also
		be invisible."""
		candidate = _candidate(id="brew-health:f", name="f", source="brew-health",
			current_version=None, latest_version=None, expected=True)
		research = {"id": "brew-health:f", "links": [], "items": [_item(), 7]}
		view, _ = validate_one(research, candidate=candidate)
		self.assertEqual(view["degradation"]["content_losing"], ["quarantined-content"])
		self.assertEqual(view["initial_review_bucket"], "attention")
		self.assertEqual(view["risk_level"], "elevated")

	def test_marker_findings_move_nothing(self):
		"""D1(b)'s other half: an evidence-path typo is a marker, not a gate."""
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [
			_item(local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": ["no/such/path.txt"], "citations": []})]})
		self.assertIn("E-EVID-404", {f["code"] for f in findings.entries})
		self.assertEqual(view["degradation"]["content_losing"], [])
		self.assertIn("E-EVID-404", view["degradation"]["markers"])
		self.assertEqual(view["risk_level"], "low")
		self.assertEqual(view["initial_review_bucket"], "routine")

	def test_the_two_computations_cannot_disagree_on_content_losing(self):
		"""§S5's lesson: a guarantee that depends on statement order is one
		refactor from being false, so it is pinned. The bucket was computed
		from the FIRST spec_violations assignment (inside _derive_axes); the
		stored block comes from the second (after run-level checks). The
		content_losing limb must be identical in both — asserted here by
		recomputing the bucket from the final view."""
		document = V.validate_session(FIXTURE_SESSION, FIXTURE_ROOTS,
			manifest_root=MANIFEST_ROOT, unconfigured_roots=FIXTURE_UNCONFIGURED)
		for view in document["tools"]:
			with self.subTest(view["id"]):
				self.assertEqual(view["degradation"],
					model.compute_degradation(view))
				losing = model.content_losing(view)
				self.assertEqual(view["degradation"]["content_losing"], losing)
				if losing:
					self.assertEqual(view["initial_review_bucket"], "attention")
					self.assertEqual(view["risk_level"], "elevated")

	def test_late_run_level_codes_are_markers_and_flip_no_bucket(self):
		"""W-SUG-DUP-ID is raised after per-tool validation. It must appear in
		`markers` (the second computation) and must not have changed the
		bucket the first computation produced."""
		sug = {"id": "shared:id", "kind": "watch-item", "title": "w",
			"target_files": [], "command": None, "auto_runnable": False,
			"watch_topic": "t", "watch_note": "n", "rationale": "r"}
		document = session_with({"00-a.json": [
			{"id": "brew:x", "links": [], "items": [_item()], "suggestions": [dict(sug)]},
			{"id": "brew:y", "links": [], "items": [_item()], "suggestions": [dict(sug)]},
		]}, collect={"generated_at": "2026-09-07T00:00:00Z", "machine": {},
			"brew": [_candidate(), _candidate(id="brew:y", name="y")]})
		dup = [v for v in document["tools"] if "W-SUG-DUP-ID" in v["spec_violations"]]
		self.assertTrue(dup)
		for view in dup:
			self.assertIn("W-SUG-DUP-ID", view["degradation"]["markers"])
			self.assertEqual(view["degradation"]["content_losing"], [])
			self.assertEqual(view["initial_review_bucket"], "routine")


# ── I-20: watch-item hits ───────────────────────────────────────────────────
def _hit_item(watch_hit, severity="notable", local=True, **kw):
	item = _item(severity=severity, watch_hit=watch_hit, **kw)
	if local:
		item["local"] = {"direction": "unclear", "effect": "none",
			"statement": "How this lands here.", "evidence": [], "citations": []}
	return item


class WatchHitTests(unittest.TestCase):
	"""`watch_hit` is checker-authored and validator-grounded — the structured
	successor to the retired `Watch item hit:` literal, which died of being an
	unvalidated string. Every failure mode reports; nothing is dropped and no
	severity is changed."""

	TOPICS = frozenset({"a stored topic"})

	def _codes(self, item, watch_topics=TOPICS):
		research = {"id": "brew:x", "links": [], "items": [item]}
		return codes(research, watch_topics=watch_topics)

	def test_a_grounded_hit_raises_nothing_and_keeps_the_field(self):
		item = _hit_item({"topic": "a stored topic"})
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [item]},
			watch_topics=self.TOPICS)
		self.assertEqual(findings.entries, [])
		self.assertEqual(view["items"][0]["watch_hit"], {"topic": "a stored topic"})

	def test_extra_keys_inside_watch_hit_are_tolerated_and_kept(self):
		"""Same rule as `change`/`local`/`security`: the top-level unknown-key
		check does not recurse."""
		item = _hit_item({"topic": "a stored topic", "note": "checker context"})
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [item]},
			watch_topics=self.TOPICS)
		self.assertEqual(findings.entries, [])
		self.assertEqual(view["items"][0]["watch_hit"]["note"], "checker context")

	def test_an_unstored_topic_is_ungrounded_and_kept(self):
		item = _hit_item({"topic": "a topic nobody ever stored"})
		found = self._codes(item)
		self.assertIn("E-WATCH-HIT-UNGROUNDED", found)
		self.assertNotIn("W-WATCH-UNCHECKED", found)

	def test_the_match_is_exact_after_strip_never_fuzzy(self):
		"""A checker copies the string, it does not rewrite it."""
		self.assertNotIn("E-WATCH-HIT-UNGROUNDED",
			self._codes(_hit_item({"topic": "  a stored topic  "})))
		self.assertIn("E-WATCH-HIT-UNGROUNDED",
			self._codes(_hit_item({"topic": "A Stored Topic"})))

	def test_grounding_is_per_tool_not_per_string(self):
		"""A topic stored for another tool does not ground a hit here — the
		empty set for this tool is not the same as no snapshot."""
		found = self._codes(_hit_item({"topic": "a stored topic"}),
			watch_topics=frozenset())
		self.assertIn("E-WATCH-HIT-UNGROUNDED", found)
		self.assertNotIn("W-WATCH-UNCHECKED", found)

	def test_no_snapshot_degrades_to_unchecked_and_the_hit_is_kept(self):
		item = _hit_item({"topic": "a topic nobody ever stored"})
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [item]})
		found = {f["code"] for f in findings.entries}
		self.assertIn("W-WATCH-UNCHECKED", found)
		self.assertNotIn("E-WATCH-HIT-UNGROUNDED", found)
		self.assertEqual(view["items"][0]["watch_hit"],
			{"topic": "a topic nobody ever stored"})

	def test_a_snapshotless_session_reports_unchecked_for_every_hit(self):
		"""The session-level half of the same rule, on a copy of the published
		corpus with watch-items.json removed."""
		td, session = session_without_snapshot()
		self.addCleanup(shutil.rmtree, td, True)
		document = V.validate_session(session, FIXTURE_ROOTS,
			manifest_root=MANIFEST_ROOT, unconfigured_roots=FIXTURE_UNCONFIGURED)
		by_code = document["counts"]["by_code"]
		self.assertIn("W-WATCH-UNCHECKED", by_code)
		self.assertNotIn("E-WATCH-HIT-UNGROUNDED", by_code)

	def test_an_unreadable_or_wrong_typed_snapshot_is_reported_never_fatal(self):
		td, session = session_without_snapshot()
		self.addCleanup(shutil.rmtree, td, True)
		for blob in ('{"truncated', '["not", "an", "object"]'):
			with self.subTest(blob):
				with open(os.path.join(session, "watch-items.json"), "w",
						encoding="utf-8") as fh:
					fh.write(blob)
				document = V.validate_session(session, FIXTURE_ROOTS,
					manifest_root=MANIFEST_ROOT, unconfigured_roots=FIXTURE_UNCONFIGURED)
				unreadable = [f for f in document["findings"]
					if f["code"] == "E-RESEARCH-UNREADABLE"
					and f["field"] == "watch-items.json"]
				self.assertEqual(len(unreadable), 1)
				self.assertIn("W-WATCH-UNCHECKED", document["counts"]["by_code"])

	def test_a_hit_with_no_local_block_reports_both_findings(self):
		"""E-WATCH-HIT-NOLOCAL fires independently of the topic checks."""
		found = self._codes(_hit_item({"topic": "a topic nobody ever stored"},
			local=False))
		self.assertIn("E-WATCH-HIT-UNGROUNDED", found)
		self.assertIn("E-WATCH-HIT-NOLOCAL", found)

	def test_a_hit_at_info_is_reported_and_never_bumped(self):
		"""Same doctrine as W-TITLE-LONG: bumping a severity is re-rating,
		which is convergence's."""
		item = _hit_item({"topic": "a stored topic"}, severity="info")
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [item]},
			watch_topics=self.TOPICS)
		found = {f["code"] for f in findings.entries}
		self.assertIn("W-WATCH-HIT-UNRAISED", found)
		self.assertEqual(view["items"][0]["severity"], "info")

	def test_malformed_watch_hit_shapes_report_and_keep(self):
		cases = (
			({}, "E-FIELD-MISSING", "watch_hit.topic"),
			({"topic": None}, "E-FIELD-MISSING", "watch_hit.topic"),
			({"topic": 7}, "E-FIELD-TYPE", "watch_hit.topic"),
			({"topic": "   "}, "E-FIELD-MISSING", "watch_hit.topic"),
			("a bare string", "E-FIELD-TYPE", "watch_hit"),
		)
		for hit, code, field in cases:
			with self.subTest(repr(hit)):
				view, findings = validate_one(
					{"id": "brew:x", "links": [], "items": [_hit_item(hit)]},
					watch_topics=self.TOPICS)
				matches = [f for f in findings.entries
					if f["code"] == code and f["field"] == field]
				self.assertEqual(len(matches), 1)
				# Kept verbatim, whatever the shape.
				self.assertEqual(view["items"][0]["watch_hit"], hit)

	def test_a_non_dict_hit_raises_no_secondary_findings(self):
		"""A bare-string watch_hit is one E-FIELD-TYPE, not a cascade — the
		local/severity checks apply to a *claim*, which a non-object is not."""
		found = self._codes(_hit_item("a bare string", severity="info", local=False))
		self.assertNotIn("E-WATCH-HIT-NOLOCAL", found)
		self.assertNotIn("W-WATCH-HIT-UNRAISED", found)


if __name__ == "__main__":
	unittest.main()
