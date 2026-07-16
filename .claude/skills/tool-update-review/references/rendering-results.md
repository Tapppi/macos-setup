# Rendering: Results View

Everything that happens after Submit: the view transition out of the frozen
Report tab, the tab strip and unified status header, the action list,
followups and turn-based threads, the Changelog tab, the Finish button, the
polling lifecycle, markdown rendering, and — because it shares the same
design language and state-icon vocabulary — the pre-report loading page
shown before the report even exists.

This doc renders `status.json` and `research-status.json`, whose exact
shapes live in `schemas.md` (§status.json, §research-status.json) — read
those for field meanings. `server-and-session.md` documents the endpoints
this view polls/posts (`/status`, `/research-status`, `/shutdown`,
`/followup`) and the server-side loading-page code (`LOADING_PAGE_HTML` in
`server.py`) whose *design* is specified here. `apply.md` §Turn-Based
Threads documents the session-side half of turn handling (acting on new
turns, `sync-turns`) that pairs with this doc's rendering half.
`rendering-report.md` is the pre-Submit counterpart — its §Palette defines
the tokens this doc reuses, and its §Transition to Results View is the exact
handoff point into this doc.

Table of contents:
- Initial Load
- Submit Handling
- View Transition
- Layout
- Wireframes
- Element Detail (tab strip, unified status header, followups section,
  action list, recap section, changelog tab, Finish button)
- Polling Lifecycle
- Render Update Cycle
- Constraints
- Turn-Based Threads
- Markdown Rendering
- Loading Page

## Initial Load

On `DOMContentLoaded`, before rendering the report view, the page issues one
probe:

```js
const base = location.pathname.replace(/\/+$/, '');

async function probeStatus() {
    try {
        const r = await fetch(base + '/status', { cache: 'no-store' });
        if (r.ok) {
            const data = await r.json();
            // feedback already submitted (e.g. page refresh mid-apply)
            transitionToResults(data);
            startPolling();
            return;
        }
    } catch (_) { /* server not up yet, or network error — fall through */ }
    // Normal path: render report view
    renderHeader();
    renderTools();
    updateProgress();
    applyFilters();
}
probeStatus();
```

404 or any network error: render report view normally. 200: go straight to
Results view and start polling. This handles page refreshes during apply.

## Submit Handling

Replace the success-overlay code (`rendering-report.md` §Submit Behavior)
with:

```js
if (resp.ok) {
    transitionToResults(null);   // null = no initial data yet
    startPolling();
} else { /* existing error path */ }
```

## View Transition

`transitionToResults(initialData)`:

1. Leave `#filter-bar` visible and interactive — it's a read-only navigation
   aid over the now-frozen Report tab, not a decision control, so a filter
   applied before Submit can still be cleared/inspected afterward instead of
   leaving the user stuck looking at a filtered-empty report with no way to
   reset it.
2. Replace `#progress-bar-container` contents with the tab strip + results
   status bar (see §Layout below). Do not remove the element; keep it
   sticky.
3. Freeze only the decision controls in the report view: `.btn-decision`,
   `.card-comment`, `.tool-note-textarea`, and `#overall-comment` inside
   `#main` get `disabled`; add a CSS class `report-frozen` to `#main` that
   reduces opacity to 0.5. Collapse toggles, the command-chip copy button,
   and the filter/sort controls (outside `#main`) stay interactive.
4. Create `#results-panel` div (see §Layout), insert it into the DOM after
   `#progress-bar-container` and before `#main`.
5. Show `#results-panel`, hide `#main` (the Report tab will toggle these).

`transitionToResults(initialData)` accepts the first status blob or null. If
non-null, render it immediately before the first poll arrives.

**The transition must show a loading/spinner state immediately**, before the
first `/status` fetch resolves — there is real network + apply latency
between Submit and the first status write, and a blank/static panel during
that gap reads as broken. Don't wait for data to render *something*.

## Layout

Rewritten per live feedback: two separate status displays (tab-strip bar +
a results status bar) merged into one unified header; followups and recap
moved above the action list; Changelog promoted to its own tab instead of a
Results-panel subsection; recap collapsed by default so the page stays
glanceable as it grows.

