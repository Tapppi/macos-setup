#!/usr/bin/env python3
"""
items.py — the item model. This module IS the contract.

`references/item-schema.md` is its prose; everything load-bearing about the
shape of an item, the closed tag set, how an id is derived, and — critically —
**the ordering and the comparator** lives here as code, so a sibling package
asserts against an import rather than against a paragraph.

Why the ordering is in the contract at all: two parallel implementer tracks
once worked against a pinned *field* contract and still drifted on ordering,
and the drift had to be reconciled afterwards (`HANDOFF.md` §8). Field names
are not enough. `item_sort_key`/`compare_items` and the two CVE rank tables are
therefore exported, fixtured under `contract/`, and asserted by `test_items.py`.

This module holds **no I/O and no judgement**. It defines shapes, vocabularies,
derivations and orders. Validation lives in `validate_items.py`; every finding
it can raise is registered in `FINDING_CODES` below so the set of things the
deterministic layer can *say* is enumerable from one place.

Governing constraint (`REDESIGN.md` §A, §C3, criterion 1):

	The deterministic layer validates, normalizes, counts, buckets and
	calculates impact. It never deletes, trims or re-rates an item on a regex
	or heuristic rule. Judgement is reserved for convergence.

So nothing here removes an item, shortens a string, or lowers a severity. The
normalizations are shape-only and each is licensed explicitly by the schema.
"""
from __future__ import annotations  # `X | None` annotations on Python 3.9
                                     # (macOS's bundled python3, before mise
                                     # provisions a newer one — same
                                     # constraint as assemble.py/server.py)

import json
import os
import re
from urllib.parse import quote, urlsplit

# Bumped when a consumer would have to change. Consumers pin against this, not
# against a git revision.
#
# 2 — WP2 admitted memory proposals: the `method-note` suggestion kind, the
#     `self_test_failed` tag (`REDESIGN.md` §L7), and the rule that a memory
#     proposal never forces a tool onto the attention list.
CONTRACT_VERSION = 2


# ── vocabularies ────────────────────────────────────────────────────────────
# All closed. Closed means the validator recognizes exactly these; an
# unrecognized value is KEPT verbatim on the item and reported, never dropped
# and never coerced to a neighbour (`item-schema.md` §2.3).

# The eight tags. One item carries many tags; an item is never repeated per
# category, which is what killed the headliners/relevancy/notable split.
TAGS = (
	"security",     # fixes/mitigates a vulnerability, or moves a security boundary
	"fix",          # corrects incorrect behaviour, non-security
	"feature",      # adds capability or an interface that did not exist
	"breaking",     # removes/renames/changes the contract of something that works today
	"deprecation",  # announces future removal; the old path still works
	"perf",         # measurable performance or resource change, no interface change
	"packaging",    # how it is built, signed, distributed, or depends
	"chore",        # a real, cited change with no consequence for any reader
)

# "how much does this matter to THIS machine" — deliberately not a tag, and
# deliberately the same four values the report template already speaks.
SEVERITIES = ("info", "notable", "warning", "incompatible")
SEVERITY_RANK = {"info": 0, "notable": 1, "warning": 2, "incompatible": 3}

ANCHOR_KINDS = ("cve", "advisory", "issue", "commit", "release", "none")

# `direction` answers "does this land on something this setup runs?";
# `effect` answers "is that good or bad for me?". Splitting them is what lets
# `compute_impact` read `effect == "risk"` instead of the old
# `category != "security"` proxy (`item-schema.md` §2.5).
DIRECTIONS = ("reaches", "does_not_reach", "unclear")
EFFECTS = ("risk", "benefit", "none")

CITATION_KINDS = (
	"changelog", "release_notes", "advisory", "upstream_source",
	"command", "observation", "prior_review",
)

CVE_RATINGS = ("critical", "high", "medium", "low", "unknown")
RATING_BASES = ("vendor", "nvd", "cvss", "unrated")

STRUCTURAL_OPS = (
	"manifest_add", "manifest_remove", "manifest_replace", "manifest_move",
	"tap_add", "tap_remove", "install_method_change",
	"task_add", "task_change",
)

REF_TYPES = ("formula", "cask", "tap", "mas", "task", "runtime", "section")

SUGGESTION_KINDS = ("upgrade", "edit", "structural", "watch-item", "method-note")

# **Memory proposals do not force `attention`; action proposals do.**
# `method-note` and `watch-item` propose changes to what we remember. `edit`
# and `structural` propose changes to the user's system. Only the latter needs
# a decision, so only the latter belongs in a clause that means "a human has to
# look at this".
#
# This is `REDESIGN.md` §D row 4's principle, which WP1 applied to *impact*
# (see `compute_impact`) but never carried to the bucket clause. It was
# harmless while watch items were rare; §L1 now expects **many** per-tool
# method notes and watch items, so leaving it would put most of the fleet on
# the "needs you" list and undo the compaction §A and criterion 10 exist for.
#
# `compute_initial_bucket` and `W-ATTENTION-NOSUG` both go through
# `needs_a_decision` below, so a bucket and its explanation cannot drift apart.
# `compute_impact` and `compute_risk_level` read `ACTION_SUGGESTION_KINDS`
# directly, which is the closed set they have always tested.
MEMORY_SUGGESTION_KINDS = ("watch-item", "method-note")
ACTION_SUGGESTION_KINDS = ("edit", "structural")


def needs_a_decision(kind) -> bool:
	"""Does this suggestion mean a human has to look at the tool?

	**Stated as a negation on purpose.** `kind not in MEMORY_SUGGESTION_KINDS`
	rather than `kind in ACTION_SUGGESTION_KINDS`, so that a kind nobody
	recognizes — a drifted `"edits"`, a kind from a future schema, a
	wrong-typed value — fails **safe**, onto the attention list. Written the
	other way, an unrecognized kind reads as a memory proposal and lets a tool
	stay `routine`: `E-ENUM-INVALID` would be raised and would feed nothing.

	The baseline `upgrade` suggestion is every tool's, so it decides nothing.
	Anything unhashable is not a memory kind either — the membership test is
	over a tuple, which any shape survives."""
	return kind != "upgrade" and kind not in MEMORY_SUGGESTION_KINDS

