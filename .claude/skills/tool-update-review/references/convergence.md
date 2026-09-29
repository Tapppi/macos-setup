# Convergence — the agentic review of the whole corpus

The operative contract for stage 4. **The contract itself is code**, so it
cannot evaporate into a prompt and cannot drift from what runs:

| Thing | Where |
|---|---|
| The op vocabulary, the checks, every code, the projections | `scripts/converge.py` |
| The five-phase applier, the loop, the gate, the label derivation | `scripts/apply_converge.py` |
| The published fixtures — contract data, view/tables, a full worked submission and its derived effect | `scripts/contract/convergence.json`, `converge.json`, `expected_converge_*.json` |
| The tests | `scripts/test_converge.py` |

The one-line contract:

> **Convergence reads the whole normalized corpus and emits an addressed,
> reasoned modification list. It never writes a corpus. A deterministic
> applier writes `corpus.post.json` beside the untouched `corpus.pre.json`,
> and the renderer receives both — every difference between the two corpora
> is derived by the applier, never declared by the agent, and every bucket
> that moves is attributed back by leave-one-out replay.**

The defect this stage exists to prevent is measured, not theorised: a trim
silently moved `brew:libpq` into `security_auto`, pre-accepted, with 10 CVEs,
and nothing recorded or displayed that.

## 1. The artefacts and the loop

```sh
# stage 3½ — deterministic. Writes corpus.pre.json (frozen for the run),
# converge-view.json (the projection you read) and converge-tables.json.
python3 scripts/apply_converge.py --session "$SD" --prepare \
	--macos-setup-root ~/project/github/tapppi/macos-setup

# your loop: draft → self-check (free, as often as you like) → submit
python3 scripts/apply_converge.py --session "$SD" --check  converge.draft.json
python3 scripts/apply_converge.py --session "$SD" --submit converge.draft.json
```

**A stale corpus is refused before anything runs.** `corpus.pre.json`
records the `contract_version` and `converge_version` it was built under;
unless both are exactly this code's (`converge.check_corpus_versions` — an
int, equal; `"5"`, `5.0`, `true` or a missing key is refused), `--check` and
`--submit` exit 4 **without recording an attempt** — it is an operator
condition, not your error — and `apply_converge()` raises
`CorpusVersionError` at every attempt, the terminal one included, so no
`corpus.post.json` or effect is ever derived from a stale corpus. Re-run
`--prepare --force`. `build_view`/`build_tables` refuse too, and assembly
renders such a session `artefacts_inconsistent`.

`--check` runs all five phases and writes nothing durable — iterate against
it until clean; it costs no attempt. `--submit` is an attempt, counted in
`converge-attempts.json` by the applier, not by you. A submission with
critical findings bounces (exit 1) with the applier's coded findings and the
resolved values — the corpus's actual content, which is usually the whole
fix. **Five attempts, then the run degrades conservatively instead of
dying** (§6). On success the applier writes `corpus.post.json`,
`converge-effect.json` and the accepted `converge.json`.

You read: `converge-view.json` (every item minus its `body`, every
suggestion whole, per-tool derived state — including, since view version 2,
the validator's `security_tier`, its `usage_evidence` record and the
`usage_item_ids`), `converge-tables.json` (the
corpus-level relations no single checker could see), `validation.json`
findings — plus **on-demand reads of `corpus.pre.json` by id** for any
`body`, `config_status.detail` or link you need. §4's quote rule makes
skipping that read detectable.

## 2. The submission

```jsonc
{
  "run_id": "…",                      // the session's — echoed, checked
  "converge_version": 3, "view_version": 2,
  "corpus_digest": "sha256:…",        // copied from converge-view.json
  "attempt": 1,
  "checks": [ /* one attestation per check, ALL SEVEN, mandatory — §5 */ ],
  "edits":  [ /* §3 */ ],
  "ledger": { /* §5 C6 — the three-store disposition */ },
  "corpus_effect": { /* §7 — what you believe you did; recomputed */ }
}
```

## 3. The edit record

