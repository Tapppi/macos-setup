# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

macOS setup automation: Homebrew installs, shell dotfiles, system preferences, and development
tooling for bootstrapping a fresh Mac. Shell-script-based with no formal build system or test suite.

## Commands

```sh
# Lint (the only validation available — run before committing)
shellcheck setup.sh tasks/*.sh backup.sh restore.sh
shellcheck dotfiles/bootstrap.sh dotfiles/config/bash/.functions

# Homebrew
brew bundle --file=Brewfile        # Install all packages
brew bundle check                  # Verify all packages installed

# Git hooks (run once after cloning — sets core.hooksPath to hooks/)
bash hooks/install.sh

# Setup tasks (NEVER run these automatically — see rules below)
./setup.sh init|install|dotfiles|config|macos|new_account|clean_account|init_ssh_1password|init_ssh_local|projects
```

## Architecture

- **`setup.sh`** — Entry point that sources and dispatches to `tasks/*.sh`. Defines shared helpers
  (`p1`/`p2`/`p3` for colored output, `ask`/`ask2`/`run` for AppleScript dialogs) and the sudo
  keep-alive pattern.
- **`tasks/init.sh`** — System init: hostname, permissions, macOS updates, guest account, SSH/1Password setup, new account creation.
- **`tasks/install.sh`** — Software installation: Homebrew + Brewfile, Bash 5 as default shell, mise runtimes, dotfiles bootstrap, nnn plugins, Claude Code MCP servers/plugins. context7 is set up via `npx ctx7 setup --claude` (OAuth login for higher rate limits; writes the API-keyed MCP server into `~/.claude.json` and installs a ctx7-owned skill/rule under `~/.claude/` that `dotfiles/bootstrap.sh` excludes from its `--delete` mirror).
- **Cursor CLI (`cursor-agent`)** — Set up by `install_cursor_agent()` in `tasks/install.sh`, which
  only clears the cask quarantine; all config is dotfiles-managed. `cursor-agent` reads much of the
  Claude Code setup natively (repo `CLAUDE.md`, `.claude/skills/**/SKILL.md`, `.claude/agents/**`,
  `~/.claude/commands/`, plus `enabledPlugins`, hooks and `permissions` from `.claude/settings*.json`),
  so `tasks/projects.sh` needs no Cursor-specific handling — the skills it links into each repo's
  `.claude/skills/` are picked up as-is. It does **not** read `~/.claude/CLAUDE.md` (ported to
  `dotfiles/home/.cursor/rules/*.mdc`) or Claude's `Bash(...)` permission entries (Cursor's shell tool
  is `Shell(...)`, so they load but never match). **Quarantine must be re-cleared after every
  `brew upgrade --cask cursor-cli`** — a fresh cask download re-quarantines the bundled
  `merkle-tree-napi` native binding and *every* `cursor-agent` invocation then dies with
  `library load disallowed by system policy`. See `dotfiles/CLAUDE.md` for the two-directory config
  split (`cli-config.json` is XDG-resolved, everything else is hardcoded to `~/.cursor/`).
