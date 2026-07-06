#!/usr/bin/env python3
"""
server.py — local review server for tool-update-review sessions.
Usage: server.py <session_dir> [port] [--bind ADDR]

Always listens on 127.0.0.1; each --bind ADDR adds a listener on the
same port (e.g. the tailscale IP from `tailscale ip -4` for remote
review). The report holds config details, so only add interfaces the
user trusts — a tailnet qualifies, a shared LAN may not. The server
stays up after POST /feedback and shuts down only when POST /shutdown
is received (or the 24h session fallback triggers). POST /followup appends
a turn (results-view-design.md C.10) to a thread — a pending_followups
entry or a failed action's own debug thread — into followup_turns.json.
Threads are multi-turn by design: unlike /feedback, there is no
duplicate-submit guard here; a second POST to the same thread_id is
expected, not an error.

Note: on macOS a browser/curl on this same machine may fail to reach
the machine's own tailscale IP (utun hairpin) — locals use 127.0.0.1,
remotes use the tailscale address; that split is why multi-bind exists.
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone


def write_json_atomic(path: str, obj) -> None:
	"""Write JSON to `path` via a `.tmp` + os.replace() so a reader (the page's
	poll, or this same server on a later request) never observes a partial
	file — shared by every write-then-rename spot in this module."""
	tmp_path = path + ".tmp"
	with open(tmp_path, "w", encoding="utf-8") as fh:
		json.dump(obj, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")
	os.replace(tmp_path, path)


# Served at GET / whenever index.html hasn't been written yet — the session
# now starts the server right after collect, before research/assemble/render
# (results-view-design.md E), so there's real minutes-long latency with
# nothing to show otherwise. Polls research-status.json (schema: E.1) and
# reloads once phase reaches "ready", at which point index.html exists and
# this fallback stops being served. Self-contained on purpose, same as the
# main report page (no CDN, inline CSS/JS) — it's the first thing a user
# sees and must render offline too.
LOADING_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tool Update Review — gathering info</title>
<style>
	:root {
		--base03: #002b36; --base02: #073642; --base01: #586e75;
		--base00: #657b83; --base0: #839496; --base1: #93a1a1;
		--base2: #eee8d5; --cyan: #2aa198; --red: #dc322f; --yellow: #b58900;
	}
	* { box-sizing: border-box; margin: 0; padding: 0; }
	body {
		background: var(--base03); color: var(--base0);
		font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
			"Helvetica Neue", Arial, sans-serif;
		font-size: 14px; line-height: 1.6;
		display: flex; align-items: center; justify-content: center;
		min-height: 100vh; padding: 24px;
	}
	#box { max-width: 520px; width: 100%; }
	h1 { color: var(--base2); font-size: 18px; margin-bottom: 6px; }
	#phase-label { color: var(--base1); font-size: 13px; margin-bottom: 18px; }
	#phase-label.warn { color: var(--yellow); }
	.group-row {
		display: flex; align-items: center; gap: 8px;
		font-size: 12px; padding: 3px 0; color: var(--base00);
	}
	.group-row .icon { width: 14px; text-align: center; flex-shrink: 0; }
	.group-row.done .icon { color: var(--cyan); }
	.group-row.running .icon { color: var(--cyan); animation: spin 1s linear infinite; }
	.group-row.failed .icon { color: var(--red); }
	@keyframes spin { to { transform: rotate(360deg); } }
	#track { height: 4px; background: var(--base02); border-radius: 2px; margin: 14px 0; overflow: hidden; }
	#fill { height: 100%; background: var(--cyan); width: 0%; transition: width 0.3s ease; }
	#count { font-size: 12px; color: var(--base01); }
</style>
</head>
<body>
<div id="box">
	<h1>Tool Update Review</h1>
	<div id="phase-label">Gathering update candidates…</div>
	<div id="track"><div id="fill"></div></div>
	<div id="count"></div>
	<div id="groups"></div>
</div>
<script>
const PHASE_TEXT = {
	collecting: "Gathering update candidates…",
	researching: "Researching changelogs…",
	assembling: "Assembling report…",
	ready: "Ready — loading report…",
};
let backoff = 2000;
const base = location.pathname.replace(/\\/+$/, '');
async function poll() {
	try {
		const r = await fetch(base + '/research-status', { cache: 'no-store' });
		if (r.ok) {
			const data = await r.json();
			render(data);
			backoff = 2000;
			if (data.phase === 'ready') { location.reload(); return; }
		} else if (r.status === 404) {
			backoff = Math.min(backoff * 1.5, 8000);
		}
	} catch (_) {
		backoff = Math.min(backoff * 1.5, 10000);
		document.getElementById('phase-label').textContent = 'Connection lost — retrying…';
		document.getElementById('phase-label').className = 'warn';
	}
	setTimeout(poll, backoff);
}
function render(data) {
	document.getElementById('phase-label').textContent = PHASE_TEXT[data.phase] || data.phase;
	document.getElementById('phase-label').className = '';
	const groups = data.groups || [];
	const done = groups.filter(g => g.state === 'done' || g.state === 'failed').length;
	document.getElementById('fill').style.width = groups.length ? (100 * done / groups.length) + '%' : '0%';
	document.getElementById('count').textContent = groups.length ? (done + ' of ' + groups.length + ' research groups done') : '';
	const icons = { pending: '○', running: '⠇', done: '✓', failed: '✗' };
	document.getElementById('groups').innerHTML = groups.map(g =>
		'<div class="group-row ' + g.state + '"><span class="icon">' + (icons[g.state] || '○') + '</span>' + g.label + '</div>'
	).join('');
}
poll();
</script>
</body>
</html>
"""


