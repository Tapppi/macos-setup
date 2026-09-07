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
	"""Every line containing `needle`, WITH its offset in the document.

	The offset is the point. `text.index(line)` would resolve to the first
	textual match of that line anywhere in the file, so two identical mentions
	would both be judged by the context of the first — and an unexplained
	second one would pass on the strength of the first one's justification."""
	out, offset = [], 0
	for line in text.splitlines(keepends=True):
		if needle in line:
			out.append((offset, line.rstrip("\n")))
		offset += len(line)
	return out


def heading_at(text: str, head: str) -> int:
	"""Offset of a heading, asserted present. Used by the ordering tests, where
	the claim is "this section comes before that one" and a bare ValueError
	from a rename says nothing about which claim broke."""
	at = text.find(head)
	assert at != -1, "no section {!r} — was it renamed?".format(head)
	return at


def section_of(text: str, head: str, until: str) -> str:
	"""One section, from its heading to the next named one.

	Both headings are asserted present with a readable message. A bare
	`str.index` raises a `ValueError` traceback that tells whoever renamed a
	heading nothing about which test wanted it."""
	start = text.find(head)
	assert start != -1, "no section {!r} — was it renamed?".format(head)
	end = text.find(until, start)
	assert end != -1, "no section {!r} after {!r}".format(until, head)
	return text[start:end]


def flat(text: str) -> str:
	"""Whitespace-flattened. Every content assertion here goes through this,
	because these files are hard-wrapped prose: reflowing a paragraph moves the
	line breaks inside a sentence, and a test that matched the old wrapping
	would fail on an edit that changed nothing. The rule this file exists to
	protect is what the sentence says, not where it breaks.

	It is not only convenience. The old fleet quota survived a check once
	precisely because it was line-wrapped and the grep was line-oriented."""
	return " ".join(text.split())


class GuidelineTestCase(unittest.TestCase):
	"""`assertSays` is `assertIn` modulo line wrapping. `assertDoesNotSay` is
	its negation. Use them for anything quoted from the guidelines; plain
	`assertIn` stays available for structural checks (a placeholder token, a
	field name) where the exact bytes are the point."""

	maxDiff = 2000

	def assertSays(self, needle, haystack, msg=None):
		"""Argument order matches `assertIn`, deliberately — it is a drop-in."""
		self.assertIn(flat(needle), flat(haystack), msg)

	def assertDoesNotSay(self, needle, haystack, msg=None):
		self.assertNotIn(flat(needle), flat(haystack), msg)


# ── intel.Brewfile (REDESIGN.md B1, criterion 20) ───────────────────────────
class IntelBrewfileTests(GuidelineTestCase):
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
			for _, line in lines_mentioning(text, "intel.Brewfile"):
				with self.subTest(name + ": " + line.strip()[:60]):
					self.assertNotRegex(line, self.INSTRUCTIONAL)

	def test_every_surviving_mention_is_a_prohibition(self):
		"""An explicit exclusion beats silence here: an agent scanning
		macos-setup finds the file whether or not we named it, and I-17 exists
		because the last run cited it seven times.

		Judged at each mention's own offset. Anchoring on `text.index(line)`
		would let a second, unexplained mention borrow the first one's
		justification — precisely the case this test exists for."""
		forbidding = ("out of this tool entirely", "do not read it", "never reaches apply")
		for name, text in sorted(CHECKER_FACING.items()):
			for offset, line in lines_mentioning(text, "intel.Brewfile"):
				with self.subTest(name + ": " + line.strip()[:60]):
					window = flat(text[offset:offset + 600])
					self.assertTrue(any(f in window for f in forbidding),
						"unexplained mention: " + line.strip())

	def test_the_relevancy_scan_list_names_only_the_live_manifest(self):
		scan = section_of(RESEARCH, "### Relevancy Is the Point",
			"### Classify Non-Changelog Findings Correctly")
		self.assertSays("Brewfile", scan)
		self.assertNotIn("intel.Brewfile", scan)


