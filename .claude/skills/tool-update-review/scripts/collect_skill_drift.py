#!/usr/bin/env python3
"""
collect_skill_drift.py — vendored agent-skill drift, as a collect.sh finding source.
Usage: collect_skill_drift.py [--dotfiles-root PATH] [--timeout SECONDS] [--no-network]

Prints ONE JSON object (the `skill_drift` value of collect.json, see
references/collection.md §Skill-Drift Collection) to stdout:
`{"findings": [...], "suppressed": [...]}`.

The skills under `dotfiles/config/agent-skills/` are vendored copies of
upstream repos. Nothing else in tool-update-review can see them drift: they
carry no version number, so the brew/mise/standalone version machinery has
nothing to compare. What they do carry is git provenance, so drift is decided
by a three-way comparison of git TREE hashes — content-addressed and identical
across repositories for identical content, which makes equality exact rather
than heuristic, and needs no blob download:

    LOCAL     git -C dotfiles rev-parse HEAD:<local_path>
    BASELINE  the pristine upstream content at the recorded sync point
    UPSTREAM  the same subpath at the upstream branch head right now

Keying the state on (LOCAL vs BASELINE, BASELINE vs UPSTREAM) rather than on
LOCAL vs UPSTREAM is the entire point: a deliberate local patch and an
upstream release both make local != upstream, and only the baseline tells
them apart. Reporting "sync this" for a local patch would ask the user to
throw their own edit away.

Provenance is already recorded; this script only reads it:
  - subtree vendors — `git log --grep=git-subtree-dir` finds the squash
    commit, whose `git-subtree-split:` trailer names the synced upstream SHA
    and whose own tree IS the pristine upstream content, so BASELINE resolves
    with no network at all.
  - sparse vendors — `Last synced commit:` in the vendor's CUSTOMISATION.md;
    no offline baseline exists, so that one costs a second fetch.
  - the vendor -> (url, branch, kind) mapping and the adopted-skill set are
    parsed out of sync-upstream.sh and .claude-plugin/marketplace.json.
    `tapppi/` appears in neither vendor table, so our own skills fall out of
    scope by construction rather than by a hardcoded skip-list.

Best-effort by construction, like the brew-health parser in collect.sh: this
runs inside a collector that must not abort. Every subprocess call carries an
explicit timeout, no exception escapes main(), and every failure still exits 0
with a parseable object. Failures degrade at the narrowest scope that failed
and always leave a trace on the page, never only in `suppressed` (which is
logged to stderr and never reaches report.json):
  - one adopted skill — a suppressed line, or, when only UPSTREAM is missing,
    an `upstream removed or renamed it` finding;
  - one vendor whose upstream cannot be reached — ONE quiet per-vendor
    `probe_error` finding, never one unknown card per adopted skill;
  - the whole source (no dotfiles checkout, no parseable vendor tables, an
    unexpected exception) — ONE `skill-drift:source-unavailable` finding, so a
    run that checked nothing cannot render as a run that found nothing wrong.
"""
from __future__ import annotations  # keeps `X | None` annotations legal on
                                    # Python 3.9 (macOS's bundled python3 —
                                    # same constraint as assemble.py)

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile


# Vendored skills live at this path inside the dotfiles repo; the vendor
# tables record prefixes relative to that repo root, so this is also the
# prefix stripped off to match a marketplace entry's "./<vendor>/..." source.
SKILLS_SUBDIR = "config/agent-skills"
SYNC_SCRIPT_REL = SKILLS_SUBDIR + "/sync-upstream.sh"
MARKETPLACE_REL = SKILLS_SUBDIR + "/.claude-plugin/marketplace.json"
# Run from the dotfiles repo root — the script cds there itself, but the
# command we hand the user is the one its own header documents.
SYNC_COMMAND = "bash " + SKILLS_SUBDIR + "/sync-upstream.sh"
LOCAL_GIT_TIMEOUT = 15


# ── subprocess / IO plumbing — nothing below may raise ───────────────────────
def _run(cmd: list, timeout: int, cwd: str | None = None):
	"""(returncode, stdout, stderr), never raising. A missing binary, a
	non-zero exit and a timeout all look the same to the caller: rc != 0.

	stdin is closed and `GIT_TERMINAL_PROMPT=0` is exported so a probe against
	a private or moved upstream fails inside `timeout` instead of blocking on
	a credential prompt: the collector runs unattended, and there is nobody to
	answer one. It happens to work on this machine only because the keychain
	answers for git; that is a property of one laptop, not of the detector."""
	try:
		p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
			cwd=cwd, stdin=subprocess.DEVNULL,
			env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
		return p.returncode, p.stdout or "", p.stderr or ""
	except Exception as exc:  # timeout, missing git, permission, anything
		return 1, "", str(exc)