- **`tasks/config.sh`** — App configuration: `defaults write`, `PlistBuddy`, `duti` file associations, login items via AppleScript, VLC/Terminal customization, launches apps for first-run setup. Does not apply macOS system defaults (use `./setup.sh macos` separately).
- **`tasks/macos.sh`** — macOS system defaults, keyboard/input sources, Finder/Dock preferences, and power-management settings. Run as a separate task because it kills UI processes (Finder, Dock, ControlCenter).
- **`tasks/projects.sh`** — Per-project setup from a workspace manifest. Scans `~/project` for gitignored `.tapppi-project.{json,yml,yaml}` manifests; per workspace it (1) symlinks each repo's named skills (resolved from `~/.config/agent-skills/`) into that repo's `.claude/skills/` — per-repo because Claude Code only discovers skills up to a repo's git root, so a workspace-level link is invisible inside a repo; (2) enables each repo's named marketplace plugins at local scope via `claude plugin install --scope local`, which records `enabledPlugins` in that repo's gitignored `.claude/settings.local.json` — this is how third-party marketplace plugins (e.g. `frontend-design@claude-plugins-official`) and our own skills wrapped as plugins (e.g. `gke-basics@tapppi-skills`, see `dotfiles/config/agent-skills/.claude-plugin/marketplace.json`) get the same per-project scoping as raw skills, since Claude Code has no `enabledSkills` toggle for unpackaged skills; any local marketplace found under `~/.config/agent-skills/` is auto-registered so its plugins resolve by name; (3) renders a `mise.local.toml` in the workspace dir whose `[env]` loads a local `0600` dotenv file via mise's `_.file` (mise walks up across git boundaries, so every repo under the workspace inherits the env; a plain file read is instant and never blocks the shell, unlike a blocking `op read` in mise's per-`cd` eval); and (4) for a `jira` block prints the one-time commands to write that dotenv file from 1Password (`op read` into a `0600` file holding `JIRA_API_TOKEN` plus `JIRA_CONFIG_FILE`/`JIRA_AUTH_TYPE`) and run `jira init`. Used to scope skills/tooling to specific projects rather than globally. Idempotent; never auto-run.
- **`backup.sh` / `restore.sh`** — Backup/restore home directory files listed in `restore.bom` as timestamped `.tar.gz` archives. Requires Homebrew rsync.
- **`dotfiles/`** — **Git submodule** (`git@github.com:tapppi/dotfiles.git`). Has two sync dirs:
  `home/` rsynced to `~/` (non-XDG files: `.claude/`, `.cursor/`, `.hammerspoon/`, etc.) and
  `config/` rsynced to `~/.config/` (XDG config: `btop/`, `git/`, `opencode/`, `tmux/`, etc.).
  Has its own git history on `master` branch. After changing dotfiles, commit inside `dotfiles/`
  then `git add dotfiles` in the parent repo.
- **`.extra`** — Git identity, personal aliases. **`.path`** — PATH extensions (GNU utils, Go, brew). **`.credentials.dist`** — Template for secrets.

## Rules

### Do Not Run Setup Scripts
**NEVER** run `setup.sh`, `tasks/*.sh`, or `dotfiles/bootstrap.sh` automatically.
These modify system configuration, install software, and require `sudo`.

**Narrow exception**: the `tool-update-review` skill's apply step may run
`./setup.sh projects` (and *only* that subcommand — never `install`, `macos`,
`init`, or bare `setup.sh`) when an accepted suggestion edits a file that
`tasks/projects.sh` manages (workspace `.tapppi-project.json` manifests,
rendered `mise.local.toml`, per-repo skill symlinks). That task is idempotent,
requires no `sudo`, and touches no system-wide state — it only re-links
skills and re-renders workspace-local env config. This exception is scoped
to that one skill and that one subcommand; it does not loosen the rule for
any other automation.

### Edit Dotfiles in the Submodule, Not in `~/`
**NEVER** edit files directly in `~/`, `~/.claude/`, `~/.cursor/`, or `~/.config/`. Always edit the source in the
`dotfiles/` submodule (`home/` or `config/` directories) and then copy the changed file to its
destination (e.g., `cp dotfiles/home/.claude/foo ~/.claude/foo`). The home directory copies are
deployment targets — the dotfiles repo is the source of truth.

### Git Identity and Attribution
- **NEVER** add AI attribution to commits (no `Co-authored-by`, no agent signatures).
  Commits must look like normal developer commits.
- **NEVER** change `user.name`, `user.email`, or any git identity configuration.
- **Exception**: In unattended contexts where the signing key is unavailable, a placeholder identity
  may be used temporarily — inform the user and note that commits need rebase/amend before pushing.

### Files to Never Commit
`.credentials`, `.DS_Store`, `Thumbs.db`, `._*`, API keys/tokens/passwords, backup tarballs.

## Code Style

### EditorConfig (enforced)
Tabs (width 2), UTF-8, LF line endings, trim trailing whitespace, insert final newline.

### Shell Scripts
- Shebang: `#!/usr/bin/env bash`
- Quote all variable expansions: `"${variable}"`
- Use `[[ ]]` for conditionals, `local` for function-scoped variables
- Function style: `function_name() {` (no `function` keyword)
- Naming: lowercase_underscores for functions/variables, UPPERCASE for exported env vars
- Group with comment headers: `# Define Function =name=`
- Use `p1`/`p2`/`p3` helpers for colored output
- Prefer `command -v` over `which`
- Keep scripts idempotent: check before installing/configuring
- Use `return 1` in functions (not `exit 1`)
- **Never introduce new shellcheck warnings.** Run `shellcheck` on every modified `.sh` file before committing
- **Bootstrap code must be portable; everything else can assume GNU.** This repo *installs* the
  tooling, so its scripts can run on a freshly imaged Mac against the stock BSD userland, before
  the Brewfile's `coreutils`/`findutils`/`gnu-sed`/`gawk`/`gnu-tar`/`grep`/`make` exist. Anything
  reachable on that path must work under both — chiefly `sed -i.bak … && rm -f …bak` rather than
  `sed -i ''` (BSD-only, GNU reads the empty string as a missing filename) or bare `sed -i`
  (GNU-only); same care for `readlink -f`, `date`, `stat`, `sort`, `grep -P` and `find -printf`.
  Once setup has run, GNU is first on PATH and non-bootstrap code can rely on it

### Brewfile
Group by category with comments, `brew`/`cask`/`mas` syntax, keep sorted within groups.
`Brewfile` is the primary manifest (Apple Silicon). `intel.Brewfile` is a copy minus ARM-only
packages (e.g. `krunkit`). Always edit `Brewfile` first, then replicate applicable changes to
`intel.Brewfile`.

**Keg-only formulae need an explicit `.path` entry.** Homebrew does not symlink these into its
`bin`, so a `brew "x"` line alone installs the formula but leaves it unreachable — this is how
`curl` silently stayed Apple's older build and `psql` was missing entirely despite both being
declared. Run `brew info <formula> | grep -i keg-only` when adding one, and if it should win, add
`$brew_prefix/opt/<formula>/bin` to `.path`. Decide deliberately: `binutils` and `e2fsprogs` are
keg-only for good reason, since they would shadow the system toolchain (`ar`/`nm`/`ld`, `uuidgen`).

### PATH Layering
`.path` is sourced last by `dotfiles/config/bash/.bash_profile`, and `activate_mise` runs after
that, so a configured login shell resolves in this order:

```text
mise shims  →  Homebrew (gnubin + keg-only)  →  Nix (/run/current-system/sw/bin)  →  macOS
```

Homebrew therefore wins over the nix-darwin config in `tapppi/systems` for anything both provide.
**To hand a tool over to Nix, remove it from the Brewfile and `brew uninstall` it — do not reorder
PATH.** That is how `nvim` resolves to the nixCats build; an earlier attempt to prepend the Nix
profile instead was reverted because it also shadowed Homebrew's `bash`, `sh` and `zsh`.

### Git Conventions
- Both repos use `master` branch (not `main`)
- Commit messages: imperative mood, concise
- GPG signing enabled via 1Password SSH agent

### Dotfiles Submodule Workflow
Run from the macos-setup repo root — never `cd` into the submodule, and never
`git add -A`. Stage the specific files you changed so unrelated work in the
submodule's working tree isn't swept up.

```sh
git -C dotfiles add <specific paths>
git -C dotfiles commit -m "Description"
git -C dotfiles push origin master
git add dotfiles
git commit -m "Update dotfiles"
```

### XDG Base Directory
`XDG_CONFIG_HOME=~/.config` is set in `dotfiles/config/bash/.exports`. Tools that support XDG read
config from `~/.config/`. Env var overrides (`INPUTRC`, `WGETRC`, `KUBECONFIG`, `PGPASSFILE`, etc.)
are also set there for tools that need explicit paths.

## Pushing branches

Pushes to agent branches (`agent/` or any conventional-commit prefix) on `origin` are pre-approved here and run without prompting,
including `--force-with-lease --force-if-includes` for rebase and squash cleanups:

```bash
git push -u origin agent/<name>
git push origin agent/<name>
git push --force-with-lease --force-if-includes origin agent/<name>
```

Name the branch every time: a bare `git push` prompts even after `-u` has set
the upstream, because the guard approves a destination it can read rather than
one it would have to infer.

The lease stops being pre-approved once the branch has an open PR carrying a
review or comment: cleaning up your own history is fine, rewriting what someone
has already read is not. It must be paired with `--force-if-includes` — the guard
refuses a bare lease, because a background fetch refreshes the remote-tracking
ref and degrades it into a plain force.

Everything else prompts, and that is not only `master`: any destination outside the
prefixes above — `release/1.2`, `hotfix-3` — prompts too, as do plain
`--force`/`-f`, deletes and a different remote. `HEAD` is resolved to the branch
you are on and judged by that same prefix rule. A push routed through
`--git-dir`, `--work-tree`, `-c` or another option that redirects where it lands
always prompts; plain `git -C <path> push` is evaluated exactly like a direct push.

The guard reads one unquoted `git push` at a time. Quoting the branch
(`git push origin "agent/$name"`) or chaining onto it (`git push … && gh pr create`)
puts the command past what it will parse, so it prompts instead of pre-approving —
keep the push on its own line, unquoted, and follow up in a separate command.

The guard decides how you may push, never whether — push only when the request
calls for it, and never restructure a command to dodge a prompt.

Enforced by `.claude/hooks/git-push-guard.sh`, registered in `.claude/settings.json`
with the allowed prefixes — both committed, so the rule and the permission travel
with the repo rather than living on one machine. That file also carries an `ask`
rule on `master` destinations, so the default branch stays protected even when the
hook cannot run — on a machine without `jq`, for instance, where the guard prompts
and says why rather than going quiet.
