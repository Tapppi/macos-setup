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
import io
import json
import os
import shutil
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


# ── pinning the reviewed version (WP5/I2 — references/apply.md §Pinning the
#      reviewed version) ────────────────────────────────────────────────────
# Criterion 22 must be checkable from status.json itself, not merely
# documented in apply.md's prose — a session that skips calling
# check_pin.py must not be able to produce a "done" status.json
# indistinguishable from one that ran it correctly. These tests exercise the
# refusal directly, at the write_status.py boundary, rather than trusting an
# apply-time agent to have followed the doc.
def _upgrade_tool(tool_id, name, source, target_version, version_pinned=False):
	return {
		"id": tool_id, "name": name, "source": source,
		"suggestions": [{
			"id": f"{tool_id}:upgrade", "kind": "upgrade", "title": f"Upgrade {name}",
			"target_files": [], "command": f"upgrade {name}", "target_version": target_version,
			"version_pinned": version_pinned, "auto_runnable": True, "needs_sudo": False,
			"rationale": "", "motivating_link": None, "diff_preview": None, "pre_accept": False,
		}],
	}


def _pin_result(phase, source, name, target_version, observed_version=None, match=False, reason=None):
	"""A well-shaped scripts/check_pin.py JSON result, matching what
	`emit()` actually prints — every fixture below builds one of these
	rather than a bespoke dict, so a shape check_pin.py's own contract
	changes gets updated in exactly one place."""
	if reason is None and not match:
		reason = "mismatch"
	return {"phase": phase, "checked_at": "2026-09-07T00:00:00Z", "source": source, "name": name,
		"target_version": target_version, "observed_version": observed_version,
		"match": match, "reason": reason}


