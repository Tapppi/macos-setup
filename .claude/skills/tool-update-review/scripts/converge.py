#!/usr/bin/env python3
"""
converge.py — the convergence output contract.

This module IS the contract for stage 4 (convergence) and stage 5 (apply),
the way `items.py` is the contract for the item model. Normative prose:
`references/convergence.md`. The applier that enforces it:
`scripts/apply_converge.py`. The published fixtures a reviewer asserts
against: `scripts/contract/convergence.json` and the `expected_converge_*`
set (see `contract/README.md`).

The shape — a modification list a deterministic applier applies:

	Convergence reads the whole normalized corpus and emits an addressed,
	reasoned modification list — `converge.json` — against it. It never
	writes a corpus. A deterministic applier writes `corpus.post.json`
	beside the untouched `corpus.pre.json`, and the renderer receives both.
	Every difference between the two corpora is DERIVED by the applier,
	never declared by the agent.

The alternative — convergence writes the post corpus and the renderer
compares the two — was rejected. The agent would still both decide and
apply, and a diff cannot tell an intended cut from a slip. A rewritten
corpus must be the whole corpus, past budget in and out, and every retry
would resubmit all of it rather than a small delta. And an element dropped
by a rewrite looks exactly like a legitimate change, where here an element
no edit names cannot disappear.

This module holds no I/O and no judgement. It defines:

  * the artefact set and the corpus-pre composition (`build_corpus_pre`),
  * the projection convergence actually reads (`build_view`,
	`build_tables`) — pure functions of `corpus.pre.json`, so stage 5 can
	recompute either and assert equality,
  * the op vocabulary, per-op preconditions and declared scope (`OPS`),
  * the seven checks and their attestation arithmetic surface (`CHECKS`),
  * every code the applier can raise (`CODES`),
  * the re-derivation of a tool's axes from an edited view
	(`derive_tool_state`) — the SAME `items.py` / `validate_items.py`
	functions the validator used, so pre and post are graded by one
	implementation, never two.

Governing constraint, same as everywhere in this layer
(`references/item-schema.md` §0):
nothing here deletes, trims or re-rates on a rule. The judgement is the
agent's; this module makes the judgement checkable.
"""
from __future__ import annotations  # Python 3.9 — same constraint as items.py

import copy
import hashlib
import json

import assemble
import items as model
import validate_items

# Bumped when a consumer of converge.json / corpus.pre.json /
# converge-effect.json would have to change. Equality is asserted, not
# compared — no migration shim exists or ships.
#
# 2 — the WP0 redesign: named corpus.pre/corpus.post artefacts, the derived
#     diff as the record, the five-phase applier, the recovery loop.
#     (1 was the rejected edit-list contract that never shipped.)
# 3 — G-SEC: corpus.pre carries the validator's `security_tier`,
#     `usage_evidence`, `usage_item_ids` and `pre_accept_bars`; the effect
#     carries `security_priority` per tool; the gate covers demotions (any
#     strict decrease in priority) as well as permissive moves; corpus.pre's
#     own versions are gated (`check_corpus_versions`).
CONVERGE_VERSION = 3

# The projection's own version, carried on converge-view.json and echoed by
# converge.json so a submission written against a stale view is refused.
# 2 — projects `security_tier`, `usage_evidence`, `usage_item_ids`, and
#     `initial_pre_accept` now means `items.accepts_baseline`.
VIEW_VERSION = 2

# Five attempts, then degrade conservatively with a
# first-class explanation. Never a silent pass, never a dead run.
MAX_ATTEMPTS = 5

# The terminal states convergence_status.state can carry. `converged` may
# still ship standing rejects at attempt 5 — they are first-class in
# `convergence_status.standing_rejects`, never silent.
DEGRADATION_STATES = ("converged", "degraded_gate", "degraded_unapplied")

# The conservative option (§3.4d): the ONE place in the design where a bucket
# is written rather than derived. Written by the applier, never by
# convergence, always recorded as forced.
FORCED_BUCKET = "security_mixed"

# The artefact set, stage by stage, so the hand-offs are enumerable.
ARTIFACTS = {
	"corpus.pre.json": "stage 3+prepare — the frozen corpus of record; immutable for the run",
	"converge-view.json": "prepare — the projection convergence reads; pure function of corpus.pre.json",
	"converge-tables.json": "prepare — corpus-level tables no single checker could compute",
	"converge.json": "stage 4 — the agent's addressed, reasoned modification list",
	"corpus.post.json": "stage 5 — written ONLY by the applier",
	"converge-effect.json": "stage 5 — the derived record: diff, attribution, labels, convergence_status",
	"converge-attempts.json": "stage 5 — the durable attempt counter the loop is enforced with",
}


# ── the op vocabulary (§1.6) ────────────────────────────────────────────────
# Each op declares which (element, field) pairs it may change; that declared
# scope is what the applier's containment check compares the derived diff
# against. `precondition` is the class of check phase 2 runs:
#   "field"   — precondition.before must equal the current value of target.field
#   "element" — the mandatory quote must be verbatim-contained in the element
#   "none"    — nothing to precondition against (add creates; flag writes nothing)
ITEM_TEXT_FIELDS = ("title", "body", "local.statement")
SUGGESTION_TEXT_FIELDS = (
	"title", "rationale", "description", "diff_preview",
	"watch_note", "method_note", "watch_topic", "method_topic",
)
REDIRECT_FIELDS = ("local.direction", "local.effect")

