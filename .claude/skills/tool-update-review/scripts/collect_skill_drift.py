#!/usr/bin/env python3
"""
collect_skill_drift.py — vendored agent-skill drift, as a collect.sh finding source.
Usage: collect_skill_drift.py [--skills-root PATH] [--timeout SECONDS] [--no-network]

Prints ONE JSON object (the `skill_drift` value of collect.json, see
references/collection.md §Skill-Drift Collection) to stdout:
`{"findings": [...], "suppressed": [...]}`.

The vendored skills live in the Tapppi/skills repo (`--skills-root`, default
`$TOOL_UPDATE_SKILLS_ROOT` or `~/project/github/tapppi/skills`; they moved
there from `dotfiles/config/agent-skills/`, see macos-setup `docs/skills.md`),
as copies of upstream repos under `<vendor>/`. Nothing else in tool-update-review can see them drift: they
carry no version number, so the brew/mise/standalone version machinery has
nothing to compare. What they do carry is git provenance, so drift is decided
by a three-way comparison of git TREE hashes — content-addressed and identical
across repositories for identical content, which makes equality exact rather
than heuristic, and needs no blob download:

    LOCAL     git -C <skills repo> rev-parse HEAD:<local_path>
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
    with no network at all. A commit that also carries
    `git-subtree-mainline:` is a merge (`git subtree add`, or the re-based
    history after the move out of dotfiles); its squash is its second
    parent, as in git-subtree's own find_latest_squash.
  - sparse vendors — `Last synced commit:` in the vendor's CUSTOMISATION.md
    (the nearest one above the copy); no offline baseline exists, so that
    one costs a second fetch.
  - the vendor -> (url, branch, kind) mapping and the adopted-skill set are
    parsed out of sync-upstream.sh and .claude-plugin/marketplace.json. A
    plugin may name its skills (`"skills": ["./anthropics/skills/x"]`) or be
    a directory holding a vendor (`"source": "./softaworks"`); either way the
    skill paths are resolved against the vendor tables. `tapppi/` appears in
    neither vendor table, so our own skills fall out of scope by construction
    rather than by a hardcoded skip-list.

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
  - the whole source — ONE `skill-drift:source-unavailable` finding, so a run
    that checked nothing cannot render as a run that found nothing wrong. A
    MISSING source (no skills checkout where it looked, not a git repo, no
    sync-upstream.sh) is a setup problem the user can fix, so that card is
    loud: `notable`, not expected, naming the path and the override. A
    source that is there but unreadable past the parser, or an unexpected
    exception, stays a quiet expected card.
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


# The vendored skills live at the ROOT of the Tapppi/skills repo: the vendor
# tables record prefixes relative to it, and a marketplace entry's "./..."
# source is relative to it too.
SYNC_SCRIPT_REL = "sync-upstream.sh"
MARKETPLACE_REL = ".claude-plugin/marketplace.json"
# Run from the skills repo root — the script cds there itself, but the
# command we hand the user is the one its own header documents.
#
# It is ALL-vendor, and nothing here may pretend otherwise. The script takes no
# vendor argument: one run pulls every subtree vendor (a three-way `git
# subtree` merge, which can conflict with a local patch) and refreshes every
# sparse vendor by overwriting its copy with `rsync --delete` (which discards
# one). So one command serves every drifted card, and its label and each
# card's detail name every vendor it touches and every local patch it puts at
# risk (`_sync_plan`). No vendor-scoped command can be offered in its place: a
# hand-run `git subtree pull` would squash a vendor's excluded paths back into
# history, which the script's own header forbids. Scoping it needs a vendor
# selector in the Tapppi/skills script itself.
SYNC_COMMAND = "bash sync-upstream.sh"
# Where the skills repo is looked for when --skills-root is not given.
SKILLS_ROOT_ENV = "TOOL_UPDATE_SKILLS_ROOT"
DEFAULT_SKILLS_ROOT = "~/project/github/tapppi/skills"
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
	"""Pipe-delimited, optionally-quoted rows; comments and blanks dropped.
	Four fields, or five for a subtree vendor that lists excluded paths
	(never vendored, so never compared); only the first four are returned."""
	rows = []
	for raw in body.splitlines():
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		if len(line) >= 2 and line[0] == line[-1] and line[0] in ('"', "'"):
			line = line[1:-1]
		fields = [f.strip() for f in line.split("|")]
		if len(fields) not in (4, 5) or not all(fields[:3]):
			continue
		rows.append(fields[:4])
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


def _rel(path) -> str:
	"""A marketplace "./x/y" path as "x/y" ("" for the repo root), or None
	when it is not a repo-relative string path."""
	if not isinstance(path, str) or not path.startswith("./"):
		return None
	parts = [p for p in path[2:].strip().split("/") if p and p != "."]
	return None if ".." in parts else "/".join(parts)


def parse_marketplace_adopted(marketplace_json_text) -> list:
	"""The adopted skill paths from .claude-plugin/marketplace.json.

	ONLY entries whose "source" is a STRING starting "./" are vendored here.
	An OBJECT source (git-subdir, e.g. find-skills) means Claude Code resolves
	the plugin itself from its own upstream — nothing of it is committed to
	this repo, so it is tool-owned and there is no local copy that could
	drift. An entry that lists `"skills": ["./…", …]` adopts exactly those,
	relative to its source (`"source": "./"` with
	`"./anthropics/skills/skill-creator"`); one without adopts its source
	directory, which may be a single skill or a directory holding a vendor
	(`"./softaworks"` — `resolve_adopted` expands it against the vendor
	tables). Returns [{name, rel_path}]; `rel_path` is "" for a bare "./".
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
			source = _rel(entry.get("source"))
			if source is None:
				continue
			skills = entry.get("skills")
			if isinstance(skills, list) and skills:
				for skill in skills:
					rel = _rel(skill)
					if rel is None:
						continue
					rel_path = _join(source, rel)
					if rel_path:
						adopted.append({"name": os.path.basename(rel_path),
							"rel_path": rel_path})
				continue
			if not source:
				continue
			name = entry.get("name") or os.path.basename(source)
			adopted.append({"name": str(name), "rel_path": source})
		return adopted
	except Exception:
		return []


