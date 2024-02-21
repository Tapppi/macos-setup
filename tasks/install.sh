#!/bin/sh

# Ask for the administrator password upfront
sudo -v

# Define Function =install=

install () {
  # Keep-alive: update existing `sudo` time stamp until `.macos` has finished
  while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &
  
  init_no_sleep

  install_macos_sw
  install_node_sw
  #install_perl_sw # segfaults for some reason
  install_python_sw
  install_ruby_sw
  install_dotfiles
}

# Install macOS Software with =brew=

install_macos_sw () {
  install_paths
  install_brew

  p1 "Installing macOS Software"

  config_xcode

  brew bundle --file="Brewfiles/core"

  config_xcode

  # Set librdkafka openssl build flags
  export CPPFLAGS=-I/usr/local/opt/openssl@1.1/include
  export LDFLAGS=-L/usr/local/opt/openssl@1.1/lib
  
  brew bundle --file="Brewfiles/languages"

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
    ruby -e \
      "$(curl -Ls 'https://github.com/Homebrew/install/raw/master/install')" \
      < /dev/null > /dev/null 2>&1
  fi

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

_npm='npm
ts-node
nodemon'

install_node_sw () {
  goback_dir=$(pwd)
  if [ ! -z "$NVM_DIR" ] && ls $NVM_DIR &> /dev/null; then
    p1 "Update nvm"
    cd "$NVM_DIR"
    git fetch --tags origin
    git checkout `git describe --abbrev=0 --tags --match "v[0-9]*" $(git rev-list --tags --max-count=1)`
    . "$NVM_DIR/nvm.sh";
  else
    p1 "Install nvm"
    export NVM_DIR="$HOME/.nvm"

    sudo mkdir -p "$NVM_DIR"
    sudo chown -R "$(whoami):admin" "$NVM_DIR"

    git clone https://github.com/creationix/nvm.git "$NVM_DIR"
    cd "$NVM_DIR"
    git checkout `git describe --abbrev=0 --tags --match "v[0-9]*" $(git rev-list --tags --max-count=1)`
    . "$NVM_DIR/nvm.sh";
  fi

  cd "$goback_dir"

  if ls $NVM_DIR &> /dev/null; then
    p1 "Installing Node.js with nvm"
    nvm install --lts
    nvm use --lts
    nvm alias default stable
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

    p1 "Installing Perl 5 with plenv"
    plenv install 5.38.2 > /dev/null 2>&1
    plenv global 5.38.2

    grep -q "${PLENV_ROOT}" "/etc/paths" || \
    sudo sed -i "" -e "1i\\
${PLENV_ROOT}/shims
" "/etc/paths"

    init_paths
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

    p1 "Installing Python 2 with pyenv"
    pyenv install --skip-existing 2.7.18
    p1 "Installing Python 3 with pyenv"
    pyenv install --skip-existing 3.12.2

    p1 "Setting Python 3 as the default"
    pyenv global 3.12.2

    p1 "Install pip & utilities"
    grep -q "${PYENV_ROOT}" "/etc/paths" || \
    sudo sed -i "" -e "1i\\
${PYENV_ROOT}/shims
" "/etc/paths"

    init_paths

    pip install --upgrade "pip" "setuptools"

    # Reference: https://github.com/pixelb/crudini
    pip install --upgrade "crudini"

    p1 "Install aiven-client"
    # Reference: https://github.com/aiven/aiven-client
    pip install --upgrade "aiven-client"
  fi
}

# Install Ruby with =rbenv=

install_ruby_sw () {
  if which rbenv > /dev/null; then
    RBENV_ROOT="/usr/local/ruby" && export RBENV_ROOT

    sudo mkdir -p "$RBENV_ROOT"
    sudo chown -R "$(whoami):admin" "$RBENV_ROOT"

    p1 "Installing Ruby with rbenv"
    rbenv install --skip-existing 3.2.3
    rbenv global 3.2.3

    grep -q "${RBENV_ROOT}" "/etc/paths" || \
    sudo sed -i "" -e "1i\\
${RBENV_ROOT}/shims
" "/etc/paths"

    init_paths;

    printf "%s\n" \
      "gem: --no-document" | \
    tee "${HOME}/.gemrc" > /dev/null;

    yes | gem update --system  > /dev/null;

    yes | gem update;

    yes | gem install bundler;
  fi
}

# Cleanup conflicting binaries for ruby update

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
