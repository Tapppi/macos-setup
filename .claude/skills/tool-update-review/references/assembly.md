# Assembly — `assemble.py` Mechanics (Step 4)

Table of contents:
- Overview
- Loading and Merging
- Suggestion-ID Uniqueness
- Evidence Validation
- Version Delta
- Security Extraction
- Risk Level
- Review Buckets and Pre-Accept
- `needs_sudo` Heuristic
- `auto_runnable` / Command Per Source
- Baseline Suggestion Synthesis
- Highlights
- Brew-Health Assembly
- Skill-Drift Assembly
- `config_status` Normalization
- `needs_attention`-Must-Have-a-Suggestion Enforcement
- Summary Counts and Output

**Every measured figure below comes from one recorded session** — 22 research
files, 77 tools (74 version-outdated plus 3 brew-health findings) — and is
illustrative, not normative. Research content is what every classifier reads,
so a fresh run moves each distribution: a report whose buckets no longer split
the way this doc records is not by itself evidence that a classifier broke.
Reproduce the numbers with `python3 scripts/assemble.py {session_dir}` and read
the resulting `report.json`. The claims that *are* normative — the ones a
reviewer should hold any run to — are the invariants in §Summary Counts and
Output.

## Overview
```sh
python3 scripts/assemble.py {session_dir} \
	[--macos-setup-root PATH] [--dotfiles-root PATH] \
	[--systems-root ~/project/github/tapppi/systems]
```

runs the deterministic validator over `{session_dir}/research/*.json`, merges
its per-tool views with `collect.sh`'s saved output (`collect.json`, step 1 —
see `references/collection.md`) and writes four files:

| File | What |
|---|---|
| `report.json` | the report (`references/schemas.md` §Report Object) |
| `validation.json` | the validator's full document — the **pre-convergence artifact** |
| `assemble.warn` | the spec-conformance channel for the run and nothing else, one finding per line, code-prefixed. **A clean run leaves it empty.** |
| `assemble.log` | everything that is not a conformance finding: what assembly did, renamed, or could not do. Also echoed to stderr. |

**The item corpus is not read here.**
`validate_items.validate_session()` (`references/item-schema.md`) owns loading,
spec validation, normalization, id assignment, the eighteen invariants,
`impact`, `risk_level` and the initial bucket. Assembly consumes the views it
returns. That boundary is the point rather than a tidiness preference: two
implementations of "does this release touch this setup" is exactly the drift
`REDESIGN.md` §C3 exists to remove, and one of them would have been a regex
away from the `brew:libpq` defect.

What assembly still owns, because none of it is a judgement about an item:

- the **version delta** (§Version Delta) — the page renders it;
- the synthesized baseline `kind: "upgrade"` suggestion, per source
  (§Baseline Suggestion Synthesis);
- the `needs_sudo` heuristic;
- **suggestion-id uniqueness** across the whole report — the validator
  *reports* a collision (`W-SUG-DUP-ID`) and changes nothing; renaming is a
  rendering necessity;
- per-suggestion **`pre_accept`**, which needs the baseline the validator has
  never seen;
- `highlights[]` and the report-level `summary`;
- the tool-level **CVE rollup**, recomputed from `items[]`.

**One entry point, one dependency order.** `finalize_tool(tool, view)` is the
last statement of *every* build path (`build_tool`, `build_health_tool` and
`build_drift_tool`), so a health finding, a vendored-skill drift finding and a
version-outdated tool can never disagree about what a field means. It computes,
strictly in this order:

```text
version_delta ─→ security ─→ risk_level ─→ review_bucket ─→ pre_accept
```

The middle three now come straight off the view — `impact`, `risk_level` and
the bucket are stage V5/V6 output — so the order matters for one reason only:
`pre_accept` reads both `risk_level` and `review_bucket`.

Two ordering constraints relative to `main()` still matter:

1. **`finalize_tool()` runs before the suggestion-id uniqueness pass.**
   `baseline_upgrade()` identifies the baseline by position + `kind` + a
   `:upgrade` id suffix, and that pass can rename a colliding id to
   `…:upgrade-2`. Because `pre_accept` is written onto the suggestion dict in
   place, a later rename is harmless — but running `finalize_tool()` *after*
   the rename would break baseline detection outright.
2. **`build_highlights()` runs after that pass**, so
   `highlights[].suggestion_ids` carry the final, deduplicated ids. The one
   thing this costs is narrow and non-silent: a *cross-tool* collision where
   an earlier tool's research suggestion squats on this tool's baseline id
   renames the baseline, so `score_tool()` can no longer see it and the tool
   loses its 10-point `manual_action` signal. `review_bucket`/`pre_accept`
   were already computed, and the rename pass logs a note naming both ids.

Every derived field above has a test: `python3 scripts/test_assemble.py` runs
the version matrix (§Version Delta), the end-to-end semantic fixtures over the
item model, the CVE scan and claim cases, the shape-drift and degradation
cases, the report-level invariants (§Summary Counts and Output) and the
assembly ↔ page contract — stdlib `unittest` only, no network, on the same
bare `python3` `assemble.py` itself targets. Run it after changing any of them.

