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
statement of *every* build path (`build_tool`, `build_health_tool` and
`build_drift_tool`), so a health finding, a vendored-skill drift finding and
a version-outdated tool can never disagree about what a field means. It
computes, strictly in this order:

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
the version matrix (§Version Delta), the end-to-end semantic fixtures
(§Security Extraction, §Review Buckets and Pre-Accept — one per source
behaviour worth pinning, including a `skill-drift` pair covering the
decision-required and expected cases), the two regexes, the
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
`standalone`, `macos`, then `brew_health.findings` and
`skill_drift.findings` appended last — so the two non-version sources
sort/render as their own groups after the version-outdated tools. Both are
read defensively (`obj.get("findings", [])` only when `obj` is a dict, `[]`
otherwise), because a `collect.json` from an older run has neither key and a
partial run can have one of them as `null`; a missing source costs its
cards, never the report. A tool with no matching research entry (subagent
failure/timeout)
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

`compute_security(tool)` produces the `security` object on every Tool
(`references/schemas.md` §1.9 for the key-by-key contract). This section is
the *why* behind each boundary — every one of them is a real regression from
a live run, not a hypothetical.

### Field scope, in and out

CVE ids are scanned from exactly:

- `headliners[].text`
- `relevancy[].summary`, `.detail`, `.motivating_change`
- `context[].title`, `.detail`
- `security.notable[].cve_id`, `.summary`

and the **claim** scan (`cve_claimed_count`) from the same set **minus
`context[]` and minus `security.notable[]`**.

| Newly scanned | Why it is in scope |
|---|---|
| `security.notable[].cve_id` | A literal id field, taken as-is rather than regexed. Research selected the entry from this current→latest range by construction — the exact property `links[].embedded_content` lacks, which is why that one stays out. It is also what makes the mismatch warning in §Validating Research's `notable` effectively unreachable: any well-formed id there is in `cve_ids` because this scan put it there. |
| `security.notable[].summary` | One short line research wrote about this range; same in-range guarantee, regex-scanned like the other prose fields. This is the fix for the 24 security-bearing tools in the live run whose ids appear nowhere a narrower scan would look. |

The **claim** scan is deliberately *not* extended: a notable summary reading
"one of 28 advisories" must not be read as a vendor claim of 28 — the same
trap the `context[]` exclusion exists for.

| Excluded field | Why |
|---|---|
| `links[].embedded_content` | An unbounded changelog excerpt that can cover releases outside the current→latest range — it would inflate `cve_count` with CVEs the user isn't being asked about. |
| `links[].url` | A CVE index page isn't a claim about *this* range. |
| `suggestions[].title`/`.rationale` | Derived text restating headliners: doubles the false-positive surface and changes no result. |
| `config_status.detail` | Backward-looking audit-trail prose ("reviewed at 5.0.0, commit a1b2c3d") — an id there is usually a *prior* run's finding. |
| `context[]`, for the **claim** scan only | stunnel's real context note reads "Both 5.80 CVEs need a running service", which the claim pattern must not read as a claim of 80. |
| `security.cve_severities[]` | A rating table, not prose about this range. An id that appears only there is a rating for something this range does not contain, so it is dropped with a warning rather than counted. |

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

### Severity Rollup and the Sum Invariant

`security.severity_counts` is assembly's rollup, but the per-id ratings are
**research's** input (`security.cve_severities`, see
`references/research.md` §CVE Severity Capture). Assembly has no network and
no advisory database; it can only count what it was given, and the one thing
it must never do is invent a class to fill a meter.

```python
def rollup_severity_counts(cve_ids, severity_by_id):
	counts = dict.fromkeys(_CVE_SEVERITIES, 0)
	for cve_id in cve_ids:                 # iterate the IDS, not the map
		counts[severity_by_id.get(cve_id, "unknown")] += 1
	return counts
```

**`sum(severity_counts.values()) == cve_count` holds by construction, not by
assertion** — the loop iterates `cve_ids`, and every id lands in exactly one
bucket. That is the whole reason to build it this way: an id research forgot
to rate becomes `unknown` instead of a broken sum, and an id research rated but
assembly never extracted is dropped with a warning instead of inflating the
total. `test_assemble.py` §6 asserts the sum anyway, because a future edit that
iterates the map instead would break it silently.

