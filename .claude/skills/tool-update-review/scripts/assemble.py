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


# ── the non-version finding sources (references/assembly.md §Brew-Health
# Assembly, §Skill-Drift Assembly) ──────────────────────────────────────────
# Sources whose candidates are *findings* rather than version updates: no
# current→latest pair, no CVE scan, no synthesized `:upgrade` baseline, and
# their own summary count instead of `total_outdated`. Kept as one set — and
# read through it everywhere the branch means "this has no version" — so a
# fourth finding source cannot be added to half the branches and land in the
# unknown-delta box for the other half. The report template makes the same
# distinction under the same name (`NON_VERSION_SOURCES`/`isNonVersion`).
# Branches that mean "this *specific* source" (the per-source label maps) stay
# written as `== "..."`.
NON_VERSION_SOURCES = frozenset({"brew-health", "skill-drift"})

# ── sources scripts/check_pin.py can preflight/verify against (WP5/I2:
# references/apply.md §Pinning the reviewed version) ─────────────────────────
# The three sources whose baseline `:upgrade` suggestion can actually run
# unattended (`upgrade_command_and_runnable` below) AND have a package
# manager query check_pin.py can compare against a recorded target_version.
# standalone/macos are deliberately excluded even though they too can carry
# `auto_runnable: true`-adjacent baselines in principle — standalone has no
# canonical `--version` output format to build a generic parser against, and
# macos is a system update, not a package-manager query — both stay on the
# manual, human-verified path in references/apply.md instead. Exported (not
# re-typed) so check_pin.py's `--source` choices and write_status.py's
# `"done"` gate (§Pinning the reviewed version) read the same set and cannot
# silently drift apart, the way a hand-copied list did for `curl` and PATH
# ordering elsewhere in this project.
PIN_CHECKABLE_SOURCES = frozenset({"brew", "cask", "mise"})


def finding_expected(tool) -> bool:
	"""True when a non-version finding needs no decision — brew-health's
	`expected` path notes and skill-drift's local_only/probe_error alike.

	The two sources keep separate flags on the Tool object because they mean
	different things to a reader (`health_expected`: an intentional
	environment note; `drift_expected`: our own patch, or a probe that could
	not run). But every *derived* axis asks the same question of both, so it
	is asked once here rather than as a second copy of the same predicate per
	source per axis — which is how three axes would come to disagree."""
	return bool(tool.get("health_expected") or tool.get("drift_expected"))


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
	if source == "skill-drift":
		# A vendored-skill sync is `git subtree pull` inside a repo the user
		# owns — never privileged. Stated here rather than left to the
		# fall-through so the answer is a decision, not an accident of ordering.
		return False
	return True


# ── auto_runnable / command per source (references/assembly.md §Baseline Suggestion Synthesis;
# references/apply.md §Executing Upgrade Suggestions — pinning the reviewed version, WP5/I2) ──
def upgrade_command_and_runnable(source: str, name: str, version: str | None = None):
	"""Returns (command, auto_runnable, manual_reason, version_pinned).

	`version` is the version that was reviewed (the Tool's `latest_version`
	at assembly time) — the version apply must land on, not whatever is
	latest when the command actually runs later. `version_pinned` tells the
	caller whether `command` itself is guaranteed to reach exactly `version`:

	- **mise** genuinely can pin — `mise upgrade tool@x.y.z` installs that
	  exact version as a CLI argument, confirmed against upstream docs
	  (`mise upgrade tiny@3.0.1` rewrites the version-specific request, not
	  just "upgrade within range"). `version_pinned=True` whenever a version
	  is given; the caller (scripts/check_pin.py) never needs a preflight
	  query for mise as a result — there is nothing upstream can drift out
	  from under an argument.
	- **brew/cask have no such mechanism for an arbitrary formula/cask.**
	  Only a curated handful of formulae ship separately versioned aliases
	  (`python@3.11`); there is no general `brew install name@version`.
	  `brew upgrade`/`brew upgrade --cask` always resolves to whatever the
	  tap currently calls latest, so `version_pinned=False` — apply is
	  expected to preflight-check what that resolves to (via
	  `scripts/check_pin.py preflight`) *before* running it, and refuse
	  rather than run when it has drifted past what was reviewed.
	- `version` is accepted as optional (default `None`) so a caller that
	  only wants `auto_runnable`/`manual_reason` and runs before a version is
	  finalized (`validate_items.py`, pre-assembly) keeps working unchanged;
	  omitting it just means "don't pin even where pinning is possible."
	- **`name` already containing `@` blocks pinning rather than mangling
	  it.** mise's own qualifier syntax uses `:` for a backend
	  (`npm:prettier`, `cargo:ripgrep` — confirmed against upstream docs,
	  `mise use -g npm:prettier@3`), never `@` inside the identifier itself,
	  so `f"{name}@{version}"` is safe for every real mise tool id this
	  project's own `~/.config/mise/config.toml` uses today. But nothing
	  guarantees a future or third-party id can't carry an `@` some other
	  way, and `mise upgrade node@20@24.6.0` would be a malformed request
	  that still claims `version_pinned=True` — a guarantee that reads as
	  one without being one. Refuse to pin in that case: fall back to the
	  unpinned command exactly as when no version is given at all, rather
	  than emit a command that has not been established to be well-formed.

	`version` never changes `auto_runnable`/`manual_reason` for any source —
	pinnability is orthogonal to whether the session may run the command at
	all.
	"""
	if source == "brew":
		return f"brew upgrade {name}", True, None, False
	if source == "cask":
		return f"brew upgrade --cask {name}", True, None, False
	if source == "mise":
		if version and "@" not in name:
			return f"mise upgrade {name}@{version}", True, None, True
		return f"mise upgrade {name}", True, None, False
	if source == "standalone":
		return None, False, "No generic upgrade command for a standalone CLI — check the tool's own docs.", False
	if source == "macos":
		return None, False, "macOS system/app update — install via System Settings or `softwareupdate -i`, not auto-run by this skill.", False
	if source == "skill-drift":
		# Unreachable from build_drift_tool (a drift finding gets no synthesized
		# baseline at all — its action is its own `:sync` remediation), but
		# answered explicitly so a future caller cannot get a runnable command
		# for a subtree pull out of the fall-through.
		return None, False, "Vendored-skill sync is always manual.", False
	return None, False, "Unknown source — no safe default command.", False


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
	if source in NON_VERSION_SOURCES or source == "macos":
		# brew-health and skill-drift have no versions at all, and a macos
		# candidate's current_version is the running `sw_vers -productVersion`
		# rather than the version of that specific update
		# (references/schemas.md §1.3), so a delta computed from it would be
		# fiction. macos is spelled out separately because it *is* a version
		# source — only its collected current_version is untrustworthy.
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
	_cve_claim_texts below) plus `relevancy[].motivating_change`, `context[]`
	and `security.notable[].cve_id`/`.summary` — that delta *is* the difference
	between the two scopes.
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
	# security.notable[] — research selected these from *this* range by
	# construction, which is the exact property links[].embedded_content lacks.
	# cve_id is a literal id field taken as-is; summary is one short line about
	# this range, regex-scanned like the other prose. Both are in the *id* scan
	# only: a summary reading "one of 28 advisories" must not become a vendor
	# claim of 28, which is the same trap the context[] exclusion exists for.
	for item in research_security(tool).get("notable") or []:
		if isinstance(item, dict):
			texts.extend((item.get("cve_id"), item.get("summary")))
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
	if tool["source"] in NON_VERSION_SOURCES:
		# Either finding is about *this* setup by definition — a health finding
		# describes the brew install in front of us, and a drifted vendored
		# skill is the user's own submodule that has fallen behind — so the
		# only question left is whether it needs deciding. Expected means it
		# does not (the intentional GNU-utils PATH note; a local_only patch or
		# a probe that could not run), which is exactly what impact "none"
		# says.
		return "none" if finding_expected(tool) else "possible"
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


