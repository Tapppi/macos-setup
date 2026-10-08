#!/usr/bin/env bash
set -uo pipefail

# Define Function =install=

install() {
	local dotfiles_ok=0

	install_macos_sw
	link_terraform_to_tofu
	install_dotfiles || dotfiles_ok=$?
	install_mise_runtimes
	# After mise runtimes: podman-compose is installed via the mise-managed uv
	install_podman_intel
	install_powershell_modules
	install_agent_skills_venv

	# These re-assert tool-owned config into files bootstrap.sh just overwrote
	# (see "Tool-owned config is re-asserted, not vendored"). That ordering only
	# holds if the sync actually completed — re-asserting onto a half-synced tree
	# produces a state that looks configured and is not. Skip and say so instead.
	if [[ "${dotfiles_ok}" -ne 0 ]]; then
		p1 "Skipping Claude Code, Codex, Cursor and herdr setup — dotfiles sync failed."
		p3 "Fix the sync, then run './setup.sh dotfiles' to re-assert the integrations,"
		p3 "and './setup.sh codex' for Codex (or './setup.sh install' for the rest)."
		return "${dotfiles_ok}"
	fi

	install_claude_code
	# After install_claude_code, whose ctx7 run provides the skill install_codex links.
	install_codex
	install_cursor_agent
	install_herdr_integrations
}

# Define Function =link_terraform_to_tofu=
# Terraform's CLI is BUSL-licensed and not in homebrew-core, so the Brewfile
# installs OpenTofu (tofu) instead. Symlink `terraform` -> `tofu` in the user
# bin dir (on PATH ahead of brew) so scripts/CI that invoke `terraform` keep
# working. Must be a real symlink, not a shell alias, so non-interactive
# scripts resolve it too. Idempotent.
link_terraform_to_tofu() {
	local tofu_bin link
	tofu_bin="$(brew --prefix)/bin/tofu"
	link="${XDG_BIN_HOME:-${HOME}/.local/bin}/terraform"
	if [[ ! -x "${tofu_bin}" ]]; then
		p3 "tofu not installed, skipping terraform->tofu symlink"
		return 0
	fi
	if [[ "$(readlink "${link}" 2>/dev/null)" == "${tofu_bin}" ]]; then
		p3 "terraform already symlinked to tofu"
		return 0
	fi
	p2 "Symlink terraform -> tofu (${tofu_bin})..."
	mkdir -p "$(dirname "${link}")"
	ln -sf "${tofu_bin}" "${link}"
}

# Define Function =install_podman_intel=
# homebrew-core only carries podman 6.x, which dropped Intel-mac support and
# never shipped the 5.8.3/5.8.4 security fixes over 5.8.2 — so on Intel the
# newest 5.x comes from the official upstream installer pkg (signed, bundles
# the gvproxy/vfkit helpers, adds /opt/podman/bin to PATH via /etc/paths.d).
# The newest 5.x tag and its pkg checksum are resolved from GitHub at run
# time, so 5.x patch releases are followed without editing this file.
# brew's podman-compose formula depends on the podman formula (which would
# pull 6.x back in on any upgrade), so podman-compose moves to a uv tool
# install. Intel stays on the default applehv/vfkit VM backend — libkrun/
# krunkit is Apple-Silicon-only; existing 5.x machines keep working.
install_podman_intel() {
	if [[ "$(uname -m)" != "x86_64" ]]; then
		return 0
	fi

	local tag version pkg_sha256
	tag="$(curl -fsSL --max-time 15 \
		'https://api.github.com/repos/containers/podman/releases?per_page=100' |
		jq -r '.[].tag_name' | grep -E '^v5\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
	if [[ -z "${tag}" ]]; then
		p3 "ERROR: could not resolve newest podman 5.x release from GitHub"
		return 1
	fi
	version="${tag#v}"
	p2 "Install podman ${version} from upstream pkg (Intel)..."

	if command -v podman >/dev/null 2>&1 &&
		[[ "$(podman --version 2>/dev/null | awk '{print $3}')" == "${version}" ]]; then
		p3 "podman ${version} already installed, skipping"
	else
		pkg_sha256="$(curl -fsSL --max-time 15 \
			"https://github.com/containers/podman/releases/download/${tag}/shasums" |
			awk '/ podman-installer-macos-amd64\.pkg$/{print $1}')"
		if [[ -z "${pkg_sha256}" ]]; then
			p3 "ERROR: could not fetch shasums for podman ${version}"
			return 1
		fi

		# Replace the brew formulae if present. podman-compose goes first so
		# podman itself uninstalls cleanly (no --ignore-dependencies needed).
		if brew list podman >/dev/null 2>&1 || brew list podman-compose >/dev/null 2>&1; then
			p3 "Removing brew podman formulae (superseded by upstream pkg + uv)..."
			podman machine stop >/dev/null 2>&1
			brew list podman-compose >/dev/null 2>&1 && brew uninstall podman-compose
			brew unpin podman >/dev/null 2>&1
			brew list podman >/dev/null 2>&1 && brew uninstall podman
		fi

		local pkg="/tmp/podman-installer-macos-amd64-${version}.pkg"
		if ! curl --fail --location --silent --show-error --output "${pkg}" \
			"https://github.com/containers/podman/releases/download/${tag}/podman-installer-macos-amd64.pkg"; then
			p3 "ERROR: download failed for podman ${version} pkg"
			return 1
		fi
		if ! printf '%s  %s\n' "${pkg_sha256}" "${pkg}" | shasum -a 256 -c - >/dev/null 2>&1; then
			p3 "ERROR: checksum mismatch for ${pkg}; aborting podman install"
			rm -f "${pkg}"
			return 1
		fi
		if ! sudo installer -pkg "${pkg}" -target /; then
			rm -f "${pkg}"
			return 1
		fi
		rm -f "${pkg}"
		p3 "Installed $(/opt/podman/bin/podman --version 2>/dev/null || echo 'podman (version check failed)')"
	fi

	# podman-compose via uv (pure-python CLI; brew formula is entangled with 6.x).
	# uv is mise-managed, so this task must run after install_mise_runtimes.
	if ! command -v podman-compose >/dev/null 2>&1; then
		if ! command -v uv >/dev/null 2>&1; then
			p3 "ERROR: uv not found (mise runtimes not installed yet?) — re-run this task after install_mise_runtimes to get podman-compose"
			return 1
		fi
		uv tool install podman-compose
	fi
}

