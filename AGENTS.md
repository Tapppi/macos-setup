# AGENTS.md - macos-setup

macOS setup automation repository: Homebrew installs, shell dotfiles, system
preferences, and development tooling for bootstrapping a fresh Mac.

## Repository Structure

```
macos-setup/
  setup.sh              # Entry point: ./setup.sh [init|install|dotfiles|config|...]
  Brewfile              # Homebrew bundle manifest (all apps/tools/casks)
  tasks/
    init.sh             # System init (hostname, users, SSH, Xcode)
    install.sh          # Software install (brew, mise runtimes, dotfiles, Claude Code and Codex plugins and context7, tapppi-skills and ikeh marketplaces, cursor-agent quarantine)
    config.sh           # App configuration (defaults, duti, login items)
    macos.sh            # macOS system defaults and power-management (separate task)
    projects.sh         # Per-project plugin enablement (tapppi-skills, ikeh marketplaces) + env from .tapppi-project manifests
  backup.sh             # Backup home dir files to tarball
  restore.sh            # Restore from backup tarball
  dotfiles/             # Git submodule -> github.com/tapppi/dotfiles (see below)
  .extra                # Personal bash config (git author, extra aliases)
  .path                 # PATH extensions (GNU utils, Go, brew)
  .credentials.dist     # Template for ~/.config/bash/.credentials (DO NOT commit filled version)
```

## What the tasks do

- **`setup.sh`** is the entry point. It sources and dispatches to `tasks/*.sh` and defines the
  shared helpers (`p1`/`p2`/`p3` for coloured output, `ask`/`ask2`/`run` for AppleScript dialogs)
  and the sudo keep-alive pattern.
- **`tasks/init.sh`**: hostname, permissions, macOS updates, guest account, SSH and 1Password
  setup, new-account creation.
- **`tasks/install.sh`**: Homebrew and the Brewfile, Bash 5 as the default shell, mise runtimes,
  the dotfiles bootstrap, nnn plugins, Claude Code and Codex marketplaces, plugins and context7
  (see *Where skills live* below). context7's key lives only in `~/.config/bash/.credentials`:
  when ctx7's skill or rule is missing, `install_claude_context7` runs
  `npx ctx7 setup --claude --oauth --yes`, which installs them under `~/.claude/` and writes no
  key; it then re-creates the user-scope MCP server as stdio with
  `CONTEXT7_API_KEY=${CONTEXT7_API_KEY:-}`. `install_codex_context7` gives Codex the same server
  with `env_vars = ["CONTEXT7_API_KEY"]`. An entry that still holds a key is migrated only when
  the key is exported or in `~/.config/bash/.credentials`, and an unreadable config is left
  alone. A hand-run `ctx7 setup` without `--oauth` writes the plain key back into
  `~/.claude.json`; never run `ctx7 setup --codex`, which also appends to the rendered
  `~/.codex/AGENTS.md`.
  `install_codex` installs Codex's user-wide plugins (`browser`, `frontend-design`,
  `ikeh-development`) and ikeh-development's Codex roles, and links ctx7's skill into
  `~/.agents/skills`. Codex comes from systems, so on a fresh Mac it may be skipped; the owner
  runs `./setup.sh codex` (it asks for `sudo`) after `nix run .#build-switch`, with every Codex
  process closed. `./setup.sh context7`, also an owner command, re-asserts only the two context7
  servers and ctx7's skill and rule.
  Plugins: `superpowers` is installed per repo from a
  workspace manifest, and `document-skills@anthropic-agent-skills` is off at user level (the
  claude.ai skill sync delivers newer docx, pdf, pptx and xlsx skills); there is no user-scope
  chrome-devtools MCP, since `browser@tapppi-skills` ships it per repo.
- **`tasks/config.sh`**: app configuration (`defaults write`, `PlistBuddy`, `duti` file
  associations, login items via AppleScript, VLC and Terminal customisation), and it launches
  apps for first-run setup. It does not apply macOS system defaults.
