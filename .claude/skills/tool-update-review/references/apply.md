# Apply — Steps 6–9 Mechanics

Table of contents:
- Overview
- Initializing status.json
- One-Transition-Per-Write Discipline
- Heartbeat and Turn Sync
- Executing `edit` Suggestions
- Brew-Health Remediation
- Skill-Drift Remediation
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
- Brewfile edits target `Brewfile` only. `intel.Brewfile` is out of this tool
  entirely (`references/research.md` §One Host, One Manifest) — a suggestion
  naming it never reaches apply, because `E-INTEL-BREWFILE` rejects it first.
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

## Skill-Drift Remediation

A `skill-drift` finding's suggestion (see `references/assembly.md`
§Skill-Drift Assembly for how it was built) is a `kind: "upgrade"` carrying
one structural command:

```sh
bash config/agent-skills/sync-upstream.sh    # run from the dotfiles repo root
```

**It is `auto_runnable: false`, always, and there is no configuration that
changes that.** Print the command and let the user run it; the session never
runs it itself, whatever `auto_run_upgrades` says (§Executing Upgrade
Suggestions' first gate). Three reasons, each sufficient on its own: the
script refuses to start on a dirty tree, so it needs a working tree the
session cannot promise; `git subtree pull` **writes commits** into the
dotfiles submodule; and it can conflict with a local customisation, which
needs the vendor's `CUSTOMISATION.md` in front of a human, not a merge
driver. Assembly enforces the same conclusion structurally — the suggestion
id ends `:sync`, so it can never pre-accept (`references/schemas.md` §1.6).

**`sync-upstream.sh` is *not* covered by the never-run-setup-scripts narrow
exception, and must not be added to it.** That exception
(§Executing `edit` Suggestions above, and macos-setup's `CLAUDE.md`) is
scoped to exactly one subcommand — `./setup.sh projects` — because it is
idempotent, needs no `sudo`, and only re-links skills and re-renders
workspace-local env config. `sync-upstream.sh` shares none of those
properties: it mutates vendored content and creates commits. It is also not
a `tasks/*.sh` script, so it was never inside the rule's letter — the point
here is that it is squarely inside its *spirit*, and "the rule doesn't
literally name it" is not a licence to run it. Widening the exception to
cover it would be the wrong fix for a case that is meant to stay manual.

**The two facts above are separate, and conflating them is the error to
avoid.** A skill-drift finding can also produce an ordinary `kind: "edit"`
— a `CUSTOMISATION.md` entry recording a local patch, a
`.claude-plugin/marketplace.json` fix after an upstream rename
(`references/research.md` §Skill-Drift Enrichment). Vendored skills *are*
files `tasks/projects.sh` manages: it symlinks them out of
`~/.config/agent-skills/` into each repo's `.claude/skills/`. So such an
edit legitimately reuses the **existing** `./setup.sh projects` exception,
exactly as any other projects-managed file does — run it after applying the
edit so the symlinks re-resolve. That is the existing exception being used
as written, not a new one, and it says nothing about `sync-upstream.sh`.

**The command is vendor-scoped; the findings are per skill.** `git subtree
pull` operates on a whole vendor prefix, so one run resolves **every**
drifted skill of that vendor — the suggestion's `label` says so ("updates
all 3 drifted anthropics skills"), and every affected skill's suggestion
carries the identical command. This is a real granularity mismatch, stated
rather than papered over. Two consequences at apply time:

- **Ask for it once per vendor, not once per accepted action.** When
  several accepted suggestions carry the same command, surface it on the
  first, poll for that one run, then resolve its siblings against it rather
  than printing the identical command again for each. Mark each sibling
  `"done"` with a note naming the run that covered it (`"Covered by the
  anthropics sync above"`), so the action list stays honest about what
  actually happened.
- **A rejected sibling does not stop the run.** If the user accepted one
  anthropics skill and rejected another, the sync still updates both —
  there is no per-skill sync in this vendoring model. Say so plainly in the
  action note instead of pretending the rejection was honoured; if the user
  genuinely wants one skill held back, that is a `discuss`, not something to
  simulate.

**Verification is not a version poll.** There is no installed version to
check (§Executing Upgrade Suggestions' polling loop assumes one). Confirm
instead that the sync landed: a new `git-subtree-split` squash commit in
`git -C dotfiles log`, or simply re-run
`python3 scripts/collect_skill_drift.py` and check that the vendor's
findings are gone. A `probe_error` finding has no remediation at all and
nothing to verify — it means the detector could not reach upstream, so the
right outcome is to note that and move on, never to guess a drift verdict.

Because the sync writes commits inside the submodule, everything after it
follows the ordinary dotfiles-submodule path: the parent repo needs
`git add dotfiles` + a pointer commit, and §Push and Terminal Status'
ordering (dotfiles first, then macos-setup) applies unchanged.

## Executing Upgrade Suggestions

Execution depends on two independent gates, both of which must pass for the
session to run anything itself:
1. `auto_runnable` on the suggestion is `true` (`false` = always manual — no
   toggle overrides this).
2. `feedback.json`'s report-level `auto_run_upgrades` toggle is `true` (page
   checkbox near Submit, **defaults to `true`** — an explicit user
   directive).

### Pinning the reviewed version (WP5/I2)

**An applied upgrade must install `target_version` — the version that was
reviewed — never whatever a package manager considers "latest" at apply
time.** A review that approves 1.2.3 and installs 1.4.0 has approved
nothing. **This is backstopped, not just documented**: `write_status.py
set-action ... done` refuses the transition — leaving the action exactly
as it was, nothing written — for any suggestion carrying a `target_version`
on a check_pin.py-checkable source (brew/cask/mise —
`assemble.PIN_CHECKABLE_SOURCES`) unless a matching `scripts/check_pin.py
verify` result was already recorded onto that action via `record-pin-check`
(below). **Be precise about what this is: a guardrail against a forgotten
step, not a boundary against a dishonest caller.** The recorded result is
whatever JSON the session hands `record-pin-check` — nothing in this skill
re-runs `check_pin.py` itself to confirm a result file is genuine, and
`record-pin-check` validates *shape* (§record-pin-check validation below),
not *truth*. What it reliably catches is the actual failure mode this
package was built for: a step silently skipped, a stale result reused, or
a preflight filed as a verify (`phase` mismatch, same section) — not a
session that deliberately fabricates a passing result. Never present this
gate to a user as proof an upgrade is safe; it is proof the documented
steps were actually run.

**Every tool's *baseline* `{source}:{name}:upgrade` suggestion** — not
every `kind: "upgrade"` suggestion generally — carries `target_version`
(`references/schemas.md` §1.6) and `version_pinned`.
A `brew-health` `:remediate` and a `skill-drift` `:sync` suggestion are
*also* `kind: "upgrade"` (`references/assembly.md` §Baseline Suggestion
Synthesis) but are never the baseline and never carry these fields —
`record-pin-check`/the gate above key off whether `target_version` is
present on the specific suggestion, never off `kind` alone, so a
remediation or a sync is never mistaken for a pinnable upgrade.
`scripts/check_pin.py` is the mechanism that holds the guarantee itself —
not a rule re-derived by prose each run:

**Branch on the suggestion's own `version_pinned`, never on `source` —**
mise is `version_pinned: true` in the common case, but `false` whenever
`name` already contains `@` (assemble.py's `upgrade_command_and_runnable`
refuses to pin a shape it has not established is well-formed), and that
mise-but-unpinned suggestion needs exactly the same preflight brew/cask
get, not the mise shortcut:

- **`version_pinned: true`**: `command` already pins the exact version as a
  CLI argument (`mise upgrade {name}@{target_version}` — confirmed against
  upstream mise docs, which rewrite the version-specific request rather
  than "upgrade within range"). Nothing upstream can drift out from under
  an argument, so there is no preflight query needed — but still run
  `scripts/check_pin.py preflight --source mise --name {name}
  --target-version {target_version} --pinned` (the `--pinned` flag is what
  tells `check_pin.py` to take the trivial-match shortcut instead of
  querying `mise outdated` — omitting it here would run a real, harmless
  query, but passing it wrong the other way, on an unpinned suggestion,
  would wrongly skip one) and record it, then go straight to running
  `command`.
- **`version_pinned: false`** (brew, cask, or a mise suggestion whose `name`
  blocked pinning): the resolved `command` always resolves to whatever the
  tap/mise currently calls latest — brew has no general
  `brew install name@version` for an arbitrary formula/cask, and an
  unpinned mise command has no version argument at all. **Run
  `scripts/check_pin.py preflight --source {source} --name {name}
  --target-version {target_version}` before running `command` at all**
  (no `--pinned`) — read-only, makes no changes — then record it:
  `scripts/write_status.py record-pin-check {session_dir} {action_id}
  preflight <result-file>` (the check's own JSON stdout, saved to a file
  first):
  - exit `0` (match) — the tap/mise still resolves to `target_version`;
    proceed.
  - exit `1` (mismatch) or `2` (indeterminate — formula/cask renamed,
    removed from its tap, moved, or an unresolvable mise id) — **refuse.**
    Do not run `command`. Mark the action `"failed"`, with the tool's
    `reason` string (from `check_pin.py`'s JSON output) as the note: it
    already names both the reviewed version and what actually resolves
    now, and tells the human to re-run the review or install manually.
    This is the answer to "the reviewed version is no longer available" —
    loud and specific, never a silent upgrade to whatever replaced it.
- **After `command` actually runs** (either path, auto or manual — see
  below), **run `scripts/check_pin.py verify --source {source} --name
  {name} --target-version {target_version}`, then `scripts/write_status.py
  record-pin-check {session_dir} {action_id} verify <result-file>`** before
  attempting `set-action ... done` at all — the gate above refuses that
  transition without it. Exit `1`/`2` from `check_pin.py verify` means the
  installed version is not the one that was reviewed — most likely a race
  where a new release landed in the (usually brief) window between preflight
  and the command finishing, or, for a pinned brew formula, the command was
  silently a no-op. **Mark the action `"failed"` with `check_pin.py`'s
  `reason`, never attempt `"done"`** — an upgrade that ran but landed on an
  unreviewed version is exactly the defect this whole mechanism exists to
  catch, and running is not itself proof it reached the right place; the
  gate would refuse the attempt anyway, but fail it deliberately rather than
  letting the refusal be the first sign something is wrong.
- **A baseline with no `target_version` at all** (`null`) means collection
  could not determine a `latest_version` for this tool — assembly already
  forces `auto_runnable: false` and a `manual_reason` explaining this
  (`references/assembly.md` §Baseline Suggestion Synthesis) rather than
  leaving a suggestion `check_pin.py verify` could never match. Treat it
  like any other manual-only baseline: print the reason, do not attempt to
  run or pin it, and it never reaches the `write_status.py` gate above
  either (no `target_version` ⇒ not gated — see `record-pin-check`'s
  docstring).
- `standalone` and `macos` never reach either check — both are always
  `auto_runnable: false` (`upgrade_command_and_runnable`), so they only ever
  go through the manual polling path below, which verifies them its own way
  (there is no single canonical `--version` output format to build a generic
  parser against, unlike brew/mise), and `write_status.py`'s gate does not
  apply to them either (`assemble.PIN_CHECKABLE_SOURCES` excludes both).

#### `record-pin-check` validation and the phase mismatch it refuses

A preflight and a verify can produce identical-looking JSON for the same
tool at the same version — both are just "does X equal target_version". If
`record-pin-check` trusted the phase named on its own command line, filing
a saved *preflight* result under `verify` (by mistake, or because the
upgrade was never actually run) would still open the `"done"` gate: nothing
about the JSON itself would say otherwise. So `check_pin.py`'s `emit()`
stamps `phase` and `checked_at` into the result **itself**, not left for
whoever saves the file to assert — and `record-pin-check {session_dir}
{action_id} {phase} <result-file>` refuses to file a result whose own
`"phase"` disagrees with the `{phase}` argument it is being recorded under.
It also validates the file's shape before writing anything — `source` in
`assemble.PIN_CHECKABLE_SOURCES`, `name` a non-empty string, `match` a
bool, and `target_version`/`observed_version`/`reason` each a string or
`null` — and refuses (loudly, nothing written) rather than store a
hand-built or truncated object that only fails later, silently, when the
gate's own field reads come back `None`. This closes a mistake, not an
attack: see the guardrail note at the top of this section for what the
mechanism does and does not prove.

### Executing the command

**Not auto-runnable** (`auto_runnable: false`, or the toggle off): if the
suggestion is `version_pinned: false` on a check_pin.py-checkable source
(brew, cask, or an unpinned mise id), run the preflight check above
**before** telling the user anything — a stale `command` that would install
a different version than was reviewed shouldn't be handed to a human to run
either. If it refuses, say so instead of printing the command. Otherwise print the
suggestion's `command` and ask the user to run it themselves, then poll for
completion every ~30s: `scripts/check_pin.py verify` for brew/cask/mise,
**recording each attempt with `record-pin-check` as described above** (same
exact-match semantics as above — **reaching a version is not enough; it
must be `target_version` exactly**, and a strictly newer version counts
as a mismatch, not a success), `<tool> --version` compared against
`target_version` for standalone — standalone has no `check_pin.py` support
and is not gated by `write_status.py`, so this comparison stays a judgment
call rather than a recorded, enforced one. Once verify reports a match,
mark `"done"` with the confirmed version; on a persistent mismatch use the
same `"failed"`-with-`reason` handling as the auto-run path. **Cap polling at
~20 minutes** — past that, leave the action `"running"` with a reminder note
rather than blocking the rest of the session; it can complete later and
Finish is still available. This loop assumes an installed version exists to
poll for; a `skill-drift` sync has none and is confirmed a different way
(§Skill-Drift Remediation).

**Auto-runnable and toggle on**: preflight-check and record it first as
described above (pass `--pinned` when `version_pinned: true` — this is the
one case, mise with a plain tool id, where the check is a trivial recorded
match rather than a real query; every other `version_pinned: false`
suggestion, mise included, gets the real one); refuse before running
anything if it mismatches. Otherwise run `command`
directly — plain subprocess for `needs_sudo: false`. For `needs_sudo: true`:

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
- Same `scripts/check_pin.py verify` check either way (§Pinning the
  reviewed version above) — running the command isn't itself proof it
  landed on the version that was reviewed.

**`auto_run_upgrades` defaults and overrides**: the toggle defaults to
`true` (explicit user directive), but two things always override it toward
manual regardless of the toggle's value: `auto_runnable: false` on the
suggestion itself, and the toggle being unchecked for a given session. Never
invent a third way around either — no `NOPASSWD` sudoers edits, no running
`setup.sh`/`tasks/*.sh` yourself beyond the one documented
`./setup.sh projects` exception (§Executing `edit` Suggestions above), no
running `sync-upstream.sh` yourself, which that exception does not cover
(§Skill-Drift Remediation above), no
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
