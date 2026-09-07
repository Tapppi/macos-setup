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

Paths you may scan for relevancy — the user's LIVE checkouts, read-only
(`references/research.md`'s "Relevancy is the point of this skill"):
    ~/project/github/tapppi/macos-setup  (Brewfile, tasks/*.sh, backup.sh,
        restore.sh, dotfiles/ submodule — shell/git/tmux/Claude configs)
    ~/project/github/tapppi/systems       (Nix flake)

`intel.Brewfile` is out of this tool entirely — do not read it, cite it or
target it (`references/research.md` §One Host, One Manifest).
`dotfiles/config/agent-skills/**` is vendored third-party skill content: grep
hits there are almost never a real touchpoint, so ignore them unless the tool
is genuinely configured there.

Audit trail to check for config_status (`references/research.md` §Config Status):
    ${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md

Standing notes previous runs left about these tools — method notes (read them
BEFORE you look anything up; they change where you look) and watch items
(topics to notice in the changelog you are reading anyway). Empty means these
tools have none (`references/research.md` §Watch Items (Reading)):
{{STANDING_NOTES}}
<!-- one block per tool that has any: kind (method-note | watch-item), topic,
     note. Looked up by tool id from the two stores; never hand-written. -->

Touchpoints found by word-boundary grep, per tool — where these tools are
named in the setup repos. This is a starting signal, not a finding and not a
list of what matters: verify each hit is a real touchpoint and find the ones
grep cannot (`references/research.md` §Word-Boundary Grep Rule — a script can
use a tool without ever naming it).
{{TOUCHPOINTS}}
<!-- one block per tool: the `grep -wn` hits from tiering, file:line + the
     matched line. Generated, never hand-written: a hand-written hint is where
     the nomination problem starts (§Writing Hypotheses below). -->

Follow references/research.md's full research quality bar — headliner atomicity
and category/severity classification, relevancy vs context vs
release_inventory vs filler, the noise floor's eight classes and its hard
deletion boundary, CVE severity capture (`basis`, the fetch budget, never
deriving a severity from how a description reads) and selecting `notable[]`
(cap 3, `affects_me` set from the direction of your own finding), the
vendor-silent compact-tag exception, link quality and the embedded_content
fallback, config_status, the two standing-note stores and how to
tell them apart (`references/research.md` §Standing Notes: Three Stores,
§Research-Method Notes vs Watch Items, §Watch Items (Proposing) and the
self-test that follows it), bespoke tasks/*.sh setup handling (see
`references/research.md` §Bespoke `tasks/*.sh` Setup Testing)
— read it before you start, not after. Hold yourself to the exact schema
shapes in `references/schemas.md` (headliners as {text,category,severity}
objects, relevancy items with category+severity+motivating_change, evidence
always an array, `security.cve_severities` as {cve_id,severity,basis} and
`security.notable` as {cve_id,advisory_id,severity,summary,affects_me} (§1.9) —
written even when nothing qualifies, as `[]`, since an omitted block and an
empty one mean different things to the card,
suggestions using title/target_files/rationale/motivating_link/diff_preview,
a proposed watch item using `kind: "watch-item"` with
`watch_topic`/`watch_note` instead (§1.7), a proposed method note using
`kind: "method-note"` with `method_topic`/`method_note` (§1.7b), and either
carrying `self_test_failed` when its self-test failed — tagged, never
dropped (§1.7c))
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
| `{{STANDING_NOTES}}` | This batch's tools' entries from `watch-items.json` and `method-notes.json`, looked up by tool id — empty when they have none |
| `{{TOUCHPOINTS}}` | The word-boundary grep hits from tiering (`references/research.md` §Word-Boundary Grep Rule), one block per tool — **generated from the grep, never typed by hand** |

## Batch sizing and tiering

See references/research.md for the individual-vs-batch heuristic (word-boundary
grep for a real repo touchpoint) and batch sizing guidance (~4-9 tools per
batch). This template is identical either way — only `{{TOOL_COUNT}}` and
`{{TOOL_LIST}}` differ.
