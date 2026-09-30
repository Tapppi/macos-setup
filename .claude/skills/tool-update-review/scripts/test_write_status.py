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
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WRITE_STATUS = os.path.join(SCRIPT_DIR, "write_status.py")

sys.path.insert(0, SCRIPT_DIR)
import items as model  # noqa: E402


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
				"schema_version": 1, "contract_version": model.CONTRACT_VERSION,
				"report_id": "tool-update-review-20260822T113344Z",
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
# "An applied upgrade installs the version that was reviewed" must be
# checkable from status.json itself, not merely
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


class InitMethodNoteTests(unittest.TestCase):
	"""The apply half of render-time persistence. A method note render.py
	persisted must become the RIGHT action at init: reject → a pending withdraw
	carrying the exact remover invocation; accept → done (already in store);
	comment → a pending modification; an UNREVIEWED accept → a pending add.
	Before this, every reject was `skipped` and the note stayed in the store for
	every future run — auto-persist with no working review surface, the option
	that was rejected."""

	def _note(self, sid, topic, note):
		return {"id": sid, "kind": "method-note", "title": f"Method note: {topic}",
			"target_files": [], "command": None, "auto_runnable": False,
			"rationale": "r", "method_topic": topic, "method_note": note}

	def _init(self, decisions, record, proposals=None):
		with tempfile.TemporaryDirectory(prefix="write-status-mn-") as session:
			report = {"schema_version": 2, "contract_version": model.CONTRACT_VERSION,
				"report_id": "tool-update-review-20260822T113344Z",
				"generated_at": "2026-08-22T11:33:44Z", "machine": {}, "summary": {},
				"repo_context": {}, "highlights": [], "tools": [{
					"id": "brew:jq", "name": "jq", "source": "brew", "suggestions": proposals if proposals is not None else [
						_suggestion("brew:jq:upgrade", []),
						self._note("brew:jq:method-a", "topic a", "note a"),
						self._note("brew:jq:method-g", "topic g", "note g"),
					]}]}
			feedback = {"report_id": report["report_id"], "tool_comments": {},
				"decisions": decisions}
			files = [("report.json", report), ("feedback.json", feedback)]
			if record is not None:
				# render.py stamps every record with the report it rendered.
				files.append(("method-notes.render.json",
					dict({"report_id": report["report_id"]}, **record)))
			for name, obj in files:
				with open(os.path.join(session, name), "w", encoding="utf-8") as fh:
					json.dump(obj, fh)
			p = subprocess.run([sys.executable, WRITE_STATUS, "init", session],
				capture_output=True, text=True, timeout=60)
			self.assertEqual(p.returncode, 0, p.stderr)
			with open(os.path.join(session, "status.json"), encoding="utf-8") as fh:
				return {a["id"]: a for a in json.load(fh)["actions"]}, p.stderr

	PERSISTED = {"reviewed": True, "convergence_state": "converged",
		"written": [{"tool_id": "brew:jq", "suggestion_id": "brew:jq:method-a",
			"key": "brew:jq", "topic": "topic a", "note": "note a"}],
		"already_present": [{"tool_id": "brew:jq", "suggestion_id": "brew:jq:method-g",
			"key": model.GLOBAL_METHOD_NOTE_KEY, "topic": "topic g", "note": "note g"}],
		"failed": [], "unreviewed": []}

	def test_a_rejected_persisted_note_is_a_pending_withdraw_with_the_exact_command(self):
		actions, _ = self._init({"brew:jq:method-a": {"decision": "reject", "comment": "wrong tag"},
			"brew:jq:method-g": {"decision": "reject"}}, self.PERSISTED)
		a = actions["brew:jq:method-a"]
		self.assertEqual(a["state"], "pending")
		self.assertEqual(a["decision"], "reject")
		self.assertEqual(a["label"], "Withdraw method note: topic a")
		self.assertEqual(a["detail"], ["scripts/write_status.py remove-method-note "
			"--tool-id brew:jq --topic 'topic a' --note 'note a'"])
		self.assertIn("wrong tag", a["note"])
		g = actions["brew:jq:method-g"]
		self.assertEqual(g["detail"], ["scripts/write_status.py remove-global-method-note "
			"--topic 'topic g' --note 'note g'"])
		# A reject's comment is its reason, not a modification: no investigate.
		self.assertNotIn("investigate:brew:jq:method-a", actions)

	def test_an_accepted_or_undecided_persisted_note_is_already_done(self):
		actions, _ = self._init({"brew:jq:method-a": {"decision": "accept"}}, self.PERSISTED)
		for sid in ("brew:jq:method-a", "brew:jq:method-g"):
			with self.subTest(sid):
				self.assertEqual(actions[sid]["state"], "done")
				self.assertIsNotNone(actions[sid]["finished_at"])
				self.assertIn("persisted at render", actions[sid]["note"])
				self.assertEqual(actions[sid]["detail"], [])

	def test_a_comment_on_a_persisted_note_is_a_pending_modification_whatever_the_decision(self):
		for decision in ("accept", "discuss", None):
			with self.subTest(decision):
				dec = {"comment": "say 8.2 specifically"}
				if decision:
					dec["decision"] = decision
				actions, _ = self._init({"brew:jq:method-a": dec}, self.PERSISTED)
				inv = actions["investigate:brew:jq:method-a"]
				self.assertEqual(inv["state"], "pending")
				self.assertEqual(inv["label"], "Modify method note: topic a — say 8.2 specifically")

	def test_a_failed_write_is_not_stored_and_becomes_a_pending_add_under_the_records_key(self):
		"""Round-2 finding 1: a `failed` note is NOT stored, whatever the
		convergence state. Undecided or accepted, it becomes a pending add;
		the key travels from the record (here: global, for a promoted note)
		— it is never re-derived from the tool id."""
		record = {"reviewed": True, "convergence_state": "converged",
			"store_problem": "it exists but could not be read",
			"written": [], "already_present": [], "unreviewed": [], "failed": [
			{"tool_id": "brew:jq", "suggestion_id": "brew:jq:method-a", "key": "brew:jq",
				"topic": "topic a", "note": "note a", "reason": "store it exists but could not be read"},
			{"tool_id": "brew:jq", "suggestion_id": "brew:jq:method-g", "key": model.GLOBAL_METHOD_NOTE_KEY,
				"topic": "topic g", "note": "note g", "reason": "store it exists but could not be read"}]}
		actions, _ = self._init({"brew:jq:method-a": {"decision": "accept"}}, record)
		a = actions["brew:jq:method-a"]
		self.assertEqual(a["state"], "pending")
		self.assertEqual(a["label"], "Add method note: topic a")
		self.assertIn("could not be read", a["note"])
		g = actions["brew:jq:method-g"]  # undecided + failed → still a pending add
		self.assertEqual(g["state"], "pending")
		self.assertEqual(g["detail"], ["scripts/write_status.py add-global-method-note "
			"--topic 'topic g' --note 'note g'"])
		actions, _ = self._init({"brew:jq:method-g": {"decision": "reject"}}, record)
		self.assertEqual(actions["brew:jq:method-g"]["state"], "skipped")
		# A discuss holds a failed note back too — the page says so (review A9).
		actions, _ = self._init({"brew:jq:method-g": {"decision": "discuss"}}, record)
		self.assertNotIn("add-global-method-note", " ".join(
			actions["brew:jq:method-g"].get("detail") or []))

	def test_one_store_entry_shared_by_two_ids_withdraws_once_and_adds_once(self):
		"""Round-2 finding 2: two tools' identical notes both promoted to
		global are ONE entry (render wrote one, recorded the other as
		already_present). A veto on either id is one withdraw, and the other
		id is never marked "done, in store" — it is skipped naming the
		carrier. The same for an add of a not-stored shared entry."""
		def note(sid):
			return {"id": sid, "kind": "method-note", "title": "Method note: shared",
				"target_files": [], "command": None, "auto_runnable": False,
				"rationale": "r", "method_topic": "shared", "method_note": "same text"}
		def run(decisions, record):
			with tempfile.TemporaryDirectory(prefix="write-status-mn-") as session:
				report = {"schema_version": 2, "contract_version": model.CONTRACT_VERSION,
					"report_id": "r", "generated_at": "2026-08-22T11:33:44Z", "machine": {},
					"summary": {}, "repo_context": {}, "highlights": [], "tools": [
						{"id": "brew:aa", "name": "aa", "source": "brew", "suggestions": [note("brew:aa:method-s")]},
						{"id": "brew:bb", "name": "bb", "source": "brew", "suggestions": [note("brew:bb:method-s")]}]}
				for name, obj in (("report.json", report), ("feedback.json",
						{"report_id": "r", "tool_comments": {}, "decisions": decisions}),
						("method-notes.render.json", dict({"report_id": "r"}, **record))):
					with open(os.path.join(session, name), "w", encoding="utf-8") as fh:
						json.dump(obj, fh)
				p = subprocess.run([sys.executable, WRITE_STATUS, "init", session],
					capture_output=True, text=True, timeout=60)
				self.assertEqual(p.returncode, 0, p.stderr)
				with open(os.path.join(session, "status.json"), encoding="utf-8") as fh:
					return {a["id"]: a for a in json.load(fh)["actions"]}
		g = model.GLOBAL_METHOD_NOTE_KEY
		stored = {"reviewed": True, "convergence_state": "converged", "failed": [], "unreviewed": [],
			"written": [{"tool_id": "brew:aa", "suggestion_id": "brew:aa:method-s", "key": g, "topic": "shared", "note": "same text"}],
			"already_present": [{"tool_id": "brew:bb", "suggestion_id": "brew:bb:method-s", "key": g, "topic": "shared", "note": "same text"}]}
		# Veto from bb's id only: ONE withdraw, and aa is not "done, in store".
		actions = run({"brew:bb:method-s": {"decision": "reject"}}, stored)
		withdraws = [a for a in actions.values() if a["label"].startswith("Withdraw")]
		self.assertEqual(len(withdraws), 1)
		self.assertEqual(withdraws[0]["id"], "brew:bb:method-s")
		self.assertIn("remove-global-method-note", withdraws[0]["detail"][0])
		self.assertIn("2 suggestion ids", withdraws[0]["note"])
		self.assertEqual(actions["brew:aa:method-s"]["state"], "skipped")
		self.assertIn("Same store entry as 'brew:bb:method-s'", actions["brew:aa:method-s"]["note"])
		# Both rejected: still one withdraw, carried by the first id.
		actions = run({"brew:aa:method-s": {"decision": "reject"}, "brew:bb:method-s": {"decision": "reject"}}, stored)
		self.assertEqual(sum(1 for a in actions.values() if a["label"].startswith("Withdraw")), 1)
		self.assertEqual(actions["brew:aa:method-s"]["state"], "pending")
		self.assertEqual(actions["brew:bb:method-s"]["state"], "skipped")
		# Not stored (unreviewed), both accepted: ONE add.
		unrev = {"reviewed": False, "convergence_state": "not_run", "written": [], "already_present": [], "failed": [],
			"unreviewed": [{"tool_id": "brew:aa", "suggestion_id": "brew:aa:method-s", "key": g, "topic": "shared", "note": "same text", "reason": "not_run"},
				{"tool_id": "brew:bb", "suggestion_id": "brew:bb:method-s", "key": g, "topic": "shared", "note": "same text", "reason": "not_run"}]}
		actions = run({"brew:aa:method-s": {"decision": "accept"}, "brew:bb:method-s": {"decision": "accept"}}, unrev)
		self.assertEqual(sum(1 for a in actions.values() if a["label"].startswith("Add")), 1)
		self.assertEqual(actions["brew:bb:method-s"]["state"], "skipped")
		# Any reject is a veto of the ENTRY: an accept on the other id does
		# not write it.
		actions = run({"brew:aa:method-s": {"decision": "accept"}, "brew:bb:method-s": {"decision": "reject"}}, unrev)
		self.assertFalse(any(a["label"].startswith("Add") for a in actions.values()))
		self.assertEqual(actions["brew:aa:method-s"]["note"], "Not stored (unreviewed) and vetoed — nothing written")

	def test_the_no_record_fallback_honours_the_reports_global_routing(self):
		"""No render record: the key cannot come from it, so it comes from
		the same routing render applies — report.convergence.memory."""
		with tempfile.TemporaryDirectory(prefix="write-status-mn-") as session:
			report = {"schema_version": 2, "contract_version": model.CONTRACT_VERSION,
				"report_id": "r", "generated_at": "2026-08-22T11:33:44Z", "machine": {},
				"summary": {}, "repo_context": {}, "highlights": [],
				"convergence": {"state": "converged", "memory": {"promoted_to_global": ["brew:jq:method-g"],
					"rehomed_to_method_note": [], "restored": []}},
				"tools": [{"id": "brew:jq", "name": "jq", "source": "brew",
					"suggestions": [self._note("brew:jq:method-g", "topic g", "note g")]}]}
			for name, obj in (("report.json", report), ("feedback.json",
					{"report_id": "r", "tool_comments": {}, "decisions": {"brew:jq:method-g": {"decision": "accept"}}})):
				with open(os.path.join(session, name), "w", encoding="utf-8") as fh:
					json.dump(obj, fh)
			p = subprocess.run([sys.executable, WRITE_STATUS, "init", session],
				capture_output=True, text=True, timeout=60)
			self.assertEqual(p.returncode, 0, p.stderr)
			with open(os.path.join(session, "status.json"), encoding="utf-8") as fh:
				actions = {a["id"]: a for a in json.load(fh)["actions"]}
		self.assertIn("add-global-method-note", actions["brew:jq:method-g"]["detail"][0])

	def test_an_unreviewed_accept_is_a_pending_add_and_an_unreviewed_reject_writes_nothing(self):
		record = {"reviewed": False, "convergence_state": "not_run", "written": [],
			"already_present": [], "failed": [], "unreviewed": [
			{"tool_id": "brew:jq", "suggestion_id": "brew:jq:method-a", "key": "brew:jq",
				"topic": "topic a", "note": "note a", "reason": "convergence state 'not_run'"},
			{"tool_id": "brew:jq", "suggestion_id": "brew:jq:method-g", "key": "brew:jq",
				"topic": "topic g", "note": "note g", "reason": "convergence state 'not_run'"}]}
		actions, _ = self._init({"brew:jq:method-a": {"decision": "accept"},
			"brew:jq:method-g": {"decision": "reject"}}, record)
		a = actions["brew:jq:method-a"]
		self.assertEqual(a["state"], "pending")
		self.assertEqual(a["label"], "Add method note: topic a")
		self.assertEqual(a["detail"], ["scripts/write_status.py add-method-note "
			"--tool-id brew:jq --topic 'topic a' --note 'note a'"])
		self.assertEqual(actions["brew:jq:method-g"]["state"], "skipped")

	def test_malformed_failed_proposals_never_plan_an_add(self):
		for field in ("method_topic", "method_note"):
			for value in (None, "", " \t\n"):
				for decision in (None, "accept"):
					with self.subTest(field=field, value=value, decision=decision):
						sug = self._note("brew:jq:bad", "topic", "note")
						if value is None:
							del sug[field]
						else:
							sug[field] = value
						record = {"failed": [{"suggestion_id": sug["id"], "key": None,
							"reason": "method_topic/method_note missing or empty"}]}
						by_id, _ = self._init({sug["id"]: {"decision": decision}}, record, [sug])
						actions = list(by_id.values())
						self.assertEqual(len(actions), 1)
						self.assertEqual(actions[0]["state"], "skipped")
						self.assertIn("missing or empty", actions[0]["detail"][0])
						self.assertNotIn("add-method-note", actions[0]["detail"][0])

	def test_store_level_failure_still_plans_an_undecided_add(self):
		record = {"failed": [{"suggestion_id": "brew:jq:method-a", "key": "brew:jq",
			"reason": "store could not be read"}]}
		actions, _ = self._init({}, record)
		self.assertEqual(actions["brew:jq:method-a"]["state"], "pending")
		self.assertIn("add-method-note", actions["brew:jq:method-a"]["detail"][0])

	def test_no_render_record_falls_back_to_write_on_accept_and_says_so(self):
		actions, err = self._init({"brew:jq:method-a": {"decision": "accept"},
			"brew:jq:method-g": {"decision": "reject"}}, None)
		self.assertIn("no readable method-notes.render.json", err)
		self.assertEqual(actions["brew:jq:method-a"]["state"], "pending")
		self.assertIn("add-method-note", actions["brew:jq:method-a"]["detail"][0])
		self.assertEqual(actions["brew:jq:method-g"]["state"], "skipped")

	def test_a_record_written_for_another_report_is_not_trusted(self):
		"""A record naming a different report (a re-used session dir, a copied
		file) must not decide this report's actions: its "written" entry would
		make a reject withdraw a note this run never stored. It is ignored,
		loudly, exactly as a missing record is."""
		actions, err = self._init({"brew:jq:method-a": {"decision": "reject"},
			"brew:jq:method-g": {"decision": "accept"}},
			dict(self.PERSISTED, report_id="tool-update-review-20260101T000000Z"))
		self.assertIn("was written for report", err)
		self.assertEqual(actions["brew:jq:method-a"]["state"], "skipped")
		self.assertNotIn("Withdraw", actions["brew:jq:method-a"]["label"])
		self.assertEqual(actions["brew:jq:method-g"]["state"], "pending")
		self.assertIn("add-method-note", actions["brew:jq:method-g"]["detail"][0])

	def test_an_upgrade_beside_the_notes_is_untouched(self):
		actions, _ = self._init({"brew:jq:upgrade": {"decision": "reject"}}, self.PERSISTED)
		self.assertEqual(actions["brew:jq:upgrade"]["state"], "skipped")
		self.assertEqual(actions["brew:jq:upgrade"]["detail"], [])