def _read(path: str) -> str | None:
	try:
		with open(path, "r", encoding="utf-8", errors="replace") as fh:
			return fh.read()
	except Exception:
		return None


def _ere_escape(text: str) -> str:
	"""Escape for POSIX ERE (git log --grep -E), not for Python's re — the two
	disagree about which characters may legally carry a backslash."""
	return re.sub(r'([.\[\]{}()*+?^$|\\])', r'\\\1', text)


# ── Provenance parsers ───────────────────────────────────────────────────────
def _array_body(text: str, name: str) -> str:
	"""The lines between `<name>=(` and its closing `)`, or "" if absent.
	Anchored at line start so `vendors` does not match `sparse_vendors`."""
	m = re.search(r'^[ \t]*' + name + r'=\([ \t]*$(.*?)^[ \t]*\)[ \t]*$',
		text, re.M | re.S)
	return m.group(1) if m else ""


def _table_rows(body: str) -> list:
	"""Pipe-delimited, optionally-quoted rows; comments and blanks dropped."""
	rows = []
	for raw in body.splitlines():
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		if len(line) >= 2 and line[0] == line[-1] and line[0] in ('"', "'"):
			line = line[1:-1]
		fields = [f.strip() for f in line.split("|")]
		if len(fields) != 4 or not all(fields[:3]):
			continue
		rows.append(fields)
	return rows


def parse_vendor_tables(sync_script_text) -> list:
	"""Parse sync-upstream.sh's `vendors=()` and `sparse_vendors=()` tables.

	Returns dicts: {kind:"subtree", prefix, url, branch, skills:[str]}
	             | {kind:"sparse",  dest,   url, branch, subpath}
	An unparseable or absent table yields [] rather than raising — this is a
	textual parse of a file the detector does not own, and a sync-upstream.sh
	rewrite must degrade the source, not break the collector.
	"""
	try:
		if not isinstance(sync_script_text, str):
			return []
		vendors = []
		for prefix, url, branch, skills_str in _table_rows(_array_body(sync_script_text, "vendors")):
			vendors.append({"kind": "subtree", "prefix": prefix.rstrip("/"),
				"url": url, "branch": branch, "skills": skills_str.split()})
		for dest, url, branch, subpath in _table_rows(_array_body(sync_script_text, "sparse_vendors")):
			if not subpath:
				continue
			vendors.append({"kind": "sparse", "dest": dest.rstrip("/"),
				"url": url, "branch": branch, "subpath": subpath.strip("/")})
		return vendors
	except Exception:
		return []


def parse_marketplace_adopted(marketplace_json_text) -> list:
	"""The adopted skills from .claude-plugin/marketplace.json.

	ONLY entries whose "source" is a STRING starting "./" are vendored here.
	An OBJECT source (git-subdir, e.g. find-skills) means Claude Code resolves
	the plugin itself from its own upstream — nothing of it is committed to
	this repo, so it is tool-owned and there is no local copy that could
	drift. Returns [{name, rel_path}] with the "./" stripped.
	"""
	try:
		if not isinstance(marketplace_json_text, str) or not marketplace_json_text.strip():
			return []
		data = json.loads(marketplace_json_text)
		if not isinstance(data, dict):
			return []
		plugins = data.get("plugins")
		if not isinstance(plugins, list):
			return []
		adopted = []
		for entry in plugins:
			if not isinstance(entry, dict):
				continue
			source = entry.get("source")
			if not isinstance(source, str) or not source.startswith("./"):
				continue
			rel_path = source[2:].strip().rstrip("/")
			if not rel_path:
				continue
			name = entry.get("name") or os.path.basename(rel_path)
			adopted.append({"name": str(name), "rel_path": rel_path})
		return adopted
	except Exception:
		return []


