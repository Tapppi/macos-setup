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

**The checkable claims in this document have tests.**
`scripts/test_research_guidelines.py` asserts them against this file, the
prompt template and `schemas.md` — that the routing test precedes the bar, that
the self-test tags rather than deletes, that no numeric volume target reaches a
per-tool agent, that nothing sends a checker at the Intel manifest. Run it after
editing any of them. The reason it exists is measured: the §Watch Items
(Proposing) text was **byte-identical** across three runs that produced 11, 5
and 8 proposals, so a rule here can be wrong, or quietly removed, without
anything failing.

Table of contents:
- Part 1 — Orchestrator: Dispatch
  - Prompt Template
  - Tiering: Individual-Focus, Batched, Brew-Health, Skill-Drift
  - Word-Boundary Grep Rule
  - Spawning and the Output-File Contract
  - `research-status.json` Group Updates
  - Failure Handling
- Part 2 — Subagent Quality Bar
  - What You May Touch
  - Prior Findings Are Hypotheses
  - Items Are Outward-Facing Changes
  - One Change, One Item
  - Tags vs. Severity — Independent Axes
  - CVE Severity Capture
  - Security Items: Direction Decides the Display
  - Links
  - Local Findings Are the Point
  - Classify Non-Changelog Findings Correctly
  - Don't Author "I Checked, Found Nothing"
  - The Noise Floor
  - Current → Target Is the Only Frame
  - Suggestions Are Always `kind: "edit"`
  - Config Status
  - Standing Notes: Three Stores
  - Research-Method Notes vs Watch Items
  - Writing a Research-Method Note
  - Watch Items (Reading)
  - Watch Items (Proposing)
  - Before You Propose a Standing Note: the Self-Test
  - There Is No Volume Target — and Here Is Why
  - Deduplicate Facts (One Array, Same Discipline)
  - Scope-vs-Changelog Separation
  - Bespoke `tasks/*.sh` Setup Testing
  - Write `config_status` Before `suggestions[]`
  - Schema Strictness
  - Depth by Tool
  - One Host, One Manifest
  - Brew-Health Enrichment
  - Skill-Drift Enrichment
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

### Tiering: Individual-Focus, Batched, Brew-Health, Skill-Drift

Literal one-subagent-per-tool stops scaling once the candidate list gets
large — most outdated tools (a plain patch-bump brew formula, a mise runtime
with no bespoke setup) need only a quick changelog skim, and a dedicated
subagent per one is wasted overhead. Group tools into two tiers instead,
plus one standing tier per non-version source:

- **Individual-focus**: one subagent per tool, for any tool with a real
  touchpoint in the setup repos — a bespoke `tasks/*.sh` function (grep
  `tasks/install.sh`/`tasks/config.sh`), a dotfiles config file, or a
  Brewfile pin/comment naming it. These need the full local-findings/
  config_status depth this skill exists to produce, and batching them would dilute that.
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
- **Skill-drift group** (see `references/collection.md` §Skill-Drift
  Collection for the source shape, and §Skill-Drift Enrichment below for
  what this tier does): the `skill_drift` findings get their own dedicated
  subagent, individual-tier for the same reason the brew-health group is —
  every finding is repo-touchpoint work by nature, since the thing that
  drifted *is* a file in the dotfiles submodule. It writes one research
  element per finding, matching each finding's `id`. Same two conveniences
  as the brew-health group: a finding whose collect-default remediation
  already says everything useful can simply be left out of the file
  (`assemble.py` falls back to that default), and the orchestrator can
  research the group directly rather than spawning a subagent when the set
  is small. One thing this tier does **not** do is re-derive drift: the
  detector's tree-hash verdict is exact and is not a subagent's to
  second-guess (§Skill-Drift Enrichment).

### Word-Boundary Grep Rule

**Use word-boundary grep (`grep -wn`/`-wni`) to find touchpoints, never
plain substring matching.** A substring grep produces false positives that
feed wrong context to a subagent — e.g. `grep cloc` matches inside `clock`/
`wall-clock`, `grep go` matches inside `mkdir -m go=` or `go to`. A tool that
looks like it has a bespoke touchpoint because of a substring collision gets
miscategorized into the individual tier for the wrong reason (or worse, a
real touchpoint gets diluted by unrelated grep noise in its context). Re-run
suspicious hits with `-w` before trusting them.

**`dotfiles/config/agent-skills/**` is vendored third-party skill content.**
Grep hits there are almost never a real touchpoint — they are somebody else's
documentation and scripts, which happen to name the same tools the user
installs. Ignore them unless the tool is genuinely configured there. This is a
false-positive filter on the tiering decision specifically: a batch of casks
looks like it has bespoke setup because a vendored skill's README mentions
each one, and the tier moves for a reason that does not exist.

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
paths it may scan for local findings, and the two repos' `recent_commits` from
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
instead of branching on tier.

**Each array element carries a closed set of top-level keys**
(`items.RESEARCH_KEYS`, published in `contract/contract.json`). The
validator reads exactly ten:

- `id` — `{source}:{name}`, matching a collect.sh candidate, so assembly
  can match the element back up. Required.
- `items` — the findings array, one element per real change, per
  `references/item-schema.md` §2. The substance of the file (below).
- `links` — typed links (`references/schemas.md` §1.2): the canonical
  changelog, release pages, official blog posts.
- `config_status` — the re-verification verdict (§Config Status below).
- `suggestions` — proposed edits, structural changes and memory proposals
  (§Suggestion Kinds below).
- `release_inventory` — which releases exist in the current→target range,
  one `{version, link}` entry per release.
