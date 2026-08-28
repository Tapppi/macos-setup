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


# ── version_delta (references/assembly.md §Version Delta) ───────────────────
# The corpus is not semver. One live run carries Homebrew formula revisions
# (13.55 → 13.55_1), cask "version,build" tuples (2026.2,262.8665.272), letter
# suffixes (3.7b → 3.7c), OpenSSH's portable suffix (10.4p1), nmap's decimal
# scheme (7.99 → 7.991), ImageMagick patch levels (7.1.2-27), date versions
# (20260622), calendar versions (2026.7.4) and pure build numbers
# (26163.407.4839.8659). The failure that matters is a false "patch", because
# "patch" reads as "nothing to think about" and feeds the pre-accept path — so
# a scheme this can't interpret returns "unknown", and every ambiguous
# positional call rounds *up* in significance, never down.
_V_PREFIX = re.compile(r"^[vV](?=\d)")
_BREW_REVISION = re.compile(r"_(\d+)$")
_TOKEN_SPLIT = re.compile(r"[.\-+~]")
_LEADING_NUM = re.compile(r"^(\d+)(.*)$")
_DATE8 = re.compile(r"^\d{8}$")


def split_cask_tuple(version: str) -> tuple:
	"""Cask "1.34493.1,255293a4…" → ("1.34493.1", "255293a4…"). Split on the
	FIRST comma only; the right side is Homebrew's build/revision half and
	never carries upstream semantics."""
	core, sep, build = version.partition(",")
	return (core, build if sep else None)


def split_brew_revision(version: str) -> tuple:
	"""Homebrew "13.55_1" → ("13.55", "1"). Trailing _N only — "1.2_beta" is
	part of the upstream version, not a packaging revision."""
	m = _BREW_REVISION.search(version)
	if not m:
		return (version, None)
	return (version[:m.start()], m.group(1))


def parse_version_components(core: str) -> list:
	"""→ [(int|None, suffix), …]; [] when no component carries a number at
	all. Strips a leading v, splits on . - + ~, then each token into (leading
	integer, lowercased remainder): "10.4p1" → [(10,""),(4,"p1")], "3.7c" →
	[(3,""),(7,"c")], "rc1" → [] (nothing numeric to compare)."""
	core = _V_PREFIX.sub("", core.strip())
	comps = []
	for token in _TOKEN_SPLIT.split(core):
		m = _LEADING_NUM.match(token)
		if m:
			comps.append((int(m.group(1)), m.group(2).lower()))
		else:
			comps.append((None, token.lower()))
	if not any(num is not None for num, _ in comps):
		return []
	return comps


def looks_like_date8(core: str) -> bool:
	"""8 digits parsing as YYYYMMDD with 1990 ≤ YYYY ≤ 2099, 1 ≤ MM ≤ 12,
	1 ≤ DD ≤ 31."""
	if not _DATE8.match(core):
		return False
	year, month, day = int(core[:4]), int(core[4:6]), int(core[6:8])
	return 1990 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31


def _is_calver_year(num) -> bool:
	return num is not None and 2000 <= num <= 2099


def detect_version_scheme(core_a, comps_a, core_b, comps_b) -> str:
	"""→ "date" | "calver" | "opaque" | "semver" | "none". Both sides must
	agree on a scheme; a disagreement falls through to "opaque" whenever
	either side looks like a build number, else to "semver"."""
	if not comps_a or not comps_b:
		return "none"
	if looks_like_date8(core_a) and looks_like_date8(core_b):
		return "date"
	first_a, first_b = comps_a[0][0], comps_b[0][0]
	if (_is_calver_year(first_a) and _is_calver_year(first_b)
			and len(comps_a) >= 2 and len(comps_b) >= 2):
		return "calver"
	for first in (first_a, first_b):
		# The ≥1000 guard is what keeps build numbers out of "major": Microsoft
		# Teams' 26163.407.4839.8659 → 26213.1006.5011.1671 would otherwise
		# read as a major bump of "version 26163".
		if first is None or (first >= 1000 and not _is_calver_year(first)):
			return "opaque"
	return "semver"


def first_difference(comps_a, comps_b) -> tuple:
	"""→ (index, "numeric") for the first index whose integers differ, else
	(index, "suffix") for the first index whose suffixes differ, else
	(None, None). Missing trailing components compare as (0, "")."""
	width = max(len(comps_a), len(comps_b))
	pairs = []
	for i in range(width):
		a = comps_a[i] if i < len(comps_a) else (0, "")
		b = comps_b[i] if i < len(comps_b) else (0, "")
		pairs.append((a, b))
	for i, (a, b) in enumerate(pairs):
		# Only a pair of real integers can differ *numerically*: "1.0.0-rc1" vs
		# "1.0.0" differs at index 3 by suffix (rc1 vs ""), not by number.
		if a[0] is not None and b[0] is not None and a[0] != b[0]:
			return (i, "numeric")
	for i, (a, b) in enumerate(pairs):
		if a[1] != b[1]:
			return (i, "suffix")
	return (None, None)


