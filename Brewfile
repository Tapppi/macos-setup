cask_args appdir: "/Applications"
cask_args colorpickerdir: "/Library/ColorPickers"
cask_args fontdir: "/Library/Fonts"
cask_args input_methoddir: "/Library/Input Methods"
cask_args qlplugindir: "/Library/QuickLook"
cask_args screen_saverdir: "/Library/Screen Savers"

tap "anomalyco/tap"
tap "hashicorp/tap"
tap "teamookla/speedtest"
tap "slp/krun"

## ========================================================
## CORE
## ========================================================

## Install mac app store cli
brew "mas"

## Install GNU utilities (macOS has missing and outdated tools)
# Don’t forget to add `$(brew --prefix coreutils)/libexec/gnubin` etc. to `$PATH`
brew "coreutils"
brew "findutils"
brew "binutils"
brew "diffutils"
brew "gawk"
brew "gnu-sed"
brew "gnu-getopt"
brew "gnu-tar"
brew "grep"
brew "gzip"
brew "rsync"
brew "zstd"
brew "less"
brew "make"
brew "wget"
brew "gnu-time"

# Execute commands in parallel
brew "parallel"

# Replacements for moreutils tools (ifdata and sponge), because it conflicts with parallel
#
# If we end up wanting 'ts' from moreutils, we'd need to use the following tap,
# but it lags behind in versions
# brew "slhck/moreutils/moreutils", args: ["without-parallel"]
#
# Easier network configuration (ip, bridge, ss) - https://github.com/brona/iproute2mac
brew "iproute2mac"
brew "sponge"

# I don't really use these or the macOS version is sufficient, but here for reference
# brew "gnu-indent"
# brew "gnu-which"
# brew "screen"
# brew "emacs"
# brew "gpatch"
# brew "m4"
# brew "nano"
# brew "bison"
# brew "flex"
# brew "wdiff"

## Core programs and tooling

# XCode
mas "Xcode", id: 497799835

# Bash 5
brew "bash"
brew "bash-completion@2"

# Oh my zsh - the Z-shell
brew "zsh"
brew "zsh-syntax-highlighting"
brew "zsh-history-substring-search"

# Compilers and helpers
brew "autoconf"
brew "cmake"
brew "gcc"
brew "pkgconf"

# Podman
brew "podman"
brew "podman-compose"
brew "krunkit"
cask "podman-desktop"

# Mise for installing languages and runtimes, e.g. Node, go, rust, etc.
brew "mise"
# Install php through brew if needed as the mise install requires 3rd party sources or a rebuild
# of 10m with a ton of deps
# brew "php"

# Openss* since macos is really bad about providing them
brew "openssl@3"
brew "openssh"

# Select default apps for documents and URL schemes
brew "duti"

## ========================================================
## AUTH
## ========================================================

# I mean, if you aren't using a password manager..
cask "1password"
cask "1password-cli"
mas "1Password for Safari", id: 1569813296
cask "bitwarden"

# Keys and identities, although 1Password & Tailscale replace basically all of it

# Copy ssh public key to server
brew "ssh-copy-id"

# GnuPG to enable PGP-signing commits
# Not in use as SSH keys used for git signing and no other use right now
# brew "gnupg"
# brew "pinentry-mac"

# GPG suite
# cask "gpg-suite"

## ========================================================
## BASICS
## ========================================================

# Browsers
cask "brave-browser"
cask "google-chrome"
cask "firefox"
cask "tor-browser"

# Terminal emulators
cask "iterm2"
cask "ghostty"

# Keyboard remapping
cask "karabiner-elements"

# macOS automation (per-app keyboard layout forcing, etc.)
cask "hammerspoon"

# Text editors
brew "micro"
brew "neovim"
cask "cursor"
cask "neovide-app"

# Claude desktop app
cask "claude"

# Terminal AI Agents
cask "claude-code@latest"
cask "codex"
cask "cursor-cli"
brew "anomalyco/tap/opencode"

# Monitor for AI Agent subscription usage limits
cask "claudebar"

# Mac keyboard layout editor, no longer used as I use US layout for prog
# cask "ukelele"
# Configuration application for my Keyboard
cask "uhk-agent"

# Documentation finders (dash for API/libs and tldr for short man)
cask "dash"
brew "tlrc"

## ========================================================
## EVERYDAY APPS
## ========================================================

# Comms are the foundation of teamwork
cask "slack"
cask "microsoft-teams"
cask "whatsapp"
cask "discord"
cask "spotify"

# Office tooling and workspaces
cask "google-drive"
mas "Keynote", id: 409183694
mas "Numbers", id: 409203825
mas "Pages", id: 409201541

# Notes
cask "notion"
cask "obsidian"

# The butler of your mac or the swiss army knife of searchbars - https://www.alfredapp.com/
cask "alfred"

# Manage resolutions
cask "resolutionator"
# Set display resolution from the command line (used by setup.sh config)
tap "jakehilborn/jakehilborn"
brew "displayplacer"

# Status bar organiser
cask "jordanbaird-ice"

# Status bar stats and menus
cask "istat-menus"

# Cloud service statuses in the status bar
mas "stts", id: 1187772509

# Menubar countdown clock
mas "Countdown", id: 6744842468

# Keep the Mac awake when wanted
mas "Amphetamine", id: 937984704