- **`tasks/macos.sh`**: macOS system defaults, keyboard and input sources, Finder and Dock
  preferences, power management. It is a separate task because it kills UI processes (Finder,
  Dock, ControlCenter).
- **`tasks/projects.sh`**: per-project setup from a workspace manifest. It scans `~/project` for
  gitignored `.tapppi-project.{json,yml,yaml}` manifests and, per workspace:
  1. enables each repo's named marketplace plugins at local scope with
     `claude plugin install --scope local`, which records `enabledPlugins` in that repo's
     gitignored `.claude/settings.local.json`. This is how third-party marketplace plugins (such
     as `frontend-design@claude-plugins-official`) and our own bundles published through a
     marketplace (such as `browser@tapppi-skills`, published by the `Tapppi/skills` repo's
     `.claude-plugin/marketplace.json`) get per-project scoping. Only the root `tapppi-skills`
     marketplace at `~/project/github/tapppi/skills` and the root `ikeh` marketplace at
     `~/project/github/mantadevoy/ikeh` (home of `ikeh-git@ikeh`) are registered;
  2. renders a `mise.local.toml` in the workspace directory whose `[env]` loads a local `0600`
     dotenv file through mise's `_.file`. mise walks up across git boundaries, so every repo
     under the workspace inherits the env, and a plain file read is instant, unlike a blocking
     `op read` in mise's per-`cd` evaluation;
  3. for a `jira` block, prints the one-time commands that write that dotenv file from
     1Password (`op read` into a `0600` file holding `JIRA_API_TOKEN` plus
     `JIRA_CONFIG_FILE` and `JIRA_AUTH_TYPE`) and run `jira init`.

  It is idempotent and never auto-run, and it does not link skills into repos (see *Where skills
  live*).
- **`backup.sh` / `restore.sh`**: back up and restore the home directory files listed in
  `restore.bom` as timestamped `.tar.gz` archives. They require Homebrew's rsync.
- **`.extra`** holds the git identity and personal aliases, **`.path`** the PATH extensions, and
  **`.credentials.dist`** the template for secrets.

## dotfiles/ Submodule

`dotfiles/` is a **separate git submodule** at `git@github.com:tapppi/dotfiles.git`.
See `dotfiles/README.md` for details. It has two sync directories:

- `home/` — rsynced to `~/` (files without XDG support):
  `.bash_profile`, `.bashrc`, `.claude/`, `.codex/`, `.cursor/`, `.hushlogin`, `.parallel/`
- `config/` — rsynced to `~/.config/` (XDG-compliant config):
  `bash/` (aliases, exports, functions, prompt), `btop/` (btop.conf + catppuccin theme),
  `claude/`, `containers/`, `cursor/`, `fd/`, `gh/`, `ghostty/`, `git/` (config + global
  ignore), `karabiner/`, `lazygit/`, `micro/`, `mise/`, `nnn/`, `opencode/`, `readline/inputrc`,
  `ripgrep/`, `terminal/`, `tmux/tmux.conf`, `curlrc`, `wgetrc`
- `agents/` — outside the synced trees: the sources of the user-level agent instructions
  (`core.md` plus one header per harness) and `render.sh`, which writes header plus core into
  `home/.claude/CLAUDE.md`, `home/.codex/AGENTS.md`, `config/opencode/AGENTS.md` and
  `home/.cursor/rules/00-environment.mdc`. Those outputs are generated: edit `agents/`, run
  `agents/render.sh`, and check with `agents/render.sh --check`. See `dotfiles/AGENTS.md`.
- `bootstrap.sh` - Two rsyncs: `home/` → `~/` and `config/` → `~/.config/`
- `keyboard-layouts/Finnish-prog.bundle` - Custom keyboard layout (copied separately)

### Tool-owned config is re-asserted, not vendored

`herdr` writes its own config into files the submodule tracks —
`herdr integration install claude` adds a hook script under `~/.claude/hooks/`
and a `SessionStart` entry to `~/.claude/settings.json`, and `herdr integration install codex`
adds `~/.codex/herdr-agent-state.sh` and `~/.codex/hooks.json` beside the tracked
`~/.codex/AGENTS.md`.

