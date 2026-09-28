#!/usr/bin/env python3
"""
apply_converge.py — the only writer of a corpus (REDESIGN.md §L6/§M, criterion 12/13).

Usage:
	apply_converge.py --session DIR --prepare [--macos-setup-root PATH]
	                                          [--dotfiles-root PATH] [--systems-root PATH]
	apply_converge.py --session DIR --check  converge.draft.json
	apply_converge.py --session DIR --submit converge.draft.json

`--prepare` runs the deterministic validator and writes the three stage-3½
artefacts: `corpus.pre.json` (the frozen corpus of record), `converge-view.json`
(the projection convergence reads) and `converge-tables.json`. `corpus.pre.json`
is immutable for the run — a second `--prepare` refuses unless `--force`.

`--check` runs all five phases against the draft and writes NOTHING durable.
It is the one Bash command the convergence agent may run (§5.1): it reads only
the session directory, fetches nothing, and iterating against it costs no
recovery attempt.

`--submit` is an attempt. The durable counter in `converge-attempts.json`
enforces §L4's loop: a submission with critical findings bounces with the
coded findings (exit 1) while attempts remain; at attempt 5 the run DEGRADES
CONSERVATIVELY instead of dying — `degraded_gate` forces every gate-failing
tool to `security_mixed` / not pre-accepted, `degraded_unapplied` ships
`corpus.post.json` == `corpus.pre.json` — and `convergence_status` carries a
first-class explanation either way. Never a silent pass, never a dead run.

The five phases (§1.7), all inside the pure function `apply_converge`:

	1  RESOLVE   run/version/digest identity; targets; deps; declared scope
	2  PRECHECK  per edit, in isolation — E-EDIT-*
	3  APPLY     deep copy, surviving edits, topological along `requires`
	4  DERIVE    diff(corpus_pre, corpus_post) WITHOUT consulting the edit
	             list; scope containment — E-APPLY-SCOPE
	5  VERIFY    element re-validation (E-APPLY-SCHEMA), differential
	             recomputation of every derived axis, leave-one-out
	             attribution, the hard gate (E-GATE-*), the seven checks'
	             attestations (E-CHECK-*), corpus_effect arithmetic
	             (E-EFFECT-*)

The record of what changed is the DERIVED diff between the two corpora,
computed in phase 4 without reference to the edit list. The list supplies
intent and reason only. Every `before` in `converge-effect.json` comes from
`corpus.pre.json`, never from the agent.
"""
from __future__ import annotations  # Python 3.9 — same constraint as items.py

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import assemble  # noqa: E402
import converge as contract  # noqa: E402
import items as model  # noqa: E402
import validate_items  # noqa: E402

# Resolver-dependent codes are excluded from the phase-5 element comparison:
# the applier is a pure function FORBIDDEN file access (it re-validates under
# `validate_items.NO_IO_RESOLVER`, which raises none of these), and path
# resolution and usage grounding are the stage-3 validator's job. Subtracting
# them from the pre side too keeps the comparison like-for-like — otherwise
# every touched item carrying one would raise E-APPLY-SCHEMA. An edit cannot
# introduce a new path or usage claim invisibly — the touched field is in the
# derived diff either way, and a usage entry grounds only by the carried
# `usage_evidence` record.
_PATH_CODES = frozenset({"E-EVID-404", "W-EVID-ROOT", "E-USAGE-UNGROUNDED",
	"W-USAGE-INSTALL-ONLY"})

# I-22's codes, re-checked after edits over every suggestion of every tool
# whose items changed (phase 5a).
_I22_CODES = ("E-SUG-REQUIRED-UNGROUNDED", "E-SUG-SERVES-UNRESOLVED",
	"E-REQUIREMENT-CONTRADICTED")

# Diff granularity: these item fields are compared one level deep when both
# sides are objects, so declared scope and `merge.changed_fields` can speak
# in `local.direction`-shaped paths. Everything else is one path per
# top-level field.
_NESTED_ITEM_FIELDS = ("local", "change", "security", "anchor", "watch_hit")

_MAX_DETAIL = 300


def _short(value) -> str:
	text = json.dumps(value, ensure_ascii=False, default=str) \
		if not isinstance(value, str) else value
	return text if len(text) <= _MAX_DETAIL else text[:_MAX_DETAIL] + "…"


def _finding(code, detail, edit_id=None, tool_id=None, critical=None):
	severity = contract.CODES[code][0]
	if critical is None:
		critical = severity == "critical"
	entry = {"code": code, "critical": bool(critical), "detail": detail}
	if edit_id is not None:
		entry["edit_id"] = edit_id
	if tool_id is not None:
		entry["tool_id"] = tool_id
	return entry


# ── element addressing ──────────────────────────────────────────────────────
class _Index:
	"""Resolution of every addressable element in corpus_pre. Built once in
	phase 1; `kind: "proposal"` resolves to a suggestion whose kind is a
	memory kind, and `kind: "tool"` to the view itself."""

	def __init__(self, corpus_pre):
		self.views = {}
		self.items = {}
		self.suggestions = {}
		self.all_suggestion_ids = set()
		# ids this submission's `add` edits create — a `reason.destination_id`
		# may name one (a re-home's note), so resolution covers them too.
		self.created_suggestion_ids = set()
		for view in corpus_pre.get("tools") or []:
			if not isinstance(view, dict) or not isinstance(view.get("id"), str):
				continue
			tool_id = view["id"]
			self.views[tool_id] = view
			for item in view.get("items") or []:
				if isinstance(item, dict) and isinstance(item.get("id"), str):
					self.items[(tool_id, item["id"])] = item
			for sug in view.get("suggestions") or []:
				if isinstance(sug, dict) and isinstance(sug.get("id"), str):
					self.suggestions[(tool_id, sug["id"])] = sug
					self.all_suggestion_ids.add(sug["id"])

	def resolve(self, tool_id, kind, element_id):
		"""→ (element, canonical_kind) or (None, reason)."""
		view = self.views.get(tool_id)
		if view is None:
			return None, "tool_id {!r} names no tool in corpus.pre".format(tool_id)
		if kind == "tool":
			if element_id not in (None, tool_id):
				return None, "a tool target's id must be the tool_id or null"
			return view, "tool"
		if kind == "item":
			item = self.items.get((tool_id, element_id))
			if item is None:
				return None, "{!r} names no item on {}".format(element_id, tool_id)
			return item, "item"
		if kind in ("suggestion", "proposal"):
			sug = self.suggestions.get((tool_id, element_id))
			if sug is None:
				return None, "{!r} names no suggestion on {}".format(element_id, tool_id)
			if kind == "proposal" and assemble.suggestion_kind(sug) \
					not in model.MEMORY_SUGGESTION_KINDS:
				return None, "{!r} is a {} suggestion, not a memory proposal".format(
					element_id, assemble.suggestion_kind(sug))
			return sug, "suggestion"
		return None, "unknown target.kind {!r}".format(kind)


# ── field paths ─────────────────────────────────────────────────────────────
def _get_path(element, path):
	"""(found, value) for a dotted path one level deep at most."""
	if "." in path:
		head, tail = path.split(".", 1)
		parent = element.get(head)
		if not isinstance(parent, dict):
			return False, None
		return tail in parent, parent.get(tail)
	return path in element, element.get(path)


def _set_path(element, path, value):
	if "." in path:
		head, tail = path.split(".", 1)
		parent = element.get(head)
		if not isinstance(parent, dict):
			parent = {}
			element[head] = parent
		parent[tail] = copy.deepcopy(value)
	else:
		element[path] = copy.deepcopy(value)


def _legal_fields(op, kind):
	spec = contract.OPS[op]
	fields = spec["fields"]
	if fields == "text":
		return contract.ITEM_TEXT_FIELDS if kind == "item" \
			else contract.SUGGESTION_TEXT_FIELDS
	return fields or ()


def _quote_surface(element, kind) -> list:
	"""The prose a cut's quote must be verbatim-contained in (§3.3): the
	item's `body` when it has one — the text the projection withheld, so
	there is no way to satisfy the rule without reading it — else its title
	and local statement; a suggestion's own text fields."""
	if kind == "item":
		body = element.get("body")
		if isinstance(body, str) and body.strip():
			return [body]
		out = []
		for field in ("title",):
			value = element.get(field)
			if isinstance(value, str):
				out.append(value)
		local = element.get("local")
		if isinstance(local, dict) and isinstance(local.get("statement"), str):
			out.append(local["statement"])
		return out
	out = []
	for field in contract.SUGGESTION_TEXT_FIELDS:
		value = element.get(field)
		if isinstance(value, str):
			out.append(value)
	return out


def _is_token_shortening(before, after) -> bool:
	"""trim's bar: `after` must be a strictly shorter, order-preserving token
	subsequence of `before` — words may be dropped, never rewritten."""
	if not isinstance(before, str) or not isinstance(after, str):
		return False
	if len(after) >= len(before) or not after.strip():
		return False
	b_tokens = before.split()
	a_tokens = after.split()
	it = iter(b_tokens)
	return all(any(tok == b for b in it) for tok in a_tokens)


# ── phase 1: RESOLVE ────────────────────────────────────────────────────────
def _resolve_submission(corpus_pre, converge):
	findings = []
	if not isinstance(converge, dict):
		return [_finding("E-SUBMIT-SHAPE", "converge.json is {}, not an object".format(
			type(converge).__name__))]
	for key, want in (("edits", list), ("checks", list), ("ledger", dict),
			("corpus_effect", dict)):
		if not isinstance(converge.get(key), want):
			findings.append(_finding("E-SUBMIT-SHAPE",
				"`{}` must be a {}".format(key, want.__name__)))
	if converge.get("run_id") != corpus_pre.get("run_id"):
		findings.append(_finding("E-SUBMIT-RUN",
			"submission run_id {!r} != session run_id {!r}".format(
				converge.get("run_id"), corpus_pre.get("run_id"))))
	if converge.get("converge_version") != contract.CONVERGE_VERSION \
			or converge.get("view_version") != contract.VIEW_VERSION:
		findings.append(_finding("E-SUBMIT-VERSION",
			"converge_version/view_version {!r}/{!r} != contract {}/{}".format(
				converge.get("converge_version"), converge.get("view_version"),
				contract.CONVERGE_VERSION, contract.VIEW_VERSION)))
	digest = contract.canonical_digest(corpus_pre)
	if converge.get("corpus_digest") != digest:
		findings.append(_finding("E-SUBMIT-DIGEST",
			"submission was written against {!r}; this corpus is {!r}".format(
				converge.get("corpus_digest"), digest)))
	edits = converge.get("edits")
	if isinstance(edits, list):
		seen = set()
		for edit in edits:
			if not isinstance(edit, dict) or not isinstance(edit.get("edit_id"), str) \
					or not edit["edit_id"]:
				findings.append(_finding("E-SUBMIT-SHAPE",
					"an edit is not an object with a string edit_id: {}".format(
						_short(edit))))
				continue
			if edit["edit_id"] in seen:
				findings.append(_finding("E-SUBMIT-SHAPE",
					"edit_id {!r} appears twice — addressing is broken".format(
						edit["edit_id"])))
			seen.add(edit["edit_id"])
	return findings


# ── phase 2: PRECHECK ───────────────────────────────────────────────────────
def _check_reason(edit, op, index):
	reason = edit.get("reason")
	if not isinstance(reason, dict):
		return "`reason` must be an object"
	headline = reason.get("headline")
	if not isinstance(headline, str) or not headline.strip():
		return "`reason.headline` is required on every edit"
	if len(headline) > 140:
		return "`reason.headline` is over 140 chars — the label contract renders it verbatim"
	if op in contract.CUT_OPS:
		body = reason.get("body")
		if not isinstance(body, str) or not body.strip():
			return "a cut's `reason.body` is required (§3.2)"
		rule_ref = reason.get("rule_ref")
		if not (isinstance(rule_ref, str) and rule_ref.strip()) \
				and len(body.split()) < 8:
			return "a cut's reason needs `rule_ref` or at least a sentence of reasoning"
		if reason.get("confidence") not in contract.REASON_CONFIDENCE:
			return "a cut's `reason.confidence` must be one of {}".format(
				", ".join(contract.REASON_CONFIDENCE))
	destination = reason.get("destination_id")
	if destination is not None:
		if not isinstance(destination, str):
			return "`reason.destination_id` must be a string id"
		tool_id = (edit.get("target") or {}).get("tool_id")
		if (tool_id, destination) not in index.items \
				and (tool_id, destination) not in index.suggestions \
				and destination not in index.created_suggestion_ids:
			return "`reason.destination_id` {!r} resolves to nothing on {}".format(
				destination, tool_id)
	return None


