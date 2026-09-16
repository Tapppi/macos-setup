# Rendering: Report Page

The pre-Submit report page design: the Solarized Dark palette, the two-tab
shell (an Overview that triages and an All-tools list that details), the
per-tool sections and suggestion cards inside it, how a decision stays in one
place while being controllable from two, keyboard navigation, and the
template-variable injection mechanism `render.py` uses to turn a report
object into `index.html`.

This doc renders the shapes defined in `schemas.md` (§Report Object, and
§1.8–§1.11 for the triage fields) — read that first if you need a field's
exact meaning rather than how it's drawn. `assembly.md` is the source of
truth for `version_delta`, `security`, `review_bucket`, `risk_level` and
which suggestions carry `pre_accept`; this doc only describes how that state
is *displayed*. For everything that happens after the user clicks Submit, see
`rendering-results.md`.

**The problem this layout exists to solve**: 77 heterogeneous entries in one
flat list make everything look equally important, so nothing is. The page has
to answer *"what do I need to think about, and what can I wave through"* in
the first five seconds, then hand off to the (good) per-tool detail view for
anything needing a real read. Every rule below either serves that or protects
something the flat list already got right.

Table of contents:
- Palette (tokens, derived tokens, the colour-rationing rule)
- Tab Shell (panel registry, the sticky shell bar + Submit gating, deep
  linking, back pill)
- Header and Counts
- Overview Tab (lede, stat tiles, security section, highlights, overflow
  lists, everything else)
- Page Layout — the All-tools tab (filter bar, per-tool section, header
  badges, collapse controls, content groups, per-item severity mapping, link
  click behavior, Context section, Release Inventory section, vendor-silent
  tag, per-item detail collapse, suggestion cards, Submit behavior)
- Decision State and Mirrors
- Long Strings and Overflow
- Brew-Health Rendering
- Skill-Drift Rendering
- Keyboard Navigation
- Transition to Results View
- Template Variables

## Palette

Solarized Dark palette as CSS custom properties, no external dependencies:

| Token | Hex | Role |
|---|---|---|
| `--base03` | `#002b36` | page background |
| `--base02` | `#073642` | panel / card background |
| `--base01` | `#586e75` | border, de-emphasized |
| `--base00` | `#657b83` | secondary text, `macos` source badge |
| `--base0`  | `#839496` | body text |
| `--base1`  | `#93a1a1` | emphasis text |
| `--base2`  | `#eee8d5` | heading text |
| `--yellow` | `#b58900` | `warning` severity, pin badge, discuss state |
| `--orange` | `#cb4b16` | `notable` severity badge, `cask` source badge |
| `--red`    | `#dc322f` | `incompatible` severity, prominent callout |
| `--blue`   | `#268bd2` | links, `info` severity, `brew` source badge |
| `--cyan`   | `#2aa198` | Accept confirmed state |
| `--green`  | `#859900` | version delta new-version text, diff additions, `skill-drift` source badge |
| `--violet` | `#6c71c4` | mise source badge |
| `--magenta`| `#d33682` | standalone source badge |

All `source` values (`schemas.md` §Report Object) get a badge: `brew`/`cask`
reuse the severity/link colors above (blue/orange), `mise`/`standalone` have
dedicated colors, `macos` reuses `--base00` as a neutral "system-level, not a
package manager" badge, `brew-health` gets its own dedicated color (see
§Brew-Health Rendering below), and `skill-drift` reuses `--green` (see
§Skill-Drift Rendering below). **A new source takes an existing hue, never a
new one** — the page's standing claim is that it introduces no colors beyond
the fifteen above, and `--green` was the one token no source badge had
claimed. It also reads sensibly here: green is already the page's
"repository content" color (new versions, diff additions), and a vendored
skill is repository content.

### Derived Tokens

Seven values derived from the fifteen above — **not new hues**: alphas and
steps of existing colors, so `rendering-results.md`'s claim that "this view
introduces no new colors" still holds.

| Token | Value | Why it exists |
|---|---|---|
| `--tint-red` / `--tint-yellow` / `--tint-cyan` / `--tint-blue` / `--tint-orange` | 10%-alpha `rgba()` of the matching hex | Panel tints for status surfaces. Written as literal `rgba()` of the existing hexes rather than `color-mix()`, which Safari < 16.2 drops — taking the whole rule with it. |
| `--hair` | `rgba(147, 161, 161, .14)` (14% `--base1`) | Internal division. `--base01` stays for structural card edges; a 77-row page ruled entirely in `--base01` reads as a spreadsheet grid. |
| `--base01-dim` | `#3f5b62` | `--base01` stepped toward `--base03`, so a proportion bar gets a fourth, quieter step without inventing a hue. |
| `--red-text` | `#e6706e` | `--red` #dc322f on `--base02` is ~3.6:1 — fine for a 26px numeral, under AA for an 11px CVE chip. It is the small-text red: the CVE chip and the `affects this setup` chip wear it. The severity strip's counts are `--base2` ink, not this. |
| `--mono` | `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace` | Tabular numerals for every number, version, count and CVE id. A review page reads as an instrument when its numbers line up in columns; prose stays in the system sans stack. |
| `--sticky-h` | `104px` initially, **measured at runtime** | Scroll offset for deep links. The shell bar wraps to two or three rows on narrow screens, so this cannot be a constant — see §Tab Shell. |

### The Colour-Rationing Rule

> **Warm color (red / orange / yellow) is reserved for security content and
> for things blocking Submit. Everything else is blue, cyan, or neutral.**

A page where 77 rows all carry a warm accent is the page this layout
replaced. The corollary the Overview's tiles follow: **values wear ink, not
the accent color** — identity is carried by a colored rail, a small colored
mark beside the label, and (for status tiles) a 10%-alpha tint, never by
coloring the numeral itself. Rendering both side by side settled it: the
ink-value version is calmer and the tinted security tiles still take the eye
first, which is the intended reading order. It also dodges the `--red`
small-text contrast problem above.

**The CVE severity strip is this rule's canonical case, and also its
measured limit.** Warm reserved for security content is exactly what a CVE
severity scale is, so the strip earns its `--red` / `--orange` / `--yellow`.
But those three cannot carry the classes on their own:
`dataviz/scripts/validate_palette.js` in dark mode scores `#dc322f` against
`#cb4b16` at **ΔE 5.3 for normal vision — below the 15 floor — and ΔE 1.4
under deuteranopia**. Solarized's red and orange are near-indistinguishable
even with full colour vision, and "Solarized Dark tokens only" means they
cannot be re-stepped. So the strip does not ask them to:

- every chip carries **its count and its class word**; a status colour never
  ships alone;
- the order is **fixed and severity-descending**, so class is readable from
  position;
- **`critical` is the only filled chip**, separating the one pair the
  validator calls hopeless by a channel colour-vision deficiency does not
  touch.

Colour is a three-band reinforcement (severe / moderate / negligible), not
the encoding — collapsed to `#b58900,#dc322f,#657b83` the three *bands* pass
the normal-vision floor at `[WARN] ΔE 6.5 deutan`. **Do not "fix" this by
inventing a hue**; the palette has none to add, and the reason it looks
fixable is the reason it is not.

Single file: all CSS and JS inline. Zero CDN calls; system font stacks only.
Must render correctly offline. `rendering-results.md`'s elements reuse these
same tokens rather than introducing new ones.

## Tab Shell

At most one panel is visible at a time. Four exist over the page's life:

| Panel id | Tab label | When it exists |
|---|---|---|
| `#panel-overview` | `Overview` | always |
| `#main` | `All tools` | always (today's `#main`, unchanged in role) |
| `#results-panel` | `Results` | created by `transitionToResults()` |
| `#changelog-panel` | `Changelog` | created by `transitionToResults()` |

Pre-Submit the strip is `[Overview] [All tools · N]`, defaulting to
**Overview**. Post-Submit it becomes
`[Results] [Overview] [All tools] [Changelog]`, defaulting to **Results**.

**The post-Submit strip is flat, not nested.** What used to be a single
`Report` tab is two siblings, `Overview` and `All tools`, rather than a
`Report` tab with an inner switcher: nested tabs are worse to use, and the
frozen Overview is the *most* useful thing to look at while an apply runs —
it is the summary of what was just approved.

### Panel Registry

Panels live in one `PANELS` registry (`{name: {el, label}}`), not a hardcoded
triple; `transitionToResults()` adds to it and re-renders the strip.
`selectTab(name)` sets `activeTab`, toggles `display` on every registered
panel and `active`/`aria-selected` on every tab button, and shows the back
pill only when `activeTab === 'tools' && cameFromOverview`.

- Client-side only. **No `history.pushState` for tab changes** — a tab is not
  a navigable location; only a tool deep link writes the hash (below).
- Tab buttons carry `id="tab-<name>"` and `data-tab="<name>"` and are handled
  by the existing document-level click delegate. `role="tablist"` /
  `role="tab"` / `aria-selected`, panels `role="tabpanel"` with
  `aria-labelledby`.
- The `All tools` label carries a count chip: the total tool count normally,
  `visible/total` while any filter is active — a second, always-visible signal
  that something is hidden (§Filter Bar).
- The strip is `overflow-x: auto; scrollbar-width: none`, so a four-tab
  post-Submit strip never wraps.

### The Sticky Shell Bar

`#progress-bar-container` stays sticky at `top: 0` and **absorbs the tab
strip**: it holds the strip, the progress track, the progress text, the
`N blocking →` gate button, the auto-run-upgrades toggle, and Submit. Two
stacked sticky bars (tabs plus progress) eat a quarter of a phone viewport;
and Submit plus decision progress are *global* state that must be reachable
from the Overview, where a user may well finish deciding. This also matches
what `transitionToResults()` already did to this container, so pre- and
post-Submit now use one mechanism instead of two.

**The filter bar does not live here.** It moved inside `#main` as its first
child, because it only ever governs the tool list and would be dead chrome on
the Overview (§Filter Bar covers the three guarantees that compensate for it
being one click away instead of zero).

Under 600px the bar reflows by `order`: tabs and Submit (`margin-left: auto`)
share the first row, then the progress track with a **compact** progress text
(`52/85 · 4 ⛔` instead of `52 of 85 decided · 4 incompatible undecided`) takes
a full-width row, then the auto-run toggle takes another. Both progress
strings always render and CSS picks one, so no JS branch can get them out of
sync.

### Sticky Progress Bar and Submit Gating

Inside that bar: a thin progress track (cyan decided / `--base01` remaining),
the progress text "N of M decided · K incompatible undecided" (`--red` when
K > 0), and the Submit button — **disabled until every suggestion on an
`incompatible`-severity tool has a decision**; the tooltip explains why.
Non-incompatible suggestions may be left undecided. The gate rule is
unchanged from the flat layout, and so is the counting: `updateProgress()`
walks `#main .tool-section[data-max-severity="incompatible"] .suggestion-card`,
and Overview mirrors are structurally excluded from that selector (§Decision
State and Mirrors).

Two additions make the gate impossible to miss from either tab: a
`N blocking →` button in the shell bar that jumps to the first blocking tool,
and the Overview's own blocking strip (§Overview Tab → Highlights). Both are
recomputed inside `updateProgress()` on every decision change and disappear
when the gate clears.

### Deep Linking: `#tool-<id>`

Tool ids contain a colon (`brew:podman`), so **anchors are not used for the
jump**. Every jump control is a `<button class="jump" data-jump="<tool id>">`
handled by the click delegate — that avoids `querySelector` escaping problems
and lets the jump run the multi-step behavior below. The hash is written
purely for shareability and reload survival, with
`history.replaceState(null, '', '#tool-' + encodeURIComponent(id))`:
`replaceState`, not `pushState`, because a report is not a browser-history
document and back-stack noise across 77 jumps is worse than useless. Sections
also carry `id="tool-<encodeURIComponent(id)>"` so a pasted URL resolves after
a reload.

`jumpToTool(id)`, in order:

1. Record whether we came from the Overview, then **reveal whichever capped
   Overview list is hiding this tool's card** (§Overview Tab → Overflow
   Lists) — before anything scrolls or takes focus, so the card the jump
   refers to is really on screen when the back pill returns to it.
