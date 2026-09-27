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
		# --dump-dom re-serializes #test-out's text, so "&" arrives as
		# "&amp;" — unescape ONCE to get back what the page logged.
		text = __import__("html").unescape(m.group(1))
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



class DeepLinkLandingTests(PageDriveRunner):
	"""REDESIGN §J bug 1, measured at 577.98px: scrollIntoView clamps at max
	scroll, so a deep link into a tool near the document end landed short
	and read as broken. The one-viewport tail spacer makes every section's
	top a reachable scroll offset; the prototype measured the residual at
	0.47px. Driven: jump to the LAST tool and measure where it landed."""

	def _many_tools(self, n=24):
		return [page_tool(f"brew:tool{i:02d}", f"tool{i:02d}", "1.0", "1.1",
			"routine") for i in range(n)]

	def test_a_jump_to_the_last_tool_lands_at_the_sticky_bar(self):
		out = self.drive(page_report(self._many_tools()), """
		const sections = document.querySelectorAll('#tool-list .tool-section');
		const last = sections[sections.length - 1];
		jumpToTool(last.dataset.toolId);
		const stickyH = parseFloat(getComputedStyle(document.documentElement)
			.getPropertyValue('--sticky-h'));
		const top = last.getBoundingClientRect().top;
		log('toolId=' + last.dataset.toolId);
		log('error=' + Math.abs(top - (stickyH + 8)).toFixed(2));
		log('atMax=' + (Math.ceil(window.scrollY) >=
			document.documentElement.scrollHeight - window.innerHeight - 1));
""", budget=8000)
		self.assertLessEqual(float(out["error"]), 2.0,
			"deep link landed %spx off the sticky bar" % out["error"])

	def test_the_spacer_exists_only_when_there_are_tools(self):
		out = self.drive(page_report([]), """
		const spacer = document.getElementById('tail-spacer');
		log('spacerHidden=' + (spacer.offsetParent === null));
""")
		self.assertEqual(out["spacerHidden"], "true")



class SuggestionCardBodyTests(PageDriveRunner):
	"""Three card-body defects, all measured (REVIEW §3.4): the method-note
	kind fell through to renderDiff(undefined) and rendered an EMPTY body;
	self_test_failed {limb, reason} rendered for neither memory kind; a
	bare-string target_files entry rendered a zero-width empty chip."""

	def _tool_with_suggestions(self, extra_suggestions):
		tool = page_tool("brew:memo", "memo", "1.0", "1.1", "attention",
			sev_item="warning")
		tool["suggestions"].extend(extra_suggestions)
		return page_report([tool])

	def test_a_method_note_card_renders_topic_note_rationale_and_self_test(self):
		out = self.drive(self._tool_with_suggestions([{
			"id": "brew:memo:method-changelog", "kind": "method-note",
			"title": "Method note: where the changelog lives",
			"target_files": [], "command": None, "auto_runnable": False,
			"rationale": "The release page lied on 1.0.",
			"method_topic": "where the real changelog lives",
			"method_note": "Read CHANGES.rst on the tag, not the release page.",
			"self_test_failed": {"limb": "unwitnessed",
				"reason": "predicts a failure rather than naming one"},
		}]), """
		key('2');
		const card = document.querySelector('[data-suggestion-id="brew:memo:method-changelog"]');
		const body = card.querySelector('.method-note-body');
		log('bodyExists=' + !!body);
		log('bodyVisible=' + (body && body.offsetParent !== null));
		log('topic=' + body.querySelector('.watch-item-topic').textContent.trim());
		log('hasNote=' + body.textContent.includes('Read CHANGES.rst on the tag'));
		log('hasRationale=' + body.textContent.includes('The release page lied on 1.0.'));
		const st = card.querySelector('.self-test-note');
		log('selfTest=' + (st ? st.textContent.trim() : 'none'));
		log('selfTestVisible=' + (st && st.offsetParent !== null));
""")
		self.assertEqual(out["bodyExists"], "true")
		self.assertEqual(out["bodyVisible"], "true")
		self.assertIn("where the real changelog lives", out["topic"])
		self.assertEqual(out["hasNote"], "true")
		self.assertEqual(out["hasRationale"], "true")
		self.assertIn("unwitnessed", out["selfTest"])
		self.assertIn("predicts a failure", out["selfTest"])
		self.assertEqual(out["selfTestVisible"], "true")

	def test_a_watch_item_card_renders_its_self_test_tag_too(self):
		out = self.drive(self._tool_with_suggestions([{
			"id": "brew:memo:watch-quarantine", "kind": "watch-item",
			"title": "Watch: quarantine returns", "target_files": [],
			"command": None, "auto_runnable": False, "rationale": "bit us before",
			"watch_topic": "cask quarantine after upgrade",
			"watch_note": "Check xattr after every upgrade.",
			"self_test_failed": {"limb": "scope",
				"reason": "config_status.detail already re-verifies this"},
		}]), """
		key('2');
		const card = document.querySelector('[data-suggestion-id="brew:memo:watch-quarantine"]');
		const st = card.querySelector('.self-test-note');
		log('selfTest=' + (st ? st.textContent.trim() : 'none'));
		log('topicShown=' + card.textContent.includes('cask quarantine after upgrade'));
""")
		self.assertIn("scope", out["selfTest"])
		self.assertIn("already re-verifies", out["selfTest"])
		self.assertEqual(out["topicShown"], "true")

	def test_a_bare_string_target_file_renders_its_path(self):
		out = self.drive(self._tool_with_suggestions([{
			"id": "brew:memo:edit-1", "kind": "edit",
			"title": "Edit the Brewfile",
			"target_files": ["Brewfile", {"path": "dotfiles/.functions",
				"description": "shell helpers"}, 7],
			"command": None, "auto_runnable": False, "rationale": "r",
			"diff_preview": "-a\n+b",
		}]), """
		key('2');
		const card = document.querySelector('[data-suggestion-id="brew:memo:edit-1"]');
		const chips = Array.from(card.querySelectorAll('.target-path'))
			.map(c => c.textContent.trim());
		log('chips=' + chips.join('|'));
		log('emptyChips=' + chips.filter(c => !c).length);
""")
		self.assertEqual(out["chips"], "Brewfile|dotfiles/.functions|7")
		self.assertEqual(out["emptyChips"], "0",
			"a target_files entry must never render as an empty chip")



