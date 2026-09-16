#!/usr/bin/env python3
"""
test_render.py — render.py's refusals, replacements and the HTML-breakout
escape.
Usage: python3 test_render.py [-v]

Stdlib `unittest` only (no pytest, no fixtures directory, no network), same
constraints as the sibling suites. render.py is run the way it actually runs
— `python3 render.py <report.json>` as a subprocess against the REAL
`assets/report-template.html` — so these tests also pin the template's side
of the contract: the three tokens must exist in it for the replacements to
be observable at all.

What this file is about, in order of stakes:

1. **The `</` escape.** REPORT_DATA lands inside a `<script>` block, and any
   agent-written free-text field can carry a literal `</script>`. Without the
   escape, `</script><img src=x onerror=alert(1)>` closes the script element
   and the payload renders as live HTML in the page a human then clicks
   "accept" on. This is the one test in the file that is about security
   rather than robustness.
2. **The schema-2 refusal.** The template reads the item model and nothing
   else; a schema-1 report would render a page of EMPTY cards rather than
   failing, so render.py refusing it is what keeps that impossible.
3. **The duplicate-suggestion-id refusal.** The page keys approval state on
   suggestion ids; two tools sharing one would silently cross their wires.

Every refusal case also asserts index.html was NOT written: a refusal that
leaves a stale page behind is a page somebody will serve.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_PY = os.path.join(SCRIPT_DIR, "render.py")
SERVER_PY = os.path.join(SCRIPT_DIR, "server.py")
TEMPLATE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "assets",
	"report-template.html"))


def minimal_report(**over):
	"""The smallest report render.py accepts. Everything else in report.json
	is the template's business, read at page runtime, not render time."""
	report = {
		"schema_version": 2,
		"report_id": "tool-update-review-20260916T000000Z",
		"generated_at": "2026-09-16T00:00:00Z",
		"tools": [],
	}
	report.update(over)
	return report


class RenderRunner(unittest.TestCase):
	def render(self, report, as_bytes=None, path_override=None):
		"""Run render.py against a throwaway report dir; returns
		(CompletedProcess, report_dir). The dir outlives the call so a test
		can assert what was — or was not — written into it."""
		report_dir = tempfile.mkdtemp(prefix="render-test-")
		self.addCleanup(__import__("shutil").rmtree, report_dir, True)
		report_path = os.path.join(report_dir, "report.json")
		if as_bytes is not None:
			with open(report_path, "wb") as fh:
				fh.write(as_bytes)
		else:
			with open(report_path, "w", encoding="utf-8") as fh:
				json.dump(report, fh)
		p = subprocess.run([sys.executable, RENDER_PY,
			path_override or report_path],
			capture_output=True, text=True, timeout=60)
		return p, report_dir

	def read_page(self, report_dir):
		with open(os.path.join(report_dir, "index.html"), "r",
				encoding="utf-8") as fh:
			return fh.read()


class RenderHappyPathTests(RenderRunner):
	def test_a_valid_report_renders_replaces_every_token_and_copies_the_server(self):
		report = minimal_report()
		p, report_dir = self.render(report)
		self.assertEqual(p.returncode, 0, p.stderr)
		out_path = os.path.join(report_dir, "index.html")
		# The printed path is the caller's handle on the artifact.
		self.assertEqual(p.stdout.strip(), out_path)
		html = self.read_page(report_dir)
		# All three tokens are gone…
		for token in ("__REPORT_ID__", "__GENERATED_AT__", "__REPORT_DATA__"):
			self.assertNotIn(token, html, token)
		# …replaced by the report's own values, quotes included for the two
		# attribute tokens (the token in the template carries the quotes).
		self.assertIn(json.dumps(report["report_id"]), html)
		self.assertIn(json.dumps(report["generated_at"]), html)
		self.assertIn('"schema_version": 2', html)
		# server.py rides along so `python3 server.py` works from the dir.
		with open(os.path.join(report_dir, "server.py"), encoding="utf-8") as fh:
			copied = fh.read()
		with open(SERVER_PY, encoding="utf-8") as fh:
			self.assertEqual(copied, fh.read())

	def test_the_template_actually_carries_all_three_tokens(self):
		"""The other side of the replacement contract. If the template loses
		a token, the happy-path test above would still pass for the two whose
		values happen to appear in the data — this one fails loudly."""
		with open(TEMPLATE, encoding="utf-8") as fh:
			template = fh.read()
		self.assertIn('"__REPORT_ID__"', template)
		self.assertIn('"__GENERATED_AT__"', template)
		self.assertIn("__REPORT_DATA__", template)

	def test_suggestions_without_ids_do_not_trip_the_uniqueness_pass(self):
		report = minimal_report(tools=[
			{"id": "brew:a", "suggestions": [{"kind": "edit"}, {"id": None}]},
			{"id": "brew:b", "suggestions": [{"kind": "edit"}]},
		])
		p, _ = self.render(report)
		self.assertEqual(p.returncode, 0, p.stderr)