- `vendor_silent_categories` — categories where the vendor's own notes for
  this range are pure non-detail (§Don't Author "I Checked, Found
  Nothing").
- `flags` — optional assertions about your own items: `has_security`,
  `has_breaking`, `worst_severity`, `local_findings`. The validator
  recomputes every one and its value wins; a mismatch is
  `E-FLAG-DISAGREE`, which says your items do not say what you think they
  say.
- `research_error` — your own statement that research failed for this
  tool; it is then listed with versions only.
- `cask_sudo_hint` — set `false` on a cask you verified installs without
  admin rights; assembly otherwise assumes a cask's installer needs sudo.

Five more — `name`, `source`, `current_version`, `latest_version`,
`pinned` — are recognized and **ignored**: candidate identity a checker
may echo back from its prompt, with `collect.json` authoritative for
every one of them.

**Anything else is rejected loudly.** A key this contract does not
recognize is kept verbatim in the tool's `quarantine[]` and reported as
`E-RESEARCH-UNKNOWNKEY`, and the tool is held for review with its upgrade
no longer pre-accepted. Nothing is lost and nothing is silently accepted.
In particular the retired shape — `headliners`, `relevancy`, `context`,
`notable`, `cve_severities` — is **rejected**, not read: `items[]` and
its eight tags are the single successor to all five. A checker carrying
the old habit meets the correction here, not in a finding.

`items[]` is per `references/item-schema.md` §2 — 28 fields, closed
vocabularies. Each element is one real change carrying `tags` (a closed
set of eight), one `severity`, an `anchor` the validator derives the id
from, and the optional `change`/`local`/`security` blocks. The five
validator-only flags — `security_only`, `impact`, `risk_level`,
`review_bucket`, `pre_accept` — are **forbidden** on checker output,
inside `flags` or at top level (`E-FLAG-FORBIDDEN`): a per-tool checker
has no view of the corpus and does not decide them. An item that answers
a stored watch item for its tool carries `watch_hit`
(`references/item-schema.md` §2.5): an object whose `topic` is the
verbatim topic string from `{{STANDING_NOTES}}` — see §Watch Items
(Reading).

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

**Record the tiering decision, not just the groups.** Each group carries the
`tier` it was placed in, and the file's `scope` block records how many
candidates were collected against how many were actually tiered into groups,
plus any restriction the user asked for.

Without this, a scoped run and a full run produce indistinguishable session
dirs: 22 groups either way, no record of which tools got individual depth,
which were batched, or which were never researched at all. The tiering
heuristic is documented above; the *decision it produced on this run* was
recorded nowhere, so nothing downstream could tell "this tool got a skim
because it has no touchpoint" from "this tool was not in scope". Both look
like a thin result.

### Failure Handling

On subagent failure/timeout, set `research_error` and keep the tool listed
with versions only.

---

## Part 2 — Subagent Quality Bar

**A research subagent reads this section in full, every run, before writing
findings.** Everything below applies to every tier (individual, batched,
brew-health, skill-drift) unless a rule says otherwise.

### What You May Touch

This is the discipline that governs every checker, not only the ones doing
bespoke-setup work. It is short and it is absolute:

- **Any test you run against the live machine must be non-destructive and
  read-only**, unless §Bespoke `tasks/*.sh` Setup Testing explicitly sanctions
  a throwaway scratch resource for what you are doing. Reading a config,
  running `--version`, grepping a repo: yes. Anything that writes, installs,
  upgrades, or changes state: no.
- **Never run `setup.sh`, `tasks/*.sh` or `dotfiles/bootstrap.sh`.** These
  modify system configuration, install software and require `sudo`. The repo's
  own `CLAUDE.md` says the same thing; a research subagent has no exception to
  it. If a question can only be answered by running one, the answer is that you
  could not verify it — say so, and say what you would have run.
- **Never write into the session directory except your own output file.**
  Every other file there belongs to another group or to a deterministic step,
  and one stray write costs a run that has already spent its expensive part.
- **The setup repos are the user's live checkouts.** Read them; never edit
  them, never `git` anything in them, never leave a file behind.

The paths you may scan are given in your prompt. Staying inside them is not a
courtesy: a research run happens on a machine mid-work, and the only reason it
is safe to spawn twenty of you at once is that none of you writes anything.

### Prior Findings Are Hypotheses

Your context may include findings from previous runs — things a prior review
believed about this tool. **Treat every one as a hypothesis to test, never as a
fact to carry forward.**

- **Evidence it yourself**, against *this* run's current → target range, from
  the sources you would have used had nobody told you. Cite what you found, not
  what you were told.
- **If your own evidence does not support it, drop it. Say nothing.** A prior
  finding you could not confirm is not a finding, and it is not a "possible"
  one either.
- **If your evidence supports it, write it as your own finding with your own
  citation.** Do not write "a prior review found X and it still holds" — write
  X, with the evidence you have.
- **Nothing reaches your output on the strength of history alone.**

A hypothesis is a place to look. It is never an answer, and it is never a
reason to propose anything. Last run, **six of eight** watch-item proposals
traced to a hint in the prompt that named a candidate; the only two the prompt
did not name are the only two that survived review. Being handed a candidate is
not evidence that a candidate exists.

The same asymmetry applies to a hypothesis you *disprove*. "A prior review said
v5.3 flips this default; it does not — the flag was reverted in 5.3.1, here is
the commit" is a real finding and worth writing — an item, typically `chore` at
`info`, citing the reverting commit. Silently not
mentioning a disproved hypothesis leaves the next run to rediscover it.

### Items Are Outward-Facing Changes

*Item* here means anything you report about what changed in this tool —
an element of `items[]`, whatever its tags.

**Project-internal maintenance never becomes an item.** Repo upkeep,
convention changes, documentation updates, CI and release-tooling churn, test
refactors, internal renames, lockfile bumps that change nothing anyone
installs — none of it is a finding, at any tag or severity. Do not write it
down and then rate it low; do not write it down at all.

The test is one question:

> **Did anything change for a person who uses this tool without reading its
> repository?**

If the only way to notice is to read the project's own commits, issue
templates or CONTRIBUTING file, it is internal. A rewritten test suite, a
switch of CI provider, a new linter in the pipeline, a docs site
redesign — all internal, however large the diff.

Three boundaries worth stating, because they are where this gets misapplied:

  - **Internal work with an outward consequence is outward-facing.** "Switched
    the release pipeline to reproducible builds, so the published binary's
    checksum now changes" is a real item — the consequence is, even though the
    work was internal. Cite the consequence, not the work.
  - **`chore` is not the place to put internal maintenance.** The `chore` tag
    is for a real, *outward-facing*, cited change that has no consequence for
    any reader of this report. Internal maintenance is not a low-consequence
    item; it is not an item.
  - **This is not the noise floor.** The noise floor (below) decides what to
    delete from items you have already written, and it has a hard boundary
    because a deletion there can approve an update. This rule runs earlier and
    asks something else: whether there was ever an item to write. Nothing in
    the deterministic layer enforces it — a regex that dropped
    "internal-looking" items would be exactly the behaviour this pipeline
    forbids — so it holds here or it does not hold at all.

Read this together with §Don't Author "I Checked, Found Nothing": an empty
result for a tool whose whole range was internal maintenance is the correct
result. Say nothing rather than reporting the maintenance to fill the space.

### One Change, One Item

Items cover the whole current→latest range, not just the newest release.
Skim actual release notes/changelogs — don't guess from version numbers.
**One real change per item — split compound bullets before tagging
them.** A changelog often bundles unrelated changes into one sentence
("added X and Y, plus fixed CVE Z"); if you write the bundle as one item,
it gets tagged by whichever half feels most severe and the rest of the
content is lost (seen this run: rust's "added
assert_matches!/Copy-range-types, plus fixed two Cargo CVEs" got filed
entirely as security — the macro/feature content never showed up tagged
`feature` at all). Split first, tag each resulting item independently;
both can cite the same source link (`change.link_index`) since they came
from the same release.

**There is no item cap.** The old ≤6-headliner budget is retired with the
array that carried it: a count invites padding and rationing in equal
measure. What kept the budget honest is answered elsewhere now — the
noise floor (below) decides what earns a line at all, the dedup pass
(§Deduplicate Facts) collapses restatements, and what the budget used to
catch survives as N8: one change spent as many items is redundancy,
however many items the tool carries.

### Tags vs. Severity — Independent Axes

**Tags are topic, not urgency — the two are independent axes.** An item's
tags (`references/item-schema.md` §2.2's closed set of eight) are decided
purely by *what kind of change it is*, never by how urgent or prominent
it feels relative to other items you're also reporting for the same tool.
Severity is a separate, per-item property — "how much does this matter to
this machine" — that drives visual weight within whichever content group
the tags derive (`references/rendering-report.md` §Page Layout); it never
changes the tags. Seen this run: yt-dlp's "restricts --exec command
templates to safe string conversions... to close a command-injection
footgun" is unambiguously `security` by topic, but got filed as a plain
note, apparently because it read as less prominent than the CVEs already
on the card — it should have kept the `security` tag with a lower
severity, not lost the tag. For contrast, yt-dlp's "Minimum recommended
Python raised to 3.11, Node to v22..." is a correctly-tagged `packaging`
item — genuine compatibility/requirements info that is not security, a
fix or a feature by topic at all.

### CVE Severity Capture

The card shows `security.cve_ids` as a count, not a list — the user's own
words, "no need to list the CVEs in per-tool, we need to trim those down to
allow faster review". What replaces the list is a severity breakdown, and a
breakdown assembly cannot compute on its own: it has no network and no
advisory database. So research supplies the per-id ratings it read, on the
item that carries the CVE: the item's `security` block's
`rating`/`rating_basis` (`references/item-schema.md` §2), and assembly
rolls them up into `security.severity_counts`.

**`rating` is `critical | high | medium | low | unknown`** — the CVSS
qualitative bands, because every source below already speaks them. **This is
deliberately not the item-severity vocabulary.** Item severity answers "how
much does this matter to *this* machine"; a CVE rating answers "what did the
issuer rate the flaw". Seen this run: teamviewer's `CVE-2026-19042` carries
CVSS 8.8 and is Linux-client-only, so it is `high` and irrelevant at the same
time. Collapsing the two axes is how that item would have read as the most
urgent thing on a macOS card.

**`rating_basis` is `vendor | nvd | cvss | unrated`** — where the rating came
from. `cvss` means you banded a numeric score the source published (≥9.0
critical, 7.0–8.9 high, 4.0–6.9 medium, 0.1–3.9 low); `unrated` means nobody
published one. A rating whose basis is `unrated` is read as `unknown` and
reported (I-6, `E-SEC-RATING-UNBASED`), because a rating with no source is
not a rating.

**Where the severity comes from, in priority order:**

1. **The page you already fetched for the items.** Free, so do it for
   every id you see. Vendors publish ratings inline far more often than it
   feels: Chrome's release blog tags each entry (`[$25000][…] Critical
   CVE-…`), Firefox's MFSA lists an impact per advisory, Node's
   security-release post lists a severity per CVE, MSRC gives a CVSS per CVE
   (this run has 10.0 / 9.6 / 8.8 / 7.5 for `cask:microsoft-teams`, all from
   one page), and PostgreSQL's advisory index gives CVSS for `brew:libpq`.
2. **The vendor's own advisory page**, when the release notes list bare ids.
3. **NVD**, when the vendor published no rating at all — and expect a dead end
   as often as not: NVD analysis lags disclosure by days to weeks, so a CVE
   published this month frequently has no score yet.
4. **Nothing.** Record `unknown`/`unrated`. This is a correct outcome, not a
   failure.

**Never derive a rating from how the description reads.** "Remote code
execution" is not evidence of `critical`, and "memory leak" is not evidence of
`low`. This is the same doctrine as §Links' "confirm you actually fetched the
page you're linking" and assembly's `cve_count`-vs-`cve_claimed_count` split:
the report never carries a number it cannot attach to a source.

**The fetch budget, because a tool with 18 ids cannot have 18 advisories
fetched.** This run's real distribution — `cask:google-chrome` 18 ids,
`cask:firefox` 14, `mise:node` 12, `brew:rsync` 10, `mise:go` 10,
`brew:nmap` 6, `brew:gh` 5, `brew:libpq` 4, everything else ≤4; 99 ids across
41 tools. So: **rate free from the page you already read, for every id; then
at most 3 extra fetches per tool** (per tool, not per batch), spent on the
ids whose items you expect to clear the security display bar — rating
`critical`, exploited in the wild, `direction == "reaches"`, or severity
`warning`+ (`references/item-schema.md` §Security display) — and on nothing
else. Every other id is `unknown`. "The vendor's prose singles this one
out" is not a fourth budget: an id worth a fetch on that basis is an id
whose item belongs on the card, so decide that first and let the budget
follow.

**`unknown` dominating is the expected state, not a degraded one.** Measured
on this run's text: 22 of 99 ids carry a rating the research had already read,
and 24 of the 41 security-bearing tools have no extracted ids at all. The page
is built for that: it shows the ids count, the graded count, and only the
graded classes that are non-zero. A subagent that fetched 18 Chrome advisories
to avoid an `unknown: 14` cell spent the run's budget on the number least
likely to change a decision — the reviewer is taking Chrome either way.

**A CVE-less advisory never enters the rollup.** `severity_counts` is
rolled up over CVE ids; a vendor-only advisory has no id to key
(this run: wireshark's 28 `wnpa-sec-*`, tailscale's `TS-2026-011`,
teamviewer's `TV-2026-1009`, and iproute2mac's command-injection fix, which
was never assigned one at all). Its item still carries the full `security`
block — `cve_id: null`, the vendor's id in `advisory_id`, and its
`rating`/`rating_basis` on their own terms — and still clears the display
bar on its own merits; it just never lands in `severity_counts`.

Shape and validation: `references/schemas.md` §1.9; the rollup and its sum
invariant: `references/assembly.md` §Severity Rollup and the Sum Invariant.

### Security Items: Direction Decides the Display

The security content shown inline on a tool's card is **derived, not
selected**. The validator lists an item in the card's security block when
its `rating` is `critical`, or it is `exploited_in_wild`, or its
`local.direction` is `"reaches"`, or its severity is `warning`/
`incompatible` (`items.is_security_display_item`; `references/schemas.md`
§1.9's `display_item_ids`). There is **no cap** and nothing to rank: the
old cap-of-3 `notable[]` invited padding and evicted the wrong items —
openssh filled all three of its slots with items whose own summaries said
the fix does not reach this machine. Whatever clears the bar is shown;
everything else still exists, still renders in the collapsed detail, and
still carries its findings.

So your job is not to pick the display; it is to write each
`security`-tagged item's fields accurately, because the display follows
them:

- **`local.direction` is the load-bearing field, and you set it
  explicitly.** `"reaches"` means a concrete touchpoint on this setup — a
  file, a service, a call site, a running process — in the same
  evidentiary sense every `local` block demands, and I-14 requires a
  reaching item to carry evidence. "The tool is installed" is not a
  touchpoint; if it were, every item would reach and the field would
  carry no information.
- **A `does_not_reach` security item is still worth writing.** A third of
  this run's security findings exist precisely to say the fix does *not*
  reach this machine: openssh's sshd fix landing on Apple's
  `/usr/sbin/sshd` rather than Homebrew's, `cask:microsoft-teams`' patch
  that "carries no security benefit here", `brew:fd`'s terminal-gated
  sanitization, `mise:python`'s own bundled expat, `brew:rsync`'s
  rrsync/nixpkgs split. A negative-direction finding is the answer to a
  question the reviewer would otherwise have to ask — and it stays off
  the inline display unless its rating or severity puts it there, which
  is correct.
- **A reaching security fix is not "impact".** `impact` reads
  `local.effect == "risk"` — a risk *of taking* the update — and a
  security fix that lands on something this machine runs is a reason to
  *take* it, so its `effect` is `benefit`. Treating a reaching fix as
  impact emptied the `security_auto` bucket across a whole live run
  (`references/assembly.md` §`impact`). Read the direction and effect off
  your own finding and write both.
- **Empty is the common, correct result — do not pad.** A tool with six
  medium CVEs and no touchpoint renders a severity strip and no inline
  security items, which is exactly the layout the user asked for. Seen
  this run: `brew:redis` (its own items say "all nine of 8.10.1's fixes
  are server-side") and `brew:nmap` (six bundled-libssh2 CVEs, no
  touchpoint) both correctly show none. On the projected run, 24 of the
  41 security-bearing tools come out empty.

**Worked fields from this run:**

| Tool | ids | what the fields say | what the card shows |
|---|---:|---|---|
| `brew:libpq` | 4 (28 claimed) | 3 of the 28 land in code this machine runs — those three items are `reaches`; the fourth id is server-side, `does_not_reach` | the three reaching CVEs inline |
| `brew:gh` | 5 | `CVE-2026-64654` "lands directly on gh commands this machine pre-approves for agents" — `reaches`; the other four are real and untouched here | one item inline |
| `brew:iproute2mac` | 0 | the installed 1.7.4 executes injected shell commands, reproduced on this machine — `reaches`, with `cve_id: null`, `advisory_id: null` | the run's best example of a displayed security item with no id at all |
| `cask:teamviewer` | 3 | `CVE-2026-19042` is rated `high` (basis `cvss`, 8.8) and is Linux-client-only — `does_not_reach`, severity `info` | the highest-rated CVE on the card stays out of the inline display |

That last row is the whole rule in one line: the rating records what the
issuer said, the direction records what it means here, and neither is
allowed to overwrite the other.

**Every `security`-tagged item carries its `security` block** — I-4
reports the omission (`E-SEC-BLOCK-MISSING`) and the converse
(`E-SEC-BLOCK-ORPHAN`, a block on an item that forgot the tag). And when a
release has security content the vendor refuses to detail, say exactly
that with `vendor_silent_categories: ["security"]` rather than
manufacturing items (§Don't Author "I Checked, Found Nothing") — the
field is load-bearing, not cosmetic: it feeds `has_security` and keeps
the tool out of the pre-accepting buckets.

### Links

Always the canonical changelog for the version range; add release pages for
majors and official blog posts when they exist. Every item and
suggestion should be traceable to a link — an item's through
`change.link_index`. **Prefer the most specific
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

### Local Findings Are the Point

**The `local` block is the point of this skill.** Scan the user's setup
repos — `~/project/github/tapppi/macos-setup` (Brewfile, tasks/, dotfiles/
submodule with shell/git/tmux/Claude configs) and
`~/project/github/tapppi/systems` (NixOS flake) — plus machine facts, for
places the tool is configured or its changed behavior lands, and write
what you find as the item's `local` block: `direction` (does this land on
something this setup runs), `effect` (is that good or bad for me), a
`statement`, and `evidence[]` as path objects
(`references/item-schema.md` §3 — paths only; prose belongs in
`citations[]`). Severity is the item's own: `incompatible` (something
that works here today stops working — requires `reaches` and `risk`,
I-2) > `warning` (a behaviour change here the reader must know before
upgrading — requires a `local` block, I-3) > `notable` (worth reading; no
action needed) > `info`. **A `local` block about a change rides the item
whose `change` motivates it** — an actual changelog fact driving the
finding. If there is no motivating change — you looked, and the finding
is about this setup as it stands — that is not a change item; see the
next section for where it actually belongs.

### Classify Non-Changelog Findings Correctly

**Classify non-changelog findings correctly instead of defaulting them
into `info` change items.** Three different things show up during
research that aren't "a changelog fact that lands on this setup," and
each has its own home:

- **A present-tense fact about this machine** — repo-scope, usage,
  locality, config-verification: is this tool even used here, does a
  claimed touchpoint actually hold, does existing script logic still
  cover this release. When it qualifies a change you are already
  reporting, it *is* that item's `local` block — "ffv/rfv run fzf with
  the matcher disabled" belongs on the speedup item it defuses
  (`direction: "does_not_reach"`, `effect: "none"`), never beside it as a
  second entry. When it stands alone and a reviewer deciding this tool
  would act on it, write a `local`-only item — `change: null`; I-1
  accepts either half alone — e.g. "duckdb is actively used by this
  machine's shell config", tagged for what it is about, at `info` unless
  it changes a decision. When it verifies prior config handling, it
  belongs in `config_status.detail`/`evidence` (§Config Status), not in
  `items[]` at all.
- **`release_inventory[]`** — bookkeeping about which releases exist in the
  current→latest range (e.g. "two releases landed: 2026.06.09 and
  2026.07.04"). This is inventory about the range itself, not a claim
  about tool behavior; it doesn't belong in `items[]` — one
  `{version, link}` entry per release.
- **Pure absence-of-change statements** — "no mention in the release notes
  of any change to X" with nothing else to say. These aren't findings at
  all; per the next section's filler-suppression rule, omit them entirely
  rather than filing them anywhere.

### Don't Author "I Checked, Found Nothing"

Finding no local touchpoint is the normal, expected result for most
tools on most runs — it does not need an item that says so. Seen this run:
"None of the 18 CVEs... apply to this repo's curl usage pattern," "No
relevant change found (checked commit range for 'focus' keyword)," and,
filed as if it were a real changelog fix, "No breaking
changes identified between 1.95.0 and 1.96.1." All of these should have been
*nothing* — omit the item entirely rather than narrating the due-diligence
that produced an empty result. (A scoping statement on a real change is
different: "this fix does not reach us" is that item's `local` block with
`direction: "does_not_reach"`, and §Security Items: Direction Decides the
Display says why those are worth writing.)

**Exception — vendor genuinely publishes no detail.** When a release
happened (so it's worth acknowledging) but the vendor's own notes are just a
non-answer ("This release includes security improvements. Updating is
recommended.", with nothing technical ever published), don't manufacture
one or two items restating that non-answer — name the category in
`vendor_silent_categories` (e.g. `["security"]`), which renders a single
small compact tag in its place, with the link living once on the tool's
canonical changelog reference. Seen
this run: Slack's research produced two redundant security bullets citing
the identical release-notes link, one restating the vendor's non-answer and
one explicitly noting the vendor doesn't publish detail — should have been
the one field.

### The Noise Floor

78 tools and 106 decisions in one run. Attention spent on a fact that cannot
change a decision is attention taken from one that can, so:

> **A fact earns a line only if a reviewer who believed the opposite would
> decide differently.** Everything else is research you did, not information
> the reviewer needs.

Two corollaries. The first keeps the rule from over-firing; the second keeps
it from changing a decision it has no business changing.

**Route, trim, retag or merge before you cut.** Most of what follows is
not deletion — it is relocation into `release_inventory[]` (which releases
exist) or an item's `local` block (what is true of this machine), or a
trimmed clause on an item that keeps its load-bearing half, or two
near-identical items becoming one, or the `chore` tag at `info`: the
explicit way to say "real and inconsequential", so the page collapses it
and convergence can still see it.

**Deletion has a hard boundary, and it is not editorial.** `security_only`
is an `all()` over `items[]`; `impact` and `risk_level` are `any()`s over
the same items; `has_security` reads the `security` tag, the per-item
`security` blocks and `vendor_silent_categories`. All four feed
`review_bucket` and then `pre_accept`, so a
deletion can *approve an update*. Concretely: cut the last
`security`-tagged item and the tool falls out of the security buckets into
`routine`; cut the last `feature` item and it walks the other way, from
`security_mixed` into `security_auto`, and pre-accepts itself. **Delete
only these, and only when the text carries no CVE id, no "fixes N CVEs"
claim and no item carrying `watch_hit`, and never the tool's last item:**

| Tags | Deletable at |
|---|---|
| `fix` alone | `info`; `notable` only when the item has no `local` block |
| `packaging`/`chore`, no other tag | `info` |

**A `security`-tagged item is never deletable as noise, at any severity.**
The only rule that may remove one is the dedup rule below, and only against
another security item on the same tool. Everything the user named as noise
is non-security by tag, so this costs nothing.

If the noise floor tells you to delete something outside that table, it is
telling you the item's tags or severity are wrong, not that the item should
go. A cadence note at `notable` is a mis-rating — nothing that cannot
change a decision is `notable`. A performance item at `info` is an item to
**trim**, because `perf` and `feature` are precisely the signals that say
"this release is more than patches".

**The boundary is no longer encoded in code, and that is deliberate.**
`noise_suppressible()` in `scripts/assemble.py` used to state it as a
predicate; it is **deleted** (`REDESIGN.md` §G, `item-schema.md` §8.1), because
under §C3 the deterministic layer does not delete anything, so the predicate
had nothing left to guard — it had zero production call sites and existed only
for the tests that asserted against it. The rule it encoded belongs to
convergence, and the `chore` tag is the schema's replacement for it: a checker
that judges an item real and inconsequential *says so*, the page collapses it,
and convergence can still see it. Nothing is deleted on the way.

Eight classes. N1–N4 are the ones the user named. Every one carries a
**near-miss** — a real item from this run that looks like the class and must
survive it. A class whose near-miss you cannot state is a class you are not
ready to apply.

**N1 — Release cadence and publication process.** Facts about *when and how
the vendor published*: build numbers of releases you are not taking,
patch-Tuesday chronology, "there is no 8.9", "18.5 was pulled", tag-vs-release
bookkeeping, contributor counts. Seen this run, a `brew:libpq` `chore`
item:
*"PostgreSQL 18.5 was never shipped — the project pulled it over a regression
and went straight from 18.4 to 18.6."* Genuinely interesting to someone
reading advisory metadata, and useless here: the reviewer is on 18.4 going to
18.6, and there is no version choice for the fact to inform. Trim rather than
cut when a bullet is part cadence — `mise:python`'s *"the seventh 3.14
maintenance release — around 499 bugfixes … from 86 contributors … with no API
or ABI changes"* is one load-bearing clause and three ornaments.
**Near-miss:** cadence about the release you are *moving to* is a decision
input — `cask:windows-app`'s *"11.4.0 has no published release notes yet, so
its contents are undocumented"* is why that tool is in `attention` rather than
waved through, and `brew:mpv`'s *"No upstream mpv release exists in this range
… both steps are Homebrew revisions"* is the entire changelog for its update.
The discriminator: does the sentence describe the release you are moving to
(keep), or the sequence behind it (cut)?

**N2 — Regressions in versions you step over.** A defect introduced *after*
`current_version` and fixed at or before `latest_version` is invisible to this
upgrade. Seen this run, on `brew:pkgconf`: *"Going 3.0.3 → 3.0.6
steps over 3.0.4's parse-time unescaping…"* — whose own body ends by
admitting the point, *"this host never runs the 3.0.4-only behavior"*. It
spent an item on a version this machine never ran and pushed the tool's
`why` line. See §Current → Target
Is the Only Frame for the general rule and the one carve-out. **Near-miss:**
`brew:sops`' MAC-computation regression looks identical in shape and is the
opposite, because 3.13.2 *is the installed version* — the regression is live
right now and the upgrade is the fix.

**N3 — Performance micro-details.** A speed or memory number is a fact only
when it crosses a threshold the reviewer would act on. Seen this run, a
`brew:fzf` `perf` item: *"0.74.3 optimizes non-ASCII input: reading accented
Latin input up to 37% faster and CJK input using up to 29% less memory."* —
whose own `local` block already says *"ffv/rfv run fzf with the
matcher disabled, so neither release's speedups reach them"*. Nobody declines
an upgrade because it got faster. **Near-miss:** a defect with an observable
symptom is not an optimisation (`cask:yaak`'s *"constant high CPU usage while
idle"*), and a step change is a fact in its own right (`brew:pkgconf`'s
*"roughly six times faster … on a ~40-module graph"*). A workable bright line:
an order of magnitude, on something you invoke repeatedly. 6× clears it; 37%
does not. Remember these are `perf` items, so the move is to trim to the
load-bearing clause, not to delete.

**N4 — Project-internal conventions, process, docs, packaging.** How the
project runs itself is not a change in the software you run. Seen this run,
a `brew:yt-dlp` `chore` item: *"Build: PyInstaller temporarily pinned to v6.22.0
(#17478) and 38 dependencies updated across two PRs. Affects the released
binaries' build, not the Homebrew formula this host installs."* A bullet that
concludes with its own irrelevance should not have been written. **Near-miss:**
a convention that changes how the reviewer reads the *rest* of the card stays
— `brew:iproute2mac`'s *"No CVE was assigned — the maintainer judged the issue
not major enough … so this fix will never surface in a vulnerability feed"*
tells the reviewer not to read an absent CVE as absent risk, on a card whose
`cve_count` is 0 and whose command injection was reproduced on this machine.
The discriminator: would the fact still be true under a different maintainer,
licence, docs toolchain or CI? Then it is about the project, not the software.

**N5 — Dependency-bump inventory with no stated effect.** A list of bumped
versions is inventory. Seen this run, a `brew:azcopy` `packaging` item:
*"golang.org/x/crypto → v0.54.0, golang.org/x/net → v0.57.0 … (routine
dependency refresh; the vendor does not flag any of them as an advisory
fix)"* — self-refuting in its own parenthesis. It becomes a fact when it names
an advisory (`brew:helm`'s *"Bumped google.golang.org/grpc to v1.82.1 to
address GO-2026-6061"*), crosses a boundary that changes what you run
(`cask:podman-desktop`'s Electron 42→43 Chromium major; `mise:bun`'s
*"NODE_MODULE_VERSION becomes 147, so every native addon … has to be
rebuilt"*), or *is* the release (`brew:mpv`'s ffmpeg 9.0 relink, which makes
mpv's card and ffmpeg's card one decision).

**N6 — Changes that do not reach this platform.** An item whose own text
scopes it to Windows, Linux, s390, or a server component of a client-only
install is not a change here. Seen this run, a `cask:codex`
`security`-tagged item: *"0.148.0 makes sandbox restrictions fail closed for
denied or unreadable paths **on Linux and Windows** …"* — a security item on
a macOS-only fleet. **Near-miss, and this is the important half:** when the
item carries a CVE, a CVSS or a scary name a reviewer might meet elsewhere,
the scoping *is* the finding. `cask:teamviewer`'s *"CVE-2026-19042 (CVSS
8.8) … is Linux-client-only and does not affect the macOS builds"* must
stay — written `does_not_reach`, so the display bar correctly leaves it out
of the inline security list (§Security Items: Direction Decides the
Display). Its aggregate form belongs in a `local` statement, which is where
`brew:libpq`'s *"Client-only install: 3 of the 28 CVEs land in code this
machine runs, 25 do not"* correctly sits — the single most useful line on
that card. Note that under the boundary above, a `security`-tagged
platform-scoped item is trimmed (drop the Windows clause), never deleted.

**N7 — Vacuous items and narrated due diligence.** §Don't Author "I Checked,
Found Nothing" forbids the pure case; two mutations slipped past it this run.
(a) A bullet whose whole content is "nothing notable" is still filler when it
names where you looked: `brew:gcc`'s *"Upstream itemizes nothing in the
announcement — the per-release fixed-PR list lives in the GCC 16 changes
page's 16.2 section…"* documents your navigation of GCC's website. (b) A
bullet with no concrete subject ("internal code cleanups", "various
improvements") carries no fact. **Near-miss:** the same tool's *"16.2.0 is a
bug-fix-only release from the GCC 16 branch: more than 102 regressions and
serious bugs fixed since 16.1.0, with no new features"* is a real
characterisation of the target, and the number is a scale cue. The
discriminator: the keeper says what the release **is**; the cut says what you
**did**.

**N8 — One change spent as many items.** There is no item cap any more
(§One Change, One Item), so redundancy is caught by shape, not by count.
Seen this run, `cask:gcloud-cli` spends **five** near-identical notes on one
theme — the retirement of the `api-registry mcp` / `beta services mcp`
surfaces across 577–582 — so the card contains no answer to "does anything
I run break". One item carries the same decision. **Near-miss:**
`cask:tor-browser`'s two `security` items are identical in shape and
different in content — two distinct ESR rebases carrying two distinct MFSA
sets. N8 fires on redundancy, not on symmetry.

### Current → Target Is the Only Frame

The general rule behind N2, stated once so the rest of the bar can point at
it.

> Every finding is a statement about the difference between the version
> installed **now** and the version this update would install. Nothing else in
> the release history is a finding.

For `current = C`, `target = T`, and an intermediate `I` with `C < I < T`:

| Situation | Verdict | Why |
|---|---|---|
| Behaviour introduced at or before `C`, changed by `T` | **keep** | you have it now, you won't after |
| Behaviour introduced after `C`, still present at `T` | **keep** | new to you |
| Defect introduced at `I`, fixed at or before `T` | **cut** | never on this machine |
| Behaviour added at `I` and removed by `T` | **cut** | end state equals start state |
| Defect introduced at or before `C`, fixed at `I` | **keep** | live right now; the fix is the reason to upgrade |
| Defect present at `T`, no fix yet | **keep** | you are taking it |

Row four is subtler than it looks and produced this run's most misleading
item: `cask:obsidian`'s *"The `obsidian://` confirmation dialog added in
1.13.0 was removed again in 1.13.6, so the `to` alias steps over the gate
entirely"*, filed as a **`security`-tagged item**. A gate added and removed inside
the range leaves the end state identical to the start state — the finding
manufactured a security item out of a no-op.

**Never write "going straight to X skips Y", "do not stop at Y", or "steps
over Y's regression".** The reviewer is not choosing an intermediate version;
the card offers exactly one target. All three phrasings appear in this run and
all three are cuts. Equally: do not reconstruct intermediate history to
explain a net-zero — if the reconstruction ends "so nothing changes here", it
was not a finding. (A *pin* that would stop at an intermediate is a different
thing: that is a local finding about the pin, and the target is then the
pinned version — see §Pinned Tools.)

**The one carve-out: MAJOR security in an intermediate.** An intermediate
security issue answers a different question — not "what changes" but "what was
I exposed to while I sat on `C`" — so it is worth a line. It qualifies only if
it meets **both** tests: severity `critical`, or `high` with a published CVSS
≥ 7.0 (§CVE Severity Capture's recorded value, not an impression); **and** the
exposure required something this setup actually does, evidenced as concretely
as a `local` block demands. A critical CVE in a code path this machine never
enters is still a cut. Frame it explicitly as exposure — and do not write it
as `reaches`, because the exposure ends the moment the upgrade lands and the
inline security display is about what this patch delivers. **No item in
this entire run cleared that bar**, which is the expected frequency: a run
where several items claim the carve-out is a run where the bar is being read
too loosely.

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

Per tool, compute `config_status` — this is a **re-verification of the
current→latest delta**, not a one-time lookup of whether something happened
before. A prior fix is a starting point to re-check, never a permanent
excuse to stop looking: "verify again, just from the version that was
already handled to the newest version" instead of from scratch.

1. Grep targeted `git log --oneline -- <files you're already inspecting for
   local findings>` (its Brewfile line, its `tasks/*.sh` section, its dotfiles
   config) — beyond just the last 20 commits in `repo_context` — for a
   commit whose message references this tool or its version.
2. Check
   `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md` (the
   audit trail every prior session's machine-local upgrades get appended to
   — `references/apply.md` §Push and Terminal Status) for a prior entry
   naming this tool, and note the version **V** that entry/commit was
   written against.
3. Decide the state from V vs. this run's `latest_version`:
   - **No matching commit or changelog entry at all** (nothing names this
     tool anywhere in either source): `state: "unknown"` — there's genuinely
     no prior evidence to re-verify against. The common case for a
     first-time review of a tool.
   - **V ≥ `latest_version`**: `state: "up_to_date"` — prior handling
     already covers the version this run is reviewing; nothing to
     re-verify.
   - **V < `latest_version`**: do **not** default to `"unknown"` just
     because the prior record predates today's `latest_version` — instead,
     re-verify the **V→`latest_version` delta's** changelog for a
     config-relevant change, the same cross-referencing this rule already
     used (grep the prior fix's own commit message/diff for keywords that
     also appear in this run's items — e.g. "libkrun", "Apple Silicon",
     the specific flag/requirement named):
     - Delta **contains** a change that could affect this tool's
       config/setup: `state: "needs_attention"`, with `detail` naming
       specifically what changed in the delta and why it might invalidate
       the prior fix's reasoning, **plus at least one suggestion addressing
       it** (see below).
     - Delta **contains nothing config-relevant**: `state: "up_to_date"` —
       the prior handling still holds; say so explicitly in `detail` (e.g.
       "Reviewed at 5.0.0; nothing in 5.0.0→5.5.1 affects the pin
       rationale.") rather than leaving it blank just because nothing
       changed.
   - **Delta genuinely can't be assessed** (e.g. no changelog/release notes
     exist for part of the range, or the prior fix's own commit message is
     too vague to cross-reference against anything): `state: "unknown"` —
     don't guess a verdict without a citable basis either way.
4. Cite the evidence (commit hash + subject, changelog.md entry date, plus
   whatever delta content backs a re-verified `up_to_date` or
   `needs_attention` verdict) in `config_status.evidence[]` same as any
   other finding.

This deliberately reuses the same "cite it or don't claim it" discipline as
an item's `local` block — a verdict, in either direction, without a concrete look at
the V→latest delta is worse than no verdict at all.

**A `"needs_attention"` verdict must come with at least one suggestion
addressing it** — flagging a possibly-stale fix and then giving the user
nothing to act on just moves the "did anyone check this?" question one
level up without answering it. If the re-verification concludes the old fix
still holds after all, that's `"up_to_date"`, not `"needs_attention"` with no
suggestion.

### Standing Notes: Three Stores

Three things survive a run and reach the next one. They are three different
stores because they answer three different questions, and the last run's eight
watch-item proposals conflated all three into one — two of them were method
notes filed as watch items, and both said so in their own first sentence.

| Store | Scope | Answers | How many exist | Who writes it |
|---|---|---|---|---|
| **Global method notes** | across many tools | how research works *in general* here | **rare** | convergence, by promotion |
| **Per-tool method notes** | one tool | how to research **this** tool correctly | **many** | **you** |
| **Watch items** | one tool | what to tell the user if it happens | **many** | **you** |

**You write two of the three.** A global note has to hold across many tools,
and you can see between one and nine — you are not in a position to know. So
you never propose one: you write the per-tool note and say in its `rationale`
that you think it generalises and why. Convergence reads every tool's output at
once and is the only party that can check the claim, so promotion is its call.
That is also *why* global notes are rare: the entry condition is cross-tool
evidence, and only one reader ever has it. It is not a quota anybody enforces.

Read the "how many" column as a description of the store, not as an allowance
for you. Tools have weird conventions and unusual places to publish, so per-tool
notes and watch items are both expected to be numerous across the fleet.
§There Is No Volume Target says why none of these three has a number attached,
and why you must not invent one.

**Route at the point of writing.** Two questions, in this order.

  1. **Is this about how to research, or about what to report?**

     A method note changes the next *researcher's* behaviour. A watch item
     changes the next *report*. "Never trust this project's release notes, read
     the commit range" is the first. "Tell me if they ever change the
     credential format" is the second.

  2. **If it is a method note: which tool do you write it against?**

     The one whose research it fixes. If it fixes several in your batch, write
     it against each — a note is read by tool id, so a note filed against one
     tool does not reach the others.

     If you believe it holds beyond your batch, **still write it as a per-tool
     note**, and say so in the `rationale`: "this is probably true of every
     project that publishes releases through <mechanism>". That sentence is
     what convergence promotes on. Do not write a global note yourself and do
     not write a per-tool note in general terms to approximate one — "GitHub
     release bodies are sometimes thin" is not a note anybody can act on. Name
     the tool, name the failure, and say where you think it generalises.

Question 1 has a sharper form when the answer feels like "both", and it is the
one that decides every real case:

### Research-Method Notes vs Watch Items

Two different standing notes, two different stores, one question apart.

```
A research-method note answers: how do I research this tool correctly?
A watch item answers:           what should I tell the user if it happens?
```

Before you write either, run the **routing test**:

> **Could a future release's published text plausibly contain words that match
> this topic?**

  - **No** → it is a research-method note. Write it as one. Stop here.
  - **Yes** → it may be a watch item. Continue to §Watch Items (Proposing).

The read path for watch items is changelog-content matching: next run's
researcher looks its tools up and reports a hit when this run's changelog
touches a stored `topic`. **A topic no changelog can contain will never fire.**
Filing one as a watch item does not preserve the knowledge — it files it where
nothing will read it back out.

Two worked examples, both filed as watch items last run, and neither was one:

  **`brew:iproute2mac`** — *"always research this formula by reading the
  `git compare vOLD...vNEW` commit range and the repo's issue tracker, never by
  the release notes, whose emptiness is not evidence that a release is
  cosmetic."* That is an instruction to the researcher. It fires every run
  regardless of what shipped. **Method note.**

  **`brew:nnn`** — *"This is a packaging state, not a release event, so nothing
  in a future current→latest changelog delta would surface a change to it."*
  The proposal disqualifies itself in its own first sentence. **Method note.**

**If your rationale contains a sentence saying no future changelog would
surface this, you have already answered the routing test. Believe it.**

**A method note absorbs the watch item inside it.** Many concerns are half
method and half worry: *"this vendor's release page is boilerplate, and if they
ever changed the credential format we would not see it."* Write the method
note; the worry is its reason for existing. Propose a watch item **as well**
only if the worry independently clears the bar on its own evidence — not
because writing the method note reminded you of it.

Routing is not dropping. Both stores are read back into a future run; they are
read by different readers, at different moments, for different purposes. Putting
a note in the right one is the whole of this section.

### Writing a Research-Method Note

A method note is a durable correction to how a tool gets researched. It is
worth writing when the ordinary path — read the release notes, check for CVEs —
returns a wrong or empty answer **for this tool specifically**, and will keep
doing so.

Propose one as a suggestion in the tool's `suggestions[]` with
`kind: "method-note"` (`references/schemas.md` §1.7b) — same array, same schema
strictness as any other suggestion, just a different shape:

```jsonc
{
	"id": "cask:claudebar:method-where-the-changelog-lives",
	"kind": "method-note",
	"title": "Read ClaudeBar's CHANGELOG.md, not its release bodies",
	"target_files": [],          // nothing to edit
	"command": null,             // nothing to run
	"auto_runnable": false,      // accepting it writes a note, nothing else
	"method_topic": "where the real changelog lives",
	"method_note": "the instruction, written so it reads sensibly copied
	                verbatim into the next run's context — because that is
	                exactly what happens on accept",
	"rationale": "how you know the ordinary path fails here"
}
```

Like a watch item, this is a **proposal, not a write**: nothing reaches the
store unless the user accepts it in the review UI.

**The rationale must name a failure, not predict one.** Good:

> "ClaudeBar's GitHub release bodies are boilerplate — verified: identical text
> across all twelve releases in this range. The real detail is only in
> CHANGELOG.md at the repo root. The prior review read the Releases page and
> concluded the app 'does not maintain a structured CHANGELOG.md', which cost
> this cask a real review."

That names a failure that already happened, to this tool, in a previous run of
this skill. Bad:

> "Release notes for this project are sometimes thin, so a future run might
> miss something."

The second is a worry about a vendor. The first is a correction to a procedure.
If you cannot point at the wrong answer the ordinary path produced — in a prior
run, in this run's own research, or in the source you had to fall back on — you
have the second one, and it belongs in the note's `self_test_failed` tag rather
than in the store unexamined (§Before You Propose a Standing Note covers the tag;
`unwitnessed` is the limb a method note fails).

A method note that is right stays useful for years, and it is cheap: it changes
how one researcher looks, not what the report says. That is why it does not get
the watch item's bar. What it does get is the requirement above — say what went
wrong, and how you know.

### Watch Items (Reading)

This section is the **read** side of the stores above — both of them.

**You are given your tools' entries; you do not go looking for them.** The
dispatching step reads both stores by tool id and puts the matching entries in
your prompt (`references/research-prompt-template.md`'s `{{STANDING_NOTES}}`).
The stores themselves are siblings of `changelog.md`:

```
${XDG_STATE_HOME:-~/.local/state}/tool-update-review/watch-items.json
${XDG_STATE_HOME:-~/.local/state}/tool-update-review/method-notes.json
```

If your prompt carries no entries for a tool, that tool has none — an empty
`{{STANDING_NOTES}}` is a fact, not an omission to go and correct by reading
the files yourself.

The two are read at different moments:

- **Method notes are read first, before you look anything up.** They change
  where you look and what you trust. A note saying "this project's release
  bodies are boilerplate, read CHANGELOG.md at the repo root" is worthless
  after you have already read the release page and concluded there was nothing
  in the range. Read the notes for your tools, then start.
- **Watch items are matched as you read.** They are topics to notice in the
  changelog you are going through anyway.

Method notes carry no reporting obligation: an accepted note is an instruction
to you, and following it is all it asks. If a note turns out to be wrong — the
vendor started publishing properly, the path it names no longer exists — say so
by proposing a corrected `method-note` (§Writing a Research-Method Note; the
witnessed failure is the stale note itself), so the store can be corrected. A
stale method note that nobody contradicts is worse than none, because it sends
every future run to the wrong place with confidence.

The rest of this section is the watch-item half.

`config_status` above is backward-looking: "was this tool's config already
handled." Watch items are forward-looking: "the user flagged an ongoing
concern about this tool — does *this run's* changelog touch it." Example:
after investigating cursor-cli's shell-integration hook, the user wants any
future cursor-cli changelog mentioning shell-integration/recording to be
called out automatically, not re-investigated from scratch or missed.

**File**: `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/watch-items.json`
— created on first use. Shape:

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
cross-links back here): include your tools' `topic`/`note` entries
in your own context. If this run's changes touch a watched topic, that is
not a normal `info` item — set `watch_hit` on the item, give the item at
least `notable` severity (the item's own `severity`, per
`references/item-schema.md` §2.3; the omission is reported as
`W-WATCH-HIT-UNRAISED`), and cite the watch item's `note` as part of the
local finding. This is the one case where an item's severity is elevated
by something *other* than the change's own weight — a topic the user
asked to be told about earns extra prominence regardless of how minor the
change looks on its own, and it is the only reason the tool tolerates a
severity the changelog content alone would not earn.

**The textual channel is retired** (`REDESIGN.md` §I4). `assemble.py` used to
match `/watch[\s\-]?item hit/i` against each relevancy item's `summary` +
`detail` and score a hit at 70 points in `highlights[]` — a magic string, so a
paraphrase ("this matches a watched topic", "flagged per the watch item") made
the hit invisible, the highlight silently never rendered, and the one thing the
user explicitly asked to be told about was the thing that got buried. The
regex is deleted and the literal `Watch item hit:` prefix went with it;
restoring the signal meant adding a field, never the regex.

The replacement is the **structured field** on the item, `watch_hit`
(`references/item-schema.md` §2.5):

```jsonc
"watch_hit": {
	"topic": "shell-integration / session recording"
}
```

- `topic` is copied **verbatim** from the stored watch item's `topic` as
  it appears in `{{STANDING_NOTES}}` — character for character. Not a
  paraphrase, not a restatement, not your own summary of it.
- The validator **grounds** every hit against the session's snapshot of
  the watch-item store (I-20). Four codes report what it finds:
  `E-WATCH-HIT-UNGROUNDED` — the topic names no stored watch item for
  this tool (a rewritten topic grounds nothing, which is why verbatim
  matters); `E-WATCH-HIT-NOLOCAL` — the item carries no `local` block;
  `W-WATCH-UNCHECKED` — the snapshot was absent or unreadable, so the hit
  is kept but could not be checked; `W-WATCH-HIT-UNRAISED` — the hit sits
  below `notable` (reported, never bumped — re-rating is convergence's).
- **A hit carries a `local` block**, because a hit is a statement about
  this setup: say what the change means here, with evidence.
- A grounded hit restores the highlight signal — 70 points under
  `watch_item_hit` — so the one thing the user explicitly asked to be
  told about surfaces at the top of the report instead of being buried.

A *hit* reports that a stored watch item already fired on this release;
it is a different act from *proposing* a new watch item, which is a
`kind: "watch-item"` suggestion carrying `watch_topic`/`watch_note`
(§Watch Items (Proposing)) — writing one when you mean the other either
proposes a duplicate store entry or claims a topic that does not exist.

### Watch Items (Proposing)

The section above is the *read* side — matching against watch items that
already exist. This is the *write* side's other half: **you can propose a
new watch item yourself**, from research, when you notice a standing,
forward-looking concern about a tool worth tracking on future runs — not
just when the user asks for one in a comment (that path is
`references/apply.md` §Watch Items (Writing), triggered mid-apply from a
`tool_comments`/`discuss` investigation). The cursor-cli shell-integration
example above (§Watch Items (Reading)) is exactly the kind of thing that
could have been proposed at research time, the first time it was noticed,
instead of waiting for the user to ask for it explicitly.

**The bar.** A watch item exists for one of two things, and you must say
which. Each limb is a conjunction: both halves, both answered concretely.

**(a) Something expected to BOTH change AND require a change in the user's
configs.**

  - *Expected to change* → **name the party who can change it, and say why
    they would not announce it prominently.** "Any vendor could change
    anything" is not an answer; it is true of everything.
  - *Require a config change* → **name the file, and say what the edit would
    be.**

  If you can name the file but not the edit, that file *depends on* the
  behaviour — which is exposure, not a required change. Exposure is true of
  nearly every tool on this machine, and a bar that admits it admits
  everything. `cask:obsidian`'s proposal last run named
  `dotfiles/config/bash/.aliases:35` and 31 Mermaid notes; if a confirmation
  gate came back, neither would need editing — they would start prompting.
  That is the shape to recognise.

  The one entry the user has ever accepted is the model: cursor-agent's
  shell-integration hook execs `agent record` on every new shell, the user
  deliberately did not install it and wrote an on-demand `cursor-record()`
  wrapper instead (`dotfiles/config/bash/.functions`). If the vendor changes
  the hook, **that wrapper is the file that gets edited**. Named party, named
  file, named edit.

**(b) A config or use-case that is hard to reason about after the fact AND
security-critical or load-bearing.**

  - *Hard to reason about after the fact* → **could you tell, from the
    machine's state alone, that it had already happened?** If yes, it is not
    hard to reason about after the fact — you would find it next time you
    looked. `brew:lazygit`'s proposal fails here: a config file rewritten in
    place is a diff you see the moment you open it.
  - *Security-critical or load-bearing* → **if it changed and nobody noticed
    for six months, what breaks?** A credential, a trust boundary, an
    unattended process, or something the setup depends on to work at all.
    "Mildly annoying" is neither.

  `cask:claudebar` passes both: a credential written back by an app the repo
  launches unattended at login, where the failure already fired silently for
  an unknown number of releases before issue #256 surfaced it.

It does **not** mean proposing one for every tool with a pin, a bespoke
touchpoint, or a `needs_attention` verdict — those are already tracked via
`config_status` and items' `local` findings on every run.

**How to propose one**: add a suggestion to the tool's `suggestions[]` with
`kind: "watch-item"` (`references/schemas.md` §1.7) — same array, same schema
strictness as any other suggestion, just a different shape:

```jsonc
{
	"id": "cask:cursor-cli:watch-shell-integration",
	"kind": "watch-item",
	"title": "Watch: shell-integration / session recording",
	"target_files": [],          // nothing to edit
	"command": null,             // nothing to run
	"auto_runnable": false,      // accepting it writes a watch item, nothing else
	"watch_topic": "the short phrase a future run matches against its changelog",
	"watch_note": "the fuller context, so a future hit can explain itself
	               without re-deriving everything",
	"rationale": "your answers to the self-test below, in your own words"
}
```

`watch_topic`/`watch_note` mean exactly what `watch-items.json`'s
`topic`/`note` mean (§Watch Items (Reading) above) — write them so they read
sensibly copied verbatim into that file, because that is what happens on
accept.

**`rationale` is where the self-test lands, and it is required.** It is also
the field a later pass reads to decide whether to keep this at all, so it
carries the same evidence discipline as everything else here.

**This is a proposal, not a write** — nothing touches `watch-items.json`
unless the user explicitly accepts it in the review UI
(`references/apply.md` §Watch Items (Writing)); do not also write the file
yourself from research.

How many to propose is answered in §There Is No Volume Target, two sections
down. Read it before you decide to hold one back.

### Before You Propose a Standing Note: the Self-Test

You are about to add a permanent entry to a machine-global file. Run these
questions and **write your answers into the proposal's `rationale`** — that
field is required, and it is what a later pass reads.

**Q1–Q4 are answerable from text you have already written in this same
object.** If answering one sends you off to find something new, that is the
answer: the support you were looking for is not there. Q5 is the exception and
says so — a method note's witness is usually a *previous* run's mistake, which
reaches you through the standing notes and hypotheses in your prompt rather
than through anything you wrote today.

**Nothing here deletes a proposal.** A question you fail tags the proposal and
you write it anyway, with `self_test_failed: {limb, reason}` naming the
question and your own reason in your own words (`references/schemas.md` §1.7c).
**`reason` is required whenever the tag is present** — a limb name alone gives
the later pass nothing to review the proposal against, which is a deletion
wearing a tag, and the output is rejected for it (`E-SELFTEST-NOREASON`).
A later corpus-wide pass reviews every tagged proposal and decides whether
dropping it is right. **A proposal you never write is one that pass cannot
restore** — that asymmetry is the whole reason the self-test tags instead of
cutting. Never suppress a proposal because it failed a question here.

**For a watch item — four questions.**

> **Q1 — ROUTING.** Quote the sentence in your own note that says how a future
> release's published text could match this topic. If you cannot write that
> sentence, this is a research-method note, not a watch item. **File it as
> one.** This is the one answer that is a *route* rather than a tag: the
> knowledge is kept, it just moves to the store that fits it.
>
> **Q2 — SCOPE.** Read the `config_status` you just wrote for this same tool —
> **write it before you write `suggestions[]`**, because this question quotes
> it. Quote the sentence from its `detail` showing this concern is outside its
> scope — typically a sentence naming what `config_status` *did* check, which
> does not include your concern.
>
> If `config_status.detail` instead describes re-verifying **this** concern
> against **this** run's delta, then `config_status` caught it. Write the
> proposal anyway and tag it `self_test_failed: {limb: "scope", reason: <your
> Q2 answer>}`.
>
> **If `config_status.state` is `"unknown"`, say so plainly.** Your quote will
> be something like *"there is no prior handling to re-verify"* — true, and
> worth nothing as evidence. This limb then gives you no support at all, and Q3
> and Q4 carry the whole proposal alone. Do not read a vacuous pass as a pass.
> This is not a rare corner: `config_status` was `unknown` on 22 of 78 tools
> last run.
>
> **Q3 — THE CHANGING THING.** Name the thing that could change, and who owns
> it. Then: is that thing something a file in the setup repos states, sets or
> pins? If yes, a future delta against that file is exactly what
> `config_status` re-checks every run — tag `{limb: "changing-thing", reason:
> <which file states it>}`.
>
> Note what this asks and what it does not. It asks about the thing that could
> **change** — not about any file your rationale happens to cite. A login item
> in `tasks/config.sh` that makes a third party's credential handling run
> unattended is not the changing thing; the credential's format is, and no file
> here states it. Read the clause the other way and it drops the best proposal
> in the set.
>
> **Q4 — THE LIMB.** Say which limb of the bar you are claiming, (a) or (b),
> and answer its two halves in the bar's own terms:
>   - (a) who changes it and why silently **+** which file, which edit
>   - (b) could you tell from the machine's state alone that it already
>     happened **+** what breaks after six months unnoticed
>
> If either half is unanswered, tag `{limb: "limb", reason: <which half, and
> what you could not answer>}`.

**For a method note — one question.**

> **Q5 — THE WITNESS.** Point at the wrong or empty answer the ordinary path
> produced for this tool: in a prior run, in this run's own research, or in the
> source you had to fall back on. If your rationale predicts a failure rather
> than naming one, tag `{limb: "unwitnessed", reason: <what you have instead of
> a witnessed failure>}`.

**A restatement is not an answer.** If a reply repeats the question, or recites
the bar's own wording back at it, it fails. Two rationales last run opened with
*"there is no single delta to re-check"* — the rule's own escape phrase —
while the same tool's `config_status.detail`, written by the same agent minutes
earlier, described re-checking exactly that delta. Three of eight did this.
**Writing the rule's words is not passing the rule.**

This self-test exists so that a later corpus-wide pass is not cutting a long
list every run. It is not the last word: that pass sees every tool at once and
holds final authority to cut anything, including proposals that pass every
question here.

### There Is No Volume Target — and Here Is Why

You will not be told how many watch items or method notes to produce — not for
this tool, not for your batch, not for the run. **You must not infer a number,
and you must not invent one.**

The reason is measured, not stylistic, and you need it: an agent told "there is
no budget" with no explanation reads the omission as an oversight and invents a
budget out of prudence, which is the same failure with a self-generated number.

A previous version of this rule said *"at most one or two per run across the
whole candidate set"*. That is a constraint on a sum no participant can see:
twenty-two researchers each looking at between one and nine tools. Every one of
them read a fleet allowance as a personal allowance, because from inside a
one-to-nine-tool scope there is no other available reading. The result was eight
proposals from eight different groups — **exactly one each, and not one group
proposed two.** The instruction was obeyed locally and violated globally, which
is the only outcome its structure allows.

The same rule text produced 11, then 5, then 8 proposals across three runs. It
was **byte-identical every time**. The number never came from the rule, so
asking harder cannot move it. What moves it is the routing test and the
self-test above, which change what you are asked to *produce* rather than what
you are told to feel.

Concretely:

  - **Do not hold a proposal back because you imagine others are proposing
    theirs.** You cannot see them and you would be guessing.
  - **Do not propose one because you have researched several tools and none has
    produced one yet.** An empty hand is a normal outcome. Most tools warrant
    nothing.
  - **Do not drop a proposal that failed the self-test.** Tag it. Dropping is
    the one thing you cannot undo.
  - **Judge each proposal on its own evidence**, against the bar and the
    self-test, and nothing else.

Volume is handled where volume is visible: a later pass reads every tool's
output at once and cuts what does not hold. Your job is to be right about this
tool, not to be economical about the fleet.

### Deduplicate Facts (One Array, Same Discipline)

**One `items[]` removes the old cross-array duplication by construction** —
a change can no longer be written once as a headline bullet and once as a
separate machine-relevance finding, because the upstream fact and the local
finding are two halves of one item (seen this run, under the old shape:
codex's CI/release signing migration to Azure Key Vault + rcodesign shipped
twice, as both, about the same quarantine workaround — the item model fuses
that pair). When a change is both changelog-worthy and relevant to this
setup, that is **one item** with both `change` and `local`, never two.

What survives the merge is the other axis: **two items restating one
change.** The validator catches the mechanical case — two items deriving
one anchor id raise `E-ITEM-DUP-ANCHOR`, and both are kept for convergence
to merge — so what is left to you is the pair the anchors cannot see: same
change, different anchors. The
canonical pair, seen this run in `cask:1password-cli`, both `feature`/`info`,
both citing 2.38.1:

> Reading an item with `op read` or `op item get` uses one fewer network
> round-trip (2.38.1)
>
> Resolving an item or vault by name uses one fewer round-trip across
> `op read`, `op item get`, `op item list` and `op vault get` (2.38.1)

Same release, same mechanism, overlapping command set — two items on one
optimisation. `brew:yq` did the same with two adjacent
enumerations of 4.53.4's bugfix list.

Run this as a bounded mechanical pass once `items[]` is drafted, not as
"be careful" — "I'll notice if it happens" is exactly what failed this run:

1. **Key every item.** For each element of `items[]`, write a
   three-part key in your reasoning: *(the release or version it cites, the
   subject it changes as a noun phrase, the direction of the change)*. The
   1Password pair keys as `(2.38.1, item/vault resolution, fewer
   round-trips)` — twice.
2. **Compare every pair.** A typical tool's item count keeps this cheap and
   finite. Two items are candidates when they share the
   **version** *and* the **subject**. Direction alone is not enough.
3. **Apply the reader test.** Would a reader who had seen only item A ask a
   question item B answers? If no, they are one item.
4. **Merge, do not drop.** The survivor keeps the union of the specifics — the
   1Password pair merges to one item naming all four commands. Merging is
   also the safe move mechanically: it keeps the surviving item's tags and
   severity, so it cannot move a bucket the way a deletion can (§The Noise
   Floor).
5. **Do not backfill.** A merge is not a freed slot to spend — there is no
   budget to refill (§One Change, One Item) — so never follow a merge with
   an N1–N8 item to restore a count nothing asked for.

Two boundaries. The **version conjunction in step 2 is load-bearing**:
`cask:tor-browser`'s two `security` bullets share subject and direction but
name different releases (15.0.19 → ESR 140.13, 15.0.20 → ESR 140.14) carrying
different MFSA sets, and a subject-only match would have merged two distinct
security stories into one line. And **cross-tool duplication is not
duplication**: `cask:brave-browser` restates `cask:google-chrome`'s Chromium
numbers word for word and both must stay — two separate decisions on two
separate casks. The rule is scoped to one tool, always.

Among `security`-tagged items the same rule is the *only* thing allowed to
remove one: two items describing one advisory become one, and
nothing else about a security item is ever cut for length (§The Noise Floor).

### Scope-vs-Changelog Separation

**Separate a changelog fact from a note about *this machine's* scope or
usage of the tool.** "v5 dropped X" is a changelog fact — an item's
`change` half, cited verbatim in `change.citation`; "this tool isn't
currently in the Brewfile" or "we don't use the feature this release
changes" is about *this setup* — the item's `local` half, or a
`local`-only item when no change motivates it (§Classify Non-Changelog
Findings Correctly). The item model keeps the two apart on one item:
`change.citation` is the upstream's text, `local.statement` is yours.
Rendering separates the two halves (`references/rendering-report.md` §Page
Layout), so never bury a scope note as an extra clause inside a
`change.citation`, and never write a scope observation as if it were an
upstream fact.

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
   run's items/changelog to judge whether the function's own logic
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

### Write `config_status` Before `suggestions[]`

Within one tool object, author `config_status` first. The self-test's Q2
(§Before You Propose a Standing Note) quotes `config_status.detail` to decide
whether a concern is already covered, so a proposal written before there is a
`detail` to quote has nothing to answer with — and the agent then either
invents a quote or tags the proposal `scope` for the wrong reason, which
invites a later pass to drop something that was fine.

Nothing enforces the order; it is an ordering between two fields of one object
you write in one pass. Just do it in that order.

### Schema Strictness

**Hold yourself to the exact schema shapes** (spelled out in the research
prompt): `local.evidence` is always an array of path objects, suggestions
always use `title`/`target_files`/`rationale`/`motivating_link`/
`diff_preview`, and a `security`-tagged item always carries its `security`
block — `{cve_id, advisory_id, rating, rating_basis, exploited_in_wild}`
(`references/item-schema.md` §2). Loose
shapes (bare strings, ad-hoc `description` fields) force hand
normalization during assembly and have caused real rework.

**The validator tolerates a drifted shape; that does not make it
acceptable.** Every array the contract declares — `items`, `links`,
`release_inventory`, `suggestions`, `vendor_silent_categories` — is
normalized at the boundary (`references/item-schema.md` §7, stage V3): a
non-list becomes `[]` and is reported (`W-SHAPE-COERCED`), a wrong-typed
member is quarantined on the tool verbatim, and every case is a finding
naming the tool and the field. That exists because one report is assembled
from ~22 research files covering ~77 tools, and a single drifted file used
to abort the whole run *after* the expensive part of the session was
already spent — the tolerance buys a reported, held-for-review tool
instead of a destroyed report.

What it does **not** buy is your work reaching the reviewer as written. An
`"items": "no notable changes"` string is not parsed into an item; it is
coerced to `[]`, the coercion is content-losing
(`references/item-schema.md` §8), and the tool is held in `attention` at
`elevated` risk with no changelog at all. Writing the array correctly is
still the only way the work you did reaches the user.

### Depth by Tool

Node semi-detailed (security advisories, breaking changes, notable features
per minor); other runtimes coarse (breaking changes and majors only);
everything else proportional to how much the user configures it.

### One Host, One Manifest

**This review covers the host it is running on, and `Brewfile` is the only
manifest it looks at.** The collector's `machine` block describes that host;
assess impact against it and against `tasks/*.sh`'s arch-conditional blocks as
they apply there.

`intel.Brewfile` is **out of this tool entirely**: not a source of candidates,
not a compatibility check, not a suggestion target, and not on the page. Do
not read it, cite it, or name it in a `target_files` entry — the deterministic
layer rejects a suggestion that does (`references/item-schema.md` I-17), and
the Intel host is going to NixOS rather than being reviewed here.

Arch still matters *within* this host: an ARM-only dependency is an
incompatibility on an x86_64 machine, not a footnote. Read it off the
`machine` block rather than off which manifest a package is listed in.

### Brew-Health Enrichment

**Interconnection is the value.** Findings are often causally linked — an
untrusted tap is *why* its kegs show as "no formula"; a deprecated cask may
also appear in the same review as a plain version update. Research (or the
orchestrator authoring the brew-health file directly, per the Tiering
section above) should connect these: recommend trusting the tap that owns
an orphaned-keg backend rather than reinstalling; recommend migrating a
deprecated cask rather than updating it.

### Skill-Drift Enrichment

A `skill-drift` finding arrives knowing *that* a vendored skill and its
upstream disagree, and nothing about *what changed* or whether taking the
change is safe (`references/collection.md` §Skill-Drift Collection). Filling
that in is ordinary changelog work with a git range in place of a release
page — the finding hands you `upstream_url`, `upstream_branch`,
`upstream_subpath`, `baseline_sha` and `upstream_sha`:

1. **Diff the upstream range for this skill's subpath, and only that.**
   `git log --oneline <baseline_sha>..<upstream_sha> -- <upstream_subpath>`
   plus a `--stat` diff of the same range, against a shallow clone or fetch
   of `upstream_url`. The sync command is vendor-scoped but the *decision*
   is per skill, so vendor-wide output is not an answer to this finding —
   it is how nine google skills turn into one undifferentiated "267 commits
   behind" shrug.
2. **Read the `SKILL.md` frontmatter `description` diff first.** That string
   decides when the skill triggers at all, so a rewording changes agent
   behaviour everywhere the skill is installed — a bigger practical change
   than a new reference doc, and the one diff that never looks important in
   a `--stat`. Same for a renamed or moved skill directory: it breaks the
   `.claude-plugin/marketplace.json` entry and every symlink
   `tasks/projects.sh` writes into a repo's `.claude/skills/`, which is an
   `incompatible`-severity item (`reaches`/`risk`), not a note.
3. **Cross-reference the vendor's `CUSTOMISATION.md`.** A documented local
   patch touching the same files is what turns "sync it" into "sync it and
   re-apply X" — mandatory for a `diverged` finding, whose whole character
   is that both sides moved; a suggestion that reads as a clean pull there
   is actively misleading. For a `local_only` finding the same check is the
   reassuring half: the customisation is intact and upstream has not moved,
   so the honest output is a short `local`-only item naming the patch
   (`chore` at `info`), not a warning and not a suggestion.
4. **Answer "is this sync safe to take", concretely** — new or removed
   scripts the skill shells out to, a changed dependency, a new required
   tool, an instruction that now conflicts with this setup's own CLAUDE.md
   rules. That judgment is the point of enriching the finding at all.

**Tier**: individual, like brew-health — every one of these is
repo-touchpoint work.

**Links**: the upstream compare page for the exact range,
`https://github.com/<owner>/<repo>/compare/<baseline_sha>...<upstream_sha>`,
as a `changelog`-type link labelled with the skill's subpath so it is
obvious which slice of a vendor-wide compare to read. Link it for the human;
do **not** derive drift from it. The compare API caps its file list at 300
and reports no drift for a large vendor that plainly has some
(`references/collection.md` §Skill-Drift Collection) — the detector's
tree-hash verdict is exact, and research adds meaning to it rather than
re-deciding it. If the compare genuinely disagrees with `drift_state`, that
is a finding about the compare page, not about the skill.

**Two things not to author here.** Do not write a suggestion that runs the
sync — the finding's own remediation already carries the vendor-scoped
command, and a duplicate just splits one decision across two cards
(`references/apply.md` §Skill-Drift Remediation). And never propose an
`edit` that hand-edits vendored files to match upstream: that produces a
tree matching neither the baseline nor upstream, so the *next* run reads it
as `diverged` and the real sync then has to reconcile an edit nobody
recorded. A legitimate `edit` here targets something we own — a
`CUSTOMISATION.md` entry documenting a patch this run discovered, most
often.

### Pinned Tools

Never suggest unpinning a pinned tool unless the blocking reason is
verified gone in the new version — the pin exists because an upgrade broke
something. Treat a pin as a signal that this tool needs a real look, not a
reason to skip it (collection includes pinned formulae for exactly this
reason — see `references/collection.md` §Version Sources).