OPS = {
	"delete": {"kinds": ("item", "suggestion", "proposal"), "fields": None,
		"precondition": "element", "bucket_capable": True, "cut": True,
		"scope": "removal of target.id"},
	"merge": {"kinds": ("item",), "fields": None,
		"precondition": "element", "bucket_capable": True, "cut": True,
		"scope": "changed_fields on target.id; `after` is the WHOLE merged element"},
	"retag": {"kinds": ("item",), "fields": ("tags",),
		"precondition": "field", "bucket_capable": True, "cut": True,
		"scope": "tags on target.id"},
	"rerate": {"kinds": ("item",), "fields": ("severity",),
		"precondition": "field", "bucket_capable": True, "cut": True,
		"scope": "severity on target.id"},
	"redirect": {"kinds": ("item",), "fields": REDIRECT_FIELDS,
		"precondition": "field", "bucket_capable": True, "cut": True,
		"scope": "target.field on target.id"},
	"add": {"kinds": ("suggestion",), "fields": None,
		"precondition": "none", "bucket_capable": True, "cut": False,
		"scope": "creation of one MEMORY suggestion under target.tool_id"},
	"trim": {"kinds": ("item", "suggestion", "proposal"), "fields": "text",
		"precondition": "field", "bucket_capable": False, "cut": False,
		"scope": "target.field on target.id; after must be a token-subsequence shortening"},
	"reword": {"kinds": ("item", "suggestion", "proposal"), "fields": "text",
		"precondition": "field", "bucket_capable": False, "cut": False,
		"scope": "target.field on target.id"},
	"move_evidence": {"kinds": ("item",), "fields": ("local.evidence",),
		"precondition": "field", "bucket_capable": False, "cut": False,
		"scope": "local.evidence + local.citations on target.id"},
	"annotate": {"kinds": ("item", "suggestion", "proposal", "tool"), "fields": None,
		"precondition": "field", "bucket_capable": False, "cut": False,
		"scope": "convergence_notes on target.id"},
	"flag": {"kinds": ("item", "suggestion", "proposal", "tool"), "fields": None,
		"precondition": "none", "bucket_capable": False, "cut": False,
		"scope": "EMPTY — writes nothing to the corpus"},
}

# The six that require `bucket_claim`; the other five must omit it. The
# applier rejects the inverse of either (E-EDIT-OP).
BUCKET_CAPABLE_OPS = tuple(op for op in OPS if OPS[op]["bucket_capable"])

# The ops that can change a tool's G-SEC display PRIORITY — attribution's
# candidate set for the "security_priority" pseudo-axis. Priority reads
# everything the bucket reads PLUS `local.evidence` (a usage confirmation,
# I-23), which `move_evidence` edits — the op that most directly removes a
# confirmation. `move_evidence` stays NOT bucket-capable: it still may not
# carry a `bucket_claim`, so no submission field changes.
PROMINENCE_CAPABLE_OPS = BUCKET_CAPABLE_OPS + ("move_evidence",)

# §3.1 — a cut is any edit that can remove content or move it down the review
# surface. Every cut carries the full reason record and a verbatim quote.
CUT_OPS = tuple(op for op in OPS if OPS[op]["cut"])

# Validator-assigned fields no edit may write, whatever the op. Rewriting an
# id destroys within-run duplicate detection and the modification list's own
# addressing (§7.1) — under this contract there is no field in which to
# express one, and merge/add payloads that try are rejected.
IMMUTABLE_ELEMENT_FIELDS = ("id", "id_stability")

REASON_CONFIDENCE = ("high", "medium", "low")

# `bucket_claim.direction` values, and the restrictiveness rank the applier
# classifies real moves with. Lower rank = less review = more permissive.
# `attention` demands the human; `security_auto` auto-accepts.
CLAIM_DIRECTIONS = ("permissive", "restrictive", "lateral")
BUCKET_RESTRICTIVENESS = {
	"attention": 3, "security_mixed": 2, "routine": 1, "security_auto": 0,
}

# E-GATE-UNREASONED's mechanical surface: an attributed permissive edit's
# reason.body must NAME the consequence. "Names it" is spelled as containing
# at least one of these tokens (case-insensitive) — the rule is declared here
# rather than living as hidden judgement inside the applier.
GATE_CONSEQUENCE_TOKENS = (
	"security_auto", "pre-accept", "pre_accept", "pre-accepted",
	"auto-accept", "auto-accepted", "auto-update", "auto_update",
)
# The demotion gate's twin (G-SEC, O3): an attributed edit that LOWERS a
# fix's display priority must name that consequence in its reason.body.
PROMINENCE_CONSEQUENCE_TOKENS = ("priority", "highlight", "prominence")

# The derived fields no edit may write, whatever the op — validator records
# convergence reads and recomputes, never authors (convergence.md §8's
# never-write list). Tool-level derived fields are not addressable by any
# op, and a suggestion takes only text edits, `delete` and `annotate`.
NEVER_WRITE_FIELDS = ("security_tier", "usage_evidence", "usage_item_ids",
	"serves_item_ids", "pre_accept_bars")


class CorpusVersionError(Exception):
	"""corpus.pre.json was built by a different contract or converge version
	than this code's. Raised, never returned: no corpus.post and no effect is
	ever derived from a stale corpus — a shipping state built from one would
	present another contract's tier and predicate as this one's."""


def check_corpus_versions(corpus_pre) -> None:
	"""Refuse a corpus.pre whose own `contract_version`/`converge_version`
	is not exactly this code's — `type(v) is int` and equal, so a missing
	key, "4", 4.0 or True is refused. The submission's versions are a
	separate gate (E-SUBMIT-VERSION); neither masks the other."""
	if not isinstance(corpus_pre, dict):
		raise CorpusVersionError("corpus.pre.json is {}, not an object".format(
			type(corpus_pre).__name__))
	for key, expected in (("contract_version", model.CONTRACT_VERSION),
			("converge_version", CONVERGE_VERSION)):
		got = corpus_pre.get(key)
		if type(got) is not int or got != expected:
			raise CorpusVersionError(
				"corpus.pre.json was built by contract/converge {!r}/{!r}; this "
				"applier is {}/{} — re-run --prepare --force".format(
					corpus_pre.get("contract_version"), corpus_pre.get("converge_version"),
					model.CONTRACT_VERSION, CONVERGE_VERSION))

