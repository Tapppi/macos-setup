#!/usr/bin/env bash
# repo_context.sh — emit repo_context.json (references/schemas.md §Report Object;
# references/collection.md §Repo Freshness) for the two
# setup repos. Usage: repo_context.sh <macos-setup-root> [dotfiles-root]
# (dotfiles-root defaults to <macos-setup-root>/dotfiles)
#
# Read-only: fetches from origin (network) but never pulls/merges. Prints
# the assembled JSON to stdout — the session redirects it to
# {session_dir}/repo_context.json for assemble.py to read (references/collection.md).
set -euo pipefail

macos_setup_root="${1:-.}"
dotfiles_root="${2:-${macos_setup_root}/dotfiles}"

# repo_freshness <path> -> one JSON object {up_to_date,ahead,behind,recent_commits}
repo_freshness() {
	local path="$1"
	# git -C ... rev-parse --is-inside-work-tree (not a bare `-d .git` test)
	# so this also works for a submodule, where .git is a file pointing at
	# the real gitdir under the parent's .git/modules/, not a directory.
	if ! git -C "${path}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		jq -n '{up_to_date: null, ahead: null, behind: null, recent_commits: [], error: "not a git repo"}'
		return
	fi
	git -C "${path}" fetch origin --quiet 2>/dev/null || true

	local upstream="@{u}"
	git -C "${path}" rev-parse --abbrev-ref --symbolic-full-name "@{u}" >/dev/null 2>&1 || upstream="origin/master"

	local ahead behind
	ahead="$(git -C "${path}" rev-list --count "${upstream}..HEAD" 2>/dev/null || echo 0)"
	behind="$(git -C "${path}" rev-list --count "HEAD..${upstream}" 2>/dev/null || echo 0)"

	local commits_json
	commits_json="$(git -C "${path}" log --oneline -20 2>/dev/null | jq -R . | jq -s .)"

	jq -n --argjson ahead "${ahead}" --argjson behind "${behind}" --argjson commits "${commits_json}" '
		{ up_to_date: ($ahead == 0 and $behind == 0), ahead: $ahead, behind: $behind, recent_commits: $commits }'
}

jq -n --argjson macos_setup "$(repo_freshness "${macos_setup_root}")" \
	--argjson dotfiles "$(repo_freshness "${dotfiles_root}")" \
	'{ macos_setup: $macos_setup, dotfiles: $dotfiles }'