2. Switch to the `tools` tab.
3. **If the target is hidden by a filter, `clearFilters()` and toast**
   `Filters cleared to show {name}.` — a jump must never silently fail
   because a filter hides its destination — the same principle as step 1's
   reveal, applied to the other thing that can hide a target.
4. Expand the section. Auto-advance is *not* triggered; it fires on collapse
   only.
5. `focusSection(idx, {scroll: false})`, so `j`/`k` continue from where you
   land rather than from where you were. **Scrolling is suppressed here on
   purpose**: `focusSection()`'s own `scrollIntoView({block: 'nearest'})`
   would be a second scroll issued in the same task, and the browser resolves
   it against the pre-animation offset — with the target still below the fold,
   `nearest` then means "align its *bottom* edge", which stranded the jump
   `innerHeight − sectionHeight − scroll-margin` px too low. The `focus()`
   call inside already passes `preventScroll`.
6. `scrollIntoView({block: 'start'})` — **one motion, and the last word on
   where the jump lands**. `.tool-section` carries
   `scroll-margin-top: calc(var(--sticky-h) + 8px)`, and `--sticky-h` is
   **measured at runtime** by a `ResizeObserver` on the shell bar
   (`observeStickyHeight()`, with a `resize` listener as fallback) — the bar
   wraps on narrow screens, so a hardcoded offset would tuck the target under
   it.
7. Set `[data-flash]` for 1.6s — a **static** `outline: 2px solid var(--cyan)`,
   not an animation. A pulsing 77-row page is noise, and this is also the
   `prefers-reduced-motion`-safe choice (`scrollIntoView`'s `behavior` drops
   to `auto` under that query).
8. `replaceState` the hash.

On load the order is `renderTabStrip()` → `renderHeader()` → `renderTools()` →
per-section decision badges → `applyFilters()` → `collapseAllButFirst()` →
`renderOverview()` → `syncAllMirrors()` → `updateProgress()` →
`observeStickyHeight()`, and only then does the page select `tools` and jump
when `location.hash` starts with `#tool-`, else `overview`. **`renderOverview()`
must run after `renderTools()`** — its mirrors resolve against canonical cards
that have to exist first — and `syncAllMirrors()` after both, to pick up
everything assembly pre-accepted. The hash jump runs on the post-Submit path
too: in a frozen report it still works, because mirrors are disabled but
navigation is not.

**Back-to-overview pill** (`#backpill`, fixed bottom-left): visible only while
`activeTab === 'tools' && cameFromOverview`. Clicking it — or pressing
`Escape` with no modal open — returns to the Overview and clears the flag.
Clicking a tab directly also clears it: an explicit tab click is not "arrived
via jump".

## Header and Counts

`#page-header` compresses to one flex row: title, generation time, machine
context (arch highlighted when Intel — it gates compatibility), and the
repo-freshness note, which surfaces only when a repo is *behind* origin
(`repo_context`; `recent_commits[]` is research context and is never
rendered).

**The counts row is gone.** `#header-counts` ("N tools with updates, K
incompatible, S suggestions") is superseded by the Overview's stat tiles,
which say the same thing better and are always one tab click away. Keeping
both means two places to read one number and two places for them to disagree.
`renderHeader()` lost only its counts branch; nothing else in the header
changed.

## Overview Tab

`#panel-overview`, rendered by `renderOverview()` — the default pre-Submit
panel. It answers the five-second question and then gets out of the way. It
holds no decision state of its own: every control on it is a mirror
(§Decision State and Mirrors).

`renderOverview()` must run **after** `renderTools()`, because its mirrors
resolve against canonical cards that have to exist first — see §Tab Shell →
Deep Linking for the full load order.

### Lede

One sentence above the tiles, prose in `--base1` with numbers in
`--mono`/`--base2`:

> **42 of 74 updates need a decision.** The other **32** are patch-level or
> security-only with no impact here — already accepted below.

(Those figures are the recorded run's — `assembly.md` §Review Buckets and
Pre-Accept — worked through the definitions below; every run splits
differently.)

- "need a decision" = `security_mixed` + `attention` tools, excluding both
  non-version sources, `brew-health` and `skill-drift` (each counted
  separately in its own band). The lede's denominator is
  `total_outdated` — a sentence reading "42 of 74 updates" must not silently
  count things that are not updates.
- "the other" = `routine` + `security_auto`.
- Zero needing a decision reads *"Nothing needs a decision — all 74 updates
  are routine or security-only with no impact here."*
- **Both trailing clauses are conditional on the data.** The security clause
  is dropped when the report has no security content at all, and "— already
  accepted below" is dropped unless at least one suggestion actually carries
  `pre_accept: true`. A lede that claims either without the data behind it is
  the page inventing a fact.

This is the five-second answer; everything below it is the evidence.

### Stat Tiles

Single headline numbers, so these are **stat tiles, not charts**. The delta
axis is *ordinal magnitude* and the security axis is *status* — different
jobs, so they sit in two labelled groups rather than one undifferentiated row
of six, which would read as a wall.

**Group A — "Change size — N updates"** (three tiles, then a proportion bar):

| Tile | Value | Label | Secondary | Accent | Click |
|---|---|---|---|---|---|
| 1 | `summary.by_delta.major` | Major | `breaking-change candidates` | `--orange` | All-tools tab, `delta=major`, scroll to top |
| 2 | `summary.by_delta.minor` | Minor | `feature releases` | `--blue` | All-tools tab, `delta=minor` |
| 3 | `summary.by_delta.patch` | Patch | `+R revision, U unknown` | neutral (`--base01`) | All-tools tab, `delta=patch` |

**Revision and unknown get no tiles of their own.** They fold into the Patch
tile's secondary line (`+4 revision`, `+4 revision, 1 unknown`, or
`fixes only` when both are zero) and keep their own segment in the proportion
bar, which uses `--base01-dim` with a `title` distinguishing the two. For a
reader they are the same question as patch — "nothing to think about" — and
two more near-zero tiles is exactly the noise this layout exists to remove.

The group's colors are an ordinal scale of *attention* mapped onto the page's
existing severity vocabulary, not a new ramp: `--orange` is already `notable`,
`--blue` is already `info`, and patch gets no color at all. A synthetic
single-hue light→dark ramp was rejected — the palette has no ramp steps, and
faking them with alpha reads as "disabled" in Solarized Dark.

**Group B — "Security — N of M tools affected"**:

| Tile | Value | Label | Secondary | Accent | Click |
|---|---|---|---|---|---|
| 4 | `summary.security.cve_count` | CVEs fixed | two worst non-zero **graded** classes in words (`3 critical · 30 high`), falling back to `across N tools` | `--red` + `--tint-red` | scroll to `#sec-section` |
| 5 | `summary.security.auto_count` | Security only | `no impact here · accepted` | `--cyan` | scroll to `#sec-auto` **and expand it** |
| 6 | `summary.security.mixed_count` | Security + other | `decide these` | `--yellow` + `--tint-yellow` | scroll to `#sec-mixed` |

`--cyan` on tile 5 is not decorative: cyan is already this page's "Accept
confirmed" color, so the tile is literally the color of the state it reports.

**Tile 4's secondary line stays uncoloured** even though it names severity
classes. The one colour-carrying instance of the severity scale lives on the
security section heading a couple of hundred pixels below; two coloured
severity scales in one viewport saying the same thing is precisely the
duplication this layout exists to remove. It falls back to `across N tools`
whenever `summary.security.severity_counts` is absent or grades nothing —
never to a row of zeros.

Shared tile rules:

- **Values wear ink** (`--base2`) on every tile, per the rationing rule in
  §Palette. Identity comes from the 3px left rail, a small colored square
  before the label, and the tint on the two status tiles.
- A **zero value renders `--base01`** — an empty category should recede, never
  shout.
- **Every tile is a real `<button>`**, keyboard-focusable with a `--cyan`
  `:focus-visible` outline. **No dead numbers**: a tile whose target is empty
  renders `disabled` with a dimmed value rather than being hidden, because a
  stable 3+3 grid is easier to re-read run over run than a grid that changes
  shape.
