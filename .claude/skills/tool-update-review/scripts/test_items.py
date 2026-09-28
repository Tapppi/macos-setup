#!/usr/bin/env python3
"""
test_items.py — the item model, and the fixtures it publishes.
Usage: python3 test_items.py [-v]

Stdlib `unittest` only (no pytest, no network), same as `test_assemble.py`, so
it runs on the same bare python3 the scripts themselves target.

Five groups:

1. Vocabularies and the group mapping — the closed sets, and what an
   unrecognized member does (it is kept and reported, never dropped).
2. Ids — derivation from a declared anchor, the grammars, disambiguation.
3. **The ordering and the comparator.** This is the group that exists because
   the cost of not having it was measured: two implementer tracks
   against a pinned *field* contract drifted on ordering and had to be
   reconciled by hand. Every tier of the sort key is asserted, and the
   published fixture is asserted against the code so it cannot go stale.
4. The two CVE rank tables, asserted tier for tier against `assemble.py`'s
   private copies. Those two tables are the single most drift-prone thing in
   the contract and they are NOT interchangeable.
5. Fixture agreement — `contract.json` and `expected_validation.json` are
   generated, so the tests assert the checked-in copies still match what the
   code produces. A published fixture that can go stale is worth no more than
   a paragraph.
"""
import json
import os
import re
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble  # noqa: E402
import items as model  # noqa: E402
import validate_items  # noqa: E402


ORDERING = model.load_fixture("ordering.json")
COMPARATOR = model.load_fixture("comparator.json")
BY_ID = {i["id"]: i for i in ORDERING["items"]}


# ── 1. Vocabularies and grouping ────────────────────────────────────────────
class VocabularyTests(unittest.TestCase):
	def test_the_tag_set_is_the_documented_eight(self):
		self.assertEqual(len(model.TAGS), 8)
		self.assertEqual(set(model.TAGS), {
			"security", "fix", "feature", "breaking", "deprecation", "perf",
			"packaging", "chore"})

	def test_there_is_no_notable_tag_because_notable_is_a_severity(self):
		"""Two spellings of one concept is the conflation this redesign removes.
		Visibility is `severity >= notable`, which says it once."""
		self.assertNotIn("notable", model.TAGS)
		self.assertIn("notable", model.SEVERITIES)

	def test_every_tag_maps_to_a_group_and_every_group_is_in_precedence(self):
		self.assertEqual(set(model.GROUP_OF_TAG), set(model.TAGS))
		self.assertEqual(set(model.GROUP_OF_TAG.values()), set(model.GROUP_PRECEDENCE))

	def test_a_breaking_change_groups_under_fixes_where_a_reader_looks(self):
		"""The measured misfile: codex's "`codex exec --full-auto` was removed"
		is filed today as notes/notable, so it renders where nobody looks."""
		self.assertEqual(model.primary_group({"tags": ["breaking"]}), "fixes")

	def test_an_unrecognized_tag_contributes_to_no_group_but_is_not_an_error_here(self):
		self.assertEqual(model.primary_group({"tags": ["hardening"]}), "notes")
		self.assertEqual(model.primary_group({"tags": ["hardening", "feature"]}), "features")

	def test_primary_group_never_raises_on_a_drifted_item(self):
		for shape in ({}, {"tags": None}, {"tags": "security"}, {"tags": [7]},
				{"tags": [None]}, {"tags": []}):
			with self.subTest(repr(shape)):
				self.assertEqual(model.primary_group(shape), "notes")

	def test_severity_rank_puts_an_unknown_value_after_info_not_among_the_four(self):
		self.assertEqual(model.severity_rank("bogus"), -1)
		self.assertEqual(model.severity_rank(None), -1)
		self.assertEqual(model.severity_rank(["warning"]), -1)
		self.assertLess(model.severity_rank("bogus"), model.severity_rank("info"))

	def test_security_only_tags_disqualify_exactly_the_documented_four(self):
		disqualifying = set(model.TAGS) - model.SECURITY_ONLY_TAGS
		self.assertEqual(disqualifying, {"feature", "breaking", "deprecation", "perf"})

	def test_a_security_item_at_any_severity_is_allowed_for_security_only(self):
		for severity in model.SEVERITIES:
			with self.subTest(severity):
				self.assertTrue(model.allowed_for_security_only(
					{"tags": ["security"], "severity": severity}))

	def test_a_non_security_item_above_notable_is_not(self):
		self.assertFalse(model.allowed_for_security_only(
			{"tags": ["fix"], "severity": "warning"}))
		self.assertTrue(model.allowed_for_security_only(
			{"tags": ["fix"], "severity": "notable"}))

	def test_an_unknown_tag_is_not_evidence_of_harmlessness(self):
		self.assertFalse(model.allowed_for_security_only(
			{"tags": ["hardening"], "severity": "info"}))

	def test_recompute_flags_reads_only_recognized_tags(self):
		flags = model.recompute_flags([
			{"tags": ["securty"], "severity": "info"},
			{"tags": ["fix"], "severity": "notable", "local": {"direction": "unclear"}},
		])
		self.assertEqual(flags, {"has_security": False, "has_breaking": False,
			"worst_severity": "notable", "local_findings": 1})

	def test_the_four_emittable_flags_and_the_five_validator_only_ones_are_disjoint(self):
		self.assertFalse(set(model.CHECKER_FLAGS) & set(model.VALIDATOR_ONLY_FLAGS))
		self.assertIn("security_only", model.VALIDATOR_ONLY_FLAGS)
		self.assertIn("review_bucket", model.VALIDATOR_ONLY_FLAGS)
		self.assertIn("pre_accept", model.VALIDATOR_ONLY_FLAGS)


# ── 2. Ids ──────────────────────────────────────────────────────────────────
class IdentityTests(unittest.TestCase):
	def test_the_published_id_cases(self):
		for case in COMPARATOR["ids"]["cases"]:
			with self.subTest(case["expect_id"]):
				self.assertEqual(model.derive_item_id(case["tool_id"], case["anchor"]),
					case["expect_id"])
				self.assertEqual(model.id_stability(case["anchor"]), case["expect_stability"])

	def test_anchor_grammars(self):
		good = [
			("cve", "CVE-2026-18408"), ("cve", "CVE-1999-0001"),
			("advisory", "wnpa-sec-2026-87"), ("advisory", "GHSA-abcd-efgh-ijkl"),
			("issue", "getsops/sops#2245"), ("issue", "#2245"),
			("commit", "f2ff0b2a"), ("commit", "openai/codex@f2ff0b2a"),
			("release", "10.5p1/ssh-Z-key-order"),
		]
		for kind, value in good:
			with self.subTest("good", kind=kind, value=value):
				self.assertTrue(model.anchor_is_wellformed({"kind": kind, "value": value}))
		bad = [
			("cve", "CVE-26-1"), ("cve", "cve-2026-18408"),
			("advisory", "GH"), ("issue", "2245"), ("issue", "sops#abc"),
			("commit", "f2ff0b"), ("commit", "zzzzzzz"), ("release", "10.5p1"),
			("release", "10.5p1/has a space"),
		]
		for kind, value in bad:
			with self.subTest("bad", kind=kind, value=value):
				self.assertFalse(model.anchor_is_wellformed({"kind": kind, "value": value}))

	def test_kind_none_needs_a_slug_and_no_value(self):
		self.assertTrue(model.anchor_is_wellformed({"kind": "none", "slug": "quarantine"}))
		self.assertFalse(model.anchor_is_wellformed({"kind": "none"}))
		self.assertFalse(model.anchor_is_wellformed({"kind": "none", "slug": "  "}))
		self.assertFalse(model.anchor_is_wellformed(
			{"kind": "none", "slug": "x", "value": "y"}))

	def test_a_malformed_anchor_still_yields_an_addressable_id(self):
		"""An item with no handle is unaddressable by convergence's edit list,
		which is worse than an ugly one."""
		for anchor in (None, "nonsense", {}, {"kind": "bogus", "value": "x"}):
			with self.subTest(repr(anchor)):
				item_id = model.derive_item_id("brew:x", anchor)
				self.assertTrue(item_id.startswith("brew:x#"))

	def test_a_duplicate_id_is_suffixed_and_both_survive(self):
		seen = set()
		first = model.disambiguate("brew:sops#issue:x", seen)
		seen.add(first)
		second = model.disambiguate("brew:sops#issue:x", seen)
		seen.add(second)
		third = model.disambiguate("brew:sops#issue:x", seen)
		self.assertEqual([first, second, third],
			["brew:sops#issue:x", "brew:sops#issue:x~2", "brew:sops#issue:x~3"])


