# Conventions

Style and layout rules for this repo's scripts, the Brewfile and the shell environment it sets up.
`.editorconfig` holds the formatting rules, and `shellcheck` is the linter.

## Shell scripts

- `#!/usr/bin/env bash` shebang.
- Quote every expansion: `"${variable}"`, not `$variable`.
- `[[ ]]` for conditionals; `[ ]` only where POSIX compatibility is needed.
- Functions as `function_name() {`, without the `function` keyword. Lowercase with underscores for
  function and variable names (`install_brew`, `my_var`), uppercase for exported variables
  (`EDITOR`, `GOPATH`), and `local` for function-scoped variables.
- Group related code under comment headers: `# Define Function =name=`.
- Output through the helpers `setup.sh` defines: `p1` (bold blue heading), `p2` (blue subheading),
  `p3` (grey detail). Interactive dialogs use the AppleScript helpers `ask`, `ask2` and `run`.
- Keep scripts idempotent: check whether something exists before installing or configuring it.
- Check prerequisites before using them (`if ! command -v brew >/dev/null`), and prefer
  `command -v` to `which`.
- In functions, fail with `return 1`, never `exit 1`, which kills the calling shell. Send expected
  failures' stderr to `/dev/null`.
- `setup.sh` keeps `sudo` alive for the duration of a run.

### Bootstrap code must be portable

This repo installs the tooling, so its scripts can run on a freshly imaged Mac against the stock
BSD userland, before the Brewfile's `coreutils`, `findutils`, `gnu-sed`, `gawk`, `gnu-tar`, `grep`
and `make` exist. Anything reachable on that path must work under both: chiefly
`sed -i.bak … && rm -f …bak` rather than `sed -i ''` (BSD-only; GNU reads the empty string as a
missing filename) or bare `sed -i` (GNU-only). Take the same care with `readlink -f`, `date`,
`stat`, `sort`, `grep -P` and `find -printf`. Once setup has run, GNU is first on PATH, and
non-bootstrap code can rely on it.

## Brewfile

- Group by category with comments, and keep each group sorted.
- `brew "name"` for formulae, `cask "name"` for GUI apps, `mas "name", id:` for the App Store.
- Comment out packages that are temporarily unavailable or problematic.
- `Brewfile` is the primary manifest (Apple Silicon). `intel.Brewfile` is a copy without the
  ARM-only packages (such as `krunkit`). Edit `Brewfile` first, then replicate the applicable
  changes to `intel.Brewfile`.
- **Keg-only formulae need an explicit `.path` entry.** Homebrew does not link them into its
  `bin`, so a `brew "x"` line alone installs the formula but leaves it unreachable. That is how
  `curl` silently stayed Apple's older build and `psql` was missing although both were declared.
  Run `brew info <formula> | grep -i keg-only` when adding one, and if it should win, add
  `$brew_prefix/opt/<formula>/bin` to `.path`. Decide deliberately: `binutils` and `e2fsprogs` are
  keg-only for good reason, since they would shadow the system toolchain (`ar`, `nm`, `ld`,
  `uuidgen`).

## PATH layering

`.path` is sourced last by `dotfiles/config/bash/.bash_profile`, and mise activates after that, so
a configured login shell resolves commands in this order:

```text
mise shims  →  Homebrew (gnubin + keg-only)  →  Nix (/run/current-system/sw/bin)  →  macOS
```

Homebrew therefore wins over the nix-darwin config in `Tapppi/systems` for anything both provide.
**To hand a tool over to Nix, remove it from the Brewfile and `brew uninstall` it; do not reorder
PATH.** That is how `nvim` resolves to the nixCats build. An earlier attempt to prepend the Nix
profile instead was reverted, because it also shadowed Homebrew's `bash`, `sh` and `zsh`.

## XDG base directories

`XDG_CONFIG_HOME=~/.config` is set in `dotfiles/config/bash/.exports`, so tools that support XDG
read their config from `~/.config/`. The same file sets explicit overrides (`INPUTRC`, `WGETRC`,
`KUBECONFIG`, `PGPASSFILE` and others) for tools that need a path.