def classify(local, baseline, upstream) -> str:
	"""Three-way tree-hash comparison -> drift_state.

	Any unresolved side -> 'probe_error' (an unknown is never reported as a
	state we did not measure). Otherwise the state is keyed on the two
	comparisons that can tell a local patch from an upstream release.
	"""
	if local is None or baseline is None or upstream is None:
		return "probe_error"
	if local == baseline:
		return "in_sync" if baseline == upstream else "upstream_ahead"
	return "local_only" if baseline == upstream else "diverged"


def subtree_baseline_sha(dotfiles_root: str, prefix: str):
	"""The most recent `git subtree --squash` commit for `prefix`.

	Returns (squash_commit_sha, subtree_split_sha) — the first is a commit in
	THIS repo whose tree is the pristine upstream content (so BASELINE needs
	no network), the second names the upstream commit it came from. (None,
	None) when no such commit exists.
	"""
	try:
		if not prefix:
			return (None, None)
		pattern = "^git-subtree-dir: " + _ere_escape(prefix.rstrip("/")) + "$"
		# One `git log`, not two: `%H%n%B` puts the sha on the first line and
		# the raw body (which carries the trailers) on the rest, so the commit
		# never has to be looked up a second time to read its own message.
		rc, out, _ = _run(["git", "-C", dotfiles_root, "log", "-n", "1",
			"--extended-regexp", "--grep", pattern, "--format=%H%n%B"], LOCAL_GIT_TIMEOUT)
		if rc != 0 or not out.strip():
			return (None, None)
		commit, _, body = out.partition("\n")
		commit = commit.strip()
		if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
			return (None, None)
		m = re.search(r'^git-subtree-split:\s*([0-9a-fA-F]{7,40})\s*$', body, re.M)
		return (commit, m.group(1).lower() if m else None)
	except Exception:
		return (None, None)


def sparse_baseline_sha(customisation_md_text):
	"""`Last synced commit: <40-hex>` from a sparse vendor's CUSTOMISATION.md."""
	try:
		if not isinstance(customisation_md_text, str):
			return None
		m = re.search(r'Last synced commit:\s*`?([0-9a-fA-F]{40})`?',
			customisation_md_text, re.I)
		return m.group(1).lower() if m else None
	except Exception:
		return None



# ── Vendor / skill geometry ──────────────────────────────────────────────────
def _vendor_path(vendor: dict) -> str:
	"""The vendor's repo-relative root: a subtree's prefix, a sparse copy's dest."""
	return (vendor.get("prefix") if vendor.get("kind") == "subtree"
		else vendor.get("dest")) or ""


def _vendor_rel(vendor: dict) -> str:
	"""The vendor path relative to config/agent-skills — the form a
	marketplace `"./<...>"` source is written in."""
	path = _vendor_path(vendor).rstrip("/")
	if path == SKILLS_SUBDIR:
		return ""
	if path.startswith(SKILLS_SUBDIR + "/"):
		return path[len(SKILLS_SUBDIR) + 1:]
	return path


def _vendor_name(vendor: dict) -> str:
	"""The upstream's short name — `anthropics`, `google`, `softaworks`. A
	sparse vendor's dest names the skill (`softaworks/jira`), so the vendor is
	its parent directory."""
	rel = _vendor_rel(vendor)
	if vendor.get("kind") == "sparse" and "/" in rel:
		return rel.split("/")[0]
	return os.path.basename(_vendor_path(vendor).rstrip("/")) or rel or "vendor"


def _vendor_dir(vendor: dict) -> str:
	"""Where the vendor's CUSTOMISATION.md lives."""
	path = _vendor_path(vendor).rstrip("/")
	if vendor.get("kind") == "sparse":
		return os.path.dirname(path) or path
	return path


def _match_vendor(vendors: list, rel_path: str):
	"""Index of the vendor owning `rel_path`, by longest matching prefix, or
	None. `tapppi/…` matches nothing because it is in neither vendor table —
	our own skills are out of scope by construction, not by a skip-list."""
	best, best_len = None, -1
	for idx, vendor in enumerate(vendors):
		rel = _vendor_rel(vendor)
		if not rel:
			continue
		if rel_path == rel or rel_path.startswith(rel + "/"):
			if len(rel) > best_len:
				best, best_len = idx, len(rel)
	return best


def _remainder(vendor: dict, rel_path: str) -> str:
	rel = _vendor_rel(vendor)
	return rel_path[len(rel) + 1:] if len(rel_path) > len(rel) else ""


def _join(*parts) -> str:
	return "/".join(p.strip("/") for p in parts if p)