# ── 3. The ordering and the comparator ──────────────────────────────────────
class OrderingContractTests(unittest.TestCase):
	"""Pin the shared comparator, not just the field names."""

	def test_the_published_ordering_fixture_is_what_the_code_produces(self):
		self.assertEqual([i["id"] for i in model.order_items(ORDERING["items"])],
			ORDERING["expected_order"])

	def test_the_published_group_expectations_hold(self):
		for item_id, group in ORDERING["expected_primary_group"].items():
			with self.subTest(item_id):
				self.assertEqual(model.primary_group(BY_ID[item_id]), group)

	def test_the_published_comparator_pairs(self):
		for pair in COMPARATOR["pairs"]:
			with self.subTest("{} vs {}".format(pair["left"], pair["right"])):
				self.assertEqual(
					model.compare_items(BY_ID[pair["left"]], BY_ID[pair["right"]]),
					pair["expect"], pair["why"])

	def test_the_order_is_total_so_two_sorts_of_one_corpus_agree(self):
		"""Without the id tiebreak the order is stable-but-input-dependent,
		which is exactly the drift that had to be reconciled by hand."""
		keys = [model.item_sort_key(i) for i in ORDERING["items"]]
		self.assertEqual(len(set(keys)), len(keys))
		shuffled = list(reversed(ORDERING["items"]))
		self.assertEqual([i["id"] for i in model.order_items(shuffled)],
			ORDERING["expected_order"])

	def test_order_items_never_mutates_its_argument(self):
		original = list(ORDERING["items"])
		snapshot = [i["id"] for i in original]
		model.order_items(original)
		self.assertEqual([i["id"] for i in original], snapshot)

	def test_an_unhashable_enum_value_sorts_rather_than_raising(self):
		"""Every enum in this model is kept verbatim when out of vocabulary, so
		any of them can arrive as a list or a dict. A rank lookup that raises
		TypeError on one bad field costs the whole tool."""
		hostile = [
			{"id": "u#1", "tags": ["fix"], "severity": "info",
				"local": {"direction": ["reaches"], "effect": {"a": 1}}},
			{"id": "u#2", "tags": [["security"]], "severity": ["info"]},
			{"id": "u#3", "tags": ["security"], "severity": "warning",
				"security": {"rating": ["critical"], "exploited_in_wild": True}},
		]
		self.assertEqual(len(model.order_items(hostile)), 3)
		self.assertEqual(model.compare_items(hostile[0], hostile[1]), -1)
		model.security_display_sort_key(hostile[2])
		self.assertFalse(model.allowed_for_security_only(hostile[1]))
		self.assertEqual(model.worst_severity(hostile), "warning")

	def test_order_items_keeps_a_malformed_member_rather_than_raising(self):
		out = model.order_items([{"id": "t#a", "tags": ["chore"], "severity": "info"},
			"a bare string", None, 7])
		self.assertEqual(len(out), 4)

	def test_comparator_is_antisymmetric_and_reflexive_across_the_fixture(self):
		for left in ORDERING["items"]:
			for right in ORDERING["items"]:
				forward = model.compare_items(left, right)
				self.assertEqual(forward, -model.compare_items(right, left))
				if left is right:
					self.assertEqual(forward, 0)

	def test_worst_severity_cases(self):
		for case in COMPARATOR["worst_severity"]:
			with self.subTest(str(case["severities"])):
				self.assertEqual(
					model.worst_severity([{"severity": s} for s in case["severities"]]),
					case["expect"])

	def test_finding_order_is_by_subject_not_by_severity(self):
		"""A reader scans by subject, and a stable subject order is what makes
		two runs diffable."""
		findings = [
			{"tool_id": "brew:b", "item_id": None, "code": "E-A", "field": "x", "value": ""},
			{"tool_id": "brew:a", "item_id": "i2", "code": "W-Z", "field": "", "value": ""},
			{"tool_id": "brew:a", "item_id": "i1", "code": "W-Z", "field": "", "value": ""},
		]
		self.assertEqual(
			[f["tool_id"] + ":" + str(f["item_id"]) for f in
				sorted(findings, key=model.finding_sort_key)],
			["brew:a:i1", "brew:a:i2", "brew:b:None"])


class SecurityDisplayTests(unittest.TestCase):
	"""The replacement for `notable[]`'s clause 3. A bar, not a count."""

	def test_the_published_bar_cases(self):
		for case in COMPARATOR["security_display"]["cases"]:
			with self.subTest(case["item"]["id"]):
				self.assertEqual(model.is_security_display_item(case["item"]),
					case["expect"], case.get("why", ""))

	def test_the_openssh_padding_case_selects_nothing(self):
		"""All three of openssh's notable slots were filled with items that say
		in their own summaries that the fix does not reach this machine. Zero
		items qualify, and that is the correct answer."""
		openssh = [
			{"id": "a", "tags": ["security", "fix"], "severity": "notable",
				"security": {"rating": "unknown", "exploited_in_wild": False},
				"local": {"direction": "does_not_reach"}},
			{"id": "b", "tags": ["security", "fix"], "severity": "info",
				"security": {"rating": "unknown", "exploited_in_wild": False},
				"local": {"direction": "does_not_reach"}},
			{"id": "c", "tags": ["security", "fix"], "severity": "info",
				"security": {"rating": "unknown", "exploited_in_wild": False},
				"local": {"direction": "does_not_reach"}},
		]
		self.assertEqual(model.security_display_items(openssh), [])

	def test_there_is_no_cap_so_nothing_is_ever_padded_or_truncated_to_three(self):
		items = [{"id": "i{}".format(n), "tags": ["security"], "severity": "warning",
			"security": {"rating": "high", "exploited_in_wild": False}} for n in range(7)]
		self.assertEqual(len(model.security_display_items(items)), 7)


class CveRankTableTests(unittest.TestCase):
	"""Two tables, and they are not interchangeable. `unknown` means "no rating
	recorded" when resolving a conflict (so it loses to a real `low`), and
	"ungraded but real" when ordering the display (so it does not sort below
	one).

	These used to be asserted tier-for-tier against `assemble.py`'s private
	copies, because there were two implementations that could drift. There is
	one now: assembly imports the model. What is left to guard is that nobody
	reintroduces a copy — a private table would go green against this file
	while the report and the page disagreed."""

	def test_assembly_keeps_no_private_rank_table(self):
		for gone in ("_CVE_SEVERITY_RANK", "_NOTABLE_SEVERITY_RANK", "_SEVERITY_RANK",
				"_CVE_SEVERITIES", "_SEVERITY_BASES"):
			self.assertFalse(hasattr(assemble, gone),
				f"assemble.{gone} is back — the model publishes it, import it")

	def test_unknown_is_below_low_when_resolving_and_above_it_when_ordering(self):
		self.assertLess(model.CVE_WORSE_RANK["unknown"], model.CVE_WORSE_RANK["low"])
		self.assertGreater(model.CVE_ORDER_RANK["unknown"], model.CVE_ORDER_RANK["low"])

	def test_the_published_rank_orders(self):
		ranks = COMPARATOR["cve_ranks"]
		self.assertEqual(ranks["worse_descending"],
			sorted(ranks["worse_descending"], key=lambda r: -model.CVE_WORSE_RANK[r]))
		self.assertEqual(ranks["order_descending"],
			sorted(ranks["order_descending"], key=lambda r: -model.CVE_ORDER_RANK[r]))


