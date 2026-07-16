#!/usr/bin/env bash
# collect.sh — gather update candidates for the tool-update-review skill.
# Usage: collect.sh [path-to-Brewfile]
# Emits one JSON object on stdout:
#   { machine: {...}, brew: [...], mise: [...], standalone: [...], macos: [...] }
# Network use is limited to `brew`/`mise`'s own update checks plus the macOS
# software-update lookup; every lookup is best-effort with a timeout so the
# script works offline (latest_version is then null and research must fill
# it in).
set -euo pipefail

brewfile="${1:-Brewfile}"

# Machine context — arch gates compatibility findings (e.g. ARM-only deps)
arch="$(uname -m)"
os_name="macOS $(sw_vers -productVersion 2>/dev/null || echo '?')"
hostname="$(hostname -s 2>/dev/null || echo '?')"

# Names manifested in the Brewfile, so transitive deps don't pollute the report
brewfile_formulae="[]"
brewfile_casks="[]"
if [[ -f "${brewfile}" ]]; then
	# `brew outdated`/`brew info` report both formulae and casks under their
	# short (untapped) name even when the Brewfile taps a qualified one
	# (e.g. hashicorp/tap/terraform, some-tap/cask-name), so strip any
	# tap prefix from both — matching `brew outdated`'s own `c.token`/
	# `f.full_name`-vs-short-name behavior for casks too, not just formulae.
	brewfile_formulae="$(grep -E '^brew "' "${brewfile}" | sed -E 's/^brew "([^"]+)".*/\1/' | sed -E 's|.*/||' | jq -R . | jq -s .)"
	brewfile_casks="$(grep -E '^cask "' "${brewfile}" | sed -E 's/^cask "([^"]+)".*/\1/' | sed -E 's|.*/||' | jq -R . | jq -s .)"
else
	echo "warning: Brewfile not found at ${brewfile}; brew section will be empty" >&2
fi

# brew outdated intersected with Brewfile entries; keeps pin state.
# `brew outdated` matches on the short name even when the Brewfile taps
# a qualified name (e.g. hashicorp/tap/terraform), hence the `sed s|.*/||`.
# --greedy is required for casks: brew silently skips `auto_updates: true`
# and `version :latest` casks otherwise (e.g. self-updating desktop apps
# like the Claude app, 1Password, Tailscale) even when they're genuinely
# behind — without it this report misses exactly the apps most likely to
# have drifted unnoticed.
brew_json="$(brew outdated --json=v2 --greedy 2>/dev/null | jq \
	--argjson formulae "${brewfile_formulae}" \
	--argjson casks "${brewfile_casks}" '
	[ (.formulae[] | select(.name as $n | $formulae | index($n)) | {
			id: ("brew:" + .name), name: .name, source: "brew",
			current_version: (.installed_versions | last),
			latest_version: .current_version,
			pinned: .pinned
		}),
		(.casks[] | select(.name as $n | $casks | index($n)) | {
			id: ("cask:" + .name), name: .name, source: "cask",
			current_version: .installed_versions[-1],
			latest_version: .current_version,
			pinned: false
		}) ]' || echo '[]')"

# Pinned formulae are hidden from `brew outdated` by default; surface them
# explicitly so a pinned-but-behind tool (the whole point of a pin) shows up.
# Filtered to Brewfile-manifested formulae only, same scope as brew_json
# above — a pin on a transitive dependency (not in the Brewfile) isn't ours
# to track and shouldn't pollute the report.
pinned_json="[]"
pinned_names="$(brew list --pinned 2>/dev/null || true)"
if [[ -n "${pinned_names}" ]]; then
	pinned_json="$(while IFS= read -r name; do
		printf '%s' "${brewfile_formulae}" | jq -e --arg n "${name}" 'index($n)' >/dev/null || continue
		info="$(brew info --json=v2 "${name}" 2>/dev/null)" || continue
		printf '%s' "${info}" | jq --arg name "${name}" '
			.formulae[0] | {
				id: ("brew:" + $name), name: $name, source: "brew",
				current_version: (.installed | last | .version),
				latest_version: .versions.stable,
				pinned: true
			}'
	done <<< "${pinned_names}" | jq -s .)"
fi