def compute_version_delta(current, latest, source: str, tool_id: str | None = None) -> tuple:
	"""→ (delta, scheme, note). The only function callers use; `tool_id` only
	names the tool in the compare-equal warning and may be omitted."""
	if source in ("brew-health", "macos"):
		# brew-health has no versions at all, and a macos candidate's
		# current_version is the running `sw_vers -productVersion` rather than
		# the version of that specific update (references/schemas.md §1.3), so
		# a delta computed from it would be fiction.
		return ("unknown", "none", "no version delta for this source")
	if not isinstance(current, str) or not isinstance(latest, str) or not current.strip() or not latest.strip():
		return ("unknown", "none", "missing version")

	core_a, build_a = split_cask_tuple(current.strip())
	core_a, rev_a = split_brew_revision(core_a)
	core_b, build_b = split_cask_tuple(latest.strip())
	core_b, rev_b = split_brew_revision(core_b)
	# Strip the leading v once, here, so detect_version_scheme's date8 test and
	# parse_version_components read the same string — otherwise "v20260622"
	# parses as a date but fails the scheme check and lands in opaque/unknown.
	core_a = _V_PREFIX.sub("", core_a.strip())
	core_b = _V_PREFIX.sub("", core_b.strip())
	comps_a = parse_version_components(core_a)
	comps_b = parse_version_components(core_b)
	if not comps_a or not comps_b:
		return ("unknown", "none", "no numeric component")

	scheme = detect_version_scheme(core_a, comps_a, core_b, comps_b)
	index, kind = first_difference(comps_a, comps_b)
	# Direction is ignored throughout: a downgrade classifies by the same
	# first-differing index as an upgrade would.
	if index is None:
		if rev_a != rev_b or build_a != build_b:
			# "revision" means packaging-only and nothing else — a Homebrew _N
			# bump or a cask build-half bump with an identical upstream
			# version. Deliberately NOT used for semver index ≥ 3
			# (ImageMagick's 7.1.2-27 → 7.1.2-29 is a real upstream patch
			# level, so that is "patch").
			return ("revision", scheme, "packaging revision only")
		# Shouldn't happen — the tool wouldn't be listed as outdated.
		if tool_id:
			print(f"warning: {tool_id}: current and latest compare equal", file=sys.stderr)
		return ("unknown", scheme, "versions compare equal")
	if scheme == "opaque":
		return ("unknown", "opaque", "build-number scheme, not interpretable")
	if kind == "suffix":
		# A same-number letter bump is upstream's own bugfix marker (tmux
		# 3.7b → 3.7c); a pre-release → final (1.0.0-rc1 → 1.0.0) lands here too.
		return ("patch", scheme, f"suffix change at index {index}")
	if scheme == "date":
		return ("minor", "date", "date-versioned release")
	if scheme == "calver":
		# Calendar versioning never produces "major", by design: a calver year
		# rolls over on the calendar, not on a compatibility promise, and
		# mapping it to major would put yt-dlp/mise/bitwarden in the major box
		# every January. "minor" is the honest middle — a real upstream
		# release, no compatibility promise either way — and never reaches the
		# pre-accept path on delta grounds. A trailing-component bump
		# (2026.2 → 2026.2.4) is a genuine bugfix release, so "patch".
		return ("minor" if index <= 1 else "patch", "calver", f"calver index {index}")
	if comps_a[0][0] == 0 and comps_b[0][0] == 0:
		# semver §4 — in 0.x "anything MAY change at any time", so every
		# position shifts one step up in significance (uv 0.11 → 0.12 and
		# codex 0.144 → 0.149, which removed a documented flag, are majors).
		delta = {0: "major", 1: "major", 2: "minor"}.get(index, "patch")
		return (delta, scheme, f"index {index} (0.x rule)")
	delta = {0: "major", 1: "minor", 2: "patch"}.get(index, "patch")
	return (delta, scheme, f"index {index}")


# ── security extraction (references/assembly.md §Security Extraction) ───────
# Research writes free text; CVE ids show up wherever a subagent mentions
# them. `\b` before CVE rejects "NOTCVE-2026-1234"; (?:19|20)\d{2} pins the
# year so "CVE-3026-1234" doesn't match; \d{4,} has no upper bound because
# MITRE's sequence has none — capping it would silently turn a real 7-digit id
# into a non-match.
_CVE_RE = re.compile(r"\bCVE-(?:19|20)\d{2}-\d{4,}\b", re.IGNORECASE)
# The vendor's own count when it says "fixes 33 CVEs" without listing them.
# (?<![\d.]) is the guard against stunnel's real context sentence "Both 5.80
# CVEs need a running service", which otherwise yields a bogus claim of 80,
# and against "1234 CVEs" pairing with \d{1,3}.
_CVE_CLAIM_RE = re.compile(
	r"(?<![\d.])(\d{1,3})\s+"
	r"(?:CVEs?|security (?:issues|vulnerabilities|fixes|advisories)|vulnerabilities)\b",
	re.IGNORECASE)


def content_items(tool: dict) -> list:
	"""The tool's substantive changelog content — the two arrays that carry a
	category *and* a severity."""
	return list(tool.get("headliners") or []) + list(tool.get("relevancy") or [])