def resolve_adopted(adopted: list, vendors: list) -> list:
	"""Each adopted path as the vendored skill(s) it stands for.

	A path under a vendor (or a sparse copy itself) is one skill of that
	vendor, as written. A path that holds vendors instead (a plugin whose
	source is `./softaworks`, or a subtree vendor's own root) stands for
	every skill those vendors carry under it: a sparse vendor's one copy, a
	subtree vendor's adopted skills from its table row. Anything
	else is kept as written and later suppressed as our own. De-duplicated
	by path, first name wins."""
	out, seen = [], set()

	def add(name, rel_path):
		if rel_path not in seen:
			seen.add(rel_path)
			out.append({"name": name, "rel_path": rel_path})
	for entry in adopted:
		rel_path = entry["rel_path"]
		owner = _match_vendor(vendors, rel_path)
		# A path strictly inside a vendor, or a sparse copy itself, is one
		# skill. A subtree vendor's own root is not: it holds that vendor's
		# adopted skills (and its excluded paths, which are never compared).
		if owner is not None and not (vendors[owner].get("kind") == "subtree"
				and _vendor_rel(vendors[owner]) == rel_path):
			add(entry["name"], rel_path)
			continue
		held = []
		for vendor in vendors:
			vrel = _vendor_rel(vendor)
			if not vrel or not (rel_path in ("", vrel) or vrel.startswith(rel_path + "/")):
				continue
			if vendor.get("kind") == "sparse":
				held.append(vrel)
			else:
				held.extend(_join(vrel, skill) for skill in vendor.get("skills") or [])
		if not held:
			add(entry["name"], rel_path)
		for path in held:
			add(os.path.basename(path), path)
	return out


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


