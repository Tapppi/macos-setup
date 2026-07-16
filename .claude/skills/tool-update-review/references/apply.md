# Apply — Steps 6–9 Mechanics

Table of contents:
- Overview
- Initializing status.json
- One-Transition-Per-Write Discipline
- Heartbeat and Turn Sync
- Executing `edit` Suggestions
- Brew-Health Remediation
- Executing Upgrade Suggestions
- Executing `watch-item` Suggestions
- Bespoke Setup Execution
- Tool Comments and Discuss
- Agent-Initiated Followups
- Watch Items (Writing)
- Turn-Based Threads (Session Side)
- Push and Terminal Status
- Surfacing Discuss Items
- Teardown

## Overview

Steps 6–9 cover everything from the first `status.json` write through pushing
commits and closing out the run: initializing the live action list, applying
each accepted suggestion with a per-action status write, handling
investigation threads (`tool_comments`/`discuss`/agent-initiated followups),
and finally pushing + writing the terminal status. `references/schemas.md`
§status.json has the full field-by-field schema this section assumes;
`references/server-and-session.md` covers the endpoints (`/status`,
`/followup`, `/shutdown`) and consolidated failure-mode table this step
relies on. Server-side/session-side failure handling (crash recovery, stale
`written_at`, POST 409 on a second tab, server gone at Finish) lives in
`references/server-and-session.md` §Failure Modes — not duplicated here.

## Initializing status.json

Immediately after detecting `feedback.json` (step 5), run
`scripts/write_status.py init {session_dir}` — it reads `feedback.json` +
`report.json` and writes the initial `status.json` atomically. Mechanics
worth knowing when debugging this step (not just "run the script"):

- **One action per suggestion in `report.json`**, iterated in suggestion
  order — not just the ids present in `feedback.json`'s `decisions` map.
  The front end only gates Submit on incompatible-severity suggestions, so a
  lower-severity suggestion can be legitimately submitted with **no decision
  at all** and simply never appear in `decisions`; treating an absent id as
  "skip the action" would silently drop it from both the action list and
  `summary.undecided`. An absent decision gets `state: "skipped"` like an
  explicit reject.
- accepted/discuss → `state: "pending"`; rejected/undecided → `state:
  "skipped"` immediately (they will never run).
- **One investigation action per `discuss` decision that has a comment**
  (a bare discuss with nothing written has nothing to investigate) and **one
  investigation action per `tool_comments` entry** — both labeled
  `"Investigate: {tool/suggestion} — {comment}"`, `state: "pending"`, no
  `decision`. See §Tool Comments and Discuss below for what happens to these.
- **Synthetic `commit:dotfiles` / `commit:macos-setup` / `push:dotfiles` /
  `push:macos-setup` actions** — only created for repos that will actually
  get a commit, inferred from whether any accepted suggestion's
  `target_files` fall under `dotfiles/` vs. elsewhere. Never render a no-op
  action for a repo nothing touched.

This replaces hand-typed atomic-write code that used to get re-derived every
run.

## One-Transition-Per-Write Discipline

**Every `status.json` write covers exactly one state transition** — never
write `pending → running` and `running → done` in the same atomic op. The
page needs to actually observe `"running"` (to show the spinner) before it
flips to a terminal state; collapsing both into one write means the user
never sees the in-progress state at all, even though the work genuinely took
time. This applies to every action, including synthetic commit/push actions,
and is the same discipline `research-status.json`'s per-group
`pending → running → done/failed` writes follow (see
`references/server-and-session.md`).

## Heartbeat and Turn Sync

**While any action is `"running"` or any `pending_followups` entry is
awaiting a decision, keep a heartbeat going** rather than only writing
`status.json` at hard state transitions. A long-running background command
(a slow cask installer, a poll loop for a manual upgrade) can leave
`written_at` stale for minutes with nothing actually wrong, and the page's
staleness heuristic (`references/server-and-session.md`) will falsely read
that as "session may have stopped." Every ~20–30s during these stretches,
run:

- `scripts/write_status.py touch {session_dir}` — bumps `written_at` only,
  no other field changes.
- `scripts/write_status.py sync-turns {session_dir}` — merges any new turns
  from `followup_turns.json` into the matching `pending_followups` entry's
  `turns[]` or an action's `thread[]` (see §Turn-Based Threads below), so a
  turn the user submits gets picked up promptly rather than only at the next
  action's natural status write.

