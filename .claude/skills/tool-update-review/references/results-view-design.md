# Tool Update Review — Results View Extension Design

Extension to `references/design.md`. Covers the live-tracking Results view that
replaces the static success overlay after Submit. The report view remains accessible
as a read-only tab. The server stays alive until the user clicks Finish.

Table of contents:
- A. `status.json` schema
- B. Server changes — endpoint spec
- C. Page design — transition, layout, wireframes, polling
- D. Session workflow changes for `SKILL.md`

---

## A. `status.json` Schema

### File location and write pattern

```
{session_dir}/status.json          <- live file; page polls this
{session_dir}/status.json.tmp      <- transient; os.replace()d into place
```

The server reads the file on every GET /status request. The session writes it
atomically: open `.tmp`, json.dump + trailing newline, `os.replace()` to final
path. The page tolerates 404 (server not yet written first status) and
handles partial/older-shaped responses gracefully by defaulting every field
it reads (`data.summary || {}`, `?? 0`, etc.) rather than branching on
`schema_version` — there's no code path that actually inspects that field
today; it exists so a future incompatible schema bump has somewhere to
signal itself.

### Schema (schema_version 2)

```jsonc
{
  "schema_version": 2,
  "report_id": "tool-update-review-20260704T143012",

  // "applying" | "discussing" | "done"
  // "applying"   — session executing accepted suggestion actions
  // "discussing" — apply pass complete, session surfacing discuss items in
  //                conversation (user may still be interacting with session)
  // "done"       — all work complete; done flag true; Finish button enabled
  "phase": "applying",

  "started_at": "2026-07-04T14:52:10Z",  // when session began apply pass
  "written_at": "2026-07-04T14:53:42Z",  // timestamp of this write (staleness check)

  "actions": [
    {
      // Suggestion-backed: the suggestion id from feedback.json decisions
      // Synthetic (non-suggestion work): "{verb}:{context}" format
      //   commit:dotfiles       — git commit in dotfiles submodule
      //   commit:macos-setup    — git commit in parent repo
      // Add more synthetic ids as needed; the page treats them identically.
      "id": "brew:podman:keep-pin-add-comment",

      // Human-readable one-liner shown in the action list
      "label": "Add pin comment to Brewfile",

      // Mirrors the user's decision from feedback.json, null for synthetic actions
      "decision": "accept",   // "accept" | "reject" | "discuss" | null

      // "pending" — not yet started
      // "running" — actively executing (spinner shown)
      // "done"    — completed successfully
      // "failed"  — execution error (detail lines explain)
      // "skipped" — not executed (decision was reject/discuss/undecided, or
      //             a dependency failed)
      "state": "running",

      "started_at": "2026-07-04T14:52:11Z",  // ISO UTC; null if not started
      "finished_at": null,                    // ISO UTC; null if not finished

      // Written on transition to done/failed/skipped; null while pending/running
      "note": null,    // e.g. "Committed as abc1234 in dotfiles"

      // Last ≤10 lines of relevant output; empty array if none
      // e.g. diff hunk applied, commit hash, error message
      "detail": [],

      // Present only on a "failed" action (or one the user has commented
      // on) — same Turn shape as pending_followups' turns[] above (C.10).
      // This is what backs the failed-action inline retry/debug UI (C.6):
      // a failed action isn't just a dead end, it's a thread the user can
      // add a debug comment to or retry from, using the exact same
      // mechanism a followup uses. Absent/empty for actions nothing has
      // been added to yet.
      "thread": []
    }
    // ... more actions in execution order
  ],

  // Threads raised live, mid-apply — by investigating a tool_comments entry
  // or a discuss comment (user-initiated), or by the session itself hitting
  // something during apply that needs an explicit decision, not just a
  // plain success/fail outcome (agent-initiated — SKILL.md/design.md D).
  // Each is a conversation, not a one-shot decision: turns accumulate, the
  // newest is what's "active", and a thread is never permanently closed to
  // new input — even one already resolved can get another out-of-turn turn
  // later (e.g. an unrelated Gatekeeper popup noticed after an upgrade the
  // followup was originally about had already been applied).
  "pending_followups": [
    {
      "id": "mise:node:from-comment-hold-for-nvmrc",
      "kind": "edit",
      "tool_id": "mise:node",              // originating tool
      "tool_name": "node",                 // display name for the origin line
      "origin": "user_comment",            // "user_comment" | "agent_initiated"
      // Purely descriptive of the latest concluded decision — never a hard
      // "closed" gate. "pending": awaiting a decision on the latest turn.
      // "applied"/"rejected": last decision was acted on, but the thread
      // still accepts new turns (see turns[] below).
      "resolution": "pending",            // "pending" | "applied" | "rejected"
      "turns": [
        {
          "turn": 1,
          "author": "user",                 // "user" | "agent"
          "at": "2026-07-04T14:53:00Z",
          "decision": "discuss",            // "accept" | "reject" | "discuss" | null (agent turns: null)
          "comment": "Hold off until the project upgrades its .nvmrc",
          "action_taken": null               // agent turns: what it did, e.g. "Applied edit, committed abc1234"
        }
        // ... more turns, newest last
      ]
      // ...rest of the fields are identical to a normal Suggestion object
      // (title, rationale, target_files/diff_preview or command,
      // auto_runnable/needs_sudo/manual_reason, motivating_link) — a
      // followup is a suggestion with a conversation attached, not a
      // different kind of thing.
    }
  ],

  // Free-form markdown text written by the session at done time — pure
  // narrative summary now (applied edits with commit hashes, failed
  // actions with remediation hints for the ones that stay unresolved,
  // rejected/undecided items). It does NOT need to mention followup counts
  // or status — those live entirely in pending_followups/the merged status
  // header (C.4) now, computed live on every poll, so recap can never go
  // stale relative to them the way a frozen "N pending" sentence used to.
  // Empty string until the session writes it (phase "done"). Rendered as
  // parsed markdown (C.10), not preformatted text.
  "recap": "",

  // Each element is the markdown text of one changelog entry appended to
  // ${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md
  // during this session. Empty list until entries are written.
  // Example element: "## 2026-07-04\n### brew: gh 2.48.0 → 2.52.0\n..."
  "changelog_entries": [],

  // Written at done time. Counts are over user decisions (from feedback),
  // not over action execution states. "failed" counts execution failures
  // among accepted suggestions (not user rejections).
  "summary": {
    "applied":   3,   // accepted + execution succeeded
    "rejected":  2,   // user rejected
    "discussed": 1,   // user selected discuss (not applied, raised in session)
    "undecided": 0,   // no decision made (skipped)
    "failed":    0    // accepted but execution failed
  },

  // Terminal signal. The page enables the Finish button when true.
  // Set to true only in the same write that sets phase "done".
  "done": false
}
```