Three rules on the input side, all warn-and-continue:

- **An id not in `cve_ids` is dropped.** A rating for something this range does
  not contain is not a rating for this card.
- **A grade with no `basis`, or a word outside the vocabulary, becomes
  `unknown`.** A rating with no recorded source is not a rating — the same
  doctrine as `cve_count` never carrying a claim.
- **Two ratings for one id keep the worse.** Real once ratings come from a
  vendor page and NVD; understating a severity is the failure mode with a cost.

`cve_severities` is **emitted as the resolved, graded subset** — one entry per
id assembly actually counted, sorted like `cve_ids`. That keeps `report.json`
self-describing (a reviewer can rebuild `severity_counts` from it with `jq`)
and lets `summarize_security()` rebuild the report-wide map without a private
key. An id absent from it is `unknown`, which is why the emitted list is
usually far shorter than `cve_ids`.

Report-wide, `summary.security.severity_counts` counts over the **union** of
ids, for exactly the reason `cve_count` does — never sum the per-tool counts,
since one advisory can land on two tools. It therefore sums to
`summary.security.cve_count`. Two tools rating one id differently is the same
event as two sources rating it differently inside one tool, so it resolves the
same way — **the worse wins, and it warns**, naming both ratings. It used to
resolve in silence, which meant a header reading "1 critical" could come from
one tool's page contradicting another's with nothing said about it.

**`unknown` will dominate, and that is the designed steady state.** On the
live run's text only 22 of 99 ids carry a rating anyone published in a page
research had already read, and 24 of the 41 security-bearing tools have no
extracted ids at all. A page must render the honest compact form by default.

### Validating Research's `notable`

`security.notable[]` is research-supplied and assembly-validated. Every rule
is warn-and-continue; none of them aborts a run, because one report is
assembled from ~22 model-written files.

| Research supplied | Assembly does |
|---|---|
| a non-list, or a non-dict member | `as_item_list()` drops it with its existing warning |
| a `summary` that is not a string (`{"text": …}`, `42`) | drop the entry, warn. Stringifying it renders the repr, and the page's `typeof === 'string'` guard passes it by then — this side is the only place the drift is visible |
| an entry with no `summary` | drop it, warn — a notable with no line is nothing to render |
| `severity` absent or outside the vocabulary | coerce to `"unknown"`, warn |
| `severity` disagreeing with this id's `cve_severities` entry | **the map wins**, warn — one source of truth, and `severity_counts` must agree with what the card shows |
| `severity` other than `unknown` on a `cve_id` **absent from** `cve_severities` | keep the grade, warn. The same disagreement as the row above, reached by omission: the card would read `critical` while `severity_counts` buckets that id as `unknown`, since the rollup is built from `cve_severities` alone. The grade is kept because it is the only one research found, and *not* promoted into the map because a `notable[]` rating carries no `basis` — inventing one is the unsourced rating §CVE Severity Capture forbids. The warning names the fix: put the grade in `cve_severities`, with a basis |
| a `cve_id` that is not a resolvable id in `cve_ids` | null the id, keep the item, warn. Normally unreachable: `notable[].cve_id` is inside the id scan, so a well-formed id is in `cve_ids` by construction — this catches a malformed one and stops the page chipping an id the report cannot resolve |
| a non-bool `affects_me` | coerce, warn |
| `affects_me: true` with no `security`-category relevancy item on the tool | keep it, warn. The two are the same claim — but assembly **never** sets or clears the flag, because a third of one live run's security relevancy items are negative-direction findings whose whole point is that the fix does *not* reach this machine (openssh's sshd, microsoft-teams, fd, mise:python's expat, rsync). Auto-deriving would invert every one of them |
| more than 3 entries | sort by the `notable` key (`affects_me` first, then worst severity, then `(year, sequence)`, then id-less last) and keep the first 3, warn with the count dropped. **Ordering strictly precedes the cap**, so research writing its strongest item last costs nothing |
| any `notable` on a `brew-health` tool | drop silently — `has_security` is forced `false` there, and a notable would make the security section's count disagree with its cards |
| a `notable` with `research_error` or no headliners | force `[]`, warn. Same doctrine as "No research ⇒ never `security_only`" |