**Implement this the same way as the steps 5/10 waits** — a cheap
backgrounded shell loop, never a model-wakeup timer. A short-interval
wakeup would reprocess the full conversation on every tick (expensive, and a
cache miss past the ~5-minute prompt-cache TTL). The loop should only
actually notify/wake the session when something meaningful happens — a new
turn appears, or the long-running command it's watching completes — same
pattern as the feedback.json/Finish-button waits (`references/
server-and-session.md`), just covering this mid-apply window too.

After `sync-turns` reports a new turn, act on it per its `decision`: accept →
apply now, reject → mark resolved, discuss/comment → append an agent turn.
`write_status.py` has no subcommand for "answer a turn" — the answer itself
(an edit, a retry, a reply) is exactly the judgment call this session makes,
not something to script.

## Executing `edit` Suggestions

Applied directly to the macos-setup repo in-session:
- Dotfiles paths go through the submodule workflow (commit inside
  `dotfiles/` first, then `git add dotfiles` + commit in the parent repo —
  see the macos-setup `CLAUDE.md`'s Dotfiles Submodule Workflow section).
- Brewfile edits need per-host decisions (`Brewfile` vs. `intel.Brewfile`).
- Commit per `CLAUDE.md`: specific paths staged, imperative-mood messages,
  no AI attribution.
- **When a target file is managed by `tasks/projects.sh`** (workspace
  `.tapppi-project.json` manifests, rendered `mise.local.toml`, per-repo
  skill symlinks): apply the edit, then run `./setup.sh projects` — the one
  documented, narrow exception to `CLAUDE.md`'s never-run-setup-scripts
  rule, scoped to that single idempotent subcommand — instead of
  hand-simulating what that task does.
- The `systems` repo (nix) is out of scope for direct edits — surface nix
  findings as notes only.

## Brew-Health Remediation

A `brew-health` finding's suggestion is one of two shapes (see
`references/assembly.md` §Brew-Health Assembly for how it was built):
- A normal `kind: "edit"` (e.g. migrating a deprecated cask to its
  replacement in the Brewfile) — applied and committed exactly like any
  other edit, above.
- A `kind: "upgrade"` command that is **structural** rather than a version
  bump: `brew trust`/`untap`/`link`, `brew install <missing-dep>`, or
  `brew install --cask <replacement> && brew uninstall --cask <old>`.

**Trust, untap, link, and uninstall default to `auto_runnable: false`** —
print the command and let the user run it themselves; never auto-trust a
tap, untap, or uninstall an app, since those are security/availability
decisions, not upgrades. A plain `brew install <missing-dep>` is
`auto_runnable: true` and follows the normal upgrade execution path below.
**Never run a structural brew change on the strength of a default
remediation alone** — only an explicit accept, same as anything else.

## Executing Upgrade Suggestions

Execution depends on two independent gates, both of which must pass for the
session to run anything itself:
1. `auto_runnable` on the suggestion is `true` (`false` = always manual — no
   toggle overrides this).
2. `feedback.json`'s report-level `auto_run_upgrades` toggle is `true` (page
   checkbox near Submit, **defaults to `true`** — an explicit user
   directive).

**Not auto-runnable** (`auto_runnable: false`, or the toggle off): print the
suggestion's `command` and ask the user to run it themselves, then poll for
completion by checking the installed version every ~30s (`mise current
<tool>` for mise, `brew list --versions <name>` for brew/cask, `<tool>
--version` for standalone). Once the installed version reaches
`latest_version`, mark `"done"` with the confirmed version. **Cap polling at
~20 minutes** — past that, leave the action `"running"` with a reminder note
rather than blocking the rest of the session; it can complete later and
Finish is still available.

**Auto-runnable and toggle on**: run `command` directly — plain subprocess
for `needs_sudo: false`. For `needs_sudo: true`:

- The **session** (the orchestrating Claude Code agent, in-conversation —
  never `server.py`, which never executes suggestions) is what runs the
  command.
- brew formulae, mise, and standalone CLIs never need elevation — only a
  handful of casks that ship a `pkg` installer (e.g. Wireshark's ChmodBPF
  helper) do, and Homebrew invokes that `sudo` internally. **Never wrap the
  outer `brew`/`mise` command itself in `sudo`** — Homebrew refuses to run
  as root.