None of it is vendored in dotfiles. `install()` relies on ordering instead:
`install_dotfiles` runs first and `bootstrap.sh` overwrites the tracked files,
dropping the tool's keys; `install_herdr_integrations` runs afterwards and writes
them back. The live `~/.claude/settings.json` therefore has
a `hooks` key the tracked copy does not, and that is correct.

Both halves are easy to break:

- Keep these tasks **last in `install()`**, after `install_dotfiles`. Ahead of it,
  bootstrap wipes what they just wrote.
- Any dispatch running `install_dotfiles` must re-assert too — `./setup.sh
  dotfiles` calls `install_herdr_integrations` for this reason, or a
  dotfiles-only sync silently disables the integration.

Never fix a missing key by copying it into `dotfiles/home/`: that tracks a path
and payload the tool owns and rewrites between versions, going stale silently on
the next upgrade. Re-run the writing command instead (`./setup.sh herdr`).

`~/.claude/skills/` and `~/.claude/hooks/` are written by the tools that own
them, not by dotfiles, and bootstrap leaves both alone.

### Committing to the dotfiles submodule

The submodule has its own git history. Both repos use `master` branch. The parent repo tracks the
submodule commit pointer. After changing dotfiles, always update the parent repo reference with `git
add dotfiles`.

Run from the macos-setup repo root — never `cd` into the submodule, and never `git add -A`. Stage
the specific files you changed so unrelated work in the submodule's working tree isn't swept up.

```sh
git -C dotfiles add <specific paths>
git -C dotfiles commit -m "Description of change"
git -C dotfiles push origin master
git add dotfiles
git commit -m "Update dotfiles"
```

## Build / Run / Test Commands

This is a shell-script-based repo with no formal build system or test suite.

```sh
# Git hooks (run once after cloning — sets core.hooksPath to hooks/)
bash hooks/install.sh

# Full setup (requires sudo, interactive dialogs)
./setup.sh init     # System initialization
./setup.sh install  # Install all software
./setup.sh dotfiles # Bootstrap dotfiles only
./setup.sh herdr    # herdr's agent-state integrations only (also part of install)
./setup.sh codex    # Codex plugins, ikeh roles and context7 only (also part of install)
./setup.sh context7 # context7 for Claude Code and Codex only (also part of install)
./setup.sh config   # Apply app configuration (optionally named: config [name...])
./setup.sh macos    # Apply macOS system defaults (kills Finder, Dock, etc.)
./setup.sh projects # Per-project plugins + env from .tapppi-project manifests
reload              # Reloads all shell configurations

# Homebrew
brew bundle --file=Brewfile # Install all packages
brew bundle check           # Verify all packages installed

# Dotfiles bootstrap
./dotfiles/bootstrap.sh -f # Force-sync dotfiles to ~

# Lint shell scripts
shellcheck setup.sh tasks/*.sh backup.sh restore.sh
shellcheck dotfiles/bootstrap.sh dotfiles/config/bash/.functions
```

There is no test suite. Use `shellcheck` to validate shell scripts before committing.
**Never introduce new shellcheck warnings.** Run `shellcheck` on every modified `.sh` file
before committing.

**Bootstrap code must be portable; everything else can assume GNU.** This repo *installs* the
tooling, so its scripts can run on a freshly imaged Mac against the stock BSD userland, before the
Brewfile's `coreutils`/`findutils`/`gnu-sed`/`gawk`/`gnu-tar`/`grep`/`make` exist. Anything
reachable on that path must work under both — chiefly `sed -i.bak … && rm -f …bak` rather than
`sed -i ''` (BSD-only, GNU reads the empty string as a missing filename) or bare `sed -i`
(GNU-only); same care for `readlink -f`, `date`, `stat`, `sort`, `grep -P` and `find -printf`.
Once setup has run, GNU is first on PATH and non-bootstrap code can rely on it.

## Code Style

### EditorConfig (enforced via `.editorconfig`)