```
#progress-bar-container (repurposed — stays sticky):
┌──────────────────────────────────────────────────────────────────┐
│  [Results ●]  [Report]  [Changelog]      Applying… · updated 0s │
└──────────────────────────────────────────────────────────────────┘

#results-panel:
┌──────────────────────────────────────────────────────────────────┐
│  #results-header (unified — always visible)                     │
│  Applying changes… · updated 0s ago                              │
│  [progress bar ===========------------------]  41/72 done       │
│  0 failed · 2 followups: 0 resolved, 2 pending                  │
├──────────────────────────────────────────────────────────────────┤
│  FOLLOWUPS  (hidden until pending_followups non-empty)          │
│  ... thread cards (§Turn-Based Threads) ...                     │
├──────────────────────────────────────────────────────────────────┤
│  ACTIONS                                                         │
│  [ state icon ]  label                         note (if any)    │
│  a "failed" row expands inline to retry/debug controls           │
│  ...                                                              │
├──────────────────────────────────────────────────────────────────┤
│  NOTES / RECAP  (collapsed by default — title only until expanded, │
│                  hidden entirely until recap non-empty)          │
├──────────────────────────────────────────────────────────────────┤
│                                    [Finish ↗]  (disabled / enabled) │
└──────────────────────────────────────────────────────────────────┘

#changelog-panel (separate tab, not part of #results-panel):
┌──────────────────────────────────────────────────────────────────┐
│  Changelog — {date}              (date shown once for the run)  │
│  ### brew: gh 2.48.0 → 2.52.0                    ▸ show          │
│  ### cask: google-chrome 148.0.7778.179 → 150.0.7871.47   ▸ show │
│  ...                                                              │
└──────────────────────────────────────────────────────────────────┘
```

## Wireframes

### In-progress state, with pending followups

```
╔══════════════════════════════════════════════════════════════════╗
║  [Results ●]  [Report]  [Changelog]      Applying… · updated 1s ║
╠══════════════════════════════════════════════════════════════════╣
║  Applying changes · updated 1s ago                               ║
║  [=============---------------------------]  2 / 5 done         ║
║  0 failed · 1 followup: 0 resolved, 1 pending                   ║
╠══════════════════════════════════════════════════════════════════╣
║  FOLLOWUPS                                                       ║
║  mise:node — "Hold off until the project upgrades its .nvmrc"   ║
║  [ Accept ] [ Reject ] [ Discuss ]   comment…        [ Send ]   ║
╠══════════════════════════════════════════════════════════════════╣
║  ACTIONS                                                         ║
║  ✓  brew:podman:keep-pin-add-comment  Add pin comment to Brewfile║
║  ✓  commit:dotfiles                   Committed abc1234          ║
║  ⠇  mise:node:update-nvmrc            Updating .nvmrc…          ║  ← spinner
║  ○  commit:macos-setup                (pending)                  ║
║  —  brew:curl:no-action               Skipped (rejected)         ║
╠══════════════════════════════════════════════════════════════════╣
║  NOTES / RECAP                          (greyed — not yet ready) ║
╠══════════════════════════════════════════════════════════════════╣
║                                    [Finish ↗]  (disabled, grey)  ║
╚══════════════════════════════════════════════════════════════════╝
```

### Done state, with a failed action expanded for debugging

```
╔══════════════════════════════════════════════════════════════════╗
║  [Results ●]  [Report]  [Changelog]  Done · 2026-07-04 14:58 UTC║
╠══════════════════════════════════════════════════════════════════╣
║  Complete · done                                                 ║
║  [==========================================]  4 / 4 done       ║
║  1 failed · 0 followups                                         ║
╠══════════════════════════════════════════════════════════════════╣
║  ACTIONS                                                         ║
║  ✓  brew:podman:keep-pin-add-comment  Add pin comment to Brewfile║
║  ✓  commit:dotfiles                   Committed abc1234          ║
║  ✓  mise:node:update-nvmrc            Updated .nvmrc to 22.x    ║
║  ✗  cask:karabiner-elements:upgrade   Failed: sudo required      ║  ← expanded below
║     brew upgrade --cask karabiner-elements                       ║
║     [ Retry ]   comment…                        [ Send ]        ║
╠══════════════════════════════════════════════════════════════════╣
║  NOTES / RECAP                                        ▸ show     ║
╠══════════════════════════════════════════════════════════════════╣
║                                    [Finish ↗]  (enabled, cyan)   ║
╚══════════════════════════════════════════════════════════════════╝
```