def cve_sort_key(cve_id: str) -> tuple:
	"""(year, sequence) as ints, so CVE-2026-9595 sorts before CVE-2026-12143
	— the page renders the list verbatim and a lexical sort gets that pair
	wrong."""
	parts = cve_id.split("-")
	try:
		return (int(parts[1]), int(parts[2]))
	except (IndexError, ValueError):
		return (0, 0)


def _cve_scan_texts(tool: dict) -> list:
	"""Every field a CVE id may be counted from: the claim scan's fields (see
	_cve_claim_texts below) plus `relevancy[].motivating_change` and
	`context[]` — that delta *is* the difference between the two scopes.
	Excluded deliberately:
	links[].embedded_content (an unbounded changelog excerpt that can cover
	releases outside this current→latest range, inflating the count with CVEs
	the user isn't being asked about) and links[].url (a CVE index page isn't a
	claim about this range); suggestions[] (derived text restating headliners —
	doubles the false-positive surface, changes no result); and
	config_status.detail (backward-looking audit prose, where an id is usually
	a *prior* run's finding). Safe because has_security never depends on ids —
	a missed id understates cve_count, it can't flip a security release into a
	non-security one."""
	texts = list(_cve_claim_texts(tool))
	for item in tool.get("relevancy") or []:
		texts.append(item.get("motivating_change"))
	for item in tool.get("context") or []:
		texts.extend((item.get("title"), item.get("detail")))
	return [t for t in texts if isinstance(t, str)]


def _cve_claim_texts(tool: dict) -> list:
	"""Narrower than the id scan: context[] is out, because stunnel's context
	note ("Both 5.80 CVEs need a running service") is exactly the sentence the
	claim pattern must not read as a claim."""
	texts = []
	for item in tool.get("headliners") or []:
		texts.append(item.get("text"))
	for item in tool.get("relevancy") or []:
		texts.extend((item.get("summary"), item.get("detail")))
	return [t for t in texts if isinstance(t, str)]


def extract_cve_ids(tool: dict) -> list:
	ids = set()
	for text in _cve_scan_texts(tool):
		for match in _CVE_RE.findall(text):
			ids.add(match.upper())
	return sorted(ids, key=cve_sort_key)


def extract_cve_claim(tool: dict) -> int | None:
	"""The vendor's own largest stated count, or None. Max wins, never sum:
	Firefox's two releases claim 50 and 47, and Chrome's headliner claims 370
	while a relevancy detail describes a 68-fix subset of it — summing
	double-counts the subset, while the max is a defensible floor."""
	claims = []
	for text in _cve_claim_texts(tool):
		claims.extend(int(n) for n in _CVE_CLAIM_RE.findall(text))
	return max(claims) if claims else None


def config_needs_attention(tool: dict) -> bool:
	"""One spelling of "this tool's config may be stale", read defensively:
	`config_status` is normalized in both build paths but can still be
	present-but-null on a Tool object arriving from anywhere else."""
	return (tool.get("config_status") or {}).get("state") == "needs_attention"


def suggestion_kind(sug: dict) -> str:
	"""`kind` is omittable and defaults to "edit" (references/schemas.md §1.2),
	so every read has to apply that default — the `!= "upgrade"` and
	`== "edit"` tests below both change meaning without it. (`== "watch-item"`
	happens not to, since the default is never that value, but it goes through
	the same helper so the five call sites can't drift apart again.)"""
	return sug.get("kind") or "edit"


def research_produced_content(tool: dict) -> bool:
	"""A subagent that failed, timed out, or returned an empty shell has told
	us nothing — never the same as "nothing but security fixes"."""
	return not tool.get("research_error") and bool(tool.get("headliners"))


def _allowed_for_security_only(item: dict) -> bool:
	"""Only an explicit allowed (category, severity) pair passes, so a missing
	or unrecognized value disqualifies rather than slipping through."""
	c, s = item.get("category"), item.get("severity")
	return (c == "security"  # any severity
			or (c == "fixes" and s in ("info", "notable"))
			or (c == "notes" and s == "info"))


def compute_security_only(tool: dict, has_security: bool) -> bool:
	"""Substantive content is security/patch only. A `features` item at any
	severity disqualifies (a new feature is not a security patch), and so does
	`notes` above info — in one live run codex's breaking change ("`codex exec
	--full-auto` was removed") is filed as notes/notable, which category alone
	would have waved through. A `security` item at any severity is allowed,
	including warning: a high-severity *security* item is a reason to take the
	update, not to hold it."""
	if not has_security:
		return False
	if not research_produced_content(tool):
		return False
	silent = tool.get("vendor_silent_categories") or []
	if [c for c in silent if c != "security"]:
		# The vendor published nothing about a non-security category, so we
		# can't claim the content is security-only.
		return False
	return all(_allowed_for_security_only(i) for i in content_items(tool))


