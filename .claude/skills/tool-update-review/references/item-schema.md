# The Item Schema and the Deterministic Validator

The per-tool checker's output contract, and the validator that consumes it.

**This document is normative prose. The contract itself is code**, so it cannot
evaporate into a prompt and cannot drift from what actually runs:

| Thing | Where |
|---|---|
| The model — vocabularies, groups, ids, **the ordering and the comparator** | `scripts/items.py` |
| The six stages, the twenty invariants, the finding codes | `scripts/validate_items.py` |
| The published fixtures a sibling package imports and asserts against | `scripts/contract/` (see its `README.md`) |
| The tests | `scripts/test_items.py`, `scripts/test_validate_items.py` |
| The design authority | `$XDG_STATE_HOME/tool-update-review/REDESIGN.md` and `HANDOFF.md` — **not in this repo.** Every `§A`/`§C3`/`§L1`/`criterion N` citation in the skill resolves there |

Design record and the measurements behind every rule below:
`~/.local/state/tool-update-review/scratch/design/item-schema.md`.

---

## 0. The governing constraint

> The deterministic layer **validates, normalizes, counts, buckets and
> calculates impact. It never deletes, trims or re-rates an item on a regex or
> heuristic rule.** Judgement is reserved for convergence.

The defect this exists to prevent is measured, not theorised. A rule-driven
trim silently moved `brew:libpq` into `security_auto`, **pre-accepted, with 10
CVEs**, and nothing said a rule had put it there. So, concretely, in this layer:

- a malformed evidence string is **reported and kept**, never moved to
  `citations[]` — auto-moving is a regex deciding what a field means;
- two items deriving one id are **reported and both kept**, never merged —
  merging is deletion plus a severity choice;
- an unrecognized tag is **reported and kept**, never dropped;
- an over-long title is **reported**, never truncated;
- a wrong-typed array member is **quarantined on the tool**, whole and
  untruncated, never dropped — from any array it came from;
- `initial_review_bucket` is a **baseline for convergence to review**, not a
  decision.

Second standing rule: **degradation is per tool and loud.** No malformed input
aborts a run instead of costing one tool. Every stage runs inside a per-tool
boundary and loading inside a per-file and per-entry one.

---

## 1. Scope: what counts as an item

**Items are outward-facing changes.** Project-internal maintenance — repo
upkeep, convention changes, documentation updates — **never becomes an item.**

This is a scoping rule about what a checker writes, not a trimming rule applied
to what it wrote, so it does not conflict with "no final bucketing here" or with
convergence being the final cutting surface. **There is deliberately no filter
for it in the validator**: a regex that deleted "internal-looking" items would
be exactly the banned behaviour. It is enforced where it belongs — in the
checker's own guidelines, and in this schema.

**A checker's guidelines are where this is enforced**: `references/research.md`
§Items Are Outward-Facing Changes states the rule to the agent that writes
items, in the terms it decides in.

**`intel.Brewfile` is out of this tool entirely.** Not a source of candidates,
no compatibility checks against it, no suggestions targeting it, and it does not
appear in the report. I-17 makes that a runtime check.

---

## 2. The item

One array replaces four. Each element is **one real change**, carrying **tags**
(a closed set of eight), **one severity**, and two optional blocks:

- `change` — the upstream fact: version, one verbatim citation, a link ref.
- `local` — the finding about *this* setup: direction, effect, evidence,
  citations.

```jsonc
{
  "id": "brew:sops#issue:getsops%2Fsops%232245",  // validator-assigned
  "id_stability": "anchored",                      // validator-assigned
  "anchor": {"kind": "issue", "value": "getsops/sops#2245"},   // REQUIRED

  "title": "3.13.3 fixes a wrong MAC computed when decrypting YAML sequences with a comment",
  "body":  "3.13.2 — the installed version — mis-computes the MAC, so any file …",
  "tags": ["fix"],
  "severity": "warning",

  "change": {"version": "3.13.3", "citation": "Fix a bug introduced in 3.13.2 …", "link_index": 0},

  "local": {
    "direction": "does_not_reach",
    "effect": "benefit",
    "statement": "The defective binary is what runs here, but nothing hands sops a file …",
    "evidence": [{"path": "Brewfile", "lines": [358]},
                 {"path": "dotfiles/config/git/config", "lines": [[189, 191]], "note": "textconv"}],
    "citations": [{"kind": "command", "text": "sops --version → 3.13.2", "url": null}]
  },

  "security": {"cve_id": null, "advisory_id": null, "rating": "unknown",
               "rating_basis": "unrated", "exploited_in_wild": false}
}
```

