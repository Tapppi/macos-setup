#!/bin/bash

# Define Function =install=

install () {
  install_macos_sw
  install_mise_runtimes
  install_dotfiles
}

# Define Function =install_xcode=
install_xcode() {
  p2 "Check xcode installation..."
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
install_macos_sw () {
  p1 "Installing macOS Software..."

  install_xcode

  p2 "Check permissions and paths..."
  # Fix Homebrew permissions for fish directory
  if [ -d "/usr/local/share/fish" ]; then
    sudo chown -R "$(whoami):admin" /usr/local/share/fish
  fi
  install_paths

  # Set librdkafka openssl build flags
  export CPPFLAGS=-I/usr/local/opt/openssl@3/include
  export LDFLAGS=-L/usr/local/opt/openssl@3/lib

  install_brew

  install_xcode

  # Switch to using brew-installed Bash 5 as default shell
  BREW_PREFIX=$(brew --prefix)
  if ! grep -F -q "${BREW_PREFIX}/bin/bash" /etc/shells; then
    echo "${BREW_PREFIX}/bin/bash" | sudo tee -a /etc/shells
  fi
  if [ "$SHELL" != "${BREW_PREFIX}/bin/bash" ]; then
    chsh -s "${BREW_PREFIX}/bin/bash"
  fi

  install_links
  install_amphetamine_enhancer
}

# Add =/usr/local/bin/sbin= to Default Path
install_paths () {
  if ! grep -Fq "/usr/local/sbin" /etc/paths; then
    p2 "Add /usr/local/sbin to /etc/paths"
    sudo sed -i "" -e "/\/usr\/sbin/{x;s/$/\/usr\/local\/sbin/;G;}" /etc/paths
  fi
}

# Install Software with Homebrew Package Manager
# brew commands invalidate sudo timestamp in order to prevent builds from using sudo
# if there is a need for sudo after brew installation, we'll just have to re-enter password
install_brew () {
  p2 "Installing and/or configuring brew"
  if ! which brew > /dev/null; then
	p2 "Installing brew..."
    ruby -e \
      "$(curl -Ls 'https://github.com/Homebrew/install/raw/master/install')" \
      < /dev/null > /dev/null 2>&1
  else
	p3 "Brew already installed"
  fi

  p3 "Brew update and doctor..."
  brew analytics off
  brew update
  brew doctor

  p3 "Install Brewfile..."
  brew bundle --file="Brewfile"

  p2 "Brew installation done!"
}

# Link System Utilities to Applications
_links='/System/Library/CoreServices/Applications
/Applications/Xcode.app/Contents/Applications
/Applications/Xcode.app/Contents/Developer/Applications
/Applications/Xcode-beta.app/Contents/Applications
/Applications/Xcode-beta.app/Contents/Developer/Applications'

install_links () {
  p2 "Install links to System Utilities in Applications..."
  printf "%s\n" "${_links}" | \
  while IFS="$(printf '\t')" read link; do
    find "${link}" -maxdepth 1 -name "*.app" -type d -print0 2> /dev/null | \
    xargs -0 -I {} -L 1 ln -s "{}" "/Applications" 2> /dev/null
  done
  p3 "Installed links!"
}

install_amphetamine_enhancer () {
  if [ ! -d "/Applications/Amphetamine Enhancer.app" ]; then
    p2 "Install Amphetamine Enhancer through /tmp"
    goback_dir=$(pwd)

    cd /tmp

    curl -sSL -o "Amphetamine Enhancer.dmg" https://github.com/x74353/Amphetamine-Enhancer/raw/master/Releases/Current/Amphetamine%20Enhancer.dmg
    hdiutil attach -quiet Amphetamine\ Enhancer.dmg
    cp -R /Volumes/Amphetamine\ Enhancer/Amphetamine\ Enhancer.app /Applications
    hdiutil detach -quiet /Volumes/Amphetamine\ Enhancer
    rm -rf Amphetamine\ Enhancer.dmg

    cd "$goback_dir"

    p3 "Ampthetamine Enhancer installed!"
    open "/Applications/Amphetamine Enhancer.app"
  fi
}

install_mise_runtimes () {
  p2 "Installing language runtimes with mise..."

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

  # Ensure mise is activated in the current shell
  eval "$(mise activate bash)"

  # Install Node.js (LTS version)
  p3 "Installing Node.js LTS..."
  mise use -g node@lts

  # Install uv (Python package manager)
  p3 "Installing uv latest..."
  mise use -g uv@latest

  # Install Python (latest LTS/stable, but now pinned to 3.13 for compatiblity)
  p3 "Installing Python latest..."
  mise use -g python@latest

  # Install Ruby (latest stable)
  p3 "Installing Ruby latest..."
  mise use -g ruby@latest

  # Install Go (latest stable)
  p3 "Installing Go latest..."
  mise use -g go@latest

  # Install Rust (latest stable)
  p3 "Installing Rust latest..."
  mise use -g rust@latest

  # Install Zig (latest)
  p3 "Installing Zig latest..."
  mise use -g zig@latest

  p2 "Installing Python utilities aiven-client and crudini with uv"
  # Reference: https://github.com/pixelb/crudini
  uv tool install "crudini"
  # Reference: https://github.com/aiven/aiven-client
  uv tool install "aiven-client"

  p2 "Configure gem"
  # Configure gem to not generate documentation to make it faster
  printf "%s\n" \
    "gem: --no-document" | \
  tee "${HOME}/.gemrc" > /dev/null

  # This is slow, I don't really think we need to be updating system gems on every install..
  # yes | gem update --system > /dev/null
  # yes | gem update
  # yes | gem install bundler

  p2 "Mise installations done!"
}

# Install dotfiles with =dotfiles/bootstrap.sh=
install_dotfiles () {
  p1 "Installing dotfiles..."
  ./dotfiles/bootstrap.sh -f

  cp ./{.extra,.path} ~/.config/bash/
}