class InitSyncGroupingTests(unittest.TestCase):
	"""`sync-upstream.sh` takes no vendor argument, so every drifted card of
	every vendor carries the identical command. `init` plans it once."""

	CMD = "bash sync-upstream.sh"

	def _sync(self, tool_id, command=None):
		return {"id": f"{tool_id}:sync", "kind": "upgrade", "title": "Sync every vendor",
			"target_files": [], "command": command or self.CMD, "auto_runnable": False,
			"needs_sudo": False, "rationale": "r", "motivating_link": None,
			"diff_preview": None}

	def _init(self, decisions, extra=(), then=None):
		"""→ the actions by id; with `then` — a list of (action id, state) set
		after init, followed by `finalize` — → (actions, summary)."""
		ids = ["skill:a", "skill:b", "skill:c", *extra]
		with tempfile.TemporaryDirectory(prefix="write-status-sync-") as session:
			report = {"schema_version": 2, "contract_version": model.CONTRACT_VERSION,
				"report_id": "tool-update-review-20260822T113344Z",
				"generated_at": "2026-08-22T11:33:44Z", "machine": {}, "summary": {},
				"repo_context": {}, "highlights": [], "tools": [
					{"id": tid, "name": tid, "source": "skill-drift",
						"suggestions": [self._sync(tid)]} for tid in ids]}
			feedback = {"report_id": report["report_id"], "tool_comments": {},
				"decisions": decisions}
			for name, obj in (("report.json", report), ("feedback.json", feedback)):
				with open(os.path.join(session, name), "w", encoding="utf-8") as fh:
					json.dump(obj, fh)
			p = subprocess.run([sys.executable, WRITE_STATUS, "init", session],
				capture_output=True, text=True, timeout=60)
			self.assertEqual(p.returncode, 0, p.stderr)
			if then is not None:
				for action_id, state in then:
					subprocess.run([sys.executable, WRITE_STATUS, "set-action",
						session, action_id, state], check=True,
						capture_output=True, text=True, timeout=60)
				subprocess.run([sys.executable, WRITE_STATUS, "finalize", session,
					"--phase", "done"], check=True, capture_output=True,
					text=True, timeout=60)
			with open(os.path.join(session, "status.json"), encoding="utf-8") as fh:
				status = json.load(fh)
			actions = {a["id"]: a for a in status["actions"]}
			return actions if then is None else (actions, status["summary"])

	def test_a_covered_sibling_is_counted_with_its_carriers_outcome(self):
		"""Integration review round 3, R3-3: an accepted card `init` skipped as
		covered by the carrier's run was counted nowhere, so `applied` +
		`failed` fell short of the accepted count. It shares the carrier's
		outcome."""
		accept = {"skill:a:sync": {"decision": "accept"},
			"skill:b:sync": {"decision": "accept"},
			"skill:c:sync": {"decision": "reject"}}
		actions, summary = self._init(accept, then=[("skill:a:sync", "done")])
		self.assertEqual(actions["skill:b:sync"]["covered_by"], "skill:a:sync")
		self.assertNotIn("covered_by", actions["skill:a:sync"])
		self.assertNotIn("covered_by", actions["skill:c:sync"])
		self.assertEqual((summary["applied"], summary["failed"],
			summary["rejected"]), (2, 0, 1))
		_, summary = self._init(accept, then=[("skill:a:sync", "failed")])
		self.assertEqual((summary["applied"], summary["failed"]), (0, 2))
		# A carrier that never ran covers nothing.
		_, summary = self._init(accept, then=[])
		self.assertEqual((summary["applied"], summary["failed"]), (0, 0))

	def test_identical_sync_commands_become_one_pending_action(self):
		actions = self._init({"skill:a:sync": {"decision": "accept"},
			"skill:b:sync": {"decision": "accept"},
			"skill:c:sync": {"decision": "reject"}})
		self.assertEqual(actions["skill:a:sync"]["state"], "pending")
		self.assertIn("skill:b:sync", actions["skill:a:sync"]["note"])
		self.assertEqual(actions["skill:b:sync"]["state"], "skipped")
		self.assertIn("skill:a:sync", actions["skill:b:sync"]["note"])
		# The rejected card stays skipped, and says the run syncs it anyway.
		self.assertEqual(actions["skill:c:sync"]["state"], "skipped")
		self.assertIn("syncs this card too", actions["skill:c:sync"]["note"])
		self.assertEqual([a for a, v in actions.items() if v["state"] == "pending"],
			["skill:a:sync"])

	def test_the_first_accepted_id_carries_the_run_not_the_first_card(self):
		actions = self._init({"skill:a:sync": {"decision": "reject"},
			"skill:b:sync": {"decision": "accept"}, "skill:c:sync": {"decision": "accept"}})
		self.assertEqual([a for a, v in actions.items() if v["state"] == "pending"],
			["skill:b:sync"])
		self.assertEqual(actions["skill:c:sync"]["state"], "skipped")

	def test_a_lone_accept_names_the_cards_its_run_covers_and_commands_group_by_tokens(self):
		actions = self._init({"skill:a:sync": {"decision": "accept"}})
		self.assertEqual(actions["skill:a:sync"]["state"], "pending")
		# The one run still syncs the other two cards, and the note says so.
		self.assertIn("skill:c:sync", actions["skill:a:sync"]["note"])
		with tempfile.TemporaryDirectory(prefix="write-status-sync2-") as session:
			def tool(tid, command):
				return {"id": tid, "name": tid, "source": "skill-drift",
					"suggestions": [self._sync(tid, command)]}
			report = {"schema_version": 2, "contract_version": model.CONTRACT_VERSION,
				"report_id": "tool-update-review-20260822T113344Z",
				"generated_at": "2026-08-22T11:33:44Z", "machine": {}, "summary": {},
				"repo_context": {}, "highlights": [], "tools": [
					tool("skill:a", self.CMD), tool("skill:b", "bash  sync-upstream.sh"),
					tool("skill:c", "bash other.sh")]}
			feedback = {"report_id": report["report_id"], "tool_comments": {},
				"decisions": {f"skill:{x}:sync": {"decision": "accept"} for x in "abc"}}
			for name, obj in (("report.json", report), ("feedback.json", feedback)):
				with open(os.path.join(session, name), "w", encoding="utf-8") as fh:
					json.dump(obj, fh)
			subprocess.run([sys.executable, WRITE_STATUS, "init", session],
				capture_output=True, text=True, timeout=60, check=True)
			with open(os.path.join(session, "status.json"), encoding="utf-8") as fh:
				got = {a["id"]: a["state"] for a in json.load(fh)["actions"]}
		# Same tokens (extra whitespace) group; a different command does not.
		self.assertEqual(got, {"skill:a:sync": "pending", "skill:b:sync": "skipped",
			"skill:c:sync": "pending"})