# What each memory kind must carry, and what each field is for. Both payloads
# are written so they read sensibly copied verbatim into the store on accept,
# because that is exactly what happens (`references/research.md`).
#
# `rationale` is required on both, and that is the one that answers the
# measured failure. The last run's defect was never a missing topic — it was
# rationales reciting the bar's own escape phrase, three of eight falsified by
# a sibling field in the same object. The self-test writes its four answers
# into `rationale`, §Standing Notes puts the generalisation claim there, and
# convergence promotes on it: requiring it non-empty is the least that field
# can be owed. Nothing here judges whether the answers are good — that is
# convergence's, and always was — but an unvalidated load-bearing string is
# how `Watch item hit:` died.
MEMORY_PAYLOAD_FIELDS = {
	"watch-item": {
		"watch_topic": "the short phrase a future run matches against its changelog",
		"watch_note": "the context that lets a future hit explain itself without "
			"re-deriving everything",
		"rationale": "your answers to the self-test, in your own words",
	},
	"method-note": {
		"method_topic": "what the note is about, in a few words",
		"method_note": "the instruction itself, written to read sensibly when copied "
			"verbatim into the next run's context",
		"rationale": "the failure the ordinary research path already produced here",
	},
}

# `REDESIGN.md` §L7: the per-tool agent's self-test applies a **tag**, never a
# removal. A proposal the agent never writes is one convergence cannot restore,
# so a failing self-test still writes the proposal and names the limb it failed.
# Convergence reviews every tagged proposal and decides whether dropping it is
# right.
#
# One value per limb the agent can fail, so convergence can tell "config_status
# already covers this" from "the bar's halves were never both answered" without
# re-reading prose:
SELF_TEST_LIMBS = (
	# Q2 — the tool's own `config_status.detail` describes re-verifying THIS
	# concern against THIS run's delta. Measured: 3 of 8 last run.
	"scope",
	# Q3 — the thing that could change is stated, set or pinned by a file in
	# the setup repos, so a future delta against that file is already checked.
	"changing-thing",
	# Q4 — the claimed limb's two halves are not both answered in the bar's
	# own terms (named party + named edit, or state-visibility + what breaks).
	"limb",
	# Method notes: the rationale predicts a failure rather than naming one
	# that already happened to this tool.
	"unwitnessed",
)

# `REDESIGN.md` §B1: the Intel Mac is out of this tool entirely. Not a source
# of candidates, not a compatibility check, not a suggestion target, not on the
# page. I-17 makes that a runtime check rather than a review item.
FORBIDDEN_MANIFESTS = ("intel.Brewfile",)

# `REDESIGN.md` §L2 (D2). A title is readable at a glance; detail belongs in
# `body`. Measured on the recorded run: median 142 chars, max 578 — a `body`
# that landed in the title field, and the largest single lever left on the
# report's default surface. The bar is REPORTED (W-TITLE-LONG), never enforced
# by truncation: truncating is deletion, and deletion is convergence's call.
TITLE_MAX_CHARS = 120


# ── tag → content group ─────────────────────────────────────────────────────
# Derived, never authored. This reproduces today's four content groups exactly
# and fixes one known misfile: codex's "`codex exec --full-auto` was removed"
# is filed today as notes/notable; tagged `breaking` it lands in `fixes`, where
# a reader looks for it. The mapping is data, not layout — the page may regroup.
GROUP_OF_TAG = {
	"security": "security",
	"breaking": "fixes",
	"deprecation": "fixes",
	"fix": "fixes",
	"feature": "features",
	"perf": "features",
	"packaging": "notes",
	"chore": "notes",
}
GROUP_PRECEDENCE = ("security", "fixes", "features", "notes")


def primary_group(item) -> str:
	"""The one group this item renders under. Unknown tags contribute nothing
	(they are reported by E-TAG-UNKNOWN and kept on the item); an item with no
	recognized tag falls to `notes` for rendering only, having already raised
	E-TAG-NONE."""
	tags = item.get("tags") if isinstance(item, dict) else None
	present = set()
	for tag in tags if isinstance(tags, list) else ():
		group = GROUP_OF_TAG.get(tag) if isinstance(tag, str) else None
		if group:
			present.add(group)
	for group in GROUP_PRECEDENCE:
		if group in present:
			return group
	return "notes"


def severity_rank(value) -> int:
	"""-1 for anything outside the vocabulary, so an out-of-spec severity sorts
	*after* `info` rather than crashing a comparison or silently reading as one
	of the four."""
	return SEVERITY_RANK.get(value, -1) if isinstance(value, str) else -1


def worst_severity(items) -> str | None:
	"""The tool's worst item severity, or None when nothing is rated. Only
	recognized severities count — an out-of-spec value is a reported defect,
	not a new top of the scale."""
	best = None
	for item in items or ():
		if not isinstance(item, dict):
			continue
		rank = severity_rank(item.get("severity"))
		if rank < 0:
			continue
		if best is None or rank > SEVERITY_RANK[best]:
			best = item["severity"]
	return best


# ── CVE rating ranks — TWO tables, and they are not interchangeable ─────────
# This pair is the single most drift-prone thing in the contract, which is why
# both are exported and `test_items.py` asserts they still agree with
# `assemble.py`'s private copies tier for tier.
#
# CVE_WORSE_RANK is for *resolution*: when two sources grade one CVE, the worse
# grade wins, and `unknown` means "no rating recorded" — which must lose to a
# real `low`, so it is 0.
#
# CVE_ORDER_RANK is for *ordering* the security display list, where `unknown`
# means "ungraded but real" and must NOT sort below a rated `low` — an absent
# grade is not evidence of harmlessness. It therefore sits between medium and
# low. Conflating the two is how a teamviewer Linux-only CVSS 8.8 would read as
# urgent on a macOS card.
CVE_WORSE_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}
CVE_ORDER_RANK = {"critical": 5, "high": 4, "medium": 3, "unknown": 2, "low": 1}


# ── anchors and ids (`item-schema.md` §7) ───────────────────────────────────
# Measured: 1 of 373 headliners was written the same way twice across two runs.
# So an id cannot come from what a checker *writes*; it comes from what a
# checker *declares*. Ids are assigned by the validator, never by the checker —
# 22 blind checkers minting free-form ids is exactly how suggestion ids collide
# today, and assembly carries a whole rename pass for it.
#
# `REDESIGN.md` §I1 removed cross-run diff from scope, so an id's cross-run
# stability no longer has to carry weight. Ids remain required for within-run
# duplicate detection and as convergence's addressing scheme.
ANCHOR_PATTERNS = {
	"cve": re.compile(r"^CVE-(?:19|20)\d{2}-\d{4,}$"),
	"advisory": re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,}$"),
	"issue": re.compile(r"^(?:[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)?#\d+$"),
	"commit": re.compile(r"^(?:[A-Za-z0-9._-]+/[A-Za-z0-9._-]+@)?[0-9a-fA-F]{7,40}$"),
	"release": re.compile(r"^[^/\s]+/[^\s]+$"),
}

