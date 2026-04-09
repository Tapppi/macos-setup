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

# Tailscale VPN for my personal network
cask "tailscale-app"

# Bash 5
brew "bash"
brew "bash-completion@2"

# Fish shell
brew "fish"

# Oh my zsh - the Z-shell
brew "zsh"
brew "zsh-syntax-highlighting"
brew "zsh-history-substring-search"

# Watch something happen by running a command multiple times
brew "watch"

# Safe rm so I don't delete my laptop
brew "safe-rm"

# Compilers and helpers
brew "autoconf"
brew "cmake"
brew "gcc"
brew "pkgconf"

# Podman
brew "podman"
brew "podman-compose"
cask "podman-desktop"

# Mise for installing languages and runtimes, e.g. Node, go, rust, etc.
brew "mise"
# Install php through brew as the mise install requires 3rd party sources or a rebuild of 10m with
# a ton of deps
brew "php"

# Git version control
brew "git"
brew "git-lfs"
brew "diff-so-fancy"
cask "kdiff3"

# Openss* since macos is really bad about providing them
brew "openssl@3"
brew "openssh"

# GnuPG to enable PGP-signing commits.
brew "gnupg"
brew "pinentry-mac"

# Select default apps for documents and URL schemes
brew "duti"

# C++ kafka lib that works as basis for node-rdkafka etc.
brew "librdkafka"

## Media
brew "ffmpeg"
brew "mpv"

## ========================================================
## SOFTWARE
## ========================================================

cask_args appdir: "/Applications"
cask_args colorpickerdir: "/Library/ColorPickers"
cask_args fontdir: "/Library/Fonts"
cask_args input_methoddir: "/Library/Input Methods"
cask_args qlplugindir: "/Library/QuickLook"
cask_args screen_saverdir: "/Library/Screen Savers"

## Taps
tap "boz/repo"
tap "anomalyco/tap"
tap "sqitchers/sqitch"
tap "teamookla/speedtest"

## Basics

# Browsers
cask "brave-browser"
cask "google-chrome"
cask "firefox"
cask "tor-browser"

# GPG suite
cask "gpg-suite"

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

# Terminal AI Agents
cask "claude-code"
cask "codex"
brew "anomalyco/tap/opencode"

# Monitor for AI Agent subscription usage limits
cask "claudebar"

# Documentation finders (dash for API/libs and tldr for short man)
cask "dash"
brew "tlrc"

# Archive extraction
brew "p7zip"
cask "the-unarchiver"

# Mac keyboard layout editor
cask "ukelele"

# File compression for gzip/deflate if tar isn't enough
# DEPRECATED, could use zstd or brotli depending on use-case?
# brew "zopfli"

# Notification on long running tasks
brew "noti"

## Everyday apps
cask "slack"
cask "microsoft-teams"
cask "vlc"
cask "whatsapp"
cask "spotify"
cask "google-drive"
cask "1password"
cask "1password-cli"
mas "Keynote", id: 409183694
mas "Numbers", id: 409203825
mas "Pages", id: 409201541
mas "WiFi Explorer Lite", id: 1408727408
# Pro, but it's expensive
# mas "Wifi Explorer", id: 494803304

# Notes
cask "notion"
cask "obsidian"

# API testing
cask "postman"
cask "yaak"

# The butler of your mac or the swiss army knife of searchbars - https://www.alfredapp.com/
cask "alfred"

# Background noise
# Not available in the Finnish app store :(
# mas "Coffitivity", id: 659901392

# Manage resolutions
cask "resolutionator"

# Status bar organiser
cask "jordanbaird-ice"

# Status bar stats and menus
cask "istat-menus"

# Torrent client
cask "transmission"

# GUI for open files and sockets
cask "sloth"

# Various cloud service statuses in the status bar
mas "stts", id: 1187772509

## Everyday commandline tools

# Nice httpie client
brew "httpie"

# Github CLI
brew "gh"

# Lazygit for Git TUI
brew "lazygit"

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

# JSON and yaml query/mangling/cleanup/prettify tool
brew "jq"
brew "yq"