# A store snapshot the session HOLDS but the applier cannot READ is a
# different fact from one that was never taken — the operator's remedy is
# "fix the copied file", not "go and copy it". The applier is a pure
# function of corpus.pre.json, so the distinction must ride in the corpus;
# it rides as a reserved entry INSIDE the store value (absent stays None)
# so the absent case's corpus — and every pinned digest — is byte-identical
# to before the distinction existed. The key cannot collide with a real
# entry: store keys are tool ids, which always contain a colon, or the
# reserved "global" section — and `apply_converge._load_store` refuses a
# file that carries the sentinel as a key, so only the loader ever writes it.
STORE_UNREADABLE_KEY = "__store_unreadable__"
# The fourth fact: the session holds no snapshot BECAUSE no store existed to
# copy (SKILL.md step 3: "a store that does not exist yet is simply not
# copied"). `--prepare` records it when the session has no snapshot and the
# live store is missing too, so "never copied" (absent — go and copy it) is
# no longer said of a store nobody could have copied. Same reserved-entry
# encoding and the same loader refusal as the unreadable sentinel.
STORE_NONEXISTENT_KEY = "__store_nonexistent__"
STORE_RESERVED_KEYS = (STORE_UNREADABLE_KEY, STORE_NONEXISTENT_KEY)
STORE_STATES = ("present", "absent", "unreadable", "nonexistent")


def store_status(stores, name) -> str:
	"""present | absent | unreadable | nonexistent, for one store in
	corpus.pre's block."""
	value = (stores or {}).get(name)
	if not isinstance(value, dict):
		return "absent"
	if STORE_UNREADABLE_KEY in value:
		return "unreadable"
	if STORE_NONEXISTENT_KEY in value:
		return "nonexistent"
	return "present"


def store_entries(stores, name):
	"""The store's entries dict, or None when there is nothing readable —
	an unreadable snapshot grounds nothing, exactly as the validator's own
	load treats it (E-RESEARCH-UNREADABLE, then None). A nonexistent store
	is None too: the validator read the session dir, found no snapshot and
	grounded nothing, and the applier's re-derivation must read what it
	read."""
	return (stores or {}).get(name) if store_status(stores, name) == "present" 		else None


# The finding codes that constitute C1's deterministic input. Includes
# E-REACHES-UNEVIDENCED so C1's step-4 population (a claimed live risk with
# no path behind it) is a subset of the table and the attestation's
# `findings` count can be required EQUAL to the table's length.
EVIDENCE_FINDING_CODES = (
	"E-EVID-MALFORMED", "E-EVID-404", "W-EVID-ROOT", "E-REACHES-UNEVIDENCED",
	# G-SEC (I-23): a usage claim the validator could not ground, or that
	# quotes only the install line — C1 reviews them with the rest.
	"E-USAGE-UNGROUNDED", "W-USAGE-INSTALL-ONLY",
)


# ── the seven checks (§2) ───────────────────────────────────────────────────
# The judgement half of each check is the agent's and lives in
# `references/convergence.md` §2. THIS half is the deterministic attestation
# surface: which ops the check may emit, and which of its numbers stage 5
# recomputes (`apply_converge.verify_c1`..`verify_c7`). A missing or
# arithmetically inconsistent attestation is a stage-5 verification failure,
# not a warning.
CHECKS = (
	("C1-evidence", {
		"ops": ("move_evidence", "flag"),
		"attests": "scanned.tools/items/evidence_entries exact; findings == len(evidence_findings)"}),
	# `delete`/`merge` are admitted here as well as in C6: C2 is the general
	# item-review pass, and the design's own worked cut (§3.2's brew:noti
	# delete, a dedup) files under C2-tags-visibility. C1 stays delete-free —
	# a citation in the wrong field is a filing error, not noise.
	("C2-tags-visibility", {
		"ops": ("retag", "rerate", "redirect", "trim", "reword", "delete",
			"merge", "flag"),
		"attests": "scanned.items exact; clean + distinct edited items cover every item"}),
	("C3-security-only", {
		"ops": ("retag", "rerate", "flag"),
		"attests": "security_only_tools enumerates EVERY security_only tool with its cve_count and a verdict"}),
	("C4-notable-security", {
		"ops": ("redirect", "rerate", "flag"),
		"attests": "scanned.security_items/rating_unrated/direction_does_not_reach/anchor_duplicates exact"}),
	("C5-auto-approval", {
		"ops": ("retag", "rerate", "redirect", "flag"),
		"attests": "tools enumerates EVERY security_auto or pre-accept-eligible tool with deciding_input + verdict"}),
	("C6-memory", {
		"ops": ("delete", "add", "flag"),
		"attests": "the ledger: every proposal in exactly one disposition; every tagged proposal reviewed in both directions; fired_this_run recomputed"}),
	("C7-collisions", {
		"ops": ("delete", "annotate", "reword", "trim", "flag"),
		"attests": "clusters_inspected == len(file_collisions); every surviving suggestion id resolves"}),
)
CHECK_IDS = tuple(name for name, _ in CHECKS)
CHECK_OPS = {name: spec["ops"] for name, spec in CHECKS}