def _check_bucket_claim(edit, op):
	claim = edit.get("bucket_claim")
	if op in contract.BUCKET_CAPABLE_OPS:
		if not isinstance(claim, dict):
			return "`bucket_claim` is REQUIRED on a bucket-capable op ({})".format(op)
		if not isinstance(claim.get("moves_bucket"), bool):
			return "`bucket_claim.moves_bucket` must be a bool"
		buckets = tuple(contract.BUCKET_RESTRICTIVENESS)
		if claim.get("expected_from") not in buckets \
				or claim.get("expected_to") not in buckets:
			return "`bucket_claim.expected_from/to` must be buckets"
		if claim.get("direction") not in contract.CLAIM_DIRECTIONS:
			return "`bucket_claim.direction` must be one of {}".format(
				", ".join(contract.CLAIM_DIRECTIONS))
		return None
	if claim is not None:
		return "`bucket_claim` is FORBIDDEN on {} — it cannot move a bucket".format(op)
	return None


def _check_after(edit, op, kind, element, field, index, tool_id):
	"""after/payload validity per op. → error string or None."""
	after = edit.get("after")
	if op in ("delete", "flag"):
		return "`after` is forbidden on {}".format(op) if "after" in edit else None
	if op == "retag":
		if not isinstance(after, list) or not after \
				or not all(isinstance(t, str) for t in after):
			return "`after` must be a non-empty list of tags"
		unknown = [t for t in after if t not in model.TAGS]
		if unknown:
			return "convergence may not write a tag outside the closed set: {}".format(
				unknown)
		return None
	if op == "rerate":
		return None if after in model.SEVERITIES else \
			"`after` must be a severity: {}".format(", ".join(model.SEVERITIES))
	if op == "redirect":
		vocabulary = model.DIRECTIONS if field == "local.direction" else model.EFFECTS
		return None if after in vocabulary else \
			"`after` must be one of {}".format(", ".join(vocabulary))
	if op in ("trim", "reword"):
		if not isinstance(after, str) or not after.strip():
			return "`after` must be a non-empty string"
		if op == "trim":
			before = (edit.get("precondition") or {}).get("before")
			if not _is_token_shortening(before, after):
				return "`after` is not a token-subsequence shortening of the true before"
		return None
	if op == "move_evidence":
		if not isinstance(after, dict) or not isinstance(after.get("citation"), dict):
			return "`after` must be {citation: {...}, evidence?: {...}}"
		citation = after["citation"]
		if citation.get("kind") not in model.CITATION_KINDS:
			return "`after.citation.kind` must be one of {}".format(
				", ".join(model.CITATION_KINDS))
		text = citation.get("text")
		if not isinstance(text, str) or not text.strip():
			return "`after.citation.text` must be non-empty"
		replacement = after.get("evidence")
		if replacement is not None and not isinstance(replacement, dict):
			return "`after.evidence`, when given, must be an evidence object"
		return None
	if op == "annotate":
		if not isinstance(after, str) or not after.strip():
			return "`after` must be the note, a non-empty string"
		return None
	if op == "merge":
		if not isinstance(after, dict):
			return "`after` must be the WHOLE merged element"
		for immutable in contract.IMMUTABLE_ELEMENT_FIELDS:
			if after.get(immutable) != element.get(immutable):
				return "`after.{}` may not differ — validator-assigned (§7.1)".format(
					immutable)
		changed = edit.get("changed_fields")
		if not isinstance(changed, list) or not changed \
				or not all(isinstance(f, str) for f in changed):
			return "`changed_fields` is REQUIRED on a merge"
		duplicates = sorted({f for f in changed if changed.count(f) > 1})
		if duplicates:
			# Left in, the duplicate scope keys made E-EDIT-DUP name the
			# edit as its own conflict — an unactionable message for a
			# malformed list.
			return "`changed_fields` lists {} more than once".format(duplicates)
		return None
	if op == "add":
		if not isinstance(after, dict):
			return "`after` must be the whole new element"
		if assemble.suggestion_kind(after) not in model.MEMORY_SUGGESTION_KINDS:
			return ("an added suggestion must be a memory proposal — an authored "
				"edit/structural suggestion has no research behind it and no "
				"checker owning it (§7.2)")
		new_id = after.get("id")
		if not isinstance(new_id, str) or not new_id.startswith(tool_id + ":"):
			return "`after.id` must be a string prefixed {!r}".format(tool_id + ":")
		return None
	return "unhandled op {!r}".format(op)  # unreachable while OPS is closed


def _precheck_edit(edit, index):
	"""→ list of (code, detail) for one edit, in isolation."""
	problems = []
	op = edit.get("op")
	if op not in contract.OPS:
		return [("E-EDIT-OP", "unknown op {!r}".format(op))]
	if edit.get("check") not in contract.CHECK_IDS:
		problems.append(("E-EDIT-OP",
			"`check` must name one of the seven checks, got {!r}".format(
				edit.get("check"))))
	target = edit.get("target")
	if not isinstance(target, dict) or not isinstance(target.get("tool_id"), str):
		return problems + [("E-EDIT-TARGET", "`target` must carry a tool_id")]
	tool_id, kind = target["tool_id"], target.get("kind")
	spec = contract.OPS[op]
	if kind not in spec["kinds"]:
		return problems + [("E-EDIT-OP",
			"op {} cannot target kind {!r}".format(op, kind))]
	if op == "add":
		if target.get("id") is not None:
			problems.append(("E-EDIT-TARGET", "an add targets no existing element — id must be null"))
		if tool_id not in index.views:
			return problems + [("E-EDIT-TARGET",
				"tool_id {!r} names no tool in corpus.pre".format(tool_id))]
		element, canonical = index.views[tool_id], "suggestion"
	else:
		element, canonical = index.resolve(tool_id, kind, target.get("id"))
		if element is None:
			return problems + [("E-EDIT-TARGET", canonical)]

	field = target.get("field")
	legal = _legal_fields(op, canonical)
	if spec["fields"] is not None:
		if field not in legal:
			problems.append(("E-EDIT-TARGET",
				"field {!r} is illegal for {} on a {} (legal: {})".format(
					field, op, canonical, ", ".join(legal))))
			return problems
	elif field is not None:
		problems.append(("E-EDIT-TARGET",
			"`target.field` must be null for {} — its field is implicit".format(op)))

	# precondition, per class (§1.5)
	if spec["precondition"] == "field":
		precondition = edit.get("precondition")
		if not isinstance(precondition, dict) or "before" not in precondition:
			problems.append(("E-EDIT-PRECOND",
				"`precondition.before` is required on a field-scoped op"))
		else:
			if op == "annotate":
				# The one appended field convergence may create: `before` is
				# the note count so two annotates compose without conflict.
				notes = element.get("convergence_notes")
				current = len(notes) if isinstance(notes, list) else 0
				if precondition["before"] != current:
					problems.append(("E-EDIT-PRECOND",
						"convergence_notes holds {} notes, precondition said {!r}".format(
							current, precondition["before"])))
			elif op == "move_evidence":
				local = element.get("local")
				entries = local.get("evidence") if isinstance(local, dict) else None
				if not isinstance(entries, list) \
						or precondition["before"] not in entries:
					problems.append(("E-EDIT-PRECOND",
						"precondition.before matches no local.evidence entry: {}".format(
							_short(precondition["before"]))))
			else:
				found, current = _get_path(element, field)
				if not found or current != precondition["before"]:
					problems.append(("E-EDIT-PRECOND",
						"target.field {} is {}, precondition said {}".format(
							field, _short(current if found else "<absent>"),
							_short(precondition["before"]))))
	if op in contract.CUT_OPS:
		quote = edit.get("quote")
		if not isinstance(quote, str) or not quote.strip():
			problems.append(("E-EDIT-QUOTE",
				"`quote` is required on every cut (§3.3)"))
		elif not any(quote in text for text in _quote_surface(element, canonical)):
			problems.append(("E-EDIT-QUOTE",
				"quote is not verbatim-contained in the addressed element: {}".format(
					_short(quote))))
	claim_problem = _check_bucket_claim(edit, op)
	if claim_problem:
		problems.append(("E-EDIT-OP", claim_problem))
	if op != "merge" and edit.get("changed_fields") is not None:
		problems.append(("E-EDIT-OP", "`changed_fields` is merge-only"))
	after_problem = _check_after(edit, op, canonical, element, field, index, tool_id)
	if after_problem:
		problems.append(("E-EDIT-OP", after_problem))
	elif spec["fields"] is not None and op not in ("annotate", "move_evidence") \
			and spec["precondition"] == "field":
		precondition = edit.get("precondition")
		if isinstance(precondition, dict) \
				and precondition.get("before") == edit.get("after"):
			problems.append(("E-EDIT-OP", "after == before — the edit changes nothing"))
	if op == "merge" and not problems:
		after = edit["after"]
		derived = _element_diff_paths(element, after, "item")
		declared = set(edit.get("changed_fields") or ())
		if not derived:
			problems.append(("E-EDIT-OP", "the merged element is identical — the edit changes nothing"))
		if not (declared <= _known_paths(element, after)):
			problems.append(("E-EDIT-OP",
				"changed_fields names paths outside the diff grammar: {}".format(
					sorted(declared - _known_paths(element, after)))))
	reason_problem = _check_reason(edit, op, index)
	if reason_problem:
		problems.append(("E-EDIT-OP", reason_problem))
	return problems


def _known_paths(before, after) -> set:
	paths = set()
	for key in set(before) | set(after):
		if key in _NESTED_ITEM_FIELDS and isinstance(before.get(key), dict) \
				and isinstance(after.get(key), dict):
			for sub in set(before[key]) | set(after[key]):
				paths.add("{}.{}".format(key, sub))
		else:
			paths.add(key)
	return paths


# ── declared scope (§1.6) ───────────────────────────────────────────────────
def _declared_scope(edit, canonical_kind):
	op = edit.get("op")
	target = edit.get("target") or {}
	tool_id, element_id = target.get("tool_id"), target.get("id")
	kind = "suggestion" if canonical_kind == "proposal" else canonical_kind
	if op == "flag":
		return []
	if op == "delete":
		return [("remove", tool_id, kind, element_id)]
	if op == "add":
		return [("create", tool_id, "suggestion", (edit.get("after") or {}).get("id"))]
	if op == "merge":
		return [("field", tool_id, kind, element_id, path)
			for path in edit.get("changed_fields") or ()]
	if op == "move_evidence":
		return [("field", tool_id, kind, element_id, "local.evidence"),
			("field", tool_id, kind, element_id, "local.citations")]
	if op == "annotate":
		return [("field", tool_id, kind,
			tool_id if kind == "tool" else element_id, "convergence_notes")]
	return [("field", tool_id, kind, element_id, target.get("field"))]


# ── dependency handling ─────────────────────────────────────────────────────
def _order_and_close(edits_by_id, rejected):
	"""→ (application order, extra rejections). Rejection is transitively
	closed over `requires`; a cycle rejects its members; `supersedes` drops
	the superseded edit (recorded as superseded, not applied).

	A superseded edit stays suppressed even when its superseder is later
	rejected — the agent retracted it, and applying a retracted edit is
	worse than losing the replacement; the suppression is visible in
	`superseded` and the rejection in `rejected`, and during the loop any
	reject bounces the submission anyway."""
	extra = []
	superseded = set()
	dead = set(rejected)
	for edit_id, edit in edits_by_id.items():
		for other in edit.get("supersedes") or []:
			if other in edits_by_id:
				superseded.add(other)
			else:
				# The edit is REJECTED, not merely noted — leaving it out of
				# `dead` let its dependents apply without it, which is how a
				# merge's absorbed sibling got deleted while the merge that
				# was to carry its content never ran (silent content loss,
				# the exact failure shape (a) exists to prevent).
				extra.append((edit_id, "E-EDIT-DEP",
					"supersedes unknown edit {!r}".format(other)))
				dead.add(edit_id)
	changed = True
	while changed:
		changed = False
		for edit_id, edit in edits_by_id.items():
			if edit_id in dead:
				continue
			for req in edit.get("requires") or []:
				if req not in edits_by_id or req in dead or req in superseded:
					extra.append((edit_id, "E-EDIT-DEP",
						"requires {!r}, which is unknown, rejected or superseded".format(req)))
					dead.add(edit_id)
					changed = True
					break
	order = []
	placed = set()
	pending = [eid for eid in sorted(edits_by_id)
		if eid not in dead and eid not in superseded]
	while pending:
		progressed = False
		for edit_id in list(pending):
			requires = [r for r in edits_by_id[edit_id].get("requires") or []
				if r not in dead and r not in superseded]
			if all(r in placed for r in requires):
				order.append(edit_id)
				placed.add(edit_id)
				pending.remove(edit_id)
				progressed = True
		if not progressed:
			for edit_id in pending:  # a requires cycle
				extra.append((edit_id, "E-EDIT-DEP",
					"`requires` forms a cycle through {!r}".format(edit_id)))
			break
	return order, extra, superseded


