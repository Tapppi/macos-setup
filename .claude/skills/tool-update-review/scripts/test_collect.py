#!/usr/bin/env python3
"""
test_collect.py — the candidate-set matrix for collect.sh's brew section.
Usage: python3 test_collect.py [-v]

Stdlib `unittest` only (no pytest, no fixtures directory, NO NETWORK), same
constraints as test_skill_drift.py. collect.sh is a shell script, so it is
tested the way it actually runs: a temp workspace, a temp `bin/` holding stub
`brew`/`mise`/`softwareupdate` executables placed first on PATH, and a real
`bash collect.sh Brewfile` invocation whose stdout is parsed as JSON. Nothing
here touches the real Homebrew install or the network, and every run costs
about a second.

The one thing these tests are about: **which candidates survive the Brewfile
intersection**. `brew outdated --json=v2` reports a formula under its FULL
tap-qualified name (`slp/krun/krunkit`) while a Brewfile may declare the same
package either way, so both sides are normalised to the short name before the
membership test (references/collection.md §Version Sources). Stripping
only the Brewfile side is how every third-party-tap formula silently fell out
of the candidate set — the report looked complete while a whole tap was
invisible — which is the regression this file exists to catch.

The deliberate exclusion is asserted alongside it: a formula that is *not*
manifested in the Brewfile stays out even when its tap-mate is in, because
transitive dependencies are out of scope by design, not by accident.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COLLECT_SH = os.path.join(SCRIPT_DIR, "collect.sh")

# A stub `brew` that answers only the subcommands collect.sh calls. `outdated`
# and `list --pinned` read their payload out of the environment so each test
# supplies its own; `doctor` and everything else stay silent, which collect.sh
# already treats as "nothing to report".
BREW_STUB = """#!/usr/bin/env bash
case "$1" in
	outdated) cat "${STUB_OUTDATED}" ;;
	list) [[ "${2:-}" == "--pinned" ]] && printf '%s' "${STUB_PINNED:-}" ;;
	info) [[ -n "${STUB_INFO:-}" ]] && cat "${STUB_INFO}" ;;
	*) : ;;
