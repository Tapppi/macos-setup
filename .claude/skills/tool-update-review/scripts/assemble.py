#!/usr/bin/env python3
"""
assemble.py — validate research, then merge it with collect.sh into report.json.
Usage: assemble.py <session_dir> [--macos-setup-root PATH] [--dotfiles-root PATH]

Reads {session_dir}/collect.json (collect.sh's saved stdout, see
references/collection.md) and every {session_dir}/research/*.json file (see
references/item-schema.md — each one a JSON array of partial Tool objects, one
per tool, each carrying its own "id" and its own `items[]`), and writes
{session_dir}/report.json (references/schemas.md §Report Object).

**The item corpus is not read here.** `validate_items.validate_session()` owns
loading, spec validation, normalization, id assignment, the eighteen
invariants, impact and the initial bucket; this file consumes the views it
returns. That is not a layering nicety — two implementations of "does this
release touch this setup" is the drift the whole redesign exists to remove
(`REDESIGN.md` §C3), and one of them would have been a regex away from the
`brew:libpq` defect. Everything derived from items — `security`, `impact`,
`risk_level`, `review_bucket` — is read from the validation view, never
recomputed here.

What this file still owns, because none of it is a judgement about an item:

  * the version delta (`compute_version_delta` and friends)
  * the synthesized baseline `kind: "upgrade"` suggestion, per source
  * the `needs_sudo` heuristic
  * suggestion-id uniqueness across the whole report
  * per-suggestion `pre_accept`
  * `highlights[]` and the report-level `summary`
  * the tool-level CVE rollup, recomputed from `items[]`

Three output channels, and the split is load-bearing
(`references/item-schema.md` §3.3):

  report.json    the report
  assemble.warn  the spec-conformance channel FOR THE RUN AND NOTHING ELSE —
                 one validator finding per line, prefixed with its code, in
                 the validator's order. **A clean run produces zero lines.**
  assemble.log   everything that is not a conformance finding: what assembly
                 did, renamed, or could not do. Also echoed to stderr.

The old channel was one undifferentiated stderr tail running 275 lines at a
1:272 signal ratio, which is what made the one genuinely wrong evidence path
invisible.
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

import items as model


# ── the two channels (references/item-schema.md §3.3) ───────────────────────
# A conformance finding is the validator's and goes to assemble.warn. Anything
# assembly says about its own work is a note and goes here. Keeping them apart
# is the point: a channel that mixes "this citation does not resolve" with
# "brew-health suppressed an expected note" is a channel nobody reads.
_LOG_LINES: list = []


def note(message: str) -> None:
	"""Record one operational line and echo it to stderr.

	stderr as well as the file, deliberately: a human watching a live run
	should still see it as it happens, and the file exists so it is still
	there afterwards."""
	_LOG_LINES.append(message)
	print(message, file=sys.stderr)


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
			note(f"warning: {tool_id}: current and latest compare equal")
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


# ── the tool-level CVE rollup, recomputed from items[] ──────────────────────
# (references/item-schema.md §6: the rollup is unchanged in meaning; its source
# is now the items rather than a side array that could disagree with them.
# `security.cve_severities[]` is gone along with `resolve_cve_severities`'s
# reconciliation between two lists — per-CVE grading lives on the item that
# carries the CVE.)
#
# Research writes free text; CVE ids show up wherever a checker mentions them.
# `\b` before CVE rejects "NOTCVE-2026-1234"; (?:19|20)\d{2} pins the year so
# "CVE-3026-1234" doesn't match; \d{4,} has no upper bound because MITRE's
# sequence has none — capping it would silently turn a real 7-digit id into a
# non-match.
_CVE_RE = re.compile(r"\bCVE-(?:19|20)\d{2}-\d{4,}\b", re.IGNORECASE)
# The vendor's own count when it says "fixes 33 CVEs" without listing them.
# (?<![\d.]) is the guard against stunnel's real context sentence "Both 5.80
# CVEs need a running service", which otherwise yields a bogus claim of 80,
# and against "1234 CVEs" pairing with \d{1,3}.
_CVE_CLAIM_RE = re.compile(
	r"(?<![\d.])(\d{1,3})\s+"
	r"(?:CVEs?|security (?:issues|vulnerabilities|fixes|advisories)|vulnerabilities)\b",
	re.IGNORECASE)


def cve_sort_key(cve_id: str) -> tuple:
	"""(year, sequence) as ints, so CVE-2026-9595 sorts before CVE-2026-12143
	— the page renders the list verbatim and a lexical sort gets that pair
	wrong."""
	parts = cve_id.split("-")
	try:
		return (int(parts[1]), int(parts[2]))
	except (IndexError, ValueError):
		return (0, 0)


def _str(value) -> str:
	return value if isinstance(value, str) else ""


def _block(item: dict, key: str) -> dict:
	"""One of the item's optional blocks, read defensively. V2 reports a
	wrong-typed `change`/`local`/`security` and then leaves it on the item
	exactly as written — nulling it would make a truncated copy the only
	record — so every read here has to survive a non-dict."""
	value = item.get(key) if isinstance(item, dict) else None
	return value if isinstance(value, dict) else {}


def _claim_texts(item: dict) -> list:
	"""The vendor's own words about this change. Narrower than the id scan on
	purpose: `local.statement` is *our* analysis, and reading a claim out of it
	would let "the 5 CVEs above do not reach us" become a vendor claim of 5."""
	return [_str(item.get("title")), _str(item.get("body")),
		_str(_block(item, "change").get("citation"))]


def _scan_texts(item: dict) -> list:
	"""Every field a CVE id may be counted from. `local.statement` joins the
	claim fields — it is prose about *this* range by construction.

	Excluded deliberately: `local.citations[].text`, which legitimately cites a
	prior review or an upstream advisory from outside this current→latest range
	(`prior_review` is a citation kind), and `links[].embedded_content`, an
	unbounded changelog excerpt that can cover releases the user is not being
	asked about. Safe because `has_security` never depends on ids — a missed id
	understates `cve_count`, it cannot flip a security release into a
	non-security one."""
	return _claim_texts(item) + [_str(_block(item, "local").get("statement"))]


def item_cve_id(item: dict) -> str | None:
	"""The one CVE this item is a record of, or None. Structural, not scanned:
	`security.cve_id` first, then a `cve`-kind anchor. A checker that writes
	both writes them about the same advisory (I-5 checks the grammar of the
	first, I-9 of the second), so the tie goes to the explicit field."""
	explicit = _block(item, "security").get("cve_id")
	if isinstance(explicit, str) and _CVE_RE.fullmatch(explicit.strip()):
		return explicit.strip().upper()
	anchor = item.get("anchor") if isinstance(item, dict) else None
	if isinstance(anchor, dict) and anchor.get("kind") == "cve":
		value = anchor.get("value")
		if isinstance(value, str) and _CVE_RE.fullmatch(value.strip()):
			return value.strip().upper()
	return None


def extract_cve_ids(tool: dict) -> list:
	"""Every CVE id in this range, structural ids and prose mentions alike.

	The prose scan survives the schema change because understating is the
	failure mode with a cost: an id a checker names in a body but forgets to
	put in `security.cve_id` is still an advisory the user is being asked
	about, and `cve_count` is defined as the size of this list."""
	ids = set()
	for item in tool.get("items") or ():
		if not isinstance(item, dict):
			continue
		explicit = item_cve_id(item)
		if explicit:
			ids.add(explicit)
		for text in _scan_texts(item):
			for match in _CVE_RE.findall(text):
				ids.add(match.upper())
	return sorted(ids, key=cve_sort_key)


def extract_cve_claim(tool: dict) -> int | None:
	"""The vendor's own largest stated count, or None. Max wins, never sum:
	Firefox's two releases claim 50 and 47, and Chrome's headline item claims
	370 while another item describes a 68-fix subset of it — summing
	double-counts the subset, while the max is a defensible floor."""
	claims = []
	for item in tool.get("items") or ():
		if not isinstance(item, dict):
			continue
		for text in _claim_texts(item):
			claims.extend(int(n) for n in _CVE_CLAIM_RE.findall(text))
	return max(claims) if claims else None


def tool_cve_ratings(tool: dict, announce: bool = True) -> dict:
	"""→ {cve_id: rating} for the ids this tool's items actually grade.

	`announce=False` returns the same map silently. A within-tool disagreement
	is a fact about ONE tool and belongs in the log once, from the call that
	computes that tool's own rollup — and this function is called twice per
	tool, once from `compute_security` and once from `summarize_security`,
	which needs the values rather than a second announcement. The array this
	replaced (`security.cve_severities[]`) made the second call unnecessary by
	existing; deleting it is what created the second reader.

	Three rules survive verbatim from `resolve_cve_severities`, which this
	replaces: a word outside the vocabulary reads as `unknown` rather than a
	guess; a grade with no recorded basis is not a grade (I-6 reports it, and
	research is forbidden from deriving a severity from how a description
	reads); and when two items grade one id differently the **worse** wins,
	because understating is the failure mode with a cost. What is gone is the
	reconciliation between two lists that could disagree — there is one list."""
	tool_id = tool.get("id", "<unknown>")
	by_id: dict = {}
	for item in tool.get("items") or ():
		if not isinstance(item, dict):
			continue
		cve_id = item_cve_id(item)
		if not cve_id:
			continue
		sec = _block(item, "security")
		rating, basis = sec.get("rating"), sec.get("rating_basis")
		if rating not in model.CVE_RATINGS:
			rating = "unknown"
		if rating != "unknown" and basis not in _GRADED_BASES:
			# Reported as E-SEC-RATING-UNBASED; said here too, because the
			# consequence — the id buckets as `unknown` in severity_counts —
			# is assembly's and shows on the card.
			if announce:
				note(f"note: {tool_id}: {cve_id} is rated {rating!r} with basis {basis!r} — a rating "
					f"with no recorded source is not a rating; counting it as unknown")
			rating = "unknown"
		if rating == "unknown":
			# Recording it would say nothing an absent entry does not already say.
			continue
		prior = by_id.get(cve_id)
		if prior is not None and prior != rating:
			if announce:
				note(f"note: {tool_id}: {cve_id} is rated both {prior!r} and {rating!r} by two "
					f"items — keeping the worse")
			if model.CVE_WORSE_RANK[rating] <= model.CVE_WORSE_RANK[prior]:
				continue
		by_id[cve_id] = rating
	return by_id


# `unrated` is a member of the rating-basis vocabulary and is NOT a basis: it
# is the word for "nobody graded this". I-6 says so as an invariant
# (`rating != unknown` ⇒ `rating_basis != unrated`, E-SEC-RATING-UNBASED), and
# assembly has to read it the same way — treating `unrated` as a source is how
# a `critical` with no provenance would reach a card.
_GRADED_BASES = frozenset(model.RATING_BASES) - {"unrated"}


def rollup_severity_counts(cve_ids: list, severity_by_id: dict) -> dict:
	"""`{critical, high, medium, low, unknown}` over this tool's ids.

	**The invariant `sum(counts.values()) == cve_count` holds by construction**,
	because the loop iterates `cve_ids` and every id lands in exactly one
	bucket — an id nothing rated becomes `unknown` instead of a broken sum.
	Iterating the rating map instead would silently break it, which is why
	test_assemble.py asserts the sum anyway."""
	counts = dict.fromkeys(model.CVE_RATINGS, 0)
	for cve_id in cve_ids:
		counts[severity_by_id.get(cve_id, "unknown")] += 1
	return counts


def config_needs_attention(tool: dict) -> bool:
	"""One spelling of "this tool's config may be stale", read defensively:
	`config_status` is normalized by the validator but can still be
	present-but-null on a Tool object arriving from anywhere else."""
	return (tool.get("config_status") or {}).get("state") == "needs_attention"


def suggestion_kind(sug: dict) -> str:
	"""`kind` is omittable and defaults to "edit" (references/schemas.md §1.2),
	so every read has to apply that default — every test below changes meaning
	without it."""
	return sug.get("kind") or "edit"


# A memory proposal changes what we REMEMBER; an action proposal changes the
# user's system (`REDESIGN.md` §O). Only the second is an authored action, and
# only the second may push a tool toward a decision.
#
# Spelled as a NEGATION on purpose, and this is the part that is easy to get
# wrong: the positive form ("is the kind one of edit/structural") **fails
# open** — a typo'd `"edits"` carrying a real config edit falls through and
# scores nothing. An unrecognized kind must count as an action.
#
# The set is the model's, not a private copy: the bucket clause
# (`validate_items.compute_initial_bucket`) asks `items.needs_a_decision`,
# which reads the same tuple, so the two layers cannot come to disagree —
# `test_research_guidelines.DocumentedScopeTests` asserts that agreement on
# the live functions across every kind, recognized or drifted.
_MEMORY_SUGGESTION_KINDS = frozenset(model.MEMORY_SUGGESTION_KINDS)


def is_action_suggestion(sug) -> bool:
	"""Does this suggestion propose a change to the user's system?"""
	if not isinstance(sug, dict):
		return False
	kind = suggestion_kind(sug)
	return kind != "upgrade" and kind not in _MEMORY_SUGGESTION_KINDS