# ── phase 3: APPLY ──────────────────────────────────────────────────────────
def _apply_edit(corpus, edit, index_post):
	op = edit["op"]
	target = edit.get("target") or {}
	tool_id = target.get("tool_id")
	view = index_post.views[tool_id]
	if op == "flag":
		return
	if op == "add":
		view.setdefault("suggestions", []).append(copy.deepcopy(edit["after"]))
		return
	element, canonical = index_post.resolve(tool_id, target.get("kind"), target.get("id"))
	if op == "delete":
		array = view["items"] if canonical == "item" else view["suggestions"]
		array.remove(element)
		return
	if op == "merge":
		items = view["items"]
		items[items.index(element)] = copy.deepcopy(edit["after"])
		return
	if op == "move_evidence":
		local = element["local"]
		local["evidence"].remove((edit.get("precondition") or {})["before"])
		citations = local.get("citations")
		if not isinstance(citations, list):
			citations = []
			local["citations"] = citations
		citation = dict(copy.deepcopy(edit["after"]["citation"]))
		citation.setdefault("url", None)
		citations.append(citation)
		replacement = edit["after"].get("evidence")
		if replacement is not None:
			local["evidence"].append(copy.deepcopy(replacement))
		return
	if op == "annotate":
		notes = element.setdefault("convergence_notes", [])
		notes.append({"edit_id": edit["edit_id"], "note": edit["after"]})
		return
	_set_path(element, target["field"], edit["after"])


# ── phase 4: DERIVE ─────────────────────────────────────────────────────────
def _element_diff_paths(before, after, kind) -> set:
	paths = set()
	if kind == "item":
		for key in set(before) | set(after):
			if key in _NESTED_ITEM_FIELDS and isinstance(before.get(key), dict) \
					and isinstance(after.get(key), dict):
				for sub in set(before[key]) | set(after[key]):
					if before[key].get(sub) != after[key].get(sub):
						paths.add("{}.{}".format(key, sub))
			elif before.get(key) != after.get(key):
				paths.add(key)
	else:
		for key in set(before) | set(after):
			if before.get(key) != after.get(key):
				paths.add(key)
	return paths


def _elements_by_id(view, key):
	out = {}
	for entry in view.get(key) or []:
		if isinstance(entry, dict) and isinstance(entry.get("id"), str):
			out[entry["id"]] = entry
	return out


def _derive_diff(corpus_pre, corpus_post):
	"""The structural difference between the two corpora, computed WITHOUT
	reference to the edit list (§1.7 phase 4). Every `before` here is derived
	from corpus_pre. → (diff entries, scope-key set)."""
	diff = []
	keys = set()
	pre_views = {v["id"]: v for v in corpus_pre.get("tools") or []
		if isinstance(v, dict) and isinstance(v.get("id"), str)}
	post_views = {v["id"]: v for v in corpus_post.get("tools") or []
		if isinstance(v, dict) and isinstance(v.get("id"), str)}
	for tool_id in sorted(set(pre_views) | set(post_views)):
		pre_v, post_v = pre_views.get(tool_id, {}), post_views.get(tool_id, {})
		pre_notes = pre_v.get("convergence_notes")
		post_notes = post_v.get("convergence_notes")
		if pre_notes != post_notes:
			keys.add(("field", tool_id, "tool", tool_id, "convergence_notes"))
			diff.append({"tool_id": tool_id, "kind": "tool", "element": tool_id,
				"change": "field", "field": "convergence_notes",
				"before": copy.deepcopy(pre_notes), "after": copy.deepcopy(post_notes)})
		for kind, array in (("item", "items"), ("suggestion", "suggestions")):
			pre_e = _elements_by_id(pre_v, array)
			post_e = _elements_by_id(post_v, array)
			for element_id in sorted(set(pre_e) | set(post_e)):
				if element_id not in post_e:
					keys.add(("remove", tool_id, kind, element_id))
					diff.append({"tool_id": tool_id, "kind": kind,
						"element": element_id, "change": "removed",
						"before": copy.deepcopy(pre_e[element_id]), "after": None})
				elif element_id not in pre_e:
					keys.add(("create", tool_id, kind, element_id))
					diff.append({"tool_id": tool_id, "kind": kind,
						"element": element_id, "change": "created",
						"before": None, "after": copy.deepcopy(post_e[element_id])})
				else:
					for path in sorted(_element_diff_paths(
							pre_e[element_id], post_e[element_id], kind)):
						keys.add(("field", tool_id, kind, element_id, path))
						found_b, before = _get_path(pre_e[element_id], path)
						found_a, after = _get_path(post_e[element_id], path)
						diff.append({"tool_id": tool_id, "kind": kind,
							"element": element_id, "change": "field", "field": path,
							"before": copy.deepcopy(before) if found_b else None,
							"after": copy.deepcopy(after) if found_a else None})
	return diff, keys


# ── phase 5: element re-validation ──────────────────────────────────────────
def _element_codes(item, tool_id, link_count, watch_topics) -> set:
	findings = validate_items.Findings()
	# The explicit no-I/O mode: `RootResolver([])` would still answer an
	# absolute evidence path with `os.path.exists`, and I-23 would read the
	# file. The applier re-grounds nothing; it reads the carried record.
	resolver = validate_items.NO_IO_RESOLVER
	try:
		validate_items.validate_item(copy.deepcopy(item), tool_id, item.get("id"),
			link_count, findings, resolver, watch_topics)
	except Exception as exc:  # noqa: BLE001 — a crash is a schema failure, not an abort
		return {"E-VALIDATOR-CRASH:{}".format(type(exc).__name__)}
	return {entry["code"] for entry in findings.sorted()} - _PATH_CODES


def _suggestion_codes(sug, tool_id) -> set:
	findings = validate_items.Findings()
	kind = assemble.suggestion_kind(sug)
	if kind not in model.SUGGESTION_KINDS:
		findings.add("E-ENUM-INVALID", "unknown suggestion kind", tool_id=tool_id)
	validate_items._validate_memory_proposal(sug, kind, findings, tool_id, sug.get("id"))
	return {entry["code"] for entry in findings.sorted()}


def _pre_codes_for(corpus_pre, element_id) -> set:
	return {f.get("code") for f in corpus_pre.get("findings") or []
		if isinstance(f, dict) and f.get("item_id") == element_id} - _PATH_CODES


# ── G-SEC: display priority across the edit (§4.7) ─────────────────────────
def _prominence(corpus_pre, corpus_post) -> dict:
	"""tool_id → {"from", "to", "lost"} for every tool whose G-SEC display
	priority differs pre vs post, EITHER direction — read through
	`converge.axis_value`. `lost` is any strict decrease in rank (§12
	A-R3-2), leaving G-SEC included."""
	out = {}
	pre_views = {v.get("id"): v for v in corpus_pre.get("tools") or []
		if isinstance(v, dict)}
	for post_view in corpus_post.get("tools") or []:
		if not isinstance(post_view, dict):
			continue
		pre_view = pre_views.get(post_view.get("id"))
		if pre_view is None:
			continue
		before = contract.axis_value(pre_view, contract.PRIORITY_AXIS)
		after = contract.axis_value(post_view, contract.PRIORITY_AXIS)
		if before != after:
			out[post_view["id"]] = {"from": before, "to": after,
				"lost": contract.is_demotion(before, after)}
	return out


def _post_edit_i22(corpus_pre, corpus_post, diff) -> list:
	"""W-EDIT-SCHEMA notes for I-22 codes an item edit newly causes on a
	suggestion of the same tool (phase 5a'). Both sides computed by the ONE
	rule, `validate_items.i22_findings`, over the pre and post items."""
	notes = []
	item_edits = {}
	for entry in diff:
		if entry.get("kind") == "item":
			item_edits.setdefault(entry["tool_id"], set()).update(entry.get("edit_ids") or ())
	pre_views = {v.get("id"): v for v in corpus_pre.get("tools") or [] if isinstance(v, dict)}
	for post_view in corpus_post.get("tools") or []:
		tool_id = post_view.get("id") if isinstance(post_view, dict) else None
		if tool_id not in item_edits or tool_id not in pre_views:
			continue
		pre_items = _elements_by_id(pre_views[tool_id], "items")
		post_items = _elements_by_id(post_view, "items")
		pre_sugs = _elements_by_id(pre_views[tool_id], "suggestions")
		implicating = sorted(item_edits[tool_id])
		for sug_id, sug in sorted(_elements_by_id(post_view, "suggestions").items()):
			if not model.needs_a_decision(assemble.suggestion_kind(sug)):
				continue
			before = {code for code, _, _ in validate_items.i22_findings(
				pre_sugs.get(sug_id, sug), pre_items)}
			after = validate_items.i22_findings(sug, post_items)
			for code, message, _ in after:
				if code in before:
					continue
				notes.append(_finding("W-EDIT-SCHEMA",
					"{} on {} now carries {} after an item edit: {}".format(
						sug_id, tool_id, code, message),
					edit_id=implicating[0] if implicating else None, tool_id=tool_id))
	return notes


# ── attribution (§3.4b) ─────────────────────────────────────────────────────
def _closure_without(edits_by_id, order, omit) -> list:
	"""The application order with `omit` and everything requiring it (
	transitively) removed — an omission respects the same dependency rule a
	rejection does."""
	dead = set(omit)
	changed = True
	while changed:
		changed = False
		for edit_id in order:
			if edit_id in dead:
				continue
			if any(req in dead for req in edits_by_id[edit_id].get("requires") or []):
				dead.add(edit_id)
				changed = True
	return [eid for eid in order if eid not in dead]


def _tool_state(corpus_pre, tool_id, edits_by_id, order, index_kind_cache):
	"""Re-derive one tool's axes under a subset of edits — the replay
	primitive leave-one-out is built from."""
	view = copy.deepcopy(next(v for v in corpus_pre["tools"] if v.get("id") == tool_id))
	shell = {"tools": [view]}
	index_post = _Index(shell)
	for edit_id in order:
		edit = edits_by_id[edit_id]
		if (edit.get("target") or {}).get("tool_id") == tool_id:
			_apply_edit(shell, edit, index_post)
			index_post = _Index(shell)
	contract.derive_tool_state(view, watch_topics_cache(corpus_pre, index_kind_cache, tool_id))
	return {axis: contract.axis_value(view, axis)
		for axis in contract.MOVED_AXES + (contract.PRIORITY_AXIS,)}


def watch_topics_cache(corpus_pre, cache, tool_id):
	if tool_id not in cache:
		cache[tool_id] = contract.watch_topics_for(corpus_pre, tool_id)
	return cache[tool_id]


def _attribute(corpus_pre, tool_id, moved_axes, tool_edit_ids, edits_by_id, order,
		topics_cache):
	"""Leave-one-out per moved axis; where no single omission restores the
	pre value, search minimal omission subsets (joint causes); `attributed_to`
	is then every member of the smallest restoring subset. `unattributed` is
	the defensive residue — with deterministic per-tool derivation, omitting
	every edit always restores, so a true unattributed move means the applier
	cannot explain its own output and the gate fails closed."""
	pre_view = next(v for v in corpus_pre["tools"] if v.get("id") == tool_id)
	tool_order = [eid for eid in order if eid in tool_edit_ids]
	# Only a bucket-capable op can change a derived MOVED axis: every one is a
	# function of tags, severity, `local` enums, `security` blocks and the
	# suggestion/item sets — text trims, evidence moves, annotations and
	# flags touch none of those inputs. Display priority reads one input more,
	# `local.evidence` (a usage confirmation), so its candidate set adds
	# `move_evidence` (`PROMINENCE_CAPABLE_OPS`). Restricting the replay to
	# the capable set keeps leave-one-out at k replays and the subset search
	# tractable.
	out = {}
	for axis, change in moved_axes.items():
		ops = contract.PROMINENCE_CAPABLE_OPS if axis == contract.PRIORITY_AXIS \
			else contract.BUCKET_CAPABLE_OPS
		capable = [eid for eid in tool_order if edits_by_id[eid].get("op") in ops]
		pre_value = contract.axis_value(pre_view, axis)
		causes = []
		for edit_id in capable:
			kept = _closure_without(edits_by_id, tool_order, {edit_id})
			state = _tool_state(corpus_pre, tool_id, edits_by_id, kept, topics_cache)
			if state[axis] == pre_value:
				causes.append(edit_id)
		joint = False
		if not causes and capable:
			from itertools import combinations
			found = None
			# Redundant joint sufficiency is rare and small; past the cap the
			# honest answer is "all of them, jointly" rather than 2^k replays.
			searchable = len(capable) <= 12
			for size in range(2, len(capable) + 1) if searchable else ():
				for combo in combinations(capable, size):
					kept = _closure_without(edits_by_id, tool_order, set(combo))
					state = _tool_state(corpus_pre, tool_id, edits_by_id, kept,
						topics_cache)
					if state[axis] == pre_value:
						found = list(combo)
						break
				if found:
					break
			if found is None:
				kept = _closure_without(edits_by_id, tool_order, set(capable))
				state = _tool_state(corpus_pre, tool_id, edits_by_id, kept,
					topics_cache)
				if state[axis] == pre_value:
					found = list(capable)
			if found:
				causes, joint = found, True
		out[axis] = {"attributed_to": causes, "joint": joint,
			"unattributed": not causes}
	return out