# ── CVE severity, notable security items, and the noise floor ───────────────
# (references/assembly.md §Severity Rollup and the Sum Invariant, §Validating
# Research's `notable`; references/research.md §CVE Severity Capture,
# §Selecting Notable Security Items, §The Noise Floor)
#
# Two severity vocabularies meet here and must not be confused. Relevancy
# severity (info/notable/warning/incompatible, `_SEVERITY_RANK` below) is "how
# much does this matter to *this* machine"; CVE severity is "what did the
# issuer rate the flaw". Conflating them is how teamviewer's Linux-only
# CVSS 8.8 would end up reading as urgent on a macOS card.
_CVE_SEVERITIES = ("critical", "high", "medium", "low", "unknown")
_CVE_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}
# Ordering rank for `notable[]`, deliberately NOT _CVE_SEVERITY_RANK. In that
# map `unknown` is 0 because it means "no rating recorded", which is exactly
# right for the worse-wins resolutions that read it — an absent grade must
# never beat a present one. On a `notable[]` entry it means something else:
# research picked this item under one of R5's clauses and *nobody published a
# grade for it*. R5 clause 3 exists precisely for that case (`brew:iproute2mac`'s
# command injection was never assigned a CVE, and is one of the two most
# important security items in the recorded run), so ranking it below `low`
# would evict the item the clause was written for. An absent grade is not
# evidence of a small flaw. The vocabulary is unchanged — this is a ranking
# fix, not a schema change (references/schemas.md §1.9).
_NOTABLE_SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "unknown": 2, "low": 1}
# Where the rating came from. A grade with no basis is not a grade — research
# is forbidden from deriving a severity from how a description reads, and this
# field is how assembly can tell a fetched rating from an impression.
_SEVERITY_BASES = ("vendor", "nvd", "cvss", "unrated")
_NOTABLE_CAP = 3
# Below this length a summary/text comparison is not evidence of anything —
# two 12-character bullets can collide by accident. Only used for the
# no-id fallback in resolve_notable_ref().
_REF_MATCH_MIN = 40


def severity_rank(ranks: dict, value, default: int) -> int:
	"""`ranks.get(value, default)` for a severity a research subagent wrote.

	Every other read of a severity is an `==` or an `in (…)` comparison, which
	survives any shape; a *dict lookup* does not — `{}.get(["warning"])` raises
	`TypeError: unhashable type`. One relevancy item written as
	`"severity": ["warning"]` used to raise that out of build_highlights()'
	sort key and abort the report for all 78 tools. An unrecognized severity
	must cost that item its rank and nothing more; as_item_list() has already
	warned about the shape at the boundary, so this stays quiet."""
	if not isinstance(value, str):
		return default
	return ranks.get(value, default)


def research_security(tool: dict) -> dict:
	"""The research-supplied `security` block, read defensively. Until
	compute_security() overwrites it, `tool["security"]` is whatever the
	research file carried — including a bare string or null (§Shape
	Normalization); every read of it goes through here."""
	block = tool.get("security")
	return block if isinstance(block, dict) else {}


def _norm_text(value) -> str:
	"""Whitespace-collapsed text, for the comparisons below. Never used to
	*match* a highlight's `why` — that string is already truncated to 220
	chars, so a comparison against it silently fails on a longer summary."""
	return " ".join(str(value or "").split())


def content_ref(kind: str, index: int) -> str:
	"""The stable identity of one content item on a tool — `"rel:0"`,
	`"hl:2"`. Emitted on both sides of the highlights↔security dedupe
	(`highlights[].why_ref`, `security.notable[].source_ref`) so the match is
	on a slot rather than on prose."""
	return f"{kind}:{index}"


def resolve_cve_severities(cve_ids: list, entries: list, tool_id: str) -> tuple:
	"""→ (emitted_entries, {cve_id: severity}) for the ids research actually
	graded. An id it graded but assembly never extracted is dropped with a
	warning rather than inflating the rollup; a grade with no basis, or a word
	outside the vocabulary, becomes `unknown` rather than a guess."""
	id_set = set(cve_ids)
	by_id: dict = {}
	basis_by_id: dict = {}
	for entry in entries:
		cid = entry.get("cve_id")
		cid = cid.upper() if isinstance(cid, str) else ""
		if not cid:
			print(f"warning: {tool_id}: cve_severities entry has no cve_id — dropping it: {entry!r}", file=sys.stderr)
			continue
		if cid not in id_set:
			print(f"warning: {tool_id}: cve_severities rates {cid}, which is not in cve_ids — "
				f"dropping it (a rating for something this range does not contain)", file=sys.stderr)
			continue
		sev, basis = entry.get("severity"), entry.get("basis")
		if sev not in _CVE_SEVERITIES:
			print(f"warning: {tool_id}: {cid} severity {sev!r} is not one of {_CVE_SEVERITIES} — reading it as unknown", file=sys.stderr)
			sev = "unknown"
		if sev != "unknown" and basis not in _SEVERITY_BASES:
			print(f"warning: {tool_id}: {cid} is rated {sev!r} with basis {basis!r} — a rating with no "
				f"recorded source is not a rating; reading it as unknown", file=sys.stderr)
			sev = "unknown"
		if sev == "unknown":
			# Recording it would say nothing an absent entry doesn't already say.
			continue
		if cid in by_id and by_id[cid] != sev:
			# Two sources disagreeing is real once ratings come from a vendor
			# page and NVD. Understating a severity is the failure mode with a
			# cost, so the worse wins.
			print(f"warning: {tool_id}: {cid} rated both {by_id[cid]!r} and {sev!r} — keeping the worse", file=sys.stderr)
			if _CVE_SEVERITY_RANK[sev] <= _CVE_SEVERITY_RANK[by_id[cid]]:
				continue
		by_id[cid], basis_by_id[cid] = sev, basis
	emitted = [{"cve_id": cid, "severity": by_id[cid], "basis": basis_by_id[cid]}
		for cid in sorted(by_id, key=cve_sort_key)]
	return emitted, by_id


def rollup_severity_counts(cve_ids: list, severity_by_id: dict) -> dict:
	"""`{critical, high, medium, low, unknown}` over this tool's ids.

	**The invariant `sum(counts.values()) == cve_count` holds by construction**,
	because the loop iterates `cve_ids` and every id lands in exactly one
	bucket — an id research forgot to rate becomes `unknown` instead of a
	broken sum. Iterating the rating map instead would silently break it,
	which is why test_assemble.py §6 asserts the sum anyway."""
	counts = dict.fromkeys(_CVE_SEVERITIES, 0)
	for cve_id in cve_ids:
		counts[severity_by_id.get(cve_id, "unknown")] += 1
	return counts


def clean_notable(entries: list, tool_id: str) -> list:
	"""Shape-normalize research's `notable[]` members. Never validates, orders
	or caps — that is finalize_notable(), which runs after the CVE-id scan.

	Running first is what makes the scan see every id research supplied,
	including ones on entries the cap later drops: `cve_ids[]` is the range's
	id inventory, not a list of what the card renders, so an id must not
	vanish from `severity_counts` because its entry lost a `notable[]` slot."""
	kept = []
	for entry in entries:
		raw_summary = entry.get("summary")
		if raw_summary is not None and not isinstance(raw_summary, str):
			# _norm_text() would stringify it — `{"text": "…"}` renders as its
			# repr, `42` as "42" — and the page's `typeof === 'string'` guard
			# then passes it, because by then it *is* a string. The drift has
			# to be caught on this side or not at all.
			print(f"warning: {tool_id}: security.notable summary was {type(raw_summary).__name__}, "
				f"not a string — dropping the entry rather than rendering its repr: {entry!r}", file=sys.stderr)
			continue
		summary = _norm_text(raw_summary)
		if not summary:
			print(f"warning: {tool_id}: security.notable entry has no summary — dropping it: {entry!r}", file=sys.stderr)
			continue
		cve_id = entry.get("cve_id")
		cve_id = cve_id.strip().upper() if isinstance(cve_id, str) and cve_id.strip() else None
		advisory_id = entry.get("advisory_id")
		advisory_id = advisory_id.strip() if isinstance(advisory_id, str) and advisory_id.strip() else None
		affects_me = entry.get("affects_me")
		if affects_me is not None and not isinstance(affects_me, bool):
			print(f"warning: {tool_id}: security.notable affects_me was {type(affects_me).__name__}, "
				f"not a bool — coercing: {affects_me!r}", file=sys.stderr)
		kept.append({
			"cve_id": cve_id,
			"advisory_id": advisory_id,
			"severity": entry.get("severity"),
			"summary": summary,
			"affects_me": bool(affects_me),
		})
	return kept


