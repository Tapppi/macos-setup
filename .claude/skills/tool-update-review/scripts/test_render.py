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
	def render(self, report, as_bytes=None, path_override=None, state_home=None,
			report_dir=None):
		"""Run render.py against a throwaway report dir; returns
		(CompletedProcess, report_dir). The dir outlives the call so a test
		can assert what was — or was not — written into it.

		Every run gets its OWN XDG_STATE_HOME (a throwaway, unless the test
		passes one to share across two renders), so render-time persistence
		can never touch the machine's real method-note store from a test."""
		if report_dir is None:
			report_dir = tempfile.mkdtemp(prefix="render-test-")
			self.addCleanup(__import__("shutil").rmtree, report_dir, True)
		if state_home is None:
			state_home = tempfile.mkdtemp(prefix="render-test-state-")
			self.addCleanup(__import__("shutil").rmtree, state_home, True)
		report_path = os.path.join(report_dir, "report.json")
		if as_bytes is not None:
			with open(report_path, "wb") as fh:
				fh.write(as_bytes)
		else:
			with open(report_path, "w", encoding="utf-8") as fh:
				json.dump(report, fh)
		env = dict(os.environ, XDG_STATE_HOME=state_home)
		p = subprocess.run([sys.executable, RENDER_PY,
			path_override or report_path],
			capture_output=True, text=True, timeout=60, env=env)
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
		code (deferred from pass 1; no shim ships). A string spelling
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

	def test_a_contract_4_report_cannot_feed_the_page_an_old_breaking_reason(self):
		"""Under contract 4 `fix-with-breaking` meant ANY breaking item; the
		page now reads it as "reaches this machine" (breakingReachOf,
		p2ReachRank, and a forced row's stored `forced_display.reasons`). The
		code kept its spelling, so only the version can tell the two apart:
		a contract-4 report carrying that reason — on the tier and on the
		forced-display snapshot — is refused, while the same tool under the
		current contract renders."""
		self.assertGreater(items.CONTRACT_VERSION, 4,
			"fix-with-breaking changed meaning in contract 5; the gate must not accept 4")
		tier = {"priority": "P2", "tier": "P2", "reasons": ["fix-with-breaking", "fix"],
			"holds": [], "ids": {"fix-with-breaking": ["brew:curl:x"], "fix": ["brew:curl:y"]}}
		tool = {"id": "brew:curl", "name": "curl", "source": "brew",
			"security_tier": tier,
			"forced_conservative": {"forced_display": {"priority": "P2",
				"reasons": ["fix-with-breaking", "fix"]}}}
		p = self.refuse(minimal_report(contract_version=4, tools=[tool]))
		self.assertIn(f"contract_version must be {items.CONTRACT_VERSION}, got 4",
			p.stderr)
		p, _ = self.render(minimal_report(tools=[tool]))
		self.assertEqual(p.returncode, 0, p.stderr)

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

	def drive(self, report, scenario_js, budget=6000, state_home=None, report_dir=None):
		p, report_dir = self.render(report, state_home=state_home, report_dir=report_dir)
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
	"""A report-page bug, measured at 577.98px: scrollIntoView clamps at max
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
	"""One tool exercising all four item destinations
	(rendering-report.md §Content Groups) plus every chip kind (§Chips)."""
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
		# Review A10: the head no longer claims nothing in it reaches.
		self.assertIn("nothing to decide here", out["foldLabel"])
		self.assertNotIn("do not reach", out["foldLabel"])
		self.assertNotIn("reach this setup", out["foldLabel"])
		# Review A14: the security head names every criterion of its bar.
		self.assertIn("exploited in the wild", groups[0])
		self.assertIn("warning or worse", groups[0])
		self.assertEqual(out["foldCount"], "2")
		self.assertEqual(out["foldHiddenByDefault"], "true")
		self.assertEqual(out["foldVisibleAfterClick"], "true")
		self.assertEqual(out["foldTitles"], "Startup is 2x faster|Something oddly tagged")

	def test_the_fold_head_counts_what_reaches(self):
		"""Review A10: a notable fix that reaches but is not decisive folds;
		the head must say it reaches rather than claim nothing does."""
		tool = decision_surface_tool()
		tool["items"].append({"id": "brew:surface#release:2.0/lands",
			"title": "A small fix that lands here", "tags": ["fix"], "severity": "notable",
			"local": {"direction": "reaches", "effect": "benefit",
				"statement": "s", "evidence": [], "citations": []}})
		out = self.drive(page_report([tool]), """
		key('2');
		const head = document.querySelector('#tool-list .tool-section .item-fold-head');
		log('foldLabel=' + head.textContent.trim().replace(/\\s+/g, ' '));
""")
		self.assertIn("Everything else (3)", out["foldLabel"])
		self.assertIn("(1 of them reach this setup)", out["foldLabel"])

	def test_the_reaches_group_holds_only_what_reaches(self):
		"""Pass 6: brew:libpq's card listed CVE-2026-15741, CVE-2026-16241 and
		slug:server-side-cves — all does_not_reach — under "Other changes
		that reach this machine", because the `security` tag alone made them
		decisive. A decisive non-reaching item stays visible only when it is
		decisive for its rating or a watch hit; by its tag alone it folds."""
		tool = page_tool("brew:libpq", "libpq", "18.5", "18.6", "security_auto")
		def item(slug, tags, severity, direction, security=None):
			out = {"id": "brew:libpq#slug:" + slug, "title": slug, "tags": tags,
				"severity": severity, "local": {"direction": direction, "effect": "none",
					"statement": "s", "evidence": [], "citations": []}}
			if security:
				out["security"] = security
			return out
		sec = {"cve_id": None, "rating": "medium", "rating_basis": "nvd",
			"exploited_in_wild": False, "nature": "fix"}
		tool["items"] = [
			item("server-side-cves", ["security", "fix"], "notable", "does_not_reach", sec),
			item("client-cve", ["security", "fix"], "notable", "reaches", sec),
			item("psql-risk", ["feature"], "notable", "reaches"),
			item("warn-unclear", ["feature"], "warning", "unclear"),
			item("info-reaches", ["feature"], "info", "reaches"),
		]
		tool["items"][2]["local"]["effect"] = "risk"
		tool["security"] = {"cve_ids": [], "cve_count": 0, "cve_claimed_count": None,
			"has_security": True, "security_only": False, "impact": "possible",
			"severity_counts": None, "display_item_ids": ["brew:libpq#slug:client-cve"]}
		out = self.drive(page_report([tool]), """
		key('2');
		const body = document.querySelector('#tool-list .tool-section .tool-body');
		const titles = sel => Array.from(body.querySelectorAll(sel + ' .content-item .ci-text'))
			.map(i => i.textContent.trim()).join('|');
		log('security=' + titles('.cat-security'));
		log('reaches=' + titles('.cat-reaches'));
		log('other=' + titles('.cat-other'));
		log('otherHead=' + (body.querySelector('.cat-other .content-group-title') || {textContent: 'none'}).textContent.trim().replace(/\\s+/g, ' '));
		log('fold=' + titles('.item-fold'));
""")
		self.assertEqual(out["security"], "client-cve")
		self.assertEqual(out["reaches"], "psql-risk")
		self.assertEqual(out["other"], "warn-unclear")
		self.assertIn("not shown to reach this machine", out["otherHead"])
		self.assertEqual(sorted(out["fold"].split("|")), ["info-reaches", "server-side-cves"])

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



class CommaVersionTests(PageDriveRunner):
	"""Pass 6: cask:claude (`1.22209.3,babe1157…` → `2.9939.4,a166d8a7…`),
	cask:cursor and cask:datagrip ran past the version pair's ellipsis, so the
	target version — the one part a reader needs — was the part cut off."""

	def test_the_target_version_is_shown_and_the_full_pair_kept_in_the_title(self):
		claude = page_tool("cask:claude", "claude",
			"1.22209.3,babe11577dfefe3e209c06bd674628d862f0dbae",
			"2.9939.4,a166d8a7c640e65ad825ebfb99d74ccbb9c8940d", "security_mixed")
		datagrip = page_tool("cask:datagrip", "datagrip", "2026.2,262.8665.272",
			"2026.2.5,262.10315.132", "security_mixed")
		rebuild = page_tool("cask:rebuilt", "rebuilt", "5.7.3,2320", "5.7.3,2349abcdef01",
			"security_mixed")
		# Review A11: builds sharing their first 8 characters must still differ.
		stamped = page_tool("cask:stamped", "stamped", "5.7.3,20260901a1", "5.7.3,20260901b2",
			"security_mixed")
		for tool in (claude, datagrip, rebuild, stamped):
			tool["cask_sudo_hint"] = False
			tool["source"] = "cask"
		out = self.drive(page_report([claude, datagrip, rebuild, stamped]), """
		key('2');
		['cask:claude', 'cask:datagrip', 'cask:rebuilt', 'cask:stamped'].forEach(id => {
			const vd = document.querySelector('#tool-list .tool-section[data-tool-id="' + id + '"] .tool-header .version-delta');
			const nv = vd.querySelector('.v-new').getBoundingClientRect(), box = vd.getBoundingClientRect();
			log('shown:' + id + '=' + vd.textContent.trim().replace(/\\s+/g, ' '));
			log('title:' + id + '=' + vd.title);
			log('fits:' + id + '=' + (nv.right <= box.right + 0.5));
		});
""")
		self.assertEqual(out["shown:cask:claude"], "1.22209.3 → 2.9939.4")
		self.assertEqual(out["title:cask:claude"],
			"1.22209.3,babe11577dfefe3e209c06bd674628d862f0dbae → "
			"2.9939.4,a166d8a7c640e65ad825ebfb99d74ccbb9c8940d")
		self.assertEqual(out["shown:cask:datagrip"], "2026.2 → 2026.2.5")
		# one version rebuilt: the build IS the change, shortened
		self.assertEqual(out["shown:cask:rebuilt"], "5.7.3,2320 → 5.7.3,2349abcd…")
		self.assertEqual(out["shown:cask:stamped"], "5.7.3,20260901a1 → 5.7.3,20260901b2")
		for tool in ("cask:claude", "cask:datagrip", "cask:rebuilt"):
			self.assertEqual(out["fits:" + tool], "true", tool)


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

	def test_a_replaced_research_entry_is_readable_on_its_card_and_escaped(self):
		"""The validator quarantines a replaced duplicate entry verbatim and
		holds the tool, but the page used to render only "1 quarantined
		entry" — so when the replaced entry carried the breaking change, the
		reviewer decided without it. It is now behind a fold on the card,
		naming its research file, as escaped text: checker output is shown,
		never interpreted."""
		tool = page_tool("brew:dup", "dup", "1.0", "1.1", "attention")
		earlier = {"id": "brew:dup", "links": [], "items": [{
			"title": "Removes the --legacy flag <img src=x onerror=\"window.PWNED=1\">",
			"tags": ["breaking"], "severity": "warning"}]}
		tool["quarantine"] = [{"field": "duplicate research entry (03-early.json)",
			"item_id": None, "value": earlier}]
		tool["spec_violations"] = ["W-ENTRY-DUPLICATE"]
		tool["degradation"] = {"content_losing": ["quarantined-content"],
			"markers": ["W-ENTRY-DUPLICATE"], "quarantined": 1}
		clean = page_tool("brew:clean", "clean", "1.0", "1.1", "routine")
		out = self.drive(page_report([tool, clean]), """
		key('2');
		const s = document.querySelector('#tool-list .tool-section[data-tool-id="brew:dup"]');
		const fold = s.querySelector('.quarantine-fold');
		log('fold=' + (fold ? fold.dataset.open : 'none'));
		const body = fold.querySelector('.item-fold-body');
		s.classList.remove('collapsed');  // two tools: only the first card opens on its own
		log('foldVisible=' + (fold.offsetParent !== null));
		log('hiddenBefore=' + (body.offsetParent === null));
		log('head=' + fold.querySelector('.item-fold-head').textContent.replace(/\\s+/g, ' ').trim());
		fold.querySelector('.item-fold-head').click();
		log('open=' + fold.dataset.open);
		log('visibleAfter=' + (body.offsetParent !== null));
		log('field=' + fold.querySelector('.quarantine-field').textContent);
		const pre = fold.querySelector('.quarantine-value');
		log('hasLegacy=' + pre.textContent.includes('Removes the --legacy flag'));
		log('hasTagText=' + pre.textContent.includes('<img src=x'));
		log('imgElements=' + fold.querySelectorAll('img').length);
		log('pwned=' + (window.PWNED === 1));
		const c = document.querySelector('#tool-list .tool-section[data-tool-id="brew:clean"]');
		log('cleanFold=' + c.querySelectorAll('.quarantine-fold').length);
