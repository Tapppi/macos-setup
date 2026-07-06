---
name: tool-update-review
description: >
  Generate an interactive changelog review page for pending tool updates —
  brew/Brewfile packages and casks (including self-updating desktop apps),
  mise runtimes, standalone CLIs genuinely unmanaged by brew, and macOS
  system/app updates — with agent-written headliners, canonical
  changelog/release/blog links, and relevancy analysis against this machine
  and the user's setup repos (macos-setup, dotfiles, systems). Use this
  whenever the user asks to
  check tool updates, review changelogs, see what's outdated, asks "what's new
  in <tool>", wants headliner changes since a version, or wants update
  suggestions reviewed/applied — even if they only mention one tool or say
  something casual like "anything interesting in the latest brew updates?".
---

# Tool Update Review

Produce a per-tool changelog review the user acts on in the browser: version
deltas, headline changes, links to canonical sources, findings about *their*
environment, and concrete suggested edits they Accept / Reject / Discuss.
Decisions come back into the session as `feedback.json` and accepted edits are
applied to the setup repos.

Schemas, page/interaction spec, server contract, and failure modes live in
`references/design.md` — read it before assembling the report or rendering,
and follow its schema exactly (`schema_version: 1`). The live-tracking
Results view that takes over after Submit — `status.json`'s schema, the
`/status` and `/followup` server endpoints, and the tab-strip/action-list
page behavior — is specified in `references/results-view-design.md`; read it
before step 6 (writing the first `status.json`). The same doc's section E
covers the pre-report loading page and `research-status.json` — read that
part before step 2 (Serve).

## Workflow

### 1. Collect candidates

Pick a `report_id` (`tool-update-review-{YYYYMMDDTHHMMSSZ}`) and create its
session dir, `/tmp/{report_id}/`. Run `scripts/collect.sh` from the
macos-setup repo root (pass the Brewfile path if elsewhere) and save its
stdout to `{session_dir}/collect.json` — `assemble.py` (step 4) reads it
from there rather than from conversation memory. It emits machine context
plus outdated tools from four sources: Brewfile-manifested brew/cask
packages (transitive deps excluded, pinned formulae included — a pin
usually marks a *known* incompatibility worth re-checking, not a tool to
skip), mise runtimes, standalone CLIs, and `softwareupdate -l` entries
(macOS system/app updates — `source: "macos"`).

If the user scoped the request ("just podman", "only claude"), filter the
candidate list before researching.

**Also check repo freshness** (design.md C.5): run
`scripts/repo_context.sh . dotfiles > {session_dir}/repo_context.json` —
it fetches from origin (read-only, never auto-pulls/merges) and emits the
`repo_context` object (A.1) directly, so this doesn't get hand-derived from
raw `git fetch`/`rev-parse`/`log` output each run. `assemble.py` merges the
file into the report verbatim; if it's missing at assemble time,
`assemble.py` warns and falls back to an empty/`up_to_date` placeholder
rather than failing. If either repo comes back behind, say so up front in
conversation — recommendations grounded in a stale checkout can be wrong —
but don't block the review on it.

### 2. Serve (start now — before research even begins)

Research over a large candidate list takes minutes, and there's nothing to
show for it until assemble+render finish — start the server right after
collect instead of waiting for the report to exist, so the browser tab can
open immediately and show a live "gathering info" progress view instead of
sitting on a blank tab. `server.py` tolerates a missing `report.json` at
startup for exactly this reason (results-view-design.md E); `GET /` falls
back to an embedded loading page until `index.html` appears.

Start the server loopback-only (extra `--bind` listeners only if the user
asks):

```sh
python3 {skill_dir}/scripts/server.py {session_dir} &
```

Poll `GET /health` until 200 (≤5 s). Then pick URLs by session type — decide
at runtime, never hardcode hostnames (there are multiple server hosts):

- **Remote session** (`SSH_CONNECTION` or `SSH_TTY` set): also start a
  **foreground** `tailscale serve --set-path /updates {port}` as a background
  task (never `--bg`; foreground config dies with the session). Print both
  URLs: `https://$(tailscale status --json | jq -r '.Self.DNSName' | sed
  's/\.$//')/updates` and the loopback URL.
- **Local session**: loopback URL only, and `open` it. Tailnet serving only
  on request.

Write an initial `research-status.json` (schema: results-view-design.md
E.1) with `phase: "collecting"` and an empty `groups` array before moving on
— the loading page's first poll should find *something*, even if it's just
"gathering update candidates" with no group list yet.