def decision_surface_tool():
	"""One tool exercising all four item destinations (report-page.md §4.2)
	plus every chip kind (§5)."""
	tool = page_tool("brew:surface", "surface", "1.0", "2.0", "security_mixed")
	tool["items"] = [
		{"id": "brew:surface#cve:CVE-2026-1111", "title": "Fixes CVE-2026-1111 in the parser",
			"tags": ["security"], "severity": "warning",
			"security": {"cve_id": "CVE-2026-1111", "rating": "high",
				"rating_basis": "vendor", "exploited_in_wild": False},
			"local": {"direction": "reaches", "effect": "risk",
				"statement": "The parser runs here.", "evidence": [], "citations": []}},
		{"id": "brew:surface#release:2.0/flag-removed", "title": "The --full-auto flag is removed",
			"tags": ["breaking", "fix"], "severity": "warning",
			"local": {"direction": "reaches", "effect": "risk",
				"statement": "Used in .functions.", "evidence": [], "citations": []}},
		{"id": "brew:surface#release:2.0/config-move", "title": "Config moves to XDG",
			"tags": ["packaging"], "severity": "warning",
			"local": {"direction": "reaches", "effect": "risk",
				"statement": "Config lives at ~/.config here.", "evidence": [], "citations": []}},
		{"id": "brew:surface#release:2.0/speedup", "title": "Startup is 2x faster",
			"tags": ["perf"], "severity": "info",
			"local": {"direction": "does_not_reach", "effect": "none",
				"statement": "s", "evidence": [], "citations": []}},
		{"id": "brew:surface#release:2.0/mystery", "title": "Something oddly tagged",
			"tags": ["experimental"], "severity": "info"},
	]
	tool["security"] = {"cve_ids": ["CVE-2026-1111"], "cve_count": 1,
		"cve_claimed_count": None, "has_security": True, "security_only": False,
		"impact": "possible", "severity_counts": {"critical": 0, "high": 1,
			"medium": 0, "low": 0, "unknown": 0},
		"display_item_ids": ["brew:surface#cve:CVE-2026-1111"]}
	return tool