- Bar segments carry the same `data-act`/`data-arg` as their tile, so clicking
  a segment does what clicking its tile does, and each segment's `title` gives
  `label — N (P%)`. **The tiles are the bar's legend** — adjacent, direct-
  labeled, same colors — so there is no separate legend box.
- Group A's bar is out of the delta total (`sum(by_delta)`); Group B's is out
  of `total_outdated`, with the remainder in `--base01-dim`. Those are the
  same number by the invariant in `schemas.md` §1.1 — which is precisely why
  **neither bar is ever drawn out of `by_bucket`**, whose denominator includes
  the brew-health findings. Mixing the two produces a percentage that means
  nothing.

**Data fallbacks.** `summary.by_delta` absent → derive by counting
`tool.version_delta` over version-source tools only, i.e. skipping every
non-version source (`brew-health`, `skill-drift`) exactly as assembly's own
`by_delta` does — a fallback that counted them would disagree with the
summary it is standing in for, which is worse than not drawing the group;
no tool carries the field either → **hide Group A entirely** rather than
draw three zeros.
`summary.security` absent → derive from `tool.security.*`; no tool carries it
→ hide Group B *and* the whole security section, and the lede drops its
security clause. Same principle throughout: degrade to silence, never to a
fabricated zero.

### Security Section

Anchor `#sec-section`. Heading: 🛡 **Security patches** with a `--mono`
sub-line — `N CVEs · M tools · A auto-approved · X need a look` — and, below
it, the report-level instance of the severity strip described next.

#### The security summary strip

`sevMeterHtml(sec, opts)`. One component, three placements, and **the
replacement for every enumerated CVE-id list this page used to draw**. It
returns `''` for a tool with no security content and *something* for every
tool that has any: a security card must never render a blank or an all-zero
summary.

| Where | Variant | Purpose |
|---|---|---|
| under the `#sec-section` heading | `.sevmeter.wide` | Aggregate over `summary.security.severity_counts`. **This is the legend** for every card instance below it — adjacent, direct-labelled, same colours — so no legend box exists. |
| the **head row** of every mixed card | default | Per tool, sitting immediately left of `impact:`. |
| every auto-approved strip row | default | Replaces the CVE-chip cloud, which is what returns those rows to one line each. |

**Why the head row and not a body row.** Three reasons, in order of weight.
(1) It survives the single-column variant below: a third of the cards lose
their security column entirely, and for those the strip is the only per-card
security signal left — a body row sitting above a lone `Changes · N` list
would read as that list's heading, which it is not. (2) It inherits a vacated
slot: the CVE chips lived in the security column's `colhead`, and that column
is going away for many cards, so the head absorbing the summary keeps one
clean split — **head = what this is and how bad, body = what changed**.
(3) The head row's existing `.spacer` (`flex: 1`) pushes the strip and
`impact:` into a right-hand block that lines up down the whole stack —
measured at 1440px, every visible card's block ends on the same x — so "how
bad is this one" is a single vertical scan.

**Design for the ungraded case, because it is the common one.** Research
grades only the severities a vendor already states in the text it is reading —
it never goes CVE hunting to fill a meter. On the live report that leaves **24 of 41
security-bearing tools with no CVE id at all** and most of the rest graded
three deep. So:

- the id count and the graded count are **stated in words**;
- **`unknown` is never a chip.** It is the residual between those two numbers,
  and a chip reading `48 unrated` adds nothing the subtraction already says.
  It is equally never folded into `low`: "we don't know" and "it's minor" are
  different claims and the page must not upgrade one into the other;
- **a chip renders only for a graded class that is non-zero**, so any colour
  at all on the strip means something really was graded.

This deliberately differs from the stat tiles' "no dead numbers — a zero tile
renders `disabled`" rule, and the difference is not an inconsistency. The
tiles are a 3+3 grid read *once* per run, where a stable shape helps
run-over-run comparison. The strip is read *41 times* in one scroll, where
stability is bought instead by fixed worst-first order and right-edge
alignment. It matches the rule §Header Badges already follows for the
severity-tier counts ("omit a tier with zero items rather than showing 0").

Chip form: 3px left rail in the class colour, `--hair` box, count in `--base2`
`--mono` ink, class word in `--base00`. `critical` is `--red` + `--tint-red`
(the only filled chip), `high` `--orange`, `medium` `--yellow`, `low`
`--base00`. See §Palette → The Colour-Rationing Rule for the measured reason
colour is not carrying the class.

The states, all of them:

| Data | Renders |
|---|---|
| `has_security: false` | nothing at all |
| ids, fully graded | `🛡 3 CVE ids` + chips summing to 3 |
| ids, partly graded | `🛡 18 CVE ids · 3 graded` + the three chips |
| ids, nothing graded | `🛡 18 CVE ids · none graded`, no chips. **Never a fabricated `low`.** |
| `cve_claimed_count > cve_count` | `🛡 18 CVE ids of 370 stated · …` — honestly framed as a **sample of a larger stated total**, never as the total, with the full sentence in `title` |
| zero ids, a claim (`cask:brave-browser`: 0 extracted, 370 claimed) | `🛡 370 fixes claimed upstream · none itemized here` — real information the page used to throw away entirely |
| zero ids, no claim — **the majority case** | `🛡 security fixes · no CVE ids published`. It must not be silence, which would make a security card look like it has no security content; it must not be loud, because no ids and no severities is genuinely low information |
| `severity_counts` grading **more** than there are ids | the graded clause says only `· N graded` and never restates the id count — it states what the chips themselves support and lets the contradiction show rather than repairing it |

`toolSecurity()` is a **whitelisting normalizer**, so every one of these
fields had to be added to it explicitly *and* shape-checked. This is the
silent-failure trap in this whole change: each new field has a designed
fallback, so an implementation that forgets one ships a page that renders
exactly as it did before and looks entirely correct.

- `cve_count` — **not read from the report at all; it is `len(cve_ids)`**,
  which is what the contract already defines it as (`schemas.md` §Security:
  an id-backed count, never a claim). A stored count that disagrees with the
  list it counts is malformed, not a second opinion, and every reader on the
  page — strip, badge, detail, mixed-card sort — takes the number from this
  one place. A vendor's unbacked number has its own field.
- `cve_claimed_count` — a non-negative integer **strictly greater than
  `cve_count`**, else `null`. A claim smaller than what research found is not
  a claim about a larger total, it is a contradiction, and `18 of 4 stated`
  is worse than saying nothing.
- `severity_counts` — validated **as a whole object**: a missing key reads as
  zero (that much is well defined), but any value that is not a non-negative
  integer rejects the entire object, because a breakdown with one garbage
  value is not a breakdown that can be trusted about the others. An all-zero
  object is `null` — there is nothing to break down.
- `display_item_ids` — a list of **item ids**, kept only when each is a
  string. The page looks each id up in `tool.items` and renders that item;
  an id that does not resolve is skipped rather than rendered as a stub. There
  is nothing to re-cap and nothing to re-sort: the selection is a bar rather
  than a cap, and the order is the contract's total
  `security_display_sort_key`, so following the list reproduces the report
  exactly. An empty list means "nothing cleared the bar" — the single-column
  card — and is common. It is not a data gap.

Rendered order is **(a) auto-approved as a single collapsed one-line strip,
then (b) mixed, expanded**. Group (a) still comes first in reading order, but
occupies one line until asked for, so the eye lands on the cards that
actually need a decision. That is the resolution of "list them in this order"
versus "triage first".

**Group (a) — `security_auto`** (`.autostrip`, `#sec-auto`, `--cyan` rail +
`--tint-cyan`). Collapsed head, always visible:

```
✓  5 security-only, no impact here — accepted    LIBPQ · OPENSSH · STUNNEL · …    ▸ show
```

The inline tool-name list is `--base01` uppercase micro-type that truncates
with `…`; it exists so the collapsed state is still informative — you can see
*which* tools were waved through without expanding. Expanded, one grid row per
tool:

| Element | Spec |
|---|---|
| Accept toggle | `.acc-toggle[data-mirrors]` — `✓` filled cyan when accepted, `○` outlined `--base01` when not. **This is how the user un-accepts.** `title` flips between `Accepted — click to un-accept` and `Not accepted — click to accept`. A single `○` row among `✓` rows is unmistakable at a glance. |
| Name | `--base1`. The row is *not* itself clickable — a whole-row click would fight the toggle. The toggle acts; `details →` navigates. |
| Versions | `--mono`, new version in `--green`, truncated per §Long Strings and Overflow with the full value in `title`. |
| Source badge | the existing `.source-badge` colors. |
| Security summary | the strip above, in its default size. It replaced a CVE-chip cloud that pushed most rows onto a second line. In this grid the strip sits in the single flexible track, so a row holds to one line until a `needs_sudo` chip squeezes that track — there the lead is allowed to wrap inside its own cell rather than overflow into `impact:`, and the row takes a second line on its own terms instead of being pushed onto one. |
| `needs_sudo` chip | rendered here too — see §Suggestion Card; a pre-accepted admin-password upgrade must not be silent in *any* of its appearances. |
| Impact | `no impact here` (`--cyan`) / `possible impact` (`--yellow`) / `impact unknown` (`--base01`). Group (a) should be all-`none` by the data contract; render defensively anyway. |
| `details →` | `data-jump` to the tool. |

This group shows **counts only**. The ids themselves are no longer here — see
§Page Layout → Content Groups for the one place on the All-tools tab that
enumerates them. The per-CVE prose lives in the tool's Security content group,
one click away; the whole point of the group is that these need confirming,
not reading.

**Group (b) — `security_mixed`** (`#sec-mixed`, one `.mixcard` per tool):
`--base02`, 3px `--red` left rail, `--hair` border. Head row is name,
versions, source badge, delta pill, spacer, `impact: <value>` right-aligned.
Body is `grid-template-columns: 1fr 1fr` — **equal columns, deliberately**.
The left (security) column gets `--tint-red` and a `--hair` right border; the
right is untinted. Equal width even at a 1:5 item ratio: the asymmetry *is*
the information ("one CVE, five unrelated changes"), and ragged column widths
down a stack of cards destroy the side-by-side reading this section exists
for.
Under 760px the grid collapses to one column, security on top with a bottom
hairline instead of a right one.