class EvidenceShorthandTests(unittest.TestCase):
	def test_the_published_shorthand_cases(self):
		for case in COMPARATOR["evidence_shorthand"]["cases"]:
			with self.subTest(repr(case["input"])):
				self.assertEqual(model.parse_evidence_shorthand(case["input"]),
					case["expect"], case.get("why", ""))

	def test_the_line_list_form_the_run_already_produced_is_in_the_grammar(self):
		"""`dotfiles/home/.claude/settings.json:162,183` is a real citation and
		today's `_TRAILING_LINE_REF` silently fails to strip it."""
		self.assertEqual(
			model.parse_evidence_shorthand("dotfiles/home/.claude/settings.json:162,183"),
			{"path": "dotfiles/home/.claude/settings.json", "lines": [162, 183]})

	def test_a_non_string_is_not_shorthand(self):
		for value in (None, 7, [], {}, {"path": "Brewfile"}):
			with self.subTest(repr(value)):
				self.assertIsNone(model.parse_evidence_shorthand(value))

	def test_url_syntax_is_checked_and_nothing_is_ever_fetched(self):
		self.assertTrue(model.url_is_absolute_http("https://example.com/a"))
		self.assertTrue(model.url_is_absolute_http("http://example.com"))
		for bad in ("example.com", "ftp://example.com", "/a/b", "", None,
				"https://", "javascript:alert(1)"):
			with self.subTest(repr(bad)):
				self.assertFalse(model.url_is_absolute_http(bad))


# ── 5a. regenerate.py's concurrency guard ──────────────────────────────────
class RegenerateGuardTests(unittest.TestCase):
	"""regenerate.py reads the working tree and rewrites six shared fixtures,
	so running it over another agent's uncommitted edits to them sweeps those
	edits into this run's diff (IMPLEMENTATION §7.16's incident). It refuses
	instead, unless told the changes are the caller's own; and a directory git
	cannot answer for is a refusal, never "clean"."""

	@classmethod
	def setUpClass(cls):
		sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
			"contract"))
		import regenerate
		cls.regenerate = regenerate

	def _repo(self, td):
		import subprocess
		subprocess.run(["git", "init", "-q", td], check=True, capture_output=True)
		return td

	def test_dirty_outputs_names_exactly_the_uncommitted_generated_files(self):
		import tempfile
		with tempfile.TemporaryDirectory() as td:
			self._repo(td)
			self.assertEqual(self.regenerate.dirty_outputs(td), [])
			for name in ("contract.json", "unrelated.json"):
				with open(os.path.join(td, name), "w", encoding="utf-8") as fh:
					fh.write("{}")
			self.assertEqual(self.regenerate.dirty_outputs(td), ["contract.json"])

	def test_outside_git_the_answer_is_unknown_not_clean(self):
		import tempfile
		with tempfile.TemporaryDirectory() as td:
			self.assertIsNone(self.regenerate.dirty_outputs(td))

	def test_main_refuses_to_overwrite_uncommitted_fixtures(self):
		import tempfile
		with tempfile.TemporaryDirectory() as td:
			self._repo(td)
			path = os.path.join(td, "expected_validation.json")
			with open(path, "w", encoding="utf-8") as fh:
				fh.write("someone else's edit")
			with unittest.mock.patch("sys.stderr"):
				self.assertEqual(self.regenerate.main([], here=td), 2)
			with open(path, encoding="utf-8") as fh:
				self.assertEqual(fh.read(), "someone else's edit")
			self.assertEqual(sorted(os.listdir(td)), [".git", "expected_validation.json"])

	def test_main_refuses_when_git_cannot_answer(self):
		import tempfile
		with tempfile.TemporaryDirectory() as td:
			with unittest.mock.patch("sys.stderr"):
				self.assertEqual(self.regenerate.main([], here=td), 2)
			self.assertEqual(os.listdir(td), [])

	def test_allow_dirty_regenerates_every_output(self):
		import tempfile
		with tempfile.TemporaryDirectory() as td:
			self._repo(td)
			with open(os.path.join(td, "contract.json"), "w", encoding="utf-8") as fh:
				fh.write("mine")
			with unittest.mock.patch("sys.stdout"):
				self.assertEqual(self.regenerate.main(["--allow-dirty"], here=td), 0)
			self.assertEqual(sorted(n for n in os.listdir(td) if n != ".git"),
				sorted(self.regenerate.GENERATED))
			with open(os.path.join(td, "contract.json"), encoding="utf-8") as fh:
				self.assertEqual(json.load(fh), model.contract())


# ── 5. Fixture agreement ────────────────────────────────────────────────────
class PublishedFixtureTests(unittest.TestCase):
	"""A published fixture that can go stale is worth no more than a paragraph.
	Regenerate with `python3 contract/regenerate.py`, then read the diff."""

	def test_contract_json_is_what_the_code_produces(self):
		self.assertEqual(model.load_fixture("contract.json"), model.contract())

	def test_the_golden_validation_run_still_matches(self):
		session, roots, unconfigured = validate_items.fixture_session()
		got = validate_items.validate_session(session, roots, manifest_root=roots[0],
			unconfigured_roots=unconfigured)
		expected = model.load_fixture("expected_validation.json")
		self.assertEqual(json.loads(json.dumps(got)), expected)

	def test_the_golden_run_is_reproducible_within_one_process(self):
		session, roots, unconfigured = validate_items.fixture_session()
		first = validate_items.validate_session(session, roots, manifest_root=roots[0],
			unconfigured_roots=unconfigured)
		second = validate_items.validate_session(session, roots, manifest_root=roots[0],
			unconfigured_roots=unconfigured)
		self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

	def test_every_finding_code_declares_a_severity_and_a_summary(self):
		for code, (severity, invariant, summary) in model.FINDING_CODES.items():
			with self.subTest(code):
				self.assertIn(severity, ("error", "warning"))
				self.assertTrue(summary)
				self.assertEqual(code.startswith("W-"), severity == "warning")
				if invariant is not None:
					self.assertRegex(invariant, r"^I-\d+$")

	def test_every_finding_code_is_exercised_somewhere_in_the_suite(self):
		"""A code nothing produces is a claim, not a check. 40 of the 46 come
		out of the golden corpus; the six that need a broken file, a hostile
		entry, a synthetic crash or a snapshot-less session are named by their
		unit tests."""
		here = os.path.dirname(os.path.abspath(__file__))
		golden = set(model.load_fixture("expected_validation.json")["counts"]["by_code"])
		source = ""
		for name in ("test_items.py", "test_validate_items.py"):
			with open(os.path.join(here, name), encoding="utf-8") as fh:
				source += fh.read()
		for code in model.FINDING_CODES:
			with self.subTest(code):
				self.assertTrue(code in golden or code in source,
					"{} is declared but nothing exercises it".format(code))

	def test_all_twenty_three_invariants_have_at_least_one_code(self):
		"""I-19 is WP2's — the memory-proposal shape and the self-test tag
		(a tag, never a removal). I-20 is the watch-item hit's grounding. I-21–I-23
		are G-SEC's: a grounded `nature: fix`, a grounded `required` edit, a
		grounded usage quote. An invariant with no code cannot be reported, so
		the numbering and the code table are asserted equal rather than kept in
		step by hand."""
		invariants = {inv for _, inv, _ in model.FINDING_CODES.values() if inv}
		self.assertEqual(invariants, {"I-{}".format(n) for n in range(1, 24)})

	def test_intel_brewfile_is_never_a_legal_value_anywhere_in_the_contract(self):
		"""Out of this tool entirely — not a candidate source,
		not a compatibility check, not a suggestion target, not on the page. The
		one place it may be named is the finding that rejects it."""
		self.assertEqual(model.FORBIDDEN_MANIFESTS, ("intel.Brewfile",))
		contract = model.contract()
		self.assertIn("intel.Brewfile", contract["findings"]["E-INTEL-BREWFILE"]["summary"])
		del contract["findings"]["E-INTEL-BREWFILE"]
		self.assertNotIn("intel.Brewfile", json.dumps(contract))

	def test_the_fixture_corpus_cites_no_intel_brewfile_except_as_the_rejected_case(self):
		session, _, _ = validate_items.fixture_session()
		nonconforming = os.path.join(session, "research", "01-nonconforming.json")
		for name in sorted(os.listdir(os.path.join(session, "research"))):
			path = os.path.join(session, "research", name)
			with open(path, "r", encoding="utf-8") as fh:
				text = fh.read()
			with self.subTest(name):
				if path == nonconforming:
					self.assertIn("intel.Brewfile", text)
				else:
					self.assertNotIn("intel.Brewfile", text)


