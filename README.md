# macos-setup

> Macos setup automation with basic settings, application installs and dotfiles

## Usage

1. Backup credentials and personal settings by running `backup.sh path/to/backup.tgz`
2. Copy backup and this repo to new mac
3. Run `./setup.sh init`
4. If no non-superadmin account exists yet, run `./setup.sh new_account` and log in with the new user
5. If need keys or creds from backup for install, use `restore.sh path/to/backup.tgz`
6. Run `./setup.sh clean_account` to remove default dock icons
   - Run `./setup.sh init_ssh_1password` or `./setup.sh init_ssh_local` to set up SSH
7. Run `./setup.sh install && ./setup.sh config` and reboot your computer

There are still rough edges and you will probably have to fix something by
hand, but it should still be quicker than starting over or using time machine..

## What it does

### Backup and restore

[`backup.sh`](/backup.sh) does a backup of the home directory files listed in
[`restore.bom`](/restore.bom). It includes hard to generalise plist settings
and credentials. Other files, such as documents are expected to be backed up
to file-sharing, e.g. google drive or dropbox.

Backed up configs include:
- **1Password SSH agent** (`~/.config/1Password/ssh/agent.toml`) — vault allowlist for the SSH agent (which vaults/items are offered to SSH servers)
- **Alfred** — preferences, alfred preferences bundle, workflow data
- **iStat Menus** — menubar and status plist preferences
- **iTerm2** — preferences plist
- **Resolutionator** — preferences plist
- **Postico** — preferences and saved connections
- **Keys/credentials** — `~/.ssl`, `~/.credentials`, `~/.pgpass`

[`restore.sh`](/restore.sh) extracts the backup tarball back to home folder.
To add gpg key to gpg suite, e.g. from 1Password, copy the gpg key to `key_public.asc`
and `key_secret.asc` before running `restore.sh`.

The scripts take the path of the backup tarball as an argument.

### Setup

[`setup.sh`](/setup.sh) orchestrates [`tasks/`](/tasks) for setting up a new
Mac. To use, run `./setup.sh <task>` in the root folder of this repo.

Available tasks:

| Task | Description |
|------|-------------|
| `init` | System init: hostname, updates, xcode devtools, guest off (no account creation) |
| `new_account` | Create a new macOS admin account (run separately if IT hasn't done an account split) |
| `clean_account` | Wipe default dock icons from the new account's Dock |
| `init_ssh_1password` | Write SSH config to use 1Password agent (standalone) |
| `init_ssh_local` | Generate a local SSH key if you don't have one from backup |
| `install` | Install all software and runtimes (also works as update) |
| `dotfiles` | Bootstrap dotfiles only (re-sync without full install) |
| `config` | Apply macOS and application configuration |

#### init

- Sets up and asks for basic info such as hostname
- Installs system updates and Xcode devtools
- Turns off guest account
- Does **not** create a new user account — run `new_account` separately if needed

#### new_account

- Creates a new macOS admin account to replace the default superadmin
- Looks up avatar from Gravatar and name/username from GitHub by email
- Run this when `init` has already been done but no account split has been set up yet
- Skip it if company IT has already created a non-superadmin account for you

#### install

Also works as update — rerun it to update apps and runtimes.

- Installs Brew and all packages from [`Brewfile`](/Brewfile) (Homebrew and
  Mac App Store)
- Sets default shell to Bash 5 instead of ancient Bash from macOS
- Installs lots of GNU utils to supplement and overwrite macOS builtins
- Installs language runtimes with Mise (see
  [`dotfiles/.config/mise/config.toml`](/dotfiles/.config/mise/config.toml)
  for versions)
  - Bun, Go, Node, Python, Ruby, Rust, uv, Zig
  - PHP is installed through Homebrew due to problems in mise install (see
    Brewfile comments)
- Installs crudini and aiven-client with uv tools
- Installs Amphetamine Enhancer from GitHub
- Bootstraps [_dotfiles_ subrepo](https://github.com/tapppi/dotfiles) and
  installs nnn plugins
  - Core dotfiles are in the [`dotfiles` subrepo](https://github.com/tapppi/dotfiles)
  - Personal and extra configs are in this repo at [`.extra`](/.extra),
    [`.path`](/.path) and [`.credentials.dist`](/.credentials.dist)

#### config

- Configures VLC (playback preferences, hardware acceleration, subtitle encoding)
- Sets up a custom Terminal.app profile (InconsolataLGC font, Solarized, 121×35 window)
- Sets default file associations via duti (Neovide for text/code, VLC for
  media, The Unarchiver for archives)
- Registers login items (1Password, Alfred, Amphetamine, Google Drive, Ice,
  iStat Menus, Resolutionator, Slack, Spotify, stts, WhatsApp)
- Tags apps requiring admin rights with a Finder tag (iStat Menus, Wireshark)
- Applies macOS system settings as defined in the `.macos` dotfile
- Launches iStat Menus, Alfred, Amphetamine, and stts for first-run setup

## Manual steps

Not all steps have been automated:

- Disable startup sound: System Settings → Sound → uncheck "Play sound on
  startup" (no scriptable method on Apple Silicon)
- Set up iTerm2 key binds if backups don't restore correctly:
  https://medium.com/@jonnyhaynes/jump-forwards-backwards-and-delete-a-word-in-iterm2-on-mac-os-43821511f0a

## Thanks to...

- [Mathias Bynens](https://mths.be) for his [_dotfiles_ repository](https://github.com/mathiasbynens/dotfiles)
  which is the upstream for the fork used here
- @ptb and [his _macOS Setup_ repository](https://github.com/ptb/mac-setup)
  for inspiration and basis for installation scripts
- @bkuhlamnn and [his _mac_os(-config)_ repositories](https://github.com/bkuhlmann/mac_os-config)
  and [_dotfiles_ repository](https://github.com/bkuhlmann/dotfiles)
  for inspiration and prior-art as well as some useful utility functions