- The mechanism: `scripts/askpass.sh` pops a native macOS password dialog
  via `osascript ... with hidden answer` and prints the entered text to
  stdout — this is what `SUDO_ASKPASS` points at. Run the command with
  `SUDO_ASKPASS=<path to askpass.sh>` and `ASKPASS_PROMPT="<the exact
  command>"` set in its environment. If the command's own internal `sudo`
  call can't find a controlling terminal, it falls back to the askpass
  helper automatically — standard macOS `sudo` behavior for GUI-launched
  installers, not something this skill invents. The password is typed by
  the user into that native dialog and never touches the session's own code
  path, the HTTP server, or any file.
- **This can't be verified as bulletproof for every possible cask/tool** —
  behavior depends on the invoked tool's own internal elevation call
  correctly detecting no-tty. Bound the command with a **~90s no-output
  timeout** (not just total wall-clock, since some installers are
  legitimately slow) — if it hangs, kill it, mark the action `"failed"` with
  a note to run `command` manually in a real terminal instead, and move on
  rather than blocking the rest of the apply pass.
- Never persist, cache, log, or extend-timestamp any credential. No
  `NOPASSWD` sudoers edits, no `sudo -v` timestamp tricks. Each privileged
  command gets its own fresh native prompt.
- Same "poll to confirm the version landed" verification either way —
  running the command isn't itself proof it worked.

**`auto_run_upgrades` defaults and overrides**: the toggle defaults to
`true` (explicit user directive), but two things always override it toward
manual regardless of the toggle's value: `auto_runnable: false` on the
suggestion itself, and the toggle being unchecked for a given session. Never
invent a third way around either — no `NOPASSWD` sudoers edits, no running
`setup.sh`/`tasks/*.sh` yourself beyond the one documented
`./setup.sh projects` exception (§Executing `edit` Suggestions above), no
auto-applying anything derived from a free-text comment without a fresh
accept/reject (§Tool Comments and Discuss below).

