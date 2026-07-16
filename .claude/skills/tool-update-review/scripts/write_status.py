#!/usr/bin/env python3
"""
write_status.py — atomic status.json read-modify-write helper (see
references/apply.md for steps 6-9 mechanics, references/schemas.md §Status
Object, and references/rendering-results.md §Turn-Based Threads).

Every subcommand does one atomic .tmp + os.replace() write, matching the
"one state transition per write" discipline status.json's design requires.
This replaces the ad hoc atomic-write code that used to get re-typed by
hand for every status.json update during apply.

Subcommands:
  init            <session_dir>                      write the initial status.json from feedback.json + report.json
  set-action      <session_dir> <id> <state> [--note TEXT] [--detail-file FILE] [--thread-turn-file FILE]
  touch           <session_dir>                       bump written_at only (heartbeat, no other field changes)
  sync-turns      <session_dir>                        merge followup_turns.json into pending_followups[].turns / actions[].thread
  add-followup    <session_dir> <followup-json-file>  append (or replace, by id) a pending_followups entry
  append-changelog <session_dir> <entry-file>...       append changelog entries to status.json AND changelog.md
  add-watch-item  --tool-id ID --topic TEXT --note TEXT   append an accepted watch-item proposal's {topic, note,
                                                           added_at} to watch-items.json (references/apply.md §Watch
                                                           Items (Writing)). No <session_dir> — this file is
                                                           machine-global, not scoped to any one review session, and
                                                           this subcommand never touches status.json.
  finalize        <session_dir> [--phase discussing|done] --recap TEXT|--recap-file FILE
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def now_iso() -> str:
	return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_atomic(path: str, obj) -> None:
	# Duplicates server.py's own write_json_atomic deliberately, not by
	# oversight: render.py copies server.py standalone into each session
	# dir, so it has to stay import-free of anything outside that one file.
	# A shared helper module would need render.py to also copy it, adding
	# fragile cross-file coupling to save ~8 lines.
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as fh:
		json.dump(obj, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")
	os.replace(tmp, path)


def load_json(path: str, default=None):
	try:
		with open(path, "r", encoding="utf-8") as fh:
			return json.load(fh)
	except (FileNotFoundError, json.JSONDecodeError):
		return default


def status_path(session_dir: str) -> str:
	return os.path.join(session_dir, "status.json")


def load_status(session_dir: str) -> dict:
	status = load_json(status_path(session_dir))
	if status is None:
		print(f"Error: no status.json in {session_dir!r} — run 'init' first", file=sys.stderr)
		sys.exit(1)
	return status


# ── init ────────────────────────────────────────────────────────────────
def cmd_init(args):
	session_dir = args.session_dir
	feedback = load_json(os.path.join(session_dir, "feedback.json"))
	report = load_json(os.path.join(session_dir, "report.json"))
	if feedback is None or report is None:
		print("Error: feedback.json and report.json must both exist first", file=sys.stderr)
		sys.exit(1)

	suggestions_by_id = {
		sug["id"]: (tool, sug)
		for tool in report.get("tools", [])
		for sug in tool.get("suggestions", [])
	}

	actions = []
	touches_dotfiles = False
	touches_macos_setup = False

	# Iterate every suggestion in report.json (suggestion order), not just
	# the ids present in feedback.json's decisions map — the front end only
	# gates Submit on incompatible-severity suggestions (references/rendering-report.md §Page Layout), so
	# a lower-severity suggestion can be legitimately submitted with no
	# decision at all and simply never appear in `decisions`. Treating an
	# absent id as "skip the action" (the previous behavior) silently
	# dropped it from the action list and from summary.undecided entirely.
	decisions = feedback.get("decisions", {})
	for sid, entry in suggestions_by_id.items():
		dec = decisions.get(sid, {})
		decision = dec.get("decision")  # None if truly undecided
		state = "pending" if decision in ("accept", "discuss") else "skipped"
		label = entry[1].get("title", sid)
		actions.append({
			"id": sid, "label": label, "decision": decision, "state": state,
			"started_at": None, "finished_at": None, "note": None, "detail": [], "thread": [],
		})
		if decision == "accept":
			_, sug = entry
			for tf in sug.get("target_files", []):
				path = tf.get("path", "")
				if path.startswith("dotfiles/"):
					touches_dotfiles = True
				else:
					touches_macos_setup = True

		# One investigation action per discuss decision *with a comment*
		# (references/apply.md §Turn-Based Threads) — a bare discuss with no comment has nothing to
		# investigate.
		if decision == "discuss" and dec.get("comment"):
			actions.append({
				"id": f"investigate:{sid}", "label": f"Investigate: {sid} — {dec['comment']}",
				"decision": None, "state": "pending",
				"started_at": None, "finished_at": None, "note": None, "detail": [], "thread": [],
			})

	# Investigation actions — one per tool_comments entry (references/apply.md §Tool Comments and Discuss).
	for tool_id, comment in feedback.get("tool_comments", {}).items():
		actions.append({
			"id": f"investigate:{tool_id}", "label": f"Investigate: {tool_id} — {comment}",
			"decision": None, "state": "pending",
			"started_at": None, "finished_at": None, "note": None, "detail": [], "thread": [],
		})

	# Synthetic commit/push actions — only for repos that will actually get
	# a commit (references/apply.md §Initializing status.json; §Push and Terminal Status) — never render a no-op action.
	if touches_dotfiles:
		actions.append({"id": "commit:dotfiles", "label": "Commit changes in dotfiles submodule",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": []})
	if touches_dotfiles or touches_macos_setup:
		actions.append({"id": "commit:macos-setup", "label": "Commit changes in macos-setup",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": []})
	if touches_dotfiles:
		actions.append({"id": "push:dotfiles", "label": "Push dotfiles to origin",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": []})
	if touches_dotfiles or touches_macos_setup:
		actions.append({"id": "push:macos-setup", "label": "Push macos-setup to origin",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": []})

	status = {
		"schema_version": 2,
		"report_id": feedback.get("report_id", report.get("report_id", "")),
		"phase": "applying",
		"started_at": now_iso(),
		"written_at": now_iso(),
		"actions": actions,
		"pending_followups": [],
		"recap": "",
		"changelog_entries": [],
		"summary": {"applied": 0, "rejected": 0, "discussed": 0, "undecided": 0, "failed": 0},
		"done": False,
	}
	write_json_atomic(status_path(session_dir), status)
	print(f"Initialized status.json with {len(actions)} actions")


# ── set-action ──────────────────────────────────────────────────────────
def cmd_set_action(args):
	status = load_status(args.session_dir)
	action = next((a for a in status["actions"] if a["id"] == args.action_id), None)
	if action is None:
		print(f"Error: no action with id {args.action_id!r}", file=sys.stderr)
		sys.exit(1)

	action["state"] = args.state
	if args.state == "running":
		action["started_at"] = now_iso()
	elif args.state in ("done", "failed", "skipped"):
		action["finished_at"] = now_iso()
	if args.note is not None:
		action["note"] = args.note
	if args.detail_file:
		with open(args.detail_file, "r", encoding="utf-8") as fh:
			action["detail"] = fh.read().splitlines()[-10:]
	if args.thread_turn_file:
		# Appends an agent turn onto this action's own debug thread
		# (references/rendering-results.md §Turn-Based Threads) — the mechanism references/apply.md
		# §Turn-Based Threads means when it says to "append an agent turn answering or asking
		# back" on a failed action's thread.
		with open(args.thread_turn_file, "r", encoding="utf-8") as fh:
			turn = json.load(fh)
		thread = action.setdefault("thread", [])
		turn.setdefault("turn", len(thread) + 1)
		turn.setdefault("author", "agent")
		turn.setdefault("at", now_iso())
		thread.append(turn)

	status["written_at"] = now_iso()
	write_json_atomic(status_path(args.session_dir), status)
	print(f"{args.action_id}: {args.state}")


# ── touch (heartbeat, task #24) ──────────────────────────────────────────
def cmd_touch(args):
	status = load_status(args.session_dir)
	status["written_at"] = now_iso()
	write_json_atomic(status_path(args.session_dir), status)
	print("touched written_at")


# ── sync-turns (merge browser-submitted turns, references/apply.md §Turn-Based Threads) ─
def cmd_sync_turns(args):
	status = load_status(args.session_dir)
	turns_path = os.path.join(args.session_dir, "followup_turns.json")
	all_turns = load_json(turns_path, default={})

	# followup_turns.json (server.py's /followup handler) only ever records
	# *user* turns, numbered from its own file — its length is unrelated to
	# a thread's real length in status.json once even one agent turn has
	# been appended there directly (references/apply.md §Turn-Based Threads has no subcommand for
	# that; it's appended straight into status.json). Comparing raw array
	# lengths (`len(new) > len(existing)`) breaks both ways: it can miss a
	# genuinely new user turn (if an agent turn already pushed `existing`
	# longer than `new`) and, when it does trigger, it *overwrites* the
	# whole thread with `new` — silently erasing any agent turn that was
	# never in followup_turns.json to begin with. Track how many user turns
	# from followup_turns.json have already been pulled into status.json
	# per thread, in a small side file, and only ever *append* the ones
	# beyond that — never replace the array wholesale.
	sync_state_path = os.path.join(args.session_dir, ".followup_turns_synced.json")
	synced_counts = load_json(sync_state_path, default={})

	by_id = {f["id"]: f for f in status.get("pending_followups", [])}
	action_by_id = {a["id"]: a for a in status.get("actions", [])}
	changed = False

	for thread_id, turns in all_turns.items():
		target = None
		field = None
		if thread_id in by_id:
			target, field = by_id[thread_id], "turns"
		elif thread_id in action_by_id:
			target, field = action_by_id[thread_id], "thread"
		if target is None:
			continue

		already_synced = synced_counts.get(thread_id, 0)
		new_turns = turns[already_synced:]
		if not new_turns:
			continue
		target.setdefault(field, []).extend(new_turns)
		synced_counts[thread_id] = len(turns)
		changed = True

	if changed:
		status["written_at"] = now_iso()
		write_json_atomic(status_path(args.session_dir), status)
		write_json_atomic(sync_state_path, synced_counts)
		print("synced new turns")
	else:
		print("no new turns")


# ── add-followup ──────────────────────────────────────────────────────────
def cmd_add_followup(args):
	status = load_status(args.session_dir)
	with open(args.followup_file, "r", encoding="utf-8") as fh:
		followup = json.load(fh)
	followup.setdefault("resolution", "pending")
	followup.setdefault("turns", [])

	followups = status.setdefault("pending_followups", [])
	existing_idx = next((i for i, f in enumerate(followups) if f["id"] == followup["id"]), None)
	if existing_idx is not None:
		followups[existing_idx] = followup
	else:
		followups.append(followup)

	status["written_at"] = now_iso()
	write_json_atomic(status_path(args.session_dir), status)
	print(f"added/updated followup {followup['id']!r}")


# ── append-changelog (status.json + the durable changelog.md audit trail) ─
def cmd_append_changelog(args):
	status = load_status(args.session_dir)
	entries = []
	for path in args.entry_files:
		with open(path, "r", encoding="utf-8") as fh:
			entries.append(fh.read().rstrip("\n"))

	status["changelog_entries"] = status.get("changelog_entries", []) + entries
	status["written_at"] = now_iso()
	write_json_atomic(status_path(args.session_dir), status)

	changelog_md = os.path.expanduser(
		os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/tool-update-review/changelog.md"
	)
	os.makedirs(os.path.dirname(changelog_md), exist_ok=True)
	with open(changelog_md, "a", encoding="utf-8") as fh:
		for entry in entries:
			fh.write("\n\n" + entry + "\n")

	print(f"appended {len(entries)} changelog entries")


# ── add-watch-item (references/apply.md §Watch Items (Writing)) ───────────
def cmd_add_watch_item(args):
	# Deliberately independent of any session_dir/status.json — watch-items.json
	# is a machine-global audit trail read at *research* time on a later run
	# (references/research.md §Watch Items (Reading)), not part of this
	# session's own state. Same directory/atomic-write pattern as changelog.md
	# (append-changelog above).
	watch_path = os.path.expanduser(
		os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/tool-update-review/watch-items.json"
	)
	os.makedirs(os.path.dirname(watch_path), exist_ok=True)
	watch_items = load_json(watch_path, default={})
	if not isinstance(watch_items, dict):
		watch_items = {}
	entries = watch_items.setdefault(args.tool_id, [])
	entries.append({
		"topic": args.topic,
		"note": args.note,
		"added_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
	})
	write_json_atomic(watch_path, watch_items)
	print(f"added watch item for {args.tool_id!r}: {args.topic!r}")


# ── finalize ──────────────────────────────────────────────────────────────
def cmd_finalize(args):
	status = load_status(args.session_dir)
	recap = args.recap
	if args.recap_file:
		with open(args.recap_file, "r", encoding="utf-8") as fh:
			recap = fh.read()
	if recap is not None:
		status["recap"] = recap

	status["phase"] = args.phase
	status["done"] = (args.phase == "done")

	actions = status.get("actions", [])
	summary = {"applied": 0, "rejected": 0, "discussed": 0, "undecided": 0, "failed": 0}
	for a in actions:
		if a.get("decision") == "accept":
			if a["state"] == "done":
				summary["applied"] += 1
			elif a["state"] == "failed":
				summary["failed"] += 1
		elif a.get("decision") == "reject":
			summary["rejected"] += 1
		elif a.get("decision") == "discuss":
			summary["discussed"] += 1
		elif a.get("decision") is None and a["state"] == "skipped":
			summary["undecided"] += 1
	status["summary"] = summary
	status["written_at"] = now_iso()
	write_json_atomic(status_path(args.session_dir), status)
	print(f"finalized: phase={args.phase}")


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = parser.add_subparsers(dest="cmd", required=True)

	p = sub.add_parser("init")
	p.add_argument("session_dir")
	p.set_defaults(func=cmd_init)

	p = sub.add_parser("set-action")
	p.add_argument("session_dir")
	p.add_argument("action_id")
	p.add_argument("state", choices=["pending", "running", "done", "failed", "skipped"])
	p.add_argument("--note")
	p.add_argument("--detail-file")
	p.add_argument("--thread-turn-file")
	p.set_defaults(func=cmd_set_action)

	p = sub.add_parser("touch")
	p.add_argument("session_dir")
	p.set_defaults(func=cmd_touch)

	p = sub.add_parser("sync-turns")
	p.add_argument("session_dir")
	p.set_defaults(func=cmd_sync_turns)

	p = sub.add_parser("add-followup")
	p.add_argument("session_dir")
	p.add_argument("followup_file")
	p.set_defaults(func=cmd_add_followup)

	p = sub.add_parser("append-changelog")
	p.add_argument("session_dir")
	p.add_argument("entry_files", nargs="+")
	p.set_defaults(func=cmd_append_changelog)

	p = sub.add_parser("add-watch-item")
	p.add_argument("--tool-id", required=True)
	p.add_argument("--topic", required=True)
	p.add_argument("--note", required=True)
	p.set_defaults(func=cmd_add_watch_item)

	p = sub.add_parser("finalize")
	p.add_argument("session_dir")
	p.add_argument("--phase", choices=["discussing", "done"], default="done")
	p.add_argument("--recap")
	p.add_argument("--recap-file")
	p.set_defaults(func=cmd_finalize)

	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
