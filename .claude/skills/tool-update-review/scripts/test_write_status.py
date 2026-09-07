#!/usr/bin/env python3
"""
test_write_status.py — write_status.py's read of agent-written report.json.
Usage: python3 test_write_status.py [-v]

Stdlib `unittest` only (no pytest, no fixtures directory, no network), same
constraints as test_assemble.py.

Scope is deliberately one boundary rather than the whole module: `init` is the
only subcommand that reads fields a *research subagent* wrote, and it is the
step the entire apply pass depends on — no status.json means no action list,
no progress page and nothing for the session to drive. Every other subcommand
reads status.json, which this script wrote itself.

`suggestions[].target_files[]` is the field that matters here. assemble.py
normalizes every other research-supplied array at the one boundary where
research output becomes a Tool object (`as_item_list`), but `target_files`
passes through untouched and lands in report.json in whatever shape the
subagent wrote — so `tf.get("path")` was an `AttributeError` waiting on the
obvious drift, `["Brewfile"]` instead of `[{"path": "Brewfile"}]`. That cost
the whole apply pass, not one action, which is the failure this file exists
to keep fixed.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WRITE_STATUS = os.path.join(SCRIPT_DIR, "write_status.py")


def _suggestion(sid, target_files):
	return {"id": sid, "kind": "edit", "title": "Pin azcopy", "target_files": target_files,
		"command": None, "auto_runnable": False, "needs_sudo": False,
		"rationale": "", "motivating_link": None, "diff_preview": None, "pre_accept": False}


class InitTargetFileDriftTests(unittest.TestCase):
	"""`target_files[]` is subagent output, so any member can be any shape.
	One malformed member must cost that member, never the status file."""

	HOSTILE_TARGET_FILES = [
		("bare string", ["Brewfile"]),
		("bare int", [7]),
		("null member", [None]),
		("nested array", [["Brewfile"]]),
		("path is a dict", [{"path": {"name": "Brewfile"}}]),
		("path is an int", [{"path": 3}]),
		("path is null", [{"path": None}]),
		("no path at all", [{"description": "the Brewfile"}]),
		("the whole array is null", None),
	]

	def _init(self, target_files):
		"""Run the real `write_status.py init`; return (status, stderr)."""
		with tempfile.TemporaryDirectory(prefix="write-status-test-") as session:
			report = {
				"schema_version": 1, "report_id": "tool-update-review-20260822T113344Z",
				"generated_at": "2026-08-22T11:33:44Z", "machine": {}, "summary": {},
				"repo_context": {}, "highlights": [],
				"tools": [{
					"id": "brew:azcopy", "name": "azcopy", "source": "brew",
					"suggestions": [
						_suggestion("brew:azcopy:upgrade", [{"path": "dotfiles/config/bash/.path"}]),
						_suggestion("brew:azcopy:edit", target_files),
					],
				}],
			}
			feedback = {"report_id": report["report_id"], "tool_comments": {},
				"decisions": {"brew:azcopy:upgrade": {"decision": "accept"},
					"brew:azcopy:edit": {"decision": "accept"}}}
			for name, obj in (("report.json", report), ("feedback.json", feedback)):
				with open(os.path.join(session, name), "w", encoding="utf-8") as fh:
					json.dump(obj, fh)
			p = subprocess.run([sys.executable, WRITE_STATUS, "init", session],
				capture_output=True, text=True, timeout=60)
			self.assertEqual(p.returncode, 0, p.stderr)
			with open(os.path.join(session, "status.json"), "r", encoding="utf-8") as fh:
				return json.load(fh), p.stderr

	def test_one_hostile_member_never_costs_the_status_file(self):
		for label, target_files in self.HOSTILE_TARGET_FILES:
			with self.subTest(label):
				status, _ = self._init(target_files)
				action_ids = [a["id"] for a in status["actions"]]
				# Both suggestions still have an action…
				self.assertIn("brew:azcopy:upgrade", action_ids, label)
				self.assertIn("brew:azcopy:edit", action_ids, label)
				# …and the readable sibling target file still decides the
				# synthetic commit/push actions, which is the only thing
				# target_files[] is read for.
				self.assertIn("commit:dotfiles", action_ids, label)
				self.assertIn("push:dotfiles", action_ids, label)

	def test_an_unreadable_member_is_warned_about_not_swallowed(self):
		for label, target_files in self.HOSTILE_TARGET_FILES:
			if target_files is None:
				continue   # an absent array says nothing and warns about nothing
			with self.subTest(label):
				_, stderr = self._init(target_files)
				self.assertIn("brew:azcopy:edit", stderr, label)
				self.assertIn("target_files", stderr, label)

	def test_a_bare_string_is_read_as_the_path_it_obviously_is(self):
		# Dropping it would silently lose a commit action; guessing silently
		# would be the same failure in the other direction. Read it, say so.
		status, stderr = self._init(["dotfiles/config/bash/.exports"])
		self.assertIn("commit:dotfiles", [a["id"] for a in status["actions"]])
		self.assertIn("reading it as the path", stderr)

	def test_well_shaped_target_files_are_unaffected(self):
		status, stderr = self._init([{"path": "Brewfile", "description": "pin"}])
		action_ids = [a["id"] for a in status["actions"]]
		self.assertIn("commit:macos-setup", action_ids)
		self.assertIn("commit:dotfiles", action_ids)
		self.assertEqual(stderr, "")


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