**Caveat**: re-running this repo's `install_mise_runtimes` task (`mise
install`) does *not* upgrade an already-installed runtime pinned to
`"latest"` — mise resolves `"latest"` once, at first install. Only a bare
`mise upgrade [tool]` re-resolves it, which is exactly what the synthesized
`command` runs — don't substitute `install.sh` for it.

## Executing `watch-item` Suggestions

A `kind: "watch-item"` suggestion (`references/schemas.md` §1.7,
`references/research.md` §Watch Items (Proposing)) is a research-proposed
standing concern, accepted/rejected/discussed through the exact same
`feedback.json` `decisions` map as any other suggestion — nothing special
about how the decision arrives, only about what accepting it does:

- **Accept**: run `scripts/write_status.py add-watch-item --tool-id
  {tool_id} --topic "{watch_topic}" --note "{watch_note}"` (writes the
  `{topic, note, added_at}` entry into `watch-items.json` under this tool's
  id — see §Watch Items (Writing) below for the file mechanics shared with
  the other two ways a watch item gets written). No repo edit, no command,
  no commit action gets synthesized for this suggestion — `target_files` is
  always `[]`, so `write_status.py init`'s dotfiles/macos-setup commit
  detection never fires for it. Mark the action `"done"` with a note like
  `"Added watch item: {watch_topic}"`.
- **Reject**: already handled generically at `init` time (rejected →
  `state: "skipped"` immediately) — no file write, nothing further to do.
- **Discuss**: normal discuss handling (§Tool Comments and Discuss below) —
  never writes `watch-items.json` on the strength of a discuss alone.

## Bespoke Setup Execution

For tools with setup logic beyond a plain package command (podman's
`install_podman_intel` and similar): the baseline `upgrade` suggestion still
just runs the plain package-manager command as described above — bespoke
setup doesn't change that; `auto_runnable: false` is the fallback only for
the rare case where no command at all is safely testable for that tool, not
the default for "has bespoke setup."

What bespoke setup *does* change is research-time (step 3): the research
subagent audits the `tasks/*.sh` function's own code against the run's
changelog and, if a fix is needed, empirically tests the specific affected
command(s) non-destructively before proposing a normal, tested `kind: "edit"`
suggestion targeting that file. See `references/research.md` §Bespoke Setup
Testing for the full testing mandate and worked example — that's research's
job, not apply's. By the time apply sees this suggestion, it's a normal
`edit`: applying it is exactly the same as any other accepted edit
(§Executing `edit` Suggestions above) — nothing about a bespoke-setup origin
changes how it gets applied or committed. Never construct or run a
`setup.sh`/`tasks/*.sh` invocation yourself beyond the one documented
`./setup.sh projects` exception and the scoped, tested commands research
already verified.

## Tool Comments and Discuss

**Investigating a `tool_comments` entry or a `discuss` decision's comment
never applies anything directly** — this holds even when `auto_run_upgrades`
is on, since a free-text comment triggering unreviewed changes defeats the
entire point of a review. If the investigation concludes a concrete change
is warranted:

1. Write a followup object to a JSON file (same shape as any other
   suggestion — `id` like `{source}:{name}:from-comment-{slug}`,
   `origin: "user_comment"`).
2. Run `scripts/write_status.py add-followup {session_dir} <file>`.
3. This surfaces live in the Results view for an explicit accept/reject/
   discuss — never auto-applied.

The investigation action itself (labeled `"Investigate: {tool} —
{comment}"`, created at init — see above) stays `"running"` until that
follow-on decision lands, then reflects the outcome (`"done, no change
needed"` / `"done, see new suggestion {id}"` / etc.).

## Agent-Initiated Followups

**Create a followup yourself, don't just raise it in conversation, when
something during apply needs an explicit decision beyond plain
success/fail** — e.g. an accepted cask upgrade completing but its installer
triggering an unrelated Gatekeeper quarantine popup. Add a
`pending_followups` entry the same way as §Tool Comments and Discuss above,
with `origin: "agent_initiated"` instead of `"user_comment"`. The Results
view should be the single place someone checks for "things needing my
decision" — not split across the page and the chat transcript.

**A standing concern noticed mid-apply** (not from research, and not from a
user comment either — the session itself, applying something, notices a
pattern worth watching on future runs) is the same mechanism with
`kind: "watch-item"` instead of `"edit"`/`"upgrade"`: a `pending_followups`
entry, `origin: "agent_initiated"`, carrying `watch_topic`/`watch_note`
instead of `target_files`/`command`/`diff_preview`. Rendered identically to
any other followup (`references/rendering-results.md` §Turn-Based
Threads — `origin` is metadata, not a different card shape). On a user turn
accepting it (via `sync-turns`, §Heartbeat and Turn Sync above), write the
`watch-items.json` entry the same way as §Executing `watch-item` Suggestions
above, then re-run `scripts/write_status.py add-followup` with the updated
object (`resolution: "applied"`, the accepting turn appended) — the same
"answer a turn is a judgment call, not a scriptable transition" mechanism
§Turn-Based Threads (Session Side) below already documents, just applied to
this specific kind of followup.

## Watch Items (Writing)

`watch-items.json` (see `references/research.md` §Watch Items (Reading) for
the file shape and the read side that consumes it on future runs) gets a new
entry from **exactly one action**, regardless of which of three paths
proposed it — `scripts/write_status.py add-watch-item --tool-id {tool_id}
--topic "{topic}" --note "{note}"` (atomic write, no `session_dir` argument:
this file is machine-global, not scoped to one review session). Never write
the file any other way (no hand-rolled `jq`/Python edit) — always go through
this subcommand so every write follows the same atomic pattern the rest of
this skill's state files use.

The three paths that can trigger it, and the one thing they have in common
— **never written on the strength of a proposal alone; always a separate,
explicit accept**:

1. **A research-proposed `kind: "watch-item"` suggestion, accepted** (the
   normal case going forward — `references/research.md` §Watch Items
   (Proposing), execution mechanics in §Executing `watch-item` Suggestions
   above).
2. **An agent-initiated followup proposing one mid-apply, accepted** (the
   session itself notices a standing concern while applying something —
   §Agent-Initiated Followups above).
3. **A comment expressing a standing concern, investigated** (the original,
   pre-suggestion path: if a `tool_comments` entry or `discuss` comment
   expresses a standing concern rather than a one-off question — a
   preference like "tell me if this tool's shell integration changes, ever"
   shouldn't only live in this conversation's memory — investigate per
   §Tool Comments and Discuss above, then call `add-watch-item` directly as
   part of that investigation's outcome. Do this **in addition to (never
   instead of)** surfacing any concrete one-off suggestion the comment also
   warrants — a standing concern does not replace acting on the immediate
   ask. A one-time question ("does this release fix the bug I hit last
   week?") doesn't need one; use judgment rather than creating a watch item
   for every comment.)

Paths 1 and 2 are the preferred, review-gated way to propose a watch item
now — surfaced explicitly in the review UI for accept/reject, same as any
other suggestion — rather than the session silently deciding one is
warranted from a comment. Path 3 remains for the free-text case where
nothing already produced a formal proposal to accept.

## Turn-Based Threads (Session Side)

Followups (and a `"failed"` action's own debug thread) are multi-turn: the
heartbeat loop's `sync-turns` call (§Heartbeat and Turn Sync above) surfaces
new user turns on any open thread; act on them promptly — accept → apply
now, reject → mark resolved, discuss/comment → append an agent turn
answering or asking back. This applies **including on a thread already
marked `resolution: "applied"` or `"rejected"`** — an out-of-turn addition
can still arrive later (e.g. a Gatekeeper quarantine popup noticed after the
original followup's suggestion was already applied) and threads are never
permanently closed to new input.

Appending an agent turn (e.g. onto a `"failed"` action's own thread) is done
via `scripts/write_status.py set-action ... --thread-turn-file <file>` —
the file's JSON turn object gets `turn`/`author: "agent"`/`at` filled in
automatically if omitted. There is no separate subcommand for appending an
agent turn onto a `pending_followups` entry outside of `add-followup`;
answering a turn is a judgment call the session makes directly, not a
scriptable transition. The rendering half of this mechanism (thread cards,
Send button, decoupled decision-select-from-submit) and the exact Turn
object shape live in `references/schemas.md` §status.json and
`references/rendering-results.md` §Turn-Based Threads — this section only
covers what the session *does* when a new turn shows up.

## Push and Terminal Status

**Push any commits made in step 7 before writing the terminal status** —
this skill's `edit` commits are meant to land on origin, not sit local-only
(explicit user directive; a standing exception to the usual "confirm before
push" caution, scoped to this skill's own apply step). **Order matters**:
`git -C dotfiles push origin master` first (if the submodule got commits),
then push macos-setup itself (which carries the updated submodule pointer
commit) — pushing macos-setup first would publish a submodule pointer
referencing a dotfiles commit the remote doesn't have yet. The
`push:dotfiles`/`push:macos-setup` actions already exist from step 6's
`init` (only for repos that got a commit — no no-op action to skip). On push
failure (diverged remote, network, auth): mark `"failed"` with the git error
in `detail`, **don't retry destructively (no force-push)**, leave it for the
user to resolve manually — this is exactly the kind of hard-to-reverse-if-
wrong action that stops rather than guesses.

For each machine-local upgrade actually applied, write a changelog entry
file:

```
## {date}
### {source}: {name} {old}→{new}
{one to three sentence summary}
```

and run `scripts/write_status.py append-changelog {session_dir}
<entry-file>...` — one call, one or more entry files — which appends them to
`status.json`'s `changelog_entries` **and**
`${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md` (creates
the dir/file if absent) atomically, so this can't drift out of sync the way
two separate hand-written appends could.

Then run `scripts/write_status.py finalize {session_dir} --phase
done|discussing --recap-file <path>` (write the recap text — applied edits
with commit hashes *and push status*, failed actions with remediation hints
for the ones that stay unresolved, rejected/undecided items — to a file
first). It sets `done`, computes `summary` from the actions' own
decision/state fields, and writes `written_at`, all atomically. **Recap does
not need to mention discuss items' comments or followup/failed-action
counts** — those live in `pending_followups`/the Results view's unified
header and per-action threads now (`references/rendering-results.md`),
computed live on every poll, so a frozen recap sentence about them can't go
stale.

## Surfacing Discuss Items

After writing terminal status, raise each `discuss` item in the session:
tool name, suggestion title, user's comment. Don't apply until the user
confirms. No `status.json` update needed for this step — recap already
mentions these.

## Teardown

Kill the tailscale serve proxy (if started). Clean up any temp files.
Session complete. See `references/server-and-session.md` §Waiting for
Finish for the wait this follows.
