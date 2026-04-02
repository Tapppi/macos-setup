#!/usr/bin/env bash
set -uo pipefail

# Define Function =init=
init() {
	init_sudo
	init_hostname
	init_perms
	init_updates

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
	# Wipe all (default) app icons from the Dock
	# This is only really useful when setting up a new Mac, or if you don't use
	# the Dock to launch apps.
	defaults write com.apple.dock persistent-apps -array
	killall Dock
}

if test "${1}" = 0; then
	printf "\n%s\n" "$(which init)"
fi

# Define Function =init_paths=
init_paths() {
	test -x "/usr/libexec/path_helper" &&
		eval "$(/usr/libexec/path_helper -s)" &&
		hash -r
}

# Temporarily Eliminate Prompts for Password
# Creates a user-specific sudoers rule for the duration of the setup.
# Cleaned up by init_rm_sudoers at the end of init.
init_sudo() {
	sudo mkdir -p /etc/sudoers.d
	if ! printf "%s\n" "$(whoami) ALL=(ALL) NOPASSWD: ALL" |
		sudo tee "/etc/sudoers.d/setup-nopasswd" >/dev/null 2>&1; then
		p1 "ERROR: Could not write to /etc/sudoers.d/"
		p2 "On macOS 15.3+, Full Disk Access may be required for your terminal."
		p3 "1. Open System Settings > Privacy & Security > Full Disk Access"
		p3 "2. Enable access for your terminal app"
		p3 "3. Restart your terminal and run this script again."
		return 1
	fi
}

# Set Hostname
init_hostname() {
	local current first rest capitalized
	current="$(hostname -s)"
	first="$(printf '%s' "${current}" | cut -c1 | tr '[:lower:]' '[:upper:]')"
	rest="$(printf '%s' "${current}" | cut -c2-)"
	capitalized="${first}${rest}"

	local a
	a=$(ask2 "Set Computer Name and Hostname" "Set Hostname" "Cancel" "Set Hostname" "${capitalized}" "false")
	if [[ -n "${a}" ]]; then
		local cap_name lower_name cap_first cap_rest
		cap_first="$(printf '%s' "${a}" | cut -c1 | tr '[:lower:]' '[:upper:]')"
		cap_rest="$(printf '%s' "${a}" | cut -c2-)"
		cap_name="${cap_first}${cap_rest}"
		lower_name="$(printf '%s' "${a}" | tr '[:upper:]' '[:lower:]')"

		sudo scutil --set ComputerName "${cap_name}"
		sudo scutil --set HostName "${lower_name}"
		sudo scutil --set LocalHostName "${lower_name}"
	fi
}

# Set Permissions on Install Destinations
# Note: /Library/QuickLook removed - Sequoia dropped legacy .qlgenerator plugin support
_dest='/usr/local/bin
/Library/Desktop Pictures
/Library/ColorPickers
/Library/Fonts
/Library/Input Methods
/Library/PreferencePanes
/Library/Screen Savers
/Library/User Pictures'

init_perms() {
	# On Apple Silicon, Homebrew uses /opt/homebrew instead of /usr/local
	if [[ "$(uname -m)" == "arm64" ]] && [[ ! -d "/opt/homebrew" ]]; then
		sudo mkdir -p /opt/homebrew
		sudo chown -R "$(whoami):admin" /opt/homebrew
	fi

	while IFS= read -r d; do
		[[ -z "${d}" ]] && continue
		test -d "${d}" || sudo mkdir -p "${d}"
		sudo chgrp -R admin "${d}"
		sudo chmod -R g+w "${d}"
	done <<< "${_dest}"
}

# Install macOS Updates
init_updates() {
	sudo softwareupdate --install --all --agree-to-license
}

# Customize SSH for 1Password Agent
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

	p3 "Wrote ${onepassword_agent_toml} and replaced ${HOME}/.ssh/config; lock and unlock 1Password if it does not pick up the new vault list immediately"
}

# Unused function due to switching to _1password variant,
# but saved for reference when needing local keys
init_ssh_local() {
	if ! test -d "${HOME}/.ssh"; then
		mkdir -m go= "${HOME}/.ssh"
		local e
		e="$(ask 'New SSH Key: Email Address?' 'OK' '')"
		ssh-keygen -t ed25519 -a 100 -C "${e}"
		cat <<EOF >"${HOME}/.ssh/config"
Host *
  AddKeysToAgent yes
  UseKeychain yes
  IdentitiesOnly yes
  IdentityFile ~/.ssh/id_ed25519
EOF
		ssh-add --apple-use-keychain ~/.ssh/id_ed25519
		pbcopy <"${HOME}/.ssh/id_ed25519.pub"
		open "https://github.com/settings/keys"
	fi
}

# Configure New Account
# Run separately: ./setup.sh new_account
# Creates a new admin user, intended to replace the default macOS superadmin.
# Prompts for current admin password to grant SecureToken (required for FileVault).
init_new_account() {
	local e g n u email_hash pic_path
	e="$(ask 'New macOS Account: Email Address?' 'OK' '')"

	email_hash="$(printf '%s' "${e}" | tr '[:upper:]' '[:lower:]' | shasum -a 256 | cut -d' ' -f1)"
	pic_path="/Library/User Pictures/${e}.jpg"
	curl --output "${pic_path}" --silent \
		"https://gravatar.com/avatar/${email_hash}.jpg?s=512"

	g="$(curl --location --silent \
		"https://api.github.com/search/users?q=${e}" |
		sed -n 's/^.*"url": "\(.*\)".*/\1/p')"
	g="$(curl --location --silent "${g}")"

	n="$(printf '%s' "${g}" | sed -n 's/^.*"name": "\(.*\)".*/\1/p')"
	n="$(ask 'New macOS Account: Real Name?' 'OK' "${n}")"

	u="$(printf '%s' "${g}" | sed -n 's/^.*"login": "\(.*\)".*/\1/p')"
	u="$(ask 'New macOS Account: User Name?' 'OK' "${u}")"

	sudo defaults write \
		"/System/Library/User Template/Non_localized/Library/Preferences/.GlobalPreferences.plist" \
		"com.apple.swipescrolldirection" -bool false

	# -adminUser/-adminPassword grants SecureToken to the new user
	# (required for FileVault unlock at boot)
	# You will be prompted for: 1) current admin password, 2) new user password
	sudo sysadminctl -adminUser "$(whoami)" -adminPassword - \
		-addUser "${u}" -fullName "${n}" -password - \
		-shell "$(which bash)" -picture "${pic_path}"
}

# Configure Guest Users
init_guest() {
	sudo sysadminctl -guestAccount off
}

# Reinstate =sudo= Password
# Removes the temporary passwordless sudo rule created by init_sudo.
init_rm_sudoers() {
	sudo rm -f /etc/sudoers.d/setup-nopasswd

	/usr/bin/read -n 1 -p "Press any key to continue.
" -s
	if run "Log Out Then Log Back In?" "Cancel" "Log Out"; then
		osascript -e 'tell app "loginwindow" to «event aevtrlgo»'
	fi
}
