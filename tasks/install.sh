#!/usr/bin/env bash
set -uo pipefail

# Define Function =install=

install() {
	install_macos_sw
	install_dotfiles
	install_mise_runtimes
	install_claude_code
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
	if [ "${SHELL}" != "${BREW_PREFIX}/bin/bash" ]; then
		chsh -s "${BREW_PREFIX}/bin/bash"
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
	if [[ "$(uname -m)" == "arm64" ]]; then
		brew bundle --file="Brewfile"
	else
		brew bundle --file="intel.Brewfile"
	fi

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

# Install Claude Code MCP servers and plugins
install_claude_code() {
	p2 "Configuring Claude Code MCP servers and plugins..."
	if ! command -v claude >/dev/null 2>&1; then
		p3 "Claude Code not installed, skipping"
		return 0
	fi

	# context7: library/framework documentation lookup (not built into Claude Code)
	if ! claude mcp list 2>/dev/null | grep -q context7; then
		claude mcp add --scope user --transport stdio context7 -- npx -y @upstash/context7-mcp
	fi
	# chrome-devtools: browser debugging, Lighthouse audits, performance tracing
	if ! claude mcp list 2>/dev/null | grep -q chrome-devtools; then
		claude mcp add --scope user --transport stdio chrome-devtools -- npx -y chrome-devtools-mcp@latest
	fi
	# playwright: browser testing and UX automation (via official plugin)
	claude plugin install playwright
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
