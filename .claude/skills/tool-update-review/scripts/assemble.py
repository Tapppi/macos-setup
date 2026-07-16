#!/usr/bin/env python3
"""
assemble.py — merge collect.sh + research/*.json into report.json.
Usage: assemble.py <session_dir> [--macos-setup-root PATH] [--dotfiles-root PATH]

Reads {session_dir}/collect.json (collect.sh's saved stdout, see
references/collection.md) and every {session_dir}/research/*.json file (see
references/research.md — each one a JSON array of partial Tool objects, one
per tool, each carrying its own "id"), merges them into the full report
object (references/schemas.md §Report Object), and writes
{session_dir}/report.json.

This replaces the ad hoc hand-assembly used before this script existed,
which silently skipped two documented rules: it blanket-set needs_sudo:false
on every synthesized upgrade suggestion (references/schemas.md §Report Object
says default true when unsure) and never checked that cited evidence paths actually exist.
Both are enforced here in code instead of being re-derived — and re-skipped
— by hand each run.
"""
from __future__ import annotations  # keeps `X | None` annotations legal on
                                     # Python 3.9 (macOS's bundled python3,
                                     # before mise provisions a newer one —
                                     # same constraint as server.py)

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


# ── needs_sudo heuristic (references/schemas.md §Report Object; references/apply.md §Executing Upgrade Suggestions) ──
# brew formulae, mise, and standalone CLIs never invoke a privileged
# installer themselves — only some casks (pkg-shipping installers) do, and
# Homebrew handles the internal `sudo` call itself. Default a cask to
# needs_sudo:true (references/schemas.md §Report Object: "default to true when genuinely unsure") and
# only trust false when research explicitly confirmed it via
# "cask_sudo_hint": false on its returned Tool object — never assume.
def needs_sudo_for(source: str, research_obj: dict) -> bool:
	if source in ("brew", "mise", "standalone"):
		return False
	if source == "cask":
		return research_obj.get("cask_sudo_hint") is not False
	if source == "macos":
		return True
	return True


# ── auto_runnable / command per source (references/assembly.md §Baseline Suggestion Synthesis) ──
def upgrade_command_and_runnable(source: str, name: str):
	if source == "brew":
		return f"brew upgrade {name}", True, None
	if source == "cask":
		return f"brew upgrade --cask {name}", True, None
	if source == "mise":
		return f"mise upgrade {name}", True, None
	if source == "standalone":
		return None, False, "No generic upgrade command for a standalone CLI — check the tool's own docs."
	if source == "macos":
		return None, False, "macOS system/app update — install via System Settings or `softwareupdate -i`, not auto-run by this skill."
	return None, False, "Unknown source — no safe default command."


# ── risk_level (references/assembly.md §Risk Level, "default-accept low-risk upgrades") ──
# Computed here from objective signals already in the report rather than a
# subjective per-tool call from research — code enforces one rule
# consistently instead of relying on every subagent to judge it the same
# way. "elevated" if any of: pinned, a relevancy finding above "info", any
# edit-kind suggestion authored, or a major-version bump (semver-aware).
# Unparseable versions default to "elevated" — an unknown delta size is not
# a low-risk delta.
_LEADING_INT = re.compile(r"^\s*v?(\d+)")


def _leading_major(version) -> int | None:
	if not isinstance(version, str):
		return None
	m = _LEADING_INT.match(version)
	return int(m.group(1)) if m else None


def is_major_bump(current, latest) -> bool:
	cur_major = _leading_major(current)
	lat_major = _leading_major(latest)
	if cur_major is None or lat_major is None:
		return True  # unparseable — treat as elevated, not low-risk
	return cur_major != lat_major


def compute_risk_level(tool: dict) -> str:
	if tool.get("pinned"):
		return "elevated"
	for item in tool.get("relevancy", []):
		if item.get("severity") in ("warning", "incompatible"):
			return "elevated"
	for sug in tool.get("suggestions", []):
		if sug.get("kind", "edit") == "edit":
			return "elevated"
	if is_major_bump(tool.get("current_version"), tool.get("latest_version")):
		return "elevated"
	return "low"