class DegradationFixtureTests(unittest.TestCase):
	"""`contract/degradation.json` pins the fail-closed predicate as data. A
	published fixture that can go stale is worth no more than a paragraph, so
	every case is driven through the live functions here."""

	FIXTURE = model.load_fixture("degradation.json")

	def test_the_reason_tuple_and_code_map_are_the_published_ones(self):
		self.assertEqual(tuple(self.FIXTURE["reasons"]), model.DEGRADATION_REASONS)
		self.assertEqual(self.FIXTURE["content_losing_codes"], model.CONTENT_LOSING_CODES)

	def test_every_published_case_agrees_with_content_losing(self):
		for case in self.FIXTURE["cases"]:
			with self.subTest(str(case["tool"])[:60]):
				self.assertEqual(model.content_losing(case["tool"]), case["expect"],
					case.get("why", ""))

	def test_the_degradation_block_cases(self):
		for case in self.FIXTURE["degradation_block"]:
			with self.subTest(str(case["tool"])[:60]):
				self.assertEqual(model.compute_degradation(case["tool"]), case["expect"],
					case.get("why", ""))

	def test_every_reason_is_reachable_and_every_code_maps_to_a_reason(self):
		self.assertTrue(set(model.CONTENT_LOSING_CODES.values())
			<= set(model.DEGRADATION_REASONS))
		covered = {r for case in self.FIXTURE["cases"] for r in case["expect"]}
		self.assertEqual(covered, set(model.DEGRADATION_REASONS),
			"a reason no fixture case produces is a claim, not a check")


class ContractMirrorTests(unittest.TestCase):
	"""The contract's published blocks and the hand-written fixtures that
	mirror them must stay byte-equal — a fixture that can drift from the
	contract is two contracts."""

	def test_bucketing_json_mirrors_the_contract_block(self):
		fixture = model.load_fixture("bucketing.json")
		block = model.contract()["bucketing"]
		self.assertEqual(fixture["clause_order"], block["clause_order"])
		self.assertEqual(fixture["d2_routing"], block["d2_routing"])
		self.assertEqual(fixture["g_sec_routing"], block["g_sec_routing"])
		self.assertEqual(fixture["pre_accept"], model.PRE_ACCEPT_PREDICATE)
		self.assertEqual(fixture["pre_accept_bars"]["order"],
			list(model.PRE_ACCEPT_BARS))

	def test_degradation_json_mirrors_the_contract_block(self):
		fixture = model.load_fixture("degradation.json")
		block = model.contract()["degradation"]
		self.assertEqual(fixture["reasons"], block["reasons"])
		self.assertEqual(fixture["content_losing_codes"], block["content_losing_codes"])

	def test_stores_json_mirrors_the_contract_block(self):
		"""**Set equality, both directions**: every store in `MEMORY_STORES`
		appears in `stores.json` and every store in `stores.json` appears in
		`MEMORY_STORES`.

		A subset assertion here checked only the first direction, while this
		docstring claimed both — and the direction it left unchecked is the
		one that actually drifts. `stores.json` is the file a later pass edits
		when it pins a layout ahead of the code that will implement it; that is
		how this store set grew in the first place. A block landing there and
		never in `MEMORY_STORES` is the likelier mistake, not the rarer one.

		A store is identified **structurally** — an object carrying `path` —
		rather than by subtracting a list of prose key names. A list of names
		to ignore is one more thing to keep in sync with the file, it makes
		every new prose key a spurious failure, and the day someone quiets
		that failure by appending a *store* name to it, this test stops
		checking that store without saying so. The one gap a structural rule
		leaves — a dict that is neither prose nor a store — is closed by
		requiring everything else in the file to be a string.

		Within a store the check is deliberately one-directional: every key
		the contract publishes must match the fixture, and the fixture may
		carry more (`after_one_write`, `after_one_global_write` are golden
		states that exist to be driven against the live writers, and the
		contract does not publish them)."""
		fixture = model.load_fixture("stores.json")
		block = model.contract()["memory_stores"]
		fixture_stores = {name for name, value in fixture.items()
			if isinstance(value, dict) and "path" in value}
		for name, value in fixture.items():
			if name not in fixture_stores:
				self.assertIsInstance(value, str,
					f"stores.json key {name!r} is neither prose nor a store block "
					f"(an object carrying \"path\") — it would sit in the gap "
					f"between the two rules and be checked by neither")
		self.assertEqual(fixture_stores, set(block))
		for name, store in block.items():
			with self.subTest(name):
				for key, value in store.items():
					self.assertEqual(fixture[name][key], value)

	def test_every_content_losing_code_is_a_registered_finding(self):
		for code in model.CONTENT_LOSING_CODES:
			self.assertIn(code, model.FINDING_CODES)


