# Server and Session (`server.py`, Serve/Wait/Teardown)

Reference for `scripts/server.py`'s endpoint contract and the
orchestrator-side mechanics of starting it, waiting on it, and tearing it
down. Read this when maintaining `server.py`, or when running steps 2, 5,
10, or 11.

## Table of Contents

- [Server Endpoints](#server-endpoints)
- [Pre-Report Status (Loading Page Mechanics)](#pre-report-status-loading-page-mechanics)
- [Starting the Server](#starting-the-server)
- [Waiting for Submission](#waiting-for-submission)
- [Waiting for Finish](#waiting-for-finish)
- [Teardown](#teardown)
- [Operational Notes](#operational-notes)
- [Failure Modes](#failure-modes)

## Server Endpoints

`server.py {session_dir} {port}` — stdlib only (`http.server`,
`socketserver`, `json`, `os`, `threading`). Bind `127.0.0.1` only by
default; each `--bind ADDR` adds a listener on the same port (e.g. a
tailscale IP for remote review) — only add interfaces the user trusts, the
report holds config details. Try port 8742, on `EADDRINUSE` increment up to
8751, then fail with an `lsof -i :8742` hint. `allow_reuse_address = True`,
daemon threads. Multi-bind instances share one `server_holder`; a shutdown
triggered on any interface tears down all of them.

Current endpoint contract (post pre-report/results-view extensions):

| Method | Path (suffix-matched) | Behavior |
|---|---|---|
| GET | `/health` | 200 `{"status":"ok"}` — session polls this before opening the browser. |
| GET | `/status` | `status.json` verbatim, 404 if absent. No in-memory caching — reads the file fresh on every request so the page always gets the latest atomic write. `Cache-Control: no-store`. |
| GET | `/research-status` | `research-status.json` verbatim, 404 if absent. Same no-cache treatment as `/status`. |
| GET | `/` (and anything else — `/updates`, favicon probes) | `{session_dir}/index.html` read at request time if it exists, else the embedded `LOADING_PAGE_HTML` fallback (see below) — the server starts before research/render finish, so a missing `index.html` is the normal state for the first stretch of a session. |
| POST | `/followup` | Appends a turn to a thread (a `pending_followups` entry or a `"failed"` action's own `thread`) into `followup_turns.json`: `{"thread_id", "decision", "comment"}`, `decision` one of `"accept"\|"reject"\|"discuss"`. **No duplicate-decision guard** — threads are multi-turn by design, so a second POST to the same `thread_id` is expected and valid, not an error. Turn shape and thread semantics: `references/rendering-results.md` §Turn-Based Threads; schema: `references/schemas.md`. |
| POST | `/shutdown` | Schedules `server.shutdown()` on a background daemon thread after a ~1s delay (so the response flushes before the process starts tearing down); idempotent — still 200 if shutdown is already scheduled. |
| POST | `/feedback` | Validates JSON + `report_id` + known suggestion ids (400 on any mismatch, 503 if `report.json` doesn't exist yet — practically unreachable, see lazy-load below); 409 if `feedback.json` already exists (one submission per session, guarded by a lock around the check-then-write so two concurrent POSTs can't both pass the check before either writes); writes `feedback.json.tmp` then `os.replace()`. **Does not shut down the server** — it stays up so the page can keep polling `/status` through the whole apply pass. Only `/shutdown` actually stops it. |
| — | anything else | 404. |

Routing order matters because paths are matched by *suffix*
(`self.path.split("?", 1)[0].rstrip("/")`), so the server works both served
directly and behind a proxy mount (e.g. `tailscale serve --set-path
/updates`) whether or not the proxy strips the mount prefix:

```
GET:  /health → /status → /research-status → catch-all (index.html or loading page)
POST: /followup → /shutdown → /feedback → else 404
```

Unchanged across every extension to this file: quiet-disconnect
suppression (`ENOTCONN`/`ECONNRESET`/`EPIPE` swallowed in
`handle_error`), port-walking, `allow_reuse_address`, daemon threads.

## Pre-Report Status (Loading Page Mechanics)

The server tolerates a missing `report.json` at startup — the session now
starts it right after Collect (step 1), before research/assemble/render
have produced anything, so there's genuine minutes-long latency with
nothing to show otherwise. Two mechanics make this safe:

- **`report_meta()` lazy load.** `report_id` and the known-suggestion-id
  set used to validate `POST /feedback` move from being loaded once at
  server startup to a `report_meta()` helper that lazily re-reads
  `report.json` on first actual need (i.e. when `/feedback` is POSTed) —
  by which point the real report has always rendered, since Submit isn't
  reachable before then. `POST /feedback` returns 503 in the (practically
  unreachable) case where `report.json` still doesn't exist at that point,
  rather than crashing the server.
- **`GET /` fallback.** If `index.html` doesn't exist yet, `GET /` serves
  an embedded loading page (`LOADING_PAGE_HTML`, a Python string constant
  in `server.py` — no separate template file, since it needs no report
  data) instead of 404ing. It polls `GET {base}/research-status` and calls
  `location.reload()` once `phase` reaches `"ready"`; the next `GET /` then
  finds `index.html` and serves the real report.

`research-status.json` itself is written by the orchestrator, not the
server (schema: `references/schemas.md` §research-status.json). The
loading page's visual design (icons, polling backoff, Solarized styling)
is specified in `references/rendering-results.md` §Loading Page — it's
described there alongside the rest of the page-design docs even though the
markup physically lives in this script.

## Starting the Server

Research over a large candidate list takes minutes, and there's nothing to
show for it until assemble+render finish — start the server right after
collect instead of waiting for the report to exist, so the browser tab can
open immediately and show a live "gathering info" progress view instead of
sitting on a blank tab (mechanics above).

Start the server loopback-only (extra `--bind` listeners only if the user
asks):

```sh
python3 {skill_dir}/scripts/server.py {session_dir} &
```

Poll `GET /health` until 200 (≤5 s). Then pick URLs by session type —
decide at runtime, never hardcode hostnames (there are multiple server
hosts):

- **Remote session** (`SSH_CONNECTION` or `SSH_TTY` set): also start a
  **foreground** `tailscale serve --set-path /updates {port}` as a
  background task (never `--bg`; foreground config dies with the
  session). Print both URLs: `https://$(tailscale status --json | jq -r
  '.Self.DNSName' | sed 's/\.$//')/updates` and the loopback URL.
- **Local session**: loopback URL only, and `open` it. Tailnet serving
  only on request.

Write an initial `research-status.json` with `phase: "collecting"` and an
empty `groups` array before moving on — the loading page's first poll
should find *something*, even if it's just "gathering update candidates"
with no group list yet. As research tiering (step 3) groups the
candidates, and as assemble/render (step 4) run, that file's `phase`
progresses `collecting → researching → assembling → ready`; `ready` is
written only immediately after `index.html` actually exists, since that's
the exact signal the loading page's poll is waiting for to trigger its
reload into the real report.

## Waiting for Submission

**Implement this wait as a single backgrounded shell command, not a
recurring model wakeup/poll loop.** A wakeup-loop re-invokes the model on a
timer, and each invocation past the ~5-minute prompt-cache TTL reprocesses
the full conversation at full (uncached) input price — expensive and
pointless for a review that can sit untouched for a long time while the
user reads in the browser. Instead, run something like:

```sh
until [ -f "{session_dir}/feedback.json" ]; do sleep 5; done
```

as a backgrounded tool call and let the harness notify the session exactly
once, when the file actually appears — zero interim reprocessing regardless
of how long the user takes. Do **not** block on server exit — the server
stays up after feedback (see `/feedback`'s contract above). Timeout after
24 hours: offer to re-open the page or abandon.

If `feedback.json` already exists when the server starts (e.g. session
crashed and restarted): validate `report_id`. Match → skip the wait, print
"Resuming from existing feedback," proceed straight to writing the initial
`status.json`. Mismatch → warn and ask: re-serve fresh (rename the stale
file) or use the stale feedback.

## Waiting for Finish

Same principle as Waiting for Submission — **backgrounded shell command,
not a wakeup loop.** The page's Finish button posts `/shutdown`, which
triggers a 1s-delayed `server.shutdown()`; `serve_forever()` then returns,
the daemon threads join, and the server process exits — its `/health`
endpoint stops responding. Run something like:

```sh
until ! curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:{port}/health | grep -q 200; do sleep 5; done
```

backgrounded, and let the harness notify once when it exits — this can be
a long, open-ended wait (the user may take a while reviewing results), and
a periodic wakeup here pays the same avoidable full-context reprocessing
cost as the submission wait. Cap at 24h: if the backgrounded check hasn't
fired by then, print "Review session timed out — you can still click
Finish in the browser, or close it." and proceed to teardown anyway.

## Teardown

Kill the tailscale serve proxy (if started). Clean up any temp files.
Session complete.

## Operational Notes

- The report page is fully offline (no CDN). The tailscale serve proxy
  needs no firewall approval and serves tailnet-only HTTPS; the `/updates`
  path mount leaves the root hostname free for other serves. The page and
  server are subpath-tolerant (relative feedback URL, suffix routing), so
  they work at `/` and behind the mount alike. Never bind `0.0.0.0` or LAN
  interfaces without asking — the report exposes config details.
- Restarting the server mid-review is safe: page state lives in the open
  tab; same port + `report_id` keep Submit working.
- macOS quirk: a machine can't reach its *own* tailscale IP (utun
  hairpin) — verify locally against 127.0.0.1, remotely via tailscale. This
  split is exactly why multi-bind exists (loopback bind for local checks,
  a separate tailscale-address bind for remote).

## Failure Modes

Consolidated across both the original server design and the results-view
extension — this is the single authoritative failure-modes table for the
server/session layer; `references/apply.md` and other docs cross-link here
rather than repeating rows.

| Failure | Handling |
|---|---|
| All ports busy | Fail with `lsof -i :{port}` hint. |
| Tab closed before Submit | Server stays up; 24h timeout on the submission wait, offer re-open (state lives in the page until the tab closes; re-open re-renders fresh). |
| Server crash before/during the submission wait | Check for a valid `feedback.json` (partial submit may have landed anyway); else offer re-serve. |
| `feedback.json` exists at serve time, `report_id` matches | Skip the submission wait, proceed straight to writing the initial `status.json`. Print "Resuming from existing feedback." |
| `feedback.json` exists at serve time, `report_id` mismatches | Warn; ask the user: "Use stale feedback from a different session?" Default: rename the stale file and re-serve fresh. Confirm before use either way — never silently accept a mismatched file. |
| One tool's research fails/times out | `research_error` set and shown; no suggestions for that tool, it's still listed with versions only. |
| `POST /feedback` with unknown suggestion id(s) | 400; page shows an error toast. |
| `POST /feedback` returns 409 from a second tab | Expected, not a bug — a double-click Submit, a page refresh with Submit re-clicked, or a genuine second tab. Page shows "Feedback already submitted" toast; the first submit stands. |
| `kind: "upgrade"` accepted but the user never runs the printed command | Polling for the version to land caps at ~20 min; the action stays `"running"` with a reminder note rather than blocking the rest of the session. Finish remains available; it can complete later. |
| Session crashes mid-apply (stale `"running"` action) | Page detects `written_at` >120s old with `done: false`; shows a "session may have stopped" hint. Restart the session from the submission-wait step — it reads the existing `feedback.json` and resumes. Already-completed actions show `done`/`failed` in `actions`; the resumed session checks each action's state before re-running it. |
| User never clicks Finish | 24h timeout on the Finish wait; teardown the tailscale proxy anyway; server exits; session reports done in the terminal. The browser tab goes inert (poll errors, stale indicator). |
| Apply-time edit fails (e.g. merge conflict) | Action state → `"failed"` with the error in `detail`. Continue to the next action — one failed action never blocks the rest of the apply pass. Note it in the recap. |
| Server gone when Finish is clicked | `fetch('/shutdown')` network error; page shows a "Safe to close this window" toast. |

---

Related: `references/collection.md` (Serve starts right after Collect
finishes, before research begins), `references/schemas.md`
(`status.json`/`research-status.json`/`feedback.json` shapes this server
reads and writes), `references/rendering-results.md` (the Results view and
loading page that poll `/status`/`/research-status` and post to
`/followup`/`/shutdown`), `references/apply.md` (the orchestrator-side
`write_status.py` calls and heartbeat loop that keep `status.json` current
while this server serves it read-only).
