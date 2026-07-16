# Rendering: Report Page

The pre-Submit report page design: the Solarized Dark palette, full page
layout (header, filter bar, sticky progress bar, per-tool sections, content
groups, suggestion cards), keyboard navigation, and the template-variable
injection mechanism `render.py` uses to turn a report object into
`index.html`.

This doc renders the shapes defined in `schemas.md` (§Report Object) — read
that first if you need a field's exact meaning rather than how it's drawn.
`assembly.md` is the source of truth for `risk_level` and which suggestions
start pre-accepted; this doc only describes how that state is *displayed*.
For everything that happens after the user clicks Submit, see
`rendering-results.md`.

Table of contents:
- Palette
- Page Layout (header/counts, filter bar, sticky progress bar + Submit
  gating, per-tool section, header badges, collapse controls, content
  groups, per-item severity mapping, link click behavior, Context section,
  Release Inventory section, vendor-silent tag, per-item detail collapse,
  suggestion cards, Submit behavior)
- Brew-Health Rendering
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
| `--green`  | `#859900` | version delta new-version text, diff additions |
| `--violet` | `#6c71c4` | mise source badge |
| `--magenta`| `#d33682` | standalone source badge |

All `source` values (`schemas.md` §Report Object) get a badge: `brew`/`cask`
reuse the severity/link colors above (blue/orange), `mise`/`standalone` have
dedicated colors, `macos` reuses `--base00` as a neutral "system-level, not a
package manager" badge, and `brew-health` gets its own dedicated color (see
§Brew-Health Rendering below).

Single file: all CSS and JS inline. Zero CDN calls; system font stacks only.
Must render correctly offline. `rendering-results.md`'s elements reuse these
same tokens rather than introducing new ones.

## Page Layout

### Header and Counts

Header: title, date, machine context (arch highlighted when Intel — it gates
compatibility). Counts row: tools with updates, incompatible, suggestions.

### Filter Bar

Source select (All|brew|cask|mise|standalone), severity select, "Only
relevant to me" toggle (hides tools with empty `relevancy[]`), sort select
(**Needs decision first** [new default] | Incompatible first | Name | Source
| Major-delta first). "Needs decision first" sorts any tool with at least one
undecided suggestion (including the baseline `upgrade` suggestion — nearly
every tool has one) above tools whose suggestions are all decided/absent;
incompatible severity breaks ties within that. Client-side only: toggle
`display:none` and reorder DOM nodes. **The filter bar must stay reachable in
the frozen Results view** (see `rendering-results.md`) so a filter applied
before Submit can still be cleared/inspected afterward — never leave the user
stuck looking at a filtered-empty report with no way to reset it.

### Sticky Progress Bar and Submit Gating

Sticky progress bar (`position: sticky; top: 0`): thin progress bar (cyan
decided / base01 remaining), text "N of M decided · K incompatible undecided"
(red flash when K > 0), Submit button — disabled until every suggestion on an
`incompatible`-severity tool has a decision; tooltip explains why.
Non-incompatible suggestions may be left undecided.

### Per-Tool Section

