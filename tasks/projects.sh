#!/usr/bin/env bash
# tasks/projects.sh — Set up per-project tooling and agent skills from a manifest.
#
# A "workspace" directory (e.g. ~/project/acme/) carries a gitignored
# `.tapppi-project.{json,yml,yaml}` manifest. This task scans ${HOME}/project
# for such manifests and, per workspace:
#   1. symlinks each named skill into the corresponding repo's `.claude/skills/`.
#      Skills are per-repo because Claude Code only discovers skills up to a
#      repo's git root, so a workspace-level link would not be seen inside a repo.
#   2. enables each named plugin for the repo via `claude plugin install
#      --scope local`, which records it in that repo's gitignored
#      `.claude/settings.local.json` (enabledPlugins). This covers both
#      third-party marketplace plugins (e.g. `frontend-design@claude-plugins-
#      official`) and our own skills that have been wrapped as plugins (e.g.
#      `browser@tapppi-skills`, see agent-skills/tapppi/.claude-plugin/) —
#      unlike raw skills, Claude Code has no `enabledSkills` toggle, so a
#      skill only gets this per-project scoping if it's packaged as a plugin.
#      Any local marketplace under SKILLS_ROOT (one with a
#      `.claude-plugin/marketplace.json`) is auto-registered so its plugins
#      resolve by name.
#   3. provisions a shared per-workspace environment: renders a `mise.local.toml`
#      in the workspace dir whose [env] loads a local 0600 dotenv file via mise's
#      `_.file`. mise walks up the directory tree across git boundaries, so every
#      repo under the workspace inherits the env.
#   4. for a `jira` block, prints the one-time commands to write that dotenv file
#      from 1Password and run `jira init` (it does not run them).
#
# Why a dotenv file and not `op read` in mise: mise evaluates [env] on every
# cd/prompt, so a blocking `op read` would freeze the shell. mise just reads a
# local file (instant). The secret is written once from 1Password into a 0600
# file that lives in the workspace dir, so it works the same locally and over
# SSH (where the login Keychain is locked); keep that dir out of any git repo.
#
# Manifest schema (in a workspace dir, e.g. ~/project/acme/.tapppi-project.json):
#   {
#     "skills": {                         # repo path (rel. to workspace) -> skills
#       "service-a": ["jira", "gke-basics"],
#       "service-b": ["jira"]
#     },
#     "plugins": {                        # repo path (rel. to workspace) -> plugin@marketplace
#       "service-a": ["frontend-design@claude-plugins-official", "browser@tapppi-skills"]
#     },
#     "jira": {
#       "installation": "local",
#       "server": "https://jira.example.com",
#       "login": "me@example.com",
#       "auth_type": "bearer",
#       "project": "PROJ",
#       "board": "",
#       "token_op_ref": "op://Vault/Item/field",
#       "env_file": "project.env"         # rel. to workspace; mise loads it for all repos
#     }
#   }
#
# Idempotent: safe to re-run after editing any manifest. Run via:
#   ./setup.sh projects

# p1/p2/p3 are provided by setup.sh when sourced via ./setup.sh projects.

PROJECT_ROOT="${HOME}/project"
SKILLS_ROOT="${XDG_CONFIG_HOME:-${HOME}/.config}/agent-skills"

# Define Function =projects_warn=
projects_warn() {
	printf "\033[33m=> WARN: %s\033[0m\n" "${1}" >&2
}

# Define Function =projects_manifest_json= — emit a manifest as JSON on stdout
projects_manifest_json() {
	local manifest="${1}"
	case "${manifest}" in
		*.json) cat "${manifest}" ;;
		*.yml | *.yaml) yq -o=json '.' "${manifest}" ;;
		*) return 1 ;;
	esac
}