- **Indentation:** Tabs, width 2
- **Charset:** UTF-8
- **Line endings:** LF (Unix)
- **Final newline:** Always insert
- **Trailing whitespace:** Always trim
- **Markdown (`.md`, `.mdc`):** spaces with two-space list indentation, and prose wrapped at 100
  columns (`[*.{md,mdc}]` in `.editorconfig`). Tables, fenced code and a line that is one long
  link or code span may overflow. Fenced code carries a language.

### Shell Scripts

- Use `#!/usr/bin/env bash` shebang
- Quote all variable expansions: `"${variable}"` not `$variable`
- Use `[[ ]]` for conditionals (bash), `[ ]` only for POSIX compatibility
- Functions: `function_name() {` (no `function` keyword)
- Use lowercase with underscores for function/variable names: `install_brew`, `my_var`
- Use UPPERCASE for exported env vars: `EDITOR`, `GOPATH`
- Use `local` for function-scoped variables
- Group related code with comment headers: `# Define Function =name=`
- Use `p1`, `p2`, `p3` helpers for colored output (defined in setup.sh)
- Prefer `command -v` over `which` for checking command availability
- Validate prerequisites before proceeding (check for brew, mise, etc.)

### Formatting

- Print helpers: `p1` (bold blue heading), `p2` (blue subheading), `p3` (gray detail)
- Interactive dialogs use AppleScript via `ask`, `ask2`, `run` helpers in setup.sh
- Keep scripts idempotent: check if something exists before installing/configuring

### Error Handling

- Check for required tools before using them (`if ! command -v brew >/dev/null`)
- Use `return 1` in functions for errors (not `exit 1` which kills the shell)
- Redirect stderr: `2>/dev/null` for expected failures
- The sudo keep-alive pattern in setup.sh maintains elevated privileges

### Git Conventions

- Commits are SSH-signed through 1Password (`commit.gpgsign = true` in
  `dotfiles/config/git/config`); handle signing failures as the user-level instructions
  describe
- Default branch: `main` for new repos (set in gitconfig)
- This repo and dotfiles use `master` branch
- Commit messages: imperative mood, concise (e.g. "Add podman", "Update dotfiles")
- Use `diff-so-fancy` as pager (configured in gitconfig)
- URL shorthands: `gh:user/repo` expands to `git@github.com:user/repo`
- Useful aliases: `g s` (status), `g d` (diff). The `cam` alias stages every change, untracked
  files included (submodule pointers excepted), so do not use it here (see *Git workflows and
  pushing branches*)

### Brewfile

- Group by category with comments
- Use `brew "name"` for formulae, `cask "name"` for GUI apps, `mas "name", id:` for App Store
- Keep sorted within each category group
- Comment out temporarily unavailable or problematic packages
- `Brewfile` is the primary manifest (Apple Silicon). `intel.Brewfile` is a copy minus
  ARM-only packages (e.g. `krunkit`). Always edit `Brewfile` first, then replicate
  applicable changes to `intel.Brewfile`
- **Keg-only formulae need an explicit `.path` entry.** Homebrew does not symlink these
  into its `bin`, so a `brew "x"` line alone installs the formula but leaves it
  unreachable — this is how `curl` silently stayed Apple's older build and `psql` was
  missing entirely despite both being declared. Run `brew info <formula> | grep -i keg-only`
  when adding one, and if it should win, add `$brew_prefix/opt/<formula>/bin` to `.path`.
  Decide deliberately: `binutils` and `e2fsprogs` are keg-only for good reason, since they
  would shadow the system toolchain (`ar`/`nm`/`ld`, `uuidgen`)

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

### XDG Base Directory

`XDG_CONFIG_HOME=~/.config` is set in `dotfiles/config/bash/.exports`. Tools that support XDG
read config from `~/.config/`. Env var overrides (`INPUTRC`, `WGETRC`, `KUBECONFIG`,
`PGPASSFILE` and others) are also set there for tools that need explicit paths.

### Git Identity and Attribution

- **NEVER** add AI attribution to commits (no `Co-authored-by`, no
  `Ultraworked with`, no agent signatures in commit bodies or trailers).
  Commits must look like normal developer commits.