class DecisionSurfaceTests(PageDriveRunner):
	"""The §4 fold and the §5 chips: three visible groups, one fold that
	absorbs everything below the visibility bar, chips wearing ink inside a
	colored ring. Asserted on VISIBILITY (offsetParent), not presence — "it
	is in REPORT" is the defect, not the fix."""

	def test_the_three_groups_and_the_fold_partition_the_card(self):
		out = self.drive(page_report([decision_surface_tool()]), """
		key('2');
		const body = document.querySelector('#tool-list .tool-section .tool-body');
		const groups = Array.from(body.querySelectorAll('.content-group-title'))
			.map(g => g.textContent.trim().replace(/\\s+/g, ' '));
		log('groups=' + groups.join('|'));
		const fold = body.querySelector('.item-fold');
		const foldHead = fold.querySelector('.item-fold-head');
		log('foldLabel=' + foldHead.textContent.trim().replace(/\\s+/g, ' '));
		const folded = fold.querySelectorAll('.content-item');
		log('foldCount=' + folded.length);
		log('foldHiddenByDefault=' + Array.from(folded).every(i => i.offsetParent === null));
		foldHead.click();
		log('foldVisibleAfterClick=' + Array.from(folded).every(i => i.offsetParent !== null));
		log('foldTitles=' + Array.from(folded).map(i =>
			i.querySelector('.ci-text').textContent.trim()).join('|'));
""")
		groups = out["groups"].split("|")
		self.assertEqual(len(groups), 3)
		self.assertIn("Security", groups[0])
		self.assertIn("Breaking & deprecations", groups[1])
		self.assertIn("Other changes that reach this machine", groups[2])
		self.assertIn("Everything else (2)", out["foldLabel"])
		self.assertIn("do not reach this setup", out["foldLabel"])
		self.assertEqual(out["foldCount"], "2")
		self.assertEqual(out["foldHiddenByDefault"], "true")
		self.assertEqual(out["foldVisibleAfterClick"], "true")
		self.assertEqual(out["foldTitles"], "Startup is 2x faster|Something oddly tagged")

	def test_the_chips_wear_ink_and_lead_the_line(self):
		out = self.drive(page_report([decision_surface_tool()]), """
		key('2');
		const body = document.querySelector('#tool-list .tool-section .tool-body');
		const secItem = body.querySelector('#tool-list .content-item');
		const reaches = body.querySelectorAll('.item-chip.reaches');
		log('reachesChips=' + reaches.length);
		log('reachesVisible=' + Array.from(reaches).every(c => c.offsetParent !== null));
		const cve = body.querySelector('.cat-security .content-item .cve');
		log('cveChip=' + (cve ? cve.textContent.trim() : 'none'));
		const secondTag = body.querySelector('.cat-breaking .item-chip.tag');
		log('secondTag=' + (secondTag ? secondTag.textContent.trim() : 'none'));
		// The breaking tag itself is implied by its heading and NOT chipped.
		const breakingChips = Array.from(body.querySelectorAll('.cat-breaking .item-chip.tag'))
			.map(c => c.textContent.trim());
		log('breakingChips=' + breakingChips.join('|'));
		const fold = body.querySelector('.item-fold');
		fold.querySelector('.item-fold-head').click();
		const unknown = fold.querySelector('.item-chip.unknown');
		log('unknownChip=' + (unknown ? unknown.textContent.trim() : 'none'));
		// A single-known-tag background item gets no tag chip at all.
		const perfItem = Array.from(fold.querySelectorAll('.content-item'))
			.find(i => i.textContent.includes('Startup'));
		log('perfChips=' + perfItem.querySelectorAll('.item-chip.tag').length);
""")
		self.assertEqual(out["reachesChips"], "3")
		self.assertEqual(out["reachesVisible"], "true")
		self.assertEqual(out["cveChip"], "CVE-2026-1111")
		self.assertEqual(out["secondTag"], "fix")
		self.assertEqual(out["breakingChips"], "fix",
			"the group-implied tag must be elided; the second tag must show")
		self.assertEqual(out["unknownChip"], "experimental")
		self.assertEqual(out["perfChips"], "0",
			"93%% of items carry one tag and must carry no chip")

	def test_the_cut_surfaces_are_gone_and_the_version_pair_folds_the_count(self):
		tool = decision_surface_tool()
		tool["release_inventory"] = [
			{"version": "1.5", "link": None}, {"version": "2.0", "link": None}]
		out = self.drive(page_report([tool]), """
		key('2');
		log('tierBadges=' + document.querySelectorAll('.severity-tier-badges').length);
		log('riSections=' + document.querySelectorAll('.release-inventory-list').length);
		const vd = document.querySelector('#tool-list .tool-header .version-delta');
		log('versionPair=' + vd.textContent.trim().replace(/\\s+/g, ' '));
		const deltaLabel = document.querySelector('#filter-bar #filter-delta').closest('label');
		const srcLabel = document.querySelector('#filter-bar #filter-source').closest('label');
		log('deltaControlHidden=' + (deltaLabel.offsetParent === null));
		log('sourceControlHidden=' + (srcLabel.offsetParent === null));
		// The filter STATE survives the control's removal: a tile still sets it.
		runOverviewAction('filter-delta', 'major');
		log('filterStillWorks=' + (document.getElementById('filter-delta').value === 'major'));
""")
		self.assertEqual(out["tierBadges"], "0")
		self.assertEqual(out["riSections"], "0")
		self.assertIn("(2)", out["versionPair"])
		self.assertEqual(out["deltaControlHidden"], "true")
		self.assertEqual(out["sourceControlHidden"], "true")
		self.assertEqual(out["filterStillWorks"], "true")



