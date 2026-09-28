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

import copy
import json
import os
import re
from urllib.parse import quote, urlsplit

# Bumped when a consumer would have to change. Consumers assert EQUALITY and
# refuse on mismatch — there is no migration shim and none ships
# (`REDESIGN.md` §I9). A consumer built for a lower version would silently
# drop fields and trust a `pre_accept` computed under a different predicate;
# one built for a higher version would present output produced before a
# defect class was closed as though it were not.
#
# 2 — WP2 admitted memory proposals: the `method-note` suggestion kind, the
#     `self_test_failed` tag (`REDESIGN.md` §L7), and the rule that a memory
#     proposal never forces a tool onto the attention list.
# 3 — D1–D4: `watch_hit` on the item (grounded against a per-session
#     watch-items snapshot), the closed top-level research-key set and
#     E-RESEARCH-UNKNOWNKEY, the `degradation` block on every tool, and the
#     fail-closed bucket/pre-accept precedence (content-losing input,
#     elevated risk, reaching security changes and watch hits can no longer
#     be pre-accepted).
# 4 — G-SEC: `security.nature`, suggestion `requirement`/`serves`, evidence
#     `role`/`quote` and the recorded `usage_evidence`, `config_status.state`
#     validated, I-21–I-23, `security_tier` on view and Tool, the
#     `enum-invalid`/`container-unreadable` bars, and the rewritten bucket
#     clause and pre-accept predicate (positively identified security fixes
#     are accepted by tier).
CONTRACT_VERSION = 4


# ── vocabularies ────────────────────────────────────────────────────────────
# All closed. Closed means the validator recognizes exactly these; an
# unrecognized value is KEPT verbatim on the item and reported, never dropped
# and never coerced to a neighbour (`item-schema.md` §2.3).

# The two finding sources — collect.json candidates with no version delta, so
# no upgrade to accept (assembly's NON_VERSION_SOURCES is this set). The
# security tier never applies to them.
NON_VERSION_SOURCES = ("brew-health", "skill-drift")

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

# G-SEC (CONTRACT 4). The `security` tag covers two different facts — a fixed
# vulnerability and a moved security boundary — so the tag alone is not "a
# fix". `security.nature` says which, and only a GROUNDED `fix` (I-21) makes a
# tool G-SEC. `unclear` is the honest answer when the checker cannot tell.
SECURITY_NATURES = ("fix", "boundary", "unclear")
# On an ACTION suggestion: `required` — the upgrade does not work here without
# this edit (I-22 grounds it against an `incompatible` item it `serves`);
# `proposed` — worth doing, not needed for the upgrade to work. Absent reads
# `proposed`: the user's stated default, "upgrade only by default".
SUGGESTION_REQUIREMENTS = ("required", "proposed")
# On an object-form `local.evidence[]` entry: `usage` — this setup USES the
# affected thing (a `quote` of the line that shows it is required, and I-23
# grounds it in the file); `install` — the tool is installed (the Brewfile
# line, the install task); `reference` — anything else worth pointing at.
EVIDENCE_ROLES = ("usage", "install", "reference")
# `config_status.state` — the vocabulary `references/research.md` §Config
# Status always documented, validated since CONTRACT 4.
CONFIG_STATES = ("up_to_date", "needs_attention", "unknown")

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
	"""Does any item CLAIM to answer a stored watch item?

	This is the pre-acceptance bar's predicate and only that. A dict is a
	claim even when malformed or ungrounded: a bad one is reported
	(E-FIELD-MISSING / E-FIELD-TYPE / E-WATCH-HIT-UNGROUNDED) and the claim
	still bars, which is the fail-closed direction — holding a tool on an
	unverified claim costs a click; auto-accepting past one is the defect
	class this redesign exists for.

	Prominence is the opposite trade. The 70-point `watch_item_hit`
	highlight reads the view's `watch_hit_item_ids` — `grounded_watch_hit`
	below — never this predicate, and so does the item badge (the page's
	`⚑ watch hit` chip, which reads the same export): scoring
	an unverified claim would let a paraphrased or invented topic displace a
	genuinely scoring tool from the capped highlight list, which is precisely
	the unvalidated-channel failure the structured field was introduced to
	kill."""
	return any(isinstance(i.get("watch_hit"), dict)
		for i in items or () if isinstance(i, dict))


def grounded_watch_hit(item, watch_topics) -> bool:
	"""True iff the item carries a well-formed `watch_hit` whose topic is in
	the stored topic set for this tool.

	`watch_topics` is None when the session supplied no snapshot; nothing is
	grounded then — an unchecked hit is kept and BARS pre-acceptance
	(fail-closed, `has_watch_hit`), but earns no prominence, because
	prominence for an unverifiable claim is the channel the regex died of.
	The validator exports the grounded ids per view as `watch_hit_item_ids`;
	the highlight and the page's `⚑ watch hit` item badge both read that
	export, never the raw claim."""
	if not isinstance(item, dict) or watch_topics is None:
		return False
	hit = item.get("watch_hit")
	if not isinstance(hit, dict):
		return False
	topic = hit.get("topic")
	return isinstance(topic, str) and topic.strip() in watch_topics


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


# ── G-SEC: the security tier (CONTRACT 4) ───────────────────────────────────
# `REDESIGN.md` §F criterion 24, in the user's words: a security fix REACHING
# this machine is a good thing and is accepted by default; one whose upgrade
# needs a config change, or that is confirmed relevant to actual usage, is
# shown first — in that order — in addition to being accepted; one whose
# upgrade does not work here without an edit is not accepted at all and is the
# highest priority there is.
#
# Two AXES, kept apart on purpose (orchestrator reading O2, applied at every
# level — R1): display PRIORITY ("which fixes matter") and the acceptance HOLD
# ("why it is not taken"). A held tool keeps its priority: held means not
# accepted, never hidden. `tier` is the §7.28 row both combine into.
#
# The tier is computed ONCE per view, here, from fields a validation view and
# an assembled Tool both carry. The validator stores it; assembly copies it;
# convergence recomputes it through this same function after every edit; the
# page reads it. Nothing re-derives it.
SECURITY_TIERS = ("P0", "held", "P1", "P2", "P3")   # display order
SECURITY_PRIORITIES = ("P0", "P1", "P2", "P3")
# The prominence order convergence's demotion gate reads (§12 A-R3-2): any
# STRICT decrease in this rank is a demotion. Not G-SEC ranks 0.
PRIORITY_RANK = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
ACCEPTED_TIERS = ("P1", "P2", "P3")
HIGHLIGHT_PRIORITIES = ("P0", "P1", "P2")