**The `notable` key orders and evicts with one comparison, which is why
`affects_me` comes first.** Whatever the key ranks last is what a four-entry
list loses. `affects_me: true` means a concrete touchpoint on *this* setup, so
an item carrying one is never evicted by a higher-rated item that misses this
machine — and R5 already cleared every entry on its own merits before the key
sees it, so promoting one cannot smuggle in a weak item. With severity first,
three `low` CVEs nobody here can reach evicted `brew:iproute2mac`'s reproduced
command injection, which is precisely the item R5's third clause was written
to surface.

The severity tier uses `_NOTABLE_SEVERITY_RANK`, **not** `_CVE_SEVERITY_RANK`:
one vocabulary, two ranks, disagreeing on `unknown` on purpose. In the rollup
`unknown` means "no rating recorded" and must never beat a recorded one, so it
ranks lowest; on a `notable[]` entry it means "research selected this and
nobody published a grade", which is not evidence of a small flaw, so it ranks
above `low`. The `severity` vocabulary itself is unchanged
(`references/schemas.md` §1.9) — this is a ranking fix, not a schema change.

Assembly also computes `source_ref` on each entry — the `"rel:{i}"`/`"hl:{i}"`
identity of the content item it restates, resolved by CVE or advisory id
first and only then by comparing *untruncated* summaries. That is the handle
§Highlights matches on. Among relevancy items the id matches, it takes the
**max-severity** one, because `_highlight_why_parts()` picks a highlight's
`why` the same way and §Highlights compares the two refs — matching the first
naming item instead misses the dedupe whenever two relevancy items name one
CVE. An `advisory_id` is free-form vendor text, so a short one can substring-
match prose that never mentioned it; that is a known, unguarded cost of one
missed dedupe, not something a length-and-digit predicate fixes (`2026-11`
against "Release 2026-11 ships the new resolver" defeats it, and word
boundaries do not save it either).

### Emitting `notable` at all: `[]` versus an absent key

`compute_security()` emits the `notable` key only when assembly has an answer
to give, because the page reads its **presence** as information
(`references/schemas.md` §1.9, `references/rendering-report.md` §Group (b)):
`[]` means "the selection ran and nothing qualified" and draws the
single-column card, while an absent key means "the question was never put"
and falls back to deriving the column from the tool's own security content.

The key is emitted when research supplied a readable `security` block, when
research supplied nothing at all (`research_error` / no headliners, where the
table above forces `[]`), or on a `brew-health` tool, where `[]` is likewise
assembly's own decision. It is omitted for a research file that carried real
content and no `security` block — a file that predates the field, on whose
behalf assembly must not claim "nothing here is notable" — and for a block
too drifted to read at its root (a bare string, a list, `null`), which
`normalize_research_security()` warns about for exactly this reason.

This is not a hypothetical distinction. The recorded run's 22 research files
carry no `security` block at all, so emitting `[]` unconditionally collapsed
77 of its 78 cards to a single column and deleted the security column from
every one of them. `test_assemble.py` §7 pins both halves.

Note what is **not** here: nothing removes a security item for being noisy.
`compute_security()` derives `has_security` from the security category and the
ids, and that feeds `review_bucket` and then `pre_accept` — so a presentation
rule that removed the last security item would silently move a tool to
`routine` and auto-accept it. The noise floor is bounded to keep that
impossible; the boundary lives in `noise_suppressible()` and is asserted in
`test_assemble.py` §6 (`references/research.md` §The Noise Floor).

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
this setup. Both non-version sources short-circuit: brew-health is `"none"`
when `health_expected`, else `"possible"`; skill-drift is `"none"` when
`drift_expected`, else `"possible"`. Each is *about this machine* by
definition, so the only question left is whether it is the expected kind.
The two flags stay separate on the Tool object because they mean different
things to a reader, but every derived axis asks the same question of them,
so impact, `risk_level` and `review_bucket` all ask it through one
`finding_expected()` helper instead of each carrying its own copy.

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