# `cve`/`advisory`/`issue`/`commit` name a thing that keeps its name. `release`
# is half a slug and `none` is all slug.
ANCHORED_KINDS = ("cve", "advisory", "issue", "commit")


def anchor_is_wellformed(anchor) -> bool:
	"""Does this anchor satisfy the grammar for its own kind? (I-9.)"""
	if not isinstance(anchor, dict):
		return False
	kind = anchor.get("kind")
	if kind not in ANCHOR_KINDS:
		return False
	if kind == "none":
		# §7.2: `value` must be absent/null and a `slug` is required instead.
		# The field table omits `slug`; the grammar table requires it, and the
		# grammar table is what an id is derived from, so it wins.
		if anchor.get("value") is not None:
			return False
		slug = anchor.get("slug")
		return isinstance(slug, str) and bool(slug.strip())
	value = anchor.get("value")
	if not isinstance(value, str):
		return False
	return bool(ANCHOR_PATTERNS[kind].match(value))


def id_stability(anchor) -> str:
	"""`anchored` for the four kinds that name a durable thing, `slug`
	otherwise. Exported because any consumer comparing items across corpora has
	to say which subset it is defined over rather than reporting a slug-id
	difference as a change."""
	kind = anchor.get("kind") if isinstance(anchor, dict) else None
	return "anchored" if kind in ANCHORED_KINDS else "slug"


def derive_item_id(tool_id: str, anchor) -> str:
	"""`{tool_id}#{kind}:{urlencoded(value)}`, e.g.
	`brew:sops#issue:getsops%2Fsops%232245`.

	`kind: "none"` derives from the declared `slug` instead, under a `slug:`
	discriminator so it can never collide with a real anchor kind. A
	malformed or missing anchor still yields a usable, reported id — the item
	is kept (E-ANCHOR-MALFORMED), and an item with no handle is unaddressable
	by convergence, which is worse than an ugly one."""
	# `kind` is unvalidated here — ids are assigned before V2 reports on it — so
	# it can be any JSON value, and a dict-membership test on an unhashable one
	# raises. Every comparison below is written to survive that.
	kind = anchor.get("kind") if isinstance(anchor, dict) else None
	if kind == "none":
		slug = anchor.get("slug")
		raw = slug if isinstance(slug, str) else ""
		return "{}#slug:{}".format(tool_id, quote(raw, safe=""))
	if isinstance(kind, str) and kind in ANCHOR_PATTERNS:
		value = anchor.get("value")
		raw = value if isinstance(value, str) else ""
		return "{}#{}:{}".format(tool_id, kind, quote(raw, safe=""))
	# Unusable anchor. Give it a handle that says so rather than none at all.
	return "{}#malformed:{}".format(tool_id, quote(_anchor_repr(anchor), safe=""))


def _anchor_repr(anchor) -> str:
	if isinstance(anchor, dict):
		for key in ("value", "slug", "kind"):
			value = anchor.get(key)
			if isinstance(value, str) and value:
				return value
		return ""
	return str(anchor) if anchor is not None else ""


def disambiguate(item_id: str, seen) -> str:
	"""Two items deriving one id is the strongest available signal that one
	change got written twice — the measured `brew:sops` defect, where both
	texts name `#2245`. The validator does NOT merge them: merging is deletion
	plus a severity choice, i.e. exactly the judgement C3 reserves. It keeps
	both, suffixes the later `~2`/`~3`, and reports E-ITEM-DUP-ANCHOR."""
	if item_id not in seen:
		return item_id
	n = 2
	while "{}~{}".format(item_id, n) in seen:
		n += 1
	return "{}~{}".format(item_id, n)


# ── evidence shorthand (`item-schema.md` §3.1) ──────────────────────────────
# Evidence is PATHS ONLY, as objects. A bare string is accepted and normalized
# iff it is a path, optionally with a line locator. No spaces, no prose, no
# trailing punctuation, no parenthetical — the parenthetical *is* the
# conflation this split removes, and it now lives in `note`.
#
# `:L1,L2` is in the grammar because the recorded run already produced
# `dotfiles/home/.claude/settings.json:162,183` and today's `_TRAILING_LINE_REF`
# silently fails to strip it.
_EVIDENCE_SHORTHAND = re.compile(
	r"^(?P<path>[~$A-Za-z0-9_./@+\-]+)"
	r"(?::(?P<lines>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*))?$")


def parse_evidence_shorthand(value):
	"""→ the object form, or None when this string is not evidence.

	None is not "move it to citations". The validator raises E-EVID-MALFORMED
	and keeps the string verbatim on the item: auto-moving a non-path into
	`citations[]` is a regex deciding what a field means — the class §C3
	forbids — and it would paper over a broken path that happens to read like
	prose."""
	if not isinstance(value, str):
		return None
	match = _EVIDENCE_SHORTHAND.match(value)
	if not match:
		return None
	entry = {"path": match.group("path")}
	lines = match.group("lines")
	if lines:
		parsed = []
		for part in lines.split(","):
			if "-" in part:
				start, end = part.split("-", 1)
				parsed.append([int(start), int(end)])
			else:
				parsed.append(int(part))
		entry["lines"] = parsed
	return entry


def evidence_path(entry):
	"""The path of an evidence entry in either form, or None."""
	if isinstance(entry, dict):
		path = entry.get("path")
		return path if isinstance(path, str) else None
	parsed = parse_evidence_shorthand(entry)
	return parsed["path"] if parsed else None


def url_is_absolute_http(value) -> bool:
	"""A citation `url`, if present, must parse as an absolute http(s) URL.
	Syntax only — the validator never fetches one. A citation is not
	checkable, so it is not checked, so it cannot warn (which is the whole of
	the 272-of-275 problem)."""
	if not isinstance(value, str) or not value:
		return False
	try:
		parts = urlsplit(value)
	except ValueError:
		return False
	return parts.scheme in ("http", "https") and bool(parts.netloc)