`--systems-root` defaults to `~/project/github/tapppi/systems` and rarely needs
overriding — pass it explicitly only if the harness mounts that repo somewhere
else. It exists because every research subagent is told to scan and cite that
repo for relevancy, so **evidence resolution** (the validator's, now) needs it
as a root alongside `--macos-setup-root`/`--dotfiles-root`.

**Every measured figure below comes from one recorded session** and is
illustrative, not normative. Research content is what every classifier reads,
so a fresh run moves each distribution: a report whose buckets no longer split
the way this doc records is not by itself evidence that a classifier broke. The
claims that *are* normative are the invariants in §Summary Counts and Output.

## Loading and Merging
`validate_session()` reads `collect.json` and every `research/*.json`, and
returns one **view** per candidate. Assembly indexes the views by tool id and
builds a Tool object from `view + candidate`. Every array on the Tool object
that carries item-model content — `items`, `quarantine`, `links`,
`config_status`, `vendor_silent_categories`, `suggestions` — is the view's,
already spec-checked, normalized, id-assigned and in canonical order.

Assembly reads the **raw** research object for exactly two fields the item
model does not cover (`references/item-schema.md` §6): `release_inventory[]`,
which is bookkeeping about the release cadence rather than a claim about any
change, and `cask_sudo_hint`, a per-source packaging hint the `needs_sudo`
heuristic reads. That is the whole of assembly's remaining contact with
free-form research output, and `as_item_list()` guards it the same way it
always did.

**Degradation is per unit and loud, at four boundaries.** A malformed file
costs that file; a malformed entry costs that entry; a malformed item costs
that item's *checks* and never the item itself; a candidate assembly cannot
build costs one card. Nothing costs the run. The validator owns the first
three (`references/item-schema.md` §8); `build_tool_guarded()` owns the fourth,
checking the identity keys first — `build_tool()` reads
`candidate["source"]`/`["id"]`/`["name"]` directly and a KeyError there names
only the key, never the candidate it came from — and then running the build
inside a boundary that catches the next unanticipated shape.

**A tool the validator could not read is never promoted.** `validator_error`
forces `impact: "unknown"` and `risk_level: "elevated"`, and no auto-accepting
bucket accepts either. A stage that failed halfway leaves a view missing
exactly the content it had not reached yet — the breaking item, the structural
suggestion — so a bucket computed from what survived would pre-accept a tool
*because* the thing holding it back is the thing that went missing.

`spec_violations[]` and `quarantine[]` ride along on every Tool object, so a
consumer sees a degraded tool without opening `validation.json`.

### Shape Normalization at the Research Boundary
Owned by the validator, not by assembly (`references/item-schema.md` §7, stage
V3). The rule it applies is narrower than the one it replaced, and the
narrowing is the point: **only the normalizations the schema licenses by name**
— `null` → `[]`, a non-list → `[]` with a finding, an evidence shorthand string
→ its object form, a wrong-typed array member **quarantined on the tool rather
than dropped**.

Everything else is reported and left exactly as written. A malformed evidence
string is not moved to `citations[]`; an unrecognized tag is not dropped; an
over-long title is not truncated; two items deriving one id are not merged.
Each of those would be a regex deciding what a field means, which is the
banned behaviour (criterion 1).

Assembly keeps one normalizer, `as_item_list()`, for the two raw research
fields the item model does not cover (§Loading and Merging). Same doctrine:
`dict.get(key, default)` only substitutes when the key is *absent*, so a
present-but-null `"release_inventory": null` reaches `for entry in …` as
`None`; coerce, note, never abort.

### The Later Boundaries: One Bad Unit Never Costs the Report
One report is assembled from ~22 research files over ~78 candidates, after the
expensive part of the session is already spent. Anything that can only be wrong
about *one* unit must cost that unit and leave the rest rendering.

| Boundary | Owner | A failure costs |
|---|---|---|
| one research file | validator (V1) | that file |
| one entry in a file | validator (V1) | that entry — recorded in `validation.json.orphans` |
| one item inside an entry | validator | that item's **checks**, never the item: it is kept verbatim with its assigned id |
| one validation stage | validator | that stage's checks; whatever conformed before it stays, and the tool's derived axes read `unknown` |
| one candidate | `build_tool_guarded()` | one card |
| one tool's highlight | `build_highlights()` | that tool's slot |

The last two are assembly's. `build_tool_guarded()` checks the identity keys
first, because `build_tool()` reads `candidate["source"]`/`["id"]`/`["name"]`
directly and a KeyError there names only the key, never the candidate. Then the
build runs inside a boundary that catches the next unanticipated shape and logs
which card was lost.

`build_highlights()` contains both of its loops per tool for the same reason:
highlights are a *derived* section, so one tool whose shape it cannot read must
cost that tool its slot and nothing else. Losing the whole report — and with it
the 77 cards that are fine — over a ranking is the trade this pass exists to
refuse.

Fuzz-tested rather than asserted: `scratch/spof/repro/fuzz.py` holds
`assemble.main()` at **0 of 346** hostile shapes aborting, and the validator's
session-level twin at **0 of 282**.

## Suggestion-ID Uniqueness
Suggestion ids must be unique **globally across the whole report**, not just
within one tool's `suggestions[]` — a collision almost always means a
research subagent copied an id pattern rather than deriving it from its own
tool. Assembly walks every tool's suggestions in order, and on a collision
appends a disambiguating numeric suffix (`-2`, `-3`, …) rather than silently
dropping either suggestion, warning to stderr each time. This check runs
*after* the baseline `upgrade` suggestion has been synthesized and inserted
(below), so a baseline id colliding with a research-authored id gets caught
too.

The same pass also **assigns an id to a suggestion that has none**, rather
than skipping it: `{tool_id}:sug-{n}`, first free `n`, with a warning naming
the tool and the assigned id. Every downstream consumer indexes suggestions
by id — `write_status.py` builds `{sug["id"]: …}` and would `KeyError` on a
missing one, four steps and one user decision later — so leaving it out just
moves the failure somewhere it can't be diagnosed. A synthetic id is worse
than a research-derived one and the warning says so; a crash mid-apply is
worse than both.

This pass runs *between* `finalize_tool()` and `build_highlights()`, which is
the ordering the Overview above spells out.

## Evidence Validation
**Assembly no longer validates evidence.** The whole machinery —
`evidence_exists()`, `strip_evidence_suffixes()`, `_COMMIT_LIKE`,
`_TRAILING_PAREN`, `_TRAILING_LINE_REF` — is gone, along with the conflation
that made it necessary.

Under the item model, `local.evidence[]` holds **paths only, as objects**
(`{path, lines, note}`) and prose citation has its own field
(`local.citations[]`, `change.citation`). The validator resolves the paths
against the configured repo roots and reports three outcomes — resolves,
`W-EVID-ROOT` (resolves only under a known-but-unconfigured sibling repo),
`E-EVID-404` (resolves nowhere) — and **never fetches a URL or path-checks a
citation**, because a citation is not checkable, so it is not checked, so it
cannot warn.

That split is why the channel is worth reading now. On the recorded run, 272 of
275 `assemble.warn` lines were `evidence not found`, of which exactly **one**
was a genuinely wrong path. 140 of the warned strings had a leading path token
that resolves once the prose is split off it; the rest were prose that should
never have been path-checked at all.

`config_status` gets the same split, and it was the worst offender: 133 of
those 272 warnings, against 286 evidence strings.

Detail, including the accepted shorthand forms and why a malformed entry is
**reported and kept** rather than moved to `citations[]`:
`references/item-schema.md` §3.

## Version Delta
`compute_version_delta(current, latest, source, tool_id=None) -> (delta,
scheme, note)` is the only function callers use; the three values land on the
Tool object as `version_delta`, `version_scheme` and `version_delta_note`
(vocabulary and enum: `references/schemas.md` §1.8). `tool_id` is optional and
only names the tool in the compare-equal warning.

**Why not "just semver".** The corpus is not semver. One live run carries
Homebrew formula revisions (`13.55 → 13.55_1`), cask `version,build` tuples
(`2026.2,262.8665.272`), letter suffixes (`3.7b → 3.7c`), OpenSSH's portable
suffix (`10.4p1`), nmap's decimal scheme (`7.99 → 7.991`), ImageMagick patch
levels (`7.1.2-27`), date versions (`20260622`), calendar versions
(`2026.7.4`) and pure build numbers (`26163.407.4839.8659`). The failure that
matters is a **false `patch`**, because `patch` reads as "nothing to think
about" and feeds the pre-accept path — so a scheme the classifier can't
interpret returns `unknown`, and every ambiguous positional call rounds *up*
in significance, never down.

### The algorithm, in order
1. **Non-version sources short-circuit.** `source in ("brew-health",
   "skill-drift", "macos")` → `("unknown", "none", "no version delta for
   this source")`. brew-health and skill-drift have no versions at all
   (both `current_version` and `latest_version` are `null`); a `macos`
   candidate's `current_version` is the running `sw_vers -productVersion`
   rather than that specific update's version (`references/schemas.md`
   §1.3), so a delta computed from it would be fiction. The membership test
   is a tuple rather than a chain of `==` precisely so the next non-version
   source is one string, not another branch to get wrong.