def subtree_baseline_sha(skills_root: str, prefix: str):
	"""The most recent `git subtree --squash` commit for `prefix`.

	Returns (baseline_rev, subtree_split_sha) — the first is a revision in
	THIS repo whose tree is the pristine upstream content (so BASELINE needs
	no network), the second names the upstream commit it came from. (None,
	None) when no such commit exists. A commit carrying
	`git-subtree-mainline:` is the merge that brought a squash in (`git
	subtree add`, or the re-based base after the move out of dotfiles); its
	own tree is the whole repo, so the baseline is its second parent — the
	squash — exactly as git-subtree's find_latest_squash reads it.
	"""
	try:
		if not prefix:
			return (None, None)
		pattern = "^git-subtree-dir: " + _ere_escape(prefix.rstrip("/")) + "$"
		# One `git log`, not two: `%H%n%B` puts the sha on the first line and
		# the raw body (which carries the trailers) on the rest, so the commit
		# never has to be looked up a second time to read its own message.
		rc, out, _ = _run(["git", "-C", skills_root, "log", "-n", "1",
			"--extended-regexp", "--grep", pattern, "--format=%H%n%B"], LOCAL_GIT_TIMEOUT)
		if rc != 0 or not out.strip():
			return (None, None)
		commit, _, body = out.partition("\n")
		commit = commit.strip()
		if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
			return (None, None)
		m = re.search(r'^git-subtree-split:\s*([0-9a-fA-F]{7,40})\s*$', body, re.M)
		if re.search(r'^git-subtree-mainline:', body, re.M):
			rc, out, _ = _run(["git", "-C", skills_root, "rev-parse", commit + "^2"],
				LOCAL_GIT_TIMEOUT)
			squash = out.strip() if rc == 0 else ""
			if not re.fullmatch(r'[0-9a-f]{40,64}', squash):
				return (None, None)
			commit = squash
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
	"""The vendor path relative to the skills repo root — the form a
	marketplace `"./<...>"` source is written in."""
	return _vendor_path(vendor).strip("/")


def _vendor_name(vendor: dict) -> str:
	"""The upstream's short name — `anthropics`, `google`, `softaworks`. A
	sparse vendor's dest names the skill (`softaworks/jira`), so the vendor is
	its parent directory."""
	rel = _vendor_rel(vendor)
	if vendor.get("kind") == "sparse" and "/" in rel:
		return rel.split("/")[0]
	return os.path.basename(_vendor_path(vendor).rstrip("/")) or rel or "vendor"