class PinCheckGateTests(unittest.TestCase):
	def _session(self, tool):
		tmp = tempfile.mkdtemp(prefix="write-status-pin-test-")
		self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
		report = {
			"schema_version": 1, "contract_version": model.CONTRACT_VERSION,
			"report_id": "tool-update-review-20260907T000000Z",
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


# ── init's contract_version equality gate (references/schemas.md §1.1) ─────
# `init` does not display report.json, it synthesizes the whole action list
# from it. A report written against a different contract therefore does not
# fail visibly — it produces a plausible action list derived from fields that
# no longer mean what this code thinks they mean. No shim ships, so the
# only correct answer is a refusal, and a refusal nobody tested is a refusal
# nobody has.
class InitContractVersionGateTests(unittest.TestCase):
	def _init(self, contract_version, omit=False):
		"""Run `init` against a report carrying (or missing)
		`contract_version`; return (returncode, stderr, session_dir)."""
		session = tempfile.mkdtemp(prefix="write-status-cv-test-")
		self.addCleanup(shutil.rmtree, session, ignore_errors=True)
		report = {
			"schema_version": 2, "report_id": "tool-update-review-20260917T000000Z",
			"generated_at": "2026-09-17T00:00:00Z", "machine": {}, "summary": {},
			"repo_context": {}, "highlights": [],
			"tools": [{"id": "brew:azcopy", "name": "azcopy", "source": "brew",
				"suggestions": [_suggestion("brew:azcopy:upgrade", [])]}],
		}
		if not omit:
			report["contract_version"] = contract_version
		feedback = {"report_id": report["report_id"], "tool_comments": {},
			"decisions": {"brew:azcopy:upgrade": {"decision": "accept"}}}
		for name, obj in (("report.json", report), ("feedback.json", feedback)):
			with open(os.path.join(session, name), "w", encoding="utf-8") as fh:
				json.dump(obj, fh)
		p = subprocess.run([sys.executable, WRITE_STATUS, "init", session],
			capture_output=True, text=True, timeout=60)
		return p.returncode, p.stderr, session

	def test_the_current_contract_version_is_accepted(self):
		rc, stderr, session = self._init(model.CONTRACT_VERSION)
		self.assertEqual(rc, 0, stderr)
		self.assertTrue(os.path.exists(os.path.join(session, "status.json")))

	def test_a_mismatched_contract_version_is_refused_and_writes_nothing(self):
		for label, value, omit in (
			("one below", model.CONTRACT_VERSION - 1, False),
			("one above", model.CONTRACT_VERSION + 1, False),
			("a string of the right number", str(model.CONTRACT_VERSION), False),
			("null", None, False),
			("absent entirely", None, True),
		):
			with self.subTest(label):
				rc, stderr, session = self._init(value, omit=omit)
				self.assertNotEqual(rc, 0, label)
				self.assertIn("contract_version", stderr)
				# No half-written status.json: an apply pass that read one
				# would be running off exactly the shape this gate refused.
				self.assertFalse(os.path.exists(os.path.join(session, "status.json")), label)

	def test_the_refusal_names_the_remedy_not_just_the_mismatch(self):
		_, stderr, _ = self._init(1)
		self.assertIn(str(model.CONTRACT_VERSION), stderr)
		self.assertIn("Re-run the review", stderr)


# ── the three memory stores (contract/stores.json) ─────────────────────────
# `test_items.StoreLayoutTests` drives each writer to its pinned golden state.
# These cover the other half — what the writers REFUSE — because every one of
# these refusals stands between an accepted proposal and a store file that has
# been accumulating for months.
class MemoryStoreWriterTests(unittest.TestCase):
	def _state_home(self):
		tmp = tempfile.mkdtemp(prefix="write-status-store-test-")
		self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
		return tmp

	def _run(self, state_home, *argv):
		env = dict(os.environ, XDG_STATE_HOME=state_home)
		return subprocess.run([sys.executable, WRITE_STATUS, *argv],
			capture_output=True, text=True, timeout=60, env=env)

	def _store(self, state_home, filename):
		path = os.path.join(state_home, "tool-update-review", filename)
		with open(path, "r", encoding="utf-8") as fh:
			return json.load(fh)

	def test_method_note_writers_never_store_the_same_note_twice(self):
		"""Carried finding: a render that crashed after persisting leaves a
		record claiming nothing, so an accept on the page plans an add at
		apply for a note that did land. The writer is the one place that can
		see the store, so it is where the duplicate is refused."""
		state_home = self._state_home()
		for argv, key in ((("add-method-note", "--tool-id", "brew:jq"), "brew:jq"),
				(("add-global-method-note",), model.GLOBAL_METHOD_NOTE_KEY)):
			with self.subTest(key=key):
				for _ in range(2):
					p = self._run(state_home, *argv, "--topic", "t", "--note", "n")
					self.assertEqual(p.returncode, 0, p.stderr)
				self.assertIn("already in the store", p.stdout)
				p = self._run(state_home, *argv, "--topic", "t", "--note", "other text")
				self.assertEqual(p.returncode, 0, p.stderr)
				entries = self._store(state_home, "method-notes.json")[key]
				self.assertEqual([(e["topic"], e["note"]) for e in entries],
					[("t", "n"), ("t", "other text")])

	def test_method_note_writers_refuse_blank_topic_or_note(self):
		state_home = self._state_home()
		for command in ("add-method-note", "add-global-method-note"):
			for field in ("topic", "note"):
				for blank in ("", " \t\n"):
					with self.subTest(command=command, field=field, blank=blank):
						argv = [command] + (["--tool-id", "brew:jq"] if command == "add-method-note" else [])
						values = {"topic": "valid topic", "note": "valid note", field: blank}
						p = self._run(state_home, *argv, "--topic", values["topic"], "--note", values["note"])
						self.assertNotEqual(p.returncode, 0)
						self.assertIn(f"{field} must be a non-blank string", p.stderr)
						self.assertFalse(os.path.exists(os.path.join(state_home, "tool-update-review", model.METHOD_NOTES_STORE)))

	def test_an_empty_xdg_state_home_falls_back_like_the_shell(self):
		"""Review A6: `XDG_STATE_HOME=` (empty) must mean the default, as
		`${XDG_STATE_HOME:-…}` does in SKILL.md's snapshot and in the applier —
		never a store under `/tool-update-review/` at the filesystem root."""
		home = self._state_home()
		env = dict(os.environ, XDG_STATE_HOME="", HOME=home)
		p = subprocess.run([sys.executable, WRITE_STATUS, "add-watch-item", "--tool-id",
			"brew:nnn", "--topic", "plugin dir", "--note", "n"],
			capture_output=True, text=True, timeout=60, env=env)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertTrue(os.path.isfile(os.path.join(home, ".local", "state",
			"tool-update-review", model.WATCH_ITEMS_STORE)))
		import apply_converge
		import write_status
		old = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "HOME")}
		os.environ.update(XDG_STATE_HOME="", HOME=home)
		try:
			expected = os.path.join(home, ".local", "state", "tool-update-review", "x")
			self.assertEqual(write_status.store_path("x"), expected)
			self.assertEqual(apply_converge.live_store_path("x"), expected)
		finally:
			for k, v in old.items():
				if v is None:
					os.environ.pop(k, None)
				else:
					os.environ[k] = v

	def test_each_store_is_created_on_first_use(self):
		"""No pre-seeding: a fresh XDG_STATE_HOME has no
		state directory at all, and the first accepted proposal makes both
		the directory and the file."""
		for filename, argv in (
			(model.WATCH_ITEMS_STORE, ("add-watch-item", "--tool-id", "brew:nnn",
				"--topic", "plugin dir", "--note", "n")),
			(model.METHOD_NOTES_STORE, ("add-method-note", "--tool-id", "brew:nnn",
				"--topic", "where the changelog is", "--note", "n")),
			(model.METHOD_NOTES_STORE, ("add-global-method-note",
				"--topic", "tags beat release pages", "--note", "n")),
		):
			with self.subTest(argv[0]):
				state_home = self._state_home()
				self.assertFalse(os.path.exists(os.path.join(state_home, "tool-update-review")))
				p = self._run(state_home, *argv)
				self.assertEqual(p.returncode, 0, p.stderr)
				self.assertEqual(os.listdir(os.path.join(state_home, "tool-update-review")),
					[filename])

	def test_the_three_stores_do_not_collide_in_one_state_home(self):
		"""Three stores, two files: the per-tool notes and the global notes
		share `method-notes.json` and must not overwrite each other, and the
		watch store is a separate file throughout."""
		state_home = self._state_home()
		self.assertEqual(self._run(state_home, "add-watch-item", "--tool-id", "cask:x",
			"--topic", "w", "--note", "n").returncode, 0)
		self.assertEqual(self._run(state_home, "add-method-note", "--tool-id", "cask:x",
			"--topic", "m", "--note", "n").returncode, 0)
		self.assertEqual(self._run(state_home, "add-global-method-note",
			"--topic", "g", "--note", "n").returncode, 0)
		self.assertEqual(sorted(os.listdir(os.path.join(state_home, "tool-update-review"))),
			sorted([model.WATCH_ITEMS_STORE, model.METHOD_NOTES_STORE]))
		notes = self._store(state_home, model.METHOD_NOTES_STORE)
		self.assertEqual(sorted(notes), sorted(["cask:x", model.GLOBAL_METHOD_NOTE_KEY]))
		self.assertEqual([e["topic"] for e in notes["cask:x"]], ["m"])
		self.assertEqual([e["topic"] for e in notes[model.GLOBAL_METHOD_NOTE_KEY]], ["g"])
		self.assertEqual([e["topic"] for e in
			self._store(state_home, model.WATCH_ITEMS_STORE)["cask:x"]], ["w"])

	def test_a_second_write_appends_and_never_replaces(self):
		state_home = self._state_home()
		for topic in ("first", "second", "third"):
			self.assertEqual(self._run(state_home, "add-method-note", "--tool-id",
				"brew:jq", "--topic", topic, "--note", "n").returncode, 0)
		self.assertEqual([e["topic"] for e in
			self._store(state_home, model.METHOD_NOTES_STORE)["brew:jq"]],
			["first", "second", "third"])

	def test_a_tool_id_with_no_colon_is_refused_by_both_per_tool_writers(self):
		"""What reserves the colon-free namespace. Without this a mistyped
		`--tool-id global` writes into the global store, and a note about one
		tool silently becomes a note the next run applies to every tool."""
		for sub in ("add-watch-item", "add-method-note"):
			for tool_id in (model.GLOBAL_METHOD_NOTE_KEY, "nnn", ""):
				with self.subTest(f"{sub} {tool_id!r}"):
					state_home = self._state_home()
					p = self._run(state_home, sub, "--tool-id", tool_id,
						"--topic", "t", "--note", "n")
					self.assertNotEqual(p.returncode, 0)
					self.assertIn("--tool-id", p.stderr)
					self.assertFalse(os.path.exists(
						os.path.join(state_home, "tool-update-review")))

	def test_an_unreadable_existing_store_is_refused_never_replaced(self):
		"""The difference between "absent" and "unreadable" is the
		difference between creating a store and destroying one. `load_json`'s
		`default=` collapses them; `_load_store` is what keeps them apart."""
		for label, body in (
			("truncated json", '{"brew:jq": [{"topic": "t",'),
			("a json array", '["brew:jq"]'),
			("a json string", '"nope"'),
			("not json at all", 'brew:jq = topic'),
		):
			with self.subTest(label):
				state_home = self._state_home()
				path = os.path.join(state_home, "tool-update-review",
					model.METHOD_NOTES_STORE)
				os.makedirs(os.path.dirname(path))
				with open(path, "w", encoding="utf-8") as fh:
					fh.write(body)
				p = self._run(state_home, "add-method-note", "--tool-id", "brew:jq",
					"--topic", "t", "--note", "n")
				self.assertNotEqual(p.returncode, 0, label)
				self.assertIn("refusing to write", p.stderr)
				with open(path, "r", encoding="utf-8") as fh:
					self.assertEqual(fh.read(), body, label)

	def test_a_key_holding_something_other_than_an_array_is_refused(self):
		state_home = self._state_home()
		path = os.path.join(state_home, "tool-update-review", model.WATCH_ITEMS_STORE)
		os.makedirs(os.path.dirname(path))
		body = json.dumps({"brew:jq": {"topic": "t"}})
		with open(path, "w", encoding="utf-8") as fh:
			fh.write(body)
		p = self._run(state_home, "add-watch-item", "--tool-id", "brew:jq",
			"--topic", "t", "--note", "n")
		self.assertNotEqual(p.returncode, 0)
		self.assertIn("not an array of entries", p.stderr)
		with open(path, "r", encoding="utf-8") as fh:
			self.assertEqual(fh.read(), body)

	# ── remove-method-note / remove-global-method-note ─────────────────────
	# The apply-side half of render-time persistence: a note
	# render.py wrote and the user rejected on the page is withdrawn here.
	def _seed(self, state_home, *specs):
		"""specs: (subcommand, tool_id_or_None, topic, note)."""
		for sub, tool_id, topic, note in specs:
			argv = [sub] + (["--tool-id", tool_id] if tool_id else []) + ["--topic", topic, "--note", note]
			self.assertEqual(self._run(state_home, *argv).returncode, 0)

	def test_removal_matches_by_topic_and_deletes_an_emptied_key(self):
		state_home = self._state_home()
		self._seed(state_home,
			("add-method-note", "brew:jq", "where the changelog lives", "read the tag"),
			("add-method-note", "brew:yq", "keep", "keep this one"))
		p = self._run(state_home, "remove-method-note", "--tool-id", "brew:jq",
			"--topic", "where the changelog lives")
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertIn("removed method note", p.stdout)
		store = self._store(state_home, model.METHOD_NOTES_STORE)
		# The emptied key is DELETED, not left as [] — the shape the golden
		# pins, and a shape no reader can tell from absent.
		self.assertNotIn("brew:jq", store)
		self.assertEqual([e["topic"] for e in store["brew:yq"]], ["keep"])

	def test_removal_of_one_of_several_keeps_the_rest_in_order(self):
		state_home = self._state_home()
		self._seed(state_home,
			("add-method-note", "brew:jq", "first", "a"),
			("add-method-note", "brew:jq", "second", "b"),
			("add-method-note", "brew:jq", "third", "c"))
		p = self._run(state_home, "remove-method-note", "--tool-id", "brew:jq",
			"--topic", "second")
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertEqual([e["topic"] for e in
			self._store(state_home, model.METHOD_NOTES_STORE)["brew:jq"]], ["first", "third"])

	def test_not_found_is_an_explicit_error_and_writes_nothing(self):
		"""Never a silent success: a veto that removed nothing must say so,
		or a bad note stays in the store with a green apply log."""
		state_home = self._state_home()
		self._seed(state_home, ("add-method-note", "brew:jq", "present", "n"))
		path = os.path.join(state_home, "tool-update-review", model.METHOD_NOTES_STORE)
		with open(path, encoding="utf-8") as fh:
			before = fh.read()
		for tool_id, topic, note in (("brew:jq", "absent", None),
				("brew:other", "present", None), ("brew:jq", "present", "wrong note")):
			with self.subTest(f"{tool_id} {topic} {note}"):
				argv = ["remove-method-note", "--tool-id", tool_id, "--topic", topic]
				if note is not None:
					argv += ["--note", note]
				p = self._run(state_home, *argv)
				self.assertEqual(p.returncode, 1)
				self.assertIn("nothing to remove", p.stderr)
				self.assertIn("Nothing was written", p.stderr)
				with open(path, encoding="utf-8") as fh:
					self.assertEqual(fh.read(), before)

	def test_an_ambiguous_topic_is_refused_until_note_narrows_it(self):
		"""Two entries with one topic is a reachable store state — the
		writers refuse only an identical (topic, note), so a same-topic note
		with different text is a second entry — so
		the remover refuses to guess and lists the candidates; the exact
		note (from method-notes.render.json) narrows it to one."""
		state_home = self._state_home()
		self._seed(state_home,
			("add-method-note", "brew:jq", "dup", "first note"),
			("add-method-note", "brew:jq", "dup", "second note"))
		p = self._run(state_home, "remove-method-note", "--tool-id", "brew:jq", "--topic", "dup")
		self.assertEqual(p.returncode, 1)
		self.assertIn("2 entries", p.stderr)
		self.assertIn("refusing to guess", p.stderr)
		self.assertIn("first note", p.stderr)
		self.assertIn("second note", p.stderr)
		self.assertEqual(len(self._store(state_home, model.METHOD_NOTES_STORE)["brew:jq"]), 2)
		p = self._run(state_home, "remove-method-note", "--tool-id", "brew:jq",
			"--topic", "dup", "--note", "second note")
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertEqual([e["note"] for e in
			self._store(state_home, model.METHOD_NOTES_STORE)["brew:jq"]], ["first note"])

	def test_the_global_remover_takes_no_tool_id_and_the_per_tool_one_refuses_the_reserved_key(self):
		state_home = self._state_home()
		self._seed(state_home,
			("add-global-method-note", None, "tags beat release pages", "cite the tag"),
			("add-method-note", "brew:jq", "local", "n"))
		p = self._run(state_home, "remove-method-note", "--tool-id",
			model.GLOBAL_METHOD_NOTE_KEY, "--topic", "tags beat release pages")
		self.assertNotEqual(p.returncode, 0)
		self.assertIn("--tool-id", p.stderr)
		store = self._store(state_home, model.METHOD_NOTES_STORE)
		self.assertEqual(len(store[model.GLOBAL_METHOD_NOTE_KEY]), 1)
		p = self._run(state_home, "remove-global-method-note", "--topic", "tags beat release pages")
		self.assertEqual(p.returncode, 0, p.stderr)
		store = self._store(state_home, model.METHOD_NOTES_STORE)
		self.assertNotIn(model.GLOBAL_METHOD_NOTE_KEY, store)
		self.assertIn("brew:jq", store)

	def test_an_unreadable_store_is_refused_by_the_remover_too(self):
		state_home = self._state_home()
		path = os.path.join(state_home, "tool-update-review", model.METHOD_NOTES_STORE)
		os.makedirs(os.path.dirname(path))
		body = '{"brew:jq": [{"topic": "t",'
		with open(path, "w", encoding="utf-8") as fh:
			fh.write(body)
		p = self._run(state_home, "remove-method-note", "--tool-id", "brew:jq", "--topic", "t")
		self.assertEqual(p.returncode, 1)
		self.assertIn("refusing to write", p.stderr)
		with open(path, encoding="utf-8") as fh:
			self.assertEqual(fh.read(), body)

	def test_write_then_remove_matches_the_pinned_golden(self):
		"""contract/stores.json pins the shape after one write and one
		removal: an empty object, not {"brew:attention": []}."""
		with open(os.path.join(SCRIPT_DIR, "contract", "stores.json"), encoding="utf-8") as fh:
			golden = json.load(fh)["method-notes.json"]
		state_home = self._state_home()
		entry = golden["after_one_write"]["brew:attention"][0]
		self._seed(state_home, ("add-method-note", "brew:attention", entry["topic"], entry["note"]))
		p = self._run(state_home, "remove-method-note", "--tool-id", "brew:attention",
			"--topic", entry["topic"], "--note", entry["note"])
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertEqual(self._store(state_home, model.METHOD_NOTES_STORE),
			golden["after_one_write_then_one_removal"])

	def test_every_entry_carries_exactly_the_pinned_three_fields(self):
		state_home = self._state_home()
		self._run(state_home, "add-global-method-note", "--topic", "t", "--note", "n")
		(entry,) = self._store(state_home,
			model.METHOD_NOTES_STORE)[model.GLOBAL_METHOD_NOTE_KEY]
		self.assertEqual(set(entry),
			set(model.MEMORY_STORES[model.METHOD_NOTES_STORE]["entry"]))


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