# ── the checks' attestation verification (§2) ───────────────────────────────
def _attestation_map(converge, findings):
	entries = {}
	for entry in converge.get("checks") or []:
		if not isinstance(entry, dict) or entry.get("check") not in contract.CHECK_IDS:
			findings.append(_finding("E-CHECK-CLOSURE",
				"an attestation names no known check: {}".format(_short(entry))))
			continue
		if entry["check"] in entries:
			findings.append(_finding("E-CHECK-CLOSURE",
				"check {} is attested twice".format(entry["check"])))
		entries[entry["check"]] = entry
	for name in contract.CHECK_IDS:
		if name not in entries:
			findings.append(_finding("E-CHECK-CLOSURE",
				"check {} has no attestation — one per check is mandatory".format(name)))
	return entries


def _verify_closure_and_ops(attestations, edits, findings):
	by_check = {name: set() for name in contract.CHECK_IDS}
	for edit in edits:
		check = edit.get("check")
		if check in by_check:
			by_check[check].add(edit["edit_id"])
			if edit.get("op") not in contract.CHECK_OPS[check]:
				findings.append(_finding("E-CHECK-OP",
					"{} emits {} edits only (got {})".format(
						check, "/".join(contract.CHECK_OPS[check]), edit.get("op")),
					edit_id=edit["edit_id"]))
	for name, entry in attestations.items():
		declared = entry.get("edits")
		declared = set(declared) if isinstance(declared, list) else set()
		if declared != by_check[name]:
			findings.append(_finding("E-CHECK-CLOSURE",
				"{}.edits != the edits naming it (attested {}, actual {})".format(
					name, sorted(declared), sorted(by_check[name]))))


def _scanned(entry) -> dict:
	scanned = entry.get("scanned")
	return scanned if isinstance(scanned, dict) else {}


def verify_c1(entry, corpus_pre, tables, findings):
	"""C1 — evidence hygiene. The scanned counts and the findings count are
	recomputable exactly: the step-4 population (a claimed live risk with no
	path behind it) is E-REACHES-UNEVIDENCED's population, which
	EVIDENCE_FINDING_CODES already includes."""
	distributions = tables["distributions"]
	want = {"tools": len(corpus_pre.get("tools") or []),
		"items": distributions["items"],
		"evidence_entries": distributions["evidence_entries"]}
	scanned = _scanned(entry)
	for key, value in want.items():
		if scanned.get(key) != value:
			findings.append(_finding("E-CHECK-ARITH",
				"C1 scanned.{} is {!r}; the corpus holds {}".format(
					key, scanned.get(key), value)))
	expected = len(tables["evidence_findings"])
	if entry.get("findings") != expected:
		findings.append(_finding("E-CHECK-ARITH",
			"C1 findings is {!r}; evidence_findings holds {}".format(
				entry.get("findings"), expected)))


def verify_c2(entry, corpus_pre, tables, findings, edits):
	"""C2 — tags → visibility. Full coverage is arithmetic: the clean set
	plus the distinct items its edits touched must account for every item."""
	total = tables["distributions"]["items"]
	scanned = _scanned(entry)
	if scanned.get("items") != total:
		findings.append(_finding("E-CHECK-ARITH",
			"C2 scanned.items is {!r}; the corpus holds {}".format(
				scanned.get("items"), total)))
	touched = {(e.get("target") or {}).get("id") for e in edits
		if e.get("check") == "C2-tags-visibility"
		and (e.get("target") or {}).get("kind") == "item"}
	touched.discard(None)
	clean = entry.get("clean")
	if isinstance(clean, list):
		clean_count = len(set(clean) - touched)
	elif isinstance(clean, int):
		clean_count = clean
	else:
		clean_count = None
	if clean_count is None or clean_count + len(touched) != total:
		findings.append(_finding("E-CHECK-ARITH",
			"C2 clean ({}) + items its edits touch ({}) != scanned items ({})".format(
				_short(clean), len(touched), total)))


def verify_c3(entry, corpus_pre, tables, findings):
	"""C3 — enumerating the security-only set IN FULL is the check."""
	expected = {}
	for view in corpus_pre.get("tools") or []:
		if (view.get("bucket_inputs") or {}).get("security_only"):
			cves = set()
			for item in view.get("items") or []:
				security = item.get("security") if isinstance(item, dict) else None
				if isinstance(security, dict) and isinstance(security.get("cve_id"), str):
					cves.add(security["cve_id"])
			expected[view["id"]] = len(cves)
	listed = entry.get("security_only_tools")
	listed = listed if isinstance(listed, list) else []
	by_tool = {row.get("tool_id"): row for row in listed if isinstance(row, dict)}
	if set(by_tool) != set(expected):
		findings.append(_finding("E-CHECK-ARITH",
			"C3 must enumerate exactly the security_only tools {} (got {})".format(
				sorted(expected), sorted(by_tool))))
		return
	for tool_id, row in by_tool.items():
		if row.get("cve_count") != expected[tool_id]:
			findings.append(_finding("E-CHECK-ARITH",
				"C3 {}: cve_count {!r} != {}".format(
					tool_id, row.get("cve_count"), expected[tool_id])))
		verdict = row.get("verdict")
		if not isinstance(verdict, str) or not verdict.strip():
			findings.append(_finding("E-CHECK-ARITH",
				"C3 {}: a one-line verdict is required".format(tool_id)))


def verify_c4(entry, corpus_pre, tables, findings):
	"""C4 — the four populations are recomputable exactly."""
	security_items = unrated = does_not_reach = 0
	for view in corpus_pre.get("tools") or []:
		for item in view.get("items") or []:
			if not isinstance(item, dict):
				continue
			tags = item.get("tags")
			if not (isinstance(tags, list) and "security" in tags):
				continue
			security_items += 1
			security = item.get("security")
			if isinstance(security, dict) and security.get("rating_basis") == "unrated":
				unrated += 1
			local = item.get("local")
			if isinstance(local, dict) and local.get("direction") == "does_not_reach":
				does_not_reach += 1
	want = {"security_items": security_items, "rating_unrated": unrated,
		"direction_does_not_reach": does_not_reach,
		"anchor_duplicates": len(tables["anchor_duplicates"])}
	scanned = _scanned(entry)
	for key, value in want.items():
		if scanned.get(key) != value:
			findings.append(_finding("E-CHECK-ARITH",
				"C4 scanned.{} is {!r}; the corpus holds {}".format(
					key, scanned.get(key), value)))


def verify_c5(entry, corpus_pre, tables, findings):
	"""C5 — one verdict per auto-bucketed or pre-accept-eligible tool, no
	sampling. The enumerated set must equal the recomputed set."""
	expected = {view["id"] for view in corpus_pre.get("tools") or []
		if view.get("initial_review_bucket") == "security_auto"
		or view.get("initial_pre_accept")}
	listed = entry.get("tools")
	listed = listed if isinstance(listed, list) else []
	by_tool = {row.get("tool_id"): row for row in listed if isinstance(row, dict)}
	if set(by_tool) != expected:
		findings.append(_finding("E-CHECK-ARITH",
			"C5 must enumerate exactly the auto/pre-accept set {} (got {})".format(
				sorted(expected), sorted(by_tool))))
		return
	for tool_id, row in by_tool.items():
		for key in ("deciding_input", "verdict"):
			value = row.get(key)
			if not isinstance(value, str) or not value.strip():
				findings.append(_finding("E-CHECK-ARITH",
					"C5 {}: `{}` is required".format(tool_id, key)))


def verify_c6(entry, corpus_pre, tables, findings, ledger):
	"""C6 — the ledger IS the attestation (§2.6): every proposal in exactly
	one disposition, every tagged proposal reviewed in both directions with
	`restored` recorded, `fired_this_run` recomputed against the grounded
	hits, `used_correctly` present on every existing watch item."""
	proposal_ids = {p["suggestion_id"] for bucket in tables["proposals"].values()
		for p in bucket}
	tagged_ids = {p["suggestion_id"] for bucket in tables["proposals"].values()
		for p in bucket if p["self_test"]["verdict"] == "fails"}
	if not isinstance(ledger, dict):
		findings.append(_finding("E-CHECK-ARITH", "C6: the ledger block is missing"))
		return
	watch = ledger.get("watch_items") if isinstance(ledger.get("watch_items"), dict) else {}
	notes_tool = ledger.get("method_notes_tool") \
		if isinstance(ledger.get("method_notes_tool"), dict) else {}
	notes_global = ledger.get("method_notes_global") \
		if isinstance(ledger.get("method_notes_global"), dict) else {}
	disposed = {}
	for block, arrays in (
			(watch, ("proposed_kept", "proposed_cut", "rehomed_to_method_note")),
			(notes_tool, ("kept", "cut", "promoted_to_global")),
			(notes_global, ("kept", "cut", "demoted_to_tool"))):
		for array in arrays:
			for row in block.get(array) or []:
				if isinstance(row, dict) and isinstance(row.get("suggestion_id"), str):
					disposed.setdefault(row["suggestion_id"], []).append(array)
					if row["suggestion_id"] in tagged_ids \
							and array in ("proposed_kept", "proposed_cut", "kept", "cut") \
							and not isinstance(row.get("restored"), bool):
						findings.append(_finding("E-CHECK-ARITH",
							"C6 {}: a tagged proposal's disposition must record "
							"`restored`".format(row["suggestion_id"])))
	for sug_id in sorted(proposal_ids):
		homes = disposed.get(sug_id, [])
		if len(homes) != 1:
			findings.append(_finding("E-CHECK-ARITH",
				"C6 {}: must appear in exactly ONE disposition (found {})".format(
					sug_id, homes or "none")))
	for sug_id in sorted(set(disposed) - proposal_ids):
		findings.append(_finding("E-CHECK-ARITH",
			"C6 {}: disposed but proposed by nothing in this run".format(sug_id)))
	# Absent vs present-but-empty, kept apart the way watch-hit grounding
	# keeps "never checked" (None) apart from "checked, no match" (empty
	# set). An absent store makes the checks below — and C6's own
	# duplicate-against-store step — vacuous, and a vacuous check that says
	# nothing is how three grounding channels on this project shipped with
	# nothing writing their input.
	stores = corpus_pre.get("stores") or {}
	for store_name in ("watch_items", "method_notes"):
		status = contract.store_status(stores, store_name)
		if status == "absent":
			findings.append(_finding("W-STORE-UNCHECKED",
				"the {} store was never snapshotted into this session — C6's "
				"store-dependent checks ran against nothing, not against an "
				"empty store".format(store_name.replace("_", "-"))))
		elif status == "unreadable":
			# The opposite remedy from absent: the operator DID copy the
			# snapshot; the copy cannot be read. Say so, or the note sends
			# them to re-copy a file that needs fixing.
			findings.append(_finding("W-STORE-UNCHECKED",
				"the {} store WAS snapshotted into this session but cannot be "
				"read ({}) — fix the copied file; C6's store-dependent checks "
				"ran against nothing".format(store_name.replace("_", "-"),
					stores[store_name].get(contract.STORE_UNREADABLE_KEY))))
	# the existing store, per tool in the run
	snapshot = contract.store_entries(stores, "watch_items") or {}
	run_tools = {v.get("id") for v in corpus_pre.get("tools") or []}
	hits = {}
	for view in corpus_pre.get("tools") or []:
		grounded = set(view.get("watch_hit_item_ids") or [])
		topics = set()
		for item in view.get("items") or []:
			if isinstance(item, dict) and item.get("id") in grounded:
				topics.add((item.get("watch_hit") or {}).get("topic", "").strip())
		hits[view.get("id")] = topics
	existing = {(row.get("tool_id"), row.get("topic")): row
		for row in watch.get("existing") or [] if isinstance(row, dict)}
	for tool_id, entries in sorted(snapshot.items()):
		if tool_id not in run_tools:
			continue
		for stored in entries if isinstance(entries, list) else ():
			if not isinstance(stored, dict) or not isinstance(stored.get("topic"), str):
				continue
			topic = stored["topic"].strip()
			row = existing.get((tool_id, topic))
			if row is None:
				findings.append(_finding("E-CHECK-ARITH",
					"C6: stored watch item ({}, {!r}) has no `existing` row — "
					"\"checked, no match\" and \"never checked\" must be "
					"distinguishable".format(tool_id, topic)))
				continue
			fired = topic in hits.get(tool_id, set())
			if row.get("fired_this_run") is not fired:
				findings.append(_finding("E-CHECK-ARITH",
					"C6 ({}, {!r}): fired_this_run must be {} — grounded hits say so".format(
						tool_id, topic, fired)))
			if not isinstance(row.get("used_correctly"), bool):
				findings.append(_finding("E-CHECK-ARITH",
					"C6 ({}, {!r}): used_correctly must be recorded (§I4)".format(
						tool_id, topic)))


def verify_c7(entry, corpus_pre, tables, findings, surviving_suggestion_ids):
	"""C7 — cross-tool collisions. The cluster count is the table's; every
	surviving suggestion the attestation names must actually survive."""
	expected = len(tables["file_collisions"])
	if entry.get("clusters_inspected") != expected:
		findings.append(_finding("E-CHECK-ARITH",
			"C7 clusters_inspected is {!r}; file_collisions holds {}".format(
				entry.get("clusters_inspected"), expected)))
	for row in entry.get("resolved") or []:
		if not isinstance(row, dict):
			continue
		survivor = row.get("surviving_suggestion_id")
		if survivor is not None and survivor not in surviving_suggestion_ids:
			findings.append(_finding("E-CHECK-ARITH",
				"C7: surviving suggestion {!r} does not survive in corpus.post".format(
					survivor)))


