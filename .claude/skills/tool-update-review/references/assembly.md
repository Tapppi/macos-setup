# Assembly — `assemble.py` Mechanics (Step 4)

Table of contents:
- Overview
- Loading and Merging
- Suggestion-ID Uniqueness
- Evidence Validation
- Risk Level
- `needs_sudo` Heuristic
- `auto_runnable` / Command Per Source
- Baseline Suggestion Synthesis
- Brew-Health Assembly
- `needs_attention`-Must-Have-a-Suggestion Enforcement
- Summary Counts and Output

## Overview

`scripts/assemble.py {session_dir}` merges `collect.sh`'s saved output
(`collect.json`, step 1 — see `references/collection.md`) with every
`research/*.json` file written in step 3 (see `references/research.md`) into
the single report object (`references/schemas.md` §Report Object), and writes
`{session_dir}/report.json`. This is purely mechanical/structural work — it
computes `summary` counts, enforces suggestion-id uniqueness, validates
evidence paths, assigns each tool a `risk_level`, and applies a real
`needs_sudo` heuristic — none of it is a subjective per-tool judgment left to
research, so every run applies the same rule the same way.

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

## Evidence Validation

Every `relevancy[]` and `context[]` item's `evidence`, plus
`config_status.evidence`, gets checked: a string that looks like a
`path` or `path:line` is resolved against the macos-setup and dotfiles repo
roots (absolute paths checked directly); a string that looks like a commit
citation (`commit …` or a bare 7–40 char hex prefix) is left alone — nothing
to check. Anything else that doesn't resolve under either root is **warned
about, never dropped** — a bad citation is a research-quality problem worth
surfacing to whoever's watching the run, not a reason to silently strip
content the human reviewer would otherwise have seen.

Evidence is normalized to a list first: the schema (`references/schemas.md`)
requires evidence to always be an array; a research subagent that returns a
bare string instead would make a naive `for ev in evidence` iterate
individual characters. Assembly coerces a bare string into a one-element
list and warns, rather than corrupting the rest of the validation output with
single-letter "evidence" entries.

## Risk Level

`risk_level` (`"low"` | `"elevated"`, see `references/schemas.md` §Report
Object for the pre-accept rendering effect) is computed entirely from
signals already present in the assembled Tool object — never a subjective
per-tool call from research, so every run applies the same rule the same
way. A tool is `"elevated"` if **any** of:
- `pinned` is true,
- any `relevancy[]` item has severity `warning` or `incompatible`,
- any suggestion on the tool has `kind` `"edit"` (default when `kind` is
  omitted), or
- the version delta is a major bump — semver-aware (compares the leading
  integer of `current_version`/`latest_version`); **an unparseable version
  pair defaults to `"elevated"`**, since an unknown delta size is never
  treated as low-risk.

Otherwise `"low"`. This only ever affects the render-time pre-accept
decision for the tool's **baseline** `upgrade` suggestion (a `"low"` tool's
baseline starts pre-accepted instead of undecided) — research-authored
`edit` suggestions always start undecided regardless of the tool's
`risk_level`. See `references/rendering-report.md` for how `risk_level`
drives that render-time default.

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

**`risk_level` for brew-health**: `"low"` when `health_expected` is true
(informational, never pre-anything), `"elevated"` otherwise — this keeps
every structural remediation (trust, untap, cask migration) starting
undecided; the render-time pre-accept path is scoped to `:upgrade`-suffixed
**baseline** ids only, and brew-health findings never have one of those, so
in practice a brew-health tool never renders pre-accepted regardless of this
flag — the flag mainly governs whether the card reads as "fine, informational"
vs. "needs a look."

**`summary.health_count`**: the count of `source: "brew-health"` tools,
tracked separately from `summary.total_outdated` (which excludes them) since
they're environment issues, not version updates. `collect.sh`'s
`suppressed` list (the noise-filtered findings — see
`references/collection.md`) is logged to stderr here, not rendered anywhere.

See `references/rendering-report.md` §Brew-Health Rendering for how the
category label, source badge, and `health_count` badge actually render, and
`references/apply.md` §Brew-Health Remediation for how these suggestions get
applied.

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

`report.json` is written with `schema_version: 1` (see
`references/schemas.md` §Report Object for the full top-level shape) and
`report_id` taken from the session dir's basename. Assembly itself does not
touch `research-status.json`, `status.json`, or any server-side state — see
`references/server-and-session.md` for the `phase: "ready"` write that
happens right after `render.py` runs.
