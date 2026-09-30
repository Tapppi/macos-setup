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
                                                       REFUSES state "done" for a suggestion carrying a
                                                       target_version on a check_pin.py-checkable source
                                                       (assemble.PIN_CHECKABLE_SOURCES) unless record-pin-check
                                                       already recorded a matching "verify" result for it
                                                       (WP5/I2: references/apply.md §Pinning the reviewed version)
  record-pin-check <session_dir> <id> {preflight|verify} <result-json-file>   record one scripts/check_pin.py
                                                           JSON result onto an action's pin_checks{}; the "verify"
                                                           phase is what set-action's "done" gate above reads.
                                                           Validates required keys/types and REFUSES (nothing
                                                           written) when the result's own "phase" disagrees with
                                                           the {preflight|verify} it is being filed under.
  touch           <session_dir>                       bump written_at only (heartbeat, no other field changes)
  sync-turns      <session_dir>                        merge followup_turns.json into pending_followups[].turns / actions[].thread
  add-followup    <session_dir> <followup-json-file>  append (or replace, by id) a pending_followups entry
  append-changelog <session_dir> <entry-file>...       append changelog entries to status.json AND changelog.md
  add-watch-item  --tool-id ID --topic TEXT --note TEXT   append an accepted watch-item proposal's {topic, note,
                                                           added_at} to watch-items.json (references/apply.md §Watch
                                                           Items (Writing)). No <session_dir> — this file is
                                                           machine-global, not scoped to any one review session, and
                                                           this subcommand never touches status.json.
  remove-method-note --tool-id ID --topic TEXT [--note TEXT]
                                                       remove ONE method-note entry by exact (tool id, topic[, note])
                                                       match — never by index. Not found is an error (exit 1, nothing
                                                       written); more than one match REFUSES and lists them, so pass
                                                       --note to disambiguate. An emptied key is deleted. This is the
                                                       apply-side half of render-time persistence: a note render.py
                                                       wrote and the user then rejected on the page comes out here
                                                       (references/rendering-report.md §Method Notes).
  remove-global-method-note --topic TEXT [--note TEXT]   the same removal against the reserved global key.
  add-method-note --tool-id ID --topic TEXT --note TEXT   the same write, one store over: an accepted method-note
                                                           proposal's {topic, note, added_at} into method-notes.json
                                                           (references/apply.md §Method Notes (Writing)). Idempotent:
                                                           an entry with the same topic AND note is not written twice. A watch item
                                                           says what to tell the user if it happens; a method note says
                                                           how to research this tool correctly next time.
  add-global-method-note --topic TEXT --note TEXT         the third store: a method note that holds across many tools,
                                                           written under method-notes.json's reserved "global" key.
                                                           Promotion-only — convergence is the one stage that can see
                                                           a note generalises (references/schemas.md §1.7b Scope).
  finalize        <session_dir> [--phase discussing|done] --recap TEXT|--recap-file FILE
