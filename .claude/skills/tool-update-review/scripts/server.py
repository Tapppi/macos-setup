#!/usr/bin/env python3
"""
server.py — local review server for tool-update-review sessions.
Usage: server.py <session_dir> [port] [--bind ADDR]

Always listens on 127.0.0.1; each --bind ADDR adds a listener on the
same port (e.g. the tailscale IP from `tailscale ip -4` for remote
review). The report holds config details, so only add interfaces the
user trusts — a tailnet qualifies, a shared LAN may not. The server
stays up after POST /feedback and shuts down only when POST /shutdown
is received (or the 24h session fallback triggers). POST /followup records
a decision for a suggestion that didn't exist at render time (e.g. one
raised by investigating a tool_comments entry mid-apply) into
followup_decisions.json — one decision per suggestion id, no session-wide
duplicate guard like /feedback has.

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


def write_json_atomic(path: str, obj) -> None:
	"""Write JSON to `path` via a `.tmp` + os.replace() so a reader (the page's
	poll, or this same server on a later request) never observes a partial
	file — shared by every write-then-rename spot in this module."""
	tmp_path = path + ".tmp"
	with open(tmp_path, "w", encoding="utf-8") as fh:
		json.dump(obj, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")
	os.replace(tmp_path, path)


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

	# ── Load report metadata ──────────────────────────────────────────────
	report_path = os.path.join(session_dir, "report.json")
	try:
		with open(report_path, "r", encoding="utf-8") as fh:
			report = json.load(fh)
	except FileNotFoundError:
		print(f"Error: report.json not found in {session_dir!r}", file=sys.stderr)
		sys.exit(1)
	except json.JSONDecodeError as exc:
		print(f"Error: report.json is not valid JSON: {exc}", file=sys.stderr)
		sys.exit(1)

	expected_report_id = report.get("report_id", "")
	known_ids: set[str] = set()
	for tool in report.get("tools", []):
		for sug in tool.get("suggestions", []):
			sid = sug.get("id")
			if sid:
				known_ids.add(sid)

	# ── Mutable holder so the handler closure can reach every listener ────
	server_holder: list = []

	# ── Request handler ───────────────────────────────────────────────────
	def make_handler(sess_dir: str, k_ids: set, exp_id: str):
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

				# Anything else (/, /updates, favicon probes) gets the page.
				index_path = os.path.join(sess_dir, "index.html")
				try:
					with open(index_path, "rb") as fh:
						data = fh.read()
				except FileNotFoundError:
					self.send_error(404, "index.html not found")
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

					sid = payload.get("suggestion_id")
					decision = payload.get("decision")
					if not sid or decision not in ("accept", "reject", "discuss"):
						self._error(400, "Expected suggestion_id and decision in accept|reject|discuss")
						return

					followup_path = os.path.join(sess_dir, "followup_decisions.json")
					with write_lock:
						try:
							with open(followup_path, "r", encoding="utf-8") as fh:
								existing = json.load(fh)
						except (FileNotFoundError, json.JSONDecodeError):
							existing = {}

						if sid in existing:
							self._error(409, f"suggestion {sid!r} already decided")
							return

						existing[sid] = {
							"decision": decision,
							"comment": payload.get("comment", ""),
						}
						write_json_atomic(followup_path, existing)

					body = json.dumps({"status": "written", "path": followup_path}).encode("utf-8")
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

	Handler = make_handler(session_dir, known_ids, expected_report_id)

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