def _vendor_dir(vendor: dict, root: str | None = None) -> str:
	"""Where the vendor's CUSTOMISATION.md lives: a subtree's prefix; for a
	sparse copy, the nearest directory above it that holds one
	(`softaworks/` for `softaworks/skills/jira`), or its parent when none
	does or no root is given."""
	path = _vendor_path(vendor).rstrip("/")
	if vendor.get("kind") != "sparse":
		return path
	parent = os.path.dirname(path) or path
	if root:
		probe = parent
		while probe:
			if os.path.isfile(os.path.join(root, probe, "CUSTOMISATION.md")):
				return probe
			probe = os.path.dirname(probe)
	return parent


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
def _probe_vendor(vendor: dict, skills_root: str, probe_repo: str,
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
		squash, split = subtree_baseline_sha(skills_root, vendor.get("prefix", ""))
		if squash is None:
			return None, ("no `git subtree --squash` commit recorded for "
				"`{}`".format(vendor.get("prefix", "")))
		baseline_repo, baseline_rev, baseline_sha = skills_root, squash, (split or squash)
	else:
		cust_dir = _vendor_dir(vendor, skills_root)
		cust = _read(os.path.join(skills_root, cust_dir, "CUSTOMISATION.md"))
		baseline_sha = sparse_baseline_sha(cust or "")
		if baseline_sha is None:
			return None, ("no `Last synced commit:` recorded in "
				"`{}/CUSTOMISATION.md`".format(cust_dir))

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


# What one run does to each kind of vendor, for the text that says so.
_SYNC_EFFECT = {"subtree": "a git subtree merge",
	"sparse": "a sparse copy, overwritten with `rsync --delete`"}
# What that run does to a local patch, by (state, vendor kind).
_PATCH_FATE = {
	("diverged", "subtree"): "diverged: the merge can conflict with the patch",
	("diverged", "sparse"): "diverged: the copy is overwritten, so the patch must be re-applied",
	("local_only", "subtree"): "local patch: the merge keeps it while upstream leaves it alone",
	("local_only", "sparse"): "local patch: the copy is overwritten, so the patch must be re-applied",
}


def _sync_plan(vendors: list, findings: list, unchecked: list) -> dict:
	"""What ONE `bash sync-upstream.sh` run does, read off the finished finding
	list: every vendor in its tables (it takes no vendor argument), the drifted
	skills it resolves, the local patches it merges over or overwrites, the
	skills it stops shipping because upstream removed them, and the vendors it
	syncs that this run could not check."""
	names = []
	for vendor in vendors:
		pair = (_vendor_name(vendor), vendor.get("kind") or "subtree")
		if pair not in names:
			names.append(pair)
	def label(f):
		return "{}/{}".format(f["vendor"], f["skill"])
	skill_findings = [f for f in findings if f.get("skill")]
	return {
		"vendors": names,
		"drifted": sorted(label(f) for f in skill_findings
			if f["drift_state"] in ("upstream_ahead", "diverged")),
		"patched": sorted((label(f), _PATCH_FATE.get((f["drift_state"],
			f.get("vendor_kind") or "subtree"), "local patch"))
			for f in skill_findings if f["drift_state"] in ("diverged", "local_only")),
		"removed": sorted(label(f) for f in skill_findings
			if f["drift_state"] == "probe_error"),
		"unchecked": sorted(set(unchecked)),
	}


def _remediation(plan: dict) -> dict:
	"""The one remediation every drifted card carries — the same command on
	each, labelled as what it is: a sync of every vendor at once."""
	label = "Sync every vendor from upstream ({})".format(
		", ".join(name for name, _ in plan["vendors"]))
	if plan["patched"]:
		label += " — review local patches first"
	return {"command": SYNC_COMMAND, "auto_runnable": False,
		"needs_sudo": False, "label": label}


def _sync_scope(plan: dict) -> str:
	"""The paragraph every drifted card's detail ends with: what the one
	command actually touches, so accepting one card's sync is never read as
	a sync of that vendor alone."""
	drifted = plan["drifted"]
	parts = [("There is no per-vendor or per-skill sync: `{cmd}`, run from the skills "
		"repo root on a clean tree, refreshes every vendor it lists at once: {vendors}. "
		"One run resolves {what}: {skills}.").format(cmd=SYNC_COMMAND,
		vendors=", ".join("{} ({})".format(name, _SYNC_EFFECT.get(kind, kind))
			for name, kind in plan["vendors"]),
		what=("the one drifted skill" if len(drifted) == 1
			else "all {} drifted skills".format(len(drifted))),
		skills=", ".join(drifted))]
	if plan["patched"]:
		parts.append("Before running it, review these local patches against their "
			"vendor's `CUSTOMISATION.md`: {}.".format("; ".join(
				"{} ({})".format(skill, fate) for skill, fate in plan["patched"])))
	if plan["removed"]:
		parts.append("It also stops shipping {}, which upstream removed or "
			"renamed.".format(", ".join(plan["removed"])))
	if plan["unchecked"]:
		parts.append("Not checked this run, but synced by that same run anyway: "
			"{}.".format(", ".join(plan["unchecked"])))
	return " ".join(parts)


def _detail(state, skill, vendor, vendor_dir, url, branch, subpath,
		baseline_sha, upstream_sha, kind="subtree") -> str:
	"""The card's own statement. A drifted card's detail is completed with
	`_sync_scope` once every vendor is classified."""
	base = (baseline_sha or "?")[:12]
	head = (upstream_sha or "?")[:12]
	if state == "upstream_ahead":
		return ("The vendored copy of `{skill}` still matches the recorded `{vendor}` sync "
			"({base}), but upstream {url} ({branch}) has moved on `{subpath}` since — it is "
			"now at {head}. No local patch to this skill is at risk.").format(
			skill=skill, vendor=vendor, base=base, url=url, branch=branch,
			subpath=subpath, head=head)
	if state == "diverged":
		consequence = ("Syncing overwrites the patch rather than merging it: re-apply "
			"what `{dir}/CUSTOMISATION.md` lists after the sync." if kind == "sparse" else
			"Syncing needs conflict review rather than a straight pull: check that the "
			"local patches listed in `{dir}/CUSTOMISATION.md` survive it.").format(dir=vendor_dir)
		return ("The vendored copy of `{skill}` is patched relative to the recorded `{vendor}` "
			"sync ({base}) AND upstream {url} ({branch}) has moved on `{subpath}` to {head}. "
			"{consequence}").format(
			skill=skill, vendor=vendor, base=base, url=url, branch=branch,
			subpath=subpath, head=head, consequence=consequence)
	if kind == "sparse":
		fate = ("Nothing to decide, but any `{cmd}` run — it syncs every vendor, whichever "
			"card it is taken for — overwrites this sparse copy with `rsync --delete` and "
			"discards the patch: re-apply it from `{dir}/CUSTOMISATION.md` after a "
			"sync.").format(cmd=SYNC_COMMAND, dir=vendor_dir)
	else:
		fate = ("Nothing to decide: a sync merges it three-way and keeps it while upstream "
			"leaves `{subpath}` alone.").format(subpath=subpath)
	return ("The vendored copy of `{skill}` differs from the recorded `{vendor}` sync "
		"({base}), and upstream {url} ({branch}) has not moved on `{subpath}` since — so "
		"this is a deliberate local patch, not upstream drift. It should be listed in "
		"`{dir}/CUSTOMISATION.md`. {fate}").format(
		skill=skill, vendor=vendor, base=base, url=url, branch=branch,
		subpath=subpath, dir=vendor_dir, fate=fate)


def _whole_source_failure(reason: str, missing: bool = False):
	"""(findings, suppressed) for a failure that stopped the ENTIRE source.

	Suppressing it and nothing else would be silent in the worst way:
	`suppressed` is logged to stderr and never reaches report.json or the
	page, so a run that could check nothing at all would render exactly like a
	run that checked everything and found it in sync. A missing skills
	checkout, or a restructured `sync-upstream.sh`, would read as "all good".
	So the source says out loud that it did not run — one card, the same
	shape the per-vendor probe failure uses.

	`missing` — the source is not where it was looked for (no checkout, not a
	git repo, no sync-upstream.sh). That is a setup problem with a fix, and
	it is how the move out of dotfiles would otherwise have gone unnoticed,
	so the card is loud: `notable`, not expected, naming the fix. Anything
	else (unparseable tables, a crash) is an honest quiet "unknown"."""
	if missing:
		detail = (reason + " No vendored skill was checked this run. Point the "
			"check at the Tapppi/skills checkout with `--skills-root` or `$"
			+ SKILLS_ROOT_ENV + "` (default `" + DEFAULT_SKILLS_ROOT + "`), or clone "
			"it there.")
	else:
		detail = (reason + " No vendored skill was checked this run — nothing is "
			"known to be wrong with any of them, and nothing here needs deciding.")
	return [_finding(
		id="skill-drift:source-unavailable",
		name=("Vendored-skill source not found" if missing
			else "Vendored-skill check did not run"),
		drift_state="probe_error",
		severity="notable" if missing else SEVERITY["probe_error"],
		detail=detail, expected=not missing)], [reason]


def _emit(findings: list, suppressed: list) -> int:
	print(json.dumps({"findings": findings, "suppressed": suppressed}))
	return 0


# ── Detection ────────────────────────────────────────────────────────────────
def detect(skills_root: str, timeout: int, no_network: bool):
	"""(findings, suppressed) for one skills-repo checkout."""
	root = os.path.abspath(os.path.expanduser(skills_root))
	if not os.path.isdir(root):
		return _whole_source_failure(
			"There is no skills checkout at `{}`.".format(skills_root), missing=True)
	rc, _, _ = _run(["git", "-C", root, "rev-parse", "--git-dir"], LOCAL_GIT_TIMEOUT)
	if rc != 0:
		return _whole_source_failure(
			"`{}` is not a git checkout, and vendored-skill drift is read from git "
			"provenance.".format(skills_root), missing=True)

	sync_text = _read(os.path.join(root, SYNC_SCRIPT_REL))
	if sync_text is None:
		return _whole_source_failure(
			"`{}` was not found under `{}`, so the vendor -> upstream tables could not "
			"be read.".format(SYNC_SCRIPT_REL, skills_root), missing=True)
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
	for entry in resolve_adopted(adopted, vendors):
		idx = _match_vendor(vendors, entry["rel_path"])
		if idx is None:
			suppressed.append("{} — own skill, no upstream vendor in "
				"sync-upstream.sh".format(entry["rel_path"]))
			continue
		buckets.setdefault(idx, []).append(entry)

	findings = []
	seen_probe_ids = set()
	unchecked = []
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
				unchecked.append(name)
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

			# Two passes: classify every skill first — and the remediation is
			# attached only after EVERY vendor is classified, because the one
			# command it names syncs all of them (`_sync_plan`).
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
								"renamed it. The next `{cmd}` run — it syncs every vendor, whichever "
								"card it is taken for — would therefore stop shipping this "
								"skill: find where upstream moved it, or decide to keep the copy as "
								"our own, rather than discovering it gone after the next "
								"sync.").format(cmd=SYNC_COMMAND,
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
					detail=_detail(state, skill, name, _vendor_dir(vendor, root), url, branch,
						subpath, probe["baseline_sha"], probe["upstream_sha"],
						vendor.get("kind") or "subtree"),
					vendor=name, skill=skill, vendor_kind=vendor.get("kind"),
					upstream_url=url, upstream_branch=branch, upstream_subpath=subpath,
					local_path=local_path, baseline_sha=probe["baseline_sha"],
					upstream_sha=probe["upstream_sha"],
					expected=(state == "local_only")))

	# One run of the one command syncs every vendor, so every drifted card
	# carries the same remediation and ends with the same statement of what
	# that run touches — computed once everything is classified.
	plan = _sync_plan(vendors, findings, unchecked)
	for f in findings:
		if f["drift_state"] in ("upstream_ahead", "diverged"):
			f["remediation"] = _remediation(plan)
			f["detail"] = f["detail"] + " " + _sync_scope(plan)

	findings.sort(key=lambda f: (f["vendor"] or "", f["skill"] or ""))
	return findings, suppressed


