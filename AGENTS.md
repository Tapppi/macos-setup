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
    install.sh          # Software install (brew, mise runtimes, dotfiles)
    config.sh           # App configuration (defaults, duti, login items)
    macos.sh            # macOS system defaults and power-management (separate task)
  backup.sh             # Backup home dir files to tarball
  restore.sh            # Restore from backup tarball
  dotfiles/             # Git submodule -> github.com/tapppi/dotfiles (see below)
  .extra                # Personal bash config (git author, extra aliases)
  .path                 # PATH extensions (GNU utils, Go, brew)
  .credentials.dist     # Template for secret env vars (DO NOT commit filled version)
```

## dotfiles/ Submodule

`dotfiles/` is a **separate git submodule** at `git@github.com:tapppi/dotfiles.git`.
See `dotfiles/README.md` for details. It has two sync directories:

- `home/` — rsynced to `~/` (files without XDG support):
  `.bash_profile`, `.bashrc`, `.claude/`, `.hammerspoon/`, `.hushlogin`, `.parallel/`
- `config/` — rsynced to `~/.config/` (XDG-compliant config):
  `bash/` (aliases, exports, functions, prompt), `git/` (config + global ignore),
  `tmux/tmux.conf`, `readline/inputrc`, `curlrc`, `wgetrc`, `ghostty/`, `karabiner/`,
  `lazygit/`, `micro/`, `mise/`, `nnn/`, `opencode/`, `ripgrep/`, `fd/`, `terminal/`
- `bootstrap.sh` - Two rsyncs: `home/` → `~/` and `config/` → `~/.config/`
- `keyboard-layouts/Finnish-prog.bundle` - Custom keyboard layout (copied separately)

### Committing to the dotfiles submodule

The submodule has its own git history. Both repos use `master` branch. The parent repo tracks the
submodule commit pointer. After changing dotfiles, always update the parent repo reference with `git
add dotfiles`. Commit changes inside `dotfiles/`.

```sh
cd dotfiles
git add -A && git commit -m "Description of change"
git push origin master
cd ..
git add dotfiles && git commit -m "Update dotfiles"
```

## Build / Run / Test Commands

This is a shell-script-based repo with no formal build system or test suite.

```sh
# Full setup (requires sudo, interactive dialogs)
./setup.sh init     # System initialization
./setup.sh install  # Install all software
./setup.sh dotfiles # Bootstrap dotfiles only
./setup.sh config   # Apply app configuration
./setup.sh macos    # Apply macOS system defaults (kills Finder, Dock, etc.)
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

## Code Style

### EditorConfig (enforced via `.editorconfig`)

- **Indentation:** Tabs, width 2
- **Charset:** UTF-8
- **Line endings:** LF (Unix)
- **Final newline:** Always insert
- **Trailing whitespace:** Always trim

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

- Check for required tools before using them (`if ! which brew >/dev/null`)
- Use `return 1` in functions for errors (not `exit 1` which kills the shell)
- Redirect stderr: `2>/dev/null` for expected failures
- The sudo keep-alive pattern in setup.sh maintains elevated privileges

### Git Conventions

- GPG signing is enabled (`commit.gpgsign = true` in dotfiles/config/git/config)
- Default branch: `main` for new repos (set in gitconfig)
- This repo and dotfiles use `master` branch
- Commit messages: imperative mood, concise (e.g. "Add podman", "Update dotfiles")
- Use `diff-so-fancy` as pager (configured in gitconfig)
- URL shorthands: `gh:user/repo` expands to `git@github.com:user/repo`
- Useful aliases: `g s` (status), `g d` (diff), `g cam "msg"` (add all + commit)

### Brewfile

- Group by category with comments
- Use `brew "name"` for formulae, `cask "name"` for GUI apps, `mas "name", id:` for App Store
- Keep sorted within each category group
- Comment out temporarily unavailable or problematic packages
- `Brewfile` is the primary manifest (Apple Silicon). `intel.Brewfile` is a copy minus
  ARM-only packages (e.g. `krunkit`). Always edit `Brewfile` first, then replicate
  applicable changes to `intel.Brewfile`

### Git Identity and Attribution

- **NEVER** add AI attribution to commits (no `Co-authored-by`, no
  `Ultraworked with`, no agent signatures in commit bodies or trailers).
  Commits must look like normal developer commits.
- **NEVER** change `user.name`, `user.email`, or any git identity
  configuration. The repository owner's identity must remain on all commits.
- **Exception — unattended workflows**: If the agent must commit in an
  unattended context (e.g. CI, cron, background automation) where the
  owner's signing key is unavailable, it may temporarily set a placeholder
  identity to allow the commit to proceed. In this case:
  1. Clearly inform the user that commits were made with a placeholder identity.
  2. Note that these commits need `git rebase` / `git commit --amend` to
     restore the correct author before pushing to a shared remote.

### Do Not Run Setup Scripts

- **NEVER** run `setup.sh`, `tasks/*.sh`, or `dotfiles/bootstrap.sh`
  automatically. These scripts modify system configuration,
  install software, and require `sudo`. The user must always run them manually.

### Files to Never Commit

- `.credentials` (use `.credentials.dist` as template)
- `.DS_Store`, `Thumbs.db`, `._*` (in .gitignore)
- Anything containing API keys, tokens, or passwords
- Backup tarballs

## Tools & Runtime Environment

| Tool         | Purpose                 | Config location                            |
| ------------ | ----------------------- | ------------------------------------------ |
| mise         | Runtime version manager | `~/.config/mise/` (activated in bash)      |
| Homebrew     | Package manager         | `Brewfile`                                 |
| shellcheck   | Shell script linter     | (installed via brew)                       |
| ripgrep (rg) | Fast search             | `dotfiles/config/ripgrep/ripgreprc`        |
| fd           | Fast find               | `dotfiles/config/fd/ignore`                |
| nvim         | Default editor          | Separate nix flake config                  |
| opencode     | AI coding agent         | `dotfiles/config/opencode/opencode.json`   |
| lazygit      | Git TUI                 | `dotfiles/config/lazygit/config.yml`       |
| tmux         | Terminal multiplexer    | `dotfiles/config/tmux/tmux.conf` (Ctrl+A)  |