# ── the orphans (REDESIGN.md A's corollary, criterion 19) ───────────────────
class OrphanedInstructionTests(GuidelineTestCase):
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
		section = section_of(RESEARCH, "### What You May Touch",
			"### Prior Findings Are Hypotheses")
		self.assertSays("non-destructive and\n  read-only", section)
		for banned in ("setup.sh", "tasks/*.sh", "dotfiles/bootstrap.sh"):
			self.assertSays(banned, section)
		self.assertSays("session directory except your own output file", section)

	def test_the_read_only_discipline_is_not_only_in_the_bespoke_section(self):
		"""The point of the port: it governs every checker, including the ones
		that never touch a `tasks/*.sh` function."""
		general = heading_at(RESEARCH, "### What You May Touch")
		bespoke = heading_at(RESEARCH, "### Bespoke `tasks/*.sh` Setup Testing")
		self.assertLess(general, bespoke)

	def test_vendored_skill_content_is_a_named_false_positive(self):
		"""`grep -rn agent-skills` over the skill returned nothing before this."""
		section = section_of(RESEARCH, "### Word-Boundary Grep Rule", "### Spawning")
		self.assertSays("dotfiles/config/agent-skills", section)
		self.assertSays("almost never a real touchpoint", section)
		# ...and the prompt a checker actually receives says so too.
		self.assertSays("dotfiles/config/agent-skills", TEMPLATE)

	def test_the_tiering_decision_is_recorded_not_just_the_heuristic(self):
		"""research.md documented the heuristic; the decision a given run made
		was recorded nowhere, so a scoped run and a full run produced
		indistinguishable session dirs."""
		self.assertSays('"tier"', SCHEMAS)
		self.assertSays('"scope"', SCHEMAS)
		self.assertSays("scoped run and a full run produce indistinguishable", RESEARCH)

	def test_touchpoints_reach_the_prompt_as_a_generated_placeholder(self):
		"""The per-group touchpoint hints were hand-typed. The touchpoint half
		is derivable from the word-boundary grep; the nomination half is what
		§Writing Hypotheses bans."""
		self.assertSays("{{TOUCHPOINTS}}", TEMPLATE)
		self.assertSays("never typed by hand", TEMPLATE)

	def test_the_baseline_upgrade_suggestion_is_still_research_s_to_not_author(self):
		"""Listed as an orphan by the design doc, but its grep was
		case-sensitive: the instruction was already here, spelled
		"Do **not** author". Asserted so it stays."""
		self.assertSays('Do **not** author the plain "upgrade this tool" suggestion', RESEARCH)

	def test_every_template_placeholder_has_a_row_in_the_table(self):
		"""A placeholder nobody documents is a placeholder that gets filled
		with whatever the orchestrator remembers — which is how the hand-built
		prompt happened."""
		body, table = TEMPLATE.split("## Placeholder reference", 1)
		used = set(re.findall(r"\{\{[A-Z_]+\}\}", body))
		documented = set(re.findall(r"\{\{[A-Z_]+\}\}", table))
		self.assertEqual(used - documented, set())