""")
		self.assertEqual(out["fold"], "0")
		self.assertEqual(out["foldVisible"], "true")
		self.assertEqual(out["hiddenBefore"], "true")
		self.assertIn("Quarantined content (1)", out["head"])
		self.assertIn("a research entry a later one replaced", out["head"])
		self.assertEqual(out["open"], "1")
		self.assertEqual(out["visibleAfter"], "true")
		self.assertEqual(out["field"], "duplicate research entry (03-early.json)")
		self.assertEqual(out["hasLegacy"], "true")
		self.assertEqual(out["hasTagText"], "true")
		self.assertEqual(out["imgElements"], "0")
		self.assertEqual(out["pwned"], "false")
		self.assertEqual(out["cleanFold"], "0")

	def test_a_warning_alone_is_a_note_not_out_of_spec(self):
		"""Pass 6: 20 tools read "out of spec" for W-SEC-FIX-NOID alone — a
		fix whose maintainer files no CVE, which the code's own text calls
		legitimate. Only an error-severity code earns the badge."""
		noid = page_tool("brew:libpq", "libpq", "18.5", "18.6", "security_auto")
		noid["spec_violations"] = ["W-SEC-FIX-NOID"]
		noid["degradation"] = {"content_losing": [], "markers": ["W-SEC-FIX-NOID"],
			"quarantined": 0}
		mixed = page_tool("brew:mixed", "mixed", "1.0", "1.1", "security_mixed")
		mixed["spec_violations"] = ["E-ANCHOR-MALFORMED", "W-SEC-FIX-NOID"]
		mixed["degradation"] = {"content_losing": [],
			"markers": ["E-ANCHOR-MALFORMED", "W-SEC-FIX-NOID"], "quarantined": 0}
		out = self.drive(page_report([noid, mixed]), """
		key('2');
		['brew:libpq', 'brew:mixed'].forEach(id => {
			const s = document.querySelector('#tool-list .tool-section[data-tool-id="' + id + '"]');
			const badge = s.querySelector('.tool-header .spec-badge');
			log('badge:' + id + '=' + (badge ? badge.textContent + '|' + badge.title : 'none'));
			const strip = s.querySelector('.degrade-strip');
			log('lead:' + id + '=' + (strip ? strip.querySelector('.lead').textContent : 'none'));
			log('chips:' + id + '=' + Array.from(s.querySelectorAll('.marker-chip')).map(c => c.textContent).join(','));
		});
""")
		self.assertEqual(out["badge:brew:libpq"], "none")
		self.assertEqual(out["lead:brew:libpq"], "△ validator notes on this tool")
		self.assertEqual(out["chips:brew:libpq"], "W-SEC-FIX-NOID")
		badge, title = out["badge:brew:mixed"].split("|", 1)
		self.assertEqual(badge, "out of spec")
		self.assertIn("E-ANCHOR-MALFORMED", title)
		self.assertNotIn("W-SEC-FIX-NOID", title)
		self.assertEqual(out["lead:brew:mixed"], "△ validator findings on this tool")

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
	"""rendering-report.md §The Judgement Panel — the auto-update label is STRUCTURAL: a
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
		# G-SEC reworded the head (the strip is no longer "security-only")
		self.assertIn("accepted by rule", out["stripHead"])
		self.assertIn("Auto by rule", out["ruleRowTitle"])
		self.assertIn("Reject one to take it back", out["foot"])
		self.assertEqual(out["badge"], "⚑ auto by judgement")
		self.assertEqual(out["ruleBadge"], "auto by rule")
		self.assertEqual(out["lineVisible"], "true")
		self.assertIn("review in the judgement panel", out["line"])

	def test_r_on_a_judgement_row_rejects_the_pre_accepted_upgrade(self):
		"""The judgement panel's one exception (rendering-report.md §Keyboard Navigation): the row has no undecided suggestion, so
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

	def test_a_forced_tool_that_only_gained_acceptance_is_not_worded_as_auto_update(self):
		"""Pass 7 finding 8: the permissive gate also fires on a tool that
		only gained pre-acceptance outside security_auto. It was never going
		to be in the auto strip, so its line must not say it moved there."""
		forced = {"forced_bucket": "attention", "forced_pre_accept": False,
			"would_have_been": {"bucket": "routine", "pre_accept": True,
				"priority": None},
			"code": "E-GATE-UNDECLARED", "kind": "permissive"}
		tool = page_tool("brew:plain", "plain", "1.0", "1.1", "attention")
		tool["convergence"] = {"touched": True, "edit_ids": ["cv-001"], "forced": forced}
		report = converged_report([tool], state="degraded_gate", attempt=5,
			status={"attempts": 5, "state": "degraded_gate",
				"explanation": {"headline": "h", "body": "b", "attempt_log": []},
				"degraded_tools": [dict(forced, tool_id="brew:plain")],
				"standing_rejects": []})
		out = self.drive(report, """
		key('2');
		const s = document.querySelector('[data-tool-id="brew:plain"]');
		s.classList.remove('collapsed');
		log('line=' + s.querySelector('.judge-line').textContent.replace(/\\s+/g, ' ').trim());
""")
		self.assertIn("could not justify starting this tool accepted", out["line"])
		self.assertNotIn("auto-update", out["line"])
		self.assertIn("forced to attention instead of routine, accepted", out["line"])

	def test_a_degraded_gate_run_is_first_class_and_the_forced_tool_starts_undecided(self):
		forced = {"forced_bucket": "security_mixed", "forced_pre_accept": False,
			"would_have_been": {"bucket": "security_auto", "pre_accept": True,
				"priority": None},
			"code": "E-GATE-UNREASONED", "kind": "permissive"}
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
		self.assertIn("forced: forced security_mixed (would have been security_auto, "
			"accepted, priority not G-SEC)", out["m"])
		self.assertNotIn("[object Object]", out["m"] + out["line"])
		self.assertIn("could not justify this tool's move to auto-update", out["line"])
		self.assertIn("instead of security_auto, accepted, priority not G-SEC", out["line"])
		# A forced tool carries no label: not in the panel, not in the strip.
		self.assertEqual(out["panel"], "false")
		self.assertEqual(out["panelRows"], "0")
		self.assertEqual(out["stripNames"], "ruled")
		self.assertEqual(out["badge"], "forced conservative")
		self.assertIn("E-GATE-UNREASONED", out["line"])
		self.assertIn("starts undecided", out["line"])
		self.assertEqual(out["decision"], "")



class RenderPersistTests(RenderRunner):
	"""Method notes persist AT RENDER. The store fills
	from run one — an abandoned run included — and the page's reject /
	modify surface is what earns that. render.py owns the write; the
	record beside the page is what apply withdraws a rejected note from."""

	STORE = os.path.join("tool-update-review", items.METHOD_NOTES_STORE)

	def _note(self, tool_id, sid, topic, note):
		return {"id": sid, "kind": "method-note", "title": f"Method note: {topic}",
			"target_files": [], "command": None, "auto_runnable": False,
			"rationale": "it happened", "method_topic": topic, "method_note": note}

	def _report(self, **conv):
		a = page_tool("brew:a", "a", "1.0", "1.1", "attention")
		a["suggestions"].append(self._note("brew:a", "brew:a:method-1", "where the changelog lives", "read the tag"))
		a["suggestions"].append(self._note("brew:a", "brew:a:method-general", "tags beat release pages", "cite the tag"))
		b = page_tool("brew:b", "b", "1.0", "1.1", "routine")
		b["suggestions"].append(self._note("brew:b", "brew:b:method-1", "empty release body", "diff the range"))
		convergence = {"state": "converged", "memory": {
			"promoted_to_global": ["brew:a:method-general"],
			"rehomed_to_method_note": [], "restored": []}}
		convergence.update(conv)
		return page_report([a, b], convergence=convergence)

	def _store(self, state_home):
		with open(os.path.join(state_home, self.STORE), encoding="utf-8") as fh:
			return json.load(fh)

	def _record(self, report_dir):
		with open(os.path.join(report_dir, "method-notes.render.json"), encoding="utf-8") as fh:
			return json.load(fh)

	def test_every_surviving_note_is_written_and_a_promoted_one_goes_global(self):
		state_home = tempfile.mkdtemp(prefix="render-test-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		p, report_dir = self.render(self._report(), state_home=state_home)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertIn("method notes: 3 written, 0 already present, 0 failed", p.stderr)
		store = self._store(state_home)
		self.assertEqual([e["topic"] for e in store["brew:a"]], ["where the changelog lives"])
		self.assertEqual([e["topic"] for e in store["brew:b"]], ["empty release body"])
		# The ledger's promotion is honoured: the global store has its first
		# real-run writer.
		self.assertEqual([e["topic"] for e in store[items.GLOBAL_METHOD_NOTE_KEY]],
			["tags beat release pages"])
		self.assertNotIn("brew:a:method-general", json.dumps(store["brew:a"]))
		# Every entry has exactly the three pinned fields, written by the
		# store's own writer — never a hand-rolled edit.
		for key, entries in store.items():
			for e in entries:
				self.assertEqual(set(e), {"topic", "note", "added_at"}, key)
		record = self._record(report_dir)
		self.assertEqual(len(record["written"]), 3)
		self.assertEqual(record["already_present"], [])
		self.assertEqual(record["failed"], [])
		promoted = next(w for w in record["written"] if w["suggestion_id"] == "brew:a:method-general")
		self.assertEqual(promoted["key"], items.GLOBAL_METHOD_NOTE_KEY)
		self.assertEqual(promoted["tool_id"], "brew:a")

	def test_a_second_render_of_the_same_report_writes_nothing_new(self):
		"""Idempotent by content: re-rendering never duplicates."""
		state_home = tempfile.mkdtemp(prefix="render-test-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		self.render(self._report(), state_home=state_home)
		p, report_dir = self.render(self._report(), state_home=state_home)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertIn("0 written, 3 already present", p.stderr)
		store = self._store(state_home)
		self.assertEqual(len(store["brew:a"]), 1)
		self.assertEqual(len(store["brew:b"]), 1)
		self.assertEqual(len(store[items.GLOBAL_METHOD_NOTE_KEY]), 1)
		self.assertEqual(len(self._record(report_dir)["already_present"]), 3)

	def test_an_unreviewed_corpus_persists_nothing_and_says_why(self):
		"""Finding 4: under not_run / artefacts_inconsistent /
		degraded_unapplied the report carries raw proposals C6 never
		reviewed. The default when the reviewer did not review must not be
		"permanent": nothing is written, the record says unreviewed with
		the reason, and apply writes one only on an explicit accept."""
		for state in ("not_run", "artefacts_inconsistent", "degraded_unapplied"):
			with self.subTest(state):
				state_home = tempfile.mkdtemp(prefix="render-test-state-")
				self.addCleanup(__import__("shutil").rmtree, state_home, True)
				p, report_dir = self.render(self._report(state=state, memory=None),
					state_home=state_home)
				self.assertEqual(p.returncode, 0, p.stderr)
				self.assertIn("3 unreviewed — NOT persisted", p.stderr)
				self.assertFalse(os.path.exists(os.path.join(state_home, self.STORE)))
				record = self._record(report_dir)
				self.assertFalse(record["reviewed"])
				self.assertEqual(record["convergence_state"], state)
				self.assertEqual(record["written"], [])
				self.assertEqual(len(record["unreviewed"]), 3)
				self.assertIn(state, record["unreviewed"][0]["reason"])

	def test_a_degraded_gate_run_still_counts_as_reviewed(self):
		"""degraded_gate applied the submission (C6 included) and only
		forced the gate-failing tools; the notes were reviewed."""
		state_home = tempfile.mkdtemp(prefix="render-test-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		p, report_dir = self.render(self._report(state="degraded_gate"), state_home=state_home)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertTrue(self._record(report_dir)["reviewed"])
		self.assertEqual(len(self._store(state_home)["brew:a"]), 1)

	def test_a_refused_write_never_costs_the_page(self):
		"""An unreadable store is a refusal at the writer; render records
		the failure, says so, and still writes index.html — never a dead
		run, never a silent success."""
		state_home = tempfile.mkdtemp(prefix="render-test-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		path = os.path.join(state_home, self.STORE)
		os.makedirs(os.path.dirname(path))
		with open(path, "w", encoding="utf-8") as fh:
			fh.write('{"brew:a": [{"topic": "t",')
		p, report_dir = self.render(self._report(), state_home=state_home)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertTrue(os.path.exists(os.path.join(report_dir, "index.html")))
		self.assertIn("3 failed", p.stderr)
		record = self._record(report_dir)
		self.assertEqual(len(record["failed"]), 3)
		self.assertIn("could not be read", record["store_problem"])
		with open(path, encoding="utf-8") as fh:
			self.assertEqual(fh.read(), '{"brew:a": [{"topic": "t",')

	def test_a_report_with_no_method_notes_writes_no_store_and_no_noise(self):
		state_home = tempfile.mkdtemp(prefix="render-test-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		p, report_dir = self.render(page_report(six_tools()), state_home=state_home)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertNotIn("method notes:", p.stderr)
		self.assertFalse(os.path.exists(os.path.join(state_home, self.STORE)))
		self.assertEqual(self._record(report_dir)["written"], [])



def notes_report():
	"""Four tools, five method notes: one topic shared by three tools, two
	one-off topics (one re-homed, one promoted to global, one self-test
	tagged), so the grouping, the marks and the mirrors all show."""
	def note(tid, sid, topic, text, **extra):
		s = {"id": sid, "kind": "method-note", "title": f"Method note: {topic}",
			"target_files": [], "command": None, "auto_runnable": False,
			"rationale": "it happened", "method_topic": topic, "method_note": text}
		s.update(extra)
		return s
	shared = "changelog is cumulative — scope to the version pair"
	a = page_tool("brew:aa", "aa", "1.0", "1.1", "routine")
	b = page_tool("brew:bb", "bb", "1.0", "1.1", "routine")
	c = page_tool("brew:cc", "cc", "1.0", "1.1", "attention", sev_item="incompatible")
	d = page_tool("brew:nnn", "nnn", "1.0", "1.1", "routine")
	for t in (a, b, c):
		t["suggestions"].append(note(t["id"], f"{t['id']}:method-cumulative", shared,
			"Read only the entries between the two versions."))
	d["suggestions"].append(note("brew:nnn", "brew:nnn:method-readline", "readline linking in homebrew-core's nnn formula",
		"Check the formula's readline dependency before trusting the release notes.",
		self_test_failed={"limb": "unwitnessed", "reason": "predicts rather than names"}))
	c["suggestions"].append(note("brew:cc", "brew:cc:method-general", "tags beat release pages",
		"Cite the tag, not the announcement."))
	conv = {"state": "converged", "memory": {
		"promoted_to_global": ["brew:cc:method-general"],
		"rehomed_to_method_note": [{"suggestion_id": "brew:nnn:watch-x",
			"new_note_id": "brew:nnn:method-readline", "scope": "tool"}],
		"restored": ["brew:nnn:method-readline"]}}
	return page_report([a, b, c, d], convergence=conv)


class MethodNotesTabTests(PageDriveRunner):
	"""The review surface render-time persistence depends on. The notes are already in
	the store (render persisted them); the tab is how a bad one is caught.
	Veto is a mirror of reject on the canonical card, modification
	instructions write through to its comment, and a method note never
	counts as a decision anywhere."""

	def test_the_tab_groups_by_topic_with_one_offs_first_and_open(self):
		out = self.drive(notes_report(), """
		log('tabs=' + Array.from(document.querySelectorAll('.tab-btn')).map(b => b.dataset.tab).join(','));
		log('count=' + document.getElementById('tab-notes-count').textContent);
		log('countInk=' + getComputedStyle(document.getElementById('tab-notes-count')).color);
		key('3');
		log('active=' + activeTab);
		const lede = document.getElementById('notes-lede');
		log('lede=' + lede.textContent.replace(/\\s+/g, ' ').trim().slice(0, 60));
		const oneoff = document.getElementById('notes-oneoff');
		const shared = document.getElementById('notes-shared');
		log('oneoffOpen=' + oneoff.dataset.open + ' sharedOpen=' + shared.dataset.open);
		log('oneoffSub=' + oneoff.querySelector('.band-head .sub').textContent);
		log('sharedSub=' + shared.querySelector('.band-head .sub').textContent);
		log('oneoffRows=' + Array.from(oneoff.querySelectorAll('.nrow')).map(r => r.dataset.note).join(','));
		log('sharedRows=' + shared.querySelectorAll('.nrow').length);
		log('sharedTools=' + shared.querySelectorAll('.ntool').length);
		log('marks=' + Array.from(oneoff.querySelectorAll('.mark')).map(m => m.className.replace('mark ', '')).join(','));