- **NEVER** change `user.name`, `user.email`, or any git identity
  configuration. The repository owner's identity must remain on all commits.

### Do Not Run Setup Scripts

- **NEVER** run `setup.sh`, `tasks/*.sh`, or `dotfiles/bootstrap.sh`
  automatically. These scripts modify system configuration,
  install software, and require `sudo`. The user must always run them manually.

**Narrow exception:** the `tool-update-review` skill's apply step may run `./setup.sh projects`,
and only that subcommand (never `install`, `macos`, `init` or bare `setup.sh`), when an accepted
suggestion edits a file that `tasks/projects.sh` manages (workspace `.tapppi-project.json`
manifests, rendered `mise.local.toml`). That task is idempotent, needs no `sudo` and touches no
system-wide state: it only re-enables plugins and re-renders workspace-local env config. The
exception is scoped to that one skill and that one subcommand and does not loosen the rule for
any other automation.

### Edit Dotfiles in the Submodule, Not in `~/`

**NEVER** edit files directly in `~/`, `~/.claude/`, `~/.codex/`, `~/.cursor/` or
`~/.config/`. Edit the source in the `dotfiles/` submodule (`home/` or
`config/`) and copy the changed file to its destination (`cp
dotfiles/home/.claude/foo ~/.claude/foo`). The home directory copies are
deployment targets — the dotfiles repo is the source of truth.