# ── corpus_effect (§3.5) ────────────────────────────────────────────────────
def _severity_counts(corpus) -> dict:
	counts = {}
	for view in corpus.get("tools") or []:
		for item in view.get("items") or []:
			if isinstance(item, dict) and item.get("severity") in model.SEVERITIES:
				counts[item["severity"]] = counts.get(item["severity"], 0) + 1
	return {s: counts.get(s, 0) for s in model.SEVERITIES}


def _corpus_numbers(corpus) -> dict:
	items = sum(1 for view in corpus.get("tools") or []
		for item in view.get("items") or [] if isinstance(item, dict))
	severity = _severity_counts(corpus)
	warning_plus = severity["warning"] + severity["incompatible"]
	security_tagged = sum(1 for view in corpus.get("tools") or []
		for item in view.get("items") or []
		if isinstance(item, dict) and isinstance(item.get("tags"), list)
		and "security" in item["tags"])
	pre_accept = sum(1 for view in corpus.get("tools") or []
		if view.get("initial_pre_accept"))
	return {"items": items, "severity": severity, "warning_plus": warning_plus,
		"security_tagged": security_tagged, "pre_accept": pre_accept}


def compute_corpus_effect(corpus_pre, corpus_post, applied_edits, moved) -> dict:
	"""The applier's own numbers — the ones that ship whatever the agent
	declared (§3.5)."""
	pre = _corpus_numbers(corpus_pre)
	post = _corpus_numbers(corpus_post)
	edits_by_op = {}
	for edit in applied_edits:
		edits_by_op[edit["op"]] = edits_by_op.get(edit["op"], 0) + 1
	if pre["warning_plus"]:
		pct = round((post["warning_plus"] - pre["warning_plus"])
			* 100.0 / pre["warning_plus"], 1)
	else:
		pct = 0.0
	tools_moved = {"permissive": [], "restrictive": [], "lateral": []}
	for tool_id, record in sorted(moved.items()):
		tools_moved[record["direction"]].append(tool_id)
	return {
		"edits_by_op": dict(sorted(edits_by_op.items())),
		"items": {"before": pre["items"], "after": post["items"]},
		"severity_after": post["severity"],
		"severity_at_warning_or_worse": {"before": pre["warning_plus"],
			"after": post["warning_plus"], "pct_change": pct},
		"tag_security_items": {"before": pre["security_tagged"],
			"after": post["security_tagged"]},
		"tools_moved": tools_moved,
		"pre_accept": {"before": pre["pre_accept"], "after": post["pre_accept"]},
	}


def _verify_effect(declared, computed, corpus_pre, corpus_post, findings):
	for field in contract.EFFECT_FIELDS:
		if declared.get(field) != computed[field]:
			critical = field in contract.EFFECT_SAFETY_FIELDS
			findings.append(_finding("E-EFFECT-ARITH",
				"corpus_effect.{} is {}; recomputed {}".format(
					field, _short(declared.get(field)), _short(computed[field])),
				critical=critical))
	narrative = declared.get("narrative")
	if not isinstance(narrative, str) or not narrative.strip():
		findings.append(_finding("E-EFFECT-NARRATIVE",
			"corpus_effect.narrative is mandatory"))
		return
	pct = computed["severity_at_warning_or_worse"]["pct_change"]
	if pct < contract.NARRATIVE_ENUMERATION_PCT:
		pre_counts, post_counts = {}, {}
		for corpus, counts in ((corpus_pre, pre_counts), (corpus_post, post_counts)):
			for view in corpus.get("tools") or []:
				n = sum(1 for item in view.get("items") or []
					if isinstance(item, dict)
					and item.get("severity") in ("warning", "incompatible"))
				counts[view.get("id")] = n
		missing = [tool_id for tool_id, n in sorted(pre_counts.items())
			if post_counts.get(tool_id, 0) < n and tool_id not in narrative]
		if missing:
			findings.append(_finding("E-EFFECT-NARRATIVE",
				"warning-or-worse fell {}% — the narrative must name every affected "
				"tool individually; missing: {}".format(pct, missing)))


# ── the auto-update label (§4) ──────────────────────────────────────────────
def _counterweight(pre_view, attributed, edits_by_id) -> dict:
	cves = set()
	worst = None
	for item in pre_view.get("items") or []:
		security = item.get("security") if isinstance(item, dict) else None
		if not isinstance(security, dict):
			continue
		if isinstance(security.get("cve_id"), str):
			cves.add(security["cve_id"])
		rating = security.get("rating")
		if rating in model.CVE_WORSE_RANK and (worst is None
				or model.CVE_WORSE_RANK[rating] > model.CVE_WORSE_RANK[worst]):
			worst = rating
	removed = retagged = 0
	for edit_id in attributed:
		op = edits_by_id[edit_id].get("op")
		if op in ("delete", "merge"):
			removed += 1
		elif op in ("retag", "rerate", "redirect"):
			retagged += 1
	return {"cve_count": len(cves), "worst_rating": worst,
		"items_removed": removed, "items_retagged": retagged}


def _build_tool_blocks(corpus_pre, corpus_post, applied, edits_by_id, moved,
		attribution, forced, prominence=None):
	"""§4.2's per-tool `convergence` block, written into converge-effect.json
	for the report stage to merge onto each tool. The label is DERIVED here,
	never declared by convergence."""
	pre_views = {v["id"]: v for v in corpus_pre.get("tools") or []}
	post_views = {v["id"]: v for v in corpus_post.get("tools") or []}
	edits_by_tool = {}
	for edit_id in applied:
		tool_id = (edits_by_id[edit_id].get("target") or {}).get("tool_id")
		edits_by_tool.setdefault(tool_id, []).append(edit_id)
	blocks = {}
	for tool_id in sorted(post_views):
		pre_v, post_v = pre_views.get(tool_id, {}), post_views[tool_id]
		edit_ids = sorted(edits_by_tool.get(tool_id, []))
		record = moved.get(tool_id)
		is_forced = tool_id in forced
		final_auto = (post_v.get("initial_review_bucket") == "security_auto"
			or bool(post_v.get("initial_pre_accept")))
		changed_priority = (prominence or {}).get(tool_id)
		if not edit_ids and not record and not final_auto and not is_forced \
				and not changed_priority:
			continue
		block = {"touched": bool(edit_ids), "edit_ids": edit_ids}
		attributed = attribution.get(tool_id, {})
		if changed_priority:
			# G-SEC: every priority change, either direction, with its
			# attribution — the page discloses the losses expanded (O3).
			pre_tier = pre_v.get("security_tier")
			post_tier = post_v.get("security_tier")
			block["security_priority"] = {
				"from": changed_priority["from"],
				"to": changed_priority["to"],
				"lost": changed_priority["lost"],
				"reasons_from": list(pre_tier["reasons"])
					if model.valid_security_tier(pre_tier) else [],
				"reasons_to": list(post_tier["reasons"])
					if model.valid_security_tier(post_tier) else [],
				"attributed_to": sorted(attributed.get(contract.PRIORITY_AXIS, {})
					.get("attributed_to", [])),
			}
			# What the page's "Lowered by convergence" disclosure shows for each
			# attributed edit: its headline and its verbatim quote — the
			# report carries no converge.json edits, so the record does.
			block["security_priority"]["edits"] = [{
				"edit_id": eid,
				"headline": (edits_by_id[eid].get("reason") or {}).get("headline"),
				"quote": edits_by_id[eid].get("quote")
					if isinstance(edits_by_id[eid].get("quote"), str) else None,
			} for eid in block["security_priority"]["attributed_to"]]
		if record:
			bucket_attr = attributed.get("initial_review_bucket", {})
			block["bucket"] = {
				"from": pre_v.get("initial_review_bucket"),
				"to": post_v.get("initial_review_bucket"),
				"direction": record["direction"],
				"attributed_to": sorted(bucket_attr.get("attributed_to", [])),
				"unattributed": bool(bucket_attr.get("unattributed")),
			}
			accept_attr = attributed.get("initial_pre_accept", {})
			if pre_v.get("initial_pre_accept") != post_v.get("initial_pre_accept"):
				block["pre_accept"] = {
					"from": bool(pre_v.get("initial_pre_accept")),
					"to": bool(post_v.get("initial_pre_accept")),
					"attributed_to": sorted(accept_attr.get("attributed_to", [])),
				}
		if is_forced:
			block["forced"] = forced[tool_id]
			blocks[tool_id] = block
			continue  # a degraded tool carries NO auto_update_label (§4.4)
		if final_auto:
			causes = []
			for axis in ("initial_review_bucket", "initial_pre_accept"):
				causes.extend(attributed.get(axis, {}).get("attributed_to", []))
			causes = sorted(set(causes))
			if causes:
				primary = None
				for edit_id in causes:  # the single restorer, else the earliest
					single = attribution.get(tool_id, {})
					for axis_record in single.values():
						if axis_record.get("attributed_to") == [edit_id] \
								and not axis_record.get("joint"):
							primary = edit_id
							break
					if primary:
						break
				primary = primary or causes[0]
				reason = edits_by_id[primary].get("reason") or {}
				block["auto_update_label"] = {
					"source": "judgement",
					"headline": reason.get("headline"),
					"reasoning": reason.get("body") or reason.get("headline"),
					"confidence": reason.get("confidence", "high"),
					"edit_ids": causes,
					"quotes": [{"edit_id": eid, "text": edits_by_id[eid]["quote"]}
						for eid in causes
						if isinstance(edits_by_id[eid].get("quote"), str)],
					"counterweight": _counterweight(pre_v, causes, edits_by_id),
				}
			else:
				inputs = post_v.get("bucket_inputs") or {}
				post_tier = post_v.get("security_tier")
				if model.valid_security_tier(post_tier):
					# G-SEC: accepted BY TIER — name the tier and its reasons,
					# not security_only/impact, which no longer decide it.
					reasoning = ("Reached {} with zero attributed convergence edits: "
						"a positively identified security fix, security tier {} "
						"(priority {}; reasons {}), version_delta={}, risk_level={}, "
						"no pre-acceptance bars.").format(
							post_v.get("initial_review_bucket"), post_tier["tier"],
							post_tier["priority"], ", ".join(post_tier["reasons"]),
							inputs.get("version_delta"), post_v.get("risk_level"))
				else:
					reasoning = ("Reached {} with zero attributed convergence edits: "
						"security_only={}, impact={}, version_delta={}, risk_level={}, "
						"no pre-acceptance bars.").format(
							post_v.get("initial_review_bucket"),
							inputs.get("security_only"), inputs.get("impact"),
							inputs.get("version_delta"), post_v.get("risk_level"))
				block["auto_update_label"] = {
					"source": "rule",
					"headline": "Auto by rule — the deterministic path alone put it here.",
					"reasoning": reasoning,
					"confidence": "high",
					"edit_ids": [],
					"quotes": [],
					"counterweight": _counterweight(pre_v, [], edits_by_id),
				}
		blocks[tool_id] = block
	return blocks


# ── the pure function ───────────────────────────────────────────────────────
def apply_converge(corpus_pre, converge, attempt=1, terminal=False,
		attempt_log=None):
	"""(corpus_pre, converge) → the whole verdict, without touching disk.

	→ {"state", "critical", "notes", "rejected", "applied", "superseded",
	   "corpus_post", "effect"} where `state` is one of
	   "converged" | "degraded_gate" | "degraded_unapplied" | "rejected".

	"rejected" is the loop's bounce (§3.4d): critical findings while attempts
	remain. With `terminal=True` (attempt 5) the answer is always one of the
	three shipping states — implicated edits are excluded rather than
	bouncing, gate failures force the conservative option, and everything
	excluded or forced is first-class in `convergence_status`.

	A corpus.pre built under another contract or converge version raises
	`converge.CorpusVersionError` FIRST, at every attempt, the terminal one
	included — it never returns a shipping state, so no corpus.post and no
	effect is ever derived from a stale corpus (G-SEC §4.7)."""
	contract.check_corpus_versions(corpus_pre)
	submission = _resolve_submission(corpus_pre, converge)
	if submission:
		if not terminal:
			return {"state": "rejected", "critical": submission, "notes": [],
				"rejected": [], "applied": [], "superseded": [],
				"corpus_post": None, "effect": None}
		return _degrade_unapplied(corpus_pre, converge, attempt, submission,
			attempt_log)

	edits = [e for e in converge["edits"]
		if isinstance(e, dict) and isinstance(e.get("edit_id"), str)]
	edits_by_id = {e["edit_id"]: e for e in edits}
	excluded = {}
	while True:
		result = _apply_once(corpus_pre, converge, edits, edits_by_id, excluded,
			attempt)
		if not terminal:
			break
		implicated = {}
		for finding in result["critical"]:
			if finding["code"] in ("E-APPLY-SCOPE", "E-APPLY-SCHEMA") \
					and finding.get("edit_id"):
				implicated[finding["edit_id"]] = finding
		implicated = {eid: f for eid, f in implicated.items() if eid not in excluded}
		if not implicated:
			break
		excluded.update(implicated)
	if not terminal:
		if result["critical"]:
			result["state"] = "rejected"
		return result
	return _finalize_terminal(corpus_pre, converge, result, edits_by_id, attempt,
		attempt_log, excluded)