""")
		self.assertEqual(out["tabs"], "overview,tools,notes")
		self.assertEqual(out["count"], "5")
		self.assertEqual(out["countInk"], "rgb(88, 110, 117)", "the count must wear neutral ink (--base01)")
		self.assertEqual(out["active"], "notes")
		self.assertIn("Nothing here needs your attention", out["lede"])
		self.assertEqual(out["oneoffOpen"], "1 sharedOpen=0")
		self.assertIn("2 topics", out["oneoffSub"])
		self.assertIn("most likely to be wrong", out["oneoffSub"])
		self.assertIn("1 topic · 3 tools", out["sharedSub"])
		self.assertEqual(out["oneoffRows"], "brew:cc:method-general,brew:nnn:method-readline")
		self.assertEqual(out["sharedRows"], "1")
		self.assertEqual(out["sharedTools"], "3")
		self.assertEqual(out["marks"], "global,rehomed,restored,selftest")

	def test_one_topic_with_different_text_is_two_rows_and_a_shared_row_writes_to_every_entry(self):
		"""Finding 3: grouping on topic alone let brew:a's prose stand for
		brew:b's different note, and a correction typed on the row reached
		A only."""
		report = notes_report()
		# brew:nnn re-proposes the shared topic with DIFFERENT text.
		nnn = next(t for t in report["tools"] if t["id"] == "brew:nnn")
		nnn["suggestions"].append({"id": "brew:nnn:method-cumulative-variant", "kind": "method-note",
			"title": "Method note", "target_files": [], "command": None, "auto_runnable": False,
			"rationale": "r", "method_topic": "changelog is cumulative — scope to the version pair",
			"method_note": "Actually read EVERY entry — this vendor resets the log per major."})
		out = self.drive(report, """
		key('3');
		const shared = Array.from(document.querySelectorAll('#notes-shared .nrow'));
		log('sharedRows=' + shared.length);
		log('sharedTools=' + shared[0].querySelectorAll('.ntool').length);
		const variant = document.querySelector('.nrow[data-note="brew:nnn:method-cumulative-variant"]');
		log('variantIsOwnRow=' + !!variant);
		log('variantText=' + (variant ? variant.querySelector('.nt').textContent.trim() : 'none'));
		const ta = shared[0].querySelector('.note-modify');
		log('targets=' + ta.dataset.for.split(' ').length);
		ta.value = 'scope to the pair, but say which entries';
		ta.dispatchEvent(new Event('input', {bubbles: true}));
		const reached = ['brew:aa:method-cumulative', 'brew:bb:method-cumulative', 'brew:cc:method-cumulative']
			.map(sid => canonicalCard(sid).querySelector('.card-comment').value === ta.value);
		log('reached=' + reached.join(','));
		log('variantUntouched=' + (canonicalCard('brew:nnn:method-cumulative-variant').querySelector('.card-comment').value === ''));
""")
		self.assertEqual(out["sharedRows"], "1")
		self.assertEqual(out["sharedTools"], "3")
		self.assertEqual(out["variantIsOwnRow"], "true",
			"a different note under the same topic must be its own row")
		self.assertIn("read EVERY entry", out["variantText"])
		self.assertEqual(out["targets"], "3")
		self.assertEqual(out["reached"], "true,true,true")
		self.assertEqual(out["variantUntouched"], "true")

	def test_veto_is_a_mirror_of_reject_and_modify_writes_through(self):
		out = self.drive(notes_report(), """
		key('3');
		const row = document.querySelector('.nrow[data-note="brew:nnn:method-readline"]');
		const veto = row.querySelector('.veto');
		log('before=' + veto.textContent + '/' + canonicalCard('brew:nnn:method-readline').dataset.decision);
		veto.click();
		log('after=' + veto.textContent + '/' + canonicalCard('brew:nnn:method-readline').dataset.decision + '/' + row.dataset.vetoed);
		// The canonical card's own Reject button reflects it, and clicking it there restores here.
		key('2');
		const card = canonicalCard('brew:nnn:method-readline');
		log('cardLabel=' + card.querySelector('.card-state-label').textContent);
		card.querySelector('.btn-reject').click();
		key('3');
		log('restored=' + veto.textContent + '/' + row.dataset.vetoed);
		// Modification instructions write through to the canonical comment.
		const ta = row.querySelector('.note-modify');
		ta.value = 'say readline 8.2 specifically';
		ta.dispatchEvent(new Event('input', {bubbles: true}));
		log('comment=' + card.querySelector('.card-comment').value);
		// A shared row: each tool's chip has its own veto; the row is vetoed
		// only when every tool's note is.
		const shared = document.querySelector('#notes-shared .nrow');
		const chips = shared.querySelectorAll('.ntool .veto');
		chips[0].click();
		log('sharedPartial=' + shared.dataset.vetoed + '/' + canonicalCard(chips[0].dataset.mirrors).dataset.decision);
		chips[1].click(); chips[2].click();
		log('sharedAll=' + shared.dataset.vetoed);
""")
		self.assertEqual(out["before"], "veto/")
		self.assertEqual(out["after"], "restore/reject/1")
		self.assertEqual(out["cardLabel"], "REJECTED")
		self.assertEqual(out["restored"], "veto/0")
		self.assertEqual(out["comment"], "say readline 8.2 specifically")
		self.assertEqual(out["sharedPartial"], "0/reject")
		self.assertEqual(out["sharedAll"], "1")

	def test_n_and_v_drive_the_tab_and_the_focused_note(self):
		out = self.drive(notes_report(), """
		key('n');
		log('tab=' + activeTab);
		key('j');
		const ring = document.querySelector('#panel-notes [data-focused]');
		log('ring=' + (ring ? ring.dataset.note : 'none'));
		key('v');
		log('vetoed=' + canonicalCard(ring.dataset.note).dataset.decision);
		key('v');
		log('restored=' + canonicalCard(ring.dataset.note).dataset.decision);
		key('r');
		log('r=' + canonicalCard(ring.dataset.note).dataset.decision);
		key('a');
		log('a=' + canonicalCard(ring.dataset.note).dataset.decision);
""")
		self.assertEqual(out["tab"], "notes")
		self.assertEqual(out["ring"], "brew:cc:method-general")
		self.assertEqual(out["vetoed"], "reject")
		self.assertEqual(out["restored"], "")
		self.assertEqual(out["r"], "reject")
		self.assertEqual(out["a"], "")

	def test_a_method_note_never_counts_as_a_decision(self):
		"""rendering-report.md §Method Notes: the progress bar does not count method notes; schemas.md §1.7c: a memory
		proposal never forces a review. brew:cc is INCOMPATIBLE and carries
		two method notes — Submit must not be gated on them, the header
		badge must not count them, and the sort must not rank the tool as
		needing a decision for them."""
		out = self.drive(notes_report(), """
		log('progress=' + document.querySelector('#progress-text .long').textContent);
		// Decide cc's one real suggestion; the notes stay undecided.
		setDecision('brew:cc:upgrade', 'accept');
		log('progressAfter=' + document.querySelector('#progress-text .long').textContent);
		log('submitDisabled=' + document.getElementById('submit-btn').disabled);
		log('gate=' + blockingToolIds().join(','));
		key('2');
		const s = document.querySelector('[data-tool-id="brew:cc"]');
		log('badge=' + s.querySelector('[data-role="decision-badge"]').textContent);
		log('needs=' + sectionNeedsDecision(s));
""")
		self.assertEqual(out["progress"], "0 of 4 decided · 1 incompatible undecided")
		self.assertEqual(out["progressAfter"], "1 of 4 decided · 0 incompatible undecided")
		self.assertEqual(out["submitDisabled"], "false",
			"two undecided method notes on an incompatible tool must not gate Submit")
		self.assertEqual(out["gate"], "")
		self.assertEqual(out["badge"], "1 decided")
		self.assertEqual(out["needs"], "false")

	def test_vetoes_freeze_with_the_rest_and_the_panel_survives_submit(self):
		out = self.drive(notes_report(), """
		key('3');
		document.querySelector('.nrow[data-note="brew:nnn:method-readline"] .veto').click();
		transitionToResults({actions: [], done: false});
		log('tabs=' + Object.keys(PANELS).join(','));
		selectTab('notes');
		const row = document.querySelector('.nrow[data-note="brew:nnn:method-readline"]');
		log('vetoHidden=' + (row.querySelector('.veto').offsetParent === null));
		log('label=' + getComputedStyle(row.querySelector('.vetoed-label')).display);
		log('modifyHidden=' + (row.querySelector('.note-modify').offsetParent === null));
		const other = document.querySelector('.nrow[data-note="brew:cc:method-general"]');
		log('otherLabel=' + getComputedStyle(other.querySelector('.vetoed-label')).display);
		log('bandLive=' + (getComputedStyle(document.querySelector('#notes-shared .band-head')).pointerEvents));