class LoudnessChannelTests(PageDriveRunner):
	"""D1's render half, D2's visibility half, D3's badge — each asserted
	VISIBLE in the rendered DOM (offsetParent), because "it is in REPORT"
	was the measured defect: 77 of 78 tools carried spec_violations and the
	rendered DOM contained zero finding codes; risk_level occurred only
	inside the embedded JSON; bucket_inputs was read 0 times."""

	def test_a_degraded_tool_is_loud_on_its_card_and_in_the_report_notes(self):
		tool = page_tool("brew:broken", "broken", "1.0", "1.1", "attention",
			sev_item="warning")
		tool["spec_violations"] = ["E-RESEARCH-UNKNOWNKEY", "W-TITLE-LONG"]
		tool["degradation"] = {"content_losing": ["unknown-key"],
			"markers": ["W-TITLE-LONG"], "quarantined": 1}
		tool["risk_level"] = "elevated"
		out = self.drive(page_report([tool]), """
		key('2');
		const strip = document.querySelector('#tool-list .degrade-strip');
		log('strip=' + (strip ? strip.dataset.lost : 'none'));
		log('stripVisible=' + (strip && strip.offsetParent !== null));
		log('stripText=' + strip.textContent.replace(/\\s+/g, ' ').trim());
		const marker = strip.querySelector('.marker-chip');
		log('marker=' + (marker ? marker.textContent : 'none'));
		const badge = document.querySelector('#tool-list .tool-header .spec-badge');
		log('badge=' + (badge ? badge.textContent : 'none'));
		log('badgeVisible=' + (badge && badge.offsetParent !== null));
		badge.click();
		log('tabAfter=' + activeTab);
		const band = document.getElementById('band-notes');
		log('bandOpen=' + band.dataset.open);
		const rows = Array.from(band.querySelectorAll('.note-row .h')).map(h => h.textContent);
		log('rows=' + rows.join('|'));
		const meta = Array.from(band.querySelectorAll('.note-row .m')).map(m => m.textContent).join(' ');
		log('metaHasTool=' + meta.includes('brew:broken'));
""")
		self.assertEqual(out["strip"], "1")
		self.assertEqual(out["stripVisible"], "true")
		self.assertIn("content lost", out["stripText"])
		self.assertIn("unknown-key", out["stripText"])
		self.assertIn("1 quarantined", out["stripText"])
		self.assertEqual(out["marker"], "W-TITLE-LONG")
		self.assertEqual(out["badge"], "out of spec")
		self.assertEqual(out["badgeVisible"], "true")
		self.assertEqual(out["tabAfter"], "overview")
		self.assertEqual(out["bandOpen"], "1")
		self.assertIn("E-RESEARCH-UNKNOWNKEY on 1 tool", out["rows"])
		self.assertIn("W-TITLE-LONG on 1 tool", out["rows"])
		self.assertIn("1 tool lost content at validation", out["rows"])
		self.assertEqual(out["metaHasTool"], "true")

	def test_risk_and_the_pre_acceptance_bars_are_in_the_dom(self):
		tool = page_tool("brew:held", "held", "1.0", "1.1", "security_mixed")
		tool["risk_level"] = "elevated"
		tool["pre_accept_bars"] = ["elevated-risk", "reaches-item"]
		tool["bucket_inputs"] = {"has_security": True, "security_only": True,
			"impact": "possible", "version_delta": "minor", "runnable": True}
		out = self.drive(page_report([tool]), """
		key('2');
		const s = document.querySelector('#tool-list .tool-section');
		log('dataRisk=' + s.dataset.risk);
		const rb = s.querySelector('.risk-badge');
		log('riskBadge=' + (rb ? rb.textContent : 'none'));
		log('riskBadgeVisible=' + (rb && rb.offsetParent !== null));
		const why = s.querySelector('.tool-why');
		log('whyVisible=' + (why && why.offsetParent !== null));
		log('why=' + why.textContent.replace(/\\s+/g, ' ').trim());
""")
		self.assertEqual(out["dataRisk"], "elevated")
		self.assertEqual(out["riskBadge"], "elevated risk")
		self.assertEqual(out["riskBadgeVisible"], "true")
		self.assertEqual(out["whyVisible"], "true")
		self.assertIn("bucket security_mixed", out["why"])
		self.assertIn("security only", out["why"])
		self.assertIn("impact possible", out["why"])
		self.assertIn("not pre-accepted: elevated risk; a security item reaches this machine",
			out["why"])

	def test_the_watch_badge_reads_the_grounded_export_never_the_raw_claim(self):
		"""Four sites name watch_hit_item_ids — the validator's GROUNDED
		export — as the field the badge reads. item.watch_hit is the raw
		checker claim; wiring the badge to it reopens the channel pass 1
		closed. Two items both CLAIM a hit; only one is grounded."""
		tool = page_tool("brew:watched", "watched", "1.0", "1.1", "attention",
			sev_item="warning")
		tool["items"] = [
			{"id": "brew:watched#a", "title": "Grounded hit", "tags": ["fix"],
				"severity": "warning", "watch_hit": {"topic": "credential format"},
				"local": {"direction": "reaches", "effect": "risk", "statement": "s",
					"evidence": [], "citations": []}},
			{"id": "brew:watched#b", "title": "Raw claim only", "tags": ["fix"],
				"severity": "warning", "watch_hit": {"topic": "made-up topic"},
				"local": {"direction": "reaches", "effect": "risk", "statement": "s",
					"evidence": [], "citations": []}},
		]
		tool["watch_hit_item_ids"] = ["brew:watched#a"]
		unchecked = page_tool("brew:unchecked", "unchecked", "1.0", "1.1", "routine")
		unchecked["spec_violations"] = ["W-WATCH-UNCHECKED"]
		unchecked["degradation"] = {"content_losing": [], "markers": ["W-WATCH-UNCHECKED"],
			"quarantined": 0}
		out = self.drive(page_report([tool, unchecked]), """
		key('2');
		setAllCollapsed(false);
		const chips = document.querySelectorAll('#tool-list .item-chip.watch');
		log('chips=' + chips.length);
		const owner = chips[0].closest('.content-item').querySelector('.ci-text').textContent;
		log('owner=' + owner);
		log('chipVisible=' + (chips[0].offsetParent !== null));
		log('chipTitle=' + chips[0].title);
		const ws = document.querySelector('[data-tool-id="brew:unchecked"] .watch-state');
		log('unchecked=' + (ws ? ws.textContent : 'none'));
		log('uncheckedVisible=' + (ws && ws.offsetParent !== null));
""")
		self.assertEqual(out["chips"], "1",
			"the raw item.watch_hit claim must not earn a badge")
		self.assertEqual(out["owner"], "Grounded hit")
		self.assertEqual(out["chipVisible"], "true")
		self.assertIn("credential format", out["chipTitle"])
		self.assertIn("not checked this run", out["unchecked"])
		self.assertEqual(out["uncheckedVisible"], "true")

	def test_the_report_notes_band_carries_convergence_flags_and_the_not_run_state(self):
		out = self.drive(page_report(six_tools()), """
		const band = document.getElementById('band-notes');
		log('collapsed=' + (band.dataset.open === '0'));
		log('rows=' + Array.from(band.querySelectorAll('.note-row .h')).map(h => h.textContent).join('|'));
""")
		self.assertEqual(out["collapsed"], "true")
		self.assertIn("Convergence did not run", out["rows"])

		report = page_report(six_tools(), convergence={"state": "converged", "attempt": 1,
			"status": None, "moved": {}, "applied_count": 3, "rejected_count": 0,
			"flags": [{"edit_id": "cv-009", "check": "C4-notable-security",
				"tool_id": "brew:alpha", "headline": "Rating basis is vendor prose",
				"body": "The high rating quotes the vendor, not an advisory."}],
			"findings": [{"code": "W-STORE-UNCHECKED", "critical": False,
				"detail": "method-notes.json was not snapshotted", "tool_id": None}]})
		out = self.drive(report, """
		const band = document.getElementById('band-notes');
		const rows = Array.from(band.querySelectorAll('.note-row'));
		log('heads=' + rows.map(r => r.querySelector('.h').textContent).join('|'));
		log('metas=' + rows.map(r => (r.querySelector('.m') || {}).textContent || '').join('|'));
		log('sub=' + document.querySelector('#notes-section h2 .sub').textContent);
""")
		self.assertIn("Rating basis is vendor prose", out["heads"])
		self.assertIn("W-STORE-UNCHECKED", out["heads"])
		self.assertIn("C4-notable-security · cv-009 · brew:alpha", out["metas"])
		self.assertIn("nothing to decide", out["sub"])