```jsonc
{
  "edit_id": "cv-014",                 // "cv-" + ordinal, unique in the run
  "check": "C3-security-only",         // which §5 check produced it
  "op": "retag",                       // the 11-op vocabulary below
  "target": { "tool_id": "brew:libpq", "kind": "item",   // item|suggestion|proposal|tool
              "id": "brew:libpq#release:…", "field": "tags" },  // field null for whole-element ops
  "precondition": { "before": ["chore"] },   // field ops: the CURRENT value, exact
  "quote": "…",                        // every cut: verbatim from the element (§4)
  "after": ["security"],               // absent on delete/flag
  "changed_fields": null,              // merge only: the paths `after` changes
  "bucket_claim": { "moves_bucket": true, "expected_from": "security_mixed",
                    "expected_to": "security_auto", "direction": "permissive" },
  "reason": { "headline": "≤140 chars — the label renders it verbatim",
              "body": "…",             // cuts: the four things below
              "rule_ref": "references/research.md §…",
              "confidence": "high",    // high | medium | low — low is a legitimate output
              "destination_id": null },  // where the fact went, when an id exists
  "supersedes": [], "requires": []     // ordering; a rejected requirement rejects you too
}
```

The 11 ops (`converge.OPS` is authoritative — kinds, fields, preconditions):

| op | what | notes |
|---|---|---|
| `delete` | removes an item/suggestion/proposal | quote required |
| `merge` | replaces one item wholesale | `after` is the WHOLE merged element; `changed_fields` declares exactly which paths differ — an `after` that quietly changes more is `E-APPLY-SCOPE`. The absorbed sibling is its own `delete`, tied by `requires`. Never rewrite `id` |
| `retag` / `rerate` / `redirect` | `tags` / `severity` / `local.direction`·`local.effect` | closed vocabularies; convergence may not write outside them |
| `add` | appends ONE memory suggestion (`watch-item`/`method-note`) | never an item, never an `edit`/`structural` — an authored finding has no research behind it (§8) |
| `trim` | shortens a text field | `after` must be an order-preserving token subsequence of `before` — drop words, never rewrite them |
| `reword` | rewrites prose that says the same thing better | changing what an item *claims* is authoring — `flag` instead |
| `move_evidence` | re-homes one `local.evidence` entry into `citations[]` | `precondition.before` is the entry; `after.citation` the citation object |
| `annotate` | appends to `convergence_notes[]` on an item/suggestion/tool | inert — no derived field reads it; `precondition.before` is the current note count |
| `flag` | records a finding, writes nothing | the honest cheap outcome; a run with zero flags across a fleet is less plausible than one with five |

**Bucket-capable** (must carry `bucket_claim`; the rest must omit it):
`delete, merge, retag, rerate, redirect, add`. **Cuts** (full reason record +
quote): `delete, merge, retag, rerate, redirect`.

A cut's `reason.body` must contain: (1) what the item said — the `quote`
carries it; (2) why it does not earn its place, with `rule_ref` when a rule
applies; (3) what happens to the fact — deleted, merged into which id,
surviving at lower severity, re-homed; (4) **the counterfactual** — what
decision it could not have changed. A body that only restates the op is a
bad cut whatever the cut's merits.

## 4. The quote rule — read before you cut

The projection omits `body` (35% of the measured corpus). **Every cut's
`quote` must be verbatim-contained in the element being cut, and when the
element has `has_body: true` the quote must come from `body`** — text you
can only have by reading `corpus.pre.json`. A requirement to quote text you
must go and read is decidable; a requirement to have read it is not. Quoting
element X while addressing element Y is a rejected edit, so the quote is
also the addressing guard.

## 5. The seven checks

One attestation per check in `checks[]`, all seven, every run. `scanned`
counts are recomputed by the applier — skim 20 tools and report 78 and the
arithmetic catches it. Each check may emit only its listed ops
(`converge.CHECK_OPS`); everything else it finds becomes a `flag`.