### Action ordering

The `actions` array is in execution order: accepted suggestions first (in the
order they appear in the feedback), then synthetic commit actions, then
rejected/undecided items (state "skipped"). The page renders them in array
order.

### State transitions

```
pending → running → done
                 → failed
         skipped  (set directly from pending, no running state)
```

A single session write covers one transition at a time (e.g. pending→running,
then running→done in the next write). Never write both in one atomic op; the
page needs to observe "running" to show the spinner.

---

## B. Server Changes

### `/feedback` — change: remove shutdown, add duplicate guard

**Remove**: the `threading.Thread(target=_shutdown).start()` call.

**Add**: duplicate-submit guard at the top of the `do_POST` handler for
`/feedback`, before reading the request body:

```python
feedback_path = os.path.join(sess_dir, "feedback.json")
if os.path.exists(feedback_path):
    # Already submitted (e.g. second tab, page refresh with Submit clicked again)
    self._error(409, "already_submitted")
    return
```

All other `/feedback` behavior unchanged: JSON validation, report_id check,
unknown-id check, atomic write, 200 response.

### `GET .../status` — new endpoint

Added to `do_GET`, checked before the catch-all HTML serve:

```python
if self._route().endswith("/status"):
    status_path = os.path.join(sess_dir, "status.json")
    try:
        with open(status_path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        self.send_error(404, "status.json not found")
        return
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Cache-Control", "no-store")
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    self.wfile.write(data)
    return
```