def compute_impact(tool: dict) -> str:
	"""Does anything in this release touch *this* setup? → "none" |
	"possible" | "unknown"."""
	if tool["source"] == "brew-health":
		# A health finding is about this machine by definition; an expected one
		# (the intentional GNU-utils PATH note) is explicitly no-action.
		return "none" if tool.get("health_expected") else "possible"
	if not research_produced_content(tool):
		# Research failed or produced no headliners → no basis for a verdict.
		# "unknown" can never reach security_auto, so it never renders as
		# auto-approved.
		return "unknown"
	rel = tool.get("relevancy") or []
	hl = tool.get("headliners") or []
	if (tool.get("pinned")
			or config_needs_attention(tool)
			or any(suggestion_kind(s) in ("edit", "watch-item") for s in tool.get("suggestions", []))
			or any(r.get("severity") == "incompatible" for r in rel)
			# A security-category relevancy is a reason to *upgrade*, not a
			# risk of upgrading ("CVE-2026-18408 turns any dump this machine
			# restores into a shell-execution vector"), so it's excluded here —
			# treating it as impact made security_auto permanently empty across
			# a whole live run.
			or any(r.get("severity") in ("notable", "warning") and r.get("category") != "security" for r in rel)
			# A breaking change upstream is impact-shaped whether or not a
			# subagent wrote a relevancy item for it (mise:rust: three
			# fixes/warning headliners, no relevancy).
			or any(h.get("severity") in ("warning", "incompatible") and h.get("category") != "security" for h in hl)):
		return "possible"
	return "none"


def compute_security(tool: dict) -> dict:
	if tool["source"] == "brew-health":
		# Forced false: the security section is about *patches* the user can
		# take. An untrusted tap is a trust decision, not a shipped fix, and
		# counting it in tools_with_security would make the section's count
		# disagree with the cards it lists. Its security character still shows
		# via the health→category map (untrusted_tap → a security headliner).
		return {
			"cve_ids": [],
			"cve_count": 0,
			"cve_claimed_count": None,
			"has_security": False,
			"security_only": False,
			"impact": compute_impact(tool),
		}
	cve_ids = extract_cve_ids(tool)
	# vendor_silent_categories == ["security"] counts: it is research's explicit
	# statement "this release has security content the vendor refused to
	# detail", which lands the tool in security_mixed and gets it looked at.
	has_security = bool(
		any(i.get("category") == "security" for i in content_items(tool))
		or "security" in (tool.get("vendor_silent_categories") or [])
		or cve_ids)
	return {
		"cve_ids": cve_ids,
		# ALWAYS len(cve_ids) — an id-backed count, never a claim, so the page
		# can link every counted CVE to something.
		"cve_count": len(cve_ids),
		"cve_claimed_count": extract_cve_claim(tool),
		"has_security": has_security,
		"security_only": compute_security_only(tool, has_security),
		"impact": compute_impact(tool),
	}


# ── risk_level (references/assembly.md §Risk Level, "default-accept low-risk upgrades") ──
# Computed here from objective signals already in the report rather than a
# subjective per-tool call from research — code enforces one rule
# consistently instead of relying on every subagent to judge it the same
# way. "elevated" if any of: pinned, a relevancy finding above "info", any
# edit-kind suggestion authored, a major/unknown version_delta, or research
# that told us nothing. The delta condition reads version_delta rather than
# comparing leading integers itself, so the report never carries two different
# answers to "how big is this bump" — an unknown delta size is not a low-risk
# delta, and neither is unknown *content*: a tool whose research failed has no
# headliners, no relevancy and no edit suggestions, and would otherwise score
# "low" and get pre-accepted, i.e. the updates we understand least would be
# the ones auto-approved.
def compute_risk_level(tool: dict) -> str:
	if tool["source"] == "brew-health":
		# An expected/no-action finding (path_note) is informational; anything
		# else is structural, so it never reads as quietly fine.
		return "low" if tool.get("health_expected") else "elevated"
	if tool.get("pinned"):
		return "elevated"
	for item in tool.get("relevancy", []):
		if item.get("severity") in ("warning", "incompatible"):
			return "elevated"
	for sug in tool.get("suggestions", []):
		if suggestion_kind(sug) == "edit":
			return "elevated"
	if tool["version_delta"] in ("major", "unknown"):
		return "elevated"
	if tool.get("research_error"):
		return "elevated"
	if not tool.get("headliners") and not (tool.get("vendor_silent_categories") or []):
		# Returned an object but wrote nothing, and didn't say the vendor was
		# silent — documented silence (claudebar) is fine, this isn't.
		return "elevated"
	return "low"


# ── review_bucket (references/assembly.md §Review Buckets and Pre-Accept) ────
def baseline_upgrade(tool: dict):
	"""The synthesized baseline suggestion, or None. Identified by position +
	kind + `:upgrade` id suffix, so a brew-health finding's
	`{tool_id}:remediate` never matches.

	On the decision path (finalize_tool → review_bucket/pre_accept) this always
	runs *before* main()'s id-uniqueness renaming pass, which is what the
	identification assumes. The scoring path (score_tool, via build_highlights)
	necessarily runs *after* it, and stays correct for the case that pass
	actually hits: a collision *within* one tool renames the research-authored
	duplicate, never the baseline, because the baseline is `suggestions[0]` and
	claims the id first. The one case that degrades is a cross-tool collision
	where an earlier tool's research suggestion squats on this tool's baseline
	id — the baseline is then renamed to ":upgrade-2" and this returns None
	afterwards, costing the tool `score_tool()`'s 10-point `manual_action`
	signal. Bounded and never silent: it can only lower a highlight score,
	`review_bucket`/`pre_accept` were computed before the rename, and the pass
	prints a warning naming both ids."""
	sugs = tool.get("suggestions") or []
	if not sugs:
		return None
	first = sugs[0]
	if suggestion_kind(first) == "upgrade" and str(first.get("id", "")).endswith(":upgrade"):
		return first
	return None


