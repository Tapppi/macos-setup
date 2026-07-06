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
before step 6 (writing the first `status.json`).

## Workflow

### 1. Collect candidates

Run `scripts/collect.sh` from the macos-setup repo root (pass the Brewfile
path if elsewhere). It emits machine context plus outdated tools from four
sources: Brewfile-manifested brew/cask packages (transitive deps excluded,
pinned formulae included — a pin usually marks a *known* incompatibility worth
re-checking, not a tool to skip), mise runtimes, standalone CLIs, and
`softwareupdate -l` entries (macOS system/app updates — `source: "macos"`).

If the user scoped the request ("just podman", "only claude"), filter the
candidate list before researching.

**Also check repo freshness** (design.md C.5): `git fetch origin --quiet`
in macos-setup and in `dotfiles/`, then compare HEAD against the upstream
for ahead/behind counts. Read-only — never auto-pull/merge. If either repo
is behind, say so up front (recommendations grounded in a stale checkout
can be wrong) but don't block the review on it. Pull `git log --oneline
-20` from both repos into `repo_context` for research context (A.1).

### 2. Research each tool (parallel subagents)

Spawn one research subagent per tool, all in one turn. Each gets: the tool's
id/name/source/versions, the machine context (arch matters — ARM-only
dependencies are incompatibilities on x86_64, not footnotes), the paths it
may scan for relevancy, and the two repos' `recent_commits` from step 1's
`repo_context`. Each returns a partial Tool object per `references/design.md`
A.1: `headliners`, typed `links`, `relevancy`, `suggestions`, `config_status`.

Research quality bar:
- **Headliners**: ≤6 bullets covering the whole current→latest range, not just
  the newest release. Skim actual release notes/changelogs — don't guess from
  version numbers.
- **Links**: always the canonical changelog for the version range; add release
  pages for majors and official blog posts when they exist. Every relevancy
  finding and suggestion should be traceable to a link.
- **Relevancy is the point of this skill.** Scan the user's setup repos —
  `~/project/github/tapppi/macos-setup` (Brewfile, intel.Brewfile, tasks/,
  dotfiles/ submodule with shell/git/tmux/Claude configs) and
  `~/project/github/tapppi/systems` (NixOS flake) — plus machine facts, for
  places the tool is configured or its changed behavior lands. Severity:
  `incompatible` (won't work here — e.g. new major requires Apple Silicon on
  an Intel machine) > `warning` (breaks a config/workflow the user has) >
  `notable` (touches something they use) > `info`. Cite evidence as
  `file:line` paths.
- **Suggestions authored here are always `kind: "edit"`** (a concrete
  Brewfile/dotfiles/config change): target file, rationale, motivating link,
  and a short `diff_preview`. Only suggest what the changelog actually
  motivates. No edit suggestion is fine — most tools just get headliners. Do
  **not** author the plain "upgrade this tool" suggestion — that's a
  `kind: "upgrade"` suggestion synthesized mechanically in step 3 for every
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
  diff between the old fix and the new change.
- **Bespoke `tasks/*.sh` setup** (design.md C.6): if the tool has setup logic
  beyond a plain package command (grep `tasks/install.sh`/`tasks/config.sh`
  for a function referencing the tool), check whether this run's changelog
  changes something that function relies on. If so, test the specific
  affected command non-destructively (throwaway-named resource, dry-run
  flag, `--help` diff — never the user's real state, never the whole
  function or `./setup.sh install`) and propose a tested `kind: "edit"`
  suggestion, not a manual punt. The baseline `upgrade` suggestion (added in
  step 3) stays `auto_runnable: true` regardless — only mark it `false` if
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

### 3. Assemble and render

Merge collect + research into the report object (design.md A.1): compute
`summary` counts, ensure suggestion ids are unique
(`{source}:{name}:{slug}`), verify evidence paths exist (warn, don't drop).
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
plus a copy of `server.py` next to it.

### 4. Serve

Start the server loopback-only (extra `--bind` listeners only if the user
asks):

```sh
python3 {session_dir}/server.py {session_dir} &
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

Schema and write pattern: `references/results-view-design.md` A. Immediately
after detecting `feedback.json`, write `{session_dir}/status.json`
atomically (`.tmp` + `os.replace`):
- `phase: "applying"`, `done: false`, `started_at`/`written_at`: now
- `actions`: one entry per decision in suggestion order — accepted/discuss:
  state `"pending"`; rejected/undecided: state `"skipped"`. Add one
  investigation action per `tool_comments` entry and per `discuss` decision
  with a comment (see step 7). Append synthetic `commit:dotfiles` /
  `commit:macos-setup` actions (only for repos that will actually get a
  commit) and, after them, `push:dotfiles` / `push:macos-setup` (step 8) —
  all `"pending"`.
- `recap`, `changelog_entries`, `summary`: empty/zero.

### 7. Apply accepted suggestions — per-action status updates

For each accepted suggestion in order:
1. Write status.json: action state → `"running"`, `started_at: now`.
2. Apply the edit.
3. Write status.json: action state → `"done"` or `"failed"`, `finished_at:
   now`, `note: <one-line outcome>`, `detail: [<last ≤10 lines>]`.

One state transition per write (never skip `"running"`). Commit actions
follow the same pattern.

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
    What it *does* change is research (step 2, design.md C.6): audit the
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
a concrete change is warranted, add it as a *new* suggestion in `pending`
state — same shape as any other, `id` like
`{source}:{name}:from-comment-{slug}` — surfaced live in the Results view
for an explicit accept/reject/discuss, not auto-applied. The investigation
action itself (label it "Investigate: {tool} — {comment}") stays `"running"`
until that follow-on decision lands, then reflects the outcome ("done, no
change needed" / "done, see new suggestion {id}" / etc.). This applies even
when `auto_run_upgrades` is on — a free-text comment triggering unreviewed
changes defeats the entire point of a review.

### 8. Push, then write terminal status.json

**Push any commits made in step 7 before writing the terminal status** —
this skill's `edit` commits are meant to land on origin, not sit local-only
(explicit user directive; a standing exception to the usual "confirm before
push" caution, scoped to this skill's own apply step). Order matters:
`git -C dotfiles push origin master` first (if the submodule got commits),
then push macos-setup itself (which carries the updated submodule pointer
commit). Add a `push:dotfiles` / `push:macos-setup` action per repo that
actually got a commit (skip the action entirely for a repo with nothing to
push — don't render a no-op). On push failure (diverged remote, network,
auth): mark `"failed"` with the git error in `detail`, don't retry
destructively (no force-push), leave it for the user to resolve manually —
this is exactly the kind of hard-to-reverse-if-wrong action that stops
rather than guesses.

After push, write the final status.json atomically:
- `phase: "done"` (or `"discussing"` if discuss items remain — then
  transition to `"done"` after the discuss pass), `done: true`
- `recap`: applied edits with commit hashes *and push status*, failed
  actions with remediation hints, discuss items with user comments,
  rejected/undecided items
- `changelog_entries`: one entry per machine-local upgrade applied (date
  section, tool, source, old→new, why); also append each to
  `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md`
  (create dir+file if absent — never skip this, it is the audit trail and
  what the next run's `config_status` check reads)
- `summary`: counts from action outcomes; `written_at`: now

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