def compute_security(tool: dict, view: dict) -> dict:
	"""The whole `security` object (references/schemas.md §1.9).

	The rollup is assembly's; every *decision* in it is the validator's.
	`has_security`, `security_only` and `impact` are read off
	`bucket_inputs`/`impact` rather than recomputed, so the block on the card
	and the bucket on the card can never disagree — which they would the first
	time one of the two definitions was edited alone."""
	inputs = view.get("bucket_inputs") or {}
	if tool["source"] in NON_VERSION_SOURCES:
		# Forced false: the security section is about *patches* the user can
		# take. An untrusted tap is a trust decision, not a shipped fix, and
		# counting it in tools_with_security would make the section's count
		# disagree with the cards it lists. Its security character still shows
		# via the health→tag map (untrusted_tap → a `security`-tagged item).
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
			"impact": view.get("impact", "unknown"),
			"severity_counts": dict.fromkeys(model.CVE_RATINGS, 0),
			"display_item_ids": [],
		}
	cve_ids = extract_cve_ids(tool)
	return {
		"cve_ids": cve_ids,
		# ALWAYS len(cve_ids) — an id-backed count, never a claim, so the page
		# can link every counted CVE to something.
		"cve_count": len(cve_ids),
		"cve_claimed_count": extract_cve_claim(tool),
		"has_security": bool(inputs.get("has_security")),
		"security_only": bool(inputs.get("security_only")),
		"impact": view.get("impact", "unknown"),
		# Sums to cve_count by construction (rollup_severity_counts). `unknown`
		# dominating is the expected state, not a degraded one: a checker grades
		# only what the page it already read states.
		"severity_counts": rollup_severity_counts(cve_ids, tool_cve_ratings(tool)),
		# The replacement for `notable[]` — and it is a *bar*, not a cap
		# (`items.is_security_display_item`). Ids rather than copies, so the
		# page renders the one item and the two lists cannot drift apart the
		# way `notable[]` and `headliners[]` did.
		"display_item_ids": list(view.get("security_display_item_ids") or []),
	}