# ── every code the applier can raise ────────────────────────────────────────
# (severity, phase, meaning). "critical" bounces the submission while
# attempts remain (§3.4d); "note" ships as a report note. E-EFFECT-ARITH is
# graded per FIELD, not per code: `tools_moved` / `pre_accept` are safety
# fields and critical; every other number is a note (§3.5).
CODES = {
	# phase 1 — RESOLVE, submission-level: nothing applies
	"E-SUBMIT-SHAPE": ("critical", 1, "converge.json is not the contract's shape (object, edits[], checks[], ledger, corpus_effect)"),
	"E-SUBMIT-RUN": ("critical", 1, "run_id does not match the session"),
	"E-SUBMIT-VERSION": ("critical", 1, "converge_version or view_version does not match this contract"),
	"E-SUBMIT-DIGEST": ("critical", 1, "corpus_digest does not match the corpus about to be applied to"),
	# phase 2 — PRECHECK, per edit
	"E-EDIT-TARGET": ("critical", 2, "target.id does not resolve, or target.field is illegal for target.kind"),
	"E-EDIT-PRECOND": ("critical", 2, "precondition.before != the current value of target.field"),
	"E-EDIT-QUOTE": ("critical", 2, "quote absent on a cut, or not verbatim-contained in the addressed element"),
	"E-EDIT-OP": ("critical", 2, "op/kind/field combination illegal; bucket_claim on a non-bucket-capable op or absent on a bucket-capable one; changed_fields absent on a merge; trim's after not a shortening; a malformed reason"),
	"E-EDIT-DEP": ("critical", 2, "requires names an unknown or rejected edit — transitively closed"),
	"E-EDIT-DUP": ("critical", 2, "two edits write the same (element, field) with no supersedes chain between them"),
	"E-EDIT-ID": ("critical", 2, "an add whose derived id collides with an existing id in that tool"),
	# phase 4 — DERIVE
	"E-APPLY-SCOPE": ("critical", 4, "the derived change set != the declared scope set"),
	# phase 5 — VERIFY
	"E-APPLY-SCHEMA": ("critical", 5, "corpus_post fails validation where corpus_pre did not — a merge/add payload that is not a valid element"),
	"E-APPLY-INTERNAL": ("critical", 5, "the applier's own re-derivation disagrees with the validator's recorded axes on corpus_pre — an applier bug, never the agent's"),
	"E-CHECK-CLOSURE": ("critical", 5, "a check attestation is missing/duplicated, or checks[].edits != the set of edits naming that check"),
	"E-CHECK-ARITH": ("critical", 5, "an attestation count != the recomputed count"),
	"E-CHECK-OP": ("critical", 5, "an edit's op is outside its check's output vocabulary"),
	"E-GATE-UNATTRIBUTED": ("critical", 5, "a permissive move with no attributable cause — the §3 defect exactly"),
	"E-GATE-UNDECLARED": ("critical", 5, "an attributed edit's bucket_claim.moves_bucket is false"),
	"E-GATE-UNREASONED": ("critical", 5, "an attributed edit's reason.body does not name the consequence"),
	"E-EFFECT-ARITH": ("graded", 5, "a corpus_effect number != recomputed — critical on tools_moved/pre_accept, a note otherwise"),
	"E-EFFECT-NARRATIVE": ("critical", 5, "corpus_effect.narrative missing, or it fails the -20% enumeration rule"),
	# notes — never bounce
	"W-EDIT-CLAIM": ("note", 5, "a bucket_claim disagrees with the computed truth in a non-gated direction"),
	"W-STORE-UNCHECKED": ("note", 5, "a memory store was never snapshotted into the session — C6's store-dependent checks ran against nothing, which is not the same as against an empty store"),
	"W-SUBMIT-ATTEMPT": ("note", 1, "the declared attempt number disagrees with the durable counter; the counter governs"),
	"W-EDIT-SCHEMA": ("note", 5, "a touched element carries a new warning-severity finding"),
}

# corpus_effect fields the applier recomputes, and which of them are safety
# fields (§3.5): a mismatch there is critical; elsewhere it is a note.
EFFECT_SAFETY_FIELDS = ("tools_moved", "pre_accept")
EFFECT_FIELDS = (
	"edits_by_op", "items", "severity_after", "severity_at_warning_or_worse",
	"tag_security_items", "tools_moved", "pre_accept",
)
# §3.5's reporting threshold: a warning-or-worse shrink past this must
# enumerate every affected tool in the narrative. A threshold on REPORTING,
# never a cap on cutting.
NARRATIVE_ENUMERATION_PCT = -20.0


# ── canonical digest ────────────────────────────────────────────────────────
def canonical_digest(document) -> str:
	"""sha256 over canonicalized JSON — the corpus identity converge.json
	echoes and the applier refuses a mismatch of (§1.2). Catches a resumed
	run, a mixed-up session directory, or a submission written against a
	re-run validator."""
	blob = json.dumps(document, sort_keys=True, separators=(",", ":"),
		ensure_ascii=False).encode("utf-8")
	return "sha256:" + hashlib.sha256(blob).hexdigest()


# ── corpus.pre composition ──────────────────────────────────────────────────
def initial_pre_accept(view) -> bool:
	"""The deterministic pre-acceptance ELIGIBILITY of a tool's baseline
	upgrade, computed from the validation view alone.

	`assemble.apply_pre_accept`'s predicate with its two assembly-only
	conjuncts mapped onto their view-level equivalents: `sug is baseline`
	becomes "a version source" (assembly synthesizes the baseline for exactly
	those), and the baseline's `auto_runnable` becomes `bucket_inputs.runnable`
	(both are `upgrade_command_and_runnable(source, name)[1]`, a pure function
	of two immutable fields). Everything else is `items.accepts_baseline` —
	the SAME function assembly calls, reading the same view fields — so the
	two layers cannot tell two stories, and §3.4's differential recomputation
	compares THIS predicate pre vs post, which is what makes "pre_accept went
	false→true" a fact about the corpus rather than about assembly's timing.

	A view missing a key `accepts_baseline` requires (`security_tier`,
	`pre_accept_bars`, the bucket, `risk_level`) is not eligible: False,
	never a raise and never a recomputation."""
	if not isinstance(view, dict):
		return False
	if view.get("source") in validate_items.NON_VERSION_SOURCES:
		return False
	inputs = view.get("bucket_inputs")
	if not (isinstance(inputs, dict) and inputs.get("runnable")):
		return False
	try:
		return model.accepts_baseline(view)
	except (KeyError, TypeError):
		return False


