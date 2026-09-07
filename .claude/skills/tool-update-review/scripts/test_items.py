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
   HANDOFF.md §8 measured the cost of not having it: two implementer tracks
   against a pinned *field* contract drifted on ordering and had to be
   reconciled by hand. Every tier of the sort key is asserted, and the
   published fixture is asserted against the code so it cannot go stale.
4. The two CVE rank tables, asserted tier for tier against `assemble.py`'s
   private copies. Those two tables are the single most drift-prone thing in
   the contract and they are NOT interchangeable.
5. Fixture agreement — `contract.json` is generated, so the tests assert the
   checked-in copy still matches what the code produces. A published fixture
   that can go stale is worth no more than a paragraph.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble  # noqa: E402
import items as model  # noqa: E402


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
	"""HANDOFF.md §8 — pin the shared comparator, not just the field names."""

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
	one). These assert against assemble.py's private copies so the producer and
	the consumer of a security card cannot drift apart."""

	def test_the_worse_wins_table_matches_assembly(self):
		self.assertEqual(model.CVE_WORSE_RANK, assemble._CVE_SEVERITY_RANK)

	def test_the_display_ordering_table_matches_assembly(self):
		self.assertEqual(model.CVE_ORDER_RANK, assemble._NOTABLE_SEVERITY_RANK)

	def test_the_item_severity_ranks_match_assembly(self):
		self.assertEqual(model.SEVERITY_RANK, assemble._SEVERITY_RANK)

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


# ── 5. Fixture agreement ────────────────────────────────────────────────────
class PublishedFixtureTests(unittest.TestCase):
	"""A published fixture that can go stale is worth no more than a paragraph.
	Regenerate with `python3 contract/regenerate.py`, then read the diff."""

	def test_contract_json_is_what_the_code_produces(self):
		self.assertEqual(model.load_fixture("contract.json"), model.contract())

	def test_every_finding_code_declares_a_severity_and_a_summary(self):
		for code, (severity, invariant, summary) in model.FINDING_CODES.items():
			with self.subTest(code):
				self.assertIn(severity, ("error", "warning"))
				self.assertTrue(summary)
				self.assertEqual(code.startswith("W-"), severity == "warning")
				if invariant is not None:
					self.assertRegex(invariant, r"^I-\d+$")

	def test_all_eighteen_invariants_have_at_least_one_code(self):
		invariants = {inv for _, inv, _ in model.FINDING_CODES.values() if inv}
		self.assertEqual(invariants, {"I-{}".format(n) for n in range(1, 19)})

	def test_intel_brewfile_is_never_a_legal_value_anywhere_in_the_contract(self):
		"""REDESIGN.md §B1: out of this tool entirely — not a candidate source,
		not a compatibility check, not a suggestion target, not on the page. The
		one place it may be named is the finding that rejects it."""
		self.assertEqual(model.FORBIDDEN_MANIFESTS, ("intel.Brewfile",))
		contract = model.contract()
		self.assertIn("intel.Brewfile", contract["findings"]["E-INTEL-BREWFILE"]["summary"])
		del contract["findings"]["E-INTEL-BREWFILE"]
		self.assertNotIn("intel.Brewfile", json.dumps(contract))


if __name__ == "__main__":
	unittest.main()