class StoreLayoutTests(unittest.TestCase):
	"""`contract/stores.json` pins the on-disk layout of the memory stores and
	the golden state after one write of each (D4).

	**Three stores in two files.** Watch items and per-tool method notes are
	keyed by tool id; global method notes are the third store and live inside
	`method-notes.json` under a reserved colon-free key. Every one of the three
	is driven through its live `write_status.py` writer into a fresh temporary
	`XDG_STATE_HOME` and asserted equal to its golden — so "created on first
	use" is a measured property of the code, not a claim in
	a docstring."""

	FIXTURE = model.load_fixture("stores.json")

	def _run_writer(self, tmp, store, golden_key="after_one_write", command=None):
		"""Drive one store's writer into `tmp` as XDG_STATE_HOME; return
		(what landed on disk, the golden it must equal)."""
		import argparse
		import contextlib
		import io
		import write_status
		golden = self.FIXTURE[store][golden_key]
		(key,) = golden
		entry = golden[key][0]
		if command is None:
			command = {"watch-items.json": write_status.cmd_add_watch_item,
				"method-notes.json": write_status.cmd_add_method_note}[store]
		args = argparse.Namespace(tool_id=key, topic=entry["topic"], note=entry["note"])
		with unittest.mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
			with contextlib.redirect_stdout(io.StringIO()):
				command(args)
		with open(os.path.join(tmp, "tool-update-review", store), encoding="utf-8") as fh:
			return json.load(fh), golden

	def _expect(self, golden):
		import datetime
		today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
		return json.loads(json.dumps(golden).replace("<today>", today))

	def test_one_watch_item_write_produces_the_golden_state(self):
		import tempfile
		with tempfile.TemporaryDirectory() as tmp:
			got, golden = self._run_writer(tmp, "watch-items.json")
			self.assertEqual(got, self._expect(golden))

	def test_one_method_note_write_produces_the_golden_state(self):
		import tempfile
		with tempfile.TemporaryDirectory() as tmp:
			got, golden = self._run_writer(tmp, "method-notes.json")
			self.assertEqual(got, self._expect(golden))

	def test_one_global_method_note_write_produces_the_golden_state(self):
		import tempfile
		import write_status
		with tempfile.TemporaryDirectory() as tmp:
			got, golden = self._run_writer(tmp, "method-notes.json",
				golden_key="after_one_global_write",
				command=write_status.cmd_add_global_method_note)
			self.assertEqual(got, self._expect(golden))

	def test_a_fresh_state_home_holds_nothing_until_a_writer_runs(self):
		"""The other half of "created on first use": no store exists before
		one is written. A pre-seeded file would make the three tests above
		pass for the wrong reason."""
		import tempfile
		with tempfile.TemporaryDirectory() as tmp:
			self.assertFalse(os.path.exists(os.path.join(tmp, "tool-update-review")))
			self._run_writer(tmp, "watch-items.json")
			landed = os.listdir(os.path.join(tmp, "tool-update-review"))
			self.assertEqual(landed, ["watch-items.json"])

	def test_the_global_store_shares_the_per_tool_file_under_a_reserved_key(self):
		"""Three stores, two files — and the key that makes that safe. A
		reader who counts files and finds two must find the reason here
		rather than "fixing" the missing third one."""
		import write_status
		method = self.FIXTURE["method-notes.json"]
		self.assertEqual(method["global"]["key"], model.GLOBAL_METHOD_NOTE_KEY)
		self.assertEqual(write_status.items.GLOBAL_METHOD_NOTE_KEY,
			model.GLOBAL_METHOD_NOTE_KEY)
		# The whole safety argument in one assertion: a tool id always
		# carries a colon, the reserved key never does, so no tool-id lookup
		# can reach it and no tool id can be shadowed by it.
		self.assertNotIn(":", model.GLOBAL_METHOD_NOTE_KEY)
		self.assertEqual(method["global"]["writer"].split()[1], "add-global-method-note")
		self.assertNotIn("--tool-id", method["global"]["writer"])

	def test_the_two_stores_share_one_layout(self):
		"""Same entry shape, same state directory, same keying — the
		method-note writer is a rename of the watch-item writer, pinned so it
		implements this and nothing else."""
		watch, method = (self.FIXTURE["watch-items.json"],
			self.FIXTURE["method-notes.json"])
		self.assertEqual(watch["entry"], method["entry"])
		self.assertEqual(os.path.dirname(watch["path"]), os.path.dirname(method["path"]))
		self.assertEqual(watch["writer"].split()[1], "add-watch-item")
		self.assertEqual(method["writer"].split()[1], "add-method-note")
		self.assertEqual(watch["writer"].split()[2:], method["writer"].split()[2:])
		for golden in (watch["after_one_write"], method["after_one_write"]):
			for tool_id, entries in golden.items():
				self.assertIn(":", tool_id)
				for entry in entries:
					self.assertEqual(set(entry), {"topic", "note", "added_at"})
		for key, entries in method["after_one_global_write"].items():
			self.assertNotIn(":", key)
			for entry in entries:
				self.assertEqual(set(entry), {"topic", "note", "added_at"})


class BucketingFixtureTests(unittest.TestCase):
	"""`contract/bucketing.json` pins the clause order and the pre-accept
	predicate as a truth table. Each case is driven through the live
	`validate_items.compute_initial_bucket()` and `assemble.apply_pre_accept()`
	so the fixture cannot go stale against either layer."""

	FIXTURE = model.load_fixture("bucketing.json")

	def test_the_bar_order_is_the_published_one(self):
		self.assertEqual(tuple(self.FIXTURE["pre_accept_bars"]["order"]),
			model.PRE_ACCEPT_BARS)

	def test_clause_order_matches_the_functions_return_count(self):
		"""The fixture cannot pin a clause nobody wrote — and a sixth clause
		added without a fixture row is exactly the drift this file exists to
		catch."""
		import inspect
		source = inspect.getsource(validate_items.compute_initial_bucket)
		body = source.split('"""')[-1]  # strip the docstring
		returns = len(re.findall(r"^\s*return ", body, re.M))
		self.assertEqual(len(self.FIXTURE["clause_order"]), returns)

	def _drive(self, case):
		"""One row, in the validator's order: the tier (or the stored one
		the row stands for), then the bars, then the bucket — then the
		assembly half and the convergence half, which must agree."""
		import converge
		view = json.loads(json.dumps(case["view"]))
		view.setdefault("id", "brew:x")
		axes = case["axes"]
		view["risk_level"] = axes["risk_level"]
		view["impact"] = axes["impact"]
		view["bucket_inputs"] = {
			"has_security": axes["has_security"],
			"security_only": axes["security_only"],
			"impact": axes["impact"],
			"version_delta": view["version_delta"],
			"runnable": axes["runnable"],
		}
		view["security_tier"] = (case["stored_tier"] if "stored_tier" in case
			else model.security_tier(view))
		view["pre_accept_bars"] = model.pre_accept_bars(view)
		bucket = validate_items.compute_initial_bucket(
			view, axes["has_security"], axes["security_only"],
			axes["impact"], axes["risk_level"], axes["runnable"])
		view["initial_review_bucket"] = bucket
		# The assemble half: a minimal Tool with a synthesized-shaped baseline
		# (or, for the non-baseline row, a suggestion whose id does not claim
		# the baseline slot). As finalize_tool does, the tier and the bars
		# are COPIED from the view — apply_pre_accept refuses a tool nobody
		# computed them for (see test below).
		tool = dict(view, review_bucket=bucket)
		sug_id = "brew:x:upgrade" if case.get("baseline", True) else "brew:x:sug-1"
		sug = {"id": sug_id, "kind": "upgrade", "auto_runnable": case["auto_runnable"]}
		tool["suggestions"] = [sug]
		assemble.apply_pre_accept(tool)
		return view, bucket, sug["pre_accept"], converge.initial_pre_accept(view)

	def test_every_truth_table_row_holds_in_both_layers(self):
		for case in self.FIXTURE["cases"]:
			with self.subTest(case["name"]):
				view, bucket, pre_accept, eligible = self._drive(case)
				expect = case["expect"]
				self.assertEqual(model.content_losing(view),
					expect["content_losing"], case["name"])
				self.assertEqual(view["pre_accept_bars"], expect["bars"], case["name"])
				tier = view["security_tier"]
				valid = model.valid_security_tier(tier)
				self.assertEqual(tier["tier"] if valid else None, expect["tier"])
				self.assertEqual(model.security_priority(view), expect["priority"])
				self.assertEqual(bucket, expect["bucket"], case["name"])
				self.assertEqual(pre_accept, expect["pre_accept"], case["name"])
				# One predicate: convergence's view-level eligibility agrees
				# with assembly wherever the two can be compared (a baseline
				# whose auto_runnable is what the view says is runnable).
				if case.get("baseline", True) and \
						case["auto_runnable"] == case["axes"]["runnable"]:
					self.assertEqual(eligible, pre_accept, case["name"])

	def test_the_four_coherence_invariants_hold_on_every_row(self):
		"""§4.3: accepted tier ⟹ security_auto; P0/held ⟹ security_mixed or
		attention; attention with a tier ⟹ content-losing is a hold; and
		security_auto with no tier ⟹ clause 2b's conjuncts all hold."""
		for case in self.FIXTURE["cases"]:
			with self.subTest(case["name"]):
				view, bucket, _, _ = self._drive(case)
				tier = view["security_tier"]
				axes = case["axes"]
				if model.valid_security_tier(tier):
					if tier["tier"] in model.ACCEPTED_TIERS:
						self.assertEqual(bucket, "security_auto")
					else:
						self.assertIn(bucket, ("security_mixed", "attention"))
					if bucket == "attention":
						self.assertIn("content-losing", tier["holds"])
				elif tier is not None:
					self.assertNotEqual(bucket, "security_auto")
				elif bucket == "security_auto":
					self.assertTrue(axes["has_security"] and axes["security_only"]
						and axes["impact"] == "none" and axes["runnable"]
						and view["version_delta"] not in ("major", "unknown")
						and not view["pre_accept_bars"])

	def test_every_reason_and_hold_has_a_row(self):
		"""A reason or hold no row produces is a claim, not a check."""
		seen_reasons, seen_holds = set(), set()
		for case in self.FIXTURE["cases"]:
			view, _, _, _ = self._drive(case)
			tier = view["security_tier"]
			if model.valid_security_tier(tier):
				seen_reasons.update(tier["reasons"])
				seen_holds.update(tier["holds"])
		self.assertEqual(seen_reasons, set(model.TIER_REASONS))
		self.assertEqual(seen_holds, set(model.TIER_HOLDS))

	def test_apply_pre_accept_refuses_a_tool_with_no_computed_bars(self):
		"""The divergence-raises half of the finding-6 fix: the bars are
		computed once, in finalize_tool, from the validator's view. A caller
		that skips that step gets a KeyError, never a silent recomputation
		from assembled items — which can hold synthesized reaching security
		items the bucket never saw."""
		tool = {"id": "brew:x", "review_bucket": "routine", "risk_level": "low",
			"quarantine": [], "validator_error": None, "spec_violations": [],
			"items": [],
			"suggestions": [{"id": "brew:x:upgrade", "kind": "upgrade",
				"auto_runnable": True}]}
		with self.assertRaises(KeyError):
			assemble.apply_pre_accept(tool)