The user-level instruction files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.config/opencode/AGENTS.md`, `~/.cursor/rules/00-environment.mdc`) are generated, so their
source is one step further back, in `dotfiles/agents/`.

Live files with no dotfiles source (`~/.config/bash/.credentials`, `~/.codex/config.toml`,
`~/.claude.json`) are edited by the owner or through the owning tool's CLI, and are never copied
into dotfiles.

The exception is config a tool writes into a tracked path — see *Tool-owned
config is re-asserted, not vendored* above.

### Files to Never Commit

- `.credentials`, the live `~/.config/bash/.credentials` (use `.credentials.dist` as template)
- `.DS_Store`, `Thumbs.db`, `._*` (in .gitignore)
- Anything containing API keys, tokens, or passwords
- Backup tarballs

## Credentials and keys

Tool API keys live in the untracked, `0600` file `~/.config/bash/.credentials`, which
`dotfiles/config/bash/.bash_profile` sources first and which exports variables such as
`CONTEXT7_API_KEY`. Every interactive shell sees them; non-interactive shells (including
`bash -lc`), cron and GUI-launched apps do not. `.credentials.dist` is its template, and
`restore.bom` lists the file for `backup.sh`. Never commit or print a key, and never resolve a
variable into a tracked file.

## Where skills live

A skill belongs to the repo that uses it, committed at
`<repo>/.agents/skills/<bundle>/` with a committed *relative* symlink at
`<repo>/.claude/skills/<bundle>`. Both paths are needed: Claude Code reads only
`.claude/skills`, Codex only `.agents/skills`, Cursor and OpenCode both.

**[docs/skills.md](docs/skills.md)** has the rest — the three routes capability
arrives by, the bundle layout, why the symlink is relative, and the workspace
trust requirement.

Two marketplaces are registered on this machine: `tapppi-skills` and `ikeh`,
each a checkout under `~/project/github/` declared as a directory source.
`tasks/install.sh` registers them and installs no `ikeh` plugin: `claude plugin
install` is user-scope by default, which would enable `ikeh-git`'s hooks in every
repo. A repo enables `ikeh-git@ikeh` itself, through its committed
`enabledPlugins` or a `tasks/projects.sh` manifest.

## Cursor CLI (`cursor-agent`)

`install_cursor_agent()` in `tasks/install.sh` only clears the cask quarantine; all
config is dotfiles-managed.

**Quarantine must be re-cleared after every `brew upgrade --cask cursor-cli`.** A
fresh cask download re-quarantines the bundled `merkle-tree-napi` native binding,
and every `cursor-agent` invocation then dies with `library load disallowed by
system policy` (plus a Gatekeeper popup per run). Re-run
`xattr -dr com.apple.quarantine "$(brew --prefix)/Caskroom/cursor-cli"`.

Cursor reads much of the Claude Code setup natively — a repo's `AGENTS.md` and `CLAUDE.md`
(following its `@` imports), `.claude/skills/**/SKILL.md`, `.claude/agents/**`,
`~/.claude/commands/`, and `enabledPlugins`/hooks/`permissions` from `.claude/settings*.json` —
so `tasks/projects.sh` needs no Cursor-specific handling: a repo's committed `.claude/skills/`
and `.agents/skills/` are both discovered as-is. It does **not** read
`~/.claude/CLAUDE.md` (the generated `~/.cursor/rules/00-environment.mdc` carries the user-level
instructions instead) or Claude's `Bash(...)` permission entries (Cursor's shell tool is
`Shell(...)`).

See `dotfiles/AGENTS.md` for the two-directory config split — `cli-config.json` is
XDG-resolved, everything else is hardcoded to `~/.cursor/`.

## Tools & Runtime Environment

| Tool         | Purpose                 | Config location                            |
| ------------ | ----------------------- | ------------------------------------------ |
| mise         | Runtime version manager | `~/.config/mise/` (activated in bash)      |
| Homebrew     | Package manager         | `Brewfile`                                 |
| shellcheck   | Shell script linter     | (installed via brew)                       |
| ripgrep (rg) | Fast search             | `dotfiles/config/ripgrep/ripgreprc`        |
| fd           | Fast find               | `dotfiles/config/fd/ignore`                |
| nvim         | Default editor          | Separate nix flake config                  |
| opencode     | AI coding agent         | `dotfiles/config/opencode/` (`opencode.json`, `oh-my-openagent.json`, generated `AGENTS.md`) |
| codex        | AI coding agent (CLI)   | `dotfiles/home/.codex/AGENTS.md` (generated); `~/.codex/config.toml` is Codex-owned and untracked |
| cursor-agent | AI coding agent (CLI)   | `dotfiles/config/cursor/cli-config.json` (XDG-resolved) + `dotfiles/home/.cursor/` (mcp.json, generated rules/00-environment.mdc) |
| btop         | System resource monitor  | `dotfiles/config/btop/btop.conf`           |
| lazygit      | Git TUI                 | `dotfiles/config/lazygit/config.yml`       |
| tmux         | Terminal multiplexer    | `dotfiles/config/tmux/tmux.conf` (Ctrl+A)  |

## Git workflows and pushing branches

Git work here follows the `ikeh-git:git-workflows` skill from the `ikeh-git`
plugin, which `.claude/settings.json` enables; load it before the first commit.
The plugin's two guards run on every Bash call:

- **Push guard.** A push to `origin` of an agent branch — `agent/`, any
  conventional-commit prefix, `debug/` or `backup/`, the plugin's default list,
  since this repo sets no `branchPrefixes` — runs without a prompt, including
  `--force-with-lease --force-if-includes` until the branch's PR carries a review
  or comment. Every other push prompts: `master`, other destinations, plain
  `--force`/`-f`, deletes, another remote. Name the branch on each push. The
  `ask` rules on `master` still prompt for any push whose text contains `main`
  or `master`, so keep those words out of agent branch names.
- **Worktree guard.** Whole-tree staging (`git add -A`, `git commit -a` and their
  relatives) in the main checkout is denied, and so is any rebase of `master`.
  `requireWorktree` is off here, so a small change may still be committed from the
  main checkout by explicit path.

```bash
git push -u origin agent/<name>
git push --force-with-lease --force-if-includes origin agent/<name>
```

The guard decides how you may push, never whether: push only when the request
calls for it, and answer a prompt rather than reshaping the command until it
stops. The `ask` rules on `master` in `.claude/settings.json` are a backstop for
when the hook does not run, not a rule to reason from.

Committed enablement installs nothing. On a new machine, run
`claude plugin install ikeh-git@ikeh --scope local` in this repo; `tasks/install.sh`
registers the `ikeh` marketplace.