class PinCheckGateTests(unittest.TestCase):
	def _session(self, tool):
		tmp = tempfile.mkdtemp(prefix="write-status-pin-test-")
		self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
		report = {
			"schema_version": 1, "report_id": "tool-update-review-20260907T000000Z",
			"generated_at": "2026-09-07T00:00:00Z", "machine": {}, "summary": {},
			"repo_context": {}, "highlights": [], "tools": [tool],
		}
		sid = tool["suggestions"][0]["id"]
		feedback = {"report_id": report["report_id"], "tool_comments": {},
			"decisions": {sid: {"decision": "accept"}}}
		for name, obj in (("report.json", report), ("feedback.json", feedback)):
			with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
				json.dump(obj, fh)
		p = subprocess.run([sys.executable, WRITE_STATUS, "init", tmp],
			capture_output=True, text=True, timeout=60)
		self.assertEqual(p.returncode, 0, p.stderr)
		return tmp, sid

	def _set_action(self, session, action_id, state):
		return subprocess.run([sys.executable, WRITE_STATUS, "set-action", session, action_id, state],
			capture_output=True, text=True, timeout=60)

	def _record(self, session, action_id, phase, result):
		result_file = os.path.join(session, f"result-{phase}-{action_id.replace(':', '_')}.json")
		with open(result_file, "w", encoding="utf-8") as fh:
			json.dump(result, fh)
		return subprocess.run(
			[sys.executable, WRITE_STATUS, "record-pin-check", session, action_id, phase, result_file],
			capture_output=True, text=True, timeout=60)

	def _status(self, session):
		with open(os.path.join(session, "status.json"), "r", encoding="utf-8") as fh:
			return json.load(fh)

	def test_done_is_refused_without_any_recorded_verify(self):
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		p = self._set_action(session, sid, "done")
		self.assertNotEqual(p.returncode, 0)
		self.assertIn("check_pin.py verify", p.stderr)
		action = next(a for a in self._status(session)["actions"] if a["id"] == sid)
		self.assertEqual(action["state"], "pending")  # untouched — refusal never wrote the transition

	def test_done_is_refused_on_a_recorded_mismatch(self):
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		self._record(session, sid, "verify", _pin_result("verify", "brew", "podman", "5.5.1",
			observed_version="5.6.0", match=False, reason="drifted"))
		p = self._set_action(session, sid, "done")
		self.assertNotEqual(p.returncode, 0)
		action = next(a for a in self._status(session)["actions"] if a["id"] == sid)
		self.assertEqual(action["state"], "pending")

	def test_done_succeeds_after_a_recorded_match(self):
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		r = self._record(session, sid, "verify", _pin_result("verify", "brew", "podman", "5.5.1",
			observed_version="5.5.1", match=True))
		self.assertEqual(r.returncode, 0, r.stderr)
		p = self._set_action(session, sid, "done")
		self.assertEqual(p.returncode, 0, p.stderr)
		action = next(a for a in self._status(session)["actions"] if a["id"] == sid)
		self.assertEqual(action["state"], "done")
		self.assertTrue(action["pin_checks"]["verify"]["match"])

	def test_evidence_recorded_for_a_different_target_version_does_not_satisfy_the_gate(self):
		# Guards against stale or copy-pasted evidence — e.g. a re-review
		# changed target_version and the old "verify" record is still sitting
		# there from before.
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		self._record(session, sid, "verify", _pin_result("verify", "brew", "podman", "5.5.0",
			observed_version="5.5.0", match=True))
		p = self._set_action(session, sid, "done")
		self.assertNotEqual(p.returncode, 0)

	def test_evidence_recorded_for_a_different_tool_name_does_not_satisfy_the_gate(self):
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		self._record(session, sid, "verify", _pin_result("verify", "brew", "not-podman", "5.5.1",
			observed_version="5.5.1", match=True))
		p = self._set_action(session, sid, "done")
		self.assertNotEqual(p.returncode, 0)

	def test_evidence_recorded_for_a_different_source_does_not_satisfy_the_gate(self):
		# mise:node and brew:node could carry the same name/target_version —
		# source has to be checked too, or a verify for one satisfies the
		# other.
		session, sid = self._session(_upgrade_tool("brew:node", "node", "brew", "5.5.1"))
		self._record(session, sid, "verify", _pin_result("verify", "mise", "node", "5.5.1",
			observed_version="5.5.1", match=True))
		p = self._set_action(session, sid, "done")
		self.assertNotEqual(p.returncode, 0)

	def test_mise_pinned_upgrade_is_gated_the_same_way(self):
		# version_pinned=True (the command itself pins the version) does not
		# exempt it — the gate is about check_pin.py verify evidence, not
		# about whether the command was pinned.
		session, sid = self._session(
			_upgrade_tool("mise:node", "node", "mise", "24.6.0", version_pinned=True))
		p = self._set_action(session, sid, "done")
		self.assertNotEqual(p.returncode, 0)
		self._record(session, sid, "verify", _pin_result("verify", "mise", "node", "24.6.0",
			observed_version="24.6.0", match=True))
		p = self._set_action(session, sid, "done")
		self.assertEqual(p.returncode, 0, p.stderr)

	def test_record_pin_check_refuses_a_preflight_result_filed_as_verify(self):
		# A preflight and a verify can be byte-identical for the same tool at
		# the same version — check_pin.py's emit() stamps "phase" into the
		# result itself so this can't be filed under the wrong name.
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		r = self._record(session, sid, "verify", _pin_result("preflight", "brew", "podman", "5.5.1",
			observed_version="5.5.1", match=True))
		self.assertNotEqual(r.returncode, 0)
		self.assertIn("phase", r.stderr)
		p = self._set_action(session, sid, "done")
		self.assertNotEqual(p.returncode, 0)

	def test_record_pin_check_refuses_a_malformed_result(self):
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		for label, bad in (
			("not an object", ["not", "a", "dict"]),
			("missing source", {"phase": "verify", "name": "podman", "match": True}),
			("match not a bool", {"phase": "verify", "source": "brew", "name": "podman", "match": "yes"}),
			("target_version not a string", {"phase": "verify", "source": "brew", "name": "podman",
				"match": True, "target_version": 5.51}),
		):
			with self.subTest(label):
				r = self._record(session, sid, "verify", bad)
				self.assertNotEqual(r.returncode, 0, label)
				action = next(a for a in self._status(session)["actions"] if a["id"] == sid)
				self.assertNotIn("verify", action.get("pin_checks", {}), label)

	def test_non_pin_checkable_sources_are_never_gated(self):
		# standalone/macos baselines carry target_version too (assemble.py
		# writes it onto every baseline) but check_pin.py has no --source for
		# either — gating them would make it impossible to ever mark them
		# done at all. They stay on the pre-existing manual-verification path.
		for source, name in (("standalone", "yt-dlp"), ("macos", "Safari")):
			with self.subTest(source):
				session, sid = self._session(_upgrade_tool(f"{source}:{name}", name, source, "9.9.9"))
				p = self._set_action(session, sid, "done")
				self.assertEqual(p.returncode, 0, p.stderr)

	def test_non_upgrade_edit_suggestions_are_never_gated(self):
		tool = {
			"id": "brew:azcopy", "name": "azcopy", "source": "brew",
			"suggestions": [_suggestion("brew:azcopy:edit", [{"path": "Brewfile"}])],
		}
		session, sid = self._session(tool)
		p = self._set_action(session, sid, "done")
		self.assertEqual(p.returncode, 0, p.stderr)

	def test_failed_and_skipped_are_never_gated_only_done_is(self):
		session, sid = self._session(_upgrade_tool("brew:podman", "podman", "brew", "5.5.1"))
		for state in ("running", "failed"):
			p = self._set_action(session, sid, state)
			self.assertEqual(p.returncode, 0, (state, p.stderr))


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
