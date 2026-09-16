#!/usr/bin/env python3
"""
validate_items.py — the deterministic validator (REDESIGN.md §C3).

Usage: validate_items.py <session_dir> [--macos-setup-root PATH]
                                       [--dotfiles-root PATH] [--systems-root PATH]

Reads {session_dir}/collect.json and every {session_dir}/research/*.json, and
writes {session_dir}/validation.json — the machine-readable primary, and the
pre-convergence artifact convergence (C4) consumes. A human-readable tail of
the same data, one finding per line and code-prefixed, goes to
{session_dir}/assemble.warn — the spec-conformance channel for the run and
nothing else (references/item-schema.md §3.3).

In a normal run `assemble.py` calls `validate_session()` directly and writes
both files itself; this CLI exists so the validator can be run, and its output
diffed, on its own.

Six stages:

	V1   load, per file and per entry
	V2   spec validation      — required fields, types, closed vocabularies
	V3   shape normalization  — the normalizations §5.3 licenses, and no others
	V3b  id assignment and uniqueness, from the checker's declared anchor
	V4   the twenty invariants
	V5   impact
	V6   initial bucketing

THE CONSTRAINT, above every other consideration in this file
(`REDESIGN.md` §A, §C3, acceptance criterion 1):

	The deterministic layer validates, normalizes, counts, buckets and
	calculates impact. It NEVER deletes, trims or re-rates an item on a regex
	or heuristic rule. Judgement is reserved for convergence.

Every stage below reports and changes nothing. The one class of mutation is
shape normalization, each instance of which is licensed by name in
`references/item-schema.md` §5.3 — an evidence shorthand string becoming its
object form, a null array becoming `[]`. A wrong-typed array member is
*quarantined on the tool*, not dropped, because dropping is deletion and a
human would have read it. `initial_review_bucket` is a baseline for
convergence to review, not a decision.

The defect this exists to make impossible is measured, not theorised: a
rule-driven trim silently moved `brew:libpq` into `security_auto`,
pre-accepted, with 10 CVEs, and nothing on the page said a rule had put it
there (`HANDOFF.md` §3).

Second constraint, criterion 4: degradation is per tool and loud. No malformed
input aborts the run. Each stage runs inside a per-tool boundary, and the
loading stage inside a per-file and per-entry one.
"""
from __future__ import annotations  # Python 3.9 — same constraint as assemble.py

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import assemble
import items as model

# What we reuse from assemble.py, and why each one rather than a second copy:
#   NON_VERSION_SOURCES / finding_expected — one spelling of "this has no
#     version", so a fourth finding source cannot land in half the branches.
#   suggestion_kind — `kind` is omittable and defaults to "edit"; five call
#     sites in assemble already share this default and a sixth must not drift.
#   compute_version_delta — the delta feeds bucketing here and rendering there;
#     two answers to "how big is this bump" in one report is the bug.
#   upgrade_command_and_runnable — `runnable` gates `security_auto`. The
#     validator runs before assembly synthesizes the baseline suggestion, so it
#     asks the same function rather than guessing from `source`.
#   read_candidate_list / read_findings_block — the candidate set is
#     collection's contract, already read defensively there.
#   config_needs_attention, cve_sort_key — one-liners whose duplication would
#     be one more place to skip a `None` guard.
NON_VERSION_SOURCES = assemble.NON_VERSION_SOURCES

# The one shape nothing can be salvaged from: collect.json IS the candidate set.
# Raised rather than exited on, so an importing test can assert the message
# without catching SystemExit — and so a genuine OSError from anywhere else in
# the run is never mistaken for it (which `EnvironmentError` would do, being an
# alias for OSError).
class NoCandidateSet(Exception):
	pass


# config_status's default, spelled here because the validator's version carries
# `citations` — the §3.2 split — which assembly's does not. A factory rather
# than a module-level dict: `dict(CONSTANT)` is a shallow copy, so every tool
# that fell back to it would share one `evidence` list.
def default_config_status():
	return {"state": "unknown", "detail": "", "evidence": [], "citations": []}


# ── findings ────────────────────────────────────────────────────────────────
_MAX_VALUE_CHARS = 300


def _short(value) -> str:
	"""A finding's `value` is JSON, so it has to be a string, and it goes in a
	file a human reads — so it is bounded. Truncating the *report of* an
	offending value is not trimming an item: the item keeps every byte."""
	if value is None:
		return ""
	text = value if isinstance(value, str) else repr(value)
	if len(text) > _MAX_VALUE_CHARS:
		return text[:_MAX_VALUE_CHARS] + "…"
	return text


class Findings:
	"""An ordered accumulator. Sorting happens once at emit time, under
	`items.finding_sort_key`, so two runs over one corpus produce a
	byte-identical file."""

	def __init__(self):
		self.entries = []

	def add(self, code, message, tool_id=None, item_id=None, field=None, value=None):
		self.entries.append({
			"code": code,
			"severity": model.finding_severity(code),
			"tool_id": tool_id,
			"item_id": item_id,
			"field": field,
			"value": _short(value),
			"message": message,
		})

	def codes_for(self, tool_id):
		return sorted({e["code"] for e in self.entries if e["tool_id"] == tool_id})

	def sorted(self):
		return sorted(self.entries, key=model.finding_sort_key)


# ── V1: load, per file and per entry ────────────────────────────────────────
def v1_load(research_dir: str, findings: Findings):
	"""→ (by_id, orphans). Every failure here costs one file or one entry.

	One bare string in one research array used to raise AttributeError out of
	`load_research` and abort the whole 78-tool run after the expensive part of
	the session was already spent."""
	by_id = {}
	orphans = []
	if not os.path.isdir(research_dir):
		findings.add("E-RESEARCH-UNREADABLE",
			"no research/ directory — every tool will show research_error",
			field=research_dir)
		return by_id, orphans
	try:
		names = sorted(os.listdir(research_dir))
	except OSError as exc:
		findings.add("E-RESEARCH-UNREADABLE",
			"could not list research/: {}: {}".format(type(exc).__name__, exc),
			field=research_dir)
		return by_id, orphans
	for fname in names:
		if not fname.endswith(".json"):
			continue
		fpath = os.path.join(research_dir, fname)
		try:
			with open(fpath, "r", encoding="utf-8") as fh:
				entries = json.load(fh)
		except Exception as exc:
			# Deliberately wider than (OSError, json.JSONDecodeError): a
			# subagent killed mid-write leaves a truncated multi-byte character
			# (UnicodeDecodeError) and a pathologically nested array raises
			# RecursionError — neither is an OSError or a ValueError.
			findings.add("E-RESEARCH-UNREADABLE",
				"{}: {}".format(type(exc).__name__, exc), field=fname)
			continue
		if not isinstance(entries, list):
			findings.add("E-RESEARCH-NOTARRAY",
				"expected a JSON array of tool objects, got {}".format(type(entries).__name__),
				field=fname)
			continue
		for entry in entries:
			if not isinstance(entry, dict):
				findings.add("E-ENTRY-NOTOBJECT",
					"an entry is {}, not an object".format(type(entry).__name__),
					field=fname, value=entry)
				orphans.append({"file": fname, "reason": "not an object", "value": _short(entry)})
				continue
			tid = entry.get("id")
			if not tid or not isinstance(tid, str):
				findings.add("E-ENTRY-NOID", "an entry has no usable string \"id\"",
					field=fname, value=tid)
				orphans.append({"file": fname, "reason": "no usable id", "value": _short(tid)})
				continue
			if tid in by_id:
				findings.add("W-ENTRY-DUPLICATE",
					"a later file overwrites an earlier entry for this tool",
					tool_id=tid, field=fname)
			by_id[tid] = entry
	return by_id, orphans


# ── V3: shape normalization ─────────────────────────────────────────────────
def as_list(value, findings: Findings, tool_id, field, item_id=None):
	"""`null` → `[]` silently: an omitted array and a null array mean the same
	thing. Any other non-list → `[]` with a warning naming the actual type."""
	if value is None:
		return []
	if not isinstance(value, list):
		findings.add("W-SHAPE-COERCED",
			"{} was {}, not an array — read as empty".format(field, type(value).__name__),
			tool_id=tool_id, item_id=item_id, field=field, value=value)
		return []
	return list(value)


def split_members(value, findings: Findings, tool_id, field, item_id=None):
	"""→ (good dict members, quarantined members).

	A wrong-typed member is KEPT, on the tool, under `quarantine`. Today's
	`as_item_list` drops it; under §C3 nothing deterministic deletes content a
	human would have seen. The quarantine is the difference between "we could
	not read this" and "this never existed"."""
	good, bad = [], []
	for member in as_list(value, findings, tool_id, field, item_id):
		if isinstance(member, dict):
			good.append(member)
		else:
			findings.add("W-MEMBER-QUARANTINED",
				"a {} entry is {}, not an object — quarantined, not dropped".format(
					field, type(member).__name__),
				tool_id=tool_id, item_id=item_id, field=field, value=member)
			# The member itself, not `_short()` of it: the quarantine exists so
			# nothing a human would have read is lost, and a truncated repr
			# loses both the tail and the structure. It came from JSON, so it
			# is JSON-serializable by construction.
			bad.append({"field": field, "item_id": item_id, "value": member})
	return good, bad


_LINES_INT = (int,)


def normalize_evidence(raw, findings: Findings, tool_id, item_id, field):
	"""Evidence is paths only, as objects (§3.1). A bare string is normalized
	to the object form iff it is a path with an optional line locator.

	A non-conforming string raises E-EVID-MALFORMED and is KEPT VERBATIM in the
	list. It is deliberately not moved to `citations[]`: auto-moving is a regex
	deciding what a field means — the class §C3 forbids — and it would paper
	over a broken path that happens to read like prose."""
	out = []
	for entry in as_list(raw, findings, tool_id, field, item_id):
		if isinstance(entry, dict):
			path = entry.get("path")
			if not isinstance(path, str) or not path:
				findings.add("E-EVID-MALFORMED", "an evidence object has no string `path`",
					tool_id=tool_id, item_id=item_id, field=field, value=entry)
				out.append(entry)
				continue
			# A copy, not a rebuild: a rebuilt object silently drops every key
			# the schema did not anticipate, and a checker's extra field is
			# content a human would have read.
			normalized = dict(entry)
			lines = entry.get("lines")
			if lines == []:
				del normalized["lines"]  # `[]` and absent mean the same thing
			elif lines is not None:
				if not isinstance(lines, list):
					findings.add("E-FIELD-TYPE",
						"evidence `lines` is {}, not an array".format(type(lines).__name__),
						tool_id=tool_id, item_id=item_id, field=field + ".lines", value=lines)
				else:
					kept, bad = [], 0
					for loc in lines:
						if isinstance(loc, bool):
							pass  # bool is an int subclass; a line number it is not
						elif isinstance(loc, _LINES_INT):
							kept.append(loc)
							continue
						elif (isinstance(loc, list) and len(loc) == 2
								and all(isinstance(x, _LINES_INT) and not isinstance(x, bool) for x in loc)):
							kept.append([loc[0], loc[1]])
							continue
						bad += 1
						findings.add("E-FIELD-TYPE",
							"an evidence line locator is neither an int nor a two-element [start, end]",
							tool_id=tool_id, item_id=item_id, field=field + ".lines", value=loc)
					# The normalization is licensed only when it succeeds. A
					# partly-unreadable locator list is reported and left
					# exactly as written rather than half-rewritten.
					if not bad:
						normalized["lines"] = kept
			out.append(normalized)
			continue
		parsed = model.parse_evidence_shorthand(entry)
		if parsed is None:
			findings.add("E-EVID-MALFORMED",
				"not a path — evidence holds paths only; prose belongs in citations[]",
				tool_id=tool_id, item_id=item_id, field=field, value=entry)
			out.append(entry)  # kept verbatim: nothing a human would read is lost
		else:
			out.append(parsed)
	return out


