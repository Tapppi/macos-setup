# Research Subagent Prompt Template

Fill in the placeholders and pass the result verbatim as the subagent's
prompt (`references/research.md`). This exists because the prompt used to get
retyped by hand every run, with no fixed skeleton — real risk of forgetting
a quality-bar rule or drifting the schema shape between runs. The
substantive rules (what counts as a good headliner, how to classify
category/severity, when something is context vs relevancy vs filler, link
quality, watch items, ...) live in `references/research.md` and are deliberately
**not** duplicated here — this template's job is only the boilerplate that
never changes: what to fill in, what file to write, what shape to write it
in. Read `references/research.md` in full before filling this in; this is not a
substitute for it.

One subagent gets one filled-in copy of this template — whether it's
covering a single tool (individual-focus tier) or a batch (`references/research.md`'s
tiering).

```
You are researching {{TOOL_COUNT}} tool update(s) for the tool-update-review
skill. For each tool below, produce one Tool research object per
`references/schemas.md`, and write the complete array (one element per
tool, even if {{TOOL_COUNT}} is 1) to:

    {{OUTPUT_PATH}}

Tools in this batch:
{{TOOL_LIST}}
<!-- one block per tool:
     - id: {source}:{name}
     - name, source, current_version, latest_version, pinned
-->

Machine context (arch matters — ARM-only dependencies are incompatibilities
on x86_64, not footnotes):
{{MACHINE_JSON}}

Repo context — recent commits, for config_status cross-referencing and
general awareness of what's already changed recently (`references/collection.md`):
{{REPO_CONTEXT_JSON}}

Paths you may scan for relevancy (`references/research.md`'s "Relevancy is the point
of this skill"):
    ~/project/github/tapppi/macos-setup  (Brewfile, intel.Brewfile, tasks/,
        dotfiles/ submodule — shell/git/tmux/Claude configs)
    ~/project/github/tapppi/systems       (NixOS flake)

Audit trail to check for config_status and watch items (`references/research.md`):
    ${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md
    ${XDG_STATE_HOME:-~/.local/state}/tool-update-review/watch-items.json

Follow references/research.md's full research quality bar — headliner atomicity
and category/severity classification, relevancy vs context vs
release_inventory vs filler, the vendor-silent compact-tag exception, link
quality and the embedded_content fallback, config_status and watch-item
cross-referencing (reading *and*, rarely, proposing a new one — see
`references/research.md` §Watch Items (Proposing)), bespoke tasks/*.sh setup
handling (see `references/research.md` §Bespoke `tasks/*.sh` Setup Testing)
— read it before you start, not after. Hold yourself to the exact schema
shapes in `references/schemas.md` (headliners as {text,category,severity}
objects, relevancy items with category+severity+motivating_change, evidence
always an array, suggestions using
title/target_files/rationale/motivating_link/diff_preview, and a proposed
watch item using `kind: "watch-item"` with `watch_topic`/`watch_note`
instead — §1.7)
— loose shapes force hand-normalization during assembly.

Write your findings as a JSON array to {{OUTPUT_PATH}} using the Write
tool. Do not return the JSON as your final message text — the array in the
file is what gets read; a conversational summary of what you found is
fine as your actual response.
```

## Placeholder reference

| Placeholder | Filled with |
|---|---|
| `{{TOOL_COUNT}}` | Number of tools in this subagent's scope (1 for individual-focus, N for a batch) |
| `{{OUTPUT_PATH}}` | `{session_dir}/research/{tool-or-batch-slug}.json` |
| `{{TOOL_LIST}}` | One block per tool: id, name, source, current_version, latest_version, pinned — from `collect.sh`'s output |
| `{{MACHINE_JSON}}` | The `machine` object from `collect.sh`'s output |
| `{{REPO_CONTEXT_JSON}}` | The contents of `{session_dir}/repo_context.json` (`scripts/repo_context.sh`'s output) |

## Batch sizing and tiering

See references/research.md for the individual-vs-batch heuristic (word-boundary
grep for a real repo touchpoint) and batch sizing guidance (~4-9 tools per
batch). This template is identical either way — only `{{TOOL_COUNT}}` and
`{{TOOL_LIST}}` differ.