Per-tool `<section>`: collapsible; header row with name, `current → latest`
(latest in green), source badge, PINNED badge (yellow) when pinned, and a
**`config_status` badge** when `state` isn't `"unknown"`: a quiet small green
check + "config current" for `up_to_date` (hover/click for the
`detail`/evidence), a visible orange/red banner for `needs_attention` ("⚠
config may be stale — {detail}") placed right under the header before any
content group, since it's a review-the-review flag the user shouldn't have
to dig for. `config_status` is computed by research — see `research.md`
§Config Status for how the verdict is reached; this section only covers how
it's drawn.

### Header Badges

At a glance without expanding:

- **Decision-count badge**: while the tool has ≥1 undecided suggestion, show
  an attention-styled badge with the undecided count (colored icon — e.g.
  `--yellow` "?" or "!" — not neutral chrome, since this is the "does this
  need me" signal). Once every suggestion on the tool has a decision,
  downgrade to a quiet "N decided" badge (`--base01`, same low-key treatment
  as the `config_status` "ok" badge) — the badge never disappears, it just
  stops demanding attention.
- **Severity-tier counts**: one small count per severity level actually
  present among the tool's headliners+relevancy items (info/notable/warning/
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
wall of buttons — both `headliners[]` and `relevancy[]` render entirely
inside four content groups: Security, Fixes, Features, Notes.** Each item
carries its own `category` (which group) and `severity` (`schemas.md`, both
assigned by research) — the groups themselves are neutral, purely-
organizational containers with no color of their own; **only individual
items are colored**, by their own `severity`, independent of which group
they're in. This is a deliberate change from an earlier version of this
design that colored the whole group box by a single severity/notability
accent and derived category client-side from keyword matching — that
heuristic is exactly what caused topic and urgency to get conflated (a
low-profile security item reading as "minor" would get bucketed into Notes
by the same signal that was supposed to be its severity, not its topic).
Research assigns both explicitly now; the page just renders what it's given,
no classification logic of its own. Nothing is shown twice: a headliner
that's really a security fix renders once, under Security, not also in a
generic list. Evidence paths for relevancy items stay attached to their item
wherever it lands. Lead with title + one-line description for each item;
push its changelog/release link into a compact footer-style reference per
item (a direct deep link where the source supports line-level anchors, e.g.
a CHANGELOG.md section) rather than a shared links block.

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

From `context[]` (`schemas.md`): a callout, visually distinct from the four
groups above — no severity coloring, since these aren't change-risk items.
Each item starts **collapsed to its `title` only**; clicking expands to show
`detail`/`evidence`/`link`. This is a stronger collapse default than
suggestion cards or content-group items get elsewhere — context items tend
to be a one-line claim followed by a full paragraph (e.g. "the existing
warning and control flow remain accurate as written" followed by the
reasoning why), and showing that expanded by default for every tool would
bury the actual changelog content above it.

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

A baseline `upgrade` suggestion on a `"low"` `risk_level` tool renders
pre-accepted (Accept button already shown active) instead of undecided — see
`assembly.md` §Risk Level for the computation; this doc only covers the
resulting visual.

### Submit Behavior

Submit: POST JSON to `/feedback`; on 200 show full-page overlay "Feedback
submitted — return to your terminal"; on error keep data, show retry. (This
is the pre-extension behavior; the current page instead transitions into the
Results view on success — see §Transition to Results View below.)

## Brew-Health Rendering

The page treats a `brew-health` tool like any other card except the header's
version-delta slot shows the finding category label (e.g. "untrusted tap")
instead of `null → null`, the source badge uses the dedicated `brew-health`
color (§Palette), and a `health_count` header badge counts them separately
from `total_outdated` (they're environment issues, not updates). See
`collection.md` §Brew-Health Collection for the finding taxonomy this
renders, and `assembly.md` §Brew-Health Assembly for how a finding becomes a
Tool object in the first place.

## Keyboard Navigation

Nice-to-have: `j`/`k` next/previous tool section (blue outline, scroll into
view); `a`/`r`/`c` act on first undecided suggestion in focused tool
(repeats cycle); `s` submit if ready; `f` cycle filter presets All →
Incompatible → Relevant; `?` help overlay. Suppressed while typing in a
textarea.

## Transition to Results View

After Submit (or on page load if `feedback.json` was already submitted —
`GET /status` returns 200), the page transitions into a Results view instead
of the static success overlay described in §Submit Behavior above — this is
the exact point where this doc hands off. Everything from here on (the
Results panel layout, tab strip, action list, followups, polling, the
pre-report loading page) is specified in full in `rendering-results.md`; it
is not reproduced here.

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
field (headliners, rationale, `config_status.detail`, `tool_comments`, ...)
— release notes and security advisories routinely quote HTML/JS snippets —
prematurely closing the `<script>` tag and corrupting the rest of the page.
`rendering-results.md` §Markdown Rendering reuses this same escape-first
discipline for agent-authored recap/changelog/turn text.

Rendering is done in JS from `REPORT.tools[]`: sections carry `data-tool-id`
and `data-max-severity` attributes; suggestion cards carry
`data-suggestion-id` and `data-decision` (CSS attribute selectors drive
visual state). Submit walks the DOM to build the feedback payload.

Suggestion ids: `{source}:{name}:{slug}` — deterministic kebab-case slug of
the action, never index-based, unique within the report (`-2`, `-3` suffix
on collision). The session looks up accepted ids in its in-memory report to
get `target_files`, `diff_preview`, `rationale` for the edit.
</content>