# ── evidence path validation (references/assembly.md §Evidence Validation, "verify evidence paths") ──
# Evidence strings are either a repo-relative "path" or "path:line" (checked
# against the given repo roots), or a non-path citation (a commit hash/
# subject, a changelog.md entry description) that config_status evidence
# also uses — those are left alone. Never drops an item either way; just
# warns to stderr so an agent skimming the run can catch a bad citation.
_COMMIT_LIKE = re.compile(r"\bcommit\b|^[0-9a-f]{7,40}\b", re.IGNORECASE)

# A citation can trail a human-readable parenthetical describing the hit
# (e.g. "tasks/install.sh:56-104 (install_podman_intel)") and/or a line
# locator that's a single line (":112") or a range ("Brewfile:83-87") —
# strip both, in that order (a parenthetical always trails any line locator,
# never the reverse), down to the real path before checking existence.
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")
_TRAILING_LINE_REF = re.compile(r":\d+(?:-\d+)?$")


def strip_evidence_suffixes(evidence: str) -> str:
	s = _TRAILING_PAREN.sub("", evidence)
	s = _TRAILING_LINE_REF.sub("", s)
	return s


def evidence_exists(evidence: str, roots) -> bool | None:
	"""True/False if this looks like a checkable path; None if it doesn't
	look like a path at all (e.g. a commit citation) — nothing to check."""
	if _COMMIT_LIKE.search(evidence):
		return None
	candidate = strip_evidence_suffixes(evidence)
	candidate = os.path.expanduser(candidate)
	if os.path.isabs(candidate):
		return os.path.exists(candidate)
	for root in roots:
		if os.path.exists(os.path.join(root, candidate)):
			return True
		# A citation is sometimes prefixed with its own repo's directory name
		# (e.g. "systems/flake.nix:1", "dotfiles/config/git/..." ) — the
		# dotfiles case happens to already resolve above purely by luck (the
		# dotfiles submodule is physically nested inside the macos-setup
		# checkout, so "dotfiles/..." already matches under
		# --macos-setup-root); "systems/..." has no such nesting under any
		# root, so the same convention needs an explicit strip-and-retry:
		# if the candidate's leading path segment matches this root's own
		# directory name, retry with that segment stripped.
		prefix = os.path.basename(os.path.normpath(root)) + "/"
		if candidate.startswith(prefix):
			stripped = candidate[len(prefix):]
			if os.path.exists(os.path.join(root, stripped)):
				return True
	# Not found under any root — could still be a loose phrase rather than a
	# real path (e.g. "no bespoke touchpoint"); only warn, never drop.
	return False


# ── config_status normalization (references/assembly.md §Evidence Validation; a research subagent can
# legitimately return `config_status: null` — e.g. nothing to compute for a
# macOS-source tool — rather than omitting the key entirely. `dict.get(key,
# default)` only substitutes the default when the key is *absent*; a
# present-but-null value passes straight through as None and a later
# `.get("state")` call on it raises AttributeError, crashing the whole run.
# Normalize both the whole object and its `detail` sub-field here so every
# downstream `.get()` call always sees a dict) ────────────────────────────
_DEFAULT_CONFIG_STATUS = {"state": "unknown", "detail": "", "evidence": []}


def normalize_config_status(research_obj: dict) -> dict:
	cs = research_obj.get("config_status")
	if not isinstance(cs, dict):
		return dict(_DEFAULT_CONFIG_STATUS)
	cs = dict(cs)
	if cs.get("detail") is None:
		cs["detail"] = ""
	return cs


def as_evidence_list(evidence, tool_id: str, context: str) -> list:
	"""references/schemas.md §Report Object says evidence is always an array. A research subagent
	that instead returns a bare string would make `for ev in evidence`
	iterate individual characters — coerce and warn rather than silently
	corrupting the warning output with single-letter "evidence" entries."""
	if not evidence:
		return []
	if isinstance(evidence, list):
		return evidence
	print(f"warning: {tool_id}: {context} evidence was a bare string, not an array — wrapping it: {evidence!r}", file=sys.stderr)
	return [evidence]


def validate_evidence(tool: dict, roots) -> None:
	tool_id = tool.get("id", "<unknown>")
	for group in ("relevancy", "context"):
		for item in tool.get(group, []):
			for ev in as_evidence_list(item.get("evidence"), tool_id, group):
				result = evidence_exists(ev, roots)
				if result is False:
					print(f"warning: {tool_id}: evidence not found: {ev!r}", file=sys.stderr)
	for ev in as_evidence_list((tool.get("config_status") or {}).get("evidence"), tool_id, "config_status"):
		if evidence_exists(ev, roots) is False:
			print(f"warning: {tool_id}: config_status evidence not found: {ev!r}", file=sys.stderr)