def compute_review_bucket(tool: dict) -> str:
	"""Strict precedence, first match wins. Note review_bucket is a
	*review-effort* axis, orthogonal to source: the page groups brew-health
	cards by `source` and counts them with summary.health_count, never by
	bucket — "routine" on the one expected PATH note means "nothing to decide
	here", not "hide it"."""
	sec = tool["security"]
	if tool["source"] == "brew-health":
		return "routine" if tool.get("health_expected") else "attention"
	baseline = baseline_upgrade(tool)
	runnable = bool(baseline and baseline.get("auto_runnable"))
	if (sec["has_security"] and sec["security_only"] and sec["impact"] == "none"
			and tool["version_delta"] not in ("major", "unknown") and runnable):
		return "security_auto"
	if sec["has_security"]:
		return "security_mixed"
	# No delta or research_error test here on purpose: risk_level is computed
	# first (finalize_tool's dependency order) and already returns "elevated"
	# for both, so repeating them would be dead code that reads like a
	# safety net.
	if (tool["risk_level"] == "elevated"
			or config_needs_attention(tool)
			or any(suggestion_kind(s) != "upgrade" for s in tool.get("suggestions", []))
			or not runnable):
		return "attention"
	# A vendor that published nothing lands here, not in attention, provided
	# research ran and said so — claudebar publishes no notes at all, every
	# run, forever, and demanding attention for it each time is noise the user
	# can't act on. A research *failure* is different: transient, fixable by a
	# rerun, and it raises risk_level above.
	return "routine"


def apply_pre_accept(tool: dict) -> None:
	"""The single pre-accept mechanism (the page reads `pre_accept` instead of
	re-deriving it from risk_level). Only the baseline `{source}:{name}:upgrade`
	suggestion is ever eligible — never a research-authored edit, never a
	watch-item, never a brew-health remediation. `auto_runnable: false` is a
	hard exclusion: "accepted" would claim a decision about something the skill
	cannot execute. `needs_sudo` deliberately does *not* block it — blocking it
	would un-pre-accept nearly every cask (the heuristic defaults casks to
	true), and it isn't silent: the card renders visibly as ACCEPTED before
	submit and apply routes through the askpass prompt the user answers
	interactively (the page renders a "needs admin password" chip for it).
	The security_auto clause is a union with the existing risk_level path, not
	a second mechanism; it can only ever differ from `risk_level == "low"` for
	a tool whose one elevating signal is a security-category relevancy at
	warning severity — "this security fix matters to you", a reason to take the
	update rather than hold it."""
	baseline = baseline_upgrade(tool)
	for sug in tool.get("suggestions", []):
		sug["pre_accept"] = bool(
			sug is baseline
			and sug.get("auto_runnable")
			and (tool["risk_level"] == "low" or tool["review_bucket"] == "security_auto"))


def finalize_tool(tool: dict) -> None:
	"""Compute every derived field, in dependency order. Mutates in place.

	The order is strict — version_delta → security → risk_level →
	review_bucket → pre_accept — and anything else produces stale reads. This
	runs as the last statement of both build paths, which is *before* main()'s
	suggestion-id uniqueness pass: baseline_upgrade() identifies the baseline
	by its ":upgrade" id suffix, and that pass can rename a colliding id to
	":upgrade-2". Because pre_accept is written onto the suggestion dict in
	place, a later rename is harmless; running finalize_tool() after it would
	break baseline detection."""
	delta, scheme, note = compute_version_delta(
		tool.get("current_version"), tool.get("latest_version"), tool["source"], tool.get("id"))
	tool["version_delta"] = delta
	tool["version_scheme"] = scheme
	tool["version_delta_note"] = note
	tool["security"] = compute_security(tool)
	tool["risk_level"] = compute_risk_level(tool)
	tool["review_bucket"] = compute_review_bucket(tool)
	apply_pre_accept(tool)


# ── evidence path validation (references/assembly.md §Evidence Validation, "verify evidence paths") ──
# Evidence strings are either a repo-relative "path" or "path:line" (checked
# against the given repo roots), or a non-path citation (a commit hash/
# subject, a changelog.md entry description) that config_status evidence
# also uses — those are left alone. Never drops an item either way; just
# warns to stderr so an agent skimming the run can catch a bad citation.
# The word must stand alone: `dotfiles/commit-hooks/x.sh` and
# `config/commit/msg` are paths worth checking, while `macos-setup commit
# 5f25045 — "…"` is a citation with nothing to check. Anchoring the
# alternative to the string start instead would reject 19 real citations in
# one live run ("dotfiles commit 1a55cef — …"), trading one false negative for
# a pile of false "evidence not found" warnings.
_COMMIT_LIKE = re.compile(r"\bcommit\b(?![-/])|^[0-9a-f]{7,40}\b", re.IGNORECASE)

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