# Every priority reason, in its fixed emission order, with its level. Closed
# and published, like DEGRADATION_REASONS, so the page's chips never reshuffle.
TIER_REASON_LEVELS = (
	("required-edit", "P0"),         # an action suggestion reads `required` (I-22)
	("pinned", "P0"),                # the tool is pinned; the fix cannot land
	("incompatible-unfixed", "P0"),  # an `incompatible` item no REQUIRED edit serves
	("edit-proposed", "P1"),         # an action suggestion reads `proposed`
	("config-attention", "P1"),      # needs_attention with NO action suggestion (§7.29)
	("relevant-fix", "P2"),          # a positive fix confirmed against actual usage (I-23)
	("fix-with-breaking", "P2"),     # any `breaking`-tagged item, any severity (R4)
	("fix-with-risk", "P2"),         # a NON-security item with effect == risk (O1)
	("vendor-unread", "P2"),         # vendor_silent_categories ∋ "security"
	("fix", "P3"),                   # a positive fix — always present when one exists
)
TIER_REASONS = tuple(code for code, _ in TIER_REASON_LEVELS)
TIER_REASON_LEVEL = dict(TIER_REASON_LEVELS)

# Every acceptance hold, fixed order. Holds and the P0 reasons are both "not
# accepted"; `pre_accept_bars` on a G-SEC view is their union.
TIER_HOLDS = (
	"content-losing",        # D1 — content_losing(view) is non-empty
	"security-item-risk",    # a security item (tag or block) with effect == risk (§7.27)
	"watch-hit",             # E3 — any item CLAIMS a watch hit (claim rule, fail-closed)
	"enum-invalid",          # a tier-input enum present and unreadable (R7: every tool)
	"container-unreadable",  # an item/suggestion container of the wrong type (R7)
	"research-incomplete",   # R3 — the checker reported research_error
	"not-runnable",          # R3 — no runnable upgrade; "accepted" would claim a run
	"forced-conservative",   # R3 — written by the applier at a failed terminal gate
	"tier-uncomputed",       # the view default; observed only if the tier never ran
)
# Reasons and holds that belong to the tool rather than to an element, so their
# `ids` entry is always [].
TOOL_LEVEL_TIER_CODES = ("pinned", "vendor-unread", "config-attention",
	"content-losing", "research-incomplete", "not-runnable", "forced-conservative",
	"tier-uncomputed")

# Text + glyph, never colour alone. The page's copy is pinned against this by
# test_render.py, so the two cannot drift.
TIER_LABELS = {
	"required-edit": {"glyph": "⛔",
		"text": "Needs you — a config change is required before this upgrade works"},
	"pinned": {"glyph": "⛔", "text": "Needs you — pinned; lift the pin to take the fix"},
	"incompatible-unfixed": {"glyph": "⛔",
		"text": "Needs you — breaks something here; no config fix proposed"},
	"edit-proposed": {"glyph": "⚙",
		"text": "Accepted — config edits proposed, not applied unless you accept them"},
	"config-attention": {"glyph": "⚙",
		"text": "Accepted — config needs attention — no edit proposed"},
	"relevant-fix": {"glyph": "◎", "text": "Accepted — the fix touches how you use it"},
	"fix-with-breaking": {"glyph": "◎",
		"text": "Accepted — also has a breaking change: take a quick look"},
	"fix-with-risk": {"glyph": "◎", "text": "Accepted — also carries a risk here"},
	"vendor-unread": {"glyph": "◎",
		"text": "Accepted — vendor declares a security release without details"},
	"fix": {"glyph": "·", "text": "Accepted — a security fix, nothing flagged for this setup"},
}

# The view default (§4.2): what a view carries until the tier function has run
# on its final state. Valid by `valid_security_tier` — a held P3 — so a view
# whose tier was never computed is never accepted and never highlighted.
TIER_UNCOMPUTED = {"tier": "held", "priority": "P3", "reasons": [],
	"holds": ["tier-uncomputed"], "ids": {"tier-uncomputed": []}, "fix_item_ids": []}

# I-23's install-declaration test, AS DATA: a pattern per file kind with a
# `{name}` slot filled with the tool's (regex-escaped) name, and — for TOML —
# the section it applies in. A usage quote whose every occurrence lies on one
# of these (or on a comment/blank line) shows the tool is INSTALLED, not USED.
# `section`: None = anywhere in the file; "tools" = under `[tools]` or
# `[tools.<x>]`; "top" = before any table header.
INSTALL_DECLARATION_PATTERNS = (
	{"kind": "brewfile", "section": None,
		"pattern": r'^\s*(?:brew|cask|tap|mas)\s+"(?:[^"/\s]+/[^"/\s]+/)?{name}"'},
	{"kind": "tool-versions", "section": None, "pattern": r"^\s*{name}\s+\S"},
	{"kind": "mise-toml", "section": "tools",
		"pattern": r'^\s*"?(?:[A-Za-z0-9_-]+:)?{name}(?:@[^"\s=]*)?"?\s*='},
	{"kind": "mise-toml", "section": "tools.{name}", "pattern": r"^\s*\S"},
	{"kind": "mise-toml", "section": None,
		"pattern": r"^\s*\[\s*tools\.\"?{name}\"?\s*\]"},
	{"kind": "mise-toml", "section": "top",
		"pattern": r'^\s*tools\.\"?{name}\"?\s*='},
	{"kind": "shell", "section": None,
		"pattern": r"\bbrew\s+(?:install|reinstall|upgrade)\b[^#\n]*?(?<![\w./@-]){name}(?![\w.-])"},
	{"kind": "shell", "section": None,
		"pattern": r"\bmise\s+(?:use|install)\b[^#\n]*?(?<![\w./@:-]){name}(?:@\S*)?(?![\w.-])"},
)
# The file kinds I-23 classifies a matched line with, from the path.
MISE_TOML_NAMES = ("mise.toml", ".mise.toml", "mise.local.toml")
# The size cap on a file I-23 reads to ground a usage quote. Over it, the entry
# is E-USAGE-UNGROUNDED ("unreadable"), never a crash and never a partial read.
USAGE_FILE_MAX_BYTES = 1_000_000