# ── the ordering, and the comparator (`HANDOFF.md` §8) ──────────────────────
# One total order over items, so WP2, WP3 and WP4 cannot drift. Read top to
# bottom, an ordered list answers "what would change my decision?" first:
#
#   1. content group        security → fixes → features → notes
#   2. severity, worst first
#   3. does it reach this machine?   reaches → unclear → does_not_reach → (no
#                                    local block at all)
#   4. is that good or bad?          risk → none → benefit → (no local block)
#   5. id, ascending — the tiebreak that makes the order TOTAL, so two
#      independently-sorted copies of one corpus are byte-identical.
#
# Step 5 is the part that matters most and is easiest to forget. Without it the
# order is stable-but-input-dependent, which is precisely the drift that had to
# be reconciled by hand last time.
DIRECTION_ORDER = {"reaches": 0, "unclear": 1, "does_not_reach": 2}
EFFECT_ORDER = {"risk": 0, "none": 1, "benefit": 2}
NO_LOCAL_RANK = 3


def _rank_of(table, value, default: int) -> int:
	"""A rank lookup that survives an unhashable value. Every enum in this model
	is kept verbatim when it is out of vocabulary, so any of them can arrive as
	a list or a dict."""
	return table.get(value, default) if isinstance(value, str) else default


def _local_of(item):
	local = item.get("local") if isinstance(item, dict) else None
	return local if isinstance(local, dict) else None


def item_sort_key(item) -> tuple:
	"""The canonical order. Total: equal keys imply equal ids."""
	local = _local_of(item)
	if local is None:
		direction_rank = NO_LOCAL_RANK
		effect_rank = NO_LOCAL_RANK
	else:
		# `_rank_of` rather than a bare `.get`: an out-of-vocabulary direction is
		# reported (E-ENUM-INVALID) and then KEPT VERBATIM on the item, so a list
		# or a dict reaches this lookup and an unhashable key raises TypeError.
		# Losing the whole tool to one bad enum is the degradation defect, not a
		# guard against it.
		direction_rank = _rank_of(DIRECTION_ORDER, local.get("direction"), NO_LOCAL_RANK)
		effect_rank = _rank_of(EFFECT_ORDER, local.get("effect"), NO_LOCAL_RANK)
	item_id = item.get("id") if isinstance(item, dict) else None
	return (
		GROUP_PRECEDENCE.index(primary_group(item)),
		-severity_rank(item.get("severity") if isinstance(item, dict) else None),
		direction_rank,
		effect_rank,
		item_id if isinstance(item_id, str) else "",
	)


def order_items(items) -> list:
	"""A new list in canonical order. Never mutates its argument, never drops a
	member — a non-dict member sorts to the end rather than raising, because a
	malformed member is a finding, not a reason to lose the whole list."""
	return sorted(list(items or ()), key=item_sort_key)


def compare_items(left, right) -> int:
	"""-1 / 0 / 1 under the canonical order. Exported so a sibling package can
	assert an ordering decision without importing `sorted` semantics."""
	a, b = item_sort_key(left), item_sort_key(right)
	if a < b:
		return -1
	if a > b:
		return 1
	return 0


def security_display_sort_key(item) -> tuple:
	"""Order for the items shown inline in the security block. Distinct from
	`item_sort_key` because this list is graded by what the *issuer* said
	first, then by whether it reaches us. Uses CVE_ORDER_RANK — see the note on
	the two rank tables above."""
	sec = item.get("security") if isinstance(item, dict) else None
	sec = sec if isinstance(sec, dict) else {}
	local = _local_of(item)
	item_id = item.get("id") if isinstance(item, dict) else None
	return (
		-_rank_of(CVE_ORDER_RANK, sec.get("rating"), 0),
		0 if sec.get("exploited_in_wild") else 1,
		_rank_of(DIRECTION_ORDER, (local or {}).get("direction"), NO_LOCAL_RANK),
		-severity_rank(item.get("severity") if isinstance(item, dict) else None),
		item_id if isinstance(item_id, str) else "",
	)


def is_security_display_item(item) -> bool:
	"""The replacement for `notable[]`'s clause 3 — a bar, not a count.

	The old clause promoted "any security-category relevancy at notable or
	worse" and had no direction test, because direction lived only on the entry
	it produced, downstream of the decision it should have gated. All three of
	openssh's slots were filled with items that say in their own summaries that
	the fix does not reach this machine. Direction now exists before selection
	and the predicate reads it.

	There is deliberately NO cap. A cap is a count, and counts invite padding.
	Whatever clears the bar is shown; everything else still exists, still
	renders in the collapsed detail, and still carries its finding."""
	if not isinstance(item, dict):
		return False
	tags = item.get("tags")
	if not isinstance(tags, list) or "security" not in tags:
		return False
	sec = item.get("security")
	sec = sec if isinstance(sec, dict) else {}
	local = _local_of(item) or {}
	return bool(
		sec.get("rating") == "critical"
		or sec.get("exploited_in_wild")
		or local.get("direction") == "reaches"
		or item.get("severity") in ("warning", "incompatible"))


def security_display_items(items) -> list:
	return sorted((i for i in items or () if is_security_display_item(i)),
		key=security_display_sort_key)


# ── watch-item hits (I-20) ──────────────────────────────────────────────────
# `watch_hit` is the READ side of the watch-item loop: "a watch item that
# already exists fired on this release". It is checker-authored and
# validator-grounded against the session's watch-item snapshot — the
# structured successor to the retired `Watch item hit:` literal, which died
# of being an unvalidated string (`REDESIGN.md` §O). The WRITE side —
# "please store a new watch item" — is `memory_proposals.watch_topic` /
# `watch_note` on a suggestion, and conflating the two is what produced the
# literal channel in the first place.
def has_watch_hit(items) -> bool:
	"""Does any item claim to answer a stored watch item?

	The one predicate for every consumer of the claim — the 70-point
	`watch_item_hit` highlight and the page's badge ask this rather than
	re-deriving it, because the retired channel died precisely of consumers
	matching it differently. A dict is a claim even when malformed: a
	malformed one is reported (E-FIELD-MISSING / E-FIELD-TYPE on
	`watch_hit.topic`) and the claim still counts, which is the fail-closed
	direction."""
	return any(isinstance(i.get("watch_hit"), dict)
		for i in items or () if isinstance(i, dict))