# ── review_bucket / pre_accept (references/assembly.md §Review Buckets and Pre-Accept) ──
def baseline_upgrade(tool: dict):
	"""The synthesized baseline suggestion, or None. Identified by position +
	kind + `:upgrade` id suffix, so a brew-health finding's
	`{tool_id}:remediate` and a skill-drift finding's `{tool_id}:sync` never
	match — which is the single mechanism that makes both non-version sources
	impossible to pre-accept (apply_pre_accept only ever writes True onto the
	object this returns).

	On the decision path (finalize_tool → pre_accept) this always runs *before*
	main()'s id-uniqueness renaming pass, which is what the identification
	assumes. The scoring path (score_tool, via build_highlights) necessarily
	runs *after* it, and stays correct for the case that pass actually hits: a
	collision *within* one tool renames the research-authored duplicate, never
	the baseline, because the baseline is `suggestions[0]` and claims the id
	first. The one case that degrades is a cross-tool collision where an
	earlier tool's research suggestion squats on this tool's baseline id — the
	baseline is then renamed to ":upgrade-2" and this returns None afterwards,
	costing the tool `score_tool()`'s 10-point `manual_action` signal. Bounded
	and never silent: it can only lower a highlight score, `pre_accept` was
	computed before the rename, and the pass logs a note naming both ids."""
	sugs = tool.get("suggestions") or []
	if not sugs:
		return None
	first = sugs[0]
	if suggestion_kind(first) == "upgrade" and str(first.get("id", "")).endswith(":upgrade"):
		return first
	return None


def apply_pre_accept(tool: dict) -> None:
	"""The single pre-accept mechanism (the page reads `pre_accept` instead of
	re-deriving it from risk_level). Only the baseline `{source}:{name}:upgrade`
	suggestion is ever eligible — never a research-authored edit, never a
	structural change, never a watch-item, never a brew-health remediation,
	never a skill-drift sync. `auto_runnable: false` is a hard exclusion:
	"accepted" would claim a decision about something the skill cannot execute.
	`needs_sudo` deliberately does *not* block it — blocking it would
	un-pre-accept nearly every cask (the heuristic defaults casks to true), and
	it isn't silent: the card renders visibly as ACCEPTED before submit and
	apply routes through the askpass prompt the user answers interactively.
	The security_auto clause is a union with the existing risk_level path, not
	a second mechanism; it can only ever differ from `risk_level == "low"` for
	a tool whose one elevating signal is a `security`-tagged item with a local
	finding at warning severity — "this security fix matters to you", a reason
	to take the update rather than hold it."""
	baseline = baseline_upgrade(tool)
	for sug in tool.get("suggestions", []):
		sug["pre_accept"] = bool(
			sug is baseline
			and sug.get("auto_runnable")
			and (tool["risk_level"] == "low" or tool["review_bucket"] == "security_auto"))


def finalize_tool(tool: dict, view: dict) -> None:
	"""Compute every derived field, in dependency order. Mutates in place.

	Three of the five now come straight off the validation view — `impact`,
	`risk_level` and the bucket are stage V5/V6 output, and recomputing them
	here would be a second implementation of the one thing `REDESIGN.md` §C3
	says must have exactly one. What is left is the version delta (assembly's,
	because the page renders it), the CVE rollup, and `pre_accept`, which needs
	the baseline suggestion the validator has never seen.

	`bucket_inputs` rides along on the tool so a reader — and, once C4 exists,
	convergence — can see *why* a card carries the bucket it does without
	re-deriving it."""
	delta, scheme, delta_note = compute_version_delta(
		tool.get("current_version"), tool.get("latest_version"), tool["source"], tool.get("id"))
	tool["version_delta"] = delta
	tool["version_scheme"] = scheme
	tool["version_delta_note"] = delta_note
	tool["security"] = compute_security(tool, view)
	tool["risk_level"] = view.get("risk_level", "elevated")
	tool["review_bucket"] = view.get("initial_review_bucket", "attention")
	tool["bucket_inputs"] = dict(view.get("bucket_inputs") or {})
	apply_pre_accept(tool)