# ── the three stores (REDESIGN.md L1, criterion 16) ─────────────────────────
class ThreeStoresTests(GuidelineTestCase):
	"""The volume disagreement that stalled this was a category error. There is
	no single answer to "how many" because there are three different things,
	and a guideline that conflates them produces last run's mess: eight
	proposals, of which two were method notes in a watch item's container."""

	SECTION = "### Standing Notes: Three Stores"

	def section(self):
		return section_of(RESEARCH, self.SECTION,
			"### Research-Method Notes vs Watch Items")

	def test_all_three_stores_are_named_with_their_scope_and_volume(self):
		text = self.section()
		for phrase in ("Global method notes", "Per-tool method notes", "Watch items"):
			self.assertSays(phrase, text)
		self.assertSays("across many tools", text)
		self.assertSays("**rare**", text)
		self.assertSays("**many**", text)

	def test_rare_is_justified_structurally_and_not_by_a_number(self):
		"""A global note is rare because its entry condition is cross-tool
		evidence and exactly one reader in the pipeline has it. Stated any
		other way "rare" reads as a budget, and a budget is what criterion 14
		forbids."""
		text = self.section()
		self.assertSays("the entry condition is cross-tool evidence", text)
		self.assertSays("not a quota anybody enforces", text)
		self.assertNotRegex(text, r"\b(?:at most|no more than|up to)\s+\w+\s+(?:notes?|items?)")

	def test_the_agent_is_told_to_route_at_the_point_of_writing(self):
		text = self.section()
		self.assertSays("Route at the point of writing", text)
		# Two ordered questions, not a table to interpret.
		self.assertSays("how to research, or about what to report", text)
		self.assertSays("which tool do you write it against", text)

	def test_a_per_tool_agent_never_proposes_a_global_note(self):
		"""It sees one to nine tools; "holds across many tools" is not a claim
		it is in a position to make. Convergence reads every tool at once and
		promotes. Without this the store either stays empty or fills with
		per-tool observations stated at the wrong altitude."""
		text = self.section()
		self.assertSays("You write two of the three", text)
		self.assertSays("you never propose one", text)
		self.assertSays("promotion is its call", text)
		self.assertSays("convergence, by promotion", text)

	def test_the_generalisation_claim_has_somewhere_to_go(self):
		"""A rule that only says "do not" leaves the knowledge nowhere. The
		checker records the claim in the rationale; convergence promotes on it."""
		text = self.section()
		self.assertSays("say so in the `rationale`", text)
		self.assertSays("what convergence promotes on", text)

	def test_the_routing_test_precedes_the_watch_item_bar(self):
		"""It has to run first: a topic no changelog can match is filed in a
		store whose only mechanism cannot reach it."""
		self.assertLess(heading_at(RESEARCH, "### Research-Method Notes vs Watch Items"),
			heading_at(RESEARCH, "### Watch Items (Proposing)"))

	def test_the_routing_test_is_stated_as_one_answerable_question(self):
		text = section_of(RESEARCH, "### Research-Method Notes vs Watch Items",
			"### Writing a Research-Method Note")
		self.assertSays("Could a future release's published text plausibly contain words that match",
			" ".join(text.split()))
		self.assertSays("**No**", text)
		self.assertSays("**Yes**", text)
		# Both worked failures, named, with the sentence that gives them away.
		self.assertSays("brew:iproute2mac", text)
		self.assertSays("brew:nnn", text)
		self.assertSays("Believe it.", text)

	def test_routing_is_stated_not_to_be_dropping(self):
		"""L7's companion: moving a note between stores keeps the knowledge. An
		agent that reads routing as rejection stops writing them."""
		text = section_of(RESEARCH, "### Research-Method Notes vs Watch Items",
			"### Writing a Research-Method Note")
		self.assertSays("Routing is not dropping", text)

	def test_a_method_note_must_name_a_failure_rather_than_predict_one(self):
		text = section_of(RESEARCH, "### Writing a Research-Method Note",
			"### Watch Items (Reading)")
		self.assertSays("name a failure, not predict one", text)
		for field in ("method_topic", "method_note", "rationale"):
			self.assertSays(field, text)

	def test_the_method_note_shape_the_guideline_shows_is_the_one_that_validates(self):
		"""A worked shape that the validator rejects is worse than none — the
		agent copies it and gets an error it cannot connect to the guideline."""
		import sys
		sys.path.insert(0, HERE)
		import items as model
		text = section_of(RESEARCH, "### Writing a Research-Method Note",
			"### Watch Items (Reading)")
		for field in model.MEMORY_PAYLOAD_FIELDS["method-note"]:
			self.assertSays('"{}"'.format(field), text)
		for field in ("target_files", "command", "auto_runnable"):
			self.assertSays('"{}"'.format(field), text)
		self.assertSays("proposal, not a write", text)

	def test_method_notes_are_read_before_research_begins(self):
		"""A note saying "read CHANGELOG.md, the release page is boilerplate"
		is worthless delivered after the release page has been read."""
		text = section_of(RESEARCH, "### Watch Items (Reading)",
			"### Watch Items (Proposing)")
		self.assertSays("read first, before you look anything up", text)

	def test_both_stores_reach_the_checker_through_the_prompt(self):
		"""REDESIGN.md I4: the per-tool agent is GIVEN its watch items rather
		than sent to find them."""
		self.assertSays("{{STANDING_NOTES}}", TEMPLATE)
		self.assertSays("{{STANDING_NOTES}}", RESEARCH)
		self.assertSays("method-notes.json", RESEARCH)

	def test_the_method_note_kind_is_in_the_schema_the_checker_writes_against(self):
		self.assertSays('kind: "method-note"', SCHEMAS)
		self.assertSays("method_topic", SCHEMAS)
		self.assertSays("method_note", SCHEMAS)