# ── the closed top-level research-key set (U1) ──────────────────────────────
# Every top-level key a research object may carry. Anything else is
# E-RESEARCH-UNKNOWNKEY: quarantined, reported, and content-losing — the
# retired schema (headliners[], relevancy[], context[], notable[],
# cve_severities) used to be discarded with zero findings, and 70 of the 78
# entries in the real recorded corpus then landed routine/low/pre-accepted
# over an empty items[].
#
# RESEARCH_KEYS_READ is the measured read surface: every key
# validate_items.py or assemble.py reads off a research object.
# test_validate_items.py asserts that claim against the live source, so a new
# read cannot be added without widening the set here.
RESEARCH_KEYS_READ = (
	"id", "research_error", "links", "vendor_silent_categories", "items",
	"config_status", "suggestions", "flags", "release_inventory", "cask_sudo_hint",
)
# Candidate identity a checker may echo back from its prompt. Recognized and
# IGNORED — collect.json's candidate is authoritative for every one of them.
RESEARCH_KEYS_ECHOED = ("name", "source", "current_version", "latest_version", "pinned")
RESEARCH_KEYS = RESEARCH_KEYS_READ + RESEARCH_KEYS_ECHOED


# ── degradation: the fail-closed predicate (D1) ─────────────────────────────
# The four ways a view can be missing content a human would have read. Order
# is the emission order, fixed, so the page's chips never reshuffle between
# runs.
DEGRADATION_REASONS = (
	"quarantined-content",        # quarantine[] is non-empty
	"shape-coerced",              # W-SHAPE-COERCED — an array or object arrived as
	                              #   something else and was read as empty
	"unrecognized-research-key",  # E-RESEARCH-UNKNOWNKEY
	"validator-error",            # a validator stage failed partway on this tool
)

# The finding codes that themselves signal lost content. W-MEMBER-QUARANTINED
# is deliberately NOT here: the loss it reports is signalled by the non-empty
# `quarantine[]` it produced, and coding it twice would make a tool whose
# quarantine was later emptied still read as degraded.
CONTENT_LOSING_CODES = {
	"W-SHAPE-COERCED": "shape-coerced",
	"E-RESEARCH-UNKNOWNKEY": "unrecognized-research-key",
}


def content_losing(tool) -> list:
	"""→ the ordered reasons this tool's view is missing content a human would
	have read, or [] when nothing was lost.

	Reads a validation view and an assembled Tool identically — both carry
	`quarantine`, `validator_error` and `spec_violations` — so the bucket the
	validator computes and the card the page renders cannot come to disagree.

	This is `_guard`'s doctrine (the per-stage boundary in validate_items)
	applied to the paths that were never given it: computing a bucket from
	what survived is the `brew:libpq` defect by another route, whether the
	content went missing because a stage crashed or because a container
	arrived as the wrong type. Every OTHER finding is a marker on the card
	(D1(b)): an evidence-path typo is not a reason to hold an upgrade.

	Never raises: a drifted container is not evidence of loss — it is
	E-FIELD-TYPE's business."""
	if not isinstance(tool, dict):
		return []
	codes = tool.get("spec_violations")
	codes = {c for c in codes if isinstance(c, str)} if isinstance(codes, list) else set()
	code_reasons = {reason for code, reason in CONTENT_LOSING_CODES.items() if code in codes}
	reasons = []
	for reason in DEGRADATION_REASONS:
		if reason == "quarantined-content":
			quarantine = tool.get("quarantine")
			fires = isinstance(quarantine, list) and bool(quarantine)
		elif reason == "validator-error":
			fires = bool(tool.get("validator_error"))
		else:
			fires = reason in code_reasons
		if fires:
			reasons.append(reason)
	return reasons


def compute_degradation(tool) -> dict:
	"""→ {"content_losing": [...], "markers": [...], "quarantined": int}

	`markers` is every other finding code on this tool, sorted: D1's "renders
	as a visible marker rather than changing a bucket" half, given one place
	to be read from instead of requiring the page to re-derive it."""
	codes = tool.get("spec_violations") if isinstance(tool, dict) else None
	codes = [c for c in codes if isinstance(c, str)] if isinstance(codes, list) else []
	quarantine = tool.get("quarantine") if isinstance(tool, dict) else None
	return {
		"content_losing": content_losing(tool),
		"markers": sorted(c for c in codes if c not in CONTENT_LOSING_CODES),
		"quarantined": len(quarantine) if isinstance(quarantine, list) else 0,
	}


# ── the pre-acceptance bar (D2, E3) ─────────────────────────────────────────
# Emission order, fixed, like DEGRADATION_REASONS and for the same reason.
PRE_ACCEPT_BARS = ("elevated-risk", "reaches-item", "watch-hit")


