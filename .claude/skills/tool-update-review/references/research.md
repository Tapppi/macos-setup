# Research (Step 3) Reference

This is the reference for the skill's step 3, "Research each tool." It has
two audiences and is split into two parts for them:

- **Part 1 — Orchestrator: Dispatch** is what the orchestrating session
  needs to spawn research subagents correctly: how to tier tools, how to
  spawn them, the file-write contract, and how to keep
  `research-status.json` current while research is in flight.
- **Part 2 — Subagent Quality Bar** is what a research subagent itself must
  follow while producing findings. **A research subagent reads this section
  in full, every run, before writing findings** — it is deliberately not
  summarized or excerpted in the subagent's prompt (see
  `references/research-prompt-template.md`), so drift between runs doesn't
  creep in.

Table of contents:
- Part 1 — Orchestrator: Dispatch
  - Prompt Template
  - Tiering: Individual-Focus, Batched, Brew-Health
  - Word-Boundary Grep Rule
  - Spawning and the Output-File Contract
  - `research-status.json` Group Updates
  - Failure Handling
- Part 2 — Subagent Quality Bar
  - Headliners
  - Category vs. Severity — Independent Axes
  - Links
  - Relevancy Is the Point
  - Classify Non-Changelog Findings Correctly
  - Don't Author "I Checked, Found Nothing"
  - Suggestions Are Always `kind: "edit"`
  - Config Status
  - Watch Items (Reading)
  - Deduplicate Facts
  - Scope-vs-Changelog Separation
  - Bespoke `tasks/*.sh` Setup Testing
  - Schema Strictness
  - Depth by Tool
  - Heterogeneous Hosts
  - Brew-Health Enrichment
  - Pinned Tools

---

## Part 1 — Orchestrator: Dispatch

### Prompt Template

Build each subagent's prompt by filling in
`references/research-prompt-template.md` — a fixed skeleton (what to fill
in, what file to write, what shape) so the boilerplate structure doesn't get
retyped by hand and drift between runs. It intentionally doesn't repeat this
document's quality bar (Part 2 below); tell each subagent to read that in
full before filling the template in, every run.

### Tiering: Individual-Focus, Batched, Brew-Health

Literal one-subagent-per-tool stops scaling once the candidate list gets
large — most outdated tools (a plain patch-bump brew formula, a mise runtime
with no bespoke setup) need only a quick changelog skim, and a dedicated
subagent per one is wasted overhead. Group tools into two tiers instead,
plus a third standing tier for brew-health findings:

- **Individual-focus**: one subagent per tool, for any tool with a real
  touchpoint in the setup repos — a bespoke `tasks/*.sh` function (grep
  `tasks/install.sh`/`tasks/config.sh`), a dotfiles config file, or a
  Brewfile pin/comment naming it. These need the full relevancy/config_status
  depth this skill exists to produce, and batching them would dilute that.
- **Batched-by-category**: one subagent covering ~4-9 tools with no repo
  touchpoint beyond a plain Brewfile/mise line — group by rough category
  (CLI utilities, GUI casks, mise runtimes) so the subagent's changelog-skim
  work stays coherent. Give it the same schema and depth requirements per
  tool inside the batch; a batch subagent still returns one full Tool
  research object per tool, just from one process instead of N.
