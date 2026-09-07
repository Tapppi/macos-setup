#!/usr/bin/env python3
"""
test_check_pin.py — the pin/refuse matrix for check_pin.py (WP5/I2).

Stdlib `unittest` only (no pytest, no fixtures directory, NO NETWORK), same
constraints and harness shape as test_collect.py: a temp workspace, a temp
`bin/` holding stub `brew`/`mise` executables placed first on PATH, and a
real `python3 check_pin.py ...` invocation whose stdout is parsed as JSON.
Nothing here touches the real Homebrew/mise install or the network.

What this file is about: **does check_pin.py ever call something a match
that is not the reviewed version, and does it refuse (never guess) when it
cannot tell?** Three outcomes throughout: match (exit 0), mismatch (exit 1,
a *different* version was actually found), indeterminate (exit 2, the tool
could not be queried at all — renamed, removed, moved, or a bare command
failure). A caller (references/apply.md) treats 1 and 2 identically:
refuse. This file keeps them distinguished anyway, because they print a
different `reason` a human has to be able to act on.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECK_PIN = os.path.join(SCRIPT_DIR, "check_pin.py")

sys.path.insert(0, SCRIPT_DIR)
import check_pin  # noqa: E402 — direct import, for the do_preflight/do_verify
                   # unit tests below that bypass argparse's own `choices=`
                   # gate on purpose (DirectFunctionCallTests)

# A stub `brew` that answers only what check_pin.py calls:
#   brew info --json=v2 [--cask] NAME     (preflight)
#   brew list --versions [--cask] NAME    (verify)
# Both read their canned answer out of the environment, keyed by name, so
# each test supplies exactly the fixture it needs and an unmatched name
# naturally reads back as "brew has never heard of this" — the same shape a
# renamed/removed/moved formula produces for real.
BREW_STUB = """#!/usr/bin/env bash
if [[ "${STUB_BREW_HARD_FAIL:-}" == "1" ]]; then
	echo "Error: stubbed brew failure" >&2
	exit 1
fi
if [[ "$1" == "info" && "$2" == "--json=v2" ]]; then
	if [[ "$3" == "--cask" ]]; then
		name="$4"
		if [[ -n "${STUB_CASK_INFO_RAW:-}" && "${name}" == "${STUB_CASK_NAME:-}" ]]; then
			printf '%s' "${STUB_CASK_INFO_RAW}"
		else
			echo '{"formulae":[],"casks":[]}'
		fi
	else
		name="$3"
		if [[ -n "${STUB_FORMULA_INFO_RAW:-}" && "${name}" == "${STUB_FORMULA_NAME:-}" ]]; then
			printf '%s' "${STUB_FORMULA_INFO_RAW}"
		else
			echo '{"formulae":[],"casks":[]}'
		fi
	fi
	exit 0
fi
if [[ "$1" == "list" && "$2" == "--versions" ]]; then
	if [[ "$3" == "--cask" ]]; then
		name="$4"
		[[ "${name}" == "${STUB_CASK_NAME:-}" ]] && echo "${name} ${STUB_CASK_INSTALLED:-}"
	else
		name="$3"
		[[ "${name}" == "${STUB_FORMULA_NAME:-}" ]] && echo "${name} ${STUB_FORMULA_INSTALLED:-}"
	fi
	exit 0
fi
exit 1
"""

MISE_STUB = """#!/usr/bin/env bash
if [[ "${STUB_MISE_HARD_FAIL:-}" == "1" ]]; then
	exit 1
fi
if [[ "$1" == "current" ]]; then
	name="$2"
	[[ "${name}" == "${STUB_MISE_NAME:-}" ]] && echo "${STUB_MISE_VERSION:-}"
	exit 0
fi
if [[ "$1" == "outdated" && "$2" == "--json" ]]; then
	printf '%s' "${STUB_MISE_OUTDATED_RAW:-\\{\\}}"
	exit 0