""")
		self.assertEqual(out["tabs"], "results,overview,tools,notes,changelog")
		self.assertEqual(out["vetoHidden"], "true")
		# A flex item's computed display blockifies; shown is "not none".
		self.assertNotEqual(out["label"], "none")
		self.assertEqual(out["modifyHidden"], "true")
		self.assertEqual(out["otherLabel"], "none")
		self.assertEqual(out["bandLive"], "auto")



WRITE_STATUS_PY = os.path.join(SCRIPT_DIR, "write_status.py")


class PersistenceLoopTests(PageDriveRunner):
	"""Render-time persistence, driven end to end — the test that closes the pass:
	render writes a note; the user vetoes it on the tab and types a
	modification on another with no decision clicked; Submit's payload
	(buildFeedbackPayload) becomes feedback.json; `init` synthesizes the
	actions; the withdraw action's own command runs; the store no longer
	holds the vetoed note. The bargain — a bad note is visible AND
	correctable — measured, not asserted."""

	STORE = os.path.join("tool-update-review", items.METHOD_NOTES_STORE)

	def _store(self, state_home):
		with open(os.path.join(state_home, self.STORE), encoding="utf-8") as fh:
			return json.load(fh)

	def _run_ws(self, state_home, argv, cwd=None):
		env = dict(os.environ, XDG_STATE_HOME=state_home)
		return subprocess.run([sys.executable, WRITE_STATUS_PY] + argv,
			capture_output=True, text=True, timeout=60, env=env, cwd=cwd)

	def _run_detail(self, state_home, action):
		"""Run the exact command the action's detail carries — what apply.md
		tells the agent to run — with scripts/write_status.py resolved."""
		argv = __import__("shlex").split(action["detail"][0])
		self.assertEqual(argv[0], "scripts/write_status.py")
		return self._run_ws(state_home, argv[1:])

	def _drive_and_init(self, report, state_home, scenario):
		"""render → drive → feedback.json → init, ONE render: the page
		driven is the page beside the record init reads, so both see the
		same per-note outcome. Returns (out, payload, actions, report_dir)."""
		report_dir = tempfile.mkdtemp(prefix="render-loop-")
		self.addCleanup(__import__("shutil").rmtree, report_dir, True)
		out = self.drive(report, scenario, state_home=state_home, report_dir=report_dir)
		payload = json.loads(out["payload"])
		with open(os.path.join(report_dir, "feedback.json"), "w", encoding="utf-8") as fh:
			json.dump(payload, fh)
		p = self._run_ws(state_home, ["init", report_dir])
		self.assertEqual(p.returncode, 0, p.stderr)
		with open(os.path.join(report_dir, "status.json"), encoding="utf-8") as fh:
			actions = {a["id"]: a for a in json.load(fh)["actions"]}
		return out, payload, actions, report_dir

	def test_a_vetoed_note_leaves_the_store_and_a_comment_becomes_an_action(self):
		state_home = tempfile.mkdtemp(prefix="render-loop-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		out, payload, actions, _ = self._drive_and_init(notes_report(), state_home, """
		log('stored=' + document.getElementById('notes-lede').dataset.stored);
		key('3');
		// veto the re-homed one-off note …
		document.querySelector('.nrow[data-note="brew:nnn:method-readline"] .veto').click();
		// … type a modification on another with NO decision clicked …
		const ta = document.querySelector('.nrow[data-note="brew:cc:method-general"] .note-modify');
		ta.value = 'name the tag format';
		ta.dispatchEvent(new Event('input', {bubbles: true}));
		log('autoDecision=' + canonicalCard('brew:cc:method-general').dataset.decision);
		// … and clear it again on a third, which must detach the auto decision.
		const ta2 = document.querySelector('#notes-shared .nrow .note-modify');
		ta2.value = 'x'; ta2.dispatchEvent(new Event('input', {bubbles: true}));
		ta2.value = '';  ta2.dispatchEvent(new Event('input', {bubbles: true}));
		log('detached=' + canonicalCard('brew:aa:method-cumulative').dataset.decision);
		log('payload=' + JSON.stringify(buildFeedbackPayload()));
""")
		self.assertEqual(out["stored"], "5")
		self.assertEqual(out["autoDecision"], "discuss")
		self.assertEqual(out["detached"], "")
		# The store held all five notes after render (the reviewed case).
		before = self._store(state_home)
		self.assertIn("brew:nnn", before)
		self.assertIn(items.GLOBAL_METHOD_NOTE_KEY, before)
		# The payload carries the veto and the comment-attached discuss.
		self.assertEqual(payload["decisions"]["brew:nnn:method-readline"]["decision"], "reject")
		self.assertEqual(payload["decisions"]["brew:cc:method-general"],
			{"decision": "discuss", "comment": "name the tag format"})
		self.assertNotIn("brew:aa:method-cumulative", payload["decisions"])
		# init: the veto is a PENDING withdraw with the exact command …
		withdraw = actions["brew:nnn:method-readline"]
		self.assertEqual(withdraw["state"], "pending")
		self.assertEqual(withdraw["label"],
			"Withdraw method note: readline linking in homebrew-core's nnn formula")
		self.assertIn("remove-method-note --tool-id brew:nnn", withdraw["detail"][0])
		# … the comment is a pending modification …
		self.assertEqual(actions["investigate:brew:cc:method-general"]["state"], "pending")
		self.assertIn("name the tag format", actions["investigate:brew:cc:method-general"]["label"])
		# … and an untouched persisted note is already done.
		self.assertEqual(actions["brew:aa:method-cumulative"]["state"], "done")
		# Apply runs the withdraw exactly as written.
		p = self._run_detail(state_home, withdraw)
		self.assertEqual(p.returncode, 0, p.stderr)
		after = self._store(state_home)
		self.assertNotIn("brew:nnn", after, "the vetoed note must leave the store")
		self.assertEqual([e["topic"] for e in after[items.GLOBAL_METHOD_NOTE_KEY]],
			["tags beat release pages"])
		self.assertEqual(len(after["brew:aa"]), 1)

	def test_an_unreviewed_note_is_written_only_on_an_explicit_accept(self):
		report = notes_report()
		report["convergence"] = {"state": "not_run"}
		state_home = tempfile.mkdtemp(prefix="render-loop-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		out, payload, actions, _ = self._drive_and_init(report, state_home, """
		log('stored=' + document.getElementById('notes-lede').dataset.stored);
		log('lede=' + document.getElementById('notes-lede').textContent.replace(/\\s+/g, ' ').trim().slice(0, 40));
		key('3');
		const row = document.querySelector('.nrow[data-note="brew:nnn:method-readline"]');
		log('control=' + (row.querySelector('.veto') ? 'veto' : row.querySelector('.mirror') ? 'mirror' : 'none'));
		key('2');
		const card = canonicalCard('brew:nnn:method-readline');
		log('storeLine=' + card.querySelector('.note-store-line').dataset.storage + '|' + card.querySelector('.note-store-line').textContent.slice(0, 28));
		key('3');
		key('j'); key('j');  // second one-off row: nnn (cc sorts first)
		const ring = document.querySelector('#panel-notes [data-focused]');
		log('ring=' + ring.dataset.note);
		key('v');            // accept (write at apply) on an unreviewed row
		log('decision=' + card.dataset.decision);
		log('payload=' + JSON.stringify(buildFeedbackPayload()));
""")
		self.assertFalse(os.path.exists(os.path.join(state_home, self.STORE)),
			"render must not persist unreviewed proposals")
		self.assertEqual(out["stored"], "0")
		self.assertEqual(out["lede"], "Not in the store. Convergence did not re")
		self.assertEqual(out["control"], "mirror")
		self.assertEqual(out["storeLine"], "unreviewed|NOT in the method-note store")
		self.assertEqual(out["ring"], "brew:nnn:method-readline")
		self.assertEqual(out["decision"], "accept")
		self.assertEqual(payload["decisions"]["brew:nnn:method-readline"]["decision"], "accept")
		add = actions["brew:nnn:method-readline"]
		self.assertEqual(add["state"], "pending")
		self.assertTrue(add["label"].startswith("Add method note:"))
		self.assertEqual(actions["brew:aa:method-cumulative"]["state"], "skipped")
		p = self._run_detail(state_home, add)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertEqual(sorted(self._store(state_home)), ["brew:nnn"])

	def test_v_toggles_failed_store_write_veto_through_feedback_and_init(self):
		for failed in (True, False):
			for presses in (1, 2):
				with self.subTest(failed=failed, presses=presses):
					state_home = tempfile.mkdtemp(prefix="render-veto-state-")
					self.addCleanup(__import__("shutil").rmtree, state_home, True)
					report = notes_report()
					if failed:
						path = os.path.join(state_home, self.STORE)
						os.makedirs(os.path.dirname(path))
						with open(path, "w", encoding="utf-8") as fh:
							fh.write('{"broken":')
					else:
						report["convergence"]["state"] = "not_run"
					out, payload, actions, _ = self._drive_and_init(report, state_home, """
		key('n'); key('j');
		const row = document.querySelector('#panel-notes [data-focused]');
		log('focused=' + row.dataset.note);
		for (let i = 0; i < PRESSES; i++) key('v');
		log('payload=' + JSON.stringify(buildFeedbackPayload()));
""".replace('PRESSES', str(presses)))
					sid = 'brew:cc:method-general'
					self.assertEqual(out['focused'], sid)
					if presses == 1:
						self.assertEqual(payload['decisions'][sid]['decision'], 'reject' if failed else 'accept')
					else:
						self.assertNotIn(sid, payload['decisions'])
					should_write = (failed and presses == 2) or (not failed and presses == 1)
					self.assertEqual(actions[sid]['state'], 'pending' if should_write else 'skipped')
					if should_write:
						self.assertIn('add-global-method-note', actions[sid]['detail'][0])
					else:
						self.assertEqual(actions[sid]['detail'], [])

	def test_a_failed_write_is_shown_as_not_stored_and_becomes_a_pending_add(self):
		"""Round-2 finding 1. The store is unreadable at render under
		`converged`, so every write fails. What the page says about storage
		comes from the per-note render outcome, never the run's state: NOT
		stored, with the reason, and the controls offer accept and reject.
		An undecided failed note is still written at apply (render meant to
		store it); a rejected one is not; the key travels from the record —
		global for the promoted note."""
		state_home = tempfile.mkdtemp(prefix="render-loop-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		path = os.path.join(state_home, self.STORE)
		os.makedirs(os.path.dirname(path))
		with open(path, "w", encoding="utf-8") as fh:
			fh.write('{"brew:x": [{"topic": "t",')
		out, payload, actions, _ = self._drive_and_init(notes_report(), state_home, """
		const lede = document.getElementById('notes-lede');
		log('stored=' + lede.dataset.stored + '/' + lede.dataset.total);
		log('lede=' + lede.textContent.replace(/\\s+/g, ' ').trim());
		key('2');
		const line = canonicalCard('brew:cc:method-general').querySelector('.note-store-line');
		log('storeLine=' + line.dataset.storage + '|' + line.textContent);
		key('3');
		const row = document.querySelector('.nrow[data-note="brew:cc:method-general"]');
		log('control=' + (row.querySelector('.veto') ? 'veto' : row.querySelector('.mirror') ? 'mirror' : 'none'));
		log('notStored=' + row.querySelector('.not-stored').textContent);
		setStripOpen(document.getElementById('notes-shared'), true);
		const chips = Array.from(document.querySelectorAll('#notes-shared .ntool'));
		log('chipControls=' + chips.map(c => c.querySelector('.veto') ? 'veto' : c.querySelector('.mirror') ? 'mirror' : 'toggle').join(','));
		// cc's global note stays undecided; nnn's is accepted on its row;
		// aa's shared note is rejected on its chip.
		document.querySelector('.nrow[data-note="brew:nnn:method-readline"] .mirror [data-action="accept"]').click();
		chips[0].querySelector('.mirror [data-action="reject"]').click();
		log('payload=' + JSON.stringify(buildFeedbackPayload()));