def usage_file_kind(path) -> str:
	"""brewfile | tool-versions | mise-toml | shell | other, from the path's
	basename (and, for `mise/config.toml`, its parent directory)."""
	if not isinstance(path, str):
		return "other"
	norm = path.replace("\\", "/").rstrip("/")
	base = norm.rsplit("/", 1)[-1]
	parent = norm.rsplit("/", 2)[-2] if norm.count("/") >= 1 else ""
	if base == "Brewfile":
		return "brewfile"
	if base == ".tool-versions":
		return "tool-versions"
	if base in MISE_TOML_NAMES or (base == "config.toml" and parent == "mise"):
		return "mise-toml"
	if base.endswith(".sh") or base.startswith(".bash"):
		return "shell"
	return "other"


def _items_of(view) -> list:
	raw = view.get("items") if isinstance(view, dict) else None
	return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def _suggestions_of(view) -> list:
	raw = view.get("suggestions") if isinstance(view, dict) else None
	return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def _kind_of(sug) -> object:
	"""`kind` is omittable and defaults to "edit" — assemble.suggestion_kind's
	rule, spelled here because this module may not import assembly."""
	return sug.get("kind") or "edit"


def _is_action(sug) -> bool:
	return isinstance(sug, dict) and needs_a_decision(_kind_of(sug))


def _str_id(element) -> str:
	value = element.get("id") if isinstance(element, dict) else None
	return value if isinstance(value, str) else ""


def _sug_ref(sug, tool_id) -> str:
	sid = _str_id(sug)
	return sid or "{}:<no id>".format(tool_id)


def _local_field(item, field):
	local = item.get("local") if isinstance(item, dict) else None
	return local.get(field) if isinstance(local, dict) else None


def is_security_content(item) -> bool:
	"""Security content: the `security` tag OR a `security` block — I-4's
	orphan case errs toward security here, as it does everywhere."""
	if not isinstance(item, dict):
		return False
	tags = item.get("tags")
	return bool((isinstance(tags, list) and "security" in tags)
		or isinstance(item.get("security"), dict))


def is_positive_fix(item) -> bool:
	"""The one predicate for "a positively identified security fix" (I-21).

	`security.nature == "fix"` counts only when GROUNDED: the item is tagged
	`security`, carries a `security` object (an orphan block is not a fix),
	and has a `change` with a non-empty verbatim `citation` — a fix is an
	upstream fact, so it is cited from upstream. An ungrounded `fix` is a
	PROMOTING claim, so unverified it counts for nothing (E-SEC-FIX-UNGROUNDED
	says why). A pure function of the item, so convergence recomputes it."""
	if not isinstance(item, dict):
		return False
	tags = item.get("tags")
	security = item.get("security")
	change = item.get("change")
	if not (isinstance(tags, list) and "security" in tags and isinstance(security, dict)):
		return False
	if security.get("nature") != "fix":
		return False
	citation = change.get("citation") if isinstance(change, dict) else None
	return isinstance(citation, str) and bool(citation.strip())


def suggestion_requirement(sug, items_by_id) -> str:
	"""→ "required" | "proposed" for an action suggestion, read against the
	tool's CURRENT items (`items_by_id`) — convergence can rerate, merge or
	delete the items a suggestion serves, so the reading is never a
	validator-time flag.

	  * `requirement` absent → proposed (the user's default).
	  * out of vocabulary / not a string → REQUIRED — the worst reading of an
	    unreadable restricting claim (the `enum-invalid` hold fires as well).
	  * `required` → required, grounded or not: a restricting claim stands
	    unverified (E-SUG-REQUIRED-UNGROUNDED reports it; holding a fix costs a
	    click, auto-accepting past "this breaks without the edit" is the
	    defect class).
	  * `proposed` → required iff an id in `serves_item_ids` names a CURRENT
	    item whose severity is `incompatible` — an edit that answers something
	    the upgrade breaks is required by definition (the contradiction,
	    recomputed; E-REQUIREMENT-CONTRADICTED is its finding). An id naming
	    no current item links nothing."""
	if not isinstance(sug, dict):
		return "proposed"
	declared = sug.get("requirement")
	if declared is None:
		declared = "proposed"
	elif not (isinstance(declared, str) and declared in SUGGESTION_REQUIREMENTS):
		return "required"
	if declared == "required":
		return "required"
	served = sug.get("serves_item_ids")
	for item_id in served if isinstance(served, list) else ():
		item = items_by_id.get(item_id) if isinstance(item_id, str) else None
		if isinstance(item, dict) and item.get("severity") == "incompatible":
			return "required"
	return "proposed"


def _items_by_id(items) -> dict:
	return {i["id"]: i for i in items if isinstance(i.get("id"), str)}


def required_edit_items(view) -> list:
	"""The ids of CURRENT items served by an action suggestion that reads
	`required` — the incompatible items a proposed fix exists for. An
	incompatible item outside this set is P0 `incompatible-unfixed`."""
	items = _items_of(view)
	by_id = _items_by_id(items)
	served = set()
	for sug in _suggestions_of(view):
		if not _is_action(sug) or suggestion_requirement(sug, by_id) != "required":
			continue
		ids = sug.get("serves_item_ids")
		for item_id in ids if isinstance(ids, list) else ():
			if isinstance(item_id, str) and item_id in by_id:
				served.add(item_id)
	return [i["id"] for i in items if _str_id(i) in served]


def _usage_keys(usage_evidence) -> list:
	"""The grounded entries of a view's `usage_evidence` record. Hostile input
	grounds nothing."""
	if not isinstance(usage_evidence, list):
		return []
	return [r.get("entry") for r in usage_evidence
		if isinstance(r, dict) and isinstance(r.get("entry"), dict)]


