# Research Subagent Prompt Template

Fill in the placeholders and pass the result verbatim as the subagent's
prompt (`references/research.md`). This exists because the prompt used to get
retyped by hand every run, with no fixed skeleton — real risk of forgetting
a quality-bar rule or drifting the schema shape between runs. The
substantive rules (what counts as an item, tags vs. severity, local
findings vs. inventory vs. filler, link
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
skill. For each tool below, produce one research object in the exact
closed shape below, and write the complete array (one element per
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

Paths you may scan for local findings — the user's LIVE checkouts,
read-only (`references/research.md` §Local Findings Are the Point):
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
(topics to notice in the changelog you are reading anyway). A watch item's
`topic` below is the exact string to put in `watch_hit.topic` when this
run's changes answer it — copied verbatim, never rewritten. Empty means these
tools have none (`references/research.md` §Watch Items (Reading)):
{{STANDING_NOTES}}
<!-- one block per tool that has any: kind (method-note | watch-item), topic,
     note. Looked up by tool id from the two stores; never hand-written. -->

Prior findings, as HYPOTHESES to verify — never as facts to restate. Each was
believed by a previous review of this machine; confirm or refute it against
today's versions from the sources you would have used had nobody told you, and
say plainly when one no longer holds (`references/research.md` §Prior Findings
Are Hypotheses):
{{HYPOTHESES}}
<!-- one block per tool that has any. Drawn mechanically by tool id from
     changelog.md, the two standing-note stores and the previous run's report.
     Form rules below in §Writing Hypotheses — they are not optional, and they
     are the difference between raising research quality and nominating an
     answer. -->

Touchpoints found by word-boundary grep, per tool — where these tools are
named in the setup repos. This is a starting signal, not a finding and not a
list of what matters: verify each hit is a real touchpoint and find the ones
grep cannot (`references/research.md` §Word-Boundary Grep Rule — a script can
use a tool without ever naming it).
{{TOUCHPOINTS}}
<!-- one block per tool: the `grep -wn` hits from tiering, file:line + the
     matched line. Generated, never hand-written: a hand-written hint is where
     the nomination problem starts (§Writing Hypotheses below). -->

Follow references/research.md's full research quality bar — one real
change per item and the splitting of compound bullets, tags vs. severity
as independent axes, local findings vs. release_inventory vs. filler, the
noise floor's eight classes and its hard deletion boundary, CVE rating
capture (`rating_basis`, the fetch budget, never deriving a rating from
how a description reads), direction and effect read off your own finding,
the vendor-silent compact-tag exception, link quality and the
embedded_content fallback, config_status, the two standing-note stores
and how to tell them apart (`references/research.md` §Standing Notes:
Three Stores, §Research-Method Notes vs Watch Items, §Watch Items
(Proposing) and the self-test that follows it), bespoke tasks/*.sh setup
handling (see `references/research.md` §Bespoke `tasks/*.sh` Setup
Testing) — read it before you start, not after.

Each research object carries EXACTLY this closed set of top-level keys
(published in `scripts/contract/contract.json`; an unrecognized key is
quarantined, reported as E-RESEARCH-UNKNOWNKEY, and holds the tool for
review with its upgrade no longer pre-accepted):

    id, items, links, config_status, suggestions, release_inventory,
    vendor_silent_categories, flags, research_error, cask_sudo_hint

plus five echoed identity keys — name, source, current_version,
latest_version, pinned — recognized and ignored; collect.json is
authoritative for them. The retired shapes (headliners, relevancy,
context, notable, cve_severities) are REJECTED, not read — items[] and
its tags are their single successor. Never emit security_only, impact,
risk_level, review_bucket or pre_accept anywhere in your output: they are
validator-only (E-FLAG-FORBIDDEN).

Hold yourself to the exact shapes (`references/item-schema.md` §2):

- items[] — one element per real change. Required on every item:
    anchor: {kind: "cve"|"advisory"|"issue"|"commit"|"release"|"none",
             value}  (for kind "none": value null plus a "slug" string;
             the validator derives the item id from the anchor — never
             write an id yourself)
    title:  one line, <= 120 chars — the fact and nothing else; the why/
            how/consequence goes in the optional body
    tags:   >= 1 of the closed eight: security | fix | feature |
            breaking | deprecation | perf | packaging | chore
    severity: info | notable | warning | incompatible — "how much does
            this matter to THIS machine"
  and at least one of:
    change: {version, citation, link_index} — the upstream fact;
            citation is the VERBATIM upstream text (required when change
            is present), link_index indexes your links[]
    local:  {direction: "reaches"|"does_not_reach"|"unclear",
             effect: "risk"|"benefit"|"none", statement,
             evidence: [{path, lines, note}, ...],
             citations: [{kind, text, url}, ...]} — the finding about
            this setup; evidence is PATHS ONLY (a "reaches" item must
            carry at least one), prose and commands go in citations
  A "security" tag requires a security block, and vice versa:
    security: {cve_id, advisory_id,
               rating: "critical"|"high"|"medium"|"low"|"unknown",
               rating_basis: "vendor"|"nvd"|"cvss"|"unrated",
               exploited_in_wild: bool}
  Severity consistency: "incompatible" requires direction "reaches" AND
  effect "risk"; "warning" requires a local block.
  An item that answers a stored watch item for its tool additionally
  carries
    watch_hit: {topic} — copy the topic string exactly as it appears in
    the standing notes above; do not rewrite it. Such an item must carry
    a local block and at least "notable" severity.
- suggestions[] — always title/target_files/rationale/motivating_link/
  diff_preview, plus per kind:
    "upgrade" — NEVER authored by you; it is synthesized mechanically
      for every tool.
    "edit" (the default) — a concrete Brewfile/dotfiles/config change.
    "structural" — the change re-manages an entity (deprecated cask
      migrated, formula moved between sections, tap trusted/dropped,
      install handed between mechanisms, setup.sh task added/changed):
      add a structural block {op: "manifest_add"|"manifest_remove"|
      "manifest_replace"|"manifest_move"|"tap_add"|"tap_remove"|
      "install_method_change"|"task_add"|"task_change",
      subjects: [{type, name}, ...] — the entities the change is ABOUT,
      manifest: "Brewfile" or null (intel.Brewfile is out of this tool
      entirely), from, to, anchor} — see `references/research.md`
      §Suggestion Kinds for each op's required fields.
    "watch-item" — a proposed watch item: watch_topic/watch_note/
      rationale.
    "method-note" — a proposed method note: method_topic/method_note/
      rationale.
    Either memory kind carries self_test_failed: {limb, reason} when its
    self-test failed — tagged, never dropped.

Loose shapes force hand-normalization, and a coerced shape is
content-losing: the tool is then held for review instead of
pre-accepting.

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
| `{{HYPOTHESES}}` | Prior findings for this batch's tools, drawn mechanically by tool id — one block per tool that has any, in the form §Writing Hypotheses requires |
| `{{STANDING_NOTES}}` | This batch's tools' entries from `watch-items.json` and `method-notes.json`, looked up by tool id — empty when they have none. Filling it also copies the same `watch-items.json` verbatim to `{session_dir}/watch-items.json` (one `cp`, no transformation — SKILL.md step 3), so the validator grounds `watch_hit` claims against exactly what the checkers were given |
| `{{TOUCHPOINTS}}` | The word-boundary grep hits from tiering (`references/research.md` §Word-Boundary Grep Rule), one block per tool — **generated from the grep, never typed by hand** |

## Writing Hypotheses

`{{HYPOTHESES}}` is where a prior run's findings reach a checker. **There are
rules about their form, because the form changes what comes back**, and the
measured effect is large in both directions.

A hypothesis states an **observable** and asks for verification:

> **GOOD** — "A prior review found v5.3 flips the Makefile default O_NORL 0→1,
> and homebrew-core's formula runs a plain `make install` with no `O_NORL=0`.
> Verify independently against the current formula and the v5.3 source."

It never names a conclusion, a proposal, or an artefact kind:

> **BAD** — "A prior review proposed a watch item for the widening local-data
> surface; decide for yourself whether it clears the bar (the fleet-wide budget
> is one or two total)."

The second is a nomination wearing a question's clothes. It tells the checker a
candidate exists, grants permission to take it, and — with a budget in the same
breath — implies it is *the* one. **That exact sentence produced one of the
last run's eight watch-item proposals, and six of the eight trace to hints of
that shape.** The two the prompt did not name are the two both convergence
passes kept.

The good example is not hypothetical either: it measurably raised the quality
of that group's research. The difference between them is that one hands over a
**question with its evidence** and the other hands over an **answer**.

Hard rules:

  - **Never name an artefact kind.** Do not write "watch item", "method note"
    or "suggestion" in a hypothesis. State the observation; the bar decides
    what it becomes.
  - **Never mention volume, budgets, counts, or how many were proposed last
    time.** Not in a hypothesis, not anywhere else in the prompt
    (`references/research.md` §There Is No Volume Target).
  - **Never carry a prior conclusion without its evidence.** If you cannot
    supply what it was based on, do not supply the conclusion.
  - **Never phrase one as a decision for the checker to ratify** — "decide for
    yourself whether", "confirm this is still worth flagging". Ask for the
    observation, not for a verdict on a prior agent's judgement.
  - **Prefer the raw observation to the prior agent's phrasing of it.**

Hypotheses are drawn **mechanically, by tool id**, from `changelog.md`, the two
standing-note stores and the previous run's report. They are not hand-written
per run. A hand-written hint is where every one of the last run's nominations
came from.

## Batch sizing and tiering

See references/research.md for the individual-vs-batch heuristic (word-boundary
grep for a real repo touchpoint) and batch sizing guidance (~4-9 tools per
batch). This template is identical either way — only `{{TOOL_COUNT}}` and
`{{TOOL_LIST}}` differ.