2. **Missing or blank input** → `("unknown", "none", "missing version")`.
3. Split each side: `split_cask_tuple()` (first comma only — the right half
   is Homebrew's build/revision half and never carries upstream semantics),
   then `split_brew_revision()` (trailing `_N` only, so `1.2_beta` stays part
   of the upstream version) → `core`, `build`, `rev`. A leading `v` is
   stripped from each core once, here, so the date-8 test and the component
   parser read the same string.
4. `parse_version_components()` both cores → `[(int|None, suffix), …]`.
   Either empty (nothing numeric to compare, e.g. `"stable"`) →
   `("unknown", "none", "no numeric component")`.
5. `detect_version_scheme()` — the table in `references/schemas.md` §1.8.
   Both sides must agree; a disagreement falls through to `opaque` whenever
   either side looks like a build number, else to `semver`.
6. `first_difference()` — the first index whose **integers** differ
   (`"numeric"`), else the first index whose **suffixes** differ
   (`"suffix"`), else `(None, None)`. Missing trailing components compare as
   `(0, "")`, so `2.46.1 → 2.47` differs at index 1. Only a pair of real
   integers can differ numerically, which is why `1.0.0-rc1 → 1.0.0` differs
   at index 3 by suffix rather than by number.
7. Then:
   - **No difference** → `("revision", scheme, "packaging revision only")` if
     `rev` or `build` differs; else `("unknown", scheme, "versions compare
     equal")` — which shouldn't happen, since the tool wouldn't be listed as
     outdated, so it also prints `warning: {id}: current and latest compare
     equal`.
   - `scheme == "opaque"` → `("unknown", "opaque", "build-number scheme, not
     interpretable")`.
   - difference of kind `"suffix"` → `("patch", scheme, f"suffix change at
     index {i}")`.
   - `scheme == "date"` → `("minor", "date", "date-versioned release")`.
   - `scheme == "calver"` → index ≤ 1 → `minor`, ≥ 2 → `patch`; note
     `f"calver index {i}"`.
   - `scheme == "semver"` with **both** leading components `0` → index 0 →
     `major`, 1 → `major`, 2 → `minor`, ≥3 → `patch`; note
     `f"index {i} (0.x rule)"`.
   - `scheme == "semver"` otherwise → 0 → `major`, 1 → `minor`, 2 → `patch`,
     ≥3 → `patch`; note `f"index {i}"`.

Direction is ignored throughout: a downgrade classifies by the same
first-differing index an upgrade would.

**`"revision"` means packaging-only and nothing else** — a Homebrew `_N`
rebuild or a cask build-half bump with an identical upstream version. It is
deliberately *not* used for semver index ≥ 3: ImageMagick's `7.1.2-27 →
7.1.2-29` is a real upstream patch level, so that is `"patch"`.

### Worked examples — this table is the test matrix
Every row below is a real `current → latest` pair from a live run and is
executed as a parametrized case by `scripts/test_assemble.py` (§1, `VERSION_
MATRIX`). The point of writing them down is that the next person changing the
classifier doesn't have to re-derive "is `7.99 → 7.991` a minor?" from
scratch.

| current | latest | `version_delta` | scheme | note | why |
|---|---|---|---|---|---|
| `6.0.1_1` | `6.1.0` | `minor` | semver | `index 1` | `_1` stripped first; upstream 6.0.1 → 6.1.0 |
| `8.1.2_1` | `9.0.1` | `major` | semver | `index 0` | ffmpeg 8 → 9 |
| `13.55` | `13.55_1` | `revision` | semver | `packaging revision only` | Homebrew rebuild, no upstream change |
| `0.41.0_6` | `0.41.0_8` | `revision` | semver | `packaging revision only` | revision-to-revision |
| `7.99` | `7.991` | `minor` | semver | `index 1` | nmap: a 2-component scheme can't distinguish this from a real minor, so it rounds up |
| `3.7b` | `3.7c` | `patch` | semver | `suffix change at index 1` | tmux letter release — upstream's own bugfix marker |
| `2.46.1` | `2.47` | `minor` | semver | `index 1` | short side padded with 0 |
| `20260622` | `20260722` | `minor` | date | `date-versioned release` | GNU parallel |
| `v20260622` | `v20260722` | `minor` | date | `date-versioned release` | leading `v` stripped before the date test |
| `2026.7.4` | `2026.8.19` | `minor` | calver | `calver index 1` | yt-dlp month bump |
| `1.22209.3,babe1157…` | `1.34493.1,255293a4…` | `minor` | semver | `index 1` | cask tuple; the left half decides |
| `2026.2,262.8665.272` | `2026.2.4,262.10315.24` | `patch` | calver | `calver index 2` | DataGrip bugfix release |
| `10.4p1` | `10.5p1` | `minor` | semver | `index 1` | OpenSSH portable; `p1` ignored because a numeric index differs first |
| `26.07.0` | `26.08.0` | `minor` | semver | `index 1` | poppler — a 2-digit year is *not* treated as calver (too ambiguous with a real semver major) |
| `150.0.7871.129` | `151.0.7922.174` | `major` | semver | `index 0` | Chrome; honest, and the noise is handled in §Highlights |
| `18.4` | `18.6` | `minor` | semver | `index 1` | libpq; PostgreSQL calls these minor releases |
| `1.12.4` | `1.12.6` | `patch` | semver | `index 2` | kdiff3 |
| `4.53.3` | `4.53.6` | `patch` | semver | `index 2` | yq |
| `1.3.14` | `1.4.0` | `minor` | semver | `index 1` | bun |
| `7.1.2-27` | `7.1.2-29` | `patch` | semver | `index 3` | ImageMagick patch level — an upstream change, so `patch`, not `revision` |
| `26163.407.4839.8659` | `26213.1006.5011.1671` | `unknown` | opaque | `build-number scheme, not interpretable` | Teams; must not read as major or patch |
| `1.0.0-rc1` | `1.0.0` | `patch` | semver | `suffix change at index 3` | pre-release → final |
| `3.12.17,0fb76205…` | `3.17.8,2fdd31c9…` | `minor` | semver | `index 1` | Cursor cask tuple |
| `1.2.3` | `1.2.3,999` | `revision` | semver | `packaging revision only` | cask build-half-only bump |
| `null` | `null` | `unknown` | none | `missing version` | brew-health / missing data |
| `0.144.6` | `0.149.0` | `major` | semver | `index 1 (0.x rule)` | codex — and that release genuinely removed `codex exec --full-auto` |
| `0.11.29` | `0.12.5` | `major` | semver | `index 1 (0.x rule)` | uv |
| `0.4.73` | `0.4.81` | `minor` | semver | `index 2 (0.x rule)` | claudebar |
| `0.74.1` | `0.74.3` | `minor` | semver | `index 2 (0.x rule)` | fzf — a 0.x patch position rounds up to `minor`, never down |

Measured distribution over that run's 74 version-outdated tools: major 8,
minor 38, patch 25, revision 2, unknown 1 — sum 74 = `summary.total_outdated`
(§Summary Counts and Output).

**`is_major_bump()` is gone.** The old rule compared the leading integers of
the two version strings, which called `20260622 → 20260722` (GNU parallel, a
routine monthly snapshot) a major bump while missing `0.11.29 → 0.12.5` (uv)
and `0.144.6 → 0.149.0` (codex — which removed a flag) entirely. Both are
fixed by the shared classifier, and the report stopped carrying two different
answers to "how big is this bump":
`is_major_bump()`/`_leading_major()`/`_LEADING_INT` were deleted, and the page
reads `data-delta` for its "Major delta first" sort rather than re-parsing
versions client-side.

## Security Extraction
The tool-level `security` object (`references/schemas.md` §1.9). Every field is
derived; there is no research-supplied half any more.

### Field scope, in and out
A CVE id reaches `cve_ids` two ways.

**Structurally** — `item.security.cve_id`, or a `cve`-kind `item.anchor`. This
is the field the item model built for the purpose, and it is why
`security.cve_severities[]` could be deleted: per-CVE grading lives on the item
that carries the CVE, so there is no second list to reconcile against the
first.

**By scanning** — `item.title`, `item.body`, `item.change.citation` and
`item.local.statement`. All four are prose about *this* current→latest range by
construction. The scan survives the schema change because understating is the
failure mode with a cost: an id a checker names in a body but forgets to put in
`security.cve_id` is still an advisory the user is being asked about, and
`cve_count` is defined as the size of this list.

Deliberately excluded:

| Excluded | Why |
|---|---|
| `item.local.citations[].text` | `prior_review` is a citation kind; it legitimately names an advisory from outside this range |
| `links[].embedded_content`, `links[].url` | an unbounded changelog excerpt can cover releases the user is not being asked about |
| `suggestions[]` | derived text restating items — doubles the false-positive surface, changes no result |
| `config_status.detail` | backward-looking audit prose, where an id is usually a *prior* run's finding |

The **claim** scan (`cve_claimed_count`) is narrower still: `local.statement`
is out of it, because "the 5 CVEs above do not reach us" is our analysis, not
the vendor claiming five fixes.

The exclusions are safe because `has_security` never depends on ids — a missed
id understates `cve_count`, it cannot flip a security release into a
non-security one.

### The two regexes and their bounds
```python
_CVE_RE = re.compile(r"\bCVE-(?:19|20)\d{2}-\d{4,}\b", re.IGNORECASE)

_CVE_CLAIM_RE = re.compile(
	r"(?<![\d.])(\d{1,3})\s+"
	r"(?:CVEs?|security (?:issues|vulnerabilities|fixes|advisories)|vulnerabilities)\b",
	re.IGNORECASE)
```

- `\b` before `CVE` rejects `NOTCVE-2026-1234`.
- `(?:19|20)\d{2}` pins the year to 1900‥2099, so `CVE-3026-1234` and
  `CVE-ABCD-1234` don't match.
- `\d{4,}` has **no upper bound**, because MITRE's sequence has none —
  capping it would silently turn a real 7-digit id into a non-match. Greedy
  `\d{4,}` followed by `\b` consumes the whole run of digits, so
  `CVE-2026-9` doesn't match while `CVE-2026-1234567890` does.
- `(?<![\d.])` on the claim pattern is the stunnel guard above; together with
  `\d{1,3}` it also rejects `fixes 1234 CVEs`.
- Matching is case-insensitive; every hit is upper-cased before storing.

Known non-handled forms, documented rather than guessed at: comma/slash
ranges (`CVE-2026-4444/4445` yields only the first) and spelled-out numbers
("resolves twelve CVEs"). Both are vanishingly rare in vendor notes. The
regex cases, including the two real-world false positives the bounds exist to
reject, are in `scripts/test_assemble.py` §3.

### Dedupe, and why the report-wide count is a union
Per-tool `cve_count` is the size of that tool's id list. `summary.security.
cve_count` is the size of the **union** across tools, not the sum: one advisory
routinely lands on two tools — openssh and ssh-copy-id ship from the same
source tarball, a bundled-OpenSSL CVE hits several casks — and counting it
twice would inflate the one number the security section leads with. On the
recorded corpus the per-tool counts sum to 77 while the union is 76.

The report-wide `severity_counts` is rebuilt from each tool's item ratings
rather than summed from the per-tool rollups, for the same reason. Two tools
rating one id differently is real once ratings come from different pages: the
**worse** wins, and the resolution is written to `assemble.log` rather than
being made silently.

### Severity Rollup and the Sum Invariant
`severity_counts` is `{critical, high, medium, low, unknown}`, all five keys
always present, over `cve_ids`.

**`sum(counts.values()) == cve_count` holds by construction**, because the
rollup iterates `cve_ids` and every id lands in exactly one bucket — an id
nothing rated becomes `unknown` instead of a broken sum. Iterating the rating
map instead would silently break it, which is why `test_assemble.py` asserts
the sum anyway, on every tool and on the report.

Three rules survive verbatim from the deleted `resolve_cve_severities()`:

- a rating word outside the vocabulary reads as `unknown`, never a guess;
- **a rating with no basis is not a rating.** `rating_basis ∈ vendor | nvd |
  cvss | unrated`, and `unrated` is *not* a basis — it is the word for "nobody
  graded this", so a graded rating carrying it is counted as `unknown`. I-6
  reports the same thing as `E-SEC-RATING-UNBASED`;
- when two items grade one id differently, the **worse** wins and the
  resolution is logged.

The CVE vocabulary is not the item severity vocabulary. Item severity is "how
much does this matter to this machine"; CVE severity is "what did the issuer
rate the flaw". Conflating them is how teamviewer's Linux-only CVSS 8.8 would
read as urgent on a macOS card.

**Two rank tables, and they are not interchangeable.** `items.CVE_WORSE_RANK`
resolves a conflict, where `unknown` means "no rating recorded" and must lose
to a real `low`. `items.CVE_ORDER_RANK` orders the display, where `unknown`
means "ungraded but real" and must not sort below a rated `low` — an absent
grade is not evidence of harmlessness. Both are published in
`contract/contract.json` so a consumer cannot pick the wrong one, and
`test_assemble.py` asserts the page's copy agrees tier for tier.

`unknown` dominating is the expected state, not a degraded one: a checker
grades only what the page it already read states. An all-`unknown`
`severity_counts` is a correct report.

### Validating Research's `notable`
*(Retired. `security.notable[]` no longer exists.)*

The security column now renders the items named by
`security.display_item_ids`, and the selection is a **bar, not a cap**: an item
qualifies when its rating is `critical`, or it is exploited in the wild, or
`local.direction == "reaches"`, or its severity is `warning`/`incompatible`
(`items.is_security_display_item`). Order is the contract's
`security_display_sort_key` — total, so a consumer that follows the list
reproduces the report exactly.

Everything the old validation pass existed to do went away with the second
array it was reconciling:

| Old rule | Why it is gone |
|---|---|
| null a `cve_id` that does not resolve in `cve_ids` | the id *is* the item's, and `cve_ids` is derived from the items |
| `severity` disagreeing with `cve_severities` | one list, one grade — `cve_severities[]` is deleted |
| a grade present here and absent from the map | same |
| coerce a non-bool `affects_me` | replaced by `local.direction`, which the validator checks against a closed vocabulary |
| `affects_me: true` with nothing backing it | I-14 makes it checkable: `direction == "reaches"` requires non-empty `evidence[]` |
| order, then evict the weakest past a cap of 3 | there is no cap. A cap is a count, and counts invite padding |
| resolve `source_ref` to the item this restates | there is no second entry to point back — the id *is* the item |

**The cap is the one worth dwelling on.** It ordered *and* evicted with one
comparison, so `affects_me` had to outrank severity or three unreachable `low`
CVEs would evict a reproduced command injection that lands on a wrapper this
machine runs (`brew:iproute2mac`). Under a bar there is nothing to evict: what
clears it is shown, what does not still renders in the collapsed detail, and
the old clause-3 padding disappears — openssh filled all three of its slots
with items whose own summaries say the fix does not reach this machine, and
under the bar it selects none.

Measured on the recorded corpus re-expressed in the item model: 32 capped
entries become **21** selected ones, and no item is lost from the page.

### `cve_count` vs. a vendor that says "fixes 33 CVEs"
`cve_count` is **always** `len(cve_ids)` — an id-backed count, never a claim,
so the page can attach every counted CVE to something concrete.
`cve_claimed_count` carries the vendor's own largest stated count separately.

**Max wins, never sum.** Firefox's two releases in one range claim 50 and 47;
Chrome's launch item claims 370 while a second item describes the 68-fix
subset of it. Summing double-counts the subset; the max is a defensible floor.

The claim pattern's two bounds are both real false positives it exists to
reject: `(?<![\d.])` stops stunnel's context sentence "Both 5.80 CVEs need a
running service" yielding a claim of 80, and `\d{1,3}` stops "1234 CVEs"
pairing with a three-digit tail.

The claim scan reads `item.title`, `item.body` and `item.change.citation` —
the vendor's own words — and deliberately **not** `item.local.statement`,
which is our analysis: "the 5 CVEs above do not reach us" is not a vendor
claiming five fixes.

`tools_with_unlisted_cves` counts tools whose claim exceeds their id count, so
the header can read "59 CVEs · 6 tools report more without ids" instead of
silently understating.

### `security_only` — "substantive content is security/patch only"
Computed by the validator (`compute_security_only`) and read off its view.

Requires `has_security` *and* that the checker actually produced items, then
allows only these combinations across `items[]`: tags ⊆ {`security`, `fix`,
`chore`, `packaging`}, with `security` at **any** severity and the rest only at
`info`/`notable`. Everything else disqualifies — `feature`, `breaking`,
`deprecation`, `perf` at any severity; an unrecognized tag (an unknown tag is
not evidence of harmlessness); a non-string tag member; and a non-`security`
entry in `vendor_silent_categories`.

This is the old `(category, severity)` pair test restated over tags. It
preserves every documented disqualification and fixes one: codex's breaking
"`codex exec --full-auto` was removed" was filed `notes`/`notable` and
disqualified on the accident of its severity, where tagged `breaking` it
disqualifies on what it *is*.

It also widens one case deliberately — a `chore`/`packaging` item at `notable`
passes where `notes`/`notable` used to disqualify. `chore` means "a real, cited
change with no consequence for any reader", so the filing accident no longer
carries the decision. Measured on the recorded corpus re-expressed in the item
model, three tools move `security_mixed → security_auto` on this rule
(`brew:iproute2mac`, `brew:rsync`, `cask:tor-browser`) and all three are
genuinely security-only releases.

**A security item at `warning` is allowed**, at any severity: a high-severity
*security* item is a reason to take the update, not to hold it.

### `impact` — the user's "confirmed no impact on me"
Computed by the validator (`compute_impact`) and read off its view.

```
brew-health / skill-drift  → "none" if the finding is expected, else "possible"
no items, or research/validator error → "unknown"
pinned, or config needs attention,
  or an edit/structural suggestion,
  or an item at "incompatible",
  or an item with local.effect == "risk" at notable+,
  or a "breaking"-tagged item at warning+     → "possible"
otherwise                                     → "none"
```

Three heuristics present in the pre-item implementation are **gone**, and their
removal is the point (`REDESIGN.md` criterion 1):

- **`category != "security"` as a proxy for "not a risk"** → replaced by
  `local.effect == "risk"`. The documented reason for the proxy — "a security
  relevancy is a reason to *upgrade*, not a risk of upgrading" — is now said
  directly by the checker in a field built for it. The proxy was also wrong in
  one direction: a security change *can* be a risk here.
- **the separate headliner clause**, which existed only because a headliner had
  no local finding. Its real case (`mise:rust`) is now `breaking`-tagged items
  at warning+, which the last clause covers.
- **`"watch-item"` in the suggestion clause** (`REDESIGN.md` §D row 4). A watch
  item proposes a change to what we remember, not to the user's system.

Two "never" rules hold the whole thing up:

- **No items ⇒ never `security_only`, and `impact` is `"unknown"`.** A checker
  that failed, timed out or returned an empty shell told us nothing; "we know
  nothing" must never be reported as "nothing but security fixes". A validator
  stage that failed halfway counts the same way — what survived is not the
  corpus. `"unknown"` can never reach `security_auto`, so it never renders as
  auto-approved.
- **A `security`-tagged item is not by itself impact.** Counting it as impact
  made `security_auto` permanently empty across a whole live run.

## Risk Level
Computed by the validator (`compute_risk_level`) and read off its view — one
implementation, so the report never carries two answers.

`"elevated"` if any of: `pinned`; any item carrying a `local` block at
`warning`/`incompatible`; any `edit`- or `structural`-kind suggestion;
`version_delta` is `major`/`unknown`; `research_error` or `validator_error` is
set; `vendor_silent_categories` contains `"security"`; or the tool has no items
**and** an empty `vendor_silent_categories`. Otherwise `"low"`.

The `"security"`-silence clause and the no-items clause read the same field and
mean opposite things, so they are worth keeping apart in your head. A non-empty
`vendor_silent_categories` normally means "this vendor publishes nothing, ever"
— claudebar, every run — and *suppresses* the no-items elevation, because
demanding attention for it each time is noise. `["security"]` means the
opposite: there is security content and we could not read it. Without its own
clause the first rule swallows the second, the tool is not elevated, and
`pre_accept` — which reads `risk_level` — auto-approves an unread security
release.

**How far the clause reaches is decided by the bucket precedence, not by it.**
`security_auto` returns from clause 2 and `risk_level` is not consulted until
clause 4, while `pre_accept` is `risk_level == "low"` **or**
`review_bucket == "security_auto"`. So elevated risk is not a bar on
pre-acceptance for any tool that reaches `security_auto`, and this clause stops
pre-acceptance for exactly the tools that miss it — which is the shape that
motivated it, a vendor-silent-security tool carrying non-security content. One
whose readable items are all security-only is still pre-accepted, by design.

The last three conditions extend the "an unknown delta size is never low-risk"
doctrine to unknown *content*: a tool whose checker failed has no items and no
edit suggestions, and would otherwise score `"low"` and get pre-accepted — the
skill would silently auto-approve exactly the updates it understands least.
Documented silence is different and stays `"low"`: a vendor that publishes
nothing, ever, is noise the user cannot act on.

The "an item with a `local` block at warning+" clause is the old "a relevancy
item above info" clause over the same population — an item with a `local` block
*is* a relevancy item, said as what it always meant. `structural` joins `edit`
because both are authored, non-baseline actions.

## Review Buckets and Pre-Accept
`review_bucket` answers "how much of a human does this need"; `pre_accept`
answers "does this one suggestion start accepted". They are computed back to
back, in that order, because the second reads the first.

### `compute_review_bucket()` — strict precedence, first match wins
Computed by the validator as `initial_review_bucket`, carried onto the Tool
object as `review_bucket`, and carried alongside it as `bucket_inputs` —
`{has_security, security_only, impact, version_delta, runnable}` — so a reader,
and convergence once it exists, can see *why* without re-deriving it.

**It is a baseline for convergence to review, not a decision**
(`REDESIGN.md` §C3). The name differs between the two files for exactly that
reason.

Order of evaluation:

1. the two non-version sources — `brew-health` and `skill-drift`: `routine`
   when the finding is expected, else `attention`;
2. `security_auto` — `has_security` **and** `security_only` **and**
   `impact == "none"` **and** `version_delta` not `major`/`unknown` **and** a
   runnable baseline;
3. `security_mixed` — `has_security`;
4. `attention` — elevated `risk_level`, or stale `config_status`, or any
   non-`upgrade` suggestion, or nothing runnable;
5. `routine`.

No delta or `research_error` test appears in the `attention` clause on purpose:
`risk_level` is computed first and already returns `"elevated"` for both, so
repeating them would be dead code that reads like a safety net.

**`has_security` is wider than the tag.** It is true when any item is tagged
`security`, **or** `vendor_silent_categories` contains `"security"`, **or** any
item carries a `security` block at all. The second and third limbs are safety,
not tidiness, and the effective value is computed **once**
(`validate_items._derive_axes`) and used by `security_only`, the bucket and
`bucket_inputs` alike — a value that is "security" for bucketing and "not
security" for the security-only test is its own auto-accept route. The full
argument, including why `items.recompute_flags` stays tag-only, is in
`references/schemas.md` §1.9.

### `pre_accept` — one mechanism, not two
```python
def baseline_upgrade(tool):
	"""suggestions[0], if it is kind "upgrade" with an id ending ":upgrade"."""

def apply_pre_accept(tool):
	baseline = baseline_upgrade(tool)
	for sug in tool.get("suggestions", []):
		sug["pre_accept"] = bool(
			sug is baseline
			and sug.get("auto_runnable")
			and (tool["risk_level"] == "low" or tool["review_bucket"] == "security_auto"))
```

The flag is written onto **every** suggestion, so no consumer has to
distinguish "false" from "absent". The page reads it and never re-derives the
decision — that re-derivation is exactly how the rendered state and the
submitted payload drift apart (`references/rendering-report.md` §Suggestion
Card).

Precisely what can be pre-accepted:

- **Only the baseline `{source}:{name}:upgrade` suggestion.** Never a
  research-authored `kind: "edit"` (those always start undecided —
  `references/schemas.md` §1.4), never a `kind: "watch-item"`, and never a
  brew-health `{tool_id}:remediate`: it *is* `suggestions[0]` for a health
  tool, but its id ends `:remediate`, so `baseline_upgrade()` returns `None`
  and no health suggestion can pre-accept — including an `auto_runnable: true`
  one like `brew install dtc`. That is the same outcome §Brew-Health Assembly
  already documented, now enforced in code instead of holding by coincidence.
- **`auto_runnable: false` is a hard exclusion.** A `macos` or `standalone`
  baseline, or a health remediation the user must run themselves, never
  pre-accepts: "accepted" would claim a decision about something the skill
  cannot execute.
- **`needs_sudo: true` does *not* block it.** This looks wrong next to the
  rule above until you see the two facts behind it. First, blocking it would
  un-pre-accept nearly every cask — the heuristic defaults casks to `true`
  unless research sets `cask_sudo_hint: false` — which guts the feature; the
  live run's `cask:wireshark-app` is a `security_auto` cask with
  `needs_sudo: true`. Second, it is not silent: the card renders visibly as
  ACCEPTED before the user submits, and at apply time `needs_sudo` routes
  through the askpass prompt (`references/apply.md` §Executing Upgrade
  Suggestions), which the user answers interactively. The page's side of that
  bargain is mandatory: **a pre-accepted `needs_sudo: true` suggestion must
  render an explicit "needs admin password" chip** wherever it appears,
  including its Overview mirrors. If that trade is ever rejected, the stricter
  variant is one clause — `and not sug.get("needs_sudo")` — in
  `apply_pre_accept()`.

**How `security_auto` interacts with `risk_level`.** `pre_accept` is a
*union* of the existing `risk_level == "low"` path and
`review_bucket == "security_auto"`, evaluated at one place on one field.
There is no second mechanism, no second decision surface in the page, and the
`/feedback` payload is unchanged.

The union can differ from `risk_level == "low"` in exactly **one** situation,
and it is provable from the definitions: `security_auto` requires
`impact == "none"`, which already excludes `pinned`, `needs_attention`, any
`edit`/`structural` suggestion, any item at `incompatible` and any item whose
`local.effect == "risk"` at `notable`+; and it requires `version_delta` not in
`("major", "unknown")`, which excludes the delta condition. The only remaining
way for such a tool to be `elevated` is **a `security`-tagged item with a
`local` block at `warning` severity** — "this security fix matters to you", which is a reason
to take the update, not to hold it. Measured: exactly 2 of the 8
(`brew:libpq`, `cask:wireshark-app`). Overall effect on that run: roughly two
thirds of the baselines start accepted — 48 of the 74 synthesized ones. The
three brew-health findings have no baseline at all, so they are outside that
denominator, not rejections of it.

## `needs_sudo` Heuristic
Applied per-suggestion at synthesis time (below), by source:
- `brew`, `mise`, `standalone` → always `false` — these never invoke a
  privileged installer themselves.
- `skill-drift` → always `false`. The sync is a `git subtree pull` inside a
  repo the user owns; nothing about it is privileged. Stated as its own arm
  rather than left to the `unknown` fallback below, so the answer is
  deliberate instead of accidental — the fallback would have said `true` and
  put a needless askpass prompt in front of a git command.
- `cask` → `true` **unless** the research object explicitly set
  `"cask_sudo_hint": false` on its returned Tool object — never assumed
  false by default. Only a handful of casks ship a `pkg` installer needing
  elevation (e.g. Wireshark's ChmodBPF helper), but assuming `false` and
  hitting an unsatisfiable password prompt is worse than an unnecessary
  askpass popup, so the default leans `true`.
- `macos` → always `true`.
- anything else (unknown source) → `true`.

See `references/apply.md` §Executing Upgrade Suggestions for what `true`
actually triggers at apply time (the askpass mechanism).

## `auto_runnable` / Command Per Source
The baseline suggestion's `command` and `auto_runnable` default are also
source-driven, and so — WP5/I2 — is whether `command` pins the exact
version that was reviewed (`target_version`, §1.6) or apply has to check
for drift separately (`version_pinned`; `references/apply.md` §Pinning the
reviewed version):

| Source | Command | `auto_runnable` | `version_pinned` |
|---|---|---|---|
| `brew` | `brew upgrade {name}` | `true` | `false` — no general `brew install name@version` |
| `cask` | `brew upgrade --cask {name}` | `true` | `false` — same reason |
| `mise` | `mise upgrade {name}@{target_version}` (or `mise upgrade {name}` if `name` already contains `@`) | `true` | `true` unless `name` already contains `@`, in which case `false` — pinning is refused rather than risking a malformed `name@version@version` command |
| `standalone` | none | `false` — no generic upgrade command exists; check the tool's own docs | `false` — moot, no command |
| `macos` | none | `false` — install via System Settings or `softwareupdate -i`, never auto-run by this skill | `false` — moot, no command |
| `skill-drift` | none | `false` — vendored-skill sync is always manual (§Skill-Drift Assembly) | `false` — moot, no command |
| unknown | none | `false` | `false` |

`upgrade_command_and_runnable(source, name, version)` takes the tool's
`latest_version` as `version` and only mise's branch reads it — passing it
to brew/cask changes nothing about their `command`, which is the point: a
generic formula/cask upgrade cannot be pinned by argument, so
`references/apply.md`'s `scripts/check_pin.py` preflight/verify is how
apply catches drift for them instead. `version` defaults to `None` so a
pre-assembly caller that only wants `auto_runnable` (`validate_items.py`,
which runs before a version is finalized) keeps working unchanged and
never pins by accident.

**`build_tool()` refuses to synthesize a runnable or pinned baseline when
`tool["latest_version"]` is falsy** — collection degrading per-tool rather
than aborting (§G1) means a candidate can reach here with no usable
`latest_version` at all (the same "missing version" shape
`compute_version_delta` already recognizes for the version-delta axis). In
that case `command`/`version_pinned` are forced to `None`/`false` and
`auto_runnable` to `false` regardless of what the source table above says,
with a `manual_reason` explaining there is nothing to pin or verify
against. Never leave a suggestion runnable with a target `scripts/check_pin.py
verify` could never match — that would report every such upgrade as failed
forever, including ones that landed correctly, which is worse than no
check at all.

When `auto_runnable` is `false`, a `manual_reason` string is attached
explaining why — the session always just tells the user what to run for
that suggestion, never executes anything (see `references/apply.md`).

The `skill-drift` row is defensive rather than load-bearing: no baseline is
synthesized for that source at all (§Baseline Suggestion Synthesis), so the
row is never reached in a normal run. It is written down anyway because the
`unknown` fallback is a *safe* default only by accident, and a source whose
manualness is a documented rule should say so in the table rather than
inherit it (`manual_reason: "Vendored-skill sync is always manual."`).

## Baseline Suggestion Synthesis
**Every tool gets a synthesized baseline `kind: "upgrade"` suggestion**, id
`{source}:{name}:upgrade`, inserted as the first element of its
`suggestions[]` array. This is mechanical assembly-step work, never left to
the research subagent — it's the only thing that actually tracks an
outdated tool to completion; without it, a plain patch bump with no config
impact would get zero suggestions and silently never get upgraded.
Research-authored `edit` suggestions are always additional to this baseline,
never a replacement for it. The baseline's `rationale` is the fixed string
"Picks up the changes described in items[] above."; its
`motivating_link` is the tool's first `links[]` entry if one exists, else
`null`; its `target_version` is `tool["latest_version"]` — the version that
was reviewed, which `references/apply.md` must install or refuse (WP5/I2,
§`auto_runnable` / Command Per Source above) — except when collection could
not determine one at all, in which case `target_version` stays `null` and
the baseline is forced `auto_runnable: false` rather than left runnable
with nothing to verify against (§`auto_runnable` / Command Per Source
above).

**Exception: the two non-version sources get no baseline upgrade
suggestion** — a `brew-health` or `skill-drift` finding has no version to
upgrade at all (`current_version`/`latest_version` are both `null`). See
§Brew-Health Assembly and §Skill-Drift Assembly below for what each gets
instead.

**The baseline is the only suggestion that can carry `pre_accept: true`**
(§Review Buckets and Pre-Accept). `baseline_upgrade()` identifies it
structurally — `suggestions[0]`, `kind: "upgrade"`, id ending `:upgrade` — and
that identification is why `finalize_tool()` must run *before* the
suggestion-id uniqueness pass, which can rename a colliding id to
`…:upgrade-2`. The three-part test is deliberate rather than "the first
suggestion": a brew-health tool's `{tool_id}:remediate` and a skill-drift
tool's `{tool_id}:sync` also sit at index 0 with `kind: "upgrade"`, and the
id suffix is the one thing that tells them apart — which is what makes
pre-accept impossible for either source *by construction* rather than by a
flag someone could set the other way.

## Highlights
"The biggest decision drivers / inputs needed / major patches", as a ranked
top-8 array on the report (`references/schemas.md` §1.11 for the object
shape). Scoring is deterministic and stdlib-only — `assemble.py` is a plain
script, so the ranking has to be reproducible from the data alone, with no
per-run judgment anywhere in it.

`build_highlights()` runs in `main()` **after** the suggestion-id uniqueness
pass, so `highlights[].suggestion_ids` carry the final ids. That is the one
ordering requirement; everything else in this section is pure function of the
finished Tool objects.

### Scoring
`score_tool(tool)` returns `(score, reasons)` — reason codes in this fixed
emission order, so the page's chips never reshuffle between runs.

| Points | Reason code | Fires when |
|---:|---|---|
| 100 | `incompatible_finding` | any item **with a `local` block** at `incompatible` |
| 60 | `config_stale` | `config_status.state == "needs_attention"` |
| 45 | `warning_finding` | any item **with a `local` block** at `warning` |
| 40 | `breaking_change` | any `breaking`-tagged item at `warning`/`incompatible` |
| 35 | `proposed_edit` | any suggestion with `kind` in `edit`/`structural` |
| 30 | `pinned` | `tool.pinned` |
| 30 | `security_mixed` | `review_bucket == "security_mixed"` |
| 25 | `major_bump` | `version_delta == "major"` |
| 25 | `watch_item_proposed` | any suggestion with `kind == "watch-item"` |
| 20 | `changelog_warning` | any item **without** a `local` block at `warning` |
| 20 | `research_failed` | `research_error` or `validator_error` is set |
| 15 | `unknown_scheme` | `version_delta == "unknown"` |
| 10 | `cves` | `security.cve_count >= 1` |
| 10 | `manual_action` | a baseline exists and its `auto_runnable` is false |

`review_bucket == "security_auto"` contributes **nothing** — it is by
definition the bucket that needs no decision.

**The old relevancy/headliner pairs became one question: does this item carry a
`local` block?** `incompatible_finding` vs `breaking_change` and
`warning_finding` vs `changelog_warning` were always "is this about my machine
or about the release", inferred from which array the item was written into.
`breaking_change` now reads the `breaking` tag directly rather than inferring a
release-level break from an `incompatible` headliner.

**`watch_item_hit` is gone, and its absence is deliberate.** It scored 70 for a
regex match on the literal phrase `Watch item hit:` in relevancy prose —
a magic string `REDESIGN.md` §I4 retires outright, because a subagent that
paraphrased it made the user's own standing concern silently worth nothing. The
replacement is a **structured field** on the checker's output, which does not
exist in the item schema yet; the signal is removed rather than reimplemented
against prose that no longer has a guaranteed shape. Restoring it means adding
the field, not the regex.

`manual_action` reads the baseline through `baseline_upgrade()`, which is why
a cross-tool id collision that renames a baseline costs a tool this one
10-point signal (§Overview, ordering constraint 2). Bounded and logged.

**CVE severity is available and `score_tool()` deliberately does not use it.**
Weighting `critical` ids would push highlights *toward* the security content
this design exists to stop them restating, and §Threshold is already explicit
that a bare CVE count must not qualify a tool on its own. Measured before
deciding: on the recorded run the flat 10-point `cves` signal fires on 3 of the
8 highlights and changes the membership of none of them. Do not re-litigate it
without a measurement that says otherwise.

### Threshold, cap, ordering
- **Threshold: score ≥ 40.** A bare `major` (25) or a bare CVE count (10)
  does not qualify on its own — this is what keeps Chrome/Firefox/gcloud's
  rolling majors out of highlights while leaving them counted in
  `summary.by_delta.major`. Lowering the threshold re-admits exactly the noise
  it exists to exclude.
- **Cap: 8**, with no per-source quota. A brew-health finding competes on the
  same scale, which is correct — a missing dependency is a real "input
  needed", and `brew-health:missing_dependency:dtc` ranked 4th at 175 points
  in the live run. Raising the cap is safe; lowering the threshold is not.
- **Sort key:** `(-score, -max_severity_rank, -cve_count, tool_id)`, where
  `max_severity_rank` is taken over `items[]` with `items.SEVERITY_RANK`
  (`{info: 0, notable: 1, warning: 2, incompatible: 3}`, absent → −1). The
  trailing `tool_id` makes ties fully deterministic.

### The highlight object
- `title` — `f"{name} {current_version} → {latest_version}"`, or the
  finding's `name` for a brew-health or skill-drift tool, which has no
  versions at all. `_highlight_title()` needs an arm per non-version source;
  without one the card renders the literal `name None → None`.
- `why` — the first match in this fixed order, whitespace-collapsed and
  truncated to 220 chars on a word boundary with `…`:
  1. the highest-severity item **carrying a `local` block**, by its `title`
     (ties resolve to canonical order — `items[]` arrives already sorted);
  2. `config_status.detail`, when `state == "needs_attention"`;
  3. `"Research produced no changelog for this update."`, when
     `research_error`;
  4. the first **non-security** item's `title`, when the bucket is
     `security_auto`/`security_mixed`;
  4b. the first `security`-tagged item's `title`, only when the tool has no
     non-security item to say instead;
  5. `f"Major version bump {current} → {latest}."`, when
     `version_delta == "major"`;
  6. the first item's `title`;
  7. `""` — nothing to say, only reachable for a tool with no items at all.

  Step 4 used to *be* 4b, and that guaranteed the duplication the whole
  security redesign is about: it returned the same line the mixed card's
  security column renders in full. Step 1 stays first even when the winning
  item is security-tagged — for `cask:windows-app` ("The installed 11.3.7
  predates both security releases in this range, including the CVE-2026-61352
  RDP client RCE") that line *is* the decision, and demoting it would leave a
  worse one. The duplication step 1 can still produce is handled by the
  drop-and-backfill below, not by picking a weaker line.
- `why_source` — which of the branches above produced the line
  (`item_local_security` | `item_local_other` | `config_status` |
  `research_error` | `item_security` | `item_other` | `major_bump` | `none`).
  The `local_` half means the line came from an item with a `local` block —
  the old `relevancy_*` vs `headliner_*` split, said as what it always meant.
  Provenance for a reviewer and for the dedupe; a page may ignore it.
- `why_ref` — the **item id** `why` came from, or `null` for the branches that
  synthesize their own text.
- `severity` — max severity across `items[]`, in the item severity vocabulary
  so the page reuses one palette. With no items: `needs_attention` →
  `"warning"`; `research_error` or a `major`/`unknown` delta → `"notable"`;
  else `"info"`.
- `suggestion_ids` — **every** suggestion id on the tool, in array order
  (baseline first when present). The page looks each id up in `tools[]` to
  decide whether to offer a jump or a decision control, and renders the raw
  id rather than dropping a row when the lookup fails — a silent drop would
  hide an assembly bug.

**De-duplication against the security cards, with backfill.** After ranking,
`build_highlights()` walks the sorted candidates and skips any whose `why_ref`
appears in that tool's `security.display_item_ids`, continuing down the list
until the cap is filled. **The highlight yields, never the security card** — on
the tools where this fires the duplicated line is usually the most important
sentence on the card, and the section is supposed to carry eight *distinct*
decision drivers rather than eight rows of which two repeat something 200px
below. Each drop writes a `note:` to `assemble.log` naming the tool and the id.

The match is on the **item id** on both sides, never on text. `why` has already
been through `_truncate_why()`'s 220-char cut, and the live run carries a
222-char item title (`brew:mise`) — a string comparison would have failed there
silently, which is the failure mode this design exists to avoid.

Measured on the recorded corpus re-expressed in the item model, the top 8 moves
by exactly one row against the pre-item baseline, and for a traceable reason:
`cask:windows-app` (155) was previously *dropped* by this dedupe because its
`why` restated a `notable[]` entry the old cap had promoted regardless of
direction. Under the bar nothing is selected for that tool, no dedupe fires, it
takes its slot, and `cask:codex` (135) falls off the bottom. Both tools still
render in full; only the ranking moved.

## Brew-Health Assembly
A `brew_health.findings[]` entry (emitted by `collect.sh`, see
`references/collection.md` §Brew-Health Collection for the finding shape and
noise filter) becomes a Tool object with `source: "brew-health"`, an extra
`health_category` (the finding's `category`) and `health_expected` (the
finding's `expected` flag) pair, and no version-delta fields.

**Items**: research's `items[]` if a brew-health subagent (or the orchestrator
directly — see `references/research.md`) enriched this finding; otherwise
assembly synthesizes exactly one item from the finding's own `detail`, so the
problem still shows in a content group even with no enrichment.

The synthesized item carries **`change: null` and a `local` block**, which is
more honest than the old synthetic headliner: a health finding is not an
upstream change at all, it is a statement about *this* install. `direction` is
`reaches` for the same reason, and `effect` is `risk` unless the finding is
`expected`. It carries no `evidence[]`, and nothing reports that — the item is
synthesized after validation, so I-14 never sees it, and inventing an evidence
path for a `brew doctor` line would be inventing a citation.

The item's **tag** (and so which of Security/Fixes/Features/Notes it renders
under, via `items.GROUP_OF_TAG`) is derived from `health_category` by a fixed
mapping. The tags below reproduce the previous category mapping exactly:

| `health_category` | Tag → content group |
|---|---|
| `untrusted_tap` | Security |
| `missing_keg`, `unlinked_keg`, `missing_dependency` | Fixes |
| `deprecated_cask`, `disabled_cask`, `deprecated_formula`, `disabled_formula`, `path_note`, `other` | Notes |

(Untrusted taps map to Security because trusting/untapping is a
security-relevant decision, not a topic-neutral fix.)

**Suggestions**: research's `suggestions[]` if present (research can author
a better fix than the default — e.g. a Brewfile `edit` migrating a
deprecated cask, or the trust-vs-untap judgment for a tap — see
`references/research.md` §Brew-Health Enrichment); otherwise assembly
synthesizes a single suggestion from the finding's own default
`remediation` object (`id: "{tool_id}:remediate"`, `kind: "upgrade"`,
`command`/`auto_runnable`/`needs_sudo` copied straight from `remediation`).
A finding with `remediation: null` (an `expected` finding like the
intentional GNU-utils PATH note) gets **no** suggestion at all — it renders
as a quiet info card with nothing to decide. When the synthesized
suggestion's `auto_runnable` is `false`, assembly attaches
`manual_reason: "Structural brew change — review and run this yourself."`

**Derived fields for brew-health.** A health finding goes through the same
`finalize_tool()` entry point as every other tool (§Overview), so it can
never disagree about what a field means — but almost every field
short-circuits, because there is no version pair and no changelog:

| Field | Value | Why |
|---|---|---|
| `version_delta` | `"unknown"` | There is no delta. Reusing `"unknown"` rather than inventing a sixth enum value keeps the field's type uniform for the page. |
| `version_scheme` | `"none"` | Explicitly "there was nothing to parse", so the UI doesn't caption it as an unrecognized scheme. |
| `version_delta_note` | `"no version delta for this source"` | |
| counted in `summary.by_delta` | **No** | Health findings are environment issues, not updates. Excluding them preserves `sum(by_delta) == total_outdated`, and stops non-updates inflating the "unknown" box. |
| `security.has_security` | `false`, always | The security section is about *patches* the user can take. An untrusted tap is a trust decision, not a shipped fix; counting it in `tools_with_security` would make the section's count disagree with the cards it lists. The tap's security character still shows — the category map above files `untrusted_tap` under Security, so the card renders with a `security`-tagged item, and the finding's own `severity` drives highlight scoring. |
| `security.cve_ids` / `cve_count` / `cve_claimed_count` | `[]` / `0` / `null` | Extraction is skipped for this source. |
| `security.security_only` | `false`, always | Follows from `has_security: false`. |
| `security.impact` | `"none"` if `health_expected` else `"possible"` | A health finding is *about this machine* by definition; an expected one (the intentional GNU-utils PATH note) is explicitly no-action. |
| `review_bucket` | `"routine"` if `health_expected` else `"attention"` | Every structural finding (unlinked keg, missing dependency, untrusted tap, deprecated cask) demands a human. |
| `risk_level` | `"low"` if `health_expected` else `"elevated"` | Unchanged semantics, now produced by `compute_risk_level()` instead of a hand-set line at the end of `build_health_tool()`. |
| `pre_accept` | `false` on every suggestion | `baseline_upgrade()` returns `None` for a health tool — its suggestion id ends `:remediate`, not `:upgrade` — so no health suggestion can ever pre-accept, including `brew install dtc`, which *is* `auto_runnable: true`. |

**How health findings stay visible instead of being swept into "routine":**
the page groups them by `source == "brew-health"` and counts them with
`summary.health_count` — **never** by `review_bucket`. `review_bucket` is a
*review-effort* axis, orthogonal to the source axis; `routine` on the one
expected PATH note means "nothing to decide here", not "hide it". Structural
findings additionally land in `attention` and, when their severity and
relevancy warrant it, in `highlights` (the `dtc` missing-dependency finding
ranked 4th in the live run).

**`summary.health_count`**: the count of `source: "brew-health"` tools,
tracked separately from `summary.total_outdated` (which excludes them) since
they're environment issues, not version updates. `collect.sh`'s
`suppressed` list (the noise-filtered findings — see
`references/collection.md`) is logged to stderr here, not rendered anywhere.

See `references/rendering-report.md` §Brew-Health Rendering for how the
category label, source badge, and `health_count` badge actually render, and
`references/apply.md` §Brew-Health Remediation for how these suggestions get
applied.

## Skill-Drift Assembly
A `skill_drift.findings[]` entry (emitted by `collect.sh` via
`collect_skill_drift.py` — see `references/collection.md` §Skill-Drift
Collection for the finding shape and the three-way tree-hash comparison
behind it) becomes a Tool object through `build_drift_tool()`, which is
`build_health_tool()`'s structure with a different vocabulary: `source:
"skill-drift"`, plus `drift_state`, `drift_expected` (the finding's
`expected` flag), `drift_vendor` and `drift_skill`
(`references/schemas.md` §1.3). `current_version`/`latest_version` are
hardcoded `None`, `pinned` `False`, `research_error` `None`, and — as in
every build path — `research_obj = research_obj or {}` happens *first*, so a
research file that returned a bare `null` for this finding can't
`AttributeError` on the next key read.

**Items**: research's `items[]` if the skill-drift group enriched this finding
(`references/research.md` §Skill-Drift Enrichment); otherwise assembly
synthesizes exactly one from the finding's own `detail`, carrying the finding's
`severity`, so the drift still shows in a content group with no enrichment at
all. Same shape as brew-health's synthesized item — `change: null`, a `local`
block, `direction: "reaches"` — and its tag comes from a fixed `drift_state` →
tag map, the same shape the `health_category` map has:

| `drift_state` | Tag → content group |
|---|---|
| `upstream_ahead`, `diverged` | Fixes |
| `local_only`, `probe_error` | Notes |
| `in_sync` | Notes — defensive only; an in-sync skill is never a finding |

(Nothing maps to Security. A skill lagging its upstream is a maintenance
fact, not a shipped patch — see the `has_security` row below. An
unrecognized state falls back to `chore`/Notes rather than dropping the item,
for the same reason the label map has a fallback: a state we don't know
about is still a thing the user should see.)

**Suggestions**: research's `suggestions[]` if present (research can author
a better action than the default — e.g. an `edit` recording a newly
discovered local patch in the vendor's `CUSTOMISATION.md`, or a `diverged`
finding's conflict-review note); otherwise assembly synthesizes a single
suggestion from the finding's `remediation` object, with
`command`/`auto_runnable`/`needs_sudo` copied straight from it. A finding
whose `remediation` is `null` — every `local_only` and every `probe_error`
— gets **no** suggestion at all. That is a quiet card with nothing to decide
in every case but one: the `probe_error` that means upstream removed or
renamed an adopted skill has no command to offer (running the sync is what
would lose the skill) yet is `expected: false`, so it buckets to
`attention` and carries its decision in its detail text instead. Because the synthesized suggestion is always `auto_runnable: false`,
it always carries a `manual_reason`.

**The suggestion id is `{tool_id}:sync`, and that is the whole pre-accept
story.** `baseline_upgrade()` accepts only an id ending `:upgrade`
(§Baseline Suggestion Synthesis), so it returns `None` for a drift tool and
`apply_pre_accept()` writes `pre_accept: false` on every suggestion the tool
has. There is no flag to get wrong and no second code path: a sync rewrites
vendored files inside the dotfiles submodule and can conflict with a local
customisation, which is never a "just do it" — exactly the reasoning that
bars a brew-health `:remediate`.

**Derived fields for skill-drift.** A drift finding goes through the same
`finalize_tool()` entry point as every other tool (§Overview), so it cannot
disagree about what a field means — but as with brew-health, almost
everything short-circuits, because there is no version pair and no
changelog:

| Field | Value | Why |
|---|---|---|
| `version_delta` | `"unknown"` | There is no delta. Reusing `"unknown"` keeps the field's type uniform for the page rather than inventing a sixth enum value. |
| `version_scheme` | `"none"` | Explicitly "nothing to parse". |
| `version_delta_note` | `"no version delta for this source"` | Same string the other non-version sources use. |
| counted in `summary.by_delta` | **No** | Drift is not an update. Excluding it is what preserves `sum(by_delta) == total_outdated` unchanged (§Summary Counts and Output). |
| `security.has_security` | `false`, always | The security section is about patches the user can take. A stale vendored skill is a maintenance fact; counting it would make the section's count disagree with the cards it lists. |
| `security.cve_ids` / `cve_count` / `cve_claimed_count` | `[]` / `0` / `null` | Extraction is skipped for this source. |
| `security.security_only` | `false`, always | Follows from `has_security: false`. |
| `security.severity_counts` | every band `0` | Rolls up over `cve_ids`, which is empty here. The keys still ship, so the page's severity code needs no per-source special case (§Severity Rollup and the Sum Invariant). |
| `security.display_item_ids` | `[]`, always | Forced for the same reason `has_security` is: an inline security item on a card whose security strip never renders would be a lie. |
| `security.impact` | `"none"` if `drift_expected` else `"possible"` | Drift is *about this machine* by definition; the expected kinds (`local_only`, `probe_error`) are explicitly no-action. |
| `risk_level` | `"low"` if `drift_expected` else `"elevated"` | `upstream_ahead` and `diverged` both mean a decision is owed; a deliberate local patch does not. |
| `review_bucket` | `"routine"` if `drift_expected` else `"attention"` | Same split, on the review-effort axis. |
| `pre_accept` | `false` on every suggestion | The `:sync` id suffix, above. |

**How drift findings stay visible instead of being swept into "routine":**
the page groups them by `source == "skill-drift"` and counts them with
`summary.skill_drift_count` — **never** by `review_bucket`, which is a
review-effort axis orthogonal to source. `routine` on a `local_only` finding
means "nothing to decide here", not "hide it"; the user still gets to see
that we are carrying a patch upstream doesn't have.

**`summary.skill_drift_count`**: the count of `source: "skill-drift"` tools,
tracked separately from `summary.total_outdated` (which excludes them) for
the same reason `health_count` is — these are not version updates. The
detector's `suppressed` list (in-sync skills, our own `tapppi/` skills, the
adopted skills of an unprobed vendor — see `references/collection.md`) is
logged to stderr here, not rendered anywhere.

**Granularity, stated openly.** `git subtree pull` is per-vendor, so every
drifted skill of one vendor carries the *same* vendor-level command; the
remediation `label` says so (`"Sync anthropics from upstream (updates all 3
drifted anthropics skills)"`), and accepting one of them resolves its
siblings too. Assembly does not merge them into a single card: each skill is
its own finding because each may need its own read of what changed upstream.
See `references/apply.md` §Skill-Drift Remediation for how the apply step
handles the resulting one-command-many-actions case.