## ========================================================
## CLI AND DEV WORKFLOW
## ========================================================

# Safe rm so I don't delete my laptop
brew "safe-rm"

# Autocorrecting for the commandline
brew "thefuck"

# Terminal windows you know and love, and a manager for sessions
brew "tmux"
brew "tmuxinator"

# Terminal file manager - https://github.com/jarun/nnn
brew "nnn"

# View file/folder size and delete them, like `du` but with a UX
brew "ncdu"
# System resource monitor TUI - https://github.com/aristocratos/btop
brew "btop"
# Better cd - quick access to folders from anywhere - https://github.com/ajeetdsouza/zoxide
brew "zoxide"
# Grep but fast like a Ferrari
brew "ripgrep"
# Simpler to use 'find'
brew "fd"
# Fuzzy matcher
brew "fzf"
# Cat with syntax highlighting etc.
brew "bat"
# Modern ls replacement with colors, git status, icons
brew "eza"
# Watch something happen by running a command multiple times
brew "watch"

# Git version control
brew "git"
brew "git-lfs"
brew "diff-so-fancy"
cask "kdiff3"
# Lazygit for Git TUI
brew "lazygit"
# Github CLI
brew "gh"

# API Clients
brew "curl"
# Nice httpie client
brew "httpie"
cask "yaak"

# JSON and yaml query/mangling/cleanup/prettify tool
brew "jq"
brew "yq"

# Monitor data passing through a pipe - pipeviewer - https://www.ivarch.com/programs/pv.shtml
brew "pv"

# Count lines of code
brew "cloc"
# Shell script linter
brew "shellcheck"

# Notification on long running tasks
brew "noti"
# Do something when mac goes to sleep/wakes up/etc.
# brew "sleepwatcher"

# Go ham
brew "cmatrix"

## ========================================================
## FILE TOOLS
## ========================================================

# Rename files with a bunch of helpers - http://plasmasturm.org/code/rename/
brew "rename"
# Display directories as trees
brew "tree"
# Moves files or folders to trash
brew "trash"
# Find duplicate files
brew "fdupes"
# Manipulate macos file tags
brew "tag"
# The unix spell checker
brew "aspell"

# Markdown presentation writer
brew "marp-cli"
# Ghostscript for pdf utility scripts
brew "ghostscript"

## Archives and File Systems

brew "p7zip"
cask "the-unarchiver"
# Parallel gzip
brew "pigz"
# Create bootable flash media, especially for raspberry pis
cask "raspberry-pi-imager"
# Utilities for ext2, ext3, ext4 fs
brew "e2fsprogs"

## ========================================================
## INFRA AND PLATFORM TOOLING
## ========================================================

## It's just someone else's server
cask "gcloud-cli"
brew "azure-cli"
brew "azcopy"

brew "hashicorp/tap/terraform"
brew "hashicorp/tap/terraform-ls"

# Let's encrypt tooling
brew "certbot"

# Secrets file encryption (age, PGP, AWS KMS, GCP KMS, etc.)
brew "sops"

## Kubernetes
brew "kubernetes-cli"
brew "helm"
# Overview of resources and exploratory UI - https://github.com/derailed/k9s
brew "k9s"
# Watch a resource (e.g. deployment being rolled out, pod status) - https://github.com/pulumi/kubespy
brew "kubespy"
# Tail the logs of some collection of resources
brew "kail"

## ========================================================
## DATABASES AND CLIENTS
## ========================================================

# Databases, except it's just SQLITE because everything else is in containers now
brew "sqlite"

# Redis client (redis-cli) and Medis GUI - don't run 'brew services start redis'
brew "redis"
mas "Medis", id: 1063631769

# Postgres
cask "postico@1"
cask "datagrip"
# PostgreSQL client only (psql, pg_dump etc.) - keg-only, no server daemon
brew "libpq"

# DuckDB cli
brew "duckdb"

# Kafkacat for reading kafka data
brew "kcat"

## ========================================================
## NETWORK
## ========================================================

# See https://github.com/ctfs/write-ups for interesting CTF tooling

# The Wire
cask "wireshark-app"
# GUI for open files and sockets
cask "sloth"

# Go fast
brew "teamookla/speedtest/speedtest"
# 'traceroute' and 'ping' in a single tool
brew "mtr"
# Network map, port scanning utility
brew "nmap"

# Tailscale VPN for my personal overlay network & funnel for exposing ports to interwebs
cask "tailscale-app"

# TLS tunnel wrapper for adding TLS to non-TLS connections
brew "stunnel"

## ========================================================
## AV, MEDIA, IMAGE UTILS
## ========================================================

# Kapture your screen
cask "kap"

# Video transcoding etc.
cask "handbrake-app"

# Pls no exif, tool also does renaming so no GUI right now
brew "exiftool"
# cask "exifrenamer"

cask "vlc"
brew "ffmpeg"
brew "mpv"

# Do pretty much anything to images - https://www.imagemagick.org/
brew "imagemagick"
# Download YouTube videos from the command line
brew "yt-dlp"

# Optical character recognition tool
brew "tesseract"

## ========================================================
## FONT TOOLS
## ========================================================
# tap "bramstein/webfonttools"
# brew "sfnt2woff"
# brew "sfnt2woff-zopfli"
# brew "woff2"
