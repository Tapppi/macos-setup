#!/usr/bin/env bash
set -uo pipefail

# Define Function =commands=
# Prints "name<TAB>description" for each command of the dispatch `case` at the
# end of this file, read from its `name) # description` lines.
commands() {
	awk '/^case "\$\{1\}" in$/ { in_case = 1; next }
		in_case && /^esac$/ { exit }
		in_case && /^[a-z0-9_-]+\)/ {
			name = $0; sub(/\).*/, "", name)
			desc = $0; if (!sub(/^[^#]*# ?/, "", desc)) desc = ""
			printf "%s\t%s\n", name, desc
		}' "${BASH_SOURCE[0]}"
}

# Define Function =usage=
usage() {
	echo "Usage: ${0} <command>"
	echo "Commands:"
	commands | while IFS=$'\t' read -r name desc; do
		printf '  %-20s %s\n' "${name}" "${desc}"
	done
	echo "See README.md for more information."
}

# Usage and an unknown command are handled here, before anything asks for sudo.
case "${1:-}" in
"" | -h | --help | help)
	usage
	exit 0
	;;
esac
if ! commands | cut -f 1 | grep -qxF -- "${1}"; then
	echo "Unknown command: ${1}" >&2
	usage >&2
	exit 2
fi

# Bootstrap Homebrew on PATH. The dotfile-installed shell rc files only set
# brew paths in interactive shells (`[ -n "$PS1" ]`), so subscripts started
# from a non-interactive context would otherwise fail with `brew: command
# not found` and `sudo: tag: command not found`.
if [[ -x /opt/homebrew/bin/brew ]]; then
	eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# Require explicit trust for non-official taps (mirrors .exports). Non-interactive
# setup runs don't source the shell rc files, so set it here too. tasks/install.sh
# trusts the Brewfile's taps (brew trust --tap) before bundling.
export HOMEBREW_REQUIRE_TAP_TRUST=1

# Add mise shims directory so tools installed via `mise install` (uv, etc.)
# are on PATH. mise's standard `activate` hook only fires on PROMPT_COMMAND
# and so does nothing in a non-interactive script.
export PATH="${HOME}/.local/share/mise/shims:${PATH}"

# Add XDG_BIN_HOME. .path exports it for interactive shells only, so a setup run
# started non-interactively would not see tools that install themselves there —
# herdr's remote auto-install being the one that matters here.
export PATH="${HOME}/.local/bin:${PATH}"

# Ask for the administrator password upfront and keep it alive, except for
# `projects`, which needs no root: it only enables local-scope plugins and
# renders workspace files, and the tool-update-review skill runs it unattended.
if [[ "${1}" != projects ]]; then
	sudo -v

	# Keep-alive: update existing `sudo` time stamp until has finished
	while true; do
		sudo -n true
		sleep 120
		kill -0 "$$" || exit
	done 2>/dev/null &
fi

# Define Function =ask=
ask() {
	osascript - "${1}" "${2}" "${3}" <<EOF 2>/dev/null
    on run { _title, _action, _default }
      tell app "System Events" to return text returned of (display dialog _title with title _title buttons { "Cancel", _action } default answer _default)
    end run
EOF
}

# Define Function =ask2=
ask2() {
	osascript - "$1" "$2" "$3" "$4" "$5" "$6" <<EOF 2>/dev/null
on run { _text, _title, _cancel, _action, _default, _hidden }
  tell app "Terminal" to return text returned of (display dialog _text with title _title buttons { _cancel, _action } cancel button _cancel default button _action default answer _default hidden answer _hidden)
end run
EOF
}

# Define Function =p=
p1() {
	printf "\n\033[1m\033[34m==> %s\033[0m\n" "${1}"
}

# Define Function =p2= (darker blue version)
p2() {
	printf "\033[34m=> %s\033[0m\n" "${1}"
}

# Define Function =p3= (gray version)
p3() {
	printf "\033[90m=> %s\033[0m\n" "${1}"
}

# Define Function =run=
run() {
	osascript - "${1}" "${2}" "${3}" <<EOF 2>/dev/null
    on run { _title, _cancel, _action }
      tell app "Terminal" to return button returned of (display dialog _title with title _title buttons { _cancel, _action } cancel button 1 default button 2 giving up after 5)
    end run
EOF
}

# Dispatch. Each command is one `name)` line whose trailing comment is its usage
# text: commands() reads them back from here, so the usage list and the check of
# the argument above cannot drift from this case.
case "${1}" in
init) # System initialisation: hostname, permissions, updates, SSH, new account
	. tasks/init.sh
	init
	;;
new_account) # Create a new user account
	. tasks/init.sh
	new_account
	;;
clean_account) # Clean up an account
	. tasks/init.sh
	clean_account
	;;
init_ssh_local) # Set up a local SSH key
	. tasks/init.sh
	init_ssh_local
	;;
init_ssh_1password) # Set up SSH through the 1Password agent
	. tasks/init.sh
	init_ssh_1password
	;;
install) # Install all software and configure the agent harnesses
	. tasks/install.sh
	install
	;;
dotfiles) # Sync dotfiles, then re-assert the skill link and herdr integrations
	. tasks/install.sh
	# Gated on the sync succeeding: bootstrap.sh has just overwritten the tracked
	# agent config files, dropping the keys herdr writes into them, and putting
	# them back is what keeps a dotfiles-only sync from silently disabling the
	# integration. The user-level skill link that bootstrap.sh does not write is
	# re-asserted here too. But re-asserting onto a half-synced tree is worse
	# than not re-asserting at all, so a failed sync stops here and says why.
	dotfiles_status=0
	install_dotfiles || dotfiles_status=$?
	if [[ "${dotfiles_status}" -eq 0 ]]; then
		install_claude_user_skills
		install_herdr_integrations
	else
		p1 "Skipping skill links and herdr integrations — dotfiles sync failed (exit ${dotfiles_status})."
		p3 "Fix the sync, then re-run './setup.sh dotfiles'."
		exit "${dotfiles_status}"
	fi
	;;
herdr) # herdr's agent-state integrations only (also part of install)
	. tasks/install.sh
	install_herdr_integrations
	;;
codex) # Codex's plugins, ikeh roles, skill links and context7 only (also part of install)
	. tasks/install.sh
	install_codex
	;;
pi) # Pi's user-level skills and MCP servers only (also part of install)
	. tasks/install.sh
	install_pi
	;;
opencode) # Check OpenCode's skill paths and provider logins only (also part of install)
	. tasks/install.sh
	install_opencode
	;;
context7) # Re-assert context7 for Claude Code and Codex only (also part of install)
	. tasks/install.sh
	install_context7
	;;
agent-skills) # The document skills' venv, markitdown and npm packages only (also part of install)
	. tasks/install.sh
	# Runs every step and reports the combined status, so a failing step exits non-zero.
	agent_skills_status=0
	install_agent_skills || agent_skills_status=$?
	exit "${agent_skills_status}"
	;;
config) # [name...] App configuration: all config_* and custom_* steps, or only those named
	. tasks/config.sh
	shift
	config "$@"
	;;
macos) # macOS system defaults (restarts Finder, Dock and ControlCenter)
	./tasks/macos.sh
	;;
projects) # Per-project plugins and env from .tapppi-project manifests (no sudo)
	. tasks/projects.sh
	projects
	;;
esac