def _apply_once(corpus_pre, converge, edits, edits_by_id, excluded, attempt):
	critical, notes = [], []
	rejected = []
	index = _Index(corpus_pre)
	for edit in edits:
		if edit.get("op") == "add" and edit["edit_id"] not in excluded:
			new_id = (edit.get("after") or {}).get("id") \
				if isinstance(edit.get("after"), dict) else None
			if isinstance(new_id, str):
				index.created_suggestion_ids.add(new_id)

	# phase 2 — PRECHECK, per edit in isolation
	precheck_rejected = set()
	for edit in edits:
		if edit["edit_id"] in excluded:
			finding = excluded[edit["edit_id"]]
			rejected.append({"edit_id": edit["edit_id"], "code": finding["code"],
				"detail": finding["detail"]})
			precheck_rejected.add(edit["edit_id"])
			continue
		problems = _precheck_edit(edit, index)
		for code, detail in problems:
			critical.append(_finding(code, detail, edit_id=edit["edit_id"]))
			rejected.append({"edit_id": edit["edit_id"], "code": code,
				"detail": detail})
		if problems:
			precheck_rejected.add(edit["edit_id"])

	# E-EDIT-DUP — same (element, field), no supersedes chain. A
	# whole-element op (delete/merge) conflicts with EVERY other write on
	# that element — applying a field edit to an element another edit
	# removes or replaces would silently lose one of the two. move_evidence
	# edits conflict only on the same entry; annotate appends and never
	# conflicts with itself; flag writes nothing and conflicts with nothing.
	writes = {}
	element_writers = {}
	for edit in edits:
		if edit["edit_id"] in precheck_rejected or edit["op"] == "flag":
			continue
		target = edit.get("target") or {}
		if edit["op"] == "add":
			canonical = "suggestion"
		else:
			_, canonical = index.resolve(target.get("tool_id"), target.get("kind"),
				target.get("id"))
		if edit["op"] not in ("add",):
			element_key = (target.get("tool_id"), canonical, target.get("id"))
			element_writers.setdefault(element_key, []).append(
				(edit["edit_id"], edit["op"]))
		for key in _declared_scope(edit, canonical or target.get("kind")):
			if edit["op"] == "move_evidence" and key[-1] == "local.evidence":
				key = key + (json.dumps((edit.get("precondition") or {})
					.get("before"), sort_keys=True, default=str),)
			if edit["op"] == "move_evidence" and key[-1] == "local.citations":
				continue
			if edit["op"] == "annotate":
				continue
			writes.setdefault(key, []).append(edit["edit_id"])
	for element_key, writers in sorted(element_writers.items()):
		if len(writers) < 2 or not any(op in ("delete", "merge")
				for _, op in writers):
			continue
		writes.setdefault(("element",) + element_key, []).extend(
			edit_id for edit_id, _ in writers)
	for key, writers in sorted(writes.items()):
		if len(writers) < 2:
			continue
		chained = set()
		for edit_id in writers:
			chained.update(edits_by_id[edit_id].get("supersedes") or [])
		conflicted = [w for w in writers if w not in chained]
		if len(conflicted) > 1:
			for edit_id in conflicted:
				if edit_id in precheck_rejected:
					continue
				critical.append(_finding("E-EDIT-DUP",
					"{} write the same target {} with no supersedes chain".format(
						conflicted, key), edit_id=edit_id))
				rejected.append({"edit_id": edit_id, "code": "E-EDIT-DUP",
					"detail": "conflicting writers: {}".format(conflicted)})
				precheck_rejected.add(edit_id)

	# E-EDIT-ID — an add colliding with an existing or just-created id
	created = set()
	for edit in edits:
		if edit["edit_id"] in precheck_rejected or edit["op"] != "add":
			continue
		new_id = (edit.get("after") or {}).get("id")
		if new_id in index.all_suggestion_ids or new_id in created:
			critical.append(_finding("E-EDIT-ID",
				"added id {!r} collides with an existing id".format(new_id),
				edit_id=edit["edit_id"]))
			rejected.append({"edit_id": edit["edit_id"], "code": "E-EDIT-ID",
				"detail": new_id})
			precheck_rejected.add(edit["edit_id"])
		else:
			created.add(new_id)

	order, dep_problems, superseded = _order_and_close(edits_by_id, precheck_rejected)
	for edit_id, code, detail in dep_problems:
		critical.append(_finding(code, detail, edit_id=edit_id))
		rejected.append({"edit_id": edit_id, "code": code, "detail": detail})
	order = [eid for eid in order
		if eid not in {r["edit_id"] for r in rejected}]

	# phase 3 — APPLY, surviving edits only, requires-order
	corpus_post = copy.deepcopy(corpus_pre)
	index_post = _Index(corpus_post)
	for edit_id in order:
		_apply_edit(corpus_post, edits_by_id[edit_id], index_post)
		index_post = _Index(corpus_post)

	# phase 4 — DERIVE: the diff without the edit list, then containment
	diff, derived_keys = _derive_diff(corpus_pre, corpus_post)
	declared = {}
	for edit_id in order:
		edit = edits_by_id[edit_id]
		target = edit.get("target") or {}
		if edit["op"] == "add":
			canonical = "suggestion"
		else:
			_, canonical = index.resolve(target.get("tool_id"),
				target.get("kind"), target.get("id"))
		for key in _declared_scope(edit, canonical):
			declared.setdefault(key, []).append(edit_id)
	for key in sorted(derived_keys - set(declared)):
		owners = sorted({eid for eid in order
			if (edits_by_id[eid].get("target") or {}).get("id") == key[3]
			or (edits_by_id[eid].get("op") == "add"
				and (edits_by_id[eid].get("after") or {}).get("id") == key[3])})
		critical.append(_finding("E-APPLY-SCOPE",
			"the corpora differ at {} and no edit declares it".format(key),
			edit_id=owners[0] if owners else None))
	for key in sorted(set(declared) - derived_keys):
		critical.append(_finding("E-APPLY-SCOPE",
			"edit(s) {} declare {} but the corpora do not differ there".format(
				declared[key], key), edit_id=declared[key][0]))
	for entry in diff:
		key_kind = "remove" if entry["change"] == "removed" else (
			"create" if entry["change"] == "created" else "field")
		if key_kind == "field":
			key = ("field", entry["tool_id"], entry["kind"], entry["element"],
				entry["field"])
		else:
			key = (key_kind, entry["tool_id"], entry["kind"], entry["element"])
		entry["edit_ids"] = sorted(declared.get(key, []))

	# phase 5a — element re-validation (E-APPLY-SCHEMA)
	topics_cache = {}
	touched = {}
	for entry in diff:
		if entry["kind"] in ("item", "suggestion") and entry["change"] != "removed":
			touched.setdefault((entry["tool_id"], entry["kind"], entry["element"]),
				entry["change"])
	for (tool_id, kind, element_id), change in sorted(touched.items()):
		post_view = next(v for v in corpus_post["tools"] if v.get("id") == tool_id)
		element = _elements_by_id(post_view,
			"items" if kind == "item" else "suggestions").get(element_id)
		if element is None:
			continue
		if kind == "item":
			codes = _element_codes(element, tool_id,
				len(post_view.get("links") or []),
				watch_topics_cache(corpus_pre, topics_cache, tool_id))
		else:
			codes = _suggestion_codes(element, tool_id)
		baseline = set() if change == "created" else \
			_pre_codes_for(corpus_pre, element_id)
		new_codes = codes - baseline
		for code in sorted(new_codes):
			severity = model.finding_severity(code.split(":", 1)[0]) \
				if code.split(":", 1)[0] in model.FINDING_CODES else "error"
			implicating = sorted({eid for entry in diff
				if entry["element"] == element_id for eid in entry["edit_ids"]})
			if severity == "error":
				critical.append(_finding("E-APPLY-SCHEMA",
					"{} on {} raises {} where corpus.pre did not".format(
						element_id, tool_id, code),
					edit_id=implicating[0] if implicating else None,
					tool_id=tool_id))
			else:
				notes.append(_finding("W-EDIT-SCHEMA",
					"{} on {} now carries {}".format(element_id, tool_id, code),
					edit_id=implicating[0] if implicating else None,
					tool_id=tool_id))

	# phase 5a' — I-22 after edits (G-SEC): a `required`/`proposed` reading
	# depends on the CURRENT severity of the items a suggestion serves, and a
	# rerate, merge or delete can change it. Re-run I-22 over every suggestion
	# of every tool whose items changed: a code present post and not pre is a
	# W-EDIT-SCHEMA note implicating the item edit — not E-APPLY-SCHEMA: a
	# reasoned rerate of an `incompatible` item is legitimate convergence
	# work, the reading already fails safe (an ungrounded `required` stays
	# required), and any acceptance or prominence change it causes meets the
	# permissive gate or the demotion gate below.
	notes.extend(_post_edit_i22(corpus_pre, corpus_post, diff))

	# phase 5b — differential recomputation over every tool (§3.4a), with the
	# pre side re-derived through the SAME function and checked against the
	# validator's record (E-APPLY-INTERNAL).
	moved = {}
	for post_view in corpus_post.get("tools") or []:
		tool_id = post_view.get("id")
		pre_view = next((v for v in corpus_pre["tools"] if v.get("id") == tool_id), None)
		if pre_view is None:
			continue
		if pre_view.get("validator_error"):
			# A tool whose validator stage failed carries _guard's
			# conservative defaults, not a completed derivation — recomputing
			# would "repair" axes the validator could not stand behind. Its
			# post AXES stay the recorded pre axes (attention/elevated — the
			# conservative direction), so it can never be edited INTO an auto
			# bucket, and the internal self-check has no baseline to hold.
			# The item-derived id exports are a different matter: phase 3 has
			# already applied edits here, and carrying the pre lists forward
			# would hand the renderer `security_display_item_ids` naming
			# items a delete removed. Those are pure functions of the items,
			# so they ARE re-derived — against the recorded axes.
			contract.derive_item_exports(post_view,
				watch_topics_cache(corpus_pre, topics_cache, tool_id))
			continue
		topics = watch_topics_cache(corpus_pre, topics_cache, tool_id)
		pre_check = contract.derive_tool_state(copy.deepcopy(pre_view), topics)
		# The self-check covers the G-SEC tier (whole object) and the bars too:
		# the validator stored both, and the applier re-derives both.
		for axis in contract.MOVED_AXES + ("security_tier", "pre_accept_bars"):
			if pre_check.get(axis) != pre_view.get(axis):
				critical.append(_finding("E-APPLY-INTERNAL",
					"re-deriving {} on corpus.pre gives {}; the validator "
					"recorded {} — an applier bug, not the agent's".format(
						axis, _short(pre_check.get(axis)), _short(pre_view.get(axis))),
					tool_id=tool_id))
		contract.derive_tool_state(post_view, topics)
		axes = {}
		for axis in contract.MOVED_AXES:
			if pre_view.get(axis) != post_view.get(axis):
				axes[axis] = {"from": pre_view.get(axis), "to": post_view.get(axis)}
		if axes:
			moved[tool_id] = {"axes": axes,
				"direction": contract.classify_move(pre_view, post_view)}
	# G-SEC display priority — its own pass, independent of `moved`
	# (MOVED_AXES is unchanged, so a priority-only change never enters it).
	prominence = _prominence(corpus_pre, corpus_post)

	# phase 5c — leave-one-out attribution and the hard gate (§3.4b/c)
	attribution = {}
	gate = []
	for tool_id in sorted(set(prominence) - set(moved)):
		tool_edit_ids = {eid for eid in order
			if (edits_by_id[eid].get("target") or {}).get("tool_id") == tool_id}
		attribution[tool_id] = _attribute(corpus_pre, tool_id,
			{contract.PRIORITY_AXIS: prominence[tool_id]},
			tool_edit_ids, edits_by_id, order, topics_cache)
	for tool_id, record in sorted(moved.items()):
		tool_edit_ids = {eid for eid in order
			if (edits_by_id[eid].get("target") or {}).get("tool_id") == tool_id}
		axes_to_attribute = {axis: change for axis, change in record["axes"].items()}
		if tool_id in prominence:
			axes_to_attribute[contract.PRIORITY_AXIS] = prominence[tool_id]
		attribution[tool_id] = _attribute(corpus_pre, tool_id, axes_to_attribute,
			tool_edit_ids, edits_by_id, order, topics_cache)
		pre_view = next(v for v in corpus_pre["tools"] if v.get("id") == tool_id)
		post_view = next(v for v in corpus_post["tools"] if v.get("id") == tool_id)
		entered_auto = (post_view.get("initial_review_bucket") == "security_auto"
			and pre_view.get("initial_review_bucket") != "security_auto")
		gained_accept = (post_view.get("initial_pre_accept")
			and not pre_view.get("initial_pre_accept"))
		if not entered_auto and not gained_accept:
			continue
		would_have = {"bucket": post_view.get("initial_review_bucket"),
			"pre_accept": bool(post_view.get("initial_pre_accept")),
			"priority": model.security_priority(post_view)}
		for axis in (["initial_review_bucket"] if entered_auto else []) \
				+ (["initial_pre_accept"] if gained_accept else []):
			axis_attr = attribution[tool_id].get(axis, {})
			causes = axis_attr.get("attributed_to") or []
			if not causes:
				critical.append(_finding("E-GATE-UNATTRIBUTED",
					"{} moved permissively on {} with no attributable cause".format(
						tool_id, axis), tool_id=tool_id))
				gate.append({"tool_id": tool_id, "code": "E-GATE-UNATTRIBUTED",
					"kind": "permissive", "would_have_been": would_have})
				continue
			for edit_id in causes:
				claim = edits_by_id[edit_id].get("bucket_claim") or {}
				if not claim.get("moves_bucket"):
					critical.append(_finding("E-GATE-UNDECLARED",
						"{} is attributed for {}'s permissive move but claims "
						"moves_bucket: false".format(edit_id, tool_id),
						edit_id=edit_id, tool_id=tool_id))
					gate.append({"tool_id": tool_id, "code": "E-GATE-UNDECLARED",
						"kind": "permissive", "would_have_been": would_have})
				body = ((edits_by_id[edit_id].get("reason") or {})
					.get("body") or "").lower()
				if not any(token in body for token in contract.GATE_CONSEQUENCE_TOKENS):
					critical.append(_finding("E-GATE-UNREASONED",
						"{}'s reason.body does not name the consequence of the "
						"permissive move it causes on {}".format(edit_id, tool_id),
						edit_id=edit_id, tool_id=tool_id))
					gate.append({"tool_id": tool_id, "code": "E-GATE-UNREASONED",
						"kind": "permissive", "would_have_been": would_have})
		# W-EDIT-CLAIM — the prediction vs the computed truth, non-gated
		for edit_id in sorted(tool_edit_ids):
			claim = edits_by_id[edit_id].get("bucket_claim")
			if not isinstance(claim, dict):
				continue
			bucket_causes = attribution[tool_id].get("initial_review_bucket", {}) \
				.get("attributed_to", [])
			if claim.get("moves_bucket") and edit_id not in bucket_causes \
					and "initial_review_bucket" in record["axes"]:
				notes.append(_finding("W-EDIT-CLAIM",
					"{} claimed the bucket move on {} but is not attributed for "
					"it".format(edit_id, tool_id), edit_id=edit_id, tool_id=tool_id))
	# The demotion gate (G-SEC, O3, §12 A-R3-2): ANY strict decrease in a
	# fix's display priority is consequential. It must be attributable, and
	# every attributed edit's reason.body must name the consequence. No new
	# code and no new submission field: E-GATE-UNATTRIBUTED /
	# E-GATE-UNREASONED with the gate record's `kind: "demotion"`.
	# E-GATE-UNDECLARED does not apply — `moves_bucket` is about the bucket,
	# and `move_evidence` may not carry a claim at all.
	for tool_id, change in sorted(prominence.items()):
		if not change["lost"]:
			continue
		post_view = next(v for v in corpus_post["tools"] if v.get("id") == tool_id)
		would_have = {"bucket": post_view.get("initial_review_bucket"),
			"pre_accept": bool(post_view.get("initial_pre_accept")),
			"priority": change["to"]}
		causes = attribution.get(tool_id, {}).get(contract.PRIORITY_AXIS, {}) \
			.get("attributed_to") or []
		if not causes:
			critical.append(_finding("E-GATE-UNATTRIBUTED",
				"{}'s security priority fell {} → {} with no attributable "
				"cause".format(tool_id, change["from"], change["to"] or "not G-SEC"),
				tool_id=tool_id))
			gate.append({"tool_id": tool_id, "code": "E-GATE-UNATTRIBUTED",
				"kind": "demotion", "would_have_been": would_have})
			continue
		for edit_id in causes:
			body = ((edits_by_id[edit_id].get("reason") or {}).get("body") or "")
			body = body.lower() if isinstance(body, str) else ""
			if not any(token in body for token in contract.PROMINENCE_CONSEQUENCE_TOKENS):
				critical.append(_finding("E-GATE-UNREASONED",
					"{} lowers {}'s security priority {} → {} and its reason.body "
					"does not name that consequence ({})".format(edit_id, tool_id,
						change["from"], change["to"] or "not G-SEC",
						"/".join(contract.PROMINENCE_CONSEQUENCE_TOKENS)),
					edit_id=edit_id, tool_id=tool_id))
				gate.append({"tool_id": tool_id, "code": "E-GATE-UNREASONED",
					"kind": "demotion", "would_have_been": would_have})

	# claims of movement on tools that did not move at all
	for edit_id in order:
		claim = edits_by_id[edit_id].get("bucket_claim")
		if isinstance(claim, dict) and claim.get("moves_bucket"):
			tool_id = (edits_by_id[edit_id].get("target") or {}).get("tool_id")
			if tool_id not in moved:
				notes.append(_finding("W-EDIT-CLAIM",
					"{} claimed a bucket move on {}, which did not move".format(
						edit_id, tool_id), edit_id=edit_id, tool_id=tool_id))

	# phase 5d — the checks' attestations
	tables = contract.build_tables(corpus_pre)
	attestations = _attestation_map(converge, critical)
	live_edits = [edits_by_id[eid] for eid in order]
	_verify_closure_and_ops(attestations, edits, critical)
	surviving = {sug.get("id") for view in corpus_post.get("tools") or []
		for sug in view.get("suggestions") or [] if isinstance(sug, dict)}
	if "C1-evidence" in attestations:
		verify_c1(attestations["C1-evidence"], corpus_pre, tables, critical)
	if "C2-tags-visibility" in attestations:
		verify_c2(attestations["C2-tags-visibility"], corpus_pre, tables, critical,
			edits)
	if "C3-security-only" in attestations:
		verify_c3(attestations["C3-security-only"], corpus_pre, tables, critical)
	if "C4-notable-security" in attestations:
		verify_c4(attestations["C4-notable-security"], corpus_pre, tables, critical)
	if "C5-auto-approval" in attestations:
		verify_c5(attestations["C5-auto-approval"], corpus_pre, tables, critical)
	if "C6-memory" in attestations:
		verify_c6(attestations["C6-memory"], corpus_pre, tables, critical,
			converge.get("ledger"))
	if "C7-collisions" in attestations:
		verify_c7(attestations["C7-collisions"], corpus_pre, tables, critical,
			surviving)

	# phase 5e — corpus_effect arithmetic (§3.5)
	computed_effect = compute_corpus_effect(corpus_pre, corpus_post,
		live_edits, moved)
	effect_findings = []
	_verify_effect(converge.get("corpus_effect") or {}, computed_effect,
		corpus_pre, corpus_post, effect_findings)
	for finding in effect_findings:
		(critical if finding["critical"] else notes).append(finding)

	# flags are findings for the human — surfaced as notes, never corpus writes
	for edit_id in order:
		edit = edits_by_id[edit_id]
		if edit["op"] == "flag":
			notes.append({"code": "FLAG", "critical": False,
				"edit_id": edit_id,
				"tool_id": (edit.get("target") or {}).get("tool_id"),
				"detail": (edit.get("reason") or {}).get("headline", "")})

	# Verifiers append into one list; severity is the FINDING'S, not the
	# list's. Partition here so a note-severity finding (W-STORE-UNCHECKED)
	# can never bounce a submission by mere membership.
	notes.extend(f for f in critical if not f.get("critical"))
	critical = [f for f in critical if f.get("critical")]

	return {"state": "converged", "critical": critical, "notes": notes,
		"rejected": rejected, "applied": list(order),
		"superseded": sorted(superseded), "corpus_post": corpus_post,
		"diff": diff, "moved": moved, "prominence": prominence,
		"attribution": attribution,
		"gate": gate, "computed_effect": computed_effect,
		"effect": None}


