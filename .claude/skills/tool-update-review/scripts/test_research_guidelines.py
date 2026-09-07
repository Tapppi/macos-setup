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


# ── the self-test tags, never removes (REDESIGN.md L7, criterion 17) ────────
class SelfTestTests(unittest.TestCase):
	"""§E3 asked for a self-test; §L7 closed the one lossy point in it. A
	failing proposal is still written, tagged with the failing limb and the
	agent's own reason, and convergence verifies that dropping it is
	appropriate. A proposal the agent never writes is one convergence cannot
	restore."""

	HEAD = "### Before You Propose a Standing Note: the Self-Test"

	def section(self):
		start = RESEARCH.index(self.HEAD)
		return RESEARCH[start:RESEARCH.index("### There Is No Volume Target")]

	def test_the_self_test_tags_and_never_deletes(self):
		text = self.section()
		self.assertIn("Nothing here deletes a proposal", text)
		self.assertIn("self_test_failed", text)
		self.assertIn("Never suppress a proposal because it failed a question here", text)

	def test_the_asymmetry_is_stated_as_the_reason(self):
		"""Without the reason an agent optimises for a short list, which is the
		behaviour that produced 0 accepted proposals out of 24."""
		self.assertIn("cannot\nrestore", self.section())

	def test_every_limb_in_the_vocabulary_is_reachable_from_a_question(self):
		"""A limb the guidelines never tell anyone to use is a limb nothing
		emits; a question with no limb is a failure with nowhere to go."""
		import sys
		sys.path.insert(0, HERE)
		import items as model
		text = self.section()
		for limb in model.SELF_TEST_LIMBS:
			with self.subTest(limb):
				self.assertIn('"' + limb + '"', text)

	def test_routing_is_the_one_answer_that_moves_rather_than_tags(self):
		text = self.section()
		self.assertIn("Q1 — ROUTING", text)
		self.assertIn("route* rather than a tag", text)

	def test_the_unknown_config_status_hole_is_named(self):
		"""22 of 78 tools had no prior handling, so Q2's quote limb is satisfied
		by "there is no prior handling to re-verify" — true, and evidence for
		nothing. Left unsaid, the test has a hole over a quarter of the fleet."""
		text = self.section()
		self.assertIn("22 of 78", text)
		self.assertIn("Do not read a vacuous pass as a pass", text)

	def test_q3_asks_about_the_changing_thing_not_any_cited_file(self):
		"""Read loosely, this clause drops the best proposal in the set:
		claudebar cites tasks/config.sh:399, which is not the thing that could
		change."""
		text = self.section()
		self.assertIn("not about any file your rationale happens to cite", text)
		self.assertIn("credential's format is", text)

	def test_reciting_the_rule_is_named_as_a_failure(self):
		text = self.section()
		self.assertIn("no single delta to re-check", text)
		self.assertIn("Writing the rule's words is not passing the rule", text)

	def test_a_method_note_has_its_own_limb(self):
		self.assertIn("Q5 — THE WITNESS", self.section())

	def test_convergence_keeps_final_authority(self):
		"""E3: the upstream self-test reduces what reaches convergence; it does
		not replace or bind it."""
		self.assertIn("final authority to cut anything", self.section())