def judged_tool(tid, name, source="judgement", **over):
	"""A security_auto, pre-accepted tool with a convergence block whose
	auto_update_label has the given source (references/convergence.md §9)."""
	tool = page_tool(tid, name, "18.4", "18.6", "security_auto", pre=True, tags=("security",))
	tool["security"] = {"cve_ids": ["CVE-2026-2222"], "cve_count": 1,
		"cve_claimed_count": 28, "has_security": True, "security_only": True,
		"impact": "none", "severity_counts": {"critical": 0, "high": 1, "medium": 0,
			"low": 0, "unknown": 0}, "display_item_ids": []}
	tool["bucket_inputs"] = {"has_security": True, "security_only": True,
		"impact": "none", "version_delta": "minor", "runnable": True}
	tool["convergence"] = {
		"touched": True, "edit_ids": ["cv-021"],
		"bucket": {"from": "security_mixed", "to": "security_auto",
			"direction": "permissive", "attributed_to": ["cv-021"],
			"unattributed": source == "judgement_unattributed"},
		"auto_update_label": {
			"source": source,
			"headline": "Cadence note re-rated to chore — this is what makes it security-only.",
			"reasoning": "The pulled-release note is bookkeeping about cadence, not a change; no reviewer who believed the opposite would hold a 10-CVE upgrade over it.",
			"confidence": "medium", "edit_ids": ["cv-021"],
			"quotes": [{"edit_id": "cv-021",
				"text": "PostgreSQL 18.5 was never shipped; the 18.5 release was pulled"}],
			"counterweight": {"cve_count": 10, "worst_rating": "high",
				"items_removed": 1, "items_retagged": 0},
		},
	}
	tool.update(over)
	return tool


