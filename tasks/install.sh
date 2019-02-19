#!/bin/sh

config_xcode() {
  x="$(find '/Applications' -maxdepth 1 -regex '.*/Xcode[^ ]*.app' -print -quit)"
  if test -n "${x}"; then
    sudo xcode-select -s "${x}"
    sudo xcodebuild -license accept
  fi
}

# Define Function =install=

install () {
  install_macos_sw
  install_node_sw
  install_perl_sw
  install_python_sw
  install_ruby_sw
  install_dotfiles

  config_admin_req
  config_istat_menus
  config_vlc
  config_macos
}

# Install macOS Software with =brew=

install_macos_sw () {
  p "Installing macOS Software"
  install_paths
  install_brew

  brew bundle --file="Brewfiles/core"

  # Set librdkafka openssl build flags
  export CPPFLAGS=-I/usr/local/opt/openssl/include
  export LDFLAGS=-L/usr/local/opt/openssl/lib
  
  brew bundle --file="Brewfiles/languages"

  brew bundle --file="Brewfiles/software"

  BREW_PREFIX=$(brew --prefix)

  # Standard "g*" name for sha256sum
  ln -s "${BREW_PREFIX}/bin/gsha256sum" "${BREW_PREFIX}/bin/sha256sum"

  # Switch to using brew-installed bash as default shell
  if ! fgrep -q "${BREW_PREFIX}/bin/bash" /etc/shells; then
    echo "${BREW_PREFIX}/bin/bash" | sudo tee -a /etc/shells;
    chsh -s "${BREW_PREFIX}/bin/bash";
  fi;

  setup_xcode

  install_links
  sudo xattr -rd "com.apple.quarantine" "/Applications" > /dev/null 2>&1
  sudo chmod -R go=u-w "/Applications" > /dev/null 2>&1
}

# Add =/usr/local/bin/sbin= to Default Path

install_paths () {
  if ! grep -Fq "/usr/local/sbin" /etc/paths; then
    sudo sed -i "" -e "/\/usr\/sbin/{x;s/$/\/usr\/local\/sbin/;G;}" /etc/paths
  fi
}

# Install Homebrew Package Manager

install_brew () {
  if ! which brew > /dev/null; then
    ruby -e \
      "$(curl -Ls 'https://github.com/Homebrew/install/raw/master/install')" \
      < /dev/null > /dev/null 2>&1
  fi
  printf "" > "${BREWFILE}"
  brew analytics off
  brew update
  brew doctor
  brew tap "homebrew/bundle"
}

# Link System Utilities to Applications

_links='/System/Library/CoreServices/Applications
/Applications/Xcode.app/Contents/Applications
/Applications/Xcode.app/Contents/Developer/Applications
/Applications/Xcode-beta.app/Contents/Applications
/Applications/Xcode-beta.app/Contents/Developer/Applications'

install_links () {
  printf "%s\n" "${_links}" | \
  while IFS="$(printf '\t')" read link; do
    find "${link}" -maxdepth 1 -name "*.app" -type d -print0 2> /dev/null | \
    xargs -0 -I {} -L 1 ln -s "{}" "/Applications" 2> /dev/null
  done
}

# Install Node.js with =nvm=

_npm='´ts-node
nodemon'

install_node_sw () {
  if which nvm > /dev/null; then
    p "Update nvm"
    cd "$NVM_DIR"
    git fetch --tags origin
    git checkout `git describe --abbrev=0 --tags --match "v[0-9]*" $(git rev-list --tags --max-count=1)`
    . "$NVM_DIR/nvm.sh"
  else
    p "Install nvm"
    export NVM_DIR="$HOME/.nvm"

    sudo mkdir -p "$NVM_DIR"
    sudo chown -R "$(whoami):admin" "$NVM_DIR"

    git clone https://github.com/creationix/nvm.git "$NVM_DIR"
    cd "$NVM_DIR"
    git checkout `git describe --abbrev=0 --tags --match "v[0-9]*" $(git rev-list --tags --max-count=1)`
    . "$NVM_DIR/nvm.sh"
  fi

  if which nvm > /dev/null; then
    p "Installing Node.js with nvm"
    nvm install --lts
    nvm use --lts
    nvm alias default lts
  fi

  T=$(printf '\t')

  printf "%s\n" "$_npm" | \
  while IFS="$T" read pkg; do
    npm install --global "$pkg"
  done
}

# Install Perl 5 with =plenv=

