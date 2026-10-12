# AGENTS.md - macos-setup

Setup automation for a Mac: the Homebrew bundle, `setup.sh` with its `tasks/*.sh`, and the
`dotfiles` submodule synced into `~`. It is the legacy half of the machine configuration and is
migrating into [Tapppi/systems](https://github.com/Tapppi/systems), which already owns a narrow
set (its `AGENTS.md` lists what). This repo is public: keep private values and secrets out of it.

## Layout

- `setup.sh`: the entry point. `./setup.sh <task>` dispatches to `tasks/` and defines the shared
  helpers. Its dispatch `case` names every command with a one-line description, and the
  [README](README.md) describes each task.
- `tasks/`: `init.sh` (system init), `install.sh` (software, runtimes, the document skills'
  tools, dotfiles sync, agent harnesses), `config.sh` (app configuration), `macos.sh` (macOS
  defaults; a separate task because it kills Finder, Dock and ControlCenter), `projects.sh`
  (per-workspace plugins and env from `.tapppi-project` manifests). Each function's header
  comment documents it.
- `Brewfile` and `intel.Brewfile`: the Homebrew manifests.
- `dotfiles/`: a submodule ([Tapppi/dotfiles](https://github.com/Tapppi/dotfiles)) with its own
  `AGENTS.md`: shell and harness config, and the user-level agent instructions in `agents/`.
- `.extra` (git identity, personal aliases), `.path` (PATH extensions), `.credentials.dist` (the
  template for `~/.config/bash/.credentials`); `backup.sh`, `restore.sh` and `restore.bom`;
  `hooks/` (git hooks).
- `docs/`: [harnesses.md](docs/harnesses.md) (how setup configures the agent harnesses, and why
  tool-owned config is re-asserted), [skills.md](docs/skills.md) (how skills and plugins reach a
  repo), [conventions.md](docs/conventions.md) (shell, Brewfile, PATH and XDG conventions).

## Rules

- **Never run `setup.sh`, `tasks/*.sh` or `dotfiles/bootstrap.sh`.** They change system
  configuration, install software and need `sudo`; the owner runs them. The one exception: the
  `tool-update-review` skill's apply step may run `./setup.sh projects`, only that subcommand,
  when an accepted suggestion edits a file `tasks/projects.sh` manages (workspace
  `.tapppi-project.json` manifests, rendered `mise.local.toml`). That task is idempotent, needs no
  `sudo` and touches no system-wide state. The exception covers no other automation.
- **Edit dotfiles in the submodule, never in `~`.** Change the source in `dotfiles/home/` or
  `dotfiles/config/` and copy the changed file into place; the files under `~/`, `~/.claude/`,
  `~/.codex/`, `~/.cursor/`, `~/.pi/` and `~/.config/` are deployment targets. The generated
  user-level instruction files are edited one step further back, in `dotfiles/agents/`. Live files
  with no dotfiles source (`~/.config/bash/.credentials`, `~/.codex/config.toml`,
  `~/.claude.json`) are changed by the owner or the owning tool's CLI and never copied into
  dotfiles.
- **Tool-owned config is re-asserted, not vendored.** Keep the re-asserting steps last in
  `install()`, after `install_dotfiles`, and make every dispatch that runs `install_dotfiles`
  re-assert too. Never copy a tool's key into `dotfiles/home/`;
  [harnesses.md](docs/harnesses.md#tool-owned-config-is-re-asserted-not-vendored) explains why.
- **`shellcheck` every modified `.sh` file before committing**, and never introduce new warnings.
- **Bootstrap code must be portable** to the stock BSD userland, since it can run before the GNU
  tools are installed; everything else may assume GNU.
  [conventions.md](docs/conventions.md#bootstrap-code-must-be-portable) has the pitfalls.
- Keep scripts idempotent, and fail in functions with `return 1`, not `exit 1`.
- Never commit `.credentials`, keys, tokens, passwords, `.DS_Store` or backup tarballs.

## Commands

```sh
bash hooks/install.sh                                  # once after cloning: core.hooksPath
shellcheck setup.sh tasks/*.sh backup.sh restore.sh
shellcheck dotfiles/bootstrap.sh dotfiles/config/bash/.functions dotfiles/agents/render.sh
dotfiles/agents/render.sh --check                      # generated instructions are current
brew bundle check --file=Brewfile                      # read-only
```

There is no test suite.

## Style

`.editorconfig` sets the formatting: tabs (width 2) for code, and for Markdown spaces with
two-space list indentation, prose wrapped at 100 columns and fenced code with a language. Shell
style, Brewfile rules and PATH layering are in [conventions.md](docs/conventions.md).

## Git

Git work here follows the `ikeh-git:git-workflows` skill from the `ikeh-git` plugin, which
`.claude/settings.json` enables; load it before the first commit. Commits are SSH-signed through
1Password; messages are imperative and concise ("Add podman", "Update dotfiles"). Use the
configured git identity, never change it, and add no AI attribution. This repo and dotfiles use
`master`.

The plugin's two guards run on every Bash call:

- **Push guard.** A push to `origin` of an agent branch (`agent/`, any conventional-commit
  prefix, `debug/` or `backup/`: the plugin's default list, since this repo sets no
  `branchPrefixes`) runs without a prompt, including `--force-with-lease --force-if-includes`
  until the branch's PR carries a review or comment. Every other push prompts. Name the branch on
  each push, and keep `main` and `master` out of agent branch names: the `ask` rules prompt for
  any push whose text contains them.
- **Worktree guard.** Whole-tree staging in the main checkout is denied, and so is any rebase of
  `master`. `requireWorktree` is off, so a small change may be committed from the main checkout by
  explicit path. Do not use the `cam` alias here: it stages every change.

The guard decides how you may push, never whether: push only when the request calls for it, and
answer a prompt rather than reshaping the command until it stops. Committed enablement installs
nothing: on a new machine, run `claude plugin install ikeh-git@ikeh --scope local` in this repo.

### Committing to the dotfiles submodule

dotfiles has its own history on `master`, and this repo tracks its commit. Run from this repo's
root, never `cd` into the submodule, and stage only the paths you changed:

```sh
git -C dotfiles add <specific paths>
git -C dotfiles commit -m "Description of change"
git -C dotfiles push origin master
git add dotfiles
git commit -m "Update dotfiles"
```
