#!/usr/bin/env bash
# tasks/skills.sh — Link globally-vendored agent skills into specific repos.
#
# Repo-level skill adoption. Instead of activating a skill for every project
# (a symlink in ~/.claude/skills), a repo opts in by carrying a gitignored
# `.local-skills.json` (or `.yml`/`.yaml`) manifest. This task scans
# ${HOME}/project for such manifests and, for each repo:
#   1. symlinks the named skills into the repo's `.claude/skills/`, and
#   2. provisions per-repo auth env from an optional `auth` block by rendering
#      a gitignored `mise.local.toml` [env]: non-secret `config` verbatim, plus
#      `token_files` that load a secret into an env var by `cat`-ing a local
#      0600 file (instant, so it never blocks the shell — unlike `op read`).
#      The secret files are written once from 1Password via the commands the
#      jira hint prints; the secret value never lands in mise.local.toml.
#
# Both the manifest and the generated/linked files are kept out of the target
# repo's git index (manifest via the global gitignore; symlinks and mise file
# via the repo's .git/info/exclude).
#
# Manifest schema:
#   {
#     "skills": ["jira", "bigquery-basics"],
#     "auth": {
#       "config":      { "JIRA_CONFIG_FILE": "{{config_root}}/.jira-config.yml",
#                        "JIRA_AUTH_TYPE": "bearer" },
#       "token_files": { "JIRA_API_TOKEN": "~/.config/project/jira_pat.txt" }
#     },
#     "jira": { "...": "...", "token_op_ref": "op://Vault/Item/field" }
#   }
#
# Idempotent: safe to re-run after editing any manifest. Run via:
#   ./setup.sh skills

# p1/p2/p3 are provided by setup.sh when sourced via ./setup.sh skills.

PROJECT_ROOT="${HOME}/project"
SKILLS_ROOT="${XDG_CONFIG_HOME:-${HOME}/.config}/agent-skills"

# Define Function =skills_warn=
skills_warn() {
	printf "\033[33m=> WARN: %s\033[0m\n" "${1}" >&2
}

# Define Function =skills_manifest_json= — emit a manifest as JSON on stdout
skills_manifest_json() {
	local manifest="${1}"
	case "${manifest}" in
		*.json) cat "${manifest}" ;;
		*.yml | *.yaml) yq -o=json '.' "${manifest}" ;;
		*) return 1 ;;
	esac
}

# Define Function =skills_resolve= — print the vendored dir for a skill name
# Matches a directory named <name> that contains a SKILL.md anywhere under
# SKILLS_ROOT. Warns (and returns 1) on zero or multiple matches.
skills_resolve() {
	local name="${1}"
	local -a matches=()
	local d
	while IFS= read -r d; do
		[[ -f "${d}/SKILL.md" ]] && matches+=("${d}")
	done < <(find "${SKILLS_ROOT}" -type d -name "${name}" 2>/dev/null)

	if [[ "${#matches[@]}" -eq 0 ]]; then
		skills_warn "skill '${name}' not found under ${SKILLS_ROOT} (with a SKILL.md)"
		return 1
	elif [[ "${#matches[@]}" -gt 1 ]]; then
		skills_warn "skill '${name}' is ambiguous: ${matches[*]}"
		return 1
	fi
	printf '%s\n' "${matches[0]}"
}

# Define Function =skills_link= — idempotently symlink one skill into a repo
skills_link() {
	local repo="${1}" name="${2}" target="${3}"
	local link_dir="${repo}/.claude/skills"
	local link="${link_dir}/${name}"

	mkdir -p "${link_dir}"
	if [[ -L "${link}" ]]; then
		if [[ "$(readlink "${link}")" == "${target}" ]]; then
			p3 "ok ${name}"
			return 0
		fi
		rm "${link}"
	elif [[ -e "${link}" ]]; then
		skills_warn "skip '${name}': ${link} exists and is not a symlink"
		return 1
	fi
	ln -s "${target}" "${link}" && p2 "linked ${name} -> ${target}"
}