def _tree_sha(repo: str, rev: str, subpath: str, timeout: int):
	"""The git tree hash of `subpath` at `rev`, or None if it does not
	resolve. Content-addressed, so equal hashes mean byte-equal trees even
	across unrelated repositories — which is what makes the comparison exact
	rather than a heuristic."""
	target = "{}:{}".format(rev, subpath) if subpath else "{}^{{tree}}".format(rev)
	rc, out, _ = _run(["git", "-C", repo, "rev-parse", target], timeout)
	if rc != 0:
		return None
	sha = out.strip()
	return sha if re.fullmatch(r'[0-9a-f]{40,64}', sha) else None


# ── Upstream probe ───────────────────────────────────────────────────────────
def _probe_vendor(vendor: dict, dotfiles_root: str, probe_repo: str,
		timeout: int, no_network: bool):
	"""Resolve where BASELINE and UPSTREAM are read from for one vendor.

	Returns (probe, error). `probe` carries baseline_repo/baseline_rev (the
	revision whose tree is the pristine synced content), baseline_sha (the
	upstream commit that was synced) and upstream_sha. `error` is a short
	human reason; when it is set, the vendor gets ONE probe_error finding and
	its skills are suppressed.
	"""
	kind = vendor.get("kind")
	url, branch = vendor.get("url", ""), vendor.get("branch", "")
	baseline_repo = baseline_rev = baseline_sha = None

	if kind == "subtree":
		# The squash commit's own tree IS the upstream content at the sync
		# point, so the baseline costs no network at all.
		squash, split = subtree_baseline_sha(dotfiles_root, vendor.get("prefix", ""))
		if squash is None:
			return None, ("no `git subtree --squash` commit recorded for "
				"`{}`".format(vendor.get("prefix", "")))
		baseline_repo, baseline_rev, baseline_sha = dotfiles_root, squash, (split or squash)
	else:
		cust = _read(os.path.join(dotfiles_root, _vendor_dir(vendor), "CUSTOMISATION.md"))
		baseline_sha = sparse_baseline_sha(cust or "")
		if baseline_sha is None:
			return None, ("no `Last synced commit:` recorded in "
				"`{}/CUSTOMISATION.md`".format(_vendor_dir(vendor)))

	if no_network:
		return None, "network probes are disabled (--no-network)"

	rc, _, err = _run(["git", "init", "-q", probe_repo], timeout)
	if rc != 0:
		return None, "could not create a probe repository ({})".format(_last_line(err))
	rc, _, err = _run(["git", "-C", probe_repo, "remote", "add", "origin", url], LOCAL_GIT_TIMEOUT)
	if rc != 0:
		return None, "could not register upstream {} ({})".format(url, _last_line(err))

	ok, err = _fetch(probe_repo, branch, timeout)
	if not ok:
		return None, "fetching {} ({}) failed: {}".format(url, branch, err)
	rc, out, _ = _run(["git", "-C", probe_repo, "rev-parse", "FETCH_HEAD"], LOCAL_GIT_TIMEOUT)
	upstream_sha = out.strip() if rc == 0 else ""
	if not re.fullmatch(r'[0-9a-f]{40,64}', upstream_sha):
		return None, "upstream {} ({}) resolved to no commit".format(url, branch)

	if kind != "subtree":
		# No offline baseline for a sparse vendor — its synced commit has to
		# be fetched too, unless upstream has not moved off it.
		if baseline_sha != upstream_sha:
			ok, err = _fetch(probe_repo, baseline_sha, timeout)
			if not ok:
				return None, ("recorded sync commit {} is unreachable in {}: "
					"{}".format(baseline_sha[:12], url, err))
		baseline_repo, baseline_rev = probe_repo, baseline_sha

	return {"baseline_repo": baseline_repo, "baseline_rev": baseline_rev,
		"baseline_sha": baseline_sha, "upstream_sha": upstream_sha,
		"probe_repo": probe_repo}, None


def _fetch(repo: str, ref: str, timeout: int):
	"""Trees-only, single-commit fetch of one ref. ~0.7s per ref measured
	against GitHub, and no blob is ever downloaded."""
	rc, _, err = _run(["git", "-C", repo, "fetch", "--filter=blob:none",
		"--depth", "1", "--quiet", "origin", ref], timeout)
	return rc == 0, _last_line(err)


