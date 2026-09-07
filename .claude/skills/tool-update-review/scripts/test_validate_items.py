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
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

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
		manifest_root=None):
	"""→ (view, findings). One tool, straight through V2–V6, with the fixture's
	own roots so nothing depends on what sits beside the repo."""
	findings = V.Findings()
	resolver = V.RootResolver(roots if roots is not None else FIXTURE_ROOTS,
		unconfigured_roots=FIXTURE_UNCONFIGURED if unconfigured is None else unconfigured)
	manifest = V.Manifest(manifest_root or MANIFEST_ROOT)
	view = V.validate_tool(candidate or _candidate(), research, findings, resolver, manifest)
	return view, findings


def codes(research=None, **kw):
	return sorted({f["code"] for f in validate_one(research, **kw)[1].entries})


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

	def test_a_bad_line_locator_is_reported_and_the_path_still_resolves(self):
		view, findings = validate_one({"id": "brew:x", "links": [], "items": [_item(
			local={"direction": "unclear", "effect": "none", "statement": "s",
				"evidence": [{"path": "Brewfile", "lines": ["six", True, [1, 2, 3]]}]})]})
		self.assertEqual(view["items"][0]["local"]["evidence"], [{"path": "Brewfile"}])
		self.assertEqual({f["code"] for f in findings.entries}, {"E-FIELD-TYPE"})

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

	def test_a_crashing_stage_costs_one_tool_and_says_so(self):
		"""The next unknown shape — the one no test anticipated — must cost one
		tool, loudly, rather than the run."""
		original = V.validate_item

		def explode(*args, **kwargs):
			raise RuntimeError("synthetic")

		V.validate_item = explode
		try:
			document = session_with({"a.json": [
				{"id": "brew:x", "links": [], "items": [_item()]},
				{"id": "brew:y", "links": [], "items": []}]},
				collect={"generated_at": "t", "machine": {},
					"brew": [_candidate(), _candidate(id="brew:y", name="y")]})
		finally:
			V.validate_item = original
		self.assertIn("E-VALIDATOR-CRASH", document["counts"]["by_code"])
		self.assertEqual([t["id"] for t in document["tools"]], ["brew:x", "brew:y"])
		self.assertEqual(document["tools"][0]["initial_review_bucket"], "attention")
		self.assertEqual(document["tools"][1]["items"], [])

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
			with open(os.path.join(td, "validation.warn"), encoding="utf-8") as fh:
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
			with open(os.path.join(td, "validation.warn"), encoding="utf-8") as fh:
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


if __name__ == "__main__":
	unittest.main()