def validate_citations(raw, findings: Findings, tool_id, item_id, field):
	"""Enum, non-empty text, URL syntax. Never fetched, never resolved against
	the filesystem. That is the whole point: a citation is not checkable, so it
	is not checked, so it cannot warn — which is how 272 of 275 warning lines
	stopped being the reason nobody read the file."""
	good, bad = split_members(raw, findings, tool_id, field, item_id)
	for citation in good:
		if citation.get("kind") not in model.CITATION_KINDS:
			findings.add("E-CITE-KIND",
				"kind must be one of: {}".format(", ".join(model.CITATION_KINDS)),
				tool_id=tool_id, item_id=item_id, field=field + ".kind",
				value=citation.get("kind"))
		text = citation.get("text")
		if not isinstance(text, str) or not text.strip():
			findings.add("E-CITE-EMPTY", "a citation carries no text",
				tool_id=tool_id, item_id=item_id, field=field + ".text", value=text)
		url = citation.get("url")
		if url is not None and not model.url_is_absolute_http(url):
			findings.add("E-CITE-URL", "not an absolute http(s) URL",
				tool_id=tool_id, item_id=item_id, field=field + ".url", value=url)
	return good, bad


# ── evidence path resolution (§3.1) ─────────────────────────────────────────
class RootResolver:
	"""Resolves an evidence path against the configured roots, then against
	sibling repos that are not configured.

	`W-EVID-ROOT` exists because 7 of the recorded run's 9 bare-path failures
	were `tieto/…` — a real repo the checker legitimately read, that is simply
	not in the root list. That is a *configuration* finding about the run, not a
	checker bug, and conflating the two is how the one genuinely wrong path
	(`mise:uv` citing `dotfiles/config/bash/.path`, which lives at the
	macos-setup root) stayed buried among 272 warnings.

	Discovery is one `listdir` of the macos-setup root's parent, filtered to
	directories containing `.git`. No network, no guessing, and it is done once.

	(`item-schema.md` open question 4 asks whether discovery or an explicit
	list is right. Both are here: `unconfigured_roots` takes an explicit list
	and skips discovery entirely, which is what the published fixture uses so
	the golden output does not depend on what happens to sit beside the repo on
	one machine. Discovery is the default because it is what the spec
	specifies, and it is what the real run wants — the point of W-EVID-ROOT is
	to notice a repo nobody configured.)"""

	def __init__(self, roots, unconfigured_roots=None, discover_from=None):
		self.roots = [r for r in roots if r]
		self._siblings = list(unconfigured_roots) if unconfigured_roots is not None else None
		self._discover_from = discover_from or (self.roots[0] if self.roots else None)

	def _sibling_roots(self):
		if self._siblings is not None:
			return self._siblings
		self._siblings = []
		base = self._discover_from
		if not base:
			return self._siblings
		parent = os.path.dirname(os.path.normpath(base))
		try:
			names = sorted(os.listdir(parent))
		except OSError:
			return self._siblings
		for name in names:
			path = os.path.join(parent, name)
			if path in self.roots or not os.path.isdir(path):
				continue
			if os.path.exists(os.path.join(path, ".git")):
				self._siblings.append(path)
		return self._siblings

	@staticmethod
	def _under(root, candidate) -> bool:
		if os.path.exists(os.path.join(root, candidate)):
			return True
		# A citation is sometimes prefixed with its own repo's directory name
		# ("systems/flake.nix"). Retry with that segment stripped — the same
		# convention assemble.evidence_exists() already honours.
		prefix = os.path.basename(os.path.normpath(root)) + "/"
		if candidate.startswith(prefix):
			return os.path.exists(os.path.join(root, candidate[len(prefix):]))
		return False

	def resolve(self, path: str):
		"""→ (outcome, detail) where outcome is "ok" | "unconfigured" | "missing"."""
		candidate = os.path.expanduser(path)
		if os.path.isabs(candidate):
			return ("ok", None) if os.path.exists(candidate) else ("missing", None)
		for root in self.roots:
			if self._under(root, candidate):
				return ("ok", None)
		for root in self._sibling_roots():
			if self._under(root, candidate):
				return ("unconfigured", os.path.basename(os.path.normpath(root)))
		return ("missing", None)


# ── the manifest, for I-16's structural preconditions ───────────────────────
_MANIFEST_ENTRY = re.compile(r'^\s*(brew|cask|tap|mas)\s+"([^"]+)"')
_SECTION_RULE = re.compile(r"^##\s*=+\s*$")
_SECTION_TITLE = re.compile(r"^##\s+(\S.*?)\s*$")
# `setup.sh` dispatches on `[[ "${1}" = "token" ]]`. `${1:-}` and a bare `$1`
# are accepted too, so a stylistic change in the script cannot quietly turn
# `task_add`'s "not already dispatched" precondition into "always passes".
_DISPATCH_TOKEN = re.compile(
	r'\[\[\s*"(?:\$\{1(?::-[^}]*)?\}|\$1)"\s*=\s*"([A-Za-z0-9_-]+)"\s*\]\]')


class Manifest:
	"""The Brewfile as data: which entries exist, in which section, plus the
	`setup.sh` subcommands already dispatched.

	`REDESIGN.md` §B1 puts `intel.Brewfile` out of this tool entirely, so this
	only ever reads `Brewfile`. I-17 makes citing the other one an error rather
	than a thing to quietly resolve.

	`readable` is False when the file is not there — a precondition that cannot
	be checked is reported as unchecked (W-STRUCT-UNCHECKED), never as passing.
	A silent skip is how a structural fix covering the wrong subject set stays
	invisible."""

	def __init__(self, root: str):
		self.root = root
		self.readable = False
		self.entries = {}      # (type, name) -> section
		self.sections = set()  # normalized section/subsection titles
		self.tasks = set()
		self.tasks_readable = False
		self._load_brewfile()
		self._load_setup()

	def _load_brewfile(self):
		path = os.path.join(self.root, "Brewfile")
		try:
			with open(path, "r", encoding="utf-8") as fh:
				lines = fh.read().splitlines()
		except (OSError, UnicodeDecodeError):
			# UnicodeDecodeError is the one that was missing: one non-UTF-8
			# byte in the real Brewfile (an em dash truncated mid-character)
			# aborted the whole run with a bare traceback before a single tool
			# was built — no report.json, no validation.json, no assemble.warn.
			# The designed degradation (`readable=False` → W-STRUCT-UNCHECKED)
			# existed and was unreachable behind the OSError-only handler.
			return
		self.readable = True
		# A top-level header is three lines — rule, title, rule — so the
		# CLOSING rule must not arm `previous_was_rule` again. Without the
		# `expect_close` state the first `## comment` after a header block gets
		# promoted to a section, and `manifest_move`'s "already in that section"
		# check then compares against the wrong name. The real Brewfile happens
		# to survive because a blank line follows each header; that is luck.
		section = None
		previous_was_rule = False
		expect_close = False
		for line in lines:
			if _SECTION_RULE.match(line):
				if expect_close:
					expect_close = False
					previous_was_rule = False
				else:
					previous_was_rule = True
				continue
			title = _SECTION_TITLE.match(line)
			if title:
				name = title.group(1)
				self.sections.add(_norm_section(name))
				if previous_was_rule:
					section = name
					expect_close = True
				previous_was_rule = False
				continue
			previous_was_rule = False
			expect_close = False
			entry = _MANIFEST_ENTRY.match(line)
			if entry:
				self.entries[(entry.group(1), entry.group(2))] = section

	def _load_setup(self):
		path = os.path.join(self.root, "setup.sh")
		try:
			with open(path, "r", encoding="utf-8") as fh:
				text = fh.read()
		except (OSError, UnicodeDecodeError):
			# Same width as _load_brewfile, same reason: an undecodable
			# setup.sh is "cannot check", never "abort the run".
			return
		self.tasks_readable = True
		self.tasks = set(_DISPATCH_TOKEN.findall(text))

	@staticmethod
	def _keyword(ref_type):
		"""`Ref.type` and the Brewfile's own keywords agree except that a
		formula line is spelled `brew`. Translated in one place, so a lookup
		cannot silently miss and read as "not in the manifest"."""
		return "brew" if ref_type == "formula" else ref_type

	def has(self, ref_type, name) -> bool:
		return (self._keyword(ref_type), name) in self.entries

	def section_of(self, ref_type, name):
		return self.entries.get((self._keyword(ref_type), name))

	def has_section(self, name) -> bool:
		return _norm_section(name) in self.sections


def _norm_section(name) -> str:
	return re.sub(r"\s+", " ", str(name)).strip().casefold()


# ── V2 + V4: one item ───────────────────────────────────────────────────────
# I-5's grammar is I-9's `cve` anchor grammar. Read from the model rather than
# recompiled here: two copies of one pattern, in exactly the pair of files this
# contract exists to keep in sync, is how they come to disagree.
_CVE_ID = model.ANCHOR_PATTERNS["cve"]


def _require_string(value, findings, tool_id, item_id, field, allow_empty=False):
	if value is None:
		findings.add("E-FIELD-MISSING", "{} is required".format(field),
			tool_id=tool_id, item_id=item_id, field=field)
		return False
	if not isinstance(value, str):
		findings.add("E-FIELD-TYPE", "{} is {}, not a string".format(field, type(value).__name__),
			tool_id=tool_id, item_id=item_id, field=field, value=value)
		return False
	if not allow_empty and not value.strip():
		findings.add("E-FIELD-MISSING", "{} is empty".format(field),
			tool_id=tool_id, item_id=item_id, field=field)
		return False
	return True