# ── no volume target (REDESIGN.md C4, criterion 14) ─────────────────────────
class NoVolumeTargetTests(unittest.TestCase):
	"""The fleet quota at `scratch/research-0828b.js:38` is the one orphan that
	must NOT be ported. Its measured history becomes reasoning in the guideline
	text instead of a number."""

	HEAD = "### There Is No Volume Target"

	def section(self):
		start = RESEARCH.index(self.HEAD)
		return RESEARCH[start:RESEARCH.index("### Deduplicate Facts")]

	def explanations(self):
		"""The two places a quota phrase may legitimately appear: the section
		explaining why the old rule failed, and the prompt-builder's worked
		example of the hint that must never be written again. Both quote the
		historical text; neither instructs anyone."""
		builder = TEMPLATE[TEMPLATE.index("## Writing Hypotheses"):]
		builder = builder[:builder.index("## Batch sizing and tiering")]
		return " ".join((self.section() + "\n" + builder).split())

	# Flattened, because the old rule's own text was line-wrapped and a
	# line-oriented grep misses it — which is how it survived a check once.
	FLAT = {name: " ".join(text.split()) for name, text in CHECKER_FACING.items()}

	QUOTA_PHRASES = (
		"at most one or two per run",
		"across the whole candidate set",
		"across the whole fleet",
		"the expected total is one or two",
		"assume someone else is covering",
		"fleet-wide budget",
	)

	def test_no_quota_phrase_survives_outside_the_explanation(self):
		explanation = self.explanations()
		for name, flat in sorted(self.FLAT.items()):
			for phrase in self.QUOTA_PHRASES:
				with self.subTest(name + ": " + phrase):
					if phrase not in flat:
						continue
					self.assertIn(phrase, explanation,
						"{} states a fleet quota outside the section explaining why "
						"the old one failed".format(name))
					self.assertEqual(flat.count(phrase), explanation.count(phrase))

	def test_no_numeric_target_is_stated_to_a_per_tool_agent(self):
		"""Criterion 14. Any sentence pairing a count with a proposal noun is
		the defect, whatever wording it wears."""
		pattern = re.compile(
			r"(?:at most|no more than|up to|expect|aim for|limit(?:ed)? to)\s+"
			r"(?:one|two|three|a few|\d+)\b[^.]{0,60}"
			r"(?:watch items?|method notes?|proposals?|suggestions?)", re.I)
		explanation = self.explanations()
		for name, text in sorted(CHECKER_FACING.items()):
			for match in pattern.finditer(" ".join(text.split())):
				with self.subTest(name + ": " + match.group(0)[:50]):
					self.assertIn(match.group(0), explanation)

	def test_the_reason_is_in_the_guideline_text_not_only_a_design_doc(self):
		"""An agent told "there is no budget" with no explanation infers the
		omission is an oversight and invents one out of prudence."""
		text = self.section()
		self.assertIn("The reason is measured, not stylistic", text)
		self.assertIn("byte-identical", text)
		self.assertIn("exactly one each", text)
		self.assertIn("must not invent one", text)

	def test_the_four_concrete_don_ts_are_present(self):
		text = self.section()
		for phrase in ("Do not hold a proposal back", "Do not propose one because",
				"Do not drop a proposal that failed the self-test",
				"Judge each proposal on its own evidence"):
			self.assertIn(phrase, text)

	def test_volume_is_pushed_to_where_it_is_visible(self):
		self.assertIn("Volume is handled where volume is visible", self.section())


# ── the bar (REDESIGN.md C4) ────────────────────────────────────────────────
class WatchItemBarTests(unittest.TestCase):
	"""Each limb is a conjunction and each half has to be answerable with an
	artefact. The undecidable phrasings are what produced five rationales
	reciting the rule's own escape phrase."""

	def section(self):
		start = RESEARCH.index("### Watch Items (Proposing)")
		return RESEARCH[start:RESEARCH.index("### Before You Propose a Standing Note")]

	def test_both_limbs_are_stated_as_conjunctions_with_named_halves(self):
		text = self.section()
		self.assertIn("Each limb is a conjunction", text)
		self.assertIn("name the party who can change it", text)
		self.assertIn("name the file, and say what the edit would\n    be", text)
		self.assertIn("could you tell, from the\n    machine's state alone", text)
		self.assertIn("what breaks?", text)

	def test_exposure_is_distinguished_from_a_required_change(self):
		"""The half the last run's failures all missed: naming a file that
		depends on the behaviour is not naming an edit."""
		text = self.section()
		self.assertIn("exposure, not a required change", text)
		self.assertIn("cask:obsidian", text)

	def test_the_accepted_entry_is_the_worked_example(self):
		text = self.section()
		self.assertIn("cursor-record()", text)
		self.assertIn("Named party, named\n  file, named edit", text)

	def test_the_undecidable_escape_clause_is_gone_from_the_bar(self):
		"""`there's no single delta to re-check` was a self-assessed assertion
		with the wording supplied. It survives only where the self-test names
		it as a failure."""
		self.assertNotIn("no single delta to re-check", self.section())


