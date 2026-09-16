# The published item contract

**This directory is WP1's deliverable to WP2, WP3 and WP4.** It exists because
the last time two implementer tracks worked against a pinned *field* contract,
they still drifted on ordering and had to be reconciled afterwards
(`HANDOFF.md` §8). Field names are not enough, so the comparator and the
ordering are published here as things you can import and assert against.

Normative prose: `references/item-schema.md`. Code: `scripts/items.py` (model,
ordering, comparator) and `scripts/validate_items.py` (the six stages).

## Importing it

```python
import sys
sys.path.insert(0, "<skill>/scripts")
import items, validate_items

items.CONTRACT_VERSION          # assert EQUALITY and refuse on mismatch — a sibling
                                #   package that merely reads the number and continues
                                #   has bought nothing. No migration shim exists or ships.
items.contract()                # the whole contract as data == contract.json
items.order_items(my_items)     # THE ordering — do not re-derive one
items.compare_items(a, b)       # THE comparator, -1 / 0 / 1
items.primary_group(item)       # which of the four content groups it renders under
items.security_display_items(x) # the bar that replaced notable[]'s clause 3
items.derive_item_id(tool, anchor)
items.FINDING_CODES             # every code the deterministic layer can raise
items.MEMORY_SUGGESTION_KINDS   # watch-item, method-note — proposals about MEMORY
items.ACTION_SUGGESTION_KINDS   # edit, structural — proposals about the SYSTEM
items.SELF_TEST_LIMBS           # the limbs a self-test tag may name (REDESIGN.md L7)
```

Run the validator over your own corpus:

```python
document = validate_items.validate_session(session_dir, roots)
```

## The eight fixtures

| File | Generated? | Asserts |
|---|---|---|
| `contract.json` | yes, from `items.contract()` | the field table, every vocabulary, the group map, the rank tables, the ordering spec, the finding codes, the closed research-key set, the degradation rule, the bucketing clause order, the pre-accept predicate, the watch-hit contract, the memory-store layout |
| `ordering.json` | hand-written | a shuffled item list and the exact order `items.order_items()` must produce, one entry per tier of the sort key — including that `watch_hit` is not a tier |
| `comparator.json` | hand-written | pairwise comparisons, `worst_severity`, both CVE rank tables, the security-display bar, id derivation, the evidence shorthand grammar |
| `bucketing.json` | hand-written | the clause order of `compute_initial_bucket` and the `apply_pre_accept` predicate, as a truth table driven through both live functions (D1, D2, E3) |
| `degradation.json` | hand-written | `content_losing()` / `compute_degradation()` over synthetic tool shapes, and the `degradation` block's own shape (D1) |
| `stores.json` | hand-written | the on-disk layout of `watch-items.json` and `method-notes.json`, and the golden file state after one write of each (D4) |
| `session/` | hand-written | a complete research corpus: three conforming tools (one carrying a grounded watch hit), one that violates nearly every invariant, one retired-schema entry, the two content-losing degradation routes, the elevated security-only route, a watch-hit tool with one grounded control and five invalid hits, its `watch-items.json` grounding snapshot, one unmatched entry, one non-array file, one brew-health finding |
| `expected_validation.json` | yes, from `session/` | the exact `validation.json` that corpus must produce — every finding, every id, every derived flag, every bucket |

`session/roots/` ships the repo tree the fixture's evidence paths resolve
against, so the golden output does not depend on what sits beside the repo on
one machine. `session/roots/tieto/` is the stand-in for a real repo that is not
a configured root — the `W-EVID-ROOT` case.

## Asserting against the golden run

```python
session, roots, unconfigured = validate_items.fixture_session()
got = validate_items.validate_session(session, roots,
	manifest_root=roots[0], unconfigured_roots=unconfigured)
assert got == items.load_fixture("expected_validation.json")
```

`validate_session` writes nothing, so this is read-only.

## Changing the contract

1. Change `items.py` / `validate_items.py`.
2. `python3 contract/regenerate.py`
3. **Read the diff.** A change in `expected_validation.json` you did not intend
   is the fixture doing its job.
4. `python3 -m unittest test_items test_validate_items`
5. Bump `items.CONTRACT_VERSION` if a consumer would have to change.

`test_items.py` asserts every fixture still agrees with the code, so a fixture
cannot go stale — which is the only reason a published fixture is worth more
than a paragraph.

## Memory proposals

`watch-item` and `method-note` propose changes to **what we remember**; `edit`
and `structural` propose changes to **the user's system**. Only the latter needs
a decision, so only the latter raises `risk_level`, moves a tool into
`attention`, or answers a `needs_attention` config status. `REDESIGN.md` §L1
expects *many* per-tool method notes and watch items, so the older spelling of
that clause — "anything that is not an upgrade" — would have put most of the
fleet on the "needs you" list.

A memory proposal may carry `self_test_failed: {limb, reason}`. **The self-test
tags, it never removes** (§L7): a failing proposal is still written, and
convergence reviews every tagged one to verify that dropping it is appropriate —
a proposal the agent never writes is one convergence cannot restore. The tagged
ids are exported per tool as `self_test_tagged_suggestion_ids` so convergence
works from a list rather than re-reading prose. Prose: `references/schemas.md`
§1.7b/§1.7c; agent guidelines: `references/research.md`.

## What the contract will not do

The deterministic layer validates, normalizes, counts, buckets and calculates
impact. **It never deletes, trims or re-rates an item on a regex or heuristic
rule** (`REDESIGN.md` §A, §C3, criterion 1). Judgement is convergence's.

So: a malformed evidence string is reported and kept, not moved to
`citations[]`. A duplicate id is reported and suffixed, not merged. An
unrecognized tag is reported and kept, not dropped. An over-long title is
reported, not truncated. `initial_review_bucket` is a baseline for convergence
to review, not a decision.