# ── the raw research fields the item model does not cover ───────────────────
# `release_inventory[]` is "unchanged and out of this schema's way"
# (references/item-schema.md §6) and `cask_sudo_hint` is a per-source packaging
# hint, so neither has a validation view to read. They are passed through from
# the research object, and they are the whole of assembly's remaining contact
# with raw research output.
def as_item_list(value, tool_id: str, field: str, member_type=dict) -> list:
	"""Coerce a research-supplied array field to a list of well-shaped members.

	`dict.get(key, default)` only substitutes the default when the key is
	*absent*, so a present-but-null `"release_inventory": null` reaches
	`for entry in …` as None, and a drifted `"release_inventory": "none"`
	reaches `entry.get(…)` as a string. Both used to abort the run — and one
	report is assembled from ~22 research files covering ~78 tools, so a single
	drifted file destroyed the whole report *after* the expensive part of the
	session was already spent. Degrade to one noted tool instead."""
	if value is None:
		return []
	if not isinstance(value, list):
		note(f"warning: {tool_id}: {field} was {type(value).__name__}, not an array — ignoring it: {value!r}")
		return []
	kept = []
	for entry in value:
		if isinstance(entry, member_type):
			kept.append(entry)
		else:
			note(f"warning: {tool_id}: {field} entry was {type(entry).__name__}, "
				f"not {member_type.__name__} — dropping it: {entry!r}")
	return kept


# ── main assembly ───────────────────────────────────────────────────────────
def read_findings_block(collect: dict, key: str, source: str) -> tuple:
	"""→ (findings, suppressed) for one `{findings, suppressed}` block of
	collect.json (`brew_health`, `skill_drift`).

	Read defensively, for the same reason as_item_list() exists one boundary
	over: each block is produced by a detector that can fail, time out, or be an
	older version of itself, and the whole block can be absent (a session
	collected before that detector existed), null, or the wrong shape. A finding
	source is an *addition* to the report — never let a malformed one cost the
	version updates the run was actually for. Anything unusable is noted and
	dropped, never silently swallowed.

	The block key is authoritative about `source`: a finding that omits it (or
	disagrees) is stamped, because build_tool() dispatches on that field and a
	missing one would otherwise be a KeyError several tools into the loop."""
	block = collect.get(key)
	if block is None:
		return ([], [])
	if not isinstance(block, dict):
		note(f"warning: collect.json {key!r} was {type(block).__name__}, not an object — ignoring it")
		return ([], [])
	raw = block.get("findings") or []
	if not isinstance(raw, list):
		note(f"warning: collect.json {key}.findings was {type(raw).__name__}, not an array — ignoring it")
		raw = []
	findings = []
	for item in raw:
		if not isinstance(item, dict) or not item.get("id"):
			note(f"warning: collect.json {key}.findings: dropping a malformed entry: {item!r}")
			continue
		if item.get("source") != source:
			note(f"warning: {item['id']}: {key}.findings entry had source {item.get('source')!r} — reading it as {source!r}")
			item = dict(item, source=source)
		findings.append(item)
	raw_sup = block.get("suppressed") or []
	if not isinstance(raw_sup, list):
		note(f"warning: collect.json {key}.suppressed was {type(raw_sup).__name__}, not an array — ignoring it")
		raw_sup = []
	return (findings, [s for s in raw_sup if isinstance(s, str)])