def as_item_list(value, tool_id: str, field: str, member_type=dict) -> list:
	"""Coerce a research-supplied array field to a list of well-shaped members.

	Same doctrine as `as_evidence_list()`/`normalize_config_status()` above,
	applied to every array the schema declares: `dict.get(key, default)` only
	substitutes the default when the key is *absent*, so a present-but-null
	`"relevancy": null` reaches `for item in …` as None, and a drifted
	`"headliners": "no notable changes"` (or `["a change"]`, or `{"text": …}`)
	reaches `item.get(…)` as a string. Both abort the run — and one report is
	assembled from ~22 research files covering ~77 tools, so a single drifted
	file used to destroy the whole report *after* the expensive part of the
	session was already spent. Degrade to one warned-about tool instead:
	coerce, warn to stderr, never abort, never silently swallow."""
	if value is None:
		return []
	if not isinstance(value, list):
		print(f"warning: {tool_id}: {field} was {type(value).__name__}, not an array — ignoring it: {value!r}", file=sys.stderr)
		return []
	kept = []
	for item in value:
		if isinstance(item, member_type):
			kept.append(item)
		else:
			print(f"warning: {tool_id}: {field} entry was {type(item).__name__}, not {member_type.__name__} — dropping it: {item!r}", file=sys.stderr)
	return kept


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
	headliners = as_item_list(research_obj.get("headliners"), tool_id, "headliners")
	if not headliners:
		headliners = [{
			"text": candidate.get("detail", candidate.get("name", "")),
			"category": _HEALTH_CATEGORY_GROUP.get(category, "notes"),
			"severity": candidate.get("severity", "notable"),
		}]

	# Suggestions: research's if present, else synthesize from the finding's
	# default remediation (skipped for expected/no-action findings, e.g. the
	# intentional GNU-utils PATH note, which carries remediation: null).
	suggestions = as_item_list(research_obj.get("suggestions"), tool_id, "suggestions")
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
		"links": as_item_list(research_obj.get("links"), tool_id, "links"),
		"config_status": normalize_config_status(research_obj),
		"relevancy": as_item_list(research_obj.get("relevancy"), tool_id, "relevancy"),
		"context": as_item_list(research_obj.get("context"), tool_id, "context"),
		"release_inventory": as_item_list(research_obj.get("release_inventory"), tool_id, "release_inventory"),
		"vendor_silent_categories": as_item_list(
			research_obj.get("vendor_silent_categories"), tool_id, "vendor_silent_categories", member_type=str),
		"suggestions": suggestions,
	}
	# Same needs_attention-must-have-a-suggestion guard build_tool applies —
	# a brew-health research subagent could set needs_attention on a finding
	# whose default remediation is null (e.g. missing_keg), shipping an
	# unactionable banner; surface it loudly rather than silently.
	if config_needs_attention(tool) and not tool["suggestions"]:
		print(f"warning: {tool_id}: config_status is needs_attention with no suggestion addressing it", file=sys.stderr)
	# Every derived field (version_delta/security/risk_level/review_bucket/
	# pre_accept) comes from the one shared entry point, so a health finding
	# and a version-outdated tool can never disagree about what a field means.
	finalize_tool(tool)
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
		# Every array normalized here, at the one boundary where research's
		# free-form output becomes a Tool object (as_item_list above) — nothing
		# downstream re-checks a shape.
		"headliners": as_item_list(research_obj.get("headliners"), tool_id, "headliners"),
		"links": as_item_list(research_obj.get("links"), tool_id, "links"),
		"config_status": normalize_config_status(research_obj),
		"relevancy": as_item_list(research_obj.get("relevancy"), tool_id, "relevancy"),
		"context": as_item_list(research_obj.get("context"), tool_id, "context"),
		"release_inventory": as_item_list(research_obj.get("release_inventory"), tool_id, "release_inventory"),
		"vendor_silent_categories": as_item_list(
			research_obj.get("vendor_silent_categories"), tool_id, "vendor_silent_categories", member_type=str),
		"suggestions": as_item_list(research_obj.get("suggestions"), tool_id, "suggestions"),
	}

	# Enforce the needs_attention-must-have-a-suggestion rule (references/research.md
	# §Config Status / references/assembly.md §Evidence Validation, enforcement point) — a
	# violation here is a research-prompt bug, but assembly still surfaces
	# it loudly rather than silently shipping an unactionable banner.
	if config_needs_attention(tool) and not tool["suggestions"]:
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

	finalize_tool(tool)
	return tool