def usage_confirmed(item, usage_evidence) -> bool:
	"""Is this item confirmed against actual usage (I-23)?

	`local.direction == "reaches"` and at least one of its `role: "usage"`
	evidence entries is, as authored (after the validator's shape
	normalization), a member of the tool's `usage_evidence` — the record the
	stage-3 validator wrote when it grounded that entry in the file. Keyed on
	the AUTHORED entry (§12 A-R3-1), not on the matched occurrence, so an entry
	with no `lines`, or a wider range than the match, is the same entry after
	convergence carries it. Pure: no file is read here, ever — convergence
	recomputes this with the record, and a forged entry matches nothing."""
	if not isinstance(item, dict) or _local_field(item, "direction") != "reaches":
		return False
	evidence = _local_field(item, "evidence")
	if not isinstance(evidence, list):
		return False
	keys = _usage_keys(usage_evidence)
	return any(isinstance(entry, dict) and entry.get("role") == "usage" and entry in keys
		for entry in evidence)


def usage_item_ids(view) -> list:
	"""The view's usage-confirmed item ids, like `watch_hit_item_ids`."""
	record = view.get("usage_evidence") if isinstance(view, dict) else None
	return [i["id"] for i in _items_of(view)
		if isinstance(i.get("id"), str) and usage_confirmed(i, record)]


def _bad_enum(value, vocabulary) -> bool:
	"""Present and unreadable: not None, and not a string in the vocabulary.
	Absent is E-FIELD-MISSING's business and makes no claim."""
	return value is not None and not (isinstance(value, str) and value in vocabulary)


def _enum_invalid(view):
	"""→ (fires, element ids). Every tier-input enum present and unreadable:
	severity, local.direction, local.effect, security.nature, evidence `role`,
	suggestion `kind` and (on an action suggestion) `requirement`, and
	config_status.state (tool-level — it fires with no element id)."""
	fires, ids = False, []
	tool_id = view.get("id") if isinstance(view, dict) else None
	for item in _items_of(view):
		bad = _bad_enum(item.get("severity"), SEVERITIES)
		local = item.get("local")
		if isinstance(local, dict):
			bad = (bad or _bad_enum(local.get("direction"), DIRECTIONS)
				or _bad_enum(local.get("effect"), EFFECTS))
			evidence = local.get("evidence")
			bad = bad or any(isinstance(e, dict) and _bad_enum(e.get("role"), EVIDENCE_ROLES)
				for e in (evidence if isinstance(evidence, list) else ()))
		security = item.get("security")
		if isinstance(security, dict):
			bad = bad or _bad_enum(security.get("nature"), SECURITY_NATURES)
		if bad:
			fires = True
			ids.append(_str_id(item))
	for sug in _suggestions_of(view):
		kind = _kind_of(sug)
		bad = not (isinstance(kind, str) and kind in SUGGESTION_KINDS)
		if _is_action(sug):
			bad = bad or _bad_enum(sug.get("requirement"), SUGGESTION_REQUIREMENTS)
		if bad:
			fires = True
			ids.append(_sug_ref(sug, tool_id))
	status = view.get("config_status") if isinstance(view, dict) else None
	if isinstance(status, dict) and _bad_enum(status.get("state"), CONFIG_STATES):
		fires = True
	return fires, ids


def enum_invalid(view) -> bool:
	return _enum_invalid(view)[0]


ITEM_CONTAINERS = (("local", dict), ("security", dict), ("change", dict),
	("watch_hit", dict), ("tags", list))


def _container_unreadable(view):
	"""→ (fires, element ids). An item's `local`/`security`/`change`/
	`watch_hit` present and not an object, `tags` present and not an array, or
	an action suggestion's `serves` present and not an array. The adversarial
	case: `local: "risk"` used to read as an item with no local claim at all."""
	fires, ids = False, []
	tool_id = view.get("id") if isinstance(view, dict) else None
	for item in _items_of(view):
		if any(item.get(field) is not None and not isinstance(item.get(field), shape)
				for field, shape in ITEM_CONTAINERS):
			fires = True
			ids.append(_str_id(item))
	for sug in _suggestions_of(view):
		serves = sug.get("serves")
		if _is_action(sug) and serves is not None and not isinstance(serves, list):
			fires = True
			ids.append(_sug_ref(sug, tool_id))
	return fires, ids


def container_unreadable(view) -> bool:
	return _container_unreadable(view)[0]


def derive_tier(priority, holds) -> str:
	"""The §7.28 row: P0 wherever the priority is P0 (a P0 tool is never
	accepted, held or not); else `held` if any hold applies; else the
	priority."""
	if priority == "P0":
		return "P0"
	return "held" if holds else priority


