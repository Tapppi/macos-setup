---
name: tool-update-review
description: >
  Generate an interactive changelog review page for pending tool updates —
  brew/Brewfile packages and casks (including self-updating desktop apps),
  mise runtimes, standalone CLIs genuinely unmanaged by brew, and macOS
  system/app updates, plus Homebrew environment-health findings from `brew
  doctor` — with agent-written headliners, canonical changelog/release/blog
  links, and relevancy analysis against this machine and the user's setup
  repos (macos-setup, dotfiles, systems). Use this whenever the user asks to
  check tool updates, review changelogs, see what's outdated, asks "what's new
  in <tool>", wants headliner changes since a version, wants a holistic look
  at their brew/tap/cask/keg health, or wants update
  suggestions reviewed/applied — even if they only mention one tool or say
  something casual like "anything interesting in the latest brew updates?".
---

# Tool Update Review

Produce a per-tool changelog review the user acts on in the browser: version
deltas, headline changes, links to canonical sources, findings about *their*
environment, and concrete suggested edits they Accept / Reject / Discuss.
Decisions come back into the session as `feedback.json` and accepted edits
are applied to the setup repos, plus Homebrew environment-health findings
(`brew-health`) — see `references/collection.md`.

This file is a lean index. Every step below stays short and links out to the
reference doc with the full mechanics — read that doc before doing the work
it covers, not just when something breaks.

## Reference Documents

- `references/schemas.md` — every JSON shape: report object, `feedback.json`,
  `status.json`, `research-status.json`. Read this first if you need a
  field's exact shape.
- `references/collection.md` — step 1: `collect.sh`'s sources, brew-health
  taxonomy, repo freshness.
- `references/research.md` — step 3: tiering, the full research quality bar,
  config_status, watch items, bespoke-setup testing, brew-health enrichment.
  Read in full if you are a research subagent.
- `references/assembly.md` — step 4: `assemble.py`'s merge/risk/suggestion
  logic.
- `references/apply.md` — steps 6–9: executing suggestions, followups,
  push/changelog.
- `references/server-and-session.md` — `server.py`'s endpoints, serve/wait
  mechanics, consolidated failure modes.
- `references/rendering-report.md` — the pre-Submit report page design.
- `references/rendering-results.md` — the post-Submit Results view design.
- `references/research-prompt-template.md` — the subagent prompt skeleton
  (step 3).

## Sources & Finding Categories

- `brew` / `cask` — Brewfile packages (see `references/collection.md`).
- `mise` — runtime versions (see `references/collection.md`).
- `standalone` — CLIs unmanaged by brew (see `references/collection.md`).
- `macos` — `softwareupdate -l` entries (see `references/collection.md`).
- `brew-health` — Homebrew environment-health findings from `brew doctor`
  (deprecated/disabled casks, orphaned/unlinked kegs, untrusted taps,
  missing deps) — not a version delta. Full taxonomy and noise filter:
  `references/collection.md`; enrichment: `references/research.md`;
  assembly: `references/assembly.md`; rendering:
  `references/rendering-report.md`; apply: `references/apply.md`.

## Workflow

### 1. Collect candidates

Pick a `report_id` (`tool-update-review-{YYYYMMDDTHHMMSSZ}`) and create its
session dir, `/tmp/{report_id}/`. Run `scripts/collect.sh` from the
macos-setup repo root (pass the Brewfile path if elsewhere) and save its
stdout to `{session_dir}/collect.json` — `assemble.py` (step 4) reads it from
there rather than from conversation memory. It emits machine context plus
outdated tools from four version sources, **plus** a `brew_health` object
(`brew doctor` environment-health findings — the "state of my brew install,"
not version deltas).

If the user scoped the request ("just podman", "only claude"), filter the
candidate list before researching — scoping to version updates skips
brew-health; scoping to "environment health" skips the version sources.

**Also check repo freshness**: run `scripts/repo_context.sh . dotfiles >
{session_dir}/repo_context.json` — it fetches from origin (read-only, never
auto-pulls/merges) and emits the `repo_context` object directly. If either
repo comes back behind, say so up front in conversation, but don't block the
review on it.

Full source/pin/noise-filter/scoping/repo-freshness detail:
`references/collection.md`.

### 2. Serve (start now — before research even begins)

Research over a large candidate list takes minutes, and there's nothing to
show for it until assemble+render finish — start the server right after
collect instead of waiting for the report to exist, so the browser tab can
open immediately and show a live "gathering info" progress view instead of
sitting on a blank tab.

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

Write an initial `research-status.json` with `phase: "collecting"` and an
empty `groups` array before moving on — the loading page's first poll should
find *something*.

Endpoint contract and failure modes: `references/server-and-session.md`.
Loading-page design: `references/rendering-results.md`.