### Changelog tab

```
╔══════════════════════════════════════════════════════════════════╗
║  [Results]  [Report]  [Changelog ●]        Done · 2026-07-04    ║
╠══════════════════════════════════════════════════════════════════╣
║  Changelog — 2026-07-04                                          ║
║  ### brew: gh 2.48.0 → 2.52.0                          ▸ show   ║
╚══════════════════════════════════════════════════════════════════╝
```

### Closing splash (full-page overlay, after Finish clicked)

```
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║                         ✓                                        ║
║                   Session complete                               ║
║           This window can be closed.                             ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

## Element Detail

**Tab strip** (replaces progress-bar-container contents):
- Three buttons: "Results", "Report", "Changelog" (Changelog is a sibling
  tab, not a Results-panel subsection, since it's audit-trail content
  someone browses independently of live apply progress). Active tab uses
  `--cyan` underline or filled background. Clicking one shows its panel,
  hides the other two (`#main`, `#results-panel`, `#changelog-panel`).
- Right-aligned status text: phase label + "updated N s ago" computed from
  `written_at`. Turn red if `written_at` is >120 s ago and `done` is false
  (stale session hint).

**Unified status header** (`#results-header`, merging what used to be two
separate displays into one, always visible at the top of `#results-panel`):
- Line 1 — phase + staleness, same text/logic the tab strip used to show
  alone: `"Applying changes"` / `"In discussion"` / `"Complete"`, plus
  `"· updated N s ago"`, plus `"· session may have stopped"` in yellow when
  stale (>120s, `done` false).
- Line 2 — a progress bar (`#results-progress-track`/`#results-progress-fill`,
  same visual language as the Report tab's sticky progress bar,
  `rendering-report.md` §Sticky Progress Bar and Submit Gating) whose fill
  width is `doneCount / totalActions` and which dynamically absorbs the
  row's leftover horizontal space (`flex: 1`) rather than a fixed width; a
  done-count label (`"41 / 72 done"`) sits alongside it.
- Line 3 — failure count (`"K failed"`, red when K > 0) and a followup
  count split into resolved/pending (`"J followups: R resolved, P pending"`,
  pending > 0 in yellow) — **computed live from `pending_followups` on
  every poll** (count `resolution !== "pending"` vs `=== "pending"`), never
  baked into recap prose. This is the fix for a real staleness bug: recap
  text used to say "2 pending followups still need your decision" and that
  sentence went stale the instant the user decided one, because it was
  frozen prose written once. The live count here can't go stale the same
  way since it's recomputed from the actual array shape every render.

**Followups section** (`#followups-section`, moved above the action list):
hidden until `pending_followups` is non-empty. One thread card per entry
(§Turn-Based Threads) — multi-turn, not a one-shot decision. Rendered before
`#action-list` so the things most needing the user's attention aren't
buried below a scroll.

**Action list item** per action entry:

| State   | Icon | Color             |
|---------|------|-------------------|
| pending | ○    | `--base01`        |
| running | ⠇    | `--cyan` (spins)  |
| done    | ✓    | `--green`         |
| failed  | ✗    | `--red`           |
| skipped | —    | `--base01`        |

Row layout: `[icon] [label]  [note]` on one line. Clicking a row expands a
`<pre>` showing `detail` lines (if non-empty), in monospace, `--base00` text
on `--base03` background. Collapsed by default. **A `"failed"` row gets an
inline retry/debug thread instead of pointing at recap for remediation** —
same Turn-based mechanism as a followup (§Turn-Based Threads), scoped to
that action's own `thread` field (`schemas.md`). Recap no longer needs to
hold any per-failure remediation text; it's pure narrative summary now.

**Recap section** (`#results-recap`): hidden until `recap` is non-empty.
**Collapsed by default** — only a section label + one-line "Notes / Recap ▸
show" control is visible; expanding reveals the full text, rendered as
parsed markdown (§Markdown Rendering) rather than a raw `<pre>` dump. This
keeps the top of the panel glanceable even as recap grows long; the unified
header above already covers the at-a-glance status, so recap's only job now
is narrative detail for whoever wants to read it.