def validate_item(item, tool_id, item_id, link_count, findings: Findings,
		resolver: RootResolver, watch_topics=None):
	"""V2 spec validation, V3 normalization and V4's per-item invariants, on
	one item. → (normalized item, quarantined members). The input is never
	mutated, and no field is ever removed from the output.

	Every branch reports. None removes the item, shortens a field or changes a
	severity."""
	out = dict(item)
	out["id"] = item_id

	# ── anchor (I-9) ───────────────────────────────────────────────────
	anchor = item.get("anchor")
	if anchor is None:
		findings.add("E-FIELD-MISSING", "anchor is required — the id is derived from it",
			tool_id=tool_id, item_id=item_id, field="anchor")
	elif not isinstance(anchor, dict):
		findings.add("E-FIELD-TYPE", "anchor is {}, not an object".format(type(anchor).__name__),
			tool_id=tool_id, item_id=item_id, field="anchor", value=anchor)
	else:
		if anchor.get("kind") not in model.ANCHOR_KINDS:
			findings.add("E-ENUM-INVALID",
				"anchor.kind must be one of: {}".format(", ".join(model.ANCHOR_KINDS)),
				tool_id=tool_id, item_id=item_id, field="anchor.kind", value=anchor.get("kind"))
		elif not model.anchor_is_wellformed(anchor):
			if anchor.get("kind") == "none":
				findings.add("E-ANCHOR-MALFORMED",
					"anchor.kind \"none\" requires a non-empty `slug` and a null `value`",
					tool_id=tool_id, item_id=item_id, field="anchor", value=anchor)
			else:
				findings.add("E-ANCHOR-MALFORMED",
					"anchor.value does not match the grammar for kind \"{}\": {}".format(
						anchor.get("kind"), model.ANCHOR_PATTERNS[anchor["kind"]].pattern),
					tool_id=tool_id, item_id=item_id, field="anchor.value",
					value=anchor.get("value"))
	out["id_stability"] = model.id_stability(anchor)

	# ── title / body — the §L2 split ───────────────────────────────────
	title = item.get("title")
	if _require_string(title, findings, tool_id, item_id, "title"):
		if len(title) > model.TITLE_MAX_CHARS:
			findings.add("W-TITLE-LONG",
				"title is {} chars (bar is {}); a title is readable at a glance and the "
				"rest belongs in `body`".format(len(title), model.TITLE_MAX_CHARS),
				tool_id=tool_id, item_id=item_id, field="title", value=title)
		elif "\n" in title:
			findings.add("W-TITLE-LONG", "title contains a newline; it is one line",
				tool_id=tool_id, item_id=item_id, field="title", value=title)
	body = item.get("body")
	if body is not None and not isinstance(body, str):
		findings.add("E-FIELD-TYPE", "body is {}, not a string".format(type(body).__name__),
			tool_id=tool_id, item_id=item_id, field="body", value=body)

	# ── tags (I-11) ────────────────────────────────────────────────────
	raw_tags = item.get("tags")
	if raw_tags is None:
		findings.add("E-FIELD-MISSING", "tags is required, with at least one member",
			tool_id=tool_id, item_id=item_id, field="tags")
		tags = []
	elif not isinstance(raw_tags, list):
		findings.add("E-FIELD-TYPE", "tags is {}, not an array".format(type(raw_tags).__name__),
			tool_id=tool_id, item_id=item_id, field="tags", value=raw_tags)
		tags = []
	else:
		tags = raw_tags
		if not tags:
			findings.add("E-FIELD-MISSING", "tags is empty; an item carries at least one tag",
				tool_id=tool_id, item_id=item_id, field="tags")
	recognized = 0
	for tag in tags:
		if not isinstance(tag, str):
			findings.add("E-FIELD-TYPE", "a tag is {}, not a string".format(type(tag).__name__),
				tool_id=tool_id, item_id=item_id, field="tags", value=tag)
			continue
		if tag in model.TAGS:
			recognized += 1
		else:
			# Kept verbatim on the item; ignored by every derived computation.
			findings.add("E-TAG-UNKNOWN",
				"tag outside the closed set — kept on the item, ignored by every derived flag",
				tool_id=tool_id, item_id=item_id, field="tags", value=tag)
	if tags and recognized == 0:
		findings.add("E-TAG-NONE",
			"no recognized tag — the item groups under `notes` for rendering only and "
			"contributes to no derived flag. It is never dropped",
			tool_id=tool_id, item_id=item_id, field="tags", value=tags)

	# ── severity ───────────────────────────────────────────────────────
	severity = item.get("severity")
	if severity is None:
		findings.add("E-FIELD-MISSING", "severity is required",
			tool_id=tool_id, item_id=item_id, field="severity")
	elif severity not in model.SEVERITIES:
		findings.add("E-ENUM-INVALID",
			"severity must be one of: {}".format(", ".join(model.SEVERITIES)),
			tool_id=tool_id, item_id=item_id, field="severity", value=severity)

	# ── change (I-7, I-8) ──────────────────────────────────────────────
	change = item.get("change")
	if change is not None and not isinstance(change, dict):
		# Reported, and left on the item exactly as written (V2's rule): the
		# finding's `value` is bounded for readability, so nulling the field
		# here would be the only surviving copy losing its tail.
		findings.add("E-FIELD-TYPE", "change is {}, not an object".format(type(change).__name__),
			tool_id=tool_id, item_id=item_id, field="change", value=change)
		change = None
	elif isinstance(change, dict):
		version = change.get("version")
		if version is not None and not isinstance(version, str):
			findings.add("E-FIELD-TYPE", "change.version is {}, not a string".format(
				type(version).__name__),
				tool_id=tool_id, item_id=item_id, field="change.version", value=version)
		citation = change.get("citation")
		if not isinstance(citation, str) or not citation.strip():
			# `motivating_change` renamed and made mandatory-when-change-is-
			# present. The old rule worked — zero hollow motivating_change
			# across 92 relevancy items — so it survives unchanged.
			findings.add("E-CHANGE-UNCITED",
				"change.citation must be the verbatim upstream text, never null or "
				"\"none found\"",
				tool_id=tool_id, item_id=item_id, field="change.citation", value=citation)
		link_index = change.get("link_index")
		if link_index is not None:
			if isinstance(link_index, bool) or not isinstance(link_index, int):
				findings.add("E-FIELD-TYPE", "change.link_index is {}, not an int".format(
					type(link_index).__name__),
					tool_id=tool_id, item_id=item_id, field="change.link_index", value=link_index)
			elif not (0 <= link_index < link_count):
				findings.add("E-LINK-INDEX",
					"link_index {} is out of range of links[] (length {})".format(
						link_index, link_count),
					tool_id=tool_id, item_id=item_id, field="change.link_index", value=link_index)

	# ── local (I-14) ───────────────────────────────────────────────────
	local = item.get("local")
	quarantine = []
	if local is not None and not isinstance(local, dict):
		findings.add("E-FIELD-TYPE", "local is {}, not an object".format(type(local).__name__),
			tool_id=tool_id, item_id=item_id, field="local", value=local)
		local = None  # for the checks below only; the item keeps what was written
	elif isinstance(local, dict):
		normalized_local = dict(local)
		direction = local.get("direction")
		if direction is None:
			findings.add("E-FIELD-MISSING", "local.direction is required",
				tool_id=tool_id, item_id=item_id, field="local.direction")
		elif direction not in model.DIRECTIONS:
			findings.add("E-ENUM-INVALID",
				"local.direction must be one of: {}".format(", ".join(model.DIRECTIONS)),
				tool_id=tool_id, item_id=item_id, field="local.direction", value=direction)
		effect = local.get("effect")
		if effect is None:
			findings.add("E-FIELD-MISSING", "local.effect is required",
				tool_id=tool_id, item_id=item_id, field="local.effect")
		elif effect not in model.EFFECTS:
			findings.add("E-ENUM-INVALID",
				"local.effect must be one of: {}".format(", ".join(model.EFFECTS)),
				tool_id=tool_id, item_id=item_id, field="local.effect", value=effect)
		_require_string(local.get("statement"), findings, tool_id, item_id, "local.statement")
		evidence = normalize_evidence(local.get("evidence"), findings, tool_id, item_id,
			"local.evidence")
		normalized_local["evidence"] = evidence
		citations, bad = validate_citations(local.get("citations"), findings, tool_id,
			item_id, "local.citations")
		normalized_local["citations"] = citations
		quarantine.extend(bad)
		# I-14 — the mechanical replacement for "if you can point at the
		# touchpoint, you owe a relevancy item". Warns; never sets or clears
		# the direction for you.
		if direction == "reaches" and not evidence:
			findings.add("E-REACHES-UNEVIDENCED",
				"direction \"reaches\" with no evidence — the claim that it lands here "
				"needs a path",
				tool_id=tool_id, item_id=item_id, field="local.evidence")
		_resolve_evidence(evidence, findings, tool_id, item_id, "local.evidence", resolver)
		out["local"] = normalized_local
		local = normalized_local

	# ── I-1 ────────────────────────────────────────────────────────────
	if not isinstance(change, dict) and not isinstance(local, dict):
		findings.add("E-ITEM-EMPTY",
			"an item with neither `change` nor `local` says nothing",
			tool_id=tool_id, item_id=item_id)

	# ── I-2, I-3 — definitional consistency, not heuristics ────────────
	# These compare two fields the checker wrote against each other. A
	# violation is reported and NOTHING is changed — no severity is lowered.
	if severity == "incompatible":
		if not (isinstance(local, dict) and local.get("direction") == "reaches"
				and local.get("effect") == "risk"):
			findings.add("E-SEV-INCOMPAT-UNGROUNDED",
				"`incompatible` means something that works here today stops working, so it "
				"requires local.direction \"reaches\" and local.effect \"risk\"",
				tool_id=tool_id, item_id=item_id, field="severity")
	if severity == "warning" and not isinstance(local, dict):
		findings.add("E-SEV-WARNING-UNGROUNDED",
			"`warning` is a behaviour change *here*, so it requires a `local` block",
			tool_id=tool_id, item_id=item_id, field="severity")

	# ── security block (I-4, I-5, I-6) ─────────────────────────────────
	security = item.get("security")
	tagged_security = isinstance(tags, list) and "security" in tags
	if security is not None and not isinstance(security, dict):
		findings.add("E-FIELD-TYPE", "security is {}, not an object".format(
			type(security).__name__),
			tool_id=tool_id, item_id=item_id, field="security", value=security)
		security = None  # for the checks below only; the item keeps what was written
	if tagged_security and not isinstance(security, dict):
		findings.add("E-SEC-BLOCK-MISSING",
			"tagged `security` with no `security` block — the per-item rating lives there",
			tool_id=tool_id, item_id=item_id, field="security")
	if isinstance(security, dict) and not tagged_security:
		findings.add("E-SEC-BLOCK-ORPHAN",
			"a `security` block on an item not tagged `security`",
			tool_id=tool_id, item_id=item_id, field="security")
	if isinstance(security, dict):
		cve_id = security.get("cve_id")
		if cve_id is not None:
			if not isinstance(cve_id, str) or not _CVE_ID.match(cve_id):
				findings.add("E-CVE-MALFORMED", "not a CVE id",
					tool_id=tool_id, item_id=item_id, field="security.cve_id", value=cve_id)
		advisory_id = security.get("advisory_id")
		if advisory_id is not None and not isinstance(advisory_id, str):
			findings.add("E-FIELD-TYPE", "security.advisory_id is {}, not a string".format(
				type(advisory_id).__name__),
				tool_id=tool_id, item_id=item_id, field="security.advisory_id", value=advisory_id)
		rating = security.get("rating")
		if rating not in model.CVE_RATINGS:
			findings.add("E-ENUM-INVALID",
				"security.rating must be one of: {}".format(", ".join(model.CVE_RATINGS)),
				tool_id=tool_id, item_id=item_id, field="security.rating", value=rating)
		basis = security.get("rating_basis")
		if basis not in model.RATING_BASES:
			findings.add("E-ENUM-INVALID",
				"security.rating_basis must be one of: {}".format(", ".join(model.RATING_BASES)),
				tool_id=tool_id, item_id=item_id, field="security.rating_basis", value=basis)
		exploited = security.get("exploited_in_wild")
		if exploited is not None and not isinstance(exploited, bool):
			findings.add("E-FIELD-TYPE", "security.exploited_in_wild is {}, not a bool".format(
				type(exploited).__name__),
				tool_id=tool_id, item_id=item_id, field="security.exploited_in_wild",
				value=exploited)
		if rating != "unknown" and basis == "unrated":
			findings.add("E-SEC-RATING-UNBASED",
				"a rating of \"{}\" with basis \"unrated\" — a grade with no issuer is a "
				"guess".format(rating),
				tool_id=tool_id, item_id=item_id, field="security.rating_basis")

	# ── watch_hit (I-20) ───────────────────────────────────────────────
	# Checker-authored, validator-grounded. `watch_topics` is the stored
	# topic set for THIS tool from the session's watch-item snapshot; None
	# means no snapshot was supplied and the check degrades to a warning.
	# Extra keys inside `watch_hit` are tolerated and kept, exactly as they
	# are inside `change`, `local` and `security`.
	raw_hit = item.get("watch_hit")
	if raw_hit is not None:
		if not isinstance(raw_hit, dict):
			# Reported, and kept verbatim on the item (V2's rule).
			findings.add("E-FIELD-TYPE", "watch_hit is {}, not an object".format(
				type(raw_hit).__name__),
				tool_id=tool_id, item_id=item_id, field="watch_hit", value=raw_hit)
		else:
			topic = raw_hit.get("topic")
			if topic is None:
				findings.add("E-FIELD-MISSING",
					"watch_hit.topic is required — the VERBATIM topic of the stored watch "
					"item this change answers",
					tool_id=tool_id, item_id=item_id, field="watch_hit.topic")
			elif not isinstance(topic, str):
				findings.add("E-FIELD-TYPE", "watch_hit.topic is {}, not a string".format(
					type(topic).__name__),
					tool_id=tool_id, item_id=item_id, field="watch_hit.topic", value=topic)
			elif not topic.strip():
				findings.add("E-FIELD-MISSING", "watch_hit.topic is empty",
					tool_id=tool_id, item_id=item_id, field="watch_hit.topic")
			elif watch_topics is None:
				findings.add("W-WATCH-UNCHECKED",
					"no watch-item snapshot in this session, so the hit could not be "
					"checked against the store — it is kept",
					tool_id=tool_id, item_id=item_id, field="watch_hit.topic", value=topic)
			elif topic.strip() not in watch_topics:
				findings.add("E-WATCH-HIT-UNGROUNDED",
					"names no stored watch item for this tool — the topic is copied "
					"verbatim from the standing notes, never paraphrased",
					tool_id=tool_id, item_id=item_id, field="watch_hit.topic", value=topic)
			# These two fire independently of the topic checks — a hit that is
			# ungrounded AND has no local block reports both, in the manner of
			# 01-nonconforming's two-findings-in-one cases.
			if not isinstance(item.get("local"), dict):
				findings.add("E-WATCH-HIT-NOLOCAL",
					"a watch-item hit is a statement about this setup, so it carries a "
					"`local` block",
					tool_id=tool_id, item_id=item_id, field="watch_hit")
			if item.get("severity") == "info":
				findings.add("W-WATCH-HIT-UNRAISED",
					"a hit on a watched topic earns at least `notable` — reported, never "
					"bumped: re-rating is convergence's",
					tool_id=tool_id, item_id=item_id, field="severity", value="info")

	return out, quarantine