The full field table is `items.contract()["fields"]`, published as
`contract/contract.json`.

### 2.1 `title` and `body` — the split

**A title is readable at a glance.** Measured on the recorded run, `title` ran a
median of **142 characters and a maximum of 578** — a `body` that landed in the
wrong field, and the largest single lever on the report's default surface.

- `title` — required, one line, **≤ 120 characters**. The fact and nothing else.
- `body` — optional. Everything that does not fit: the why, the how, the
  consequence.

The bar is checked and **reported** (`W-TITLE-LONG`). It is never enforced by
truncation, because truncating hides a data defect behind a clamp.

### 2.2 Tags — the closed set of eight

| tag | means | excludes |
|---|---|---|
| `security` | fixes or mitigates a vulnerability, or moves a security boundary: auth, sandboxing, crypto, trust, permissions, signing | a bug fix with no security consequence; a "hardening" claim with no described flaw (→ `chore`) |
| `fix` | corrects incorrect behaviour, non-security | a change that only alters an interface (→ `breaking`) |
| `feature` | adds capability or an interface that did not exist | re-enabling something that was broken (→ `fix`) |
| `breaking` | removes, renames or changes the contract of something that works today | a deprecation where the old path still works (→ `deprecation`) |
| `deprecation` | announces future removal; old behaviour still works in the target version | actual removal (→ `breaking`) |
| `perf` | measurable performance or resource change, no interface change | a perf change that also changes an interface (add `breaking` too) |
| `packaging` | how the tool is built, signed, distributed or depends: new dependency, new minimum OS, notarization, tap/formula/keg-only changes | an upstream code change with no packaging consequence |
| `chore` | a real, cited change with no consequence for any reader: docs, CI, dependency bumps with no behavioural note | anything with a described symptom or interface effect |

Tags are **not exclusive**. `security` + `packaging` is the cask-quarantine
story; `feature` + `breaking` is a new interface that removes an old one.

**There is no `notable` tag** — `notable` is already a *severity*, and two
spellings of one concept is the conflation this schema removes. Visibility is
`severity >= notable`.

**`chore` earns its slot** as the explicit home for what the noise floor used to
delete. Nothing is deleted deterministically any more; `chore` lets a checker
*say* "this is real and inconsequential" so the page can collapse it and
convergence can still see it.

**An unrecognized tag** is kept verbatim on the item, ignored by every derived
computation, and raises `E-TAG-UNKNOWN`. An item with no recognized tag raises
`E-TAG-NONE`, groups under `notes` for rendering only, and is never dropped.

**Content group** is derived, never authored:
`security` → security; `breaking`/`deprecation`/`fix` → fixes;
`feature`/`perf` → features; `packaging`/`chore` → notes. Precedence in that
order.

### 2.3 Severity — on the item, never on a category

`info | notable | warning | incompatible`. It means **"how much does this matter
to this machine"**, which is why it lives on the item: `security` at `info` (a
fix for a path this setup never executes) and `security` at `warning` (a live
precondition here) are both common and must not collapse.

| severity | means | requires |
|---|---|---|
| `incompatible` | after the upgrade, something that works here today stops working | `direction == reaches` **and** `effect == risk` (I-2) |
| `warning` | a behaviour change here the reader must know before upgrading, or a security fix with a live precondition here | `local` present (I-3) |
| `notable` | real and worth reading; no action needed | — |
| `info` | bookkeeping | — |

The two requirements are **definitional consistency checks, not heuristics**:
they compare two fields the checker wrote against each other. A violation is
reported and nothing is changed.

### 2.4 `direction` and `effect`

`direction` — `reaches` | `does_not_reach` | `unclear`: does this land on
something this setup runs?
`effect` — `risk` | `benefit` | `none`: is that good or bad for me?

Both required whenever `local` is present. `unclear` is a legitimate, honest
value — the checker looked and could not decide — and is distinct from `local`
being absent, which means nobody looked.

