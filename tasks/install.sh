#!/usr/bin/env bash
set -uo pipefail

# Define Function =install=

install() {
	install_macos_sw
	link_terraform_to_tofu
	install_dotfiles
	install_mise_runtimes
	install_powershell_modules
	install_agent_skills_venv
	install_claude_code
	install_cursor_agent
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

# Define Function =preflight_gatekeeper_network=
# Bare CLI binaries shipped in casks can't carry a stapled notarization ticket,
# so on first exec (e.g. a cask generating shell completions) syspolicyd does an
# ONLINE lookup to api.apple-cloudkit.com. If that path stalls the exec hangs
# forever and wedges `brew bundle`. This can't fix the network, but it flags the
# usual causes (full-tunnel VPN, slow/custom primary DNS) up front so a hang
# isn't a mystery. Warn-only — never blocks the install.
preflight_gatekeeper_network() {
	p2 "Checking Gatekeeper notarization network path (warn-only)..."
	local host t bad=0
	for host in api.apple-cloudkit.com ocsp.apple.com; do
		t="$(curl -s -o /dev/null -w '%{time_total}' --max-time 6 "https://${host}/" 2>/dev/null)"
		if [[ -z "${t}" ]]; then
			p1 "  ${host}: unreachable within 6s"
			bad=1
		elif awk -v t="${t}" 'BEGIN { exit !(t > 2.0) }'; then
			p1 "  ${host}: slow response (${t}s)"
			bad=1
		else
			p3 "  ${host}: reachable (${t}s)"
		fi
	done

	local primary_dns
	primary_dns="$(scutil --dns 2>/dev/null | awk '/nameserver\[0\]/{print $3; exit}')"
	if [[ "${primary_dns}" == "100.100.100.100" ]]; then
		p2 "  Primary DNS is Tailscale MagicDNS (100.100.100.100); slow resolution of Apple hosts can stall Gatekeeper."
		p3 "    Temporarily bypass with: tailscale set --accept-dns=false   (restore: --accept-dns=true)"
		bad=1
	fi

	if [[ "${bad}" -ne 0 ]]; then
		p1 "  Notarization path looks risky. If a cask install hangs, fix the network then:"
		p1 "    sudo killall syspolicyd   # clear the wedge, then re-run ./setup.sh install"
	else
		p3 "Gatekeeper notarization path looks healthy."
	fi
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

	# Casks that ship a bare CLI binary (1password-cli's `op`, codex) generate
	# shell completions at install time, which execs the freshly-quarantined
	# binary. That first exec makes syspolicyd do an ONLINE notarization lookup
	# (bare binaries can't carry a stapled ticket); if the path to Apple stalls
	# — a full-tunnel VPN or a slow primary DNS resolver — the exec hangs forever
	# and would wedge the whole bundle. Warn about that likely cause up front,
	# and cap the bundle with a generous wall-clock timeout so a stall can't hang
	# setup indefinitely (override with BREW_BUNDLE_TIMEOUT, in seconds).
	preflight_gatekeeper_network
	run_with_timeout "${BREW_BUNDLE_TIMEOUT:-5400}" brew bundle --file="${brewfile}" ||
		p1 "brew bundle exited non-zero (timeout or a package failure); setup continues — re-run './setup.sh install' after resolving."

	# Strip com.apple.quarantine from the bare-binary casks after install so a
	# later exec doesn't trigger another online Gatekeeper assessment.
	# (claude-code@latest and cursor-cli are cleared in their own install steps.)
	clear_cask_quarantine 1password-cli
	clear_cask_quarantine codex

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
# agent skills that need Python libraries (anthropics pdf/pptx/docx/xlsx etc.).
# Per-skill dependencies are appended below as skills are adopted.
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
	p3 "Installing Python deps for adopted anthropics doc skills..."
	uv pip install --python "${venv_python}" --quiet \
		pypdf pdf2image pillow reportlab numpy \
		defusedxml lxml \
		openpyxl pandas

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

	p3 "Claude Code MCP servers and plugins..."
	# context7: library/framework documentation lookup (not built into Claude Code)
	if ! claude mcp list 2>/dev/null | grep -q context7; then
		claude mcp add --scope user --transport stdio context7 -- npx -y @upstash/context7-mcp
	fi
	# chrome-devtools: browser debugging, Lighthouse audits, performance tracing
	if ! claude mcp list 2>/dev/null | grep -q chrome-devtools; then
		claude mcp add --scope user --transport stdio chrome-devtools -- npx -y chrome-devtools-mcp@latest
	fi
	# playwright: browser testing and UX automation (via official plugin).
	# The catalog can be stale on a fresh machine, so refresh before install.
	claude plugin marketplace update claude-plugins-official
	claude plugin install playwright@claude-plugins-official

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

# Clear macOS quarantine from cursor-cli cask
# Upstream issue: bundled node binary triggers Gatekeeper
# https://github.com/Homebrew/homebrew-cask/issues/246786
install_cursor_agent() {
	p2 "Configuring Cursor Agent CLI..."

	if ! command -v cursor-agent >/dev/null 2>&1; then
		p3 "cursor-agent not installed, skipping"
		return 0
	fi

	clear_cask_quarantine cursor-cli
}

# Install dotfiles with =dotfiles/bootstrap.sh=
install_dotfiles() {
	p1 "Installing dotfiles..."

    mkdir -p ~/.config/bash/
	cp ./{.extra,.path} ~/.config/bash/

	./dotfiles/bootstrap.sh -f

	p2 "Installing nnn plugins..."
	# Install official nnn plugins
	sh -c "$(curl -fsSL https://raw.githubusercontent.com/jarun/nnn/master/plugins/getplugs)"
	p3 "nnn plugins installed!"
}