# ── highlights (references/assembly.md §Highlights) ─────────────────────────
# "The biggest decision drivers / inputs needed / major patches", ranked by a
# fixed score rather than per-tool judgment — assemble.py is a plain script, so
# the ranking has to be reproducible from the data alone. review_bucket
# "security_auto" contributes nothing: it is by definition the bucket that
# needs no decision.
_WATCH_HIT_RE = re.compile(r"watch[\s\-]?item hit", re.IGNORECASE)
_SEVERITY_RANK = {"info": 0, "notable": 1, "warning": 2, "incompatible": 3}
_RANK_SEVERITY = {v: k for k, v in _SEVERITY_RANK.items()}
# Threshold 40: a bare "major" (25) or a bare CVE count (10) must not qualify
# on its own — that's what keeps Chrome/Firefox/gcloud's rolling majors out of
# the highlights while leaving them counted in the major box. Lowering it
# re-admits exactly the noise it exists to exclude; raising the cap is safe.
_HIGHLIGHT_THRESHOLD = 40
_HIGHLIGHT_CAP = 8
_WHY_MAX = 220


def _max_sev_rank(tool: dict) -> int:
	ranks = [_SEVERITY_RANK.get(i.get("severity"), -1) for i in content_items(tool)]
	return max(ranks) if ranks else -1


def score_tool(tool: dict) -> tuple:
	"""→ (score, reasons) — reason codes in a fixed emission order, so the
	page's chips never reshuffle between runs."""
	rel = tool.get("relevancy") or []
	hl = tool.get("headliners") or []
	sugs = tool.get("suggestions") or []
	baseline = baseline_upgrade(tool)
	scored = []

	def add(points: int, code: str, fires) -> None:
		if fires:
			scored.append((points, code))

	# references/research.md's "⚠ Watch item hit: …" convention — the user
	# asked to be told about this topic, so a hit outranks almost everything.
	# Detection is textual because nothing in the schema marks a hit
	# structurally; the research contract makes the phrase mandatory.
	watch_hit = any(_WATCH_HIT_RE.search(f"{r.get('summary') or ''} {r.get('detail') or ''}") for r in rel)

	add(100, "incompatible_finding", any(r.get("severity") == "incompatible" for r in rel))
	add(70, "watch_item_hit", watch_hit)
	add(60, "config_stale", config_needs_attention(tool))
	add(45, "warning_finding", any(r.get("severity") == "warning" for r in rel))
	add(40, "breaking_change", any(h.get("severity") == "incompatible" for h in hl))
	add(35, "proposed_edit", any(suggestion_kind(s) == "edit" for s in sugs))
	add(30, "pinned", bool(tool.get("pinned")))
	add(30, "security_mixed", tool["review_bucket"] == "security_mixed")
	add(25, "major_bump", tool["version_delta"] == "major")
	add(25, "watch_item_proposed", any(suggestion_kind(s) == "watch-item" for s in sugs))
	add(20, "changelog_warning", any(h.get("severity") == "warning" for h in hl))
	add(20, "research_failed", bool(tool.get("research_error")))
	add(15, "unknown_scheme", tool["version_delta"] == "unknown")
	add(10, "cves", tool["security"]["cve_count"] >= 1)
	add(10, "manual_action", bool(baseline) and not baseline.get("auto_runnable"))
	return (sum(p for p, _ in scored), [code for _, code in scored])


def _truncate_why(text: str) -> str:
	"""≤ _WHY_MAX chars, cut on a word boundary with an ellipsis."""
	text = " ".join((text or "").split())
	if len(text) <= _WHY_MAX:
		return text
	cut = text[:_WHY_MAX - 1]
	space = cut.rfind(" ")
	if space > 0:
		cut = cut[:space]
	return cut.rstrip() + "…"


def _highlight_title(tool: dict) -> str:
	if tool["source"] == "brew-health":
		return tool.get("name") or tool["id"]   # a health finding has no versions
	return f"{tool.get('name')} {tool.get('current_version')} → {tool.get('latest_version')}"


def _highlight_why(tool: dict) -> str:
	"""First match in a fixed order, so the card's one line is the most
	specific thing we know about this tool."""
	rel = tool.get("relevancy") or []
	if rel:
		# Ties resolve to array order — max() keeps the first maximum.
		best = max(rel, key=lambda r: _SEVERITY_RANK.get(r.get("severity"), -1))
		if best.get("summary"):
			return _truncate_why(best["summary"])
	cs = tool.get("config_status") or {}
	if config_needs_attention(tool) and cs.get("detail"):
		return _truncate_why(cs["detail"])
	if tool.get("research_error"):
		return "Research produced no changelog for this update."
	if tool["review_bucket"] in ("security_auto", "security_mixed"):
		for item in tool.get("headliners") or []:
			if item.get("category") == "security" and item.get("text"):
				return _truncate_why(item["text"])
	if tool["version_delta"] == "major":
		return _truncate_why(f"Major version bump {tool.get('current_version')} → {tool.get('latest_version')}.")
	for item in tool.get("headliners") or []:
		if item.get("text"):
			return _truncate_why(item["text"])
	return ""


def _highlight_severity(tool: dict) -> str:
	"""Max severity across headliners + relevancy, in relevancy's vocabulary so
	the page reuses the existing palette."""
	rank = _max_sev_rank(tool)
	if rank in _RANK_SEVERITY:
		return _RANK_SEVERITY[rank]
	if config_needs_attention(tool):
		return "warning"
	if tool.get("research_error") or tool["version_delta"] in ("major", "unknown"):
		return "notable"
	return "info"


