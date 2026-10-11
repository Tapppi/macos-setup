# Where skills live

How agent capability reaches a repo, and the layout our repos commit. Linked
from `CLAUDE.md` and `AGENTS.md` rather than inlined in them — they are loaded
into every session, and this is reference material, not a standing rule.

The live configuration is the source of truth for *what is currently enabled*;
this file describes the shape, not the inventory. For the shared bundles
themselves, see the `Tapppi/skills` repo (`~/project/github/tapppi/skills`), which
publishes them as the `tapppi-skills` marketplace.

A skill reaches a repo by exactly three routes:

1. **Always-on user-level skills.** Installed once for the machine — a
   user-level skill directory, or a plugin enabled at user scope — and active
   in every repo. Machine-global by construction, so nothing repo-specific
   goes here: a user-level copy cannot follow a branch.

   Route 1 has more than one location, and each harness reads its own set. Each user-level skill
   reaches each harness exactly once, through a location no other harness would read a second
   time:

   | Skill | Claude Code | Codex | OpenCode | Pi |
   | --- | --- | --- | --- | --- |
   | `frontend-design` | user-scope plugin | user-level plugin | `skills.paths` | package |
   | `my-machine-configs` | link in `~/.claude/skills` | link in `~/.codex/skills` | the Claude link | package of dotfiles `agents/` |
   | ctx7's `context7-mcp` | ctx7's own copy in `~/.claude/skills` | link in `~/.codex/skills` | the Claude copy | link in `~/.pi/agent/skills` |
   | `pi-config` | none | none | none | package |

   - `my-machine-configs` is the machine-configuration skill. Its source is dotfiles
     `agents/skills/my-machine-configs/`, outside the trees `bootstrap.sh` syncs, so each harness
     reaches the checkout through an absolute link or a package path, never a copy. Every link
     resolves through macos-setup's main checkout, never a worktree. A missing source is skipped
     with a hint rather than linked, and a link path that is not a symlink is left alone.
     `install_claude_user_skills` writes the Claude link, and `./setup.sh dotfiles` re-asserts it
     after a sync.
   - `~/.agents/skills` holds nothing of ours. Codex, OpenCode, Cursor and Pi all read it, so a
     skill there would be listed twice in OpenCode and Cursor beside `~/.claude/skills`. Earlier
     installs linked ctx7's skill there; `install_codex` removes that link when it is that exact
     symlink.
   - Codex reads its plugins and `~/.codex/skills`, where `install_codex` links ctx7's skill and
     `my-machine-configs`. Codex calls that root the deprecated user skills location but still
     scans it. It was chosen over `/etc/codex/skills` from systems, which only Codex reads; if a
     Codex upgrade drops the root, the links move there. Codex's user-level plugins are
     `frontend-design` and `ikeh-development`, and its bundled skills load from
     `~/.codex/skills/.system`. `./setup.sh codex` re-runs `install_codex` once systems provides
     `codex` (an owner command: `setup.sh` asks for `sudo` first).
   - OpenCode reads `~/.claude/skills`, which brings it the Claude link and ctx7's copy, and the
     `skills.paths` in `opencode.json`: `frontend-design` and the ikeh-development plugin's skills
     from the ikeh checkout. Its own configuration help is the built-in `customize-opencode` skill,
     which `permission.skill` leaves allowed. `./setup.sh opencode` checks that every path exists
     and prints the provider logins still to do.
   - Pi reads `~/.pi/agent/skills`, `~/.agents/skills` and the skills of the packages listed in
     its own `~/.pi/agent/settings.json`. `install_pi` adds the packages with `pi install <dir>`,
     which loads a directory's `skills/` in place, and links ctx7's skill, because no Pi command
     writes a `skills` entry. `pi-config`, the generic Pi configuration skill, comes from
     tapppi/skills; the other harnesses ship their own. On Pi 0.99 or later `install_pi` also
     adds context7 to `~/.pi/agent/mcp.json` when it is missing. `./setup.sh pi` re-runs it once
     systems provides `pi`.
   - The claude.ai account sync writes `~/.claude/skills/synced/`, which
     Claude Code, OpenCode and Cursor all read. `skillOverrides` in
     `settings.json` hides synced skills from Claude Code by name, and
     `permission.skill` in `opencode.json` denies the unwanted ones in
     OpenCode, which also drops them from its skill list. Cursor ignores
     `skillOverrides`, so it lists every synced skill.
   - Cursor reads `~/.cursor/skills-cursor` (its own), `~/.claude/skills`
     under the home directory (it ignores `CLAUDE_CONFIG_DIR`, so another
     Claude profile's skills never reach it), `~/.agents/skills`,
     `~/.codex/skills` and the caches of enabled Claude Code plugins. It
     therefore lists `my-machine-configs` and `context7-mcp` twice, an
     accepted residue carried with the Cursor refresh (SYSMI-67).
2. **Plugins from a marketplace.** Capability someone else publishes is
   consumed from their marketplace rather than vendored: they ship versions, we
   choose which to enable, and none of their release cadence lands in our
   history. Which marketplaces a given repo consumes is that repo's business.
   Anthropic's document family (docx, pdf, pptx, xlsx) is never vendored,
   because its licence forbids redistribution, so it is in no repo of ours,
   public or private. On this machine the claude.ai sync delivers the newer
   copies. Their plugin, `document-skills@anthropic-agent-skills`, is off at
   user level, and a repo can still enable it.

   Which configuration switches a plugin on belongs to the harness and to the
   repo, not to this document: each harness keeps its own, each repo settles
   whether that configuration is committed, and the set of harnesses differs
   from repo to repo. In this repo, `tasks/projects.sh` writes a repo's
   gitignored local list from a workspace manifest; its own comments say how.

   OpenCode loads no Claude Code plugins; it reads only Claude Code's skill
   directories. Pi reads neither. A plugin can still reach both by shipping an
   OpenCode module and a Pi package of its own, as `browser` does (below).
   Cursor loads a user-scope plugin only when the user `enabledPlugins` says
   `true`, so a new user-scope plugin reaches Cursor only when it is enabled by
   name.
3. **Repo-committed `.agents/skills` or `.claude/skills`**, discovered in
   place. In our repos this is the bundle layout below. In other people's
   repos it is whatever they commit under their own conventions, read as-is.
   We impose no layout there.

**`browser` is enabled per repo, in every harness.** The tapppi-skills `browser` plugin (its skill
plus the Playwright and Chrome DevTools MCP servers) is off at user level. macos-setup, tieto and
ikeh each commit the same four files, and each harness reads the one meant for it:

| Harness | Committed file | Entry | Per machine, once |
| --- | --- | --- | --- |
| Claude Code | `.claude/settings.json` | `"browser@tapppi-skills": true` in `enabledPlugins` | None to load it; see the install record below |
| Codex | `.codex/config.toml` | `[plugins."browser@tapppi-skills"]` with `enabled = true` | The project is trusted in Codex |
| OpenCode | `opencode.json` at the repo root | a `plugin` entry for the plugin's `opencode/browser.js`, under `{env:HOME}` | None |
| Pi | `.pi/settings.json` | a `packages` entry for the plugin directory, under `~` | `/trust` in the repo once |
| Cursor | (Claude Code's file) | | The install record below |

- The plugin ships its own OpenCode module and Pi package beside its `.mcp.json`, its one MCP
  definition (tapppi/skills `README.md`). The OpenCode and Pi entries point into the checkout at
  `~/project/github/tapppi/skills`; on a machine without it, each harness starts without
  browser.
- Claude Code loads the plugin from the committed key alone, because tapppi-skills is a
  directory marketplace. Its documentation says a plugin needs an install, so each repo also gets
  one project-scope install record, run once from the main checkout:
  `claude plugin install browser@tapppi-skills --scope project`. Cursor needs that record, and
  loads the plugin only where the record's path equals the workspace root, so it has browser in
  the main checkout but not in worktrees. The committed file keeps Claude Code's own 2-space
  serialisation, so the install leaves it byte-identical; the other files follow `.editorconfig`.
- Codex keeps the plugin installed at user level with `enabled = false`: its cache is what the
  repo's `enabled = true` switches on, and `install_codex` writes the `false` after every
  `codex plugin add`, which always writes `true`. A hand-run `codex plugin add
  browser@tapppi-skills`, or enabling it in Codex's `/plugins`, therefore turns it on in every
  project until `./setup.sh codex` runs again. A new browser version also reaches Codex only
  through `./setup.sh codex`, whose `codex plugin add` re-copies the cache; OpenCode and Pi read
  the checkout directly, and Claude Code and Cursor take it through the install record.
- Pi loads nothing project-scoped in an untrusted repo, so browser appears there only after
  `/trust`. Pi matches trust by path, so a worktree inside the repo (`.claude/worktrees/`)
  inherits it, and a worktree elsewhere needs its own `/trust`. Codex trusts every linked
  worktree of a trusted checkout.

Verified harnesses: Claude Code 2.1.293, codex-cli 0.161.0, Cursor 2026.10.01 (read from its
source), OpenCode 1.18.35 and Pi 1.0.4 for the route-1 locations and the per-repo `browser` files.
Routes 2 and 3 were last verified on Claude Code 2.1.267, codex-cli 0.154.0,
Cursor 2026.09.02 and OpenCode 1.15.12; Pi's `.agents/skills` reading comes
from its 0.87.1 documentation and source.

**Route 3 in our repos is a committed bundle**, in this shape:

```text
<repo>/.agents/skills/<bundle>/          # canonical, a real directory
        .claude-plugin/plugin.json       # one manifest; Claude Code AND Codex read it
        skills/<name>/SKILL.md           # conventional layout
        [agents/ hooks/ .mcp.json]       # optional, additive
<repo>/.claude/skills/<bundle> -> ../../.agents/skills/<bundle>   # relative, committed
```

Both paths are needed because no single one is universal: Claude Code reads
only `.claude/skills`, Codex and Pi read `.agents/skills`, not
`.claude/skills` (Pi once the project is trusted), and Cursor and OpenCode
read both. Claude Code loads a directory containing `.claude-plugin/` as a
zero-install `<bundle>@skills-dir` plugin at scope `project` — no
marketplace, no `enabledPlugins` entry, discovered in place, so edits on a
branch are live. It works through the committed relative symlink; discovery
accepts a symlinked entry deliberately, not by accident.

**The symlink must be relative, and it must be committed.** That is the whole
reason worktrees work without provisioning: git carries the symlink, and a
relative target resolves inside whichever worktree reads it. An absolute
symlink into a machine-global directory pins every worktree to one copy, which
is exactly what this shape avoids.

A plain skill (`.agents/skills/<name>/SKILL.md`, no `.claude-plugin/`) is also
fine. The only difference is namespacing: a bundle's skills appear as
`<bundle>:<skill>`, a plain skill is unnamespaced. Prefer a bundle for
anything shared, versioned, or carrying hooks/agents/MCP.

A bundle's `SKILL.md` belongs at `skills/<name>/SKILL.md`. This is a naming
convention, not a compatibility requirement — Claude Code and Codex both load
a bundle-root `SKILL.md`. The nested layout is kept because a bundle carrying
more than one skill needs it and because it avoids the degenerate
`<bundle>:<bundle>` name a root-level file produces.

**Zero-install adoption requires workspace trust.** A project-scope
`.claude/skills/` bundle is scanned but dropped in an untrusted workspace:
`claude plugin list` reports `(suppressed)@skills-dir` and withholds even the
bundle name, and `claude -p` does not grant trust. A fresh clone therefore needs
one interactive trust acceptance before its committed capability loads. A git
worktree inherits the main checkout's trust, so a new worktree needs nothing.
Codex applies the same rule to its own project layer: while a project is
untrusted, even a syntactically broken `<repo>/.codex/config.toml` is ignored
in silence.