def _last_line(text: str) -> str:
	lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
	return lines[-1][:160] if lines else "no error output"


# ── Finding construction ─────────────────────────────────────────────────────
SEVERITY = {"upstream_ahead": "notable", "diverged": "warning",
	"local_only": "info", "probe_error": "info"}


def _finding(**kw) -> dict:
	"""One finding with every contract key present — assemble.py reads these
	by name, so a missing key must never be an absent key."""
	base = {"id": None, "name": None, "source": "skill-drift", "drift_state": None,
		"severity": "info", "detail": "", "vendor": None, "skill": None,
		"vendor_kind": None, "upstream_url": None, "upstream_branch": None,
		"upstream_subpath": None, "local_path": None, "baseline_sha": None,
		"upstream_sha": None, "expected": False, "remediation": None,
		"pinned": False, "current_version": None, "latest_version": None}
	base.update(kw)
	return base


def _remediation(vendor_name: str, drifted: int) -> dict:
	label = "Sync {} from upstream".format(vendor_name)
	if drifted > 1:
		label += " (updates all {} drifted {} skills)".format(drifted, vendor_name)
	return {"command": SYNC_COMMAND, "auto_runnable": False,
		"needs_sudo": False, "label": label}


def _detail(state, skill, vendor, vendor_dir, url, branch, subpath,
		baseline_sha, upstream_sha, drifted) -> str:
	base = (baseline_sha or "?")[:12]
	head = (upstream_sha or "?")[:12]
	also = ("" if drifted <= 1 else
		" The sync is per-vendor, so that one run also refreshes the other {} drifted "
		"`{}` skill{}.".format(drifted - 1, vendor, "" if drifted == 2 else "s"))
	if state == "upstream_ahead":
		return ("The vendored copy of `{skill}` still matches the recorded `{vendor}` sync "
			"({base}), but upstream {url} ({branch}) has moved on `{subpath}` since — it is "
			"now at {head}. Nothing local is at risk: re-sync the vendor with `{cmd}` from the "
			"dotfiles repo root, on a clean tree.{also}").format(
			skill=skill, vendor=vendor, base=base, url=url, branch=branch,
			subpath=subpath, head=head, cmd=SYNC_COMMAND, also=also)
	if state == "diverged":
		return ("The vendored copy of `{skill}` is patched relative to the recorded `{vendor}` "
			"sync ({base}) AND upstream {url} ({branch}) has moved on `{subpath}` to {head}. "
			"Syncing needs conflict review rather than a straight pull: check that the local "
			"patches listed in `{dir}/CUSTOMISATION.md` survive it. Run `{cmd}` from the "
			"dotfiles repo root, on a clean tree.{also}").format(
			skill=skill, vendor=vendor, base=base, url=url, branch=branch,
			subpath=subpath, head=head, dir=vendor_dir, cmd=SYNC_COMMAND, also=also)
	return ("The vendored copy of `{skill}` differs from the recorded `{vendor}` sync "
		"({base}), and upstream {url} ({branch}) has not moved on `{subpath}` since — so "
		"this is a deliberate local patch, not upstream drift. It should be listed in "
		"`{dir}/CUSTOMISATION.md`. Nothing to do; a sync would discard it.").format(
		skill=skill, vendor=vendor, base=base, url=url, branch=branch,
		subpath=subpath, dir=vendor_dir)


def _whole_source_failure(reason: str):
	"""(findings, suppressed) for a failure that stopped the ENTIRE source.

	Suppressing it and nothing else would be silent in the worst way:
	`suppressed` is logged to stderr and never reaches report.json or the
	page, so a run that could check nothing at all would render exactly like a
	run that checked everything and found it in sync. A missing `dotfiles/`
	checkout, or a restructured `sync-upstream.sh`, would read as "all good".
	So the source says out loud that it did not run — one quiet, expected
	card, the same shape the per-vendor probe failure uses."""
	return [_finding(
		id="skill-drift:source-unavailable",
		name="Vendored-skill check did not run",
		drift_state="probe_error", severity=SEVERITY["probe_error"],
		detail=(reason + " No vendored skill was checked this run — nothing is "
			"known to be wrong with any of them, and nothing here needs deciding."),
		expected=True)], [reason]


def _emit(findings: list, suppressed: list) -> int:
	print(json.dumps({"findings": findings, "suppressed": suppressed}))
	return 0


