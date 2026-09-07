#!/usr/bin/env python3
"""
test_research_guidelines.py — the checkable claims in the per-tool checker's
guidelines. Usage: python3 test_research_guidelines.py [-v]

Stdlib `unittest` only, same constraint as the other suites here.

**Why a test suite over prose.** Everything a research subagent is told lives
in `references/research.md` and `references/research-prompt-template.md`, and
the measured history is that prose rules drift silently: the §Watch Items
(Proposing) text was byte-identical across three runs that produced 11, 5 and 8
proposals, and the single strongest instruction in the last run existed only in
an untracked scratch file. `REDESIGN.md` §A's corollary — *"anything living in
a prompt will evaporate"* — is the reason this file exists. Every assertion
below is a claim the guidelines make that can be checked mechanically, so a
later edit that quietly removes one fails a test instead of a run.

These are text assertions, deliberately. They do not check that an agent obeys
the guidelines — nothing here can — only that the instruction is still in the
file an agent reads.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REFERENCES = os.path.join(SKILL, "references")


def read(*parts):
	with open(os.path.join(SKILL, *parts), encoding="utf-8") as fh:
		return fh.read()


# What a research subagent actually reads: Part 2 of research.md in full, the
# filled-in template, and the schema shapes it writes against.
RESEARCH = read("references", "research.md")
TEMPLATE = read("references", "research-prompt-template.md")
SCHEMAS = read("references", "schemas.md")
SKILL_MD = read("SKILL.md")

CHECKER_FACING = {
	"research.md": RESEARCH,
	"research-prompt-template.md": TEMPLATE,
	"schemas.md": SCHEMAS,
}


def lines_mentioning(text, needle):
	return [ln for ln in text.splitlines() if needle in ln]


# ── intel.Brewfile (REDESIGN.md B1, criterion 20) ───────────────────────────
class IntelBrewfileTests(unittest.TestCase):
	"""B1 puts the Intel manifest out of this tool entirely. The last run
	produced seven `target_files` entries pointing at it, so silence is not
	enough — the prompt said to scan it. What a checker-facing document may
	still do is forbid it; what it may not do is send anyone to look."""

	INSTRUCTIONAL = re.compile(
		r"\(Brewfile, intel\.Brewfile"      # the old scan-paths list
		r"|vs\.? `?intel\.Brewfile"          # "decide between the two manifests"
		r"|\"intel\.Brewfile\"\s*$"          # an example value in a schema block
	)

	def test_no_checker_facing_document_sends_anyone_to_the_intel_manifest(self):
		for name, text in sorted(CHECKER_FACING.items()):
			for line in lines_mentioning(text, "intel.Brewfile"):
				with self.subTest(name + ": " + line.strip()[:60]):
					self.assertNotRegex(line, self.INSTRUCTIONAL)

	def test_every_surviving_mention_is_a_prohibition(self):
		"""An explicit exclusion beats silence here: an agent scanning
		macos-setup finds the file whether or not we named it, and I-17 exists
		because the last run cited it seven times."""
		forbidding = ("out of this tool entirely", "do not read it", "never reaches apply")
		for name, text in sorted(CHECKER_FACING.items()):
			for line in lines_mentioning(text, "intel.Brewfile"):
				with self.subTest(name):
					self.assertTrue(any(f in line for f in forbidding)
						or any(f in text[max(0, text.index(line)):][:600] for f in forbidding),
						"unexplained mention: " + line.strip())

	def test_the_relevancy_scan_list_names_only_the_live_manifest(self):
		scan = RESEARCH[RESEARCH.index("### Relevancy Is the Point"):][:600]
		self.assertIn("Brewfile", scan)
		self.assertNotIn("intel.Brewfile", scan)


# ── the orphans (REDESIGN.md A's corollary, criterion 19) ───────────────────
class OrphanedInstructionTests(unittest.TestCase):
	"""Seven instructions ran the last review and lived only in an untracked
	scratch file, `scratch/research-0828b.js`. `REDESIGN.md` §A: *"anything
	living in a prompt will evaporate."* Each test below is the home one of
	them now has.

	One is deliberately absent: the fleet-wide watch-item quota at `:38`
	(*"across the whole fleet the expected total is one or two ... assume
	someone else is covering the marginal case"*). §C4 forbids it and
	criterion 14 checks that it stayed out. `NoVolumeTargetTests` asserts its
	absence."""

	def test_the_read_only_discipline_governs_every_checker(self):
		"""It existed only scoped to bespoke-setup testing; the general form —
		and the session-dir write restriction — were in scratch alone."""
		section = RESEARCH[RESEARCH.index("### What You May Touch"):]
		section = section[:section.index("### Headliners")]
		self.assertIn("non-destructive and\n  read-only", section)
		for banned in ("setup.sh", "tasks/*.sh", "dotfiles/bootstrap.sh"):
			self.assertIn(banned, section)
		self.assertIn("session directory except your own output file", section)

	def test_the_read_only_discipline_is_not_only_in_the_bespoke_section(self):
		"""The point of the port: it governs every checker, including the ones
		that never touch a `tasks/*.sh` function."""
		general = RESEARCH.index("### What You May Touch")
		bespoke = RESEARCH.index("### Bespoke `tasks/*.sh` Setup Testing")
		self.assertLess(general, bespoke)

	def test_vendored_skill_content_is_a_named_false_positive(self):
		"""`grep -rn agent-skills` over the skill returned nothing before this."""
		section = RESEARCH[RESEARCH.index("### Word-Boundary Grep Rule"):]
		section = section[:section.index("### Spawning")]
		self.assertIn("dotfiles/config/agent-skills", section)
		self.assertIn("almost never a real touchpoint", section)
		# ...and the prompt a checker actually receives says so too.
		self.assertIn("dotfiles/config/agent-skills", TEMPLATE)

	def test_the_tiering_decision_is_recorded_not_just_the_heuristic(self):
		"""research.md documented the heuristic; the decision a given run made
		was recorded nowhere, so a scoped run and a full run produced
		indistinguishable session dirs."""
		self.assertIn('"tier"', SCHEMAS)
		self.assertIn('"scope"', SCHEMAS)
		self.assertIn("scoped run and a full run produce indistinguishable", RESEARCH)

	def test_touchpoints_reach_the_prompt_as_a_generated_placeholder(self):
		"""The per-group touchpoint hints were hand-typed. The touchpoint half
		is derivable from the word-boundary grep; the nomination half is what
		§Writing Hypotheses bans."""
		self.assertIn("{{TOUCHPOINTS}}", TEMPLATE)
		self.assertIn("never typed by hand", TEMPLATE)

	def test_the_baseline_upgrade_suggestion_is_still_research_s_to_not_author(self):
		"""Listed as an orphan by the design doc, but its grep was
		case-sensitive: the instruction was already here, spelled
		"Do **not** author". Asserted so it stays."""
		flat = " ".join(RESEARCH.split())
		self.assertIn('Do **not** author the plain "upgrade this tool" suggestion', flat)

	def test_every_template_placeholder_has_a_row_in_the_table(self):
		"""A placeholder nobody documents is a placeholder that gets filled
		with whatever the orchestrator remembers — which is how the hand-built
		prompt happened."""
		body, table = TEMPLATE.split("## Placeholder reference", 1)
		used = set(re.findall(r"\{\{[A-Z_]+\}\}", body))
		documented = set(re.findall(r"\{\{[A-Z_]+\}\}", table))
		self.assertEqual(used - documented, set())


# ── the three stores (REDESIGN.md L1, criterion 16) ─────────────────────────
class ThreeStoresTests(unittest.TestCase):
	"""The volume disagreement that stalled this was a category error. There is
	no single answer to "how many" because there are three different things,
	and a guideline that conflates them produces last run's mess: eight
	proposals, of which two were method notes in a watch item's container."""

	SECTION = "### Standing Notes: Three Stores"

	def section(self):
		start = RESEARCH.index(self.SECTION)
		return RESEARCH[start:RESEARCH.index("### Research-Method Notes vs Watch Items")]

	def test_all_three_stores_are_named_with_their_scope_and_volume(self):
		text = self.section()
		for phrase in ("Global method notes", "Per-tool method notes", "Watch items"):
			self.assertIn(phrase, text)
		self.assertIn("across many tools", text)
		self.assertIn("**rare**", text)
		self.assertIn("**many**", text)

	def test_rare_is_justified_by_definition_and_not_by_a_number(self):
		"""A global note is rare because holding across many tools is its entry
		condition. Stated any other way it reads as a budget, and a budget is
		what criterion 14 forbids."""
		text = self.section()
		self.assertIn("by definition", text)
		self.assertNotRegex(text, r"\b(?:at most|no more than|up to)\s+\w+\s+(?:notes?|items?)")

	def test_the_agent_is_told_to_route_at_the_point_of_writing(self):
		text = self.section()
		self.assertIn("Route at the point of writing", text)
		# Two ordered questions, not a table to interpret.
		self.assertIn("how to research, or about what to report", text)
		self.assertIn("does it hold for this tool, or for many", text)

	def test_the_routing_test_precedes_the_watch_item_bar(self):
		"""It has to run first: a topic no changelog can match is filed in a
		store whose only mechanism cannot reach it."""
		self.assertLess(RESEARCH.index("### Research-Method Notes vs Watch Items"),
			RESEARCH.index("### Watch Items (Proposing)"))

	def test_the_routing_test_is_stated_as_one_answerable_question(self):
		text = RESEARCH[RESEARCH.index("### Research-Method Notes vs Watch Items"):]
		text = text[:text.index("### Writing a Research-Method Note")]
		self.assertIn("Could a future release's published text plausibly contain words that match",
			" ".join(text.split()))
		self.assertIn("**No**", text)
		self.assertIn("**Yes**", text)
		# Both worked failures, named, with the sentence that gives them away.
		self.assertIn("brew:iproute2mac", text)
		self.assertIn("brew:nnn", text)
		self.assertIn("Believe it.", text)

	def test_routing_is_stated_not_to_be_dropping(self):
		"""L7's companion: moving a note between stores keeps the knowledge. An
		agent that reads routing as rejection stops writing them."""
		text = RESEARCH[RESEARCH.index("### Research-Method Notes vs Watch Items"):]
		self.assertIn("Routing is not dropping", text)

	def test_a_method_note_must_name_a_failure_rather_than_predict_one(self):
		text = RESEARCH[RESEARCH.index("### Writing a Research-Method Note"):]
		text = text[:text.index("### Watch Items (Reading)")]
		self.assertIn("name a failure, not predict one", text)
		for field in ("method_topic", "method_note", "rationale"):
			self.assertIn(field, text)

	def test_method_notes_are_read_before_research_begins(self):
		"""A note saying "read CHANGELOG.md, the release page is boilerplate"
		is worthless delivered after the release page has been read."""
		text = RESEARCH[RESEARCH.index("### Watch Items (Reading)"):]
		text = text[:text.index("### Watch Items (Proposing)")]
		self.assertIn("read first, before you look anything up", text)

	def test_both_stores_reach_the_checker_through_the_prompt(self):
		"""REDESIGN.md I4: the per-tool agent is GIVEN its watch items rather
		than sent to find them."""
		self.assertIn("{{STANDING_NOTES}}", TEMPLATE)
		self.assertIn("{{STANDING_NOTES}}", RESEARCH)
		self.assertIn("method-notes.json", RESEARCH)

	def test_the_method_note_kind_is_in_the_schema_the_checker_writes_against(self):
		self.assertIn('kind: "method-note"', SCHEMAS)
		self.assertIn("method_topic", SCHEMAS)
		self.assertIn("method_note", SCHEMAS)


if __name__ == "__main__":
	unittest.main()