# ── the self-test tags, never removes (REDESIGN.md L7, criterion 17) ────────
class SelfTestTests(GuidelineTestCase):
	"""§E3 asked for a self-test; §L7 closed the one lossy point in it. A
	failing proposal is still written, tagged with the failing limb and the
	agent's own reason, and convergence verifies that dropping it is
	appropriate. A proposal the agent never writes is one convergence cannot
	restore."""

	HEAD = "### Before You Propose a Standing Note: the Self-Test"

	def section(self):
		return section_of(RESEARCH, self.HEAD, "### There Is No Volume Target")

	def test_the_self_test_tags_and_never_deletes(self):
		text = self.section()
		self.assertSays("Nothing here deletes a proposal", text)
		self.assertSays("self_test_failed", text)
		self.assertSays("Never suppress a proposal because it failed a question here", text)

	def test_the_asymmetry_is_stated_as_the_reason(self):
		"""Without the reason an agent optimises for a short list, which is the
		behaviour that produced 0 accepted proposals out of 24."""
		self.assertSays("cannot\nrestore", self.section())

	def test_every_limb_in_the_vocabulary_is_reachable_from_a_question(self):
		"""A limb the guidelines never tell anyone to use is a limb nothing
		emits; a question with no limb is a failure with nowhere to go."""
		import sys
		sys.path.insert(0, HERE)
		import items as model
		text = self.section()
		for limb in model.SELF_TEST_LIMBS:
			with self.subTest(limb):
				self.assertSays('"' + limb + '"', text)

	def test_routing_is_the_one_answer_that_moves_rather_than_tags(self):
		text = self.section()
		self.assertSays("Q1 — ROUTING", text)
		self.assertSays("route* rather than a tag", text)

	def test_the_unknown_config_status_hole_is_named(self):
		"""22 of 78 tools had no prior handling, so Q2's quote limb is satisfied
		by "there is no prior handling to re-verify" — true, and evidence for
		nothing. Left unsaid, the test has a hole over a quarter of the fleet."""
		text = self.section()
		self.assertSays("22 of 78", text)
		self.assertSays("Do not read a vacuous pass as a pass", text)

	def test_q3_asks_about_the_changing_thing_not_any_cited_file(self):
		"""Read loosely, this clause drops the best proposal in the set:
		claudebar cites tasks/config.sh:399, which is not the thing that could
		change."""
		text = self.section()
		self.assertSays("not about any file your rationale happens to cite", text)
		self.assertSays("credential's format is", text)

	def test_reciting_the_rule_is_named_as_a_failure(self):
		text = self.section()
		self.assertSays("no single delta to re-check", text)
		self.assertSays("Writing the rule's words is not passing the rule", text)

	def test_every_tag_example_carries_a_reason(self):
		"""`reason` is required, and the validator rejects a tag without one.
		A worked example that omits it teaches the shape that fails — prose
		that is correct in one paragraph and unfollowable three below it."""
		for example in re.finditer(r"\{limb: [^}]*\}", self.section()):
			with self.subTest(example.group(0)[:40]):
				self.assertIn("reason:", example.group(0))

	def test_the_reason_requirement_is_stated_where_the_tag_is_introduced(self):
		self.assertSays("`reason` is required whenever the tag is present",
			self.section())

	def test_a_method_note_has_its_own_limb(self):
		self.assertSays("Q5 — THE WITNESS", self.section())

	def test_convergence_keeps_final_authority(self):
		"""E3: the upstream self-test reduces what reaches convergence; it does
		not replace or bind it."""
		self.assertSays("final authority to cut anything", self.section())