def _resolve_evidence(evidence, findings, tool_id, item_id, field, resolver):
	for entry in evidence:
		path = model.evidence_path(entry)
		if not path:
			continue
		try:
			outcome, detail = resolver.resolve(path)
		except OSError as exc:
			findings.add("W-STRUCT-UNCHECKED",
				"could not resolve an evidence path ({}: {})".format(type(exc).__name__, exc),
				tool_id=tool_id, item_id=item_id, field=field, value=path)
			continue
		if outcome == "unconfigured":
			findings.add("W-EVID-ROOT",
				"resolves under \"{}\", which is a real repo but not a configured root — "
				"a configuration finding about the run, not a checker defect".format(detail),
				tool_id=tool_id, item_id=item_id, field=field, value=path)
		elif outcome == "missing":
			findings.add("E-EVID-404", "well-formed path that resolves under no root",
				tool_id=tool_id, item_id=item_id, field=field, value=path)


# ── suggestions and the structural outlet (§4) ──────────────────────────────
_OP_REQUIRES = {
	"manifest_add": ("manifest", "to", "anchor.section"),
	"manifest_remove": ("manifest", "from"),
	"manifest_replace": ("manifest", "from", "to"),
	"manifest_move": ("manifest", "from", "anchor.section"),
	"tap_add": ("manifest", "to"),
	"tap_remove": ("manifest", "from"),
	"install_method_change": ("from", "to"),
	"task_add": ("to", "anchor.file"),
	"task_change": ("to", "anchor.file"),
}


def _ref_ok(ref, findings, tool_id, sug_id, field):
	if not isinstance(ref, dict):
		findings.add("E-FIELD-TYPE", "{} is {}, not a Ref object".format(field,
			type(ref).__name__), tool_id=tool_id, item_id=sug_id, field=field, value=ref)
		return False
	ok = True
	if ref.get("type") not in model.REF_TYPES:
		findings.add("E-ENUM-INVALID", "{}.type must be one of: {}".format(
			field, ", ".join(model.REF_TYPES)),
			tool_id=tool_id, item_id=sug_id, field=field + ".type", value=ref.get("type"))
		ok = False
	if not isinstance(ref.get("name"), str) or not ref["name"]:
		findings.add("E-FIELD-MISSING", "{}.name is required".format(field),
			tool_id=tool_id, item_id=sug_id, field=field + ".name", value=ref.get("name"))
		ok = False
	return ok


def _struct_get(block, dotted):
	if dotted == "anchor.section":
		return (block.get("anchor") or {}).get("section") if isinstance(block.get("anchor"), dict) else None
	if dotted == "anchor.file":
		return (block.get("anchor") or {}).get("file") if isinstance(block.get("anchor"), dict) else None
	return block.get(dotted)


def validate_structural(block, tool_id, sug_id, findings: Findings, manifest: Manifest):
	"""The typed structural outlet (`REDESIGN.md` §B3). Its load-bearing field
	is `subjects[]` — the entities the change is *about*, separate from the
	files it edits.

	The measured case: four groups independently produced quarantine
	suggestions; three edit CLAUDE.md/AGENTS.md and the fourth adds a
	`setup.sh quarantine` dispatch whose body covers two of the four casks the
	others document as needing it. A `target_files` intersection cannot see
	that — zero file overlap. The collision is semantic: same subject,
	different files. So the outlet carries the subject set.

	Returns the subject refs for the run-wide `subject_index`."""
	subjects = []
	if not isinstance(block, dict):
		findings.add("E-FIELD-TYPE", "structural is {}, not an object".format(
			type(block).__name__), tool_id=tool_id, item_id=sug_id, field="structural",
			value=block)
		return subjects

	op = block.get("op")
	if op not in model.STRUCTURAL_OPS:
		findings.add("E-ENUM-INVALID", "structural.op must be one of: {}".format(
			", ".join(model.STRUCTURAL_OPS)),
			tool_id=tool_id, item_id=sug_id, field="structural.op", value=op)
		op = None

	raw_subjects = block.get("subjects")
	if not isinstance(raw_subjects, list) or not raw_subjects:
		findings.add("E-FIELD-MISSING",
			"structural.subjects must name at least one entity the change is about — "
			"it is the collision key `target_files` cannot provide",
			tool_id=tool_id, item_id=sug_id, field="structural.subjects", value=raw_subjects)
	else:
		for ref in raw_subjects:
			if _ref_ok(ref, findings, tool_id, sug_id, "structural.subjects[]"):
				subjects.append("{}:{}".format(ref["type"], ref["name"]))

	manifest_name = block.get("manifest")
	if manifest_name is not None:
		if _names_forbidden_manifest(manifest_name):
			findings.add("E-INTEL-BREWFILE",
				"intel.Brewfile is out of this tool entirely — not a candidate source, not "
				"a compatibility check, not a suggestion target, not on the page",
				tool_id=tool_id, item_id=sug_id, field="structural.manifest",
				value=manifest_name)
			manifest_name = None
		elif manifest_name != "Brewfile":
			findings.add("E-ENUM-INVALID", "structural.manifest must be \"Brewfile\" or null",
				tool_id=tool_id, item_id=sug_id, field="structural.manifest", value=manifest_name)
			manifest_name = None

	refs_ok = True
	for field in ("from", "to"):
		ref = block.get(field)
		if ref is not None and not _ref_ok(ref, findings, tool_id, sug_id,
				"structural." + field):
			refs_ok = False

	if op is None:
		return subjects

	for required in _OP_REQUIRES[op]:
		if _struct_get(block, required) in (None, ""):
			findings.add("E-FIELD-MISSING",
				"structural.{} is required for op \"{}\"".format(required, op),
				tool_id=tool_id, item_id=sug_id, field="structural." + required)
			return subjects

	# A precondition is a claim about a well-formed op. Checking one against a
	# Ref that already failed its own shape check would dereference `name` on
	# something that has none — the shape failure is already reported, so stop
	# here rather than trading one finding for a crashed stage.
	if not refs_ok:
		return subjects
	# The anchor limb is scoped to the ops that actually read one. Applying it
	# to all nine let a plain `"anchor": null` — a checker emitting a uniform
	# block — skip I-16 entirely with no finding at all, which is the silent
	# skip this outlet exists to prevent.
	if any(r.startswith("anchor.") for r in _OP_REQUIRES[op]):
		if any(not isinstance(_struct_get(block, r), str)
				for r in _OP_REQUIRES[op] if r.startswith("anchor.")):
			findings.add("E-FIELD-TYPE",
				"structural.anchor's value for op \"{}\" is not a string".format(op),
				tool_id=tool_id, item_id=sug_id, field="structural.anchor")
			return subjects

	_check_preconditions(op, block, tool_id, sug_id, findings, manifest, manifest_name)
	return subjects