def resolve_notable_ref(tool: dict, entry: dict):
	"""→ the `content_ref()` of the headliner/relevancy item this notable
	restates, or None. Resolved by a stable token first — the CVE or advisory
	id, which is exactly the kind of handle prose comparison lacks — and only
	then by comparing the *untruncated* summaries."""
	rel = tool.get("relevancy") or []
	hl = tool.get("headliners") or []
	# HAZARD, deliberately unguarded: an `advisory_id` is free-form vendor text,
	# so a short one ("2026-11") can substring-match prose that never mentioned
	# it ("Release 2026-11 ships the new resolver"). The obvious guard —
	# require length and a digit — admits that exact string, and word-boundary
	# matching does not save it either; a real fix needs a stricter id shape,
	# not a longer predicate. A false ref costs one deduped highlight, so this
	# stays a known cost rather than a wrong guard.
	token = (entry.get("cve_id") or entry.get("advisory_id") or "").upper()
	if token:
		matches = [i for i, item in enumerate(rel)
			if token in _norm_text(" ".join(str(item.get(k) or "")
				for k in ("summary", "detail", "motivating_change"))).upper()]
		if matches:
			# The **max-severity** match, not the first: _highlight_why_parts()
			# picks the max-severity relevancy item for a highlight's `why`, and
			# the R6 dedupe compares that ref against this one. Returning the
			# first match instead misses the dedupe on any tool where two
			# relevancy items name one CVE — the highlight then restates the
			# security card in full, which is the duplication R6 exists to stop.
			# max() keeps the first maximum, exactly as that function does.
			return content_ref("rel", max(matches, key=lambda i: severity_rank(_SEVERITY_RANK, rel[i].get("severity"), -1)))
		for i, item in enumerate(hl):
			if token in _norm_text(item.get("text")).upper():
				return content_ref("hl", i)
	summary = _norm_text(entry.get("summary"))
	if len(summary) >= _REF_MATCH_MIN:
		for kind, items, field in (("rel", rel, "summary"), ("hl", hl, "text")):
			for i, item in enumerate(items):
				other = _norm_text(item.get(field))
				if len(other) >= _REF_MATCH_MIN and (summary in other or other in summary):
					return content_ref(kind, i)
	return None


def _notable_sort_key(entry: dict) -> tuple:
	"""`affects_me` first, then worst severity, then the CVE id's
	`(year, sequence)` so the order matches `cve_ids[]`, with an id-less entry
	last inside its group.

	**`affects_me` outranks severity, and the cap is why.** The key orders and
	then evicts, so whatever it ranks last is what a four-entry list loses. An
	item flagged `affects_me` has a concrete touchpoint on *this* setup; a
	higher-rated one without a touchpoint is, on this card, the less useful of
	the two — and R5 already guarantees every entry qualified on its own
	before it got here, so promoting one never smuggles in a weak item. With
	severity first, three `low` CVEs nobody can reach here evict a reproduced
	command injection that lands on a wrapper this machine runs, which is the
	exact inversion R5 clause 3 was written to prevent.

	The severity tier uses `_NOTABLE_SEVERITY_RANK`, where `unknown` sits above
	`low` — see the note there for why the two rank maps differ."""
	return (
		0 if entry["affects_me"] else 1,
		-severity_rank(_NOTABLE_SEVERITY_RANK, entry["severity"], 0),
		cve_sort_key(entry["cve_id"]) if entry["cve_id"] else (10 ** 9, 0),
		entry["advisory_id"] or "",
	)


def finalize_notable(tool: dict, entries: list, cve_ids: list, severity_by_id: dict, tool_id: str) -> list:
	"""Validate, order (`affects_me` first, then worst severity —
	`_notable_sort_key`) and cap research's `notable[]`. Ordering strictly
	precedes the cap, so an over-long array loses its weakest entries rather
	than its last ones. The page caps again rather than trusting this, exactly
	as it already does for `highlights[]`."""
	id_set = set(cve_ids)
	# R1: `affects_me` is research's call, from the *direction* of its finding —
	# a third of one live run's security relevancy items exist precisely to say
	# a fix does NOT reach this machine. Assembly warns when the claim has no
	# supporting relevancy item; it never sets or clears the flag itself.
	has_security_relevancy = any(r.get("category") == "security" for r in tool.get("relevancy") or [])
	for entry in entries:
		if entry["cve_id"] and entry["cve_id"] not in id_set:
			# Normally unreachable: notable[].cve_id is inside the id scan, so a
			# well-formed id is in cve_ids by construction. A malformed one is
			# nulled rather than emitted, so the page never chips an id the
			# report cannot resolve.
			print(f"warning: {tool_id}: security.notable names {entry['cve_id']!r}, which is not a "
				f"resolvable CVE id in this range — dropping the id, keeping the item", file=sys.stderr)
			entry["cve_id"] = None
		sev = entry["severity"]
		if sev not in _CVE_SEVERITIES:
			if sev is not None:
				print(f"warning: {tool_id}: security.notable severity {sev!r} is not one of "
					f"{_CVE_SEVERITIES} — reading it as unknown", file=sys.stderr)
			sev = "unknown"
		if entry["cve_id"]:
			mapped = severity_by_id.get(entry["cve_id"])
			if mapped and mapped != sev:
				# One source of truth: severity_counts and the card must agree.
				print(f"warning: {tool_id}: security.notable rates {entry['cve_id']} {sev!r} while "
					f"cve_severities rates it {mapped!r} — the map wins", file=sys.stderr)
				sev = mapped
			elif mapped is None and sev != "unknown":
				# Rated here and nowhere else. The rollup is built from
				# cve_severities, so the card would read `critical` while
				# severity_counts buckets the same id as `unknown` — the
				# disagreement the row above exists to prevent, arrived at by
				# omission instead of by contradiction. Warn and keep the grade:
				# a notable[] rating carries no `basis`, so promoting it into
				# the map would manufacture the unsourced rating R2 forbids,
				# and dropping it would discard the only grade research found.
				print(f"warning: {tool_id}: security.notable rates {entry['cve_id']} {sev!r} but "
					f"cve_severities has no entry for it — severity_counts will bucket it as "
					f"unknown; the grade belongs in cve_severities, with a basis", file=sys.stderr)
		entry["severity"] = sev
		if entry["affects_me"] and not has_security_relevancy:
			print(f"warning: {tool_id}: security.notable claims affects_me with no security-category "
				f"relevancy item backing it — the two are the same claim: {entry['summary']!r}", file=sys.stderr)
		entry["source_ref"] = resolve_notable_ref(tool, entry)
	entries.sort(key=_notable_sort_key)   # stable, so equal keys keep research's order
	if len(entries) > _NOTABLE_CAP:
		print(f"warning: {tool_id}: security.notable has {len(entries)} entries — keeping the worst "
			f"{_NOTABLE_CAP}, dropping {len(entries) - _NOTABLE_CAP}", file=sys.stderr)
		entries = entries[:_NOTABLE_CAP]
	return entries