# mise runtimes. `mise outdated --json` reports current:null for
# alias-pinned runtimes (e.g. node pinned to "lts") even though the actually
# resolved version is available via `mise current <tool>` — fall back to
# that per-entry instead of shipping a null current_version for them (only
# alias-pinned tools hit this; version-pinned ones like go/python/rust/uv
# already report a real current from `mise outdated` itself).
# Field separator is \x01 (not tab): bash `read` collapses consecutive
# *whitespace-class* IFS delimiters (space/tab/newline) even when IFS is
# set explicitly to just one of them, which silently drops the empty
# `current` field for exactly the alias-pinned tools this block exists to
# fix (rediscovered by hand this way once already — see git history).
# A non-whitespace delimiter like \x01 doesn't get that collapsing
# treatment, so the three fields — one jq call instead of three per entry —
# read back correctly even when the middle one is empty.
mise_json="$(mise outdated --json 2>/dev/null | jq -r '
	to_entries[] | [.key, (.value.current // ""), .value.latest] | join("\u0001")' 2>/dev/null \
	| while IFS=$'\x01' read -r name current latest; do
		[[ -z "${name}" ]] && continue
		if [[ -z "${current}" ]]; then
			current="$(mise current "${name}" 2>/dev/null | head -n1 || true)"
		fi
		jq -n --arg id "mise:${name}" --arg name "${name}" \
			--arg cur "${current}" --arg lat "${latest}" '
			{ id: $id, name: $name, source: "mise",
				current_version: (if $cur == "" then null else $cur end),
				latest_version: $lat, pinned: false }'
	done | jq -s . 2>/dev/null)" || true
# Validate post-hoc rather than `|| echo '[]'` directly on the assignment:
# under `set -o pipefail`, a mid-pipeline failure (e.g. a stray non-JSON
# line from `mise outdated`) makes the *pipeline's* exit status non-zero
# even when a later stage already printed valid output — `|| echo '[]'`
# right there would then fire *in addition to* that valid output, making
# `mise_json` the two concatenated strings `[]\n[]` instead of one valid
# array. The `|| true` above exists for a second, sharper reason: under
# `set -e`, that same pipefail-driven non-zero status would abort the
# script at the assignment itself, before this validation check below
# ever runs at all — `|| true` absorbs it so the check is reachable, and
# the check itself is what actually decides whether `mise_json` needs to
# fall back to `[]`.
if ! printf '%s' "${mise_json}" | jq -e . >/dev/null 2>&1; then
	mise_json='[]'
fi

# Standalone CLIs installed outside brew. Both `claude-code@latest` and
# `codex` used to need this (they predate their Brewfile cask entries — see
# git history), but both are now plain Homebrew casks with `auto_updates`
# unset and a real resolved `version` (not the `:latest` sentinel), and their
# `/opt/homebrew/bin/*` shims are confirmed symlinks into the Caskroom — so
# `brew outdated --greedy` (brew_json above) already tracks them accurately.
# No tool currently needs a standalone check; the source type stays in the
# schema for a future CLI that's genuinely unmanaged by brew.
standalone_json="[]"

# macOS system software updates. `softwareupdate -l` has no JSON output and
# is network-bound, so treat it the same way as the checks above: best-effort,
# empty array on failure/timeout, research fills gaps.
macos_current="$(sw_vers -productVersion 2>/dev/null || echo '?')"
macos_updates_raw="$(softwareupdate -l 2>/dev/null | grep -oE 'Title: [^,]+, Version: [^,]+' | sed -E 's/Title: (.+), Version: (.+)/\1'$'\t''\2/' || true)"
macos_json="[]"
if [[ -n "${macos_updates_raw}" ]]; then
	macos_json="$(printf '%s\n' "${macos_updates_raw}" | jq -R -s --arg cur "${macos_current}" '
		split("\n") | map(select(length > 0) | split("\t")) | map({
			id: ("macos:" + (.[0] | ascii_downcase | gsub("[^a-z0-9]+"; "-"))),
			name: .[0], source: "macos",
			current_version: $cur, latest_version: (.[1] // null), pinned: false
		})' 2>/dev/null || echo '[]')"
fi

# Homebrew environment health (`brew doctor`) — holistic state of the brew
# install, not version deltas: deprecated/disabled casks, orphaned kegs with
# no formula, unlinked kegs, untrusted taps, missing dependencies, and any
# other warning block, each parsed into a structured finding with a default
# remediation. Parsing (and the noise filter) is Python for testability —
# same rationale as assemble.py/render.py being Python. Best-effort: an empty
# result on any failure, never aborts the collector.
#
# Noise filter (SKILL.md): unlinked kegs that are mise-managed language
# runtimes (ruby/python/node/…) are an *expected* byproduct of managing
# runtimes with mise while brew installs one as a transitive dependency —
# they are moved to `suppressed`, not reported as actionable. Non-prefixed
# GNU-utils PATH notes are intentional here (the Brewfile deliberately puts
# gnubin first), so they collapse into a single `expected` info finding
# rather than N actionable warnings.
brew_health_json="$(python3 - <<'PY' 2>/dev/null || echo '{"findings":[],"suppressed":[]}'
import json, re, subprocess

def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout or ""
    except Exception:
        return ""

# brew doctor exits non-zero when it finds anything; capture regardless.
doctor = ""
try:
    p = subprocess.run(["brew", "doctor"], capture_output=True, text=True, timeout=180)
    doctor = (p.stdout or "") + "\n" + (p.stderr or "")
except Exception:
    doctor = ""

# Split into blocks, each starting at a "Warning:" line.
blocks, cur = [], None
for ln in doctor.splitlines():
    if ln.startswith("Warning:"):
        if cur is not None:
            blocks.append(cur)
        cur = [ln]
    elif cur is not None:
        cur.append(ln)
if cur is not None:
    blocks.append(cur)

def first_indent_group(body):
    """The first contiguous run of 2+-space-indented lines — the affected
    item list, which in every known block comes before any prose/command
    examples that follow (e.g. the trust block's `brew trust …` examples)."""
    items, started = [], False
    for ln in body:
        if re.match(r'^\s{2,}\S', ln):
            items.append(ln.strip()); started = True
        elif started:
            break
    return items

# Bare base names only — is_runtime() strips any @version/suffix before the
# lookup, so a versioned keg like "node@20" already resolves to "node" here.
RUNTIME_SHADOW = {
    "ruby","python","node","go","rust","php","perl","bun","deno","zig",
    "elixir","erlang","lua","dotnet","java","openjdk","crystal","nim",
    "julia","kotlin","scala","swift","dart","r",
}
def is_runtime(name):
    base = re.split(r'[@\s]', name, 1)[0].lower()
    return base in RUNTIME_SHADOW

def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-') or "item"

findings, suppressed, path_notes = [], [], []

def add(cat, name, severity, detail, affected, remediation, expected=False, key=None):
    findings.append({
        "id": f"brew-health:{cat}:{slug(key or (affected[0] if affected else name))}",
        "name": name, "source": "brew-health", "category": cat,
        "severity": severity, "detail": detail, "affected": affected,
        "remediation": remediation, "expected": expected,
        "pinned": False, "current_version": None, "latest_version": None,
    })

for b in blocks:
    head = b[0][len("Warning:"):].strip()
    body = b[1:]
    hl = head.lower()
    items = first_indent_group(body)
    if "deprecated or disabled" in hl:
        # brew doctor emits this substring for two distinct warnings — one for
        # formulae ("Some installed formulae are deprecated or disabled.") and
        # one for casks ("Some installed casks are deprecated or disabled.").
        # Branch on which so a deprecated *formula* isn't looked up (and
        # mislabeled) as a cask.
        is_cask_block = "cask" in hl
        kind_word = "cask" if is_cask_block else "formula"
        for c in items:
            info = run(["brew", "info", "--cask", c]) if is_cask_block else run(["brew", "info", c])
            # A currently-*disabled* item can no longer be installed at all — a
            # stronger signal than merely deprecated; brew states it as
            # "Disabled because …" vs "Deprecated because …".
            disabled_now = re.search(r'^Disabled because', info, re.M) is not None
            # Stop at the first sentence terminator so a trailing "It will be
            # disabled on <date>." sentence doesn't get swallowed into the
            # reason (the date is captured separately below).
            reason_m = re.search(r'^(?:Deprecated|Disabled) because (.+?)(?:[.!]|$)', info, re.M)
            reason = reason_m.group(1).strip() if reason_m else None
            date_m = re.search(r'will be disabled on (\d{4}-\d{2}-\d{2})', info)
            disable_date = date_m.group(1) if date_m else None
            repl_m = re.search(r'Replacement:\s*\n\s*brew install(\s+--cask)?\s+(\S+)', info)
            repl = repl_m.group(2) if repl_m else None
            repl_is_cask = bool(repl_m and repl_m.group(1))
            state = "disabled" if disabled_now else "deprecated"
            det = f"{kind_word.capitalize()} `{c}` is {state} upstream"
            if reason:
                det += f" ({reason})"
            if disable_date and not disabled_now:
                det += f"; scheduled to be disabled on {disable_date}"
            det += "."
            if repl:
                det += f" Homebrew names replacement `{repl}`."
                repl_flag = " --cask" if repl_is_cask else ""
                old_flag = " --cask" if is_cask_block else ""
                rem = {"command": f"brew install{repl_flag} {repl} && brew uninstall{old_flag} {c}",
                       "auto_runnable": False, "needs_sudo": False,
                       "label": f"Install {repl}, then remove {c} (also update the Brewfile)"}
            else:
                rem = None
            if is_cask_block:
                cat = "disabled_cask" if disabled_now else "deprecated_cask"
            else:
                cat = "disabled_formula" if disabled_now else "deprecated_formula"
            title = f"{state.capitalize()} {kind_word}: {c}"
            add(cat, title, "warning", det, [c], rem)
    elif "have no formulae" in hl:
        det = ("Installed kegs have no corresponding formula (deleted upstream, installed "
               "manually, or from a tap that is now untrusted/removed): " + ", ".join(items) +
               ". If any come from a tap flagged untrusted (see untrusted-tap findings), "
               "trusting that tap restores their formulae rather than reinstalling.")
        add("missing_keg", "Orphaned kegs (no formula)", "notable", det, items, None, key="orphaned-kegs")
    elif "unlinked kegs" in hl:
        for k in items:
            if is_runtime(k):
                suppressed.append(f"unlinked keg `{k}` (mise-managed runtime; brew keg is a dependency shadow — expected, not actionable)")
                continue
            add("unlinked_keg", f"Unlinked keg: {k}", "warning",
                f"Keg `{k}` is unlinked in the Cellar; formulae depending on it may fail to build/run. "
                f"Relink with `brew link {k}` (add `--overwrite` if it reports a conflict).",
                [k], {"command": f"brew link {k}", "auto_runnable": False, "needs_sudo": False, "label": f"Relink {k}"})
    elif "not trusted" in hl:
        for t in items:
            add("untrusted_tap", f"Untrusted tap: {t}", "warning",
                f"Tap `{t}` is not trusted, so Homebrew is ignoring its formulae/casks/commands. "
                f"Trust it (`brew trust {t}`) if you rely on it, or remove it (`brew untap {t}`) if not. "
                f"Prefer trusting only the specific formulae you need where the tap supports it.",
                [t], {"command": f"brew trust {t}", "auto_runnable": False, "needs_sudo": False,
                      "label": f"Trust {t} (or `brew untap {t}` to remove)"})
    elif "missing dependencies" in hl:
        for cmd in items:
            dep = re.sub(r'^brew install\s+', '', cmd).strip()
            add("missing_dependency", f"Missing dependency: {dep}", "notable",
                f"An installed formula/cask is missing dependency `{dep}`. Install it with `brew install {dep}`.",
                [dep], {"command": f"brew install {dep}", "auto_runnable": True, "needs_sudo": False, "label": f"Install {dep}"})
    elif "non-prefixed" in hl:
        um = re.search(r'non-prefixed (\S+)', hl)
        path_notes.append(um.group(1) if um else "gnu-utils")
    else:
        add("other", f"brew doctor: {head[:70]}", "notable", "\n".join(b).strip(), items, None, key=head[:40])

if path_notes:
    names = ", ".join(sorted(set(path_notes)))
    det = (f"Non-prefixed GNU utilities ({names}) are earlier in PATH than the macOS "
           "defaults. This is intentional here (the Brewfile deliberately adds "
           "coreutils/findutils gnubin to PATH); brew doctor flags it generically "
           "because it can affect some source builds. No action needed unless a "
           "specific build fails.")
    add("path_note", "GNU utils in PATH (intentional)", "info", det, sorted(set(path_notes)), None, expected=True, key="gnu-utils-path")

print(json.dumps({"findings": findings, "suppressed": suppressed}))
PY
)"
if ! printf '%s' "${brew_health_json}" | jq -e . >/dev/null 2>&1; then
	brew_health_json='{"findings":[],"suppressed":[]}'
fi

jq -n \
	--arg arch "${arch}" --arg os "${os_name}" --arg host "${hostname}" \
	--argjson brew "${brew_json}" --argjson pinned "${pinned_json}" \
	--argjson mise "${mise_json}" --argjson standalone "${standalone_json}" \
	--argjson macos "${macos_json}" --argjson brew_health "${brew_health_json}" '
	{
		machine: { arch: $arch, os: $os, hostname: $host },
		brew: (($brew + $pinned) | unique_by(.id)
			| map(select(.current_version != .latest_version))),
		mise: ($mise | map(select(.current_version != .latest_version))),
		standalone: ($standalone
			| map(select(.latest_version == null or .current_version != .latest_version))),
		macos: $macos,
		brew_health: $brew_health
	}'