# ── Detection ────────────────────────────────────────────────────────────────
def detect(dotfiles_root: str, timeout: int, no_network: bool):
	"""(findings, suppressed) for one dotfiles checkout."""
	root = os.path.abspath(dotfiles_root)
	if not os.path.isdir(root):
		return _whole_source_failure(
			"There is no dotfiles checkout at `{}`.".format(dotfiles_root))
	rc, _, _ = _run(["git", "-C", root, "rev-parse", "--git-dir"], LOCAL_GIT_TIMEOUT)
	if rc != 0:
		return _whole_source_failure(
			"`{}` is not a git checkout, and vendored-skill drift is read from git "
			"provenance.".format(dotfiles_root))

	sync_text = _read(os.path.join(root, SYNC_SCRIPT_REL))
	if sync_text is None:
		return _whole_source_failure(
			"`{}` was not found under `{}`, so the vendor -> upstream tables could not "
			"be read.".format(SYNC_SCRIPT_REL, dotfiles_root))
	vendors = parse_vendor_tables(sync_text)
	if not vendors:
		return _whole_source_failure(
			"No vendor tables could be parsed out of `{}`.".format(SYNC_SCRIPT_REL))
	adopted = parse_marketplace_adopted(_read(os.path.join(root, MARKETPLACE_REL)) or "")
	if not adopted:
		return _whole_source_failure(
			"No vendored plugin entries were found in `{}`.".format(MARKETPLACE_REL))

	suppressed = []
	buckets = {}
	for entry in adopted:
		idx = _match_vendor(vendors, entry["rel_path"])
		if idx is None:
			suppressed.append("{} — own skill, no upstream vendor in "
				"sync-upstream.sh".format(entry["rel_path"]))
			continue
		buckets.setdefault(idx, []).append(entry)

	findings = []
	seen_probe_ids = set()
	with tempfile.TemporaryDirectory(prefix="skill-drift-") as tmp_root:
		for idx, vendor in enumerate(vendors):
			entries = buckets.get(idx)
			if not entries:
				continue
			name = _vendor_name(vendor)
			url, branch = vendor.get("url", ""), vendor.get("branch", "")
			probe, error = _probe_vendor(vendor, root,
				os.path.join(tmp_root, "probe-{}".format(idx)), timeout, no_network)
			if error is not None:
				# ONE quiet card per vendor — never one unknown card per skill.
				probe_id = "skill-drift:{}:probe-failed".format(name)
				for entry in entries:
					suppressed.append("{}/{} not verified — the upstream probe for `{}` "
						"failed".format(name, entry["name"], name))
				if probe_id in seen_probe_ids:
					continue
				seen_probe_ids.add(probe_id)
				findings.append(_finding(
					id=probe_id, name="Upstream probe failed: {}".format(name),
					drift_state="probe_error", severity=SEVERITY["probe_error"],
					detail=("Could not verify the vendored `{name}` skills against upstream "
						"{url} ({branch}): {why}. The {n} adopted `{name}` skill{s} {verb} not "
						"checked this run — nothing is known to be wrong with {obj}, and "
						"nothing needs deciding.").format(name=name, url=url, branch=branch,
						why=error, n=len(entries),
						s="" if len(entries) == 1 else "s",
						verb="was" if len(entries) == 1 else "were",
						obj="it" if len(entries) == 1 else "them"),
					vendor=name, skill=None, vendor_kind=vendor.get("kind"),
					upstream_url=url, upstream_branch=branch,
					local_path=_vendor_path(vendor), expected=True))
				continue

			# Two passes: classify everything first, because the remediation
			# label has to state how many of this vendor's skills one sync fixes.
			results = []
			for entry in entries:
				remainder = _remainder(vendor, entry["rel_path"])
				if vendor.get("kind") == "subtree":
					local_path = _join(vendor.get("prefix"), remainder)
					subpath = remainder
				else:
					local_path = _join(vendor.get("dest"), remainder)
					subpath = _join(vendor.get("subpath"), remainder)
				local = _tree_sha(root, "HEAD", local_path, LOCAL_GIT_TIMEOUT)
				baseline = _tree_sha(probe["baseline_repo"], probe["baseline_rev"],
					subpath, LOCAL_GIT_TIMEOUT)
				upstream = _tree_sha(probe["probe_repo"], probe["upstream_sha"],
					subpath, LOCAL_GIT_TIMEOUT)
				results.append((entry, local_path, subpath, local, baseline, upstream,
					classify(local, baseline, upstream)))
			drifted = sum(1 for r in results if r[6] in ("upstream_ahead", "diverged"))

			for entry, local_path, subpath, local, baseline, upstream, state in results:
				skill = entry["name"]
				if state == "in_sync":
					suppressed.append("{}/{} in sync with upstream {} ({}) at {}".format(
						name, skill, url, branch, probe["upstream_sha"][:12]))
					continue
				if state == "probe_error":
					# One of the three trees did not resolve. WHICH one is the
					# whole story, so the line says it — and one combination is
					# not a probe failure at all.
					if local is not None and baseline is not None:
						# Local and baseline both resolve and upstream does not:
						# the skill existed at the sync point and is absent from
						# upstream now, so upstream deleted or renamed it. That is
						# a real event with a real consequence — the next vendor
						# sync quietly stops shipping this skill — so it is a
						# finding to decide on, not a suppressed line. No
						# remediation: running the sync is what loses the skill.
						findings.append(_finding(
							id="skill-drift:{}/{}".format(name, skill),
							name="{} ({})".format(skill, name),
							drift_state="probe_error", severity="notable",
							detail=(
								"The vendored copy of `{skill}` is still here and still matches the "
								"recorded `{vendor}` sync ({base}), but `{subpath}` no longer exists "
								"in upstream {url} ({branch}) at {head} — upstream has removed or "
								"renamed it. Syncing `{vendor}` would therefore stop shipping this "
								"skill: find where upstream moved it, or decide to keep the copy as "
								"our own, rather than discovering it gone after the next "
								"sync.").format(
								skill=skill, vendor=name, base=(probe["baseline_sha"] or "?")[:12],
								subpath=subpath, url=url, branch=branch,
								head=(probe["upstream_sha"] or "?")[:12]),
							vendor=name, skill=skill, vendor_kind=vendor.get("kind"),
							upstream_url=url, upstream_branch=branch,
							upstream_subpath=subpath, local_path=local_path,
							baseline_sha=probe["baseline_sha"],
							upstream_sha=probe["upstream_sha"], expected=False))
						continue
					unresolved = " and ".join(side for side, sha in (
						("local", local), ("baseline", baseline), ("upstream", upstream))
						if sha is None)
					suppressed.append("{}/{} not verified — `{}` did not resolve in "
						"{}".format(name, skill, subpath, unresolved))
					continue
				findings.append(_finding(
					id="skill-drift:{}/{}".format(name, skill),
					name="{} ({})".format(skill, name),
					drift_state=state, severity=SEVERITY[state],
					detail=_detail(state, skill, name, _vendor_dir(vendor), url, branch,
						subpath, probe["baseline_sha"], probe["upstream_sha"], drifted),
					vendor=name, skill=skill, vendor_kind=vendor.get("kind"),
					upstream_url=url, upstream_branch=branch, upstream_subpath=subpath,
					local_path=local_path, baseline_sha=probe["baseline_sha"],
					upstream_sha=probe["upstream_sha"],
					expected=(state == "local_only"),
					remediation=(None if state == "local_only"
						else _remediation(name, drifted))))

	findings.sort(key=lambda f: (f["vendor"] or "", f["skill"] or ""))
	return findings, suppressed


def main(argv=None) -> int:
	ap = argparse.ArgumentParser(description=(
		"Report drift between the vendored agent skills and their upstreams, "
		"as collect.json's `skill_drift` object."))
	ap.add_argument("--dotfiles-root", default="dotfiles",
		help="path to the dotfiles checkout (default: dotfiles, relative to the "
			"macos-setup repo root collect.sh runs from)")
	ap.add_argument("--timeout", type=int, default=30,
		help="seconds allowed per upstream network operation (default: 30)")
	ap.add_argument("--no-network", action="store_true",
		help="skip every upstream fetch; each vendor degrades to one probe_error finding")
	args = ap.parse_args(argv)
	try:
		findings, suppressed = detect(args.dotfiles_root, max(1, args.timeout), args.no_network)
	except Exception as exc:  # the collector must never abort on our account
		return _emit(*_whole_source_failure(
			"Vendored-skill detection failed ({}: {}).".format(type(exc).__name__, exc)))
	return _emit(findings, suppressed)


if __name__ == "__main__":
	sys.exit(main())