No in-memory caching. Reads the file on every request so the page always gets
the latest atomic write.

### `POST .../shutdown` — new endpoint

Added to `do_POST`, checked before the `/feedback` branch:

```python
if self._route().endswith("/shutdown"):
    # Idempotent: if shutdown already scheduled, still return 200.
    body = json.dumps({"status": "shutdown_scheduled"}).encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

    def _shutdown():
        time.sleep(1)
        for httpd in server_holder:
            httpd.shutdown()

    threading.Thread(target=_shutdown, daemon=True).start()
    return
```

The response flushes before the 1 s delay fires. Subsequent POST /shutdown
calls while the server is shutting down may get an ECONNRESET (quiet-disconnect
handler covers it) or another 200 — both are fine.

### Routing order in `do_GET`

```
1. endswith("/health")   → {"status":"ok"}
2. endswith("/status")   → status.json or 404
3. catch-all             → index.html
```

### Routing order in `do_POST`

```
1. endswith("/shutdown") → schedule shutdown, 200
2. endswith("/feedback") → validate + write feedback.json, 200 (or 400/409)
3. else                  → 404
```

### Unchanged behaviors

- Multi-bind (`--bind ADDR`) support: all server instances share `server_holder`;
  shutdown triggered on any interface tears down all.
- Quiet-disconnect suppression (ENOTCONN/ECONNRESET/EPIPE).
- Port-walking (8742–8751).
- `allow_reuse_address = True`, daemon threads.

---

## C. Page Design

### C.1 Initial-load state detection

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

### C.2 Submit handler change

Replace the success-overlay code with:

```js
if (resp.ok) {
    transitionToResults(null);   // null = no initial data yet
    startPolling();
} else { /* existing error path */ }
```

### C.3 View transition (`transitionToResults`)

1. Leave `#filter-bar` visible and interactive — it's a read-only navigation
   aid over the now-frozen Report tab, not a decision control, so a filter
   applied before Submit can still be cleared/inspected afterward instead of
   leaving the user stuck looking at a filtered-empty report with no way to
   reset it (superseding an earlier draft of this doc, which said to hide
   it — design.md B.2/B.4 is authoritative here).
2. Replace `#progress-bar-container` contents with the tab strip + results
   status bar (see layout below). Do not remove the element; keep it sticky.
3. Freeze only the decision controls in the report view: `.btn-decision`,
   `.card-comment`, `.tool-note-textarea`, and `#overall-comment` inside
   `#main` get `disabled`; add a CSS class `report-frozen` to `#main` that
   reduces opacity to 0.5. Collapse toggles, the command-chip copy button,
   and the filter/sort controls (outside `#main`) stay interactive.
4. Create `#results-panel` div (see layout), insert it into the DOM after
   `#progress-bar-container` and before `#main`.
5. Show `#results-panel`, hide `#main` (the Report tab will toggle these).

`transitionToResults(initialData)` accepts the first status blob or null. If
non-null, render it immediately before the first poll arrives.

### C.4 Results view layout