def compute_security(tool: dict) -> dict:
	"""The whole `security` object (references/schemas.md §1.9).

	Self-consuming, like the rest of finalize_tool()'s fields: on entry
	`tool["security"]` is whatever the *research* file supplied
	(`cve_severities`, `notable`), and the returned object replaces it. Called
	exactly once per tool, from finalize_tool() — a second call would read its
	own output.

	Eight keys are always present; `notable` is the ninth and is emitted only
	when assembly has an answer to give — see the comment on the return."""
	tool_id = tool.get("id", "<unknown>")
	if tool["source"] in NON_VERSION_SOURCES:
		# Forced false: the security section is about *patches* the user can
		# take. An untrusted tap is a trust decision, not a shipped fix, and
		# counting it in tools_with_security would make the section's count
		# disagree with the cards it lists. Its security character still shows
		# via the health→category map (untrusted_tap → a security headliner).
		# A notable[] here is dropped silently for the same reason — the same
		# doctrine as "No research ⇒ never security_only", applied twice.
		# A skill-drift finding is forced the same way and for the same reason:
		# its detail text names an upstream range, and scanning that prose for
		# CVE ids would attribute an advisory the user cannot act on here to a
		# card that ships no patch.
		return {
			"cve_ids": [],
			"cve_count": 0,
			"cve_claimed_count": None,
			"has_security": False,
			"security_only": False,
			"impact": compute_impact(tool),
			"severity_counts": dict.fromkeys(_CVE_SEVERITIES, 0),
			"cve_severities": [],
			"notable": [],
		}
	research_sec = research_security(tool)
	# Read before the self-consuming assignment below overwrites it: whether
	# research supplied a readable `security` block at all is itself an answer,
	# and the emit decision at the bottom of this function turns on it.
	research_answered = bool(research_sec)
	notable = clean_notable(
		as_item_list(research_sec.get("notable"), tool_id, "security.notable"), tool_id)
	produced = research_produced_content(tool)
	if notable and not produced:
		# "We know nothing" is never "here is what matters most" — same rule
		# security_only lives under.
		print(f"warning: {tool_id}: research produced no headliners but supplied "
			f"{len(notable)} security.notable entries — dropping them", file=sys.stderr)
		notable = []
	# Put the cleaned list back before the scan, so the ids counted are exactly
	# the ids that will be emitted.
	tool["security"] = {"notable": notable}
	cve_ids = extract_cve_ids(tool)
	cve_severities, severity_by_id = resolve_cve_severities(
		cve_ids, as_item_list(research_sec.get("cve_severities"), tool_id, "security.cve_severities"), tool_id)
	notable = finalize_notable(tool, notable, cve_ids, severity_by_id, tool_id)
	# vendor_silent_categories == ["security"] counts: it is research's explicit
	# statement "this release has security content the vendor refused to
	# detail", which lands the tool in security_mixed and gets it looked at. A
	# notable[] entry counts for the same reason — it *is* security content, and
	# a card carrying one whose security strip never rendered would be a lie.
	has_security = bool(
		any(i.get("category") == "security" for i in content_items(tool))
		or "security" in (tool.get("vendor_silent_categories") or [])
		or cve_ids
		or notable)
	out = {
		"cve_ids": cve_ids,
		# ALWAYS len(cve_ids) — an id-backed count, never a claim, so the page
		# can link every counted CVE to something.
		"cve_count": len(cve_ids),
		"cve_claimed_count": extract_cve_claim(tool),
		"has_security": has_security,
		"security_only": compute_security_only(tool, has_security),
		"impact": compute_impact(tool),
		# Sums to cve_count by construction (rollup_severity_counts). `unknown`
		# dominating is the expected state, not a degraded one: research grades
		# only what the page it already read states, plus the ≤3 notable items.
		"severity_counts": rollup_severity_counts(cve_ids, severity_by_id),
		"cve_severities": cve_severities,
	}
	# `notable: []` and an absent `notable` are two different answers, and the
	# page renders two different cards from them (references/schemas.md §1.9):
	# `[]` says the selection ran and nothing qualified — the single-column card
	# — while an absent key says the question was never put to this tool, and
	# the page falls back to deriving the column from the tool's security
	# content the way it did before the field existed. So emit the key only when
	# assembly actually has an answer: research supplied a readable `security`
	# block, or research told us nothing at all (research_error / no
	# headliners), where `[]` is forced by the same doctrine as "No research ⇒
	# never security_only". A research file with real content and no `security`
	# block predates the field, and asserting "nothing here is notable" on its
	# behalf deletes the security column from every card it touches — 77 of the
	# 78 in the recorded run, whose 22 research files carry no security block at
	# all. A block too drifted to read (a bare string, a list) is not an answer
	# either: it omits the key and falls back, rather than reporting a silence
	# that research never uttered.
	if research_answered or not produced:
		out["notable"] = notable
	return out


# ── the noise floor's decision boundary (references/research.md §The Noise Floor) ──
# The noise floor is a rule about what a research subagent writes, so nothing
# in main() calls this. It exists because the rule has a hard edge that prose
# alone gets wrong, and test_assemble.py §6 asserts the property that edge is
# drawn to protect: **deleting every suppressible item on a tool changes no
# decision.** A presentation rule must never be able to approve, or un-approve,
# an update.
#
# The edge is not arbitrary. `security_only` is an all() over
# headliners + relevancy, `impact` and `risk_level` are any()s over the same
# items, and `has_security` reads the security category and the CVE ids. An
# item is deletable only when it sits outside every one of those reads:
#
#   * not `security`-category (R3 — removing the last security item takes
#     has_security with it, moving the tool to routine and pre-accepting it);
#   * an (category, severity) pair `_allowed_for_security_only()` already
#     passes, so removing it cannot flip that all() — which excludes `features`
#     at any severity and `notes` above `info`;
#   * contributing nothing to impact (no non-security relevancy at notable+,
#     no non-security headliner at warning+) or to risk_level (no relevancy at
#     warning+);
#   * carrying no CVE id, no "N CVEs" claim and no watch-item hit — cutting any
#     of those changes cve_count, cve_claimed_count or a 70-point highlight
#     signal;
#   * and never the tool's last headliner, which would read as "research told
#     us nothing".
#
# Everything else the noise floor objects to is **trimmed or merged, never
# deleted** — a merge keeps the surviving item's category and severity, so it
# cannot move any of the reads above.
_SUPPRESSIBLE_PAIRS = {
	("headliners", "fixes", "info"), ("headliners", "fixes", "notable"),
	("headliners", "notes", "info"),
	("relevancy", "fixes", "info"), ("relevancy", "notes", "info"),
}