# ── no volume target (REDESIGN.md C4, criterion 14) ─────────────────────────
class NoVolumeTargetTests(GuidelineTestCase):
	"""The fleet quota at `scratch/research-0828b.js:38` is the one orphan that
	must NOT be ported. Its measured history becomes reasoning in the guideline
	text instead of a number."""

	HEAD = "### There Is No Volume Target"

	def section(self):
		return section_of(RESEARCH, self.HEAD, "### Deduplicate Facts")

	def explanations(self):
		"""The places a quota phrase may legitimately appear, **per document**:
		research.md's section explaining why the old rule failed, and the
		template's worked example of the hint that must never be written again.
		Both quote the historical text; neither instructs anyone.

		Kept per document deliberately. Pooling them and comparing counts
		against one document's total compares mismatched scopes — a phrase
		appearing legitimately in both regions counts 2 in the pool and 1 in
		each file, and the test would fail on correct guidelines."""
		return {
			"research.md": flat(self.section()),
			"research-prompt-template.md": flat(section_of(
				TEMPLATE, "## Writing Hypotheses", "## Batch sizing and tiering")),
			"schemas.md": "",
		}

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
		explained = self.explanations()
		for name, document in sorted(self.FLAT.items()):
			for phrase in self.QUOTA_PHRASES:
				with self.subTest(name + ": " + phrase):
					seen = document.count(phrase)
					if not seen:
						continue
					self.assertEqual(seen, explained[name].count(phrase),
						"{} states a fleet quota outside the passage explaining why "
						"the old one failed".format(name))

	def test_no_numeric_target_is_stated_to_a_per_tool_agent(self):
		"""Criterion 14. Any sentence pairing a count with a proposal noun is
		the defect, whatever wording it wears."""
		pattern = re.compile(
			r"(?:at most|no more than|up to|expect|aim for|limit(?:ed)? to)\s+"
			r"(?:one|two|three|a few|\d+)\b[^.]{0,60}"
			r"(?:watch items?|method notes?|proposals?|suggestions?)", re.I)
		explained = self.explanations()
		for name, text in sorted(CHECKER_FACING.items()):
			for match in pattern.finditer(flat(text)):
				with self.subTest(name + ": " + match.group(0)[:50]):
					self.assertIn(match.group(0), explained[name])

	def test_the_reason_is_in_the_guideline_text_not_only_a_design_doc(self):
		"""An agent told "there is no budget" with no explanation infers the
		omission is an oversight and invents one out of prudence."""
		text = self.section()
		self.assertSays("The reason is measured, not stylistic", text)
		self.assertSays("byte-identical", text)
		self.assertSays("exactly one each", text)
		self.assertSays("must not invent one", text)

	def test_the_four_concrete_don_ts_are_present(self):
		text = self.section()
		for phrase in ("Do not hold a proposal back", "Do not propose one because",
				"Do not drop a proposal that failed the self-test",
				"Judge each proposal on its own evidence"):
			self.assertSays(phrase, text)

	def test_volume_is_pushed_to_where_it_is_visible(self):
		self.assertSays("Volume is handled where volume is visible", self.section())