def _status(state, attempt, attempt_log, headline, body, degraded_tools,
		standing_rejects):
	return {
		"attempts": attempt,
		"state": state,
		"explanation": {"headline": headline, "body": body,
			"attempt_log": list(attempt_log or [])},
		"degraded_tools": degraded_tools,
		"standing_rejects": standing_rejects,
	}


def _degrade_unapplied(corpus_pre, converge, attempt, findings, attempt_log):
	"""§3.4d row 2: no submission survives — corpus.post IS corpus.pre. The
	report renders the pre-convergence corpus and says why."""
	corpus_post = copy.deepcopy(corpus_pre)
	rejects = [{"edit_id": f.get("edit_id"), "code": f["code"],
		"detail": f["detail"]} for f in findings]
	codes = sorted({f["code"] for f in findings})
	status = _status("degraded_unapplied", attempt, attempt_log,
		"Convergence did not converge in {} attempts; nothing was applied.".format(
			attempt),
		"Nothing in the final submission could be applied ({}). corpus.post.json "
		"is corpus.pre.json verbatim, the report renders the pre-convergence "
		"corpus, and every rejected edit is listed with its code.".format(
			", ".join(codes)),
		[], rejects)
	effect = {
		"run_id": corpus_pre.get("run_id"),
		"generated_at": corpus_pre.get("generated_at"),
		"attempt": attempt,
		"state": "degraded_unapplied",
		"applied": [], "rejected": rejects, "superseded": [],
		"diff": [], "moved": {}, "attribution": {}, "tools": {},
		"corpus_effect": compute_corpus_effect(corpus_pre, corpus_post, [], {}),
		"findings": findings,
		"convergence_status": status,
	}
	return {"state": "degraded_unapplied", "critical": findings, "notes": [],
		"rejected": rejects, "applied": [], "superseded": [],
		"corpus_post": corpus_post, "effect": effect}


def _finalize_terminal(corpus_pre, converge, result, edits_by_id, attempt,
		attempt_log, excluded):
	"""Attempt 5: whatever remains, ship something conservative and explained
	(§L4 / criterion 13). Gate-failing tools are FORCED to the conservative
	option — the one place in the design a bucket is written rather than
	derived, written by the applier, recorded as forced."""
	corpus_post = result["corpus_post"]
	if not result["applied"] and (result["critical"] or result["rejected"]):
		# Nothing survived AND something is wrong — §3.4d row 2. The two
		# populations are NOT the same set and both must be consulted: a
		# precheck rejection carries a critical finding, but an edit the
		# terminal loop excluded for E-APPLY-SCOPE/SCHEMA lives only in
		# `rejected` — its finding disappeared with the re-run that excluded
		# it. Keying off `critical` alone reported "converged" on a run
		# where nothing applied and everything was wrong. A run that
		# legitimately proposes no edits (all three collections empty) has
		# converged; see TerminalStateMatrixTests for the pinned matrix.
		seen = {(f.get("edit_id"), f.get("code")) for f in result["critical"]}
		submission = list(result["critical"]) + [
			r for r in result["rejected"]
			if (r.get("edit_id"), r.get("code")) not in seen]
		return _degrade_unapplied(corpus_pre, converge, attempt, submission,
			attempt_log)
	forced = {}
	for entry in result["gate"]:
		tool_id = entry["tool_id"]
		if tool_id in forced:
			continue
		view = next(v for v in corpus_post["tools"] if v.get("id") == tool_id)
		pre_view = next((v for v in corpus_pre["tools"] if v.get("id") == tool_id), {})
		forced[tool_id] = {
			"forced_bucket": contract.FORCED_BUCKET,
			"forced_pre_accept": False,
			"would_have_been": entry["would_have_been"],
			"code": entry["code"],
			"kind": entry.get("kind", "permissive"),
		}
		# §12 A-R3-3 — the forced-display snapshot: the PRE-convergence
		# priority, its reasons and their labels, so the page keeps the
		# prominence the gate could not see justified — even when the forced
		# view is no longer G-SEC (the sole fix deleted). Display only:
		# acceptance stays off (FORCED_BUCKET, forced_pre_accept False, and
		# the `forced-conservative` hold/bar), and `security_tier` stays the
		# PURE recomputation of the forced view — never overwritten by the
		# snapshot — so `valid_security_tier` and stored == security_tier(view)
		# both still hold. The page shows the snapshot's priority when present.
		pre_tier = pre_view.get("security_tier")
		if model.valid_security_tier(pre_tier):
			forced[tool_id]["forced_display"] = {
				"priority": pre_tier["priority"],
				"reasons": list(pre_tier["reasons"]),
				"labels": {code: model.TIER_LABELS[code]["text"]
					for code in pre_tier["reasons"]},
			}
		view["initial_review_bucket"] = contract.FORCED_BUCKET
		view["initial_pre_accept"] = False
		view["forced_conservative"] = forced[tool_id]
		view["security_tier"] = model.security_tier(view)
		view["pre_accept_bars"] = model.pre_accept_bars(view)
	state = "degraded_gate" if forced else "converged"
	standing = [r for r in result["rejected"]]
	standing_findings = [f for f in result["critical"]
		if f["code"] not in ("E-GATE-UNATTRIBUTED", "E-GATE-UNDECLARED",
			"E-GATE-UNREASONED") or f.get("tool_id") not in forced]
	degraded_tools = [dict(record, tool_id=tool_id)
		for tool_id, record in sorted(forced.items())]
	if state == "degraded_gate":
		headline = "{} tool(s) reached auto-update without surviving the gate " \
			"and were forced to {}.".format(len(forced), contract.FORCED_BUCKET)
		body = ("After {} attempts the gate still failed on: {}. Each is forced "
			"out of the auto strip into the review flow — the reviewer pays one "
			"card, which is the safe failure. The rest of the corpus keeps its "
			"convergence. Standing rejects and findings are listed "
			"below.").format(attempt, ", ".join(sorted(forced)))
	elif standing or standing_findings:
		headline = "Converged at attempt {} with {} standing reject(s).".format(
			attempt, len(standing))
		body = ("The applied submission ships; every rejected edit and every "
			"unresolved finding is listed here rather than silently dropped.")
	else:
		headline = "Converged at attempt {}.".format(attempt)
		body = "All edits applied; every check attested; the gate passed."
	status = _status(state, attempt, attempt_log, headline, body,
		degraded_tools, standing)
	# Recompute the shipped numbers AFTER forcing, so pre_accept/tools_moved
	# describe the corpus that ships.
	moved = {}
	for view in corpus_post.get("tools") or []:
		pre_view = next((v for v in corpus_pre["tools"]
			if v.get("id") == view.get("id")), None)
		if pre_view is None:
			continue
		axes = {}
		for axis in contract.MOVED_AXES:
			if pre_view.get(axis) != view.get(axis):
				axes[axis] = {"from": pre_view.get(axis), "to": view.get(axis)}
		if axes:
			moved[view["id"]] = {"axes": axes,
				"direction": contract.classify_move(pre_view, view)}
	result = dict(result, prominence=_prominence(corpus_pre, corpus_post))
	effect = _build_effect(corpus_pre, corpus_post, converge, result, edits_by_id,
		attempt, state, moved, forced, status)
	return {"state": state, "critical": standing_findings, "notes": result["notes"],
		"rejected": standing, "applied": result["applied"],
		"superseded": result["superseded"], "corpus_post": corpus_post,
		"effect": effect}