def security_tier(view):
	"""→ None (not G-SEC) or the tier object (§3.2 of the pass-4b plan).

	A version-source tool is G-SEC iff at least one item is a positive fix
	(`is_positive_fix`) or its vendor declares an unread security release.
	Every other tool has no tier and is judged by the pre-G-SEC rules
	(`pre_accept_bars`' non-G-SEC branch), plus the two universal bars.

	Reads only fields a validation view and an assembled Tool both carry.
	NEVER RAISES: every read is guarded (fuzz asserts it), because this runs
	in the validator's final act outside every guarded stage."""
	if not isinstance(view, dict) or view.get("source") in NON_VERSION_SOURCES:
		return None
	items = _items_of(view)
	fixes = [i for i in items if is_positive_fix(i)]
	silent = view.get("vendor_silent_categories")
	vendor_unread = isinstance(silent, list) and any(c == "security" for c in silent)
	if not fixes and not vendor_unread:
		return None
	tool_id = view.get("id") if isinstance(view.get("id"), str) else ""
	by_id = _items_by_id(items)
	usage = view.get("usage_evidence")
	found = {}

	actions = [s for s in _suggestions_of(view) if _is_action(s)]
	required = [s for s in actions if suggestion_requirement(s, by_id) == "required"]
	proposed = [s for s in actions if suggestion_requirement(s, by_id) == "proposed"]
	if required:
		found["required-edit"] = [_sug_ref(s, tool_id) for s in required]
	if view.get("pinned"):
		found["pinned"] = []
	served = set(required_edit_items(view))
	unfixed = [_str_id(i) for i in items
		if i.get("severity") == "incompatible" and _str_id(i) not in served]
	if unfixed:
		found["incompatible-unfixed"] = unfixed
	if proposed:
		found["edit-proposed"] = [_sug_ref(s, tool_id) for s in proposed]
	status = view.get("config_status")
	if (isinstance(status, dict) and status.get("state") == "needs_attention"
			and not actions):
		found["config-attention"] = []
	relevant = [_str_id(i) for i in fixes if usage_confirmed(i, usage)
		and _local_field(i, "effect") in ("benefit", "none")]
	if relevant:
		found["relevant-fix"] = relevant
	breaking = [_str_id(i) for i in items
		if isinstance(i.get("tags"), list) and "breaking" in i["tags"]]
	if breaking:
		found["fix-with-breaking"] = breaking
	risky = [_str_id(i) for i in items
		if not is_security_content(i) and _local_field(i, "effect") == "risk"]
	if risky:
		found["fix-with-risk"] = risky
	if vendor_unread:
		found["vendor-unread"] = []
	if fixes:
		found["fix"] = [_str_id(i) for i in fixes]

	held = {}
	if content_losing(view):
		held["content-losing"] = []
	security_risk = [_str_id(i) for i in items
		if is_security_content(i) and _local_field(i, "effect") == "risk"]
	if security_risk:
		held["security-item-risk"] = security_risk
	watched = [_str_id(i) for i in items if isinstance(i.get("watch_hit"), dict)]
	if watched:
		held["watch-hit"] = watched
	fires, ids = _enum_invalid(view)
	if fires:
		held["enum-invalid"] = ids
	fires, ids = _container_unreadable(view)
	if fires:
		held["container-unreadable"] = ids
	if view.get("research_error"):
		held["research-incomplete"] = []
	inputs = view.get("bucket_inputs")
	if not (isinstance(inputs, dict) and inputs.get("runnable") is True):
		held["not-runnable"] = []
	if view.get("forced_conservative"):
		held["forced-conservative"] = []

	reasons = [code for code in TIER_REASONS if code in found]
	holds = [code for code in TIER_HOLDS if code in held]
	priority = next(level for level in SECURITY_PRIORITIES
		if any(TIER_REASON_LEVEL[r] == level for r in reasons))
	ids = {code: found[code] for code in reasons}
	ids.update({code: held[code] for code in holds})
	return {
		"tier": derive_tier(priority, holds),
		"priority": priority,
		"reasons": reasons,
		"holds": holds,
		"ids": ids,
		"fix_item_ids": [_str_id(i) for i in fixes],
	}


def _ordered_subset(value, vocabulary) -> bool:
	if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
		return False
	if not all(v in vocabulary for v in value):
		return False
	positions = [vocabulary.index(v) for v in value]
	return positions == sorted(set(positions))


def valid_security_tier(obj) -> bool:
	"""Vocabulary membership of every field, the fixed orders, `ids` covering
	exactly the reasons and holds, the priority equal to the highest reason
	level (P3 when there is none — the uncomputed default), and `tier ==
	derive_tier(priority, holds)`. `{}`, a list, a bare "P2", or an internally
	inconsistent object are all invalid — and an invalid tier is never
	accepted (`accepts_baseline`) and bars as `tier-uncomputed`."""
	if not isinstance(obj, dict) or set(obj) != {"tier", "priority", "reasons",
			"holds", "ids", "fix_item_ids"}:
		return False
	tier, priority = obj["tier"], obj["priority"]
	reasons, holds, ids = obj["reasons"], obj["holds"], obj["ids"]
	if not (isinstance(priority, str) and priority in SECURITY_PRIORITIES):
		return False
	if not (isinstance(tier, str) and tier in SECURITY_TIERS):
		return False
	if not _ordered_subset(reasons, TIER_REASONS) or not _ordered_subset(holds, TIER_HOLDS):
		return False
	expected = next((level for level in SECURITY_PRIORITIES
		if any(TIER_REASON_LEVEL[r] == level for r in reasons)), "P3")
	if priority != expected or tier != derive_tier(priority, holds):
		return False
	if not isinstance(ids, dict) or set(ids) != set(reasons) | set(holds):
		return False
	if not all(isinstance(v, list) and all(isinstance(x, str) for x in v)
			for v in ids.values()):
		return False
	fixes = obj["fix_item_ids"]
	return isinstance(fixes, list) and all(isinstance(x, str) for x in fixes)


def security_priority(x):
	"""The one accessor for display priority on a view or a Tool:
	`security_tier.priority` when the tier is valid, else None (not G-SEC, or
	malformed). Convergence's attribution, its demotion gate, the effect
	block and the summary counts all read priority through this."""
	tier = x.get("security_tier") if isinstance(x, dict) else None
	return tier["priority"] if valid_security_tier(tier) else None


def priority_rank(priority) -> int:
	"""P0 4 · P1 3 · P2 2 · P3 1 · anything else (not G-SEC) 0."""
	return PRIORITY_RANK.get(priority, 0) if isinstance(priority, str) else 0


# ── the pre-acceptance bar (D2, E3, G-SEC) ──────────────────────────────────
# Emission order, fixed, like DEGRADATION_REASONS and for the same reason. The
# first twelve are a G-SEC view's (P0 reasons ∪ holds); the last two are
# emitted only for a tool that is NOT G-SEC; `watch-hit`, `enum-invalid` and
# `container-unreadable` apply to both.
PRE_ACCEPT_BARS = ("required-edit", "pinned", "incompatible-unfixed",
	"content-losing", "security-item-risk", "watch-hit", "enum-invalid",
	"container-unreadable", "research-incomplete", "not-runnable",
	"forced-conservative", "tier-uncomputed", "elevated-risk", "reaches-item")