class RenderEscapeTests(RenderRunner):
	PAYLOAD = "</script><img src=x onerror=alert(1)>"

	def test_a_script_closing_sequence_never_lands_verbatim_in_the_page(self):
		"""REPORT_DATA is a JS object literal inside a <script> element. HTML
		parses the element's end BEFORE JavaScript ever runs, so a literal
		"</script>" inside any agent-written string ends the block mid-JSON
		and everything after it — here an onerror handler — is live markup.
		"<\\/" is identical inside a JS string, so the escape costs nothing."""
		report = minimal_report(tools=[{
			"id": "brew:x",
			"items": [{"id": "brew:x#none:t", "title": self.PAYLOAD,
				"tags": ["fix"], "severity": "info"}],
			"suggestions": [{"id": "brew:x:upgrade", "kind": "upgrade",
				"rationale": self.PAYLOAD}],
		}])
		p, report_dir = self.render(report)
		self.assertEqual(p.returncode, 0, p.stderr)
		html = self.read_page(report_dir)
		# The attack sequence appears nowhere in the page…
		self.assertNotIn(self.PAYLOAD, html)
		self.assertNotIn("</script><img", html)
		# …its escaped spelling does (twice: the item and the suggestion)…
		self.assertEqual(html.count("<\\/script><img src=x onerror=alert(1)>"), 2)
		# …and it still reads back as the SAME string once the page's JS
		# parses the object literal — the escape must never alter content,
		# only its byte spelling inside the script element.
		self.assertEqual(json.loads(json.dumps(self.PAYLOAD).replace("</", "<\\/")),
			self.PAYLOAD)

	def test_the_escape_touches_only_closing_tag_bytes(self):
		"""A title full of ordinary angle brackets and slashes must come
		through byte-identical — over-escaping would corrupt rendered text."""
		title = "a < b, path/to/file, 2 </ maybe, a <= b"
		report = minimal_report(tools=[{"id": "brew:x", "items": [
			{"id": "brew:x#none:t", "title": title, "tags": ["fix"],
				"severity": "info"}], "suggestions": []}])
		p, report_dir = self.render(report)
		self.assertEqual(p.returncode, 0, p.stderr)
		html = self.read_page(report_dir)
		self.assertIn(title.replace("</", "<\\/"), html)


class RenderRefusalTests(RenderRunner):
	def refuse(self, report=None, **kw):
		p, report_dir = self.render(report, **kw)
		self.assertEqual(p.returncode, 1, p.stdout)
		# A refusal never leaves a page behind.
		self.assertFalse(os.path.exists(os.path.join(report_dir, "index.html")))
		return p

	def test_a_non_schema_2_report_is_refused_by_value(self):
		"""Schema 1, a missing version, and a STRING "2" are all refusals:
		the template renders `tools[].items[]` and nothing else, so a
		schema-1 report would produce a page of empty cards that looks like
		a quiet release rather than failing."""
		for version in (1, None, "2", 3):
			with self.subTest(repr(version)):
				report = minimal_report()
				if version is None:
					del report["schema_version"]
				else:
					report["schema_version"] = version
				p = self.refuse(report)
				self.assertIn("schema_version must be 2", p.stderr)

	def test_a_duplicate_suggestion_id_across_tools_is_refused_naming_both(self):
		report = minimal_report(tools=[
			{"id": "brew:a", "suggestions": [{"id": "brew:a:upgrade"}]},
			{"id": "brew:b", "suggestions": [{"id": "brew:a:upgrade"}]},
		])
		p = self.refuse(report)
		self.assertIn("duplicate suggestion id", p.stderr)
		# Naming both tools is what makes the message actionable.
		self.assertIn("brew:a", p.stderr)
		self.assertIn("brew:b", p.stderr)

	def test_a_missing_report_file_is_a_refusal_not_a_traceback(self):
		p, report_dir = self.render(minimal_report(),
			path_override=os.path.join(tempfile.gettempdir(),
				"render-test-no-such", "report.json"))
		self.assertEqual(p.returncode, 1)
		self.assertIn("report not found", p.stderr)
		self.assertNotIn("Traceback", p.stderr)

	def test_an_unparseable_report_is_a_refusal_not_a_traceback(self):
		p, report_dir = self.render(None, as_bytes=b"{not json")
		self.assertEqual(p.returncode, 1)
		self.assertIn("not valid JSON", p.stderr)
		self.assertNotIn("Traceback", p.stderr)
		self.assertFalse(os.path.exists(os.path.join(report_dir, "index.html")))


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
