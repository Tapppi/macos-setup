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
  - Tiering: Individual-Focus, Batched, Brew-Health, Skill-Drift
  - Word-Boundary Grep Rule
  - Spawning and the Output-File Contract
  - `research-status.json` Group Updates
  - Failure Handling
- Part 2 — Subagent Quality Bar
  - Headliners
  - Category vs. Severity — Independent Axes
  - CVE Severity Capture
  - Selecting Notable Security Items
  - Links
  - Relevancy Is the Point
  - Classify Non-Changelog Findings Correctly
  - Don't Author "I Checked, Found Nothing"
  - The Noise Floor
  - Current → Target Is the Only Frame
  - Suggestions Are Always `kind: "edit"`
  - Config Status
  - Watch Items (Reading)
  - Watch Items (Proposing)
  - Deduplicate Facts (Across Arrays, and Within One)
  - Scope-vs-Changelog Separation
  - Bespoke `tasks/*.sh` Setup Testing
  - Schema Strictness
  - Depth by Tool
  - Heterogeneous Hosts
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
brew-health, skill-drift) unless a rule says otherwise.

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
**The ≤6 budget is per tool, not per changelog section** — seen this run,
`cask:gcloud-cli` spent five of its six `notes` slots on one deprecation
family, leaving no room for an answer to "does anything I run break" (§The
Noise Floor, N8).

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

### CVE Severity Capture

The card shows `security.cve_ids` as a count, not a list — the user's own
words, "no need to list the CVEs in per-tool, we need to trim those down to
allow faster review". What replaces the list is a severity breakdown, and a
breakdown assembly cannot compute on its own: it has no network and no
advisory database. So research supplies the per-id ratings it read, in a
`security.cve_severities` array of `{cve_id, severity, basis}`
(`references/schemas.md` §1.9), and assembly rolls them up into
`security.severity_counts`.

**`severity` is `critical | high | medium | low | unknown`** — the CVSS
qualitative bands, because every source below already speaks them. **This is
deliberately not `relevancy[]`'s vocabulary.** Relevancy severity answers "how
much does this matter to *this* machine"; CVE severity answers "what did the
issuer rate the flaw". Seen this run: teamviewer's `CVE-2026-19042` carries
CVSS 8.8 and is Linux-client-only, so it is `high` and irrelevant at the same
time. Collapsing the two axes is how that item would have read as the most
urgent thing on a macOS card.

**`basis` is `vendor | nvd | cvss | unrated`** — where the rating came from.
`cvss` means you banded a numeric score the source published (≥9.0 critical,
7.0–8.9 high, 4.0–6.9 medium, 0.1–3.9 low); `unrated` means nobody published
one. Assembly reads a grade with no basis as `unknown`, because a rating with
no source is not a rating.

**Where the severity comes from, in priority order:**

1. **The page you already fetched for the headliners.** Free, so do it for
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

**Never derive a severity from how the description reads.** "Remote code
execution" is not evidence of `critical`, and "memory leak" is not evidence of
`low`. This is the same doctrine as §Links' "confirm you actually fetched the
page you're linking" and assembly's `cve_count`-vs-`cve_claimed_count` split:
the report never carries a number it cannot attach to a source.

**The fetch budget, because a tool with 18 ids cannot have 18 advisories
fetched.** This run's real distribution — `cask:google-chrome` 18 ids,
`cask:firefox` 14, `mise:node` 12, `brew:rsync` 10, `mise:go` 10,
`brew:nmap` 6, `brew:gh` 5, `brew:libpq` 4, everything else ≤4; 99 ids across
41 tools. So: **rate free from the page you already read, for every id; then
at most 3 extra fetches per tool** (per tool, not per batch), spent on the ids
going into `notable[]` — and on nothing else. Every other id is `unknown`.
"The vendor's prose singles this one out" is not a fourth budget: an id worth
a fetch on that basis is an id that belongs in `notable[]`, so decide that
first and let the budget follow.

**`unknown` dominating is the expected state, not a degraded one.** Measured
on this run's text: 22 of 99 ids carry a rating the research had already read,
and 24 of the 41 security-bearing tools have no extracted ids at all. The page
is built for that: it shows the ids count, the graded count, and only the
graded classes that are non-zero. A subagent that fetched 18 Chrome advisories
to avoid an `unknown: 14` cell spent the run's budget on the number least
likely to change a decision — the reviewer is taking Chrome either way.