- **Brew-health group** (see `references/collection.md` §Brew-Health
  Collection for the source shape, and §Brew-Health Enrichment below for
  what this tier actually does): the `brew_health` findings get their own
  dedicated subagent (individual-tier — they're all repo-touchpoint work by
  nature: which tap owns which keg, whether a deprecated cask is in the
  Brewfile, whether an untrusted tap is actually used here). It writes one
  research element per finding (matching each finding's `id`) enriching the
  collect-default remediation into the *right* fix for this setup: a
  Brewfile `edit` migrating a deprecated cask to its replacement; the
  trust-vs-untap judgment per tap (trust the tap that owns a backend you
  rely on; flag for Discuss a tap whose future is genuinely undecided;
  recommend untap for one that's unused); and the causal links between
  findings (an untrusted tap is *why* its kegs are orphaned — trust it
  rather than reinstall). Findings the default remediation already handles
  well (a plain missing dependency, an intentional path note) need no
  enrichment; leaving a finding out of the research file is fine —
  `assemble.py` falls back to the collect default. This group can be
  researched by the orchestrator directly instead of a subagent when the
  finding set is small and the setup context is already in hand — it's the
  same output file either way (`research/{id}.json`).

### Word-Boundary Grep Rule

**Use word-boundary grep (`grep -wn`/`-wni`) to find touchpoints, never
plain substring matching.** A substring grep produces false positives that
feed wrong context to a subagent — e.g. `grep cloc` matches inside `clock`/
`wall-clock`, `grep go` matches inside `mkdir -m go=` or `go to`. A tool that
looks like it has a bespoke touchpoint because of a substring collision gets
miscategorized into the individual tier for the wrong reason (or worse, a
real touchpoint gets diluted by unrelated grep noise in its context). Re-run
suspicious hits with `-w` before trusting them.

Grep is a starting signal, not the last word — it only catches *literal*
mentions. A script can use a tool conceptually without ever naming it (e.g.
`tasks/init.sh` configures `~/.ssh/config` and the 1Password SSH agent but
never contains the string "openssh"; a dotfiles alias may wrap a tool under a
different name). So grep the **whole** `tasks/*.sh` set (install.sh,
config.sh, init.sh, macos.sh, projects.sh — not only install.sh/config.sh)
and the dotfiles configs, then also skim for conceptual usage. **Any real
touchpoint → individual-focus tier; when genuinely unsure whether a touchpoint
is "real," default to individual** — batching a tool that has a bespoke setup
function, a dotfiles config, or a Brewfile pin/comment reliably produces
thinner research than the touchpoint warrants.

### Spawning and the Output-File Contract

Spawn every subagent (both tiers) in one turn. Each gets: the tool
id(s)/name/source/versions in its scope, the machine context (arch matters —
ARM-only dependencies are incompatibilities on x86_64, not footnotes), the
paths it may scan for relevancy, and the two repos' `recent_commits` from
step 1's `repo_context` (see `references/collection.md` §Repo Freshness for
where that comes from).

**Each subagent writes its own output JSON directly to
`{session_dir}/research/{tool-or-batch-slug}.json`** rather than returning
its findings as conversation text for you to hand-transcribe — at any real
candidate count, retyping every subagent's structured output yourself is
slow and error-prone (a transcription slip silently changes what ships in
the report). Tell each subagent the exact path to write and the exact shape
(next paragraph); step 4 (`references/assembly.md`) reads the files back
rather than reconstructing them from memory. **Every research file is a JSON
array**, regardless of tier — an individual-focus subagent writes a
one-element array, a batch subagent writes one element per tool in its
batch — so `assemble.py` parses every file in `research/` identically
instead of branching on tier. Each array element is a partial Tool object
per `references/schemas.md` §Report Object (`headliners`, typed `links`,
`relevancy`, `context`, `release_inventory`, `suggestions`,
`config_status`) plus its own `"id"` field (`{source}:{name}`, matching a
collect.sh candidate) so assembly can match it back up.

### `research-status.json` Group Updates

Once tiering above has grouped the candidates, write `research-status.json`
(schema: `references/schemas.md` §research-status.json)'s `groups` array
with every group `"pending"` and `phase: "researching"` — this is what lets
the pre-report loading page (`references/rendering-results.md` §Loading
Page) show the real group list instead of an empty one. As each subagent is
spawned/finishes, update that group's `state` (`pending → running →
done`/`failed`) — same one-transition-per-write discipline used for
`status.json` (`references/server-and-session.md` §Pre-Report Status covers
the write pattern and the server side of this file).

### Failure Handling

On subagent failure/timeout, set `research_error` and keep the tool listed
with versions only.

---

## Part 2 — Subagent Quality Bar

**A research subagent reads this section in full, every run, before writing
findings.** Everything below applies to every tier (individual, batched,
brew-health) unless a rule says otherwise.

### Headliners

**≤6 bullets** covering the whole current→latest range, not just the newest
release. Skim actual release notes/changelogs — don't guess from version
numbers. **One fact per bullet — split compound bullets before classifying
them.** A changelog often bundles unrelated changes into one sentence
("added X and Y, plus fixed CVE Z"); if you classify the bundle as one
atomic item, it gets filed under whichever topic feels most severe and the
rest of the content is lost (seen this run: rust's "added
assert_matches!/Copy-range-types, plus fixed two Cargo CVEs" got filed
entirely under Security — the macro/feature content never showed up under
Features at all). Split first, classify each resulting item independently;
both can cite the same source link since they came from the same release.

### Category vs. Severity — Independent Axes

**Category is topic, not urgency — the two are independent axes.** Which
group a headliner/relevancy item belongs to (Security/Fixes/Features/Notes)
is decided purely by *what kind of change it is*, never by how urgent or
prominent it feels relative to other items you're also reporting for the
same tool. Severity/priority is a separate, per-item property that drives
visual weight *within* whichever category the item topically belongs to
(`references/rendering-report.md` §Page Layout) — it never changes which
category the item is in. Seen this run: yt-dlp's "restricts --exec command
templates to safe string conversions... to close a command-injection
footgun" is unambiguously Security by topic, but got filed under Notes,
apparently because it read as less prominent than the CVEs already sitting
under Security for that tool — it should have stayed Security with a lower
per-item priority, not moved category. For contrast, yt-dlp's "Minimum
recommended Python raised to 3.11, Node to v22..." is a correctly-placed
Notes item — genuine compatibility/requirements info that isn't
Security/Fixes/Features by topic at all.

### Links

Always the canonical changelog for the version range; add release pages for
majors and official blog posts when they exist. Every relevancy finding and
suggestion should be traceable to a link. **Prefer the most specific
destination that actually covers the version range, never a generic "all
versions" index page when a dedicated page for this range exists** — a
GitHub Releases page for the specific tag, a CHANGELOG.md section anchor, a
dedicated release-notes URL. Confirm you actually fetched/read the page
you're linking, not just guessed its URL from convention (seen this run:
stunnel's SECURITY and FEATURES links both pointed at the generic
version-list page instead of the 5.79 release itself). **Never link to
something that dead-ends or triggers a file download** — if the only source
for the content is inside a downloaded tarball/archive or a raw
non-browsable file (seen this run: stunnel's FIXES link pointed at a
NEWS.md "fetched from the 5.79 source tarball," which isn't a page a user
can click through to), don't link it at all. Instead extract the relevant
excerpt (not the whole file) and set it as `embedded_content` (markdown,
`references/schemas.md` §Report Object) on the link object with `url`
omitted — the page renders that in a modal instead of navigating
externally. This is the exception, not the default; most tools have a
normal browsable page and should just link it.

### Relevancy Is the Point

**Relevancy is the point of this skill.** Scan the user's setup repos —
`~/project/github/tapppi/macos-setup` (Brewfile, intel.Brewfile, tasks/,
dotfiles/ submodule with shell/git/tmux/Claude configs) and
`~/project/github/tapppi/systems` (NixOS flake) — plus machine facts, for
places the tool is configured or its changed behavior lands. Severity:
`incompatible` (won't work here — e.g. new major requires Apple Silicon on
an Intel machine) > `warning` (breaks a config/workflow the user has) >
`notable` (touches something they use) > `info`. Cite evidence as
`file:line` paths. **`relevancy[]` requires a genuine `motivating_change`**
— an actual changelog item driving the finding. If you're about to write
`motivating_change: null` or something like "none found"/"not a
changelog-driven finding", that finding isn't relevancy at all; see the next
section for where it actually belongs.

### Classify Non-Changelog Findings Correctly

**Classify non-changelist findings correctly instead of defaulting them into
`relevancy` with severity `"info"`.** Three different things show up during
research that aren't "a changelog item affects this setup," and each has
its own home (`references/schemas.md` §Report Object):

- **`context[]`** — present-tense repo-scope/usage/locality/
  config-verification notes: is this tool even used here, does a claimed
  touchpoint actually hold, does existing script logic still cover this
  release. E.g. "azure-cli has no bespoke touchpoint anywhere in this
  repo," "duckdb is actively used by this machine's shell config," "the
  existing warning and control flow in install_macos_sw remain accurate as
  written." Keep these — they're genuinely useful — just route them to
  `context`, not `relevancy`.
- **`release_inventory[]`** — bookkeeping about which releases exist in the
  current→latest range (e.g. "two releases landed: 2026.06.09 and
  2026.07.04"). This is inventory about the range itself, not a claim about
  tool behavior; it doesn't belong as prose under any content group or
  under `context` — one `{version, link}` entry per release.
- **Pure absence-of-change statements** — "no mention in the release notes
  of any change to X" with nothing else to say. These aren't findings at
  all; per the next section's filler-suppression rule, omit them entirely
  rather than filing them anywhere.

### Don't Author "I Checked, Found Nothing"

An empty `relevancy`/`context` array is the normal, expected result for most
tools on most runs — it does not need an item that says so. Seen this run:
"None of the 18 CVEs... apply to this repo's curl usage pattern," "No
relevant change found (checked commit range for 'focus' keyword)," and,
filed under FIXES as if it were a real changelog bullet, "No breaking
changes identified between 1.95.0 and 1.96.1." All of these should have been
*nothing* — omit the item entirely rather than narrating the due-diligence
that produced an empty result.

**Exception — vendor genuinely publishes no detail.** When a release
happened (so it's worth acknowledging) but the vendor's own notes are just a
non-answer ("This release includes security improvements. Updating is
recommended.", with nothing technical ever published), don't manufacture 1-2
bullets restating that non-answer — render a single small compact tag
instead (e.g. "No detailed changelog published"), with the link living once
on the tool's canonical changelog reference, not repeated per bullet. Seen
this run: Slack's research produced two redundant SECURITY bullets citing
the identical release-notes link, one restating the vendor's non-answer and
one explicitly noting the vendor doesn't publish detail — should have been
one compact tag.

### Suggestions Are Always `kind: "edit"`

Suggestions authored here are always `kind: "edit"` (a concrete
Brewfile/dotfiles/config change): target file, rationale, motivating link,
and a short `diff_preview`. Only suggest what the changelog actually
motivates. No edit suggestion is fine — most tools just get headliners. Do
**not** author the plain "upgrade this tool" suggestion — that's a
`kind: "upgrade"` suggestion synthesized mechanically in step 4
(`references/assembly.md` §Baseline Suggestion Synthesis) for every tool,
not something to duplicate here.

### Config Status

Per tool, compute `config_status`:

1. Grep targeted `git log --oneline -- <files you're already inspecting for
   relevancy>` (its Brewfile line, its `tasks/*.sh` section, its dotfiles
   config) — beyond just the last 20 commits in `repo_context` — for a
   commit whose message references this tool or its version.
2. Check
   `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md` (the
   audit trail every prior session's machine-local upgrades get appended to
   — `references/apply.md` §Push and Terminal Status) for a prior entry
   naming this tool.
3. If either shows the config was already touched for a version ≥ today's
   `latest_version`: `state: "up_to_date"`. If touched for an *older*
   version and this run's headliners describe something that could
   invalidate that fix's own reasoning (grep the fix's own commit
   message/diff for keywords that also appear in the new headliners — e.g.
   "libkrun", "Apple Silicon", the specific flag/requirement named): `state:
   "needs_attention"`, with `detail` explaining specifically what changed
   since. Otherwise `state: "unknown"` — don't guess a verdict without a
   citable diff between the old fix and the new change.
4. Cite the evidence (commit hash + subject, changelog.md entry date) in
   `config_status.evidence[]` same as any other finding.

This deliberately reuses the same "cite it or don't claim it" discipline as
`relevancy[]` — a `needs_attention` verdict without a concrete diff between
what changed is worse than no verdict at all.

**A `"needs_attention"` verdict must come with at least one suggestion
addressing it** — flagging a possibly-stale fix and then giving the user
nothing to act on just moves the "did anyone check this?" question one
level up without answering it. If the re-check concludes the old fix still
holds after all, that's `"up_to_date"`, not `"needs_attention"` with no
suggestion.

### Watch Items (Reading)

`config_status` above is backward-looking: "was this tool's config already
handled." Watch items are forward-looking: "the user flagged an ongoing
concern about this tool — does *this run's* changelog touch it." Example:
after investigating cursor-cli's shell-integration hook, the user wants any
future cursor-cli changelog mentioning shell-integration/recording to be
called out automatically, not re-investigated from scratch or missed.

**File**: `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/watch-items.json`
— a sibling to `changelog.md`, same directory, created on first use. Shape:

```jsonc
{
	"cask:cursor-cli": [
		{
			"topic": "shell-integration / session recording",
			"note": "User wants any change to cursor-agent's shell hook or `agent record` behavior called out — see 2026-07-06's investigation: install-shell-integration execs `agent record` on every new shell via ~/.zshrc, zsh-only, undocumented data handling. User implemented an on-demand cursor-record() function instead of the vendor's always-on hook.",
			"added_at": "2026-07-06"
		}
	]
}
```

One entry per concern (a tool can have several); `topic` is a short phrase
the next run's research subagent matches against its changelog content,
`note` is the fuller context so a hit can explain itself without re-deriving
everything.

**Reading watch items** (this is the research-time half of the workflow;
the write side — appending a new entry during step 7's `tool_comments`/
`discuss` investigation — is `references/apply.md` §Watch Items, which
cross-links back here): before researching, check whether your assigned
tool(s) have any `watch-items.json` entries and include their `topic`/`note`
in your own context. If this run's headliners/changelog touch a watched
topic, that's not a normal `info` relevancy finding — bump it to at least
`notable` severity (`references/schemas.md` §Report Object) and prefix the
summary so it reads as a highlight (e.g. "⚠ Watch item hit: ..."), citing
the watch item's `note` as part of the evidence. This is the one case where
relevancy severity is elevated by something *other* than the changelog
content's own weight — a topic the user asked to be told about earns extra
prominence regardless of how minor the change looks on its own.

### Deduplicate Facts

**Deduplicate facts across headliners and relevancy for the same tool
before returning.** It's easy to restate one underlying change twice — once
as a headliner bullet, once as a relevancy finding citing the same
PR/commit to reach the same conclusion (seen this run: codex's CI/release
signing migration to Azure Key Vault + rcodesign showed up as both a
headliner and a full relevancy finding about the same quarantine
workaround). Once you've drafted both arrays, check whether any headliner
and relevancy finding describe the same underlying change; if so, keep it
once — normally as the relevancy finding, since that's the more specific,
actionable placement — and drop or trim the headliner rather than shipping
both. Do this self-check every time rather than assuming it won't happen;
it happens by default when a change is both changelog-worthy and relevant
to this setup.

### Scope-vs-Changelog Separation

**Separate a changelog fact from a note about *this machine's* scope or
usage of the tool.** "v5 dropped X" is a changelog fact (Security/Fixes/
Features); "this tool isn't currently in the Brewfile" or "we don't use the
feature this release changes" is context about *this setup*, not a change
in the tool itself — keep the two apart in your returned object
(`relevancy[]` for the former is fine, but don't write a scope/usage
observation as if it were a headliner). Rendering pulls context-flavored
notes into their own section (`references/rendering-report.md` §Page
Layout) instead of mixing them into the changelog groups, so return them in
a way that's cleanly separable — don't bury a scope note as an extra clause
on a changelog bullet.

### Bespoke `tasks/*.sh` Setup Testing

Some tools have setup logic in `tasks/install.sh`/`tasks/config.sh` beyond a
plain package-manager command (podman's `install_podman_intel`, custom
`config_duti` mappings, etc.). The default posture for these is **not**
"mark the baseline upgrade `auto_runnable: false` and tell the user to
re-run `install.sh` themselves" — that punts on exactly the kind of fix this
skill exists to produce, and leaves the task script's own code un-updated
and untested. Instead, this is the research-time half of the workflow (the
apply-time tail — how an accepted, tested fix actually gets applied — is
`references/apply.md` §Bespoke Setup Execution, which cross-links back
here):

1. **Read the current function implementation** and compare it against this
   run's headliners/changelog to judge whether the function's own logic
   (not just the package version) needs to change — e.g. a
   removed/renamed flag, a changed default, a new required step.
2. **If a change is needed, identify the specific underlying command(s)
   affected** — not the whole function, and never the whole
   `tasks/install.sh`/`./setup.sh install` entrypoint (that has broad,
   unrelated side effects and is exactly what CLAUDE.md's setup-script rule
   exists to prevent running unattended).
3. **Test those specific commands directly, non-destructively, before
   proposing anything.** This is the load-bearing safety rule: never run a
   test that could affect the user's real state (e.g. reinitializing their
   actual `podman-machine-default`, overwriting a real duti association).
   Prefer, in order: read-only inspection (`--help` output, `--version`,
   dry-run/`--dry-run` flags where the tool has them); a throwaway-named
   resource created and torn down within the same test (a scratch podman
   machine, a temp directory) — see the worked example in
   `references/schemas.md` §Report Object's
   `brew:podman:fix-machine-init-flags` suggestion; if neither is possible
   for a given tool, that specific fix is not verifiable this way — fall
   back to the `auto_runnable: false` / `manual_reason` path for it
   (`references/schemas.md` §Report Object) instead of guessing.
4. **Propose a normal `kind: "edit"` suggestion** targeting the
   `tasks/*.sh` file, with a `diff_preview` reflecting the *tested* fix and
   a `rationale` that says plainly what was tested and how — this is the
   one category of suggestion held to a higher evidence bar than a plain
   changelog-motivated edit, precisely because machine-setup code is more
   consequential to get wrong than a comment or a Brewfile line.

The baseline `upgrade` suggestion (added in step 4,
`references/assembly.md` §Baseline Suggestion Synthesis) stays
`auto_runnable: true` regardless of any of the above — a bespoke-setup
problem in the *surrounding* task code is not a reason to block the
*package* upgrade itself from auto-running. Only mark it `false` if no
command for this tool is safely testable at all, which should be rare.

### Schema Strictness

**Hold yourself to the exact schema shapes** (spelled out in the research
prompt): `evidence` is always an array, suggestions always use
`title`/`target_files`/`rationale`/`motivating_link`/`diff_preview`. Loose
shapes (bare strings, ad-hoc `description` fields) force hand
normalization during assembly and have caused real rework.

### Depth by Tool

Node semi-detailed (security advisories, breaking changes, notable features
per minor); other runtimes coarse (breaking changes and majors only);
everything else proportional to how much the user configures it.

### Heterogeneous Hosts

**The fleet has heterogeneous hosts** (until the eventual nix migration):
`Brewfile` manifests the Apple Silicon host(s), `intel.Brewfile` the Intel
host(s), and `tasks/*.sh` contain arch-conditional blocks. The collector's
`machine` block describes only the host running this review. Assess impact
per affected host/manifest — the same update can be `incompatible` on one
host and desirable on the other (e.g. an ARM-only major on an Intel
machine). Set severity to the worst affected host, spell out the per-host
split in `detail`, and make each suggestion's `target_files` name the
specific manifest(s) it touches (a Brewfile edit usually needs a decision
about its intel counterpart, not a blind mirror).

### Brew-Health Enrichment

**Interconnection is the value.** Findings are often causally linked — an
untrusted tap is *why* its kegs show as "no formula"; a deprecated cask may
also appear in the same review as a plain version update. Research (or the
orchestrator authoring the brew-health file directly, per the Tiering
section above) should connect these: recommend trusting the tap that owns
an orphaned-keg backend rather than reinstalling; recommend migrating a
deprecated cask rather than updating it.

### Pinned Tools

Never suggest unpinning a pinned tool unless the blocking reason is
verified gone in the new version — the pin exists because an upgrade broke
something. Treat a pin as a signal that this tool needs a real look, not a
reason to skip it (collection includes pinned formulae for exactly this
reason — see `references/collection.md` §Version Sources).
