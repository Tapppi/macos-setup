#!/usr/bin/env python3
"""
test_converge.py — the convergence output contract and its applier.
Usage: python3 test_converge.py [-v]

Stdlib `unittest` only, same constraint as every suite here.

Groups:

1. Fixture agreement — `convergence.json`, `expected_converge_view.json`,
   `expected_converge_tables.json` and `expected_converge_effect.json` are
   generated, so the checked-in copies must match what the code produces,
   and `contract/converge.json` (the hand-written submission) must still
   converge with zero critical findings. Ordering drifted twice on this
   project against a pinned FIELD contract; executable fixtures are the fix.
2. corpus.pre composition and the projections.
3. The applier's phases — every code in `converge.CODES`, both directions:
   the malformed submission is REPORTED (never silently skipped, never a
   traceback), and the conforming one passes.
4. Differential recomputation, leave-one-out attribution, the hard gate.
5. The seven checks' attestation arithmetic — each `verify_cN` has a test
   that fails if the verifier is deleted (this codebase measured ten areas
   that survived deletion green; an uncaught check is a known failure mode
   here, not a hypothesis).
6. corpus_effect arithmetic and the narrative rule.
7. The five-attempt loop and its conservative degradation, through the CLI.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_converge  # noqa: E402
import converge as C  # noqa: E402
import items as model  # noqa: E402
import validate_items  # noqa: E402


# ── the fixture corpus, built once ──────────────────────────────────────────
def _fixture_corpus():
	session, roots, unconfigured = validate_items.fixture_session()
	validation = validate_items.validate_session(session, roots,
		manifest_root=roots[0], unconfigured_roots=unconfigured)
	with open(os.path.join(session, "collect.json"), encoding="utf-8") as fh:
		collect = json.load(fh)
	with open(os.path.join(session, "watch-items.json"), encoding="utf-8") as fh:
		stores = {"watch_items": json.load(fh)}
	return C.build_corpus_pre(validation, collect, stores)


FIXTURE_PRE = _fixture_corpus()
FIXTURE_SUBMISSION = model.load_fixture("converge.json")


def fixture_pre():
	return copy.deepcopy(FIXTURE_PRE)


def fixture_submission():
	return copy.deepcopy(FIXTURE_SUBMISSION)


# ── synthetic corpora ───────────────────────────────────────────────────────
def make_item(tool_id, number, tags=("fix",), severity="notable", local=None,
		security=None, title=None, body=None, watch_hit=None):
	anchor = {"kind": "issue", "value": "o/r#{}".format(number)}
	item = {
		"anchor": anchor,
		"title": title or "Synthetic change number {}".format(number),
		"tags": list(tags),
		"severity": severity,
		"change": {"version": "1.0.1",
			"citation": "Upstream text for change {}".format(number),
			"link_index": None},
		"id": model.derive_item_id(tool_id, anchor),
		"id_stability": model.id_stability(anchor),
	}
	if body is not None:
		item["body"] = body
	if local is not None:
		item["local"] = local
	if security is not None:
		item["security"] = security
	if watch_hit is not None:
		item["watch_hit"] = watch_hit
	return item


def plain_local(direction="unclear", effect="none", statement="How it lands here."):
	return {"direction": direction, "effect": effect, "statement": statement,
		"evidence": [], "citations": []}


def make_view(tool_id, items, suggestions=None, watch_topics=None,
		version_delta="patch"):
	"""A validator-shaped view whose derived axes are stamped by the SAME
	function the applier recomputes with, so the internal self-check holds
	by construction."""
	source, _, name = tool_id.partition(":")
	view = {
		"id": tool_id, "source": source, "name": name, "pinned": False,
		"research_error": None, "validator_error": None,
		"links": [], "vendor_silent_categories": [],
		"items": model.order_items(items),
		"quarantine": [],
		"config_status": validate_items.default_config_status(),
		"suggestions": list(suggestions or []),
		"subject_refs": [], "flags": model.recompute_flags(items),
		"version_delta": version_delta,
		"impact": "unknown", "risk_level": "elevated",
		"initial_review_bucket": "attention",
		"bucket_inputs": {"has_security": False, "security_only": False,
			"impact": "unknown", "version_delta": version_delta, "runnable": True},
		"security_display_item_ids": [], "watch_hit_item_ids": [],
		"self_test_tagged_suggestion_ids": [],
		"spec_violations": [],
		"degradation": {"content_losing": [], "markers": [], "quarantined": 0},
	}
	view["config_status"]["state"] = "up_to_date"
	C.derive_tool_state(view, watch_topics)
	return view


def build_pre(views, watch_store=None, findings=None):
	return {
		"contract_version": model.CONTRACT_VERSION,
		"generated_at": "2026-09-17T00:00:00Z",
		"session_id": "synthetic",
		"run_id": "synthetic",
		"converge_version": C.CONVERGE_VERSION,
		"clean": not findings,
		"findings": list(findings or []),
		"counts": {"by_code": {}, "by_tool": {}, "by_severity": {}},
		"orphans": [], "unmatched": [],
		"subject_index": {}, "target_file_index": {},
		"stores": {"watch_items": watch_store, "method_notes": None},
		"tools": views,
	}


# ── the submission helper ───────────────────────────────────────────────────
def _auto_checks(pre, edits):
	tables = C.build_tables(pre)
	distributions = tables["distributions"]
	by_check = {name: [] for name in C.CHECK_IDS}
	for edit in edits:
		if edit.get("check") in by_check:
			by_check[edit["check"]].append(edit["edit_id"])
	touched_c2 = {(e.get("target") or {}).get("id") for e in edits
		if e.get("check") == "C2-tags-visibility"
		and (e.get("target") or {}).get("kind") == "item"}
	touched_c2.discard(None)
	security_only = []
	for view in pre["tools"]:
		if (view.get("bucket_inputs") or {}).get("security_only"):
			cves = {i["security"]["cve_id"] for i in view["items"]
				if isinstance(i.get("security"), dict)
				and isinstance(i["security"].get("cve_id"), str)}
			security_only.append({"tool_id": view["id"], "cve_count": len(cves),
				"verdict": "ok"})
	auto = [{"tool_id": view["id"], "deciding_input": "risk_level",
		"verdict": "ok"} for view in pre["tools"]
		if view.get("initial_review_bucket") == "security_auto"
		or view.get("initial_pre_accept")]
	security_items = unrated = dnr = 0
	for view in pre["tools"]:
		for item in view["items"]:
			tags = item.get("tags")
			if not (isinstance(tags, list) and "security" in tags):
				continue
			security_items += 1
			sec = item.get("security")
			if isinstance(sec, dict) and sec.get("rating_basis") == "unrated":
				unrated += 1
			local = item.get("local")
			if isinstance(local, dict) and local.get("direction") == "does_not_reach":
				dnr += 1
	return [
		{"check": "C1-evidence",
			"scanned": {"tools": len(pre["tools"]),
				"items": distributions["items"],
				"evidence_entries": distributions["evidence_entries"]},
			"findings": len(tables["evidence_findings"]),
			"edits": by_check["C1-evidence"]},
		{"check": "C2-tags-visibility",
			"scanned": {"items": distributions["items"]},
			"clean": distributions["items"] - len(touched_c2),
			"edits": by_check["C2-tags-visibility"]},
		{"check": "C3-security-only", "security_only_tools": security_only,
			"edits": by_check["C3-security-only"]},
		{"check": "C4-notable-security",
			"scanned": {"security_items": security_items,
				"rating_unrated": unrated, "direction_does_not_reach": dnr,
				"anchor_duplicates": len(tables["anchor_duplicates"])},
			"edits": by_check["C4-notable-security"]},
		{"check": "C5-auto-approval", "tools": auto,
			"edits": by_check["C5-auto-approval"]},
		{"check": "C6-memory", "edits": by_check["C6-memory"]},
		{"check": "C7-collisions",
			"clusters_inspected": len(tables["file_collisions"]),
			"resolved": [], "flagged": 0, "edits": by_check["C7-collisions"]},
	]


def _auto_ledger(pre):
	tables = C.build_tables(pre)
	snapshot = (pre.get("stores") or {}).get("watch_items") or {}
	run_tools = {v["id"] for v in pre["tools"]}
	existing = []
	for tool_id, entries in sorted(snapshot.items()):
		if tool_id not in run_tools:
			continue
		grounded_topics = set()
		view = next(v for v in pre["tools"] if v["id"] == tool_id)
		for item in view["items"]:
			if item.get("id") in (view.get("watch_hit_item_ids") or []):
				grounded_topics.add((item.get("watch_hit") or {})
					.get("topic", "").strip())
		for stored in entries:
			topic = stored["topic"].strip()
			existing.append({"tool_id": tool_id, "topic": topic,
				"fired_this_run": topic in grounded_topics,
				"used_correctly": True, "note": "auto"})
	watch_kept, notes_kept = [], []
	for bucket, out in (("watch_item", watch_kept), ("method_note_tool", notes_kept)):
		for proposal in tables["proposals"][bucket]:
			row = {"suggestion_id": proposal["suggestion_id"], "reason": "auto"}
			if proposal["self_test"]["verdict"] == "fails":
				row["restored"] = True
			out.append(row)
	return {
		"watch_items": {"existing": existing, "proposed_kept": watch_kept,
			"proposed_cut": [], "rehomed_to_method_note": []},
		"method_notes_tool": {"kept": notes_kept, "cut": [],
			"promoted_to_global": []},
		"method_notes_global": {"kept": [], "cut": [], "demoted_to_tool": []},
	}


def make_submission(pre, edits, attempt=1, checks=None, ledger=None,
		corpus_effect=None):
	"""A submission whose attestations, ledger and effect are correct for
	`edits` over `pre` — so a test perturbs exactly the one thing it is
	about."""
	submission = {
		"run_id": pre.get("run_id"),
		"converge_version": C.CONVERGE_VERSION,
		"view_version": C.VIEW_VERSION,
		"corpus_digest": C.canonical_digest(pre),
		"attempt": attempt,
		"checks": checks if checks is not None else _auto_checks(pre, edits),
		"edits": edits,
		"ledger": ledger if ledger is not None else _auto_ledger(pre),
		"corpus_effect": corpus_effect or {},
	}
	if corpus_effect is None:
		probe = apply_converge.apply_converge(pre, copy.deepcopy(submission))
		computed = probe.get("computed_effect") or apply_converge \
			.compute_corpus_effect(pre, pre, [], {})
		effect = copy.deepcopy(computed)
		effect["narrative"] = "Movement summary naming every tool: " + \
			", ".join(sorted(v["id"] for v in pre["tools"])) + "."
		submission["corpus_effect"] = effect
	return submission


def run(pre, submission, terminal=False, attempt=1):
	return apply_converge.apply_converge(copy.deepcopy(pre), submission,
		attempt=attempt, terminal=terminal)


def codes_of(result):
	return sorted({f["code"] for f in result["critical"]})


def cut_reason(headline="A reasoned cut.", body=None):
	return {"headline": headline,
		"body": body or ("The item cannot change any decision on its own; the "
			"fact survives in the sibling item and the tool's bucket is "
			"untouched by this removal."),
		"rule_ref": "references/convergence.md §3.2", "confidence": "high"}


def lateral(bucket):
	return {"moves_bucket": False, "expected_from": bucket,
		"expected_to": bucket, "direction": "lateral"}


# ═════════════════════════════════════════════════════════════════════════════
class FixtureAgreementTests(unittest.TestCase):
	"""The published fixtures still agree with the code — a fixture that can
	go stale is worth no more than a paragraph."""

	def test_convergence_contract_fixture(self):
		self.assertEqual(model.load_fixture("convergence.json"), C.contract())

	def test_view_fixture(self):
		self.assertEqual(model.load_fixture("expected_converge_view.json"),
			C.build_view(FIXTURE_PRE))

	def test_tables_fixture(self):
		self.assertEqual(model.load_fixture("expected_converge_tables.json"),
			C.build_tables(FIXTURE_PRE))

	def test_effect_fixture_and_pinned_submission_converge(self):
		result = run(FIXTURE_PRE, fixture_submission())
		self.assertEqual(result["state"], "converged")
		self.assertEqual(result["critical"], [])
		effect = apply_converge.finalize_clean(fixture_pre(),
			fixture_submission(), result, 1, [])
		expected = model.load_fixture("expected_converge_effect.json")
		self.assertEqual(expected["corpus_pre_digest"],
			C.canonical_digest(FIXTURE_PRE))
		self.assertEqual(expected["corpus_post_digest"],
			C.canonical_digest(result["corpus_post"]))
		self.assertEqual(expected["effect"], effect)

	def test_pinned_label_is_judgement_with_counterweight(self):
		"""The fixture's one permissive move carries the §4 label: source
		computed (not asserted), reasoning verbatim, quote from the cut."""
		expected = model.load_fixture("expected_converge_effect.json")
		block = expected["effect"]["tools"]["brew:openssh"]
		label = block["auto_update_label"]
		self.assertEqual(label["source"], "judgement")
		self.assertEqual(label["edit_ids"], ["cv-003"])
		self.assertEqual(block["bucket"]["attributed_to"], ["cv-003"])
		self.assertIn("security_auto", label["reasoning"])
		self.assertEqual(label["counterweight"]["items_removed"], 1)
		self.assertTrue(label["quotes"][0]["text"])

	def test_view_version_and_digest_travel(self):
		view = model.load_fixture("expected_converge_view.json")
		self.assertEqual(view["view_version"], C.VIEW_VERSION)
		self.assertEqual(view["corpus_digest"], C.canonical_digest(FIXTURE_PRE))


class CorpusPreTests(unittest.TestCase):
	def test_versions_and_flags_composed(self):
		openssh = next(v for v in FIXTURE_PRE["tools"] if v["id"] == "brew:openssh")
		self.assertEqual(openssh["current_version"], "10.4p1")
		self.assertEqual(openssh["latest_version"], "10.5p1")
		self.assertTrue(openssh["initial_pre_accept"])
		self.assertEqual(openssh["pre_accept_bars"], [])

	def test_initial_pre_accept_conjuncts(self):
		"""Every conjunct of the view-level predicate flips it — the mapping
		of assemble.apply_pre_accept onto the view, clause for clause."""
		base = next(v for v in FIXTURE_PRE["tools"] if v["id"] == "brew:openssh")
		self.assertTrue(C.initial_pre_accept(base))
		for mutate in (
				lambda v: v.__setitem__("initial_review_bucket", "attention"),
				lambda v: v.__setitem__("risk_level", "elevated"),
				lambda v: v.__setitem__("source", "brew-health"),
				lambda v: v["bucket_inputs"].__setitem__("runnable", False),
				lambda v: v.__setitem__("quarantine", [{"field": "x",
					"item_id": None, "value": 1}]),
				# The EXPORTED bars, never recomputed (G-SEC: one predicate,
				# items.accepts_baseline) — so the bar conjunct is the field.
				lambda v: v.__setitem__("pre_accept_bars", ["enum-invalid"]),
				lambda v: v.__setitem__("pre_accept_bars", "not a list"),
				lambda v: v.pop("pre_accept_bars"),
				lambda v: v.pop("security_tier"),
				lambda v: v.__setitem__("security_tier", {}),
				lambda v: v.__setitem__("forced_conservative", {"code": "x"})):
			view = copy.deepcopy(base)
			mutate(view)
			self.assertFalse(C.initial_pre_accept(view))

	def test_inputs_not_mutated_and_corpus_immutable_shape(self):
		session, roots, unconfigured = validate_items.fixture_session()
		validation = validate_items.validate_session(session, roots,
			manifest_root=roots[0], unconfigured_roots=unconfigured)
		snapshot = copy.deepcopy(validation)
		with open(os.path.join(session, "collect.json"), encoding="utf-8") as fh:
			collect = json.load(fh)
		C.build_corpus_pre(validation, collect, {})
		self.assertEqual(validation, snapshot)

	def test_digest_is_order_insensitive_and_content_sensitive(self):
		a = {"x": 1, "y": [1, 2]}
		b = {"y": [1, 2], "x": 1}
		self.assertEqual(C.canonical_digest(a), C.canonical_digest(b))
		self.assertNotEqual(C.canonical_digest(a), C.canonical_digest({"x": 2,
			"y": [1, 2]}))


class ProjectionTests(unittest.TestCase):
	def test_body_becomes_has_body(self):
		view = C.build_view(FIXTURE_PRE)
		codex = next(t for t in view["tools"] if t["id"] == "cask:codex")
		with_body = next(i for i in codex["items"]
			if i["id"] == "cask:codex#issue:openai%2Fcodex%234110")
		without = next(i for i in codex["items"]
			if i["id"] == "cask:codex#cve:CVE-2026-18408")
		self.assertTrue(with_body["has_body"])
		self.assertFalse(without["has_body"])
		self.assertNotIn("body", with_body)

	def test_config_status_projected_to_state(self):
		view = C.build_view(FIXTURE_PRE)
		codex = next(t for t in view["tools"] if t["id"] == "cask:codex")
		self.assertEqual(codex["config_status_state"], "needs_attention")
		self.assertNotIn("config_status", codex)

	def test_suggestions_ride_whole(self):
		view = C.build_view(FIXTURE_PRE)
		codex = next(t for t in view["tools"] if t["id"] == "cask:codex")
		pre_codex = next(t for t in FIXTURE_PRE["tools"] if t["id"] == "cask:codex")
		self.assertEqual(codex["suggestions"], pre_codex["suggestions"])


class TablesTests(unittest.TestCase):
	def test_proposals_routed_with_self_test_verdicts(self):
		tables = C.build_tables(FIXTURE_PRE)
		watch = {p["suggestion_id"]: p for p in tables["proposals"]["watch_item"]}
		self.assertEqual(sorted(watch), ["brew:nonconforming:watch-no-reason"])
		self.assertEqual(watch["brew:nonconforming:watch-no-reason"]
			["self_test"]["verdict"], "fails")
		notes = {p["suggestion_id"]: p
			for p in tables["proposals"]["method_note_tool"]}
		self.assertEqual(notes["brew:attention:method-xdg-path"]
			["self_test"]["verdict"], "passes")
		self.assertEqual(tables["proposals"]["method_note_global"], [])

	def test_file_collisions_need_two(self):
		pre = fixture_pre()
		pre["target_file_index"] = {"setup.sh": ["a:1"], "Brewfile": ["a:1", "b:2"]}
		tables = C.build_tables(pre)
		self.assertEqual([c["path"] for c in tables["file_collisions"]],
			["Brewfile"])

	def test_anchor_duplicates_cross_tool_only(self):
		security = {"cve_id": "CVE-2026-11111", "advisory_id": None,
			"rating": "high", "rating_basis": "nvd", "exploited_in_wild": False}
		item_a = make_item("brew:a", 1, tags=("security", "fix"), security=security,
			local=plain_local())
		item_a["anchor"] = {"kind": "cve", "value": "CVE-2026-11111"}
		item_a["id"] = model.derive_item_id("brew:a", item_a["anchor"])
		item_b = copy.deepcopy(item_a)
		item_b["id"] = model.derive_item_id("brew:b", item_b["anchor"])
		pre = build_pre([make_view("brew:a", [item_a]),
			make_view("brew:b", [item_b])])
		tables = C.build_tables(pre)
		self.assertEqual(len(tables["anchor_duplicates"]), 1)
		self.assertEqual(tables["anchor_duplicates"][0]["anchor"],
			"cve:CVE-2026-11111")

	def test_evidence_findings_are_the_declared_codes(self):
		tables = C.build_tables(FIXTURE_PRE)
		self.assertEqual(
			sorted({f["code"] for f in tables["evidence_findings"]}),
			sorted({"E-EVID-MALFORMED", "E-EVID-404", "W-EVID-ROOT",
				"E-REACHES-UNEVIDENCED", "E-USAGE-UNGROUNDED", "W-USAGE-INSTALL-ONLY"}))
		self.assertEqual(len(tables["evidence_findings"]), 9)


# ═════════════════════════════════════════════════════════════════════════════
class SubmissionLevelTests(unittest.TestCase):
	"""Phase 1 — a submission that cannot be resolved applies NOTHING."""

	def test_not_an_object(self):
		result = run(FIXTURE_PRE, ["not", "a", "submission"])
		self.assertEqual(result["state"], "rejected")
		self.assertIn("E-SUBMIT-SHAPE", codes_of(result))
		self.assertIsNone(result["corpus_post"])

	def test_run_id_version_digest(self):
		submission = fixture_submission()
		submission["run_id"] = "someone-elses-session"
		self.assertIn("E-SUBMIT-RUN", codes_of(run(FIXTURE_PRE, submission)))
		submission = fixture_submission()
		submission["view_version"] = 99
		self.assertIn("E-SUBMIT-VERSION", codes_of(run(FIXTURE_PRE, submission)))
		submission = fixture_submission()
		submission["corpus_digest"] = "sha256:" + "0" * 64
		self.assertIn("E-SUBMIT-DIGEST", codes_of(run(FIXTURE_PRE, submission)))

	def test_duplicate_edit_id(self):
		submission = fixture_submission()
		submission["edits"].append(copy.deepcopy(submission["edits"][0]))
		self.assertIn("E-SUBMIT-SHAPE", codes_of(run(FIXTURE_PRE, submission)))

	def test_missing_blocks(self):
		submission = fixture_submission()
		del submission["ledger"]
		self.assertIn("E-SUBMIT-SHAPE", codes_of(run(FIXTURE_PRE, submission)))


class PrecheckTests(unittest.TestCase):
	"""Phase 2 — per-edit rejection, coded, never silent."""

	def setUp(self):
		self.item = make_item("brew:t", 1, severity="notable",
			local=plain_local(statement="A statement long enough to quote from."))
		self.view = make_view("brew:t", [self.item])
		self.pre = build_pre([self.view])

	def one(self, edit, **kwargs):
		return run(self.pre, make_submission(self.pre, [edit], **kwargs))

	def delete_edit(self, **overrides):
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "delete",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": self.item["id"], "field": None},
			"quote": "A statement long enough to quote from.",
			"bucket_claim": lateral("routine"),
			"reason": cut_reason()}
		edit.update(overrides)
		return edit

	def test_clean_delete_applies(self):
		result = self.one(self.delete_edit())
		self.assertEqual(result["critical"], [])
		self.assertEqual(result["applied"], ["cv-001"])
		post_view = result["corpus_post"]["tools"][0]
		self.assertEqual(post_view["items"], [])

	def test_target_unresolved(self):
		result = self.one(self.delete_edit(target={"tool_id": "brew:t",
			"kind": "item", "id": "brew:t#issue:nothing", "field": None}))
		self.assertIn("E-EDIT-TARGET", codes_of(result))
		self.assertEqual(result["rejected"][0]["code"], "E-EDIT-TARGET")

	def test_unknown_tool(self):
		result = self.one(self.delete_edit(target={"tool_id": "brew:ghost",
			"kind": "item", "id": self.item["id"], "field": None}))
		self.assertIn("E-EDIT-TARGET", codes_of(result))

	def test_field_on_whole_element_op(self):
		result = self.one(self.delete_edit(target={"tool_id": "brew:t",
			"kind": "item", "id": self.item["id"], "field": "tags"}))
		self.assertIn("E-EDIT-TARGET", codes_of(result))

	def retag_edit(self, **overrides):
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "retag",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": self.item["id"], "field": "tags"},
			"precondition": {"before": ["fix"]},
			"quote": "A statement long enough to quote from.",
			"after": ["chore"],
			"bucket_claim": lateral("routine"),
			"reason": cut_reason()}
		edit.update(overrides)
		return edit

	def test_precondition_mismatch(self):
		result = self.one(self.retag_edit(precondition={"before": ["security"]}))
		self.assertIn("E-EDIT-PRECOND", codes_of(result))
		detail = next(f for f in result["critical"]
			if f["code"] == "E-EDIT-PRECOND")["detail"]
		self.assertIn("fix", detail)  # the resolved value comes back

	def test_quote_missing_and_unresolved(self):
		result = self.one({k: v for k, v in self.retag_edit().items()
			if k != "quote"})
		self.assertIn("E-EDIT-QUOTE", codes_of(result))
		result = self.one(self.retag_edit(quote="text the element never held"))
		self.assertIn("E-EDIT-QUOTE", codes_of(result))

	def test_quote_must_come_from_body_when_item_has_one(self):
		"""§3.3 — the projection withheld `body`; a cut of an item that has
		one can only be satisfied by text read out of corpus.pre."""
		item = make_item("brew:t", 2, body="The body paragraph nobody saw in "
			"the projection.", local=plain_local())
		view = make_view("brew:t", [self.item, item])
		pre = build_pre([view])
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "delete",
			"target": {"tool_id": "brew:t", "kind": "item", "id": item["id"],
				"field": None},
			"quote": item["title"],  # verbatim from the TITLE — not enough
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]))
		self.assertIn("E-EDIT-QUOTE", codes_of(result))
		edit["quote"] = "paragraph nobody saw"
		result = run(pre, make_submission(pre, [edit]))
		self.assertNotIn("E-EDIT-QUOTE", codes_of(result))

	def test_bucket_claim_required_and_forbidden(self):
		result = self.one({k: v for k, v in self.delete_edit().items()
			if k != "bucket_claim"})
		self.assertIn("E-EDIT-OP", codes_of(result))
		trim = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "trim",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": self.item["id"], "field": "local.statement"},
			"precondition": {"before": "A statement long enough to quote from."},
			"after": "A statement long enough to quote",
			"bucket_claim": lateral("routine"),
			"reason": {"headline": "Shorter."}}
		self.assertIn("E-EDIT-OP", codes_of(self.one(trim)))

	def test_retag_outside_closed_set(self):
		result = self.one(self.retag_edit(after=["notes"]))
		self.assertIn("E-EDIT-OP", codes_of(result))

	def test_rerate_and_redirect_vocabularies(self):
		rerate = self.retag_edit(op="rerate",
			target={"tool_id": "brew:t", "kind": "item", "id": self.item["id"],
				"field": "severity"},
			precondition={"before": "notable"}, after="loud")
		self.assertIn("E-EDIT-OP", codes_of(self.one(rerate)))
		redirect = self.retag_edit(op="redirect",
			target={"tool_id": "brew:t", "kind": "item", "id": self.item["id"],
				"field": "local.direction"},
			precondition={"before": "unclear"}, after="sideways")
		self.assertIn("E-EDIT-OP", codes_of(self.one(redirect)))

	def test_trim_must_be_token_subsequence(self):
		trim = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "trim",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": self.item["id"], "field": "local.statement"},
			"precondition": {"before": "A statement long enough to quote from."},
			"after": "A statement rewritten in other words.",
			"reason": {"headline": "Shorter."}}
		self.assertIn("E-EDIT-OP", codes_of(self.one(trim)))
		trim["after"] = "A statement long enough to quote"  # dropped "from."
		result = self.one(trim)
		self.assertNotIn("E-EDIT-OP", codes_of(result))

	def test_noop_edit_rejected(self):
		result = self.one(self.retag_edit(after=["fix"],
			precondition={"before": ["fix"]}))
		self.assertIn("E-EDIT-OP", codes_of(result))

	def test_headline_length_capped(self):
		result = self.one(self.delete_edit(reason=cut_reason(
			headline="x" * 141)))
		self.assertIn("E-EDIT-OP", codes_of(result))

	def test_cut_reason_needs_body_and_confidence(self):
		bad = cut_reason()
		del bad["body"]
		self.assertIn("E-EDIT-OP", codes_of(self.one(self.delete_edit(reason=bad))))
		bad = cut_reason()
		bad["confidence"] = "certain"
		self.assertIn("E-EDIT-OP", codes_of(self.one(self.delete_edit(reason=bad))))

	def test_requires_unknown_and_transitive(self):
		first = self.delete_edit()
		second = {"edit_id": "cv-002", "check": "C2-tags-visibility",
			"op": "rerate",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": self.item["id"], "field": "severity"},
			"precondition": {"before": "notable"},
			"quote": "A statement long enough to quote from.",
			"after": "info", "bucket_claim": lateral("routine"),
			"reason": cut_reason(), "requires": ["cv-404"]}
		result = run(self.pre, make_submission(self.pre, [second]))
		self.assertIn("E-EDIT-DEP", codes_of(result))
		# a rejected requirement rejects its dependents, transitively
		first["target"]["id"] = "brew:t#issue:nothing"  # rejects
		second["requires"] = ["cv-001"]
		third = dict(copy.deepcopy(second), edit_id="cv-003", requires=["cv-002"])
		third["after"] = "warning"
		result = run(self.pre, make_submission(self.pre,
			[first, second, third]))
		rejected_ids = {r["edit_id"] for r in result["rejected"]}
		self.assertEqual(rejected_ids, {"cv-001", "cv-002", "cv-003"})

	def test_stale_supersedes_rejects_the_edit_and_its_dependents(self):
		"""Review finding 2 — the exact silent-content-loss shape (a) exists
		to prevent: a merge with a stale `supersedes` id must be rejected AND
		take its required delete down with it, or the sibling is deleted
		while the merge that was to absorb its content never runs."""
		item1 = make_item("brew:t", 11, body="Body one to quote at length.",
			local=plain_local())
		item2 = make_item("brew:t", 12, body="Body two to quote at length.",
			local=plain_local())
		pre = build_pre([make_view("brew:t", [item1, item2])])
		after = copy.deepcopy(item1)
		after["body"] = "Merged body carrying item two's fact."
		merge = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "merge",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": item1["id"], "field": None},
			"quote": "Body one to quote", "after": after,
			"changed_fields": ["body"], "supersedes": ["cv-ghost"],
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		delete = {"edit_id": "cv-002", "check": "C2-tags-visibility",
			"op": "delete",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": item2["id"], "field": None},
			"quote": "Body two to quote", "requires": ["cv-001"],
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		for terminal in (False, True):
			result = run(pre, make_submission(pre, [merge, delete]),
				terminal=terminal, attempt=5 if terminal else 1)
			self.assertEqual(result["applied"], [])
			self.assertEqual(
				sorted(r["edit_id"] for r in result["rejected"]
					if r.get("edit_id")),
				["cv-001", "cv-002"])
			post_ids = [i["id"] for i in
				result["corpus_post"]["tools"][0]["items"]]
			self.assertIn(item2["id"], post_ids)  # the sibling SURVIVES
			self.assertEqual(result["state"],
				"degraded_unapplied" if terminal else "rejected")

	def test_merge_duplicate_changed_fields_is_coded_not_self_conflict(self):
		"""Review finding 4 — a duplicated path must come back as its own
		E-EDIT-OP, not as the edit named as its own E-EDIT-DUP conflict."""
		item = make_item("brew:t", 13, body="A body long enough to quote.",
			local=plain_local())
		pre = build_pre([make_view("brew:t", [item])])
		after = copy.deepcopy(item)
		after["body"] = "New body."
		merge = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "merge",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": item["id"], "field": None},
			"quote": "long enough to quote", "after": after,
			"changed_fields": ["body", "body"],
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [merge]))
		self.assertIn("E-EDIT-OP", codes_of(result))
		self.assertNotIn("E-EDIT-DUP", codes_of(result))
		detail = next(f["detail"] for f in result["critical"]
			if f["code"] == "E-EDIT-OP")
		self.assertIn("more than once", detail)

	def test_duplicate_writers_and_supersedes(self):
		a = self.retag_edit()
		b = self.retag_edit(edit_id="cv-002", after=["packaging"])
		result = run(self.pre, make_submission(self.pre, [a, b]))
		self.assertIn("E-EDIT-DUP", codes_of(result))
		b_sup = dict(copy.deepcopy(b), supersedes=["cv-001"])
		result = run(self.pre, make_submission(self.pre, [a, b_sup]))
		self.assertNotIn("E-EDIT-DUP", codes_of(result))
		self.assertIn("cv-001", result["superseded"])
		post_item = result["corpus_post"]["tools"][0]["items"][0]
		self.assertEqual(post_item["tags"], ["packaging"])

	def test_delete_conflicts_with_field_edit_on_same_element(self):
		result = run(self.pre, make_submission(self.pre,
			[self.delete_edit(), self.retag_edit(edit_id="cv-002")]))
		self.assertIn("E-EDIT-DUP", codes_of(result))

	def test_add_id_collision_and_prefix(self):
		proposal = {"id": "brew:t:note", "kind": "method-note",
			"method_topic": "t", "method_note": "n", "rationale": "r"}
		view = make_view("brew:t", [self.item], suggestions=[proposal])
		pre = build_pre([view])
		add = {"edit_id": "cv-001", "check": "C6-memory", "op": "add",
			"target": {"tool_id": "brew:t", "kind": "suggestion", "id": None,
				"field": None},
			"after": dict(proposal),
			"bucket_claim": lateral("attention"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [add]))
		self.assertIn("E-EDIT-ID", codes_of(result))
		add2 = copy.deepcopy(add)
		add2["after"]["id"] = "elsewhere:note"
		result = run(pre, make_submission(pre, [add2]))
		self.assertIn("E-EDIT-OP", codes_of(result))

	def test_add_must_be_memory_kind(self):
		add = {"edit_id": "cv-001", "check": "C6-memory", "op": "add",
			"target": {"tool_id": "brew:t", "kind": "suggestion", "id": None,
				"field": None},
			"after": {"id": "brew:t:edit-something", "kind": "edit",
				"title": "an authored action"},
			"bucket_claim": lateral("attention"), "reason": cut_reason()}
		result = self.one(add)
		self.assertIn("E-EDIT-OP", codes_of(result))

	def test_unknown_check_and_unknown_op(self):
		self.assertIn("E-EDIT-OP",
			codes_of(self.one(self.delete_edit(check="C9-invented"))))
		self.assertIn("E-EDIT-OP",
			codes_of(self.one(self.delete_edit(op="obliterate"))))


class GlobalNotePromotionTests(unittest.TestCase):
	"""Pass 6, cv-017: five per-tool notes (karabiner-elements,
	tailscale-app, spotify, obsidian, gcloud-cli) each recorded an app that
	self-updated past its Caskroom version; convergence flagged it and kept
	them per-tool, because promotion MOVES one tool-worded proposal and C6
	had no op to say the general thing. With `reword` in C6's vocabulary the
	route is: promote one, reword it general, cut the sibling it covers."""

	def setUp(self):
		def note(tool, app):
			return {"id": "{}:method-caskroom-lag".format(tool), "kind": "method-note",
				"title": "t", "method_topic": "{} version vs Caskroom".format(app),
				"method_note": "{} updates itself; its Caskroom version lags "
					"the running app.".format(app), "rationale": "r"}
		self.a = note("cask:spotify", "Spotify")
		self.b = note("cask:obsidian", "Obsidian")
		self.pre = build_pre([
			make_view("cask:spotify", [make_item("cask:spotify", 1)], suggestions=[self.a]),
			make_view("cask:obsidian", [make_item("cask:obsidian", 2)], suggestions=[self.b]),
		], watch_store={})
		self.pre["stores"]["method_notes"] = {}

	def submission(self, check="C6-memory"):
		general_topic = "Self-updating cask app ahead of its Caskroom version"
		general_note = ("A self-updating cask app runs ahead of the version the "
			"Caskroom records; read the running app's version, not the Caskroom's.")
		edits = [
			{"edit_id": "cv-001", "check": check, "op": "reword",
				"target": {"tool_id": "cask:spotify", "kind": "proposal",
					"id": self.a["id"], "field": "method_topic"},
				"precondition": {"before": self.a["method_topic"]}, "after": general_topic,
				"reason": {"headline": "Say the cross-tool pattern, not one app."}},
			{"edit_id": "cv-002", "check": check, "op": "reword",
				"target": {"tool_id": "cask:spotify", "kind": "proposal",
					"id": self.a["id"], "field": "method_note"},
				"precondition": {"before": self.a["method_note"]}, "after": general_note,
				"reason": {"headline": "Say the cross-tool pattern, not one app."}},
			{"edit_id": "cv-003", "check": "C6-memory", "op": "delete",
				"target": {"tool_id": "cask:obsidian", "kind": "proposal",
					"id": self.b["id"], "field": None},
				"quote": "Caskroom version lags",
				"bucket_claim": lateral("attention"),
				"reason": cut_reason("Covered by the promoted global note.")},
		]
		ledger = _auto_ledger(self.pre)
		ledger["method_notes_tool"] = {"kept": [], "cut": [
			{"suggestion_id": self.b["id"], "reason": "the global note says it"}],
			"promoted_to_global": [
			{"suggestion_id": self.a["id"], "reason": "two tools, one pattern"}]}
		return make_submission(self.pre, edits, ledger=ledger), general_topic, general_note

	def test_promote_reword_and_cut_converges_with_the_general_wording(self):
		submission, topic, text = self.submission()
		result = run(self.pre, submission)
		self.assertEqual(result["state"], "converged", codes_of(result))
		post = {v["id"]: v for v in result["corpus_post"]["tools"]}
		promoted = next(s for s in post["cask:spotify"]["suggestions"] if s["id"] == self.a["id"])
		self.assertEqual((promoted["method_topic"], promoted["method_note"]), (topic, text))
		self.assertFalse(any(s["id"] == self.b["id"] for s in post["cask:obsidian"]["suggestions"]))

	def test_the_reword_is_c6s_own_op(self):
		self.assertIn("reword", dict(C.CHECKS)["C6-memory"]["ops"])
		# filed under a check that does not own memory, it is still refused
		submission, _, _ = self.submission(check="C4-notable-security")
		self.assertIn("E-CHECK-OP", codes_of(run(self.pre, submission)))


class ApplyAndScopeTests(unittest.TestCase):
	def setUp(self):
		self.item = make_item("brew:t", 1, body="The body carries the detail "
			"the projection withheld.", local=plain_local(
			statement="A statement long enough to quote from."))
		self.other = make_item("brew:t", 2, severity="info")
		self.view = make_view("brew:t", [self.item, self.other])
		self.pre = build_pre([self.view])

	def merge_edit(self, changed_fields, mutate):
		after = copy.deepcopy(self.item)
		mutate(after)
		return {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "merge",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": self.item["id"], "field": None},
			"quote": "carries the detail",
			"after": after, "changed_fields": changed_fields,
			"bucket_claim": lateral("routine"), "reason": cut_reason()}

	def test_merge_scope_containment_is_not_a_tautology(self):
		"""An `after` that quietly also changed severity is caught here and
		nowhere else (§1.7 phase 4)."""
		def mutate(after):
			after["body"] = "New body."
			after["severity"] = "info"  # NOT declared
		result = run(self.pre, make_submission(self.pre,
			[self.merge_edit(["body"], mutate)]))
		self.assertIn("E-APPLY-SCOPE", codes_of(result))

	def test_merge_declared_and_derived_agree(self):
		def mutate(after):
			after["body"] = "New body."
		result = run(self.pre, make_submission(self.pre,
			[self.merge_edit(["body"], mutate)]))
		self.assertNotIn("E-APPLY-SCOPE", codes_of(result))
		post = result["corpus_post"]["tools"][0]
		merged = next(i for i in post["items"] if i["id"] == self.item["id"])
		self.assertEqual(merged["body"], "New body.")

	def test_merge_cannot_rewrite_id(self):
		def mutate(after):
			after["id"] = "brew:t#issue:renamed"
		result = run(self.pre, make_submission(self.pre,
			[self.merge_edit(["id"], mutate)]))
		self.assertIn("E-EDIT-OP", codes_of(result))

	def test_move_evidence_and_annotate_apply(self):
		item = make_item("brew:t", 3, local={"direction": "unclear",
			"effect": "none", "statement": "s",
			"evidence": ["prose that belongs in citations"], "citations": []})
		view = make_view("brew:t", [item])
		pre = build_pre([view])
		edits = [
			{"edit_id": "cv-001", "check": "C1-evidence", "op": "move_evidence",
				"target": {"tool_id": "brew:t", "kind": "item",
					"id": item["id"], "field": "local.evidence"},
				"precondition": {"before": "prose that belongs in citations"},
				"after": {"citation": {"kind": "observation",
					"text": "prose that belongs in citations"}},
				"reason": {"headline": "Filed as a citation."}},
			{"edit_id": "cv-002", "check": "C7-collisions", "op": "annotate",
				"target": {"tool_id": "brew:t", "kind": "item",
					"id": item["id"], "field": None},
				"precondition": {"before": 0},
				"after": "See also brew:other's identical change.",
				"reason": {"headline": "Cross-reference."}},
		]
		result = run(pre, make_submission(pre, edits))
		self.assertEqual(codes_of(result), [])
		post_item = result["corpus_post"]["tools"][0]["items"][0]
		self.assertEqual(post_item["local"]["evidence"], [])
		self.assertEqual(post_item["local"]["citations"][0]["text"],
			"prose that belongs in citations")
		self.assertEqual(post_item["convergence_notes"][0]["edit_id"], "cv-002")

	def test_annotate_tool_level(self):
		edit = {"edit_id": "cv-001", "check": "C7-collisions", "op": "annotate",
			"target": {"tool_id": "brew:t", "kind": "tool", "id": None,
				"field": None},
			"precondition": {"before": 0},
			"after": "The losing card's finding lives on brew:other now.",
			"reason": {"headline": "Cross-reference."}}
		result = run(self.pre, make_submission(self.pre, [edit]))
		self.assertEqual(codes_of(result), [])
		post_view = result["corpus_post"]["tools"][0]
		self.assertEqual(len(post_view["convergence_notes"]), 1)

	def test_diff_before_is_derived_not_declared(self):
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "rerate",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": self.other["id"], "field": "severity"},
			"precondition": {"before": "info"},
			"quote": self.other["title"],
			"after": "notable", "bucket_claim": lateral("routine"),
			"reason": cut_reason()}
		result = run(self.pre, make_submission(self.pre, [edit]))
		entry = next(d for d in result["diff"] if d["change"] == "field")
		self.assertEqual(entry["before"], "info")
		self.assertEqual(entry["after"], "notable")
		self.assertEqual(entry["edit_ids"], ["cv-001"])

	def test_schema_regression_is_caught(self):
		"""A merge that drops the security tag but keeps the block raises
		I-4 where corpus.pre did not — E-APPLY-SCHEMA."""
		security = {"cve_id": "CVE-2026-22222", "advisory_id": None,
			"rating": "high", "rating_basis": "nvd", "exploited_in_wild": False}
		item = make_item("brew:t", 4, tags=("security", "fix"),
			security=security, local=plain_local())
		view = make_view("brew:t", [item])
		pre = build_pre([view])
		after = copy.deepcopy(item)
		after["tags"] = ["fix"]
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "merge",
			"target": {"tool_id": "brew:t", "kind": "item", "id": item["id"],
				"field": None},
			"quote": item["title"], "after": after, "changed_fields": ["tags"],
			"bucket_claim": lateral("security_mixed"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]))
		self.assertIn("E-APPLY-SCHEMA", codes_of(result))

	def test_retagging_a_chore_item_security_bounces_without_a_block(self):
		"""convergence.md §5 C2 tells the agent a credential-handling change
		filed as `chore` can only be flagged: a `retag` adding `security`
		leaves the block I-4 requires missing, and the submission bounces."""
		item = make_item("brew:t", 5, tags=("chore",), local=plain_local())
		pre = build_pre([make_view("brew:t", [item])])
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "retag",
			"target": {"tool_id": "brew:t", "kind": "item", "id": item["id"],
				"field": "tags"},
			"precondition": {"before": ["chore"]},
			"quote": item["title"], "after": ["security", "chore"],
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]))
		self.assertIn("E-APPLY-SCHEMA", codes_of(result))

	def test_added_proposal_missing_payload_is_schema_failure(self):
		add = {"edit_id": "cv-001", "check": "C6-memory", "op": "add",
			"target": {"tool_id": "brew:t", "kind": "suggestion", "id": None,
				"field": None},
			"after": {"id": "brew:t:new-note", "kind": "method-note",
				"title": "no payload"},
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		result = run(self.pre, make_submission(self.pre, [add]))
		self.assertIn("E-APPLY-SCHEMA", codes_of(result))


# ═════════════════════════════════════════════════════════════════════════════
def _auto_tool():
	"""A tool one deletion away from security_auto: two security fixes that
	do not reach, plus one feature item blocking security_only."""
	security = {"cve_id": None, "advisory_id": None, "rating": "unknown",
		"rating_basis": "unrated", "exploited_in_wild": False}
	fixes = [make_item("brew:auto", n, tags=("security", "fix"),
		severity="info", security=copy.deepcopy(security),
		local=plain_local(direction="does_not_reach", effect="none"))
		for n in (1, 2)]
	blocker = make_item("brew:auto", 3, tags=("feature",), severity="notable",
		local=plain_local(statement="The feature statement to quote."))
	return make_view("brew:auto", fixes + [blocker]), blocker


def _gate_delete(blocker, declared=True, reasoned=True):
	body = ("Removing it makes the tool security-only, which moves it into "
		"security_auto and pre-accepts the upgrade; the counterweight is on "
		"the card.") if reasoned else \
		("A feature note is detail, not a decision input, and nobody would "
		"decide differently believing the opposite of it.")
	return {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "delete",
		"target": {"tool_id": "brew:auto", "kind": "item",
			"id": blocker["id"], "field": None},
		"quote": "The feature statement to quote.",
		"bucket_claim": {"moves_bucket": declared,
			"expected_from": "security_mixed", "expected_to": "security_auto",
			"direction": "permissive" if declared else "lateral"},
		"reason": cut_reason(body=body)}


class GateAndAttributionTests(unittest.TestCase):
	def test_declared_reasoned_move_passes_and_labels(self):
		view, blocker = _auto_tool()
		self.assertEqual(view["initial_review_bucket"], "security_mixed")
		pre = build_pre([view])
		result = run(pre, make_submission(pre, [_gate_delete(blocker)]))
		self.assertEqual(codes_of(result), [])
		self.assertEqual(result["moved"]["brew:auto"]["direction"], "permissive")
		attribution = result["attribution"]["brew:auto"]
		self.assertEqual(attribution["initial_review_bucket"]["attributed_to"],
			["cv-001"])
		effect = apply_converge.finalize_clean(pre,
			make_submission(pre, [_gate_delete(blocker)]), result, 1, [])
		label = effect["tools"]["brew:auto"]["auto_update_label"]
		self.assertEqual(label["source"], "judgement")

	def test_gate_undeclared(self):
		view, blocker = _auto_tool()
		pre = build_pre([view])
		result = run(pre, make_submission(pre,
			[_gate_delete(blocker, declared=False)]))
		self.assertIn("E-GATE-UNDECLARED", codes_of(result))

	def test_gate_unreasoned(self):
		view, blocker = _auto_tool()
		pre = build_pre([view])
		result = run(pre, make_submission(pre,
			[_gate_delete(blocker, reasoned=False)]))
		self.assertIn("E-GATE-UNREASONED", codes_of(result))

	def test_gate_unattributed_fails_closed(self):
		"""The defensive branch: if attribution cannot explain a permissive
		move, the gate fires rather than trusting it."""
		view, blocker = _auto_tool()
		pre = build_pre([view])
		empty = {"initial_review_bucket": {"attributed_to": [], "joint": False,
			"unattributed": True}}
		with mock.patch.object(apply_converge, "_attribute",
				return_value=empty):
			result = run(pre, make_submission(pre, [_gate_delete(blocker)]))
		self.assertIn("E-GATE-UNATTRIBUTED", codes_of(result))

	def test_restrictive_move_is_reported_never_blocked(self):
		item = make_item("brew:t", 1, local=plain_local(
			statement="A statement long enough to quote from."))
		view = make_view("brew:t", [item])
		pre = build_pre([view])
		self.assertEqual(view["initial_review_bucket"], "routine")
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "rerate",
			"target": {"tool_id": "brew:t", "kind": "item", "id": item["id"],
				"field": "severity"},
			"precondition": {"before": "notable"},
			"quote": "A statement long enough to quote from.",
			"after": "warning",
			"bucket_claim": {"moves_bucket": True, "expected_from": "routine",
				"expected_to": "attention", "direction": "restrictive"},
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]))
		self.assertEqual(codes_of(result), [])
		self.assertEqual(result["moved"]["brew:t"]["direction"], "restrictive")

	def test_joint_attribution_when_no_single_omission_restores(self):
		"""Two rerates, either alone sufficient to elevate risk — leave-one-
		out finds no single cause and the subset search attributes both."""
		items = [make_item("brew:t", n, local=plain_local(
			statement="Statement {} to quote.".format(n))) for n in (1, 2)]
		view = make_view("brew:t", items)
		pre = build_pre([view])
		edits = []
		for n, item in enumerate(items, start=1):
			edits.append({"edit_id": "cv-00{}".format(n),
				"check": "C2-tags-visibility", "op": "rerate",
				"target": {"tool_id": "brew:t", "kind": "item",
					"id": item["id"], "field": "severity"},
				"precondition": {"before": "notable"},
				"quote": "Statement {} to quote.".format(n),
				"after": "warning",
				"bucket_claim": {"moves_bucket": True,
					"expected_from": "routine", "expected_to": "attention",
					"direction": "restrictive"},
				"reason": cut_reason()})
		result = run(pre, make_submission(pre, edits))
		self.assertEqual(codes_of(result), [])
		attribution = result["attribution"]["brew:t"]["risk_level"]
		self.assertTrue(attribution["joint"])
		self.assertEqual(attribution["attributed_to"], ["cv-001", "cv-002"])

	def test_internal_selfcheck_detects_derivation_drift(self):
		"""A corpus whose recorded axes disagree with the one implementation
		is an applier-side bug — E-APPLY-INTERNAL, never a silent re-grade."""
		pre = fixture_pre()
		view = next(v for v in pre["tools"] if v["id"] == "brew:watched")
		view["initial_review_bucket"] = "attention"  # falsified record
		submission = make_submission(pre, [])
		result = run(pre, submission)
		self.assertIn("E-APPLY-INTERNAL", codes_of(result))

	def test_validator_error_tool_keeps_recorded_axes(self):
		pre = fixture_pre()
		view = next(v for v in pre["tools"] if v["id"] == "brew:watched")
		view["validator_error"] = "stage crashed"
		view["initial_review_bucket"] = "attention"
		result = run(pre, make_submission(pre, []))
		self.assertNotIn("E-APPLY-INTERNAL", codes_of(result))
		post = next(v for v in result["corpus_post"]["tools"]
			if v["id"] == "brew:watched")
		self.assertEqual(post["initial_review_bucket"], "attention")

	def test_validator_error_exports_are_rederived_after_edits(self):
		"""Review finding 3 — the axes stay recorded, but the id-list
		exports are pure functions of the items and must not hand the
		renderer an id a delete removed."""
		security = {"cve_id": "CVE-2026-33333", "advisory_id": None,
			"rating": "high", "rating_basis": "nvd",
			"exploited_in_wild": False}
		reaching = make_item("brew:v", 1, tags=("security", "fix"),
			security=security,
			local=plain_local(direction="reaches", effect="benefit",
				statement="Reaches this setup for the quote."))
		reaching["local"]["evidence"] = [{"path": "Brewfile"}]
		other = make_item("brew:v", 2, local=plain_local())
		view = make_view("brew:v", [reaching, other])
		view["validator_error"] = "stage crashed"
		pre = build_pre([view])
		self.assertIn(reaching["id"], view["security_display_item_ids"])
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "delete",
			"target": {"tool_id": "brew:v", "kind": "item",
				"id": reaching["id"], "field": None},
			"quote": "Reaches this setup for the quote.",
			"bucket_claim": lateral(view["initial_review_bucket"]),
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]))
		self.assertEqual(codes_of(result), [])
		post = result["corpus_post"]["tools"][0]
		self.assertEqual(post["security_display_item_ids"], [])
		self.assertNotIn("reaches-item", post["pre_accept_bars"])
		# the conservative axes are still the recorded ones
		self.assertEqual(post["initial_review_bucket"],
			view["initial_review_bucket"])
		self.assertEqual(post["risk_level"], view["risk_level"])

	def test_absent_store_is_a_note_but_empty_store_is_not(self):
		"""Coordinator item 6 — absent and present-but-empty are different
		facts, the same distinction watch-hit grounding draws between
		"never checked" and "checked, no match"."""
		pre = build_pre([make_view("brew:t", [make_item("brew:t", 9)])])
		result = run(pre, make_submission(pre, []))
		stores_flagged = sorted(f["detail"].split(" store", 1)[0]
			for f in result["notes"] if f["code"] == "W-STORE-UNCHECKED")
		self.assertEqual(stores_flagged,
			["the method-notes", "the watch-items"])
		self.assertEqual(result["state"], "converged")  # a note never bounces
		present = build_pre([make_view("brew:t", [make_item("brew:t", 9)])],
			watch_store={})
		present["stores"]["method_notes"] = {}
		result = run(present, make_submission(present, []))
		self.assertEqual([f for f in result["notes"]
			if f["code"] == "W-STORE-UNCHECKED"], [])
		self.assertEqual(C.build_tables(pre)["store_state"],
			{"watch_items": "absent", "method_notes": "absent"})
		self.assertEqual(C.build_tables(present)["store_state"],
			{"watch_items": "present", "method_notes": "present"})

	def test_unreadable_store_is_diagnosed_as_unreadable(self):
		"""Review round 2, finding 2 — a snapshot the operator DID copy but
		the applier cannot read must not be diagnosed as never-copied: the
		remedies are opposites."""
		pre = build_pre([make_view("brew:t", [make_item("brew:t", 9)])],
			watch_store={})
		pre["stores"]["method_notes"] = {
			C.STORE_UNREADABLE_KEY: "JSONDecodeError: bad"}
		result = run(pre, make_submission(pre, []))
		self.assertEqual(result["state"], "converged")  # still only a note
		notes = [f["detail"] for f in result["notes"]
			if f["code"] == "W-STORE-UNCHECKED"]
		self.assertEqual(len(notes), 1)
		self.assertIn("cannot be read", notes[0])
		self.assertIn("JSONDecodeError: bad", notes[0])
		self.assertNotIn("never snapshotted", notes[0])
		self.assertEqual(C.build_tables(pre)["store_state"],
			{"watch_items": "present", "method_notes": "unreadable"})
		# an unreadable watch store grounds nothing — same as the
		# validator's own E-RESEARCH-UNREADABLE-then-None
		self.assertIsNone(C.watch_topics_for(
			{"stores": {"watch_items": {C.STORE_UNREADABLE_KEY: "x"}}},
			"brew:t"))

	def test_a_store_file_carrying_the_sentinel_key_is_not_a_store(self):
		"""The sentinel rides INSIDE the store value, so a file whose real
		top-level key is that string used to pass through as `present` data
		that `store_status` then read as unreadable — quoting the file's own
		value as the reason. The loader refuses it with its own reason."""
		with tempfile.TemporaryDirectory() as td:
			path = os.path.join(td, "method-notes.json")
			with open(path, "w", encoding="utf-8") as fh:
				json.dump({C.STORE_UNREADABLE_KEY: "a value the file chose",
					"brew:t": [{"topic": "t", "note": "n"}]}, fh)
			loaded = apply_converge._load_store(path)
			self.assertEqual(list(loaded), [C.STORE_UNREADABLE_KEY])
			self.assertIn("no store holds", loaded[C.STORE_UNREADABLE_KEY])
			self.assertNotIn("a value the file chose", loaded[C.STORE_UNREADABLE_KEY])
			self.assertEqual(C.store_status({"method_notes": loaded}, "method_notes"),
				"unreadable")
			# a real store passes through untouched
			store = {"brew:t": [{"topic": "t", "note": "n"}], "global": []}
			with open(path, "w", encoding="utf-8") as fh:
				json.dump(store, fh)
			self.assertEqual(apply_converge._load_store(path), store)

	def test_nonexistent_store_is_not_a_never_copied_store(self):
		"""Pass 6: the method-notes store did not exist yet, step 3 copied
		nothing (as SKILL.md says it should), and C6 still reported "never
		snapshotted into this session". A store that does not exist is the
		fourth state and says nothing; absent keeps its note."""
		pre = build_pre([make_view("brew:t", [make_item("brew:t", 9)])],
			watch_store={})
		pre["stores"]["method_notes"] = {C.STORE_NONEXISTENT_KEY: True}
		result = run(pre, make_submission(pre, []))
		self.assertEqual([f for f in result["notes"]
			if f["code"] == "W-STORE-UNCHECKED"], [])
		self.assertEqual(C.build_tables(pre)["store_state"],
			{"watch_items": "present", "method_notes": "nonexistent"})
		# it grounds nothing, like the validator's missing snapshot
		self.assertIsNone(C.store_entries(pre["stores"], "method_notes"))
		self.assertIsNone(C.watch_topics_for(
			{"stores": {"watch_items": {C.STORE_NONEXISTENT_KEY: True}}}, "brew:t"))

	def test_a_store_file_carrying_the_nonexistent_sentinel_is_not_a_store(self):
		with tempfile.TemporaryDirectory() as td:
			path = os.path.join(td, "method-notes.json")
			with open(path, "w", encoding="utf-8") as fh:
				json.dump({C.STORE_NONEXISTENT_KEY: True}, fh)
			loaded = apply_converge._load_store(path)
			self.assertEqual(C.store_status({"method_notes": loaded}, "method_notes"),
				"unreadable")
			self.assertIn(C.STORE_NONEXISTENT_KEY, loaded[C.STORE_UNREADABLE_KEY])

	def test_prepare_tells_a_missing_store_from_an_uncopied_one(self):
		"""--prepare reads only whether the LIVE store exists: none at
		XDG_STATE_HOME → nonexistent; one there but no session copy → absent
		(C6's note, whose remedy is to copy it). The session's own copy wins
		whatever the live state."""
		session_src, roots, _ = validate_items.fixture_session()
		for live, want in ((False, "nonexistent"), (True, "absent")):
			with tempfile.TemporaryDirectory() as td:
				state = os.path.join(td, "state")
				os.makedirs(os.path.join(state, "tool-update-review"))
				if live:
					with open(os.path.join(state, "tool-update-review",
							"method-notes.json"), "w", encoding="utf-8") as fh:
						fh.write("{}")
				session = os.path.join(td, "session")
				shutil.copytree(session_src, session)
				argv = ["--session", session, "--prepare",
					"--macos-setup-root", roots[0], "--dotfiles-root", roots[1],
					"--systems-root", roots[2]]
				with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}), \
						mock.patch("sys.stdout"), mock.patch("sys.stderr"):
					self.assertEqual(apply_converge.main(argv), 0)
				with open(os.path.join(session, "converge-tables.json"),
						encoding="utf-8") as fh:
					tables = json.load(fh)
				self.assertEqual(tables["store_state"],
					{"watch_items": "present", "method_notes": want}, want)

	def test_fixture_session_flags_only_the_missing_method_store(self):
		"""The pinned session snapshots watch-items.json and nothing writes
		method-notes.json — the effect must say so, once."""
		result = run(FIXTURE_PRE, fixture_submission())
		flagged = [f["detail"] for f in result["notes"]
			if f["code"] == "W-STORE-UNCHECKED"]
		self.assertEqual(len(flagged), 1)
		self.assertIn("method-notes", flagged[0])


# ═════════════════════════════════════════════════════════════════════════════
class CheckVerifierTests(unittest.TestCase):
	"""Each verify_cN fires on the wrong attestation and stays silent on the
	right one. Deleting a verifier fails the firing test — that is the
	deletion detection the acceptance criteria demand."""

	def _submission(self):
		return fixture_submission()

	def _entry(self, submission, name):
		return next(e for e in submission["checks"] if e["check"] == name)

	def assert_fires(self, submission, code="E-CHECK-ARITH", fragment=None):
		result = run(FIXTURE_PRE, submission)
		self.assertIn(code, codes_of(result))
		if fragment:
			details = " | ".join(f["detail"] for f in result["critical"]
				if f["code"] == code)
			self.assertIn(fragment, details)

	def test_fixture_attestations_all_pass(self):
		result = run(FIXTURE_PRE, self._submission())
		for code in ("E-CHECK-ARITH", "E-CHECK-CLOSURE", "E-CHECK-OP"):
			self.assertNotIn(code, codes_of(result))

	def test_c1_scanned_and_findings(self):
		submission = self._submission()
		self._entry(submission, "C1-evidence")["scanned"]["items"] = 23
		self.assert_fires(submission, fragment="C1 scanned.items")
		submission = self._submission()
		self._entry(submission, "C1-evidence")["findings"] = 4
		self.assert_fires(submission, fragment="C1 findings")

	def test_c2_coverage(self):
		submission = self._submission()
		self._entry(submission, "C2-tags-visibility")["clean"] = 16
		self.assert_fires(submission, fragment="C2 clean")

	def test_c3_enumeration_and_counts(self):
		submission = self._submission()
		self._entry(submission, "C3-security-only")["security_only_tools"] \
			.pop()  # drops brew:quarantined
		self.assert_fires(submission, fragment="C3 must enumerate")
		submission = self._submission()
		self._entry(submission, "C3-security-only")["security_only_tools"] \
			[0]["cve_count"] = 7
		self.assert_fires(submission, fragment="cve_count")

	def test_c4_populations(self):
		submission = self._submission()
		self._entry(submission, "C4-notable-security")["scanned"] \
			["rating_unrated"] = 0
		self.assert_fires(submission, fragment="C4 scanned.rating_unrated")

	def test_c5_enumeration_and_verdicts(self):
		submission = self._submission()
		self._entry(submission, "C5-auto-approval")["tools"] = []
		self.assert_fires(submission, fragment="C5 must enumerate")
		submission = self._submission()
		self._entry(submission, "C5-auto-approval")["tools"][0]["verdict"] = " "
		self.assert_fires(submission, fragment="C5 brew:openssh")

	def test_c6_dispositions(self):
		submission = self._submission()
		submission["ledger"]["watch_items"]["rehomed_to_method_note"] = []
		self.assert_fires(submission, fragment="exactly ONE disposition")
		submission = self._submission()
		submission["ledger"]["watch_items"]["existing"][0]["fired_this_run"] \
			= False
		self.assert_fires(submission, fragment="fired_this_run")
		submission = self._submission()
		del submission["ledger"]["watch_items"]["existing"][1]["used_correctly"]
		self.assert_fires(submission, fragment="used_correctly")
		submission = self._submission()
		del submission["ledger"]["method_notes_tool"]["kept"][1]["restored"]
		self.assert_fires(submission, fragment="restored")
		submission = self._submission()
		submission["ledger"]["watch_items"]["existing"] = \
			submission["ledger"]["watch_items"]["existing"][:1]
		self.assert_fires(submission, fragment="no `existing` row")

	def test_c7_clusters_and_survivors(self):
		submission = self._submission()
		self._entry(submission, "C7-collisions")["clusters_inspected"] = 3
		self.assert_fires(submission, fragment="C7 clusters_inspected")
		submission = self._submission()
		self._entry(submission, "C7-collisions")["resolved"] = [
			{"surviving_suggestion_id": "brew:nonconforming:watch-no-reason"}]
		self.assert_fires(submission, fragment="does not survive")

	def test_closure_missing_and_mismatched(self):
		submission = self._submission()
		submission["checks"] = [e for e in submission["checks"]
			if e["check"] != "C4-notable-security"]
		self.assert_fires(submission, code="E-CHECK-CLOSURE",
			fragment="C4-notable-security has no attestation")
		submission = self._submission()
		self._entry(submission, "C1-evidence")["edits"] = ["cv-001"]
		self.assert_fires(submission, code="E-CHECK-CLOSURE",
			fragment="C1-evidence.edits")

	def test_op_discipline(self):
		submission = self._submission()
		delete = next(e for e in submission["edits"]
			if e["edit_id"] == "cv-003")
		delete["check"] = "C1-evidence"
		# keep closure consistent so ONLY the op rule fires
		self._entry(submission, "C1-evidence")["edits"].append("cv-003")
		self._entry(submission, "C2-tags-visibility")["edits"].remove("cv-003")
		self.assert_fires(submission, code="E-CHECK-OP",
			fragment="C1-evidence emits")


class CorpusEffectTests(unittest.TestCase):
	def test_safety_field_mismatch_is_critical(self):
		submission = fixture_submission()
		submission["corpus_effect"]["pre_accept"] = {"before": 1, "after": 2}
		result = run(FIXTURE_PRE, submission)
		self.assertIn("E-EFFECT-ARITH", codes_of(result))
		self.assertEqual(result["state"], "rejected")

	def test_other_field_mismatch_is_a_note(self):
		submission = fixture_submission()
		submission["corpus_effect"]["edits_by_op"]["trim"] = 9
		result = run(FIXTURE_PRE, submission)
		self.assertEqual(result["state"], "converged")
		self.assertIn("E-EFFECT-ARITH",
			sorted({f["code"] for f in result["notes"]}))

	def test_narrative_mandatory(self):
		submission = fixture_submission()
		del submission["corpus_effect"]["narrative"]
		result = run(FIXTURE_PRE, submission)
		self.assertIn("E-EFFECT-NARRATIVE", codes_of(result))

	def test_narrative_enumeration_past_threshold(self):
		"""A -20%+ warning-or-worse shrink must name every affected tool —
		a reporting threshold, never a cap: the cut itself is not blocked."""
		items = [make_item("brew:t", n, severity="warning",
			local=plain_local(statement="Statement {} to quote.".format(n),
				direction="unclear"))
			for n in (1, 2)]
		view = make_view("brew:t", items)
		pre = build_pre([view])
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "rerate",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": items[0]["id"], "field": "severity"},
			"precondition": {"before": "warning"},
			"quote": "Statement 1 to quote.",
			"after": "info", "bucket_claim": lateral("attention"),
			"reason": cut_reason()}
		submission = make_submission(pre, [edit])
		submission["corpus_effect"]["narrative"] = "Something fell."
		result = run(pre, submission)
		self.assertIn("E-EFFECT-NARRATIVE", codes_of(result))
		submission["corpus_effect"]["narrative"] = \
			"brew:t lost one warning item to a re-rate."
		result = run(pre, submission)
		self.assertNotIn("E-EFFECT-NARRATIVE", codes_of(result))

	def test_shipped_numbers_are_the_appliers(self):
		result = run(FIXTURE_PRE, fixture_submission())
		effect = apply_converge.finalize_clean(fixture_pre(),
			fixture_submission(), result, 1, [])
		recomputed = apply_converge.compute_corpus_effect(FIXTURE_PRE,
			result["corpus_post"],
			[e for e in fixture_submission()["edits"]
				if e["edit_id"] in result["applied"]],
			result["moved"])
		for field in C.EFFECT_FIELDS:
			self.assertEqual(effect["corpus_effect"][field], recomputed[field])


class LabelContractTests(unittest.TestCase):
	def test_rule_label_for_untouched_auto_tool(self):
		"""convergence.md §9 — a tool auto by rule alone still gets a label, so the reader
		can tell which kind it is looking at."""
		security = {"cve_id": None, "advisory_id": None, "rating": "unknown",
			"rating_basis": "unrated", "exploited_in_wild": False}
		fixes = [make_item("brew:pure", n, tags=("security", "fix"),
			severity="info", security=copy.deepcopy(security),
			local=plain_local(direction="does_not_reach"))
			for n in (1, 2)]
		view = make_view("brew:pure", fixes)
		self.assertEqual(view["initial_review_bucket"], "security_auto")
		pre = build_pre([view])
		submission = make_submission(pre, [])
		result = run(pre, submission)
		self.assertEqual(codes_of(result), [])
		effect = apply_converge.finalize_clean(pre, submission, result, 1, [])
		label = effect["tools"]["brew:pure"]["auto_update_label"]
		self.assertEqual(label["source"], "rule")
		self.assertTrue(label["headline"])
		self.assertTrue(label["reasoning"])
		self.assertEqual(label["edit_ids"], [])

	def test_absent_not_null_for_non_auto_tools(self):
		result = run(FIXTURE_PRE, fixture_submission())
		effect = apply_converge.finalize_clean(fixture_pre(),
			fixture_submission(), result, 1, [])
		watched = effect["tools"]["brew:watched"]
		self.assertNotIn("auto_update_label", watched)


# ═════════════════════════════════════════════════════════════════════════════
class TerminalDegradationTests(unittest.TestCase):
	"""Degrading after five attempts — the pure function's terminal behavior. The CLI
	loop around it is LoopCliTests."""

	def test_degraded_gate_forces_conservative(self):
		view, blocker = _auto_tool()
		pre = build_pre([view])
		submission = make_submission(pre,
			[_gate_delete(blocker, reasoned=False)])
		result = run(pre, submission, terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_gate")
		post = next(v for v in result["corpus_post"]["tools"]
			if v["id"] == "brew:auto")
		self.assertEqual(post["initial_review_bucket"], "security_mixed")
		self.assertFalse(post["initial_pre_accept"])
		self.assertEqual(post["forced_conservative"]["code"],
			"E-GATE-UNREASONED")
		status = result["effect"]["convergence_status"]
		self.assertEqual(status["state"], "degraded_gate")
		self.assertEqual(status["degraded_tools"][0]["tool_id"], "brew:auto")
		self.assertEqual(status["degraded_tools"][0]["would_have_been"],
			{"bucket": "security_auto", "pre_accept": True, "priority": None})
		self.assertEqual(status["degraded_tools"][0]["kind"], "permissive")
		explanation = status["explanation"]
		self.assertIn("1 tool(s) reached auto-update without surviving the gate",
			explanation["headline"])
		self.assertIn("forced to security_mixed", explanation["headline"])
		self.assertNotIn("priority", explanation["headline"])
		self.assertIn("kept out of the auto strip", explanation["body"])
		# a degraded tool carries NO auto_update_label (convergence.md §9)
		self.assertNotIn("auto_update_label",
			result["effect"]["tools"]["brew:auto"])

	def test_degraded_unapplied_ships_pre_verbatim(self):
		result = run(FIXTURE_PRE, {"nonsense": True}, terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_unapplied")
		self.assertEqual(result["corpus_post"], FIXTURE_PRE)
		status = result["effect"]["convergence_status"]
		self.assertEqual(status["state"], "degraded_unapplied")
		self.assertTrue(status["explanation"]["headline"])
		self.assertTrue(status["standing_rejects"])

	def test_clean_zero_edit_submission_converges_at_terminal(self):
		"""A run that legitimately proposes no edits has converged — routing
		it to degraded_unapplied with an empty code list would discredit the
		degradation channel (review finding 5)."""
		pre = fixture_pre()
		result = run(pre, make_submission(pre, []), terminal=True, attempt=5)
		self.assertEqual(result["state"], "converged")
		status = result["effect"]["convergence_status"]
		self.assertEqual(status["state"], "converged")
		self.assertEqual(status["explanation"]["headline"],
			"Converged at attempt 5.")
		self.assertEqual(status["standing_rejects"], [])

	def test_terminal_excludes_implicated_edits_and_ships_the_rest(self):
		"""At attempt 5 a scope-violating merge is excluded — reported, not
		silently skipped — and the sound edits still apply."""
		item = make_item("brew:t", 1, body="The body to quote from at length.",
			local=plain_local(statement="A statement long enough to quote."))
		other = make_item("brew:t", 2)
		view = make_view("brew:t", [item, other])
		pre = build_pre([view])
		after = copy.deepcopy(item)
		after["body"] = "New body."
		after["severity"] = "info"  # undeclared
		bad = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "merge",
			"target": {"tool_id": "brew:t", "kind": "item", "id": item["id"],
				"field": None},
			"quote": "body to quote", "after": after, "changed_fields": ["body"],
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		good = {"edit_id": "cv-002", "check": "C2-tags-visibility",
			"op": "rerate",
			"target": {"tool_id": "brew:t", "kind": "item", "id": other["id"],
				"field": "severity"},
			"precondition": {"before": "notable"},
			"quote": other["title"], "after": "info",
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		submission = make_submission(pre, [bad, good])
		result = run(pre, submission, terminal=True, attempt=5)
		self.assertEqual(result["state"], "converged")
		self.assertEqual(result["applied"], ["cv-002"])
		self.assertEqual(result["rejected"][0]["edit_id"], "cv-001")
		status = result["effect"]["convergence_status"]
		self.assertEqual(status["standing_rejects"][0]["edit_id"], "cv-001")
		post = result["corpus_post"]["tools"][0]
		merged = next(i for i in post["items"] if i["id"] == item["id"])
		self.assertNotIn("body", set(merged) - set(item))  # bad edit not applied
		self.assertEqual(merged.get("severity"), "notable")


class TerminalStateMatrixTests(unittest.TestCase):
	"""The pinned population matrix for attempt 5 (convergence.md §6). The review round
	proved reasoning about one collection from a single example is how a
	degradation path regresses: `critical` and `rejected` are NOT the same
	set — a precheck rejection carries a finding, an edit the terminal loop
	excluded for E-APPLY-SCOPE/SCHEMA lives only in `rejected`. Every row
	here is asserted through the pure function.

	| applied | rejected | critical | gate | state |
	|---|---|---|---|---|
	| []  | []  | []  | -   | converged            |
	| []  | ≠[] | []  | -   | degraded_unapplied   |
	| []  | ≠[] | ≠[] | -   | degraded_unapplied   |
	| ≠[] | ≠[] | any | no  | converged (standing) |
	| ≠[] | any | any | yes | degraded_gate        |
	"""

	def _scope_violating_merge(self, pre, item):
		after = copy.deepcopy(item)
		after["body"] = "New body."
		after["severity"] = "info"  # undeclared → E-APPLY-SCOPE → excluded
		return {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "merge",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": item["id"], "field": None},
			"quote": "body to quote", "after": after,
			"changed_fields": ["body"],
			"bucket_claim": lateral("routine"), "reason": cut_reason()}

	def test_all_empty_converges(self):
		pre = fixture_pre()
		result = run(pre, make_submission(pre, []), terminal=True, attempt=5)
		self.assertEqual(result["state"], "converged")

	def test_excluded_only_degrades_unapplied(self):
		"""THE regression (review round 2, finding 1): nothing survived and
		the only record is in `rejected` — the state must be
		degraded_unapplied, never a convergence report."""
		item = make_item("brew:t", 1, body="The body to quote from at length.",
			local=plain_local())
		pre = build_pre([make_view("brew:t", [item])])
		submission = make_submission(pre,
			[self._scope_violating_merge(pre, item)])
		result = run(pre, submission, terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_unapplied")
		self.assertEqual(result["applied"], [])
		status = result["effect"]["convergence_status"]
		self.assertEqual(status["state"], "degraded_unapplied")
		self.assertEqual(
			[(r["edit_id"], r["code"]) for r in status["standing_rejects"]],
			[("cv-001", "E-APPLY-SCOPE")])
		self.assertIn("Nothing in the final submission could be applied",
			status["explanation"]["body"])
		self.assertEqual(result["corpus_post"], pre)

	def test_precheck_rejected_all_degrades_unapplied(self):
		item = make_item("brew:t", 1, local=plain_local(
			statement="A statement long enough to quote from."))
		pre = build_pre([make_view("brew:t", [item])])
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility",
			"op": "delete",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": "brew:t#issue:nothing", "field": None},
			"quote": "A statement long enough to quote from.",
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]), terminal=True,
			attempt=5)
		self.assertEqual(result["state"], "degraded_unapplied")
		# the record dedups: the precheck rejection appears once, not once
		# per population it lives in
		self.assertEqual(
			[r["edit_id"] for r in result["effect"]["convergence_status"]
				["standing_rejects"]],
			["cv-001"])

	def test_partial_apply_with_rejects_converges_standing(self):
		item = make_item("brew:t", 1, body="The body to quote from at length.",
			local=plain_local())
		other = make_item("brew:t", 2)
		pre = build_pre([make_view("brew:t", [item, other])])
		good = {"edit_id": "cv-002", "check": "C2-tags-visibility",
			"op": "rerate",
			"target": {"tool_id": "brew:t", "kind": "item",
				"id": other["id"], "field": "severity"},
			"precondition": {"before": "notable"},
			"quote": other["title"], "after": "info",
			"bucket_claim": lateral("routine"), "reason": cut_reason()}
		submission = make_submission(pre,
			[self._scope_violating_merge(pre, item), good])
		result = run(pre, submission, terminal=True, attempt=5)
		self.assertEqual(result["state"], "converged")
		self.assertEqual(result["applied"], ["cv-002"])
		self.assertTrue(result["effect"]["convergence_status"]
			["standing_rejects"])

	def test_gate_failure_degrades_gate(self):
		view, blocker = _auto_tool()
		pre = build_pre([view])
		result = run(pre, make_submission(pre,
			[_gate_delete(blocker, reasoned=False)]), terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_gate")


class LoopCliTests(unittest.TestCase):
	"""The durable counter, the artefacts, and the refusals — through main()."""

	def setUp(self):
		self.tmp = tempfile.mkdtemp(prefix="converge-test-")
		self.addCleanup(shutil.rmtree, self.tmp, True)
		self.session = os.path.join(self.tmp, "session")
		os.makedirs(self.session)
		self.pre = fixture_pre()
		self._write(os.path.join(self.session, "corpus.pre.json"), self.pre)

	def _write(self, path, document):
		with open(path, "w", encoding="utf-8") as fh:
			json.dump(document, fh, ensure_ascii=False)

	def _read(self, name):
		with open(os.path.join(self.session, name), encoding="utf-8") as fh:
			return json.load(fh)

	def _draft(self, submission):
		path = os.path.join(self.tmp, "converge.draft.json")
		self._write(path, submission)
		return path

	def _main(self, *argv):
		stdout = mock.patch("sys.stdout")
		stderr = mock.patch("sys.stderr")
		with stdout, stderr:
			return apply_converge.main(["--session", self.session] + list(argv))

	def test_prepare_writes_three_artifacts_and_refuses_overwrite(self):
		session_src, roots, _ = validate_items.fixture_session()
		prepare_dir = os.path.join(self.tmp, "prepare-session")
		shutil.copytree(session_src, prepare_dir)
		for name in ("corpus.pre.json",):
			path = os.path.join(prepare_dir, name)
			if os.path.exists(path):
				os.remove(path)
		argv = ["--session", prepare_dir, "--prepare",
			"--macos-setup-root", roots[0], "--dotfiles-root", roots[1],
			"--systems-root", roots[2]]
		with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
			self.assertEqual(apply_converge.main(argv), 0)
			for name in ("corpus.pre.json", "converge-view.json",
					"converge-tables.json"):
				self.assertTrue(os.path.exists(os.path.join(prepare_dir, name)))
			# immutable for the run: a second prepare refuses without --force
			self.assertEqual(apply_converge.main(argv), 4)
			self.assertEqual(apply_converge.main(argv + ["--force"]), 0)

	def test_prepare_pins_an_unreadable_store_as_unreadable(self):
		"""The CLI half of the three-state distinction: a corrupt
		method-notes.json reaches corpus.pre as the sentinel, and the
		written tables say `unreadable`, not `absent`."""
		session_src, roots, _ = validate_items.fixture_session()
		prepare_dir = os.path.join(self.tmp, "prepare-unreadable")
		shutil.copytree(session_src, prepare_dir)
		with open(os.path.join(prepare_dir, "method-notes.json"), "w",
				encoding="utf-8") as fh:
			fh.write("{corrupt")
		argv = ["--session", prepare_dir, "--prepare",
			"--macos-setup-root", roots[0], "--dotfiles-root", roots[1],
			"--systems-root", roots[2]]
		with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
			self.assertEqual(apply_converge.main(argv), 0)
		with open(os.path.join(prepare_dir, "corpus.pre.json"),
				encoding="utf-8") as fh:
			corpus = json.load(fh)
		self.assertIn(C.STORE_UNREADABLE_KEY,
			corpus["stores"]["method_notes"])
		with open(os.path.join(prepare_dir, "converge-tables.json"),
				encoding="utf-8") as fh:
			tables = json.load(fh)
		self.assertEqual(tables["store_state"],
			{"watch_items": "present", "method_notes": "unreadable"})

	def test_check_writes_nothing_and_counts_no_attempt(self):
		submission = make_submission(self.pre, [])
		code = self._main("--check", self._draft(submission))
		self.assertEqual(code, 0)
		self.assertEqual(sorted(os.listdir(self.session)), ["corpus.pre.json"])

	def test_submit_bounce_then_converge(self):
		bad = make_submission(self.pre, [])
		bad["corpus_effect"]["pre_accept"] = {"before": 9, "after": 9}
		self.assertEqual(self._main("--submit", self._draft(bad)), 1)
		log = self._read("converge-attempts.json")
		self.assertEqual(len(log["attempts"]), 1)
		self.assertIn("E-EFFECT-ARITH", log["attempts"][0]["codes"])
		self.assertFalse(os.path.exists(
			os.path.join(self.session, "corpus.post.json")))
		good = make_submission(self.pre, [], attempt=2)
		self.assertEqual(self._main("--submit", self._draft(good)), 0)
		effect = self._read("converge-effect.json")
		self.assertEqual(effect["state"], "converged")
		self.assertEqual(effect["attempt"], 2)
		log_entries = effect["convergence_status"]["explanation"]["attempt_log"]
		self.assertEqual([e["attempt"] for e in log_entries], [1, 2])
		self.assertIn("E-EFFECT-ARITH", log_entries[0]["codes"])
		self.assertTrue(os.path.exists(
			os.path.join(self.session, "corpus.post.json")))
		self.assertTrue(os.path.exists(
			os.path.join(self.session, "converge.json")))
		# terminal: nothing is resubmittable
		self.assertEqual(self._main("--submit", self._draft(good)), 4)

	def test_five_attempts_then_degrade_unapplied(self):
		garbage = {"not": "a submission"}
		for attempt in range(1, 5):
			self.assertEqual(self._main("--submit", self._draft(garbage)), 1)
		self.assertEqual(self._main("--submit", self._draft(garbage)), 0)
		effect = self._read("converge-effect.json")
		self.assertEqual(effect["state"], "degraded_unapplied")
		self.assertEqual(effect["attempt"], 5)
		self.assertEqual(self._read("corpus.post.json"), self.pre)
		log = self._read("converge-attempts.json")
		self.assertEqual([e["attempt"] for e in log["attempts"]],
			[1, 2, 3, 4, 5])

	def test_attempt_disagreement_is_noted_not_fatal(self):
		submission = make_submission(self.pre, [], attempt=4)
		self.assertEqual(self._main("--submit", self._draft(submission)), 0)
		effect = self._read("converge-effect.json")
		self.assertIn("W-SUBMIT-ATTEMPT",
			{f["code"] for f in effect["findings"]})

	def test_non_object_draft_is_coded_on_both_subcommands(self):
		"""Review finding 1 — a draft that is valid JSON but not an object
		must reach E-SUBMIT-SHAPE on --submit exactly as on --check, with
		the attempt recorded and no traceback."""
		path = os.path.join(self.tmp, "array.json")
		self._write(path, ["not", "an", "object"])
		self.assertEqual(self._main("--check", path), 1)
		self.assertEqual(self._main("--submit", path), 1)
		log = self._read("converge-attempts.json")
		self.assertEqual(len(log["attempts"]), 1)
		self.assertIn("E-SUBMIT-SHAPE", log["attempts"][0]["codes"])
		self.assertFalse(os.path.exists(
			os.path.join(self.session, "corpus.post.json")))

	def test_broken_attempts_file_refuses_instead_of_resetting(self):
		"""The counter is the loop's enforcement — a corrupted file must
		not hand the run five fresh attempts."""
		attempts_path = os.path.join(self.session, "converge-attempts.json")
		with open(attempts_path, "w", encoding="utf-8") as fh:
			fh.write("{corrupt")
		submission = make_submission(self.pre, [])
		self.assertEqual(self._main("--submit", self._draft(submission)), 4)
		with open(attempts_path, encoding="utf-8") as fh:
			self.assertEqual(fh.read(), "{corrupt")  # untouched, not reset

	def test_unreadable_draft_is_a_coded_rejection(self):
		path = os.path.join(self.tmp, "broken.json")
		with open(path, "w", encoding="utf-8") as fh:
			fh.write("{not json")
		self.assertEqual(self._main("--check", path), 1)


class ContractSurfaceTests(unittest.TestCase):
	def test_every_code_documented(self):
		for code, (severity, phase, meaning) in C.CODES.items():
			self.assertIn(severity, ("critical", "note", "graded"))
			self.assertTrue(meaning)

	def test_bucket_capable_and_cut_sets(self):
		self.assertEqual(set(C.BUCKET_CAPABLE_OPS),
			{"delete", "merge", "retag", "rerate", "redirect", "add"})
		self.assertEqual(set(C.CUT_OPS),
			{"delete", "merge", "retag", "rerate", "redirect"})

	def test_check_ids_are_the_seven(self):
		self.assertEqual(len(C.CHECK_IDS), 7)
		self.assertEqual(C.CHECK_IDS[0], "C1-evidence")
		self.assertEqual(C.CHECK_IDS[-1], "C7-collisions")

	def test_flag_scope_is_empty(self):
		self.assertEqual(C.OPS["flag"]["scope"],
			"EMPTY — writes nothing to the corpus")


# ═════════════════════════════════════════════════════════════════════════════
# G-SEC — the tier survives convergence, and prominence cannot be lost silently
def _usage_entry(n):
	return {"path": "dotfiles/home/.conf{}".format(n), "role": "usage",
		"quote": "line {} that uses it".format(n)}


def _gfix(tool_id, n, usage=False, direction=None, severity="notable", statement=None):
	"""A positively identified fix; with `usage`, its local block carries a
	usage entry (grounded only if the view's record names it)."""
	# with `usage`, an install entry rides beside it, so moving the usage
	# entry out leaves a reaching claim that still has evidence (I-14)
	evidence = [_usage_entry(n), {"path": "Brewfile", "role": "install"}] if usage else []
	local = plain_local(direction=direction or ("reaches" if usage else "does_not_reach"),
		effect="benefit", statement=statement or "Fix {} statement to quote.".format(n))
	local["evidence"] = evidence
	return make_item(tool_id, n, tags=("security", "fix"), severity=severity,
		security={"cve_id": None, "advisory_id": None, "rating": "medium",
			"rating_basis": "nvd", "exploited_in_wild": False, "nature": "fix"},
		local=local)


def _gview(tool_id, items, suggestions=None, grounded=(), version_delta="patch"):
	view = make_view(tool_id, items, suggestions, version_delta=version_delta)
	view["usage_evidence"] = [{"entry": _usage_entry(n), "matched_lines": [1]}
		for n in grounded]
	C.derive_tool_state(view, None)
	return view


def _incompat(tool_id, n, statement="Incompatible statement to quote."):
	local = plain_local(direction="reaches", effect="risk", statement=statement)
	local["evidence"] = [{"path": "Brewfile"}]
	return make_item(tool_id, n, tags=("breaking",), severity="incompatible", local=local)


def _edit(tool_id, requirement, serves=(), sid=None, title="An edit title to quote."):
	sug = {"id": sid or tool_id + ":edit", "kind": "edit", "title": title,
		"target_files": [], "rationale": "r", "serves_item_ids": list(serves)}
	if requirement is not None:
		sug["requirement"] = requirement
	return sug


def _redirect(tool_id, item, after="unclear", body=None, edit_id="cv-001",
		bucket="security_auto"):
	return {"edit_id": edit_id, "check": "C2-tags-visibility", "op": "redirect",
		"target": {"tool_id": tool_id, "kind": "item", "id": item["id"],
			"field": "local.direction"},
		"precondition": {"before": item["local"]["direction"]},
		"quote": item["local"]["statement"], "after": after,
		"bucket_claim": lateral(bucket),
		"reason": cut_reason(body=body)}


def _move(tool_id, item, n, body=None, edit_id="cv-002"):
	reason = {"headline": "The quoted line is a setting, not a use."}
	if body:
		reason["body"] = body
	return {"edit_id": edit_id, "check": "C1-evidence", "op": "move_evidence",
		"target": {"tool_id": tool_id, "kind": "item", "id": item["id"],
			"field": "local.evidence"},
		"precondition": {"before": _usage_entry(n)},
		"after": {"citation": {"kind": "observation", "text": "a setting line"}},
		"reason": reason}


PRIORITY_BODY = ("The quoted line does not show the vulnerable path is used here; "
	"this lowers the fix's security priority out of the highlight panel.")


class GSecConvergenceTests(unittest.TestCase):
	def test_the_fixture_run_keeps_libpq_and_lowers_duckdb_with_its_reason(self):
		"""The hand-written submission's reasoned demotion (cv-014) and the
		carried record: libpq's usage confirmation survives convergence
		without re-grounding; duckdb's P2 → P3 is attributed and disclosed."""
		result = run(FIXTURE_PRE, fixture_submission())
		self.assertEqual(result["critical"], [])
		post = {v["id"]: v for v in result["corpus_post"]["tools"]}
		self.assertEqual(model.security_priority(post["brew:libpq"]), "P2")
		self.assertEqual(model.security_priority(post["brew:duckdb"]), "P3")
		self.assertEqual(result["prominence"], {"brew:duckdb":
			{"from": "P2", "to": "P3", "lost": True}})
		effect = model.load_fixture("expected_converge_effect.json")["effect"]
		block = effect["tools"]["brew:duckdb"]["security_priority"]
		self.assertEqual(block["attributed_to"], ["cv-014"])
		self.assertTrue(block["lost"])
		self.assertEqual(block["reasons_from"], ["relevant-fix", "fix"])
		self.assertEqual(block["reasons_to"], ["fix"])
		# MOVED_AXES unchanged: the demotion is not a bucket move
		self.assertNotIn("brew:duckdb", result["moved"])
		# the rule label names the tier for a G-SEC tool accepted by rule
		label = effect["tools"]["brew:libpq"]["auto_update_label"]
		self.assertEqual(label["source"], "rule")
		self.assertIn("security tier P2", label["reasoning"])

	def test_the_tier_and_bars_are_self_checked(self):
		view = _gview("brew:g", [_gfix("brew:g", 1)])
		for field, value in (("security_tier", copy.deepcopy(model.TIER_UNCOMPUTED)),
				("pre_accept_bars", ["watch-hit"])):
			with self.subTest(field):
				pre = build_pre([copy.deepcopy(view)])
				pre["tools"][0][field] = value
				result = run(pre, make_submission(pre, []))
				internal = [f for f in result["critical"] if f["code"] == "E-APPLY-INTERNAL"]
				self.assertTrue(any(field in f["detail"] for f in internal), result["critical"])

	def test_a_reasoned_redirect_demotion_is_recorded_and_attributed(self):
		item = _gfix("brew:g", 1, usage=True)
		view = _gview("brew:g", [item], grounded=[1])
		self.assertEqual(model.security_priority(view), "P2")
		pre = build_pre([view])
		submission = make_submission(pre, [_redirect("brew:g", item, body=PRIORITY_BODY)])
		result = run(pre, submission)
		self.assertEqual(codes_of(result), [])
		self.assertEqual(result["prominence"]["brew:g"],
			{"from": "P2", "to": "P3", "lost": True})
		self.assertEqual(result["moved"], {})
		self.assertEqual(result["attribution"]["brew:g"][C.PRIORITY_AXIS]["attributed_to"],
			["cv-001"])
		effect = apply_converge.finalize_clean(pre, submission, result, 1, [])
		self.assertEqual(effect["tools"]["brew:g"]["security_priority"]["attributed_to"],
			["cv-001"])

	def test_an_unreasoned_demotion_bounces_and_is_forced_at_attempt_five(self):
		item = _gfix("brew:g", 1, usage=True)
		view = _gview("brew:g", [item], grounded=[1])
		pre = build_pre([view])
		submission = make_submission(pre, [_redirect("brew:g", item)])
		result = run(pre, submission)
		self.assertIn("E-GATE-UNREASONED", codes_of(result))
		self.assertEqual({g["kind"] for g in result["gate"]}, {"demotion"})
		# at the terminal attempt: forced conservative, prominence kept
		result = run(pre, submission, terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_gate")
		post = result["corpus_post"]["tools"][0]
		forced = post["forced_conservative"]
		self.assertEqual(forced["kind"], "demotion")
		self.assertEqual(forced["would_have_been"]["priority"], "P3")
		self.assertEqual(forced["forced_display"]["priority"], "P2")
		self.assertEqual(forced["forced_display"]["reasons"], ["relevant-fix", "fix"])
		# explained as what happened — a lowered priority, not a failed move
		# to auto-update
		explanation = result["effect"]["convergence_status"]["explanation"]
		self.assertIn("had their security priority lowered without a reason that "
			"survived the gate", explanation["headline"])
		self.assertIn("keeps its prior priority", explanation["headline"])
		self.assertNotIn("auto-update", explanation["headline"])
		self.assertNotIn("auto strip", explanation["body"])
		self.assertIn("pre-convergence priority", explanation["body"])
		self.assertEqual(forced["forced_display"]["labels"]["relevant-fix"],
			model.TIER_LABELS["relevant-fix"]["text"])
		self.assertFalse(post["initial_pre_accept"])
		self.assertEqual(post["initial_review_bucket"], C.FORCED_BUCKET)
		# the stored tier stays the PURE recomputation, holding
		self.assertEqual(post["security_tier"], model.security_tier(post))
		self.assertIn("forced-conservative", post["security_tier"]["holds"])
		self.assertEqual(post["security_tier"]["tier"], "held")
		self.assertIn("forced-conservative", post["pre_accept_bars"])

	def test_forcing_never_loosens_a_tool_nor_files_it_as_security(self):
		"""Review A2: a non-security `attention` tool that gained
		pre-acceptance without surviving the gate goes back to `attention` —
		not to `security_mixed`, which is both looser and a security label."""
		item = make_item("brew:t", 1, severity="warning", local=plain_local(
			statement="A statement long enough to quote from."))
		view = make_view("brew:t", [item])
		self.assertEqual(view["initial_review_bucket"], "attention")
		pre = build_pre([view])
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "rerate",
			"target": {"tool_id": "brew:t", "kind": "item", "id": item["id"],
				"field": "severity"},
			"precondition": {"before": "warning"},
			"quote": "A statement long enough to quote from.", "after": "notable",
			"bucket_claim": lateral("attention"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]), terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_gate")
		post = result["corpus_post"]["tools"][0]
		self.assertEqual(post["initial_review_bucket"], "attention")
		self.assertEqual(post["forced_conservative"]["forced_bucket"], "attention")
		self.assertFalse(post["initial_pre_accept"])
		self.assertNotIn("initial_review_bucket",
			result["effect"]["moved"].get("brew:t", {}).get("axes", {}))
		explanation = result["effect"]["convergence_status"]["explanation"]
		self.assertIn("forced to attention", explanation["headline"])
		# Pass 7 finding 8: it only gained acceptance — never worded as a
		# move to auto-update or a stay out of the auto strip.
		self.assertIn("1 tool(s) would have started accepted without surviving the gate",
			explanation["headline"])
		self.assertNotIn("auto-update", explanation["headline"])
		self.assertNotIn("auto strip", explanation["body"])
		self.assertIn("brew:t starts undecided instead of with the pre-acceptance",
			explanation["body"])

	def test_the_forced_bucket_is_the_strictest_candidate(self):
		def v(bucket, has_security=False):
			return {"initial_review_bucket": bucket,
				"bucket_inputs": {"has_security": has_security}}
		cases = (
			(v("attention"), v("routine"), "attention"),
			(v("routine"), v("routine"), "routine"),
			(v("security_mixed"), v("security_auto"), "security_mixed"),
			(v("security_auto"), v("security_auto"), "security_mixed"),
			(v("attention", True), v("security_auto"), "attention"),
			(v("routine", True), v("routine", True), "security_mixed"),
			(v("bogus"), v("routine"), "attention"),
		)
		for pre_v, post_v, want in cases:
			with self.subTest(pre=pre_v, post=post_v):
				self.assertEqual(C.forced_bucket(pre_v, post_v), want)

	def test_a_forced_tool_whose_sole_fix_was_deleted_keeps_its_display(self):
		"""convergence.md §6: the forced-display snapshot is carried even when G-SEC applicability
		disappears — the recomputed tier is null, the display is not."""
		item = _gfix("brew:g", 1, usage=True)
		view = _gview("brew:g", [item], grounded=[1])
		pre = build_pre([view])
		delete = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "delete",
			"target": {"tool_id": "brew:g", "kind": "item", "id": item["id"], "field": None},
			"quote": item["local"]["statement"], "bucket_claim": lateral("security_auto"),
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [delete]), terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_gate")
		post = result["corpus_post"]["tools"][0]
		self.assertIsNone(post["security_tier"])
		self.assertEqual(post["forced_conservative"]["forced_display"]["priority"], "P2")
		self.assertFalse(post["initial_pre_accept"])

	def test_move_evidence_removes_the_confirmation_and_is_attributed(self):
		item = _gfix("brew:g", 1, usage=True)
		view = _gview("brew:g", [item], grounded=[1])
		pre = build_pre([view])
		result = run(pre, make_submission(pre, [_move("brew:g", item, 1)]))
		self.assertEqual(result["prominence"]["brew:g"]["to"], "P3")
		self.assertEqual(result["attribution"]["brew:g"][C.PRIORITY_AXIS]["attributed_to"],
			["cv-002"])
		# move_evidence needs no reason.body as an op — as a demotion it does
		self.assertIn("E-GATE-UNREASONED", codes_of(result))
		result = run(pre, make_submission(pre,
			[_move("brew:g", item, 1, body=PRIORITY_BODY)]))
		self.assertEqual(codes_of(result), [])

	def test_two_necessary_causes_are_both_attributed(self):
		"""Two usage-confirmed fixes; a redirect of one and a move of the
		other — neither alone demotes, so each omission restores and both
		are attributed."""
		one, two = _gfix("brew:g", 1, usage=True), _gfix("brew:g", 2, usage=True)
		view = _gview("brew:g", [one, two], grounded=[1, 2])
		pre = build_pre([view])
		edits = [_redirect("brew:g", one, body=PRIORITY_BODY),
			_move("brew:g", two, 2, body=PRIORITY_BODY)]
		result = run(pre, make_submission(pre, edits))
		self.assertEqual(codes_of(result), [])
		attributed = result["attribution"]["brew:g"][C.PRIORITY_AXIS]
		self.assertEqual(sorted(attributed["attributed_to"]), ["cv-001", "cv-002"])
		self.assertFalse(attributed["joint"])

	def test_two_redundant_causes_are_attributed_jointly(self):
		"""Each edit alone removes the one confirmation, so no single omission
		restores P2 — the subset search attributes both, `joint: true`."""
		item = _gfix("brew:g", 1, usage=True)
		view = _gview("brew:g", [item], grounded=[1])
		pre = build_pre([view])
		edits = [_redirect("brew:g", item, body=PRIORITY_BODY),
			_move("brew:g", item, 1, body=PRIORITY_BODY)]
		result = run(pre, make_submission(pre, edits))
		self.assertEqual(codes_of(result), [])
		attributed = result["attribution"]["brew:g"][C.PRIORITY_AXIS]
		self.assertEqual(sorted(attributed["attributed_to"]), ["cv-001", "cv-002"])
		self.assertTrue(attributed["joint"])

	def test_a_merge_carrying_the_grounded_entry_keeps_the_confirmation(self):
		one = _gfix("brew:g", 1, usage=True)
		two = _gfix("brew:g", 2, statement="The second fix, to quote.")
		view = _gview("brew:g", [one, two], grounded=[1])
		pre = build_pre([view])
		merged = copy.deepcopy(two)
		merged["local"]["direction"] = "reaches"
		merged["local"]["evidence"] = [_usage_entry(1)]
		merged["body"] = "Absorbs fix 1; the same usage line shows both."
		edits = [
			{"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "delete",
				"target": {"tool_id": "brew:g", "kind": "item", "id": one["id"], "field": None},
				"quote": one["local"]["statement"], "bucket_claim": lateral("security_auto"),
				"reason": cut_reason()},
			{"edit_id": "cv-002", "check": "C2-tags-visibility", "op": "merge",
				"target": {"tool_id": "brew:g", "kind": "item", "id": two["id"], "field": None},
				"quote": "The second fix, to quote.", "after": merged,
				"changed_fields": ["body", "local.direction", "local.evidence"],
				"requires": ["cv-001"], "bucket_claim": lateral("security_auto"),
				"reason": cut_reason()}]
		result = run(pre, make_submission(pre, edits))
		self.assertEqual(codes_of(result), [])
		self.assertEqual(result["prominence"], {})
		post = result["corpus_post"]["tools"][0]
		self.assertEqual(post["usage_item_ids"], [two["id"]])

	def test_a_forged_usage_entry_grounds_nothing(self):
		item = _gfix("brew:g", 1, statement="The fix, to quote.")
		view = _gview("brew:g", [item], grounded=[])
		pre = build_pre([view])
		forged = copy.deepcopy(item)
		forged["local"]["direction"] = "reaches"
		forged["local"]["evidence"] = [_usage_entry(9)]
		edit = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "merge",
			"target": {"tool_id": "brew:g", "kind": "item", "id": item["id"], "field": None},
			"quote": "The fix, to quote.", "after": forged,
			"changed_fields": ["local.direction", "local.evidence"],
			"bucket_claim": lateral("security_auto"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [edit]))
		self.assertEqual(result["prominence"], {})
		self.assertEqual(model.security_priority(result["corpus_post"]["tools"][0]), "P3")

	def test_a_held_to_accepted_edit_trips_the_permissive_gate(self):
		item = _gfix("brew:g", 1)
		watch = make_item("brew:g", 2, local=plain_local(statement="Watched, to quote."),
			watch_hit={"topic": "t"})
		view = _gview("brew:g", [item, watch])
		self.assertEqual(view["security_tier"]["tier"], "held")
		pre = build_pre([view])
		delete = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "delete",
			"target": {"tool_id": "brew:g", "kind": "item", "id": watch["id"], "field": None},
			"quote": "Watched, to quote.",
			"bucket_claim": {"moves_bucket": True, "expected_from": "security_mixed",
				"expected_to": "security_auto", "direction": "permissive"},
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [delete]))
		self.assertIn("E-GATE-UNREASONED", codes_of(result))
		self.assertEqual({g["kind"] for g in result["gate"]}, {"permissive"})
		delete["reason"] = cut_reason(body="The watch claim was a duplicate; without it "
			"the fix moves to security_auto and is pre-accepted.")
		result = run(pre, make_submission(pre, [delete]))
		self.assertEqual(codes_of(result), [])

	def test_a_p1_to_p2_demotion_with_moved_axes_unchanged(self):
		"""convergence.md §6: ANY strict decrease is a demotion — here the sole
		proposed edit deleted on a usage-confirmed fix. A major delta keeps
		the risk elevated, so no MOVED axis changes."""
		item = _gfix("brew:g", 1, usage=True)
		edit = _edit("brew:g", "proposed")
		view = _gview("brew:g", [item], [edit], grounded=[1], version_delta="major")
		self.assertEqual(model.security_priority(view), "P1")
		pre = build_pre([view])
		delete = {"edit_id": "cv-001", "check": "C7-collisions", "op": "delete",
			"target": {"tool_id": "brew:g", "kind": "suggestion", "id": edit["id"],
				"field": None},
			"quote": "An edit title to quote.", "bucket_claim": lateral("security_auto"),
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [delete]))
		self.assertEqual(result["moved"], {})
		self.assertEqual(result["prominence"]["brew:g"],
			{"from": "P1", "to": "P2", "lost": True})
		self.assertIn("E-GATE-UNREASONED", codes_of(result))
		delete["reason"] = cut_reason(body="The edit duplicates another tool's; dropping it "
			"lowers this fix's priority from P1 to P2, still highlighted.")
		result = run(pre, make_submission(pre, [delete]))
		self.assertEqual(codes_of(result), [])

	def test_an_edit_on_a_duplicated_suggestion_id_is_refused(self):
		"""Review A12: two suggestions on one tool sharing an id (W-SUG-DUP-ID)
		name no ONE element. A delete of that id used to remove whichever
		copy compared equal first; it is refused as E-EDIT-TARGET instead."""
		item = _gfix("brew:g", 1)
		edit = _edit("brew:g", "proposed")
		twin = copy.deepcopy(edit)
		twin["title"] = "A different edit with the same id."
		view = _gview("brew:g", [item], [edit, twin])
		pre = build_pre([view])
		delete = {"edit_id": "cv-001", "check": "C7-collisions", "op": "delete",
			"target": {"tool_id": "brew:g", "kind": "suggestion", "id": edit["id"],
				"field": None},
			"quote": "An edit title to quote.", "bucket_claim": lateral("security_auto"),
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [delete]))
		self.assertIn("E-EDIT-TARGET", codes_of(result))
		self.assertNotIn("E-APPLY-SCOPE", codes_of(result))
		self.assertTrue(any("more than one suggestion" in f["detail"]
			for f in result["critical"] if f["code"] == "E-EDIT-TARGET"))

	def test_a_delete_removes_the_resolved_element_not_an_equal_one(self):
		"""The identity half of review A12: `list.remove` takes the FIRST equal
		element. Two equal items can never both exist (ids are unique), so
		this is pinned on the helper directly."""
		a, b = {"id": "x", "v": 1}, {"id": "x", "v": 1}
		view = {"id": "brew:g", "items": [], "suggestions": [a, b]}

		class Idx:
			views = {"brew:g": view}

			def resolve(self, tool_id, kind, element_id):
				return b, "suggestion"
		apply_converge._apply_edit({}, {"op": "delete", "target": {"tool_id": "brew:g",
			"kind": "suggestion", "id": "x"}}, Idx())
		self.assertEqual(len(view["suggestions"]), 1)
		self.assertIs(view["suggestions"][0], a)

	def test_a_rerate_of_the_served_item_rereads_a_proposed_edit(self):
		"""Technical round-2 finding 6: the reading is over CURRENT items. A
		proposed edit serving an incompatible item reads required (P0); rerated
		to warning, it reads proposed again — P0 → P1, pre-acceptance gained —
		which the permissive gate and the demotion gate both see."""
		fix, inc = _gfix("brew:g", 1), _incompat("brew:g", 2)
		sug = _edit("brew:g", "proposed", [inc["id"]])
		view = _gview("brew:g", [fix, inc], [sug])
		self.assertEqual(view["security_tier"]["reasons"][0], "required-edit")
		pre = build_pre([view])
		rerate = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "rerate",
			"target": {"tool_id": "brew:g", "kind": "item", "id": inc["id"],
				"field": "severity"},
			"precondition": {"before": "incompatible"},
			"quote": "Incompatible statement to quote.", "after": "warning",
			"bucket_claim": {"moves_bucket": True, "expected_from": "security_mixed",
				"expected_to": "security_auto", "direction": "permissive"},
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [rerate]))
		self.assertEqual(result["prominence"]["brew:g"],
			{"from": "P0", "to": "P1", "lost": True})
		self.assertIn("brew:g", result["moved"])
		self.assertEqual({g["kind"] for g in result["gate"]}, {"permissive", "demotion"})
		rerate["reason"] = cut_reason(body="It breaks nothing here, so the upgrade is "
			"pre-accepted and the fix's priority falls from P0 to P1.")
		result = run(pre, make_submission(pre, [rerate]))
		self.assertEqual(codes_of(result), [])

	def test_a_tool_failing_both_gates_is_forced_and_explained_for_both(self):
		"""Review round 2: a tool that entered security_auto AND had its
		priority lowered, with neither surviving, records BOTH gate kinds —
		the explanation and the forced record must not keep only the first."""
		fix, inc = _gfix("brew:g", 1), _incompat("brew:g", 2)
		sug = _edit("brew:g", "proposed", [inc["id"]])
		pre = build_pre([_gview("brew:g", [fix, inc], [sug])])
		rerate = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "rerate",
			"target": {"tool_id": "brew:g", "kind": "item", "id": inc["id"],
				"field": "severity"},
			"precondition": {"before": "incompatible"},
			"quote": "Incompatible statement to quote.", "after": "warning",
			"bucket_claim": {"moves_bucket": True, "expected_from": "security_mixed",
				"expected_to": "security_auto", "direction": "permissive"},
			"reason": cut_reason()}
		result = run(pre, make_submission(pre, [rerate]), terminal=True, attempt=5)
		self.assertEqual(result["state"], "degraded_gate")
		forced = result["corpus_post"]["tools"][0]["forced_conservative"]
		self.assertEqual(forced["kinds"], ["permissive", "demotion"])
		self.assertEqual(forced["kind"], "permissive")  # first kind, for old readers
		self.assertEqual(forced["code_by_kind"],
			{"permissive": "E-GATE-UNREASONED", "demotion": "E-GATE-UNREASONED"})
		self.assertEqual(forced["forced_display"]["priority"], "P0")
		status = result["effect"]["convergence_status"]
		self.assertEqual(status["degraded_tools"][0]["kinds"], ["permissive", "demotion"])
		self.assertEqual(result["effect"]["tools"]["brew:g"]["forced"]["kinds"],
			["permissive", "demotion"])
		explanation = status["explanation"]
		self.assertIn("reached auto-update without surviving the gate",
			explanation["headline"])
		self.assertIn("had their security priority lowered", explanation["headline"])
		self.assertIn("keeps its prior priority", explanation["headline"])
		self.assertIn("kept out of the auto strip", explanation["body"])
		self.assertIn("pre-convergence priority", explanation["body"])
		self.assertIn("brew:g both", explanation["body"])

	def test_a_required_edit_stays_required_and_the_note_says_why(self):
		fix, inc = _gfix("brew:g", 1), _incompat("brew:g", 2)
		sug = _edit("brew:g", "required", [inc["id"]])
		view = _gview("brew:g", [fix, inc], [sug])
		pre = build_pre([view])
		rerate = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "rerate",
			"target": {"tool_id": "brew:g", "kind": "item", "id": inc["id"],
				"field": "severity"},
			"precondition": {"before": "incompatible"},
			"quote": "Incompatible statement to quote.", "after": "warning",
			"bucket_claim": lateral("security_mixed"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [rerate]))
		self.assertEqual(codes_of(result), [])
		post = result["corpus_post"]["tools"][0]
		self.assertEqual(post["security_tier"]["reasons"][0], "required-edit")
		notes = [n for n in result["notes"] if n["code"] == "W-EDIT-SCHEMA"]
		self.assertTrue(any("E-SUG-REQUIRED-UNGROUNDED" in n["detail"]
			and n["edit_id"] == "cv-001" for n in notes), notes)

	def test_deleting_the_served_item_leaves_the_required_edit_p0(self):
		fix, inc = _gfix("brew:g", 1), _incompat("brew:g", 2)
		sug = _edit("brew:g", "required", [inc["id"]])
		view = _gview("brew:g", [fix, inc], [sug])
		pre = build_pre([view])
		delete = {"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "delete",
			"target": {"tool_id": "brew:g", "kind": "item", "id": inc["id"], "field": None},
			"quote": "Incompatible statement to quote.",
			"bucket_claim": lateral("security_mixed"), "reason": cut_reason()}
		result = run(pre, make_submission(pre, [delete]))
		self.assertEqual(codes_of(result), [])
		post = result["corpus_post"]["tools"][0]
		self.assertEqual(post["security_tier"]["priority"], "P0")
		self.assertEqual(post["security_tier"]["reasons"][0], "required-edit")
		notes = " ".join(n["detail"] for n in result["notes"] if n["code"] == "W-EDIT-SCHEMA")
		self.assertIn("E-SUG-SERVES-UNRESOLVED", notes)

	def test_merging_the_served_item_away_leaves_an_unserved_survivor_p0(self):
		fix = _gfix("brew:g", 1)
		served = _incompat("brew:g", 2, statement="The served one, to quote.")
		other = _incompat("brew:g", 3, statement="The other one, to quote.")
		sug = _edit("brew:g", "proposed", [served["id"]])
		view = _gview("brew:g", [fix, served, other], [sug])
		self.assertEqual(view["security_tier"]["ids"]["incompatible-unfixed"], [other["id"]])
		pre = build_pre([view])
		merged = dict(copy.deepcopy(other), body="Absorbs the served incompatible item.")
		edits = [
			{"edit_id": "cv-001", "check": "C2-tags-visibility", "op": "delete",
				"target": {"tool_id": "brew:g", "kind": "item", "id": served["id"],
					"field": None},
				"quote": "The served one, to quote.",
				"bucket_claim": lateral("security_mixed"), "reason": cut_reason()},
			{"edit_id": "cv-002", "check": "C2-tags-visibility", "op": "merge",
				"target": {"tool_id": "brew:g", "kind": "item", "id": other["id"],
					"field": None},
				"quote": "The other one, to quote.", "after": merged,
				"changed_fields": ["body"], "requires": ["cv-001"],
				"bucket_claim": lateral("security_mixed"), "reason": cut_reason()}]
		result = run(pre, make_submission(pre, edits))
		self.assertEqual(codes_of(result), [])
		tier = result["corpus_post"]["tools"][0]["security_tier"]
		# the merged-away id links nothing, so the proposed edit reads proposed
		# again — and the survivor, served by no required edit, is P0
		self.assertEqual(tier["ids"]["incompatible-unfixed"], [other["id"]])
		self.assertNotIn("required-edit", tier["reasons"])
		self.assertEqual(tier["priority"], "P0")
		notes = " ".join(n["detail"] for n in result["notes"] if n["code"] == "W-EDIT-SCHEMA")
		self.assertIn("E-SUG-SERVES-UNRESOLVED", notes)


