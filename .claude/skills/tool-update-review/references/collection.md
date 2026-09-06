# Collection (Step 1)

Reference for `scripts/collect.sh` and `scripts/repo_context.sh` — the two
deterministic, non-agentic data-gathering scripts that run before any
research subagent is spawned, plus `scripts/collect_skill_drift.py`, the
detector `collect.sh` shells out to for §Skill-Drift Collection. Read this
when running step 1, or when maintaining any of them.

## Table of Contents

- [Version Sources](#version-sources)
- [Brew-Health Collection](#brew-health-collection)
- [Skill-Drift Collection](#skill-drift-collection)
- [Scoping](#scoping)
- [Repo Freshness](#repo-freshness)

## Version Sources

Run `scripts/collect.sh` from the macos-setup repo root (pass the Brewfile
path if elsewhere) and save its stdout to `{session_dir}/collect.json` —
`assemble.py` (step 4) reads it from there rather than from conversation
memory. It emits a `generated_at` (ISO-8601 UTC, via `date -u
+%Y-%m-%dT%H:%M:%SZ`) plus machine context plus outdated tools from four
version sources:

- **Brewfile-manifested brew/cask packages** — transitive deps excluded,
  pinned formulae included. A pin usually marks a *known* incompatibility
  worth re-checking, not a tool to skip; never treat a pin as a reason to
  drop the tool from the candidate list. A package from a third-party tap is
  manifested however either side spells it: `brew outdated --json=v2` names a
  formula in full (`slp/krun/krunkit`) and a cask by its short token, while
  the Brewfile may declare either form, so both sides are reduced to the
  short name before the intersection — and that short name is what the `id`
  carries. Comparing the two spellings directly is how a whole tap once went
  missing from a run that otherwise looked complete.
- **mise runtimes**.
- **Standalone CLIs** genuinely unmanaged by brew.
- **`softwareupdate -l` entries** — macOS system/app updates, `source:
  "macos"`.

`collect.sh` uses `brew outdated --greedy` specifically so `auto_updates:
true`/`version :latest` casks (self-updating desktop apps — e.g. the
`claude` app cask, distinct from the `claude-code@latest` CLI cask) aren't
silently skipped by the default `brew outdated`, which excludes
auto-updating casks. No standalone/cask dedup is currently needed —
`claude-code@latest` and `codex` are both plain, correctly-version-tracked
casks today (verify this hasn't drifted again before assuming otherwise —
see `collect.sh`'s `standalone_json` comment).

Every emitted tool's shape (identity/versions/`source` vocabulary) is the
Report Object's Tool shape — see `references/schemas.md` §Report Object.

### Degradation: a section, never the collector

`collect.sh` runs under `set -euo pipefail`, which turns any one command's
shrug into the death of the whole collector — and because every `brew` call
sends its stderr to `/dev/null`, that death is frequently silent: exit 1,
empty stdout, empty stderr, no collect.json and so no report. Three ordinary
things used to do exactly that, and each is now contained to the section it
belongs to, with a warning on stderr naming what was lost:

- a **Brewfile with no `brew "` lines** (or no `cask "` lines) — `grep` exits
  1 when it matches nothing, which under `pipefail` is the pipeline's status;
- a **`brew outdated` that prints a complete listing and then exits non-zero**
  — `… | jq … || echo '[]'` fired *in addition to* jq's valid output and left
  the variable holding two concatenated JSON documents;
- a **`brew info` with a notice on stdout ahead of the JSON** — the per-entry
  `jq` became the pinned loop's exit status, and one pinned formula took the
  run with it.

The pattern for all three is the one the `mise_json` block documents inline:
absorb the status with `|| true` at the assignment so the script survives to
check, then validate the captured text with `jq -e .` and fall back — never
`|| echo '[]'` inside the pipeline, which fires alongside good output rather
than instead of it. `test_collect.py` §`CollectDegradationTests` holds one
case per failure.

## Brew-Health Collection

Version deltas are only half of "is my toolchain healthy." The other half
is the *state* of the Homebrew install itself: casks the vendor has
deprecated or disabled, kegs orphaned from their formula, unlinked kegs,
untrusted taps whose formulae/casks brew is silently ignoring, and missing
dependencies. `brew doctor` surfaces all of these; the review folds them in
as a first-class source (`source: "brew-health"`) so they get the same
accept/reject/discuss → apply → status → changelog flow as an upgrade,
instead of a wall of terminal text the user has to triage by hand. These
are **not** version deltas — they render with no `current_version`/
`latest_version`.

After the version-outdated sources, `collect.sh` runs `brew doctor`, parses
each `Warning:` block into a structured finding, and emits a `brew_health`
object:

```jsonc
"brew_health": {
  "findings": [
    {
      "id": "brew-health:untrusted_tap:libkrun-krun",   // {source}:{category}:{slug}
      "name": "Untrusted tap: libkrun/krun",
      "source": "brew-health",
      "category": "untrusted_tap",   // deprecated_cask | disabled_cask |
                                      // deprecated_formula | disabled_formula |
                                      // missing_keg | unlinked_keg | untrusted_tap |
                                      // missing_dependency | path_note | other
                                      // (brew doctor emits the "deprecated or
                                      //  disabled" warning separately for
                                      //  formulae and casks, and a currently
                                      //  *disabled* item — no longer installable —
                                      //  is distinguished from a merely deprecated
                                      //  one via `brew info`'s "Disabled because …")
      "severity": "warning",         // info | notable | warning | incompatible (first guess;
                                      //   research may refine)
      "detail": "…human explanation of the finding + how to resolve it…",
      "affected": ["libkrun/krun"],  // the cask/keg/tap/dep name(s) the block named
      "remediation": {               // default fix, or null when it needs research/no action
        "command": "brew trust libkrun/krun",
        "auto_runnable": false,      // trust/untap/link default to MANUAL — structural changes
        "needs_sudo": false,
        "label": "Trust libkrun/krun (or `brew untap libkrun/krun` to remove)"
      },
      "expected": false,             // true = intentional/known-benign (rendered quietly, no action)
      "pinned": false, "current_version": null, "latest_version": null
    }
  ],
  "suppressed": [ "…one line per finding filtered as expected noise…" ]
}
```

**Noise filter (the point of "holistic, not noisy").** Two classes of
`brew doctor` output are expected byproducts of *this* setup, not problems,
so this stays holistic without becoming noisy:

- **Unlinked kegs that are mise-managed language runtimes** (ruby, python,
  node, go, rust, …). mise owns the runtime; brew installs one only as a
  transitive dependency, so it stays unlinked by design. These move to
  `suppressed` (logged, never rendered as actionable), NOT to `findings`.
- **Non-prefixed GNU-utils PATH notes** (coreutils/findutils). The Brewfile
  deliberately puts gnubin first; brew doctor flags it generically. These
  collapse into a single `expected: true`, `info`-severity `path_note`
  finding rather than N actionable warnings — visible (so the user sees
  it's accounted for) but never demanding a decision.

Any *other* warning block, known category or not (`category: "other"`), is
kept — better to surface an unknown diagnostic than silently drop it.

What happens to a `brew_health` finding next (enrichment, Tool-object
assembly, rendering, remediation execution) is out of scope for collection
— see `references/research.md` §Brew-Health Enrichment,
`references/assembly.md` §Brew-Health Assembly, `references/apply.md`
§Brew-Health Remediation.

## Skill-Drift Collection

The same "is my toolchain healthy" question, asked of the agent skills this
setup vendors. `dotfiles/config/agent-skills/` carries copies of upstream
skill repos (anthropics, google, softaworks), pulled in as git subtrees or
as sparse single-skill copies. None of the version sources above can see
them, so an upstream that has moved hundreds of commits ahead of our copy
stays invisible until somebody thinks to look — measured on this machine:
12 of the 15 adopted skills had drifted, the subtrees having been added
once and never pulled since. `collect.sh` runs
`scripts/collect_skill_drift.py` and folds its output in as a second
non-version source (`source: "skill-drift"`), so a drifted skill gets the
same accept/reject/discuss → apply → status → changelog flow as an upgrade.
Like brew-health these are **not** version deltas — they render with no
`current_version`/`latest_version`.

### Detection: a three-way tree-hash comparison

Per adopted skill the detector computes three git **tree** SHAs and
compares them:

| Side | What it is |
|---|---|
| LOCAL | `git -C dotfiles rev-parse HEAD:<local_path>` — what we ship today |
| BASELINE | the pristine upstream content at the recorded sync commit |
| UPSTREAM | the same subpath at the upstream branch's HEAD, now |

Git tree SHAs are content-addressed, so two trees are equal **iff** their
content is identical — the same hash in any repository, with no heuristics,
no line-diffing and no blob download. That exactness is what makes the
states below safe to act on:

| LOCAL vs BASELINE | BASELINE vs UPSTREAM | `drift_state` | severity | `expected` | Suggestion |
|---|---|---|---|---|---|
| `==` | `==` | `in_sync` | — | — | none — not a finding, one line in `suppressed` |
| `==` | `!=` | `upstream_ahead` | `notable` | `false` | sync this vendor |
| `!=` | `==` | `local_only` | `info` | `true` | none |
| `!=` | `!=` | `diverged` | `warning` | `false` | sync, plus conflict review |
| any SHA unresolvable | | `probe_error` | `info` | `true` | none — one per vendor |
| local+baseline resolve, upstream does not | | `probe_error` | `notable` | `false` | none — upstream removed/renamed it |

**`local_only` must never read as "upstream moved".** It means *we* patched
the copy and upstream has not moved since — an intentional customisation,
documented in the vendor's `CUSTOMISATION.md`, not a pending update.
Reporting it as drift-to-take would push the user toward a sync that
discards their own edit, and telling those two apart is the entire reason
the comparison is three-way rather than "local vs upstream". So
`local_only` is `expected: true` with no remediation: visible, quiet,
nothing to decide. `diverged` is the one that genuinely needs a human —
both sides moved, so the sync lands on top of a local patch.

`collect.sh` emits a `skill_drift` object alongside `brew_health`:

```jsonc
"skill_drift": {
  "findings": [
    {
      "id": "skill-drift:anthropics/pptx",   // {source}:{vendor}/{skill}
      "name": "pptx (anthropics)",
      "source": "skill-drift",
      "drift_state": "upstream_ahead",  // in_sync | upstream_ahead |
                                        //   local_only | diverged | probe_error
      "severity": "notable",            // info | notable | warning | incompatible
      "detail": "…human explanation of the drift + how to resolve it…",
      "vendor": "anthropics",
      "skill": "pptx",
      "vendor_kind": "subtree",         // "subtree" | "sparse"
      "upstream_url": "https://github.com/anthropics/skills",
      "upstream_branch": "main",
      "upstream_subpath": "skills/pptx",
      "local_path": "config/agent-skills/anthropics/skills/pptx",
      "baseline_sha": "5128e1865d670f5d6c9cef000e6dfc4e951fb5b9",
      "upstream_sha": "3b3fad96af16a10759d930941b4520ba0c40edae",
      "expected": false,                // true = no decision required (rendered quietly)
      "remediation": {                  // null for local_only / probe_error / in_sync
        "command": "bash config/agent-skills/sync-upstream.sh",
        "auto_runnable": false,         // ALWAYS false — see references/apply.md
        "needs_sudo": false,
        "label": "Sync anthropics from upstream (updates all 3 drifted anthropics skills)"
      },
      "pinned": false, "current_version": null, "latest_version": null
    }
  ],
  "suppressed": [ "…one line per in_sync skill, own skill, or unprobed vendor…" ]
}
```

**Provenance is already recorded — this needs no new bookkeeping.** Both
vendoring styles leave their sync point in the repo:

- **Subtree vendors** (`vendors=()` in `sync-upstream.sh`): `git subtree
  pull --squash` writes a squash commit carrying a `git-subtree-split:`
  trailer naming the upstream SHA — and that squash commit's own **tree**
  is a pristine copy of upstream at that SHA (verified byte-identical). So
  BASELINE comes out of local history and costs no fetch — a subtree vendor
  needs exactly one network call, for UPSTREAM.

  That does **not** make the states decidable offline. Without UPSTREAM you
  can see that a local patch exists (LOCAL != BASELINE) but not whether
  upstream also moved, so `local_only` and `diverged` stay
  indistinguishable — and `diverged` is the one that needs conflict review.
  Guessing `local_only` there would be the same "silence reads as fine"
  error this source exists to avoid, so offline the detector emits
  `probe_error` and classifies nothing.
- **Sparse vendors** (`sparse_vendors=()`): one skill copied out of a large
  repo, with `Last synced commit: <sha>` recorded in that vendor's
  `CUSTOMISATION.md`. There is no local pristine copy, so BASELINE has to
  be fetched like UPSTREAM.

Vendor → (URL, branch, kind, adopted skills) is parsed out of
`sync-upstream.sh`'s two pipe-delimited tables rather than copied into a
manifest of its own, so the detector and the sync tool cannot disagree
about what is vendored. The parse is defensive: an absent or unparseable
table yields no vendors and one `suppressed` line, never an exception.

**Scope falls out by construction, never from a skip-list.**

- **Our own skills have no upstream.** `config/agent-skills/tapppi/`
  appears in neither vendor table, so `browser` and `subrepo-permissions`
  are out of scope because there is nothing to compare them against — not
  because something remembered to exclude them. They are listed in
  `suppressed` saying exactly that.
- **A tool-owned skill is excluded.** The adopted set is read from
  `.claude-plugin/marketplace.json`, and only entries whose `source` is a
  **string** path (`"./anthropics/skills/pptx"`) are vendored here. An
  **object** source (`{"source": "git-subdir", …}` — `find-skills`) means
  Claude Code resolves and refreshes it itself, so it is tool-owned:
  claiming drift on a path we do not vendor invites exactly the "fix it by
  copying it into `dotfiles/`" mistake the macos-setup `CLAUDE.md`
  §Tool-Owned Config Is Re-Asserted, Not Vendored forbids.
  `~/.claude/skills/context7-mcp/`, written by `ctx7 setup`, is out of
  scope for the same reason.

**Do not reach for the GitHub compare API here.** Its
`/compare/{base}...{head}` endpoint caps the `.files` array at 300 entries,
and a consumer filtering that array by an adopted skill's subpath cannot
tell a truncated response from a clean one. Measured on this repo: the google
vendor was hundreds of commits behind, its compare response hit the cap,
and per-subpath filtering reported **zero drift for all nine adopted google
skills** — a confident, silent, completely wrong answer. Tree-hash
comparison has no such ceiling, needs no API token, and is exact.

**A probe failure is one quiet card per vendor, never fifteen unknowns.**
Fetching upstream costs one `git fetch --filter=blob:none --depth 1` per
ref (~0.7 s measured, ≤2 refs per vendor), and every part of that can fail:
no network, a moved branch, a private repo, a timeout. Each failure is
caught per vendor and emitted as a single `probe_error` finding
(`id: "skill-drift:<vendor>:probe-failed"`, `expected: true`, `info`
severity, no remediation), with that vendor's adopted skills listed in
`suppressed`. The detector never raises and never aborts the run — it
always exits 0 with valid JSON. `collect.sh` additionally caps the whole
detector with `timeout 120`, mirroring the 180s cap it puts on `brew
doctor`: the per-call timeouts bound each git invocation, not the
collector's own wall clock, and this is the step that talks to the network.

**A failure is always visible as a finding, never only as silence.** The
`suppressed` list reaches stderr and nothing else — not `report.json`, not
the page — so a run that emitted no findings would render *pixel-identical
to "every vendored skill is in sync"*. That is the one outcome this source
must never produce, because it is the failure that looks like success. Two
cases beyond the per-vendor one above therefore also emit a finding:

- **The check could not run at all** — no dotfiles checkout, not a git repo,
  no `agent-skills` directory, vendor tables that no longer parse, an
  unreadable `marketplace.json`. One `probe_error` finding,
  `id: "skill-drift:source-unavailable"`, `expected: true`, `info`, no
  remediation. Its `detail` says plainly that nothing was checked and so
  nothing is known to be wrong — an honest "unknown", never a clean bill of
  health.
- **Upstream removed or renamed an adopted skill** — LOCAL and BASELINE
  resolve but UPSTREAM does not. This is actionable rather than expected:
  the next `sync-upstream.sh` will quietly stop shipping that skill. Emitted
  as `probe_error` with `notable` severity and `expected: false`, naming the
  subpath. Any other unresolved combination stays in `suppressed`, with a
  message saying *which* of local/baseline/upstream failed to resolve rather
  than only that one did.

What happens to a `skill_drift` finding next (enrichment, Tool-object
assembly, rendering, remediation execution) is out of scope for collection
— see `references/research.md` §Skill-Drift Enrichment,
`references/assembly.md` §Skill-Drift Assembly, `references/apply.md`
§Skill-Drift Remediation.

## Scoping

If the user scoped the request ("just podman", "only claude"), filter the
candidate list before researching. Scoping to version updates only? Skip
the brew-health findings. Scoping to "environment health"? Skip the
version sources. Filter whichever the request excludes.

The two non-version sources scope independently of each other, because
they answer different questions about different things: a request scoped
to version updates drops **both** `brew_health` and `skill_drift`; "how's
my brew install" keeps brew-health and drops skill-drift; "did my vendored
skills drift?" (or a request naming a vendor or a single skill) keeps
`skill_drift` — filtered to the named vendor/skill — and drops everything
else. Filtering a vendor out of `skill_drift.findings` is safe on its own,
since each finding is self-contained; the only thing not to do is keep one
skill of a vendor while dropping its siblings, because the remediation
command is vendor-scoped and its `label` counts them (§Skill-Drift
Collection).

## Repo Freshness

Before researching (part of step 1, Collect): run `scripts/repo_context.sh
. dotfiles > {session_dir}/repo_context.json` — it fetches from origin
(network read-only, never auto-pulls/merges — that's a separate decision
for the user) and emits the `repo_context` object (`references/schemas.md`
§Report Object) directly, so this doesn't get hand-derived from raw `git
fetch`/`rev-parse`/`log` output each run:

- `git fetch origin --quiet` in each repo, then compare `git rev-parse
  HEAD` against `git rev-parse @{u}` (or `origin/master` if no upstream is
  configured) to get ahead/behind counts.
- Pull `git log --oneline -20` from each repo into `repo_context` — this is
  research *context*, not something rendered verbatim as a big log dump in
  the UI. The recent-commits list backs `config_status` computation (see
  `references/research.md` §Config Status) and gives research subagents
  visibility into very recent changes that might already address what
  they're about to suggest.

`assemble.py` merges the file into the report verbatim; if it's missing at
assemble time, `assemble.py` warns and falls back to an empty/`up_to_date`
placeholder rather than failing.

If either repo comes back behind, surface it prominently at the top of the
report, and mention it in conversation before generating — recommendations
grounded in a stale checkout can be wrong (e.g. a Brewfile pin someone
already removed upstream). Don't block the review on it, just flag it
loudly.

---

Related: `references/schemas.md` (exact `repo_context`/`brew_health`/
`skill_drift` JSON shapes), `references/research.md` (what happens to these
candidates next — tiering, config_status, brew-health and skill-drift
enrichment), `references/server-and-session.md` (Serve starts right after
this step finishes, before research begins).