def load_research(research_dir: str) -> dict:
	"""Returns {tool_id: research_obj} — the RAW research entries, for the two
	fields the item model does not cover (see as_item_list above). The
	validated corpus comes from validate_session(), not from here.

	Every file in research/ is a JSON array (references/item-schema.md §7); a
	tool with no matching entry (subagent failure/timeout) just gets
	research_error set by the validator."""
	by_id: dict = {}
	if not os.path.isdir(research_dir):
		note(f"warning: no research/ dir at {research_dir!r} — every tool will show research_error")
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
			# nothing else" is the right rule; the type is named so the note
			# still says what went wrong.
			note(f"warning: could not read {fpath!r}: {type(exc).__name__}: {exc}")
			continue
		if not isinstance(entries, list):
			note(f"warning: {fpath!r} is not a JSON array — skipping")
			continue
		for entry in entries:
			# A research file is written by a subagent, so any member can be any
			# shape. Degrade per entry, never per run: one bare string here used to
			# raise AttributeError out of load_research and abort the whole report,
			# while every malformed shape *inside* an entry is survivable downstream.
			if not isinstance(entry, dict):
				note(f"warning: an entry in {fpath!r} is {type(entry).__name__}, not an object — skipping")
				continue
			tid = entry.get("id")
			if not tid or not isinstance(tid, str):
				note(f"warning: an entry in {fpath!r} has no usable \"id\" — skipping")
				continue
			if tid in by_id:
				note(f"warning: duplicate research entry for {tid!r} ({fpath!r} overwrites an earlier file)")
			by_id[tid] = entry
	return by_id


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
	noted, because something did try to write that section."""
	if key not in collect:
		return []
	value = collect.get(key)
	if not isinstance(value, list):
		note(f"warning: collect.json {key!r} was {type(value).__name__}, not an array — ignoring it")
		return []
	kept = []
	for item in value:
		if isinstance(item, dict):
			kept.append(item)
		else:
			note(f"warning: collect.json {key}: dropping a malformed candidate: {item!r}")
	return kept


# ── brew-health finding → item tag (references/assembly.md §Brew-Health Assembly) ──
# A brew doctor finding is not a changelog fact, but it still has to reach a
# reader through the same four content groups — and under the item model a
# group is derived from a tag. These maps therefore name a TAG, and the group
# each one lands in (`items.GROUP_OF_TAG`) reproduces the previous category
# mapping exactly: packaging/chore → notes, fix → fixes, security → security.
# Untrusted taps are a trust decision, so → security.
_HEALTH_CATEGORY_TAG = {
	"deprecated_cask": "packaging",
	"disabled_cask": "packaging",
	"deprecated_formula": "packaging",
	"disabled_formula": "packaging",
	"missing_keg": "fix",
	"unlinked_keg": "fix",
	"untrusted_tap": "security",
	"missing_dependency": "fix",
	"path_note": "chore",
	"other": "chore",
}


# ── skill-drift state → item tag (references/assembly.md §Skill-Drift Assembly) ──
# Same idea as _HEALTH_CATEGORY_TAG above. A drift the user is being asked to
# resolve is a `fix` (something is behind and there is a sync to run); a drift
# that needs no decision — `local_only` (the local copy is deliberately
# patched) or a per-vendor `probe_error` (we could not check) — is a `chore`.
# Nothing here maps to `security`: a stale vendored *prompt* ships no advisory,
# and claiming otherwise would put an unactionable card in the security section.
_DRIFT_STATE_TAG = {
	"upstream_ahead": "fix",
	"diverged": "fix",
	"local_only": "chore",
	"probe_error": "chore",
	"in_sync": "chore",
}


def synthesize_finding_item(tool_id: str, statement: str, tag: str, severity: str,
		expected: bool) -> dict:
	"""The one item a brew-health or skill-drift card carries when no checker
	wrote one for it.

	`change: null` with `local` present, which is more honest than the old
	synthetic headliner: a health finding is not an upstream change at all, it
	is a statement about *this* install — so it has a local block and nothing
	upstream to cite. `direction: "reaches"` for the same reason; the finding
	is by construction about the machine in front of us. It carries no
	`evidence[]`, and nothing reports that: the item is synthesized here, after
	validation, so I-14 never sees it. That is deliberate — inventing an
	evidence path for a `brew doctor` line would be inventing a citation."""
	return {
		"id": f"{tool_id}#slug:finding",
		"id_stability": "slug",
		"anchor": {"kind": "none", "value": None, "slug": "finding"},
		"title": statement,
		"body": "",
		"tags": [tag],
		"severity": severity,
		"change": None,
		"local": {
			"direction": "reaches",
			"effect": "none" if expected else "risk",
			"statement": statement,
			"evidence": [],
			"citations": [],
		},
		"security": None,
	}


def _tool_base(view: dict, research_obj: dict, tool_id: str) -> dict:
	"""The fields every Tool object takes from its validation view.

	Everything item-shaped is the view's — already spec-checked, normalized,
	id-assigned and canonically ordered. `spec_violations[]` and `quarantine[]`
	ride along so a consumer sees a degraded tool without opening a second
	file, which is the third of the three loudness channels."""
	return {
		"items": list(view.get("items") or []),
		"quarantine": list(view.get("quarantine") or []),
		"spec_violations": list(view.get("spec_violations") or []),
		"validator_error": view.get("validator_error"),
		"links": list(view.get("links") or []),
		"config_status": view.get("config_status") or {"state": "unknown", "detail": "", "evidence": []},
		"vendor_silent_categories": list(view.get("vendor_silent_categories") or []),
		"release_inventory": as_item_list(
			research_obj.get("release_inventory"), tool_id, "release_inventory"),
		"suggestions": list(view.get("suggestions") or []),
	}


def build_health_tool(candidate: dict, research_obj: dict | None, view: dict) -> dict:
	"""Build a Tool object for a `source: "brew-health"` finding (references/assembly.md §Brew-Health Assembly). Unlike
	a version-outdated tool it has no current→latest delta and gets no
	synthesized `brew upgrade` baseline — its action is the finding's own
	remediation. Research (if a brew-health subagent ran) supplies the items
	and suggestions; absent research, this degrades to the collect.sh finding's
	own detail + default remediation so the card is still useful on its own."""
	research_obj = research_obj or {}
	tool_id = candidate["id"]
	category = candidate.get("category", "other")
	expected = bool(candidate.get("expected", False))

	tool = {
		"id": tool_id,
		"name": candidate.get("name", tool_id),
		"source": "brew-health",
		"health_category": category,
		"health_expected": expected,
		"pinned": False,
		"current_version": None,
		"latest_version": None,
		"research_error": view.get("research_error"),
	}
	tool.update(_tool_base(view, research_obj, tool_id))

	if not tool["items"]:
		tool["items"] = [synthesize_finding_item(
			tool_id,
			candidate.get("detail") or candidate.get("name") or tool_id,
			_HEALTH_CATEGORY_TAG.get(category, "chore"),
			candidate.get("severity") or "notable",
			expected)]

	# Suggestions: research's if present, else synthesize from the finding's
	# default remediation (skipped for expected/no-action findings, e.g. the
	# intentional GNU-utils PATH note, which carries remediation: null).
	if not tool["suggestions"]:
		rem = candidate.get("remediation")
		if isinstance(rem, dict) and rem.get("command"):
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
			tool["suggestions"] = [sug]

	finalize_tool(tool, view)
	return tool


def build_drift_tool(candidate: dict, research_obj: dict | None, view: dict) -> dict:
	"""Build a Tool object for a `source: "skill-drift"` finding
	(references/assembly.md §Skill-Drift Assembly) — a vendored agent skill
	under `dotfiles/config/agent-skills/` that no longer matches the upstream it
	was synced from. Structurally a sibling of build_health_tool(): a finding,
	not a version update, so no current→latest pair and no synthesized
	`brew upgrade` baseline — its action is the finding's own vendor-scoped
	sync command."""
	research_obj = research_obj or {}
	tool_id = candidate["id"]
	# `or`, not a .get() default, throughout this builder: a detector that
	# emits an explicit null leaves the key *present*, so a default would never
	# substitute and the card would render "null" where its state or problem
	# statement belongs. probe_error is the honest fallback state — "we could
	# not establish this one" — and it is `expected`, so it stays quiet.
	state = candidate.get("drift_state") or "probe_error"
	expected = bool(candidate.get("expected", False))

	tool = {
		"id": tool_id,
		"name": candidate.get("name", tool_id),
		"source": "skill-drift",
		"drift_state": state,
		# "no decision required", not "no drift" — local_only and probe_error.
		"drift_expected": expected,
		# Vendor is the granularity the sync command actually operates at, so
		# the page can say which other cards one run would resolve.
		"drift_vendor": candidate.get("vendor"),
		"drift_skill": candidate.get("skill"),
		"pinned": False,
		"current_version": None,
		"latest_version": None,
		"research_error": view.get("research_error"),
	}
	tool.update(_tool_base(view, research_obj, tool_id))

	if not tool["items"]:
		tool["items"] = [synthesize_finding_item(
			tool_id,
			candidate.get("detail") or candidate.get("name") or tool_id,
			_DRIFT_STATE_TAG.get(state, "chore"),
			candidate.get("severity") or "notable",
			expected)]

	if not tool["suggestions"]:
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
			tool["suggestions"] = [sug]

	finalize_tool(tool, view)
	return tool


def build_tool(candidate: dict, research_obj: dict | None, view: dict) -> dict:
	source = candidate["source"]
	if source == "brew-health":
		return build_health_tool(candidate, research_obj, view)
	if source == "skill-drift":
		return build_drift_tool(candidate, research_obj, view)

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
		"research_error": view.get("research_error"),
	}
	tool.update(_tool_base(view, research_obj, tool_id))

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
		"rationale": "Picks up the changes described in items[] above.",
		"motivating_link": (tool["links"][0] if tool["links"] else None),
		"diff_preview": None,
	}
	if not auto_runnable:
		upgrade_suggestion["manual_reason"] = manual_reason
	tool["suggestions"].insert(0, upgrade_suggestion)

	finalize_tool(tool, view)
	return tool


# ── the per-candidate boundary (references/assembly.md §Summary Counts and Output) ──
# One report is assembled from ~22 research files over ~78 candidates, after
# the expensive part of the session is already spent. Anything that can only
# be wrong about *one* candidate must therefore cost that one card and leave
# the other 77 rendering — the same rule the validator applies per research
# entry one stage earlier.
def build_tool_guarded(candidate: dict, research_by_id: dict, views_by_id: dict):
	"""One Tool object, or None with a note naming what was dropped.

	Two layers, both loud. The identity keys are checked first, because
	build_tool() reads `candidate["source"]`/`["id"]`/`["name"]` directly and a
	KeyError there names only the key, never the candidate it came from. Then
	the build itself runs inside a boundary: every *known* malformed shape is
	already handled by the validator one stage up, and this catches the next
	one — the report loses one card and says which, instead of not existing."""
	tool_id = candidate.get("id")
	if not isinstance(tool_id, str) or not tool_id:
		note(f"warning: collect.json candidate has no usable \"id\" — skipping it: {candidate!r}")
		return None
	source = candidate.get("source")
	if not isinstance(source, str) or not source:
		note(f"warning: {tool_id}: candidate has no usable \"source\" — skipping it")
		return None
	if source not in NON_VERSION_SOURCES and not isinstance(candidate.get("name"), str):
		# The two finding builders default their own name from the id; only the
		# version path indexes `candidate["name"]` unconditionally.
		note(f"warning: {tool_id}: candidate has no usable \"name\" — skipping it")
		return None
	view = views_by_id.get(tool_id)
	if view is None:
		# The validator walks the same candidate list through the same readers,
		# so a miss here means the two disagreed about what a candidate is —
		# worth saying rather than papering over with an empty view.
		note(f"warning: {tool_id}: no validation view for this candidate — skipping it")
		return None
	try:
		return build_tool(candidate, research_by_id.get(tool_id), view)
	except Exception as exc:
		note(f"warning: {tool_id}: could not be assembled ({type(exc).__name__}: {exc}) — dropping this "
			f"tool; the rest of the report is unaffected")
		return None


# ── highlights (references/assembly.md §Highlights) ─────────────────────────
# "The biggest decision drivers / inputs needed / major patches", ranked by a
# fixed score rather than per-tool judgment — assemble.py is a plain script, so
# the ranking has to be reproducible from the data alone. review_bucket
# "security_auto" contributes nothing: it is by definition the bucket that
# needs no decision.
#
# The `watch_item_hit` signal is GONE, and its absence is deliberate. It was
# detected by regex-matching the literal phrase "Watch item hit:" in relevancy
# prose, which `REDESIGN.md` §I4 retires outright: the per-tool checker is
# given the watch items and labels a hit **in a structured field**, so a
# paraphrase can no longer cost 70 points silently. That field does not exist
# in the item schema yet — it belongs with the checker contract — so the signal
# is removed rather than reimplemented against prose that no longer has a
# guaranteed shape.
_HIGHLIGHT_THRESHOLD = 40
_HIGHLIGHT_CAP = 8
_WHY_MAX = 220
_RANK_SEVERITY = {v: k for k, v in model.SEVERITY_RANK.items()}


def content_items(tool: dict) -> list:
	"""The tool's items, non-dict members skipped. One array now — the whole
	point of the item model — so this is a filter rather than a merge."""
	return [i for i in (tool.get("items") or []) if isinstance(i, dict)]


def local_items(tool: dict) -> list:
	"""Items carrying a finding about *this* setup. The successor to
	`relevancy[]`, and the one distinction that survived the merge: an item
	with a `local` block says something about this machine, an item without
	says something about the release."""
	return [i for i in content_items(tool) if isinstance(i.get("local"), dict)]


def _has_tag(item: dict, tag: str) -> bool:
	tags = item.get("tags")
	return isinstance(tags, list) and tag in tags


def _max_sev_rank(tool: dict) -> int:
	ranks = [model.severity_rank(i.get("severity")) for i in content_items(tool)]
	return max(ranks) if ranks else -1


def score_tool(tool: dict) -> tuple:
	"""→ (score, reasons) — reason codes in a fixed emission order, so the
	page's chips never reshuffle between runs."""
	items = content_items(tool)
	local = local_items(tool)
	sugs = [s for s in (tool.get("suggestions") or []) if isinstance(s, dict)]
	baseline = baseline_upgrade(tool)
	scored = []

	def add(points: int, code: str, fires) -> None:
		if fires:
			scored.append((points, code))

	add(100, "incompatible_finding", any(i.get("severity") == "incompatible" for i in local))
	add(60, "config_stale", config_needs_attention(tool))
	add(45, "warning_finding", any(i.get("severity") == "warning" for i in local))
	# The old `breaking_change` read "an incompatible-severity headliner", i.e.
	# a release-level break with no local finding. Under the item model the
	# claim is said directly by a tag rather than inferred from which array the
	# item was written into.
	add(40, "breaking_change", any(
		_has_tag(i, "breaking") and i.get("severity") in ("warning", "incompatible")
		for i in items))
	add(35, "proposed_edit", any(is_action_suggestion(s) for s in sugs))
	add(30, "pinned", bool(tool.get("pinned")))
	add(30, "security_mixed", tool["review_bucket"] == "security_mixed")
	add(25, "major_bump", tool["version_delta"] == "major")
	# Ported verbatim, and no `method-note` equivalent is added: a memory
	# proposal earns no highlight points, which is the same rule the bucket
	# states, expressed in the highlight surface.
	add(25, "watch_item_proposed", any(suggestion_kind(s) == "watch-item" for s in sugs))
	add(20, "changelog_warning", any(
		i.get("severity") == "warning" and not isinstance(i.get("local"), dict) for i in items))
	add(20, "research_failed", bool(tool.get("research_error") or tool.get("validator_error")))
	add(15, "unknown_scheme", tool["version_delta"] == "unknown")
	add(10, "cves", tool["security"]["cve_count"] >= 1)
	add(10, "manual_action", bool(baseline) and not baseline.get("auto_runnable"))
	return (sum(p for p, _ in scored), [code for _, code in scored])


