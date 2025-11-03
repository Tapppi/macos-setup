#!/bin/sh

# Ask for the administrator password upfront
sudo -v

# Define Function =install=

install () {
  # Keep-alive: update existing `sudo` time stamp until `.macos` has finished
  while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

  install_macos_sw
  install_mise_runtimes
  install_dotfiles
}

# Define Function =install_xcode=
install_xcode() {
  x="$(find '/Applications' -maxdepth 1 -regex '.*/Xcode[^ ]*.app' -print -quit)"
  if test -n "${x}"; then
    sudo xcode-select -s "${x}"
    sudo xcodebuild -license accept
  fi
}

# Install macOS Software with =brew=
install_macos_sw () {
  install_paths
  install_brew

  p1 "Installing macOS Software"

  install_xcode

  # Fix Homebrew permissions for fish directory
  if [ -d "/usr/local/share/fish" ]; then
    sudo chown -R "$(whoami):admin" /usr/local/share/fish
  fi

  # Set librdkafka openssl build flags
  export CPPFLAGS=-I/usr/local/opt/openssl@3/include
  export LDFLAGS=-L/usr/local/opt/openssl@3/lib

  brew bundle --file="Brewfiles/core"

  install_xcode

  brew bundle --file="Brewfiles/software"

  BREW_PREFIX=$(brew --prefix)

  # Switch to using brew-installed Bash 5 as default shell
  if ! fgrep -q "${BREW_PREFIX}/bin/bash" /etc/shells; then
    echo "${BREW_PREFIX}/bin/bash" | sudo tee -a /etc/shells
  fi
  if [ "$SHELL" != "${BREW_PREFIX}/bin/bash" ]; then
    chsh -s "${BREW_PREFIX}/bin/bash"
  fi

  install_links
  install_amphetamine_enhancer
  # sudo xattr -rd "com.apple.quarantine" "/Applications" > /dev/null 2>&1
  # sudo chmod -R go=u-w "/Applications" > /dev/null 2>&1
}

# Add =/usr/local/bin/sbin= to Default Path
install_paths () {
  if ! grep -Fq "/usr/local/sbin" /etc/paths; then
    sudo sed -i "" -e "/\/usr\/sbin/{x;s/$/\/usr\/local\/sbin/;G;}" /etc/paths
  fi
}

# Install Homebrew Package Manager
install_brew () {
  p1 "Installing and/or configuring brew"
  if ! which brew > /dev/null; then
	p1 "Installing brew..."
    ruby -e \
      "$(curl -Ls 'https://github.com/Homebrew/install/raw/master/install')" \
      < /dev/null > /dev/null 2>&1
  else
	p1 "Brew already installed"
  fi

  p1 "Update brew..."
  brew analytics off
  brew update
  brew doctor
}

# Link System Utilities to Applications
_links='/System/Library/CoreServices/Applications
/Applications/Xcode.app/Contents/Applications
/Applications/Xcode.app/Contents/Developer/Applications
/Applications/Xcode-beta.app/Contents/Applications
/Applications/Xcode-beta.app/Contents/Developer/Applications'

install_links () {
  echo "Install links to System Utilities in Applications..."
  printf "%s\n" "${_links}" | \
  while IFS="$(printf '\t')" read link; do
    find "${link}" -maxdepth 1 -name "*.app" -type d -print0 2> /dev/null | \
    xargs -0 -I {} -L 1 ln -s "{}" "/Applications" 2> /dev/null
  done
}

install_amphetamine_enhancer () {
  if [ ! -f "/Applications/Amphetamine Enhancer.app" ]; then
    echo "Install amphetamine enhancer through /tmp"
    goback_dir=$(pwd)

    cd /tmp

    curl -sSL -o "Amphetamine Enhancer.dmg" https://github.com/x74353/Amphetamine-Enhancer/raw/master/Releases/Current/Amphetamine%20Enhancer.dmg
    hdiutil attach -quiet Amphetamine\ Enhancer.dmg
    cp -R /Volumes/Amphetamine\ Enhancer/Amphetamine\ Enhancer.app /Applications
    hdiutil detach -quiet /Volumes/Amphetamine\ Enhancer
    rm -rf Amphetamine\ Enhancer.dmg

    cd "$goback_dir"
  fi
}

install_mise_runtimes () {
  # Check if brew is installed first
  if ! which brew > /dev/null; then
    p1 "ERROR: brew not found. Please install Homebrew first."
    return 1
  fi

  # Check if mise is installed via brew
  MISE_PREFIX="$(brew --prefix mise 2>/dev/null)"
  if [ -z "$MISE_PREFIX" ] || [ ! -f "$MISE_PREFIX/bin/mise" ]; then
    p1 "ERROR: mise not found. Please run 'brew install mise' first."
    return 1
  fi

  p1 "Installing language runtimes with mise"

  # Ensure mise is activated in the current shell
  eval "$(mise activate bash)"

  # Install Node.js (LTS version)
  p1 "Installing Node.js LTS"
  mise use -g node@lts

  # Install uv (Python package manager)
  p1 "Installing uv latest"
  mise use -g uv@latest

  # Install Python (latest LTS/stable, but now pinned to 3.13 for compatiblity)
  p1 "Installing Python latest"
  mise use -g python@latest

  # Install Ruby (latest stable)
  p1 "Installing Ruby latest"
  mise use -g ruby@latest

  # Install Go (latest stable)
  p1 "Installing Go latest"
  mise use -g go@latest

  # Install Rust (latest stable)
  p1 "Installing Rust latest"
  mise use -g rust@latest

  # Install Zig (latest)
  p1 "Installing Zig latest"
  mise use -g zig@latest

  # Install PHP (latest stable)
  p1 "Installing PHP latest"
  mise use -g php@latest

  p1 "Installing Python utilities aiven-client and crudini with uv"
  # Reference: https://github.com/pixelb/crudini
  uv tool install "crudini"
  # Reference: https://github.com/aiven/aiven-client
  uv tool install "aiven-client"

  # Configure gem to not generate documentation to make it faster
  printf "%s\n" \
    "gem: --no-document" | \
  tee "${HOME}/.gemrc" > /dev/null

  # This is slow, I don't really think we need to be updating system gems on every install..
  # yes | gem update --system > /dev/null
  # yes | gem update
  # yes | gem install bundler
}

# Cleanup conflicting binaries for ruby update
# Note: This function is kept for backwards compatibility but may not be needed with mise
clean_ruby_conflicts () {
    if which bundle > /dev/null; then
        trash "$(which bundle)";
    fi
    if which rdoc > /dev/null; then
        trash "$(which rdoc)";
    fi
    if which ri > /dev/null; then
        trash "$(which ri)";
    fi
}

# Install dotfiles with =dotfiles/bootstrap.sh=
install_dotfiles () {
  p1 "Installing dotfiles"
  sudo ./dotfiles/bootstrap.sh -f

  cp ./{.extra,.path} ~/
}

