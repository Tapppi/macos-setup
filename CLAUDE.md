# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

macOS setup automation: Homebrew installs, shell dotfiles, system preferences, and development
tooling for bootstrapping a fresh Mac. Shell-script-based with no formal build system or test suite.

## Commands

```sh
# Lint (the only validation available — run before committing)
shellcheck setup.sh tasks/*.sh backup.sh restore.sh
shellcheck dotfiles/bootstrap.sh dotfiles/.config/bash/.functions

# Homebrew
brew bundle --file=Brewfile        # Install all packages
brew bundle check                  # Verify all packages installed

# Setup tasks (NEVER run these automatically — see rules below)
./setup.sh init|install|dotfiles|config|new_account|clean_account|init_ssh_1password|init_ssh_local
```

## Architecture

- **`setup.sh`** — Entry point that sources and dispatches to `tasks/*.sh`. Defines shared helpers
  (`p1`/`p2`/`p3` for colored output, `ask`/`ask2`/`run` for AppleScript dialogs) and the sudo
  keep-alive pattern.
- **`tasks/init.sh`** — System init: hostname, permissions, macOS updates, guest account, SSH/1Password setup, new account creation.
- **`tasks/install.sh`** — Software installation: Homebrew + Brewfile, Bash 5 as default shell, mise runtimes, dotfiles bootstrap, nnn plugins.
- **`tasks/config.sh`** — App configuration: `defaults write`, `PlistBuddy`, `duti` file associations, login items via AppleScript, VLC/Terminal customization, launches apps for first-run setup.
- **`tasks/macos.sh`** — macOS system defaults, keyboard/input sources, Finder/Dock preferences, and power-management settings.
- **`backup.sh` / `restore.sh`** — Backup/restore home directory files listed in `restore.bom` as timestamped `.tar.gz` archives. Requires Homebrew rsync.
- **`dotfiles/`** — **Git submodule** (`git@github.com:tapppi/dotfiles.git`). Rsynced to `~` via `bootstrap.sh`. Has its own git history on `master` branch. After changing dotfiles, commit inside `dotfiles/` then `git add dotfiles` in the parent repo.
- **`.extra`** — Git identity, personal aliases. **`.path`** — PATH extensions (GNU utils, Go, brew). **`.credentials.dist`** — Template for secrets.

## Rules

### Do Not Run Setup Scripts
**NEVER** run `setup.sh`, `tasks/*.sh`, or `dotfiles/bootstrap.sh` automatically.
These modify system configuration, install software, and require `sudo`.

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

### Brewfile
Group by category with comments, `brew`/`cask`/`mas` syntax, keep sorted within groups.

### Git Conventions
- Both repos use `master` branch (not `main`)
- Commit messages: imperative mood, concise
- GPG signing enabled via 1Password SSH agent

### Dotfiles Submodule Workflow
```sh
cd dotfiles
git add -A && git commit -m "Description"
git push origin master
cd ..
git add dotfiles && git commit -m "Update dotfiles"
```
