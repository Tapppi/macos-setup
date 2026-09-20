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
2. **Plugins from a marketplace.** Capability someone else publishes is
   consumed from their marketplace rather than vendored: they ship versions, we
   choose which to enable, and none of their release cadence lands in our
   history. Which marketplaces a given repo consumes is that repo's business.
   Anthropic's document family arrives this way and only this way — its licence forbids redistribution, so it is
   vendored in no repo of ours, public or private.

   Which configuration switches a plugin on belongs to the harness and to the
   repo, not to this document: each harness keeps its own, each repo settles
   whether that configuration is committed, and the set of harnesses differs
   from repo to repo. In this repo, `tasks/projects.sh` writes a repo's
   gitignored local list from a workspace manifest; its own comments say how.
3. **Repo-committed `.agents/skills` or `.claude/skills`**, discovered in
   place. In our repos this is the bundle layout below. In other people's
   repos it is whatever they commit under their own conventions, read as-is.
   We impose no layout there.

Verified harnesses: Claude Code 2.1.267, codex-cli 0.154.0, Cursor
2026.09.02, OpenCode 1.15.12.

**Route 3 in our repos is a committed bundle**, in this shape:

```text
<repo>/.agents/skills/<bundle>/          # canonical, a real directory
        .claude-plugin/plugin.json       # one manifest; Claude Code AND Codex read it
        skills/<name>/SKILL.md           # conventional layout
        [agents/ hooks/ .mcp.json]       # optional, additive
<repo>/.claude/skills/<bundle> -> ../../.agents/skills/<bundle>   # relative, committed
```

Both paths are needed because no single one is universal: Claude Code reads
only `.claude/skills`, Codex reads only `.agents/skills`, and Cursor and
OpenCode read both. Claude Code loads a directory containing `.claude-plugin/`
as a zero-install `<bundle>@skills-dir` plugin at scope `project` — no
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