- **The left column renders the items `security.display_item_ids` names and
  nothing else** — the ones that could actually change a decision, not every
  security sentence the run produced. `securityDisplayItems()` follows that
  list in order and does **not** re-derive it: the contract's
  `security_display_sort_key` is total (rating worst-first, then
  exploited-in-the-wild, then whether it reaches us, then severity, then id),
  so following it reproduces the report exactly. Re-deriving is precisely how
  the two sides drifted apart once already. `NOTABLE_RANK` still exists for
  the display mapping and still ranks `unknown` **above** `low`, matching
  `items.CVE_ORDER_RANK`; `scripts/test_assemble.py` pins the two tables
  against each other.
- **There is no cap.** A cap is a count, and counts invite padding: the old
  `notable[]` took the worst three under a clause with no direction test, and
  `brew:openssh` filled all three slots with items whose own summaries say the
  fix does not reach this machine. The card still shows at most 3 with a
  `+N more →`, but that is a *display* limit with an escape hatch, not an
  eviction — nothing is lost from the report.
- The display class comes from `item.security.rating` when one was published
  and from the item's own `severity` otherwise; both map onto §Per-Item
  Severity through `NOTABLE_SEV`, so no new icon or colour enters the page.
  `critical`→`incompatible`, `high`→`warning`, `medium`→`notable`,
  `low`→`info` — and **`unknown` → `notable`, not `info`.** An absent grade
  means "nobody published a rating", never "a small flaw", and every route past
  the selection bar that can leave the grade absent already cleared something
  at least this high: the item reaches this machine, or it is at warning+, or
  it is exploited in the wild. Painting it `·` `--blue` put the id-less,
  ungraded, machine-touching item at the bottom of the card in the lightest ink
  on it. `low` keeps `info`: a graded-low CVE really is the least of what a
  card shows. The display table and the rank table are deliberately not one
  table: four display classes cover five ordering tiers, so `unknown` paints
  like `medium` while still sorting below it.
- An item line is: severity icon · the `affects this setup` chip when
  `local.direction == "reaches"` · the `cve_id` chip when non-null · the
  title. **Both chips lead the line rather than trailing it**, because the text
  is clamped and a trailing chip is exactly what a clamp eats. The chip is the
  `.mine-chip` — the `.sudo-chip` micro-tag shape in `--red-text`, no new
  component grammar and no new colour. It is derived now rather than declared:
  `affects_me` was a free-standing boolean a checker could set without
  evidence, where `direction` had to be answered before the selection bar could
  read it, and I-14 requires evidence for `reaches`.
- The right column takes every item whose primary group is not `security`, in
  canonical order — which already puts the ones that reach this machine first
  inside each severity tier, so the old "relevancy first" pass is what the
  ordering does for free.
- Items are **summary line only**: one severity icon plus the text. No
  `detail`, no evidence, no per-item link — those live in the tool section.
- **Both columns clamp item text to two lines** (`-webkit-line-clamp: 2`,
  scoped to `.mixcol` so the detail below stays unclamped). A triage card is
  for confirming, not reading; a 300-character headliner used to render four
  lines here.
- **Cap 3 items per column**, then `+N more →` (a `data-jump`). An empty
  column renders a `—` in `--base01`, never a collapsed zero-height panel.
- The `colhead` no longer carries a CVE-chip slot and the security heading is
  plain `Security` — the count moved into the head-row strip.
- If `vendor_silent_categories` contains `security` **and there is nothing to
  show**, the left column shows the existing "No detailed changelog published"
  pill. The pill is a fallback for an empty column, never an override: vendor
  silence is a statement about the changelog, and research can still promote a
  security item for a silent vendor from an NVD entry or a downstream
  advisory. Items always win — a silent tool with selected items renders them.

#### The collapsed security detail

`secDetailHtml()`. One full-width row, **always between `.mixcols` and
`.mixfoot`**, in both card variants — same place in both, so nothing about it
moves when a card changes shape and there is no "which container owns it" bug
waiting to happen. Closed by default; expands in place.

```
🛡  18 CVE ids · 4 security notes in full                          ▸ show
```

**The label states what is behind the control**, joined with `·`: the id
count when there are ids, and the security-note count whenever there is a note
to show at all. The note part is *not* conditioned on the card having hidden
something — the lines above are clamped to two lines and these are not, so a
note the card already drew is still only reachable in full here. Testing
"more notes than the card showed" once compared two different lists
(`notable[]` against `buildContentGroups().security`) and left six tools in the
live report — three notes, no ids — with no control and no way to read their
own text. Both sides are the same item objects now, so the de-duplication is on
the item id.
The trailing `in full` appears only when the card really did draw a subset
above; on a single-column card nothing was shown, so the label says plainly
how many there are.

Expanded: the `.cve` chip cloud, then **the card's own security lines at full,
unclamped length** — the selected items with their `affects this setup` and
`.cve` chips, exactly as the column drew them, then every other security item
on the tool, de-duplicated **by item id** — then, when `cve_claimed_count`
exceeds the id count, one prose line saying so. The body renders *those* lines
and not a parallel list, which is what made the clamp's promise of "the full
text, one click away in place" unkeepable before.

**Zero ids with a vendor claim is its own case.** `cve_claimed_count` alone
carries a label part (`370 fixes stated upstream`), so the control renders on
a tool that itemizes nothing — `cask:brave-browser` states 370 fixes and
resolves none, and with the label built from ids alone that number reached the
Overview strip and then vanished before the All-tools tab. The prose line
changes with it: with ids it says the breakdown above covers only those;
without ids it says there is no breakdown to show and the number is the
vendor's own. Asserting a breakdown that does not exist contradicted the
strip's own `none itemized here` on the same card.

**When nothing is hidden, no control renders at all.** That is §Per-Item
Detail Collapse's rule ("don't add one that opens onto nothing") applied here.

**Invariant, and the reason this is safe: the detail body contains no
`[data-mirrors]`, no `[data-mirror-dot]`, no `.suggestion-card`, no
`[data-jump]` and no `[data-act]`.** It is CVE chips, item lines and a
sentence. That is what lets it sit outside `OVERFLOW_SEL` / `revealOverflow()`
— see §Overflow Lists. If a future change needs an interactive control in
here, **that change must first make the detail a `revealOverflow()`
participant**, because `actOnFocused()` resolves `[data-mirrors]` with a
`querySelector` inside the focused card and would happily write a decision
through a control nobody can see.

Nothing else about the collapse machinery moves. `MIX_CAP` and
`compareMixedTools()` are untouched; detail state is per-card and independent
of whether its card is revealed, so `revealOverflow('mix-rest')` splices in 25
cards whose details are all still collapsed — the right default for cards the
reader has just asked to see for the first time. `getOverviewCards()` returns
the same list across a toggle, since the toggle adds and removes no card.
`jumpToTool()` targets a tool section in `#main`, never an Overview detail, so
a deep link out of a card parked inside `#mix-rest` still reveals the
overflow, switches tab, expands the section, flashes it and writes the hash
exactly as before.

**Mechanism: reuse, do not invent.** This is the page's third disclosure and
it is built from the second one's parts — `data-open`, `setStripOpen()` (whose
head lookup gains `.secdetail-head`), and a `[data-toggle-detail]` branch in
the existing click delegate that resolves with `closest('.secdetail')` exactly
as the band branch resolves with `closest('.band')`. No id: there are up to 41
of these on one page. A native `<details>` was rejected — it would be the only
one on the page, and consistency with two existing disclosures is worth more
than free find-in-page expansion of content that is CVE ids nobody Ctrl-Fs.

#### The single-column variant

**Applies exactly when `securityDisplayItems()` is empty and
`vendor_silent_categories` does not contain `security`.** The `.mixcol.sec`
element is **not rendered** (not hidden), the card carries `.onecol`, and
`.mixcard.onecol .mixcols { grid-template-columns: 1fr }` — nothing to
reconcile with the 760px query, which sets the same declaration for a
different reason.

- **Column heading becomes `Changes · N`**, not `Other changes · N`: "other"
  has nothing to be other than, and saying it anyway is a sentence the reader
  has to resolve.
- A **vendor-silent** tool keeps its left column — the "No detailed changelog
  published" pill *is* the information there.
- The **right** column's emptiness never collapses the card. An empty "Other
  changes" column keeps its `—`, because there the asymmetry is the message
  ("this tool ships security fixes and nothing else"). Only the left column is
  droppable.
- The card is still visibly a security card: it keeps its 3px `--red` rail and
  its head-row summary strip, and everything the column would have held is one
  click away in the detail below it.

**An empty `display_item_ids` is a legitimate answer, not a data gap**, and
this variant is the whole point of it — so nothing anywhere may force-promote
an item past the selection bar to keep two columns. Doing that would make this
variant unreachable, and it is the padding the bar replaced a cap to prevent.
- Foot row: mirror decision controls for the tool's **baseline upgrade**
  suggestion, the `needs_sudo` chip when it applies, then
  `+N more decision(s)` in `--base01` when the tool has more than one
  suggestion — the Overview never shows a partial decision set as if it were
  complete — then `full details →`.

**Group (b) is capped at 8, worst first**, with the same `show all N →`
expander highlights uses (§Overflow Lists below). The layout was verified
against 9 mixed cards; real data brought 31, which made the Overview ~13
viewport-heights tall and buried the very triage it exists to provide. **The
cap is a rendering decision and never an accounting one** — the section
heading, the Group B tile and the bar segment all keep counting the full set.

Assembly ranks `highlights[]` but writes no ranking onto `tools[]`, so there
is nothing to read here; the page derives one from the triage fields the
contract does guarantee. `compareMixedTools(a, b)`, first non-zero wins:

1. **worst security-item severity**, taken from `buildContentGroups(tool).security`.
   `items[]` arrives in canonical order, which is severity worst-first inside a
   group, so the first entry is the worst; a tool with no security item at all
   ranks below `info` rather than tying with it. **This deliberately keeps
   reading the whole security group rather than `display_item_ids`** — the
   order in which cards are cut by the cap must not shuffle because the
   selection bar admitted a different item. It is exactly the kind of coupling
   that gets added by accident later.
2. **higher `security.cve_count`** first;
3. **worse `security.impact`** first — `possible` < `unknown` < `none`;
4. **bigger `version_delta`** first, by the same
   `major < minor < patch < revision < unknown` rank the sort select uses;
5. **`tool_id`**, ascending — the last resort, and the reason the order is
   total at all. Without it two renders of one report could cut differently.

**Empty states.** No security content anywhere → the whole section and Group B
of the tiles are omitted. `auto_count === 0` → the strip is omitted, not
rendered empty. `mixed_count === 0` → `#sec-mixed` renders one line, *"No tool
mixes security fixes with other changes this run."*

### Highlights

Anchor `#hl-section`. Heading **Highlights** · `biggest decision drivers`.

**The blocking strip is client-derived, not read from `highlights[]`.**
`highlights[]` is assembly-ranked and cannot be relied on to contain the tools
that gate Submit, so the Overview renders its own strip whenever any
`incompatible`-severity tool still has an undecided suggestion:

```
⛔ 2 tools with incompatible findings still block Submit:  cursor →   nnn →
```

`--red` border + `--tint-red`, recomputed inside `updateProgress()` on every
decision change, gone when the gate clears. **Capped at 5 named tools**, then
`+N more →`, which switches to the All-tools tab with `severity=incompatible`
applied — uncapped, a pathological report rendered a two-row wall of links.

**Highlight card** (`.hlcard`, grid `auto 1fr`, `> * { min-width: 0 }`):

- Left rail: 3px border plus the severity icon, from `highlights[].severity`
  through the existing mapping (`incompatible` → `--red`, `warning` →
  `--yellow`, `notable` → `--orange`, `info` → `--blue`). An `incompatible`
  card additionally gets `--tint-red`.
- `h3` is `highlights[].title`; `.why` is `highlights[].why` at
  **`max-width: 78ch`** — this is the one place on the page with real prose,
  and it must not run to 1400px.
- Meta row: source badge, versions, delta pill, the CVE badge when
  `security.has_security`, then `open in tool list →`. **A finding from a
  non-version source (or any tool with no `current_version`) renders its
  source's own label — the finding category for `brew-health`, the
  drift-state for `skill-drift` — in the versions slot and drops the delta
  pill** — the same rule the
  tool header uses. Without it, such a finding reaching `highlights[]`
  renders `null → null` as "→ UNKNOWN".
- One `.hlsug` row per entry in `highlights[].suggestion_ids`: the
  suggestion's own `title` (truncating, `min-width: 0`), its `needs_sudo` chip
  when applicable, and mirror controls pushed right with `margin-left: auto`
  so **every button group in the section aligns to one vertical column**.
  Without that, the controls sat ragged and read as unrelated.
- **A `suggestion_ids` entry with no matching suggestion renders the raw id as
  its title rather than being dropped** — a silent drop hides an assembly bug.
- Zero `suggestion_ids` → no rows, just the jump link.

**Ordering**: `highlights[]` order, which is assembly's ranking and the point
of the array — with one exception, any highlight whose tool is in the live
blocking set floats to the top. No other client-side re-sorting.

**Highlights must not duplicate the security patches section — and that
de-duplication happens in assembly, not here.** When a highlight would repeat
a security item already shown on that tool's card, assembly drops the
*highlight* and backfills with the next-ranked candidate, so this section
still carries eight distinct decision drivers and the security card keeps its
single most important sentence. The page does not re-derive that: matching on
normalized `why` text would be silently wrong the moment `_truncate_why`
clips a long summary, and a page-side filter would delete the better rendering
of the two. **If assembly emits nothing new, the page degrades to today's
behaviour** — both render, which is exactly what happens on a report from an
older assembly — so nothing here has to know whether the dedupe ran.

**Cap**: assembly caps `highlights[]` at 8 (`assembly.md` §Highlights), but
the page does not assume it did: it renders the first 8 and parks any
remainder behind a `show all N →` expander (§Overflow Lists below).

**Empty state**: *"Nothing stood out as needing a decision beyond the security
patches above."* in `--base01` (the trailing clause drops when there is no
security section). The section header still renders — its absence would read
as a rendering failure.

### Overflow Lists

The mixed-security cards and the highlights are the page's two capped lists,
and they expand through **one** mechanism (`revealOverflow()`) so their
behavior can't diverge. Each renders its prefix inline and parks the remainder
in a hidden sibling (`#mix-rest`, `#hl-rest`) behind a `show all N →` button,
which removes itself once used.

The collapsed security detail (§Security Section) is the page's **third**
disclosure and is deliberately *outside* this mechanism. `revealOverflow()`
exists because `#mix-rest` and `#hl-rest` hide *cards* that hold decision
mirrors and can take the `j`/`k` focus ring; a detail body holds none of that,
by an invariant stated there. Never add `.secdetail` to `OVERFLOW_SEL`, and
never put an interactive control inside one without first doing so.

Two properties this has to preserve:

- **The overflow stays in the DOM.** Its mirrors keep tracking decisions and
  its canonical cards in `#main` are untouched, so nothing about the
  `/feedback` payload changes when a card is merely out of sight. A cap is
  never allowed to become an accounting change.
- **Nothing may land focus or a jump on a hidden card.** `jumpToTool()` calls
  `revealOverviewCardFor(id)` as its very first step — before the tab switch,
  the filter clear, or any scroll — so the card a jump refers to is really on
  screen when the back pill returns to it. `getOverviewCards()`, which drives
  `j`/`k`, filters out any card sitting inside a collapsed overflow, since
  focusing one would move the ring nowhere visible.

### Everything Else

Without this, the Overview would show ~20 of 77 tools and the obvious
question is "where did the other 57 go?". Four bands (`#else-section`)
complete the accounting, each with a head carrying a live `N of M already
accepted` count read off its own chips:

1. **Routine updates** (`review_bucket === 'routine'`, excluding every
   non-version source) — a chip cloud, one `.chip` per tool: an
   accepted-state glyph
   mirroring the baseline upgrade decision, the name, and the latest version.
   Click jumps to the tool. Chips truncate at `max-width: 230px` with the full
   text in `title` — real version strings hit 45 characters. A few dozen chips
   wrap to five or six rows, and the cloud stays legible to ~100 before it
   needs a scroll container. Collapsed by default.
2. **Other tools needing attention** (`review_bucket === 'attention'`,
   excluding every non-version source and anything already in
   `highlights[]`) — same band
   shape, chips show the `version_delta` instead of a version. This is the
   honest home for "flagged by the bucket algorithm but not important enough
   to be a highlight". Collapsed by default.
3. **Homebrew environment** (`source === 'brew-health'`) — **expanded by
   default**: there are only ever a handful and one is usually actionable. One
   row per finding: severity icon, name, the remediation command in a copyable
   `.cmd` chip (reusing the existing command-chip copy handler), `details →`.
   Counted separately from `total_outdated`, exactly as `health_count` is.
4. **Vendored agent skills** (`source === 'skill-drift'`) — the same row
   shape as band 3, but **collapsed by default**, because there can easily be
   a dozen: one adopted skill per row, and a whole vendor drifts at once
   (`assembly.md` §Skill-Drift Assembly). The head carries the count, so the
   accounting is visible without the rows being. Counted separately from
   `total_outdated`, exactly as `skill_drift_count` is. Rows repeat the
   vendor-scoped sync command per skill, which is honest rather than
   redundant — it is genuinely the same command for every skill of a vendor,
   and the remediation `label` says how many it covers.

**Bands 1 and 2 must exclude *every* non-version source, not just
brew-health.** They are chip clouds keyed on a version or a delta, so a
source with neither renders a chip that says nothing, in the one place the
page promised to account for what it isn't showing elsewhere. The rule is
one test — "does this tool have a version pair" — not a growing list of
`!== 'brew-health'` comparisons.

A band with no members is omitted rather than rendered empty; with no bands at
all, the whole section is omitted.

## Page Layout

This is the **All-tools tab** (`#main`) — the flat per-tool report, unchanged
in role and almost entirely unchanged in behavior. Everything in this section
renders inside that panel; the Overview above is a separate panel that triages
*into* it.

### Filter Bar

**The bar lives inside `#main`, as its first child** — it only ever governs
the tool list, so on the Overview it would be dead chrome and would cost a
sticky row on mobile.

Controls, left to right: **Bucket** select (All | Security + other | Security
only | Needs attention | Routine — matching `review_bucket`, `schemas.md`
§1.10), **Delta** select (All | major | minor | patch | revision | unknown),
Source select (All|brew|cask|mise|standalone|macos|brew-health|skill-drift
— one `<option>` per value the `source` vocabulary defines, so a source with
no option is a source the user cannot isolate), severity
select, **Security only** checkbox (`data-sec="1"`), "Only relevant to me"
toggle (hides tools with no item carrying a `local` block — `maxSeverity()`
returns `''` for them, so `data-max-severity` is empty), sort select (**Needs decision
first** [default] | Incompatible first | Name | Source | Major-delta first),
the auto-advance toggle, and Collapse all / Expand all.

"Needs decision first" sorts any tool with at least one undecided suggestion
(including the baseline `upgrade` suggestion — nearly every tool has one)
above tools whose suggestions are all decided/absent; incompatible severity
breaks ties within that. **"Major delta first" reads the server-computed
`data-delta`** and ranks `major < minor < patch < revision < unknown`; it never
re-parses version strings, because a client-side leading-integer diff agrees
with `assemble.py`'s classifier on neither calver, date, opaque, Homebrew
revisions nor the 0.x rule — the same tool would sort as "major" here while
the tiles and its own delta pill called it something else. Client-side only:
`data-hidden` toggles visibility and DOM nodes are reordered.

