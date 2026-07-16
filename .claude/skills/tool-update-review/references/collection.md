# Collection (Step 1)

Reference for `scripts/collect.sh` and `scripts/repo_context.sh` — the two
deterministic, non-agentic data-gathering scripts that run before any
research subagent is spawned. Read this when running step 1, or when
maintaining either script.

## Table of Contents

- [Version Sources](#version-sources)
- [Brew-Health Collection](#brew-health-collection)
- [Scoping](#scoping)
- [Repo Freshness](#repo-freshness)

## Version Sources

Run `scripts/collect.sh` from the macos-setup repo root (pass the Brewfile
path if elsewhere) and save its stdout to `{session_dir}/collect.json` —
`assemble.py` (step 4) reads it from there rather than from conversation
memory. It emits machine context plus outdated tools from four version
sources:

- **Brewfile-manifested brew/cask packages** — transitive deps excluded,
  pinned formulae included. A pin usually marks a *known* incompatibility
  worth re-checking, not a tool to skip; never treat a pin as a reason to
  drop the tool from the candidate list.
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

## Scoping

If the user scoped the request ("just podman", "only claude"), filter the
candidate list before researching. Scoping to version updates only? Skip
the brew-health findings. Scoping to "environment health"? Skip the
version sources. Filter whichever the request excludes.

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

Related: `references/schemas.md` (exact `repo_context`/`brew_health` JSON
shapes), `references/research.md` (what happens to these candidates next —
tiering, config_status, brew-health enrichment), `references/server-and-session.md`
(Serve starts right after this step finishes, before research begins).
