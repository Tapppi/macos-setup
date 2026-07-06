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

jq -n \
	--arg arch "${arch}" --arg os "${os_name}" --arg host "${hostname}" \
	--argjson brew "${brew_json}" --argjson pinned "${pinned_json}" \
	--argjson mise "${mise_json}" --argjson standalone "${standalone_json}" \
	--argjson macos "${macos_json}" '
	{
		machine: { arch: $arch, os: $os, hostname: $host },
		brew: (($brew + $pinned) | unique_by(.id)
			| map(select(.current_version != .latest_version))),
		mise: ($mise | map(select(.current_version != .latest_version))),
		standalone: ($standalone
			| map(select(.latest_version == null or .current_version != .latest_version))),
		macos: $macos
	}'