def _highlight_object(score: int, reasons: list, tool: dict) -> dict:
	return {
		"tool_id": tool["id"],
		"title": _highlight_title(tool),
		"why": _highlight_why(tool),
		"severity": _highlight_severity(tool),
		# Every suggestion on the tool, in array order (baseline first when
		# present) — the page looks each id up in tools[] to decide whether to
		# offer jump vs. approve. Read after main()'s id-uniqueness pass.
		"suggestion_ids": [s["id"] for s in tool.get("suggestions") or [] if s.get("id")],
		"reasons": reasons,
		"score": score,
	}


def build_highlights(tools: list) -> list:
	scored = []
	for tool in tools:
		score, reasons = score_tool(tool)
		if score >= _HIGHLIGHT_THRESHOLD:
			scored.append((score, reasons, tool))
	# No per-source quota — a brew-health finding competes on the same scale
	# (a missing dependency is a real "input needed"). The trailing tool id
	# makes ties fully deterministic.
	scored.sort(key=lambda x: (-x[0], -_max_sev_rank(x[2]), -x[2]["security"]["cve_count"], x[2]["id"]))
	return [_highlight_object(score, reasons, tool) for score, reasons, tool in scored[:_HIGHLIGHT_CAP]]


# ── report-level summary (references/assembly.md §Summary Counts and Output) ──
def summarize_by_delta(tools: list) -> dict:
	"""brew-health findings are environment issues, not updates — excluding
	them preserves sum(by_delta) == total_outdated (the invariant the main
	tab's boxes are built on) and stops non-updates inflating the unknown box."""
	counts = {"major": 0, "minor": 0, "patch": 0, "revision": 0, "unknown": 0}
	for tool in tools:
		if tool["source"] == "brew-health":
			continue
		counts[tool["version_delta"]] += 1
	return counts


def summarize_by_bucket(tools: list) -> dict:
	"""Counts **every** tool, brew-health findings included — the mirror image
	of summarize_by_delta() above, and deliberately so: `review_bucket` is a
	review-effort axis defined for every Tool object (§Review Buckets), and the
	page's bucket lists render health cards alongside version ones. The two
	summaries therefore have different denominators — by_delta sums to
	`total_outdated` (74 here), by_bucket to `total_outdated + health_count`
	(77) — so a page must never mix them in one percentage."""
	counts = {"security_auto": 0, "security_mixed": 0, "attention": 0, "routine": 0}
	for tool in tools:
		counts[tool["review_bucket"]] += 1
	return counts


def summarize_security(tools: list) -> dict:
	# cve_count is the size of the *union* across tools, not the sum of
	# per-tool counts: one advisory routinely lands on two tools (openssh and
	# ssh-copy-id ship from the same source tarball; a bundled-OpenSSL CVE can
	# hit several casks), and counting it twice would inflate the one number
	# the security section leads with.
	ids = set()
	for tool in tools:
		ids.update(tool["security"]["cve_ids"])
	return {
		"cve_count": len(ids),
		"tools_with_security": sum(1 for t in tools if t["security"]["has_security"]),
		"auto_count": sum(1 for t in tools if t["review_bucket"] == "security_auto"),
		"mixed_count": sum(1 for t in tools if t["review_bucket"] == "security_mixed"),
		# Tools whose vendor claims more advisories than we could extract ids
		# for, so the header can read "59 CVEs · 6 tools report more without
		# ids" instead of silently understating.
		"tools_with_unlisted_cves": sum(
			1 for t in tools
			if (t["security"]["cve_claimed_count"] or 0) > t["security"]["cve_count"]),
	}


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
				# Every downstream consumer indexes suggestions by id —
				# write_status.py builds `{sug["id"]: …}` and KeyErrors on a
				# missing one, four steps and one user decision later. Give it
				# a real id here rather than leaving a fourth place to skip it.
				n = 1
				sid = f"{tool['id']}:sug-{n}"
				while sid in seen_ids:
					n += 1
					sid = f"{tool['id']}:sug-{n}"
				print(f"warning: {tool['id']}: suggestion with no id — assigned {sid!r}", file=sys.stderr)
				sug["id"] = sid
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

	# Ranked *after* the uniqueness pass, so highlights[].suggestion_ids carry
	# the final, deduplicated ids.
	highlights = build_highlights(tools)

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
			# Additive (schema_version stays 1 — every consumer only reads keys
			# it knows, and a page written against the new report must still
			# tolerate these being absent when a user reopens an old session).
			"by_delta": summarize_by_delta(tools),
			"by_bucket": summarize_by_bucket(tools),
			"security": summarize_security(tools),
		},
		"repo_context": repo_context,
		"highlights": highlights,
		"tools": tools,
	}

	out_path = os.path.join(session_dir, "report.json")
	with open(out_path, "w", encoding="utf-8") as fh:
		json.dump(report, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")
	print(out_path)


if __name__ == "__main__":
	main()