Otherwise `"low"`. Both non-version sources short-circuit all of this on
their own "nothing to decide" flag: a `brew-health` finding is `"low"` when
`health_expected` and `"elevated"` otherwise (§Brew-Health Assembly), and a
`skill-drift` finding is `"low"` when `drift_expected` and `"elevated"`
otherwise (§Skill-Drift Assembly).

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
	if tool["source"] in NON_VERSION_SOURCES:
		return "routine" if finding_expected(tool) else "attention"
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
source-driven:

| Source | Command | `auto_runnable` |
|---|---|---|
| `brew` | `brew upgrade {name}` | `true` |
| `cask` | `brew upgrade --cask {name}` | `true` |
| `mise` | `mise upgrade {name}` | `true` |
| `standalone` | none | `false` — no generic upgrade command exists; check the tool's own docs |
| `macos` | none | `false` — install via System Settings or `softwareupdate -i`, never auto-run by this skill |
| `skill-drift` | none | `false` — vendored-skill sync is always manual (§Skill-Drift Assembly) |
| unknown | none | `false` |

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
"Picks up the changes described in headliners[] above."; its
`motivating_link` is the tool's first `links[]` entry if one exists, else
`null`.

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

**CVE severity is now available and `score_tool()` deliberately does not use
it.** Weighting `critical` ids would push highlights *toward* the security
content this design exists to stop them restating, and §Threshold is already
explicit that a bare CVE count must not qualify a tool on its own. Measured
before deciding: on the recorded run the flat 10-point `cves` signal fires on
3 of the 8 highlights and changes the membership of none of them (lowest
highlight 180 against a threshold of 40), so it is inert — and inert is the
right amount of security in this ranking. Do not re-litigate it without a
measurement that says otherwise.

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
  finding's `name` for a brew-health or skill-drift tool, which has no
  versions at all. `_highlight_title()` needs an arm per non-version source;
  without one the card renders the literal `name None → None`.