# ── history as hypotheses (REDESIGN.md E2, criterion 15) ────────────────────
class HypothesisTests(unittest.TestCase):
	"""E2 reconciles "stop seeding candidates" with the measured value of the
	hypothesis framing. Both halves are evidenced: the seeding produced six of
	eight bad proposals, and the framing measurably raised research quality.
	The difference is that one hands over an answer and the other hands over a
	question with its evidence.

	The word "hypothesis" appeared nowhere in the skill before this."""

	def checker_section(self):
		start = RESEARCH.index("### Prior Findings Are Hypotheses")
		return RESEARCH[start:RESEARCH.index("### Headliners")]

	def builder_section(self):
		start = TEMPLATE.index("## Writing Hypotheses")
		return TEMPLATE[start:TEMPLATE.index("## Batch sizing and tiering")]

	def test_the_word_reaches_the_skill_at_all(self):
		self.assertRegex(RESEARCH, r"(?i)hypothes")
		self.assertRegex(TEMPLATE, r"(?i)hypothes")
		self.assertRegex(SKILL_MD, r"(?i)hypothes")

	def test_the_checker_is_told_to_evidence_or_drop(self):
		text = self.checker_section()
		self.assertIn("never as a\nfact to carry forward", text)
		self.assertIn("Evidence it yourself", text)
		self.assertIn("drop it. Say nothing", text)
		self.assertIn("Nothing reaches your output on the strength of history alone", text)

	def test_the_checker_is_told_not_to_launder_a_prior_conclusion(self):
		"""'A prior review found X and it still holds' is history reaching
		output on history's strength, wearing a citation."""
		flat = " ".join(self.checker_section().split())
		self.assertIn('Do not write "a prior review found X and it still holds"', flat)

	def test_being_handed_a_candidate_is_named_as_not_being_evidence(self):
		self.assertIn("Being handed a candidate is\nnot evidence that a candidate exists",
			self.checker_section())

	def test_the_placeholder_exists_and_is_documented(self):
		self.assertIn("{{HYPOTHESES}}", TEMPLATE)
		body, table = TEMPLATE.split("## Placeholder reference", 1)
		self.assertIn("{{HYPOTHESES}}", body)
		self.assertIn("{{HYPOTHESES}}", table)

	def test_the_prompt_builder_gets_both_worked_examples(self):
		text = self.builder_section()
		self.assertIn("**GOOD**", text)
		self.assertIn("**BAD**", text)
		self.assertIn("O_NORL", text)                       # the one that worked
		self.assertIn("proposed a watch item", text)        # the one that nominated
		self.assertIn("nomination wearing a question's clothes", text)

	def test_naming_an_artefact_kind_in_a_hypothesis_is_banned(self):
		text = self.builder_section()
		self.assertIn("Never name an artefact kind", text)
		self.assertIn("Never mention volume, budgets, counts", text)
		self.assertIn("Never carry a prior conclusion without its evidence", text)

	def test_hypotheses_are_drawn_mechanically_rather_than_written(self):
		"""A hand-written hint is where every nomination came from, and a
		hand-built prompt is what evaporates."""
		self.assertIn("mechanically", self.builder_section())
		self.assertIn("not hand-written\nper run", self.builder_section())

	def test_the_dispatch_step_carries_the_ban(self):
		"""§6.1 asks for one line in SKILL.md step 3 pointing at the rules —
		the orchestrator fills the placeholder, so the orchestrator is who has
		to know."""
		self.assertIn("never as nominations", SKILL_MD)
		self.assertIn("Writing Hypotheses", SKILL_MD)


# ── items are outward-facing (REDESIGN.md L3, criterion 7) ──────────────────
class OutwardFacingTests(unittest.TestCase):
	"""WP1 put the rule in the schema and in `contract.json`'s `scope`, and
	deliberately did NOT make it a validator filter — a regex that deleted
	"internal-looking" items would be exactly the banned behaviour. That leaves
	the checker's guidelines as the only place it is enforced."""

	def section(self):
		start = RESEARCH.index("### Items Are Outward-Facing Changes")
		return RESEARCH[start:RESEARCH.index("### Headliners")]

	def test_the_rule_reaches_the_agent_that_writes_items(self):
		text = self.section()
		self.assertIn("Project-internal maintenance never becomes an item", text)
		for named in ("Repo upkeep", "convention changes", "documentation updates"):
			self.assertIn(named, text)

	def test_it_is_stated_as_one_answerable_question(self):
		self.assertIn("Did anything change for a person who uses this tool without reading its",
			self.section())

	def test_internal_work_with_an_outward_consequence_is_kept(self):
		"""The rule has to not over-fire: a reproducible-build switch changes
		the published checksum, and that is a real item."""
		self.assertIn("Internal work with an outward consequence is outward-facing",
			self.section())

	def test_chore_is_not_offered_as_a_place_to_put_internal_maintenance(self):
		"""Without this the rule converts into a tag choice and nothing is
		actually excluded."""
		text = self.section()
		self.assertIn("`chore` is not the place to put internal maintenance", text)
		self.assertIn("it is not an item", text)

	def test_it_is_distinguished_from_the_noise_floor(self):
		"""Different rule, different moment: the noise floor deletes from what
		was written and carries a hard boundary because a deletion there can
		approve an update. This one asks whether there was an item at all."""
		text = self.section()
		self.assertIn("This is not the noise floor", text)
		self.assertLess(RESEARCH.index("### Items Are Outward-Facing Changes"),
			RESEARCH.index("### The Noise Floor"))

	def test_the_absence_of_a_deterministic_filter_is_stated(self):
		flat = " ".join(self.section().split())
		self.assertIn("Nothing in the deterministic layer enforces it", flat)

	def test_the_schema_and_the_guidelines_agree(self):
		import sys
		sys.path.insert(0, HERE)
		import items as model
		self.assertIn("outward-facing changes only", model.contract()["scope"]["items_are"])
		self.assertIn("outward-facing", self.section())


if __name__ == "__main__":
	unittest.main()