def pre_accept_bars(tool) -> list:
	"""→ the ordered reasons this tool may not start accepted, or [].

	Reads a validation view and an assembled Tool identically, like
	`content_losing`. The bucket's clause 2 and `accepts_baseline` read the
	value the validator STORED on the view (`view["pre_accept_bars"]`), and
	assembly copies it — nothing recomputes it from assembled items, which
	can hold synthesized reaching security items the bucket never saw.

	**A G-SEC tool** (`security_tier` present): the tier's P0 reasons and its
	holds, read off the stored tier — never recomputed here. A malformed tier
	bars as `tier-uncomputed` (fail-closed). D2's elevated bar and the
	reaches limb do NOT apply: a positively identified fix is accepted by
	tier, and the priority panel is the visibility D2 asked for (§7.27).

	**Every other tool** — no security content, or security content that is
	not a positively identified fix — keeps the pre-G-SEC bars:

	- ``elevated-risk`` — D2: elevated risk bars pre-acceptance. On its own it
	  is an unstable proxy: `brew:duckdb` came out `elevated` in one recorded
	  run and `low` in the other for the identical upgrade, because the axis
	  moves with the checker's wording. Hence the next limb.
	- ``reaches-item`` — D2, narrowed to the measured case: an item that
	  ITSELF carries security content (the `security` tag, or a `security`
	  block) with `local.direction == "reaches"`. `elevated` alone misses
	  `cask:wireshark-app` (28 advisories, a `sharkd` flaw reachable from the
	  shell PATH). Do NOT re-widen it — not to any reaching item, and not to
	  any reaching item on a security tool: the measurement that justified it
	  was taken on security items only, and a reaching *feature* item would
	  hold tools like `brew:openssh` undecided for a change that is a reason
	  to take the update — a regression against a redesign that exists because
	  the old report cost 919 words per decision.
	- ``watch-hit`` — E3: a watch item exists to be told about, and one that
	  fires into an auto-accepted card is inert. The CLAIM bars (fail-closed).
	- ``enum-invalid`` — universal (R7), the renamed and widened
	  `local-enum-invalid`: any tier-input enum present and outside its
	  vocabulary (`_enum_invalid`). One character of drift ("reachs") on a
	  real CVE item used to remove the bar AND the impact signal at once;
	  an unverifiable claim fails closed here as everywhere — holding costs a
	  click.
	- ``container-unreadable`` — universal (R7): an item container of the
	  wrong type (`local: "risk"` read as no local claim at all) is a claim in
	  a shape nothing can read."""
	if not isinstance(tool, dict):
		return []
	tier = tool.get("security_tier")
	if tier is not None:
		if not valid_security_tier(tier):
			return ["tier-uncomputed"]
		found = {r for r in tier["reasons"] if TIER_REASON_LEVEL[r] == "P0"}
		found.update(tier["holds"])
		return [bar for bar in PRE_ACCEPT_BARS if bar in found]
	found = set()
	if tool.get("risk_level") == "elevated":
		found.add("elevated-risk")
	items = _items_of(tool)
	if any(_local_field(i, "direction") == "reaches" and is_security_content(i)
			for i in items):
		found.add("reaches-item")
	if has_watch_hit(items):
		found.add("watch-hit")
	if enum_invalid(tool):
		found.add("enum-invalid")
	if container_unreadable(tool):
		found.add("container-unreadable")
	return [bar for bar in PRE_ACCEPT_BARS if bar in found]


def accepts_baseline(x) -> bool:
	"""THE pre-acceptance predicate for a tool's baseline upgrade — one
	normalized input contract, read identically off a validation view and an
	assembled Tool. `assemble.apply_pre_accept` and
	`converge.initial_pre_accept` both call it, so the bucket, the checkbox and
	convergence's differential recomputation cannot tell two stories.

	Inputs, bracket access: `security_tier` (the key must be present — null
	or an object), `pre_accept_bars` (must be present — the EXPORTED bars,
	never recomputed), the bucket (`review_bucket` on a Tool,
	`initial_review_bucket` on a view), `risk_level`; `forced_conservative`
	by `.get`. A missing key raises KeyError (the assembly discipline); the
	convergence caller maps that to False.

	`None` and malformed are distinguished explicitly: `{}` is falsy and must
	never take the non-security branch."""
	tier = x["security_tier"]
	bars = x["pre_accept_bars"]
	if not isinstance(bars, list) or bars:
		return False
	if tier is None:
		bucket = x["review_bucket"] if "review_bucket" in x else x["initial_review_bucket"]
		return bool(bucket != "attention" and x["risk_level"] == "low"
			and not content_losing(x) and not x.get("forced_conservative"))
	if not valid_security_tier(tier):
		return False
	return bool(tier["tier"] in ACCEPTED_TIERS and not content_losing(x)
		and not x.get("forced_conservative"))


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
	# V4 — the twenty-three invariants
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
	# I-21 … I-23 — G-SEC (CONTRACT 4). Each checker-declared G-SEC field is
	# grounded against something the validator can check, and every failure
	# mode reports. None is content-losing; all are card markers. A PROMOTING
	# claim (`fix`, `usage`) counts for nothing unverified; a RESTRICTING one
	# (`required`) stands unverified.
	"E-SEC-FIX-UNGROUNDED": ("error", "I-21", "`security.nature: fix` without the `security` tag, a `security` block and a cited `change`"),
	"W-SEC-FIX-NOID": ("warning", "I-21", "a grounded fix with no CVE, advisory or anchored id"),
	"E-SUG-REQUIRED-UNGROUNDED": ("error", "I-22", "a `required` edit that serves no resolved `incompatible` item"),
	"E-SUG-SERVES-UNRESOLVED": ("error", "I-22", "a `serves` entry naming no item of this tool"),
	"E-REQUIREMENT-CONTRADICTED": ("error", "I-22", "a `proposed` edit serving an `incompatible` item — it reads `required`"),
	"E-USAGE-UNGROUNDED": ("error", "I-23", "a `usage` evidence entry that does not resolve, cannot be read, or whose `quote` is absent or not in the file"),
	"W-USAGE-INSTALL-ONLY": ("warning", "I-23", "a `usage` quote whose every occurrence lies on the tool's own install declaration or on comment lines"),
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
	("local.evidence[].role", "enum", "no", "|".join(EVIDENCE_ROLES)
		+ " — on an object-form entry; absent makes no usage claim"),
	("local.evidence[].quote", "string", "yes iff role == usage",
		"a VERBATIM excerpt of the whole line(s) showing the use (I-23)"),
	("security", "object|null", "yes iff 'security' in tags", "per-item security detail"),
	("security.cve_id", "string|null", "no", "CVE-(19|20)dd-dddd+"),
	("security.advisory_id", "string|null", "no", "vendor id"),
	("security.rating", "enum", "yes if security present", "|".join(CVE_RATINGS)),
	("security.rating_basis", "enum", "yes if security present", "|".join(RATING_BASES)),
	("security.exploited_in_wild", "bool", "yes if security present", "vendor/CISA says so"),
	("security.nature", "enum", "yes if security present", "|".join(SECURITY_NATURES)
		+ " — only a GROUNDED fix (I-21) makes a tool G-SEC"),
	("watch_hit", "object|null", "no", "set iff this item answers a stored watch item for this tool"),
	("watch_hit.topic", "string", "yes if watch_hit present", "VERBATIM copy of the stored watch item's `topic`"),
)