def _check_preconditions(op, block, tool_id, sug_id, findings, manifest, manifest_name):
	"""I-16. Reports; never edits an op, a subject list or a section."""
	def unchecked(what):
		findings.add("W-STRUCT-UNCHECKED",
			"{} — the precondition for op \"{}\" is unverified, not satisfied".format(what, op),
			tool_id=tool_id, item_id=sug_id, field="structural.op")

	def fail(message, field="structural.op", value=None):
		findings.add("E-STRUCT-PRECOND", message, tool_id=tool_id, item_id=sug_id,
			field=field, value=value)

	frm, to = block.get("from"), block.get("to")
	anchor = block.get("anchor") if isinstance(block.get("anchor"), dict) else {}

	manifest_ops = ("manifest_add", "manifest_remove", "manifest_replace", "manifest_move",
		"tap_add", "tap_remove")
	if op in manifest_ops:
		if not manifest_name:
			unchecked("no readable manifest was named")
			return
		if not manifest.readable:
			unchecked("no readable Brewfile under the configured macos-setup root")
			return

	if op == "manifest_add":
		if manifest.has(to["type"], to["name"]):
			fail("{} \"{}\" is already in the manifest".format(to["type"], to["name"]),
				"structural.to", to["name"])
		if not manifest.has_section(anchor.get("section")):
			fail("anchor.section \"{}\" is not a section of the manifest".format(
				anchor.get("section")), "structural.anchor.section", anchor.get("section"))
	elif op == "manifest_remove":
		if not manifest.has(frm["type"], frm["name"]):
			fail("{} \"{}\" is not in the manifest".format(frm["type"], frm["name"]),
				"structural.from", frm["name"])
	elif op == "manifest_replace":
		if not manifest.has(frm["type"], frm["name"]):
			fail("{} \"{}\" is not in the manifest".format(frm["type"], frm["name"]),
				"structural.from", frm["name"])
		if manifest.has(to["type"], to["name"]):
			fail("{} \"{}\" is already in the manifest".format(to["type"], to["name"]),
				"structural.to", to["name"])
	elif op == "manifest_move":
		if not manifest.has(frm["type"], frm["name"]):
			fail("{} \"{}\" is not in the manifest".format(frm["type"], frm["name"]),
				"structural.from", frm["name"])
			return
		if not manifest.has_section(anchor.get("section")):
			fail("anchor.section \"{}\" is not a section of the manifest".format(
				anchor.get("section")), "structural.anchor.section", anchor.get("section"))
			return
		current = manifest.section_of(frm["type"], frm["name"])
		if current is not None and _norm_section(current) == _norm_section(anchor["section"]):
			fail("{} \"{}\" is already in section \"{}\" — a move to where it already is".format(
				frm["type"], frm["name"], current), "structural.anchor.section", current)
	elif op in ("tap_add", "tap_remove"):
		ref = to if op == "tap_add" else frm
		if ref.get("type") != "tap":
			fail("op \"{}\" needs a Ref of type \"tap\"".format(op),
				"structural." + ("to" if op == "tap_add" else "from"), ref.get("type"))
		elif op == "tap_add" and manifest.has("tap", ref["name"]):
			fail("tap \"{}\" is already in the manifest".format(ref["name"]),
				"structural.to", ref["name"])
		elif op == "tap_remove" and not manifest.has("tap", ref["name"]):
			fail("tap \"{}\" is not in the manifest".format(ref["name"]),
				"structural.from", ref["name"])
	elif op == "install_method_change":
		if frm.get("type") == to.get("type"):
			fail("install_method_change needs distinct from/to types; both are \"{}\"".format(
				frm.get("type")), "structural.to", to.get("type"))
		elif frm.get("type") in ("formula", "cask", "tap", "mas"):
			if not manifest.readable:
				unchecked("no readable Brewfile under the configured macos-setup root")
			elif not manifest.has(frm["type"], frm["name"]):
				fail("{} \"{}\" does not resolve in its current mechanism".format(
					frm["type"], frm["name"]), "structural.from", frm["name"])
	elif op in ("task_add", "task_change"):
		anchor_file = anchor.get("file")
		path = os.path.join(manifest.root, anchor_file)
		if not os.path.exists(path):
			fail("anchor.file \"{}\" does not exist".format(anchor_file),
				"structural.anchor.file", anchor_file)
			return
		if op == "task_add":
			if not manifest.tasks_readable:
				unchecked("no readable setup.sh under the configured macos-setup root")
				return
			token = str(to.get("name", "")).split(":")[-1]
			if token in manifest.tasks:
				fail("setup.sh already dispatches \"{}\"".format(token),
					"structural.to", to.get("name"))


# ── V5: impact (§5.5) ───────────────────────────────────────────────────────
def research_produced_content(view) -> bool:
	"""A checker that failed, timed out or returned an empty shell has told us
	nothing — never the same as "nothing but security fixes". Neither has a
	validator stage that failed partway: what survived is not the corpus."""
	return (not view.get("research_error") and not view.get("validator_error")
		and bool(view["items"]))


def compute_impact(view) -> str:
	"""Does anything in this release touch *this* setup? → "none" | "possible"
	| "unknown".

	Two heuristics present in the old implementation are gone, and their
	removal is the point (`REDESIGN.md` §F criterion 1):

	  * `category != "security"` is replaced by `effect == "risk"`. The
	    documented reason for the proxy — "a security relevancy is a reason to
	    *upgrade*, not a risk of upgrading" — is now said directly by the
	    checker in a field built for it, instead of being inferred from a
	    category name. The proxy was also wrong in one direction: a security
	    change *can* be a risk here.
	  * the separate headliner clause is gone; it existed only because a
	    headliner had no local finding. Its real case (`mise:rust`) is now
	    `breaking`-tagged items at warning+, which the last clause covers.

	`"watch-item"` is NOT in the suggestion clause: `REDESIGN.md` §D row 4
	accepts dropping it, and `item-schema.md` §5.5 flags its own draft as wrong
	against that row. It reads `model.needs_a_decision`, the same negation the
	bucket clause and `W-ATTENTION-NOSUG` ask, and that matters here more than
	anywhere: `compute_initial_bucket` tests `security_auto` **before** its own
	suggestion clause, and `security_auto`'s inputs are `impact` and
	`security_only`. Written positively, a suggestion whose kind is typo'd
	`"edits"` — or drifted to a list — reads as no impact, and a tool with any
	security content lands in `security_auto`, pre-accepted, carrying an
	unreviewed proposed edit to the user's system. That is the `brew:libpq`
	defect this module's header names, reached by exactly the route the header
	describes.

	`needs_a_decision` and `("edit", "structural")` differ **only** on kinds
	outside the vocabulary, so no conforming input changes behaviour."""
	if view["source"] in NON_VERSION_SOURCES:
		return "none" if assemble.finding_expected(view) else "possible"
	if not research_produced_content(view):
		# "unknown" can never reach security_auto, so it never renders as
		# auto-approved. One spelling of the question, shared with
		# compute_security_only, so the two cannot come to disagree.
		return "unknown"
	suggestions = view.get("suggestions") or []
	if (view.get("pinned")
			or assemble.config_needs_attention(view)
			or any(model.needs_a_decision(assemble.suggestion_kind(s))
				for s in suggestions if isinstance(s, dict))
			or any(i.get("severity") == "incompatible" for i in view["items"])
			# `isinstance`, not `or {}`: V2 reports a wrong-typed `local` and
			# leaves it on the item, so a truthy non-dict reaches this read.
			or any(isinstance(i.get("local"), dict)
				and i["local"].get("effect") == "risk"
				and i.get("severity") in ("notable", "warning", "incompatible")
				for i in view["items"])
			or any(isinstance(i.get("tags"), list) and "breaking" in i["tags"]
				and i.get("severity") in ("warning", "incompatible")
				for i in view["items"])):
		return "possible"
	return "none"


# ── V6: initial bucketing (§5.6) ────────────────────────────────────────────
def compute_security_only(view, has_security: bool) -> bool:
	if not has_security or not research_produced_content(view):
		return False
	silent = view.get("vendor_silent_categories") or []
	if isinstance(silent, list) and [c for c in silent if c != "security"]:
		return False
	return all(model.allowed_for_security_only(i) for i in view["items"])


def compute_risk_level(view) -> str:
	"""Unchanged in meaning from `assemble.compute_risk_level`; the "relevancy
	above info" clause becomes "an item with a `local` block at warning or
	worse", which is the same population under the new shape. `structural`
	joins `edit` as an authored, non-baseline action."""
	if model.content_losing(view):
		# D1(b): a view missing content a human would have read can justify no
		# risk grade below elevated. Above the source clause on purpose — this
		# already held for validator_error and now holds for the other three
		# content-losing reasons.
		return "elevated"
	if view["source"] in NON_VERSION_SOURCES:
		return "low" if assemble.finding_expected(view) else "elevated"
	if view.get("pinned"):
		return "elevated"
	for item in view["items"]:
		if isinstance(item.get("local"), dict) and item.get("severity") in ("warning", "incompatible"):
			return "elevated"
	for sug in view.get("suggestions") or []:
		# Same negation, same reason as `compute_impact`: `risk_level` feeds
		# `pre_accept`, so an unrecognized kind must raise it, not be waved past.
		if isinstance(sug, dict) and model.needs_a_decision(assemble.suggestion_kind(sug)):
			return "elevated"
	if view["version_delta"] in ("major", "unknown"):
		return "elevated"
	if view.get("research_error") or view.get("validator_error"):
		return "elevated"
	if "security" in (view.get("vendor_silent_categories") or []):
		# Documented silence is fine; documented silence ABOUT SECURITY is not.
		# The clause below deliberately treats a non-empty
		# `vendor_silent_categories` as "the vendor publishes nothing, ever" —
		# claudebar, every run, forever — which is noise the user cannot act on.
		# `["security"]` says something else entirely: there IS security content
		# and we could not read it. Without this the two clauses combine into
		# the worst answer available — not elevated because the list is
		# non-empty, and so `pre_accept` on the baseline — and an unread
		# security release is auto-approved. `has_security` moves such a tool
		# into `security_mixed`, which makes it visible; this is what stops it
		# being pre-accepted while it sits there.
		return "elevated"
	if not view["items"] and not (view.get("vendor_silent_categories") or []):
		return "elevated"
	return "low"


def compute_initial_bucket(view, has_security, security_only, impact, risk_level, runnable) -> str:
	"""Strict precedence, first match wins. This is the one implementation:
	assembly carries the result onto the Tool as `review_bucket` and never
	recomputes it.

	**This is a baseline for convergence to review, not a decision**
	(`REDESIGN.md` §C3). It is labelled `initial_review_bucket` in the output
	and carries `bucket_inputs` so convergence can see *why* without
	re-deriving it.

	**Memory proposals do not force `attention`; action proposals do.**
	`method-note` and `watch-item` propose changes to what we remember; `edit`
	and `structural` propose changes to the user's system. Only the latter
	needs a decision, so the clause goes through `model.needs_a_decision`
	rather than testing "anything that is not an upgrade".

	That predicate is a negation — *not* a memory kind — so an unrecognized
	kind still forces a decision. Testing `in ACTION_SUGGESTION_KINDS` instead
	would let a drifted `"edits"` read as a memory proposal and leave a real
	edit on a `routine` tool, with `E-ENUM-INVALID` raised and feeding nothing.

	**This clause is not the whole guarantee**, and reading it as one is a
	mistake: `security_auto` is tested two clauses earlier and never reaches
	here. `compute_impact` and `compute_risk_level` ask the same predicate for
	that reason — they are what hold an unrecognized kind out of the
	pre-accepting bucket.

	The old spelling was harmless only while watch items were rare. `REDESIGN.md`
	§L1 now expects **many** per-tool method notes and watch items, so "not an
	upgrade" would put most of the fleet on the "needs you" list — inflating the
	surface §A and criterion 10 exist to compact, and forcing exactly the review
	§L5 was designed to make optional. `W-ATTENTION-NOSUG` reads the same tuple,
	so a bucket and its explanation cannot drift apart."""
	# Clause 0 — D1(b): content-losing input fails closed. Deliberately ABOVE
	# the source clause: a content-losing degradation on an `expected`
	# brew-health or skill-drift finding still forces review — the one class
	# of tool whose findings are routinely waved through must not also be the
	# one class whose degradation is invisible. Every non-content-losing
	# finding is a marker on the card and moves nothing.
	if model.content_losing(view):
		return "attention"
	if view["source"] in NON_VERSION_SOURCES:
		return "routine" if assemble.finding_expected(view) else "attention"
	if (has_security and security_only and impact == "none"
			and view["version_delta"] not in ("major", "unknown") and runnable
			# D2 (widened) + E3: elevated risk, a reaching change, or a watch
			# hit is a reason a human should look, and this is the one bucket
			# whose name means "no decision needed" — a barred tool falls
			# through to `security_mixed`, which renders an expanded card with
			# an "affects this setup" badge and correct counters. Guarding
			# HERE rather than in apply_pre_accept keeps the bucket and the
			# checkbox telling one story; a pre_accept-only guard leaves the
			# tool inside a collapsed strip headed "accepted" while
			# individually undecided, and anything downstream of finalize_tool
			# desyncs the Overview tiles. `model.pre_accept_bars` documents
			# each limb's measured justification.
			and not model.pre_accept_bars(view)):
		return "security_auto"
	if has_security:
		return "security_mixed"
	if (risk_level == "elevated"
			or assemble.config_needs_attention(view)
			or any(model.needs_a_decision(assemble.suggestion_kind(s))
				for s in (view.get("suggestions") or []) if isinstance(s, dict))
			or not runnable):
		return "attention"
	return "routine"