See `references/rendering-report.md` §Skill-Drift Rendering for how the
drift-state label, source badge and Overview band render, and
`references/research.md` §Skill-Drift Enrichment for what a research
subagent adds to one of these findings.

## `config_status` Normalization
Normalized by the validator, not by assembly. A checker can legitimately return
`config_status: null` — nothing to compute for a macOS-source tool — rather
than omitting the key, and `dict.get(key, default)` only substitutes when the
key is *absent*, so a present-but-null value used to reach a later
`.get("state")` as `None` and crash the run.

The view always carries a dict with `state`, `detail` (never `null`),
`evidence` (path objects) and `citations` (prose). A `config_status` that is
present but not an object is reported as `W-SHAPE-COERCED` and read as
`unknown`.

The evidence/citation split matters most here: `config_status` produced 133 of
the recorded run's 272 `evidence not found` warnings, against 286 strings.

## `needs_attention`-Must-Have-a-Suggestion Enforcement
A `config_status.state` of `"needs_attention"` (computed by research — see
`references/research.md` §Config Status for the full rule) is supposed to
always pair with at least one suggestion addressing it; a banner telling the
user something might be stale with nothing to act on just relocates the
"did anyone check this?" question rather than answering it. Assembly can't
fix a violation of this rule (it's a research-prompt-following problem, not
a structural one it can repair), but it does **surface it loudly**: if a
tool's `config_status.state` is `"needs_attention"` and its `suggestions[]`
array is empty after merging research, assembly prints a warning to stderr
naming the tool. This applies identically to brew-health Tool objects (via
`build_health_tool`) and ordinary version-outdated ones (via `build_tool`) —
the same guard runs in both code paths.