- `why` — the first match in this fixed order, whitespace-collapsed and
  truncated to 220 chars on a word boundary with `…`:
  1. the highest-severity `relevancy` item's `summary` (ties resolve to array
     order);
  2. `config_status.detail`, when `state == "needs_attention"`;
  3. `"Research produced no changelog for this update."`, when
     `research_error`;
  4. the first **non-security** headliner's `text`, when the bucket is
     `security_auto`/`security_mixed`;
  4b. the first `security`-category headliner's `text`, only when the tool has
     no non-security headliner to say instead;
  5. `f"Major version bump {current} → {latest}."`, when
     `version_delta == "major"`;
  6. the first headliner's `text`;
  7. `""` — nothing to say, only reachable for a health finding with no
     headliner, which can't happen since assembly synthesizes one.

  Step 4 used to *be* 4b, and that guaranteed the duplication the whole
  security redesign is about: it returned the same line the mixed card's
  security column renders in full. Step 1 stays first even when the winning
  relevancy is security-category — for `cask:windows-app` ("The installed
  11.3.7 predates both security releases in this range, including the
  CVE-2026-61352 RDP client RCE") that line *is* the decision, and demoting it
  would leave a worse one. The duplication that step 1 can still produce is
  handled by the drop-and-backfill below, not by picking a weaker line.
- `why_source` — which of the branches above produced the line
  (`relevancy_security` | `relevancy_other` | `config_status` |
  `research_error` | `headliner_security` | `headliner_other` | `major_bump` |
  `none`). Provenance for a reviewer and for the dedupe; a page may ignore it.
- `why_ref` — the `"rel:{i}"`/`"hl:{i}"` identity of the content item `why`
  came from, or `null` for the branches that synthesize their own text.
- `severity` — max severity across `headliners + relevancy`, in relevancy's
  vocabulary so the page reuses one palette. With no items:
  `needs_attention` → `"warning"`; `research_error` or a `major`/`unknown`
  delta → `"notable"`; else `"info"`.
- `suggestion_ids` — **every** suggestion id on the tool, in array order
  (baseline first when present). The page looks each id up in `tools[]` to
  decide whether to offer a jump or a decision control, and renders the raw
  id rather than dropping a row when the lookup fails — a silent drop would
  hide an assembly bug.

**De-duplication against the security cards, with backfill.** After ranking,
`build_highlights()` walks the sorted candidates and skips any whose `why_ref`
matches a `source_ref` in that tool's `security.notable[]`, continuing down the
list until the cap is filled. **The highlight yields, never the security
card** — on the tools where this fires the duplicated line is usually the most
important sentence on the card, and the section is supposed to carry eight
*distinct* decision drivers rather than eight rows of which two repeat
something 200px below. Each drop prints a `note:` naming the tool and the ref.

The match is on the emitted identities on both sides, never on text. `why` has
already been through `_truncate_why()`'s 220-char cut, and the live run
carries a 222-char relevancy summary (`brew:mise`) — a string comparison would
have failed there silently, which is the failure mode this design exists to
avoid.

Measured on the recorded run with the security block projected onto it: four
candidates were dropped (`cask:windows-app`, `brew:gh`, `cask:teamviewer`,
`brew:libpq`), two of them inside the visible top 8, and the section still
returned 8 distinct tools — `cask:claude-code@latest` and `cask:libreoffice`
backfilled the freed slots.

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

**Headliners**: research's `headliners[]` if the skill-drift group enriched
this finding (`references/research.md` §Skill-Drift Enrichment); otherwise
assembly synthesizes exactly one from the finding's own `detail`, carrying
the finding's `severity`, so the drift still shows in a content group with
no enrichment at all. Its `category` comes from a fixed
`drift_state` → content-group map, the same shape brew-health's
`health_category` map has:

| `drift_state` | Content group |
|---|---|
| `upstream_ahead`, `diverged` | Fixes |
| `local_only`, `probe_error` | Notes |
| `in_sync` | Notes — defensive only; an in-sync skill is never a finding |

(Nothing maps to Security. A skill lagging its upstream is a maintenance
fact, not a shipped patch — see the `has_security` row below. An
unrecognized state falls back to Notes rather than dropping the headliner,
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
| `security.severity_counts` / `cve_severities` | every band `0` / `[]` | Both roll up over `cve_ids`, which is empty here. The keys still ship, so the page's severity code needs no per-source special case (§Severity Rollup and the Sum Invariant). |
| `security.notable` | `[]`, always | Research's own `security.notable` for a drift finding is dropped silently, for the same reason `has_security` is forced: a notable entry *is* security content, and one surviving on a card whose security strip never renders would be a lie. |
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
- `total_outdated` — the tools whose `source` is **not** in
  `NON_VERSION_SOURCES`, counted the same way `by_delta` counts them
  rather than by subtracting each finding count off `len(tools)`. Adding a
  fourth finding source to the set is then the whole change; under the
  subtraction form, forgetting the new term was how `by_delta` silently
  stopped summing to it.
- `incompatible_count` / `warning_count` — counts of `relevancy[]` items
  across all tools at that severity.
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
- `len(t.security.notable) <= 3`; every non-null `notable[].cve_id` and every
  `cve_severities[].cve_id` resolves in `t.security.cve_ids`.
- `t.security.notable == []` whenever `t.source == "brew-health"` or
  `t.research_error` is set.
- no `highlights[].why_ref` appears in its own tool's
  `security.notable[].source_ref` set.

`highlights[]` is a **top-level** key alongside `tools`, not part of
`summary` — see §Highlights above.

`report.json` is written with `schema_version: 1` (see
`references/schemas.md` §Report Object for the full top-level shape) and
`report_id` taken from the session dir's basename. **The version stays `1`
because every addition above is purely additive** — including the fields added
for the severity rollup (`security.severity_counts`, `security.cve_severities`,
`security.notable`, `summary.security.severity_counts`, and
`highlights[].why_source`/`.why_ref`) — nothing was renamed or
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