# ── 7. G-SEC: the security tier, its predicates and the one acceptance check ─
def _fix(item_id="brew:x#cve:CVE-2026-50001", **kw):
	item = {"id": item_id, "tags": ["security", "fix"], "severity": "notable",
		"change": {"citation": "Fix a heap overflow"},
		"security": {"rating": "medium", "rating_basis": "nvd", "nature": "fix"}}
	item.update(kw)
	return item


def _gview(items=(), suggestions=(), **kw):
	view = {"id": "brew:x", "source": "brew", "items": list(items),
		"suggestions": list(suggestions), "quarantine": [], "spec_violations": [],
		"validator_error": None, "config_status": {"state": "up_to_date"},
		"bucket_inputs": {"runnable": True}}
	view.update(kw)
	return view


class GSecTierTests(unittest.TestCase):
	"""`items.security_tier` — applicability, every reason and hold, the two
	axes kept apart (O2), and never raising."""

	def test_applicability_is_a_grounded_fix_or_vendor_unread(self):
		self.assertIsNone(model.security_tier(_gview([])))
		self.assertIsNotNone(model.security_tier(_gview([_fix()])))
		self.assertIsNotNone(model.security_tier(_gview(vendor_silent_categories=["security"])))
		# Security content that is not a positively identified fix: not G-SEC.
		for item in (
				_fix(security={"rating": "medium", "nature": "boundary"}),
				_fix(security={"rating": "medium", "nature": "unclear"}),
				_fix(security={"rating": "medium"}),                      # nature absent
				_fix(change={"citation": "  "}),                          # uncited
				_fix(change=None),
				_fix(tags=["fix"]),                                       # orphan block
				_fix(security=None)):
			with self.subTest(item=item):
				self.assertIsNone(model.security_tier(_gview([item])))

	def test_a_finding_source_is_never_g_sec(self):
		for source in model.NON_VERSION_SOURCES:
			self.assertIsNone(model.security_tier(_gview([_fix()], source=source)))
		self.assertEqual(assemble.NON_VERSION_SOURCES, frozenset(model.NON_VERSION_SOURCES))

	def test_p0_required_edit_needs_a_required_action(self):
		inc = {"id": "brew:x#slug:inc", "tags": ["breaking"], "severity": "incompatible",
			"local": {"direction": "reaches", "effect": "risk"}}
		edit = {"id": "brew:x:e", "kind": "edit", "requirement": "required",
			"serves_item_ids": ["brew:x#slug:inc"]}
		tier = model.security_tier(_gview([_fix(), inc], [edit]))
		self.assertEqual((tier["tier"], tier["priority"]), ("P0", "P0"))
		self.assertIn("required-edit", tier["reasons"])
		# the incompatible item is SERVED by the required edit, so it is not
		# also "incompatible-unfixed" (R5)
		self.assertNotIn("incompatible-unfixed", tier["reasons"])
		self.assertEqual(tier["ids"]["required-edit"], ["brew:x:e"])

	def test_p0_pinned_and_incompatible_unfixed(self):
		tier = model.security_tier(_gview([_fix()], pinned=True))
		self.assertEqual(tier["reasons"][0], "pinned")
		self.assertEqual(tier["ids"]["pinned"], [])
		inc = {"id": "brew:x#slug:inc", "tags": ["packaging"], "severity": "incompatible"}
		tier = model.security_tier(_gview([_fix(), inc]))
		self.assertEqual(tier["ids"]["incompatible-unfixed"], ["brew:x#slug:inc"])
		self.assertEqual(tier["tier"], "P0")

	def test_config_attention_and_edit_proposed_never_co_fire(self):
		"""§7.29 (R2 overridden): needs_attention with NO action suggestion is P1
		`config-attention`; with a proposed edit it is P1 `edit-proposed` only;
		with a required edit it is P0."""
		attention = {"state": "needs_attention"}
		tier = model.security_tier(_gview([_fix()], config_status=attention))
		self.assertEqual(tier["reasons"], ["config-attention", "fix"])
		self.assertEqual((tier["tier"], tier["priority"]), ("P1", "P1"))
		self.assertEqual(model.TIER_LABELS["config-attention"]["text"],
			"Accepted — config needs attention — no edit proposed")
		proposed = {"id": "brew:x:e", "kind": "edit", "requirement": "proposed"}
		tier = model.security_tier(_gview([_fix()], [proposed], config_status=attention))
		self.assertEqual(tier["reasons"], ["edit-proposed", "fix"])
		required = dict(proposed, requirement="required")
		tier = model.security_tier(_gview([_fix()], [required], config_status=attention))
		self.assertEqual(tier["priority"], "P0")
		self.assertNotIn("config-attention", tier["reasons"])
		# a memory proposal is not an action: config-attention still fires
		memory = {"id": "brew:x:m", "kind": "method-note"}
		tier = model.security_tier(_gview([_fix()], [memory], config_status=attention))
		self.assertEqual(tier["reasons"], ["config-attention", "fix"])

	def test_p1_outranks_p2(self):
		tier = model.security_tier(_gview([_fix(), {"id": "brew:x#slug:b",
			"tags": ["breaking"], "severity": "info"}],
			[{"id": "brew:x:e", "kind": "edit", "requirement": "proposed"}]))
		self.assertEqual(tier["reasons"], ["edit-proposed", "fix-with-breaking", "fix"])
		self.assertEqual(tier["priority"], "P1")

	def test_relevant_fix_needs_a_recorded_usage_entry_and_a_benign_effect(self):
		entry = {"path": "a.conf", "role": "usage", "quote": "x"}
		record = [{"entry": entry, "matched_lines": [1]}]
		item = _fix(local={"direction": "reaches", "effect": "benefit", "evidence": [entry]})
		tier = model.security_tier(_gview([item], usage_evidence=record))
		self.assertEqual(tier["reasons"], ["relevant-fix", "fix"])
		self.assertEqual(model.usage_item_ids(_gview([item], usage_evidence=record)),
			[item["id"]])
		# no record: nothing grounded, nothing confirmed
		self.assertEqual(model.security_tier(_gview([item]))["reasons"], ["fix"])
		# a forged entry (different quote) matches no record
		forged = _fix(local={"direction": "reaches", "effect": "benefit",
			"evidence": [dict(entry, quote="y")]})
		self.assertEqual(model.security_tier(_gview([forged], usage_evidence=record))
			["reasons"], ["fix"])
		# not reaching: no confirmation
		unclear = _fix(local={"direction": "unclear", "effect": "benefit", "evidence": [entry]})
		self.assertEqual(model.security_tier(_gview([unclear], usage_evidence=record))
			["reasons"], ["fix"])
		# the fix's own effect is risk: no relevant-fix — and it is held
		risky = _fix(local={"direction": "reaches", "effect": "risk", "evidence": [entry]})
		tier = model.security_tier(_gview([risky], usage_evidence=record))
		self.assertEqual(tier["reasons"], ["fix"])
		self.assertEqual(tier["holds"], ["security-item-risk"])
		# hostile records ground nothing and never raise
		for hostile in (None, "x", 42, [None], [{"entry": "x"}], {"entry": entry}):
			self.assertEqual(model.security_tier(_gview([item], usage_evidence=hostile))
				["reasons"], ["fix"])

	def test_fix_with_breaking_is_any_breaking_item_at_any_severity(self):
		for severity in ("info", "notable", "warning"):
			tier = model.security_tier(_gview([_fix(), {"id": "brew:x#slug:b",
				"tags": ["breaking"], "severity": severity}]))
			self.assertEqual(tier["reasons"], ["fix-with-breaking", "fix"])

	def test_fix_with_risk_is_a_non_security_risk_item(self):
		feature = {"id": "brew:x#slug:f", "tags": ["feature"], "severity": "notable",
			"local": {"direction": "reaches", "effect": "risk"}}
		self.assertEqual(model.security_tier(_gview([_fix(), feature]))["reasons"],
			["fix-with-risk", "fix"])
		boundary = {"id": "brew:x#slug:s", "tags": ["security"], "severity": "notable",
			"security": {"nature": "boundary"}, "local": {"direction": "reaches", "effect": "risk"}}
		tier = model.security_tier(_gview([_fix(), boundary]))
		self.assertEqual(tier["reasons"], ["fix"])
		self.assertEqual(tier["holds"], ["security-item-risk"])

	def test_vendor_unread_is_p2(self):
		tier = model.security_tier(_gview(vendor_silent_categories=["security"]))
		self.assertEqual(tier["reasons"], ["vendor-unread"])
		self.assertEqual(tier["fix_item_ids"], [])
		self.assertEqual(tier["tier"], "P2")

	def test_every_hold_fires_and_holds_never_lower_priority(self):
		"""O2 / R1: acceptance hold and display priority are separate axes."""
		breaking = {"id": "brew:x#slug:b", "tags": ["breaking"], "severity": "info"}
		cases = {
			"content-losing": dict(quarantine=[{"field": "items", "value": 1}]),
			"watch-hit": dict(items=[_fix(), breaking, {"id": "brew:x#slug:w",
				"tags": ["fix"], "watch_hit": {"topic": "t"}}]),
			"enum-invalid": dict(config_status={"state": "stale"}),
			"container-unreadable": dict(items=[_fix(), breaking, {"id": "brew:x#slug:c",
				"tags": ["fix"], "local": "risk"}]),
			"research-incomplete": dict(research_error="timed out"),
			"not-runnable": dict(bucket_inputs={"runnable": False}),
			"forced-conservative": dict(forced_conservative={"code": "E-GATE-UNREASONED"}),
		}
		for hold, extra in cases.items():
			with self.subTest(hold):
				kw = {"items": [_fix(), breaking]}
				kw.update(extra)
				tier = model.security_tier(_gview(**kw))
				self.assertIn(hold, tier["holds"])
				self.assertEqual(tier["priority"], "P2")
				self.assertEqual(tier["tier"], "held")
				self.assertTrue(model.valid_security_tier(tier))
		# a held P0 stays P0 — never "held"
		inc = {"id": "brew:x#slug:inc", "tags": ["packaging"], "severity": "incompatible"}
		tier = model.security_tier(_gview([_fix(), inc], research_error="x"))
		self.assertEqual((tier["tier"], tier["priority"]), ("P0", "P0"))
		self.assertEqual(tier["holds"], ["research-incomplete"])

	def test_enum_invalid_reads_every_tier_input(self):
		cases = [
			[_fix(severity="critical")],
			[_fix(local={"direction": "reachs", "effect": "none"})],
			[_fix(local={"direction": "reaches", "effect": "risks"})],
			[_fix(security={"nature": "fix", "rating": "high"}),
				_fix("brew:x#slug:n", security={"nature": "fixed"})],
			[_fix(local={"direction": "unclear", "effect": "none",
				"evidence": [{"path": "a", "role": "uses"}]})],
		]
		for items in cases:
			with self.subTest(items=items):
				self.assertTrue(model.enum_invalid(_gview(items)))
		self.assertTrue(model.enum_invalid(_gview([_fix()], [{"kind": "edits"}])))
		self.assertTrue(model.enum_invalid(_gview([_fix()], [{"kind": ["edit"]}])))
		self.assertTrue(model.enum_invalid(_gview([_fix()],
			[{"kind": "edit", "requirement": "mandatory"}])))
		self.assertTrue(model.enum_invalid(_gview([_fix()], config_status={"state": 3})))
		# absent is no claim, and `requirement` on a memory kind is read by nothing
		self.assertFalse(model.enum_invalid(_gview([_fix(severity=None)])))
		self.assertFalse(model.enum_invalid(_gview([_fix()],
			[{"kind": "method-note", "requirement": "mandatory"}])))
		self.assertFalse(model.enum_invalid(_gview([_fix()], config_status={})))

	def test_container_unreadable_reads_every_container(self):
		for field, value in (("local", "risk"), ("security", ["x"]), ("change", "c"),
				("watch_hit", "a bare string"), ("tags", "security")):
			with self.subTest(field):
				item = {"id": "brew:x#slug:c", "tags": ["fix"], field: value}
				self.assertTrue(model.container_unreadable(_gview([item])))
		self.assertTrue(model.container_unreadable(_gview([],
			[{"kind": "edit", "serves": "cve:X"}])))
		self.assertFalse(model.container_unreadable(_gview([],
			[{"kind": "watch-item", "serves": "cve:X"}])))
		self.assertFalse(model.container_unreadable(_gview([{"id": "a", "local": None}])))

	def test_security_tier_never_raises(self):
		hostile = [None, "a string", 42, True, [], {}, ["x"], [None], [42], [[1]],
			{"k": "v"}, [{"k": ["v"]}], [{"id": []}], "", [""], {"a": {"b": {"c": 1}}}]
		keys = ("items", "suggestions", "vendor_silent_categories", "config_status",
			"pinned", "research_error", "validator_error", "quarantine",
			"spec_violations", "bucket_inputs", "usage_evidence", "forced_conservative",
			"id", "source")
		for key in keys:
			for shape in hostile:
				view = _gview([_fix(), {"id": [], "tags": [{"x": 1}], "severity": [1],
					"local": {"direction": {}, "effect": [], "evidence": [{"role": []}]},
					"security": {"nature": ["fix"]}}],
					[{"kind": {"a": 1}, "requirement": [], "serves": {}, "serves_item_ids": [[1]]}])
				view[key] = shape
				tier = model.security_tier(view)
				self.assertTrue(tier is None or model.valid_security_tier(tier),
					(key, shape, tier))
				model.pre_accept_bars(dict(view, security_tier=tier))
		for shape in hostile:
			if not isinstance(shape, dict):
				self.assertIsNone(model.security_tier(shape))