# Suggestion fields G-SEC reads (CONTRACT 4), beside ITEM_FIELDS.
SUGGESTION_FIELDS = (
	("requirement", "enum", "no (absent reads proposed)",
		"|".join(SUGGESTION_REQUIREMENTS) + " — on an action suggestion (edit, "
		"structural, or any unrecognized kind); read by nothing on a memory or "
		"upgrade suggestion"),
	("serves", "array<item ref>", "yes if requirement == required (I-22)",
		"the items this edit answers: the part of the derived item id after `#` "
		"(`cve:CVE-2026-1234`, `slug:krun-arm`) or the full id"),
	("serves_item_ids", "array<string>", "validator-assigned",
		"`serves` resolved against the tool's item ids; the original stays "
		"verbatim. No convergence op can write it"),
)


# Mirrored by `contract/bucketing.json` — test_items.py asserts the two stay
# equal, so the published fixture and the published contract cannot drift.
BUCKET_CLAUSE_ORDER = (
	"0. content_losing(view) is non-empty -> attention   [D1 - above the source clause on purpose]",
	"1. source in NON_VERSION_SOURCES -> routine if expected else attention",
	"2. security_tier(view) is not None -> security_auto if the stored tier is valid and tier in ACCEPTED_TIERS (P1, P2, P3) else security_mixed   [G-SEC - P0 and held tools are never in the auto strip]",
	"2b. has_security and security_only and impact == \"none\" and version_delta not in (major, unknown) and runnable and not pre_accept_bars(view) -> security_auto   [pre-G-SEC, unchanged: D2/E3 - a barred tool falls through to security_mixed]",
	"3. has_security -> security_mixed",
	"4. risk_level elevated, or config needs_attention, or an edit/structural suggestion (items.needs_a_decision), or not runnable -> attention",
	"5. -> routine",
)
D2_ROUTING = (
	"clause-2b guard (orchestrator ruling, overriding the spec's variants A and B): "
	"a barred security-only tool falls through to security_mixed — never to attention (A), "
	"and never left in security_auto with a cleared checkbox (B). security_mixed already "
	"renders an expanded card with an 'affects this setup' badge and correct counters; "
	"anything downstream of finalize_tool desyncs the Overview tiles. Since CONTRACT 4 "
	"D2 governs every tool that is NOT G-SEC — including one whose only security "
	"content is not a positively identified fix.")
G_SEC_ROUTING = (
	"a version-source tool with a positively identified security fix (security.nature "
	"== fix, grounded by I-21) or a vendor-declared unread security release is G-SEC: "
	"security_tier(view) computes its display priority (P0-P3) and its acceptance holds "
	"once, the validator stores it, and clause 2 routes it — an accepted tier (P1, P2, "
	"P3) to security_auto, P0 and held to security_mixed. D2's elevated bar and the "
	"reaches limb do not apply to it; the priority panel is the visibility D2 asked "
	"for. enum-invalid and container-unreadable bar every tool, G-SEC or not (R7).")
PRE_ACCEPT_PREDICATE = (
	"sug is baseline AND auto_runnable AND items.accepts_baseline(tool) — one "
	"predicate for assembly and convergence, reading security_tier (key required: "
	"null or an object), pre_accept_bars (key required: the EXPORTED bars, never "
	"recomputed), the bucket, risk_level and forced_conservative. Not G-SEC "
	"(security_tier null): bucket != \"attention\" AND risk_level == \"low\" AND not "
	"content_losing AND pre_accept_bars == [] AND not forced_conservative. G-SEC: the "
	"tier is valid AND tier in ACCEPTED_TIERS AND not content_losing AND "
	"pre_accept_bars == [] AND not forced_conservative. A malformed tier ({} included) "
	"is never accepted. The bars and the tier are the VALIDATOR'S, copied by "
	"finalize_tool — assembly never recomputes them from assembled items, which can "
	"hold synthesized reaching security items the bucket never saw")