# ── main assembly ───────────────────────────────────────────────────────────
def load_research(research_dir: str) -> dict:
	"""Returns {tool_id: research_obj}. Every file in research/ is a JSON
	array (references/research.md §Spawning and the Output-File Contract); a tool with no matching entry (subagent
	failure/timeout) just gets research_error set later, in build_tool()."""
	by_id: dict[str, dict] = {}
	if not os.path.isdir(research_dir):
		print(f"warning: no research/ dir at {research_dir!r} — every tool will show research_error", file=sys.stderr)
		return by_id
	for fname in sorted(os.listdir(research_dir)):
		if not fname.endswith(".json"):
			continue
		fpath = os.path.join(research_dir, fname)
		try:
			with open(fpath, "r", encoding="utf-8") as fh:
				entries = json.load(fh)
		except (OSError, json.JSONDecodeError) as exc:
			print(f"warning: could not read {fpath!r}: {exc}", file=sys.stderr)
			continue
		if not isinstance(entries, list):
			print(f"warning: {fpath!r} is not a JSON array — skipping", file=sys.stderr)
			continue
		for entry in entries:
			tid = entry.get("id")
			if not tid:
				print(f"warning: an entry in {fpath!r} has no \"id\" — skipping", file=sys.stderr)
				continue
			if tid in by_id:
				print(f"warning: duplicate research entry for {tid!r} ({fpath!r} overwrites an earlier file)", file=sys.stderr)
			by_id[tid] = entry
	return by_id


# ── brew-health finding → headliner category (references/assembly.md §Brew-Health Assembly) ──
# A brew doctor finding is not a changelog fact, but the four content groups
# (Security/Fixes/Features/Notes) are still where its problem statement reads
# best on the card. Map each health category to the group whose topic fits;
# untrusted taps are a trust/security decision, so → security.
_HEALTH_CATEGORY_GROUP = {
	"deprecated_cask": "notes",
	"disabled_cask": "notes",
	"deprecated_formula": "notes",
	"disabled_formula": "notes",
	"missing_keg": "fixes",
	"unlinked_keg": "fixes",
	"untrusted_tap": "security",
	"missing_dependency": "fixes",
	"path_note": "notes",
	"other": "notes",
}


def build_health_tool(candidate: dict, research_obj: dict | None) -> dict:
	"""Build a Tool object for a `source: "brew-health"` finding (references/assembly.md §Brew-Health Assembly). Unlike
	a version-outdated tool it has no current→latest delta and gets no
	synthesized `brew upgrade` baseline — its action is the finding's own
	remediation. Research (if a brew-health subagent ran) can override the
	headliners/context/suggestions; absent research, this degrades to the
	collect.sh finding's own detail + default remediation so the card is
	still useful on its own."""
	research_obj = research_obj or {}
	tool_id = candidate["id"]
	category = candidate.get("category", "other")

	# Headliners: research's if present, else one synthesized from the
	# finding's own detail so the problem still shows in a content group.
	headliners = research_obj.get("headliners")
	if not headliners:
		headliners = [{
			"text": candidate.get("detail", candidate.get("name", "")),
			"category": _HEALTH_CATEGORY_GROUP.get(category, "notes"),
			"severity": candidate.get("severity", "notable"),
		}]

	# Suggestions: research's if present, else synthesize from the finding's
	# default remediation (skipped for expected/no-action findings, e.g. the
	# intentional GNU-utils PATH note, which carries remediation: null).
	suggestions = list(research_obj.get("suggestions", []))
	if not suggestions:
		rem = candidate.get("remediation")
		if rem and rem.get("command"):
			sug = {
				"id": f"{tool_id}:remediate",
				"kind": "upgrade",  # a single command to run, like an upgrade
				"title": rem.get("label") or candidate.get("name", "Remediate"),
				"target_files": [],
				"command": rem["command"],
				"auto_runnable": rem.get("auto_runnable", False),
				"needs_sudo": rem.get("needs_sudo", False),
				"rationale": candidate.get("detail", ""),
				"motivating_link": None,
				"diff_preview": None,
			}
			if not sug["auto_runnable"]:
				sug["manual_reason"] = "Structural brew change — review and run this yourself."
			suggestions = [sug]

	tool = {
		"id": tool_id,
		"name": candidate.get("name", tool_id),
		"source": "brew-health",
		"health_category": category,
		"health_expected": bool(candidate.get("expected", False)),
		"pinned": False,
		"current_version": None,
		"latest_version": None,
		"research_error": None,
		"headliners": headliners,
		"links": research_obj.get("links", []),
		"config_status": normalize_config_status(research_obj),
		"relevancy": research_obj.get("relevancy", []),
		"context": research_obj.get("context", []),
		"release_inventory": research_obj.get("release_inventory", []),
		"vendor_silent_categories": research_obj.get("vendor_silent_categories", []),
		"suggestions": suggestions,
	}
	# Same needs_attention-must-have-a-suggestion guard build_tool applies —
	# a brew-health research subagent could set needs_attention on a finding
	# whose default remediation is null (e.g. missing_keg), shipping an
	# unactionable banner; surface it loudly rather than silently.
	if tool["config_status"].get("state") == "needs_attention" and not tool["suggestions"]:
		print(f"warning: {tool_id}: config_status is needs_attention with no suggestion addressing it", file=sys.stderr)
	# An expected/no-action finding (path_note) is informational: keep it
	# visible but low-risk so it never demands a decision. Everything else is
	# structural — treat as elevated so it isn't quietly pre-accepted.
	tool["risk_level"] = "low" if tool["health_expected"] else "elevated"
	return tool