def pre_accept_bars(tool) -> list:
	"""→ the ordered reasons this tool may not start accepted, or [].

	Reads a validation view and an assembled Tool identically (`risk_level`,
	`bucket_inputs.has_security`, `items`), like `content_losing` and for the
	same reason. The guard site is `compute_initial_bucket`'s security_auto
	clause: a barred security-only tool falls through to `security_mixed`,
	whose card renders expanded with an "affects this setup" badge — guarding
	only in `apply_pre_accept` would leave the tool inside a collapsed strip
	headed "accepted" while individually undecided. `apply_pre_accept` asks
	the same function as its second call site, so the bucket and the checkbox
	cannot tell two stories.

	- ``elevated-risk`` — D2: elevated risk bars pre-acceptance everywhere.
	  On its own it is an unstable proxy: `brew:duckdb` came out `elevated`
	  in one recorded run and `low` in the other for the identical upgrade,
	  because the axis moves with the checker's wording. Hence the next limb.
	- ``reaches-item`` — D2, widened: an item whose `local.direction` is
	  `"reaches"`, counted only where the tool has security content.
	  `elevated` alone misses `cask:wireshark-app` (28 advisories, a `sharkd`
	  flaw reachable from the shell PATH, `config_status: unknown`); the
	  direction is already computed — it is what `security_display_item_ids`
	  exports — and across the 17 recorded `security_auto` instances it
	  selects all 3 tools a human would want, with no false positives. It is
	  restricted to the security path because that is the population it was
	  measured on — and because this redesign exists to kill a report that
	  cost 919 words per decision, with 373 of 584 items unable to change any
	  decision: a predicate that quietly un-compresses the routine population
	  is a regression against the thing being built.
	- ``watch-hit`` — E3: a watch item exists to be told about, and one that
	  fires into an auto-accepted card is inert — which is the measured
	  end-state the redesign exists to fix. Bars everywhere, bounded by the
	  size of a store the user filled deliberately."""
	if not isinstance(tool, dict):
		return []
	reasons = []
	if tool.get("risk_level") == "elevated":
		reasons.append("elevated-risk")
	raw = tool.get("items")
	items = [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
	inputs = tool.get("bucket_inputs")
	has_security = bool(inputs.get("has_security")) if isinstance(inputs, dict) else False
	if has_security and any(isinstance(i.get("local"), dict)
			and i["local"].get("direction") == "reaches" for i in items):
		reasons.append("reaches-item")
	if has_watch_hit(items):
		reasons.append("watch-hit")
	return reasons


# ── which flags a checker may emit (`item-schema.md` §5.1) ──────────────────
#   A checker may emit a flag iff (a) it is a pure function of that checker's
#   own items, AND (b) it is not an input to an auto-approving decision.
#
# The four below pass both. They are assertions, not inputs: the validator
# recomputes each and its value wins, raising E-FLAG-DISAGREE on a mismatch. A
# disagreement is a strong signal that the checker's items do not say what it
# thinks they say — loud, and worth nothing as a data source.
CHECKER_FLAGS = ("has_security", "has_breaking", "worst_severity", "local_findings")

# These fail (b). `security_only` and `impact` gate `security_auto`, which
# pre-accepts; `risk_level` gates `pre_accept`; `review_bucket`/`pre_accept`
# are named outright by §C2. A checker emitting one raises E-FLAG-FORBIDDEN —
# criterion 2 made a runtime check instead of a review item.
VALIDATOR_ONLY_FLAGS = ("security_only", "impact", "risk_level", "review_bucket", "pre_accept")


def recompute_flags(items) -> dict:
	items = [i for i in (items or ()) if isinstance(i, dict)]
	def has(tag):
		return any(isinstance(i.get("tags"), list) and tag in i["tags"] for i in items)
	return {
		"has_security": has("security"),
		"has_breaking": has("breaking"),
		"worst_severity": worst_severity(items),
		"local_findings": sum(1 for i in items if isinstance(i.get("local"), dict)),
	}


# ── security_only (`item-schema.md` §5.6) ───────────────────────────────────
# The (category, severity) pair test becomes a tag/severity test. Every
# documented disqualification survives, and one misfile is fixed: codex's
# breaking change now disqualifies on its tag rather than on the accident of
# having been filed `notes`/`notable`.
SECURITY_ONLY_TAGS = frozenset({"security", "fix", "chore", "packaging"})


def allowed_for_security_only(item) -> bool:
	if not isinstance(item, dict):
		return False
	raw = item.get("tags")
	if not isinstance(raw, list) or not raw:
		return False
	# A tag written as a list or a dict is kept verbatim on the item, so
	# `set(tags)` would raise on an unhashable member. A non-string member
	# disqualifies — it is not evidence of harmlessness — but a *repeated*
	# string tag is not a defect and must not.
	if any(not isinstance(t, str) for t in raw):
		return False
	tags = set(raw)
	if not tags <= SECURITY_ONLY_TAGS:
		# feature / breaking / deprecation / perf disqualify, and so does an
		# unrecognized tag — an unknown tag is not evidence of harmlessness.
		return False
	if "security" in tags:
		return True  # any severity: a security item is a reason to TAKE the update
	return item.get("severity") in ("info", "notable")


# ── the finding codes ───────────────────────────────────────────────────────
# Every finding the deterministic layer can raise, in one enumerable place, so
# a consumer can render a legend and a reviewer can grep the set.
#
# `severity` here is the finding's own severity, not an item's:
#   error   — the output is out of spec; convergence must look
#   warning — worth saying, does not by itself mean the checker got it wrong
#
# NOTHING in this table licenses a deletion, a trim or a re-rating. Every stage
# reports and changes nothing beyond the shape normalizations §5.3 licenses.
FINDING_CODES = {
	# V1 — load (per file / per entry; never per run)
	"E-RESEARCH-UNREADABLE": ("error", None, "a research file could not be read"),
	"E-RESEARCH-NOTARRAY": ("error", None, "a research file is not a JSON array"),
	"E-ENTRY-NOTOBJECT": ("error", None, "a research entry is not an object"),
	"E-ENTRY-NOID": ("error", None, "a research entry has no usable string id"),
	"W-ENTRY-DUPLICATE": ("warning", None, "two research entries claim one tool id"),
	"W-ENTRY-UNMATCHED": ("warning", None, "a research entry names no collected candidate"),
	# V2 — spec validation
	"E-FIELD-MISSING": ("error", None, "a required field is absent"),
	"E-FIELD-TYPE": ("error", None, "a field has the wrong type"),
	"E-ENUM-INVALID": ("error", None, "a field's value is outside its closed vocabulary"),
	"E-CITE-KIND": ("error", None, "a citation kind is outside the vocabulary"),
	"E-CITE-EMPTY": ("error", None, "a citation has no text"),
	"E-CITE-URL": ("error", None, "a citation url is not an absolute http(s) URL"),
	# V3 — shape normalization
	"W-SHAPE-COERCED": ("warning", None, "a field was the wrong shape and was coerced"),
	"W-MEMBER-QUARANTINED": ("warning", None, "a wrong-typed array member was quarantined, not dropped"),
	# §L2 — the title/body split
	"W-TITLE-LONG": ("warning", None, "title is longer than the glanceable bar; detail belongs in body"),
	# V4 — the twenty invariants
	"E-ITEM-EMPTY": ("error", "I-1", "an item has neither `change` nor `local`"),
	"E-SEV-INCOMPAT-UNGROUNDED": ("error", "I-2", "`incompatible` without reaches+risk"),
	"E-SEV-WARNING-UNGROUNDED": ("error", "I-3", "`warning` without a `local` block"),
	"E-SEC-BLOCK-MISSING": ("error", "I-4", "tagged `security` with no `security` block"),
	"E-SEC-BLOCK-ORPHAN": ("error", "I-4", "`security` block on an item not tagged `security`"),
	"E-CVE-MALFORMED": ("error", "I-5", "`security.cve_id` is not a CVE id"),
	"E-SEC-RATING-UNBASED": ("error", "I-6", "a rating with no basis"),
	"E-CHANGE-UNCITED": ("error", "I-7", "`change` present with no verbatim citation"),
	"E-LINK-INDEX": ("error", "I-8", "`change.link_index` is out of range of `links[]`"),
	"E-ANCHOR-MALFORMED": ("error", "I-9", "`anchor.value` does not match its kind's grammar"),
	"E-ITEM-DUP-ANCHOR": ("error", "I-10", "two items in one tool derive one id"),
	"E-TAG-UNKNOWN": ("error", "I-11", "a tag outside the closed set (kept verbatim)"),
	"E-TAG-NONE": ("error", "I-11", "no recognized tag on the item"),
	"E-EVID-MALFORMED": ("error", "I-12", "an evidence entry is not a path"),
	"E-EVID-404": ("error", "I-12", "an evidence path resolves nowhere"),
	"W-EVID-ROOT": ("warning", "I-12", "an evidence path resolves only under an unconfigured repo"),
	"E-FLAG-DISAGREE": ("error", "I-13", "a checker-emitted flag disagrees with recomputation"),
	"E-REACHES-UNEVIDENCED": ("error", "I-14", "`direction: reaches` with no evidence"),
	"W-ATTENTION-NOSUG": ("warning", "I-15", "`config_needs_attention` with no action suggestion"),
	"E-STRUCT-PRECOND": ("error", "I-16", "a structural op's precondition does not hold"),
	"E-INTEL-BREWFILE": ("error", "I-17", "intel.Brewfile is out of this tool entirely"),
	"W-SUG-DUP-ID": ("warning", "I-18", "a suggestion id is not unique across the report"),
	"W-STRUCT-UNCHECKED": ("warning", "I-16", "a structural precondition could not be checked"),
	# I-19 — the self-test tag is load-bearing (REDESIGN.md L7): convergence
	# keys its review off it. A tag naming no reason is a drop with extra
	# steps, which is the one thing criterion 17 exists to prevent.
	"E-SELFTEST-NOREASON": ("error", "I-19", "a `self_test_failed` tag with no reason"),
	# I-20 — a watch-item hit names a stored watch item for this tool and says
	# what it means here. The checker authors `watch_hit`; the validator
	# grounds it against the session's watch-item snapshot. An unvalidated
	# channel is how the `Watch item hit:` literal died — worth 70 highlight
	# points and silently dead the moment it was paraphrased — so every
	# failure mode of the structured field reports.
	"E-WATCH-HIT-UNGROUNDED": ("error", "I-20", "`watch_hit.topic` names no stored watch item for this tool"),
	"E-WATCH-HIT-NOLOCAL": ("error", "I-20", "a watch-item hit with no `local` block"),
	"W-WATCH-UNCHECKED": ("warning", "I-20", "a watch-item hit could not be checked against the stored watch items"),
	"W-WATCH-HIT-UNRAISED": ("warning", "I-20", "a watch-item hit left at `info`; a watched topic earns at least `notable`"),
	# criterion 2 — no per-tool checker emits a bucket or an auto-approval
	"E-FLAG-FORBIDDEN": ("error", None, "a checker emitted a validator-only flag"),
	# U1 — the closed top-level key set. Content-losing: the value went into
	# quarantine[] instead of the report, so the tool is held for review.
	"E-RESEARCH-UNKNOWNKEY": ("error", None, "a research object carries a top-level key outside the closed set (kept in quarantine)"),
	# criterion 4 — degradation is per tool and loud. The next unknown shape
	# costs one tool and says so, rather than costing the run.
	"E-VALIDATOR-CRASH": ("error", None, "a validator stage failed on this tool; it is kept with what conformed"),
}


def finding_severity(code: str) -> str:
	entry = FINDING_CODES.get(code)
	return entry[0] if entry else "error"


def finding_sort_key(finding) -> tuple:
	"""Findings are ordered by tool, then item, then code, then field, then the
	offending value — total and input-independent, so two runs over one corpus
	produce byte-identical `validation.json`. Not by severity: a reader scans
	by subject, and a stable subject order is what makes two runs diffable."""
	def s(key):
		value = finding.get(key)
		return value if isinstance(value, str) else ""
	return (s("tool_id"), s("item_id"), s("code"), s("field"), s("value"))


# ── the machine-readable contract ───────────────────────────────────────────
# `contract/contract.json` is this dict, dumped. `test_items.py` asserts they
# are equal, so the fixture cannot go stale against the code — which is the
# only reason a published fixture is worth more than a paragraph.
ITEM_FIELDS = (
	# (name, type, required, note)
	("id", "string", "validator-assigned", "{tool_id}#{kind}:{urlencoded(value)}"),
	("id_stability", "enum", "validator-assigned", "anchored | slug"),
	("anchor", "object", "yes", "how the id is formed"),
	("anchor.kind", "enum", "yes", "|".join(ANCHOR_KINDS)),
	("anchor.value", "string", "yes unless kind == none", "grammar per kind"),
	("anchor.slug", "string", "yes iff kind == none", "free-form, not stable"),
	("title", "string", "yes", "readable at a glance; <= {} chars".format(TITLE_MAX_CHARS)),
	("body", "string|null", "no", "the detail: why/how. Overflow from title lands here"),
	("tags", "array<string>", "yes, len >= 1", "closed set of 8"),
	("severity", "enum", "yes", "|".join(SEVERITIES)),
	("change", "object|null", "no (I-1)", "the upstream fact"),
	("change.version", "string|null", "no", "release the change landed in"),
	("change.citation", "string", "yes if change present", "VERBATIM upstream text"),
	("change.link_index", "int|null", "no", "index into the tool's links[]"),
	("local", "object|null", "no (I-1)", "the finding about this setup"),
	("local.direction", "enum", "yes if local present", "|".join(DIRECTIONS)),
	("local.effect", "enum", "yes if local present", "|".join(EFFECTS)),
	("local.statement", "string", "yes if local present", "the finding in prose"),
	("local.evidence", "array<Evidence>", "yes if local present, may be []", "PATHS ONLY, objects"),
	("local.citations", "array<Citation>", "no, default []", "prose, commands, upstream refs"),
	("security", "object|null", "yes iff 'security' in tags", "per-item security detail"),
	("security.cve_id", "string|null", "no", "CVE-(19|20)dd-dddd+"),
	("security.advisory_id", "string|null", "no", "vendor id"),
	("security.rating", "enum", "yes if security present", "|".join(CVE_RATINGS)),
	("security.rating_basis", "enum", "yes if security present", "|".join(RATING_BASES)),
	("security.exploited_in_wild", "bool", "yes if security present", "vendor/CISA says so"),
	("watch_hit", "object|null", "no", "set iff this item answers a stored watch item for this tool"),
	("watch_hit.topic", "string", "yes if watch_hit present", "VERBATIM copy of the stored watch item's `topic`"),
)


def contract() -> dict:
	"""The whole contract as data. A sibling package that wants to assert
	against field names, vocabularies, the group mapping, the ordering spec or
	the finding codes imports this rather than re-deriving any of it."""
	return {
		"contract_version": CONTRACT_VERSION,
		"scope": {
			"items_are": "outward-facing changes only — project-internal maintenance "
				"(repo upkeep, convention changes, documentation updates) never becomes "
				"an item (REDESIGN.md L3). This is a scoping rule about what counts as "
				"an item, enforced in the schema and the agent guidelines; it is NOT a "
				"trimming rule and there is no filter for it in the validator.",
			"intel_brewfile": "out of this tool entirely (REDESIGN.md B1); I-17 checks it",
			"deterministic_layer": "validates, normalizes, counts, buckets, calculates "
				"impact. Never deletes, trims or re-rates.",
		},
		"vocabularies": {
			"tags": list(TAGS),
			"severities": list(SEVERITIES),
			"anchor_kinds": list(ANCHOR_KINDS),
			"directions": list(DIRECTIONS),
			"effects": list(EFFECTS),
			"citation_kinds": list(CITATION_KINDS),
			"cve_ratings": list(CVE_RATINGS),
			"rating_bases": list(RATING_BASES),
			"structural_ops": list(STRUCTURAL_OPS),
			"ref_types": list(REF_TYPES),
			"suggestion_kinds": list(SUGGESTION_KINDS),
			"self_test_limbs": list(SELF_TEST_LIMBS),
		},
		"memory_proposals": {
			"kinds": list(MEMORY_SUGGESTION_KINDS),
			"action_kinds": list(ACTION_SUGGESTION_KINDS),
			"bucket_rule": "memory proposals do not force `attention`; action "
				"proposals do. `watch-item` and `method-note` propose changes to what "
				"we remember, `edit` and `structural` to the user's system — only the "
				"latter needs a decision. Every axis that can pre-accept "
				"(compute_impact, compute_risk_level, compute_initial_bucket, "
				"W-ATTENTION-NOSUG) asks `needs_a_decision`, a NEGATION, so a kind "
				"outside the vocabulary demands a decision rather than reading as a "
				"memory proposal.",
			"payload": {kind: dict(fields)
				for kind, fields in sorted(MEMORY_PAYLOAD_FIELDS.items())},
			"self_test_failed": {
				"shape": {"limb": "|".join(SELF_TEST_LIMBS), "reason": "non-empty string"},
				"absent_means": "the proposal passed its self-test",
				"rule": "REDESIGN.md L7 — the self-test TAGS, never removes. A failing "
					"proposal is still written; convergence reviews every tagged one and "
					"verifies that dropping it is appropriate. A proposal the agent never "
					"writes is one convergence cannot restore.",
				"on_a_non_memory_kind": "E-FIELD-TYPE — the tag is present iff the kind "
					"is a memory kind",
				"with_no_reason": "E-SELFTEST-NOREASON",
			},
			"exported_for_convergence": "self_test_tagged_suggestion_ids, per tool",
		},
		"fields": [
			{"name": n, "type": t, "required": r, "note": note}
			for n, t, r, note in ITEM_FIELDS
		],
		"limits": {"title_max_chars": TITLE_MAX_CHARS},
		"groups": {
			"of_tag": dict(GROUP_OF_TAG),
			"precedence": list(GROUP_PRECEDENCE),
		},
		"ranks": {
			"severity": dict(SEVERITY_RANK),
			"cve_worse": dict(CVE_WORSE_RANK),
			"cve_order": dict(CVE_ORDER_RANK),
			"direction_order": dict(DIRECTION_ORDER),
			"effect_order": dict(EFFECT_ORDER),
			"no_local_rank": NO_LOCAL_RANK,
		},
		"ordering": {
			"item_sort_key": [
				"index of primary_group in groups.precedence",
				"negated severity rank (worst first; unknown severity last)",
				"direction rank (reaches, unclear, does_not_reach, no local block)",
				"effect rank (risk, none, benefit, no local block)",
				"id ascending — the tiebreak that makes the order total",
			],
			"security_display_sort_key": [
				"negated cve_order rating rank",
				"exploited_in_wild first",
				"direction rank",
				"negated severity rank",
				"id ascending",
			],
			"finding_sort_key": ["tool_id", "item_id", "code", "field", "value"],
		},
		"flags": {
			"checker_may_emit": list(CHECKER_FLAGS),
			"validator_only": list(VALIDATOR_ONLY_FLAGS),
			"on_disagreement": "the validator's value wins; E-FLAG-DISAGREE is raised",
		},
		"security_only_tags": sorted(SECURITY_ONLY_TAGS),
		"security_display": {
			"predicate": "rating == critical, or exploited_in_wild, or "
				"local.direction == reaches, or severity in (warning, incompatible)",
			"cap": None,
		},
		"findings": {
			code: {"severity": sev, "invariant": inv, "summary": summary}
			for code, (sev, inv, summary) in sorted(FINDING_CODES.items())
		},
		"anchor_grammars": dict(
			[(kind, pattern.pattern) for kind, pattern in sorted(ANCHOR_PATTERNS.items())]
			+ [("none", "value must be null; a non-empty `slug` is required instead")]),
		"evidence_shorthand": _EVIDENCE_SHORTHAND.pattern,
	}


# ── fixture access ──────────────────────────────────────────────────────────
CONTRACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract")


def fixture_path(name: str) -> str:
	"""Absolute path to a published fixture. See `contract/README.md`."""
	return os.path.join(CONTRACT_DIR, name)


def load_fixture(name: str):
	with open(fixture_path(name), "r", encoding="utf-8") as fh:
		return json.load(fh)