class NoIOAndCorpusVersionTests(unittest.TestCase):
	def test_element_revalidation_reads_no_file(self):
		"""Technical round-2 finding 7: the applier is FORBIDDEN file access.
		An existing absolute path in a usage entry would be resolved and read
		by I-14/I-23 under a real resolver; under NO_IO_RESOLVER nothing is."""
		here = os.path.abspath(__file__)
		item = _gfix("brew:g", 1)
		item["local"]["direction"] = "reaches"
		item["local"]["evidence"] = [{"path": here, "role": "usage", "quote": "import"},
			here, {"path": "/definitely/not/there"}]

		def forbidden(*args, **kwargs):
			raise AssertionError("file access from the applier: {!r}".format(args))
		with mock.patch("builtins.open", side_effect=forbidden), \
				mock.patch("os.path.exists", side_effect=forbidden), \
				mock.patch("os.path.isfile", side_effect=forbidden), \
				mock.patch("os.stat", side_effect=forbidden):
			codes = apply_converge._element_codes(item, "brew:g", 0, None)
		self.assertFalse(codes & {"E-EVID-404", "W-EVID-ROOT", "E-USAGE-UNGROUNDED",
			"W-USAGE-INSTALL-ONLY"}, codes)
		self.assertFalse(any(c.startswith("E-VALIDATOR-CRASH") for c in codes), codes)

	@staticmethod
	def bad(expected):
		"""Every refused spelling, around the CURRENT number: the string and
		float forms are of the right value, so only the type check refuses
		them — hard-coded, they would go stale on the next bump."""
		return ((None, "missing"), (expected - 1, "old"), (str(expected), "a string"),
			(float(expected), "a float"), (True, "a bool"))

	def _stale(self, key, value):
		pre = fixture_pre()
		if value is None:
			del pre[key]
		else:
			pre[key] = value
		return pre

	def test_a_stale_corpus_is_refused_at_every_attempt_and_projection(self):
		for key, expected in (("contract_version", model.CONTRACT_VERSION),
				("converge_version", C.CONVERGE_VERSION)):
			for value, why in self.bad(expected):
				with self.subTest(key=key, value=why):
					pre = self._stale(key, value)
					submission = fixture_submission()
					for terminal, attempt in ((False, 1), (True, 5)):
						with self.assertRaises(C.CorpusVersionError):
							apply_converge.apply_converge(pre, submission, attempt=attempt,
								terminal=terminal)
					with self.assertRaises(C.CorpusVersionError):
						C.build_view(pre)
					with self.assertRaises(C.CorpusVersionError):
						C.build_tables(pre)

	def test_a_stale_submission_on_a_current_corpus_is_still_e_submit_version(self):
		submission = fixture_submission()
		submission["converge_version"] = 2
		result = run(FIXTURE_PRE, submission)
		self.assertEqual(codes_of(result), ["E-SUBMIT-VERSION"])

	def test_the_cli_refuses_a_stale_corpus_without_consuming_an_attempt(self):
		tmp = tempfile.mkdtemp(prefix="converge-stale-")
		self.addCleanup(shutil.rmtree, tmp, True)
		session = os.path.join(tmp, "session")
		os.makedirs(session)
		pre = fixture_pre()
		pre["converge_version"] = 2
		with open(os.path.join(session, "corpus.pre.json"), "w", encoding="utf-8") as fh:
			json.dump(pre, fh)
		draft = os.path.join(tmp, "draft.json")
		with open(draft, "w", encoding="utf-8") as fh:
			json.dump(fixture_submission(), fh)
		for mode in ("--check", "--submit"):
			with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
				self.assertEqual(apply_converge.main(["--session", session, mode, draft]), 4)
			self.assertFalse(os.path.exists(os.path.join(session, "converge-attempts.json")))
			self.assertFalse(os.path.exists(os.path.join(session, "corpus.post.json")))


if __name__ == "__main__":
	unittest.main()