These two exist because they remove a heuristic from deterministic code. The old
`compute_impact` used `category != "security"` as a proxy for `effect != "risk"`,
with the wart documented inline; the proxy is also wrong in one direction, since
a security change *can* be a risk here. With `effect` explicit, impact reads it
directly.

`direction` is also what the old `notable[]` clause 3 needed and did not have:
it promoted "any security relevancy at notable or worse" with no direction test,
and openssh filled all three of its slots with items whose own summaries say the
fix does not reach this machine.

---

### 2.5 `watch_hit` — a stored watch item fired

```jsonc
"watch_hit": {
	"topic": "shell-integration / session recording"
}
```

Set **iff** this item answers a stored watch item for this tool. `topic` is
the **verbatim** `topic` string of the stored watch item, copied
character-for-character from what the checker was handed in
`{{STANDING_NOTES}}` — not a paraphrase, not a restatement, not the checker's
own summary. The validator grounds it against the session's watch-item
snapshot (I-20): exact match after `.strip()`, per tool — a topic stored for
another tool does not ground a hit here. Extra keys inside `watch_hit` are
tolerated and kept, exactly as inside `change`, `local` and `security`.

This is the **read** side of the watch-item loop — "a watch item that already
exists fired on this release". The **write** side — "please store a new watch
item" — is a suggestion of `kind: "watch-item"` carrying
`watch_topic`/`watch_note`, and conflating the two is what produced the retired
`Watch item hit:` literal channel. A checker that writes `watch_topic` when it
means "this fired" has signalled nothing and proposed a duplicate store entry;
one that writes `watch_hit` when it means "please remember this" references a
topic that does not exist and raises `E-WATCH-HIT-UNGROUNDED`. Both directions
are now detectable, which is the purpose of making the channel structured.

One `watch_hit` per item. A change that answers two stored watch items is
written as one item naming the more specific topic, with the second named in
`body`. The field is deliberately not an array: an array would need per-member
quarantine semantics for a case that has never been observed, and an object
can be widened later without breaking a reader.

