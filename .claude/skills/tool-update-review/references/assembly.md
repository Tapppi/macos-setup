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

merges `collect.sh`'s saved output (`collect.json`, step 1 — see
`references/collection.md`) with every `research/*.json` file written in step
3 (see `references/research.md`) into the single report object
(`references/schemas.md` §Report Object), and writes
`{session_dir}/report.json`. This is purely mechanical/structural work — it
normalizes research's free-form arrays into schema shapes, enforces
suggestion-id uniqueness, validates evidence paths, applies a real
`needs_sudo` heuristic, computes every derived triage field
(`version_delta`/`version_scheme`/`version_delta_note`, `security`,
`risk_level`, `review_bucket`, per-suggestion `pre_accept`), ranks
`highlights[]`, and computes the `summary` counts and rollups — none of it is
a subjective per-tool judgment left to research, so every run applies the
same rule the same way.

**One entry point, one dependency order.** `finalize_tool(tool)` is the last
statement of *both* build paths (`build_tool` and `build_health_tool`), so a
health finding and a version-outdated tool can never disagree about what a
field means. It computes, strictly in this order:

```text
version_delta ─→ security ─→ risk_level ─→ review_bucket ─→ pre_accept
```

Anything else produces stale reads: `review_bucket` needs `risk_level`, which
needs `version_delta`; `pre_accept` needs both `risk_level` and
`review_bucket`. Two ordering constraints relative to `main()` matter just as
much:

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
   were already computed, and the rename pass warns naming both ids.

Every derived field above has a test: `python3 scripts/test_assemble.py` runs
the version matrix (§Version Delta), eleven end-to-end semantic fixtures
(§Security Extraction, §Review Buckets and Pre-Accept), the two regexes, the
shape-drift cases (§Loading and Merging), and the report-level invariants
(§Summary Counts and Output) — stdlib `unittest` only, no network, on the same
bare `python3` `assemble.py` itself targets. Run it after changing any of
them.

`--systems-root` defaults to `~/project/github/tapppi/systems` and rarely
needs overriding — pass it explicitly only if the harness mounts that repo
somewhere else. It exists because `references/research.md` and
`references/research-prompt-template.md` both tell every research subagent to
scan and cite that repo for relevancy, so evidence validation (below) needs
it as a checkable root too, alongside `--macos-setup-root`/`--dotfiles-root`.

This replaces the ad hoc hand-assembly used before this script existed, which
silently skipped two documented rules: it blanket-set `needs_sudo: false` on
every synthesized upgrade suggestion (the schema says default `true` when
unsure) and never checked that cited evidence paths actually exist. Both are
now enforced in code instead of being re-derived — and re-skipped — by hand
each run.

After writing `report.json`, run:

```sh
python3 scripts/render.py /tmp/tool-update-review-{report_id}/report.json
```