""")
		self.assertEqual(out["stored"], "0/5")
		self.assertIn("0 of 5 in the store.", out["lede"])
		self.assertIn("5 failed to write at render", out["lede"])
		self.assertIn("could not be read", out["lede"])
		self.assertNotIn("Nothing here needs your attention", out["lede"])
		self.assertTrue(out["storeLine"].startswith("failed|NOT in the method-note store — render's write failed"),
			out["storeLine"])
		# Review A9: a discuss writes nothing at apply, so the page must not
		# promise "unless you reject it".
		for text in (out["storeLine"], out["lede"]):
			self.assertIn("a discuss or a reject holds it back", text)
			self.assertNotIn("unless you reject", text)
		self.assertEqual(out["control"], "mirror")
		self.assertEqual(out["notStored"], "not stored · write failed")
		self.assertEqual(out["chipControls"], "mirror,mirror,mirror")
		self.assertNotIn("brew:cc:method-general", payload["decisions"])
		self.assertEqual(payload["decisions"]["brew:nnn:method-readline"]["decision"], "accept")
		self.assertEqual(payload["decisions"]["brew:aa:method-cumulative"]["decision"], "reject")
		# Undecided + failed → pending add under the RECORD's key (global).
		add = actions["brew:cc:method-general"]
		self.assertEqual(add["state"], "pending")
		self.assertEqual(add["label"], "Add method note: tags beat release pages")
		self.assertIn("add-global-method-note", add["detail"][0])
		self.assertIn("could not be read", add["note"])
		self.assertEqual(actions["brew:nnn:method-readline"]["state"], "pending")
		self.assertEqual(actions["brew:bb:method-cumulative"]["state"], "pending")
		vetoed = actions["brew:aa:method-cumulative"]
		self.assertEqual((vetoed["state"], vetoed["detail"]), ("skipped", []))
		# Fix the store, run every pending add exactly as written: they land,
		# the vetoed one does not.
		with open(path, "w", encoding="utf-8") as fh:
			fh.write("{}")
		for a in actions.values():
			if a["label"].startswith("Add method note:"):
				p = self._run_detail(state_home, a)
				self.assertEqual(p.returncode, 0, p.stderr)
		store = self._store(state_home)
		self.assertEqual(sorted(store), ["brew:bb", "brew:cc", "brew:nnn", items.GLOBAL_METHOD_NOTE_KEY])
		self.assertEqual([e["topic"] for e in store[items.GLOBAL_METHOD_NOTE_KEY]], ["tags beat release pages"])

	def test_a_shared_entry_is_one_control_and_one_withdraw(self):
		"""Round-2 finding 2 (and 3's marks). aa's and bb's identical notes
		are both promoted to global: ONE store entry (render wrote it once,
		recorded bb as already_present), so the shared row shows ONE chip
		for it beside cc's own entry. Vetoing that chip rejects both ids;
		init plans ONE withdraw and bb is skipped naming the carrier —
		never "done, in store"; the withdraw runs once and cc's entries
		stay. Round two, on a fresh store: `r` on the whole row is one
		withdraw per entry — two, never three."""
		report = notes_report()
		report["convergence"]["memory"]["promoted_to_global"] += [
			"brew:aa:method-cumulative", "brew:bb:method-cumulative"]
		# Give aa and bb the SAME mark twice over (both promoted) plus a
		# self-test tag each, so the union has duplicates to collapse.
		for t in report["tools"]:
			if t["id"] in ("brew:aa", "brew:bb"):
				t["suggestions"][-1]["self_test_failed"] = {"limb": "unwitnessed", "reason": t["id"]}
		state_home = tempfile.mkdtemp(prefix="render-loop-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		out, payload, actions, _ = self._drive_and_init(report, state_home, """
		key('3');
		setStripOpen(document.getElementById('notes-shared'), true);
		const row = document.querySelector('#notes-shared .nrow');
		const chips = Array.from(row.querySelectorAll('.ntool'));
		log('chips=' + chips.map(c => c.dataset.entryKey + ':' + c.querySelector('.veto').dataset.entryIds).join('|'));
		const globalChip = chips.find(c => c.dataset.entryKey === 'global');
		// A reject on ONE id's canonical card — not the carrier's — is a
		// veto of the whole entry, and the chip says so.
		setDecision('brew:bb:method-cumulative', 'reject');
		log('oneId=' + globalChip.querySelector('.veto').textContent + '/' + globalChip.dataset.vetoed);
		setDecision('brew:bb:method-cumulative', 'reject');
		log('oneIdCleared=' + globalChip.querySelector('.veto').textContent);
		globalChip.querySelector('.veto').click();
		log('afterChip=' + ['brew:aa:method-cumulative', 'brew:bb:method-cumulative', 'brew:cc:method-cumulative']
			.map(sid => canonicalCard(sid).dataset.decision || '-').join(','));
		log('chipVetoed=' + globalChip.dataset.vetoed + '/' + globalChip.querySelector('.veto').textContent + '/' + row.dataset.vetoed);
		log('marks=' + Array.from(row.querySelectorAll('.topic .mark')).map(m => m.textContent).join('|'));
		log('rowHtmlClean=' + !/<\\/span><\\/span>|<\\/span><\\/div><\\/span>/.test(row.querySelector('.topic').innerHTML));
		key('2');
		log('bbStoreLine=' + canonicalCard('brew:bb:method-cumulative').querySelector('.note-store-line').textContent);
		log('payload=' + JSON.stringify(buildFeedbackPayload()));
""")
		self.assertEqual(out["chips"], "global:brew:aa:method-cumulative brew:bb:method-cumulative|brew:cc:brew:cc:method-cumulative")
		self.assertEqual(out["afterChip"], "reject,reject,-")
		self.assertEqual(out["chipVetoed"], "1/restore/0")
		self.assertEqual(out["oneId"], "restore/1", "a reject on bb alone vetoes the entry")
		self.assertEqual(out["oneIdCleared"], "veto")
		self.assertEqual(out["marks"], "promoted to global|self-test failed · unwitnessed")
		self.assertEqual(out["rowHtmlClean"], "true")
		self.assertIn("In the method-note store under global", out["bbStoreLine"])
		self.assertIn("One store entry with aa's identical note", out["bbStoreLine"])
		self.assertEqual(payload["decisions"]["brew:aa:method-cumulative"]["decision"], "reject")
		self.assertEqual(payload["decisions"]["brew:bb:method-cumulative"]["decision"], "reject")
		self.assertNotIn("brew:cc:method-cumulative", payload["decisions"])
		withdraws = [a for a in actions.values() if a["label"].startswith("Withdraw")]
		self.assertEqual([w["id"] for w in withdraws], ["brew:aa:method-cumulative"], "one entry, one withdraw")
		self.assertIn("remove-global-method-note", withdraws[0]["detail"][0])
		other = actions["brew:bb:method-cumulative"]
		self.assertEqual(other["state"], "skipped")
		self.assertEqual(other["note"], "Same store entry as 'brew:aa:method-cumulative', which withdraws it")
		self.assertEqual(actions["brew:cc:method-cumulative"]["state"], "done")
		p = self._run_detail(state_home, withdraws[0])
		self.assertEqual(p.returncode, 0, p.stderr)
		store = self._store(state_home)
		self.assertEqual([e["topic"] for e in store[items.GLOBAL_METHOD_NOTE_KEY]],
			["tags beat release pages"], "the shared entry is gone; cc's global note stays")
		self.assertEqual(len(store["brew:cc"]), 1)
		# Round two, fresh store: `r` on the focused shared row, `a`, `r`.
		state_home = tempfile.mkdtemp(prefix="render-loop-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		out, _, actions, _ = self._drive_and_init(report, state_home, """
		key('3');
		setStripOpen(document.getElementById('notes-shared'), true);
		for (let i = 0; i < 6 && !(document.querySelector('#panel-notes [data-focused]') || {dataset: {}}).dataset.topic; i++) key('j');
		log('ring=' + document.querySelector('#panel-notes [data-focused]').dataset.topic);
		const ids = ['brew:aa:method-cumulative', 'brew:bb:method-cumulative', 'brew:cc:method-cumulative'];
		key('r');
		log('afterRow=' + ids.map(sid => canonicalCard(sid).dataset.decision || '-').join(','));
		key('a');
		log('afterRestore=' + ids.map(sid => canonicalCard(sid).dataset.decision || '-').join(','));
		key('r');
		log('payload=' + JSON.stringify(buildFeedbackPayload()));
""")
		self.assertEqual(out["ring"], "changelog is cumulative — scope to the version pair")
		self.assertEqual(out["afterRow"], "reject,reject,reject")
		self.assertEqual(out["afterRestore"], "-,-,-")
		withdraws = sorted(a["id"] for a in actions.values() if a["label"].startswith("Withdraw"))
		self.assertEqual(withdraws, ["brew:aa:method-cumulative", "brew:cc:method-cumulative"],
			"two entries in the row (global, brew:cc), two withdraws — never three")
		for sid in withdraws:
			p = self._run_detail(state_home, actions[sid])
			self.assertEqual(p.returncode, 0, p.stderr)
		store = self._store(state_home)
		self.assertNotIn("brew:cc", store)
		self.assertEqual([e["topic"] for e in store[items.GLOBAL_METHOD_NOTE_KEY]], ["tags beat release pages"])

	def test_a_crashed_persistence_claims_nothing(self):
		"""The render survives anything persist_method_notes did not
		anticipate — and then the page claims NO note as stored, reading a
		record that says so, which init reads too."""
		state_home = tempfile.mkdtemp(prefix="render-loop-state-")
		self.addCleanup(__import__("shutil").rmtree, state_home, True)
		report_dir = tempfile.mkdtemp(prefix="render-loop-")
		self.addCleanup(__import__("shutil").rmtree, report_dir, True)
		# The record's path is a directory: the record write raises.
		os.makedirs(os.path.join(report_dir, "method-notes.render.json"))
		p, _ = self.render(notes_report(), state_home=state_home, report_dir=report_dir)
		self.assertEqual(p.returncode, 0, p.stderr)
		self.assertIn("persistence crashed", p.stderr)
		self.assertIn("no note known to be stored", p.stderr)
		with open(os.path.join(report_dir, "index.html"), encoding="utf-8") as fh:
			html = fh.read()
		self.assertIn('"method_notes_render": {', html)
		self.assertIn('"written": []', html)
		self.assertIn("persistence crashed", html)


# ── G-SEC: "Security fixes for you" (rendering-report.md) ──────────────────
def _gsec_pipeline(**kw):
	"""The published fixture session through validate → converge → assemble
	(test_assemble.run_fixture_pipeline), in a throwaway dir → the report."""
	import shutil as _sh
	import test_assemble as TA
	tmp = tempfile.mkdtemp(prefix="gsec-page-")
	try:
		report, _, _ = TA.run_fixture_pipeline(tmp, **kw)
	finally:
		_sh.rmtree(tmp, True)
	report.pop("_log", None)
	return report


def _forced_submission():
	"""The pinned submission with cv-014's reason no longer naming the
	consequence — at attempt 5 the demotion gate forces brew:duckdb."""
	sub = items.load_fixture("converge.json")
	for edit in sub["edits"]:
		if edit["edit_id"] == "cv-014":
			edit["reason"]["body"] = ("The quoted init line is a setting, not the "
				"parsing path the fix is in; unclear is the honest direction here.")
	return sub


def _resummarize_acceptance(report):
	"""After a test edits a report's tools, re-derive the exported acceptance
	counts through assembly's own functions — the page reads them and never
	recounts, so a stale summary would be the test's inconsistency."""
	import assemble
	report["summary"]["security"].update(assemble.summarize_acceptance(report["tools"]))
	report["summary"]["accepted_count"] = sum(1 for t in report["tools"]
		if t["source"] not in items.NON_VERSION_SOURCES and assemble.starts_accepted(t))


def _template_json_const(name):
	with open(TEMPLATE, encoding="utf-8") as fh:
		text = fh.read()
	m = __import__("re").search(r"const " + name + r" = (\{.*?\}|\[.*?\]);\n", text)
	assert m, name
	return json.loads(m.group(1))


class GSecTemplateDataTests(unittest.TestCase):
	def test_the_page_labels_are_the_models(self):
		self.assertEqual(_template_json_const("TIER_LABELS"), items.TIER_LABELS)
		self.assertEqual(_template_json_const("TIER_REASON_LEVEL"),
			dict(items.TIER_REASON_LEVELS))
		self.assertEqual(_template_json_const("TIER_HOLDS"), list(items.TIER_HOLDS))

	def test_every_bar_and_hold_has_words_and_the_old_bar_is_gone(self):
		with open(TEMPLATE, encoding="utf-8") as fh:
			text = fh.read()
		block = text[text.index("const BAR_TEXT = {"):]
		block = block[:block.index("};")]
		for bar in items.PRE_ACCEPT_BARS:
			self.assertIn("'{}':".format(bar), block, bar)
		self.assertNotIn("local-enum-invalid", text)

	def test_every_new_code_has_readable_text(self):
		with open(TEMPLATE, encoding="utf-8") as fh:
			text = fh.read()
		block = text[text.index("const CODE_TEXT = {"):]
		block = block[:block.index("};")]
		for code in ("E-SEC-FIX-UNGROUNDED", "W-SEC-FIX-NOID", "E-SUG-REQUIRED-UNGROUNDED",
				"E-SUG-SERVES-UNRESOLVED", "E-REQUIREMENT-CONTRADICTED", "E-USAGE-UNGROUNDED",
				"W-USAGE-INSTALL-ONLY"):
			self.assertIn("'{}':".format(code), block, code)


class GSecPriorityPanelTests(PageDriveRunner):
	"""The G-SEC page tests, from the fixture session run through the whole
	pipeline — validate → converge → assemble → render → headless Chrome —
	asserting from the live DOM with real dispatched events."""

	@classmethod
	def setUpClass(cls):
		cls.report = _gsec_pipeline()

	def test_01_visible_on_load_first_and_in_priority_order(self):
		out = self.drive(self.report, """
		const s = document.getElementById('sec-priority');
		let hidden = false;
		for (let e = s; e; e = e.parentElement) {
			if (e.hidden || getComputedStyle(e).display === 'none' || getComputedStyle(e).visibility === 'hidden') hidden = true;
		}
		log('visible=' + (!hidden && s.getClientRects().length > 0));
		const pos = id => { const el = document.getElementById(id); return el ? s.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING : 'none'; };
		log('beforeJudgement=' + (pos('judgement-section') ? 1 : 0));
		log('beforeSec=' + (pos('sec-section') ? 1 : 0));
		log('firstSection=' + document.querySelector('#panel-overview > section.ovsection').id);
		log('order=' + Array.from(s.querySelectorAll('.prow')).map(r => r.dataset.priority).join(''));
		log('count=' + s.querySelectorAll('.prow').length);
""")
		self.assertEqual(out["visible"], "true")
		self.assertEqual(out["beforeJudgement"], "1")
		self.assertEqual(out["beforeSec"], "1")
		self.assertEqual(out["firstSection"], "sec-priority")
		order = [out["order"][i:i + 2] for i in range(0, len(out["order"]), 2)]
		self.assertEqual(order, sorted(order))
		self.assertEqual((order[0], order[-1]), ("P0", "P2"))
		self.assertEqual(int(out["count"]), 12)

	def test_02_every_row_carries_its_label_text_without_colour(self):
		out = self.drive(self.report, """
		document.querySelectorAll('.prio-chip').forEach(c => { c.removeAttribute('data-p'); c.className = ''; });
		const rows = Array.from(document.querySelectorAll('#sec-priority .prow'));
		rows.forEach(r => log('row:' + r.dataset.tool + '=' + r.querySelector('.l2').textContent.replace(/\\s+/g, ' ').trim()));
		const req = document.querySelector('.prow[data-tool="brew:tier-required"] .more-reasons');
		log('moreTitle=' + (req ? req.textContent + '|' + req.title : 'none'));
""")
		labels = items.TIER_LABELS
		self.assertIn(labels["required-edit"]["text"], out["row:brew:tier-required"])
		self.assertIn(labels["pinned"]["text"], out["row:brew:tier-pinned"])
		self.assertIn(labels["incompatible-unfixed"]["text"], out["row:brew:tier-incompatible"])
		self.assertIn(labels["edit-proposed"]["text"], out["row:brew:tier-proposed"])
		self.assertIn(labels["relevant-fix"]["text"], out["row:brew:libpq"])
		self.assertIn(labels["fix-with-breaking-unseen"]["text"], out["row:brew:tier-breaking"])
		self.assertIn(labels["fix-with-risk"]["text"], out["row:brew:tier-risk"])
		self.assertIn(labels["vendor-unread"]["text"], out["row:cask:tier-vendor"])
		self.assertIn("⛔", out["row:brew:tier-required"])
		# a multi-reason row names the rest
		self.assertIn("+2 more reasons", out["moreTitle"])
		self.assertIn("also has a breaking change that reaches this machine", out["moreTitle"])
		self.assertIn("also carries a risk here", out["moreTitle"])

	def test_03_accepted_rows_start_on_and_one_reject_takes_the_upgrade_back(self):
		out = self.drive(self.report, """
		const on = t => { const b = document.querySelector('.prow[data-tool="' + t + '"] .mirror [data-action="accept"]'); return b ? b.dataset.on : 'none'; };
		['brew:libpq', 'brew:tier-proposed', 'brew:tier-config', 'brew:tier-breaking', 'brew:tier-risk', 'cask:tier-vendor']
			.forEach(t => log('on:' + t + '=' + on(t)));
		const card = () => document.querySelector('#main .suggestion-card[data-suggestion-id="brew:libpq:upgrade"]');
		log('before=' + card().dataset.decision);
		document.querySelector('.prow[data-tool="brew:libpq"] .mirror [data-action="reject"]').click();
		log('after=' + card().dataset.decision);
		const p = buildFeedbackPayload();
		log('payload=' + ((p.decisions['brew:libpq:upgrade'] || {}).decision || 'none'));
		log('mirrorRejectOn=' + document.querySelector('.prow[data-tool="brew:libpq"] .mirror [data-action="reject"]').dataset.on);
""")
		for tool in ("brew:libpq", "brew:tier-proposed", "brew:tier-config",
				"brew:tier-breaking", "brew:tier-risk", "cask:tier-vendor"):
			self.assertEqual(out["on:" + tool], "1", tool)
		self.assertEqual(out["before"], "accept")
		self.assertEqual(out["after"], "reject")
		self.assertEqual(out["payload"], "reject")
		self.assertEqual(out["mirrorRejectOn"], "1")

	def test_04_p0_and_held_rows_start_undecided_and_say_why(self):
		out = self.drive(self.report, """
		['brew:tier-required', 'brew:tier-pinned', 'brew:tier-incompatible', 'brew:tier-p0-lost',
			'brew:tier-held-p2', 'brew:tier-held-p1'].forEach(t => {
			const r = document.querySelector('.prow[data-tool="' + t + '"]');
			if (!r) { log('row:' + t + '=missing'); return; }
			const acc = r.querySelector('.mirror [data-action="accept"]');
			const held = r.querySelector('.held-chip');
			log('row:' + t + '=' + r.dataset.priority + '|' + (acc.dataset.on || '0') + '|' + (held ? held.textContent : ''));
		});
		const lost = document.querySelector('[data-tool-id="brew:tier-p0-lost"]');
		log('p0LostBucket=' + lost.dataset.bucket);
""")
		for tool in ("brew:tier-required", "brew:tier-pinned", "brew:tier-incompatible"):
			priority, on, _ = out["row:" + tool].split("|")
			self.assertEqual((priority, on), ("P0", "0"), tool)
		self.assertEqual(out["row:brew:tier-held-p2"].split("|")[:2], ["P2", "0"])
		self.assertIn("⏸ Held — a security change adds risk here", out["row:brew:tier-held-p2"])
		self.assertEqual(out["row:brew:tier-held-p1"].split("|")[:2], ["P1", "0"])
		self.assertIn("⏸ Held — a watch-item hit", out["row:brew:tier-held-p1"])
		# an attention (content-losing) tool with an incompatible item is a P0 row
		self.assertEqual(out["p0LostBucket"], "attention")
		self.assertEqual(out["row:brew:tier-p0-lost"].split("|")[:2], ["P0", "0"])
		self.assertIn("Held — content was lost at validation", out["row:brew:tier-p0-lost"])

	def test_05_the_lede_leads_with_p0_and_stops_claiming_security_only(self):
		out = self.drive(self.report, """
		log('lede=' + document.getElementById('lede').textContent.replace(/\\s+/g, ' ').trim());
		log('overview=' + document.getElementById('panel-overview').textContent.replace(/\\s+/g, ' '));
""")
		self.assertTrue(out["lede"].startswith(
			"4 security fixes need you before they can be accepted — first below."), out["lede"])
		self.assertIn("6 of them are listed in “Security fixes for you”, beside 3 held", out["lede"])
		for gone in ("security-only", "no impact here · accepted", "security-only with no impact here"):
			self.assertNotIn(gone, out["lede"])
		self.assertNotIn("security-only, no impact here", out["overview"])

	def test_05b_with_nothing_accepted_the_lede_says_so(self):
		"""No '0 of them are listed' when nothing starts accepted."""
		report = json.loads(json.dumps(self.report))
		for tool in report["tools"]:
			for sug in tool["suggestions"]:
				sug["pre_accept"] = False
		report["summary"]["security"]["accepted_priority_counts"] = {"P1": 0, "P2": 0}
		_resummarize_acceptance(report)
		out = self.drive(report, """
		log('lede=' + document.getElementById('lede').textContent.replace(/\\s+/g, ' ').trim());
""")
		self.assertIn("No security update starts accepted; 12 fixes are listed in "
			"“Security fixes for you” (3 held).", out["lede"])
		self.assertNotIn("0 of them", out["lede"])

	def test_06_tiles_bar_and_filter_carry_the_new_words(self):
		out = self.drive(self.report, """
		const tiles = Array.from(document.querySelectorAll('.tile')).map(t => t.querySelector('.l').textContent + '/' + t.querySelector('.s').textContent);
		log('tiles=' + tiles.join(' | '));
		log('seg=' + (document.querySelector('.seg-auto') || {}).title + ' | ' + (document.querySelector('.seg-mixed') || {}).title);
		log('opt=' + document.querySelector('#filter-bucket option[value="security_auto"]').textContent + ' | ' +
			document.querySelector('#filter-bucket option[value="security_mixed"]').textContent);
""")
		self.assertIn("Security · accepted/P1 2 · P2 4 of these in the panel", out["tiles"])
		self.assertIn("Security · held or needs you/decide these", out["tiles"])
		self.assertNotIn("Security only", out["tiles"])
		self.assertNotIn("Security + other", out["tiles"])
		self.assertIn("Security · accepted", out["seg"])
		self.assertIn("Security · held or needs you", out["seg"])
		self.assertEqual(out["opt"], "Security · accepted | Security · held or needs you")

	def test_07_the_strip_is_what_is_left_and_names_elevated_on_its_head(self):
		out = self.drive(self.report, """
		const strip = document.getElementById('sec-auto');
		log('names=' + Array.from(strip.querySelectorAll('.autorow .nm')).map(n => n.textContent).sort().join(','));
		const head = strip.querySelector('.autostrip-head');
		log('head=' + head.textContent.replace(/\\s+/g, ' ').trim());
		log('elevVisible=' + (strip.querySelector('.autostrip-head .elev').getClientRects().length > 0));
""")
		names = out["names"].split(",")
		for panel_tool in ("libpq", "tier-proposed", "tier-config", "tier-breaking",
				"tier-risk", "tier-vendor"):
			self.assertNotIn(panel_tool, names)
		self.assertIn("tier-fix", names)
		self.assertIn("elevated-fix", names)
		self.assertIn("duckdb", names)   # lowered to P3 by cv-014
		self.assertIn("3 more security updates accepted by rule — not listed in "
			"“Security fixes for you”", out["head"])
		self.assertNotIn("nothing flagged", out["head"])
		self.assertIn("⚠ elevated risk: elevated-fix", out["head"])
		self.assertNotIn("security-only", out["head"])
		self.assertEqual(out["elevVisible"], "true")

	def test_08_the_mixed_cap_never_cuts_a_p0_card(self):
		report = json.loads(json.dumps(self.report))
		template = next(t for t in report["tools"] if t["id"] == "brew:tier-pinned")
		for n in range(9):
			clone = json.loads(json.dumps(template).replace("brew:tier-pinned",
				"brew:tier-pinned-{}".format(n)).replace('"tier-pinned"', '"tier-pinned-{}"'.format(n)))
			report["tools"].append(clone)
		out = self.drive(report, """
		const rest = document.getElementById('mix-rest');
		const inRest = rest ? Array.from(rest.querySelectorAll('article.mixcard')).map(c => c.dataset.tool) : [];
		log('restHasP0=' + inRest.filter(t => t.startsWith('brew:tier-pinned') || t === 'brew:tier-required' || t === 'brew:tier-incompatible').length);
		log('restCount=' + inRest.length);
		const allCards = Array.from(document.querySelectorAll('#sec-mixed article.mixcard')).map(c => c.dataset.tool);
		log('firstTwelve=' + allCards.slice(0, 12).filter(t => t.startsWith('brew:tier-pinned') || t === 'brew:tier-required' || t === 'brew:tier-incompatible').length);
""")
		self.assertEqual(out["restHasP0"], "0")
		self.assertEqual(out["firstTwelve"], "12")

	def test_09_an_accepted_elevated_fix_is_never_called_never_pre_accepted(self):
		out = self.drive(self.report, """
		const b = document.querySelector('[data-tool-id="brew:elevated-fix"] .tool-header .risk-badge');
		log('title=' + b.title);
		const other = document.querySelector('[data-tool-id="brew:elevated"] .tool-header .risk-badge');
		log('otherTitle=' + other.title);
""")
		self.assertIn("accepted because it carries a security fix (P3)", out["title"])
		self.assertNotIn("never pre-accepted", out["title"])
		self.assertNotIn("accepted because", out["otherTitle"])

	def test_11_the_demotion_is_disclosed_expanded_and_on_the_card(self):
		out = self.drive(self.report, """
		const block = document.getElementById('lowered-block');
		log('visible=' + (block && block.getClientRects().length > 0 && !block.closest('[hidden]')));
		log('row=' + block.querySelector('.lwrow[data-tool="brew:duckdb"]').textContent.replace(/\\s+/g, ' ').trim());
		const card = document.querySelector('[data-tool-id="brew:duckdb"]');
		log('line=' + card.querySelector('.judge-line[data-priority-move]').textContent.replace(/\\s+/g, ' ').trim());
""")
		self.assertEqual(out["visible"], "true")
		self.assertIn("duckdb P2 → P3", out["row"])
		self.assertIn("The quoted init line is a setting", out["row"])
		self.assertIn("cv-014", out["row"])
		self.assertIn("Convergence lowered this fix's priority P2 → P3 (cv-014)", out["line"])

	def test_12_the_new_markers_render_with_their_words(self):
		out = self.drive(self.report, """
		const chip = (t, c) => { const el = document.querySelector('[data-tool-id="' + t + '"] .marker-chip[title]');
			const all = Array.from(document.querySelectorAll('[data-tool-id="' + t + '"] .marker-chip'));
			const hit = all.find(x => x.textContent === c); return hit ? hit.title : 'none'; };
		log('install=' + chip('brew:tier-fix', 'W-USAGE-INSTALL-ONLY'));
		log('ungrounded=' + chip('brew:nonconforming', 'E-SEC-FIX-UNGROUNDED'));
""")
		self.assertIn("the tool is installed, not shown to be used", out["install"])
		self.assertIn("counts for nothing", out["ungrounded"])

	def test_13_the_accepted_tile_counts_only_rows_that_start_accepted(self):
		out = self.drive(self.report, """
		const onRows = lv => Array.from(document.querySelectorAll('#sec-priority .prow[data-priority="' + lv + '"]'))
			.filter(r => (r.querySelector('.mirror [data-action="accept"]') || {dataset: {}}).dataset.on === '1').length;
		log('onP1=' + onRows('P1'));
		log('onP2=' + onRows('P2'));
		log('rowsP2=' + document.querySelectorAll('#sec-priority .prow[data-priority="P2"]').length);
		const tile = Array.from(document.querySelectorAll('.tile')).find(t => t.querySelector('.l').textContent === 'Security · accepted');
		log('tile=' + tile.querySelector('.s').textContent);
""")
		sec = self.report["summary"]["security"]
		self.assertEqual(int(out["onP1"]), sec["accepted_priority_counts"]["P1"])
		self.assertEqual(int(out["onP2"]), sec["accepted_priority_counts"]["P2"])
		# the held P2 tool is a panel row and never counted as accepted
		self.assertEqual(int(out["rowsP2"]), sec["priority_counts"]["P2"])
		self.assertEqual(sec["priority_counts"]["P2"], sec["accepted_priority_counts"]["P2"] + 1)
		self.assertEqual(out["tile"], "P1 {} · P2 {} of these in the panel".format(
			sec["accepted_priority_counts"]["P1"], sec["accepted_priority_counts"]["P2"]))
		self.assertEqual(sec["auto_count"] + sec["mixed_count"],
			sum(1 for t in self.report["tools"]
				if t["review_bucket"] in ("security_auto", "security_mixed")))

	def test_13b_the_accepted_tile_lands_where_the_accepted_tools_are(self):
		"""Every accepted security update a P1/P2 panel row: the strip does
		not render, so the tile and bar must not point at #sec-auto — they
		land on the panel that holds those tools."""
		report = json.loads(json.dumps(self.report))
		report["tools"] = [t for t in report["tools"]
			if t["review_bucket"] != "security_auto"
			or (t.get("security_tier") or {}).get("priority") in ("P1", "P2")]
		kept = [t for t in report["tools"] if t["review_bucket"] == "security_auto"]
		self.assertTrue(kept)
		report["summary"]["security"]["auto_count"] = len(kept)
		_resummarize_acceptance(report)
		out = self.drive(report, """
		log('strip=' + !!document.getElementById('sec-auto'));
		const tile = Array.from(document.querySelectorAll('.tile')).find(t => t.querySelector('.l').textContent === 'Security · accepted');
		log('tile=' + tile.querySelector('.v').textContent + '|' + tile.dataset.act + '|' + tile.dataset.arg);
		const seg = document.querySelector('.seg-auto');
		log('seg=' + seg.dataset.act + '|' + seg.dataset.arg);
		const target = document.getElementById(tile.dataset.arg);
		log('landsOnKept=' + (target ? [KEPT].every(id => !!target.querySelector('.prow[data-tool="' + id + '"]')) : 'no target'));
		selectTab('tools');
		tile.click();
		log('tab=' + document.querySelector('#panel-overview').hidden);
""".replace("[KEPT]", json.dumps([t["id"] for t in kept])))
		self.assertEqual(out["strip"], "false")
		self.assertEqual(out["tile"], "{}|scroll|sec-priority".format(len(kept)))
		self.assertEqual(out["seg"], "scroll|sec-priority")
		self.assertEqual(out["landsOnKept"], "true")
		self.assertEqual(out["tab"], "false")

	def test_14_config_attention_has_its_own_line(self):
		out = self.drive(self.report, """
		const r = document.querySelector('.prow[data-tool="brew:tier-config"]');
		log('chip=' + r.querySelector('.prio-chip').textContent.replace(/\\s+/g, ' ').trim());
		log('line=' + r.querySelector('.l3 .ln').textContent.replace(/\\s+/g, ' ').trim());
""")
		self.assertIn("Accepted — config needs attention — no edit proposed", out["chip"])
		self.assertIn("config needs attention — no edit proposed: The tracked config pins "
			"the old cipher list", out["line"])
		self.assertNotIn("proposed edit", out["line"])

	def test_14b_every_accepted_count_on_the_page_is_the_same_number(self):
		"""Pass 6: the tile said 53 accepted and 10 held-or-needs-you while the
		lede said 57 accepted — four security_mixed tools (binutils,
		1password, bitwarden, claude-code@latest) start accepted under the
		pre-G-SEC rule. Here cask:codex is made one of them: it must count as
		accepted everywhere, sit in the strip and not among the cards."""
		report = json.loads(json.dumps(self.report))
		codex = next(t for t in report["tools"] if t["id"] == "cask:codex")
		self.assertEqual(codex["review_bucket"], "security_mixed")
		next(s for s in codex["suggestions"] if s["kind"] == "upgrade")["pre_accept"] = True
		_resummarize_acceptance(report)
		sec = report["summary"]["security"]
		self.assertEqual(sec["accepted_count"] + sec["undecided_count"],
			sec["auto_count"] + sec["mixed_count"])
		out = self.drive(report, """
		const tile = l => Array.from(document.querySelectorAll('.tile')).find(t => t.querySelector('.l').textContent === l);
		log('acc=' + tile('Security · accepted').querySelector('.v').textContent);
		log('und=' + tile('Security · held or needs you').querySelector('.v').textContent);
		log('lede=' + document.getElementById('lede').textContent.replace(/\\s+/g, ' ').trim());
		log('sub=' + document.querySelector('#sec-section h2 .sub').textContent);
		log('segAuto=' + document.querySelector('.seg-auto').title);
		const strip = Array.from(document.querySelectorAll('#sec-auto .autorow .nm')).map(n => n.textContent);
		const cards = Array.from(document.querySelectorAll('#sec-mixed article.mixcard')).map(c => c.dataset.tool);
		const rows = Array.from(document.querySelectorAll('#sec-priority .prow'))
			.filter(r => (r.querySelector('.mirror [data-action="accept"]') || {dataset: {}}).dataset.on === '1').length;
		log('stripHasCodex=' + strip.includes('codex'));
		log('cardsHaveCodex=' + cards.includes('cask:codex'));
		const judged = REPORT.tools.filter(t => isJudged(t) && startsAccepted(t) && !inPriorityPanel(t)).length;
		log('parts=' + strip.length + '|' + rows + '|' + judged + '|' + cards.length);
""")
		accepted, undecided = sec["accepted_count"], sec["undecided_count"]
		self.assertEqual(out["acc"], str(accepted))
		self.assertEqual(out["und"], str(undecided))
		self.assertIn("{} security updates are accepted by default".format(accepted), out["lede"])
		self.assertIn("{} accepted · {} held or need you".format(accepted, undecided), out["sub"])
		self.assertIn("Security · accepted — {} (".format(accepted), out["segAuto"])
		total = report["summary"]["total_outdated"]
		self.assertIn("{} of {} updates need a decision".format(
			total - report["summary"]["accepted_count"], total), out["lede"])
		self.assertEqual(out["stripHasCodex"], "true")
		self.assertEqual(out["cardsHaveCodex"], "false")
		strip_n, accepted_rows, judged, cards_n = (int(x) for x in out["parts"].split("|"))
		# every accepted security update is in the strip, an accepted panel
		# row or the judgement panel, and every other one is a card
		self.assertEqual(strip_n + accepted_rows + judged, accepted)
		self.assertEqual(cards_n, undecided)

	def test_15_p2_orders_a_reaching_breaking_change_first_and_marks_the_rest(self):
		"""The user's answer to pass 6 (2026-09-29): every fix + breaking change
		stays highlighted and accepted; within P2, a row whose breaking change
		reaches this machine sorts first, and one here only for a breaking
		change not seen here sorts last and says so."""
		out = self.drive(self.report, """
		const rows = Array.from(document.querySelectorAll('#sec-priority .prow'));
		log('p2=' + rows.filter(r => r.dataset.priority === 'P2')
			.map(r => r.dataset.tool + ':' + r.dataset.breaking).join(','));
		const r = document.querySelector('.prow[data-tool="brew:tier-breaking"]');
		log('chip=' + r.querySelector('.prio-chip').textContent.replace(/\\s+/g, ' ').trim());
		log('line=' + r.querySelector('.l3 .ln').textContent.replace(/\\s+/g, ' ').trim());
""")
		p2 = [entry.split(":")[-1] for entry in out["p2"].split(",")]
		tools = [entry.rsplit(":", 1)[0] for entry in out["p2"].split(",")]
		self.assertEqual(tools[0], "brew:tier-held-p2")
		self.assertEqual(p2[0], "reaches")
		self.assertEqual(tools[-1], "brew:tier-breaking")
		self.assertEqual(p2[-1], "unseen")
		self.assertNotIn("reaches", p2[1:])
		self.assertIn("Accepted — breaking change, not seen here", out["chip"])
		self.assertIn("Breaking change, not seen here: The deprecated `--legacy` flag "
			"is removed", out["line"])


class GSecDegradedPageTests(PageDriveRunner):
	"""Page tests 10 and 15: a degraded-gate forced tool and a validator
	stage failing after the bucket — neither rendered accepted anywhere."""

	def test_10_a_forced_tool_keeps_its_priority_and_is_accepted_nowhere(self):
		report = _gsec_pipeline(submission=_forced_submission(), terminal=True, attempt=5)
		self.assertEqual(report["convergence"]["state"], "degraded_gate")
		out = self.drive(report, """
		const r = document.querySelector('.prow[data-tool="brew:duckdb"]');
		log('row=' + (r ? r.dataset.priority + '|' + r.querySelector('.l2').textContent.replace(/\\s+/g, ' ').trim() : 'none'));
		const ons = document.querySelectorAll('[data-mirrors="brew:duckdb:upgrade"][data-on="1"], [data-mirrors="brew:duckdb:upgrade"] [data-action="accept"][data-on="1"]').length;
		log('ons=' + ons);
		log('decision=' + (document.querySelector('#main .suggestion-card[data-suggestion-id="brew:duckdb:upgrade"]').dataset.decision || ''));
		log('inStrip=' + !!document.querySelector('#sec-auto .autorow [data-mirrors="brew:duckdb:upgrade"]'));
		const card = document.querySelector('[data-tool-id="brew:duckdb"]');
		card.classList.remove('collapsed');
		const line = card.querySelector('.judge-line.forced');
		log('kind=' + line.dataset.forcedKind);
		log('line=' + line.textContent.replace(/\\s+/g, ' ').trim());
		log('badge=' + card.querySelector('.judge-badge.forced').title);
		const strip = document.getElementById('degraded-strip');
		log('h=' + strip.querySelector('.h').textContent);
		log('m=' + strip.querySelector('.m').textContent.replace(/\\s+/g, ' ').trim());
""")
		# a DEMOTION failure is explained as one — never as a failed move to
		# auto-update — and would_have_been reads as words, not [object Object]
		self.assertEqual(out["kind"], "demotion")
		self.assertIn("Convergence lowered this fix's priority to P3 without a reason "
			"that survived the gate", out["line"])
		self.assertIn("keeps its prior priority P2 here", out["line"])
		self.assertIn("is not accepted", out["line"])
		self.assertNotIn("auto-update", out["line"])
		self.assertIn("had their security priority lowered", out["h"])
		self.assertNotIn("reached auto-update", out["h"])
		for text in (out["line"], out["badge"], out["m"]):
			self.assertNotIn("[object Object]", text)
		self.assertIn("would have been security_auto, accepted, priority P3", out["m"])
		self.assertIn("would have been security_auto, accepted, priority P3", out["badge"])
		priority, text = out["row"].split("|", 1)
		self.assertEqual(priority, "P2")
		self.assertIn("the fix touches how you use it", text)
		self.assertIn("Held — forced conservative by convergence", text)
		self.assertEqual(out["ons"], "0")
		self.assertNotEqual(out["decision"], "accept")
		self.assertEqual(out["inStrip"], "false")

	def test_10b_a_tool_failing_both_gates_names_both_on_the_page(self):
		"""Review round 2: the forced record carries every failed gate kind;
		a tool that both reached auto-update and had its priority lowered is
		explained as both — on its card and in the strip — not only the first."""
		import apply_converge
		report = _gsec_pipeline(submission=_forced_submission(), terminal=True, attempt=5)
		seen = []

		def both(node):
			if isinstance(node, dict):
				if node.get("forced_bucket") and node.get("kind") == "demotion":
					node.update(kind="permissive", kinds=["permissive", "demotion"],
						code="E-GATE-UNDECLARED",
						code_by_kind={"permissive": "E-GATE-UNDECLARED",
							"demotion": "E-GATE-UNREASONED"})
					seen.append(node)
				for value in node.values():
					both(value)
			elif isinstance(node, list):
				for value in node:
					both(value)
		both(report)
		self.assertTrue(seen)
		status = report["convergence"]["status"]
		headline, body = apply_converge._degraded_gate_explanation(
			{d["tool_id"]: d for d in status["degraded_tools"]}, 5)
		status["explanation"].update(headline=headline, body=body)
		out = self.drive(report, """
		const card = document.querySelector('[data-tool-id="brew:duckdb"]');
		card.classList.remove('collapsed');
		const line = card.querySelector('.judge-line.forced');
		log('kind=' + line.dataset.forcedKind);
		log('line=' + line.textContent.replace(/\\s+/g, ' ').trim());
		const strip = document.getElementById('degraded-strip');
		log('h=' + strip.querySelector('.h').textContent);
		log('b=' + strip.querySelector('.b').textContent);
		log('prio=' + document.querySelector('.prow[data-tool="brew:duckdb"]').dataset.priority);
""")
		self.assertEqual(out["kind"], "permissive demotion")
		self.assertIn("could not justify this tool's move to auto-update "
			"(E-GATE-UNDECLARED)", out["line"])
		self.assertIn("lowered this fix's priority to P3 without a reason that "
			"survived the gate (E-GATE-UNREASONED)", out["line"])
		self.assertIn("keeps its prior priority P2 here", out["line"])
		self.assertIn("instead of security_auto, accepted, priority P3", out["line"])
		self.assertIn("reached auto-update without surviving the gate", out["h"])
		self.assertIn("had their security priority lowered", out["h"])
		self.assertIn("brew:duckdb both reached auto-update and had its priority "
			"lowered", out["b"])
		self.assertEqual(out["prio"], "P2")

	def test_15_a_post_bucket_failure_renders_as_an_attention_card(self):
		import test_converge as TC
		import validate_items
		real = validate_items._self_test_tagged_ids

		def failing(suggestions, tool_id):
			if tool_id == "brew:tier-fix":
				raise RuntimeError("injected after the bucket")
			return real(suggestions, tool_id)
		patch = __import__("unittest.mock").mock.patch(
			"validate_items._self_test_tagged_ids", side_effect=failing)
		report = _gsec_pipeline(submission=lambda pre: TC.make_submission(pre, []),
			patch_validator=patch)
		out = self.drive(report, """
		const card = document.querySelector('[data-tool-id="brew:tier-fix"]');
		log('bucket=' + card.dataset.bucket);
		log('inStrip=' + !!document.querySelector('#sec-auto .autorow [data-mirrors="brew:tier-fix:upgrade"]'));
		const ons = document.querySelectorAll('[data-mirrors="brew:tier-fix:upgrade"][data-on="1"], [data-mirrors="brew:tier-fix:upgrade"] [data-action="accept"][data-on="1"]').length;
		log('ons=' + ons);
		log('decision=' + (document.querySelector('#main .suggestion-card[data-suggestion-id="brew:tier-fix:upgrade"]').dataset.decision || ''));
""")
		self.assertEqual(out["bucket"], "attention")
		self.assertEqual(out["inStrip"], "false")
		self.assertEqual(out["ons"], "0")
		self.assertNotEqual(out["decision"], "accept")


class GSecNarrowViewportTests(unittest.TestCase):
	"""At a TRUE 390×844 viewport (headless Chrome's window clamps at 500, so
	this uses Python Playwright from the agent-skills venv): nothing scrolls
	sideways on ANY tab or with every card expanded — a method note carrying a
	long URL is planted, the pass 7 defect — and the phone layout the user
	asked for on 2026-09-30 holds: the tiles are one summary line, the sticky
	bar is short, decision controls are 44 px touch targets, and the first
	"Security fixes for you" row is on screen at load. At desktop width none
	of that applies."""

	VENV_PY = os.path.expanduser("~/.local/share/agent-skills/venv/bin/python")
	LONG_URL = ("https://learn.example.invalid/cli/azure/release-notes-azure-cli/"
		+ "versions/" + "x" * 120 + "/notes")

	# The probe runs under the venv's interpreter. JS is kept free of quote
	# characters the Python layers use, so nothing needs escaping twice.
	PROBE = r"""
import sys, json
from playwright.sync_api import sync_playwright

LOAD = r'''() => {
	const box = s => { const e = document.querySelector(s); if (!e) return null;
		const r = e.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, h: r.height}; };
	const row = document.querySelector("#sec-priority .prow");
	return {sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
		vh: window.innerHeight, row: box("#sec-priority .prow"), bar: box("#progress-bar-container"),
		tiles: getComputedStyle(document.querySelector(".tilegroups")).display,
		sum: getComputedStyle(document.getElementById("tilesum")).display,
		sumText: document.getElementById("tilesum").textContent.replace(/\s+/g, " ").trim(),
		rowButtons: row ? Array.from(row.querySelectorAll(".btn-d, .jump")).map(e => e.getBoundingClientRect().height) : [],
		rows: document.querySelectorAll("#sec-priority .prow").length,
		lowered: document.querySelectorAll("#lowered-block .lwrow").length};
}'''
WIDTH = '() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]'
EXPAND = '''() => {
	document.querySelectorAll(".tool-section.collapsed").forEach(s => s.classList.remove("collapsed"));
	document.querySelectorAll(".item-fold").forEach(f => { f.dataset.open = "1"; });
}'''
EXPANDED = '''() => [document.documentElement.scrollWidth, document.documentElement.clientWidth,
	Math.min(...Array.from(document.querySelectorAll(".btn-decision")).filter(e => e.offsetParent)
		.map(e => e.getBoundingClientRect().height))]'''

out = {}
with sync_playwright() as p:
	b = p.chromium.launch()
	for w, h, key in ((390, 844, "phone"), (1280, 900, "desktop")):
		pg = b.new_page(viewport={"width": w, "height": h})
		errs = []
		pg.on("pageerror", lambda e: errs.append(str(e)))
		pg.goto("file://" + sys.argv[1])
		pg.wait_for_timeout(600)
		o = pg.evaluate(LOAD)
		o["tabs"] = {}
		for name in ("All tools", "Method notes", "Overview"):
			pg.locator("#tab-strip [role=tab]", has_text=name).click()
			pg.wait_for_timeout(300)
			o["tabs"][name] = pg.evaluate(WIDTH)
		o["longNote"] = pg.evaluate('() => document.querySelectorAll("#panel-notes .nrow .nt").length')
		pg.locator("#tab-strip [role=tab]", has_text="All tools").click()
		pg.wait_for_timeout(300)
		pg.evaluate(EXPAND)
		pg.wait_for_timeout(300)
		o["expanded"] = pg.evaluate(EXPANDED)
		o["quarantineShown"] = pg.evaluate('() => Array.from(document.querySelectorAll(".quarantine-value")).filter(e => e.offsetParent).length')
		o["quarantineClipped"] = pg.evaluate('() => Array.from(document.querySelectorAll(".quarantine-value, .quarantine-field")).filter(e => e.offsetParent && e.scrollWidth > e.clientWidth + 1).length')
		o["errors"] = errs
		out[key] = o
		pg.close()
	b.close()
print(json.dumps(out))
"""

	def _render(self):
		report = _gsec_pipeline()
		planted = False
		for tool in report["tools"]:
			for sug in tool.get("suggestions") or []:
				if sug.get("kind") == "method-note" and not planted:
					sug["method_note"] = (sug.get("method_note") or "") + " See " + self.LONG_URL
					planted = True
		self.assertTrue(planted, "the fixture carries no method note to plant the URL in")
		# And a replaced research entry carrying the same unbroken URL, so the
		# quarantine fold is measured expanded at 390 px too.
		report["tools"][0]["quarantine"] = list(report["tools"][0].get("quarantine") or []) + [{
			"field": "duplicate research entry (01-early.json)", "item_id": None,
			"value": {"id": report["tools"][0]["id"], "links": [self.LONG_URL],
				"items": [{"title": "Removes a flag " + self.LONG_URL}]}}]
		report_dir = tempfile.mkdtemp(prefix="gsec-390-")
		self.addCleanup(__import__("shutil").rmtree, report_dir, True)
		state = tempfile.mkdtemp(prefix="gsec-390-state-")
		self.addCleanup(__import__("shutil").rmtree, state, True)
		path = os.path.join(report_dir, "report.json")
		with open(path, "w", encoding="utf-8") as fh:
			json.dump(report, fh)
		p = subprocess.run([sys.executable, RENDER_PY, path], capture_output=True, text=True,
			env=dict(os.environ, XDG_STATE_HOME=state), timeout=60)
		self.assertEqual(p.returncode, 0, p.stderr)
		return os.path.join(report_dir, "index.html")

	def test_the_phone_view_and_no_sideways_scroll_on_any_tab(self):
		if not os.path.exists(self.VENV_PY):
			self.skipTest("no agent-skills venv with Playwright")
		page = self._render()
		r = subprocess.run([self.VENV_PY, "-c", self.PROBE, page],
			capture_output=True, text=True, timeout=180)
		if r.returncode != 0 and "Executable doesn't exist" in r.stderr:
			self.skipTest("Playwright has no Chromium installed")
		self.assertEqual(r.returncode, 0, r.stderr[-1500:])
		info = json.loads(r.stdout.strip().splitlines()[-1])
		phone, desk = info["phone"], info["desktop"]
		for view, width in ((phone, 390), (desk, 1280)):
			self.assertEqual(view["errors"], [])
			self.assertEqual(view["cw"], width)
			self.assertEqual(view["sw"], view["cw"], view)
			for name, (sw, cw) in view["tabs"].items():
				self.assertEqual(sw, cw, (width, name))
			self.assertEqual(view["expanded"][0], view["expanded"][1], (width, "expanded cards"))
			self.assertGreater(view["rows"], 0)
			self.assertEqual(view["lowered"], 1)
			self.assertGreater(view["longNote"], 0)
			self.assertGreater(view["quarantineShown"], 0)
			# Wrapped, not clipped: a card clips its overflow, so page width
			# alone would not see an unbroken value cut off inside the fold.
			self.assertEqual(view["quarantineClipped"], 0, width)
		# Phone: one summary line instead of the tiles, a short sticky bar,
		# 44 px decision controls, and the first panel row on screen at load.
		self.assertEqual(phone["tiles"], "none")
		self.assertEqual(phone["sum"], "block")
		self.assertRegex(phone["sumText"], r"\d+ updates: .*major.*security: .*CVEs fixed")
		self.assertLessEqual(phone["bar"]["h"], 80)
		self.assertGreaterEqual(min(phone["rowButtons"]), 44)
		self.assertGreaterEqual(phone["expanded"][2], 44)
		self.assertGreaterEqual(phone["row"]["top"], phone["bar"]["bottom"])
		self.assertLessEqual(phone["row"]["bottom"], phone["vh"], phone["row"])
		# Desktop: unchanged — tiles, no summary line, compact controls.
		self.assertEqual(desk["sum"], "none")
		self.assertNotEqual(desk["tiles"], "none")
		self.assertLess(max(desk["rowButtons"]), 44)


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