# Define Function =skills_git_exclude= — add a pattern to a repo's
# .git/info/exclude if it is a git repo and the pattern is not already ignored
skills_git_exclude() {
	local repo="${1}" pattern="${2}"
	local exclude
	exclude="$(git -C "${repo}" rev-parse --git-path info/exclude 2>/dev/null)" || return 0
	# rev-parse prints a path relative to the repo; resolve against the repo
	[[ "${exclude}" = /* ]] || exclude="${repo}/${exclude}"
	mkdir -p "$(dirname "${exclude}")"
	touch "${exclude}"
	if ! grep -qxF "${pattern}" "${exclude}"; then
		printf '%s\n' "${pattern}" >> "${exclude}"
		p3 "git exclude += ${pattern}"
	fi
}

# Define Function =skills_toml_escape= — escape a value for a TOML basic string
skills_toml_escape() {
	local v="${1}"
	v="${v//\\/\\\\}"
	v="${v//\"/\\\"}"
	printf '%s' "${v}"
}

# Define Function =skills_render_auth= — render mise.local.toml [env] from the
# manifest's `auth` block:
#   * auth.config      — non-secret vars (e.g. JIRA_CONFIG_FILE, JIRA_AUTH_TYPE),
#                        written verbatim so mise tera vars like {{config_root}}
#                        still work.
#   * auth.token_files — map of ENV_VAR -> path to a local 0600 file holding a
#                        secret; rendered as a tera `exec` that `cat`s the file.
#
# mise evaluates [env] synchronously on every cd / shell prompt, so whatever
# runs here must be instant and non-blocking. `cat` of a local file is — unlike
# `op read`, which makes a network/unlock call and froze the shell in the
# original design. Only the PATH to the secret lives here, never the secret
# itself; a missing file yields an empty var (no error) thanks to `|| true`.
# The files are written once from 1Password — see skills_jira_hint.
skills_render_auth() {
	local repo="${1}" json="${2}"
	local out="${repo}/mise.local.toml"

	local config_entries token_files
	config_entries="$(printf '%s' "${json}" | jq -r '.auth.config // {} | to_entries[] | "\(.key)\t\(.value)"')"
	token_files="$(printf '%s' "${json}" | jq -r '.auth.token_files // {} | to_entries[] | "\(.key)\t\(.value)"')"
	if [[ -z "${config_entries}" && -z "${token_files}" ]]; then
		return 0
	fi

	local sq="'"
	{
		printf '# Managed by macos-setup tasks/skills.sh — generated from .local-skills.json\n'
		printf '# Do not edit by hand; re-run ./setup.sh skills after editing the manifest.\n'
		printf '# Non-secret config below. Token vars are loaded by cat-ing a local 0600\n'
		printf '# file (instant, never blocks the shell); write those files with the\n'
		printf '# commands ./setup.sh skills prints. The secret never lives in this file.\n'
		printf '[env]\n'

		local key value esc path
		while IFS=$'\t' read -r key value; do
			[[ -z "${key}" ]] && continue
			esc="$(skills_toml_escape "${value}")"
			printf '%s = "%s"\n' "${key}" "${esc}"
		done <<< "${config_entries}"

		while IFS=$'\t' read -r key value; do
			[[ -z "${key}" ]] && continue
			path="${value/#\~/${HOME}}"
			printf '%s = "{{ exec(command=%scat %s 2>/dev/null || true%s) }}"\n' \
				"${key}" "${sq}" "${path}" "${sq}"
		done <<< "${token_files}"
	} > "${out}"

	p2 "rendered ${out}"
	skills_git_exclude "${repo}" "/mise.local.toml"

	if command -v mise >/dev/null 2>&1; then
		mise trust "${out}" >/dev/null 2>&1 && p3 "mise trust ${out}"
	else
		skills_warn "mise not on PATH; run 'mise trust ${out}' once mise is installed"
	fi
}

# Define Function =skills_jira_hint= — if the manifest has a `jira` block, print
# the one-time setup steps (these need 1Password and hit the Jira server, so we
# print rather than run them):
#   1. Write the API token to its local 0600 file from 1Password (the same path
#      auth.token_files maps JIRA_API_TOKEN to). mise then injects JIRA_API_TOKEN
#      from it; jira-cli reads that env var (lookup order env -> config -> .netrc
#      -> keychain, so env wins).
#   2. `jira init` to write the server/board/project config.
# Each step is skipped when already done (token file present / config exists).
skills_jira_hint() {
	local repo="${1}" json="${2}"

	if [[ "$(printf '%s' "${json}" | jq -r '.jira // empty')" == "" ]]; then
		return 0
	fi

	local installation server login authtype project board token_ref token_file path
	installation="$(printf '%s' "${json}" | jq -r '.jira.installation // empty')"
	server="$(printf '%s' "${json}" | jq -r '.jira.server // empty')"
	login="$(printf '%s' "${json}" | jq -r '.jira.login // empty')"
	authtype="$(printf '%s' "${json}" | jq -r '.jira.auth_type // empty')"
	project="$(printf '%s' "${json}" | jq -r '.jira.project // empty')"
	board="$(printf '%s' "${json}" | jq -r '.jira.board // empty')"
	token_ref="$(printf '%s' "${json}" | jq -r '.jira.token_op_ref // empty')"
	token_file="$(printf '%s' "${json}" | jq -r '.auth.token_files.JIRA_API_TOKEN // .jira.token_file // empty')"

	# 1) API token → local 0600 file (sourced once from 1Password; mise loads it).
	if [[ -n "${token_file}" ]]; then
		path="${token_file/#\~/${HOME}}"
		if [[ ! -s "${path}" ]]; then
			local dir
			dir="$(dirname "${path}")"
			p2 "Jira API token file missing. Sign in to op if needed, then write it once:"
			p3 "eval \"\$(op signin)\"   # only if op is locked (e.g. over SSH)"
			if [[ -n "${token_ref}" ]]; then
				p3 "mkdir -p \"${dir}\" && chmod 700 \"${dir}\" && ( umask 077; op read \"${token_ref}\" > \"${path}\" )"
			else
				p3 "mkdir -p \"${dir}\" && chmod 700 \"${dir}\" && ( umask 077; printf %s '<api-token>' > \"${path}\" )  # set jira.token_op_ref to fetch from 1Password"
			fi
		else
			p3 "Jira API token file present: ${path}"
		fi
	fi

	# 2) jira-cli config (server/board/project) via one-time `jira init`.
	local cfg="${repo}/.jira-config.yml"
	if [[ -f "${cfg}" ]]; then
		p3 "jira-cli already initialised: ${cfg}"
		return 0
	fi

	local cmd="jira init -c \"${cfg}\""
	[[ -n "${installation}" ]] && cmd+=" --installation ${installation}"
	[[ -n "${server}" ]] && cmd+=" --server \"${server}\""
	[[ -n "${login}" ]] && cmd+=" --login \"${login}\""
	[[ -n "${authtype}" ]] && cmd+=" --auth-type ${authtype}"
	[[ -n "${project}" ]] && cmd+=" --project ${project}"
	[[ -n "${board}" ]] && cmd+=" --board \"${board}\""

	p2 "Initialise jira-cli (run once from inside ${repo}; uses JIRA_API_TOKEN from mise):"
	p3 "${cmd}"
}

# Define Function =skills_apply= — apply one manifest to its repo
skills_apply() {
	local manifest="${1}"
	local repo
	repo="$(cd "$(dirname "${manifest}")" && pwd)"

	local json
	if ! json="$(skills_manifest_json "${manifest}")"; then
		skills_warn "could not parse ${manifest}"
		return 1
	fi

	p1 "Repo ${repo}"

	local name target linked=0
	while IFS= read -r name; do
		[[ -z "${name}" ]] && continue
		if target="$(skills_resolve "${name}")"; then
			skills_link "${repo}" "${name}" "${target}" && linked=$((linked + 1))
		fi
	done < <(printf '%s' "${json}" | jq -r '.skills // [] | .[]')

	if [[ "${linked}" -gt 0 ]]; then
		skills_git_exclude "${repo}" "/.claude/skills/"
	fi

	skills_render_auth "${repo}" "${json}"
	skills_jira_hint "${repo}" "${json}"
}

# Define Function =skills= — scan PROJECT_ROOT and apply every manifest found
skills() {
	if [[ ! -d "${PROJECT_ROOT}" ]]; then
		skills_warn "project root ${PROJECT_ROOT} does not exist; nothing to do"
		return 0
	fi
	for cmd in jq yq find; do
		command -v "${cmd}" >/dev/null 2>&1 || {
			skills_warn "required tool '${cmd}' not found on PATH"
			return 1
		}
	done

	p1 "Scanning ${PROJECT_ROOT} for .local-skills manifests"

	local -a manifests=()
	local m
	while IFS= read -r m; do
		manifests+=("${m}")
	done < <(find "${PROJECT_ROOT}" \
		-type d \( -name .git -o -name node_modules \) -prune -o \
		-type f \( -name '.local-skills.json' -o -name '.local-skills.yml' -o -name '.local-skills.yaml' \) -print 2>/dev/null)

	if [[ "${#manifests[@]}" -eq 0 ]]; then
		p2 "No manifests found. Add a .local-skills.json to a repo and re-run."
		return 0
	fi

	for m in "${manifests[@]}"; do
		skills_apply "${m}"
	done

	p1 "Done. Linked skills for ${#manifests[@]} repo(s)."
}
