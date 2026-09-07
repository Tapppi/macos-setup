#!/usr/bin/env python3
"""
check_pin.py — WP5 (references/apply.md §Executing Upgrade Suggestions;
references/schemas.md §1.6 `target_version`/`version_pinned`): verifies that
an upgrade suggestion's synthesized `command` will install, or did install,
the exact version that was reviewed — never a silent fallback to whatever a
package manager resolves as "latest" at apply time.

Two independently useful checks, run at two different points in
references/apply.md's "Executing Upgrade Suggestions" flow:

  preflight  Before running `command`: would running it right now land on
             `target_version`, or has something newer been released since
             the tool was reviewed (renamed/removed/moved counts too — see
             below)? Read-only; never touches the system. Takes `--pinned`
             — pass the suggestion's own `version_pinned` (schemas.md
             §1.6) verbatim, never infer it from `--source`.

             `--pinned` (mise only, and only when `name` has no "@" —
             assemble.py's upgrade_command_and_runnable): the command
             already pins the exact version as a CLI argument
             (`mise upgrade name@x.y.z`), so nothing upstream can drift out
             from under it — a trivial match, no query. Without `--pinned`
             (brew, cask, or a mise name assemble.py could not safely pin),
             `command` always resolves to whatever the tap/mise currently
             calls latest, so preflight queries `brew info`/`mise outdated`
             and compares for real.

  verify     After running `command` (or after a human ran it manually,
             per references/apply.md's polling loop): does the *currently
             installed* version equal `target_version`? Catches the race a
             preflight cannot: an upstream release landing in the window
             between preflight and the command actually finishing.

Exit codes (both subcommands):
  0  match         installed/would-install version equals target_version.
  1  mismatch      a *different* version was found (older or newer) than
                    was reviewed. This is the drift this script exists to
                    catch — the caller must refuse (or report failure),
                    never treat this as done.
  2  indeterminate the tool could not be queried at all (renamed, removed
                    from the tap, moved to a different formula, a brew/mise
                    error). Treated identically to a mismatch by the
                    caller: refuse, never guess.

Every result is also printed to stdout as one line of JSON — {"phase",
"checked_at", "source", "name", "target_version", "observed_version",
"match", "reason"} — so the calling session can drop `reason` straight into
a status.json action's `--note`/`--detail-file` (references/apply.md
§Initializing status.json) without re-deriving prose. `phase` and
`checked_at` are written by the check itself (never by whoever saves the
file) specifically so `write_status.py record-pin-check` can refuse filing
a preflight result under "verify" or vice versa — a preflight and a verify
can otherwise produce byte-identical JSON for a source with nothing left to
distinguish them.

Usage:
  check_pin.py preflight --source {brew|cask|mise} --name NAME --target-version V [--pinned]
  check_pin.py verify    --source {brew|cask|mise} --name NAME --target-version V

Only brew/cask/mise are accepted sources (`assemble.PIN_CHECKABLE_SOURCES` —
imported, not re-typed, so this list and the one `write_status.py` gates
`"done"` on cannot silently drift apart). standalone and macos never get an
`auto_runnable` baseline (assemble.py's upgrade_command_and_runnable), so
apply never needs a preflight/verify for them here; standalone's own
"poll `<tool> --version`" step (references/apply.md) has no canonical
version-string format to parse and stays a manual comparison there rather
than gaining a false sense of automation here.

No network calls of its own — brew/mise make their own, exactly as the rest
of this skill already does (references/collection.md).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

# Sibling module in this same scripts/ directory — Python already puts a
# directly-run script's own directory on sys.path[0], the same way
# validate_items.py's plain `import assemble` resolves with no path
# manipulation, whether invoked as `python3 check_pin.py` or `./check_pin.py`.
import assemble

SOURCES = tuple(sorted(assemble.PIN_CHECKABLE_SOURCES))


def _run(cmd: list[str], timeout: int = 30):
	"""(returncode, stdout, stderr) — never raises. A missing binary, a
	non-zero exit, and a timeout all look the same to every caller below:
	rc != 0. `stdin=DEVNULL` matters here for the same reason
	collect_skill_drift.py's `_run` closes it: a `brew`/`mise` that decides
	to prompt (a first-run analytics prompt, an interactive tap-trust
	question) must fail fast inside `timeout` rather than sit until it
	expires with nothing on stderr to explain why. `except Exception` (not
	just OSError/TimeoutExpired) matches that same file's rationale: this
	script's one contract is "always one line of JSON on stdout"
	(module docstring), and a narrower catch that lets some other exception
	class (a decode error, anything subprocess can raise) propagate breaks
	that contract exactly like an uncaught `raise` would."""
	try:
		p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
			stdin=subprocess.DEVNULL)
		return p.returncode, p.stdout or "", p.stderr or ""
	except Exception as exc:  # noqa: BLE001 — deliberately broad, see above
		return 1, "", str(exc)


# ── preflight queries (read-only — never install anything) ──────────────────
def brew_formula_candidate_version(name: str):
	"""What `brew upgrade {name}` would resolve to right now, per `brew info`."""
	rc, out, err = _run(["brew", "info", "--json=v2", name])
	if rc != 0 or not out.strip():
		return None, f"`brew info --json=v2 {name}` failed: {(err or 'no output').strip()}"
	try:
		data = json.loads(out)
	except json.JSONDecodeError:
		return None, f"`brew info --json=v2 {name}` did not return valid JSON"
	formulae = data.get("formulae") or []
	if not formulae:
		return None, f"brew has no formula named {name!r} — renamed, removed from its tap, or moved"
	formula = formulae[0]
	stable = (formula.get("versions") or {}).get("stable")
	if not stable:
		return None, f"`brew info` for {name!r} lists no stable version"
	# `brew info`'s versions.stable never includes the packaging revision
	# (verified against a live `brew info --json=v2` — e.g. exiftool reports
	# stable "13.55" with a separate revision:1) but `brew outdated`'s
	# current_version — the string collect.sh writes into `latest_version`,
	# and therefore into this suggestion's target_version — composes them as
	# "13.55_1". Comparing the bare `stable` against a revisioned
	# target_version would misreport every revisioned formula as drifted.
	revision = formula.get("revision")
	if revision:
		stable = f"{stable}_{revision}"
	return stable, None


def brew_cask_candidate_version(name: str):
	"""What `brew upgrade --cask {name}` would resolve to right now."""
	rc, out, err = _run(["brew", "info", "--json=v2", "--cask", name])
	if rc != 0 or not out.strip():
		return None, f"`brew info --json=v2 --cask {name}` failed: {(err or 'no output').strip()}"
	try:
		data = json.loads(out)
	except json.JSONDecodeError:
		return None, f"`brew info --json=v2 --cask {name}` did not return valid JSON"
	casks = data.get("casks") or []
	if not casks:
		return None, f"brew has no cask named {name!r} — renamed, removed, or moved to another tap"
	version = casks[0].get("version")
	if not version:
		return None, f"`brew info --cask` for {name!r} lists no version"
	return version, None


def mise_candidate_version(name: str):
	"""What `mise upgrade {name}` (unpinned) would resolve to right now, per
	`mise outdated --json`. Only reached when `--pinned false` — see
	do_preflight: a genuinely pinned `mise upgrade {name}@{version}` has
	nothing to query (the version is a CLI argument), but
	assemble.py's `upgrade_command_and_runnable` falls back to this same
	unpinned command whenever `name` already contains `@`, so mise is not
	unconditionally exempt from preflight the way it used to be."""
	rc, out, err = _run(["mise", "outdated", "--json"])
	if rc != 0 or not out.strip():
		detail = f" (stderr: {err.strip()})" if err and err.strip() else ""
		return None, f"`mise outdated --json` failed{detail}"
	try:
		data = json.loads(out)
	except json.JSONDecodeError:
		return None, "`mise outdated --json` did not return valid JSON"
	entry = data.get(name) if isinstance(data, dict) else None
	if not entry:
		return None, f"mise reports no outdated entry for {name!r} — already current, or an unknown tool id"
	latest = entry.get("latest") if isinstance(entry, dict) else None
	if not latest:
		return None, f"mise's outdated entry for {name!r} has no 'latest' version"
	return latest, None


# ── verify queries (what is actually installed right now) ───────────────────
def brew_installed_versions(name: str, cask: bool = False):
	"""Every version currently installed side by side for this formula/cask
	(plural — see do_verify for why the caller checks membership rather than
	picking one)."""
	cmd = ["brew", "list", "--versions"]
	if cask:
		cmd.append("--cask")
	cmd.append(name)
	rc, out, err = _run(cmd)
	line = out.strip().splitlines()[0] if out.strip() else ""
	if rc != 0 or not line:
		detail = f" (stderr: {err.strip()})" if err and err.strip() else ""
		return None, f"`{' '.join(cmd)}` reported nothing installed{detail} — not installed, or name mismatch"
	parts = line.split()
	if len(parts) < 2:
		return None, f"`{' '.join(cmd)}` output could not be parsed: {line!r}"
	return parts[1:], None


def mise_installed_version(name: str):
	rc, out, err = _run(["mise", "current", name])
	version = out.strip().splitlines()[0].strip() if out.strip() else ""
	if rc != 0 or not version:
		detail = f" (stderr: {err.strip()})" if err and err.strip() else ""
		return None, f"`mise current {name}` reported nothing{detail} — not installed, or name mismatch"
	return version, None


# ── the two subcommands ───────────────────────────────────────────────────
# Neither function ever raises — an unsupported `source` reaching either
# (argparse's `choices=SOURCES` already rejects one at the CLI boundary, but
# these are plain functions a future caller could reach directly) returns
# the same indeterminate shape as any other "could not be checked" answer
# rather than an uncaught exception, which is not the "always one line of
# JSON on stdout" this script promises.
def do_preflight(source: str, name: str, target_version: str, pinned: bool = False):
	if source == "mise" and pinned:
		# The command itself (`mise upgrade name@version`) pins the version as
		# an argument — nothing upstream can drift out from under that, so
		# there is nothing to query. Trivial match, always; `reason` stays
		# None because emit() reads a non-None reason as "not a match". Only
		# reachable when the caller passes `--pinned` — assemble.py's
		# upgrade_command_and_runnable can hand back an *unpinned* mise
		# command too (when `name` already contains "@"), and that case must
		# fall through to a real query below like brew/cask, not take this
		# shortcut just because the source is mise.
		return target_version, target_version, None
	if source == "brew":
		observed, err = brew_formula_candidate_version(name)
	elif source == "cask":
		observed, err = brew_cask_candidate_version(name)
	elif source == "mise":
		observed, err = mise_candidate_version(name)
	else:
		return None, target_version, f"unsupported source for preflight: {source!r}"
	if observed is None:
		return None, target_version, err
	if observed != target_version:
		return observed, target_version, (
			f"reviewed version was {target_version}, but {source}:{name} would now resolve to "
			f"{observed} — refusing to auto-install a version that was not reviewed. Re-run "
			f"tool-update-review to review {observed}, or install {target_version} manually if "
			f"you specifically want the reviewed version."
		)
	return observed, target_version, None


def do_verify(source: str, name: str, target_version: str):
	if source in ("brew", "cask"):
		versions, err = brew_installed_versions(name, cask=(source == "cask"))
		if versions is None:
			return None, target_version, err
		if target_version in versions:
			# The reviewed version is genuinely installed — a match, whatever
			# else is also installed side by side and in whatever order brew
			# happens to list them. Checking membership rather than picking
			# "the last one" is what keeps a formula with old kegs still on
			# disk from reading as a false failure.
			return target_version, target_version, None
		observed = versions[-1]
	elif source == "mise":
		observed, err = mise_installed_version(name)
		if observed is None:
			return None, target_version, err
	else:
		return None, target_version, f"unsupported source for verify: {source!r}"
	if observed != target_version:
		return observed, target_version, (
			f"expected {name} to be at {target_version} after the upgrade, but found {observed} "
			f"installed — a newer release likely appeared between review and apply. Do not treat "
			f"this as the version that was reviewed; investigate before trusting it."
		)
	return observed, target_version, None


def emit(phase: str, source: str, name: str, target_version: str, observed_version, reason):
	"""`phase` and `checked_at` are recorded *by the check itself*, not
	supplied by whoever calls record-pin-check afterward — a preflight and a
	verify can otherwise emit byte-identical JSON for mise (both are "does
	X equal target_version"), and a result file's own phase field is what
	lets record-pin-check refuse filing one under the other's name (see
	write_status.py). `checked_at` further ties a recorded result to the
	moment this process actually ran, rather than to whenever someone
	happened to save the file."""
	match = reason is None
	result = {
		"phase": phase,
		"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"source": source,
		"name": name,
		"target_version": target_version,
		"observed_version": observed_version,
		"match": match,
		"reason": reason,
	}
	print(json.dumps(result))
	if match:
		return 0
	return 1 if observed_version is not None else 2


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = parser.add_subparsers(dest="cmd", required=True)

	for cmd_name in ("preflight", "verify"):
		p = sub.add_parser(cmd_name)
		p.add_argument("--source", required=True, choices=SOURCES)
		p.add_argument("--name", required=True)
		p.add_argument("--target-version", required=True, dest="target_version")
		if cmd_name == "preflight":
			# The suggestion's own `version_pinned` (references/schemas.md
			# §1.6), passed straight through — mise is `true` in the common
			# case but `false` whenever `name` already contains "@"
			# (assemble.py's upgrade_command_and_runnable), so this must
			# never be inferred from `--source mise` alone. Defaults to
			# unpinned: a caller that omits it gets the real, always-correct
			# query rather than a trivial match it never asked for.
			p.add_argument("--pinned", action="store_true", default=False)

	args = parser.parse_args()

	if args.cmd == "preflight":
		observed, target, reason = do_preflight(args.source, args.name, args.target_version, args.pinned)
	else:
		observed, target, reason = do_verify(args.source, args.name, args.target_version)

	sys.exit(emit(args.cmd, args.source, args.name, target, observed, reason))


if __name__ == "__main__":
	main()