which injects it into `assets/report-template.html` and writes `index.html`
(plus a copy of `server.py`, for session-dir self-containedness — the live
server keeps running off the skill's own copy). **Immediately after**, update
`research-status.json` to `phase: "ready"` — see
`references/server-and-session.md` §Pre-Report Status for that write and the
loading page's polling contract; assembly's own job ends at `report.json` and
`index.html`.

`collect.sh` uses `brew outdated --greedy` so `auto_updates: true`/
`version :latest` casks (self-updating desktop apps) aren't silently skipped
by the version-outdated candidate list assembly reads — that rationale is
about what `collect.sh` emits, not assembly itself; see
`references/collection.md` §Version Sources.

## Loading and Merging

`load_research()` reads every file in `{session_dir}/research/`, in sorted
filename order. Every research file is a JSON array regardless of tier
(individual-focus or batched — see `references/research.md` §Tiering); a file
that isn't a JSON array is skipped with a warning rather than aborting the
run. Each array element must carry its own `"id"` field (matching a
`collect.sh` candidate's `{source}:{name}`) — an entry with no `id` is
skipped with a warning. If two research files both write an entry for the
same `id`, the later file (by sorted filename) silently overwrites the
earlier one, with a warning to stderr — this shouldn't happen given how
tiering partitions tools, but assembly doesn't treat it as fatal.

Candidates are assembled from `collect.json` in this order: `brew`, `mise`,
`standalone`, `macos`, then `brew_health.findings` appended last — so
brew-health cards sort/render as their own group after the version-outdated
tools. A tool with no matching research entry (subagent failure/timeout)
still gets a Tool object built, just with `research_error` set and no
headliners/suggestions beyond what the collect candidate itself carries.

`repo_context.json` (from step 1's `repo_context.sh`, see
`references/collection.md` §Repo Freshness) is read and merged into the
report verbatim under `repo_context`. If it's missing at assemble time,
`assemble.py` warns and falls back to an empty/`up_to_date` placeholder for
both `macos_setup` and `dotfiles` rather than failing the whole run.

### Shape Normalization at the Research Boundary

Research output is agent-written free-form JSON, so every array the schema
declares eventually comes back in the wrong shape. `as_item_list()`
normalizes all seven of them — `headliners`, `links`, `relevancy`, `context`,
`release_inventory`, `suggestions`, `vendor_silent_categories` (whose members
are strings, not objects) — at the one boundary where a research object
becomes a Tool object, in **both** `build_tool` and `build_health_tool`.
Nothing downstream re-checks a shape.

The doctrine is the same one `normalize_config_status()` and
`as_evidence_list()` already apply, generalized: `dict.get(key, default)`
only substitutes the default when the key is *absent*, so a present-but-null
`"relevancy": null` reaches `for item in …` as `None`, and a drifted
`"headliners": "no notable changes"` (or `["a change"]`, or `{"text": …}`)
reaches `item.get(…)` as a string. Both used to abort the run — and **one
report is assembled from ~22 research files covering ~77 tools**, so a single
drifted file destroyed the whole report *after* the expensive part of the
session was already spent. The rule now:

- a non-list value → `[]`, with a warning naming the tool, the field and the
  actual type;
- a list with wrong-typed members → the good members kept, each bad one
  dropped with its own warning;
- `null` → `[]`, silently (an omitted array and a null array mean the same
  thing).

**Degrade to one warned-about tool, never abort, never silently swallow.** A
tool whose `headliners` were dropped still gets a card, a baseline
suggestion, and a full triage classification — it just classifies as
"research told us nothing", which is what `risk_level` and `impact` already
have vocabulary for (§Risk Level). This tolerance is a robustness contract,
not a licence: a drifted research file is still a research bug and still
prints a warning, and `references/research.md` §Schema Strictness is where a
subagent is told not to produce one.

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

Every `relevancy[]` and `context[]` item's `evidence`, plus
`config_status.evidence`, gets checked: a string that looks like a
`path`, `path:line`, or `path:line-line` (a range, e.g. `Brewfile:83-87`) —
optionally followed by a human-readable ` (description)` parenthetical
(e.g. `tasks/install.sh:56-104 (install_podman_intel)`) — has both the
parenthetical and the line locator stripped, in that order, down to the real
path, then resolved against the macos-setup, dotfiles, and **systems**
(`--systems-root`, above) repo roots (absolute paths checked directly). A
research subagent sometimes prefixes a citation with its own repo's
directory name (e.g. `systems/flake.nix:1`, mirroring `dotfiles/config/...`
for the dotfiles submodule) — the `dotfiles/` case already happened to
resolve under `--macos-setup-root` purely because the submodule is
physically nested inside that checkout, but `systems/` has no such nesting
under any root, so evidence checking also retries with a leading
`{root's own directory name}/` segment stripped before giving up on that
root. A string that looks like a commit citation (`commit …` or a bare 7–40
char hex prefix) is left alone — nothing to check. Anything else that
doesn't resolve under any of the three roots (with or without that prefix
retry) is **warned about, never dropped** — a bad citation is a
research-quality problem worth surfacing to whoever's watching the run, not
a reason to silently strip content the human reviewer would otherwise have
seen.

Evidence is normalized to a list first: the schema (`references/schemas.md`)
requires evidence to always be an array; a research subagent that returns a
bare string instead would make a naive `for ev in evidence` iterate
individual characters. Assembly coerces a bare string into a one-element
list and warns, rather than corrupting the rest of the validation output with
single-letter "evidence" entries.

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
   "macos")` → `("unknown", "none", "no version delta for this source")`.
   brew-health has no versions at all; a `macos` candidate's
   `current_version` is the running `sw_vers -productVersion` rather than
   that specific update's version (`references/schemas.md` §1.3), so a delta
   computed from it would be fiction.
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

`compute_security(tool)` produces the `security` object on every Tool
(`references/schemas.md` §1.9 for the key-by-key contract). This section is
the *why* behind each boundary — every one of them is a real regression from
a live run, not a hypothetical.

### Field scope, in and out

CVE ids are scanned from exactly:

- `headliners[].text`
- `relevancy[].summary`, `.detail`, `.motivating_change`
- `context[].title`, `.detail`

and the **claim** scan (`cve_claimed_count`) from the same set **minus
`context[]`**.

| Excluded field | Why |
|---|---|
| `links[].embedded_content` | An unbounded changelog excerpt that can cover releases outside the current→latest range — it would inflate `cve_count` with CVEs the user isn't being asked about. |
| `links[].url` | A CVE index page isn't a claim about *this* range. |
| `suggestions[].title`/`.rationale` | Derived text restating headliners: doubles the false-positive surface and changes no result. |
| `config_status.detail` | Backward-looking audit-trail prose ("reviewed at 5.0.0, commit a1b2c3d") — an id there is usually a *prior* run's finding. |
| `context[]`, for the **claim** scan only | stunnel's real context note reads "Both 5.80 CVEs need a running service", which the claim pattern must not read as a claim of 80. |

Validated on the live corpus: narrow-scope (headliners + relevancy)
extraction found 58 distinct ids; adding `context` found 59 — the extra one
(`CVE-2026-53789`, rsync) is a genuine in-range CVE discussed in a context
note. Adding suggestions/links/config_status found nothing further. Hence
context in, everything else out.

The exclusions are safe because **`has_security` never depends on ids**: a
missed id understates `cve_count`, it can't flip a security release into a
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

Per tool, ids go into a `set()` and are sorted by `(year, sequence)` as
integers, so `CVE-2026-9595` precedes `CVE-2026-12143` — the page renders the
list verbatim, and a lexical sort gets that pair wrong.

Report-wide, `summary.security.cve_count` is the size of the **union** across
all tools, not the sum of per-tool counts. One advisory can easily land on two
tools here: `brew:openssh` and `brew:ssh-copy-id` ship from the same source
tarball, and a bundled-OpenSSL CVE can hit several casks. Counting it twice
would inflate the one number the security section leads with. In the recorded
run no id happened to repeat, so the union equals the sum — which is the point:
the union makes that a measured fact rather than an assumption about a corpus
that changes every month.

### `cve_count` vs. a vendor that says "fixes 33 CVEs"

`security.cve_count` is **always** `len(cve_ids)` — an id-backed count, never
a claim, so the page can attach every counted CVE to something. The claim is
captured separately in `cve_claimed_count`, and:

- **max wins, never sum.** Firefox's two releases claim 50 and 47; Chrome's
  launch headliner claims 370 while a second headliner describes the 68
  further fixes the five stable updates *since* that launch added — a subset
  of the same range. Summing double-counts it (Chrome → 438, wrong); the max
  is a defensible floor — "the vendor's own largest stated count for this range".
- it is emitted **regardless of whether ids were found** — rsync in that run
  has 7 ids against a claimed 33, and "7 of 33 listed" is more honest than
  either number alone. It is secondary text on the page, never the headline
  count.

`summary.security.tools_with_unlisted_cves` counts tools where
`cve_claimed_count > cve_count` — 6 of the 77 in that run (rsync 7/33, libpq
4/28, wireshark-app 0/28, chrome 4/370, …) — so the header can read
"59 CVEs · 6 tools report more without ids" instead of silently
understating. Claims are never summed into `summary.security.cve_count`.
Note that this is scan-scope-sensitive in a way that is easy to get wrong
when re-deriving it by eye: a vendor's "fixes N CVEs" sentence that happens
to live in `context[].detail` yields **no** claim, because `context[]` is
outside the claim scan — the tool's `cve_claimed_count` is `null` and it does
not count here, however plainly the sentence reads.

### `security_only` — "substantive content is security/patch only"

Requires `has_security`, requires that research actually produced content,
requires no non-`security` entry in `vendor_silent_categories`, and then
allows only an explicit `(category, severity)` pair across
`headliners + relevancy`: `security` at any severity, `fixes` at
`info`/`notable`, `notes` at `info`. Each disqualification earns its place:

- **`features` at any severity disqualifies.** A new feature is not a
  security patch; the tool belongs in the side-by-side "mixed" list where the
  user decides. This is the single biggest filter — in that run 39 of the 74
  version-outdated tools had security content and only 10 were security-only
  (8 of which went on to clear the remaining `security_auto` guards, §Review
  Buckets and Pre-Accept).
- **`notes` above `info` disqualifies.** Not pedantry: codex's *breaking*
  change ("`codex exec --full-auto` was removed") is filed as
  `category: "notes", severity: "notable"`. Category alone would have waved
  it through.
- **`fixes` at `warning`/`incompatible` disqualifies** — a bugfix urgent
  enough to be a warning is a behavior change worth reading.
- **`security` at any severity is allowed**, including `warning`: a
  high-severity *security* item is a reason to take the update, not to hold
  it. libpq (`security/warning`, "fixes 28 CVEs, twelve rated CVSS 8.8") is
  the canonical case.
- **`context[]` never disqualifies** — present-tense repo-scope notes carry
  no severity by design.
- **A malformed item disqualifies**, because only an explicit allowed pair
  returns true; a missing or unrecognized value falls through to false.
- **No research ⇒ `False`, always.** `research_error`, or a research object
  with empty `headliners`, both fail `research_produced_content()`. "We know
  nothing" must never be reported as "nothing but security fixes."

### `impact` — the user's "confirmed no impact on me"

`relevancy[]` is precisely "this change affects *this* setup" — its
`motivating_change` is required and must name a real changelog item
(`references/schemas.md` §1.2) — so `impact` is grounded there, with three
corrections the real data forced:

- **`"unknown"` comes first and is unconditional.** Research failed or
  produced no headliners → no basis for a verdict. `"unknown"` can never
  reach `security_auto`, so it never renders as auto-approved.
- **A `security`-category relevancy is not by itself impact.** duckdb's reads
  "Parquet hardening lands on the tool this machine's agent instructions
  point at arbitrary data files"; libpq's reads "CVE-2026-18408 turns any
  dump this machine restores into a shell-execution vector". Both are
  *reasons to upgrade*, not risks of upgrading. The naive rule — any
  relevancy item means impact — costs exactly the tools whose relevancy *is*
  the security fix: on the recorded run it takes `security_auto` from 8 down
  to 5, dropping `brew:duckdb`, `brew:libpq` and `cask:wireshark-app`, the
  only three of the eight with a relevancy item at all. When the rule was
  written it was worse than that — the comment in `compute_impact()` records
  a whole live run in which it emptied the section outright — and it gets
  worse again the more diligently research files a relevancy item per
  advisory. Excluding security-category relevancy from the "possible" test is
  what keeps the section populated.
- **A non-security relevancy at `notable`+ *is* impact.** tmux ("the running
  tmux server keeps executing 3.7b until restarted — a session is attached
  right now"), watch, yq and fzf all correctly become `possible`. An `info`
  finding is a touchpoint note, not an effect, so it doesn't flip it on its
  own.

Plus: an `incompatible` relevancy is impact regardless of category;
non-security headliners at `warning`+ count even with no relevancy item at
all (`mise:rust`: three `fixes/warning` headliners, no relevancy →
`possible`); and `pinned`, `needs_attention`, and any `edit`/`watch-item`
suggestion are impact *by construction*, each one being a pending change to
this setup. brew-health short-circuits: `"none"` when `health_expected`, else
`"possible"`.

Measured across that run's 77 tools: `none` 33, `possible` 41, `unknown` 3 —
a little over half `possible`, and `unknown` only on the three casks whose
research produced no headliners at all.

## Risk Level

`risk_level` (`"low"` | `"elevated"`, see `references/schemas.md` §1.4) is
computed entirely from signals already present in the assembled Tool object —
never a subjective per-tool call from research, so every run applies the same
rule the same way. A tool is `"elevated"` if **any** of:
- `pinned` is true,
- any `relevancy[]` item has severity `warning` or `incompatible`,
- any suggestion on the tool has `kind` `"edit"` (default when `kind` is
  omitted),
- `version_delta` is `"major"` or `"unknown"` (§Version Delta above),
- `research_error` is set, or
- the tool has no `headliners[]` **and** an empty `vendor_silent_categories`.

Otherwise `"low"`. A `brew-health` finding short-circuits all of this:
`"low"` when `health_expected`, `"elevated"` otherwise (§Brew-Health
Assembly).

The last three bullets are the doctrine "an unknown delta size is never
treated as low-risk", extended in two directions:

- **The delta condition reads `version_delta` rather than comparing leading
  integers itself.** One shared classifier means the report never carries two
  different answers to how big a bump is — and an `unknown` delta (an opaque
  build-number scheme) elevates for exactly the same reason an unparseable
  pair always did.
- **Unknown *content* is now treated like unknown delta size.** A tool whose
  research subagent failed has empty headliners, empty relevancy and no edit
  suggestions, so without these two conditions it scores `"low"` and gets
  pre-accepted — the skill would silently auto-approve exactly the updates it
  understands least. Documented silence is deliberately *not* the same thing:
  `vendor_silent_categories` non-empty with no `research_error` (cask
  `claudebar` publishes no notes at all, every run, forever) stays `"low"`,
  because demanding attention for it each run is noise the user can't act on.
  A research *failure* is transient and fixable by a rerun, so it elevates.

`risk_level` feeds the pre-accept union in §Review Buckets and Pre-Accept
below, which is where the "which suggestion actually starts accepted"
question is answered. See `references/rendering-report.md` §Suggestion Card
for how that state renders.

## Review Buckets and Pre-Accept

`review_bucket` answers "how much of a human does this need"; `pre_accept`
answers "does this one suggestion start accepted". They are computed back to
back, in that order, because the second reads the first.

### `compute_review_bucket()` — strict precedence, first match wins

```python
def compute_review_bucket(tool):
	sec = tool["security"]
	if tool["source"] == "brew-health":
		return "routine" if tool.get("health_expected") else "attention"
	baseline = baseline_upgrade(tool)
	runnable = bool(baseline and baseline.get("auto_runnable"))
	if (sec["has_security"] and sec["security_only"] and sec["impact"] == "none"
			and tool["version_delta"] not in ("major", "unknown") and runnable):
		return "security_auto"
	if sec["has_security"]:
		return "security_mixed"
	if (tool["risk_level"] == "elevated"
			or config_needs_attention(tool)
			or any(suggestion_kind(s) != "upgrade" for s in tool.get("suggestions", []))
			or not runnable):
		return "attention"
	return "routine"
