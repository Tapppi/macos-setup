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
CHECK_PIN = os.path.join(SCRIPT_DIR, "check_pin.py")

# A stub `brew` that answers the subcommands collect.sh and check_pin.py call.
# `outdated` reads its payload out of the environment so each test supplies its
# own; `list --pinned` and `info` answer too, so a collector that went back to
# asking them would be caught (test_brew_list_pinned_is_not_a_candidate_source)
# and check_pin.py's preflight has a `brew info` to read. `doctor` and
# everything else stay silent, which collect.sh treats as "nothing to report".
#
# STUB_OUTDATED_RC and STUB_OUTDATED_PREFIX exist for the degradation tests at
# the bottom of this file: a real `brew` can print a complete listing and
# *then* exit non-zero, and can put a deprecation notice on stdout ahead of the
# JSON. Both are ways a healthy brew derails a collector that assumes otherwise.
BREW_STUB = """#!/usr/bin/env bash
case "$1" in
	outdated) [[ -n "${STUB_OUTDATED_PREFIX:-}" ]] && printf '%s\\n' "${STUB_OUTDATED_PREFIX}"
		cat "${STUB_OUTDATED}"; exit "${STUB_OUTDATED_RC:-0}" ;;
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


class CollectRunner(unittest.TestCase):
	"""Shared harness: every test below runs the real collect.sh against
	stubbed brew/mise/softwareupdate executables placed first on PATH."""

	def run_collect(self, brewfile, outdated, pinned="", info=None,
			outdated_rc=None, outdated_prefix=None):
		"""Run collect.sh in a throwaway workspace; return the CompletedProcess.

		Most fixture Brewfiles carry both a `brew "` and a `cask "` line — not
		because the collector needs them (it no longer aborts when one of those
		greps matches nothing) but because real Brewfiles have both and these
		tests are about the intersection, not the empty case. The padding
		entries are simply never listed as outdated."""
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
			if outdated_rc is not None:
				env["STUB_OUTDATED_RC"] = str(outdated_rc)
			if outdated_prefix is not None:
				env["STUB_OUTDATED_PREFIX"] = outdated_prefix
			if info is not None:
				_write(os.path.join(root, "info.json"), json.dumps(info))
				env["STUB_INFO"] = os.path.join(root, "info.json")
			return subprocess.run(["bash", COLLECT_SH, "Brewfile"], cwd=work, env=env,
				capture_output=True, text=True, timeout=180)

	def collect(self, brewfile, outdated, pinned="", info=None, **kw):
		"""run_collect(), asserting the collector succeeded; return the object."""
		p = self.run_collect(brewfile, outdated, pinned=pinned, info=info, **kw)
		self.assertEqual(p.returncode, 0, p.stderr)
		return json.loads(p.stdout)

	def brew_ids(self, report):
		return [t["id"] for t in report["brew"]]


class CollectBrewSection(CollectRunner):
	"""Which candidates survive the Brewfile intersection."""

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

	# ── pinned formulae: `brew outdated` is their only surface ──

	def test_pinned_tapped_formula_keeps_its_pin_and_the_short_id(self):
		"""`brew outdated` lists an outdated pinned formula with `pinned: true`
		(pinning gates `brew upgrade`, never `outdated`), so the pin state the
		checker and the G-SEC `pinned` reason read arrives on the one surface,
		under the same short id every other formula gets."""
		report = self.collect(
			'tap "slp/krun"\nbrew "krunkit"\ncask "1password"\n',
			{"formulae": [{"name": "slp/krun/krunkit", "installed_versions": ["1.2.1"],
				"current_version": "1.3.2", "pinned": True}], "casks": []})
		self.assertEqual(self.brew_ids(report), ["brew:krunkit"])
		self.assertIs(report["brew"][0]["pinned"], True)

	def test_brew_list_pinned_is_not_a_candidate_source(self):
		"""The second `brew list --pinned` surface is gone (user decision
		2026-09-28): a pinned formula `brew outdated` does not list is not a
		candidate, whatever `brew list --pinned` and `brew info` say. Real
		brew never produces this pair for a formula that is behind — this
		test pins that collect.sh no longer asks."""
		report = self.collect('brew "duti"\ncask "1password"\n',
			{"formulae": [], "casks": []},
			pinned="duti\n",
			info={"formulae": [{"installed": [{"version": "1.5.4_1"}],
				"versions": {"stable": "1.5.5"}, "revision": 1}]})
		self.assertEqual(report["brew"], [])


class CollectDegradationTests(CollectRunner):
	"""collect.sh runs under `set -euo pipefail`, which turns a shrug from any
	one command into the death of the whole collector: no collect.json, no
	report, and — because every brew call has its stderr sent to /dev/null —
	frequently nothing on stderr to say why. Each case below aborted the run
	outright; each must now cost at most the one section it is about, loudly."""

	def test_a_brewfile_with_no_brew_lines_still_collects(self):
		"""`grep` exits 1 when it matches nothing, and under pipefail that is
		the whole pipeline's status — so a Brewfile listing only casks (or only
		formulae, or only taps) killed the collector at the assignment, silently:
		rc=1, empty stdout, empty stderr."""
		p = self.run_collect('tap "slp/krun"\n# nothing else\n', {"formulae": [], "casks": []})
		self.assertEqual(p.returncode, 0, p.stderr)
		report = json.loads(p.stdout)
		self.assertEqual(report["brew"], [])
		# Not silent: an empty candidate set has to be attributable.
		self.assertIn("no 'brew \"...\"' lines", p.stderr)
		self.assertIn("no 'cask \"...\"' lines", p.stderr)

	def test_brew_outdated_exiting_non_zero_does_not_lose_its_own_output(self):
		"""A `brew outdated` that prints a complete listing and then exits
		non-zero. `… | jq … || echo '[]'` fired *in addition to* jq's valid
		output, making brew_json the concatenation `[…]\\n[]` — unparseable text
		that reached the final `jq -n --argjson` and killed the collector with
		nothing but `jq: invalid JSON text passed to --argjson` on stderr."""
		report = self.collect(
			'brew "curl"\ncask "1password"\n',
			{"formulae": [{"name": "curl", "installed_versions": ["8.1.0"],
				"current_version": "8.2.0", "pinned": False}], "casks": []},
			outdated_rc=1)
		# The listing brew did produce is still read, not discarded.
		self.assertEqual(self.brew_ids(report), ["brew:curl"])

	def test_a_notice_ahead_of_the_outdated_json_costs_the_brew_section_not_the_run(self):
		"""`brew outdated` putting a notice on stdout ahead of its JSON makes
		the jq it is piped into emit nothing at all — zero bytes, not partial
		JSON. Without the post-hoc `jq -e .` fallback on `brew_json`, that
		empty string reaches the final `jq -n --argjson` and kills the whole
		collector: no collect.json, and nothing on stderr but
		`jq: invalid JSON text passed to --argjson`."""
		p = self.run_collect(
			'brew "curl"\ncask "1password"\n',
			{"formulae": [{"name": "curl", "installed_versions": ["8.1.0"],
				"current_version": "8.2.0", "pinned": False}], "casks": []},
			outdated_prefix="Warning: brew has something to say first")
		self.assertEqual(p.returncode, 0, p.stderr)
		report = json.loads(p.stdout)
		# The brew section is the only casualty…
		self.assertEqual(report["brew"], [])
		# …it is attributable…
		self.assertIn("could not read `brew outdated` output as JSON", p.stderr)
		# …and the rest of the run is intact.
		self.assertEqual(set(report), {"generated_at", "machine", "brew", "mise",
			"standalone", "macos", "brew_health", "skill_drift"})


class CollectPinnedRevisionTests(CollectRunner):
	"""Criterion 22's collect-side half. `brew outdated`'s current_version —
	stable composed with the packaging revision ("1.5.5_1") — becomes
	`latest_version` and so the upgrade suggestion's `target_version`, and
	check_pin.py's preflight composes `brew info`'s versions.stable with the
	revision the same way. The two sides must be the same string for a
	revisioned pinned formula, or `set-action done` is permanently refused."""

	OUTDATED_BEHIND_AT_REVISION = {"formulae": [{"name": "duti",
		"installed_versions": ["1.5.4_1"], "current_version": "1.5.5_1",
		"pinned": True}], "casks": []}
	INFO_BEHIND_AT_REVISION = {"formulae": [{
		"installed": [{"version": "1.5.4_1"}],
		"versions": {"stable": "1.5.5"}, "revision": 1}]}

	def test_both_sides_of_the_pin_check_compute_the_same_string(self):
		"""The criterion itself, across the two scripts: run collect.sh
		against a stubbed `brew outdated` and `check_pin.py preflight` against
		the matching stubbed `brew info`, and require preflight to call
		collect's `latest_version` a match."""
		with tempfile.TemporaryDirectory(prefix="collect-test-") as root:
			bindir = os.path.join(root, "bin")
			work = os.path.join(root, "work")
			os.makedirs(bindir)
			os.makedirs(work)
			_write(os.path.join(bindir, "brew"), BREW_STUB, 0o755)
			_write(os.path.join(bindir, "mise"), MISE_STUB, 0o755)
			_write(os.path.join(bindir, "softwareupdate"), SOFTWAREUPDATE_STUB, 0o755)
			_write(os.path.join(work, "Brewfile"), 'brew "duti"\ncask "1password"\n')
			_write(os.path.join(root, "outdated.json"),
				json.dumps(self.OUTDATED_BEHIND_AT_REVISION))
			_write(os.path.join(root, "info.json"),
				json.dumps(self.INFO_BEHIND_AT_REVISION))
			env = dict(os.environ)
			env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
			env["STUB_OUTDATED"] = os.path.join(root, "outdated.json")
			env["STUB_INFO"] = os.path.join(root, "info.json")
			collected = subprocess.run(["bash", COLLECT_SH, "Brewfile"], cwd=work,
				env=env, capture_output=True, text=True, timeout=180)
			self.assertEqual(collected.returncode, 0, collected.stderr)
			collected_duti = json.loads(collected.stdout)["brew"][0]
			self.assertIs(collected_duti["pinned"], True)
			target = collected_duti["latest_version"]
			self.assertEqual(target, "1.5.5_1")
			preflight = subprocess.run([sys.executable, CHECK_PIN, "preflight",
				"--source", "brew", "--name", "duti", "--target-version", target],
				env=env, capture_output=True, text=True, timeout=60)
			self.assertEqual(preflight.returncode, 0,
				preflight.stdout + preflight.stderr)
			self.assertTrue(json.loads(preflight.stdout.strip())["match"])


if __name__ == "__main__":
	unittest.main(verbosity=2 if "-v" in sys.argv else 1)