**The filter bar must stay reachable in the frozen Results view** (see
`rendering-results.md`) so a filter applied before Submit can still be
cleared/inspected afterward — never leave the user stuck looking at a
filtered-empty report with no way to reset it. It is outside the
`.report-frozen` disabling set and stays interactive after Submit. Because
"reachable" is now one tab click rather than zero, three guarantees close that
failure mode by construction rather than by adjacency:

1. a **persistent banner** under the bar whenever anything is hidden —
   `N of M tools hidden by filters  [Clear filters]`, `--yellow` outline on
   `--tint-yellow`;
2. the `All tools` **tab label shows `visible/total`** while filtered;
3. the **filtered-empty state carries its own reset** — *"No tools match these
   filters. [Clear filters]"* — never a blank panel.

`clearFilters()` resets every control including Sort (back to
`needs-decision`), and is also what `jumpToTool()` calls when a filter hides
its target (§Tab Shell).

### Per-Tool Section

Per-tool `<section>`: collapsible; header row with name, `current → latest`
(latest in green), source badge, a **delta pill**, a **`🛡 N CVE` badge**,
PINNED badge (yellow) when pinned, and a
**`config_status` badge** when `state` isn't `"unknown"`: a quiet small green
check + "config current" for `up_to_date` (hover/click for the
`detail`/evidence), a visible orange/red banner for `needs_attention` ("⚠
config may be stale — {detail}") placed right under the header before any
content group, since it's a review-the-review flag the user shouldn't have
to dig for. `config_status` is computed by research — see `research.md`
§Config Status for how the verdict is reached; this section only covers how
it's drawn.

Each section carries `data-tool-id`, `data-name`, `data-source`,
`data-max-severity` (unchanged) plus three new attributes the filter bar reads:
`data-bucket`, `data-delta`, and `data-sec` (`0`|`1`). It also carries
`scroll-margin-top: calc(var(--sticky-h) + 8px)` so a deep link doesn't land
under the sticky shell bar (§Tab Shell).

The two new header badges give the collapsed 77-row list the same vocabulary
the Overview's tiles use, so the two read as one system:

- **Delta pill** — `MAJOR` in `--orange`, `MINOR` in `--blue`, `PATCH` and
  `REVISION` in `--base01`, with `version_delta_note` as its `title`. Omitted
  for `brew-health` and `skill-drift` (no version pair) **and when
  `version_delta` is missing
  entirely**: an explicit `"unknown"` is a real classification (an opaque or
  build-number scheme) and earns its pill, but a *missing* field is not a
  classification at all, and rendering it as UNKNOWN on every row of a
  pre-triage report states something the data never said.
- **CVE badge** — `🛡 N CVE` in `--red` when `security.has_security`. When
  `has_security` is true with **zero** named ids (a vendor that says "security
  fixes" without publishing CVEs), the badge drops the count and reads
  `🛡 security`: saying "0 CVE" there is worse than saying nothing. When
  `cve_claimed_count` exceeds the id count it reads `🛡 18 of 370 CVE`, and
  with a claim but no ids at all, `🛡 370 fixes stated` — same framing as the
  summary strip, so the ids are never presented as the total.

Both badges, and the `data-*` attributes, degrade quietly on a report from an
older assembly that lacks the fields — the readers fall back coarsely and
never invent precision the data doesn't have.

### Header Badges

At a glance without expanding. The delta pill and CVE badge described in
§Per-Tool Section sit in this same row; the two below are the older,
decision-oriented pair and neither substitutes for the others.

- **Decision-count badge**: while the tool has ≥1 undecided suggestion, show
  an attention-styled badge with the undecided count (colored icon — e.g.
  `--yellow` "?" or "!" — not neutral chrome, since this is the "does this
  need me" signal). Once every suggestion on the tool has a decision,
  downgrade to a quiet "N decided" badge (`--base01`, same low-key treatment
  as the `config_status` "ok" badge) — the badge never disappears, it just
  stops demanding attention.
- **Severity-tier counts**: one small count per severity level actually
  present among the tool's `items[]` (info/notable/warning/
  incompatible), using the same icon/color mapping as per-item severity
  (below) — e.g. "⛔1 ⚠2". Omit a tier with zero items rather than showing
  "0". This is the "how significant is this tool's changelog" signal,
  independent of the decision-count badge's "does it need action" signal —
  both render, neither substitutes for the other.

### Collapse Controls

The header row's existing collapse toggle gets a twin at the very bottom of
the expanded body (after suggestion cards and the per-tool note area) — same
handler, same collapsed/expanded state; a long tool card shouldn't force a
scroll back to the top just to close it. This is distinct from the note
textarea's own Close/collapse control (below), which only dismisses the
note, not the whole section.

### Default Collapse State and Auto-Advance

On page load, every tool section starts collapsed except the first (in
current sort order) — with 70+ tools, an all-expanded initial view is
unusable. Collapsing a section (via either collapse control) auto-expands
the next section in view order; this is gated by a toggle (default **on**)
near the filter bar, so the behavior can be turned off for anyone who'd
rather step through manually. **Auto-advance triggers on the collapse action
itself** — not on "all of this tool's suggestions are decided," which would
be ambiguous for a tool with zero suggestions or one the user collapses
without deciding anything; collapsing is the one unambiguous, user-initiated
signal that means "I'm done looking at this one for now." Add "Collapse all"
/ "Expand all" buttons next to the auto-advance toggle — both bulk actions
ignore auto-advance entirely (they set every section to one state, not a
one-at-a-time walk).

### Content Groups

**There is no separate "headliners" bullet list and no separate "links row"
wall of buttons — `items[]` renders entirely inside four content groups:
Security, Fixes, Features, Notes.** Each item carries its own `tags` (topic,
a closed set of eight) and `severity` (`schemas.md`, both assigned by the
checker); the **group is derived from the tags** by the same map the contract
publishes (`items.GROUP_OF_TAG` / `contract.json` `groups.of_tag`), with
`GROUP_PRECEDENCE` settling an item that carries tags from more than one.
The groups themselves are neutral, purely-organizational containers with no
color of their own; **only individual items are colored**, by their own
`severity`, independent of which group they're in.

This is a deliberate change from an earlier version of this design that
colored the whole group box by a single severity/notability accent and derived
category client-side from keyword matching — that heuristic is exactly what
caused topic and urgency to get conflated (a low-profile security item reading
as "minor" would get bucketed into Notes by the same signal that was supposed
to be its severity, not its topic). The checker assigns both explicitly now;
the page derives one map lookup and nothing else.

**One item appears exactly once**, which is the whole point of the item model:
the old headliners/relevancy/notable split wrote one change into three arrays
with three severities implied, and the dedupe that tried to reconcile them
silently downgraded a card. A single-tag item gets no tag line — its group heading
already said it. A multi-tag item renders **all** of its tags on a plain
"Tagged: …" line in its expanded detail, including ones that map to the same
group: eliding "the tag that chose the group" hides a second tag sharing it, so
`["fix", "breaking"]` would render no `breaking` anywhere, and `breaking` is the
most decision-relevant tag in the set. Only ~7% of items carry more than one tag
(max two), so the line costs 7% of rows and can never swallow the tag a reader
needed.

Evidence paths stay attached to their item wherever it lands, and a citation
renders beside them but distinctly: `local.evidence[]` is paths only and
`local.citations[]` is prose, and conflating the two is what produced 272
"evidence not found" warnings against one real defect. Lead with title +
`body` for each item;
push its changelog/release link into a compact footer-style reference per
item (a direct deep link where the source supports line-level anchors, e.g.
a CHANGELOG.md section) rather than a shared links block.

**The Security group carries the same collapsed detail the Overview card
does**, at its foot, listing `cve_ids[]` as chips plus the
`cve_claimed_count` note. This is now the **only** place on the All-tools tab
where the ids are enumerated — the mixed card's head and the auto-strip row
both stopped listing them — so removing it would make them unreachable. Same
markup, same handler, same invariant (no interactive control in the body).
The group's items are already rendered above in full, so this instance passes
none: it is the id list and the note.

**The detail is built before the group's empty check, and is part of it.** A
tool can carry `cve_ids[]` or a `cve_claimed_count` while its Security
category renders no item at all; returning early on "no items and not
vendor-silent" dropped that detail on the floor. A group with a detail and no
items renders the detail — and, when the tool is not vendor-silent, no pill:
the pill states that the vendor published nothing, which is a different claim
from the group simply having no item in it.

Within a group the page **does not sort at all**. `items[]` arrives in the
contract's canonical order (`items.order_items`: group, then severity
worst-first, then `local.direction`, then `local.effect`, then id), so a stable
group-by reproduces it exactly — and that order already puts a finding about
*this setup* above the generic changelog line of the same severity, which is
what the old `sevRank + 0.5` half-step was arbitrating. There is nothing left
to tie-break, and re-sorting on the page is how two renders of one report came
to differ. The Overview's mixed-card comparator reads the same ordering, so
the two views agree about which item is a tool's worst (§Overview Tab →
Security Section).

### Per-Item Severity → Color/Icon Mapping

Reuses the palette's existing severity roles (§Palette above — no new colors
introduced): `incompatible` → `--red`, `⛔`; `warning` → `--yellow`, `⚠`;
`notable` → `--orange`, `●`; `info` → `--blue`, `·`. Applied to the item's
left border accent and a small leading icon — the group's own border/heading
stays neutral (`--base01`) regardless of what severities its items carry.

### Link Click Behavior

A link with only `url` (`schemas.md`) behaves as a normal anchor — opens in
a new tab. A link with `embedded_content` set opens a modal instead,
rendering that markdown text inline (any links inside the markdown itself
render as normal anchors within the modal) rather than navigating away —
this is the fallback for source content with no stable browsable destination
at all, not a general-purpose reader view; most links just have `url` and
never trigger the modal.