### 3. Research each tool (tiered subagents)

Build each subagent's prompt by filling in
`references/research-prompt-template.md` — a fixed skeleton (what to fill
in, what file to write, what shape) so the boilerplate structure doesn't
get retyped by hand and drift between runs. It intentionally doesn't repeat
this step's quality bar below; read that in full before filling the
template in, every run.

Literal one-subagent-per-tool stops scaling once the candidate list gets
large — most outdated tools (a plain patch-bump brew formula, a mise runtime
with no bespoke setup) need only a quick changelog skim, and a dedicated
subagent per one is wasted overhead. Group tools into two tiers instead:

- **Individual-focus**: one subagent per tool, for any tool with a real
  touchpoint in the setup repos — a bespoke `tasks/*.sh` function (grep
  `tasks/install.sh`/`tasks/config.sh`), a dotfiles config file, or a
  Brewfile pin/comment naming it. These need the full relevancy/config_status
  depth this skill exists to produce, and batching them would dilute that.
- **Batched-by-category**: one subagent covering ~4-9 tools with no repo
  touchpoint beyond a plain Brewfile/mise line — group by rough category
  (CLI utilities, GUI casks, mise runtimes) so the subagent's changelog-skim
  work stays coherent. Give it the same schema and depth requirements per
  tool inside the batch; a batch subagent still returns one full Tool
  research object per tool, just from one process instead of N.

**Use word-boundary grep (`grep -wn`/`-wni`) to find touchpoints, never plain
substring matching.** A substring grep produces false positives that feed
wrong context to a subagent — e.g. `grep cloc` matches inside `clock`/
`wall-clock`, `grep go` matches inside `mkdir -m go=` or `go to`. A tool
that looks like it has a bespoke touchpoint because of a substring collision
gets miscategorized into the individual tier for the wrong reason (or worse,
a real touchpoint gets diluted by unrelated grep noise in its context). Re-run
suspicious hits with `-w` before trusting them.

Spawn every subagent (both tiers) in one turn. Each gets: the tool
id(s)/name/source/versions in its scope, the machine context (arch matters —
ARM-only dependencies are incompatibilities on x86_64, not footnotes), the
paths it may scan for relevancy, and the two repos' `recent_commits` from
step 1's `repo_context`.

**Each subagent writes its own output JSON directly to
`{session_dir}/research/{tool-or-batch-slug}.json`** rather than returning
its findings as conversation text for you to hand-transcribe — at any real
candidate count, retyping every subagent's structured output yourself is
slow and error-prone (a transcription slip silently changes what ships in
the report). Tell each subagent the exact path to write and the exact shape
(next paragraph); step 4 reads the files back rather than reconstructing
them from memory. **Every research file is a JSON array**, regardless of
tier — an individual-focus subagent writes a one-element array, a batch
subagent writes one element per tool in its batch — so `assemble.py` parses
every file in `research/` identically instead of branching on tier. Each
array element is a partial Tool object per `references/design.md` A.1
(`headliners`, typed `links`, `relevancy`, `context`, `release_inventory`,
`suggestions`, `config_status`) plus its own `"id"` field (`{source}:{name}`,
matching a collect.sh candidate) so assembly can match it back up.

Research quality bar:
- **Headliners**: ≤6 bullets covering the whole current→latest range, not just
  the newest release. Skim actual release notes/changelogs — don't guess from
  version numbers. **One fact per bullet — split compound bullets before
  classifying them.** A changelog often bundles unrelated changes into one
  sentence ("added X and Y, plus fixed CVE Z"); if you classify the bundle
  as one atomic item, it gets filed under whichever topic feels most severe
  and the rest of the content is lost (seen this run: rust's "added
  assert_matches!/Copy-range-types, plus fixed two Cargo CVEs" got filed
  entirely under Security — the macro/feature content never showed up
  under Features at all). Split first, classify each resulting item
  independently; both can cite the same source link since they came from
  the same release.