**C1 — evidence hygiene** (`move_evidence`, `flag`). Work through
`converge-tables.json.evidence_findings`: prose in `evidence[]` moves to
`citations[]`; a path you cannot place is a `flag` naming what it would take
to resolve; an item claiming a live risk (`reaches`+`risk`) with empty
evidence is a `flag` — you cannot go and find the path (§8). Never `delete`:
a citation in the wrong field is a filing error, not noise. The table also
carries I-23's two usage codes (`converge.EVIDENCE_FINDING_CODES`):
`E-USAGE-UNGROUNDED` (a `usage` entry whose path, file or quote did not check
out) and `W-USAGE-INSTALL-ONLY` (a quote that is only the tool's install line
or a comment) — neither confirmed usage, so neither earned the fix a
highlight; a `move_evidence` of such an entry is filing, a `flag` is fine,
and you can never make one ground (the applier re-grounds nothing). Attest
`scanned.tools/items/evidence_entries` and `findings ==
len(evidence_findings)`.

**C2 — tags → visibility** (`retag`, `rerate`, `redirect`, `trim`, `reword`,
`delete`, `merge`, `flag`). Per item: does the tag set describe the change, or
where the vendor filed it — a trust-boundary move carries `security` whatever
the heading. Is severity consistent with `local.direction` — `notable`+ with
`does_not_reach` claims prominence for a non-event. Would a reviewer who
believed the opposite decide differently — if not, `rerate`; deletion is for
duplicates and strict subsumption, with the full record. Nothing is promoted to
fill a slot. No project-internal-maintenance filter lives here (the checker is
told not to emit such items at all); one that escapes is handled by the
counterfactual like any other item. Attest `scanned.items` and `clean` — clean +
your edited items must account for every item.

**C3 — security-only labelling** (`retag`, `rerate`, `flag`). For every
tool with `bucket_inputs.security_only == true`: identify the load-bearing
items, ask C2's questions independently of the bucket, fix the item and let
the bucket follow. **Never fix a tag to move a bucket** — a right tag under
a wrong bucket is a `flag` against the rule. Attest `security_only_tools`:
every such tool, its CVE count, a one-line verdict — enumerating the set in
full IS the check.

**C4 — notable-security tagging** (`redirect`, `rerate`, `flag`). Every
`security`-tagged item: a rating with basis `unrated` presented as graded is
a `flag`; an item whose point is the fix does NOT reach here must not occupy
a display slot ahead of one that does; an id-less real finding must not be
evicted by graded-but-unreachable CVEs; `anchor_duplicates` is a `flag` only
when the two tools disagree on severity or direction. Attest the four
scanned counts. Selection stays deterministic downstream.

**C5 — auto-approval** (`retag`, `rerate`, `redirect`, `flag`). For every
tool at `security_auto` or `initial_pre_accept: true` — one pass, in full,
no sampling: *if this upgrade were applied unattended and went wrong, would
the items in front of me have said it might?* If no, fix whichever item is
mis-tagged or mis-rated — **there is no op that writes a bucket, by
construction**. Attest one row per tool: `deciding_input`, `verdict`. This
attestation feeds §9's label.

**The set is larger since G-SEC** — every positively identified security
fix whose tier is accepted (P1/P2/P3) is pre-accept-eligible whatever its
risk level, so expect ~25–40 rows per run rather than ~9. For a G-SEC tool
`deciding_input` is `security_tier`, and the question is the same: is the
`nature: fix` real (a cited upstream fix, not a boundary move), and did the
checker miss a `required` edit or an `incompatible` item that would have held
it? Fix the item — a `retag`/`rerate`/`redirect` — and the tier follows.