### 3. Research each tool (tiered subagents)

Build each subagent's prompt by filling in
`references/research-prompt-template.md` — a fixed skeleton so the
boilerplate doesn't get retyped by hand and drift between runs.

Group tools into tiers instead of one-subagent-per-tool: **individual-focus**
(one subagent per tool with a real repo touchpoint — bespoke `tasks/*.sh`
function, dotfiles config, Brewfile pin/comment), **batched-by-category**
(one subagent covering ~4-9 tools with no repo touchpoint, grouped by rough
category), and a dedicated **brew-health group** (individual-tier — every
finding is repo-touchpoint work by nature). Use word-boundary grep
(`grep -wn`/`-wni`), never plain substring matching, to decide which tier a
tool belongs in — a substring hit (`grep cloc` matching inside `clock`) can
miscategorize a tool into the wrong tier.

**Any real touchpoint → individual-focus; do not batch it, and when unsure,
default to individual.** This is the highest-leverage tiering call — batching
a tool that has a bespoke setup function, a dotfiles config file, or a
Brewfile pin/comment produces measurably thinner research than the touchpoint
deserves (e.g. `diff-so-fancy` is the git pager in `dotfiles/config/git`;
`openssh` underpins the `~/.ssh/config` written by `tasks/init.sh`). Grep is
a starting signal, not the last word: it can miss a touchpoint where a script
uses a tool *conceptually* without ever naming it literally (init.sh
configures ssh but never contains the string "openssh"), so also skim the
`tasks/*.sh` set as a whole (install.sh, config.sh, init.sh, macos.sh,
projects.sh — not just install.sh/config.sh) and the dotfiles configs, and
individual-tier anything you find, grep hit or not. Read
`references/research.md` §Tiering in full before assigning tiers — the full
heuristic lives there.

Spawn every subagent (both tiers) in one turn. **Each subagent writes its own
output JSON directly to `{session_dir}/research/{tool-or-batch-slug}.json`**
— a JSON array, one element per tool in scope, matching `research.md`'s
Output Contract — rather than returning findings as conversation text.

**Read `references/research.md` in full before spawning subagents, every
run** — it holds the complete quality bar (headliner atomicity,
category/severity axes, link quality, relevancy vs. context vs.
release_inventory vs. filler, config_status, watch items, bespoke-setup
testing, brew-health enrichment, depth-by-tool, heterogeneous hosts) and the
tiering/dispatch mechanics summarized above.

On subagent failure/timeout, set `research_error` and keep the tool listed
with versions only.

### 4. Assemble and render

Run `scripts/assemble.py` to merge `collect.sh`'s output with every
`research/*.json` file into the report object: it computes `summary`
counts, ensures suggestion ids are unique, verifies evidence paths exist,
assigns each tool a `risk_level`, applies a `needs_sudo` heuristic, and
synthesizes a baseline `kind: "upgrade"` suggestion for every non-brew-health
tool (research-authored `edit` suggestions are additional to this, never a
replacement). Write `report.json` to the session dir, then:

```sh
python3 scripts/render.py /tmp/tool-update-review-{report_id}/report.json
```

which injects it into `assets/report-template.html` and writes `index.html`
plus a copy of `server.py` next to it. **Immediately after**, update
`research-status.json` to `phase: "ready"` — this is what the loading page's
poll is waiting for to trigger its reload into the now-real report.

Full assembly logic: `references/assembly.md`. Page/rendering spec:
`references/rendering-report.md`.

### 5. Wait for feedback.json

**Implement this wait as a single backgrounded shell command, not a
recurring model wakeup/poll loop.** A wakeup-loop re-invokes the model on a
timer, and each invocation past the ~5-minute prompt-cache TTL reprocesses
the full conversation at full (uncached) input price. Instead, run:

```sh
until [ -f "{session_dir}/feedback.json" ]; do sleep 5; done
```

as a backgrounded tool call and let the harness notify the session exactly
once, when the file actually appears. Do **not** block on server exit — the
server stays up after feedback. Timeout after 24 hours: offer to re-open the
page or abandon.

If `feedback.json` already exists when the server starts (e.g. session
crashed and restarted): validate `report_id`. Match → skip the wait, print
"Resuming from existing feedback." Mismatch → warn and ask: re-serve fresh
(rename stale file) or use stale feedback.

### 6. Write initial status.json

Immediately after detecting `feedback.json`, run `scripts/write_status.py
init {session_dir}` — it reads `feedback.json` + `report.json` and writes the
initial `status.json` atomically (one action per decision, plus synthetic
commit/push actions for repos that will get a commit).

Schema: `references/schemas.md`. Full mechanics: `references/apply.md`.

### 7. Apply accepted suggestions — per-action status updates