# Count lines of code
brew "cloc"

# Shell script linter
brew "shellcheck"

# Monitor data passing through a pipe - pipeviewer - https://www.ivarch.com/programs/pv.shtml
brew "pv"

# Rename files with a bunch of helpers - http://plasmasturm.org/code/rename/
brew "rename"

# Display directories as trees
brew "tree"

# Expose local ports to the internet - https://ngrok.com/
cask "ngrok"

# Autocorrecting for the commandline
brew "thefuck"

# Terminal windows you know and love, and a manager for sessions
brew "tmux"
brew "tmuxinator"

# View file/folder size and delete them, like `du` but with a UX
brew "ncdu"

# Terminal file manager - https://github.com/jarun/nnn
brew "nnn"

# Moves files or folders to trash
brew "trash"

# Manipulate macos file tags
brew "tag"

# Parallel gzip
brew "pigz"

# Ghostscript for pdf utility scripts
brew "ghostscript"

## Infra tooling
cask "gcloud-cli"
brew "azure-cli"
brew "azcopy"
brew "hashicorp/tap/terraform"
brew "hashicorp/tap/terraform-ls"
brew "helm"

# Secrets file encryption (age, PGP, AWS KMS, GCP KMS, etc.)
brew "sops"

# TLS tunnel wrapper for adding TLS to non-TLS connections
brew "stunnel"

# Overview of resources and exploratory UI - https://github.com/derailed/k9s
brew "k9s"

# Watch a resource (e.g. deployment being rolled out, pod status) - https://github.com/pulumi/kubespy
brew "kubespy"

# Tail the logs of some collection of resources
brew "boz/repo/kail"

## Databases, except it's just SQLITE because everything else is in containers now
brew "sqlite"

# PostgreSQL client only (psql, pg_dump etc.) - keg-only, no server daemon
brew "libpq"

# Redis client (redis-cli) - don't run 'brew services start redis'
brew "redis"

## Database GUI

# Redis
mas "Medis", id: 1063631769

# Postgres
cask "postico@1"
cask "datagrip"

## Database tooling

# DuckDB cli, which is not available through uv or such
brew "duckdb"

# Kafkacat for reading kafka data
brew "kcat"

# Postgres schema management with sqitch
brew "sqitchers/sqitch/sqitch", args: ["with-postgres-support"]

## Debug and CTF tools; see https://github.com/ctfs/write-ups

# 'traceroute' and 'ping' in a single tool
brew "mtr"

# Network map, port scanning utility
brew "nmap"

cask "wireshark-app"

## Font tools
# tap "bramstein/webfonttools"

# brew "sfnt2woff"
# brew "sfnt2woff-zopfli"
# brew "woff2"

## Random apps

# Kapture your screen
cask "kap"

# Markdown presentation writer (requires node; opencode also depends on brew node)
brew "node"
brew "marp-cli"

# Menubar countdown clock
mas "Countdown", id: 6744842468

# Amphetamine to keep awake when wanted
mas "Amphetamine", id: 937984704

# Image transcoding etc.
cask "handbrake-app"

# Pls no exif
cask "exifrenamer"
brew "exiftool"

# Rename files with GUI
cask "namechanger"

## Random command-line utilities and tools

# Do pretty much anything to images - https://www.imagemagick.org/
brew "imagemagick"

# Download YouTube videos from the command line
brew "yt-dlp"

# Let's encrypt tooling
brew "certbot"

# Create bootable flash media, especially for raspberry pis
cask "raspberry-pi-imager"

# Copy ssh public key to server
brew "ssh-copy-id"

# Optical character recognition tool
brew "tesseract"

# The unix spell checker
brew "aspell"

# Utilities for ext2, ext3, ext4 fs
brew "e2fsprogs"

# Find duplicate files
brew "fdupes"

# Do something when mac goes to sleep/wakes up/etc.
brew "sleepwatcher"

# Go fast
brew "teamookla/speedtest/speedtest"

# Go ham
brew "cmatrix"