esac
exit 0
"""
MISE_STUB = "#!/usr/bin/env bash\necho '{}'\n"
SOFTWAREUPDATE_STUB = "#!/usr/bin/env bash\n:\n"


def _write(path, text, mode=0o644):
	with open(path, "w", encoding="utf-8") as fh:
		fh.write(text)
	os.chmod(path, mode)


class CollectBrewSection(unittest.TestCase):
	"""Every test runs the real collect.sh against stubbed brew output."""

	def collect(self, brewfile, outdated, pinned="", info=None):
		"""Run collect.sh in a throwaway workspace; return the parsed object.

		Every fixture Brewfile carries both a `brew "` and a `cask "` line:
		collect.sh runs under `set -euo pipefail`, so a `grep` that matches
		nothing aborts the collector. Real Brewfiles always have both, and
		widening that is not what these tests are about — the padding entries
		are simply never listed as outdated."""
		with tempfile.TemporaryDirectory(prefix="collect-test-") as root:
			bindir = os.path.join(root, "bin")
			work = os.path.join(root, "work")
			os.makedirs(bindir)
			os.makedirs(work)
			_write(os.path.join(bindir, "brew"), BREW_STUB, 0o755)
			_write(os.path.join(bindir, "mise"), MISE_STUB, 0o755)
			_write(os.path.join(bindir, "softwareupdate"), SOFTWAREUPDATE_STUB, 0o755)
			_write(os.path.join(work, "Brewfile"), brewfile)
			_write(os.path.join(root, "outdated.json"), json.dumps(outdated))
			env = dict(os.environ)
			env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
			env["STUB_OUTDATED"] = os.path.join(root, "outdated.json")
			env["STUB_PINNED"] = pinned
			if info is not None:
				_write(os.path.join(root, "info.json"), json.dumps(info))
				env["STUB_INFO"] = os.path.join(root, "info.json")
			p = subprocess.run(["bash", COLLECT_SH, "Brewfile"], cwd=work, env=env,
				capture_output=True, text=True, timeout=180)
			self.assertEqual(p.returncode, 0, p.stderr)
			return json.loads(p.stdout)

	def brew_ids(self, report):
		return [t["id"] for t in report["brew"]]

	# ── the regression: a tap-qualified `brew outdated` name must still match ──

	def test_bare_brewfile_entry_matches_tap_qualified_outdated_name(self):
		"""`tap "slp/krun"` + `brew "krunkit"` vs `slp/krun/krunkit`."""
		report = self.collect(
			'tap "slp/krun"\nbrew "krunkit"\ncask "1password"\n',
			{"formulae": [{"name": "slp/krun/krunkit", "installed_versions": ["1.2.1"],
				"current_version": "1.3.2", "pinned": False}], "casks": []})
		self.assertEqual(self.brew_ids(report), ["brew:krunkit"])
		tool = report["brew"][0]
		self.assertEqual(tool["name"], "krunkit")
		self.assertEqual(tool["current_version"], "1.2.1")
		self.assertEqual(tool["latest_version"], "1.3.2")

	def test_tap_qualified_brewfile_entry_matches_tap_qualified_outdated_name(self):
		"""The other spelling — both sides qualified — normalises the same way."""
		report = self.collect(
			'brew "anomalyco/tap/opencode"\ncask "1password"\n',
			{"formulae": [{"name": "anomalyco/tap/opencode", "installed_versions": ["1.15.12"],
				"current_version": "1.18.29", "pinned": False}], "casks": []})
		self.assertEqual(self.brew_ids(report), ["brew:opencode"])
		self.assertEqual(report["brew"][0]["name"], "opencode")

	def test_core_formula_and_cask_ids_are_unchanged(self):
		"""The untapped majority keeps exactly the ids it always had."""
		report = self.collect(
			'brew "curl"\ncask "1password"\n',
			{"formulae": [{"name": "curl", "installed_versions": ["8.1.0"],
				"current_version": "8.2.0", "pinned": False}],
				"casks": [{"name": "1password", "installed_versions": ["8.1"],
					"current_version": "8.2", "pinned": False}]})
		self.assertEqual(sorted(self.brew_ids(report)), ["brew:curl", "cask:1password"])

	def test_tapped_cask_is_collected_under_its_short_token(self):
		"""Casks are reported short today, but the Brewfile may qualify one."""
		report = self.collect(
			'brew "curl"\ncask "some-tap/cask/widget"\n',
			{"formulae": [], "casks": [{"name": "widget", "installed_versions": ["1.0"],
				"current_version": "2.0", "pinned": False}]})
		self.assertEqual(self.brew_ids(report), ["cask:widget"])

	# ── the boundary that must NOT move: Brewfile-manifested only ──

	def test_non_manifested_tap_formula_stays_out_of_scope(self):
		"""`libkrun` is krunkit's dependency, not a Brewfile entry — excluded by
		design (references/collection.md: transitive deps excluded), and the
		tap-prefix fix must not quietly widen that scope."""
		report = self.collect(
			'tap "slp/krun"\nbrew "krunkit"\ncask "1password"\n',
			{"formulae": [
				{"name": "slp/krun/krunkit", "installed_versions": ["1.2.1"],
					"current_version": "1.3.2", "pinned": False},
				{"name": "slp/krun/libkrun", "installed_versions": ["1.18.1"],
					"current_version": "1.19.4", "pinned": False}], "casks": []})
		self.assertEqual(self.brew_ids(report), ["brew:krunkit"])

	# ── the pinned path, which does the same match in shell rather than jq ──

	def test_pinned_tapped_formula_uses_the_short_id_once(self):
		"""A pinned tapped formula is surfaced under the same id the outdated
		path would emit — two ids for one formula is how it would show up
		twice on the page."""
		report = self.collect(
			'tap "slp/krun"\nbrew "krunkit"\ncask "1password"\n',
			{"formulae": [], "casks": []},
			pinned="slp/krun/krunkit\n",
			info={"formulae": [{"installed": [{"version": "1.2.1"}],
				"versions": {"stable": "1.3.2"}}]})
		self.assertEqual(self.brew_ids(report), ["brew:krunkit"])
		self.assertTrue(report["brew"][0]["pinned"])
		self.assertEqual(len(set(self.brew_ids(report))), len(self.brew_ids(report)))


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