**C6 — memory: three stores** (`delete`, `add`, `flag`, `reword`). Stores: global
method notes (rare **by definition** — only convergence can see a note is
general), per-tool method notes (many), watch items (many).
Procedure: (1) **route before you judge** — a proposal no future changelog
delta could surface is a method note; re-home it (`delete` + `add`, ledger
`rehomed_to_method_note`), never drop it for being mis-filed. (2) **Review
every self-test-tagged proposal in both directions** — the tag never removed
anything; confirming the drop is a `delete` + ledger row with
`restored: false`; disagreeing is a ledger row with `restored: true` and the
reason the self-test was wrong. Both are first-class. (3) Verify a passing
self-test rather than re-deriving it. (4) Duplicate against the store on
`(tool_id, topic)` and against this run's proposals. (5) Promote to global
only on cross-tool evidence; demotion runs the same way. Cross-tool evidence
is a shared topic in `proposal_topic_index`, **or several per-tool
proposals that record one pattern in their own tool's words** — the first
real run had five notes (karabiner-elements, tailscale-app, spotify,
obsidian, gcloud-cli) each saying an app self-updated past its Caskroom
version, no two sharing a topic. Promotion moves ONE proposal, so say the
general thing: promote it (`promoted_to_global`), `reword` its
`method_topic` and `method_note` into the statement that holds for every
tool (target kind `proposal`, `precondition.before` the current text), and
dispose of each sibling — `cut` with a `delete` when the global note says
all it said, `kept` when it also carries a path or step only that tool has.
A global note is never one tool's paths under the global key. (6) **No number** — no store has a target count. Convergence proposes;
the stores are written only at render with the user's disposition. Attest
via the `ledger`: every proposal in exactly one disposition; every existing
watch item for a tool in the run gets an `existing` row with
`fired_this_run` (the applier recomputes it against the grounded hits) and
`used_correctly` — a hit claimed with no item behind it, or a plain match
with no hit labelled, is a `flag`. `converge-tables.json.store_state` says
what each store's snapshot actually is — `present`, `absent`,
`unreadable` or `nonexistent`: **absent, present-but-empty and
present-but-unreadable are three different facts** — the same distinction
watch-hit grounding draws between "never checked" and "checked, no match" —
and `absent` and `unreadable` each ship a `W-STORE-UNCHECKED` report note
carrying their own remedy (copy the snapshot vs fix the copied file) rather
than passing as an empty store. `--prepare` tells a store nobody could copy
from one nobody did: with no session snapshot it checks only whether the
live store (`${XDG_STATE_HOME:-~/.local/state}/tool-update-review/`)
exists — missing there too is `nonexistent`, which grounds nothing and
ships no note, because step 3 copies a store only when it exists; present
there is `absent`, the never-copied case.

**C7 — cross-tool collisions** (`delete`, `annotate`, `reword`, `trim`,
`flag`). Per `file_collisions` cluster: are these one change or several —
independent edits to different lines about different packages are not a
collision. If one change: keep the best-shaped suggestion, `delete` the
rest, `annotate` the losing tools so the finding is not lost. Check a
structural fix's `subjects[]` against the cluster (`subject_coverage_gaps`);
extend its diff only **from the cluster's own text**, else `flag`. Two diffs
quoting each other's lines: `reword` each onto its own anchor. Attest
`clusters_inspected`, `resolved` (with each surviving suggestion id),
`flagged`.

## 6. The gate, the loop, and degradation

The applier recomputes every derived axis over both corpora
(`derive_tool_state` — the same functions the validator ran). For each moved
tool it replays your edits leave-one-out; **`attributed_to` is computed,
never declared**. A tool entering `security_auto`, or `initial_pre_accept`
going false→true, must have attributed edits that (a) exist
(`E-GATE-UNATTRIBUTED`), (b) declared `moves_bucket: true`
(`E-GATE-UNDECLARED`), (c) name the consequence in `reason.body`
(`E-GATE-UNREASONED` — the body must say `security_auto` / pre-accept in so
many words; `converge.GATE_CONSEQUENCE_TOKENS`). Permissive direction only:
a move OUT of an auto bucket is reported, never blocked, and nothing caps
how many reasoned moves a run may make.