**Changelog tab** (`#changelog-panel`, sibling to `#main`/`#results-panel`,
not nested inside the Results panel): hidden until `changelog_entries` is
non-empty (tab itself can still be clicked; shows an empty state rather than
being disabled). One shared date heading for the whole panel (all entries in
one run share the same date, per changelog.md's own `## {date}` convention —
see `apply.md` §Push and Terminal Status for how those entries are written;
this is display-only). Each entry renders **heading-only by default** — just
its `### {source}: {name} {old} → {new}` line — with a "▸ show" control
revealing the summary/detail text below it, parsed as markdown
(§Markdown Rendering).

**Finish button** (`#finish-btn`):
- Disabled (grey, `cursor:not-allowed`) while `done` is false.
- Enabled (cyan) when `done` is true.
- If any action is still in state `running` when Finish is clicked (should
  not happen if `done` is properly gated, but guard anyway): show a confirm
  dialog: "Session may still be running. Finish anyway?" — yes → proceed, no
  → cancel.
- On click: POST to `base + '/shutdown'`. On 200: show closing splash
  overlay (reuse `.overlay-box` structure; text "Session complete — this
  window can be closed"). On network error: show error toast "Shutdown
  failed — the session may have already ended. Safe to close this window."

## Polling Lifecycle

```js
let pollTimer = null;
let pollBackoff = 2000;   // ms; normal interval
let sessionEnded = false;

function startPolling() {
    if (pollTimer) return;
    schedulePoll();
}

function schedulePoll() {
    pollTimer = setTimeout(poll, pollBackoff);
}

async function poll() {
    pollTimer = null;
    if (sessionEnded) return;
    try {
        const r = await fetch(base + '/status', { cache: 'no-store' });
        if (r.ok) {
            const data = await r.json();
            pollBackoff = 2000;
            renderResults(data);
            if (data.done) {
                // Stop polling; Finish button is now enabled.
                return;
            }
        } else if (r.status === 404) {
            // Server up but status.json not yet written; back off.
            pollBackoff = Math.min(pollBackoff * 1.5, 10000);
        } else {
            pollBackoff = 5000;
        }
    } catch (_) {
        // Network error: server may have gone away prematurely.
        pollBackoff = Math.min(pollBackoff * 1.5, 15000);
        updateStatusBar('Connection lost — retrying…', 'warn');
    }
    schedulePoll();
}
```

Stop polling when `done: true` renders (no `schedulePoll()` call after that
branch). Also stop on closing splash (set `sessionEnded = true`).

**Stale indicator**: `renderResults` compares `written_at` to `Date.now()`.
If delta > 120 s and `done` is false, append to the tab status text: `"
· session may have stopped"` in yellow.

## Render Update Cycle

`renderResults(data)` is called on every successful poll and on
`transitionToResults(initialData)`. Idempotent: update existing DOM elements
by id rather than re-rendering from scratch (prevents scroll jumping).

Update:
1. Tab status text (phase, updated-ago).
2. `#results-status-bar` text.
3. Action list: for each action in `data.actions`, find or create the row by
   `data-action-id`, update icon, label, note, detail pre (toggled by
   click). Preserve expanded/collapsed state of detail pres across renders.
4. Recap section: show/hide + update text.
5. Changelog section: show/hide + update entries.
6. Finish button: enable/disable.

## Constraints

All new elements use the existing CSS custom properties — see
`rendering-report.md` §Palette for the token table; this view introduces no
new colors. Spinner animation: pure CSS `@keyframes spin` on the ⠇
character's parent span, or cycle through braille frames in JS
(`⠇⠏⠋⠙⠸⠴⠦⠧` at 120 ms) — either is fine.

Subpath-tolerant URL construction (same pattern as `/feedback`):
```js
const base = location.pathname.replace(/\/+$/, '');
// base + '/status', base + '/shutdown', base + '/feedback'
```

## Turn-Based Threads

A single shared mechanism backs two surfaces: a `pending_followups` entry
and a `"failed"` action's `thread` field (both `schemas.md` §status.json).
Both are an array of **Turn** objects — see `schemas.md` for the exact
shape (`turn`, `author`, `at`, `decision`, `comment`, `action_taken`); this
section covers only how that shape is rendered and interacted with.
`apply.md` §Turn-Based Threads is the session-side counterpart: what the
session does when a new turn appears (`sync-turns`).

**Decoupled decision-select from submit**: clicking Accept/Reject/Discuss on
the active (newest, still-open) turn only sets a local pending decision —
same as a normal Report-tab card (`rendering-report.md` §Suggestion Card:
"Clicking an active decision button toggles back to undecided"). A separate
**Send** button submits the decision + whatever's in the comment box as a
new turn. This was the literal bug report motivating this design: clicking
Discuss used to fire the POST immediately, with whatever the (empty)
comment box held at click time, leaving no chance to type first.

**Multi-turn, never permanently closed**: a thread's history renders
oldest-to-newest, each past turn shown as a read-only line (author, decision
if any, comment/action_taken); the newest turn is the only one with live
controls. Submitting a new turn appends rather than overwrites — even a
thread whose `resolution` is already `"applied"`/`"rejected"` still shows an
open comment box for an out-of-turn addition (e.g. a Gatekeeper quarantine
popup noticed after the original followup's suggestion was already applied).
Submitting with an empty comment is valid — it means "go investigate/retry,
no extra direction needed," not "do nothing."

**Failed-action inline retry/debug**: a `"failed"` action row expands (same
click-to-expand as its `detail` pre, §Element Detail) to show the manual
command, a **Retry** button (re-issues the same command — useful once e.g.
askpass is fixed, or for a transient failure likely to just succeed on a
second try), and the same comment-box-plus-Send turn UI as a followup,
writing into that action's own `thread` array rather than a separate
followup id. This is the same component as a followup thread, parameterized
by which array it reads/writes — not two parallel systems.

**Agent-initiated threads**: a `pending_followups` entry's `origin` field
(`schemas.md`) distinguishes `"user_comment"` (investigating a
`tool_comments`/`discuss` entry) from `"agent_initiated"` — the session
creating one on its own after hitting something during apply that needs an
explicit decision (a security-relevant system prompt, an unusual side
effect — see `apply.md` §Turn-Based Threads for when the session does this).
**Rendering is identical either way; `origin` is metadata, not a different
card shape.**

## Markdown Rendering

Recap, changelog entry detail, and turn `comment`/`action_taken` text are
all agent-authored and likely to contain basic markdown (bold, inline code,
links, lists) — that's how the apply-phase agent naturally writes. Replace
a `<pre>`-as-plain-text treatment with a lightweight markdown renderer (page
JS: escape first via the same discipline as `rendering-report.md` §Template
Variables' `</script>`-breakout guard, then reinsert only the literal markup
the renderer itself generates — headings, lists, bold, inline code, links —
so no raw HTML from agent text ever reaches the DOM). This is not a
general-purpose markdown library; just enough for what these fields
actually contain.

## Loading Page

A large candidate list makes research take minutes; before this design
existed, nothing was served until collect+research+assemble+render all
finished, so the browser tab sat blank the whole time. The server now
starts right after Collect, before research even begins (see
`server-and-session.md` §Starting the Server and §Pre-Report Status for the
session-side sequencing and the `research-status.json` schema in
`schemas.md`), and shows a live "gathering info" state instead.

The loading page is self-contained (no CDN, inline CSS/JS — same constraint
as the main report, `rendering-report.md` §Palette), embedded as a Python
string in `server.py` (`LOADING_PAGE_HTML`) rather than a separate templated
file, since it needs no report data at all — the *code* for it lives in
`server.py` and is documented as server mechanics in
`server-and-session.md`, but its visual/interaction *design* belongs here
alongside the other page-design docs. Solarized Dark, minimal: title, phase
label, a thin progress bar (`done` groups / total groups), and a per-group
list using the same state-icon vocabulary as this doc's own action list
(§Element Detail: ○ pending, spinner `running`, ✓ done, ✗ failed) — one
consistent icon language across the loading page, the action list, and (for
severity, a different but parallel vocabulary) `rendering-report.md`'s
per-item severity icons. Polls `GET {base}/research-status` on the same
growing-backoff pattern as this doc's own `/status` poll (§Polling
Lifecycle); on `phase: "ready"`, calls `location.reload()` — the next `GET
/` finds `index.html` and serves the real report.
</content>