def noise_suppressible(tool: dict, item: dict, field: str) -> bool:
	"""Is deleting this `headliners[]`/`relevancy[]` item provably decision-neutral?"""
	if not isinstance(item, dict):
		return False
	if (field, item.get("category"), item.get("severity")) not in _SUPPRESSIBLE_PAIRS:
		return False
	if field == "headliners" and len(tool.get("headliners") or []) <= 1:
		return False
	if field == "headliners":
		texts = [item.get("text")]
	else:
		texts = [item.get(k) for k in ("summary", "detail", "motivating_change")]
	for text in texts:
		if not isinstance(text, str):
			continue
		if _CVE_RE.search(text) or _CVE_CLAIM_RE.search(text) or _WATCH_HIT_RE.search(text):
			return False
	return True


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
	if tool["source"] in NON_VERSION_SOURCES:
		# An expected/no-action finding is informational: brew-health's
		# path_note, or a skill-drift local_only (the user's own patch) or
		# probe_error. Everything else is structural in exactly the sense this
		# axis measures — an unlinked keg or an untrusted tap on one side, a
		# subtree pull that rewrites vendored files and can conflict on the
		# other — so it never reads as quietly fine.
		return "low" if finding_expected(tool) else "elevated"
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
	`{tool_id}:remediate` and a skill-drift finding's `{tool_id}:sync` never
	match — which is the single mechanism that makes both non-version sources
	impossible to pre-accept (apply_pre_accept only ever writes True onto the
	object this returns).

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
	*review-effort* axis, orthogonal to source: the page groups brew-health and
	skill-drift cards by `source` and counts them with summary.health_count /
	summary.skill_drift_count, never by bucket — "routine" on the one expected
	PATH note, or on a locally-patched skill, means "nothing to decide here",
	not "hide it"."""
	sec = tool["security"]
	if tool["source"] in NON_VERSION_SOURCES:
		return "routine" if finding_expected(tool) else "attention"
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
	watch-item, never a brew-health remediation, never a skill-drift sync.
	`auto_runnable: false` is a
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


def normalize_research_security(research_obj: dict) -> dict:
	"""The research-supplied `security` block, coerced to a dict at the one
	boundary where research's free-form output becomes a Tool object. Both
	arrays are normalized later, inside compute_security(), because their
	validation needs `cve_ids` — which does not exist yet here."""
	block = research_obj.get("security")
	if isinstance(block, dict):
		return dict(block)
	if block is not None:
		# Not merely dropped: an unreadable block leaves `notable` unemitted
		# (compute_security()), so the card derives its security column instead
		# of collapsing to one. Say so here, where the original shape is still
		# in hand.
		print(f"warning: {research_obj.get('id', '<unknown>')}: security was "
			f"{type(block).__name__}, not an object — ignoring it; notable[] will not "
			f"be emitted for this tool", file=sys.stderr)
	return {}


def as_evidence_list(evidence, tool_id: str, context: str) -> list:
	"""references/schemas.md §Report Object says evidence is always an array of
	strings. A research subagent that instead returns a bare string would make
	`for ev in evidence` iterate individual characters — coerce and warn rather
	than silently corrupting the warning output with single-letter "evidence"
	entries.

	The *members* are checked for the same reason as_item_list() checks its
	own: a structured citation (`[{"path": "Brewfile", "line": 3}]`) is a shape
	a subagent writes, and it used to reach evidence_exists()'s
	`_COMMIT_LIKE.search(evidence)` as a dict and raise TypeError out of
	validate_evidence() — aborting the report for all 78 tools over one
	citation that nothing but a stderr warning depends on."""
	if not evidence:
		return []
	if not isinstance(evidence, list):
		print(f"warning: {tool_id}: {context} evidence was a bare string, not an array — wrapping it: {evidence!r}", file=sys.stderr)
		evidence = [evidence]
	kept = []
	for ev in evidence:
		if isinstance(ev, str):
			kept.append(ev)
		else:
			print(f"warning: {tool_id}: {context} evidence entry was {type(ev).__name__}, not a string — "
				f"dropping it: {ev!r}", file=sys.stderr)
	return kept


# Member fields references/schemas.md declares as strings, across the arrays
# as_item_list() normalizes. Only these — a key a given array does not carry is
# simply absent, so one list serves headliners, relevancy, context and the rest
# without warning about a field that was never expected there.
_STRING_MEMBER_KEYS = ("category", "severity", "text", "summary", "detail", "motivating_change")


def _warn_odd_strings(item: dict, tool_id: str, field: str) -> None:
	"""Say so when a member field the schema declares as a string is not one.

	The value is left exactly as research wrote it — the page reads these
	defensively and a JS lookup on an odd value is `undefined`, not a throw.
	Python's is not: severity_rank() has to guard the rank lookups
	(`{}.get(["warning"])` raises) and _why_string() has to refuse the shapes
	_truncate_why() would call `.split()` on. Both of those degrade quietly by
	design, and a guard nobody can see is the silent failure this whole pass
	exists to avoid — so the shape is reported once here, at the one boundary
	that still holds the tool and the field it came from, rather than at each
	of the reads."""
	for key in _STRING_MEMBER_KEYS:
		value = item.get(key)
		if value is not None and not isinstance(value, str):
			print(f"warning: {tool_id}: {field} entry has {key} {value!r} ({type(value).__name__}, not a "
				f"string) — nothing downstream can read it", file=sys.stderr)


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
			if member_type is dict:
				_warn_odd_strings(item, tool_id, field)
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
def read_findings_block(collect: dict, key: str, source: str) -> tuple:
	"""→ (findings, suppressed) for one `{findings, suppressed}` block of
	collect.json (`brew_health`, `skill_drift`).

	Read defensively, for the same reason as_item_list() exists one boundary
	over: each block is produced by a detector that can fail, time out, or be an
	older version of itself, and the whole block can be absent (a session
	collected before that detector existed), null, or the wrong shape. A finding
	source is an *addition* to the report — never let a malformed one cost the
	version updates the run was actually for. Anything unusable is warned about
	and dropped, never silently swallowed.

	The block key is authoritative about `source`: a finding that omits it (or
	disagrees) is stamped, because build_tool() dispatches on that field and a
	missing one would otherwise be a KeyError several tools into the loop."""
	block = collect.get(key)
	if block is None:
		return ([], [])
	if not isinstance(block, dict):
		print(f"warning: collect.json {key!r} was {type(block).__name__}, not an object — ignoring it", file=sys.stderr)
		return ([], [])
	raw = block.get("findings") or []
	if not isinstance(raw, list):
		print(f"warning: collect.json {key}.findings was {type(raw).__name__}, not an array — ignoring it", file=sys.stderr)
		raw = []
	findings = []
	for item in raw:
		if not isinstance(item, dict) or not item.get("id"):
			print(f"warning: collect.json {key}.findings: dropping a malformed entry: {item!r}", file=sys.stderr)
			continue
		if item.get("source") != source:
			print(f"warning: {item['id']}: {key}.findings entry had source {item.get('source')!r} — reading it as {source!r}", file=sys.stderr)
			item = dict(item, source=source)
		findings.append(item)
	raw_sup = block.get("suppressed") or []
	if not isinstance(raw_sup, list):
		print(f"warning: collect.json {key}.suppressed was {type(raw_sup).__name__}, not an array — ignoring it", file=sys.stderr)
		raw_sup = []
	return (findings, [s for s in raw_sup if isinstance(s, str)])


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
		except Exception as exc:
			# Deliberately wider than (OSError, json.JSONDecodeError): a
			# subagent killed mid-write leaves a truncated multi-byte character
			# and `json.load` raises UnicodeDecodeError, and a pathologically
			# nested array raises RecursionError — neither is an OSError or a
			# ValueError, so both used to escape this handler and abort the
			# report for all 78 tools over one unreadable file. Reading a file
			# is exactly the boundary where "any failure costs this file and
			# nothing else" is the right rule; the type is named so the warning
			# still says what went wrong.
			print(f"warning: could not read {fpath!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
			continue
		if not isinstance(entries, list):
			print(f"warning: {fpath!r} is not a JSON array — skipping", file=sys.stderr)
			continue
		for entry in entries:
			# A research file is written by a subagent, so any member can be any
			# shape. Degrade per entry, never per run: one bare string here used to
			# raise AttributeError out of load_research and abort the whole report,
			# while every malformed shape *inside* an entry is survivable downstream.
			if not isinstance(entry, dict):
				print(f"warning: an entry in {fpath!r} is {type(entry).__name__}, not an object — skipping", file=sys.stderr)
				continue
			tid = entry.get("id")
			if not tid or not isinstance(tid, str):
				print(f"warning: an entry in {fpath!r} has no usable \"id\" — skipping", file=sys.stderr)
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


# ── skill-drift state → headliner category (references/assembly.md §Skill-Drift Assembly) ──
# Same idea as _HEALTH_CATEGORY_GROUP above: a drift is not a changelog fact,
# but the four content groups are still where its problem statement reads best.
# A drift the user is being asked to resolve is a "fixes" item (something is
# behind and there is a sync to run); a drift that needs no decision —
# `local_only` (the local copy is deliberately patched) or a per-vendor
# `probe_error` (we could not check) — is a "notes" item. Nothing here maps to
# `security`: a stale vendored *prompt* ships no advisory, and claiming
# otherwise would put an unactionable card in the security section.
_DRIFT_STATE_GROUP = {
	"upstream_ahead": "fixes",
	"diverged": "fixes",
	"local_only": "notes",
	"probe_error": "notes",
	"in_sync": "notes",
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
		# Research's own security block; compute_security() reads it here and
		# replaces it with the computed object (§Security Extraction).
		"security": normalize_research_security(research_obj),
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


def build_drift_tool(candidate: dict, research_obj: dict | None) -> dict:
	"""Build a Tool object for a `source: "skill-drift"` finding
	(references/assembly.md §Skill-Drift Assembly) — a vendored agent skill
	under `dotfiles/config/agent-skills/` that no longer matches the upstream it
	was synced from. Structurally a sibling of build_health_tool(): a finding,
	not a version update, so no current→latest pair and no synthesized
	`brew upgrade` baseline — its action is the finding's own vendor-scoped
	sync command. Absent research this degrades to the detector's own detail so
	the card still says what drifted and what to run."""
	research_obj = research_obj or {}
	tool_id = candidate["id"]
	# `or`, not a .get() default, throughout this builder: a detector that
	# emits an explicit null leaves the key *present*, so a default would never
	# substitute and the card would render "null" where its state or problem
	# statement belongs. probe_error is the honest fallback state — "we could
	# not establish this one" — and it is `expected`, so it stays quiet.
	state = candidate.get("drift_state") or "probe_error"

	# Headliners: research's if present, else one synthesized from the
	# finding's own detail so the drift still shows in a content group.
	headliners = as_item_list(research_obj.get("headliners"), tool_id, "headliners")
	if not headliners:
		headliners = [{
			"text": candidate.get("detail") or candidate.get("name") or tool_id,
			"category": _DRIFT_STATE_GROUP.get(state, "notes"),
			"severity": candidate.get("severity") or "notable",
		}]

	# Suggestions: research's if present, else synthesize from the finding's
	# remediation (null for the states that need no decision — local_only and
	# probe_error — so those cards carry no action at all).
	suggestions = as_item_list(research_obj.get("suggestions"), tool_id, "suggestions")
	if not suggestions:
		rem = candidate.get("remediation")
		# isinstance rather than truthiness: this block is written by a detector
		# that can emit a half-built object, and a bare string here would raise
		# on .get() and cost the whole report.
		if isinstance(rem, dict) and rem.get("command"):
			sug = {
				# `:sync`, NEVER `:upgrade` — baseline_upgrade() identifies the
				# pre-acceptable baseline by that suffix, so this one choice is
				# what makes a vendored-skill sync impossible to auto-approve. A
				# subtree pull rewrites files in the dotfiles submodule, needs a
				# clean tree and can conflict; it is never a "just do it".
				"id": f"{tool_id}:sync",
				"kind": "upgrade",  # a single command to run, like an upgrade
				"title": rem.get("label") or candidate.get("name") or "Sync from upstream",
				"target_files": [],
				"command": rem["command"],
				# Defaults are the conservative half of each pair: a detector
				# that omits the key gets manual, unprivileged.
				"auto_runnable": rem.get("auto_runnable", False),
				"needs_sudo": rem.get("needs_sudo", False),
				"rationale": candidate.get("detail") or "",
				"motivating_link": None,
				"diff_preview": None,
			}
			if not sug["auto_runnable"]:
				sug["manual_reason"] = (
					"Vendored-skill sync is always manual — `sync-upstream.sh` pulls a git "
					"subtree into the dotfiles submodule, needs a clean tree, and can conflict.")
			suggestions = [sug]

	tool = {
		"id": tool_id,
		"name": candidate.get("name", tool_id),
		"source": "skill-drift",
		"drift_state": state,
		# "no decision required", not "no drift" — local_only and probe_error.
		"drift_expected": bool(candidate.get("expected", False)),
		# Vendor is the granularity the sync command actually operates at, so
		# the page can say which other cards one run would resolve.
		"drift_vendor": candidate.get("vendor"),
		"drift_skill": candidate.get("skill"),
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
	# Same needs_attention-must-have-a-suggestion guard both other build paths
	# apply — a research subagent could flag a drift finding whose remediation
	# is null (local_only), shipping an unactionable banner.
	if config_needs_attention(tool) and not tool["suggestions"]:
		print(f"warning: {tool_id}: config_status is needs_attention with no suggestion addressing it", file=sys.stderr)
	# One shared entry point for every derived field, so a drift finding and a
	# version-outdated tool can never disagree about what a field means.
	finalize_tool(tool)
	return tool


def build_tool(candidate: dict, research_obj: dict | None) -> dict:
	source = candidate["source"]
	if source == "brew-health":
		return build_health_tool(candidate, research_obj)
	if source == "skill-drift":
		return build_drift_tool(candidate, research_obj)

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
		# Research's own security block; compute_security() reads it here and
		# replaces it with the computed object (§Security Extraction).
		"security": normalize_research_security(research_obj),
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
	# `target_version` is the reviewed version apply must land on (WP5/I2:
	# references/apply.md §Executing Upgrade Suggestions) — pass it into
	# upgrade_command_and_runnable so `command` pins it wherever the source
	# supports that (mise); `version_pinned` records whether it does.
	command, auto_runnable, manual_reason, version_pinned = upgrade_command_and_runnable(
		source, name, tool["latest_version"])
	# A tool can reach here with no usable latest_version at all — collection
	# degrading per-tool rather than aborting (§G1) means a source can supply
	# a candidate whose own `latest` came back null (e.g. mise's own
	# `outdated --json` failing to resolve one), the same "missing version"
	# shape compute_version_delta already recognizes. Never synthesize a
	# runnable, "pinned" suggestion with nothing to pin to or verify against:
	# `check_pin.py verify --target-version` would then compare against
	# nothing and could never pass, permanently reporting a failure for an
	# upgrade that actually landed correctly — worse than no check at all,
	# because it trains the reader to ignore it.
	if not tool["latest_version"]:
		command, auto_runnable, version_pinned = None, False, False
		manual_reason = ("No latest_version available for this tool — collection could not "
			"determine one, so there is nothing to pin or verify an upgrade against. "
			"Investigate the collection gap; do not run this by hand until it reports one.")
	upgrade_suggestion = {
		"id": f"{tool_id}:upgrade",
		"kind": "upgrade",
		"title": f"Upgrade {name} {tool['current_version']} → {tool['latest_version']}",
		"target_files": [],
		"command": command,
		"target_version": tool["latest_version"],
		"version_pinned": version_pinned,
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


# ── the per-candidate boundary (references/assembly.md §Summary Counts and Output) ──
# One report is assembled from ~22 research files over ~78 candidates, after
# the expensive part of the session is already spent. Anything that can only
# be wrong about *one* candidate must therefore cost that one card and leave
# the other 77 rendering — the same rule load_research() applies one boundary
# earlier, per research entry.
def read_candidate_list(collect: dict, key: str) -> list:
	"""One version-candidate section of collect.json, read the way
	read_findings_block() already reads the two finding blocks.

	`collect.get(key, [])` substitutes its default only when the key is
	*absent*: a section written as `null` by an older collector came back as
	None and `list + None` raised TypeError before a single tool was built.
	A non-dict member is dropped rather than carried to build_tool(), whose
	first act is `candidate["source"]`.

	An absent key stays silent — a session collected before a source existed
	has nothing to say about it — while a key that is *present* and unusable is
	warned about, because something did try to write that section."""
	if key not in collect:
		return []
	value = collect.get(key)
	if not isinstance(value, list):
		print(f"warning: collect.json {key!r} was {type(value).__name__}, not an array — ignoring it", file=sys.stderr)
		return []
	kept = []
	for item in value:
		if isinstance(item, dict):
			kept.append(item)
		else:
			print(f"warning: collect.json {key}: dropping a malformed candidate: {item!r}", file=sys.stderr)
	return kept


def build_tool_guarded(candidate: dict, research_by_id: dict):
	"""One Tool object, or None with a warning naming what was dropped.

	Two layers, both loud. The identity keys are checked first, because
	build_tool() reads `candidate["source"]`/`["id"]`/`["name"]` directly and a
	KeyError there names only the key, never the candidate it came from. Then
	the build itself runs inside a boundary: every *known* malformed shape is
	already handled by the normalizers this file is built out of, and this
	catches the next one — the report loses one card and says which, instead of
	not existing."""
	tool_id = candidate.get("id")
	if not isinstance(tool_id, str) or not tool_id:
		print(f"warning: collect.json candidate has no usable \"id\" — skipping it: {candidate!r}", file=sys.stderr)
		return None
	source = candidate.get("source")
	if not isinstance(source, str) or not source:
		print(f"warning: {tool_id}: candidate has no usable \"source\" — skipping it", file=sys.stderr)
		return None
	if source not in NON_VERSION_SOURCES and not isinstance(candidate.get("name"), str):
		# The two finding builders default their own name from the id; only the
		# version path indexes `candidate["name"]` unconditionally.
		print(f"warning: {tool_id}: candidate has no usable \"name\" — skipping it", file=sys.stderr)
		return None
	try:
		return build_tool(candidate, research_by_id.get(tool_id))
	except Exception as exc:
		print(f"warning: {tool_id}: could not be assembled ({type(exc).__name__}: {exc}) — dropping this "
			f"tool; the rest of the report is unaffected", file=sys.stderr)
		return None


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
	ranks = [severity_rank(_SEVERITY_RANK, i.get("severity"), -1) for i in content_items(tool)]
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


def _why_string(value, tool_id: str, field: str):
	"""Research's free text, but only when it *is* text — else None, so
	_highlight_why_parts() falls through to its next branch.

	`_truncate_why()` calls `.split()`, so a summary a subagent nested one
	level too deep (`"summary": {"text": "…"}`) used to raise AttributeError
	out of build_highlights() and abort the report for all 78 tools. Coercing
	it with `str()` is not the fix either — that renders the repr on the card,
	which is exactly what clean_notable() refuses to do one field over. Warn,
	skip the field, and let the next-best line take the slot."""
	if value is None or isinstance(value, str):
		return value or None
	print(f"warning: {tool_id}: {field} was {type(value).__name__}, not a string — not using it as a "
		f"highlight line rather than rendering its repr: {value!r}", file=sys.stderr)
	return None


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
	if tool["source"] in NON_VERSION_SOURCES:
		# A finding has no versions — the version form would render the tool's
		# name followed by "None → None".
		return tool.get("name") or tool["id"]
	return f"{tool.get('name')} {tool.get('current_version')} → {tool.get('latest_version')}"


# Which branch of _highlight_why_parts() produced a highlight's one line. The
# page renders `why`; `why_source` and `why_ref` exist so the de-duplication
# below — and a reviewer reading report.json — can tell *where* the line came
# from without re-deriving it from the prose.
_WHY_SOURCES = ("relevancy_security", "relevancy_other", "config_status", "research_error",
	"headliner_security", "headliner_other", "major_bump", "none")


def _headliner_source(item: dict) -> str:
	return "headliner_security" if item.get("category") == "security" else "headliner_other"


def _highlight_why_parts(tool: dict) -> tuple:
	"""→ (why, why_source, why_ref). First match in a fixed order, so the
	card's one line is the most specific thing we know about this tool.
	`why_ref` is the content_ref() of the item the line came from, or None for
	the branches that synthesize their own text."""
	tool_id = tool.get("id", "<unknown>")
	rel = tool.get("relevancy") or []
	if rel:
		# Ties resolve to array order — max() keeps the first maximum.
		best_i = max(range(len(rel)), key=lambda i: severity_rank(_SEVERITY_RANK, rel[i].get("severity"), -1))
		best = rel[best_i]
		summary = _why_string(best.get("summary"), tool_id, f"relevancy[{best_i}].summary")
		if summary:
			source = "relevancy_security" if best.get("category") == "security" else "relevancy_other"
			return (_truncate_why(summary), source, content_ref("rel", best_i))
	cs = tool.get("config_status") or {}
	if config_needs_attention(tool):
		detail = _why_string(cs.get("detail"), tool_id, "config_status.detail")
		if detail:
			return (_truncate_why(detail), "config_status", None)
	if tool.get("research_error"):
		return ("Research produced no changelog for this update.", "research_error", None)
	if tool["review_bucket"] in ("security_auto", "security_mixed"):
		# Non-security first. This step used to return the first *security*
		# headliner, which is the same line the card's security column renders
		# in full — a highlight slot spent restating what the reader has just
		# read (brew:gh and cask:windows-app, on the recorded run). The security
		# headliner is still the fallback when the tool has nothing else.
		for kind in ("headliner_other", "headliner_security"):
			for i, item in enumerate(tool.get("headliners") or []):
				if _headliner_source(item) != kind:
					continue
				text = _why_string(item.get("text"), tool_id, f"headliners[{i}].text")
				if text:
					return (_truncate_why(text), kind, content_ref("hl", i))
	if tool["version_delta"] == "major":
		return (_truncate_why(f"Major version bump {tool.get('current_version')} → {tool.get('latest_version')}."),
			"major_bump", None)
	for i, item in enumerate(tool.get("headliners") or []):
		text = _why_string(item.get("text"), tool_id, f"headliners[{i}].text")
		if text:
			return (_truncate_why(text), _headliner_source(item), content_ref("hl", i))
	return ("", "none", None)


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
	why, why_source, why_ref = _highlight_why_parts(tool)
	return {
		"tool_id": tool["id"],
		"title": _highlight_title(tool),
		"why": why,
		"why_source": why_source,
		"why_ref": why_ref,
		"severity": _highlight_severity(tool),
		# Every suggestion on the tool, in array order (baseline first when
		# present) — the page looks each id up in tools[] to decide whether to
		# offer jump vs. approve. Read after main()'s id-uniqueness pass.
		"suggestion_ids": [s["id"] for s in tool.get("suggestions") or [] if s.get("id")],
		"reasons": reasons,
		"score": score,
	}


def build_highlights(tools: list) -> list:
	"""Ranked, capped, and de-duplicated against the security cards.

	When a highlight's `why` restates an item already shown in full on that
	tool's `security.notable[]`, **the highlight yields and the slot is
	backfilled** from the next-ranked candidate — the security card keeps the
	sentence (it is often the single most important line on it) and the section
	still carries `_HIGHLIGHT_CAP` distinct decision drivers. The match is on
	the emitted `content_ref()` identity, never on `why`, which _truncate_why()
	has already cut at 220 chars."""
	# Both loops are contained per tool, for the same reason main()'s candidate
	# loop is: highlights are a *derived* section over content research wrote,
	# so one tool whose shape this cannot read must cost that tool its slot and
	# nothing else. Losing the whole report — and with it the 77 cards that are
	# fine — over a ranking is the trade this pass exists to refuse.
	scored = []
	for tool in tools:
		try:
			score, reasons = score_tool(tool)
		except Exception as exc:
			print(f"warning: {tool.get('id')!r}: could not be scored for highlights "
				f"({type(exc).__name__}: {exc}) — it cannot appear in that section", file=sys.stderr)
			continue
		if score >= _HIGHLIGHT_THRESHOLD:
			scored.append((score, reasons, tool))
	# No per-source quota — a brew-health finding competes on the same scale
	# (a missing dependency is a real "input needed"). The trailing tool id
	# makes ties fully deterministic.
	scored.sort(key=lambda x: (-x[0], -_max_sev_rank(x[2]), -x[2]["security"]["cve_count"], x[2]["id"]))
	highlights = []
	for score, reasons, tool in scored:
		if len(highlights) >= _HIGHLIGHT_CAP:
			break
		try:
			obj = _highlight_object(score, reasons, tool)
		except Exception as exc:
			print(f"warning: {tool.get('id')!r}: highlight could not be built "
				f"({type(exc).__name__}: {exc}) — backfilling from the ranked list", file=sys.stderr)
			continue
		refs = {n.get("source_ref") for n in tool["security"].get("notable") or [] if n.get("source_ref")}
		if obj["why_ref"] and obj["why_ref"] in refs:
			print(f"note: {tool['id']}: highlight dropped — its \"why\" restates security.notable "
				f"item {obj['why_ref']}; backfilling from the ranked list", file=sys.stderr)
			continue
		highlights.append(obj)
	return highlights


# ── report-level summary (references/assembly.md §Summary Counts and Output) ──
def summarize_by_delta(tools: list) -> dict:
	"""brew-health findings are environment issues and skill-drift findings are
	vendoring issues, not updates — excluding both preserves
	sum(by_delta) == total_outdated (the invariant the main tab's boxes are
	built on) and stops non-updates inflating the unknown box. A membership
	test, not a chain of `==`: the next finding source has to be excluded here
	by construction, since forgetting it shows up only as a quietly wrong
	unknown count."""
	counts = {"major": 0, "minor": 0, "patch": 0, "revision": 0, "unknown": 0}
	for tool in tools:
		if tool["source"] in NON_VERSION_SOURCES:
			continue
		counts[tool["version_delta"]] += 1
	return counts


def summarize_by_bucket(tools: list) -> dict:
	"""Counts **every** tool, findings included — the mirror image of
	summarize_by_delta() above, and deliberately so: `review_bucket` is a
	review-effort axis defined for every Tool object (§Review Buckets), and the
	page's bucket lists render health and skill-drift cards alongside version
	ones. Every Tool object is bucketed exactly once, so this sums to
	len(tools); spelled arithmetically that is
	`total_outdated + health_count + skill_drift_count`, one term per source
	excluded from total_outdated. by_delta has the smaller denominator, so a
	page must never mix the two in one percentage."""
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
	# Rebuilt from each tool's emitted cve_severities rather than summed from
	# the per-tool severity_counts, for the same reason cve_count is a union:
	# one advisory can land on two tools, and summing would count it twice.
	# Two tools rating one id differently is real once ratings come from
	# different pages — take the worse, since understating a severity in the
	# report header is the failure mode with a cost.
	severity_by_id: dict = {}
	for tool in tools:
		ids.update(tool["security"]["cve_ids"])
		for entry in tool["security"].get("cve_severities") or []:
			cve_id, severity = entry.get("cve_id"), entry.get("severity")
			if severity not in _CVE_SEVERITY_RANK:
				continue
			prior = severity_by_id.get(cve_id)
			if prior is not None and prior != severity:
				# Its per-tool twin in resolve_cve_severities() warns on exactly
				# this; the report-wide one used to resolve it silently, so a
				# header reading "1 critical" could come from one tool's page
				# disagreeing with another's with nothing said about it.
				print(f"warning: {cve_id} is rated {prior!r} on one tool and {severity!r} on another — "
					f"keeping the worse for the report-wide rollup", file=sys.stderr)
			if _CVE_SEVERITY_RANK[severity] > _CVE_SEVERITY_RANK.get(prior or "unknown", 0):
				severity_by_id[cve_id] = severity
	return {
		"cve_count": len(ids),
		# Over the union of ids, so this sums to cve_count above — never to the
		# sum of the per-tool counts.
		"severity_counts": rollup_severity_counts(sorted(ids, key=cve_sort_key), severity_by_id),
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
	except Exception as exc:
		# Same width as load_research()'s handler, and for the same reasons
		# (UnicodeDecodeError and RecursionError are neither OSError nor
		# ValueError). This one still exits — collect.json IS the run — but it
		# exits saying what happened instead of printing a traceback.
		print(f"Error: could not read {collect_path!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
		sys.exit(1)
	if not isinstance(collect, dict):
		# Parsed, but not the object every read below assumes: `collect.get(…)`
		# raised AttributeError three lines on. Nothing can be salvaged from a
		# collect.json that is not an object — it *is* the candidate set — so
		# this exits like the handler above rather than degrading. It exits
		# saying which file and what shape, which a traceback does not.
		print(f"Error: {collect_path!r} is {type(collect).__name__}, not a JSON object — "
			f"there is no candidate set to assemble", file=sys.stderr)
		sys.exit(1)

	repo_context_path = os.path.join(session_dir, "repo_context.json")
	try:
		with open(repo_context_path, "r", encoding="utf-8") as fh:
			repo_context = json.load(fh)
	except Exception as exc:
		print(f"warning: no usable repo_context.json at {repo_context_path!r} ({type(exc).__name__}) — using placeholder", file=sys.stderr)
		placeholder = {"up_to_date": True, "ahead": 0, "behind": 0, "recent_commits": []}
		repo_context = {"macos_setup": placeholder, "dotfiles": dict(placeholder)}

	research_by_id = load_research(os.path.join(session_dir, "research"))

	# The two finding sources (references/assembly.md §Brew-Health Assembly,
	# §Skill-Drift Assembly) are candidates too, appended after the
	# version-outdated tools so they sort/render as their own cards.
	health_findings, health_suppressed = read_findings_block(collect, "brew_health", "brew-health")
	for s in health_suppressed:
		print(f"note: brew-health suppressed (expected, not reported): {s}", file=sys.stderr)
	drift_findings, drift_suppressed = read_findings_block(collect, "skill_drift", "skill-drift")
	for s in drift_suppressed:
		print(f"note: skill-drift suppressed (in sync, or no upstream to check): {s}", file=sys.stderr)

	candidates = (
		read_candidate_list(collect, "brew") + read_candidate_list(collect, "mise") +
		read_candidate_list(collect, "standalone") + read_candidate_list(collect, "macos") +
		health_findings + drift_findings
	)

	tools = []
	for candidate in candidates:
		tool = build_tool_guarded(candidate, research_by_id)
		if tool is not None:
			tools.append(tool)

	for tool in tools:
		# Evidence validation contributes nothing to report.json — it only
		# prints "evidence not found" warnings — so a shape it cannot read must
		# never be the reason the report does not exist. Contained per tool.
		try:
			validate_evidence(tool, roots)
		except Exception as exc:
			print(f"warning: {tool.get('id')!r}: evidence validation failed "
				f"({type(exc).__name__}: {exc}) — its citations went unchecked", file=sys.stderr)

	# Suggestion-id uniqueness — global, not just within one tool. A
	# collision almost always means a research subagent copied an id
	# pattern rather than deriving it from its own tool, so append a
	# disambiguating suffix rather than silently dropping either one.
	seen_ids: dict[str, str] = {}
	for tool in tools:
		for sug in tool["suggestions"]:
			sid = sug.get("id")
			if sid is not None and not isinstance(sid, str):
				# `sid in seen_ids` is a dict lookup, so an id written as a list
				# ("id": ["brew:foo:edit"]) raised TypeError: unhashable type
				# here and aborted the report for all 78 tools — two steps after
				# every tool had already been built. An id has to be a string to
				# be an id at all: report it, then take the no-id path below,
				# which mints one this pass can actually use.
				print(f"warning: {tool['id']}: suggestion id was {type(sid).__name__}, not a string — "
					f"discarding it: {sid!r}", file=sys.stderr)
				sid = None
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
	# "outdated" counts version-outdated tools only; a brew-health finding is an
	# environment issue and a skill-drift finding is a vendoring issue, so each
	# gets its own count and neither is in total_outdated. by_bucket still
	# counts all of them, which is why its denominator is the sum of all three
	# (see summarize_by_bucket). total_outdated is a membership test against
	# NON_VERSION_SOURCES rather than `len(tools) - health_count -
	# skill_drift_count`, so it stays correct — and stays the same expression
	# summarize_by_delta uses — when a fourth finding source is added.
	health_count = sum(1 for t in tools if t["source"] == "brew-health")
	skill_drift_count = sum(1 for t in tools if t["source"] == "skill-drift")
	total_outdated = sum(1 for t in tools if t["source"] not in NON_VERSION_SOURCES)

	report_id = os.path.basename(session_dir)
	report = {
		"schema_version": 1,
		"report_id": report_id,
		"generated_at": collect.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"machine": collect.get("machine", {}),
		"summary": {
			"total_outdated": total_outdated,
			"incompatible_count": incompatible,
			"warning_count": warning,
			"suggestions_count": suggestions_count,
			"health_count": health_count,
			"skill_drift_count": skill_drift_count,
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