def main():
	args = sys.argv[1:]
	bind_addrs = ["127.0.0.1"]
	while "--bind" in args:
		idx = args.index("--bind")
		try:
			addr = args[idx + 1]
		except IndexError:
			print("Error: --bind requires an address", file=sys.stderr)
			sys.exit(1)
		if addr not in bind_addrs:
			bind_addrs.append(addr)
		del args[idx:idx + 2]

	if len(args) < 1:
		print("Usage: server.py <session_dir> [port] [--bind ADDR ...]", file=sys.stderr)
		sys.exit(1)

	session_dir = os.path.abspath(args[0])
	start_port = int(args[1]) if len(args) > 1 else 8742

	# ── Load report metadata (tolerant — may not exist yet) ────────────────
	# The session now starts the server right after collect, before research
	# has produced report.json (SKILL.md step 2 moved after Serve) — so a
	# missing report.json at startup is the normal case, not an error. GET /
	# falls back to an embedded loading page until index.html appears; the
	# feedback/report-id/known-ids machinery below re-reads report.json
	# lazily on first actual need, by which point it always exists (Submit
	# is only reachable once the real report has rendered).
	report_path = os.path.join(session_dir, "report.json")

	def load_report():
		try:
			with open(report_path, "r", encoding="utf-8") as fh:
				return json.load(fh)
		except (FileNotFoundError, json.JSONDecodeError):
			return None

	report = load_report()

	def report_meta():
		"""Returns (expected_report_id, known_suggestion_ids) — re-reads
		report.json if it wasn't available at startup."""
		rep = report if report is not None else load_report()
		if rep is None:
			return None, set()
		ids: set[str] = set()
		for tool in rep.get("tools", []):
			for sug in tool.get("suggestions", []):
				sid = sug.get("id")
				if sid:
					ids.add(sid)
		return rep.get("report_id", ""), ids

	# ── Mutable holder so the handler closure can reach every listener ────
	server_holder: list = []

	# ── Request handler ───────────────────────────────────────────────────
	def make_handler(sess_dir: str, meta_fn):
		# Serializes the check-then-write sequences below (duplicate-submit
		# guard + atomic write for /feedback and /followup). ThreadingTCPServer
		# runs each request on its own thread, so without this lock two
		# concurrent POSTs (double-click Submit, a client retry, two
		# followup decisions submitted back-to-back) could both pass an
		# existence/dict-membership check before either had written —
		# silently dropping one submission instead of the 409 the API
		# contract promises.
		write_lock = threading.Lock()

		class Handler(http.server.BaseHTTPRequestHandler):

			def log_message(self, fmt, *args):  # suppress default access log
				pass

			# Paths are matched by suffix so the server works both served
			# directly and behind a proxy mount (e.g. tailscale serve
			# --set-path /updates), whether or not the proxy strips the
			# mount prefix.
			def _route(self):
				return self.path.split("?", 1)[0].rstrip("/")

			# ── GET ───────────────────────────────────────────────────────
			def do_GET(self):
				if self._route().endswith("/health"):
					self._respond(200, json.dumps({"status": "ok"}).encode("utf-8"))
					return

				if self._route().endswith("/status"):
					status_path = os.path.join(sess_dir, "status.json")
					try:
						with open(status_path, "rb") as fh:
							data = fh.read()
					except FileNotFoundError:
						self.send_error(404, "status.json not found")
						return
					self._respond(200, data, extra_headers={"Cache-Control": "no-store"})
					return

				if self._route().endswith("/research-status"):
					rs_path = os.path.join(sess_dir, "research-status.json")
					try:
						with open(rs_path, "rb") as fh:
							data = fh.read()
					except FileNotFoundError:
						self.send_error(404, "research-status.json not found")
						return
					self._respond(200, data, extra_headers={"Cache-Control": "no-store"})
					return

				# Anything else (/, /updates, favicon probes) gets the page —
				# the real report once render.py has written it, or the
				# embedded loading page while research is still in progress.
				index_path = os.path.join(sess_dir, "index.html")
				try:
					with open(index_path, "rb") as fh:
						data = fh.read()
				except FileNotFoundError:
					self._respond(200, LOADING_PAGE_HTML.encode("utf-8"),
									content_type="text/html; charset=utf-8")
					return
				self._respond(200, data, content_type="text/html; charset=utf-8")

			# ── POST ──────────────────────────────────────────────────────
			def do_POST(self):
				if self._route().endswith("/followup"):
					length = int(self.headers.get("Content-Length", 0))
					raw = self.rfile.read(length)
					try:
						payload = json.loads(raw)
					except Exception:
						self._error(400, "Malformed JSON")
						return

					thread_id = payload.get("thread_id")
					decision = payload.get("decision")
					if not thread_id or decision not in ("accept", "reject", "discuss"):
						self._error(400, "Expected thread_id and decision in accept|reject|discuss")
						return

					# Appends a turn (results-view-design.md C.10) — no
					# duplicate-decision guard. Threads are multi-turn by
					# design: a second POST to the same thread_id (an
					# out-of-turn addition after the thread was already
					# resolved, or a race between two submits) is expected,
					# not an error like /feedback's one-shot submission.
					turns_path = os.path.join(sess_dir, "followup_turns.json")
					with write_lock:
						try:
							with open(turns_path, "r", encoding="utf-8") as fh:
								existing = json.load(fh)
						except (FileNotFoundError, json.JSONDecodeError):
							existing = {}

						thread = existing.setdefault(thread_id, [])
						thread.append({
							"turn": len(thread) + 1,
							"author": "user",
							"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
							"decision": decision,
							"comment": payload.get("comment", ""),
							"action_taken": None,
						})
						write_json_atomic(turns_path, existing)

					body = json.dumps({"status": "written", "path": turns_path}).encode("utf-8")
					self._respond(200, body)
					return

				if self._route().endswith("/shutdown"):
					# Idempotent: if shutdown already scheduled, still return 200.
					body = json.dumps({"status": "shutdown_scheduled"}).encode("utf-8")
					self._respond(200, body)

					def _shutdown():
						time.sleep(1)
						for httpd in server_holder:
							httpd.shutdown()

					threading.Thread(target=_shutdown, daemon=True).start()
					return

				if not self._route().endswith("/feedback"):
					self.send_error(404, "Not found")
					return

				# Duplicate-submit guard: 409 if feedback.json already written.
				# This early check is just a fast path to skip parsing the
				# body when it's already obviously too late; the guard that
				# actually matters is the re-check under write_lock below,
				# right before the write.
				feedback_path = os.path.join(sess_dir, "feedback.json")
				if os.path.exists(feedback_path):
					self._error(409, "already_submitted")
					return

				length = int(self.headers.get("Content-Length", 0))
				raw = self.rfile.read(length)

				# Validate JSON
				try:
					payload = json.loads(raw)
				except Exception:
					self._error(400, "Malformed JSON")
					return

				# report_id/known-ids are re-read here (not cached from
				# startup) since the server may have started before
				# report.json existed (see report_meta() above).
				exp_id, k_ids = meta_fn()
				if exp_id is None:
					self._error(503, "report not ready yet")
					return

				# Validate report_id
				if payload.get("report_id") != exp_id:
					self._error(
						400,
						f"report_id mismatch: expected {exp_id!r}, "
						f"got {payload.get('report_id')!r}",
					)
					return

				# Validate suggestion ids
				submitted_ids = set(payload.get("decisions", {}).keys())
				unknown = submitted_ids - k_ids
				if unknown:
					self._error(
						400,
						"Unknown suggestion id(s): " + ", ".join(sorted(unknown)),
					)
					return

				# Re-check-then-write under the lock: makes the duplicate
				# guard and the atomic write one indivisible step, so two
				# concurrent POSTs can't both pass the check above before
				# either has written (see write_lock's docstring above).
				with write_lock:
					if os.path.exists(feedback_path):
						self._error(409, "already_submitted")
						return
					write_json_atomic(feedback_path, payload)

				body = json.dumps(
					{"status": "written", "path": feedback_path}
				).encode("utf-8")
				self._respond(200, body)
				# Server stays up — session polls feedback.json and calls
				# POST /shutdown when apply pass is complete.

			# ── Helpers ────────────────────────────────────────────────────
			def _respond(self, code: int, body: bytes, content_type: str = "application/json", extra_headers=None):
				self.send_response(code)
				self.send_header("Content-Type", content_type)
				if extra_headers:
					for header_name, header_value in extra_headers.items():
						self.send_header(header_name, header_value)
				self.send_header("Content-Length", str(len(body)))
				self.end_headers()
				self.wfile.write(body)

			def _error(self, code: int, message: str):
				self._respond(code, json.dumps({"error": message}).encode("utf-8"))

		return Handler

	# ── Server class with address reuse and quiet disconnect handling ─────
	class Server(socketserver.ThreadingTCPServer):
		allow_reuse_address = True
		daemon_threads = True

		def handle_error(self, request, client_address):
			# Suppress tracebacks for clients that vanish mid-request
			# (ENOTCONN 57 / ECONNRESET 54 / EPIPE 32 — routine, especially
			# for same-host probes of a tailscale/utun address on macOS).
			# Use sys.exc_info() (not sys.exception(), which needs Python
			# 3.11+) — this may run under a freshly-imaged Mac's bundled
			# /usr/bin/python3 (3.9.x) before mise has provisioned a newer
			# one, and this is a setup-bootstrapping skill by design.
			exc = sys.exc_info()[1]
			if isinstance(exc, OSError) and exc.errno in (32, 54, 57):
				return
			super().handle_error(request, client_address)

	Handler = make_handler(session_dir, report_meta)

	# ── Port walking: one port that is free on every bind address ─────────
	servers: list = []
	port = start_port
	max_port = max(8751, start_port)
	while port <= max_port:
		opened = []
		try:
			for addr in bind_addrs:
				opened.append(Server((addr, port), Handler))
			servers = opened
			break
		except OSError:
			for srv in opened:
				srv.server_close()
			port += 1

	if not servers:
		print(
			f"Error: all ports {start_port}–{max_port} are busy. "
			f"Check with: lsof -i :{start_port}",
			file=sys.stderr,
		)
		sys.exit(1)

	server_holder.extend(servers)
	for addr in bind_addrs:
		print(f"SERVING http://{addr}:{port}/", flush=True)

	threads = [
		threading.Thread(target=srv.serve_forever, daemon=True)
		for srv in servers
	]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()


if __name__ == "__main__":
	main()
