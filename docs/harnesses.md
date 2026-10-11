# Agent harness setup

How `tasks/install.sh` sets up the agent harnesses (Claude Code, Codex, OpenCode, Cursor and Pi)
and the tools that write into their config. What each harness's config files are, and which of
them dotfiles tracks, is in [dotfiles/docs/harnesses.md](../dotfiles/docs/harnesses.md). How
skills and plugins reach a repo is in [skills.md](skills.md). Each function's header comment in
`tasks/install.sh` has the specifics.

## Order inside `install()`

`install()` installs macOS software and the Brewfile, syncs dotfiles with `install_dotfiles`,
then installs mise runtimes and the document skills' tools. The harness steps run only after the
sync succeeded: the user-level skill link for Claude Code, then `install_claude_code`,
`install_codex`, `install_opencode`, `install_pi`, `install_cursor_agent`, and
`install_herdr_integrations` last. Codex, OpenCode and Pi come from systems, so on a fresh Mac
their steps skip until `nix run .#build-switch` has run; each has its own dispatch for the re-run.

| Dispatch | Runs | Notes |
| --- | --- | --- |
| `./setup.sh dotfiles` | `install_dotfiles`, then the re-assertions below | Stops if the sync fails |
| `./setup.sh herdr` | `install_herdr_integrations` | |
| `./setup.sh codex` | `install_codex` | Asks for `sudo`; refuses while Codex runs |
| `./setup.sh opencode` | `install_opencode` | Checks and hints only |
| `./setup.sh pi` | `install_pi` | |
| `./setup.sh context7` | `install_context7` | Both context7 servers and ctx7's skill and rule |

All of these are owner commands.

## Tool-owned config is re-asserted, not vendored

Some tools write their own config into files dotfiles tracks:
`herdr integration install claude` adds a hook script under `~/.claude/hooks/` and a
`SessionStart` entry to `~/.claude/settings.json`, and `herdr integration install codex` adds
`~/.codex/herdr-agent-state.sh` and `~/.codex/hooks.json`. dotfiles' harness doc lists the rest.

None of it is vendored in dotfiles. `install()` relies on ordering instead: `install_dotfiles`
runs first and `bootstrap.sh` overwrites the tracked files, dropping the tools' keys;
`install_herdr_integrations` runs afterwards and writes them back. The live
`~/.claude/settings.json` therefore has a `hooks` key the tracked copy does not, and that is
correct.

Both halves are easy to break:

- Keep these steps **last in `install()`**, after `install_dotfiles`. Ahead of it, the sync wipes
  what they just wrote.
- Any dispatch running `install_dotfiles` must re-assert too. `./setup.sh dotfiles` calls
  `install_herdr_integrations` for this reason, or a dotfiles-only sync silently disables the
  integration. It also re-writes the `my-machine-configs` link for Claude Code.

Never fix a missing key by copying it into `dotfiles/home/`: that tracks a path and payload the
tool owns and rewrites between versions, and it goes stale silently on the next upgrade. Re-run
the writing command instead (`./setup.sh herdr`).

## Claude Code

- `install_claude_marketplaces` registers the `tapppi-skills` and `ikeh` marketplaces, each a
  checkout under `~/project/github/` declared as a directory source. It installs no `ikeh`
  plugin: `claude plugin install` is user-scope by default, which would enable `ikeh-git`'s hooks
  in every repo. A repo enables `ikeh-git@ikeh` itself, through its committed `enabledPlugins`.
- `install_claude_code` installs the user-level plugins the tracked `settings.json` enables.
  Per-repo plugins, such as `browser`, are committed in each repo ([skills.md](skills.md)).
- The user-level `my-machine-configs` skill lives in `dotfiles/agents/skills/`. An absolute link
  under `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/` points at it through macos-setup's main
  worktree, never through the worktree the script runs in, and is skipped when the target is
  missing.

## context7

context7's API key lives only in `~/.config/bash/.credentials`.

- When ctx7's skill or rule is missing, `install_claude_context7` runs
  `npx ctx7 setup --claude --oauth --yes`, which installs them under `~/.claude/` and writes no
  key. It then re-creates the user-scope MCP server as stdio with
  `CONTEXT7_API_KEY=${CONTEXT7_API_KEY:-}`.
- `install_codex_context7` gives Codex the same server with `env_vars = ["CONTEXT7_API_KEY"]`.
- An entry that still holds a key is migrated only when the key is exported or in
  `~/.config/bash/.credentials`, and an unreadable config is left alone.
- A hand-run `ctx7 setup` without `--oauth` writes the plain key back into `~/.claude.json`.
  Never run `ctx7 setup --codex`, which also appends to the rendered `~/.codex/AGENTS.md`.
- Cursor has no context7 server.

## Codex

`install_codex` installs Codex's user-wide plugins and ikeh-development's Codex roles, keeps
`browser@tapppi-skills` installed but disabled at user level (repos enable it), and links
`my-machine-configs` and ctx7's `context7-mcp` skill into `${CODEX_HOME:-~/.codex}/skills/`.
Codex comes from systems, so on a fresh Mac the owner runs `./setup.sh codex` after
`nix run .#build-switch`, with every Codex process closed.

## OpenCode

`config/opencode/` in dotfiles carries the whole user-level config, so `install_opencode` only
checks: it warns about each `skills.paths` entry and local `plugin` path that is missing, and
prints the login commands when no provider is configured. It never edits OpenCode's files.

## Pi

`install_pi` adds the user-level packages with `pi install <dir>` (dotfiles `agents/` for
`my-machine-configs`, tapppi/skills `pi-config`, and `frontend-design` from Claude Code's
marketplace clone), links ctx7's skill into Pi's skills directory, and, on Pi 0.99 or later, adds
context7 to Pi's `mcp.json` when the entry is missing (context7 inherits `CONTEXT7_API_KEY` from
the environment). Each step is skipped, with a hint, when its source is missing. Pi also comes
from systems, so `./setup.sh pi` is its owner re-run.

## Cursor CLI (`cursor-agent`)

`install_cursor_agent` only clears the cask quarantine; all config is dotfiles-managed.

The quarantine must be re-cleared after every `brew upgrade --cask cursor-cli`. A fresh cask
download re-quarantines the bundled `merkle-tree-napi` native binding, and every `cursor-agent`
invocation then dies with `library load disallowed by system policy`, plus a Gatekeeper popup per
run. Re-run `xattr -dr com.apple.quarantine "$(brew --prefix)/Caskroom/cursor-cli"`.

Cursor reads much of the Claude Code setup natively, so `tasks/projects.sh` needs no
Cursor-specific handling. It does not read `~/.claude/CLAUDE.md` (the generated
`~/.cursor/rules/00-environment.mdc` carries the user-level instructions instead) or Claude's
`Bash(...)` permission entries (Cursor's shell tool is `Shell(...)`).

## Credentials and keys

Tool API keys live in the untracked, `0600` file `~/.config/bash/.credentials`, which
`dotfiles/config/bash/.bash_profile` sources first and which exports variables such as
`CONTEXT7_API_KEY`. Every interactive shell sees them; non-interactive shells (including
`bash -lc`), cron and GUI-launched apps do not. `.credentials.dist` is its template, and
`restore.bom` lists the file for `backup.sh`. Never commit or print a key, and never resolve a
variable into a tracked file.