**Never rate an advisory that has no CVE.** `cve_severities` is keyed by CVE
id and feeds a count of `cve_ids`; a vendor-only advisory has no id to key
(this run: wireshark's 28 `wnpa-sec-*`, tailscale's `TS-2026-011`,
teamviewer's `TV-2026-1009`, and iproute2mac's command-injection fix, which
was never assigned one at all). Those carry their severity on the
`notable[]` entry instead, with `cve_id: null` and the vendor's id in
`advisory_id`.

Shape and validation: `references/schemas.md` §1.9; the rollup and its sum
invariant: `references/assembly.md` §Severity Rollup and the Sum Invariant.

### Selecting Notable Security Items

`security.notable[]` is the **only** security content shown inline on a tool's
card. Everything else is compressed into the severity strip and a detail list
that is collapsed by default. So the predicate has to be tight enough that two
subagents pick the same ≤3 items.

**An item qualifies when any of these holds:**

- its `severity` is `critical` — unconditionally;
- its `severity` is `high` **and** the flaw's precondition is something this
  setup actually does (a network-facing service, untrusted input this machine
  processes, code running as this user);
- it is the subject of a `security`-category `relevancy[]` item at `notable`
  or worse — **whether or not it names a CVE**. This clause is what makes the
  24 tools with no extracted ids representable at all: `brew:iproute2mac`'s
  reproduced command injection and `cask:wireshark-app`'s
  `wnpa-sec-2026-87` are the two most important security items in this run
  and neither has a CVE id;
- the vendor or CISA reports in-the-wild exploitation, or it is a zero-day —
  regardless of severity, because this is the one class where the reviewer's
  *timing* changes.

**Cap 3, ordered `affects_me: true` first**, then worst severity, then the CVE
id's `(year, sequence)`, then an id-less entry last. Assembly re-sorts and
re-caps by exactly this key *after* validating every entry, so an over-long
array loses its weakest entries rather than its last ones — write your
strongest item wherever it falls naturally.

`affects_me` outranks severity because the key evicts as well as orders, and
an ungraded item is not a weak one: `unknown` ranks above `low`, not below it.
The third clause above is what makes that matter — `brew:iproute2mac`'s
never-assigned command injection is id-less, ungraded and lands on a wrapper
this machine runs, and severity-first ordering let three unreachable `low`
CVEs push it off the card entirely. The `severity` vocabulary is unchanged
(`critical|high|medium|low|unknown`); only the rank moved.