# The memory stores (D4, REDESIGN.md §L1). Mirrored by `contract/stores.json`;
# all are machine-global, all are written only through their write_status.py
# subcommand, and all are read back at research time to fill
# {{STANDING_NOTES}}.
#
# THREE STORES IN TWO FILES, and the asymmetry is deliberate rather than an
# oversight to tidy up later. Watch items and per-tool method notes are keyed
# by tool id ({source}:{name}), which always contains a colon; global method
# notes are the third store and live in method-notes.json under the reserved,
# colon-free key `GLOBAL_METHOD_NOTE_KEY`, which therefore cannot collide with
# any tool id. A third file would have to be threaded through every reader
# that already snapshots these two (the session copy the validator grounds
# watch hits against, and the one apply_converge.py hands convergence); a
# reserved key rides along in the snapshot that already exists, and every
# tool-id lookup in the pipeline is a plain `snapshot.get(tool_id)` that can
# never reach it. Global notes are rare by definition and are filled only by
# promotion during convergence, which is the one stage that sees enough tools
# at once to know a note generalises (schemas.md §1.7b Scope).
WATCH_ITEMS_STORE = "watch-items.json"
METHOD_NOTES_STORE = "method-notes.json"
GLOBAL_METHOD_NOTE_KEY = "global"
MEMORY_STORES = {
	WATCH_ITEMS_STORE: {
		"path": "${XDG_STATE_HOME:-~/.local/state}/tool-update-review/watch-items.json",
		"writer": "write_status.py add-watch-item --tool-id ID --topic TEXT --note TEXT",
		"entry": {"topic": "string", "note": "string", "added_at": "YYYY-MM-DD (UTC)"},
	},
	METHOD_NOTES_STORE: {
		"path": "${XDG_STATE_HOME:-~/.local/state}/tool-update-review/method-notes.json",
		"writer": "write_status.py add-method-note --tool-id ID --topic TEXT --note TEXT",
		"entry": {"topic": "string", "note": "string", "added_at": "YYYY-MM-DD (UTC)"},
		"global": {
			"key": GLOBAL_METHOD_NOTE_KEY,
			"writer": "write_status.py add-global-method-note --topic TEXT --note TEXT",
			"filled_by": "promotion during convergence only — never a per-tool proposal "
				"(schemas.md §1.7b Scope)",
			"why_not_a_third_file": "a tool id always contains a colon, so this key cannot "
				"collide with one; the store then rides along in the method-notes snapshot "
				"every reader already takes, instead of needing a third one",
		},
	},
}


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
			"security_natures": list(SECURITY_NATURES),
			"suggestion_requirements": list(SUGGESTION_REQUIREMENTS),
			"evidence_roles": list(EVIDENCE_ROLES),
			"config_states": list(CONFIG_STATES),
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
		"research_keys": {
			"read": list(RESEARCH_KEYS_READ),
			"echoed_ignored": list(RESEARCH_KEYS_ECHOED),
			"forbidden": list(VALIDATOR_ONLY_FLAGS),
			"unrecognized": "E-RESEARCH-UNKNOWNKEY — the value is quarantined "
				"verbatim and the tool is held for review",
		},
		"degradation": {
			"reasons": list(DEGRADATION_REASONS),
			"content_losing_codes": dict(CONTENT_LOSING_CODES),
			"rule": "a non-empty content_losing forces `attention` and clears "
				"`pre_accept`; every other finding is a marker on the card and "
				"changes no bucket",
			"block": {"content_losing": "array<enum>", "markers": "array<code>",
				"quarantined": "int"},
		},
		"bucketing": {
			"clause_order": list(BUCKET_CLAUSE_ORDER),
			"d2_routing": D2_ROUTING,
			"g_sec_routing": G_SEC_ROUTING,
		},
		"pre_accept": {
			"predicate": PRE_ACCEPT_PREDICATE,
			"bars": list(PRE_ACCEPT_BARS),
			"bars_source": "the validator's view — the validator stores "
				"view.pre_accept_bars (a G-SEC view's are its tier's P0 reasons and "
				"holds, read off the stored tier), finalize_tool COPIES it onto the "
				"tool, and accepts_baseline refuses (KeyError) a tool nobody "
				"computed them for rather than recomputing from assembled items",
		},
		"security_tier": {
			"tiers": list(SECURITY_TIERS),
			"priorities": list(SECURITY_PRIORITIES),
			"priority_rank": dict(PRIORITY_RANK),
			"accepted": list(ACCEPTED_TIERS),
			"highlight": list(HIGHLIGHT_PRIORITIES),
			"reasons": [{"code": code, "level": level} for code, level in TIER_REASON_LEVELS],
			"holds": list(TIER_HOLDS),
			"tool_level_codes": list(TOOL_LEVEL_TIER_CODES),
			"labels": copy.deepcopy(TIER_LABELS),
			"shape": {"tier": "|".join(SECURITY_TIERS),
				"priority": "|".join(SECURITY_PRIORITIES),
				"reasons": "array<reason>, fixed order — every reason that holds",
				"holds": "array<hold>, fixed order — every hold that applies",
				"ids": "{reason or hold: [item or suggestion id]} — [] for tool-level ones",
				"fix_item_ids": "array<item id> — the grounded fixes"},
			"precedence": [
				"applicability: >=1 item is_positive_fix, or vendor_silent_categories "
				"contains security; else security_tier is null (not G-SEC)",
				"priority: the highest level with a reason — computed WITHOUT the holds",
				"tier: P0 if priority is P0; else held if any hold; else the priority",
				"accepted iff tier in ACCEPTED_TIERS",
				"a held tool keeps its priority on the page (R1) and is never counted "
				"as accepted",
			],
			"default": copy.deepcopy(TIER_UNCOMPUTED),
			"install_declaration_patterns": [dict(p) for p in INSTALL_DECLARATION_PATTERNS],
			"usage_file_kinds": {"brewfile": "Brewfile", "tool-versions": ".tool-versions",
				"mise-toml": ", ".join(MISE_TOML_NAMES) + ", mise/config.toml",
				"shell": "*.sh, .bash*", "other": "anything else — no line is an "
				"install declaration; a #-led line is still a comment"},
			"usage_file_max_bytes": USAGE_FILE_MAX_BYTES,
			"supersedes": ["reaches-item for G-SEC tools", "D2 elevated bar for G-SEC tools",
				"local-enum-invalid (renamed enum-invalid)"],
		},
		"suggestion_fields": [
			{"name": n, "type": t, "required": r, "note": note}
			for n, t, r, note in SUGGESTION_FIELDS
		],
		"watch_hit": {
			"authored_by": "the per-tool checker",
			"grounded_against": "{session_dir}/watch-items.json — a copy of the "
				"machine-global store, taken when {{STANDING_NOTES}} is filled",
			"topic_match": "exact, after .strip(); no case folding and no fuzzy match",
			"absent_snapshot": "W-WATCH-UNCHECKED; the hit is kept",
			"not_a_sort_tier": True,
			"highlight": {"code": "watch_item_hit", "points": 70,
				"scored": "grounded hits only — the view's watch_hit_item_ids "
					"export. An ungrounded, malformed or unchecked hit bars "
					"pre-acceptance (fail-closed) but earns no prominence"},
			"distinct_from": "memory_proposals.watch_topic/watch_note, which "
				"PROPOSE a watch item on a suggestion; watch_hit says an existing "
				"one FIRED, on an item",
		},
		# deepcopy, not a two-level hand-rolled copy: method-notes.json now
		# carries a nested `global` block, and a caller that mutated the
		# returned contract would otherwise be mutating MEMORY_STORES.
		"memory_stores": copy.deepcopy(
			{name: MEMORY_STORES[name] for name in sorted(MEMORY_STORES)}),
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