Rewritten (superseding the original draft below the wireframes note) per
live feedback: two separate status displays (tab-strip bar +
`#results-status-bar`) merged into one; followups and recap moved above
the action list; Changelog promoted to its own tab (#29) instead of a
Results-panel subsection; recap collapsed by default so the page stays
glanceable as it grows (#23).

```
#progress-bar-container (repurposed — stays sticky):
┌──────────────────────────────────────────────────────────────────┐
│  [Results ●]  [Report]  [Changelog]      Applying… · updated 0s │
└──────────────────────────────────────────────────────────────────┘

#results-panel:
┌──────────────────────────────────────────────────────────────────┐
│  #results-header (unified — always visible, C.6)                │
│  Applying changes… · updated 0s ago                              │
│  [progress bar ===========------------------]  41/72 done       │
│  0 failed · 2 followups: 0 resolved, 2 pending                  │
├──────────────────────────────────────────────────────────────────┤
│  FOLLOWUPS  (hidden until pending_followups non-empty)          │
│  ... thread cards (C.10) ...                                     │
├──────────────────────────────────────────────────────────────────┤
│  ACTIONS                                                         │
│  [ state icon ]  label                         note (if any)    │
│  a "failed" row expands inline to retry/debug controls (C.10)    │
│  ...                                                             │
├──────────────────────────────────────────────────────────────────┤
│  NOTES / RECAP  (collapsed by default — title only until expanded, │
│                  hidden entirely until recap non-empty)          │
├──────────────────────────────────────────────────────────────────┤
│                                    [Finish ↗]  (disabled / enabled) │
└──────────────────────────────────────────────────────────────────┘

#changelog-panel (separate tab, not part of #results-panel — C.6):
┌──────────────────────────────────────────────────────────────────┐
│  Changelog — {date}              (date shown once for the run)  │
│  ### brew: gh 2.48.0 → 2.52.0                    ▸ show          │
│  ### cask: google-chrome 148.0.7778.179 → 150.0.7871.47   ▸ show │
│  ...                                                              │
└──────────────────────────────────────────────────────────────────┘
```

### C.5 ASCII wireframes

#### In-progress state, with pending followups

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

#### Done state, with a failed action expanded for debugging

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

#### Changelog tab

```
╔══════════════════════════════════════════════════════════════════╗
║  [Results]  [Report]  [Changelog ●]        Done · 2026-07-04    ║
╠══════════════════════════════════════════════════════════════════╣
║  Changelog — 2026-07-04                                          ║
║  ### brew: gh 2.48.0 → 2.52.0                          ▸ show   ║
╚══════════════════════════════════════════════════════════════════╝
```

#### Closing splash (full-page overlay, after Finish clicked)

```
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║                         ✓                                        ║
║                   Session complete                               ║
║           This window can be closed.                             ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

### C.6 Element detail

**Tab strip** (replaces progress-bar-container contents):
- Three buttons: "Results", "Report", "Changelog" (#29 — promoted from a
  Results-panel subsection to a sibling tab, since it's audit-trail content
  someone browses independently of live apply progress). Active tab uses
  `--cyan` underline or filled background. Clicking one shows its panel,
  hides the other two (`#main`, `#results-panel`, `#changelog-panel`).
- Right-aligned status text: phase label + "updated N s ago" computed from
  `written_at`. Turn red if `written_at` is >120 s ago and `done` is false
  (stale session hint).

**Unified status header** (`#results-header`, replaces the separate tab
status text + `#results-status-bar` — #23, merging what used to be two
displays into one, always visible at the top of `#results-panel`):
- Line 1 — phase + staleness, same text/logic the tab strip used to show
  alone: `"Applying changes"` / `"In discussion"` / `"Complete"`, plus
  `"· updated N s ago"`, plus `"· session may have stopped"` in yellow when
  stale (>120s, `done` false).
- Line 2 — a progress bar (`#results-progress-track`/`#results-progress-fill`,
  same visual language as the Report tab's sticky progress bar, B.2) whose
  fill width is `doneCount / totalActions` and which dynamically absorbs
  the row's leftover horizontal space (`flex: 1`) rather than a fixed
  width; a done-count label (`"41 / 72 done"`) sits alongside it.
- Line 3 — failure count (`"K failed"`, red when K > 0) and a followup
  count split into resolved/pending (`"J followups: R resolved, P pending"`,
  pending > 0 in yellow) — **computed live from `pending_followups` on
  every poll** (count `resolution !== "pending"` vs `=== "pending"`), never
  baked into recap prose. This is the fix for a real staleness bug: recap
  text used to say "2 pending followups still need your decision" and that
  sentence went stale the instant the user decided one, because it was
  frozen prose written once. The live count here can't go stale the same
  way since it's recomputed from the actual array shape every render.

**Followups section** (`#followups-section`, moved above the action list —
#23): hidden until `pending_followups` is non-empty. One thread card per
entry (C.10) — multi-turn (#27), not a one-shot decision. Rendered before
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
inline retry/debug thread instead of pointing at recap for remediation**
(#28) — same Turn-based mechanism as a followup (C.10), scoped to that
action's own `thread` field (A). Recap no longer needs to hold any
per-failure remediation text; it's pure narrative summary now.

**Recap section** (`#results-recap`): hidden until `recap` is non-empty.
**Collapsed by default** (#23) — only a section label + one-line "Notes /
Recap ▸ show" control is visible; expanding reveals the full text, rendered
as parsed markdown (#30, C.10) rather than a raw `<pre>` dump. This keeps
the top of the panel glanceable even as recap grows long; the unified
header above already covers the at-a-glance status, so recap's only job
now is narrative detail for whoever wants to read it.

**Changelog tab** (`#changelog-panel`, sibling to `#main`/`#results-panel`,
not nested inside the Results panel — #29): hidden until
`changelog_entries` is non-empty (tab itself can still be clicked; shows an
empty state rather than being disabled). One shared date heading for the
whole panel (all entries in one run share the same date, per changelog.md's
own `## {date}` convention — C.2 step 8 unchanged, this is display-only).
Each entry renders **heading-only by default** — just its
`### {source}: {name} {old} → {new}` line — with a "▸ show" control
revealing the summary/detail text below it, parsed as markdown (#30).

**Finish button** (`#finish-btn`):
- Disabled (grey, `cursor:not-allowed`) while `done` is false.
- Enabled (cyan) when `done` is true.
- If any action is still in state `running` when Finish is clicked (should not
  happen if `done` is properly gated, but guard anyway): show a confirm dialog:
  "Session may still be running. Finish anyway?" — yes → proceed, no → cancel.
- On click: POST to `base + '/shutdown'`. On 200: show closing splash overlay
  (reuse `.overlay-box` structure; text "Session complete — this window can be
  closed"). On network error: show error toast "Shutdown failed — the session
  may have already ended. Safe to close this window."

### C.7 Polling lifecycle

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

**Stale indicator**: `renderResults` compares `written_at` to `Date.now()`. If
delta > 120 s and `done` is false, append to the tab status text:
`" · session may have stopped"` in yellow.

### C.8 `renderResults(data)`

Called on every successful poll and on `transitionToResults(initialData)`.
Idempotent: update existing DOM elements by id rather than re-rendering from
scratch (prevents scroll jumping).

Update:
1. Tab status text (phase, updated-ago).
2. `#results-status-bar` text.
3. Action list: for each action in `data.actions`, find or create the row by
   `data-action-id`, update icon, label, note, detail pre (toggled by click).
   Preserve expanded/collapsed state of detail pres across renders.
4. Recap section: show/hide + update text.
5. Changelog section: show/hide + update entries.
6. Finish button: enable/disable.

### C.9 Solarized Dark and dependency constraints

All new elements use the existing CSS custom properties. No new CDN calls.
Spinner animation: pure CSS `@keyframes spin` on the ⠇ character's parent
span, or cycle through braille frames in JS (`⠇⠏⠋⠙⠸⠴⠦⠧` at 120 ms) — either
is fine.

Subpath-tolerant URL construction (same pattern as existing /feedback):
```js
const base = location.pathname.replace(/\/+$/, '');
// base + '/status', base + '/shutdown', base + '/feedback'
```

### C.10 Turn-based threads (followups + failed-action debug) — #26/#27/#28/#31

A single shared mechanism backs two surfaces: a `pending_followups` entry
(schema A) and a `"failed"` action's `thread` field (schema A). Both are an
array of **Turn** objects:

```jsonc
{
  "turn": 1,
  "author": "user",             // "user" | "agent"
  "at": "2026-07-04T14:53:00Z",
  "decision": "discuss",        // "accept" | "reject" | "discuss" | null — agent turns: null
  "comment": "Hold off until the project upgrades its .nvmrc",
  "action_taken": null          // agent turns only: what it did, e.g. "Applied edit, committed abc1234"
}
```

**Decoupled decision-select from submit** (#26's fix, generalized): clicking
Accept/Reject/Discuss on the active (newest, still-open) turn only sets a
local pending decision — same as a normal Report-tab card (design.md B.2:
"Clicking an active decision button toggles back to undecided"). A
separate **Send** button submits the decision + whatever's in the comment
box as a new turn. This was the literal bug report this run: clicking
Discuss fired the POST immediately, with whatever the (empty) comment box
held at click time, leaving no chance to type first.

**Multi-turn, never permanently closed** (#27): a thread's history renders
oldest-to-newest, each past turn shown as a read-only line (author, decision
if any, comment/action_taken); the newest turn is the only one with live
controls. Submitting a new turn appends rather than overwrites — even a
thread whose `resolution` is already `"applied"`/`"rejected"` still shows
an open comment box for an out-of-turn addition (e.g. a Gatekeeper
quarantine popup noticed after the original followup's suggestion was
already applied). Submitting with an empty comment is valid — it means "go
investigate/retry, no extra direction needed," not "do nothing."

**Failed-action inline retry/debug** (#28): a `"failed"` action row expands
(same click-to-expand as its `detail` pre today) to show the manual command,
a **Retry** button (re-issues the same command — useful once e.g. askpass
is fixed, or for a transient failure likely to just succeed on a second
try), and the same comment-box-plus-Send turn UI as a followup, writing
into that action's own `thread` array rather than a separate followup id.
This is the same component as C.10's followup thread, parameterized by
which array it reads/writes — not two parallel systems.

**Agent-initiated threads** (#31): a `pending_followups` entry's `origin`
field (schema A) distinguishes `"user_comment"` (investigating a
`tool_comments`/`discuss` entry, as before) from `"agent_initiated"` — the
session creating one on its own after hitting something during apply that
needs an explicit decision (a security-relevant system prompt, an unusual
side effect — see D). Rendering is identical either way; `origin` is
metadata, not a different card shape.

### C.11 Markdown rendering (#30)

Recap, changelog entry detail, and turn `comment`/`action_taken` text are
all agent-authored and likely to contain basic markdown (bold, inline code,
links, lists) — that's how the apply-phase agent naturally writes. Replace
the current `<pre>`-as-plain-text treatment with a lightweight markdown
renderer (page JS: escape first via the same discipline as design.md D's
`</script>`-breakout guard, then reinsert only the literal markup the
renderer itself generates — headings, lists, bold, inline code, links — so
no raw HTML from agent text ever reaches the DOM). This is not a
general-purpose markdown library; just enough for what these fields
actually contain.

---

## D. Session Workflow Changes (`SKILL.md`)

### New step sequence

Replace "Serve and wait" and "Apply feedback" with the following (step
numbers below are this extension's own local sequence; see SKILL.md and
design.md C.2 for the actual current numbering — Serve moved earlier, to
step 2, per section E below):

**Serve**

Start the server (unchanged from original). Poll `/health` until 200. Open URL.

**5. Wait for feedback.json**

Block by polling `{session_dir}/feedback.json` existence every 5 s (do NOT
block on server exit — the server no longer shuts down after feedback). Timeout
after 24 h: offer to re-open the page or abandon.

If `feedback.json` already exists when the server starts (e.g. session crashed
and was restarted): validate `report_id`. If it matches, skip the wait and
proceed immediately to step 6. If it mismatches, warn the user and ask whether
to serve fresh (rename the old file) or use the stale one.

**6. Write initial status.json**

Immediately after detecting `feedback.json`, build the initial `status.json`
and write it atomically:
- `phase`: `"applying"`
- `started_at`: now
- `written_at`: now
- `done`: false
- `actions`: one entry per feedback decision, in suggestion order, state
  `"pending"` for accepted/rejected/discussed; plus synthetic commit actions
  at the end (state `"pending"`). Rejected and undecided decisions get
  `"skipped"` immediately — they will never run.
- `recap`, `changelog_entries`, `summary`: empty/zero.

After writing, the page's next poll will get this file (it was 404 before).

**7. Apply accepted suggestions — per-action status updates**

For each accepted suggestion in order:
1. Write status.json: set action state `"running"`, `started_at: now`.
2. Apply the edit (dotfiles submodule flow or direct file edit).
3. Write status.json: set action state `"done"` or `"failed"`, `finished_at:
   now`, `note: <one-line outcome>`, `detail: [<last ≤10 lines of output>]`.

Commit actions (`commit:dotfiles`, `commit:macos-setup`) follow the same
pattern: running → done/failed.

**Create a followup, don't just mention it in chat, when something during
apply needs the user's explicit decision beyond plain success/fail** (#31).
Concrete trigger from a real run: an already-accepted cask upgrade
completed, but its installer's Gatekeeper quarantine popped a native
malware warning on the user's screen — an unexpected side effect unrelated
to the action's own done/failed outcome. Add a `pending_followups` entry
(`origin: "agent_initiated"`, results-view-design.md A/C.10) the same way a
`tool_comments` investigation would, rather than only raising it in
conversation — the Results view should be the one place someone checks for
"things needing my decision," not split across the page and the chat
transcript. Poll `followup_turns.json` (B) throughout this step for new
user turns on any open thread (followup or failed-action) and act on them
promptly — accept → apply now, reject → mark resolved, discuss/comment →
append an agent turn with the answer or a clarifying question. A thread
being `resolution: "applied"`/`"rejected"` doesn't mean stop watching it —
a later out-of-turn turn can still arrive (C.10) and needs the same
promptness.

**8. Append to changelog and write changelog entries**

For each runtime upgrade that was applied (mise runtimes, standalone CLIs),
append one entry to `${XDG_STATE_HOME:-~/.local/state}/tool-update-review/changelog.md`:

```markdown
## {YYYY-MM-DD}
### {source}: {name} {old_version} → {new_version}
{one to three sentence summary of what changed — pulled from headliners}
```

Create the file and parent directory if they do not exist (`mkdir -p`).

Collect all appended entries as strings; include them in the terminal
status.json write (step 9).

**9. Write terminal status.json**

After all actions complete and changelog is updated:
- `phase`: `"done"` (or `"discussing"` if discuss items remain — then
  transition to `"done"` after the discuss pass).
- `done`: `true`.
- `recap`: write the end-of-work summary (applied edits with commit hashes,
  failed actions with remediation hints, discuss items with user comments,
  rejected/undecided items).
- `changelog_entries`: all strings from step 8.
- `summary`: compute from action outcomes.
- `written_at`: now.

**10. Surface discuss items in conversation**

After writing terminal status, raise each `discuss` item in the session
conversation (tool name, suggestion title, user's comment). Do not apply
until the user confirms. This step is session-side only — no status.json
update needed (recap already mentions these).

**11. Wait for Finish (POST /shutdown)**

Block by polling for server exit (the shutdown endpoint triggers
`server.shutdown()` from a thread, which causes `serve_forever()` to return,
which causes the daemon threads to join and `main()` to exit). Use
`subprocess.wait(timeout=86400)` (24 h). Alternatively poll for the process
exit every 5 s within the timeout.

If timeout expires: print a message to the terminal ("Review session timed
out — you can still click Finish in the browser, or close it.") and proceed
to teardown anyway.

**12. Teardown**

Kill the tailscale serve proxy (if started). Clean up any temp files.
Session complete.

### Failure modes

| Failure | Handling |
|---|---|
| Session crashes mid-apply (stale `running` state) | Page detects `written_at` >120 s old with `done: false`; shows "session may have stopped" hint. Restart session from step 5 — it reads existing `feedback.json` and resumes. Already-completed actions show `done`/`failed` in `actions`; session skips them (check state before re-running). |
| User never clicks Finish | 24-h timeout at step 11; teardown proxy; server exits; session reports done in terminal. The browser tab becomes inert (poll errors; stale indicator). |
| `feedback.json` exists at serve time, report_id matches | Skip wait (step 5); proceed to step 6. Print "Resuming from existing feedback." |
| `feedback.json` exists, report_id mismatch | Warn; ask user: "Use stale feedback from a different session?" Default: rename stale file and re-serve fresh. |
| Apply edit fails (e.g. merge conflict) | Action state → `"failed"` with error in `detail`. Continue to next action. Note in recap. |
| POST /feedback returns 409 from a second tab | Page shows error toast "Feedback already submitted". Expected; harmless — the first submit stands. |
| Server gone when Finish is clicked | `fetch('/shutdown')` network error; page shows "Safe to close this window" toast. |
| All ports busy | Unchanged from original design (fail with lsof hint). |

---

## E. Pre-Report Progress View

A large candidate list makes research take minutes, and — before this
section — nothing was served until collect+research+assemble+render all
finished, so the browser tab sat blank the whole time. Serve now starts
right after Collect (SKILL.md step 2, design.md C.2 step 2), before
research even begins, and shows a live "gathering info" state instead.

### E.1 `research-status.json` schema

```jsonc
{
  "phase": "collecting",   // "collecting" | "researching" | "assembling" | "ready"
  "started_at": "2026-07-06T08:58:07Z",
  "written_at": "2026-07-06T08:59:41Z",

  // Populated once tiering (SKILL.md step 3) has grouped the candidates;
  // empty during "collecting". One entry per research subagent (both
  // individual-focus and batched-by-category groups).
  "groups": [
    {
      "id": "01-podman",             // matches research/{id}.json's filename
      "label": "podman",             // display label — tool name, or a short
                                      // category label for a batch group
      "state": "done",               // "pending" | "running" | "done" | "failed"
      "tool_ids": ["brew:podman"]     // every group has ≥1; batches have several
    }
  ]
}
```

Same write pattern as `status.json` (A): `.tmp` + `os.replace()`. `phase`
transitions `collecting → researching → assembling → ready`; `ready` is
written only after `render.py` has actually produced `index.html` (SKILL.md
step 4) — it's the exact signal the loading page's poll is waiting for.

### E.2 Server changes

`GET .../research-status` — added to `do_GET`, same shape as `GET
.../status` (B): reads `research-status.json`, 404 if absent, no caching.

`GET /` (root/catch-all) — if `index.html` doesn't exist yet, serve an
embedded loading page (`LOADING_PAGE_HTML` constant in `server.py`) instead
of 404ing. This also means `server.py`'s startup can no longer treat a
missing `report.json` as fatal: `report_id`/known-suggestion-ids move from
being loaded once at startup to a `report_meta()` helper that lazily
re-reads `report.json` on first actual need (i.e. when `/feedback` is
POSTed — by which point the real report has always rendered, since Submit
isn't reachable before then). `POST /feedback` returns 503 in the
(practically unreachable) case where `report.json` still doesn't exist at
that point, rather than crashing.

### E.3 Loading page

Self-contained (no CDN, inline CSS/JS — same constraint as the main report,
B.1), embedded as a Python string in `server.py` rather than a separate
templated file, since it needs no report data at all. Solarized Dark,
minimal: title, phase label, a thin progress bar (`done` groups / total
groups), and a per-group list with the same state-icon vocabulary as the
Results view's action list (○ pending, spinner `running`, ✓ done, ✗ failed —
C.6). Polls `GET {base}/research-status` on the same growing-backoff
pattern as the Results view's `/status` poll (C.7); on `phase: "ready"`,
calls `location.reload()` — the next `GET /` finds `index.html` and serves
the real report.

### E.4 Session responsibilities (SKILL.md)

- **Step 2 (Serve)**: write an initial `research-status.json` with `phase:
  "collecting"` and empty `groups` before starting the server, so the very
  first poll has something to render.
- **Step 3 (Research)**: once tiering has grouped the candidates, write
  `groups` with every group `"pending"`, `phase: "researching"`. As each
  subagent is spawned/finishes, update that group's `state` (`pending →
  running → done`/`failed`) — same one-transition-per-write discipline as
  `status.json` (A, "State transitions").
- **Step 4 (Assemble + Render)**: `phase: "assembling"` while
  `assemble.py`/`render.py` run, then `phase: "ready"` immediately after
  `index.html` is written — not before, since `ready` is the loading page's
  cue to reload into a page that must actually exist by then.