class GSecRequirementTests(unittest.TestCase):
	"""`items.suggestion_requirement` over CURRENT items (§2.2)."""

	INC = {"id": "brew:x#slug:inc", "severity": "incompatible"}
	WARN = {"id": "brew:x#slug:w", "severity": "warning"}

	def read(self, sug, *items):
		return model.suggestion_requirement(sug, {i["id"]: i for i in items})

	def test_the_reading_table(self):
		self.assertEqual(self.read({}), "proposed")                       # absent
		self.assertEqual(self.read({"requirement": None}), "proposed")
		self.assertEqual(self.read({"requirement": "proposed"}), "proposed")
		self.assertEqual(self.read({"requirement": "required"}), "required")
		for bad in ("mandatory", "", ["required"], 1, True, {"a": 1}):
			with self.subTest(bad=bad):
				self.assertEqual(self.read({"requirement": bad}), "required")

	def test_an_ungrounded_required_still_reads_required(self):
		self.assertEqual(self.read({"requirement": "required", "serves_item_ids": []}),
			"required")
		self.assertEqual(self.read({"requirement": "required",
			"serves_item_ids": ["brew:x#slug:w"]}, self.WARN), "required")

	def test_a_proposed_edit_serving_an_incompatible_item_reads_required(self):
		sug = {"requirement": "proposed", "serves_item_ids": ["brew:x#slug:inc"]}
		self.assertEqual(self.read(sug, self.INC), "required")
		# the CURRENT severity decides: rerated to warning, it is proposed again
		self.assertEqual(self.read(sug, dict(self.INC, severity="warning")), "proposed")
		# the served item is gone (deleted/merged away): it links nothing
		self.assertEqual(self.read(sug), "proposed")
		# absent requirement is proposed, so the contradiction applies too
		self.assertEqual(self.read({"serves_item_ids": ["brew:x#slug:inc"]}, self.INC),
			"required")