```

The bucket vocabulary and what each one renders as: `references/schemas.md`
§1.10.

There is deliberately **no** major/unknown-delta or `research_error` test in
the `attention` branch. `risk_level` is computed first (the dependency order
in §Overview) and already returns `"elevated"` for both, so repeating them
would be dead code that reads like a safety net — and a reader would then
have to check whether the two copies agree.

Two consequences worth stating:

- **A `macos` or `standalone` tool can never be `security_auto`**, because
  its baseline carries `auto_runnable: false` and the `runnable` guard is
  unconditional. A security-only macOS update lands in `security_mixed`; one
  with no security content lands in `attention`. There is nothing to
  auto-approve when the skill cannot run the command.
- **A vendor that published nothing is `routine`, not `attention`** —
  provided research *ran* and said so (`vendor_silent_categories` non-empty,
  no `research_error`). Its `impact` stays `"unknown"`, so the page can show
  a small "vendor published nothing" chip without demanding a decision.

Measured over the recorded run: `security_auto` 8, `security_mixed` 31,
`attention` 13, `routine` 25 — sum 77, i.e. every tool including the three
brew-health findings. The `security_auto` set was `azcopy`, `duckdb`, `helm`,
`kubernetes-cli`, `libpq`, `opentofu`, `tor-browser`, `wireshark-app`: a
believable "just take these" list.

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
`edit`/`watch-item` suggestion, any `incompatible` relevancy and any
non-security relevancy at `notable`+; and it requires `version_delta` not in
`("major", "unknown")`, which excludes the delta condition. The only remaining
way for such a tool to be `elevated` is **a `security`-category relevancy at
`warning` severity** — "this security fix matters to you", which is a reason
to take the update, not to hold it. Measured: exactly 2 of the 8
(`brew:libpq`, `cask:wireshark-app`). Overall effect on that run: roughly two
thirds of the baselines start accepted — 48 of the 74 synthesized ones. The
three brew-health findings have no baseline at all, so they are outside that
denominator, not rejections of it.

## `needs_sudo` Heuristic

Applied per-suggestion at synthesis time (below), by source:
- `brew`, `mise`, `standalone` → always `false` — these never invoke a
  privileged installer themselves.
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
source-driven:

| Source | Command | `auto_runnable` |
|---|---|---|
| `brew` | `brew upgrade {name}` | `true` |
| `cask` | `brew upgrade --cask {name}` | `true` |
| `mise` | `mise upgrade {name}` | `true` |
| `standalone` | none | `false` — no generic upgrade command exists; check the tool's own docs |
| `macos` | none | `false` — install via System Settings or `softwareupdate -i`, never auto-run by this skill |
| unknown | none | `false` |

When `auto_runnable` is `false`, a `manual_reason` string is attached
explaining why — the session always just tells the user what to run for
that suggestion, never executes anything (see `references/apply.md`).

## Baseline Suggestion Synthesis

**Every tool gets a synthesized baseline `kind: "upgrade"` suggestion**, id
`{source}:{name}:upgrade`, inserted as the first element of its
`suggestions[]` array. This is mechanical assembly-step work, never left to
the research subagent — it's the only thing that actually tracks an
outdated tool to completion; without it, a plain patch bump with no config
impact would get zero suggestions and silently never get upgraded.
Research-authored `edit` suggestions are always additional to this baseline,
never a replacement for it. The baseline's `rationale` is the fixed string
"Picks up the changes described in headliners[] above."; its
`motivating_link` is the tool's first `links[]` entry if one exists, else
`null`.

**Exception: `brew-health` findings get no baseline upgrade suggestion** —
they have no version to upgrade at all (`current_version`/`latest_version`
are both `null`). See §Brew-Health Assembly below for what they get instead.

**The baseline is the only suggestion that can carry `pre_accept: true`**
(§Review Buckets and Pre-Accept). `baseline_upgrade()` identifies it
structurally — `suggestions[0]`, `kind: "upgrade"`, id ending `:upgrade` — and
that identification is why `finalize_tool()` must run *before* the
suggestion-id uniqueness pass, which can rename a colliding id to
`…:upgrade-2`. The three-part test is deliberate rather than "the first
suggestion": a brew-health tool's `{tool_id}:remediate` also sits at index 0
with `kind: "upgrade"`, and the id suffix is the one thing that tells them
apart.

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
| 100 | `incompatible_finding` | any `relevancy[].severity == "incompatible"` |
| 70 | `watch_item_hit` | `/watch[\s\-]?item hit/i` matches any relevancy item's `summary` + `detail` |
| 60 | `config_stale` | `config_status.state == "needs_attention"` |
| 45 | `warning_finding` | any `relevancy[].severity == "warning"` |
| 40 | `breaking_change` | any `headliners[].severity == "incompatible"` |
| 35 | `proposed_edit` | any suggestion with `kind == "edit"` |
| 30 | `pinned` | `tool.pinned` |
| 30 | `security_mixed` | `review_bucket == "security_mixed"` |
| 25 | `major_bump` | `version_delta == "major"` |
| 25 | `watch_item_proposed` | any suggestion with `kind == "watch-item"` |
| 20 | `changelog_warning` | any `headliners[].severity == "warning"` |
| 20 | `research_failed` | `research_error` is set |
| 15 | `unknown_scheme` | `version_delta == "unknown"` |
| 10 | `cves` | `security.cve_count >= 1` |
| 10 | `manual_action` | a baseline exists and its `auto_runnable` is false |

`review_bucket == "security_auto"` contributes **nothing** — it is by
definition the bucket that needs no decision.

`watch_item_hit` is detected **textually**, against the literal phrase
`Watch item hit:` that `references/research.md` §Watch Items (Reading) makes
mandatory. Nothing in the schema marks a hit structurally, so the phrase *is*
the signal: a subagent that paraphrases it makes the hit invisible to
assembly, and the user's own standing concern silently loses 70 points.

`manual_action` reads the baseline through `baseline_upgrade()`, which is why
a cross-tool id collision that renames a baseline costs a tool this one
10-point signal (§Overview, ordering constraint 2). Bounded and warned about.

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
  `max_severity_rank` is taken over `headliners + relevancy` with
  `{info: 0, notable: 1, warning: 2, incompatible: 3}` (absent → −1). The
  trailing `tool_id` makes ties fully deterministic.

### The highlight object

- `title` — `f"{name} {current_version} → {latest_version}"`, or the
  finding's `name` for a brew-health tool, which has no versions at all.
- `why` — the first match in this fixed order, whitespace-collapsed and
  truncated to 220 chars on a word boundary with `…`:
  1. the highest-severity `relevancy` item's `summary` (ties resolve to array
     order);
  2. `config_status.detail`, when `state == "needs_attention"`;
  3. `"Research produced no changelog for this update."`, when
     `research_error`;
  4. the first `security`-category headliner's `text`, when the bucket is
     `security_auto`/`security_mixed`;
  5. `f"Major version bump {current} → {latest}."`, when
     `version_delta == "major"`;
  6. the first headliner's `text`;
  7. `""` — nothing to say, only reachable for a health finding with no
     headliner, which can't happen since assembly synthesizes one.
- `severity` — max severity across `headliners + relevancy`, in relevancy's
  vocabulary so the page reuses one palette. With no items:
  `needs_attention` → `"warning"`; `research_error` or a `major`/`unknown`
  delta → `"notable"`; else `"info"`.
- `suggestion_ids` — **every** suggestion id on the tool, in array order
  (baseline first when present). The page looks each id up in `tools[]` to
  decide whether to offer a jump or a decision control, and renders the raw
  id rather than dropping a row when the lookup fails — a silent drop would
  hide an assembly bug.

Measured top 8 for the recorded run: `cask:google-chrome` (250),
`cask:windows-app` (200), `cask:claude-code@latest` (195),
`brew-health:missing_dependency:dtc` (175), `brew:mise` (170),
`brew:ffmpeg` (155), `cask:podman-desktop` (145), `brew:rsync` (130). Roughly
half the corpus — 36 of 77 tools — cleared the threshold, so it is the **cap**
that keeps the list short, not the threshold; the threshold's job is only to
keep a bare `major` or a bare CVE count from ever qualifying.

## Brew-Health Assembly

A `brew_health.findings[]` entry (emitted by `collect.sh`, see
`references/collection.md` §Brew-Health Collection for the finding shape and
noise filter) becomes a Tool object with `source: "brew-health"`, an extra
`health_category` (the finding's `category`) and `health_expected` (the
finding's `expected` flag) pair, and no version-delta fields.

**Headliners**: research's `headliners[]` if a brew-health subagent (or the
orchestrator directly — see `references/research.md`) enriched this finding;
otherwise assembly synthesizes exactly one headliner from the finding's own
`detail`, so the problem still shows in a content group even with no
enrichment. The synthesized headliner's `category` (which of
Security/Fixes/Features/Notes it renders under) is derived from
`health_category` by a fixed mapping:

| `health_category` | Content group |
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
| `security.has_security` | `false`, always | The security section is about *patches* the user can take. An untrusted tap is a trust decision, not a shipped fix; counting it in `tools_with_security` would make the section's count disagree with the cards it lists. The tap's security character still shows — the category map above files `untrusted_tap` under Security, so the card renders with a security headliner, and the finding's own `severity` drives highlight scoring. |
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

## `config_status` Normalization

A research subagent can legitimately return `config_status: null` on its Tool
object (e.g. nothing to compute for a macOS-source tool) rather than omitting
the key entirely — and `config_status.detail` can independently come back
`null` too. Assembly normalizes both a null `config_status` and a null
`detail` to the schema default (`{"state": "unknown", "detail": "",
"evidence": []}`) *before* anything downstream calls `.get()` on it, in both
`build_tool` and `build_health_tool` — a naive `research_obj.get(key,
default)` only substitutes the default when the key is absent, not when it's
present-but-`null`, and every read after that (including the
`needs_attention`-must-have-a-suggestion check right below) used to crash the
whole assembly run with an `AttributeError` on exactly this case.

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
- `total_outdated` — tool count minus `health_count`.
- `incompatible_count` / `warning_count` — counts of `relevancy[]` items
  across all tools at that severity.
- `suggestions_count` — total suggestions across all tools (after id
  uniqueness resolution).
- `health_count` — see §Brew-Health Assembly above.
- `by_delta` — `{major, minor, patch, revision, unknown}` over
  **version-outdated tools only** (`brew-health` skipped).
- `by_bucket` — `{security_auto, security_mixed, attention, routine}` over
  **every** tool, brew-health included.
- `security` — `{cve_count, tools_with_security, auto_count, mixed_count,
  tools_with_unlisted_cves}`, where `cve_count` is the size of the *union* of
  `cve_ids` across tools (§Security Extraction), not the sum.

**`by_delta` and `by_bucket` have different denominators, deliberately.**
`by_delta` sums to `total_outdated` (74 in the live run); `by_bucket` sums to
`total_outdated + health_count` (77). The asymmetry is load-bearing:
`version_delta` is meaningless for a health finding, while `review_bucket` is
defined for every Tool object and the page's bucket lists render health cards
alongside version ones. A consumer that mixes the two denominators in one
percentage — `by_bucket.routine / total_outdated` — produces a number that
means nothing, and that is precisely the bug this note exists to prevent.

Invariants a reviewer can check on any produced `report.json` (asserted end
to end in `scripts/test_assemble.py` §5, against a session assembled through
`main()`):

- `sum(summary.by_delta.values()) == summary.total_outdated`.
- `sum(summary.by_bucket.values()) == len(tools) == total_outdated + health_count`.
- `summary.security.auto_count + summary.security.mixed_count <= summary.security.tools_with_security`
  — a `has_security` tool can only be in one of the two security buckets, and
  health tools have `has_security: false`, so this is an equality in practice;
  assert `<=` to stay robust.
- `summary.security.cve_count <= sum(t.security.cve_count for t in tools)` —
  union ≤ sum, strictly less when one advisory hits two tools.
- `len(highlights) <= 8`, and every `highlights[].tool_id` resolves in
  `tools[]`.

`highlights[]` is a **top-level** key alongside `tools`, not part of
`summary` — see §Highlights above.

`report.json` is written with `schema_version: 1` (see
`references/schemas.md` §Report Object for the full top-level shape) and
`report_id` taken from the session dir's basename. **The version stays `1`
because every addition above is purely additive** — nothing was renamed or
removed, `render.py` hard-fails on any other value, and every consumer
(`write_status.py`, `server.py`'s `feedback.json` validation, the page) only
ever reads keys it knows. The converse obligation is on the page: a report
reopened from an older session dir will have none of these keys, so every
consumer must tolerate them being absent. `generated_at` is
`collect.json`'s own `generated_at` (see `references/collection.md`) when
present; if `collect.sh` ran before it emitted that field, or the value is
missing/empty for any other reason, assembly falls back to the current UTC
timestamp rather than writing an empty string. Assembly itself does not
touch `research-status.json`, `status.json`, or any server-side state — see
`references/server-and-session.md` for the `phase: "ready"` write that
happens right after `render.py` runs.
