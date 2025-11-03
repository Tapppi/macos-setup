#!/bin/bash

# Define Function =init=
init () {
  init_sudo
  init_no_sleep
  init_hostname
  init_perms
  init_updates
  init_devtools

  init_new_account
  init_guest
  init_rm_sudoers
}

init_user () {
  # Wipe all (default) app icons from the Dock
  # This is only really useful when setting up a new Mac, or if you don’t use
  # the Dock to launch apps.
  defaults write com.apple.dock persistent-apps -array
  init_ssh_1password
}

if test "${1}" = 0; then
  printf "\n$(which init)\n"
fi

# Define Function =init_paths=
init_paths () {
  test -x "/usr/libexec/path_helper" && \
    eval $(/usr/libexec/path_helper -s) && \
    hash -r
}

# Eliminate Prompts for Password
init_sudo () {
  printf "%s\n" "%wheel ALL=(ALL) NOPASSWD: ALL" | \
  sudo tee "/etc/sudoers.d/wheel" > /dev/null && \
  sudo dscl /Local/Default append /Groups/wheel GroupMembership "$(whoami)"
}

# Set Hostname from DNS
init_hostname () {
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

init_perms () {
  printf "%s\n" "${_dest}" | \
  while IFS="$(printf '\t')" read d; do
    test -d "${d}" || sudo mkdir -p "${d}"
    sudo chgrp -R admin "${d}"
    sudo chmod -R g+w "${d}"
  done
}

# Install Developer Tools
init_devtools () {
  p="${HOMEBREW_CACHE}/Cask/Command Line Tools (macOS High Sierra version 10.13).pkg"
  i="com.apple.pkg.CLTools_SDK_macOS1013"

  if test -f "${p}"; then
    if ! pkgutil --pkg-info "${i}" > /dev/null 2>&1; then
      sudo installer -pkg "${p}" -target /
    fi
  else
    xcode-select --install
  fi
}

# Install macOS Updates
init_updates () {
  sudo softwareupdate --install --all
}

# Customize SSH
init_ssh_1password () {
  if ! test -d "${HOME}/.ssh"; then
    mkdir -m go= "${HOME}/.ssh"
  fi

  cat << EOF > "${HOME}/.ssh/config"
Host *
  IdentityAgent "~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"

EOF
}

# Unused function due to switching to _1password variant,
# but saved for reference when needing local keys
init_ssh_local () {
  if ! test -d "${HOME}/.ssh"; then
    mkdir -m go= "${HOME}/.ssh"
    e="$(ask 'New SSH Key: Email Address?' 'OK' '')"
    ssh-keygen -t ed25519 -a 100 -C "$e"
    cat << EOF > "${HOME}/.ssh/config"
Host *
  AddKeysToAgent yes
  UseKeychain yes
  IdentityFile ~/.ssh/id_ed25519
EOF
    ssh-add ~/.ssh/id_ed25519
    pbcopy < "${HOME}/.ssh/id_ed25519.pub"
    open "https://github.com/settings/keys"
  fi
}

# Configure New Account
init_new_account () {
  e="$(ask 'New macOS Account: Email Address?' 'OK' '')"
  curl --output "/Library/User Pictures/${e}.jpg" --silent \
    "https://www.gravatar.com/avatar/$(md5 -qs ${e}).jpg?s=512"

  g="$(curl --location --silent \
    "https://api.github.com/search/users?q=${e}" | \
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
init_guest () {
  sudo sysadminctl -guestAccount off
}

# Reinstate =sudo= Password
init_rm_sudoers () {
  sudo -- sh -c \
    "rm -f /etc/sudoers.d/wheel; dscl /Local/Default -delete /Groups/wheel GroupMembership $(whoami)"

  /usr/bin/read -n 1 -p "Press any key to continue.
" -s
  if run "Log Out Then Log Back In?" "Cancel" "Log Out"; then
    osascript -e 'tell app "loginwindow" to «event aevtrlgo»'
  fi
}