# ── one tool ────────────────────────────────────────────────────────────────
def validate_tool(candidate, research, findings: Findings, resolver: RootResolver,
		manifest: Manifest, watch_topics=None):
	"""V2–V6 for one candidate. Returns the tool's validation view."""
	tool_id = candidate["id"]
	source = candidate.get("source")
	name = candidate.get("name") or tool_id.split(":", 1)[-1]

	# Every key a healthy view carries is present from the start, defaulted
	# conservatively, so a consumer reading `view["version_delta"]` does not
	# KeyError on precisely the tool degradation was supposed to keep usable.
	# The conservative *values* are held by `validator_error` rather than by
	# these defaults, since `_derive_axes` overwrites them whenever it runs.
	view = {
		"id": tool_id,
		"source": source,
		"name": name,
		"pinned": bool(candidate.get("pinned")),
		"research_error": None,
		# Set by `_guard` when a stage fails. Distinct from `research_error`,
		# which is the *checker* telling us it failed: this is the validator
		# telling us its own view of the tool is incomplete. Both make the
		# derived axes read `unknown`, which is the only honest answer and the
		# one that can never reach an auto-accepting bucket.
		"validator_error": None,
		"links": [],
		"vendor_silent_categories": [],
		"items": [],
		"quarantine": [],
		"config_status": default_config_status(),
		"suggestions": [],
		"subject_refs": [],
		"flags": model.recompute_flags([]),
		"version_delta": "unknown",
		"impact": "unknown",
		"risk_level": "elevated",
		"initial_review_bucket": "attention",
		"bucket_inputs": {"has_security": False, "security_only": False,
			"impact": "unknown", "version_delta": "unknown", "runnable": False},
		"security_display_item_ids": [],
		"watch_hit_item_ids": [],
		"self_test_tagged_suggestion_ids": [],
		"spec_violations": [],
		"degradation": {"content_losing": [], "markers": [], "quarantined": 0},
	}
	# Both finding sources spell the flag `expected` on the candidate and
	# `{source}_expected` on the built tool; assemble.finding_expected() reads
	# the latter, so the translation happens here rather than as a third
	# spelling of the same question.
	if source == "brew-health":
		view["health_expected"] = bool(candidate.get("expected", False))
	elif source == "skill-drift":
		view["drift_expected"] = bool(candidate.get("expected", False))
	if research is None:
		view["research_error"] = "no research entry for this candidate"
	else:
		view["research_error"] = research.get("research_error")
		_guard(view, findings, "research section",
			lambda: _read_research(view, research, findings, tool_id, resolver, manifest,
				watch_topics))

	# version delta, then the derived axes, in dependency order.
	_guard(view, findings, "derived axes",
		lambda: _derive_axes(view, candidate, findings))
	return view


def _guard(view, findings: Findings, stage, work):
	"""Run one stage of a tool's validation, or report that it failed.

	Criterion 4 with the constraint attached: the next unanticipated shape must
	cost as little as possible and say so. It must NOT cost the items already
	validated — discarding those is deletion, which is what this whole layer is
	forbidden to do — so `view` is built up in place and whatever conformed
	before the failure stays on it.

	And it must not *promote* the tool either. A stage that failed halfway
	leaves a view missing exactly the content it had not reached yet — the
	breaking item, the structural suggestion — so computing a bucket from what
	survived is the `brew:libpq` defect by another route: a tool reaching
	`security_auto`, pre-accepted, because the thing holding it back is the
	thing that went missing. `validator_error` makes the derived axes read
	`unknown`, which no auto-accepting bucket accepts."""
	try:
		work()
	except Exception as exc:  # noqa: BLE001 — the point is to contain everything
		note = "{}: {}".format(stage, type(exc).__name__)
		view["validator_error"] = (note if not view.get("validator_error")
			else view["validator_error"] + "; " + note)
		findings.add("E-VALIDATOR-CRASH",
			"the {} stage failed ({}: {}); this tool keeps whatever conformed before "
			"the failure, and its derived axes read \"unknown\" because the view is "
			"incomplete".format(stage, type(exc).__name__, exc), tool_id=view["id"])


def _read_research(view, research, findings, tool_id, resolver, manifest,
		watch_topics=None):
	"""V1-input → V2/V3/V3b/V4 for one tool's research object."""
	# The closed top-level key set (U1) — before anything else in this
	# function, so it sits inside _guard's "research section" stage and a
	# crash while reporting it degrades the tool rather than the run. The
	# five VALIDATOR_ONLY_FLAGS are excluded: they already raise
	# E-FLAG-FORBIDDEN in _check_flags — one finding, not two — and they are
	# markers, not content-losing, because the validator ignores the value
	# and computes its own, so nothing a human would have read went missing.
	quarantine = []
	view["quarantine"] = quarantine
	for key in sorted(research):
		if key in model.RESEARCH_KEYS or key in model.VALIDATOR_ONLY_FLAGS:
			continue
		findings.add("E-RESEARCH-UNKNOWNKEY",
			"a research object carries `{}`, which this contract does not recognize — "
			"the value is quarantined, not dropped, and this tool is held for "
			"review".format(key),
			tool_id=tool_id, field=key, value=research[key])
		# The value goes in VERBATIM, not _short()-ed — same rule and same
		# reason as split_members: the quarantine exists so nothing a human
		# would have read is lost, and it came from JSON so it is
		# serializable by construction.
		quarantine.append({"field": key, "item_id": None, "value": research[key]})

	links = as_list(research.get("links"), findings, tool_id, "links")
	view["links"] = links
	silent = as_list(research.get("vendor_silent_categories"), findings, tool_id,
		"vendor_silent_categories")
	view["vendor_silent_categories"] = [c for c in silent if isinstance(c, str)]

	raw_items, member_quarantine = split_members(research.get("items"), findings, tool_id, "items")
	quarantine.extend(member_quarantine)
	# V3b — ids are assigned here, after normalization and before any invariant
	# that references an item by id. Assigned in AUTHORED order, so a
	# duplicate's `~2` suffix falls on the later of the pair; the canonical
	# ordering is applied afterwards and never moves an id.
	normalized, seen = [], set()
	view["items"] = normalized
	for raw in raw_items:
		derived = model.derive_item_id(tool_id, raw.get("anchor"))
		item_id = model.disambiguate(derived, seen)
		if item_id != derived:
			findings.add("E-ITEM-DUP-ANCHOR",
				"two items derive one id — the strongest available signal that one "
				"change was written twice. Both are KEPT and the later one suffixed; "
				"convergence decides",
				tool_id=tool_id, item_id=item_id, field="anchor", value=derived)
		seen.add(item_id)
		try:
			item, item_quarantine = validate_item(raw, tool_id, item_id, len(links),
				findings, resolver, watch_topics)
		except Exception as exc:  # noqa: BLE001
			# One item's unanticipated shape costs that item's *checks*, never
			# the item: it is kept exactly as written, with its assigned id, so
			# convergence can still address it and a human can still read it.
			findings.add("E-VALIDATOR-CRASH",
				"validating this item failed ({}: {}); it is kept verbatim and "
				"unchecked".format(type(exc).__name__, exc),
				tool_id=tool_id, item_id=item_id)
			item, item_quarantine = dict(raw, id=item_id,
				id_stability=model.id_stability(raw.get("anchor"))), []
		normalized.append(item)
		quarantine.extend(item_quarantine)
	view["items"] = model.order_items(normalized)
	# The GROUNDED hits, exported like security_display_item_ids: the 70-point
	# highlight and the page's badge read this list, never the raw claim —
	# scoring an unverified topic would let a paraphrase displace a genuine
	# highlight, the unvalidated-channel failure the field exists to kill.
	# The claim itself (has_watch_hit) still bars pre-acceptance unverified.
	view["watch_hit_item_ids"] = [i["id"] for i in view["items"]
		if model.grounded_watch_hit(i, watch_topics)]
	view["config_status"] = _validate_config_status(view, research, findings, tool_id,
		resolver)
	suggestions, subject_refs, sug_quarantine = _validate_suggestions(
		research, findings, tool_id, manifest)
	view["suggestions"] = suggestions
	view["subject_refs"] = subject_refs
	# Every quarantined member ends up on the tool, whichever array it came
	# from: dropping one is deletion, and a human would have read it.
	quarantine.extend(sug_quarantine)
	_check_flags(research, view, findings, tool_id)


