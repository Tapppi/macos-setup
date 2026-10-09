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

   Route 1 has more than one location, and each harness reads its own set:
   - `browser` and `frontend-design` are user-level in every harness, and
     each reaches it once. Claude Code enables them at user scope (the
     tracked `enabledPlugins`; `install_claude_code` fetches them), and Cursor
     follows that list. Codex has them as user-wide plugins, OpenCode through
     `skills.paths` and Pi as local-path packages. A repo that also enables
     them at local scope is redundant and harmless.
   - `~/.agents/skills` holds one link, to ctx7's `context7-mcp` skill, which
     `install_codex` in `tasks/install.sh` writes. Codex, OpenCode, Cursor and
     Pi read the directory, so a skill linked there reaches all four.
   - Codex enables plugins user-wide: `install_codex` installs `browser`,
     `frontend-design` and `ikeh-development`, and `./setup.sh codex` re-runs
     it once systems provides `codex` (an owner command: `setup.sh` asks for
     `sudo` first). Codex also loads its own bundled skills from
     `~/.codex/skills/.system`.
   - OpenCode's `skills.paths` in `opencode.json` adds user-level skill
     directories: `browser`, `frontend-design` and the ikeh-development
     plugin's skills from the ikeh checkout. OpenCode has no plugin form for
     them.
   - Pi reads `~/.pi/agent/skills`, `~/.agents/skills` and the skills of the
     packages listed in its own `~/.pi/agent/settings.json`. `install_pi`
     adds `browser` and `frontend-design` there with `pi install <dir>`,
     which loads a plugin directory's `skills/` in place, and `./setup.sh pi`
     re-runs it once systems provides `pi`. A Pi package carries no MCP
     servers, so on Pi 0.99 or later `install_pi` also adds the browser
     plugin's two servers and context7 to `~/.pi/agent/mcp.json`, each only
     when missing.
   - The claude.ai account sync writes `~/.claude/skills/synced/`, which
     Claude Code, OpenCode and Cursor all read. `skillOverrides` in
     `settings.json` hides synced skills from Claude Code by name, and
     `permission.skill` in `opencode.json` denies the unwanted ones in
     OpenCode, which also drops them from its skill list. Cursor ignores
     `skillOverrides`, so it lists every synced skill.
   - Cursor reads `~/.cursor/skills-cursor` (its own), `~/.claude/skills`
     under the home directory (it ignores `CLAUDE_CONFIG_DIR`, so another
     Claude profile's skills never reach it), `~/.agents/skills` and the
     caches of enabled Claude Code plugins.
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
   directories. Pi reads neither. Cursor loads a user-scope plugin only when
   the user `enabledPlugins` says `true`, so a new user-scope plugin reaches
   Cursor only when it is enabled by name.
3. **Repo-committed `.agents/skills` or `.claude/skills`**, discovered in
   place. In our repos this is the bundle layout below. In other people's
   repos it is whatever they commit under their own conventions, read as-is.
   We impose no layout there.

Verified harnesses: Claude Code 2.1.293, codex-cli 0.159.1, Cursor
2026.10.01, OpenCode 1.18.31 and Pi 0.87.1 for the route-1 locations.
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