def _build_effect(corpus_pre, corpus_post, converge, result, edits_by_id,
		attempt, state, moved, forced, status):
	applied_edits = [edits_by_id[eid] for eid in result["applied"]]
	# The applier's numbers are the ones that ship (§3.5); the narrative is
	# the agent's and rides beside them — it was verified in phase 5e.
	shipped_effect = compute_corpus_effect(corpus_pre, corpus_post,
		applied_edits, moved)
	declared = converge.get("corpus_effect")
	shipped_effect["narrative"] = declared.get("narrative") \
		if isinstance(declared, dict) else None
	return {
		"run_id": corpus_pre.get("run_id"),
		"generated_at": corpus_pre.get("generated_at"),
		"attempt": attempt,
		"state": state,
		"applied": result["applied"],
		"superseded": result["superseded"],
		"rejected": result["rejected"],
		"diff": result["diff"],
		"moved": moved,
		"attribution": result["attribution"],
		"tools": _build_tool_blocks(corpus_pre, corpus_post, result["applied"],
			edits_by_id, moved, result["attribution"], forced,
			result.get("prominence") or {}),
		"corpus_effect": shipped_effect,
		"findings": result["notes"] + [f for f in result["critical"]],
		"convergence_status": status,
	}


def finalize_clean(corpus_pre, converge, result, attempt, attempt_log):
	"""A clean (or note-only) submission before attempt 5 — the loop's happy
	exit. Same effect shape as the terminal path, state `converged`."""
	edits = {e["edit_id"]: e for e in converge["edits"]
		if isinstance(e, dict) and isinstance(e.get("edit_id"), str)}
	status = _status("converged", attempt, attempt_log,
		"Converged at attempt {}.".format(attempt),
		"All edits applied; every check attested; the gate passed.",
		[], [])
	return _build_effect(corpus_pre, result["corpus_post"], converge, result,
		edits, attempt, "converged", result["moved"], {}, status)


# ── the CLI ─────────────────────────────────────────────────────────────────
def _read_json(path):
	with open(path, "r", encoding="utf-8") as fh:
		return json.load(fh)


def _write_json(path, document):
	with open(path, "w", encoding="utf-8") as fh:
		json.dump(document, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")


class BrokenAttemptsFile(Exception):
	"""converge-attempts.json exists but cannot be read as the counter. It
	is the loop's own guarantee — silently resetting it to zero would hand
	a corrupted file five fresh attempts."""


def _load_attempts(session_dir):
	path = os.path.join(session_dir, "converge-attempts.json")
	if not os.path.exists(path):
		return {"attempts": []}
	try:
		log = _read_json(path)
	except Exception as exc:
		raise BrokenAttemptsFile("{} could not be read ({}: {}) — the attempt "
			"counter is the loop's enforcement; resolve the file by hand "
			"rather than resetting it".format(path, type(exc).__name__, exc))
	if not (isinstance(log, dict) and isinstance(log.get("attempts"), list)):
		raise BrokenAttemptsFile("{} is not an object with an attempts[] list "
			"— the attempt counter is the loop's enforcement; resolve the "
			"file by hand rather than resetting it".format(path))
	return log


def _attempt_log_entries(log):
	return [{"attempt": entry.get("attempt"), "codes": entry.get("codes"),
		"state": entry.get("state")} for entry in log["attempts"]]


def _load_store(path):
	"""One memory-store snapshot, in the three states the corpus keeps
	apart: a readable object (present), no file (absent, None), or a file
	that exists but cannot be read as a store — the sentinel, so C6's note
	can send the operator to fix the copied file rather than to re-copy it.
	Mirrors `validate_items._load_watch_snapshot`'s shape (which raises
	E-RESEARCH-UNREADABLE into the validation findings before returning
	None); this is the store-status half of that same distinction."""
	if not os.path.exists(path):
		return None
	try:
		snapshot = _read_json(path)
	except Exception as exc:  # same width as _load_watch_snapshot, same reasons
		return {contract.STORE_UNREADABLE_KEY: "{}: {}".format(
			type(exc).__name__, exc)}
	if not isinstance(snapshot, dict):
		return {contract.STORE_UNREADABLE_KEY:
			"the file is {}, not an object keyed by tool id".format(
				type(snapshot).__name__)}
	return snapshot


def _prepare(session_dir, args):
	pre_path = os.path.join(session_dir, "corpus.pre.json")
	if os.path.exists(pre_path) and not args.force:
		print("Error: {} already exists — corpus.pre.json is immutable for the "
			"run. Pass --force only to rebuild a session you are re-validating "
			"from scratch.".format(pre_path), file=sys.stderr)
		return 4
	macos_setup_root = os.path.abspath(os.path.expanduser(args.macos_setup_root))
	dotfiles_root = os.path.abspath(os.path.expanduser(
		args.dotfiles_root or os.path.join(macos_setup_root, "dotfiles")))
	systems_root = os.path.abspath(os.path.expanduser(args.systems_root))
	try:
		validation = validate_items.validate_session(
			session_dir, [macos_setup_root, dotfiles_root, systems_root],
			manifest_root=macos_setup_root)
	except validate_items.NoCandidateSet as exc:
		print("Error: {}".format(exc), file=sys.stderr)
		return 4
	collect = _read_json(os.path.join(session_dir, "collect.json"))
	stores = {
		"watch_items": _load_store(os.path.join(session_dir, "watch-items.json")),
		"method_notes": _load_store(os.path.join(session_dir, "method-notes.json")),
	}
	corpus_pre = contract.build_corpus_pre(validation, collect, stores)
	_write_json(pre_path, corpus_pre)
	_write_json(os.path.join(session_dir, "converge-view.json"),
		contract.build_view(corpus_pre))
	_write_json(os.path.join(session_dir, "converge-tables.json"),
		contract.build_tables(corpus_pre))
	print(pre_path)
	return 0


def _findings_out(result):
	return {
		"state": result["state"],
		"critical": result["critical"],
		"notes": result["notes"],
		"rejected": result["rejected"],
		"applied": result["applied"],
	}


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--session", required=True)
	mode = parser.add_mutually_exclusive_group(required=True)
	mode.add_argument("--prepare", action="store_true")
	mode.add_argument("--check", metavar="CONVERGE_JSON")
	mode.add_argument("--submit", metavar="CONVERGE_JSON")
	parser.add_argument("--force", action="store_true",
		help="prepare only: rebuild an existing corpus.pre.json")
	parser.add_argument("--macos-setup-root", default=".")
	parser.add_argument("--dotfiles-root", default=None)
	parser.add_argument("--systems-root",
		default="~/project/github/tapppi/systems")
	args = parser.parse_args(argv)
	session_dir = os.path.abspath(os.path.expanduser(args.session))

	if args.prepare:
		return _prepare(session_dir, args)

	pre_path = os.path.join(session_dir, "corpus.pre.json")
	if not os.path.exists(pre_path):
		print("Error: {} does not exist — run --prepare first.".format(pre_path),
			file=sys.stderr)
		return 4
	corpus_pre = _read_json(pre_path)
	# A stale corpus is an operator condition, not the agent's error: refuse
	# BEFORE the attempt counter is read, so it never consumes one of the
	# five (G-SEC §4.7).
	try:
		contract.check_corpus_versions(corpus_pre)
	except contract.CorpusVersionError as exc:
		print("Error: {}".format(exc), file=sys.stderr)
		return 4
	draft_path = args.check or args.submit
	try:
		converge = _read_json(draft_path)
	except Exception as exc:
		print(json.dumps({"state": "rejected", "critical": [_finding(
			"E-SUBMIT-SHAPE", "{} could not be read as JSON: {}: {}".format(
				draft_path, type(exc).__name__, exc))]}, indent=1))
		return 1

	try:
		log = _load_attempts(session_dir)
	except BrokenAttemptsFile as exc:
		print("Error: {}".format(exc), file=sys.stderr)
		return 4
	attempt = len(log["attempts"]) + 1

	if args.check:
		result = apply_converge(corpus_pre, converge, attempt=attempt,
			terminal=False)
		print(json.dumps(_findings_out(result), ensure_ascii=False, indent=1))
		return 0 if not result["critical"] else 1

	# --submit
	effect_path = os.path.join(session_dir, "converge-effect.json")
	if os.path.exists(effect_path):
		print("Error: {} already exists — this run's convergence is terminal. "
			"The artefacts stand; nothing is resubmittable.".format(effect_path),
			file=sys.stderr)
		return 4
	if attempt > contract.MAX_ATTEMPTS:
		print("Error: {} attempts are already recorded and no terminal artefact "
			"exists — the attempts file is inconsistent; resolve by hand.".format(
				len(log["attempts"])), file=sys.stderr)
		return 4
	notes = []
	if isinstance(converge, dict) and converge.get("attempt") != attempt:
		notes.append(_finding("W-SUBMIT-ATTEMPT",
			"submission declares attempt {!r}; the durable counter says {} — "
			"the counter governs".format(converge.get("attempt"), attempt)))
	terminal = attempt >= contract.MAX_ATTEMPTS
	result = apply_converge(corpus_pre, converge, attempt=attempt,
		terminal=terminal, attempt_log=_attempt_log_entries(log))
	codes = {}
	for finding in result["critical"]:
		codes[finding["code"]] = codes.get(finding["code"], 0) + 1
	log["attempts"].append({
		"attempt": attempt,
		"submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"codes": dict(sorted(codes.items())),
		"state": result["state"],
	})
	_write_json(os.path.join(session_dir, "converge-attempts.json"), log)

	if result["state"] == "rejected":
		print(json.dumps(_findings_out(result), ensure_ascii=False, indent=1))
		return 1

	effect = result["effect"]
	if effect is None:
		effect = finalize_clean(corpus_pre, converge, result, attempt,
			_attempt_log_entries(log))
	# The log now includes the attempt that just terminated; the explanation
	# carries the complete history on every path.
	effect["convergence_status"]["explanation"]["attempt_log"] = \
		_attempt_log_entries(log)
	effect["findings"] = effect.get("findings", []) + notes
	_write_json(os.path.join(session_dir, "corpus.post.json"),
		result["corpus_post"])
	_write_json(effect_path, effect)
	_write_json(os.path.join(session_dir, "converge.json"), converge)
	print(json.dumps({"state": result["state"],
		"applied": len(result["applied"]),
		"rejected": len(result["rejected"]),
		"attempt": attempt,
		"convergence_status": effect["convergence_status"]["explanation"]["headline"],
	}, ensure_ascii=False, indent=1))
	return 0


if __name__ == "__main__":
	sys.exit(main())