## Summary Counts and Output
`summary` in the final report object:
- `total_outdated` — the tools whose `source` is **not** in
  `NON_VERSION_SOURCES`, counted the same way `by_delta` counts them
  rather than by subtracting each finding count off `len(tools)`. Adding a
  fourth finding source to the set is then the whole change; under the
  subtraction form, forgetting the new term was how `by_delta` silently
  stopped summing to it.
- `incompatible_count` / `warning_count` — counts of items **carrying a
  `local` block** across all tools at that severity. The `local` filter is the
  successor to counting `relevancy[]`: an item without one is a statement about
  the release, not about this machine, and these two boxes say "how many
  findings land on me". A brew-health or skill-drift finding now counts, where
  it could not before — it had no relevancy entry to be counted through — and
  counting it is the honest answer to the same question.
- `suggestions_count` — total suggestions across all tools (after id
  uniqueness resolution).
- `health_count` — see §Brew-Health Assembly above.
- `skill_drift_count` — see §Skill-Drift Assembly above.
- `by_delta` — `{major, minor, patch, revision, unknown}` over
  **version-outdated tools only**. The skip is a membership test over the
  set of non-version sources, not a second `==` chained onto the first, so
  the next such source is one string in one place.
- `by_bucket` — `{security_auto, security_mixed, attention, routine}` over
  **every** tool, brew-health and skill-drift included.