# ── the bar (REDESIGN.md C4) ────────────────────────────────────────────────
class WatchItemBarTests(GuidelineTestCase):
	"""Each limb is a conjunction and each half has to be answerable with an
	artefact. The undecidable phrasings are what produced five rationales
	reciting the rule's own escape phrase."""

	def section(self):
		return section_of(RESEARCH, "### Watch Items (Proposing)",
			"### Before You Propose a Standing Note")

	def test_both_limbs_are_stated_as_conjunctions_with_named_halves(self):
		text = self.section()
		self.assertSays("Each limb is a conjunction", text)
		self.assertSays("name the party who can change it", text)
		self.assertSays("name the file, and say what the edit would\n    be", text)
		self.assertSays("could you tell, from the\n    machine's state alone", text)
		self.assertSays("what breaks?", text)

	def test_exposure_is_distinguished_from_a_required_change(self):
		"""The half the last run's failures all missed: naming a file that
		depends on the behaviour is not naming an edit."""
		text = self.section()
		self.assertSays("exposure, not a required change", text)
		self.assertSays("cask:obsidian", text)

	def test_the_accepted_entry_is_the_worked_example(self):
		text = self.section()
		self.assertSays("cursor-record()", text)
		self.assertSays("Named party, named\n  file, named edit", text)

	def test_the_undecidable_escape_clause_is_gone_from_the_bar(self):
		"""`there's no single delta to re-check` was a self-assessed assertion
		with the wording supplied. It survives only where the self-test names
		it as a failure."""
		self.assertNotIn("no single delta to re-check", self.section())


# ── history as hypotheses (REDESIGN.md E2, criterion 15) ────────────────────
class HypothesisTests(GuidelineTestCase):
	"""E2 reconciles "stop seeding candidates" with the measured value of the
	hypothesis framing. Both halves are evidenced: the seeding produced six of
	eight bad proposals, and the framing measurably raised research quality.
	The difference is that one hands over an answer and the other hands over a
	question with its evidence.

	The word "hypothesis" appeared nowhere in the skill before this."""

	def checker_section(self):
		return section_of(RESEARCH, "### Prior Findings Are Hypotheses",
			"### Items Are Outward-Facing Changes")

	def builder_section(self):
		return section_of(TEMPLATE, "## Writing Hypotheses",
			"## Batch sizing and tiering")

	def test_the_word_reaches_the_skill_at_all(self):
		self.assertRegex(RESEARCH, r"(?i)hypothes")
		self.assertRegex(TEMPLATE, r"(?i)hypothes")
		self.assertRegex(SKILL_MD, r"(?i)hypothes")

	def test_the_checker_is_told_to_evidence_or_drop(self):
		text = self.checker_section()
		self.assertSays("never as a\nfact to carry forward", text)
		self.assertSays("Evidence it yourself", text)
		self.assertSays("drop it. Say nothing", text)
		self.assertSays("Nothing reaches your output on the strength of history alone", text)

	def test_the_checker_is_told_not_to_launder_a_prior_conclusion(self):
		"""'A prior review found X and it still holds' is history reaching
		output on history's strength, wearing a citation."""
		self.assertSays('Do not write "a prior review found X and it still holds"',
			self.checker_section())

	def test_being_handed_a_candidate_is_named_as_not_being_evidence(self):
		self.assertSays("Being handed a candidate is\nnot evidence that a candidate exists",
			self.checker_section())

	def test_the_placeholder_exists_and_is_documented(self):
		self.assertSays("{{HYPOTHESES}}", TEMPLATE)
		body, table = TEMPLATE.split("## Placeholder reference", 1)
		self.assertSays("{{HYPOTHESES}}", body)
		self.assertSays("{{HYPOTHESES}}", table)

	def test_the_prompt_builder_gets_both_worked_examples(self):
		text = self.builder_section()
		self.assertSays("**GOOD**", text)
		self.assertSays("**BAD**", text)
		self.assertSays("O_NORL", text)                       # the one that worked
		self.assertSays("proposed a watch item", text)        # the one that nominated
		self.assertSays("nomination wearing a question's clothes", text)

	def test_naming_an_artefact_kind_in_a_hypothesis_is_banned(self):
		text = self.builder_section()
		self.assertSays("Never name an artefact kind", text)
		self.assertSays("Never mention volume, budgets, counts", text)
		self.assertSays("Never carry a prior conclusion without its evidence", text)

	def test_hypotheses_are_drawn_mechanically_rather_than_written(self):
		"""A hand-written hint is where every nomination came from, and a
		hand-built prompt is what evaporates."""
		self.assertSays("mechanically", self.builder_section())
		self.assertSays("not hand-written\nper run", self.builder_section())

	def test_the_dispatch_step_carries_the_ban(self):
		"""§6.1 asks for one line in SKILL.md step 3 pointing at the rules —
		the orchestrator fills the placeholder, so the orchestrator is who has
		to know."""
		self.assertSays("never as nominations", SKILL_MD)
		self.assertSays("Writing Hypotheses", SKILL_MD)