# Define Function =install_xcode=
install_xcode() {
	p2 "Check xcode installation..."

	# Skip inside nix shell — nix injects /nix/store paths that confuse xcode-select
	if [[ "${PATH}" == *"/nix/store/"* ]]; then
		p3 "Inside nix shell, skipping xcode-select"
		return 0
	fi

	x="$(find '/Applications' -maxdepth 1 -regex '.*/Xcode[^ ]*.app' -print -quit)"
	if test -n "${x}"; then
		# Set the correct path for xcode-select (needs to point to Contents/Developer)
		xcode_dev_path="${x}/Contents/Developer"

		# Only change xcode-select if it's not already set to the correct path
		current_xcode_path=$(xcode-select -p 2>/dev/null || echo "")
		if [[ "${current_xcode_path}" != "${xcode_dev_path}" ]]; then
			p3 "Switch xcode from ${current_xcode_path} to ${xcode_dev_path}"
			sudo xcode-select -s "${xcode_dev_path}"
		fi

		# Only run first launch setup if it hasn't been completed
		if ! xcodebuild -checkFirstLaunchStatus 2>/dev/null; then
			p3 "Install xcode utils and accept license..."
			sudo xcodebuild -runFirstLaunch
		fi
	fi
	p3 "XCode installation checked!"
}

# Install macOS Software
install_macos_sw() {
	p1 "Installing macOS Software..."

	p2 "Check for system software updates..."
	local to_update
	to_update="$(softwareupdate --list)"
	if echo "${to_update}" | grep -q "Command Line Tools for Xcode"; then
		p1 "Updates for Xcode Command Line Tools found. Install them with 'softwareupdate --install' to prevent Xcode update from hanging"
	elif echo "${to_update}" | grep -q "Label:"; then
		p2 "Software updates found, install them with 'softwareupdate --install [-r]'"
		echo "${to_update}" | grep -A1 "Label:"
	fi

	install_xcode

	install_paths

	install_brew

	install_xcode

	BREW_PREFIX="$(brew --prefix)"

	# Fix fish permissions for brew
	# if [ -d "${BREW_PREFIX}/share/fish" ]; then
	# 	sudo chown -R "$(whoami):admin" "${BREW_PREFIX}/share/fish"
	# fi

	# Set brew installed bash 5 as default shell
	if ! grep -F -q "${BREW_PREFIX}/bin/bash" /etc/shells; then
		echo "${BREW_PREFIX}/bin/bash" | sudo tee -a /etc/shells
	fi
	# Compare against the Directory Services login shell, not $SHELL: $SHELL
	# reflects the shell at login time and stays stale until the next login, so
	# using it would re-run chsh (and re-prompt for the password) on every
	# install even when the login shell is already correct.
	local login_shell
	login_shell="$(dscl . -read "/Users/$(id -un)" UserShell 2>/dev/null | awk '{print $2}')"
	if [ "${login_shell}" != "${BREW_PREFIX}/bin/bash" ]; then
		p3 "Set login shell to ${BREW_PREFIX}/bin/bash"
		chsh -s "${BREW_PREFIX}/bin/bash"
	else
		p3 "Login shell already ${BREW_PREFIX}/bin/bash"
	fi

	install_links
	install_amphetamine_enhancer
}

# Add Homebrew sbin to Default Path
install_paths() {
	p2 "Ensure homebrew in path..."
	local brew_sbin="/usr/local/sbin"
	if [ "$(uname -m)" = "arm64" ]; then
		brew_sbin="/opt/homebrew/sbin"
	fi
	if ! grep -Fq "${brew_sbin}" /etc/paths; then
		p2 "Add ${brew_sbin} to /etc/paths"
		echo "${brew_sbin}" | sudo tee -a /etc/paths >/dev/null
	fi
}

# Install Software with Homebrew Package Manager
# brew commands invalidate sudo timestamp in order to prevent builds from using sudo
# if there is a need for sudo after brew installation, we'll just have to re-enter password
# Define Function =trust_brew_taps=
# Explicitly trust the non-official taps declared in the given Brewfile so they
# load under HOMEBREW_REQUIRE_TAP_TRUST=1 (set in setup.sh and ~/.config/bash/.exports).
# Derived from the Brewfile itself so the trust list never drifts from the manifest.
trust_brew_taps() {
	local brewfile="${1}"
	local tap
	while IFS= read -r tap; do
		[[ -z "${tap}" ]] && continue
		p3 "Trusting tap ${tap}..."
		brew trust --tap "${tap}" >/dev/null 2>&1 || p3 "  could not trust ${tap}"
	done < <(grep -E '^tap "' "${brewfile}" | sed -E 's/^tap "([^"]+)".*/\1/')
}

# Define Function =_kill_tree=
# Recursively SIGTERM a process and all its descendants (children first). Used
# by run_with_timeout so a timed-out `brew` AND any grandchild it spawned (e.g.
# a cask's `op completion` wedged on a Gatekeeper assessment) are torn down —
# killing brew alone would orphan the grandchild.
_kill_tree() {
	local pid="${1}" child
	while IFS= read -r child; do
		[[ -n "${child}" ]] && _kill_tree "${child}"
	done < <(pgrep -P "${pid}" 2>/dev/null)
	kill -TERM "${pid}" 2>/dev/null
}