- `security` — `{cve_count, severity_counts, tools_with_security, auto_count,
  mixed_count, tools_with_unlisted_cves}`, where `cve_count` is the size of the
  *union* of `cve_ids` across tools (§Security Extraction), not the sum, and
  `severity_counts` is rolled up over that same union — never summed from the
  per-tool counts (§Severity Rollup and the Sum Invariant).

**`by_delta` and `by_bucket` have different denominators, deliberately.**
`by_delta` sums to `total_outdated` (74 in the live run); **`by_bucket` sums
to `len(tools)`** — every Tool object is bucketed exactly once — which is
`total_outdated + health_count + skill_drift_count` written out by source
(77 in that run, which predates skill-drift and so carries a zero third
term). State it the first way when you need a rule that survives the next
source, the second when you need a concrete check. The asymmetry is
load-bearing: `version_delta` is meaningless for a non-version finding,
while `review_bucket` is defined for every Tool object and the page's bucket
lists render health and drift cards alongside version ones. A consumer that
mixes the two denominators in one percentage —
`by_bucket.routine / total_outdated` — produces a number that means nothing,
and that is precisely the bug this note exists to prevent.

**`by_bucket.attention` is not "updates needing attention".** Every
non-version finding that is not `expected` buckets there: on the live run
that is 12 drifted skills, which take `attention` from 12 to 24 without a
single update changing. The page never renders the raw number — it filters
non-version sources out of the Overview's chip clouds and gives drift its own
band (`references/rendering-report.md` §Skill-Drift Rendering) — but any
other consumer of `report.json` reading the field as an update count is off
by exactly that many. There is no version-only bucket count in `summary`; a
consumer that wants one counts `review_bucket` over the tools whose `source`
is not in `NON_VERSION_SOURCES` itself.