**The demotion gate (G-SEC).** A positively identified fix's display
**priority** (P0 > P1 > P2 > P3 > not G-SEC) is a derived fact the page leads
with, and losing it is consequential. The applier compares every tool's
priority pre vs post (read through `converge.axis_value` — priority lives
inside `security_tier`, which `MOVED_AXES` does not include, so
`tools_moved` keeps its meaning and you never predict a tier move). **Any
strict decrease is a demotion**, and for each one the attributed edits must
(a) exist (`E-GATE-UNATTRIBUTED`) and (b) name the consequence in
`reason.body` — `priority`, `highlight` or `prominence`
(`converge.PROMINENCE_CONSEQUENCE_TOKENS`; `E-GATE-UNREASONED`, gate record
`kind: "demotion"`). `E-GATE-UNDECLARED` does not apply: `moves_bucket` is
about the bucket. Attribution for priority replays
`converge.PROMINENCE_CAPABLE_OPS` — the bucket-capable ops **plus
`move_evidence`**, because priority also reads `local.evidence` (a usage
confirmation): moving the grounded `usage` entry out of an item removes its
confirmation, and a `move_evidence` needs no `reason.body` as an op but does
as a demotion. A merge that carries a grounded entry to another item keeps
the confirmation (the record is keyed on the authored entry); a forged entry
confirms nothing. Every priority change — either direction — is written to
the tool's effect block as `security_priority: {from, to, lost,
reasons_from, reasons_to, attributed_to, edits}` (each edit's headline and
quote), and the report page discloses the losses, expanded, in "Lowered by
convergence".

**The tier is re-derived, never written.** After your edits the applier
recomputes `security_tier`, `pre_accept_bars` and `usage_item_ids` through
the model's own functions over the CURRENT items and the carried
`usage_evidence` record, and its self-check compares its re-derivation of
corpus.pre's tier and bars against the validator's (`E-APPLY-INTERNAL`).
Element re-validation runs in the explicit **no-I/O mode**
(`validate_items.NO_IO_RESOLVER`): no path is resolved and no usage quote is
re-grounded; the resolver-dependent codes (`E-EVID-404`, `W-EVID-ROOT`,
`E-USAGE-UNGROUNDED`, `W-USAGE-INSTALL-ONLY`) are excluded from the pre/post
comparison on both sides. **I-22 after edits:** a `required`/`proposed`
reading depends on the current severity of the items an edit `serves`, so
I-22 is re-run over every suggestion of every tool whose items you edited;
a code present post and not pre (`E-SUG-REQUIRED-UNGROUNDED`,
`E-SUG-SERVES-UNRESOLVED`, `E-REQUIREMENT-CONTRADICTED`) is a `W-EDIT-SCHEMA`
note implicating the item edit — not a bounce: a reasoned rerate of an
`incompatible` item is legitimate, an ungrounded `required` still reads
required, and any acceptance or prominence change it causes meets the
permissive gate or the demotion gate.

The loop: any critical finding bounces the whole submission while attempts
remain — fix and resubmit; rejects are per-edit and transitively closed over
`requires`, and nothing is silently dropped. **At attempt 5 the run ships
anyway** — never a silent pass, never a dead run:

| State | When | What ships |
|---|---|---|
| `degraded_gate` | the gate — permissive or demotion — still fails on ≥1 tool | The submission applies; each gate-failing tool is **forced** to `pre_accept: false` and to the strictest of its pre bucket, the bucket it would have shipped and — for a security-bearing tool only — `security_mixed` (`converge.forced_bucket`: forcing never loosens a tool, and never files a non-security tool as security) — the one place a bucket is written rather than derived, written by the applier, recorded as forced (`kinds`: every gate it failed, `permissive` and/or `demotion`, each with its first code in `code_by_kind`; `kind`/`code` are the first entry's, kept for older readers). A tool that both entered `security_auto` and lost priority is explained as both. Its tier is recomputed with the `forced-conservative` hold; a G-SEC tool's forced record also carries `forced_display` — its PRE-convergence priority, reasons and labels — which the page shows (display only; the tier stays the pure recomputation, even when the forced view is no longer G-SEC). Such a tool carries **no** auto-update label. |
| `degraded_unapplied` | the submission itself cannot be resolved | `corpus.post.json` **is** `corpus.pre.json`; every rejected edit is listed with its code. |

Scope- or schema-violating edits at attempt 5 are excluded individually and
listed as standing rejects; the sound edits still apply. Either way
`convergence_status` (in `converge-effect.json`, mirrored for the report)
carries the state, a diagnosis headline and body, the attempt log, the
degraded tools with what they would have been, and the standing rejects.

## 7. `corpus_effect` — the knowledge probe

State what you believe you did: `edits_by_op`, items before/after,
`severity_after`, `severity_at_warning_or_worse` (with `pct_change`),
`tag_security_items`, `tools_moved` (permissive/restrictive/lateral),
`pre_accept` before/after, and a mandatory `narrative`. The applier
recomputes every number and ships its own; a mismatch on `tools_moved` or
`pre_accept` is critical — an agent that miscounted its own permissive moves
has not understood what it did — while any other mismatch is a report note.
If warning-or-worse falls past −20% the narrative must name every affected
tool individually: a reporting threshold, never a cap. There is no maximum
on any of these numbers — a large cut that is individually reasoned and
aggregately reported is a good run.

## 8. Scope discipline — what convergence must not do

Convergence may only use facts already in the corpus plus the relations the
validator computed. Every claim in every reason must be quotable from
`converge-view.json`, `corpus.pre.json` or `converge-tables.json`.

- **Never fetch** — no changelog, release page, advisory, CVE record. Doubt
  about an item's accuracy is a `flag`, not a lookup.
- **Never touch the live machine or the setup repos** — no `brew info`, no
  `xattr`, no grepping the repos for a cited path. Path resolution is the
  validator's job; its findings arrived in the tables.
- **Never author a finding** — `add` exists for re-homed and promoted memory
  notes only. A `reword` that changes what an item claims is authoring.
- **Never write a bucket, `pre_accept`, `risk_level`, `impact`,
  `security_only`, an item `id`, `security_tier`, `usage_evidence`,
  `usage_item_ids`, a suggestion's `serves_item_ids`, or any corpus file** —
  there is no op that can (`converge.NEVER_WRITE_FIELDS`; tool-level derived
  fields are addressable by no op, and a suggestion takes only text edits,
  `delete` and `annotate`), and stage 5 recomputes all of them from the edited
  items — except `usage_evidence`, the validator's grounding record, which it
  carries.
- **Never delete on a regex or heuristic** — if a cut needs no reason it is
  a rule, and rules do not live here.
- **Never pad, never cap, never cut for appearance, never resolve a
  checker disagreement by deleting it** — two tools contradicting each
  other is a `flag`.
- The one command you may run is `apply_converge.py --check` (and the
  `--prepare` it depends on): it reads only the session directory.

Before writing any edit, answer in one line: *which file and which field is
this claim in?* If the answer needs a fetch, or is "I worked it out", the
edit becomes a `flag`.

## 9. The auto-update label (data contract for the report)

Derived by the applier onto `converge-effect.json.tools[tool_id]`, never
declared: `auto_update_label` is present **iff** the tool's final state is
`security_auto` or carries `initial_pre_accept: true` (absent otherwise —
never null-filled; a degraded-forced tool never carries one). `source` is
`"judgement"` when leave-one-out attributes the move to your edits — the
label then carries the causing edit's `reason.headline`, its `body`
verbatim, its `confidence`, the `quotes` of what was cut, and a
`counterweight` (CVE count, worst rating, items removed/re-rated) so the
card shows both sides; `"rule"` when the deterministic path alone put it
there — its reasoning names the security tier and its reasons for a G-SEC
tool, and `security_only`/`impact` for any other. `"judgement_unattributed"`
exists for the renderer's completeness and never ships while the gate stands. Report-side rendering is
`references/rendering-report.md`'s concern; the per-tool blocks and
`convergence_status` in `converge-effect.json` are the hand-off.

## 10. Known boundaries, stated

- Nothing here detects **silent loss** — content a checker never wrote.
  Cross-run comparison is out of scope for the whole pipeline — every review
  looks at the standing state; within
  a run, an element no edit names cannot disappear at this stage, and
  §7's aggregate makes a large movement loud even when its cause is
  upstream.
- `report.json` does not yet carry `convergence_status` or the per-tool
  `convergence` blocks — assembly and the report page read the
  pre-convergence corpus until the report-page pass wires
  `corpus.post.json` and `converge-effect.json` through. The artefacts and
  their shapes are final; the merge is the renderer's half of the pre/post
  comparison that makes convergence's work checkable.
- A tool whose `validator_error` is set keeps its recorded conservative
  axes; edits to its items cannot move it anywhere, in either direction.
  Its item-derived id exports (`security_display_item_ids`,
  `watch_hit_item_ids`, `flags`, the bars) ARE re-derived after edits, so
  `corpus.post.json` never hands the renderer an id a delete removed.
