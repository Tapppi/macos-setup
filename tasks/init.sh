#!/usr/bin/env bash
set -uo pipefail

# Define Function =init=
init() {
	init_sudo
	init_hostname
	init_perms
	init_updates
	init_devtools

	init_guest
	init_rm_sudoers
}

# Define Function =new_account=
new_account() {
	init_sudo
	init_new_account
	init_rm_sudoers
}

clean_account() {
	defaults write com.apple.dock persistent-apps -array
}

if test "${1}" = 0; then
	printf "\n$(which init)\n"
fi

# Define Function =init_paths=
init_paths() {
	test -x "/usr/libexec/path_helper" &&
		eval $(/usr/libexec/path_helper -s) &&
		hash -r
}

# Eliminate Prompts for Password
init_sudo() {
	printf "%s\n" "%wheel ALL=(ALL) NOPASSWD: ALL" |
		sudo tee "/etc/sudoers.d/wheel" >/dev/null &&
		sudo dscl /Local/Default append /Groups/wheel GroupMembership "$(whoami)"
}

# Set Hostname from DNS
init_hostname() {
	a=$(ask2 "Set Computer Name and Hostname" "Set Hostname" "Cancel" "Set Hostname" $(ruby -e "print '$(hostname -s)'.capitalize") "false")
	if test -n $a; then
		sudo scutil --set ComputerName $(ruby -e "print '$a'.capitalize")
		sudo scutil --set HostName $(ruby -e "print '$a'.downcase")
	fi
}

# Set Permissions on Install Destinations
_dest='/usr/local/bin
/Library/Desktop Pictures
/Library/ColorPickers
/Library/Fonts
/Library/Input Methods
/Library/PreferencePanes
/Library/QuickLook
/Library/Screen Savers
/Library/User Pictures'

init_perms() {
	printf "%s\n" "${_dest}" |
		while IFS="$(printf '\t')" read d; do
			test -d "${d}" || sudo mkdir -p "${d}"
			sudo chgrp -R admin "${d}"
			sudo chmod -R g+w "${d}"
		done
}

# Install Developer Tools
init_devtools() {
	p="${HOMEBREW_CACHE}/Cask/Command Line Tools (macOS High Sierra version 10.13).pkg"
	i="com.apple.pkg.CLTools_SDK_macOS1013"

	if test -f "${p}"; then
		if ! pkgutil --pkg-info "${i}" >/dev/null 2>&1; then
			sudo installer -pkg "${p}" -target /
		fi
	else
		xcode-select --install
	fi
}

# Install macOS Updates
init_updates() {
	sudo softwareupdate --install --all
}

# Customize SSH
trim_whitespace() {
	local value="${1#"${1%%[![:space:]]*}"}"
	value="${value%"${value##*[![:space:]]}"}"
	printf "%s" "${value}"
}

toml_escape_string() {
	local value="${1//\\/\\\\}"
	value="${value//\"/\\\"}"
	printf "%s" "${value}"
}

init_ssh_1password() {
	local onepassword_config_root="${XDG_CONFIG_HOME:-${HOME}/.config}/1Password/ssh"
	local onepassword_agent_toml="${onepassword_config_root}/agent.toml"
	local vault_input
	local raw_vault
	local vault
	local -a vaults

	if ! test -d "${HOME}/.ssh"; then
		mkdir -m go= "${HOME}/.ssh"
	fi
	if ! test -d "${onepassword_config_root}"; then
		mkdir -p "${onepassword_config_root}"
	fi

	vault_input="$(ask '1Password SSH Agent: Vaults? (comma-separated)' 'OK' '')"
	IFS=',' read -r -a vaults <<<"${vault_input}"

	: >"${onepassword_agent_toml}"
	for raw_vault in "${vaults[@]}"; do
		vault="$(trim_whitespace "${raw_vault}")"
		if [[ -n "${vault}" ]]; then
			cat <<EOF >>"${onepassword_agent_toml}"
[[ssh-keys]]
account = "Tapani Moilanen"
vault = "$(toml_escape_string "${vault}")"

EOF
		fi
	done

	if ! test -s "${onepassword_agent_toml}"; then
		rm -f "${onepassword_agent_toml}"
		p3 '1Password SSH agent config not written: no vaults provided'
		return 1
	fi

	cat <<EOF >"${HOME}/.ssh/config"
Host *
  IdentityAgent "~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"

EOF

	p3 "Wrote ${onepassword_agent_toml}; lock and unlock 1Password if it does not pick up the new vault list immediately"
}

# Unused function due to switching to _1password variant,
# but saved for reference when needing local keys
init_ssh_local() {
	if ! test -d "${HOME}/.ssh"; then
		mkdir -m go= "${HOME}/.ssh"
		e="$(ask 'New SSH Key: Email Address?' 'OK' '')"
		ssh-keygen -t ed25519 -a 100 -C "$e"
		cat <<EOF >"${HOME}/.ssh/config"
Host *
  AddKeysToAgent yes
  UseKeychain yes
  IdentityFile ~/.ssh/id_ed25519
EOF
		ssh-add ~/.ssh/id_ed25519
		pbcopy <"${HOME}/.ssh/id_ed25519.pub"
		open "https://github.com/settings/keys"
	fi
}

# Configure New Account
init_new_account() {
	e="$(ask 'New macOS Account: Email Address?' 'OK' '')"
	curl --output "/Library/User Pictures/${e}.jpg" --silent \
		"https://www.gravatar.com/avatar/$(md5 -qs ${e}).jpg?s=512"

	g="$(curl --location --silent \
		"https://api.github.com/search/users?q=${e}" |
		sed -n 's/^.*"url": "\(.*\)".*/\1/p')"
	g="$(curl --location --silent ${g})"

	n="$(printf ${g} | sed -n 's/^.*"name": "\(.*\)".*/\1/p')"
	n="$(ask 'New macOS Account: Real Name?' 'OK' ${n})"

	u="$(printf ${g} | sed -n 's/^.*"login": "\(.*\)".*/\1/p')"
	u="$(ask 'New macOS Account: User Name?' 'OK' ${u})"

	sudo defaults write \
		"/System/Library/User Template/Non_localized/Library/Preferences/.GlobalPreferences.plist" \
		"com.apple.swipescrolldirection" -bool false

	sudo sysadminctl -admin -addUser "${u}" -fullName "${n}" -password - \
		-shell "$(which bash)" -picture "/Library/User Pictures/${e}.jpg"
}

# Configure Guest Users
init_guest() {
	sudo sysadminctl -guestAccount off
}

# Reinstate =sudo= Password
init_rm_sudoers() {
	sudo -- sh -c \
		"rm -f /etc/sudoers.d/wheel; dscl /Local/Default -delete /Groups/wheel GroupMembership $(whoami)"

	/usr/bin/read -n 1 -p "Press any key to continue.
" -s
	if run "Log Out Then Log Back In?" "Cancel" "Log Out"; then
		osascript -e 'tell app "loginwindow" to «event aevtrlgo»'
	fi
}