- **Category is topic, not urgency — the two are independent axes.** Which
  group a headliner/relevancy item belongs to (Security/Fixes/Features/
  Notes) is decided purely by *what kind of change it is*, never by how
  urgent or prominent it feels relative to other items you're also
  reporting for the same tool. Severity/priority is a separate, per-item
  property that drives visual weight *within* whichever category the item
  topically belongs to (B.2) — it never changes which category the item
  is in. Seen this run: yt-dlp's "restricts --exec command templates to
  safe string conversions... to close a command-injection footgun" is
  unambiguously Security by topic, but got filed under Notes, apparently
  because it read as less prominent than the CVEs already sitting under
  Security for that tool — it should have stayed Security with a lower
  per-item priority, not moved category. For contrast, yt-dlp's "Minimum
  recommended Python raised to 3.11, Node to v22..." is a correctly-placed
  Notes item — genuine compatibility/requirements info that isn't
  Security/Fixes/Features by topic at all.
- **Links**: always the canonical changelog for the version range; add release
  pages for majors and official blog posts when they exist. Every relevancy
  finding and suggestion should be traceable to a link. **Prefer the most
  specific destination that actually covers the version range, never a
  generic "all versions" index page when a dedicated page for this range
  exists** — a GitHub Releases page for the specific tag, a CHANGELOG.md
  section anchor, a dedicated release-notes URL. Confirm you actually
  fetched/read the page you're linking, not just guessed its URL from
  convention (seen this run: stunnel's SECURITY and FEATURES links both
  pointed at the generic version-list page instead of the 5.79 release
  itself). **Never link to something that dead-ends or triggers a file
  download** — if the only source for the content is inside a downloaded
  tarball/archive or a raw non-browsable file (seen this run: stunnel's
  FIXES link pointed at a NEWS.md "fetched from the 5.79 source tarball,"
  which isn't a page a user can click through to), don't link it at all.
  Instead extract the relevant excerpt (not the whole file) and set it as
  `embedded_content` (markdown, design.md A.1) on the link object with `url`
  omitted — the page renders that in a modal instead of navigating
  externally. This is the exception, not the default; most tools have a
  normal browsable page and should just link it.
- **Relevancy is the point of this skill.** Scan the user's setup repos —
  `~/project/github/tapppi/macos-setup` (Brewfile, intel.Brewfile, tasks/,
  dotfiles/ submodule with shell/git/tmux/Claude configs) and
  `~/project/github/tapppi/systems` (NixOS flake) — plus machine facts, for
  places the tool is configured or its changed behavior lands. Severity:
  `incompatible` (won't work here — e.g. new major requires Apple Silicon on
  an Intel machine) > `warning` (breaks a config/workflow the user has) >
  `notable` (touches something they use) > `info`. Cite evidence as
  `file:line` paths. **`relevancy[]` requires a genuine `motivating_change`**
  — an actual changelog item driving the finding. If you're about to write
  `motivating_change: null` or something like "none found"/"not a
  changelog-driven finding", that finding isn't relevancy at all; see the
  next bullet for where it actually belongs.
- **Classify non-changelist findings correctly instead of defaulting them
  into `relevancy` with severity `"info"`.** Three different things show up
  during research that aren't "a changelog item affects this setup," and
  each has its own home (design.md A.1):
  - **`context[]`** — present-tense repo-scope/usage/locality/
    config-verification notes: is this tool even used here, does a claimed
    touchpoint actually hold, does existing script logic still cover this
    release. E.g. "azure-cli has no bespoke touchpoint anywhere in this
    repo," "duckdb is actively used by this machine's shell config," "the
    existing warning and control flow in install_macos_sw remain accurate
    as written." Keep these — they're genuinely useful — just route them to
    `context`, not `relevancy`.
  - **`release_inventory[]`** — bookkeeping about which releases exist in
    the current→latest range (e.g. "two releases landed: 2026.06.09 and
    2026.07.04"). This is inventory about the range itself, not a claim
    about tool behavior; it doesn't belong as prose under any content group
    or under `context` — one `{version, link}` entry per release.
  - **Pure absence-of-change statements** — "no mention in the release
    notes of any change to X" with nothing else to say. These aren't
    findings at all; per the filler-suppression rule below, omit them
    entirely rather than filing them anywhere.
- **Don't author "I checked, found nothing" as if it were real content.**
  An empty `relevancy`/`context` array is the normal, expected result for
  most tools on most runs — it does not need an item that says so. Seen
  this run: "None of the 18 CVEs... apply to this repo's curl usage
  pattern," "No relevant change found (checked commit range for 'focus'
  keyword)," and, filed under FIXES as if it were a real changelog bullet,
  "No breaking changes identified between 1.95.0 and 1.96.1." All of these
  should have been *nothing* — omit the item entirely rather than narrating
  the due-diligence that produced an empty result.
  - **Exception — vendor genuinely publishes no detail.** When a release
    happened (so it's worth acknowledging) but the vendor's own notes are
    just a non-answer ("This release includes security improvements.
    Updating is recommended.", with nothing technical ever published),
    don't manufacture 1-2 bullets restating that non-answer — render a
    single small compact tag instead (e.g. "No detailed changelog
    published"), with the link living once on the tool's canonical
    changelog reference, not repeated per bullet. Seen this run: Slack's
    research produced two redundant SECURITY bullets citing the identical
    release-notes link, one restating the vendor's non-answer and one
    explicitly noting the vendor doesn't publish detail — should have been
    one compact tag.
- **Suggestions authored here are always `kind: "edit"`** (a concrete
  Brewfile/dotfiles/config change): target file, rationale, motivating link,
  and a short `diff_preview`. Only suggest what the changelog actually
  motivates. No edit suggestion is fine — most tools just get headliners. Do
  **not** author the plain "upgrade this tool" suggestion — that's a
  `kind: "upgrade"` suggestion synthesized mechanically in step 4 for every
  tool, not something to duplicate here.
- **`config_status`** (design.md C.5): grep targeted `git log --oneline --
  <files you're already inspecting for relevancy>` (not just the 20-commit
  `repo_context` list) plus
  `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md` for a
  prior commit/entry naming this tool. Found + version ≥ today's
  `latest_version` → `"up_to_date"`. Found for an older version + this
  run's headliners describe something that could invalidate that fix's own
  reasoning → `"needs_attention"` with a concrete `detail` naming what
  changed. Otherwise `"unknown"` — don't guess a verdict without a citable
  diff between the old fix and the new change. **A `"needs_attention"`
  verdict must come with at least one suggestion addressing it** — flagging
  a possibly-stale fix and then giving the user nothing to act on just moves
  the "did anyone check this?" question one level up without answering it.
  If the re-check concludes the old fix still holds after all, that's
  `"up_to_date"`, not `"needs_attention"` with no suggestion.
- **Watch items** (design.md C.7): also check
  `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/watch-items.json`
  for entries keyed to this tool — standing concerns the user flagged on a
  prior run (e.g. "tell me if cursor-cli's shell-integration behavior
  changes"). If this run's changelog touches a watched topic, that's not a
  normal `info` relevancy finding: bump severity to at least `notable` and
  prefix the summary so it reads as a highlight (e.g. "⚠ Watch item hit:
  ..."), citing the watch item's `note`.
- **Deduplicate facts across headliners and relevancy for the same tool
  before returning.** It's easy to restate one underlying change twice —
  once as a headliner bullet, once as a relevancy finding citing the same
  PR/commit to reach the same conclusion (seen this run: codex's CI/release
  signing migration to Azure Key Vault + rcodesign showed up as both a
  headliner and a full relevancy finding about the same quarantine
  workaround). Once you've drafted both arrays, check whether any headliner
  and relevancy finding describe the same underlying change; if so, keep it
  once — normally as the relevancy finding, since that's the more specific,
  actionable placement — and drop or trim the headliner rather than shipping
  both. Do this self-check every time rather than assuming it won't happen;
  it happens by default when a change is both changelog-worthy and
  relevant to this setup.
- **Separate a changelog fact from a note about *this machine's* scope or
  usage of the tool.** "v5 dropped X" is a changelog fact (Security/Fixes/
  Features); "this tool isn't currently in the Brewfile" or "we don't use
  the feature this release changes" is context about *this setup*, not a
  change in the tool itself — keep the two apart in your returned object
  (`relevancy[]` for the former is fine, but don't write a scope/usage
  observation as if it were a headliner). Rendering pulls context-flavored
  notes into their own section (B.2) instead of mixing them into the
  changelog groups, so return them in a way that's cleanly separable —
  don't bury a scope note as an extra clause on a changelog bullet.
- **Bespoke `tasks/*.sh` setup** (design.md C.6): if the tool has setup logic
  beyond a plain package command (grep `tasks/install.sh`/`tasks/config.sh`
  for a function referencing the tool), check whether this run's changelog
  changes something that function relies on. If so, test the specific
  affected command non-destructively (throwaway-named resource, dry-run
  flag, `--help` diff — never the user's real state, never the whole
  function or `./setup.sh install`) and propose a tested `kind: "edit"`
  suggestion, not a manual punt. The baseline `upgrade` suggestion (added in
  step 4) stays `auto_runnable: true` regardless — only mark it `false` if
  no command for this tool is safely testable at all, which should be rare.
- **Hold subagents to the exact schema shapes** (spell them out in the
  research prompt): `evidence` is always an array, suggestions always use
  `title`/`target_files`/`rationale`/`motivating_link`/`diff_preview`.
  Loose shapes (bare strings, ad-hoc `description` fields) force hand
  normalization during assembly and have caused real rework.
- **Depth by tool**: node semi-detailed (security advisories, breaking
  changes, notable features per minor); other runtimes coarse (breaking
  changes and majors only); everything else proportional to how much the user
  configures it.
- **The fleet has heterogeneous hosts** (until the eventual nix migration):
  `Brewfile` manifests the Apple Silicon host(s), `intel.Brewfile` the Intel
  host(s), and `tasks/*.sh` contain arch-conditional blocks. The collector's
  `machine` block describes only the host running this review. Assess impact
  per affected host/manifest — the same update can be `incompatible` on one
  host and desirable on the other (e.g. an ARM-only major on an Intel
  machine). Set severity to the worst affected host, spell out the per-host
  split in `detail`, and make each suggestion's `target_files` name the
  specific manifest(s) it touches (a Brewfile edit usually needs a decision
  about its intel counterpart, not a blind mirror).

On subagent failure/timeout, set `research_error` and keep the tool listed
with versions only.

### 4. Assemble and render

Run `scripts/assemble.py` to merge `collect.sh`'s output with every
`research/*.json` file written in step 3 into the report object (design.md
A.1): it computes `summary` counts, ensures suggestion ids are unique
(`{source}:{name}:{slug}`), verifies evidence paths exist (warn, don't
drop), assigns each tool a `risk_level` (design.md A.1), and applies a real
`needs_sudo` heuristic to every synthesized baseline suggestion instead of
a blanket `false`. The rendered page pre-accepts a `"low"` risk_level
tool's baseline upgrade suggestion instead of leaving it undecided
(design.md A.1/A.2) — this is render-time, computed from `risk_level`, not
something `assemble.py` bakes into `feedback.json`.
`collect.sh` uses `brew outdated --greedy` specifically so `auto_updates:
true`/`version :latest` casks (self-updating desktop apps — e.g. the `claude`
app cask, distinct from the `claude-code@latest` CLI cask) aren't silently
skipped; no standalone/cask dedup is currently needed — `claude-code@latest`
and `codex` are both plain, correctly-version-tracked casks today (verify
this hasn't drifted again before assuming otherwise — see collect.sh's
`standalone_json` comment).

**Synthesize a baseline `kind: "upgrade"` suggestion for every tool**, id
`{source}:{name}:upgrade` (design.md A.1 has the exact shape and per-source
`command`). This is mechanical assembly-step work, not left to the research
subagent — it's the only thing that actually tracks an outdated tool to
completion; without it a plain patch bump with no config impact would get
zero suggestions and silently never get upgraded. Research-authored `edit`
suggestions are additional to this baseline, never a replacement for it.

Write `report.json` to the session dir, then:

```sh
python3 scripts/render.py /tmp/tool-update-review-{report_id}/report.json
```

which injects it into `assets/report-template.html` and writes `index.html`
plus a copy of `server.py` next to it (the server is already running from
step 2, off the skill's own `scripts/server.py` — this copy is just so the
session dir stays self-contained for later reference, not something the
live process needs). **Immediately after**, update `research-status.json`
to `phase: "ready"` — this is what the loading page's poll is waiting for
to trigger its reload into the now-real report.

### 5. Wait for feedback.json

**Implement this wait as a single backgrounded shell command, not a
recurring model wakeup/poll loop.** A wakeup-loop re-invokes the model on a
timer, and each invocation past the ~5-minute prompt-cache TTL reprocesses
the full conversation at full (uncached) input price — expensive and
pointless for a review that can sit untouched for a long time while the
user reads in the browser. Instead, run something like:

```sh
until [ -f "{session_dir}/feedback.json" ]; do sleep 5; done
```

as a backgrounded tool call and let the harness notify the session exactly
once, when the file actually appears — zero interim reprocessing regardless
of how long the user takes. Do **not** block on server exit — the server
stays up after feedback. Timeout after 24 hours: offer to re-open the page
or abandon.

If `feedback.json` already exists when the server starts (e.g. session
crashed and restarted): validate `report_id`. Match → skip the wait, print
"Resuming from existing feedback." Mismatch → warn and ask: re-serve fresh
(rename stale file) or use stale feedback.

### 6. Write initial status.json

Schema: `references/results-view-design.md` A. Immediately after detecting
`feedback.json`, run `scripts/write_status.py init {session_dir}` — it
reads `feedback.json` + `report.json` and writes the initial
`status.json` atomically: one action per decision in suggestion order
(accepted/discuss → `"pending"`, rejected/undecided → `"skipped"`), one
investigation action per `tool_comments` entry and per `discuss` decision
with a comment (see step 7), and synthetic `commit:dotfiles`/
`commit:macos-setup`/`push:dotfiles`/`push:macos-setup` actions — only for
repos that will actually get a commit, inferred from which accepted
suggestions' `target_files` fall under `dotfiles/`. This replaces hand-typed
atomic-write code that used to get re-derived every run.

### 7. Apply accepted suggestions — per-action status updates

For each accepted suggestion in order:
1. Run `scripts/write_status.py set-action {session_dir} {id} running`,
   **then immediately issue the actual command/edit in the same
   uninterruptible step** — never write `"running"` and leave it as a
   separate turn before the real work starts. This run hit exactly that
   gap: an action got marked `"running"`, the session got pulled into an
   unrelated investigation before ever issuing the command, and the action
   sat showing "in progress" with no real process behind it. If something
   genuinely needs investigating mid-apply, finish or explicitly abandon
   the current action's command first — don't leave a `"running"` write
   dangling.
2. Apply the edit.
3. Run `scripts/write_status.py set-action {session_dir} {id} done|failed
   --note "<one-line outcome>" [--detail-file <path>]` (the detail file's
   last ≤10 lines become `detail[]`).

One state transition per write (never skip `"running"`). Commit actions
follow the same pattern. This replaces hand-typed atomic-write code that
used to get re-derived for every transition.

**While any action is `"running"` or any `pending_followups` entry is
awaiting a decision, keep a heartbeat going** rather than only writing
status.json at hard state transitions — a long-running background command
(a slow cask installer, a poll loop for a manual upgrade) can leave
`written_at` stale for minutes with nothing wrong, and the page's staleness
heuristic (results-view-design.md C.7) will falsely read that as "session
may have stopped." Every ~20-30s during these stretches, run
`scripts/write_status.py touch {session_dir}` (bumps `written_at` only)
and `scripts/write_status.py sync-turns {session_dir}` (merges any new
turns from `followup_turns.json` into the matching `pending_followups`
entry or action `thread` — results-view-design.md C.10) so a turn the user
submits gets picked up promptly rather than only at the next action's
natural status.json write. Implement this the same way as the steps 5/11
waits — **a cheap backgrounded shell loop, not a model-wakeup timer**: a
short-interval wakeup would reprocess the full conversation on every tick
(expensive, and a cache miss past ~5 minutes per the session's own
guidance on backgrounded waits vs. wakeup loops). The loop should only
actually notify/wake the session when something meaningful happens — a new
turn appears, or the long-running command it's watching completes — same
pattern as the existing feedback.json/Finish-button waits, just covering
this mid-apply window too. After `sync-turns` reports a new turn, act on
it per its `decision` (accept → apply now, reject → mark resolved,
discuss/comment → append an agent turn) — `write_status.py` has no
subcommand for "answer a turn" since the answer itself (an edit, a retry,
a reply) is exactly the judgment call this session makes, not something to
script.

Accepted suggestions split by `kind`:
- **`edit`** (Repo/automation changes) → applied directly to the macos-setup
  repo in-session. Dotfiles paths go in the submodule; Brewfile edits need
  per-host decisions; commit per CLAUDE.md (specific paths, imperative
  messages, no AI attribution). When a target file is managed by
  `tasks/projects.sh` (workspace `.tapppi-project.json` manifests, rendered
  `mise.local.toml`, per-repo skill symlinks), apply the edit and then run
  `./setup.sh projects` — the one documented, narrow exception to
  CLAUDE.md's never-run-setup-scripts rule, scoped to that single idempotent
  subcommand — instead of hand-simulating what that task does. The `systems`
  repo (nix) is out of scope — surface nix findings as notes only.
- **`upgrade`** (machine-local: mise runtimes, brew/cask packages, standalone
  CLIs) → execution depends on `auto_runnable` and the report's
  `auto_run_upgrades` toggle (feedback.json, default `true` — design.md
  C.4):
  - **Not auto-runnable** (`auto_runnable: false`, or toggle off): print the
    suggestion's `command` and ask the user to run it themselves, then poll
    for completion by checking the installed version every ~30s (`mise
    current <tool>` for mise, `brew list --versions <name>` for brew/cask,
    `<tool> --version` for standalone). Once installed reaches
    `latest_version`, mark `"done"` with the confirmed version. Cap polling
    at ~20 min — past that, leave the action `"running"` with a reminder
    note rather than blocking the rest of the session; it can complete
    later and Finish is still available.
  - **Auto-runnable and toggle on**: run `command` directly (design.md
    C.4) — plain subprocess for `needs_sudo: false`, `SUDO_ASKPASS`
    routing (`scripts/askpass.sh`) for `needs_sudo: true`, with a ~90s
    no-output timeout that falls back to "mark failed, tell the user to
    run it manually" rather than hanging the apply pass. Same "poll to
    confirm the version landed" verification either way — running the
    command isn't itself proof it worked.
  - **Caveat**: re-running this repo's `install_mise_runtimes` task (`mise
    install`) does *not* upgrade an already-installed runtime pinned to
    `"latest"` — mise resolves `"latest"` once, at first install. Only a
    bare `mise upgrade [tool]` re-resolves it, which is exactly what the
    synthesized `command` runs — don't substitute `install.sh` for it.
  - **Bespoke setup beyond a plain upgrade** (podman's `install_podman_intel`
    and similar): the baseline `upgrade` suggestion still just runs the
    plain package command as above — bespoke setup doesn't change that.
    What it *does* change is research (step 3, design.md C.6): audit the
    `tasks/*.sh` function's own code against this run's changelog, and if
    it needs a fix, test the specific affected command(s) non-destructively
    (never the whole function, never `./setup.sh install`) before proposing
    a normal, tested `kind: "edit"` suggestion targeting that file — not a
    manual punt. `auto_runnable: false` is the fallback for the rare case
    where no safe non-destructive test exists for that tool, not the
    default for "has bespoke setup." Never construct or run a
    `setup.sh`/`tasks/*.sh` invocation yourself beyond the one documented
    `./setup.sh projects` exception (see the `edit` bullet above and
    CLAUDE.md) and the scoped, tested commands from C.6 step 3.

**`tool_comments`/`discuss` investigation never applies anything directly.**
If looking into a tool comment (or a `discuss` decision's comment) concludes
a concrete change is warranted, write a followup object to a JSON file and
run `scripts/write_status.py add-followup {session_dir} <file>`
(`origin: "user_comment"`, results-view-design.md A/C.10) — same shape as
any other suggestion, `id` like `{source}:{name}:from-comment-{slug}` —
surfaced live in the Results view for an explicit accept/reject/discuss,
not auto-applied. The investigation action itself (label it "Investigate:
{tool} — {comment}") stays `"running"` until that follow-on decision
lands, then reflects the outcome ("done, no change needed" / "done, see
new suggestion {id}" / etc.). This applies even when `auto_run_upgrades`
is on — a free-text comment triggering unreviewed changes defeats the
entire point of a review.

**Create a followup yourself, don't just raise it in conversation, when
something during apply needs an explicit decision beyond plain
success/fail** (results-view-design.md C.10/D) — e.g. an accepted cask
upgrade completing but its installer triggering an unrelated Gatekeeper
quarantine popup. Add a `pending_followups` entry the same way, with
`origin: "agent_initiated"` instead. The
Results view should be the single place someone checks for "things needing
my decision" — not split across the page and the chat transcript.
Followups (and a `"failed"` action's own debug thread) are multi-turn:
the heartbeat loop's `sync-turns` call (step 7) surfaces new user turns on
any open thread; act on them promptly (accept → apply now, reject →
mark resolved, discuss/comment → append an agent turn answering or asking
back) — including on a thread already marked `resolution: "applied"` or
`"rejected"`, since an out-of-turn addition can still arrive later and
threads are never permanently closed to new input.

**If the comment expresses a standing concern rather than a one-off
question**, also append an entry to `watch-items.json` (design.md C.7) so
future runs check for it automatically — a preference like "tell me if this
tool's shell integration changes, ever" shouldn't only live in this
conversation's memory. A one-time question ("does this release fix the bug
I hit last week?") doesn't need one; use judgment rather than creating a
watch item for every comment.

### 8. Push, then write terminal status.json

**Push any commits made in step 7 before writing the terminal status** —
this skill's `edit` commits are meant to land on origin, not sit local-only
(explicit user directive; a standing exception to the usual "confirm before
push" caution, scoped to this skill's own apply step). Order matters:
`git -C dotfiles push origin master` first (if the submodule got commits),
then push macos-setup itself (which carries the updated submodule pointer
commit). The `push:dotfiles`/`push:macos-setup` actions already exist from
step 6's `init` (only for repos that got a commit — no no-op action to
skip). On push failure (diverged remote, network, auth): mark `"failed"`
with the git error in `detail`, don't retry destructively (no force-push),
leave it for the user to resolve manually — this is exactly the kind of
hard-to-reverse-if-wrong action that stops rather than guesses.

For each machine-local upgrade actually applied, write a changelog entry
file (`## {date}\n### {source}: {name} {old}→{new}\n{one to three sentence
summary}`) and run `scripts/write_status.py append-changelog {session_dir}
<entry-file>...` — one call, one or more entry files — which appends them
to `status.json`'s `changelog_entries` *and*
`${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md` (creates
the dir/file if absent) atomically, so this can't drift out of sync the way
two separate hand-written appends could.

Then run `scripts/write_status.py finalize {session_dir} --phase
done|discussing --recap-file <path>` (write the recap text — applied edits
with commit hashes *and push status*, failed actions with remediation
hints for the ones that stay unresolved, rejected/undecided items — to a
file first) — it sets `done`, computes `summary` from the actions' own
decision/state fields, and writes `written_at`, all atomically. Recap does
**not** need to mention discuss items' comments or followup/failed-action
counts — those live in `pending_followups`/the Results view's unified
header and per-action threads now (design.md C.2 step 6 note; C.10),
computed live, so a frozen recap sentence about them can't go stale.

### 9. Surface discuss items in conversation

After writing terminal status, raise each `discuss` item in the session:
tool name, suggestion title, user's comment. Don't apply until the user
confirms. No status.json update needed — recap already mentions these.

### 10. Wait for Finish (POST /shutdown)

Same principle as step 5 — **backgrounded shell command, not a wakeup
loop.** The page's Finish button posts `/shutdown`, which triggers a
1 s-delayed `server.shutdown()`; the server process then exits and its
`/health` endpoint stops responding. Run something like:

```sh
until ! curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:{port}/health | grep -q 200; do sleep 5; done
```

backgrounded, and let the harness notify once when it exits — this can be
a long, open-ended wait (the user may take a while reviewing results), and
a periodic wakeup here pays the same avoidable full-context reprocessing
cost as step 5. Cap at 24 h: if the backgrounded check hasn't fired by
then, print "Review session timed out — you can still click Finish in the
browser, or close it." and proceed to teardown.

### 11. Teardown

Kill the tailscale serve proxy (if started). Clean up any temp files. Session
complete.

## Notes

- The report page is fully offline (no CDN). The tailscale serve proxy
  needs no firewall approval and serves tailnet-only HTTPS; the `/updates`
  path mount leaves the root hostname free for other serves. The page and
  server are subpath-tolerant (relative feedback URL, suffix routing), so
  they work at `/` and behind the mount alike. Never bind `0.0.0.0` or LAN
  interfaces without asking — the report exposes config details.
- Restarting the server mid-review is safe: page state lives in the open
  tab; same port + `report_id` keep Submit working. macOS quirk: a machine
  can't reach its *own* tailscale IP (utun hairpin) — verify locally
  against 127.0.0.1, remotely via tailscale.
- Pinned tools: never suggest unpinning unless the blocking reason is verified
  gone in the new version — the pin exists because an upgrade broke something.
- `auto_run_upgrades` defaults to `true` (explicit user directive), but two
  things always override it toward manual: `auto_runnable: false` on the
  suggestion (bespoke setup, e.g. podman), and the toggle itself if the user
  unchecks it for a given session. Never invent a third way around either —
  no `NOPASSWD` sudoers edits, no running `setup.sh`/`tasks/*.sh` yourself
  beyond the one documented `./setup.sh projects` exception, no auto-applying
  anything derived from a free-text comment without a fresh accept/reject.