def main(argv=None) -> int:
	ap = argparse.ArgumentParser(description=(
		"Report drift between the vendored agent skills and their upstreams, "
		"as collect.json's `skill_drift` object."))
	ap.add_argument("--skills-root",
		default=os.environ.get(SKILLS_ROOT_ENV) or DEFAULT_SKILLS_ROOT,
		help="path to the Tapppi/skills checkout that vendors the skills (default: "
			"$" + SKILLS_ROOT_ENV + ", else " + DEFAULT_SKILLS_ROOT + ")")
	ap.add_argument("--timeout", type=int, default=30,
		help="seconds allowed per upstream network operation (default: 30)")
	ap.add_argument("--no-network", action="store_true",
		help="skip every upstream fetch; each vendor degrades to one probe_error finding")
	ap.add_argument("--failed", metavar="REASON",
		help="detect nothing; print the source-unavailable card for REASON (collect.sh's "
			"fallback when a detector run timed out, crashed or printed no object)")
	args = ap.parse_args(argv)
	if args.failed is not None:
		return _emit(*_whole_source_failure(args.failed))
	try:
		findings, suppressed = detect(args.skills_root, max(1, args.timeout), args.no_network)
	except Exception as exc:  # the collector must never abort on our account
		return _emit(*_whole_source_failure(
			"Vendored-skill detection failed ({}: {}).".format(type(exc).__name__, exc)))
	return _emit(findings, suppressed)


if __name__ == "__main__":
	sys.exit(main())