def build_tool(candidate: dict, research_obj: dict | None) -> dict:
	source = candidate["source"]
	if source == "brew-health":
		return build_health_tool(candidate, research_obj)

	name = candidate["name"]
	tool_id = candidate["id"]
	research_obj = research_obj or {}

	tool = {
		"id": tool_id,
		"name": name,
		"source": source,
		"pinned": candidate.get("pinned", False),
		"current_version": candidate.get("current_version"),
		"latest_version": candidate.get("latest_version"),
		"research_error": None if research_obj else "research subagent produced no output for this tool",
		"headliners": research_obj.get("headliners", []),
		"links": research_obj.get("links", []),
		"config_status": normalize_config_status(research_obj),
		"relevancy": research_obj.get("relevancy", []),
		"context": research_obj.get("context", []),
		"release_inventory": research_obj.get("release_inventory", []),
		"vendor_silent_categories": research_obj.get("vendor_silent_categories", []),
		"suggestions": list(research_obj.get("suggestions", [])),
	}

	# Enforce the needs_attention-must-have-a-suggestion rule (references/research.md
	# §Config Status / references/assembly.md §Evidence Validation, enforcement point) — a
	# violation here is a research-prompt bug, but assembly still surfaces
	# it loudly rather than silently shipping an unactionable banner.
	if tool["config_status"].get("state") == "needs_attention" and not tool["suggestions"]:
		print(f"warning: {tool_id}: config_status is needs_attention with no suggestion addressing it", file=sys.stderr)

	# Synthesize the baseline kind:"upgrade" suggestion (references/assembly.md §Baseline Suggestion Synthesis) —
	# mechanical, every tool gets exactly one, never left to research.
	command, auto_runnable, manual_reason = upgrade_command_and_runnable(source, name)
	upgrade_suggestion = {
		"id": f"{tool_id}:upgrade",
		"kind": "upgrade",
		"title": f"Upgrade {name} {tool['current_version']} → {tool['latest_version']}",
		"target_files": [],
		"command": command,
		"auto_runnable": auto_runnable,
		"needs_sudo": needs_sudo_for(source, research_obj),
		"rationale": "Picks up the changes described in headliners[] above.",
		"motivating_link": (tool["links"][0] if tool["links"] else None),
		"diff_preview": None,
	}
	if not auto_runnable:
		upgrade_suggestion["manual_reason"] = manual_reason
	tool["suggestions"].insert(0, upgrade_suggestion)

	tool["risk_level"] = compute_risk_level(tool)
	return tool


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("session_dir")
	parser.add_argument("--macos-setup-root", default=".")
	parser.add_argument("--dotfiles-root", default=None)
	# references/research.md / references/research-prompt-template.md both tell
	# every research subagent to scan and cite ~/project/github/tapppi/systems
	# (the NixOS flake repo) for relevancy — evidence validation needs that
	# repo root too, or every "systems/..." citation gets falsely flagged as
	# "evidence not found." Defaults to the standard workspace location; pass
	# explicitly if the harness mounts it elsewhere.
	parser.add_argument("--systems-root", default="~/project/github/tapppi/systems")
	args = parser.parse_args()

	session_dir = os.path.abspath(args.session_dir)
	macos_setup_root = os.path.abspath(args.macos_setup_root)
	dotfiles_root = os.path.abspath(args.dotfiles_root or os.path.join(macos_setup_root, "dotfiles"))
	systems_root = os.path.abspath(os.path.expanduser(args.systems_root))
	roots = [macos_setup_root, dotfiles_root, systems_root]

	collect_path = os.path.join(session_dir, "collect.json")
	try:
		with open(collect_path, "r", encoding="utf-8") as fh:
			collect = json.load(fh)
	except (OSError, json.JSONDecodeError) as exc:
		print(f"Error: could not read {collect_path!r}: {exc}", file=sys.stderr)
		sys.exit(1)

	repo_context_path = os.path.join(session_dir, "repo_context.json")
	try:
		with open(repo_context_path, "r", encoding="utf-8") as fh:
			repo_context = json.load(fh)
	except (OSError, json.JSONDecodeError):
		print(f"warning: no repo_context.json at {repo_context_path!r} — using placeholder", file=sys.stderr)
		placeholder = {"up_to_date": True, "ahead": 0, "behind": 0, "recent_commits": []}
		repo_context = {"macos_setup": placeholder, "dotfiles": dict(placeholder)}

	research_by_id = load_research(os.path.join(session_dir, "research"))

	# brew-health findings (references/assembly.md §Brew-Health Assembly) are candidates too, appended after
	# the version-outdated tools so they sort/render as their own cards.
	health = collect.get("brew_health") or {}
	health_findings = health.get("findings", []) if isinstance(health, dict) else []
	health_suppressed = health.get("suppressed", []) if isinstance(health, dict) else []
	for s in health_suppressed:
		print(f"note: brew-health suppressed (expected, not reported): {s}", file=sys.stderr)

	candidates = (
		collect.get("brew", []) + collect.get("mise", []) +
		collect.get("standalone", []) + collect.get("macos", []) +
		health_findings
	)

	tools = [build_tool(c, research_by_id.get(c["id"])) for c in candidates]

	for tool in tools:
		validate_evidence(tool, roots)

	# Suggestion-id uniqueness — global, not just within one tool. A
	# collision almost always means a research subagent copied an id
	# pattern rather than deriving it from its own tool, so append a
	# disambiguating suffix rather than silently dropping either one.
	seen_ids: dict[str, str] = {}
	for tool in tools:
		for sug in tool["suggestions"]:
			sid = sug.get("id")
			if not sid:
				continue
			if sid in seen_ids:
				n = 2
				new_id = f"{sid}-{n}"
				while new_id in seen_ids:
					n += 1
					new_id = f"{sid}-{n}"
				print(f"warning: duplicate suggestion id {sid!r} (tool {tool['id']!r}) — renamed to {new_id!r}", file=sys.stderr)
				sug["id"] = new_id
				sid = new_id
			seen_ids[sid] = tool["id"]

	incompatible = sum(1 for t in tools for r in t["relevancy"] if r.get("severity") == "incompatible")
	warning = sum(1 for t in tools for r in t["relevancy"] if r.get("severity") == "warning")
	suggestions_count = sum(len(t["suggestions"]) for t in tools)
	# "outdated" counts version-outdated tools only; brew-health findings are
	# environment issues, not updates, so they get their own count.
	health_count = sum(1 for t in tools if t["source"] == "brew-health")

	report_id = os.path.basename(session_dir)
	report = {
		"schema_version": 1,
		"report_id": report_id,
		"generated_at": collect.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"machine": collect.get("machine", {}),
		"summary": {
			"total_outdated": len(tools) - health_count,
			"incompatible_count": incompatible,
			"warning_count": warning,
			"suggestions_count": suggestions_count,
			"health_count": health_count,
		},
		"repo_context": repo_context,
		"tools": tools,
	}

	out_path = os.path.join(session_dir, "report.json")
	with open(out_path, "w", encoding="utf-8") as fh:
		json.dump(report, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")
	print(out_path)


if __name__ == "__main__":
	main()