# Define Function =projects_resolve_path= — resolve a manifest path value to an
# absolute path: ~ -> $HOME, and relative paths against the given base dir.
projects_resolve_path() {
	local base="${1}" path="${2}"
	path="${path/#\~/${HOME}}"
	[[ "${path}" = /* ]] || path="${base}/${path}"
	printf '%s\n' "${path}"
}

# Define Function =projects_resolve_skill= — print the vendored dir for a skill
# name: a directory named <name> containing a SKILL.md anywhere under SKILLS_ROOT.
projects_resolve_skill() {
	local name="${1}"
	local -a matches=()
	local d
	while IFS= read -r d; do
		[[ -f "${d}/SKILL.md" ]] && matches+=("${d}")
	done < <(find "${SKILLS_ROOT}" -type d -name "${name}" 2>/dev/null)

	if [[ "${#matches[@]}" -eq 0 ]]; then
		projects_warn "skill '${name}' not found under ${SKILLS_ROOT} (with a SKILL.md)"
		return 1
	elif [[ "${#matches[@]}" -gt 1 ]]; then
		projects_warn "skill '${name}' is ambiguous: ${matches[*]}"
		return 1
	fi
	printf '%s\n' "${matches[0]}"
}

# Define Function =projects_link_skill= — idempotently symlink one skill into a repo
projects_link_skill() {
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
		projects_warn "skip '${name}': ${link} exists and is not a symlink"
		return 1
	fi
	ln -s "${target}" "${link}" && p2 "linked ${name} -> ${target}"
}

# Define Function =projects_ensure_marketplaces= — idempotently register every
# local marketplace found under SKILLS_ROOT (a dir with a
# `.claude-plugin/marketplace.json`, e.g. agent-skills/ itself, or a vendor
# subdir within it) so `claude plugin install <plugin>@<marketplace>` can
# resolve it. No-op if `claude` is not on PATH (plugin scoping is best-effort,
# not a hard requirement).
projects_ensure_marketplaces() {
	command -v claude >/dev/null 2>&1 || return 0

	local manifest vendor_dir
	while IFS= read -r manifest; do
		[[ -z "${manifest}" ]] && continue
		vendor_dir="$(dirname "$(dirname "${manifest}")")"
		if claude plugin marketplace add "${vendor_dir}" >/dev/null 2>&1; then
			p3 "marketplace ok: ${vendor_dir}"
		else
			projects_warn "could not register marketplace at ${vendor_dir}"
		fi
	done < <(find "${SKILLS_ROOT}" -mindepth 2 -maxdepth 4 \
		-path '*/.claude-plugin/marketplace.json' 2>/dev/null)
}

# Define Function =projects_enable_plugin= — idempotently enable one
# `plugin@marketplace` for a repo at local scope (writes enabledPlugins into
# the repo's gitignored .claude/settings.local.json).
projects_enable_plugin() {
	local repo="${1}" name="${2}"
	if (cd "${repo}" && claude plugin install "${name}" --scope local >/dev/null 2>&1); then
		p3 "ok ${name}"
		return 0
	fi
	projects_warn "could not enable plugin '${name}' in ${repo}"
	return 1
}

# Define Function =projects_git_exclude= — add a pattern to a repo's
# .git/info/exclude if it is a git repo and the pattern is not already ignored
projects_git_exclude() {
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

# Define Function =projects_render_env= — render <workspace>/mise.local.toml so
# mise loads the workspace dotenv file (jira.env_file) via `_.file`. mise reads
# it for every repo under the workspace (it walks up across git boundaries), so
# the env is shared. File reads are instant; nothing here blocks the shell.
projects_render_env() {
	local workspace="${1}" json="${2}"

	local env_file
	env_file="$(printf '%s' "${json}" | jq -r '.jira.env_file // empty')"
	[[ -z "${env_file}" ]] && return 0

	local abs out
	abs="$(projects_resolve_path "${workspace}" "${env_file}")"
	out="${workspace}/mise.local.toml"

	{
		printf '# Managed by macos-setup tasks/projects.sh — generated from .tapppi-project.json\n'
		printf '# Do not edit by hand; re-run ./setup.sh projects after editing the manifest.\n'
		printf '# Loads the workspace dotenv file (incl. secrets) for every repo below it.\n'
		printf '# That file is a local 0600 file written from 1Password — see the command\n'
		printf '# ./setup.sh projects prints. No secret value is stored in this file.\n'
		printf '[env]\n'
		printf '_.file = "%s"\n' "${abs}"
	} > "${out}"

	p2 "rendered ${out}"
	projects_git_exclude "${workspace}" "/mise.local.toml"

	if command -v mise >/dev/null 2>&1; then
		mise trust "${out}" >/dev/null 2>&1 && p3 "mise trust ${out}"
	else
		projects_warn "mise not on PATH; run 'mise trust ${out}' once mise is installed"
	fi
}

# Define Function =projects_jira_hint= — if the manifest has a `jira` block,
# print the one-time setup commands (they need 1Password and reach the Jira
# server, so we print rather than run them):
#   1. Write the workspace dotenv file from 1Password: JIRA_CONFIG_FILE /
#      JIRA_AUTH_TYPE (non-secret) and JIRA_API_TOKEN (op read), all in one 0600
#      file that mise loads. jira-cli reads JIRA_API_TOKEN from the env.
#   2. `jira init` to write the server/board/project config.
# Skipped where already done (dotenv present / config exists).
# shellcheck disable=SC2016  # printed commands intentionally contain literal $(...)
projects_jira_hint() {
	local workspace="${1}" json="${2}"

	[[ "$(printf '%s' "${json}" | jq -r '.jira // empty')" == "" ]] && return 0

	local installation server login authtype project board token_ref env_file
	installation="$(printf '%s' "${json}" | jq -r '.jira.installation // empty')"
	server="$(printf '%s' "${json}" | jq -r '.jira.server // empty')"
	login="$(printf '%s' "${json}" | jq -r '.jira.login // empty')"
	authtype="$(printf '%s' "${json}" | jq -r '.jira.auth_type // empty')"
	project="$(printf '%s' "${json}" | jq -r '.jira.project // empty')"
	board="$(printf '%s' "${json}" | jq -r '.jira.board // empty')"
	token_ref="$(printf '%s' "${json}" | jq -r '.jira.token_op_ref // empty')"
	env_file="$(printf '%s' "${json}" | jq -r '.jira.env_file // empty')"

	local cfg
	cfg="${workspace}/.jira-config.yml"

	# 1) Workspace dotenv file (config + token) written once from 1Password.
	if [[ -n "${env_file}" ]]; then
		local abs
		abs="$(projects_resolve_path "${workspace}" "${env_file}")"
		if [[ ! -s "${abs}" ]]; then
			p2 "Write the workspace env file once (sign in to op first if locked):"
			printf 'eval "$(op signin)"   # only if op is locked (e.g. over SSH)\n'
			printf '( umask 077; cat > %s <<EOF\n' "${abs}"
			printf 'JIRA_CONFIG_FILE=%s\n' "${cfg}"
			[[ -n "${authtype}" ]] && printf 'JIRA_AUTH_TYPE=%s\n' "${authtype}"
			if [[ -n "${token_ref}" ]]; then
				printf 'JIRA_API_TOKEN=$(op read "%s")\n' "${token_ref}"
			else
				printf 'JIRA_API_TOKEN=<api-token>   # set jira.token_op_ref to fetch from 1Password\n'
			fi
			printf 'EOF\n)\n'
		else
			p3 "workspace env file present: ${abs}"
		fi
	fi

	# 2) jira-cli config (server/board/project) via one-time `jira init`.
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

	p2 "Initialise jira-cli (run once; uses JIRA_API_TOKEN from mise):"
	printf '%s\n' "${cmd}"
}

# Define Function =projects_apply= — apply one workspace manifest
projects_apply() {
	local manifest="${1}"
	local workspace
	workspace="$(cd "$(dirname "${manifest}")" && pwd)"

	local json
	if ! json="$(projects_manifest_json "${manifest}")"; then
		projects_warn "could not parse ${manifest}"
		return 1
	fi

	p1 "Workspace ${workspace}"

	# Per-repo skill symlinks. `skills` maps a repo path (relative to the
	# workspace, or absolute) to its list of skill names.
	local repo_key repo name target linked
	while IFS= read -r repo_key; do
		[[ -z "${repo_key}" ]] && continue
		repo="$(projects_resolve_path "${workspace}" "${repo_key}")"
		if [[ ! -d "${repo}" ]]; then
			projects_warn "repo '${repo_key}' not found at ${repo}"
			continue
		fi
		p2 "Repo ${repo}"
		linked=0
		while IFS= read -r name; do
			[[ -z "${name}" ]] && continue
			if target="$(projects_resolve_skill "${name}")"; then
				projects_link_skill "${repo}" "${name}" "${target}" && linked=$((linked + 1))
			fi
		done < <(printf '%s' "${json}" | jq -r --arg r "${repo_key}" '.skills[$r] // [] | .[]')
		[[ "${linked}" -gt 0 ]] && projects_git_exclude "${repo}" "/.claude/skills/"
	done < <(printf '%s' "${json}" | jq -r '.skills // {} | keys[]')

	# Per-repo plugin enablement. `plugins` maps a repo path to a list of
	# `plugin@marketplace` names, enabled at local scope (see
	# projects_enable_plugin). Requires `claude` on PATH; skipped otherwise.
	if command -v claude >/dev/null 2>&1; then
		local plugin_name enabled
		while IFS= read -r repo_key; do
			[[ -z "${repo_key}" ]] && continue
			repo="$(projects_resolve_path "${workspace}" "${repo_key}")"
			if [[ ! -d "${repo}" ]]; then
				projects_warn "repo '${repo_key}' not found at ${repo}"
				continue
			fi
			p2 "Repo ${repo}"
			enabled=0
			while IFS= read -r plugin_name; do
				[[ -z "${plugin_name}" ]] && continue
				projects_enable_plugin "${repo}" "${plugin_name}" && enabled=$((enabled + 1))
			done < <(printf '%s' "${json}" | jq -r --arg r "${repo_key}" '.plugins[$r] // [] | .[]')
			[[ "${enabled}" -gt 0 ]] && projects_git_exclude "${repo}" "/.claude/settings.local.json"
		done < <(printf '%s' "${json}" | jq -r '.plugins // {} | keys[]')
	fi

	projects_render_env "${workspace}" "${json}"
	projects_jira_hint "${workspace}" "${json}"
}

# Define Function =projects= — scan PROJECT_ROOT and apply every manifest found
projects() {
	if [[ ! -d "${PROJECT_ROOT}" ]]; then
		projects_warn "project root ${PROJECT_ROOT} does not exist; nothing to do"
		return 0
	fi
	for cmd in jq yq find; do
		command -v "${cmd}" >/dev/null 2>&1 || {
			projects_warn "required tool '${cmd}' not found on PATH"
			return 1
		}
	done

	projects_ensure_marketplaces

	p1 "Scanning ${PROJECT_ROOT} for .tapppi-project manifests"

	local -a manifests=()
	local m
	while IFS= read -r m; do
		manifests+=("${m}")
	done < <(find "${PROJECT_ROOT}" \
		-type d \( -name .git -o -name node_modules \) -prune -o \
		-type f \( -name '.tapppi-project.json' -o -name '.tapppi-project.yml' -o -name '.tapppi-project.yaml' \) -print 2>/dev/null)

	if [[ "${#manifests[@]}" -eq 0 ]]; then
		p2 "No manifests found. Add a .tapppi-project.json to a workspace and re-run."
		return 0
	fi

	for m in "${manifests[@]}"; do
		projects_apply "${m}"
	done

	p1 "Done. Applied ${#manifests[@]} workspace manifest(s)."
}