install_perl_sw () {
  if which plenv > /dev/null; then
    PLENV_ROOT="/usr/local/perl" && export PLENV_ROOT

    sudo mkdir -p "$PLENV_ROOT"
    sudo chown -R "$(whoami):admin" "$PLENV_ROOT"

    p "Installing Perl 5 with plenv"
    plenv install 5.26.0 > /dev/null 2>&1
    plenv global 5.26.0

    grep -q "${PLENV_ROOT}" "/etc/paths" || \
    sudo sed -i "" -e "1i\\
${PLENV_ROOT}/shims
" "/etc/paths"

    init_paths
    rehash
  fi
}

# Install Python with =pyenv=

install_python_sw () {
  if which pyenv > /dev/null; then
    CFLAGS="-I$(brew --prefix openssl)/include" && export CFLAGS
    LDFLAGS="-L$(brew --prefix openssl)/lib" && export LDFLAGS
    PYENV_ROOT="/usr/local/python" && export PYENV_ROOT

    sudo mkdir -p "$PYENV_ROOT"
    sudo chown -R "$(whoami):admin" "$PYENV_ROOT"

    p "Installing Python 2 with pyenv"
    pyenv install --skip-existing 2.7.13
    p "Installing Python 3 with pyenv"
    pyenv install --skip-existing 3.6.2
    pyenv global 2.7.13

    grep -q "${PYENV_ROOT}" "/etc/paths" || \
    sudo sed -i "" -e "1i\\
${PYENV_ROOT}/shims
" "/etc/paths"

    init_paths
    rehash

    pip install --upgrade "pip" "setuptools"

    # Reference: https://github.com/aiven/aiven-client
    pip install --upgrade "aiven-client"

    # Reference: https://github.com/pixelb/crudini
    pip install --upgrade "crudini"
  fi
}

# Install Ruby with =rbenv=

install_ruby_sw () {
  if which rbenv > /dev/null; then
    RBENV_ROOT="/usr/local/ruby" && export RBENV_ROOT

    sudo mkdir -p "$RBENV_ROOT"
    sudo chown -R "$(whoami):admin" "$RBENV_ROOT"

    p "Installing Ruby with rbenv"
    rbenv install --skip-existing 2.4.2
    rbenv global 2.4.2

    grep -q "${RBENV_ROOT}" "/etc/paths" || \
    sudo sed -i "" -e "1i\\
${RBENV_ROOT}/shims
" "/etc/paths"

    init_paths
    rehash

    printf "%s\n" \
      "gem: --no-document" | \
    tee "${HOME}/.gemrc" > /dev/null

    gem update --system > /dev/null

    trash "$(which rdoc)"
    trash "$(which ri)"
    gem update

    gem install bundler
  fi
}

# Install dotfiles with =dotfiles/bootstrap.sh=
install_dotfiles () {
  cd "$( dirname "${BASH_SOURCE[0]}" )"
  /bin/bash ../dotfiles/bootstrap.sh -f
  ~/.macos
  cd -;
}

# Define Function =config_defaults=

config_defaults () {
  printf "%s\n" "${1}" | \
  while IFS="$(printf '\t')" read domain key type value host; do
    ${2} defaults ${host} write ${domain} "${key}" ${type} "${value}"
  done
}

# Mark Applications Requiring Administrator Account

_admin_req='Docker.app
Dropbox.app
iStat Menus.app
Wireshark.app'

config_admin_req () {
  printf "%s\n" "${_admin_req}" | \
  while IFS="$(printf '\t')" read app; do
    sudo tag -a "Red, admin" "/Applications/${app}"
  done
}

# Configure VLC

_vlc_defaults='org.videolan.vlc	SUEnableAutomaticChecks	-bool	true	
org.videolan.vlc	SUHasLaunchedBefore	-bool	true	
org.videolan.vlc	SUSendProfileInfo	-bool	true	'
_vlcrc='macosx	macosx-nativefullscreenmode	1
macosx	macosx-video-autoresize	0
macosx	macosx-appleremote	0
macosx	macosx-pause-minimized	1
macosx	macosx-continue-playback	1
core	metadata-network-access	1
core	volume-save	0
core	spdif	1
core	sub-language	English
core	medium-jump-size	30
subsdec	subsdec-encoding	UTF-8
avcodec	avcodec-hw	vda'

config_vlc () {
  config_defaults "${_vlc_defaults}"
  if which crudini > /dev/null; then
    test -d "${HOME}/Library/Preferences/org.videolan.vlc" || \
      mkdir -p "${HOME}/Library/Preferences/org.videolan.vlc"
    printf "%s\n" "${_vlcrc}" | \
    while IFS="$(printf '\t')" read section key value; do
      crudini --set "${HOME}/Library/Preferences/org.videolan.vlc/vlcrc" "${section}" "${key}" "${value}"
    done
  fi
}