def _why_string(value, tool_id: str, field: str):
	"""Research's free text, but only when it *is* text — else None, so
	_highlight_why_parts() falls through to its next branch.

	`_truncate_why()` calls `.split()`, so a title a subagent nested one level
	too deep (`"title": {"text": "…"}`) used to raise AttributeError out of
	build_highlights() and abort the report for all 78 tools. Coercing it with
	`str()` is not the fix either — that renders the repr on the card. Note it,
	skip the field, and let the next-best line take the slot."""
	if value is None or isinstance(value, str):
		return value or None
	note(f"warning: {tool_id}: {field} was {type(value).__name__}, not a string — not using it as a "
		f"highlight line rather than rendering its repr: {value!r}")
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
#
# The old eight collapsed to these: `relevancy_*` vs `headliner_*` were two
# names for "did this item carry a local finding", which is now one read of the
# item, and the `security` half of each is one read of its tags.
_WHY_SOURCES = ("item_local_security", "item_local_other", "config_status",
	"research_error", "item_security", "item_other", "major_bump", "none")


def _item_source(item: dict, local: bool) -> str:
	kind = "security" if _has_tag(item, "security") else "other"
	return f"item_local_{kind}" if local else f"item_{kind}"


def _highlight_why_parts(tool: dict) -> tuple:
	"""→ (why, why_source, why_ref). First match in a fixed order, so the
	card's one line is the most specific thing we know about this tool.
	`why_ref` is the **item id** of the item the line came from, or None for
	the branches that synthesize their own text — the id the validator
	assigned, so the dedupe below matches on identity rather than on prose."""
	tool_id = tool.get("id", "<unknown>")
	# Worst first, then down the list. Ties resolve to canonical order, which
	# `items[]` already arrives in — `sorted` is stable, so equal severities
	# keep it.
	#
	# It walks rather than picking the single maximum for a reason: one
	# malformed title on the worst item used to cost the tool this whole branch
	# and drop it to `config_status`/`major_bump`, even with three perfectly
	# good local findings behind it. Degrade one item, not one branch.
	for best in sorted(local_items(tool),
			key=lambda i: -model.severity_rank(i.get("severity"))):
		title = _why_string(best.get("title"), tool_id, "items[{}].title".format(best.get("id")))
		if title:
			return (_truncate_why(title), _item_source(best, True), best.get("id"))
	cs = tool.get("config_status") or {}
	if config_needs_attention(tool):
		detail = _why_string(cs.get("detail"), tool_id, "config_status.detail")
		if detail:
			return (_truncate_why(detail), "config_status", None)
	if tool.get("research_error"):
		return ("Research produced no changelog for this update.", "research_error", None)
	items = content_items(tool)
	if tool["review_bucket"] in ("security_auto", "security_mixed"):
		# Non-security first. This step used to return the first *security*
		# item, which is the same line the card's security column renders in
		# full — a highlight slot spent restating what the reader has just
		# read. The security item is still the fallback when the tool has
		# nothing else.
		for want_security in (False, True):
			for item in items:
				if _has_tag(item, "security") != want_security:
					continue
				title = _why_string(item.get("title"), tool_id,
					"items[{}].title".format(item.get("id")))
				if title:
					return (_truncate_why(title), _item_source(item, False), item.get("id"))
	if tool["version_delta"] == "major":
		return (_truncate_why(f"Major version bump {tool.get('current_version')} → {tool.get('latest_version')}."),
			"major_bump", None)
	for item in items:
		title = _why_string(item.get("title"), tool_id, "items[{}].title".format(item.get("id")))
		if title:
			return (_truncate_why(title), _item_source(item, False), item.get("id"))
	return ("", "none", None)