For each accepted suggestion in order:
1. Run `scripts/write_status.py set-action {session_dir} {id} running`,
   **then immediately issue the actual command/edit in the same
   uninterruptible step** — never write `"running"` and leave it as a
   separate turn before the real work starts. If something genuinely needs
   investigating mid-apply, finish or explicitly abandon the current
   action's command first — don't leave a `"running"` write dangling.
2. Apply the edit.
3. Run `scripts/write_status.py set-action {session_dir} {id} done|failed
   --note "<one-line outcome>" [--detail-file <path>]`.

One state transition per write (never skip `"running"`). Commit actions
follow the same pattern.

**While any action is `"running"` or any `pending_followups` entry is
awaiting a decision, keep a heartbeat going**: every ~20-30s run
`scripts/write_status.py touch {session_dir}` and `scripts/write_status.py
sync-turns {session_dir}` (same backgrounded-loop pattern as steps 5/10, not
a model-wakeup timer) so `written_at` doesn't go stale and new user turns on
open threads get picked up promptly.

Accepted suggestions split by `kind`:
- **`edit`** → applied directly to the macos-setup repo in-session; commit
  per CLAUDE.md. When the target file is managed by `tasks/projects.sh`, run
  `./setup.sh projects` after — the one documented exception to the
  never-run-setup-scripts rule.
- **`brew-health` remediations** → either a normal `edit`, or a structural
  `kind:"upgrade"` command (`trust`/`untap`/`link`, missing-dep install).
  Trust/untap/link/uninstall default `auto_runnable:false`; a plain missing-
  dep install is `auto_runnable:true`.
- **`upgrade`** → execution depends on `auto_runnable` and the
  `auto_run_upgrades` toggle: not-auto-runnable prints the command and polls
  for completion; auto-runnable-and-toggle-on runs it directly (askpass for
  `needs_sudo`), then polls to confirm the version landed either way.
- **`watch-item`** → not an edit/upgrade; a research-proposed standing
  concern (`references/research.md` §Watch Items (Proposing)). On accept,
  run `scripts/write_status.py add-watch-item` to append the proposal's
  `topic`/`note` to `watch-items.json` — no repo edit, no command runs.

`tool_comments`/`discuss` investigation never applies anything directly —
write a followup object and run `scripts/write_status.py add-followup`
instead, surfaced live in the Results view for an explicit decision. A
followup itself can also carry `kind: "watch-item"` when the session notices
a standing concern mid-apply (`origin: "agent_initiated"`) — same
accept-writes-to-`watch-items.json` behavior as the report-time proposal.

Full execution rules incl. askpass/auto_run_upgrades/bespoke-setup/
followups/watch-items: `references/apply.md`.

### 8. Push, then write terminal status.json

**Push any commits made in step 7 before writing the terminal status** —
this skill's `edit` commits are meant to land on origin, not sit
local-only. Order matters: `git -C dotfiles push origin master` first (if
the submodule got commits), then push macos-setup itself (which carries the
updated submodule pointer commit). On push failure, mark `"failed"` with the
git error, don't force-push, leave it for the user to resolve manually.

For each machine-local upgrade actually applied, write a changelog entry
file and run `scripts/write_status.py append-changelog {session_dir}
<entry-file>...`. Then run `scripts/write_status.py finalize {session_dir}
--phase done|discussing --recap-file <path>`.

Full detail: `references/apply.md`.

### 9. Surface discuss items in conversation

After writing terminal status, raise each `discuss` item in the session:
tool name, suggestion title, user's comment. Don't apply until the user
confirms. No status.json update needed — recap already mentions these.

### 10. Wait for Finish (POST /shutdown)

Same principle as step 5 — **backgrounded shell command, not a wakeup
loop.** The page's Finish button posts `/shutdown`; the server process then
exits and its `/health` endpoint stops responding. Run something like:

```sh
until ! curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:{port}/health | grep -q 200; do sleep 5; done
```

backgrounded, and let the harness notify once when it exits. Cap at 24 h: if
it hasn't fired by then, print a timeout message and proceed to teardown.

Endpoint detail: `references/server-and-session.md`.

### 11. Teardown

Kill the tailscale serve proxy (if started). Clean up any temp files. Session
complete.

## Notes

- The report page is fully offline (no CDN) and subpath-tolerant (works at
  `/` and behind the tailscale `/updates` mount alike). Never bind `0.0.0.0`
  or LAN interfaces without asking. Restarting the server mid-review is
  safe. Full detail: `references/server-and-session.md`.
- Pinned tools: never suggest unpinning unless the blocking reason is
  verified gone in the new version. Full rule: `references/research.md`.
- `auto_run_upgrades` defaults to `true`, but `auto_runnable: false` and the
  session's own toggle both always override it toward manual — never invent
  a third way around either. Full rule: `references/apply.md`.