def _derive_axes(view, candidate, findings):
	"""V5 and V6. Reads only what is on the view, and `research_produced_content`
	refuses to read a view that a failed stage left incomplete — so a tool whose
	research stage failed lands on `unknown`/`elevated`/`attention`, exactly
	where one with no research at all lands."""
	source, name = view["source"], view["name"]
	# The codes raised so far, BEFORE any axis reads them — `content_losing`
	# feeds `compute_risk_level` and `compute_initial_bucket` below. All four
	# content-losing reasons are raised during `_read_research` or by `_guard`
	# around it, both complete by now; `validate_session` re-runs both
	# assignments after the run-level checks so `markers` also picks up the
	# late codes (W-SUG-DUP-ID, W-ENTRY-UNMATCHED). That the content_losing
	# limb cannot differ between the two computations is a positional
	# guarantee, so it is pinned by a test rather than trusted
	# (test_validate_items.DegradationChannelTests).
	view["spec_violations"] = findings.codes_for(view["id"])
	view["degradation"] = model.compute_degradation(view)
	view["version_delta"] = assemble.compute_version_delta(
		candidate.get("current_version"), candidate.get("latest_version"), source,
		view["id"])[0]
	flags = model.recompute_flags(view["items"])
	view["flags"] = flags
	# The EFFECTIVE has_security, and it is deliberately wider than the flag.
	#
	# `recompute_flags` is tag-only and stays tag-only: it is the value
	# `E-FLAG-DISAGREE` compares a checker's claim against, and a checker that
	# correctly reported `has_security: false` from its own items must not read
	# as disagreeing with us. So the widening happens here, at the point of use.
	#
	# `vendor_silent_categories == ["security"]` is research's explicit
	# statement "this release has security content the vendor refused to
	# detail". Dropping it is not merely a lost label — `compute_risk_level`
	# only elevates a tool with no items when `vendor_silent_categories` is
	# ALSO empty, so a non-empty one actively suppresses that elevation. Tag-only
	# here would therefore produce the worst combination available: not elevated
	# (vendor_silent is non-empty) and not security (no security tag) → routine
	# → pre-accepted. A field whose whole purpose is "look at this" would
	# guarantee nobody does.
	#
	# Computed ONCE and used everywhere below — `security_only`'s gate, the
	# bucket, and `bucket_inputs` — because a value that is "security" for
	# bucketing and "not security" for the security-only test is its own
	# auto-accept route, and a bucket its own recorded inputs cannot explain is
	# exactly the opacity §C3 exists to remove.
	# The third limb closes the same hole from the other side. I-4 already
	# reports a `security` block on an item that forgot the tag
	# (E-SEC-BLOCK-ORPHAN) — but reporting it while treating the tool as
	# non-security is precisely how a CVE-carrying tool would reach a bucket
	# that pre-accepts. A degraded run still renders and still applies, so a
	# finding is not a gate; erring toward "security" is.
	has_security = bool(flags["has_security"]
		or "security" in (view.get("vendor_silent_categories") or [])
		or any(isinstance(i.get("security"), dict) for i in view["items"]
			if isinstance(i, dict)))
	impact = compute_impact(view)
	security_only = compute_security_only(view, has_security)
	risk_level = compute_risk_level(view)
	runnable = (False if source in NON_VERSION_SOURCES
		else bool(assemble.upgrade_command_and_runnable(source, name)[1]))
	view["impact"] = impact
	view["risk_level"] = risk_level
	# Assigned BEFORE the bucket: clause 2's bar (`model.pre_accept_bars`)
	# reads `risk_level` off the view, so it must exist when the clause runs.
	# Same values either way — this is ordering, not meaning.
	view["bucket_inputs"] = {
		"has_security": has_security,
		"security_only": security_only,
		"impact": impact,
		"version_delta": view["version_delta"],
		"runnable": runnable,
	}
	view["initial_review_bucket"] = compute_initial_bucket(view, has_security,
		security_only, impact, risk_level, runnable)
	view["security_display_item_ids"] = [
		i["id"] for i in model.security_display_items(view["items"])]
	# Criterion 17 makes convergence responsible for every tagged proposal, so
	# the tagged set is exported rather than left to be re-derived from prose.
	view["self_test_tagged_suggestion_ids"] = _self_test_tagged_ids(
		view["suggestions"], view["id"])

	# I-15 — existing rule, kept. It reads the same tuple as
	# `compute_initial_bucket`: a memory proposal is not an answer to
	# "needs_attention but what do I do about it", so it must not silence the
	# warning any more than it may raise the bucket.
	if assemble.config_needs_attention(view) and not any(
			model.needs_a_decision(assemble.suggestion_kind(s))
			for s in view["suggestions"] if isinstance(s, dict)):
		findings.add("W-ATTENTION-NOSUG",
			"config_status is needs_attention but no edit or structural suggestion says "
			"what to do about it",
			tool_id=view["id"], field="config_status.state")


def _validate_config_status(view, research, findings, tool_id, resolver):
	"""`config_status.evidence` gets the same split as an item's (§3.2). It is
	the single worst offender today: 133 of the run's 272 warnings, against 286
	evidence strings."""
	raw = research.get("config_status")
	if raw is None:
		return default_config_status()
	if not isinstance(raw, dict):
		findings.add("W-SHAPE-COERCED",
			"config_status was {}, not an object — read as unknown".format(type(raw).__name__),
			tool_id=tool_id, field="config_status", value=raw)
		return default_config_status()
	out = dict(raw)
	if out.get("detail") is None:
		out["detail"] = ""
	evidence = normalize_evidence(raw.get("evidence"), findings, tool_id, None,
		"config_status.evidence")
	out["evidence"] = evidence
	_resolve_evidence(evidence, findings, tool_id, None, "config_status.evidence", resolver)
	citations, bad = validate_citations(raw.get("citations"), findings, tool_id, None,
		"config_status.citations")
	out["citations"] = citations
	# Every quarantined member ends up on the tool, whichever array it came from.
	view["quarantine"].extend(bad)
	return out


def _validate_suggestions(research, findings, tool_id, manifest):
	suggestions, quarantine = split_members(research.get("suggestions"), findings, tool_id,
		"suggestions")
	subject_refs = []
	for sug in suggestions:
		raw_id = sug.get("id")
		sug_id = raw_id if isinstance(raw_id, str) else None
		kind = assemble.suggestion_kind(sug)
		if kind not in model.SUGGESTION_KINDS:
			findings.add("E-ENUM-INVALID", "suggestion kind must be one of: {}".format(
				", ".join(model.SUGGESTION_KINDS)),
				tool_id=tool_id, item_id=sug_id, field="kind", value=kind)
		block = sug.get("structural")
		if kind == "structural" and block is None:
			findings.add("E-FIELD-MISSING",
				"a structural suggestion needs its `structural` block — that block, not the "
				"diff preview, is the reviewable artifact",
				tool_id=tool_id, item_id=sug_id, field="structural")
		elif kind != "structural" and block is not None:
			findings.add("E-FIELD-TYPE",
				"a `structural` block on a suggestion of kind \"{}\" — the block is present "
				"iff the kind is \"structural\"".format(kind),
				tool_id=tool_id, item_id=sug_id, field="structural")
		if block is not None:
			for subject in validate_structural(block, tool_id, sug_id, findings, manifest):
				subject_refs.append({"subject": subject,
					"suggestion_id": sug_id or (tool_id + ":<no id>")})
		_validate_memory_proposal(sug, kind, findings, tool_id, sug_id)
		# I-17, second limb — no target_files[] path is intel.Brewfile.
		for target in as_list(sug.get("target_files"), findings, tool_id, "target_files", sug_id):
			path = target.get("path") if isinstance(target, dict) else target
			if _names_forbidden_manifest(path):
				findings.add("E-INTEL-BREWFILE",
					"intel.Brewfile is out of this tool entirely — it is not a suggestion "
					"target", tool_id=tool_id, item_id=sug_id, field="target_files", value=path)
	return suggestions, subject_refs, quarantine


# I-19 — the memory-proposal shape. Two kinds, one payload pair each, and the
# self-test tag that `REDESIGN.md` §L7 makes load-bearing.
def _validate_memory_proposal(sug, kind, findings, tool_id, sug_id):
	"""I-19. A memory proposal carries its payload, and a failed self-test
	carries its reason.

	The tag is validated rather than waved through as a free-floating extra
	field, because §L7 makes convergence key its review off it. An unvalidated
	channel is how `Watch item hit:` broke — a literal string worth 70
	highlight points that nobody checked, which silently stopped firing the
	moment it was paraphrased. A misspelt `self_test_failed` would be invisible
	in exactly the same way.

	A tag with no reason is a drop with extra steps, and criterion 17 exists to
	prevent precisely that, so the reason is required whenever the tag is
	present."""
	# `assemble.suggestion_kind` returns `sug.get("kind")` verbatim, so a
	# drifted `"kind": ["edit"]` arrives unhashable and `.get()` would raise —
	# costing this tool its entire suggestions array, which is deletion, which
	# is the one thing this layer may not do. Same guard as `derive_item_id`'s.
	payload = model.MEMORY_PAYLOAD_FIELDS.get(kind) if isinstance(kind, str) else None
	if payload:
		for field, purpose in sorted(payload.items()):
			value = sug.get(field)
			if not isinstance(value, str) or not value.strip():
				findings.add("E-FIELD-MISSING",
					"a \"{}\" proposal needs `{}` — {}".format(kind, field, purpose),
					tool_id=tool_id, item_id=sug_id, field=field)

	tag = sug.get("self_test_failed")
	if tag is None:
		return
	if payload is None:
		findings.add("E-FIELD-TYPE",
			"a `self_test_failed` tag on a suggestion of kind \"{}\" — the tag is present "
			"iff the kind is one of: {}".format(
				kind if isinstance(kind, str) else type(kind).__name__,
				", ".join(model.MEMORY_SUGGESTION_KINDS)),
			tool_id=tool_id, item_id=sug_id, field="self_test_failed")
		return
	if not isinstance(tag, dict):
		findings.add("E-FIELD-TYPE",
			"`self_test_failed` must be an object with `limb` and `reason`",
			tool_id=tool_id, item_id=sug_id, field="self_test_failed",
			value=type(tag).__name__)
		return
	limb = tag.get("limb")
	if limb not in model.SELF_TEST_LIMBS:
		findings.add("E-ENUM-INVALID",
			"a self-test limb must be one of: {}".format(", ".join(model.SELF_TEST_LIMBS)),
			tool_id=tool_id, item_id=sug_id, field="self_test_failed.limb",
			value=limb if isinstance(limb, str) else None)
	reason = tag.get("reason")
	if not isinstance(reason, str) or not reason.strip():
		findings.add("E-SELFTEST-NOREASON",
			"a `self_test_failed` tag with no reason — convergence reviews the tag to "
			"decide whether dropping the proposal is right, and cannot do that from the "
			"limb name alone",
			tool_id=tool_id, item_id=sug_id, field="self_test_failed.reason")


def _self_test_tagged_ids(suggestions, tool_id):
	"""The tagged set, exported per tool so criterion 17 is checkable rather
	than asserted: convergence has to review every tagged proposal and verify
	that dropping it is appropriate.

	Memory kinds only. A tag on an `edit` is a shape error, not a droppable
	memory proposal, and E-FIELD-TYPE is the loud channel for it — listing it
	here would invite convergence to treat a proposed change to the user's
	system as a note it may quietly discard.

	An id-less suggestion gets the same `<no id>` placeholder a structural
	subject ref does, so a proposal missing its id is still visible here rather
	than silently absent from the list convergence works from."""
	out = []
	for sug in suggestions:
		if not isinstance(sug, dict) or sug.get("self_test_failed") is None:
			continue
		if assemble.suggestion_kind(sug) not in model.MEMORY_SUGGESTION_KINDS:
			continue
		sug_id = sug.get("id")
		out.append(sug_id if isinstance(sug_id, str) else tool_id + ":<no id>")
	return out


def _names_forbidden_manifest(path) -> bool:
	"""I-17's second limb. Compares the normalized basename, so `./intel.Brewfile`
	and `macos-setup/intel.Brewfile` are the same finding as the bare name — a
	string-equality test would wave both through."""
	if not isinstance(path, str) or not path:
		return False
	return os.path.basename(os.path.normpath(path)) in model.FORBIDDEN_MANIFESTS


def _check_flags(research, view, findings, tool_id):
	"""I-13 plus criterion 2's enforcement.

	The four emittable flags are assertions, not inputs: the validator's
	recomputation wins and the disagreement is reported, because a disagreement
	says the checker's items do not say what it thinks they say. The five
	validator-only flags gate `security_auto`/`pre_accept`; a checker emitting
	one is an error, not an overrule — a wrong `has_security` is a wrong label,
	a wrong `security_only` is an unreviewed upgrade."""
	recomputed = model.recompute_flags(view["items"])
	declared = research.get("flags")
	declared = declared if isinstance(declared, dict) else {}
	for flag in model.CHECKER_FLAGS:
		if flag not in declared:
			continue
		if declared[flag] != recomputed[flag]:
			findings.add("E-FLAG-DISAGREE",
				"checker said {!r}, items say {!r} — the validator's value wins".format(
					declared[flag], recomputed[flag]),
				tool_id=tool_id, field="flags." + flag, value=declared[flag])
	for flag in model.VALIDATOR_ONLY_FLAGS:
		for container, where in ((declared, "flags." + flag), (research, flag)):
			if flag in container:
				findings.add("E-FLAG-FORBIDDEN",
					"a per-tool checker has no view of the corpus and does not decide this; "
					"it is computed here",
					tool_id=tool_id, field=where, value=container[flag])