**Empty is the common, correct result — do not pad to three.** This is the
rule that does the actual shrinking: a tool with six medium CVEs and no
touchpoint gets `notable: []` and renders as a severity strip with a single
column of changes, which is exactly the layout the user asked for. Seen this
run: `brew:redis` (its own `context[]` says "all nine of 8.10.1's fixes are
server-side") and `brew:nmap` (six bundled-libssh2 CVEs, no touchpoint) are
both correctly empty. On the projected run, 24 of the 41 security-bearing
tools come out empty.

**`affects_me` is a direction, and you set it explicitly.** It means a
concrete touchpoint on this setup — a file, a service, a call site, a running
process — in the same evidentiary sense `relevancy[]` demands. "The tool is
installed" is not a touchpoint; if it were, the flag would be true everywhere
and carry no information.

The trap is assuming that a security item *with* a relevancy finding is
therefore `affects_me: true`. **It is not.** A third of this run's
security-category relevancy items exist precisely to say the fix does *not*
reach this machine: openssh's sshd fix landing on Apple's `/usr/sbin/sshd`
rather than Homebrew's, `cask:microsoft-teams`' patch that "carries no
security benefit here", `brew:fd`'s terminal-gated sanitization,
`mise:python`'s own bundled expat, `brew:rsync`'s rrsync/nixpkgs split. Those
are **`affects_me: false` and still worth writing** — a negative-direction
finding is the answer to a question the reviewer would otherwise have to ask,
and it may still be `notable` if its severity qualifies. Read the direction of
your own finding and set the flag from it.

The converse does hold: if you can point at the touchpoint, you owe a
`relevancy[]` item too — they are the same claim. Assembly warns when
`affects_me: true` has no security-category relevancy backing it, and warns
only: it never sets or clears the flag for you.

**`affects_me` does not change `security.impact`.** `impact` is a bucket
input; `affects_me` is a display flag on one item. A security-category
relevancy is a reason to *take* the update, not a risk of taking it — treating
it as impact emptied the `security_auto` bucket across a whole live run
(`references/assembly.md` §`impact`). Nothing here touches that.

**Worked selections from this run:**

| Tool | ids | `notable[]` | Why |
|---|---:|---|---|
| `brew:libpq` | 4 (28 claimed) | 3 — `CVE-2026-18408`, `CVE-2026-19385`, `CVE-2026-6464` | its `context[]` already says 3 of 28 land in code this machine runs, and those are the three; the fourth id is server-side |
| `brew:gh` | 5 | 1 — `CVE-2026-64654` | it "lands directly on gh commands this machine pre-approves for agents"; the other four are real and untouched here |
| `brew:iproute2mac` | 0 | 1 — `cve_id: null`, `advisory_id: null` | the installed 1.7.4 executes injected shell commands, reproduced on this machine. The run's best example of a `notable` with no id at all |
| `cask:teamviewer` | 3 | 2 — `CVE-2026-12703`, `CVE-2026-16444`, **not** `CVE-2026-19042` | the highest CVSS on the card is the Linux-only one. It stays on the card as a low-priority item and must never be promoted |

That last row is the whole rule in one line.

**Write the `security` block even when nothing qualifies — `"notable": []`.**
An empty array and a missing key are different answers to the card
(`references/schemas.md` §1.9): `[]` says you looked and nothing rose to the
bar, and the card drops its security column rather than drawing an empty one;
a missing key says the question was never put, and assembly makes the card
fall back to listing every security sentence the run produced. Omitting the
block on a tool you did assess therefore ships the noisy card the whole
selection exists to replace.

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

### The Noise Floor

78 tools and 106 decisions in one run. Attention spent on a fact that cannot
change a decision is attention taken from one that can, so:

> **A fact earns a line only if a reviewer who believed the opposite would
> decide differently.** Everything else is research you did, not information
> the reviewer needs.

Two corollaries. The first keeps the rule from over-firing; the second keeps
it from changing a decision it has no business changing.

**Route, trim or merge before you cut.** Most of what follows is not deletion
— it is relocation into `release_inventory[]` (which releases exist) or
`context[]` (what is true of this machine), or a trimmed clause on a bullet
that keeps its load-bearing half, or two near-identical bullets becoming one.

**Deletion has a hard boundary, and it is not editorial.** `security_only` is
an `all()` over `headliners + relevancy`; `impact` and `risk_level` are
`any()`s over the same items; `has_security` reads the security category and
the CVE ids. All four feed `review_bucket` and then `pre_accept`, so a
deletion can *approve an update*. Concretely: cut the last `security` item and
the tool falls out of the security buckets into `routine`; cut the last
`features` item and it walks the other way, from `security_mixed` into
`security_auto`, and pre-accepts itself. **Delete only these, and only when
the text carries no CVE id, no "fixes N CVEs" claim and no `Watch item hit:`,
and never the tool's last headliner:**

| Array | Deletable pairs |
|---|---|
| `headliners[]` | `fixes/info`, `fixes/notable`, `notes/info` |
| `relevancy[]` | `fixes/info`, `notes/info` |

**A `security`-category item is never deletable as noise, at any severity.**
The only rule that may remove one is the dedup rule below, and only against
another security item on the same tool. Everything the user named as noise is
non-security by category, so this costs nothing.

If the noise floor tells you to delete something outside that table, it is
telling you the item's category or severity is wrong, not that the item should
go. A cadence note filed `notes/notable` is a mis-rating — nothing that cannot
change a decision is `notable`. A performance bullet filed `features/info` is
a bullet to **trim**, because `features` is precisely the signal that says
"this release is more than patches". The boundary is encoded as
`noise_suppressible()` in `scripts/assemble.py`, and
`scripts/test_assemble.py` §6 asserts the property it exists for: deleting
every suppressible item on a tool moves no `review_bucket` and no
`pre_accept`.

Eight classes. N1–N4 are the ones the user named. Every one carries a
**near-miss** — a real item from this run that looks like the class and must
survive it. A class whose near-miss you cannot state is a class you are not
ready to apply.

**N1 — Release cadence and publication process.** Facts about *when and how
the vendor published*: build numbers of releases you are not taking,
patch-Tuesday chronology, "there is no 8.9", "18.5 was pulled", tag-vs-release
bookkeeping, contributor counts. Seen this run, in `brew:libpq`'s `notes`:
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
upgrade. Seen this run, `brew:pkgconf`'s `relevancy`: *"Going 3.0.3 → 3.0.6
steps over 3.0.4's parse-time unescaping…"* — whose own detail text ends by
admitting the point, *"this host never runs the 3.0.4-only behavior"*. It
spent a relevancy slot and pushed the tool's `why` line. See §Current → Target
Is the Only Frame for the general rule and the one carve-out. **Near-miss:**
`brew:sops`' MAC-computation regression looks identical in shape and is the
opposite, because 3.13.2 *is the installed version* — the regression is live
right now and the upgrade is the fix.

**N3 — Performance micro-details.** A speed or memory number is a fact only
when it crosses a threshold the reviewer would act on. Seen this run, in
`brew:fzf`'s `features`: *"0.74.3 optimizes non-ASCII input: reading accented
Latin input up to 37% faster and CJK input using up to 29% less memory."* —
while the same tool's `context[]` already says *"ffv/rfv run fzf with the
matcher disabled, so neither release's speedups reach them"*. Nobody declines
an upgrade because it got faster. **Near-miss:** a defect with an observable
symptom is not an optimisation (`cask:yaak`'s *"constant high CPU usage while
idle"*), and a step change is a fact in its own right (`brew:pkgconf`'s
*"roughly six times faster … on a ~40-module graph"*). A workable bright line:
an order of magnitude, on something you invoke repeatedly. 6× clears it; 37%
does not. Remember these are `features` items, so the move is to trim to the
load-bearing clause, not to delete.

**N4 — Project-internal conventions, process, docs, packaging.** How the
project runs itself is not a change in the software you run. Seen this run,
`brew:yt-dlp`'s `notes`: *"Build: PyInstaller temporarily pinned to v6.22.0
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
versions is inventory. Seen this run, `brew:azcopy`'s `notes`:
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
install is not a change here. Seen this run, `cask:codex`'s `security`:
*"0.148.0 makes sandbox restrictions fail closed for denied or unreadable
paths **on Linux and Windows** …"* — a security headliner on a macOS-only
fleet. **Near-miss, and this is the important half:** when the item carries a
CVE, a CVSS or a scary name a reviewer might meet elsewhere, the scoping *is*
the finding. `cask:teamviewer`'s *"CVE-2026-19042 (CVSS 8.8) … is
Linux-client-only and does not affect the macOS builds"* must stay — and must
never be promoted into `notable[]`. Its aggregate form belongs in `context[]`,
which is where `brew:libpq`'s *"Client-only install: 3 of the 28 CVEs land in
code this machine runs, 25 do not"* correctly sits — the single most useful
line on that card. Note that under the boundary above, a `security`-category
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

**N8 — One change spent as many bullets.** The ≤6 headliner budget is per
tool, not per changelog section. Seen this run, `cask:gcloud-cli` spends
**five** of six `notes` bullets on one theme — the retirement of the
`api-registry mcp` / `beta services mcp` surfaces across 577–582 — so its six
slots contain no answer to "does anything I run break". One bullet carries the
same decision. **Near-miss:** `cask:tor-browser`'s two `security` bullets are
identical in shape and different in content — two distinct ESR rebases
carrying two distinct MFSA sets. N8 fires on redundancy, not on symmetry.

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
entirely"*, filed as a **security** relevancy. A gate added and removed inside
the range leaves the end state identical to the start state — the finding
manufactured a security item out of a no-op.

**Never write "going straight to X skips Y", "do not stop at Y", or "steps
over Y's regression".** The reviewer is not choosing an intermediate version;
the card offers exactly one target. All three phrasings appear in this run and
all three are cuts. Equally: do not reconstruct intermediate history to
explain a net-zero — if the reconstruction ends "so nothing changes here", it
was not a finding. (A *pin* that would stop at an intermediate is a different
thing: that is a relevancy finding about the pin, and the target is then the
pinned version — see §Pinned Tools.)

**The one carve-out: MAJOR security in an intermediate.** An intermediate
security issue answers a different question — not "what changes" but "what was
I exposed to while I sat on `C`" — so it is worth a line. It qualifies only if
it meets **both** tests: severity `critical`, or `high` with a published CVSS
≥ 7.0 (§CVE Severity Capture's recorded value, not an impression); **and** the
exposure required something this setup actually does, evidenced as concretely
as `relevancy[]` demands. A critical CVE in a code path this machine never
enters is still a cut. Frame it explicitly as exposure, and keep it out of
`security.notable[]`, which is about what this patch delivers. **No item in
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
   relevancy>` (its Brewfile line, its `tasks/*.sh` section, its dotfiles
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
     also appear in this run's headliners — e.g. "libkrun", "Apple Silicon",
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
`relevancy[]` — a verdict, in either direction, without a concrete look at
the V→latest delta is worse than no verdict at all.

**A `"needs_attention"` verdict must come with at least one suggestion
addressing it** — flagging a possibly-stale fix and then giving the user
nothing to act on just moves the "did anyone check this?" question one
level up without answering it. If the re-verification concludes the old fix
still holds after all, that's `"up_to_date"`, not `"needs_attention"` with no
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
`notable` severity (`references/schemas.md` §Report Object), prefix the
summary with the literal phrase below, and cite the watch item's `note` as
part of the evidence. This is the one case where relevancy severity is
elevated by something *other* than the changelog content's own weight — a
topic the user asked to be told about earns extra prominence regardless of
how minor the change looks on its own.

**The prefix is mandatory and exact.** The relevancy item's `summary` **must**
contain the literal phrase `Watch item hit:` — write it as
`⚠ Watch item hit: <what changed>`:

```jsonc
"summary": "⚠ Watch item hit: install-shell-integration now writes to ~/.zprofile as well"
```

This is not a formatting preference. `assemble.py` detects watch-item hits
**textually**, matching `/watch[\s\-]?item hit/i` against each relevancy
item's `summary` + `detail`, and scores a hit at 70 points in `highlights[]`
(`references/assembly.md` §Highlights) — enough to clear the threshold on its
own. Nothing in the schema marks a hit structurally, so the phrase *is* the
signal: paraphrase it ("this matches a watched topic", "flagged per the watch
item") and the hit becomes invisible to assembly, the highlight silently never
renders, and the one thing the user explicitly asked to be told about is the
thing that gets buried.

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

**When to propose one** — rare, not a default. A genuine standing concern
looks like: an intentional deviation from the vendor's default behavior that
a future release could silently reintroduce or break (an on-demand wrapper
replacing an always-on hook, a pin whose blocking condition is narrow and
easy to miss changing back), or a config decision whose correctness depends
on something the vendor could change without prominent announcement. It does
**not** mean proposing one for every tool with a pin, a bespoke touchpoint,
or a `needs_attention` verdict — those are already tracked via
`config_status`/relevancy on every run; a watch item is for a concern that
`config_status`'s per-run re-verification (§Config Status above) wouldn't
naturally catch because there's no single delta to re-check, just an
ongoing "did the vendor change their mind about X" question.

**How to propose one**: add a suggestion to the tool's `suggestions[]` with
`kind: "watch-item"` (`references/schemas.md` §1.7) — same array, same
schema strictness as any other suggestion, just a different shape:
`target_files: []`, `command: null`, `auto_runnable: false`, and the
proposal's payload in `watch_topic`/`watch_note` (same field meaning as
`watch-items.json`'s `topic`/`note`, §Watch Items (Reading) above — write
them so they'd read sensibly if copied verbatim into that file, because
that's exactly what happens on accept). Give it a real `rationale` explaining
why this is worth watching, same evidence-discipline as everything else
here. **This is a proposal, not a write** — nothing touches
`watch-items.json` unless the user explicitly accepts it in the review UI
(`references/apply.md` §Watch Items (Writing)); do not also write the file
yourself from research. At most one or two per run across the whole
candidate set is the expected volume — if you're proposing one for most
tools you research, you're almost certainly over-applying this.

### Deduplicate Facts (Across Arrays, and Within One)

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

**The other axis: two headliners inside one tool restating one change.** The
canonical pair, seen this run in `cask:1password-cli`, both `features/info`,
both citing 2.38.1:

> Reading an item with `op read` or `op item get` uses one fewer network
> round-trip (2.38.1)
>
> Resolving an item or vault by name uses one fewer round-trip across
> `op read`, `op item get`, `op item list` and `op vault get` (2.38.1)

Same release, same mechanism, overlapping command set — two of six headliner
slots on one optimisation. `brew:yq` did the same with two adjacent
enumerations of 4.53.4's bugfix list.

Run this as a bounded mechanical pass once both arrays are drafted, not as
"be careful" — "I'll notice if it happens" is exactly what failed this run:

1. **Key every item.** For each entry in `headliners + relevancy`, write a
   three-part key in your reasoning: *(the release or version it cites, the
   subject it changes as a noun phrase, the direction of the change)*. The
   1Password pair keys as `(2.38.1, item/vault resolution, fewer
   round-trips)` — twice.
2. **Compare every pair.** ≤6 headliners plus typically ≤5 relevancy items is
   ≤55 pairs; cheap and finite. Two items are candidates when they share the
   **version** *and* the **subject**. Direction alone is not enough.
3. **Apply the reader test.** Would a reader who had seen only item A ask a
   question item B answers? If no, they are one item.
4. **Merge, do not drop.** The survivor keeps the union of the specifics — the
   1Password pair merges to one bullet naming all four commands. Merging is
   also the safe move mechanically: it keeps the surviving item's category and
   severity, so it cannot move a bucket the way a deletion can (§The Noise
   Floor).
5. **Re-run the budget.** A merge frees a slot; spend it on something not yet
   covered, or ship five bullets. Do not backfill with an N1–N8 item to get
   back to six.

Two boundaries. The **version conjunction in step 2 is load-bearing**:
`cask:tor-browser`'s two `security` bullets share subject and direction but
name different releases (15.0.19 → ESR 140.13, 15.0.20 → ESR 140.14) carrying
different MFSA sets, and a subject-only match would have merged two distinct
security stories into one line. And **cross-tool duplication is not
duplication**: `cask:brave-browser` restates `cask:google-chrome`'s Chromium
numbers word for word and both must stay — two separate decisions on two
separate casks. The rule is scoped to one tool, always.

Within `security.notable[]` the same rule is the *only* thing allowed to
remove a security item: two entries describing one advisory become one, and
nothing else about a security item is ever cut for length (§The Noise Floor).

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
`title`/`target_files`/`rationale`/`motivating_link`/`diff_preview`, and the
security block is `security.cve_severities[{cve_id, severity, basis}]` plus
`security.notable[{cve_id, advisory_id, severity, summary, affects_me}]`
(`references/schemas.md` §1.9). Loose
shapes (bare strings, ad-hoc `description` fields) force hand
normalization during assembly and have caused real rework.

**Assembly tolerates a drifted shape; that does not make it acceptable.**
Every array the schema declares — `headliners`, `links`, `relevancy`,
`context`, `release_inventory`, `suggestions`, `vendor_silent_categories`, and
the `security` block's `cve_severities`/`notable` — is
coerced at the boundary by `as_item_list()` (`references/assembly.md`
§Loading and Merging → Shape Normalization): a non-list becomes `[]`, a
wrong-typed member is dropped, and each case prints a warning naming the tool
and the field. That exists because one report is assembled from ~22 research
files covering ~77 tools, and a single drifted file used to abort the whole
run *after* the expensive part of the session was already spent — the
tolerance buys a warned-about tool instead of a destroyed report.

What it does **not** buy is your content surviving. A `"headliners": "no
notable changes"` string is not parsed into a headliner; it is discarded, and
the tool then classifies as "research told us nothing" — `risk_level` elevates
and the card ships with no changelog at all. Writing the array correctly is
still the only way the work you did reaches the user.

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
   `incompatible`-severity relevancy finding, not a note.
3. **Cross-reference the vendor's `CUSTOMISATION.md`.** A documented local
   patch touching the same files is what turns "sync it" into "sync it and
   re-apply X" — mandatory for a `diverged` finding, whose whole character
   is that both sides moved; a suggestion that reads as a clean pull there
   is actively misleading. For a `local_only` finding the same check is the
   reassuring half: the customisation is intact and upstream has not moved,
   so the honest output is a short `context[]` note naming the patch, not a
   relevancy item and not a suggestion.
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