def build_corpus_pre(validation, collect, stores=None):
	"""→ corpus.pre.json: the validation document, whole, plus what
	convergence's stage needs pinned beside it — per-tool versions from
	collect.json, the deterministic `initial_pre_accept`, the memory-store
	snapshots the ledger is verified against, and the run identity. The
	validator's `pre_accept_bars` and `security_tier` are kept as recorded. A pure function; the input documents are not mutated.

	`corpus.pre.json` is immutable for the whole run. Nothing between the
	validator writing it and the renderer reading it mutates it — not
	convergence, not the applier, not the recovery loop (§1.1)."""
	corpus = copy.deepcopy(validation)
	corpus["run_id"] = corpus.get("session_id")
	corpus["converge_version"] = CONVERGE_VERSION
	corpus["stores"] = {
		"watch_items": copy.deepcopy((stores or {}).get("watch_items")),
		"method_notes": copy.deepcopy((stores or {}).get("method_notes")),
	}
	versions = {}
	if isinstance(collect, dict):
		for section in ("brew", "mise", "standalone", "macos"):
			for cand in assemble.read_candidate_list(collect, section):
				if isinstance(cand.get("id"), str):
					versions[cand["id"]] = cand
	for view in corpus.get("tools") or []:
		cand = versions.get(view.get("id"), {})
		view["current_version"] = cand.get("current_version")
		view["latest_version"] = cand.get("latest_version")
		# `pre_accept_bars` and `security_tier` are the VALIDATOR'S, kept as
		# recorded (G-SEC): the applier's self-check compares its own
		# re-derivation against them, and assembly's fresh-vs-frozen check
		# requires them to compare equal.
		view["initial_pre_accept"] = initial_pre_accept(view)
	return corpus


def watch_topics_for(corpus_pre, tool_id):
	"""The stored watch-item topic set for one tool, from the snapshot pinned
	into corpus.pre — same semantics as the validator's `_watch_topics_for`:
	exact string match after .strip(), per tool, None when no snapshot."""
	snapshot = store_entries(corpus_pre.get("stores"), "watch_items")
	if not isinstance(snapshot, dict):
		return None
	entries = snapshot.get(tool_id)
	return frozenset(
		e["topic"].strip() for e in (entries if isinstance(entries, list) else ())
		if isinstance(e, dict) and isinstance(e.get("topic"), str) and e["topic"].strip())


# ── the re-derivation (§3.4a's one implementation) ──────────────────────────
def derive_tool_state(view, watch_topics) -> dict:
	"""Recompute every derived axis of one tool view from its items and
	suggestions — the applier's half of the differential recomputation.

	Mirrors `validate_items._derive_axes` and the bucket step of its final act
	(`_finalize` → `_assign_bucket`) step for step, THROUGH THE SAME
	FUNCTIONS (`compute_impact`, `compute_security_only`,
	`compute_risk_level`, `model.security_tier`, `model.pre_accept_bars`,
	`compute_initial_bucket`, `model.security_display_items`,
	`model.grounded_watch_hit`, `model.usage_item_ids`), so there is one
	implementation of every axis, not two. What it does NOT recompute:

	  * `version_delta` and `bucket_inputs.runnable` — pure functions of
		fields no edit can write (versions, source, name); carried forward.
	  * `spec_violations` / `quarantine` / `degradation` — the validator's
		record of what stage 3 saw. An edit cannot repair a degradation, and
		re-deriving `content_losing` from the carried record is exactly what
		`model.content_losing` does anyway.
	  * `usage_evidence` — I-23's grounding record. Grounding reads files, and
		the applier is forbidden file access; the tier reads the CARRIED
		record against the current items, so a moved or forged entry confirms
		nothing and a carried grounded one keeps confirming.

	Mutates `view` in place (callers hand it a deep copy) and returns it.
	`apply_converge` also runs this over corpus_pre and asserts the result
	equals the validator's recorded axes, tier and bars (E-APPLY-INTERNAL on
	divergence), so "same implementation" is checked every run rather than
	trusted."""
	items_list = [i for i in (view.get("items") or []) if isinstance(i, dict)]
	flags = model.recompute_flags(items_list)
	view["flags"] = flags
	# The effective has_security — the same three limbs as `_derive_axes`,
	# for the same measured reasons (see that function's comment block).
	has_security = bool(flags["has_security"]
		or "security" in (view.get("vendor_silent_categories") or [])
		or any(isinstance(i.get("security"), dict) for i in items_list))
	impact = validate_items.compute_impact(view)
	security_only = validate_items.compute_security_only(view, has_security)
	risk_level = validate_items.compute_risk_level(view)
	runnable = bool((view.get("bucket_inputs") or {}).get("runnable"))
	view["impact"] = impact
	view["risk_level"] = risk_level
	view["bucket_inputs"] = {
		"has_security": has_security,
		"security_only": security_only,
		"impact": impact,
		"version_delta": view.get("version_delta"),
		"runnable": runnable,
	}
	view["security_tier"] = model.security_tier(view)
	view["pre_accept_bars"] = model.pre_accept_bars(view)
	view["initial_review_bucket"] = validate_items.compute_initial_bucket(
		view, has_security, security_only, impact, risk_level, runnable)
	return derive_item_exports(view, watch_topics)


def derive_item_exports(view, watch_topics) -> dict:
	"""The pure item-derived exports — `flags`, the security-display,
	watch-hit and usage id lists, the security tier, the pre-acceptance bars
	and the eligibility — recomputed from `view["items"]` against the axes
	currently on the view.

	Split out of `derive_tool_state` for the one tool class whose AXES must
	stay as recorded: a `validator_error` view (see `apply_converge` phase
	5b). Its bucket/risk/impact are the validator's conservative constants
	(`validate_items.CONSERVATIVE_AXES`, written in its final act) and are
	not re-derived — but its id-list exports and its tier are pure functions
	of the items, and after a delete they would otherwise name elements that
	no longer exist in the artefact the renderer reads. The tier is
	recomputed with the `content-losing` hold present (`validator_error` is
	content-losing), so such a tool is never accepted. Mutates and returns
	`view`."""
	items_list = [i for i in (view.get("items") or []) if isinstance(i, dict)]
	view["flags"] = model.recompute_flags(items_list)
	view["security_display_item_ids"] = [
		i["id"] for i in model.security_display_items(items_list)
		if isinstance(i.get("id"), str)]
	view["watch_hit_item_ids"] = [i["id"] for i in items_list
		if model.grounded_watch_hit(i, watch_topics)
		and isinstance(i.get("id"), str)]
	view["security_tier"] = model.security_tier(view)
	view["pre_accept_bars"] = model.pre_accept_bars(view)
	view["usage_item_ids"] = model.usage_item_ids(view)
	view["initial_pre_accept"] = initial_pre_accept(view)
	return view