# ── the watch-item snapshot (I-20) ──────────────────────────────────────────
def _load_watch_snapshot(path, findings: Findings):
	"""The per-session copy of the machine-global watch-item store — the
	orchestrating session writes it at step 2, at the moment it fills
	{{STANDING_NOTES}}, so grounding checks a hit against the evidence the
	claim was made from rather than against a store that may have moved since.

	Absent is legal and costs nothing but the check: every hit then raises
	W-WATCH-UNCHECKED and is kept. Unreadable or wrong-typed is the same case
	plus one E-RESEARCH-UNREADABLE naming the file. No run ever aborts on it —
	W-STRUCT-UNCHECKED is the sibling: a check that cannot run degrades to a
	warning, never to silence."""
	if not os.path.exists(path):
		return None
	try:
		with open(path, "r", encoding="utf-8") as fh:
			snapshot = json.load(fh)
	except Exception as exc:  # same width as v1_load, same reasons
		findings.add("E-RESEARCH-UNREADABLE",
			"{}: {}".format(type(exc).__name__, exc), field="watch-items.json")
		return None
	if not isinstance(snapshot, dict):
		findings.add("E-RESEARCH-UNREADABLE",
			"watch-items.json is {}, not an object keyed by tool id".format(
				type(snapshot).__name__), field="watch-items.json")
		return None
	return snapshot


def _watch_topics_for(snapshot, tool_id):
	"""The stored topic set for one tool, or None when there is no snapshot.

	Exact string match after .strip() — no normalization, no case folding, no
	fuzzy match: a checker copies the string, it does not rewrite it. And per
	tool: a topic stored for another tool does not ground a hit here."""
	if snapshot is None:
		return None
	entries = snapshot.get(tool_id)
	return frozenset(
		e["topic"].strip() for e in (entries if isinstance(entries, list) else ())
		if isinstance(e, dict) and isinstance(e.get("topic"), str) and e["topic"].strip())


# ── the run ─────────────────────────────────────────────────────────────────
def validate_session(session_dir: str, roots, manifest_root=None, unconfigured_roots=None):
	"""→ the validation document. Never raises for input shape."""
	findings = Findings()
	resolver = RootResolver(roots, unconfigured_roots=unconfigured_roots)
	manifest = Manifest(manifest_root or (roots[0] if roots else "."))
	watch_snapshot = _load_watch_snapshot(
		os.path.join(session_dir, "watch-items.json"), findings)

	collect_path = os.path.join(session_dir, "collect.json")
	try:
		with open(collect_path, "r", encoding="utf-8") as fh:
			collect = json.load(fh)
	except Exception as exc:
		raise NoCandidateSet("could not read {!r}: {}: {}".format(
			collect_path, type(exc).__name__, exc))
	if not isinstance(collect, dict):
		raise NoCandidateSet("{!r} is {}, not a JSON object — there is no candidate "
			"set to validate".format(collect_path, type(collect).__name__))

	research_by_id, orphans = v1_load(os.path.join(session_dir, "research"), findings)

	health, _ = assemble.read_findings_block(collect, "brew_health", "brew-health")
	drift, _ = assemble.read_findings_block(collect, "skill_drift", "skill-drift")
	candidates = (
		assemble.read_candidate_list(collect, "brew")
		+ assemble.read_candidate_list(collect, "mise")
		+ assemble.read_candidate_list(collect, "standalone")
		+ assemble.read_candidate_list(collect, "macos")
		+ health + drift)

	known = set()
	views = []
	for candidate in candidates:
		tool_id = candidate.get("id")
		if not isinstance(tool_id, str) or not tool_id:
			findings.add("E-ENTRY-NOID", "a collect.json candidate has no usable \"id\"",
				field="collect.json", value=candidate)
			continue
		if not isinstance(candidate.get("source"), str) or not candidate["source"]:
			findings.add("E-FIELD-MISSING", "a candidate has no usable \"source\"",
				tool_id=tool_id, field="collect.json")
			continue
		known.add(tool_id)
		# `validate_tool` guards each of its own stages and never raises, so a
		# degraded tool arrives here as a complete view carrying whatever
		# conformed — not as a blank replacement for it.
		views.append(validate_tool(candidate, research_by_id.get(tool_id), findings,
			resolver, manifest,
			watch_topics=_watch_topics_for(watch_snapshot, tool_id)))

	unmatched = []
	for tool_id in sorted(research_by_id):
		if tool_id not in known:
			findings.add("W-ENTRY-UNMATCHED",
				"names no candidate in collect.json — kept as unmatched, merged into no tool",
				tool_id=tool_id)
			unmatched.append(tool_id)

	# I-18 — suggestion ids unique across the whole report.
	seen_sug = {}
	for view in views:
		for sug in view.get("suggestions") or []:
			sid = sug.get("id") if isinstance(sug, dict) else None
			if not isinstance(sid, str) or not sid:
				continue
			if sid in seen_sug:
				findings.add("W-SUG-DUP-ID",
					"also used by {} — a collision almost always means an id pattern was "
					"copied rather than derived".format(seen_sug[sid]),
					tool_id=view["id"], item_id=sid, field="suggestions[].id")
			else:
				seen_sug[sid] = view["id"]

	# §4.5 — deterministic support for an agentic decision, never the decision.
	# The validator reports the asymmetry between a structural change's subject
	# set and the subjects its siblings document; it never edits a subjects list.
	subject_index = {}
	target_file_index = {}
	for view in views:
		for ref in view.get("subject_refs") or []:
			subject_index.setdefault(ref["subject"], []).append(ref["suggestion_id"])
		for sug in view.get("suggestions") or []:
			if not isinstance(sug, dict):
				continue
			sid = sug.get("id") if isinstance(sug.get("id"), str) else view["id"] + ":<no id>"
			targets = sug.get("target_files")
			for target in targets if isinstance(targets, list) else ():
				path = target.get("path") if isinstance(target, dict) else target
				if isinstance(path, str):
					target_file_index.setdefault(path, []).append(sid)

	entries = findings.sorted()
	for view in views:
		view["spec_violations"] = findings.codes_for(view["id"])
		# Second computation, same function: picks up the codes raised after
		# per-tool validation. The content_losing limb cannot change here —
		# every content-losing signal is raised inside the per-tool stages —
		# and DegradationChannelTests pins that rather than trusting it.
		view["degradation"] = model.compute_degradation(view)

	by_code, by_tool, by_severity = {}, {}, {}
	for entry in entries:
		by_code[entry["code"]] = by_code.get(entry["code"], 0) + 1
		by_severity[entry["severity"]] = by_severity.get(entry["severity"], 0) + 1
		if entry["tool_id"]:
			by_tool[entry["tool_id"]] = by_tool.get(entry["tool_id"], 0) + 1

	return {
		"contract_version": model.CONTRACT_VERSION,
		"generated_at": collect.get("generated_at") or datetime.now(timezone.utc)
			.strftime("%Y-%m-%dT%H:%M:%SZ"),
		"session_id": os.path.basename(os.path.normpath(session_dir)),
		# A clean run produces zero findings. With a nonzero count the run is
		# degraded and says so here, in assemble.warn, and in the exit code.
		"clean": not entries,
		"findings": entries,
		"counts": {
			"by_code": dict(sorted(by_code.items())),
			"by_tool": dict(sorted(by_tool.items())),
			"by_severity": dict(sorted(by_severity.items())),
		},
		"orphans": orphans,
		"unmatched": unmatched,
		"subject_index": {k: sorted(set(v)) for k, v in sorted(subject_index.items())},
		"target_file_index": {k: sorted(set(v)) for k, v in sorted(target_file_index.items())},
		"tools": views,
	}


def warn_lines(document) -> list:
	"""The human-readable tail of the same data — one finding per line, prefixed
	with its code, in the same order.

	This is `assemble.warn` (`references/item-schema.md` §3.3): **the
	spec-conformance channel for the run and nothing else**, and a clean run
	produces zero lines. Both entry points write it from this one function —
	`assemble.py` in the pipeline, this file's `main()` when the validator is
	run on its own — so there is one channel with one name, not two files with
	the same content."""
	out = []
	for entry in document["findings"]:
		where = entry.get("item_id") or entry.get("tool_id") or "-"
		field = entry.get("field")
		value = entry.get("value")
		line = "{} {}: {}".format(entry["code"], where, entry["message"])
		if field:
			line += " [{}]".format(field)
		if value:
			line += " → {!r}".format(value)
		out.append(line)
	return out


# ── the published contract fixture ──────────────────────────────────────────
def fixture_session():
	"""→ (session_dir, roots, unconfigured_roots) for `contract/session`.

	The whole point of the bundled roots is that the golden output does not
	depend on what happens to sit beside the repo on one machine: every
	evidence path in the fixture resolves — or fails to — against files that
	ship with it. `unconfigured_roots` is passed explicitly for the same
	reason, so `W-EVID-ROOT` has a case without discovery having to find one.

	A sibling package asserts against the contract like this:

		import sys; sys.path.insert(0, SCRIPTS_DIR)
		import items, validate_items
		session, roots, extra = validate_items.fixture_session()
		got = validate_items.validate_session(session, roots,
			manifest_root=roots[0], unconfigured_roots=extra)
		assert got == items.load_fixture("expected_validation.json")

	`validate_session` writes nothing, so this is read-only."""
	session = model.fixture_path("session")
	roots_dir = os.path.join(session, "roots")
	macos_setup = os.path.join(roots_dir, "macos-setup")
	return (
		session,
		[macos_setup, os.path.join(macos_setup, "dotfiles"), os.path.join(roots_dir, "systems")],
		[os.path.join(roots_dir, "tieto")],
	)


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("session_dir")
	parser.add_argument("--macos-setup-root", default=".")
	parser.add_argument("--dotfiles-root", default=None)
	parser.add_argument("--systems-root", default="~/project/github/tapppi/systems")
	args = parser.parse_args(argv)

	session_dir = os.path.abspath(args.session_dir)
	macos_setup_root = os.path.abspath(os.path.expanduser(args.macos_setup_root))
	dotfiles_root = os.path.abspath(os.path.expanduser(
		args.dotfiles_root or os.path.join(macos_setup_root, "dotfiles")))
	systems_root = os.path.abspath(os.path.expanduser(args.systems_root))

	try:
		document = validate_session(session_dir, [macos_setup_root, dotfiles_root, systems_root],
			manifest_root=macos_setup_root)
	except NoCandidateSet as exc:
		# Exit >3 is reserved for a genuine I/O/environment failure. A degraded
		# run is not a failure and never reaches here.
		print("Error: {}".format(exc), file=sys.stderr)
		return 4

	out_path = os.path.join(session_dir, "validation.json")
	with open(out_path, "w", encoding="utf-8") as fh:
		json.dump(document, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")
	warn_path = os.path.join(session_dir, "assemble.warn")
	lines = warn_lines(document)
	with open(warn_path, "w", encoding="utf-8") as fh:
		fh.write("\n".join(lines) + ("\n" if lines else ""))
	print(out_path)
	# 0 clean, 3 degraded. 3 is not a failure — everything downstream still
	# runs. The workflow surfaces it; it never aborts.
	return 0 if document["clean"] else 3


if __name__ == "__main__":
	sys.exit(main())