class GSecValidityTests(unittest.TestCase):
	def test_the_default_is_a_valid_held_p3(self):
		self.assertTrue(model.valid_security_tier(model.TIER_UNCOMPUTED))
		self.assertEqual(model.TIER_UNCOMPUTED["tier"], "held")

	def test_every_computed_tier_is_valid(self):
		tier = model.security_tier(_gview([_fix()]))
		self.assertTrue(model.valid_security_tier(tier))

	def test_malformed_tiers_are_invalid(self):
		good = model.security_tier(_gview([_fix(), {"id": "brew:x#slug:b",
			"tags": ["breaking"], "severity": "info"}], research_error="x"))
		self.assertTrue(model.valid_security_tier(good))
		bad = [None, {}, [], "P2", 3]
		for mutate in (
				lambda t: t.__setitem__("tier", "P2"),              # held ≠ P2
				lambda t: t.__setitem__("priority", "P3"),          # not the highest level
				lambda t: t.__setitem__("priority", "P9"),
				lambda t: t.__setitem__("reasons", ["fix", "fix-with-breaking"]),  # order
				lambda t: t.__setitem__("holds", ["no-such-hold"]),
				lambda t: t["ids"].pop("fix"),
				lambda t: t.__setitem__("fix_item_ids", "x"),
				lambda t: t.__setitem__("extra", 1)):
			t = json.loads(json.dumps(good))
			mutate(t)
			bad.append(t)
		for tier in bad:
			with self.subTest(tier=tier):
				self.assertFalse(model.valid_security_tier(tier))
				self.assertIsNone(model.security_priority({"security_tier": tier}))
				if tier is not None:
					self.assertEqual(model.pre_accept_bars({"security_tier": tier}),
						["tier-uncomputed"])

	def test_the_labels_cover_every_reason_with_text_and_glyph(self):
		self.assertEqual(set(model.TIER_LABELS), set(model.TIER_REASONS))
		for code, label in model.TIER_LABELS.items():
			self.assertTrue(label["text"] and label["glyph"], code)

	def test_the_priority_rank(self):
		ranks = [model.priority_rank(p) for p in ("P0", "P1", "P2", "P3", None, "x", [])]
		self.assertEqual(ranks, [4, 3, 2, 1, 0, 0, 0])


class AcceptsBaselineTests(unittest.TestCase):
	"""`items.accepts_baseline` — one normalized input contract (§4.4)."""

	def _tool(self, tier="compute", **kw):
		view = _gview([_fix()])
		view["security_tier"] = model.security_tier(view) if tier == "compute" else tier
		view["pre_accept_bars"] = model.pre_accept_bars(view)
		view["review_bucket"] = "security_auto"
		view["risk_level"] = "elevated"
		view.update(kw)
		return view

	def test_an_accepted_tier_is_accepted_at_elevated_risk(self):
		self.assertTrue(model.accepts_baseline(self._tool()))

	def test_each_required_key_missing_raises_independently(self):
		for key in ("security_tier", "pre_accept_bars"):
			with self.subTest(key):
				tool = self._tool()
				del tool[key]
				with self.assertRaises(KeyError):
					model.accepts_baseline(tool)

	def test_none_takes_the_pre_g_sec_predicate(self):
		tool = self._tool(tier=None, risk_level="low", review_bucket="security_auto",
			pre_accept_bars=[])
		self.assertTrue(model.accepts_baseline(tool))
		self.assertFalse(model.accepts_baseline(dict(tool, risk_level="elevated")))
		self.assertFalse(model.accepts_baseline(dict(tool, review_bucket="attention")))
		view = dict(tool)
		del view["review_bucket"]
		view["initial_review_bucket"] = "attention"
		self.assertFalse(model.accepts_baseline(view))

	def test_malformed_tiers_are_never_accepted(self):
		"""At LOW risk in security_auto, so the non-G-SEC branch WOULD accept:
		a falsy malformed tier (`{}`, `[]`) that took that branch is caught."""
		for tier in ({}, [], "P2", 3, True, {"tier": "P3"},
				dict(model.TIER_UNCOMPUTED, tier="P3")):
			with self.subTest(tier=tier):
				self.assertFalse(model.accepts_baseline(self._tool(tier=tier,
					pre_accept_bars=[], risk_level="low")))
		self.assertTrue(model.accepts_baseline(self._tool(tier=None, pre_accept_bars=[],
			risk_level="low")), "the control: None takes the pre-G-SEC branch and accepts")

	def test_bars_content_losing_and_forcing_each_refuse(self):
		self.assertFalse(model.accepts_baseline(self._tool(pre_accept_bars=["watch-hit"])))
		for bars in ("", None, {}, "x", 0):
			self.assertFalse(model.accepts_baseline(self._tool(pre_accept_bars=bars)))
		self.assertFalse(model.accepts_baseline(self._tool(
			quarantine=[{"field": "items", "value": 1}])))
		self.assertFalse(model.accepts_baseline(self._tool(
			forced_conservative={"code": "E-GATE-UNREASONED"})))

	def test_held_and_p0_tiers_are_not_accepted(self):
		held = self._tool()
		held["security_tier"] = model.security_tier(dict(held, research_error="x"))
		self.assertFalse(model.accepts_baseline(held))
		pinned = self._tool()
		pinned["security_tier"] = model.security_tier(dict(pinned, pinned=True))
		self.assertFalse(model.accepts_baseline(pinned))


class UsageFileKindTests(unittest.TestCase):
	def test_kinds_from_the_path(self):
		for path, kind in (("Brewfile", "brewfile"), ("macos-setup/Brewfile", "brewfile"),
				(".tool-versions", "tool-versions"), ("mise.toml", "mise-toml"),
				(".mise.toml", "mise-toml"), ("mise.local.toml", "mise-toml"),
				("dotfiles/config/mise/config.toml", "mise-toml"),
				("config.toml", "other"), ("tasks/install.sh", "shell"),
				("dotfiles/home/.bash_profile", "shell"), (".pg_service.conf", "other"),
				(None, "other"), (42, "other")):
			with self.subTest(path):
				self.assertEqual(model.usage_file_kind(path), kind)

	def test_every_pattern_carries_a_name_slot_and_a_known_kind(self):
		for spec in model.INSTALL_DECLARATION_PATTERNS:
			self.assertIn("{name}", spec["pattern"] + (spec["section"] or ""))
			self.assertIn(spec["kind"], ("brewfile", "tool-versions", "mise-toml", "shell"))


if __name__ == "__main__":
	unittest.main()
