#!/usr/bin/env python3
"""
render.py — inject report.json into the HTML template and write index.html.
Usage: render.py <path-to-report.json>

Reads report.json, validates schema_version, contract_version and
suggestion-id uniqueness, performs three token replacements in the template,
writes index.html next to report.json, PERSISTS every surviving method-note
proposal into the method-note store (criterion 18 — see
persist_method_notes), and copies server.py alongside it. Prints the output
path.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items  # noqa: E402
import write_status  # noqa: E402  — the stores' read-only path helpers

RENDER_RECORD = write_status.METHOD_NOTES_RENDER_RECORD

# The convergence states under which C6 actually reviewed the proposals —
# the submission applied (cuts, re-homes, promotions in effect). `not_run`
# (the pre-convergence corpus), `artefacts_inconsistent` (the PRE corpus
# rendered because the artefacts disagree) and `degraded_unapplied` (the
# submission set aside; corpus.post IS corpus.pre) all mean the reviewer did
# not review. The page never re-derives this: it reads the per-note outcome
# from the record embedded as REPORT.method_notes_render, and the loop test
# drives both sides.
REVIEWED_STATES = ("converged", "degraded_gate")


def persist_method_notes(report: dict, report_dir: str) -> dict:
	"""Criterion 18 / REDESIGN §L5: method notes persist AT RENDER, not on
	Submit, so the store fills from run one — including a run the user
	abandons, which is the historical norm. The report page then carries
	the surface that earns this: every persisted note is visible there, a
	Reject withdraws it at apply (`write_status.py remove-method-note`),
	and a comment attaches modification instructions. Render owns the
	write; apply owns the disposition (references/rendering-report.md
	§Method Notes).

	What is written: every `kind: "method-note"` suggestion still on a tool
	in report.json — i.e. what survived convergence's C6, which cut or
	re-homed the rest before this stage ran — **and only when convergence
	actually reviewed** (`REVIEWED_STATES`). Under `not_run`,
	`artefacts_inconsistent` or `degraded_unapplied` the report carries raw
	proposals nobody reviewed, and the default when the reviewer did not
	review must not be "permanent": nothing is written, every note is
	recorded as `unreviewed` with the reason, the page says so, and apply
	writes one only on an explicit accept. A suggestion the ledger
	promoted to global (`report.convergence.memory.promoted_to_global`, or
	a re-homed note with `scope: "global"`) goes under the reserved global
	key through `add-global-method-note` — the only real-run writer of
	that store. Everything else goes under its tool id.

	Idempotent by content: an entry with the same topic AND note already
	under the key is skipped, so re-rendering one report never duplicates.
	Same topic with different text still writes — convergence dedupes
	proposals against the store by (tool_id, topic) before this stage, so
	a same-topic survivor is one it judged distinct.

	Never the run: a write the store refuses (unreadable file, a key
	holding a non-array) is recorded as failed, said on stderr, and the
	page still renders. The whole outcome is written beside the page as
	method-notes.render.json — what was written, what already existed,
	what failed — which is also what apply reads to withdraw a rejected
	note by its exact (key, topic, note)."""
	conv = report.get("convergence") if isinstance(report.get("convergence"), dict) else {}
	state = conv.get("state") if isinstance(conv.get("state"), str) else "absent"
	reviewed = state in REVIEWED_STATES
	global_ids = write_status.global_method_note_ids(report)

	store_file = write_status.store_path(items.METHOD_NOTES_STORE)
	store, problem = write_status._load_store(store_file)
	record = {
		"report_id": report.get("report_id", ""),
		"rendered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"store": store_file,
		"reviewed": reviewed,
		"convergence_state": state,
		"written": [], "already_present": [], "failed": [], "unreviewed": [],
	}
	if problem is not None:
		# The store cannot be read: nothing can be deduped against it and
		# every write would be refused by the writer anyway. Say so once,
		# record every proposal as failed, keep rendering.
		record["store_problem"] = problem
	script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "write_status.py")

	for tool in report.get("tools", []):
		if not isinstance(tool, dict) or not isinstance(tool.get("id"), str):
			continue
		for sug in tool.get("suggestions", []) or []:
			if not isinstance(sug, dict) or sug.get("kind") != "method-note":
				continue
			topic, note_text = sug.get("method_topic"), sug.get("method_note")
			entry = {"tool_id": tool["id"], "suggestion_id": sug.get("id"),
				"key": None, "topic": topic, "note": note_text}
			if not (isinstance(topic, str) and topic.strip()
					and isinstance(note_text, str) and note_text.strip()):
				entry["reason"] = "method_topic/method_note missing or empty"
				record["failed"].append(entry)
				continue
			is_global = isinstance(sug.get("id"), str) and sug["id"] in global_ids
			key = items.GLOBAL_METHOD_NOTE_KEY if is_global else tool["id"]
			entry["key"] = key
			if not reviewed:
				entry["reason"] = (f"convergence state {state!r}: C6 did not review this "
					f"proposal; apply writes it only on an explicit accept")
				record["unreviewed"].append(entry)
				continue
			if problem is not None:
				entry["reason"] = f"store {problem}"
				record["failed"].append(entry)
				continue
			existing = store.get(key) if isinstance(store.get(key), list) else []
			if any(isinstance(e, dict) and e.get("topic") == topic
					and e.get("note") == note_text for e in existing):
				record["already_present"].append(entry)
				continue
			argv = [sys.executable, script] + (
				["add-global-method-note"] if is_global
				else ["add-method-note", "--tool-id", tool["id"]]
			) + ["--topic", topic, "--note", note_text]
			proc = subprocess.run(argv, capture_output=True, text=True)
			if proc.returncode != 0:
				entry["reason"] = proc.stderr.strip()[-400:]
				record["failed"].append(entry)
				continue
			record["written"].append(entry)
			# Keep the in-memory view current so a second proposal with the
			# same (topic, note) in this run is deduped against the first.
			store.setdefault(key, []).append({"topic": topic, "note": note_text})

	with open(os.path.join(report_dir, RENDER_RECORD), "w", encoding="utf-8") as fh:
		json.dump(record, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")
	if record["unreviewed"]:
		print(f"method notes: {len(record['unreviewed'])} unreviewed — NOT persisted "
			f"(convergence state {state!r}; accept on the page writes at apply) → "
			f"{RENDER_RECORD}", file=sys.stderr)
	elif record["written"] or record["already_present"] or record["failed"]:
		print(f"method notes: {len(record['written'])} written, "
			f"{len(record['already_present'])} already present, "
			f"{len(record['failed'])} failed → {RENDER_RECORD}", file=sys.stderr)
	return record


def main():
	if len(sys.argv) < 2:
		print("Usage: render.py <path-to-report.json>", file=sys.stderr)
		sys.exit(1)

	report_path = os.path.abspath(sys.argv[1])
	report_dir = os.path.dirname(report_path)
	script_dir = os.path.dirname(os.path.abspath(__file__))

	template_path = os.path.normpath(
		os.path.join(script_dir, "..", "assets", "report-template.html")
	)
	server_src = os.path.join(script_dir, "server.py")

	# ── Load report ───────────────────────────────────────────────────────
	try:
		with open(report_path, "r", encoding="utf-8") as fh:
			report = json.load(fh)
	except FileNotFoundError:
		print(f"Error: report not found: {report_path}", file=sys.stderr)
		sys.exit(1)
	except json.JSONDecodeError as exc:
		print(f"Error: report.json is not valid JSON: {exc}", file=sys.stderr)
		sys.exit(1)

	# ── Validate schema_version ───────────────────────────────────────────
	# 2 since `tools[].items[]` replaced headliners/relevancy/context and
	# `security.notable[]`. The template reads the item model and nothing else,
	# so a schema-1 report would render a page of empty cards rather than
	# failing — refusing it here is what keeps that impossible. An old report
	# is re-read by checking out the pipeline it was written with.
	if report.get("schema_version") != 2:
		print(
			f"Error: schema_version must be 2, got {report.get('schema_version')!r}",
			file=sys.stderr,
		)
		sys.exit(1)

	# ── Validate contract_version (references/schemas.md §1.1; REDESIGN §I9) ──
	# The same exact-equality gate write_status.py init applies, for the same
	# reason and with no shim: the template renders enum values, id formats
	# and derived-field shapes this contract pins. A report written against
	# another contract would not fail visibly — it would render a plausible
	# page whose fields no longer mean what the template thinks they mean,
	# and a human would click Accept on it. An old report is re-read by
	# checking out the pipeline that wrote it.
	if report.get("contract_version") != items.CONTRACT_VERSION:
		print(
			f"Error: report.json contract_version must be {items.CONTRACT_VERSION}, got "
			f"{report.get('contract_version')!r} — refusing to render a report written "
			f"against another contract (no migration shim exists, REDESIGN.md §I9). "
			f"Re-run the review, or check out the pipeline this report was written with.",
			file=sys.stderr,
		)
		sys.exit(1)

	# ── Validate suggestion id uniqueness ─────────────────────────────────
	seen: dict[str, str] = {}  # id → tool id
	for tool in report.get("tools", []):
		tool_id = tool.get("id", "<unknown>")
		for sug in tool.get("suggestions", []):
			sid = sug.get("id")
			if not sid:
				continue
			if sid in seen:
				print(
					f"Error: duplicate suggestion id {sid!r} "
					f"(in tool {tool_id!r} and {seen[sid]!r})",
					file=sys.stderr,
				)
				sys.exit(1)
			seen[sid] = tool_id

	# ── Extract template variables ────────────────────────────────────────
	report_id = report.get("report_id", "")
	generated_at = report.get("generated_at", "")

	# ── Read template ─────────────────────────────────────────────────────
	try:
		with open(template_path, "r", encoding="utf-8") as fh:
			html = fh.read()
	except FileNotFoundError:
		print(f"Error: template not found: {template_path}", file=sys.stderr)
		sys.exit(1)

	# ── Persist method notes (criterion 18) — BEFORE the payload is built,
	# because the page must say what is true about storage per note, and
	# only the render outcome knows that. The record rides into the page
	# as REPORT.method_notes_render through the same escaped replacement
	# below — one interpolation path, never a second. report.json on disk
	# is untouched; the same record sits beside it as
	# method-notes.render.json. A store problem can never cost the render:
	# persist_method_notes records refusals, and anything it did not
	# anticipate becomes a record that claims nothing. ─────────────────────
	try:
		record = persist_method_notes(report, report_dir)
	except Exception as exc:  # the render must still happen
		# Nothing is known about what reached the store, so NO note is
		# claimed as stored and none as failed (a failed note becomes an
		# add at apply, which could duplicate a write that did land before
		# the crash). The page then says "no render record" per note and
		# init treats them the same way — both read THIS record, which is
		# written in place of whatever an earlier render left there.
		problem = f"{type(exc).__name__}: {exc}"
		record = {"report_id": report.get("report_id", ""), "reviewed": False,
			"convergence_state": "unknown", "store_problem": f"persistence crashed — {problem}",
			"written": [], "already_present": [], "failed": [], "unreviewed": []}
		print(f"warning: method-note persistence crashed ({problem}) — the page renders "
			f"with no note known to be stored", file=sys.stderr)
		try:
			with open(os.path.join(report_dir, RENDER_RECORD), "w", encoding="utf-8") as fh:
				json.dump(record, fh, ensure_ascii=False, indent="\t")
				fh.write("\n")
		except OSError as write_exc:
			print(f"warning: could not write {RENDER_RECORD} either ({write_exc})", file=sys.stderr)

	# ── Three token replacements per references/rendering-report.md §Template Variables ───
	# The tokens inside attribute quotes include the surrounding quotes.
	html = html.replace('"__REPORT_ID__"',    json.dumps(report_id))
	html = html.replace('"__GENERATED_AT__"', json.dumps(generated_at))
	# The REPORT_DATA token is unquoted — it lands as a JS object literal.
	# Escape "<" wholesale (as \u003c — valid JSON, identical once parsed)
	# so no agent-written free-text field can splice markup into the
	# surrounding <script> element. Escaping only "</" was not enough: a
	# literal "<!--" flips the HTML parser into script-data-double-escaped
	# state, where the template's own "</script>" no longer terminates the
	# element and the rest of the document is swallowed. A denylist of
	# breakout spellings ("</", "<!--", "<script") is a losing game, so no
	# "<" survives at all. JSON puts "<" only inside string literals, so the
	# blanket replace can never touch structure.
	page_report = dict(report, method_notes_render=record)
	report_json = json.dumps(page_report, ensure_ascii=False).replace("<", "\\u003c")
	html = html.replace("__REPORT_DATA__", report_json)

	# ── Write index.html ──────────────────────────────────────────────────
	out_path = os.path.join(report_dir, "index.html")
	with open(out_path, "w", encoding="utf-8") as fh:
		fh.write(html)

	# ── Copy server.py ────────────────────────────────────────────────────
	server_dst = os.path.join(report_dir, "server.py")
	shutil.copy2(server_src, server_dst)

	print(out_path)


if __name__ == "__main__":
	main()