def rule_tool(tid, name):
	tool = page_tool(tid, name, "1.0", "1.0.1", "security_auto", pre=True, tags=("security",))
	tool["security"]["has_security"] = True
	tool["security"]["security_only"] = True
	tool["security"]["impact"] = "none"
	tool["convergence"] = {"touched": False, "edit_ids": [],
		"auto_update_label": {"source": "rule",
			"headline": "Auto by rule — the deterministic path alone put it here.",
			"reasoning": "Reached security_auto with zero attributed convergence edits.",
			"confidence": "high", "edit_ids": [], "quotes": [],
			"counterweight": {"cve_count": 0, "worst_rating": "unknown",
				"items_removed": 0, "items_retagged": 0}}}
	return tool


def converged_report(tools, **over):
	conv = {"state": "converged", "attempt": 1, "status": {"attempts": 1,
		"state": "converged", "explanation": {"headline": "Converged at attempt 1.",
			"body": "All edits applied.", "attempt_log": []},
		"degraded_tools": [], "standing_rejects": []},
		"moved": {}, "findings": [], "flags": [], "applied_count": 1, "rejected_count": 0}
	conv.update(over)
	return page_report(tools, convergence=conv)


class JudgementPanelTests(PageDriveRunner):
	"""report-page.md §3 — the auto-update label is STRUCTURAL: a
	judgement-moved tool leaves the collapsed auto strip for an always-open
	panel with four lines (identity + CVE + mirror; the headline; the
	verbatim cut; from → to beside the counterweight), and Reject is the
	affordance. The failure it prevents: brew:libpq pre-accepted with 10
	CVEs inside a closed strip nobody opened."""

	def test_a_judged_tool_leaves_the_strip_for_the_panel_with_all_four_lines(self):
		tools = [judged_tool("brew:libpq", "libpq"), rule_tool("brew:ruled", "ruled")]
		out = self.drive(converged_report(tools), """
		const panel = document.getElementById('jpanel');
		log('panel=' + !!panel);
		log('panelVisible=' + (panel && panel.offsetParent !== null));
		const rows = panel.querySelectorAll('.jrow');
		log('rows=' + rows.length + ':' + rows[0].dataset.tool);
		const row = rows[0];
		log('l2=' + row.querySelector('.l2').textContent);
		log('l3=' + row.querySelector('.l3').textContent.replace(/\\s+/g, ' ').trim());
		log('l4=' + row.querySelector('.l4').textContent.replace(/\\s+/g, ' ').trim());
		log('cve=' + row.querySelector('.cve-badge').textContent);
		const acceptBtn = row.querySelector('.mirror [data-action="accept"]');
		log('acceptOn=' + acceptBtn.dataset.on);
		log('reasonHidden=' + (row.querySelector('.reason').offsetParent === null));
		row.querySelector('[data-toggle-reason]').click();
		log('reasonShown=' + (row.querySelector('.reason').offsetParent !== null));
		log('reasonFoot=' + row.querySelector('.reason-foot').textContent);
		// The strip: judged tool gone, rule tool present, heading says by rule.
		const strip = document.getElementById('sec-auto');
		log('stripNames=' + Array.from(strip.querySelectorAll('.autorow .nm')).map(n => n.textContent).join(','));
		log('stripHead=' + strip.querySelector('.autostrip-head .t').textContent.trim());
		log('ruleRowTitle=' + strip.querySelector('.autorow').title);
		// The tile still counts the whole bucket.
		log('foot=' + panel.querySelector('.jpanel-foot').textContent.trim());
		// Inline copies on the card.
		key('2');
		const s = document.querySelector('[data-tool-id="brew:libpq"]');
		log('badge=' + s.querySelector('.tool-header .judge-badge').textContent);
		log('ruleBadge=' + document.querySelector('[data-tool-id="brew:ruled"] .judge-badge').textContent);
		s.classList.remove('collapsed');
		const line = s.querySelector('.judge-line');
		log('lineVisible=' + (line.offsetParent !== null));
		log('line=' + line.textContent.replace(/\\s+/g, ' ').trim());
""")
		self.assertEqual(out["panel"], "true")
		self.assertEqual(out["panelVisible"], "true")
		self.assertEqual(out["rows"], "1:brew:libpq")
		self.assertIn("Cadence note re-rated to chore", out["l2"])
		self.assertIn("CUT", out["l3"])
		self.assertIn("18.5 release was pulled", out["l3"])
		self.assertIn("security_mixed → security_auto", out["l4"])
		self.assertIn("against: 10 CVEs · worst high · 1 item removed", out["l4"])
		self.assertIn("1 of 28 CVE", out["cve"])
		self.assertEqual(out["acceptOn"], "1", "the mirror must show accept already on")
		self.assertEqual(out["reasonHidden"], "true")
		self.assertEqual(out["reasonShown"], "true")
		self.assertIn("confidence medium · cv-021 in converge.json", out["reasonFoot"])
		self.assertEqual(out["stripNames"], "ruled",
			"the judged tool must LEAVE the collapsed strip")
		self.assertIn("auto-accepted by rule", out["stripHead"])
		self.assertIn("Auto by rule", out["ruleRowTitle"])
		self.assertIn("Reject one to take it back", out["foot"])
		self.assertEqual(out["badge"], "⚑ auto by judgement")
		self.assertEqual(out["ruleBadge"], "auto by rule")
		self.assertEqual(out["lineVisible"], "true")
		self.assertIn("review in the judgement panel", out["line"])

	def test_r_on_a_judgement_row_rejects_the_pre_accepted_upgrade(self):
		"""§9.1's one exception: the row has no undecided suggestion, so
		"first undecided" finds nothing — on this row only, the keys act on
		the first mirror. Found by driving the prototype; pinned here."""
		out = self.drive(converged_report([judged_tool("brew:libpq", "libpq"),
				page_tool("brew:other", "other", "1.0", "1.1", "attention", sev_item="warning")]), """
		key('1');
		key('j');
		const ring = document.querySelector('#panel-overview [data-focused]');
		log('ring=' + (ring ? ring.className + ':' + ring.dataset.tool : 'none'));
		key('r');
		const card = canonicalCard('brew:libpq:upgrade');
		log('decision=' + card.dataset.decision);
		const rowBtn = document.querySelector('.jrow .mirror [data-action="reject"]');
		log('mirrorOn=' + rowBtn.dataset.on);
""")
		self.assertEqual(out["ring"], "jrow:brew:libpq")
		self.assertEqual(out["decision"], "reject")
		self.assertEqual(out["mirrorOn"], "1")

	def test_an_unattributed_move_renders_red_with_the_defect_named(self):
		out = self.drive(converged_report([judged_tool("brew:odd", "odd",
				source="judgement_unattributed")]), """
		const row = document.querySelector('.jrow');
		log('unattributed=' + row.dataset.unattributed);
		log('l2=' + row.querySelector('.l2').textContent);
		log('bg=' + getComputedStyle(row).backgroundColor);
""")
		self.assertEqual(out["unattributed"], "1")
		self.assertEqual(out["l2"], "moved to auto-update, cause not attributable")
		self.assertNotEqual(out["bg"], "rgba(0, 0, 0, 0)")

	def test_a_degraded_gate_run_is_first_class_and_the_forced_tool_starts_undecided(self):
		forced = {"forced_bucket": "security_mixed", "forced_pre_accept": False,
			"would_have_been": "security_auto", "code": "E-GATE-UNREASONED"}
		tool = page_tool("brew:forced", "forced", "1.0", "1.1", "security_mixed",
			tags=("security",))
		tool["security"]["has_security"] = True
		tool["convergence"] = {"touched": True, "edit_ids": ["cv-003"], "forced": forced}
		report = converged_report([tool, rule_tool("brew:ruled", "ruled")],
			state="degraded_gate", attempt=5,
			status={"attempts": 5, "state": "degraded_gate",
				"explanation": {"headline": "1 tool(s) reached auto-update without surviving the gate and were forced to security_mixed.",
					"body": "After 5 attempts the gate still failed on: brew:forced.",
					"attempt_log": [{"attempt": 1, "codes": {"E-GATE-UNREASONED": 1}, "state": "rejected"},
						{"attempt": 5, "codes": {}, "state": "degraded_gate"}]},
				"degraded_tools": [dict(forced, tool_id="brew:forced")],
				"standing_rejects": [{"edit_id": "cv-009", "code": "E-EDIT-OP"}]})
		out = self.drive(report, """
		const strip = document.getElementById('degraded-strip');
		log('strip=' + !!strip);
		log('tone=' + strip.dataset.tone);
		log('stripVisible=' + (strip.offsetParent !== null));
		log('h=' + strip.querySelector('.h').textContent);
		log('b=' + strip.querySelector('.b').textContent);
		log('m=' + strip.querySelector('.m').textContent.replace(/\\s+/g, ' ').trim());
		log('panel=' + !!document.getElementById('jpanel'));
		log('panelRows=' + document.querySelectorAll('.jrow').length);
		log('stripNames=' + Array.from(document.querySelectorAll('#sec-auto .autorow .nm')).map(n => n.textContent).join(','));
		key('2');
		const s = document.querySelector('[data-tool-id="brew:forced"]');
		log('badge=' + s.querySelector('.judge-badge').textContent);
		s.classList.remove('collapsed');
		log('line=' + s.querySelector('.judge-line').textContent.replace(/\\s+/g, ' ').trim());
		log('decision=' + canonicalCard('brew:forced:upgrade').dataset.decision);
""")
		self.assertEqual(out["strip"], "true")
		self.assertEqual(out["tone"], "yellow")
		self.assertEqual(out["stripVisible"], "true")
		self.assertIn("Convergence degraded (degraded_gate)", out["h"])
		self.assertIn("forced to security_mixed", out["h"])
		self.assertIn("After 5 attempts", out["b"])
		self.assertIn("5 attempts", out["m"])
		self.assertIn("#1 rejected (E-GATE-UNREASONED)", out["m"])
		self.assertIn("1 standing reject", out["m"])
		self.assertIn("forced: forced security_mixed (would have been security_auto)", out["m"])
		# A forced tool carries no label: not in the panel, not in the strip.
		self.assertEqual(out["panel"], "false")
		self.assertEqual(out["panelRows"], "0")
		self.assertEqual(out["stripNames"], "ruled")
		self.assertEqual(out["badge"], "forced conservative")
		self.assertIn("E-GATE-UNREASONED", out["line"])
		self.assertIn("starts undecided", out["line"])
		self.assertEqual(out["decision"], "")


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
