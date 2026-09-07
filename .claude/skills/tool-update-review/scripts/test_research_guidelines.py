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


if __name__ == "__main__":
	unittest.main()