"""
from __future__ import annotations

import argparse
import json
import shlex
import os
import sys
from datetime import datetime, timezone

# Same directory — see check_pin.py's own comment on why this needs no
# sys.path manipulation. Used only for PIN_CHECKABLE_SOURCES: the "done" gate
# below must ask the same question check_pin.py's own --source choices ask,
# from the same single set, not a second hand-typed list that can drift.
import assemble
# For CONTRACT_VERSION and MEMORY_STORES — `init`'s equality gate and the
# three store writers read the one published contract rather than re-typing
# either the number or the file names.
import items


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
# ── method notes at init (the apply half of render-time persistence) ───────
# render.py persisted every convergence-reviewed method-note proposal at
# render and recorded, per suggestion id, exactly what happened to it in
# METHOD_NOTES_RENDER_RECORD. init reads that record so a decision on a
# method note becomes the RIGHT action, planned per store entry
# (_method_note_actions): a vetoed stored entry is ONE pending withdraw (the
# exact remover invocation in `detail`), an accepted stored note is already
# done, a not-stored entry (render's write failed, or convergence did not
# review) is ONE pending add when accepted — or, for a failed write, when
# left undecided — and any modification instruction is a pending "Modify
# method note" investigation. Without this, a veto on the page was a
# `skipped` action that never ran and the note stayed in the store for
# every future run.
METHOD_NOTES_RENDER_RECORD = "method-notes.render.json"
_RENDER_RECORD_BUCKETS = ("written", "already_present", "failed", "unreviewed")


def _load_render_record(session_dir: str, report_id: str):
	"""→ {suggestion_id: (bucket, entry)} from the render record, or None
	when the record is absent, unreadable, or written for a different report
	(said on stderr: no note is then known to be stored, so an accept writes
	at apply and anything else writes nothing — the pre-render-persist
	behaviour).

	The record is trusted only for the report it names. A session dir
	re-used for a second review, or a record copied in beside the wrong
	report.json, would otherwise map this report's suggestion ids onto
	another run's outcomes — a veto withdrawing a note this run never
	wrote, or an accept read as "already in the store" when nothing was."""
	path = os.path.join(session_dir, METHOD_NOTES_RENDER_RECORD)
	record = load_json(path)
	if not isinstance(record, dict):
		print(f"warning: no readable {METHOD_NOTES_RENDER_RECORD} in {session_dir} — "
			f"no method note is known to be stored (accept writes at apply, "
			f"nothing else writes)", file=sys.stderr)
		return None
	if record.get("report_id") != report_id:
		print(f"warning: {METHOD_NOTES_RENDER_RECORD} in {session_dir} was written for "
			f"report {record.get('report_id')!r}, not {report_id!r} — ignored; no "
			f"method note is known to be stored (accept writes at apply, nothing "
			f"else writes)", file=sys.stderr)
		return None
	by_id = {}
	for bucket in _RENDER_RECORD_BUCKETS:
		for entry in record.get(bucket) or []:
			if isinstance(entry, dict) and isinstance(entry.get("suggestion_id"), str):
				by_id[entry["suggestion_id"]] = (bucket, entry)
	return by_id


def _store_command(verb: str, key: str, topic: str, note: str) -> str:
	"""The exact writer/remover invocation for one entry, shell-quoted, so
	the apply agent runs what the record says rather than retyping it."""
	global_key = key == items.GLOBAL_METHOD_NOTE_KEY
	argv = ["scripts/write_status.py",
		f"{verb}-global-method-note" if global_key else f"{verb}-method-note"]
	if not global_key:
		argv += ["--tool-id", key]
	argv += ["--topic", topic, "--note", note]
	return shlex.join(argv)


def _blank_action(sid: str, label: str, decision, state: str) -> dict:
	return {"id": sid, "label": label, "decision": decision, "state": state,
		"started_at": None, "finished_at": None, "note": None, "detail": [],
		"thread": [], "pin_checks": {}}


def global_method_note_ids(report: dict) -> set:
	"""The suggestion ids convergence routed to the global method-note store
	(`report.convergence.memory.promoted_to_global`, and a re-homed note
	with `scope: "global"`). ONE rule, used by render.py to choose the key
	it writes under and by init's no-record fallback, so a promoted note is
	keyed `global` on both sides even when the render record is missing."""
	conv = report.get("convergence") if isinstance(report.get("convergence"), dict) else {}
	memory = conv.get("memory") if isinstance(conv.get("memory"), dict) else {}
	out = {s for s in (memory.get("promoted_to_global") or []) if isinstance(s, str)}
	for row in memory.get("rehomed_to_method_note") or []:
		if isinstance(row, dict) and row.get("scope") == "global" \
				and isinstance(row.get("new_note_id"), str):
			out.add(row["new_note_id"])
	return out


def _method_note_actions(notes: list, record, report: dict) -> list:
	"""→ the actions for every kind:"method-note" suggestion, planned PER
	STORE ENTRY (references/apply.md §Executing `method-note` Suggestions;
	the model is stated once in references/rendering-report.md §Method
	Notes, and the page follows the same one).

	Three units, kept apart. A **suggestion id** is what the page and the
	decisions map act on. A **store entry** `(key, topic, note)` is what
	render writes and dedupes on — several ids map to one entry when two
	tools propose one note and convergence promotes both; it is the unit
	of persistence and of veto. A **render outcome** per id (`written` /
	`already_present` / `failed` / `unreviewed`, or none) is what is true
	about storage: an entry is STORED iff any of its ids was written or
	already present; `failed` and `unreviewed` are NOT stored, whatever the
	run's convergence state.

	Per entry: any reject is a veto of the entry. Stored + veto → ONE
	pending withdraw carried by the first rejected id, the other ids
	skipped naming it (never "done, in store" — the entry is going).
	Stored, no veto → done per id (a discuss stays pending). Not stored +
	veto → nothing written. Not stored, no veto → ONE pending add when an
	id accepted it — or, for a STORE-WRITE FAILED entry, when an id is merely
	undecided: render meant to store it and the user saw no reason not to,
	so the failed write is the one persisted-path case that still needs
	the write. The key always travels from the record. Comments are per
	id: a non-reject comment is a pending "Modify method note"."""
	global_ids = global_method_note_ids(report)
	entries = {}
	for sid, tool, sug, dec in notes:
		topic = sug.get("method_topic") if isinstance(sug.get("method_topic"), str) else ""
		note = sug.get("method_note") if isinstance(sug.get("method_note"), str) else ""
		bucket, rec = (record or {}).get(sid, (None, None))
		if rec is not None and isinstance(rec.get("key"), str):
			key = rec["key"]
		else:
			key = items.GLOBAL_METHOD_NOTE_KEY if sid in global_ids else tool.get("id", "")
		entry = entries.setdefault((key, topic, note), {"members": [], "buckets": set(), "reasons": [], "malformed": False})
		entry["malformed"] |= not topic.strip() or not note.strip() or (
			bucket == "failed" and not (rec and isinstance(rec.get("key"), str) and rec["key"].strip()))
		entry["members"].append((sid, sug, dec))
		entry["buckets"].add(bucket)
		if rec is not None and isinstance(rec.get("reason"), str):
			entry["reasons"].append(rec["reason"])

	actions = []
	for (key, topic, note), entry in entries.items():
		members = sorted(entry["members"], key=lambda m: m[0])
		buckets = entry["buckets"]
		storage = "stored" if buckets & {"written", "already_present"} \
			else "failed" if "failed" in buckets \
			else "unreviewed" if "unreviewed" in buckets else "no render record"
		ids = [sid for sid, _, _ in members]
		shared = f" (one store entry, {len(ids)} suggestion ids: {', '.join(ids)})" if len(ids) > 1 else ""
		rejected = [sid for sid, _, dec in members if dec.get("decision") == "reject"]
		writers = [] if rejected or storage == "stored" else [
			sid for sid, _, dec in members if dec.get("decision") == "accept"
		] or ([sid for sid, _, dec in members if dec.get("decision") is None]
			if storage == "failed" else [])
		reason = "; ".join(dict.fromkeys(entry["reasons"])) or storage
		for sid, sug, dec in members:
			decision = dec.get("decision")
			comment = dec.get("comment").strip() if isinstance(dec.get("comment"), str) else ""
			title = sug.get("title", sid)
			if entry["malformed"]:
				action = _blank_action(sid, title, decision, "skipped")
				action["note"] = "Malformed method note — nothing written"
				action["detail"] = ["method_topic/method_note missing or empty, or failed proposal has no store key"]
			elif storage == "stored" and rejected:
				if sid == rejected[0]:
					action = _blank_action(sid, f"Withdraw method note: {topic}", decision, "pending")
					action["note"] = "Vetoed on the page — remove the entry render wrote" + shared \
						+ (f" — reason: {comment}" if comment else "")
					action["detail"] = [_store_command("remove", key, topic, note)]
				else:
					action = _blank_action(sid, title, decision, "skipped")
					action["note"] = f"Same store entry as {rejected[0]!r}, which withdraws it"
			elif storage == "stored":
				if decision == "discuss":
					action = _blank_action(sid, title, decision, "pending")
					action["note"] = "In store (persisted at render) — under discussion"
				else:
					action = _blank_action(sid, title, decision, "done")
					action["finished_at"] = now_iso()
					action["note"] = f"In store — persisted at render under {key!r} ({METHOD_NOTES_RENDER_RECORD})"
			elif writers and sid == writers[0]:
				action = _blank_action(sid, f"Add method note: {topic}", decision, "pending")
				action["note"] = f"Not stored ({storage}: {reason}) — write it now{shared}"
				action["detail"] = [_store_command("add", key, topic, note)]
			elif writers:
				action = _blank_action(sid, title, decision, "skipped")
				action["note"] = f"Same store entry as {writers[0]!r}, which writes it"
			elif rejected:
				action = _blank_action(sid, title, decision, "skipped")
				action["note"] = f"Not stored ({storage}) and vetoed — nothing written"
			elif decision == "discuss":
				action = _blank_action(sid, title, decision, "pending")
				action["note"] = f"Not stored ({storage}) — under discussion"
			else:
				action = _blank_action(sid, title, decision, "skipped")
				action["note"] = f"Not stored ({storage}) and not accepted — nothing written"
			actions.append(action)
			if comment and decision != "reject":
				actions.append(_blank_action(f"investigate:{sid}",
					f"Modify method note: {topic} — {comment}", None, "pending"))
	return actions


def _group_sync_actions(actions: list, suggestions_by_id: dict) -> None:
	"""Collapse skill-drift `:sync` actions that carry the same command to ONE
	pending action, in place (references/apply.md §Skill-Drift Remediation).

	`sync-upstream.sh` takes no vendor argument, so every drifted card of every
	vendor carries the identical command and one run resolves them all; per
	card, `init` would plan N runs of it. Grouped the way method notes are
	grouped per store entry: the first ACCEPTED id keeps the pending action
	and carries the run; every other accepted id is `skipped` with a note
	naming it; a rejected id stays `skipped` and its note says the run does
	not honour the rejection (there is no per-vendor sync, so it syncs that
	card too). Commands are compared token-wise (`split()`), as assembly
	does. A `discuss` is not an accept and keeps its own pending action."""
	groups = {}
	for sid, (_, sug) in suggestions_by_id.items():
		command = sug.get("command")
		if sug.get("kind") == "upgrade" and sid.endswith(":sync") \
				and isinstance(command, str) and command.strip():
			groups.setdefault(tuple(command.split()), []).append(sid)
	by_id = {a["id"]: a for a in actions}
	for ids in groups.values():
		accepted = [sid for sid in ids if by_id[sid]["decision"] == "accept"]
		if not accepted:
			continue
		carrier = accepted[0]
		for sid in ids:
			action = by_id[sid]
			if sid == carrier:
				if len(ids) > 1:
					action["note"] = ("One run covers every drifted card: "
						+ ", ".join(ids))
			elif action["decision"] == "accept":
				action["state"] = "skipped"
				action["note"] = f"Same command as {carrier!r}, which runs it once for every drifted card"
			elif action["decision"] == "reject":
				action["note"] = (f"Rejected, but there is no per-skill sync: the run for {carrier!r} "
					f"syncs this card too")


def cmd_init(args):
	session_dir = args.session_dir
	feedback = load_json(os.path.join(session_dir, "feedback.json"))
	report = load_json(os.path.join(session_dir, "report.json"))
	if feedback is None or report is None:
		print("Error: feedback.json and report.json must both exist first", file=sys.stderr)
		sys.exit(1)

	# ── contract_version equality gate (references/schemas.md §1.1) ──────
	# The same exact-equality rule render.py applies to schema_version, for
	# the same reason and with no shim: `init` does not read report.json to
	# display it, it *synthesizes the action list* from it — every suggestion
	# id, every accept, every target_files path that decides which repos get
	# a commit action. Read a report written against a different contract and
	# the failure is not a visibly broken page, it is an apply pass driving a
	# plausible-looking action list derived from fields that no longer mean
	# what this code thinks they mean. An old report is re-read by checking
	# out the pipeline that wrote it.
	if report.get("contract_version") != items.CONTRACT_VERSION:
		print(
			f"Error: report.json contract_version must be {items.CONTRACT_VERSION}, got "
			f"{report.get('contract_version')!r} — refusing to synthesize actions from an "
			f"unknown report shape (references/schemas.md §1.1; no migration shim "
			f"exists). Re-run the review, or check out the pipeline this report "
			f"was written with.", file=sys.stderr)
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
	notes = []
	for sid, (tool, sug) in suggestions_by_id.items():
		if sug.get("kind") == "method-note":
			dec = decisions.get(sid, {})
			notes.append((sid, tool, sug, dec if isinstance(dec, dict) else {}))
	render_record = _load_render_record(session_dir, report.get("report_id", "")) \
		if notes else None
	# Method notes are planned per store entry, after the loop, so one
	# entry shared by several ids yields one withdraw or one add.
	for sid, entry in suggestions_by_id.items():
		dec = decisions.get(sid, {})
		if not isinstance(dec, dict):
			dec = {}
		if entry[1].get("kind") == "method-note":
			continue
		decision = dec.get("decision")  # None if truly undecided
		state = "pending" if decision in ("accept", "discuss") else "skipped"
		label = entry[1].get("title", sid)
		actions.append({
			"id": sid, "label": label, "decision": decision, "state": state,
			"started_at": None, "finished_at": None, "note": None, "detail": [], "thread": [], "pin_checks": {},
		})
		if decision == "accept":
			_, sug = entry
			# `target_files[]` is the one research-written array assemble.py
			# passes through un-normalized, so its members arrive in whatever
			# shape a subagent wrote — `["Brewfile"]` instead of
			# `[{"path": "Brewfile"}]` is the obvious one, and `tf.get` raised
			# AttributeError on it. That aborted `init` outright, so the whole
			# apply pass ran with no status.json at all: one drifted citation
			# cost every accepted action, not one. Read what is readable, say
			# what is not, and keep going. The cost of a dropped entry is
			# bounded — target_files only decides which repos get a synthetic
			# commit/push action — so an unreadable one is warned about rather
			# than guessed at.
			for tf in sug.get("target_files") or []:
				if isinstance(tf, dict):
					# `.get("path", "")` rather than `.get("path")` is how an
					# entry naming no file at all used to be *silently* read as
					# a macos-setup path — "" fails the dotfiles/ test, so it
					# fell into the else branch below and invented a
					# commit:macos-setup action out of nothing.
					path = tf.get("path")
				elif isinstance(tf, str):
					print(f"warning: {sid}: target_files entry was a bare string, not an object — "
						f"reading it as the path: {tf!r}", file=sys.stderr)
					path = tf
				else:
					path = None
				if not isinstance(path, str) or not path.strip():
					print(f"warning: {sid}: target_files entry has no usable \"path\" — "
						f"not counting it toward the commit actions: {tf!r}", file=sys.stderr)
					continue
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
				"started_at": None, "finished_at": None, "note": None, "detail": [], "thread": [], "pin_checks": {},
			})

	_group_sync_actions(actions, suggestions_by_id)
	actions.extend(_method_note_actions(notes, render_record, report))

	# Investigation actions — one per tool_comments entry (references/apply.md §Tool Comments and Discuss).
	for tool_id, comment in feedback.get("tool_comments", {}).items():
		actions.append({
			"id": f"investigate:{tool_id}", "label": f"Investigate: {tool_id} — {comment}",
			"decision": None, "state": "pending",
			"started_at": None, "finished_at": None, "note": None, "detail": [], "thread": [], "pin_checks": {},
		})

	# Synthetic commit/push actions — only for repos that will actually get
	# a commit (references/apply.md §Initializing status.json; §Push and Terminal Status) — never render a no-op action.
	if touches_dotfiles:
		actions.append({"id": "commit:dotfiles", "label": "Commit changes in dotfiles submodule",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": [], "pin_checks": {}})
	if touches_dotfiles or touches_macos_setup:
		actions.append({"id": "commit:macos-setup", "label": "Commit changes in macos-setup",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": [], "pin_checks": {}})
	if touches_dotfiles:
		actions.append({"id": "push:dotfiles", "label": "Push dotfiles to origin",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": [], "pin_checks": {}})
	if touches_dotfiles or touches_macos_setup:
		actions.append({"id": "push:macos-setup", "label": "Push macos-setup to origin",
			"decision": None, "state": "pending", "started_at": None, "finished_at": None,
			"note": None, "detail": [], "thread": [], "pin_checks": {}})

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


# ── pinning the reviewed version (WP5/I2) ─────────────────────────────────
# The requirement ("an applied upgrade installs the version that was reviewed,
# or refuses") must be checkable from status.json itself, not merely
# documented in references/apply.md's prose — a prompt instruction a session
# skips leaves an artifact indistinguishable from one that followed it. This
# is the enforcement: "done" is refused, not just discouraged, when the
# evidence a real scripts/check_pin.py verify ran is missing.
def _load_suggestion(session_dir: str, action_id: str):
	"""(tool, suggestion) for this action id from report.json, or (None,
	None) — a missing/unreadable report.json, or an id naming no suggestion
	(a synthetic commit/push/investigate action), never raises. report.json
	is small and this runs once per set-action call; re-reading it rather
	than threading it through every caller keeps this function usable from
	both cmd_set_action and cmd_record_pin_check without extra plumbing."""
	report = load_json(os.path.join(session_dir, "report.json"))
	if report is None:
		return None, None
	for tool in report.get("tools", []):
		for sug in tool.get("suggestions", []):
			if sug.get("id") == action_id:
				return tool, sug
	return None, None


def _requires_pin_check(tool: dict, sug: dict) -> bool:
	"""True when marking this suggestion's action "done" must be backed by a
	recorded check_pin.py verify match. Deliberately narrower than "every
	`kind: 'upgrade'` suggestion": a brew-health `:remediate` and a
	skill-drift `:sync` suggestion are *also* `kind: "upgrade"`
	(references/assembly.md §Baseline Suggestion Synthesis) but never carry
	`target_version` at all, so the `is not None` check already excludes
	them without needing to match on the id suffix. A `macos`/`standalone`
	baseline *does* carry `target_version` (assemble.py writes it onto every
	baseline) but is always `auto_runnable: false` and verified manually —
	check_pin.py has no `--source` for either
	(assemble.PIN_CHECKABLE_SOURCES), so gating them here would make it
	impossible to ever mark them done at all."""
	return (sug.get("kind") == "upgrade"
		and sug.get("target_version") is not None
		and tool.get("source") in assemble.PIN_CHECKABLE_SOURCES)


def _pin_check_satisfied(action: dict, tool: dict, sug: dict) -> bool:
	"""Not just "some verify was recorded" — it must match *this*
	suggestion's own source/name/target_version, so evidence recorded for a
	different tool (a copy-pasted result file, `mise:node` satisfying
	`brew:node` at the same version, a stale record from a previous
	target_version after a re-review) can never satisfy the gate. All three
	fields, not just name/target_version — a source mismatch is exactly as
	wrong as a name mismatch and was the one field this check used to skip."""
	verify = (action.get("pin_checks") or {}).get("verify")
	if not isinstance(verify, dict):
		return False
	return (verify.get("match") is True
		and verify.get("source") == tool.get("source")
		and verify.get("name") == tool.get("name")
		and verify.get("target_version") == sug.get("target_version"))


# ── set-action ──────────────────────────────────────────────────────────
def cmd_set_action(args):
	status = load_status(args.session_dir)
	action = next((a for a in status["actions"] if a["id"] == args.action_id), None)
	if action is None:
		print(f"Error: no action with id {args.action_id!r}", file=sys.stderr)
		sys.exit(1)

	if args.state == "done":
		tool, sug = _load_suggestion(args.session_dir, args.action_id)
		if tool is not None and _requires_pin_check(tool, sug) and not _pin_check_satisfied(action, tool, sug):
			print(
				f"Error: refusing to mark {args.action_id!r} done — no recorded "
				f"`scripts/check_pin.py verify` match for target_version "
				f"{sug.get('target_version')!r} (WP5/I2: references/apply.md §Pinning "
				f"the reviewed version). Run check_pin.py verify --source {tool.get('source')} "
				f"--name {tool.get('name')} --target-version {sug.get('target_version')}, then "
				f"record-pin-check {args.session_dir} {args.action_id} verify <result-file>, "
				f"before retrying.", file=sys.stderr)
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


def _validate_pin_check_result(result, expected_phase: str):
	"""Shape-validates one scripts/check_pin.py JSON result before it is
	ever written to status.json — a hand-built or truncated object (a bare
	`{"match": true}`, say) must fail loudly here, not silently later when
	`_pin_check_satisfied`'s `.get()`s come back `None` and every future
	reader of `pin_checks` has to re-derive the same defensiveness. Returns
	an error string, or None if the shape is acceptable.

	Also enforces the phase itself: check_pin.py's `emit()` stamps `phase`
	into the result at the moment the check actually ran (never left for
	whoever saves the file to assert), specifically so a preflight result —
	which can be byte-for-byte identical to a verify result for the same
	tool at the same version — can never be filed under the wrong phase.
	Filing a preflight as a "verify" would otherwise open the `"done"` gate
	with the upgrade never actually run."""
	if not isinstance(result, dict):
		return "result is not a JSON object"
	phase = result.get("phase")
	if phase != expected_phase:
		return (f"result's own \"phase\" is {phase!r}, but this is being recorded as "
			f"{expected_phase!r} — refusing to file a {phase!r} result under a different "
			f"phase (a preflight and a verify can look identical otherwise)")
	if not isinstance(result.get("source"), str) or not result["source"]:
		return "\"source\" must be a non-empty string"
	if not isinstance(result.get("name"), str) or not result["name"]:
		return "\"name\" must be a non-empty string"
	if not isinstance(result.get("match"), bool):
		return "\"match\" must be a bool"
	for key in ("target_version", "observed_version", "reason"):
		if key in result and result[key] is not None and not isinstance(result[key], str):
			return f"{key!r} must be a string or null"
	return None


# ── record-pin-check (WP5/I2) ──────────────────────────────────────────────
def cmd_record_pin_check(args):
	"""Records one scripts/check_pin.py JSON result verbatim onto an
	action's `pin_checks{phase}` — the artifact `set-action`'s "done" gate
	above reads. Two calls per pinnable upgrade in the normal flow
	(references/apply.md §Pinning the reviewed version): "preflight" before
	running `command`, "verify" after — only "verify" gates anything, but
	"preflight" is recorded too so a refused run is visible in the same
	place rather than only in a stderr line nobody kept."""
	status = load_status(args.session_dir)
	action = next((a for a in status["actions"] if a["id"] == args.action_id), None)
	if action is None:
		print(f"Error: no action with id {args.action_id!r}", file=sys.stderr)
		sys.exit(1)
	with open(args.result_file, "r", encoding="utf-8") as fh:
		result = json.load(fh)

	problem = _validate_pin_check_result(result, args.phase)
	if problem is not None:
		print(f"Error: refusing to record pin-check for {args.action_id!r}: {problem}", file=sys.stderr)
		sys.exit(1)

	action.setdefault("pin_checks", {})[args.phase] = result
	status["written_at"] = now_iso()
	write_json_atomic(status_path(args.session_dir), status)
	verdict = "match" if result.get("match") else "no match"
	print(f"{args.action_id}: recorded {args.phase} pin-check ({verdict})")


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

	changelog_md = items.state_path("changelog.md")
	os.makedirs(os.path.dirname(changelog_md), exist_ok=True)
	with open(changelog_md, "a", encoding="utf-8") as fh:
		for entry in entries:
			fh.write("\n\n" + entry + "\n")

	print(f"appended {len(entries)} changelog entries")


# ── the three memory stores (contract/stores.json) ────────────────────────
# Three stores in TWO files. Watch items and per-tool method notes are keyed
# by tool id; global method notes share method-notes.json under the reserved
# key below. A tool id is always `{source}:{name}` and so always contains a
# colon, which is why the contract's `keying` rule reserved the colon-free
# namespace rather than opening a third file — and why `_require_tool_id`
# exists, so the reserved key can never be reached through the per-tool door.
#
# All three are machine-global and deliberately independent of any
# session_dir/status.json: they are read at *research* time on a later run
# (references/research.md §Watch Items (Reading)), not part of this session's
# own state. Same directory/atomic-write pattern as changelog.md
# (append-changelog above).
#
# The two file names and the reserved key come from `items` — they are
# contract data (contract/stores.json pins them), not three strings this
# file gets to spell its own way.


def store_path(filename: str) -> str:
	"""`${XDG_STATE_HOME:-~/.local/state}/tool-update-review/<filename>` — a
	sibling of changelog.md, never inside a session dir (`items.state_path`:
	an empty XDG_STATE_HOME falls back, as everywhere else)."""
	return items.state_path(filename)


def _load_store(path: str):
	"""(store, None) or (None, reason).

	An **absent** file is an empty store — that is what "memory artifacts are
	created on first use" means: no pre-seeding, no
	migration machinery, the first accepted proposal creates the file.

	A file that **exists but cannot be read as an object** is a refusal, not
	a fresh start. `load_json`'s `default=` collapses both cases, and these
	stores accumulate for months: silently replacing an unparseable one with
	`{}` would destroy every note in it on the next accept, with a success
	message. Distinguishing the two is the whole guard."""
	if not os.path.exists(path):
		return {}, None
	try:
		with open(path, "r", encoding="utf-8") as fh:
			store = json.load(fh)
	except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
		return None, f"it exists but could not be read ({exc})"
	if not isinstance(store, dict):
		return None, f"it holds a {type(store).__name__}, not a JSON object keyed by tool id"
	return store, None


def _require_tool_id(tool_id: str) -> None:
	"""A tool id is `{source}:{name}` (references/schemas.md §1.3) and always
	contains a colon. Enforcing that here is what keeps the colon-free
	namespace reserved: a mistyped `--tool-id global` cannot quietly land in
	the global store, and no future reserved key can be written through a
	per-tool subcommand either."""
	if ":" not in tool_id:
		print(f"Error: --tool-id must be a {{source}}:{{name}} tool id, got {tool_id!r} — the "
			f"colon-free namespace is reserved for the global store, which is written with "
			f"`add-global-method-note` (references/schemas.md §1.7b Scope).", file=sys.stderr)
		sys.exit(1)


def append_store_entry(filename: str, key: str, topic: str, note: str,
		dedupe: bool = False) -> bool:
	"""Append one `{topic, note, added_at}` entry under `key`, creating the
	store on first use. One atomic .tmp + os.replace(), same as every other
	write in this file. → True when written.

	With `dedupe`, an entry with the same exact topic AND note already under
	`key` means nothing is written (→ False). The method-note writers ask for
	it: render persists notes before anything records that it did, so a
	render that crashed after a write leaves a record claiming nothing, and
	the accept that record plans at apply would otherwise store the note a
	second time — a duplicate every later run replays."""
	path = store_path(filename)
	for field, value in (("topic", topic), ("note", note)):
		if not isinstance(value, str) or not value.strip():
			print(f"Error: refusing to write {path} — {field} must be a non-blank string. Nothing was written.", file=sys.stderr)
			sys.exit(1)
	store, problem = _load_store(path)
	if problem is not None:
		print(f"Error: refusing to write {path} — {problem}. Fix or move the file; "
			f"nothing was written.", file=sys.stderr)
		sys.exit(1)
	entries = store.setdefault(key, [])
	if not isinstance(entries, list):
		print(f"Error: refusing to write {path} — {key!r} holds a "
			f"{type(entries).__name__}, not an array of entries. Nothing was written.",
			file=sys.stderr)
		sys.exit(1)
	if dedupe and any(isinstance(e, dict) and e.get("topic") == topic
			and e.get("note") == note for e in entries):
		return False
	entries.append({
		"topic": topic,
		"note": note,
		"added_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
	})
	os.makedirs(os.path.dirname(path), exist_ok=True)
	write_json_atomic(path, store)
	return True


def remove_store_entry(filename: str, key: str, topic: str, note) -> dict:
	"""Remove exactly one `{topic, note, added_at}` entry under `key`,
	matched by EXACT topic (and exact note when given) — never by index or
	position, because a store accumulates for months and a position is
	only meaningful against the file the caller last looked at.

	Three outcomes, none silent: not found is a refusal (exit 1, nothing
	written) that names what was looked for; more than one match is a
	refusal that lists the candidates — ambiguity goes to a human, who
	passes --note to narrow it to one; exactly one match is removed, and a
	key left with no entries is deleted rather than kept as `[]`. Every
	reader treats an absent key and an empty one identically (a per-tool
	lookup is `snapshot.get(tool_id)` read as list-or-nothing, and
	converge's store_state is file-level), so the deletion changes what no
	reader sees and keeps the file the shape the golden pins."""
	path = store_path(filename)
	store, problem = _load_store(path)
	if problem is not None:
		print(f"Error: refusing to write {path} — {problem}. Fix or move the file; "
			f"nothing was written.", file=sys.stderr)
		sys.exit(1)
	entries = store.get(key)
	if entries is None:
		print(f"Error: nothing to remove — {path} has no entries under {key!r}. "
			f"Nothing was written.", file=sys.stderr)
		sys.exit(1)
	if not isinstance(entries, list):
		print(f"Error: refusing to write {path} — {key!r} holds a "
			f"{type(entries).__name__}, not an array of entries. Nothing was written.",
			file=sys.stderr)
		sys.exit(1)
	matches = [i for i, e in enumerate(entries)
		if isinstance(e, dict) and e.get("topic") == topic
		and (note is None or e.get("note") == note)]
	if not matches:
		print(f"Error: nothing to remove — no entry under {key!r} in {path} has "
			f"topic {topic!r}" + (f" and note {note!r}" if note is not None else "")
			+ ". Nothing was written.", file=sys.stderr)
		sys.exit(1)
	if len(matches) > 1:
		listing = "\n".join(
			f"  [{i}] added {entries[i].get('added_at')!r}: {str(entries[i].get('note'))[:100]!r}"
			for i in matches)
		print(f"Error: {len(matches)} entries under {key!r} match topic {topic!r}"
			+ (" and that note" if note is not None else "")
			+ f" — refusing to guess which one. Pass --note with the exact note text "
			f"to narrow it to one. Nothing was written.\n{listing}", file=sys.stderr)
		sys.exit(1)
	removed = entries.pop(matches[0])
	if not entries:
		del store[key]
	write_json_atomic(path, store)
	return removed


# ── add-watch-item (references/apply.md §Watch Items (Writing)) ───────────
def cmd_add_watch_item(args):
	_require_tool_id(args.tool_id)
	append_store_entry(items.WATCH_ITEMS_STORE, args.tool_id, args.topic, args.note)
	print(f"added watch item for {args.tool_id!r}: {args.topic!r}")


# ── add-method-note (references/apply.md §Method Notes (Writing)) ─────────
def cmd_add_method_note(args):
	# The watch-item writer with the path and the noun changed, which is the
	# point: the two stores share one layout (contract/stores.json), so they
	# share one writer rather than growing two that can drift.
	_require_tool_id(args.tool_id)
	if append_store_entry(items.METHOD_NOTES_STORE, args.tool_id, args.topic, args.note,
			dedupe=True):
		print(f"added method note for {args.tool_id!r}: {args.topic!r}")
	else:
		print(f"method note for {args.tool_id!r}: {args.topic!r} is already in the store "
			f"with this exact text — nothing written")


# ── add-global-method-note (references/apply.md §Method Notes (Writing)) ──
def cmd_add_global_method_note(args):
	# No --tool-id, because a global note is not about a tool. It takes no
	# tool id rather than a magic one, so the reserved key is spelled in
	# exactly one place in this file.
	if append_store_entry(items.METHOD_NOTES_STORE, items.GLOBAL_METHOD_NOTE_KEY,
			args.topic, args.note, dedupe=True):
		print(f"added global method note: {args.topic!r}")
	else:
		print(f"global method note {args.topic!r} is already in the store with this "
			f"exact text — nothing written")


# ── remove-method-note / remove-global-method-note ────────────────────────
# The apply-side half of render-time persistence. render.py persists every surviving
# method-note proposal at render (references/rendering-report.md §Method
# Notes); a note the user then REJECTS on the page is withdrawn here, by the
# exact (tool id, topic, note) render recorded in method-notes.render.json.
def cmd_remove_method_note(args):
	_require_tool_id(args.tool_id)
	remove_store_entry(items.METHOD_NOTES_STORE, args.tool_id, args.topic, args.note)
	print(f"removed method note for {args.tool_id!r}: {args.topic!r}")


def cmd_remove_global_method_note(args):
	remove_store_entry(items.METHOD_NOTES_STORE, items.GLOBAL_METHOD_NOTE_KEY,
		args.topic, args.note)
	print(f"removed global method note: {args.topic!r}")


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

	p = sub.add_parser("record-pin-check")
	p.add_argument("session_dir")
	p.add_argument("action_id")
	p.add_argument("phase", choices=["preflight", "verify"])
	p.add_argument("result_file")
	p.set_defaults(func=cmd_record_pin_check)

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

	p = sub.add_parser("add-method-note")
	p.add_argument("--tool-id", required=True)
	p.add_argument("--topic", required=True)
	p.add_argument("--note", required=True)
	p.set_defaults(func=cmd_add_method_note)

	p = sub.add_parser("add-global-method-note")
	p.add_argument("--topic", required=True)
	p.add_argument("--note", required=True)
	p.set_defaults(func=cmd_add_global_method_note)

	p = sub.add_parser("remove-method-note")
	p.add_argument("--tool-id", required=True)
	p.add_argument("--topic", required=True)
	p.add_argument("--note", default=None)
	p.set_defaults(func=cmd_remove_method_note)

	p = sub.add_parser("remove-global-method-note")
	p.add_argument("--topic", required=True)
	p.add_argument("--note", default=None)
	p.set_defaults(func=cmd_remove_global_method_note)

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