Invariants a reviewer can check on any produced `report.json` (asserted end
to end in `scripts/test_assemble.py` §5, against a session assembled through
`main()`):

- `sum(summary.by_delta.values()) == summary.total_outdated` — unchanged by
  the addition of skill-drift, and it must stay that way: a non-version
  source that starts landing in `by_delta` has a bug in its short-circuit.
- `sum(summary.by_bucket.values()) == len(tools) == total_outdated +
  health_count + skill_drift_count`.
- `summary.security.auto_count + summary.security.mixed_count <= summary.security.tools_with_security`
  — a `has_security` tool can only be in one of the two security buckets, and
  health tools have `has_security: false`, so this is an equality in practice;
  assert `<=` to stay robust.
- `summary.security.cve_count <= sum(t.security.cve_count for t in tools)` —
  union ≤ sum, strictly less when one advisory hits two tools.
- `len(highlights) <= 8`, and every `highlights[].tool_id` resolves in
  `tools[]`.
- `sum(t.security.severity_counts.values()) == t.security.cve_count` for every
  tool, and `sum(summary.security.severity_counts.values()) ==
  summary.security.cve_count`.
- every `t.security.display_item_ids` entry resolves to an item id in
  `t.items`, and the list is empty whenever `t.source` is a non-version source
  or the tool produced no items. **There is no cap** — the selection is a bar.