fi
exit 1
"""


def _write(path, text, mode=0o755):
	with open(path, "w", encoding="utf-8") as fh:
		fh.write(text)
	os.chmod(path, mode)


class CheckPinRunner(unittest.TestCase):
	"""Shared harness: every test runs the real check_pin.py against stubbed
	brew/mise executables placed first on PATH."""

	def run_check(self, args, env_extra=None, timeout=30):
		with tempfile.TemporaryDirectory(prefix="check-pin-test-") as root:
			bindir = os.path.join(root, "bin")
			os.makedirs(bindir)
			_write(os.path.join(bindir, "brew"), BREW_STUB)
			_write(os.path.join(bindir, "mise"), MISE_STUB)
			env = dict(os.environ)
			env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
			env.update(env_extra or {})
			return subprocess.run([sys.executable, CHECK_PIN] + args, env=env,
				capture_output=True, text=True, timeout=timeout)

	def result(self, args, env_extra=None):
		"""run_check(), parsed stdout as JSON alongside the raw process."""
		p = self.run_check(args, env_extra=env_extra)
		self.assertTrue(p.stdout.strip(), p.stderr)
		return json.loads(p.stdout), p


class PreflightBrewFormulaTests(CheckPinRunner):
	def test_matches_the_reviewed_version(self):
		out, p = self.result(
			["preflight", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman",
				"STUB_FORMULA_INFO_RAW": json.dumps({"formulae": [{"versions": {"stable": "5.5.1"}}], "casks": []})})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])
		self.assertEqual(out["observed_version"], "5.5.1")
		self.assertIsNone(out["reason"])

	def test_a_newer_release_since_review_is_a_mismatch_not_a_pass(self):
		# The exact defect: brew now resolves to something newer than what was
		# reviewed. This must refuse, never be waved through as "close enough".
		out, p = self.result(
			["preflight", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman",
				"STUB_FORMULA_INFO_RAW": json.dumps({"formulae": [{"versions": {"stable": "5.6.0"}}], "casks": []})})
		self.assertEqual(p.returncode, 1)
		self.assertFalse(out["match"])
		self.assertEqual(out["observed_version"], "5.6.0")
		self.assertIn("5.6.0", out["reason"])
		self.assertIn("5.5.1", out["reason"])

	def test_a_formula_brew_no_longer_knows_about_is_indeterminate(self):
		# Renamed, removed from its tap, or moved — refuse, never guess.
		out, p = self.result(
			["preflight", "--source", "brew", "--name", "ghost-formula", "--target-version", "1.0.0"], {})
		self.assertEqual(p.returncode, 2)
		self.assertFalse(out["match"])
		self.assertIsNone(out["observed_version"])
		self.assertIn("ghost-formula", out["reason"])

	def test_a_hard_brew_failure_is_indeterminate_not_a_crash(self):
		out, p = self.result(
			["preflight", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_BREW_HARD_FAIL": "1"})
		self.assertEqual(p.returncode, 2)
		self.assertFalse(out["match"])

	def test_invalid_json_from_brew_is_indeterminate_not_a_crash(self):
		out, p = self.result(
			["preflight", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman", "STUB_FORMULA_INFO_RAW": "not json { at all"})
		self.assertEqual(p.returncode, 2)
		self.assertFalse(out["match"])
		self.assertIn("JSON", out["reason"])

	def test_a_revisioned_formula_composes_stable_plus_revision(self):
		# `brew info`'s versions.stable never includes the packaging revision
		# (confirmed against a live `brew info --json=v2` — e.g. exiftool
		# reports stable "13.55" with a separate revision:1), but
		# target_version (from `brew outdated`, via collect.sh) is the
		# composed "13.55_1". A naive stable-only comparison would misreport
		# every revisioned formula as drifted.
		out, p = self.result(
			["preflight", "--source", "brew", "--name", "exiftool", "--target-version", "13.55_1"],
			{"STUB_FORMULA_NAME": "exiftool",
				"STUB_FORMULA_INFO_RAW": json.dumps(
					{"formulae": [{"versions": {"stable": "13.55"}, "revision": 1}], "casks": []})})
		self.assertEqual(p.returncode, 0, out)
		self.assertTrue(out["match"])
		self.assertEqual(out["observed_version"], "13.55_1")

	def test_zero_revision_is_not_appended(self):
		out, p = self.result(
			["preflight", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman",
				"STUB_FORMULA_INFO_RAW": json.dumps(
					{"formulae": [{"versions": {"stable": "5.5.1"}, "revision": 0}], "casks": []})})
		self.assertEqual(p.returncode, 0, out)
		self.assertTrue(out["match"])


class PreflightCaskTests(CheckPinRunner):
	def test_matches_the_reviewed_version(self):
		out, p = self.result(
			["preflight", "--source", "cask", "--name", "wireshark-app", "--target-version", "4.6.6"],
			{"STUB_CASK_NAME": "wireshark-app",
				"STUB_CASK_INFO_RAW": json.dumps({"formulae": [], "casks": [{"version": "4.6.6"}]})})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])

	def test_cask_moved_to_a_newer_version_is_a_mismatch(self):
		out, p = self.result(
			["preflight", "--source", "cask", "--name", "wireshark-app", "--target-version", "4.6.6"],
			{"STUB_CASK_NAME": "wireshark-app",
				"STUB_CASK_INFO_RAW": json.dumps({"formulae": [], "casks": [{"version": "4.7.0"}]})})
		self.assertEqual(p.returncode, 1)
		self.assertFalse(out["match"])
		self.assertEqual(out["observed_version"], "4.7.0")

	def test_a_cask_brew_no_longer_knows_about_is_indeterminate(self):
		out, p = self.result(
			["preflight", "--source", "cask", "--name", "ghost-cask", "--target-version", "1.0.0"], {})
		self.assertEqual(p.returncode, 2)
		self.assertFalse(out["match"])


class PreflightMiseTests(CheckPinRunner):
	def test_pinned_mise_preflight_is_a_trivial_match_and_never_shells_out(self):
		# --pinned is what tells check_pin.py the command already pins the
		# version as a CLI argument — nothing to query, and no STUB_MISE_*
		# env is even supplied, so a real shell-out would fail.
		out, p = self.result(
			["preflight", "--source", "mise", "--name", "node", "--target-version", "24.6.0", "--pinned"], {})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])
		self.assertEqual(out["observed_version"], "24.6.0")

	def test_unpinned_mise_preflight_actually_queries_outdated(self):
		# Without --pinned (the shape assemble.py falls back to when `name`
		# already contains "@" and pinning was refused), mise gets exactly
		# the same real preflight query as brew/cask, not the trivial
		# shortcut just because the source is mise.
		out, p = self.result(
			["preflight", "--source", "mise", "--name", "node", "--target-version", "24.6.0"],
			{"STUB_MISE_OUTDATED_RAW": json.dumps({"node": {"latest": "24.6.0"}})})
		self.assertEqual(p.returncode, 0, out)
		self.assertTrue(out["match"])

	def test_unpinned_mise_preflight_catches_drift(self):
		out, p = self.result(
			["preflight", "--source", "mise", "--name", "node", "--target-version", "24.6.0"],
			{"STUB_MISE_OUTDATED_RAW": json.dumps({"node": {"latest": "24.7.0"}})})
		self.assertEqual(p.returncode, 1)
		self.assertFalse(out["match"])
		self.assertEqual(out["observed_version"], "24.7.0")

	def test_unpinned_mise_preflight_with_no_outdated_entry_is_indeterminate(self):
		out, p = self.result(
			["preflight", "--source", "mise", "--name", "node", "--target-version", "24.6.0"],
			{"STUB_MISE_OUTDATED_RAW": json.dumps({})})
		self.assertEqual(p.returncode, 2)
		self.assertFalse(out["match"])


class VerifyTests(CheckPinRunner):
	def test_brew_formula_installed_version_matches(self):
		out, p = self.result(
			["verify", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman", "STUB_FORMULA_INSTALLED": "5.5.1"})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])

	def test_brew_formula_drifted_past_the_reviewed_version_is_a_mismatch(self):
		# The command ran (or the user ran it themselves) and landed on
		# something other than what was reviewed — never "done" for this.
		out, p = self.result(
			["verify", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman", "STUB_FORMULA_INSTALLED": "5.6.0"})
		self.assertEqual(p.returncode, 1)
		self.assertFalse(out["match"])
		self.assertEqual(out["observed_version"], "5.6.0")

	def test_multiple_installed_versions_matches_by_membership(self):
		out, p = self.result(
			["verify", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman", "STUB_FORMULA_INSTALLED": "4.9.3 5.5.1"})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])

	def test_reviewed_version_not_last_on_the_line_still_matches(self):
		# brew does not promise chronological/ascending order in `--versions`
		# output. Checking membership rather than trusting position is what
		# keeps an old keg still on disk from making a genuinely-landed
		# upgrade read as a false failure — the exact opposite ordering from
		# the case above would have failed under a "take the last token"
		# rule.
		out, p = self.result(
			["verify", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"],
			{"STUB_FORMULA_NAME": "podman", "STUB_FORMULA_INSTALLED": "5.5.1 4.9.3"})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])
		self.assertEqual(out["observed_version"], "5.5.1")

	def test_not_installed_at_all_is_indeterminate(self):
		out, p = self.result(
			["verify", "--source", "brew", "--name", "podman", "--target-version", "5.5.1"], {})
		self.assertEqual(p.returncode, 2)
		self.assertFalse(out["match"])
		self.assertIsNone(out["observed_version"])

	def test_cask_installed_version_matches(self):
		out, p = self.result(
			["verify", "--source", "cask", "--name", "wireshark-app", "--target-version", "4.6.6"],
			{"STUB_CASK_NAME": "wireshark-app", "STUB_CASK_INSTALLED": "4.6.6"})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])

	def test_mise_installed_version_matches(self):
		out, p = self.result(
			["verify", "--source", "mise", "--name", "node", "--target-version", "24.6.0"],
			{"STUB_MISE_NAME": "node", "STUB_MISE_VERSION": "24.6.0"})
		self.assertEqual(p.returncode, 0)
		self.assertTrue(out["match"])

	def test_mise_drifted_past_the_reviewed_version_is_a_mismatch(self):
		out, p = self.result(
			["verify", "--source", "mise", "--name", "node", "--target-version", "24.6.0"],
			{"STUB_MISE_NAME": "node", "STUB_MISE_VERSION": "24.7.0"})
		self.assertEqual(p.returncode, 1)
		self.assertFalse(out["match"])

	def test_mise_hard_failure_is_indeterminate_not_a_crash(self):
		out, p = self.result(
			["verify", "--source", "mise", "--name", "node", "--target-version", "24.6.0"],
			{"STUB_MISE_HARD_FAIL": "1"})
		self.assertEqual(p.returncode, 2)
		self.assertFalse(out["match"])


class CliShapeTests(CheckPinRunner):
	def test_unsupported_source_is_rejected_by_argparse(self):
		p = self.run_check(
			["preflight", "--source", "standalone", "--name", "x", "--target-version", "1.0.0"])
		self.assertNotEqual(p.returncode, 0)
		self.assertIn("invalid choice", p.stderr)

	def test_missing_required_argument_is_rejected(self):
		p = self.run_check(["preflight", "--source", "brew", "--name", "podman"])
		self.assertNotEqual(p.returncode, 0)
		self.assertIn("target-version", p.stderr)

	def test_emitted_json_stamps_its_own_phase_and_timestamp(self):
		# write_status.py's record-pin-check refuses to file a preflight
		# result as a verify (or vice versa) by reading these back — they
		# have to actually be there, stamped by the check itself, not left
		# for whoever saves the file to assert.
		for phase in ("preflight", "verify"):
			with self.subTest(phase):
				out, p = self.result(
					[phase, "--source", "mise", "--name", "node", "--target-version", "24.6.0"] +
					(["--pinned"] if phase == "preflight" else []),
					{"STUB_MISE_NAME": "node", "STUB_MISE_VERSION": "24.6.0"})
				self.assertEqual(out["phase"], phase)
				self.assertRegex(out["checked_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class DirectFunctionCallTests(unittest.TestCase):
	"""do_preflight/do_verify called directly, bypassing argparse's own
	`choices=SOURCES` gate on purpose — a future caller that imports this
	module (write_status.py's "done" gate, a test) is not guaranteed to go
	through the CLI, and the module docstring's one contract is "always a
	line of JSON on stdout", not "unless called wrong". Both used to `raise
	ValueError` on an unrecognized source; that is exactly the class of bug
	criterion 4 exists to catch — a crash instead of a structured refusal."""

	def test_do_preflight_never_raises_on_an_unrecognized_source(self):
		observed, target, reason = check_pin.do_preflight("standalone", "foo", "1.0.0")
		self.assertIsNone(observed)
		self.assertEqual(target, "1.0.0")
		self.assertIn("unsupported source", reason)

	def test_do_verify_never_raises_on_an_unrecognized_source(self):
		observed, target, reason = check_pin.do_verify("macos", "Safari", "15.7")
		self.assertIsNone(observed)
		self.assertEqual(target, "15.7")
		self.assertIn("unsupported source", reason)

	def test_sources_come_from_assemble_not_a_second_hand_typed_list(self):
		# The list check_pin.py accepts and the one write_status.py gates
		# "done" on must be the same set — a hand-copied duplicate is exactly
		# how this project's own curl/PATH precedent went stale silently.
		self.assertEqual(set(check_pin.SOURCES), check_pin.assemble.PIN_CHECKABLE_SOURCES)

	def test_run_never_raises_on_a_missing_binary(self):
		rc, out, err = check_pin._run(["this-binary-does-not-exist-anywhere-xyz"])
		self.assertNotEqual(rc, 0)
		self.assertEqual(out, "")
		self.assertTrue(err)


if __name__ == "__main__":
	unittest.main(verbosity=2)