def axis_value(view, axis):
	"""The one accessor for every axis the applier compares pre vs post:
	`view.get(axis)` for `MOVED_AXES`, and `items.security_priority(view)`
	for the pseudo-axis "security_priority" — priority lives INSIDE
	`security_tier`, so a plain `.get` would read nothing (technical round-2
	finding 4)."""
	if axis == PRIORITY_AXIS:
		return model.security_priority(view)
	return view.get(axis)


# The axes the differential recomputation compares pre vs post. A tool where
# any of the first three differs is a convergence-moved tool (§3.4a).
MOVED_AXES = ("initial_review_bucket", "initial_pre_accept", "risk_level")
# G-SEC's pseudo-axis: display priority, read through `axis_value`. NOT in
# MOVED_AXES — `corpus_effect.tools_moved` keeps its meaning and the agent is
# never asked to predict a tier move — but tracked, attributed and gated on
# its own (the prominence map, the demotion gate).
PRIORITY_AXIS = "security_priority"


def is_demotion(pre_priority, post_priority) -> bool:
	"""Any STRICT decrease in priority rank — P0 > P1 > P2 > P3 > not G-SEC
	(§12 A-R3-2). Every one needs attribution, consequence reasoning and the
	expanded Overview disclosure."""
	return model.priority_rank(post_priority) < model.priority_rank(pre_priority)


def classify_move(pre_view, post_view) -> str:
	"""permissive | restrictive | lateral, for `corpus_effect.tools_moved`.

	Any component that reduces review (bucket rank down, pre-acceptance
	gained, risk lowered) makes the move permissive — erring loud, since
	permissive is the direction the gate exists for. Restrictive needs a
	restrictive component and no permissive one; anything else that still
	moved is lateral."""
	rank = BUCKET_RESTRICTIVENESS
	pre_b = rank.get(pre_view.get("initial_review_bucket"), 3)
	post_b = rank.get(post_view.get("initial_review_bucket"), 3)
	permissive = (post_b < pre_b
		or (not pre_view.get("initial_pre_accept") and post_view.get("initial_pre_accept"))
		or (pre_view.get("risk_level") == "elevated" and post_view.get("risk_level") == "low"))
	restrictive = (post_b > pre_b
		or (pre_view.get("initial_pre_accept") and not post_view.get("initial_pre_accept"))
		or (pre_view.get("risk_level") == "low" and post_view.get("risk_level") == "elevated"))
	if permissive:
		return "permissive"
	if restrictive:
		return "restrictive"
	return "lateral"


# ── the projection (§1.2) ───────────────────────────────────────────────────
def _project_item(item) -> dict:
	"""One item, whole, minus `body` — replaced by `has_body`, the on-demand
	flag. `body` is this schema's `detail`: 35% of the measured corpus in one
	field, and §3.3's quote rule is what makes editing an unread item
	detectable despite the omission."""
	projected = {}
	for key, value in item.items():
		if key == "body":
			continue
		projected[key] = copy.deepcopy(value)
	body = item.get("body")
	projected["has_body"] = bool(isinstance(body, str) and body.strip())
	return projected


def build_view(corpus_pre) -> dict:
	"""→ converge-view.json, a pure projection of corpus.pre.json. Stage 5
	recomputes it and asserts equality, which is what makes "the agent saw
	the corpus that was applied" checkable rather than assumed.

	Three deliberate reductions, each priced in the design (§1.2): item
	`body` → `has_body`; `config_status` → its `state` (detail on demand);
	`links` and `quarantine` values omitted (links are addressed by
	`change.link_index`; quarantine is counted in `degradation`). Everything
	convergence needs beyond this it reads out of corpus.pre.json by id —
	and §3.3 REQUIRES that read before any cut of an item whose `has_body`
	is true."""
	check_corpus_versions(corpus_pre)
	tools = []
	for view in corpus_pre.get("tools") or []:
		if not isinstance(view, dict):
			continue
		tools.append({
			"id": view.get("id"),
			"source": view.get("source"),
			"name": view.get("name"),
			"current_version": view.get("current_version"),
			"latest_version": view.get("latest_version"),
			"version_delta": view.get("version_delta"),
			"pinned": view.get("pinned"),
			"research_error": view.get("research_error"),
			"validator_error": view.get("validator_error"),
			"config_status_state": (view.get("config_status") or {}).get("state"),
			"flags": copy.deepcopy(view.get("flags")),
			"initial_review_bucket": view.get("initial_review_bucket"),
			"bucket_inputs": copy.deepcopy(view.get("bucket_inputs")),
			"initial_pre_accept": view.get("initial_pre_accept"),
			"pre_accept_bars": list(view.get("pre_accept_bars") or []),
			"degradation": copy.deepcopy(view.get("degradation")),
			"spec_violations": list(view.get("spec_violations") or []),
			"items": [_project_item(i) for i in view.get("items") or []
				if isinstance(i, dict)],
			"suggestions": copy.deepcopy(view.get("suggestions") or []),
			"security_display_item_ids": list(view.get("security_display_item_ids") or []),
			"watch_hit_item_ids": list(view.get("watch_hit_item_ids") or []),
			"self_test_tagged_suggestion_ids": list(
				view.get("self_test_tagged_suggestion_ids") or []),
			# G-SEC (VIEW 2): the validator's tier, its usage record and the
			# usage-confirmed items — what C5 and the demotion gate read.
			"security_tier": copy.deepcopy(view.get("security_tier")),
			"usage_evidence": copy.deepcopy(view.get("usage_evidence") or []),
			"usage_item_ids": list(view.get("usage_item_ids") or []),
		})
	return {
		"run_id": corpus_pre.get("run_id"),
		"generated_at": corpus_pre.get("generated_at"),
		"converge_version": CONVERGE_VERSION,
		"view_version": VIEW_VERSION,
		"corpus_digest": canonical_digest(corpus_pre),
		"tools": tools,
	}