After the four content groups, two more optional sections render (only when
the tool's research populated them — most tools have neither):

### Context Section

*(Retired. `context[]` no longer exists.)* A present-tense repo-scope note is
an item like any other now — `change: null`, a `local` block carrying the
statement and its evidence, and a `packaging` or `chore` tag — so it renders in
its content group with the rest, at the severity the checker gave it. The
separate collapsed callout is gone with the array: a second home for the same
kind of claim is what made "is this relevancy or context?" a judgement call at
authoring time.

### Release Inventory Section

From `release_inventory[]` (`schemas.md`): a short list, one line per
`{version, link}` pair, no collapse (these are already short) — pure
bookkeeping about what releases exist in the current→latest range, not a
claim about what any of them changed.

Both the Context and Release Inventory sections sit below Notes and above
the suggestion cards. Suggestion cards follow the grouped content; a
per-tool note textarea sits last, **with a Close/collapse control** so it
can be dismissed after reading without leaving it visually "open" forever.

### Vendor-Silent Compact Tag

Driven by `vendor_silent_categories` (`schemas.md`) — a category name listed
there renders one small pill-style tag in that group's slot ("No detailed
changelog published") instead of its normal item list, even if the group
would otherwise be empty. No bullet, no border-left accent, no per-item link
(the link lives once, on the tool's canonical changelog/release reference).
`research.md`'s quality bar forbids authoring a fake bullet to fill this gap
— this field is the correct alternative, not a fallback the page invents on
its own from empty groups.

### Per-Item Detail Collapse

Inside the four content groups, an item with real secondary detail (`detail`
beyond the one-line `summary`/title) shows only the summary in the
forefront view; a small expand control (e.g. "▸ more") reveals the rest,
evidence, and per-item link. Items with no extra detail beyond the summary
render with no expand control at all — don't add one that opens onto
nothing. This is a lighter-weight collapse than the Context section's
whole-item collapse (above) — the summary line stays visible here, since
it's real changelog content, unlike Context's scope/locality notes.

### Suggestion Card

For `kind: "upgrade"`, render the `command` in a copyable code chip instead
of a diff (there is nothing to diff) with a short "run this yourself" hint;
Accept only marks the decision; it does not imply a diff preview exists. For
`kind: "edit"`, unchanged: title, target file(s), rationale, motivating
link, diff preview (`+` green / `-` red in a `<pre>`), Accept/Reject/Discuss
buttons, comment textarea (1 row collapsed, 3 rows focused). For
`kind: "watch-item"` (`schemas.md` §1.7): no target files, no diff, no
command — the body is just the proposal's `watch_topic`/`watch_note`
(`renderWatchItemBody`), a small cyan-accented callout distinct from a diff
or command chip, since accepting it doesn't run or edit anything, it only
writes a `watch-items.json` entry (`apply.md` §Executing `watch-item`
Suggestions). Accept/Reject/Discuss buttons and the comment textarea are
otherwise identical to an `edit` card — same `decisions` plumbing, no
special-casing in the Submit payload. Clicking an active decision button
toggles back to undecided.

| State | Visual |
|---|---|
| Undecided | default card |
| Accepted | left border + Accept button filled cyan, "ACCEPTED" |
| Rejected | left border base01, card dimmed, "REJECTED" |
| Discuss | left border + Discuss button filled yellow, "DISCUSS" |

**Pre-accept is a field, not a client-side derivation.** A card renders
pre-accepted (Accept button already active, `ACCEPTED` state label) **iff
`suggestion.pre_accept` is true** — the page reads that flag and never
re-derives the decision from `risk_level` plus an id suffix, as an earlier
version did. Assembly computes it once, as the union of `risk_level == "low"`
and `review_bucket == "security_auto"`, restricted to the tool's baseline
`upgrade` suggestion with `auto_runnable` true (`assembly.md` §Review Buckets
and Pre-Accept, `schemas.md` §1.6). A second derivation in the page is exactly
how the rendered state and the submitted payload drift apart. An absent
`pre_accept` reads as `false`; the card is a normal toggle afterward either
way. **A followup card never pre-accepts**, whatever the flag says — a card
surfaced mid-apply (`rendering-results.md` §Turn-Based Threads) posts turns to
`/followup` rather than carrying a pre-Submit decision, so starting it
accepted would assert a decision nobody made in that thread.

**A pre-accepted `needs_sudo: true` suggestion must render a "needs admin
password" chip** — `🔒 needs admin password`, `title` "Accepted by default,
but applying it prompts for an admin password." This is not optional
decoration: `needs_sudo` deliberately does *not* block pre-accept (blocking it
would un-pre-accept nearly every cask, since the heuristic defaults casks to
`true`), and the chip is the whole reason that is acceptable — it is what
keeps a pre-accepted admin-password upgrade from being silent. It renders
everywhere such a suggestion appears, **including its Overview mirrors** (auto
strip rows, mixed-card feet, highlight suggestion rows), because a mirror the
user decides from without ever opening the card is precisely the case the chip
exists for.

The chip is orthogonal to the existing `🔒 May prompt for an admin password
during apply.` note in an `upgrade` card's body, which renders for any
auto-runnable `needs_sudo` suggestion whether pre-accepted or not.

### Submit Behavior

Submit: POST JSON to `/feedback`; on 200 show full-page overlay "Feedback
submitted — return to your terminal"; on error keep data, show retry. (This
is the pre-extension behavior; the current page instead transitions into the
Results view on success — see §Transition to Results View below.)

## Decision State and Mirrors

The Overview and the tool list both offer Accept/Reject/Discuss for the same
suggestions. The rule that keeps them from becoming two answers to one
question:

> **The canonical decision lives on
> `.suggestion-card[data-suggestion-id]` inside `#main`, and nowhere else.
> Everything on the Overview is a mirror: a control that writes to the
> canonical card and re-reads its state.**

There is no second decision store. Mirrors are *rendered* from `REPORT`, but
their **state** is never read from `REPORT` — only from the DOM card.

`setDecision(sid, action)` is the one place a decision changes; the tool
card's own `.btn-decision` handler routes through it too, so canonical
controls and mirrors run identical code. It looks up the canonical card,
**no-ops when that card's buttons are disabled** (so a stale mirror click can
never mutate a frozen post-Submit report), toggles
`card.dataset.decision` — clicking the active decision returns to undecided,
exactly as the tool card always behaved — then updates the state label, the
tool's decision badge, every mirror of that id, the band accepted-counts, and
the progress/gate/blocking strip.

### The `data-mirrors` contract

**Mirrors must not use the class `.suggestion-card` and must not carry
`data-suggestion-id`.** They carry `data-mirrors="<suggestion id>"` (or
`data-mirror-dot` for a read-only glyph). This single rule is what keeps three
existing selectors correct without a single edit:

- `submitFeedback()` walks `#main .suggestion-card[data-suggestion-id]` to
  build the payload — mirrors are excluded, so the payload count is the
  suggestion count no matter how many mirrors are on screen;
- `updateProgress()` counts `#main .tool-section[data-max-severity=
  "incompatible"] .suggestion-card` for the Submit gate;
- `sectionNeedsDecision()` drives the "Needs decision first" sort.

Three mirror variants, all reading the same canonical state:

| Variant | Markup | Where |
|---|---|---|
| Three-button | `.mirror[data-mirrors]` containing three `.btn-d` | mixed-card feet, highlight suggestion rows |
| Single toggle | `.acc-toggle[data-mirrors]` — `✓` / `○` | auto-strip rows |
| Read-only dot | `[data-mirror-dot]` inside a chip | routine / attention bands |

`.btn-d` reuses `.btn-decision`'s active-state colors exactly — accept →
filled `--cyan`, reject → filled `--base01`, discuss → filled `--yellow` —
with smaller metrics and no fourth state.

`syncMirrors(sid)` refreshes every node for one id; `syncAllMirrors()` does a
full pass and runs once after the initial render, to pick up everything
assembly pre-accepted, and after any bulk change.

**The `/feedback` payload shape is unchanged.** Pre-accepted and
mirror-accepted items are ordinary `accept` decisions; there is no new
decision vocabulary and no new payload field.

## Long Strings and Overflow

Required, not polish — this is a real layout failure with a real input. Cask
versions are `version,build` tuples, and `cask:cursor`'s is
`3.12.17,0fb762053c34788bb7760d5673f8a6d4c8589d52`. Untruncated it blew the
tool header row apart and, through grid/flex `min-width: auto`, forced **737px
of horizontal scroll on a 390px viewport**.

1. **`min-width: 0` on every flex/grid child that can contain a version, a
   suggestion title or a tool name.** The default `min-width: auto` lets a
   long unbreakable token set the track's min-content width, which is the
   whole mechanism above. Applied to `.hlcard > *`, `.mixcol`, `.autorow > *`,
   `.hlsug .t`, `.tool-header > *`, `.itemline > span:last-child`,
   `.cve-chips`, `.sevmeter`, `.autostrip-head .names` and `#progress-text`.
2. `.version-delta, .vd { max-width: 34ch; overflow: hidden; text-overflow:
   ellipsis; white-space: nowrap }`, with the full string in `title`.
3. `.chip { max-width: 230px }` with the same truncation and a `title`
   carrying name + version.
4. **The page body must never scroll horizontally at 390px.** Assert it
   directly: `document.documentElement.scrollWidth === clientWidth`.

## Brew-Health Rendering

The page treats a `brew-health` tool like any other card except the header's
version-delta slot shows the finding category label (e.g. "untrusted tap")
instead of `null → null`, no delta pill renders (there is no version pair),
and the source badge uses the dedicated `brew-health` color (§Palette). See
`collection.md` §Brew-Health Collection for the finding taxonomy this
renders, and `assembly.md` §Brew-Health Assembly for how a finding becomes a
Tool object in the first place.

**On the Overview**, health findings get their own band — *Homebrew
environment*, **expanded by default**, one row per finding with a severity
icon, the name, the remediation command in a copyable chip, and
`details →` (§Overview Tab → Everything Else). They are grouped and counted
by `source === 'brew-health'` and `summary.health_count`, **never** by
`review_bucket`: bucket is a review-effort axis orthogonal to source, and
`routine` on the one expected PATH note means "nothing to decide here", not
"hide it". `findingRowSeverity()` is `maxSeverity()` with a floor — the
per-source fallback it used to need is gone, because a health finding's
synthesized item carries a `local` block by construction and `maxSeverity()`
reads exactly those.