# Define Function =run_with_timeout=
# Run a command with a wall-clock timeout; return its exit code, or 124 if it
# timed out. Deliberately avoids GNU coreutils' `timeout` because on a fresh
# machine the Brewfile that installs coreutils is the very thing we wrap. Polls
# (rather than SIGALRM) so it can tear down the whole process tree on timeout.
run_with_timeout() {
	local secs="${1}"
	shift
	"${@}" &
	local pid=$! waited=0
	while kill -0 "${pid}" 2>/dev/null; do
		if [[ "${waited}" -ge "${secs}" ]]; then
			p1 "Timed out after ${secs}s; terminating: ${*}"
			_kill_tree "${pid}"
			sleep 5
			kill -KILL "${pid}" 2>/dev/null
			wait "${pid}" 2>/dev/null
			return 124
		fi
		sleep 5
		waited=$((waited + 5))
	done
	wait "${pid}"
}

install_brew() {
	p2 "Installing and/or configuring brew"
	if ! command -v brew >/dev/null 2>&1; then
		p2 "Installing brew..."
		/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

		# Ensure brew is on PATH for the rest of this script
		if [ -x "/opt/homebrew/bin/brew" ]; then
			eval "$(/opt/homebrew/bin/brew shellenv)"
		elif [ -x "/usr/local/bin/brew" ]; then
			eval "$(/usr/local/bin/brew shellenv)"
		fi
	else
		p3 "Brew already installed"
	fi

	p3 "Brew update and doctor..."
	brew analytics off
	brew update
	brew doctor

	p3 "Install Brewfile..."
	local brewfile="Brewfile"
	[[ "$(uname -m)" != "arm64" ]] && brewfile="intel.Brewfile"
	# Trust declared taps before bundling so they load under
	# HOMEBREW_REQUIRE_TAP_TRUST=1 instead of being refused.
	trust_brew_taps "${brewfile}"

	# op runs its binary at install to build completions. A quarantined
	# binary's first exec needs Gatekeeper's consent dialog, which renders on the
	# local console — on a headless/remote box nobody can click it, so the exec
	# stays suspended forever (it's the dialog, not the network). Cap the bundle
	# so that hang can't wedge setup indefinitely (override BREW_BUNDLE_TIMEOUT).
	run_with_timeout "${BREW_BUNDLE_TIMEOUT:-5400}" brew bundle --file="${brewfile}" ||
		p1 "brew bundle exited non-zero (timeout or package failure); re-run './setup.sh install' after resolving."

	# Homebrew 6.0 dropped --no-quarantine, so casks are always quarantined.
	# Clearing it post-install kills the first-launch "unverified app" popup for
	# GUI apps (and re-assessment for the op CLI). It can NOT stop the
	# quarantine popups/hangs during install itself (op above) — those fire
	# mid-bundle, before this runs, and have no fix now (accept them, or move off).
	# codex used to be on this list; it now comes from tapppi/systems (nix), where
	# store binaries carry no quarantine.
	xattr -dr com.apple.quarantine /Applications/*.app 2>/dev/null || true
	clear_cask_quarantine 1password-cli

	# Bust cached kubectl completions so they regenerate on next shell startup
	# (completions are lazily cached in .bash_profile; stale after a kubectl upgrade)
	local kubectl_comp
	kubectl_comp="$(brew --prefix)/share/bash-completion/completions/kubectl"
	if [[ -f "${kubectl_comp}" ]]; then
		p3 "Removing cached kubectl completions (will regenerate on next shell startup)..."
		rm -f "${kubectl_comp}"
	fi

	p2 "Brew installation done!"
}

# Link System Utilities to Applications
_links='/System/Library/CoreServices/Applications
/Applications/Xcode.app/Contents/Applications
/Applications/Xcode.app/Contents/Developer/Applications
/Applications/Xcode-beta.app/Contents/Applications
/Applications/Xcode-beta.app/Contents/Developer/Applications'

install_links() {
	p2 "Install links to System Utilities in Applications..."
	printf "%s\n" "${_links}" |
		while IFS= read -r link; do
			find "${link}" -maxdepth 1 -name "*.app" -type d -print0 2>/dev/null |
				xargs -0 -I {} -L 1 ln -s "{}" "/Applications" 2>/dev/null
		done
	p3 "Installed links!"
}

install_amphetamine_enhancer() {
	if [ ! -d "/Applications/Amphetamine Enhancer.app" ]; then
		p2 "Install Amphetamine Enhancer..."
		(
			cd /tmp || return
			curl -sSL -o "Amphetamine Enhancer.dmg" \
				https://github.com/x74353/Amphetamine-Enhancer/raw/master/Releases/Current/Amphetamine%20Enhancer.dmg
			hdiutil attach -quiet "Amphetamine Enhancer.dmg"
			cp -R "/Volumes/Amphetamine Enhancer/Amphetamine Enhancer.app" /Applications
			hdiutil detach -quiet "/Volumes/Amphetamine Enhancer"
			rm -f "Amphetamine Enhancer.dmg"
		)
		p3 "Amphetamine Enhancer installed!"
		open "/Applications/Amphetamine Enhancer.app"
	fi
}

install_mise_runtimes() {
	p2 "Installing language runtimes with mise..."

	# Check if brew is installed first
	if ! command -v brew >/dev/null 2>&1; then
		p1 "ERROR: brew not found. Please install Homebrew first."
		return 1
	fi

	local mise_prefix
	mise_prefix="$(brew --prefix mise 2>/dev/null)"
	if [ -z "${mise_prefix}" ] || [ ! -f "${mise_prefix}/bin/mise" ]; then
		p1 "ERROR: mise not found. Please run 'brew install mise' first."
		return 1
	fi

	# Ensure mise is activated in the current shell
	eval "$(mise activate bash)"

	# Install all runtimes defined in ~/.config/mise/config.toml
	p3 "Installing runtimes from global mise config..."
	mise install

	p2 "Installing Python utilities with uv"
	# Reference: https://github.com/pixelb/crudini
	uv tool install "crudini"
	# Reference: https://github.com/aiven/aiven-client
	uv tool install "aiven-client"

	# Libraries, not CLI tools, so `uv tool install` is the wrong verb — these must be
	# importable by the default `python3`. The agent-skills venv (see
	# install_agent_skills_venv) carries its own copies, but ad-hoc scripts run on the
	# mise interpreter and cannot see that venv.
	p3 "Installing Python libraries into the mise-managed interpreter"
	# Reference: https://openpyxl.readthedocs.io — xlsx read/write for report scripts.
	uv pip install --python "$(command -v python3)" --quiet openpyxl

	p2 "Configure gem"
	# Configure gem to not generate documentation to make it faster
	printf "%s\n" \
		"gem: --no-document" |
		tee "${HOME}/.gemrc" >/dev/null

	# This is slow, I don't really think we need to be updating system gems on every install..
	# yes | gem update --system > /dev/null
	# yes | gem update
	# yes | gem install bundler

	p2 "Mise installations done!"
}

# Define Function =install_powershell_modules=
# Installs PSScriptAnalyzer (the PowerShell linter) into the current user's
# module path via the `pwsh` provided by the powershell cask. Idempotent:
# Install-Module is skipped when the module is already available.
install_powershell_modules() {
	p2 "Installing PowerShell modules..."

	if ! command -v pwsh >/dev/null 2>&1; then
		p3 "pwsh not installed, skipping PowerShell module install"
		return 0
	fi

	p3 "Ensure PSScriptAnalyzer (PowerShell linter)..."
	pwsh -NoProfile -Command "if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) { Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force }"

	p2 "PowerShell modules installed!"
}

# Define Function =install_agent_skills_venv=
# Creates a shared uv venv at ~/.local/share/agent-skills/venv/ used by
# agent skills that need Python libraries. Its first users are Anthropic's
# docx, pdf, pptx and xlsx skills, which reach this machine through the
# claude.ai skill sync (anthropic-skills:* in Claude Code, bare names in
# OpenCode and Cursor) and are never vendored, because their licence forbids
# redistribution. The document-skills@anthropic-agent-skills plugin carries
# the same skills and needs the same deps. Per-skill dependencies are
# appended below as skills are adopted.
install_agent_skills_venv() {
	p2 "Setting up agent-skills uv venv..."

	if ! command -v uv >/dev/null 2>&1; then
		p1 "ERROR: uv not found. Mise should provide it."
		return 1
	fi

	local venv_dir="${HOME}/.local/share/agent-skills/venv"
	if [[ ! -d "${venv_dir}" ]]; then
		mkdir -p "$(dirname "${venv_dir}")"
		uv venv "${venv_dir}"
	else
		p3 "Venv already exists at ${venv_dir}"
	fi

	# Per-skill Python dependencies.
	# uv pip install --python is idempotent — safe to re-run.
	# Add deps here as skills are adopted; document each one's purpose.
	local venv_python="${venv_dir}/bin/python"
	# pdfplumber: the pdf skill's scripts/extract_form_structure.py.
	p3 "Installing Python deps for Anthropic's document skills (docx/pdf/pptx/xlsx)..."
	uv pip install --python "${venv_python}" --quiet \
		pypdf pdfplumber pdf2image pillow reportlab numpy \
		defusedxml lxml \
		openpyxl pandas

	# The browser bundle's deterministic mode (tapppi/skills, browser:browser)
	# drives Playwright from Python scripts run with this interpreter; the MCP
	# servers in that bundle bring their own browser, this one needs its own.
	# `playwright install` is idempotent: it downloads only what is missing.
	p3 "Installing Python Playwright and its Chromium for the browser bundle..."
	if ! uv pip install --python "${venv_python}" --quiet playwright ||
		! "${venv_dir}/bin/playwright" install chromium; then
		p1 "Playwright install failed; the browser bundle's scripted mode will not work until it succeeds."
		return 1
	fi

	p2 "Agent-skills venv ready at ${venv_dir}"
}

# Define Function =clear_cask_quarantine=
# Clear macOS quarantine from a CLI cask's entire Caskroom subtree.
# CLI casks ship bare Mach-O binaries (no .app bundle); on macOS 15.7+ Gatekeeper
# stalls dyld at process startup when the binary OR any parent directory carries
# com.apple.quarantine, so the tool hangs indefinitely before main() runs.
# Worse: if the binary is exec'd while still quarantined, the first-launch
# assessment can wedge in syspolicyd permanently. That stuck verdict is keyed to
# the file PATH — it survives clearing the xattr, replacing the inode, and
# symlinks — and only clears by restarting syspolicyd or rebooting. So clear the
# whole subtree (dir + files) right after install, BEFORE the binary is ever run.
clear_cask_quarantine() {
	local cask="${1}"
	local caskroom
	caskroom="$(brew --prefix)/Caskroom/${cask}"
	if [[ ! -d "${caskroom}" ]]; then
		p3 "${cask} cask directory not found, skipping quarantine fix"
		return 0
	fi
	if xattr -r -l "${caskroom}" 2>/dev/null | grep -q com.apple.quarantine; then
		p3 "Clear quarantine from ${cask} cask..."
		xattr -dr com.apple.quarantine "${caskroom}"
	else
		p3 "${cask} quarantine already cleared"
	fi
}

# Checkouts that double as plugin marketplaces (directory sources), shared by
# the Claude Code and Codex setup below. Both repos are private.
tapppi_skills_root="${HOME}/project/github/tapppi/skills"
ikeh_root="${HOME}/project/github/mantadevoy/ikeh"

# Define Function =ensure_checkout=
# Args: <root> <ssh url> <label>. Clones the repo unless a checkout is already
# there; an existing one is left exactly as it is, whatever branch it is on.
# The clone goes over SSH through the 1Password agent.
ensure_checkout() {
	local root="${1}" url="${2}" label="${3}"
	# .git is a directory in a clone and a file in a linked worktree.
	if [[ -e "${root}/.git" ]]; then
		return 0
	fi
	p3 "Cloning ${label} to ${root}..."
	mkdir -p "$(dirname "${root}")"
	if ! git clone "${url}" "${root}"; then
		p1 "Clone of ${label} failed; its marketplace will not resolve until it exists."
		return 1
	fi
}

# Define Function =context7_key_available=
# True when CONTEXT7_API_KEY is set, here or in ~/.config/bash/.credentials
# (template: .credentials.dist). Prints nothing, so the value never reaches
# the output.
context7_key_available() {
	[[ -n "${CONTEXT7_API_KEY:-}" ]] && return 0
	local credentials="${HOME}/.config/bash/.credentials"
	[[ -f "${credentials}" ]] || return 1
	# shellcheck disable=SC2016 # expanded by the inner shell, after sourcing
	bash -c 'source "${1}" >/dev/null 2>&1; [[ -n "${CONTEXT7_API_KEY:-}" ]]' _ "${credentials}"
}

# Define Function =install_claude_marketplaces=
# Registers every marketplace the tracked ~/.claude/settings.json declares in
# extraKnownMarketplaces, and the official one. The settings alone register
# nothing for the CLI (only an interactive session acts on them), so without
# these adds a non-interactive install, and tasks/projects.sh after it, would
# not resolve their plugins. `marketplace add` is a no-op once a marketplace is
# registered, so each Git one is followed by an update or an existing machine
# keeps resolving against a stale catalog.
install_claude_marketplaces() {
	claude plugin marketplace add anthropics/claude-plugins-official
	claude plugin marketplace update claude-plugins-official

	claude plugin marketplace add openai/codex-plugin-cc
	claude plugin marketplace update openai-codex

	# document-skills' marketplace only: the plugin is off at user level (the
	# docx/pdf/pptx/xlsx skills synced from claude.ai are the newer copies),
	# and a repo can still enable it locally.
	claude plugin marketplace add anthropics/skills
	claude plugin marketplace update anthropic-agent-skills

	ensure_checkout "${tapppi_skills_root}" git@github.com:Tapppi/skills.git Tapppi/skills
	claude plugin marketplace add "${tapppi_skills_root}"

	# Only the ikeh marketplace is registered, never a plugin from it: `claude
	# plugin install` defaults to user scope, so installing ikeh-git would
	# switch its hooks on in every repo. ikeh-git is enabled per repo, through
	# the repo's committed enabledPlugins or tasks/projects.sh.
	ensure_checkout "${ikeh_root}" git@github.com:mantadevoy/ikeh.git mantadevoy/ikeh
	claude plugin marketplace add "${ikeh_root}"
}

# Define Function =install_claude_context7=
# context7 (library documentation lookup, not built into Claude Code): ctx7's
# skill and rule, and a user-scope MCP server that reads its key from the
# exported CONTEXT7_API_KEY. The key lives only in ~/.config/bash/.credentials;
# unset, context7 answers at anonymous rate limits.
#
# ctx7 owns the skill (skills/context7-mcp/) and the rule (rules/context7.md);
# dotfiles tracks neither, and bootstrap leaves ~/.claude/skills/ alone. ctx7
# runs with --oauth only, which logs nothing in and writes no key, just a
# keyless HTTP entry that is replaced below. Its default mode logs in and
# writes the plain key into ~/.claude.json, and its --codex target appends to
# the dotfiles-rendered ~/.codex/AGENTS.md; neither is used here.
#
# The entry is stdio with the literal env value ${CONTEXT7_API_KEY:-}, which
# Claude Code expands at launch. An entry carrying a key (an `--api-key` or
# `--api-key=…` arg, a header or a literal env value) is migrated only when the
# key is exported or in ~/.config/bash/.credentials, so a re-run never silently
# drops a working key. If ~/.claude.json cannot be read, the entry is left alone.
install_claude_context7() {
	local claude_dir="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
	local claude_json="${CLAUDE_CONFIG_DIR:-${HOME}}/.claude.json"
	# shellcheck disable=SC2016 # a literal for Claude Code to expand, not the shell
	local env_ref='${CONTEXT7_API_KEY:-}'

	local have_ctx7=0
	[[ -f "${claude_dir}/skills/context7-mcp/SKILL.md" && -f "${claude_dir}/rules/context7.md" ]] &&
		have_ctx7=1

	# Every check below is jq -e on the entry; nothing prints a config value.
	if [[ "${have_ctx7}" -eq 1 ]] && jq -e --arg ref "${env_ref}" '.mcpServers.context7 // empty
		| (.type // "stdio") == "stdio" and .command == "npx"
			and .env.CONTEXT7_API_KEY == $ref and (has("headers") | not)
			and (any((.args // [])[]; startswith("--api-key")) | not)' \
		"${claude_json}" >/dev/null 2>&1; then
		p3 "context7 MCP server already reads CONTEXT7_API_KEY from the environment"
		return 0
	fi
	# Whether the entry holds a key: 0 yes, 1 no (or no file), anything else is
	# an unreadable file or a missing jq, which leaves the entry alone.
	local keyed=1
	if [[ -e "${claude_json}" ]]; then
		keyed=0
		jq -e --arg ref "${env_ref}" '.mcpServers.context7
			| if . == null then false else
				any((.args // [])[]; startswith("--api-key"))
				or ((.headers // {}) | length > 0)
				or ((.env.CONTEXT7_API_KEY // $ref) != $ref) end' \
			"${claude_json}" >/dev/null 2>&1 || keyed=$?
	fi
	case "${keyed}" in
	0)
		if ! context7_key_available; then
			p1 "The context7 MCP server holds its own key and CONTEXT7_API_KEY is not set; left as it is."
			p3 "Export the key from ~/.config/bash/.credentials (see .credentials.dist), then re-run."
			return 1
		fi
		;;
	1) ;;
	*)
		p1 "Could not read ${claude_json}; the context7 MCP server is left as it is."
		return 1
		;;
	esac

	# After the key check: ctx7 replaces whatever entry it finds, keyed or not.
	if [[ "${have_ctx7}" -eq 0 ]]; then
		npx -y ctx7 setup --claude --oauth --yes ||
			p1 "ctx7 setup failed; the context7 skill and rule are missing until it succeeds."
	fi

	claude mcp remove context7 -s user >/dev/null 2>&1
	# env_ref holds the literal reference; the single quotes on its assignment
	# are load-bearing, or the shell would expand the key into the config.
	claude mcp add -s user --transport stdio context7 -e "CONTEXT7_API_KEY=${env_ref}" \
		-- npx -y @upstash/context7-mcp >/dev/null
	if ! jq -e --arg ref "${env_ref}" '.mcpServers.context7.env.CONTEXT7_API_KEY == $ref' \
		"${claude_json}" >/dev/null 2>&1; then
		p1 "context7 MCP server could not be set to read CONTEXT7_API_KEY; check 'claude mcp get context7'."
		return 1
	fi
	p3 "context7 MCP server reads CONTEXT7_API_KEY from the environment"
}

# Install Claude Code MCP servers and plugins
install_claude_code() {
	p2 "Install Claude Code specifics..."
	if ! command -v claude >/dev/null 2>&1; then
		p3 "Claude Code not installed, skipping"
		return 0
	fi

	# Clear quarantine from the claude-code@latest cask before any `claude`
	# invocation below (Anthropic ships a bare Mach-O binary — see
	# clear_cask_quarantine for why this must run before first exec).
	clear_cask_quarantine claude-code@latest

	p3 "Claude Code marketplaces and plugins..."
	install_claude_marketplaces
	# Every plugin below is enabled in the tracked ~/.claude/settings.json,
	# which enables but fetches nothing — the cache is materialised here. A
	# `claude plugin install` is user scope and writes `true` into the live
	# settings, so a plugin the tracked settings set to `false` is never
	# installed here:
	# - superpowers is a per-repo choice, installed at local scope from a
	#   workspace manifest by tasks/projects.sh.
	# - document-skills@anthropic-agent-skills is off at user level.
	# A user-scope plugin also loads in OpenCode, through oh-my-openagent's
	# Claude Code compatibility, unless something sets it `false`.
	#
	# codex drives the Codex CLI from Claude Code; auth is the codex CLI's own
	# (`codex login`).
	claude plugin install duckdb-skills@claude-plugins-official
	claude plugin install codex@openai-codex
	claude plugin install skill-creator@tapppi-skills
	# No user-scope chrome-devtools MCP: browser@tapppi-skills ships it per repo.

	p3 "Claude Code context7..."
	install_claude_context7

	p3 "Claude Code vim mode..."
	# editorMode lives in ~/.claude.json (untracked, contains MCP state).
	# Set vim mode so it persists across dotfile syncs.
	local claude_json="${HOME}/.claude.json"
	if [[ -f "${claude_json}" ]]; then
		local tmp
		tmp="$(jq '.editorMode = "vim"' "${claude_json}")" && printf '%s\n' "${tmp}" > "${claude_json}"
	else
		printf '%s\n' '{"editorMode":"vim"}' > "${claude_json}"
	fi
	p3 "Claude Code configured..."
}

# Define Function =install_codex=
# Codex's user-wide plugin set, its ikeh roles, the context7 MCP server and the
# ~/.agents/skills link. Codex enables plugins for every project, so the set is
# the recorded exception in docs/skills.md: browser and frontend-design (per
# repo in Claude Code) and ikeh-development. Everything here lives in
# ~/.codex/config.toml, which Codex owns and tapppi/systems never writes, so it
# goes through the codex CLI. `codex plugin add` re-copies a plugin from its
# marketplace, so a re-run also refreshes the cache to the checkout's version.
#
# Codex itself comes from tapppi/systems (modules/darwin/codex.nix), so on a
# fresh Mac it may not exist yet: then this skips, and `./setup.sh codex` runs
# it after `nix run .#build-switch`. A new or changed plugin hook still needs a
# trust decision in Codex's /hooks, which no script can make.
install_codex() {
	p2 "Configuring Codex plugins and MCP servers..."
	local ready=0
	codex_ready || ready=$?
	case "${ready}" in
	1) return 0 ;;
	2) return 1 ;;
	esac

	ensure_checkout "${tapppi_skills_root}" git@github.com:Tapppi/skills.git Tapppi/skills
	ensure_checkout "${ikeh_root}" git@github.com:mantadevoy/ikeh.git mantadevoy/ikeh
	local source
	for source in "${tapppi_skills_root}" "${ikeh_root}" anthropics/claude-plugins-official; do
		codex plugin marketplace add "${source}" >/dev/null ||
			p1 "Codex marketplace ${source} could not be added."
	done
	codex plugin marketplace upgrade >/dev/null ||
		p1 "Codex Git marketplaces could not be upgraded."

	local plugin
	for plugin in browser@tapppi-skills ikeh-development@ikeh frontend-design@claude-plugins-official; do
		codex plugin add "${plugin}" >/dev/null ||
			p1 "Codex plugin ${plugin} could not be installed."
	done

	# ikeh-development's Codex roles (~/.codex/agents/ikeh-*.toml), per the
	# plugin's README. --check installs the committed roles and refuses if they
	# are out of date, instead of regenerating them inside the checkout.
	local build_agents="${ikeh_root}/plugins/ikeh-development/scripts/build-agents.py"
	if [[ -f "${build_agents}" ]] && command -v uv >/dev/null 2>&1; then
		uv run --quiet --script "${build_agents}" --check --install-codex ||
			p1 "ikeh Codex roles were not installed."
	else
		p1 "ikeh Codex roles skipped: ${build_agents} or uv is missing."
	fi

	install_codex_context7

	# ctx7's skill for Codex, which reads ~/.agents/skills. It is the same skill
	# Claude Code has (ctx7's own Codex target would write the key into
	# config.toml), so link that copy rather than run ctx7 again.
	local skill="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}/skills/context7-mcp"
	local link="${HOME}/.agents/skills/context7-mcp"
	if [[ ! -d "${skill}" ]]; then
		p3 "No ctx7 context7-mcp skill to link (Claude Code setup has not run)"
	elif [[ -e "${link}" && ! -L "${link}" ]]; then
		p1 "${link} exists and is not a symlink; left as it is."
	else
		mkdir -p "$(dirname "${link}")"
		ln -sfn "${skill}" "${link}"
	fi

	p3 "Codex configured. Review new or changed plugin hooks in Codex's /hooks."
}

# Define Function =codex_ready=
# 0 when the codex CLI exists and no Codex process runs. 1 when codex is absent
# (skip; it comes from tapppi/systems, so a fresh Mac may not have it yet). 2
# when Codex runs: the CLI, the ChatGPT app's app-server and the Claude codex
# plugin's broker all rewrite config.toml and could drop these edits.
codex_ready() {
	if ! command -v codex >/dev/null 2>&1; then
		p3 "Codex not installed (tapppi/systems provides it), skipping"
		p3 "After 'nix run .#build-switch' in systems, run './setup.sh codex'."
		return 1
	fi
	if pgrep -x codex >/dev/null 2>&1; then
		p1 "Codex is running; close every Codex session, then re-run."
		return 2
	fi
}

# Define Function =install_context7=
# context7 alone, for Claude Code and Codex (`./setup.sh context7`): re-asserts
# both MCP servers on the exported key, and ctx7's skill and rule, without the
# rest of install. Same guards as the full install.
install_context7() {
	p2 "Configuring context7 for Claude Code and Codex..."
	local status=0 ready=0
	if command -v claude >/dev/null 2>&1; then
		install_claude_context7 || status=1
	else
		p3 "Claude Code not installed, skipping"
	fi
	codex_ready || ready=$?
	case "${ready}" in
	0) install_codex_context7 || status=1 ;;
	2) status=1 ;;
	esac
	return "${status}"
}

# Define Function =install_codex_context7=
# Codex's context7 MCP server, stdio, with the key passed through from the
# environment by `env_vars` (Codex gives stdio servers only a fixed environment
# otherwise). `codex mcp add --env` would write the value into the file, and
# `codex mcp add` has no `env_vars` flag, so the line goes in right after the
# table header that a fresh `codex mcp add` writes. A later `codex mcp add`
# drops it again, so the server is only ever re-added together with the line.
# An entry carrying a key is migrated only when the key is available, as for
# Claude Code, and nothing is changed when Codex cannot read its config or jq
# fails. Every check is jq -e on `codex mcp get --json`; nothing prints a
# config value.
install_codex_context7() {
	local config="${CODEX_HOME:-${HOME}/.codex}/config.toml"

	# Codex must read its config, or a failing `codex mcp get` below would look
	# like an absent server.
	if ! codex mcp list --json >/dev/null 2>&1; then
		p1 "Codex cannot read ${config}; its context7 MCP server is left as it is."
		return 1
	fi
	if codex mcp get context7 --json 2>/dev/null | jq -e '.transport
		| .type == "stdio" and .command == "npx"
			and ((.env_vars // []) | index("CONTEXT7_API_KEY") != null)
			and ((.env // {}) | has("CONTEXT7_API_KEY") | not)
			and (any((.args // [])[]; startswith("--api-key")) | not)' >/dev/null; then
		p3 "Codex context7 MCP server already reads CONTEXT7_API_KEY from the environment"
		return 0
	fi
	if codex mcp get context7 --json >/dev/null 2>&1; then
		# 0 holds a key, 1 does not, anything else (jq failing) leaves it alone.
		local keyed=0
		codex mcp get context7 --json 2>/dev/null | jq -e '.transport
			| any((.args // [])[]; startswith("--api-key"))
				or ((.env // {}) | has("CONTEXT7_API_KEY"))
				or ((.http_headers // {}) | length > 0)' >/dev/null 2>&1 || keyed=$?
		case "${keyed}" in
		0)
			if ! context7_key_available; then
				p1 "Codex's context7 MCP server holds its own key and CONTEXT7_API_KEY is not set; left as it is."
				p3 "Export the key from ~/.config/bash/.credentials (see .credentials.dist), then re-run."
				return 1
			fi
			;;
		1) ;;
		*)
			p1 "Could not read Codex's context7 MCP server; it is left as it is."
			return 1
			;;
		esac
		codex mcp remove context7 >/dev/null
	fi

	codex mcp add context7 -- npx -y @upstash/context7-mcp >/dev/null
	# `command`: install() above shadows install(1) once this file is sourced.
	local tmp="${config}.context7-tmp"
	# The copy holds all of config.toml, so it is never readable by others.
	(umask 077 && awk '{ print } $0 == "[mcp_servers.context7]" && !done {
		print "env_vars = [\"CONTEXT7_API_KEY\"]"; done = 1 }' "${config}" > "${tmp}") &&
		command install -m 600 "${tmp}" "${config}"
	rm -f "${tmp}"
	if ! codex mcp get context7 --json 2>/dev/null |
		jq -e '.transport.env_vars // [] | index("CONTEXT7_API_KEY") != null' >/dev/null; then
		p1 "Codex's context7 MCP server could not be set to read CONTEXT7_API_KEY; check ${config}."
		return 1
	fi
	p3 "Codex context7 MCP server reads CONTEXT7_API_KEY from the environment"
}

# Clear macOS quarantine from cursor-cli cask
# cursor-cli ships a standalone (non-app-bundle) Node.js binary that Gatekeeper
# won't accept once quarantined. This is a permanent upstream packaging
# limitation, not a bug awaiting a fix: homebrew-cask#246786 was closed
# NOT_PLANNED ("upstream distribution issue, not a Homebrew problem").
#
# This must be re-run after EVERY `brew upgrade --cask cursor-cli`: the fresh
# download re-quarantines the bundled `merkle-tree-napi` native binding, and
# every `cursor-agent` invocation then dies with "library load disallowed by
# system policy" while spamming Gatekeeper popups. The failure looks like a
# crashed Node process dumping minified source, not a permissions error.
install_cursor_agent() {
	p2 "Configuring Cursor Agent CLI..."

	if ! command -v cursor-agent >/dev/null 2>&1; then
		p3 "cursor-agent not installed, skipping"
		return 0
	fi

	clear_cask_quarantine cursor-cli
}

# Install herdr's agent-state integrations
#
# herdr classifies every pane by which agent is running in it and whether that
# agent is working, blocked or idle. Claude Code, Codex and Cursor report only a
# session id, which is what lets herdr resume a conversation after a server
# restart; opencode's plugin additionally authors the state herdr shows in its
# agents sidebar. Without an integration an agent is classified by screen
# scraping alone.
#
# What each integration writes lands in files dotfiles tracks — for Claude Code, a
# hook script under ~/.claude/hooks/ and a SessionStart entry in
# ~/.claude/settings.json. None of it is vendored in dotfiles. Instead this task
# runs *after* install_dotfiles, so bootstrap.sh drops the tool's keys and the
# tool immediately writes them back. That ordering is the whole mechanism: keep
# this call after install_dotfiles, and keep the standalone `dotfiles` task in
# setup.sh calling it too.
#
# The alternative — tracking the hook entry in dotfiles — means carrying a path
# and payload herdr owns and rewrites between versions, which goes stale silently
# on the next upgrade. `integration install` is idempotent and rewrites an
# outdated hook, so re-running this is both safe and the way to update.
#
# herdr itself is not installed here: asterix takes it from nix
# (systems/modules/darwin/herdr.nix), and a host that is only ever attached to
# over SSH takes it from herdr's own remote auto-install into ~/.local/bin. It
# can therefore legitimately be absent at this point.
install_herdr_integrations() {
	p2 "Configuring herdr agent integrations..."

	if ! command -v herdr >/dev/null 2>&1; then
		p3 "herdr not installed, skipping"
		return 0
	fi

	# <herdr integration target>:<CLI whose presence makes it worth installing>
	local pair target cli
	for pair in claude:claude codex:codex cursor:cursor-agent opencode:opencode; do
		target="${pair%%:*}"
		cli="${pair##*:}"
		command -v "${cli}" >/dev/null 2>&1 || continue
		if ! herdr integration install "${target}" >/dev/null; then
			p3 "herdr ${target} integration failed"
		fi
	done
	p3 "herdr agent integrations configured..."
}

# Install dotfiles with =dotfiles/bootstrap.sh=
# Define Function =install_dotfiles= — sync dotfiles to ~, then install nnn plugins.
#
# bootstrap.sh's exit status is checked rather than discarded. It runs several
# rsyncs, and a failing one (a mirror whose source directory has been deleted
# exits 23) otherwise leaves no trace: the sync half-completes, the function
# returns 0, and everything after it proceeds as if ~ were fully synced.
#
# A sync failure does not abort the install — a partial sync is usually still
# better than none, and `install()` has later steps worth running. But it is
# reported loudly and returned, so callers can gate on it. `install()` uses that
# to skip the tool integrations that re-assert config *into* files bootstrap
# just wrote, since re-asserting onto a half-synced tree is what produces the
# confusing half-broken state.
install_dotfiles() {
	p1 "Installing dotfiles..."

	mkdir -p ~/.config/bash/
	cp ./{.extra,.path} ~/.config/bash/

	local bootstrap_status=0
	./dotfiles/bootstrap.sh -f || bootstrap_status=$?

	if [[ "${bootstrap_status}" -ne 0 ]]; then
		p2 "WARNING: dotfiles/bootstrap.sh exited ${bootstrap_status} — the sync is incomplete."
		p3 "The code is rsync's own (23 is a partial transfer); bootstrap.sh names the sync that failed."
		p3 "Re-run './setup.sh dotfiles' after fixing, or ~ will stay partially synced."
	fi

	p2 "Installing nnn plugins..."
	# Install official nnn plugins
	sh -c "$(curl -fsSL https://raw.githubusercontent.com/jarun/nnn/master/plugins/getplugs)"
	p3 "nnn plugins installed!"

	return "${bootstrap_status}"
}