def _highlight_severity(tool: dict) -> str:
	"""Max severity across the tool's items, in the item vocabulary so the page
	reuses the existing palette."""
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

	When a highlight's `why` restates an item the tool's security column
	already shows in full, **the highlight yields and the slot is backfilled**
	from the next-ranked candidate — the security card keeps the sentence (it
	is often the single most important line on it) and the section still
	carries `_HIGHLIGHT_CAP` distinct decision drivers. The match is on the
	item id, never on `why`, which _truncate_why() has already cut at 220
	chars."""
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
			note(f"warning: {tool.get('id')!r}: could not be scored for highlights "
				f"({type(exc).__name__}: {exc}) — it cannot appear in that section")
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
			note(f"warning: {tool.get('id')!r}: highlight could not be built "
				f"({type(exc).__name__}: {exc}) — backfilling from the ranked list")
			continue
		shown = set(tool["security"].get("display_item_ids") or [])
		if obj["why_ref"] and obj["why_ref"] in shown:
			note(f"note: {tool['id']}: highlight dropped — its \"why\" restates security item "
				f"{obj['why_ref']}; backfilling from the ranked list")
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
	# Rebuilt from each tool's item ratings rather than summed from the
	# per-tool severity_counts, for the same reason cve_count is a union: one
	# advisory can land on two tools, and summing would count it twice. Two
	# tools rating one id differently is real once ratings come from different
	# pages — take the worse, since understating a severity in the report
	# header is the failure mode with a cost.
	severity_by_id: dict = {}
	for tool in tools:
		# The tool's own emitted list, not a re-scan: `compute_security` forces
		# it empty for a non-version source, and reading the items directly
		# would let a health finding whose research supplied a CVE-bearing item
		# contribute a grade for an id the report deliberately does not count —
		# and fire a "rated differently on two tools" note about it.
		tool_ids = set(tool["security"]["cve_ids"])
		ids.update(tool_ids)
		if not tool_ids:
			# Skip before CALLING it, not after reading it: `tool_cve_ratings`
			# notes a within-tool disagreement as it resolves one, so a
			# non-version source with two items grading one CVE would write
			# "rated both critical and low" to assemble.log about an id this
			# report counts nowhere.
			continue
		# Silent: `compute_security` already announced this tool's own
		# disagreements when it built the tool's rollup. The cross-tool note
		# below is a different fact and is this loop's to make.
		for cve_id, severity in tool_cve_ratings(tool, announce=False).items():
			if cve_id not in tool_ids:
				# Unreachable for a version source — every id `item_cve_id`
				# returns is in `cve_ids` by construction — and kept as the
				# structural guarantee rather than a second thing to remember.
				continue
			prior = severity_by_id.get(cve_id)
			if prior is not None and prior != severity:
				# Its per-tool twin in tool_cve_ratings() notes exactly this;
				# the report-wide one used to resolve it silently, so a header
				# reading "1 critical" could come from one tool's page
				# disagreeing with another's with nothing said about it.
				note(f"note: {cve_id} is rated {prior!r} on one tool and {severity!r} on another — "
					f"keeping the worse for the report-wide rollup")
			if model.CVE_WORSE_RANK[severity] > model.CVE_WORSE_RANK.get(prior or "unknown", 0):
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
	# (the NixOS flake repo) for relevancy, so evidence resolution needs that
	# repo root too, or every "systems/..." citation resolves nowhere. Defaults
	# to the standard workspace location; pass explicitly if the harness mounts
	# that repo somewhere else.
	parser.add_argument("--systems-root", default="~/project/github/tapppi/systems")
	args = parser.parse_args()

	# Imported here rather than at module scope, and the direction is the
	# reason. `validate_items` imports THIS module for the shape primitives the
	# two share — the version delta, the source predicates, the collect readers
	# — so a module-level import back would be a cycle that breaks
	# `import validate_items` outright. Assembly is a *consumer* of validation,
	# one pipeline stage later, and nothing above this line needs it.
	import validate_items

	# One run, one log. main() is called more than once in a process by the
	# test suite, and a log that accumulated across runs would attribute an
	# earlier run's notes to this one.
	del _LOG_LINES[:]

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

	# Stage 3 (`REDESIGN.md` §C3), in process. `validate_session` writes
	# nothing; the artifacts below are written from the document it returns.
	try:
		document = validate_items.validate_session(session_dir, roots,
			manifest_root=macos_setup_root)
	except validate_items.NoCandidateSet as exc:
		print(f"Error: {exc}", file=sys.stderr)
		sys.exit(1)
	views_by_id = {v["id"]: v for v in document["tools"]}

	repo_context_path = os.path.join(session_dir, "repo_context.json")
	try:
		with open(repo_context_path, "r", encoding="utf-8") as fh:
			repo_context = json.load(fh)
	except Exception as exc:
		note(f"warning: no usable repo_context.json at {repo_context_path!r} ({type(exc).__name__}) — using placeholder")
		placeholder = {"up_to_date": True, "ahead": 0, "behind": 0, "recent_commits": []}
		repo_context = {"macos_setup": placeholder, "dotfiles": dict(placeholder)}

	research_by_id = load_research(os.path.join(session_dir, "research"))

	# The two finding sources (references/assembly.md §Brew-Health Assembly,
	# §Skill-Drift Assembly) are candidates too, appended after the
	# version-outdated tools so they sort/render as their own cards.
	health_findings, health_suppressed = read_findings_block(collect, "brew_health", "brew-health")
	for s in health_suppressed:
		note(f"note: brew-health suppressed (expected, not reported): {s}")
	drift_findings, drift_suppressed = read_findings_block(collect, "skill_drift", "skill-drift")
	for s in drift_suppressed:
		note(f"note: skill-drift suppressed (in sync, or no upstream to check): {s}")

	candidates = (
		read_candidate_list(collect, "brew") + read_candidate_list(collect, "mise") +
		read_candidate_list(collect, "standalone") + read_candidate_list(collect, "macos") +
		health_findings + drift_findings
	)

	tools = []
	for candidate in candidates:
		tool = build_tool_guarded(candidate, research_by_id, views_by_id)
		if tool is not None:
			tools.append(tool)

	# Suggestion-id uniqueness — global, not just within one tool. A collision
	# almost always means a research subagent copied an id pattern rather than
	# deriving it from its own tool, so append a disambiguating suffix rather
	# than silently dropping either one. The validator *reports* the collision
	# (W-SUG-DUP-ID) and changes nothing; renaming is a rendering necessity and
	# belongs here.
	seen_ids: dict = {}
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
				note(f"warning: {tool['id']}: suggestion id was {type(sid).__name__}, not a string — "
					f"discarding it: {sid!r}")
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
				note(f"warning: {tool['id']}: suggestion with no id — assigned {sid!r}")
				sug["id"] = sid
			if sid in seen_ids:
				n = 2
				new_id = f"{sid}-{n}"
				while new_id in seen_ids:
					n += 1
					new_id = f"{sid}-{n}"
				note(f"warning: duplicate suggestion id {sid!r} (tool {tool['id']!r}) — renamed to {new_id!r}")
				sug["id"] = new_id
				sid = new_id
			seen_ids[sid] = tool["id"]

	# Ranked *after* the uniqueness pass, so highlights[].suggestion_ids carry
	# the final, deduplicated ids.
	highlights = build_highlights(tools)

	# Counted over items carrying a `local` block — the successor to counting
	# `relevancy[]`. An item with no local finding is a statement about the
	# release, not about this machine, and these two boxes say "how many
	# findings land on me".
	incompatible = sum(1 for t in tools for i in local_items(t) if i.get("severity") == "incompatible")
	warning = sum(1 for t in tools for i in local_items(t) if i.get("severity") == "warning")
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
		# 2, not 1: `tools[].items[]` replaces headliners/relevancy/context and
		# `security.notable[]`, so a consumer written against schema 1 cannot
		# read this report and must not try.
		"schema_version": 2,
		"contract_version": model.CONTRACT_VERSION,
		"report_id": report_id,
		"generated_at": collect.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"machine": collect.get("machine", {}),
		# The run's spec-conformance state, so a consumer holding only
		# report.json still knows whether the corpus validated. The findings
		# themselves live in validation.json and assemble.warn.
		"validation": {
			"clean": document["clean"],
			"counts": document["counts"],
			"orphans": document["orphans"],
			"unmatched": document["unmatched"],
		},
		"summary": {
			"total_outdated": total_outdated,
			"incompatible_count": incompatible,
			"warning_count": warning,
			"suggestions_count": suggestions_count,
			"health_count": health_count,
			"skill_drift_count": skill_drift_count,
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

	# The pre-convergence artifact (criterion 12), written here because
	# assembly is where the validator ran.
	with open(os.path.join(session_dir, "validation.json"), "w", encoding="utf-8") as fh:
		json.dump(document, fh, ensure_ascii=False, indent="\t")
		fh.write("\n")

	# The two channels (references/item-schema.md §3.3). assemble.warn is the
	# spec-conformance channel and NOTHING else — a clean run leaves it empty,
	# which is the property that makes a non-empty one worth reading.
	warn_lines = validate_items.warn_lines(document)
	with open(os.path.join(session_dir, "assemble.warn"), "w", encoding="utf-8") as fh:
		fh.write("\n".join(warn_lines) + ("\n" if warn_lines else ""))
	with open(os.path.join(session_dir, "assemble.log"), "w", encoding="utf-8") as fh:
		fh.write("\n".join(_LOG_LINES) + ("\n" if _LOG_LINES else ""))
	print(out_path)


if __name__ == "__main__":
	main()