# ── the tables (§1.3) ───────────────────────────────────────────────────────
def _suggestion_tool_map(corpus_pre) -> dict:
	out = {}
	for view in corpus_pre.get("tools") or []:
		for sug in view.get("suggestions") or []:
			if isinstance(sug, dict) and isinstance(sug.get("id"), str):
				out.setdefault(sug["id"], view.get("id"))
	return out


def _iter_items(corpus_pre):
	for view in corpus_pre.get("tools") or []:
		for item in view.get("items") or []:
			if isinstance(item, dict):
				yield view, item


def _proposal_entry(view, sug) -> dict:
	kind = assemble.suggestion_kind(sug)
	payload = {field: sug.get(field)
		for field in sorted(model.MEMORY_PAYLOAD_FIELDS.get(kind, {}))}
	tag = sug.get("self_test_failed")
	if isinstance(tag, dict):
		self_test = {"verdict": "fails", "limb": tag.get("limb"),
			"reason": tag.get("reason")}
	else:
		self_test = {"verdict": "passes", "limb": None, "reason": None}
	sug_id = sug.get("id")
	return {
		"suggestion_id": sug_id if isinstance(sug_id, str) else
			"{}:<no id>".format(view.get("id")),
		"tool_id": view.get("id"),
		"kind": kind,
		"payload": payload,
		"self_test": self_test,
	}


def build_tables(corpus_pre) -> dict:
	"""→ converge-tables.json: deterministic support for agentic decisions —
	the corpus-level relations no single checker could compute, and the
	numbers the seven checks' attestations are verified against. The
	validator computes the raw material and never acts on it; neither does
	this function."""
	check_corpus_versions(corpus_pre)
	sug_tool = _suggestion_tool_map(corpus_pre)

	# C7 (a) — same file, several suggestions. Whether they are one change or
	# several is convergence's call; the collision itself is data.
	file_collisions = []
	for path, sug_ids in sorted((corpus_pre.get("target_file_index") or {}).items()):
		if len(sug_ids) < 2:
			continue
		file_collisions.append({
			"path": path,
			"suggestion_ids": list(sug_ids),
			"tool_ids": sorted({sug_tool[s] for s in sug_ids if s in sug_tool}),
		})

	# §4.5 — a structural change's subject set vs the subjects its siblings
	# document. The gap is visible as data; convergence decides.
	subject_index = copy.deepcopy(corpus_pre.get("subject_index") or {})
	subjects_by_sug = {}
	for subject, sug_ids in subject_index.items():
		for sug_id in sug_ids:
			subjects_by_sug.setdefault(sug_id, set()).add(subject)
	structural_ids = set()
	for view in corpus_pre.get("tools") or []:
		for sug in view.get("suggestions") or []:
			if isinstance(sug, dict) and assemble.suggestion_kind(sug) == "structural" \
					and isinstance(sug.get("id"), str):
				structural_ids.add(sug["id"])
	subject_coverage_gaps = []
	for sug_id in sorted(structural_ids):
		covered = subjects_by_sug.get(sug_id, set())
		if not covered:
			continue
		elsewhere = set()
		for other_id, other_subjects in subjects_by_sug.items():
			if other_id != sug_id and other_subjects & covered:
				elsewhere |= other_subjects
		gap = sorted(elsewhere - covered)
		if gap:
			subject_coverage_gaps.append({
				"cluster": sorted(covered | elsewhere),
				"structural_suggestion": sug_id,
				"subjects_covered": sorted(covered),
				"subjects_documented_elsewhere": gap,
			})

	# C4 step 4 — the same anchor in two tools. Usually correct (one CVE, two
	# affected packages); a flag only when the two disagree.
	by_anchor = {}
	for view, item in _iter_items(corpus_pre):
		anchor = item.get("anchor")
		if not isinstance(anchor, dict):
			continue
		kind, value = anchor.get("kind"), anchor.get("value")
		if kind in model.ANCHORED_KINDS and isinstance(value, str) and value:
			by_anchor.setdefault("{}:{}".format(kind, value), []).append(
				(view.get("id"), item.get("id")))
	anchor_duplicates = []
	for anchor, pairs in sorted(by_anchor.items()):
		if len({tool for tool, _ in pairs}) > 1:
			anchor_duplicates.append({
				"anchor": anchor,
				"item_ids": sorted(item_id for _, item_id in pairs
					if isinstance(item_id, str)),
			})

	# C6 — the three stores. Per-tool agents can only propose the two
	# per-tool kinds; the global store is filled by promotion, which is a
	# convergence-only judgement, so its proposal list is empty by
	# construction and stays in the shape as the reminder of that.
	proposals = {"watch_item": [], "method_note_tool": [], "method_note_global": []}
	proposal_topic_index = {}
	for view in corpus_pre.get("tools") or []:
		for sug in view.get("suggestions") or []:
			if not isinstance(sug, dict):
				continue
			kind = assemble.suggestion_kind(sug)
			if kind not in model.MEMORY_SUGGESTION_KINDS:
				continue
			entry = _proposal_entry(view, sug)
			if kind == "watch-item":
				proposals["watch_item"].append(entry)
			else:
				proposals["method_note_tool"].append(entry)
				topic = sug.get("method_topic")
				if isinstance(topic, str) and topic.strip():
					proposal_topic_index.setdefault(topic.strip(), []).append(
						view.get("id"))

	evidence_findings = [copy.deepcopy(f) for f in corpus_pre.get("findings") or []
		if isinstance(f, dict) and f.get("code") in EVIDENCE_FINDING_CODES]

	items_by_tag = {}
	items_by_severity = {}
	buckets = {}
	total_items = 0
	evidence_entries = 0
	for view, item in _iter_items(corpus_pre):
		total_items += 1
		for tag in item.get("tags") if isinstance(item.get("tags"), list) else ():
			if tag in model.TAGS:
				items_by_tag[tag] = items_by_tag.get(tag, 0) + 1
		severity = item.get("severity")
		if severity in model.SEVERITIES:
			items_by_severity[severity] = items_by_severity.get(severity, 0) + 1
		local = item.get("local")
		if isinstance(local, dict) and isinstance(local.get("evidence"), list):
			evidence_entries += len(local["evidence"])
	pre_accepted = 0
	for view in corpus_pre.get("tools") or []:
		bucket = view.get("initial_review_bucket")
		buckets[bucket] = buckets.get(bucket, 0) + 1
		if view.get("initial_pre_accept"):
			pre_accepted += 1

	# Absent and present-but-empty are DIFFERENT facts, kept apart the way
	# the watch-hit grounding keeps None ("never checked") apart from an
	# empty topic set ("checked, no match"). C6's duplicate-against-store
	# step and the applier's existing-row verification both need to know
	# which one they are running under; `verify_c6` turns "absent" into a
	# W-STORE-UNCHECKED report note.
	stores = corpus_pre.get("stores") or {}
	store_state = {name: store_status(stores, name)
		for name in ("watch_items", "method_notes")}

	return {
		"run_id": corpus_pre.get("run_id"),
		"store_state": store_state,
		"file_collisions": file_collisions,
		"subject_index": subject_index,
		"subject_coverage_gaps": subject_coverage_gaps,
		"anchor_duplicates": anchor_duplicates,
		"proposals": proposals,
		"proposal_topic_index": {k: sorted(set(v))
			for k, v in sorted(proposal_topic_index.items())},
		"evidence_findings": evidence_findings,
		"distributions": {
			"items": total_items,
			"evidence_entries": evidence_entries,
			"items_by_tag": dict(sorted(items_by_tag.items())),
			"items_by_severity": dict(sorted(items_by_severity.items())),
			"buckets": dict(sorted(buckets.items())),
			"pre_accepted": pre_accepted,
		},
	}