# ── items are outward-facing (REDESIGN.md L3, criterion 7) ──────────────────
class OutwardFacingTests(GuidelineTestCase):
	"""WP1 put the rule in the schema and in `contract.json`'s `scope`, and
	deliberately did NOT make it a validator filter — a regex that deleted
	"internal-looking" items would be exactly the banned behaviour. That leaves
	the checker's guidelines as the only place it is enforced."""

	def section(self):
		return section_of(RESEARCH, "### Items Are Outward-Facing Changes",
			"### Headliners")

	def test_the_rule_reaches_the_agent_that_writes_items(self):
		text = self.section()
		self.assertSays("Project-internal maintenance never becomes an item", text)
		for named in ("Repo upkeep", "convention changes", "documentation updates"):
			self.assertSays(named, text)

	def test_it_is_stated_as_one_answerable_question(self):
		self.assertSays("Did anything change for a person who uses this tool without reading its",
			self.section())

	def test_internal_work_with_an_outward_consequence_is_kept(self):
		"""The rule has to not over-fire: a reproducible-build switch changes
		the published checksum, and that is a real item."""
		self.assertSays("Internal work with an outward consequence is outward-facing",
			self.section())

	def test_chore_is_not_offered_as_a_place_to_put_internal_maintenance(self):
		"""Without this the rule converts into a tag choice and nothing is
		actually excluded."""
		text = self.section()
		self.assertSays("`chore` is not the place to put internal maintenance", text)
		self.assertSays("it is not an item", text)

	def test_it_is_distinguished_from_the_noise_floor(self):
		"""Different rule, different moment: the noise floor deletes from what
		was written and carries a hard boundary because a deletion there can
		approve an update. This one asks whether there was an item at all."""
		text = self.section()
		self.assertSays("This is not the noise floor", text)
		self.assertLess(heading_at(RESEARCH, "### Items Are Outward-Facing Changes"),
			heading_at(RESEARCH, "### The Noise Floor"))

	def test_the_absence_of_a_deterministic_filter_is_stated(self):
		self.assertSays("Nothing in the deterministic layer enforces it", self.section())

	def test_the_schema_and_the_guidelines_agree(self):
		import sys
		sys.path.insert(0, HERE)
		import items as model
		self.assertSays("outward-facing changes only", model.contract()["scope"]["items_are"])
		self.assertSays("outward-facing", self.section())


# ── the claims stay true of the artifact they name ─────────────────────────
class DocumentedScopeTests(GuidelineTestCase):
	"""A doc that overstates where a rule holds is worse than one that does not
	mention it: a reader checks the wrong file and concludes the code is
	broken. §1.7c describes a property of `validation.json` today, not of
	`report.json` — `assemble.py` has not been carried across."""

	def test_the_memory_bucket_claim_names_the_file_it_holds_for(self):
		text = section_of(SCHEMAS, "### 1.7c The self-test tag",
			"### 1.8 `version_delta` semantics")
		self.assertSays("scripts/validate_items.py` implements it", text)
		self.assertSays("scripts/assemble.py` does not yet", text)

	def test_the_claim_and_the_assembler_disagree_exactly_where_the_doc_says(self):
		"""Pinned so the caveat is removed when — and only when — the
		assembler stops needing it."""
		assembler = read("scripts", "assemble.py")
		self.assertIn('suggestion_kind(s) != "upgrade"', assembler)

	def test_the_unresolvable_citations_have_an_address(self):
		"""`REDESIGN.md`, `HANDOFF.md` and "criterion N" are cited across the
		skill as the authority for load-bearing decisions and exist nowhere in
		this repo. One row saying where they live is the difference between a
		reference and a dead end."""
		self.assertSays("REDESIGN.md", read("references", "item-schema.md"))
		self.assertSays("not in this repo", read("references", "item-schema.md"))


if __name__ == "__main__":
	unittest.main()