- `t.items` equals `items.order_items(t.items)`: assembly never re-sorts, so
  two copies of one corpus are byte-identical.
- no `highlights[].why_ref` appears in its own tool's
  `security.display_item_ids`.

`highlights[]` is a **top-level** key alongside `tools`, not part of
`summary` — see §Highlights above.

`report.json` is written with `schema_version: 2` (see
`references/schemas.md` §Report Object for the full top-level shape) and
`report_id` taken from the session dir's basename. **The version moved to `2`
because this change is not additive**: `tools[].items[]` replaces
`headliners[]`/`relevancy[]`/`context[]` and `security.notable[]`, and
`security.cve_severities[]` is gone. A schema-1 consumer cannot read a schema-2
report and must not try — the page would render a grid of empty cards,
silently, and look entirely correct — so `render.py` hard-fails on any other
value. An old session is re-read by checking out the pipeline that wrote it;
there is no shim, by design (`REDESIGN.md` §I9). `generated_at` is
`collect.json`'s own `generated_at` (see `references/collection.md`) when
present; if `collect.sh` ran before it emitted that field, or the value is
missing/empty for any other reason, assembly falls back to the current UTC
timestamp rather than writing an empty string. Assembly itself does not
touch `research-status.json`, `status.json`, or any server-side state — see
`references/server-and-session.md` for the `phase: "ready"` write that
happens right after `render.py` runs.