A hit carries a `local` block, because a hit is a statement about this setup
(`E-WATCH-HIT-NOLOCAL`), and earns at least `notable` (`W-WATCH-HIT-UNRAISED`
— reported, never bumped: re-rating is convergence's).

What `watch_hit` does **not** do, stated because every one of these would
re-open a promotion route: it is not read by `recompute_flags`,
`allowed_for_security_only`, `compute_impact`, `compute_security_only` or
`compute_risk_level`; it is not a tier of `item_sort_key` or
`security_display_sort_key` — two items identical on every tier sort by id,
and prominence is delivered by the 70-point `watch_item_hit` highlight,
which reads the view's `watch_hit_item_ids` export, i.e. **grounded hits
only** — an ungrounded, malformed or unchecked hit bars pre-acceptance
(fail-closed) but earns no prominence — never by reordering a card's body.
An item badge for a hit is **not yet built**: the template renders no
watch-hit marker today. When it is built it must read the same
`watch_hit_item_ids` export, never the raw `watch_hit` claim — wiring it to
the claim would re-open the unvalidated channel this field exists to close.

## 3. The evidence split

*Splitting a field is better than conflating.* Applied literally, because 272 of
275 warning lines in the recorded run were `evidence not found` — of which
**exactly one was a genuinely wrong path**.

### `local.evidence[]` — paths only, as objects

```jsonc
{"path": "tasks/install.sh", "lines": [[56, 104]], "note": "install_podman_intel"}
```

`path` is repo-relative, `~`-anchored or absolute: no spaces, no prose, no
trailing punctuation, no parenthetical. `lines` members are an int or a
two-element `[start, end]`. `note` is a short human label and is **never
load-bearing** — the validator ignores it.

The object is normalized **by copy**: any other key a checker writes survives
untouched. `lines: []` collapses to absent, since the two mean the same thing;
a `lines` list the validator cannot fully read is reported and **left exactly as
written**, because the normalization is licensed only when it succeeds and
half-rewriting a locator list loses the part it could not read.

A bare string is accepted and normalized iff it is `path`, `path:LINE`,
`path:START-END` or `path:L1,L2` (that last form is in the grammar because the
run already produced it and today's regex silently fails to strip it). The
trailing ` (description)` parenthetical is gone: it *was* the conflation, and it
now lives in `note`.

Anything else raises `E-EVID-MALFORMED`, is **kept verbatim**, and is excluded
from path resolution. It is **not** moved to `citations[]`.

**Resolution** has three outcomes:

| code | when |
|---|---|
| — | resolves under a configured root |
| `W-EVID-ROOT` | well-formed, resolves only under a **known-but-unconfigured** sibling repo |
| `E-EVID-404` | well-formed, resolves nowhere |

`W-EVID-ROOT` is separate because 7 of the run's 9 bare-path failures were
`tieto/…` — a real repo the checker legitimately read that is simply not in the
root list. That is a *configuration* finding about the run, not a checker bug,
and conflating the two is how the one real defect got buried.

### Prose has two homes

**`change.citation`** — exactly one string, the **verbatim** upstream text the
item is about. Required whenever `change` is present, never `null` /
`"none found"` / `"not changelog-driven"`. Never path-checked, never fetched.

**`local.citations[]`** — zero or more `{kind, text, url}` supporting the local
finding. `kind` is one of `changelog`, `release_notes`, `advisory`,
`upstream_source`, `command`, `observation`, `prior_review`. The validator checks
the enum, non-empty text and URL *syntax*. **It never fetches a URL and never
resolves a citation against the filesystem** — that is the point: a citation is
not checkable, so it is not checked, so it cannot warn.

`upstream_source` is a real category the run already produced — a path inside
the upstream project will never resolve locally and is excellent evidence.

`config_status` gets the same split. It is today's worst offender: 133 of the
272 warnings, against 286 evidence strings.

---

## 4. Structural changes

Structural Brewfile and setup changes need an audit trail, so `kind:
"structural"` joins `edit`/`upgrade`/`watch-item` on a suggestion, carrying a
typed `structural` block.

Its load-bearing field is **`subjects[]` — the entities the change is *about*,
separate from the files it edits.** The grounding case: four groups
independently produced cask-quarantine suggestions; three edit
`CLAUDE.md`/`AGENTS.md` and the fourth adds a `setup.sh quarantine` dispatch
whose body covers **two of the four casks the others document as needing it**. A
`target_files` intersection cannot see that — zero file overlap. The collision is
**semantic: same subject, different files.**

```jsonc
"structural": {
  "op": "task_add",
  "subjects": [{"type": "cask", "name": "codex"}, {"type": "cask", "name": "cursor-cli"}],
  "manifest": null,                       // "Brewfile", or null. NEVER intel.Brewfile
  "from": null,
  "to": {"type": "task", "name": "setup.sh:quarantine"},
  "anchor": {"file": "setup.sh", "after": "elif [[ \"${1}\" = \"herdr\" ]]"}
}
```

`Ref` = `{type, name}` with type in `formula`, `cask`, `tap`, `mas`, `task`,
`runtime`, `section`.

| `op` | required | precondition the validator checks |
|---|---|---|
| `manifest_add` | `manifest`, `to`, `anchor.section` | `to` not already in the manifest; the section exists |
| `manifest_remove` | `manifest`, `from` | `from` is in the manifest |
| `manifest_replace` | `manifest`, `from`, `to` | `from` in, `to` not |
| `manifest_move` | `manifest`, `from`, `anchor.section` | entry exists; its current section differs |
| `tap_add` / `tap_remove` | `manifest`, `to`/`from` (type `tap`) | tap line absent/present as required |
| `install_method_change` | `from`, `to` with distinct types | `from` resolves in its current mechanism |
| `task_add` / `task_change` | `to` (type `task`), `anchor.file` | the file exists; for `task_add`, the subcommand token is not already dispatched |

`diff_preview` stays, demoted: it is a **rendering** of the op for the human, not
its source of truth.

A precondition that cannot be checked — an unreadable manifest — raises
`W-STRUCT-UNCHECKED`. It is never silently treated as satisfied: a silent skip
is how a structural fix covering the wrong subject set stays invisible. A
precondition is also **not** claimed against a `from`/`to` that already failed
its own shape check: that finding is already reported, and dereferencing a Ref
with no `name` would trade it for a degraded tool.

**Known limit on `anchor.section`.** The Brewfile marks a top-level section with
a `## ====` rule above and below its title, but writes subsection titles and
ordinary prose comments with the same `## ` prefix. There is no syntax that
separates the last two, so `anchor.section` is accepted against any `## ` line.
That makes the check permissive rather than strict — a deliberate choice, since
a strict rule here would raise a *false* `E-STRUCT-PRECOND`, and under-reporting
is the safer failure for a check whose job is visibility.

The validator emits a run-wide `subject_index` alongside the existing
`target_files` intersection, so a coverage gap is visible as data. **This is
deterministic support for an agentic decision, not the decision.** The validator
reports the asymmetry; it never edits a `subjects` list.

---

## 5. Identity

Every item carries a required `anchor`, and **the validator derives the id from
it**. Checkers never write ids: 22 blind checkers minting free-form ids is
exactly how suggestion ids collide today, and prose is not an identity — 1 of 373
headliners was written the same way twice across two runs.

| kind | grammar | example | `id_stability` |
|---|---|---|---|
| `cve` | `CVE-(19\|20)\d{2}-\d{4,}` | `CVE-2026-18408` | `anchored` |
| `advisory` | `[A-Za-z][A-Za-z0-9._-]{2,}` | `wnpa-sec-2026-87` | `anchored` |
| `issue` | `{org}/{repo}#{n}` or `#{n}` | `getsops/sops#2245` | `anchored` |
| `commit` | 7–40 hex, optionally `{org}/{repo}@{sha}` | `f2ff0b2a` | `anchored` |
| `release` | `{version}/{slug}` | `10.5p1/ssh-Z-key-order` | `slug` |
| `none` | `value` must be null; a non-empty `slug` is required instead | — | `slug` |

The id is `{tool_id}#{kind}:{urlencoded(value)}`, or `{tool_id}#slug:{…}` for
`kind: "none"`.

Ids are required for **within-run duplicate detection** and as **convergence's
addressing scheme**. Cross-run diff is out of scope, so an id's cross-run
stability carries no weight and no comparison tool is built on it.

Two items deriving one id raises `E-ITEM-DUP-ANCHOR`; **both are kept** and the
later is suffixed `~2`. That is the `brew:sops` defect turned from a prose rule
an agent has to remember into a check that runs every time: both texts named
`#2245`, so "these are one change" stops being a judgement about prose and
becomes a string comparison. Convergence decides what to do about it.

---

## 6. Flags: what a checker may assert

> A checker may emit a flag iff **(a)** it is a pure function of that checker's
> own items **and (b)** it is not an input to an auto-approving decision.

**May emit** — `has_security`, `has_breaking`, `worst_severity`,
`local_findings`. These are **assertions, not inputs**: the validator recomputes
each, **its value wins**, and a mismatch raises `E-FLAG-DISAGREE`. A
disagreement says the checker's items do not say what it thinks they say — loud,
and worth nothing as a data source.

**May not emit** — `security_only`, `impact`, `risk_level`, `review_bucket`,
`pre_accept`. Emitting one raises `E-FLAG-FORBIDDEN`. The failure mode is
asymmetric: a wrong `has_security` is a wrong label, a wrong `security_only` is
an unreviewed upgrade.

Some facts need the corpus and no checker can produce them at any level of
diligence — final bucket, highlights, cross-tool collisions, watch-item
cardinality, cross-tool duplicate changes, structural coverage gaps, and the
report-wide CVE **union** (per-tool counts summed to 77; the union is 76).

---

## 7. The six stages

| Stage | Does |
|---|---|
| **V1** load | per file, per entry. An unreadable file costs that file; a non-object entry becomes an `orphan` |
| **V2** spec | required fields, types, closed vocabularies — including the closed **top-level key set** (`items.RESEARCH_KEYS`): an unrecognized key is `E-RESEARCH-UNKNOWNKEY`, quarantined verbatim and content-losing, which is how the retired `headliners[]`/`relevancy[]` schema stopped being silently discarded. **The item survives with every offending field exactly as written** — a wrong-typed `change`/`local`/`security` is reported, not nulled, because the finding's `value` is bounded for readability and nulling would make the truncated copy the only one |
| **V3** normalize | only the normalizations above: `null` → `[]`, non-list → `[]` with a warning, evidence shorthand → object form, wrong-typed members quarantined. `quarantine[]` holds wrong-typed array members **and** the payloads of unrecognized top-level keys — same entry shape `{field, item_id, value}`, `item_id: null` for the key case |
| **V3b** identify | ids assigned from the anchor, in authored order, then disambiguated |
| **V4** invariants | the twenty below. Every one **reports and changes nothing** |
| **V5** impact | `none` \| `possible` \| `unknown` |
| **V6** bucket | `initial_review_bucket` plus `bucket_inputs` |

Output: `{session_dir}/validation.json` — the machine-readable primary and the
**pre-convergence artifact** — and `{session_dir}/assemble.warn`, the same
findings one per line, code-prefixed, in the same order. A clean run produces
zero findings and an empty warn file.

`assemble.warn` is the **spec-conformance channel for the run and nothing
else** (§3.3). Everything that is not a conformance finding — what assembly
did, renamed, or could not do — goes to `{session_dir}/assemble.log`. The old
channel was one undifferentiated stderr tail running 275 lines at a 1:272
signal ratio, which is what made the single genuinely wrong evidence path
invisible.

**Who runs this.** In a normal run `assemble.py` calls `validate_session()`
directly and writes all four files; the validator's own CLI exists so the stage
can be run, and its output diffed, on its own. Assembly consumes the views:
`items`, `links`, `config_status`, `suggestions`, `vendor_silent_categories`,
`impact`, `risk_level` and the bucket are all read off the view, never
recomputed — two implementations of "does this release touch this setup" is the
drift §C3 exists to remove.

Exit codes: **0** clean, **3** degraded, **>3** only for a genuine
I/O/environment failure. **3 is not a failure** — everything downstream still
runs. The workflow surfaces it; it never aborts.

### The twenty invariants

| id | invariant | code |
|---|---|---|
| I-1 | at least one of `change`, `local` is non-null | `E-ITEM-EMPTY` |
| I-2 | `incompatible` ⇒ `reaches` ∧ `risk` | `E-SEV-INCOMPAT-UNGROUNDED` |
| I-3 | `warning` ⇒ `local` present | `E-SEV-WARNING-UNGROUNDED` |
| I-4 | `security` ∈ tags ⇔ `security` block present | `E-SEC-BLOCK-MISSING` / `E-SEC-BLOCK-ORPHAN` |
| I-5 | `security.cve_id` matches the CVE grammar when non-null | `E-CVE-MALFORMED` |
| I-6 | `rating != unknown` ⇒ `rating_basis != unrated` | `E-SEC-RATING-UNBASED` |
| I-7 | `change` present ⇒ `change.citation` non-empty | `E-CHANGE-UNCITED` |
| I-8 | `change.link_index` in range of `links[]` | `E-LINK-INDEX` |
| I-9 | `anchor` matches the grammar for its kind | `E-ANCHOR-MALFORMED` |
| I-10 | item ids unique within a tool | `E-ITEM-DUP-ANCHOR` |
| I-11 | tags ⊆ the closed set; ≥1 recognized tag | `E-TAG-UNKNOWN` / `E-TAG-NONE` |
| I-12 | evidence entries are paths, and resolve | `E-EVID-MALFORMED` / `E-EVID-404` / `W-EVID-ROOT` |
| I-13 | checker-emitted flags agree with recomputation | `E-FLAG-DISAGREE` |
| I-14 | `direction == reaches` ⇒ evidence non-empty | `E-REACHES-UNEVIDENCED` |
| I-15 | `config_needs_attention` ⇒ ≥1 action suggestion (edit / structural) | `W-ATTENTION-NOSUG` |
| I-16 | structural op preconditions hold | `E-STRUCT-PRECOND` / `W-STRUCT-UNCHECKED` |
| I-17 | no `intel.Brewfile` in a manifest or a `target_files` path | `E-INTEL-BREWFILE` |
| I-18 | suggestion ids unique across the whole report | `W-SUG-DUP-ID` |
| I-19 | a memory proposal carries its payload, and a `self_test_failed` tag carries its reason | `E-FIELD-MISSING` / `E-SELFTEST-NOREASON` |
| I-20 | a watch-item hit names a stored watch item for this tool and says what it means here | `E-WATCH-HIT-UNGROUNDED` / `E-WATCH-HIT-NOLOCAL` / `W-WATCH-UNCHECKED` / `W-WATCH-HIT-UNRAISED` |

I-14 is the mechanical replacement for the prose rule "if you can point at the
touchpoint, you owe a relevancy item" — same claim, now checkable. It warns and
never sets or clears the direction for you. Keep that discipline for all
twenty.

I-15 and the bucket clause read one tuple, `model.ACTION_SUGGESTION_KINDS`:
**memory proposals do not force `attention`; action proposals do.**
`watch-item` and `method-note` propose changes to what we remember, `edit` and
`structural` to the user's system, and only the latter needs a decision.
`REDESIGN.md` §L1 expects *many* per-tool method notes and watch items, so the
older spelling — "anything that is not an upgrade" — would put most of the
fleet on the "needs you" list.

I-19 validates the `self_test_failed` tag rather than letting it ride as an
extra field the validator happens to tolerate. §L7 makes convergence key its
review off that tag, and an unvalidated channel is exactly how `Watch item
hit:` broke: a literal string worth 70 highlight points that nobody checked,
which stopped firing the moment it was paraphrased. See `references/schemas.md`
§1.7b/§1.7c for the two memory kinds and the tag.

I-20 grounds the structured successor to that literal. `watch_hit` (§2.5) is
checker-authored; the validator checks its `topic` against
`{session_dir}/watch-items.json` — a verbatim copy of the machine-global
watch-item store, written by the orchestrating session when it fills
`{{STANDING_NOTES}}`, so the claim is checked against the evidence it was made
from. The snapshot being absent degrades every hit to `W-WATCH-UNCHECKED` and
keeps it; a run never aborts on it.

The complete code table, with each code's severity and the invariant it serves,
is `items.FINDING_CODES`, published in `contract/contract.json`.

### Impact

```
brew-health / skill-drift  → "none" if the finding is expected, else "possible"
research failed / no items → "unknown"      (unknown can never reach security_auto)
pinned, or config needs attention,
  or an edit/structural suggestion,
  or an item at "incompatible",
  or an item with effect == "risk" at notable+,
  or a "breaking" item at warning+          → "possible"
otherwise                                   → "none"
```

Three heuristics are gone relative to the old implementation, and their removal
is the point: the `category != "security"` proxy (replaced by `effect ==
"risk"`), the separate headliner clause (which existed only because a headliner
had no local finding), and `"watch-item"` in the suggestion clause.

### Initial bucketing

`security_only`, `risk_level`, `initial_review_bucket` keep their documented
definitions and precedence, with one substitution: the `(category, severity)`
pair test becomes a tag/severity test — allowed tags are `security`, `fix`,
`chore`, `packaging`, with `security` at any severity and the rest only at
`info`/`notable`. This preserves every documented disqualification and fixes
one: a `breaking` change now disqualifies on its tag rather than on the accident
of having been filed `notes`/`notable`.

The bucket carries `bucket_inputs: {has_security, security_only, impact,
version_delta, runnable}` so convergence can see *why* without re-deriving it.
**It is a baseline for convergence to review, not a decision.**

**`has_security` is wider than the tag, and computed once.** It is true when
any item is tagged `security`, **or** `vendor_silent_categories` contains
`"security"`, **or** any item carries a `security` block at all. The second
limb is research's explicit statement "this release has security content the
vendor refused to detail" — and dropping it does not merely lose a label:
`risk_level` only elevates a tool with no items when `vendor_silent_categories`
is *also* empty, so a non-empty one suppresses that elevation too. Tag-only
therefore produces the worst combination available — not elevated, not
security, `routine`, pre-accepted — from a field whose entire purpose is "look
at this". The third limb closes the same hole from the other side: I-4 reports
a `security` block on an item that forgot the tag (`E-SEC-BLOCK-ORPHAN`), and
reporting it while treating the tool as non-security is how a CVE-carrying tool
would reach a bucket that pre-accepts.

**`risk_level` carries the other half of this.** `has_security` makes such a
tool visible by moving it into `security_mixed`; it does not stop it being
pre-accepted, because `pre_accept` reads `risk_level` and
`compute_risk_level`'s no-items clause is *suppressed* by a non-empty
`vendor_silent_categories`. So `"security"`-silence elevates on its own. Both
halves are needed and the second is the easy one to miss.

It now reaches everywhere (D2): `pre_accept` requires `risk_level == "low"`
outright, and the security_auto clause itself asks `items.pre_accept_bars` —
elevated risk, a reaching security **item** (one that itself carries security
content; never merely any reaching item on a security tool), or a watch hit — so a
barred security-only tool falls through to `security_mixed`, visible and
undecided, instead of being pre-accepted "by design" the way the old
precedence allowed. The bar and the clause order are pinned as data in
`contract/bucketing.json`.

`items.recompute_flags` stays **tag-only**, deliberately: it is what
`E-FLAG-DISAGREE` compares a checker's claim against, and a checker that
correctly reported `has_security: false` from its own items must not read as
disagreeing. The widening happens at the point of use, once, and the same value
feeds `security_only`, the bucket and `bucket_inputs` — a value that is
"security" for bucketing and "not security" for the security-only test is its
own auto-accept route, and a bucket its own recorded inputs cannot explain is
exactly the opacity §C3 removes.

### Security display

The replacement for `notable[]`: an item is shown inline in the security block
when its rating is `critical`, or it is exploited in the wild, or
`direction == reaches`, or its severity is `warning`/`incompatible`.

**There is no cap.** A cap is a count, and counts invite padding. The predicate
is a bar; whatever clears it is shown, and everything else still exists, still
renders in the collapsed detail, and still carries its finding.

---

## 8. Degradation

| failure | scope | result |
|---|---|---|
| a research file is unreadable or is not an array | file | `E-RESEARCH-UNREADABLE` / `E-RESEARCH-NOTARRAY`; the run continues |
| an entry is not an object, or has no usable id | entry | recorded in `validation.json.orphans`; the run continues |
| an entry names no collected candidate | entry | kept in `unmatched`, reported; merged into no tool |
| a known entry fails spec | tool | the tool is built from whatever conformed, field by field; codes recorded on `tool.spec_violations[]` |
| validating one item raises | item | `E-VALIDATOR-CRASH` naming the item; **the item is kept verbatim with its assigned id and `id_stability`**, unchecked, so convergence can still address it |
| a later stage raises | tool | `E-VALIDATOR-CRASH` naming the stage; **everything that conformed before the failure stays on the tool** — discarding it would be deletion — and `validator_error` is set |

**A failed stage can never promote a tool.** Keeping what conformed is only half
the rule. A stage that failed halfway leaves the view missing exactly the
content it had not reached yet — the breaking item, the structural suggestion —
so a bucket computed from what survived would pre-accept a tool *because* the
thing holding it back is the thing that went missing. That is the `brew:libpq`
defect by another route. So `validator_error` makes `impact` read `unknown` and
`risk_level` read `elevated`, and no auto-accepting bucket accepts either.
| no entry at all for a candidate | tool | `research_error` set |
| the watch-item snapshot is absent, unreadable or wrong-typed | run's hits | every `watch_hit` degrades to `W-WATCH-UNCHECKED` and is kept; an unreadable or wrong-typed snapshot adds one `E-RESEARCH-UNREADABLE` naming the file |

Four loudness channels, all required: `validation.json` (the machine-readable
primary), `assemble.warn` (the human tail), `tool.spec_violations[]` so a
consumer sees it without opening a second file, and `tool.degradation` — the
channel that is pre-reduced for a consumer:

```jsonc
"degradation": {
	"content_losing": ["quarantined-content"],   // ordered; the fail-closed reasons
	"markers": ["E-EVID-404"],                   // every other code — visible, moves nothing
	"quarantined": 1
}
```

**Content-losing input fails closed** (`items.content_losing`, pinned in
`contract/degradation.json`): a non-empty `quarantine[]`, `W-SHAPE-COERCED`,
`E-RESEARCH-UNKNOWNKEY` or a `validator_error` forces
`initial_review_bucket: "attention"` (clause 0, above even the source clause)
and `risk_level: "elevated"`, and clears `pre_accept`. Computing a bucket from
what survived is the `brew:libpq` defect by another route, whether the content
went missing because a stage crashed or because a container arrived as the
wrong type. Every other finding is a **marker**: reported on the card, moving
nothing — an evidence-path typo is not a reason to hold an upgrade.