**The null-version rule also applies in `highlights[]`.** A health finding
that scores high enough to be ranked renders its finding category label in the
versions slot and drops the delta pill; without that it rendered
`null → null` as "→ UNKNOWN".

## Skill-Drift Rendering

A `skill-drift` tool renders like any other card with the same three
subtractions brew-health takes, for the same reason — there is no version
pair (`assembly.md` §Skill-Drift Assembly):

- the header's version slot shows a **drift-state label** — `upstream
  ahead`, `local patch`, `diverged`, `unverified`, `in sync` — from a fixed
  `drift_state` → label map (`DRIFT_LABEL`), kept **separate** from the
  health-category map rather than merged into it: the two vocabularies are
  unrelated, and one shared map would let a health category resolve a drift
  state by accident. The fallback names its own source too
  (`skill drift`/`environment health`), so an unrecognized value can never
  be captioned as the other source's;
- **no delta pill**, in the tool header and in `highlights[]` alike (it
  never reaches the mixed-security card — `has_security` is false for this
  source);
- the source badge uses `--green` (§Palette).

**The label map is the whole card's honesty.** `local_only` must read as
*we* changed this — `local patch`, never "behind" and never "outdated". A
label implying upstream moved would invite the user to accept a sync that
discards their own customisation, which is precisely the confusion the
three-way comparison exists to remove (`collection.md` §Skill-Drift
Collection). `probe_error` → `unverified` for the mirror-image reason: it is
not a drift verdict at all, it means we could not look, and a label that
sounds like one would report a failure to check as a clean bill of health.

**Two rules generalize rather than duplicate.** Everywhere the page asks
"does this tool have a version" — the version slot, the delta pill, the
lede's denominator, the `by_delta` fallback, the routine/attention chip
clouds, the "everything else" total — the test is **membership in the set of
non-version sources**, not equality against `brew-health`. Everywhere it
asks "which band / which label map is this", the test stays source-specific:
the Homebrew-environment band is brew-health's alone, and vendored skills
get their own sibling band (§Overview Tab → Everything Else). Blanket
replacement in either direction breaks the half it does not fit.

**On the Overview**, drift findings appear only in the *Vendored agent
skills* band, **collapsed by default** — one row per skill, severity icon,
name, the sync command in a copyable `.cmd` chip, `details →` — and never in
the routine/attention chip clouds. They are grouped and counted by
`source === 'skill-drift'` and `summary.skill_drift_count`, **never** by
`review_bucket`: `routine` on a `local_only` finding means "nothing to decide
here", not "hide it". As with health findings, the band's row severity is
`maxSeverity()` with a floor: the synthesized item carries a `local` block, so
no per-source fallback is needed.

**Its suggestion, when it has one, never renders pre-accepted**, whatever
else is true of the tool: the id ends `:sync`, so assembly wrote
`pre_accept: false`
(`schemas.md` §1.6). The card is a normal Accept/Reject/Discuss toggle, with
the manual-run hint an `auto_runnable: false` upgrade card already shows
(§Suggestion Card) — the command is one the user runs themselves
(`apply.md` §Skill-Drift Remediation).

## Keyboard Navigation

The existing keys all keep working; the model generalizes from "focused tool
section" to "focused item in the active tab", so `focusedIdx` is per-tab
(`{overview: -1, tools: -1}`).

| Key | Overview | All tools |
|---|---|---|
| `1` … `4` | switch tab (post-Submit the strip has four) | same |
| `j` / `k` | next / previous Overview card — the mixed security cards, then the highlight cards | next / previous visible tool section (unchanged) |
| `a` / `r` / `c` | act on the focused card's **first undecided mirror** | act on the focused tool's first undecided suggestion card (unchanged) |
| `s` | Submit when enabled (unchanged) | same |
| `f` | switch to All tools, then cycle the filter preset | cycle preset All → Incompatible → Relevant (unchanged) |
| `g` | jump into the focused card's tool | — |
| `Escape` | close modal / help | close modal / help; else back to Overview if you arrived by jump |
| `?` | help overlay (unchanged) | same |

On the Overview, "first undecided" is resolved against the **canonical cards**
the mirrors point at, never against any state held on the mirror itself
(§Decision State and Mirrors), and `j`/`k` skip any card parked in a collapsed
overflow list — focusing one would move the ring nowhere visible. Focus is a
blue outline plus
`scrollIntoView({block: 'nearest'})`, the same treatment tool sections already
had. Suppressed while typing in an `INPUT`/`TEXTAREA` and while any modifier
is held; tab switching and `?` work in every phase, everything else defers to
the Results view once it is active. The `?` overlay's table carries these rows
under a "Tabs" grouping.

## Transition to Results View

After Submit (or on page load if `feedback.json` was already submitted —
`GET /status` returns 200), the page transitions into a Results view instead
of the static success overlay described in §Submit Behavior above — this is
the exact point where this doc hands off. Everything from here on (the
Results panel layout, action list, followups, polling, the pre-report loading
page) is specified in full in `rendering-results.md`; it is not reproduced
here.

Three things about the transition belong on *this* side of the handoff,
because they are consequences of the tab shell:

- **The strip is rebuilt from the panel registry, not by replacing
  `#progress-bar-container.innerHTML`.** `transitionToResults()` removes the
  progress wrap, the auto-run toggle and the Submit button from the shell bar,
  registers `results` and `changelog` in `PANELS`, re-renders the strip,
  appends the right-aligned `#tab-status`, and selects `results`.
- **The Overview survives as its own tab** rather than being replaced — it is
  the summary of what was just approved, and the most useful thing to look at
  while an apply runs.
- **The Overview's mirror controls must be frozen explicitly.** They live
  outside `#main`, so the existing freeze set
  (`.btn-decision, .card-comment, .tool-note-textarea, #overall-comment`
  inside `#main`) does not reach them: `#panel-overview .btn-d` and
  `#panel-overview .acc-toggle` are disabled too, and `.report-frozen` is
  added to `#panel-overview` as well as `#main`.

`.report-frozen` is a blanket `opacity: 0.5; pointer-events: none`, and both
panels then carve back out of it everything that is not a decision. The rule
the carve-outs implement, stated once and specified control-by-control in
`rendering-results.md` §View Transition: **a frozen report stays readable and
navigable; only decision controls go inert.** It is the evidence the user
reads while the apply runs, and the blanket rule inherits all the way down, so
without the exceptions below it takes the whole report with it:

- `#main` keeps full opacity and dims only `#tool-list` and
  `#overall-section`, so the **filter bar, its hidden-count banner and the
  filtered-empty state keep their pointer events** — §Filter Bar requires the
  bar to stay interactive after Submit, and the blanket rule would otherwise
  take it down with the list it sits inside.
- **`#tool-list` gets its pointer events back wholesale** — dimmed, but live.
  Left under the blanket rule it loses every collapse toggle, every copy
  button and command chip, every changelog link, every embedded-excerpt
  disclosure (§Link Click Behavior) *and* mouse text selection over the entire
  report, for the rest of the page's life. It is restored as a block rather
  than control-by-control on purpose: a whitelist of buttons leaves every run
  of prose unhittable, and a report you cannot select a version string out of
  is read-only in name only. The decision controls inside it — `.btn-decision`,
  `.card-comment`, `.tool-note-textarea` — are killed again by their own rule,
  deliberately redundant with the `disabled` attribute `transitionToResults()`
  already set on them: a disabled control ignores clicks whatever its pointer
  events, and the redundancy guarantees that restoring the list can never be
  what makes a decision live again. `#overall-section` is *not* restored — it
  holds nothing but the overall comment box, which is a decision.
- `#panel-overview` restores pointer events on `.jump`, `.more`, `.tile`,
  `.chip`, `.cmd`, `.band-head`, `.autostrip-head`, `.secdetail-head`,
  `.btn-bar-action` and the proportion-bar segments — a list rather than a block, because this panel's
  decision mirrors sit among its navigation controls. **The `.btn-bar-action`
  entry is load-bearing**: it is what the two `show all N →` controls are, and
  without it every card parked behind a cap (§Overview Tab → Overflow Lists)
  would be unreachable for the rest of the page's life. **`.secdetail-head` is
there for the same reason**: reading is navigation, not decision, and without
it every collapsed CVE list on the page stays shut forever.

## Template Variables

Exactly three tokens, replaced by plain string substitution (no template
engine):

```html
<meta name="report-id" content="__REPORT_ID__">
<meta name="generated-at" content="__GENERATED_AT__">
<script>const REPORT = __REPORT_DATA__;</script>
```

```python
html = html.replace('"__REPORT_ID__"', json.dumps(report_id))
html = html.replace('"__GENERATED_AT__"', json.dumps(generated_at))
report_json = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
html = html.replace('__REPORT_DATA__', report_json)
```

`__REPORT_DATA__` is unquoted in the template so the JSON object lands as a
JS expression. The other two sit inside attribute quotes, so the replacement
target includes the quotes. The `"</"` → `"<\\/"` escape on `__REPORT_DATA__`
guards against a literal `</script>` inside any agent-written free-text
field (item titles and bodies, rationale, `config_status.detail`,
`tool_comments`, ...)
— release notes and security advisories routinely quote HTML/JS snippets —
prematurely closing the `<script>` tag and corrupting the rest of the page.
`rendering-results.md` §Markdown Rendering reuses this same escape-first
discipline for agent-authored recap/changelog/turn text.

Rendering is done in JS from `REPORT.tools[]`: sections carry `data-tool-id`,
`data-name`, `data-source`, `data-max-severity`, `data-bucket`, `data-delta`
and `data-sec`; suggestion cards carry `data-suggestion-id` and
`data-decision` (CSS attribute selectors drive visual state); Overview mirrors
carry `data-mirrors`/`data-mirror-dot` and deliberately carry *neither* of the
suggestion-card attributes (§Decision State and Mirrors). Submit walks
`#main`'s suggestion cards to build the feedback payload.

Suggestion ids: `{source}:{name}:{slug}` — deterministic kebab-case slug of
the action, never index-based, unique within the report (`-2`, `-3` suffix
on collision). The session looks up accepted ids in its in-memory report to
get `target_files`, `diff_preview`, `rationale` for the edit.
