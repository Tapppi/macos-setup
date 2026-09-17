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

1. **The `<` escape.** REPORT_DATA lands inside a `<script>` block, and any
   agent-written free-text field can carry a literal `</script>` (closes the
   element early; the payload renders as live HTML in the page a human then
   clicks "accept" on) or `<!--` (flips the parser into
   script-data-double-escaped state and the template's own `</script>` stops
   terminating). "<" is escaped wholesale, so both spellings — and any next
   one — die together. This is the one test group in the file that is about
   security rather than robustness.
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

sys.path.insert(0, SCRIPT_DIR)
import items  # noqa: E402


def minimal_report(**over):
	"""The smallest report render.py accepts. Everything else in report.json
	is the template's business, read at page runtime, not render time."""
	report = {
		"schema_version": 2,
		"contract_version": items.CONTRACT_VERSION,
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
	CLOSER = "</script><img src=x onerror=alert(1)>"
	DOUBLE_ESCAPE = "<!--<script>"

	def test_no_angle_bracket_survives_into_the_script_element(self):
		"""REPORT_DATA is a JS object literal inside a <script> element, and
		HTML parses the element's end BEFORE JavaScript ever runs. Escaping
		only "</" was measured insufficient: a literal "<!--" flips the
		parser into script-data-double-escaped state, where the template's
		own closing tag no longer terminates the element and the rest of the
		document is swallowed. So "<" is escaped wholesale as \u003c — a
		denylist of breakout spellings is a losing game."""
		report = minimal_report(tools=[{
			"id": "brew:x",
			"items": [{"id": "brew:x#none:t", "title": self.CLOSER,
				"tags": ["fix"], "severity": "info"},
				{"id": "brew:x#none:u", "title": self.DOUBLE_ESCAPE,
					"tags": ["fix"], "severity": "info"}],
			"suggestions": [{"id": "brew:x:upgrade", "kind": "upgrade",
				"rationale": self.CLOSER}],
		}])
		p, report_dir = self.render(report)
		self.assertEqual(p.returncode, 0, p.stderr)
		html = self.read_page(report_dir)
		# Neither breakout spelling appears anywhere in the page…
		self.assertNotIn(self.CLOSER, html)
		self.assertNotIn("</script><img", html)
		self.assertNotIn(self.DOUBLE_ESCAPE, html)
		# …their escaped spellings do (closer twice: the item and the
		# suggestion; the double-escape opener once)…
		self.assertEqual(
			html.count("\\u003c/script>\\u003cimg src=x onerror=alert(1)>"), 2)
		self.assertEqual(html.count("\\u003c!--\\u003cscript>"), 1)
		# …and the data segment carries no raw "<" from the payload at all —
		# the token replacement happened, so the only guarantee worth making
		# is on content: see the round-trip test below.

	def test_the_escape_is_content_preserving(self):
		"""\u003c inside a JSON/JS string literal IS "<" once parsed — the
		escape may change byte spelling inside the script element, never what
		the page's JS reads back. Ordinary titles full of angle brackets and
		slashes must round-trip identically."""
		for text in (self.CLOSER, self.DOUBLE_ESCAPE,
				"a < b, path/to/file, 2 </ maybe, a <= b"):
			with self.subTest(text):
				escaped = json.dumps(text, ensure_ascii=False).replace("<", "\\u003c")
				self.assertEqual(json.loads(escaped), text)

	def test_an_escaped_title_still_reaches_the_page(self):
		title = "a < b and a <= b"
		report = minimal_report(tools=[{"id": "brew:x", "items": [
			{"id": "brew:x#none:t", "title": title, "tags": ["fix"],
				"severity": "info"}], "suggestions": []}])
		p, report_dir = self.render(report)
		self.assertEqual(p.returncode, 0, p.stderr)
		html = self.read_page(report_dir)
		self.assertIn(title.replace("<", "\\u003c"), html)


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

	def test_a_foreign_contract_version_is_refused_by_value(self):
		"""Same exact-equality gate as schema_version, same shape, same exit
		code (deferred from pass 1; REDESIGN §I9 — no shim). A string spelling
		of the right number is still a refusal: equality, not coercion."""
		for version in (items.CONTRACT_VERSION - 1, items.CONTRACT_VERSION + 1,
				None, str(items.CONTRACT_VERSION)):
			with self.subTest(repr(version)):
				report = minimal_report()
				if version is None:
					del report["contract_version"]
				else:
					report["contract_version"] = version
				p = self.refuse(report)
				self.assertIn(
					f"contract_version must be {items.CONTRACT_VERSION}", p.stderr)
				self.assertNotIn("Traceback", p.stderr)

	def test_the_contract_gate_sits_after_the_schema_gate(self):
		"""A schema-1 report with a foreign contract names the schema first —
		the older, broader refusal — so the message a user acts on is the one
		that explains the page they cannot have."""
		report = minimal_report(schema_version=1, contract_version=999)
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




# ══ Driving the rendered page ═══════════════════════════════════════════════
# The defects this file pins from here down are BEHAVIOURS of the template's
# JavaScript against a real DOM — a ring on one tool and the decision landing
# on another, an item unreachable without a mouse, a deep link stranded short
# of its target. Reading the markup cannot pin any of them (the wrong-tool
# write shipped twice with plausible markup), so these tests render a fixture
# report, append a driver script, load the page in HEADLESS CHROME, dispatch
# real KeyboardEvents at it, and assert on what the page then says about
# itself. If no Chrome/Chromium binary is present the class skips loudly —
# the refusal/escape tests above still run everywhere.
CHROME_CANDIDATES = [
	os.environ.get("TOOL_UPDATE_REVIEW_CHROME") or "",
	"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
	"/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def find_chrome():
	for path in CHROME_CANDIDATES:
		if path and os.path.exists(path):
			return path
	for name in ("chromium", "google-chrome", "chrome"):
		found = __import__("shutil").which(name)
		if found:
			return found
	return None


CHROME = find_chrome()


def page_tool(tid, name, cur, lat, bucket, sev_item="info", tags=("fix",),
		delta="minor", pre=False, **over):
	"""One schema-2 Tool in the shape assemble.py emits — the smallest one
	the template renders a full section for."""
	item = {
		"id": f"{tid}#release:{lat}/one", "title": f"{name} change one",
		"tags": list(tags), "severity": sev_item,
		"local": {"direction": "unclear", "effect": "none",
			"statement": f"What this means for {name} here.",
			"evidence": [], "citations": []},
	}
	tool = {
		"id": tid, "name": name, "source": "brew", "pinned": False,
		"current_version": cur, "latest_version": lat, "research_error": None,
		"items": [item], "quarantine": [], "spec_violations": [],
		"validator_error": None,
		"degradation": {"content_losing": [], "markers": [], "quarantined": 0},
		"watch_hit_item_ids": [], "links": [],
		"config_status": {"state": "unknown", "detail": "", "evidence": []},
		"vendor_silent_categories": [], "release_inventory": [],
		"suggestions": [{
			"id": f"{tid}:upgrade", "kind": "upgrade",
			"title": f"Upgrade {name} {cur} -> {lat}", "target_files": [],
			"command": f"brew upgrade {name}", "auto_runnable": True,
			"needs_sudo": False, "rationale": "r", "motivating_link": None,
			"diff_preview": None, "pre_accept": pre,
		}],
		"version_delta": delta, "version_scheme": "semver",
		"version_delta_note": "",
		"security": {"cve_ids": [], "cve_count": 0, "cve_claimed_count": None,
			"has_security": False, "security_only": False, "impact": "unknown",
			"severity_counts": None, "display_item_ids": []},
		"risk_level": "low", "review_bucket": bucket,
		"bucket_inputs": {"has_security": False, "security_only": False,
			"impact": "unknown", "version_delta": delta, "runnable": True},
		"pre_accept_bars": [],
	}
	tool.update(over)
	return tool


def page_report(tools, **over):
	report = minimal_report(tools=tools)
	report["machine"] = {"hostname": "h", "arch": "arm64", "os": "macOS"}
	report["validation"] = {"clean": True, "counts": {}, "orphans": [], "unmatched": []}
	report["summary"] = {
		"total_outdated": len(tools), "incompatible_count": 0, "warning_count": 0,
		"suggestions_count": sum(len(t["suggestions"]) for t in tools),
		"health_count": 0, "skill_drift_count": 0,
		"by_delta": None, "by_bucket": None, "security": None,
	}
	report["repo_context"] = {}
	report["convergence"] = {"state": "not_run"}
	report["highlights"] = []
	report.update(over)
	return report


def six_tools():
	"""The wrong-tool fixture: six tools whose default 'needs-decision'
	order (severity, then name) differs from name order, so a re-sort
	visibly reorders, and whose deltas differ so a filter visibly hides."""
	tools = [
		page_tool("brew:alpha", "alpha", "1.0", "1.1", "attention", sev_item="warning"),
		page_tool("brew:bravo", "bravo", "2.0", "2.1", "routine", delta="major"),
		page_tool("brew:charlie", "charlie", "3.0", "3.2", "attention", sev_item="warning"),
		page_tool("brew:delta-tool", "delta-tool", "4.0", "4.1", "routine", delta="major"),
		page_tool("brew:echo", "echo", "5.0", "5.5", "attention", sev_item="notable"),
		page_tool("brew:foxtrot", "foxtrot", "6.0", "6.1", "routine"),
	]
	return tools


DRIVER_TEMPLATE = """
<script>
(function () {
	const out = document.createElement('pre');
	out.id = 'test-out';
	document.body.appendChild(out);
	function log(s) { out.textContent += s + '\\n'; }
	function key(k) {
		document.dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
	}
	function accepted() {
		const ids = [];
		document.querySelectorAll('#main .suggestion-card').forEach(c => {
			if (c.dataset.decision === 'accept') ids.push(c.dataset.suggestionId);
		});
		return ids.join(',');
	}
	function ring() {
		const el = document.querySelector('#main [data-focused], #panel-overview [data-focused]');
		return el ? (el.dataset.toolId || el.dataset.tool || el.id || 'ringed') : 'none';
	}
	let tries = 0;
	const t = setInterval(() => {
		tries++;
		if (!document.querySelector('#tool-list .tool-section') &&
			!document.querySelector('#tool-list #empty-state')) {
			if (tries > 200) { log('FAIL: page never rendered'); log('DONE'); clearInterval(t); }
			return;
		}
		clearInterval(t);
		try { scenario(); } catch (e) { log('ERROR: ' + e.message); }
		log('DONE');
	}, 20);
	function scenario() {
%s
	}
})();
</script>
"""


@unittest.skipUnless(CHROME, "no Chrome/Chromium binary found — page-drive "
	"tests skipped (set TOOL_UPDATE_REVIEW_CHROME to point at one)")
class PageDriveRunner(RenderRunner):
	"""render → append driver → headless Chrome → read back #test-out."""

	def drive(self, report, scenario_js, budget=6000):
		p, report_dir = self.render(report)
		self.assertEqual(p.returncode, 0, p.stderr)
		page_path = os.path.join(report_dir, "index.html")
		with open(page_path, encoding="utf-8") as fh:
			html = fh.read()
		driven = html.replace("</body>", (DRIVER_TEMPLATE % scenario_js) + "</body>")
		driven_path = os.path.join(report_dir, "driven.html")
		with open(driven_path, "w", encoding="utf-8") as fh:
			fh.write(driven)
		proc = subprocess.run([CHROME, "--headless=new", "--disable-gpu",
			"--force-prefers-reduced-motion", "--window-size=1400,900",
			"--virtual-time-budget=%d" % budget, "--dump-dom", driven_path],
			capture_output=True, text=True, timeout=180)
		import re as _re
		m = _re.search(r'<pre id="test-out">(.*?)</pre>', proc.stdout, _re.S)
		self.assertIsNotNone(m, "driver output not found in dumped DOM:\n"
			+ proc.stdout[-1500:] + proc.stderr[-1500:])
		text = m.group(1)
		self.assertIn("DONE", text, text)
		self.assertNotIn("ERROR:", text, text)
		self.assertNotIn("FAIL:", text, text)
		lines = {}
		for line in text.splitlines():
			if "=" in line:
				k, _, v = line.partition("=")
				lines[k] = v
		return lines


class WrongToolDecisionTests(PageDriveRunner):
	"""The most serious defect of the pass: `a`/`r`/`c` wrote the decision to
	a different tool than the one ringed. Reproduced twice with trusted input
	(REVIEW-2026-09-16 §3.4): a re-sort with no re-render left focusedIdx
	pointing at a stale position, and the keystroke's decision entered the
	/feedback payload for a tool the user never looked at. Both recorded
	modes are pinned here BY DRIVING KEYS at the rendered page."""

	def test_a_keystroke_decides_the_ringed_tool_across_a_resort(self):
		"""Mode 2 (1password-cli/brew:cmake): ring a tool, re-sort the list
		(applyFilters reorders the DOM in place), press `a` — the decision
		must land on the ringed tool. Before the fix this accepted
		brew:charlie:upgrade with the ring on brew:echo."""
		out = self.drive(page_report(six_tools()), """
		key('2');
		key('j'); key('j'); key('j');
		const r = document.querySelector('#main [data-focused]');
		log('ringed=' + (r ? r.dataset.toolId : 'none'));
		document.getElementById('filter-sort').value = 'name';
		applyFilters();
		key('a');
		log('accepted=' + accepted());
		log('ringedAfter=' + ring());
""")
		self.assertEqual(out["ringed"], "brew:echo")
		self.assertEqual(out["accepted"], "brew:echo:upgrade",
			"the decision landed on a different tool than the ringed one")
		self.assertEqual(out["ringedAfter"], "brew:echo")

	def test_a_keystroke_after_a_filter_hides_the_ring_decides_nothing(self):
		"""Mode 1 (brew:azcopy/cask:gcloud-cli): an Overview tile filters the
		list and hides the ringed tool. The keystroke must decide NOTHING —
		not whatever now sits at the stale index — and the ring must be
		gone rather than pointing at a hidden card."""
		out = self.drive(page_report(six_tools()), """
		key('2');
		key('j'); key('j'); key('j');
		const r = document.querySelector('#main [data-focused]');
		log('ringed=' + (r ? r.dataset.toolId : 'none'));
		runOverviewAction('filter-delta', 'major');   // a real tile action
		key('2');
		key('a');
		log('accepted=' + accepted());
		log('ringedAfter=' + ring());
		key('j');                                     // recovery: j starts from the top
		const r2 = document.querySelector('#main [data-focused]');
		log('ringedNext=' + (r2 ? r2.dataset.toolId : 'none'));
		log('hiddenNext=' + (r2 ? r2.hasAttribute('data-hidden') : 'n/a'));
""")
		self.assertEqual(out["ringed"], "brew:echo")
		self.assertEqual(out["accepted"], "",
			"a hidden ring must not resolve to a decision on another tool")
		self.assertEqual(out["ringedAfter"], "none")
		# And navigation recovers onto a VISIBLE card.
		self.assertIn(out["ringedNext"], ("brew:bravo", "brew:delta-tool"))
		self.assertEqual(out["hiddenNext"], "false")



class KeyboardReachabilityTests(PageDriveRunner):
	"""Per-item detail was keyboard-unreachable: a bare <div> with an inline
	onclick, no tabindex, no role, no key handler — 140 Tab presses and 32
	distinct keys expanded zero items, and 45 of 47 gated blocks appear
	nowhere else. `.tool-header` advertised role="button" with no tabindex —
	an ARIA contract that could not be honoured. These drive FOCUS + Enter /
	Space against the rendered page, not the markup."""

	def test_an_item_title_is_focusable_and_enter_reveals_the_detail(self):
		out = self.drive(page_report(six_tools()), """
		key('2');
		const title = document.querySelector('.content-item-title.has-expand');
		log('focusable=' + (title.tabIndex >= 0));
		log('role=' + title.getAttribute('role'));
		title.focus();
		log('focused=' + (document.activeElement === title));
		title.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
		const item = title.closest('.content-item');
		log('expanded=' + item.dataset.expanded);
		log('aria=' + title.getAttribute('aria-expanded'));
		const statement = item.querySelector('.item-statement');
		log('detailVisible=' + (statement && statement.offsetParent !== null));
		title.dispatchEvent(new KeyboardEvent('keydown', {key: ' ', bubbles: true}));
		log('collapsedAgain=' + item.dataset.expanded);
""")
		self.assertEqual(out["focusable"], "true")
		self.assertEqual(out["role"], "button")
		self.assertEqual(out["focused"], "true")
		self.assertEqual(out["expanded"], "1")
		self.assertEqual(out["aria"], "true")
		self.assertEqual(out["detailVisible"], "true",
			"the gated detail block must be visible after Enter")
		self.assertEqual(out["collapsedAgain"], "0")

	def test_the_tool_header_honours_its_advertised_button_role(self):
		out = self.drive(page_report(six_tools()), """
		key('2');
		const header = document.querySelector('#tool-list .tool-section:not(.collapsed) .tool-header');
		log('focusable=' + (header.tabIndex >= 0));
		header.focus();
		log('focused=' + (document.activeElement === header));
		header.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
		log('collapsed=' + header.closest('.tool-section').classList.contains('collapsed'));
		log('aria=' + header.getAttribute('aria-expanded'));
""")
		self.assertEqual(out["focusable"], "true")
		self.assertEqual(out["focused"], "true")
		self.assertEqual(out["collapsed"], "true")
		self.assertEqual(out["aria"], "false")

	def test_an_item_with_nothing_to_expand_advertises_no_control(self):
		"""The other half of the contract: no detail, no role, no tab stop —
		a focusable no-op control is as dishonest as an unreachable one."""
		tools = [page_tool("brew:bare", "bare", "1.0", "1.1", "routine")]
		del tools[0]["items"][0]["local"]
		out = self.drive(page_report(tools), """
		key('2');
		const title = document.querySelector('.content-item-title');
		log('hasExpand=' + title.classList.contains('has-expand'));
		log('tabIndex=' + title.tabIndex);
		log('role=' + (title.getAttribute('role') || 'none'));
""")
		self.assertEqual(out["hasExpand"], "false")
		self.assertEqual(out["role"], "none")
		self.assertNotEqual(out["tabIndex"], "0")


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