# ── the published contract ──────────────────────────────────────────────────
def contract() -> dict:
	"""The whole convergence contract as data — fixtured as
	`contract/convergence.json` so a sibling package (and WP4's renderer)
	asserts against an import rather than a paragraph."""
	return {
		"converge_version": CONVERGE_VERSION,
		"view_version": VIEW_VERSION,
		"max_attempts": MAX_ATTEMPTS,
		"artifacts": dict(ARTIFACTS),
		"ops": {op: {
			"kinds": list(spec["kinds"]),
			"fields": (list(spec["fields"]) if isinstance(spec["fields"], tuple)
				else spec["fields"]),
			"precondition": spec["precondition"],
			"bucket_capable": spec["bucket_capable"],
			"cut": spec["cut"],
			"scope": spec["scope"],
		} for op, spec in OPS.items()},
		"bucket_capable_ops": list(BUCKET_CAPABLE_OPS),
		"prominence_capable_ops": list(PROMINENCE_CAPABLE_OPS),
		"cut_ops": list(CUT_OPS),
		"item_text_fields": list(ITEM_TEXT_FIELDS),
		"suggestion_text_fields": list(SUGGESTION_TEXT_FIELDS),
		"immutable_element_fields": list(IMMUTABLE_ELEMENT_FIELDS),
		"reason_confidence": list(REASON_CONFIDENCE),
		"claim_directions": list(CLAIM_DIRECTIONS),
		"bucket_restrictiveness": dict(BUCKET_RESTRICTIVENESS),
		"gate_consequence_tokens": list(GATE_CONSEQUENCE_TOKENS),
		"prominence_consequence_tokens": list(PROMINENCE_CONSEQUENCE_TOKENS),
		"never_write_fields": list(NEVER_WRITE_FIELDS),
		"priority_axis": PRIORITY_AXIS,
		"demotion": "any strict decrease in priority rank (P0 > P1 > P2 > P3 > not "
			"G-SEC): attributed over prominence_capable_ops, every attributed edit's "
			"reason.body names a prominence_consequence_token (else E-GATE-UNREASONED, "
			"gate kind demotion), disclosed expanded on the Overview; still failing at "
			"attempt 5 → forced conservative with a forced_display snapshot of the pre "
			"priority",
		"corpus_versions": "corpus.pre's contract_version and converge_version must be "
			"exactly this code's (type int, equal) — else CorpusVersionError: "
			"apply_converge raises at every attempt, --check/--submit exit 4 recording "
			"no attempt, build_view/build_tables refuse, assembly renders "
			"artefacts_inconsistent",
		"evidence_finding_codes": list(EVIDENCE_FINDING_CODES),
		"checks": {name: {"ops": list(spec["ops"]), "attests": spec["attests"]}
			for name, spec in CHECKS},
		"codes": {code: {"severity": sev, "phase": phase, "meaning": meaning}
			for code, (sev, phase, meaning) in CODES.items()},
		"degradation_states": list(DEGRADATION_STATES),
		"forced_bucket": FORCED_BUCKET,
		"moved_axes": list(MOVED_AXES),
		"effect_fields": list(EFFECT_FIELDS),
		"effect_safety_fields": list(EFFECT_SAFETY_FIELDS),
		"narrative_enumeration_pct": NARRATIVE_ENUMERATION_PCT,
		"label": {
			"present_iff": "the tool's FINAL state is security_auto or initial_pre_accept "
				"is true, and the tool was not degraded-forced — absent otherwise, "
				"never null-filled",
			"sources": ["judgement", "rule", "judgement_unattributed"],
			"never_ships": "judgement_unattributed — under the gate and loop as specified "
				"a permissive move with no attributable cause is rejected up to five "
				"times and then forced conservative, which removes the label key entirely",
			"headline_max_chars": 140,
		},
	}
