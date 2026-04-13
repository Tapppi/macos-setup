# macos-setup

> Macos setup automation with basic settings, application installs and dotfiles

## Usage

1. Backup credentials and personal settings by running `backup.sh /path/to/backups backup-name`
2. Copy backup and this repo to new mac
3. Run `./setup.sh init` to set hostname, permissions, updates and disable guest
4. If no non-superadmin account exists yet, run `./setup.sh new_account` to create one, then log in with it
5. If you need local keys or credentials from backup for install, use `restore.sh /path/to/backups/backup-name.<timestamp>.tar.gz`
6. Run `./setup.sh clean_account` to remove default dock icons
   - Run `./setup.sh init_ssh_1password` if you use 1Password SSH signing, or `./setup.sh init_ssh_local` if you use a local key
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
- **Alfred** — preferences, alfred preferences bundle, workflow data
- **iStat Menus** — menubar and status plist preferences
- **iTerm2** — preferences plist
- **Resolutionator** — preferences plist
- **Postico** — preferences and saved connections
- **Tmuxinator** — session/project configs from `~/.config/tmuxinator`
- **Keys/credentials** — `~/.ssl`, `~/.credentials`, `~/.pgpass`

[`restore.sh`](/restore.sh) extracts the backup tarball back to home folder.

`backup.sh` takes an output directory and base name, then writes a timestamped
gzip-compressed tar archive using the format `{path}/{name}.{timestamp}.tar.gz`.
`restore.sh` takes the path to that archive and restores it back to the home
folder. `restore.sh` restores gzip-compressed tar archives with `.tar.gz` or
`.tgz` extensions.

#### 1Password-managed signing keys

This repo does not export or restore GPG private keys as part of the backup
tarball. Signing keys are expected to stay in 1Password instead, and the
1Password SSH agent allowlist is recreated with `./setup.sh init_ssh_1password`
instead of being restored from backup.

On a fresh Mac:

1. Sign in to 1Password and confirm your signing key is available in the vault.
2. Run `./setup.sh init_ssh_1password` to point SSH at the 1Password agent and write an `agent.toml` allowlist for the vaults you enter.
3. Bootstrap your dotfiles or run the full install so Git picks up the existing
   1Password SSH signing configuration.
4. If Git signing prompts appear, approve them in 1Password the first time you
   sign or use the key.

### Setup

[`setup.sh`](/setup.sh) orchestrates [`tasks/`](/tasks) for setting up a new
Mac. To use, run `./setup.sh <task>` in the root folder of this repo.

Available tasks:

| Task | Description |
|------|-------------|
| `init` | System init: temporary sudo, hostname, permissions, updates, guest off (no account creation) |
| `new_account` | Create a new macOS admin account (run separately if IT hasn't done an account split) |
| `clean_account` | Wipe default dock icons from the new account's Dock |
| `init_ssh_1password` | Write SSH config and interactive 1Password vault allowlist (standalone) |
| `init_ssh_local` | Generate a local SSH key if you don't have one from backup |
| `install` | Install all software and runtimes (also works as update) |
| `dotfiles` | Bootstrap dotfiles only (re-sync without full install) |
| `config` | Apply macOS and application configuration |

#### init

- Sets up temporary passwordless sudo for the setup duration
- Asks for and sets hostname (ComputerName, HostName, LocalHostName)
- Sets permissions on install destinations (supports Intel and Apple Silicon)
- Installs macOS updates
- Turns off guest account
- Removes temporary sudo rule and prompts to log out
- Does **not** create a new user account — run `new_account` separately if needed

#### new_account

- Creates a new macOS admin account to replace the default superadmin
- Uses `sysadminctl -adminUser/-adminPassword` so the new account gets SecureToken for FileVault
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
- Registers login items (1Password, Alfred, Amphetamine, ClaudeBar, Google
  Drive, Hammerspoon, Ice, iStat Menus, Karabiner-Elements, Resolutionator,
  Slack, Spotify, stts, WhatsApp)
- Tags apps requiring admin rights with a Finder tag (iStat Menus, Wireshark)
- Applies macOS system settings as defined in `tasks/macos.sh`
- Launches iStat Menus, Alfred, Amphetamine, Amphetamine Enhancer, ClaudeBar,
  Google Drive, Hammerspoon, Ice, Karabiner-Elements, Resolutionator,
  Spotify, and stts for first-run setup

## Manual steps

Not all steps have been automated:

- Disable startup sound: System Settings → Sound → uncheck "Play sound on
  startup" (no scriptable method on Apple Silicon)
- Review 1Password security settings and unlock behavior after sign-in. This
  repo configures SSH/signing integration, but app-level security preferences
  still need to be confirmed manually.
- Sign in to Bitwarden and verify any required vault logins or browser/app
  integrations are available on the new machine.
- Set up Brave Sync Chain so browser state can sync back to the new machine.
- Check Alfred preferences and confirm the required macOS permissions are still
  granted after setup.
- Finish the Amphetamine Enhancer helper installation in the GUI after
  `./setup.sh config` opens the app.
- Sign in to Google in macOS Settings so Calendar, Mail, Notes, and Contacts
  sync to the new machine.
- Sign in to Google Drive after `./setup.sh config` opens it, and use Ice if
  you want to hide the Google Drive menu bar icon (Google Drive does not
  expose a native setting for that).
- Launch Podman Desktop and configure the Podman machine plus Podman and
  Compose integrations if you use it for local container tooling.
- Clone `gh:tapppi/tieto` into `~/tieto` if this machine needs the work repo
  locally.
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
