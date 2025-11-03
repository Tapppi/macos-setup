# macos-setup

> Macos setup automation with basic settings, application installs and dotfiles

## Usage

1. Backup credentials and personal settings by running `backup.sh path/to/backup.tgz`
2. Copy backup and this repo to new mac
3. Run `./setup.sh init` and log in with your new non-superadmin user
4. If need keys or creds from backup for install, use `restore.sh path/to/backup.tgz`
5. Run `./setup.sh init_user` to remove dock icons and create ssh keys if needed
6. Run `./setup.sh install && ./setup.sh config` and reboot your computer

There are still rough edges and you will probably have to fix something by
hand, but it should still be quicker than starting over or using time machine..

## What it does

### Backup and restore

[`backup.sh`](/backup.sh) does a backup of the home directory files listed in
[`restore.bom`](/restore.bom). It includes hard to generalise plist settings
and credentials. Other files, such as documents are expected to be backed up
to file-sharing, e.g. google drive or dropbox.

[`restore.sh`](/restore.sh) extracts the backup tarball back to home folder.
To add gpg key to gpg suite, e.g. from 1Password, copy the gpg key to `key_public.asc`
and `key_secret.asc` before running `restore.sh`.

The scripts take the path of the backup tarball as an argument.

### Setup

[`setup.sh`](/setup.sh) has three [`tasks/`](/tasks): `init`, `install` and
`config`. To use, run `./setup.sh <task>` in the root folder of this repo.

#### init

- Sets up and asks for basic info such as hostname
- Installs updates and xcode devtools
- Turns off guest account
- Creates a new account to be used instead of the default macOS superadmin
- `./setup.sh init_user` subtask removes dock icons and uses `init_ssh_1password`
- `./setup.sh init_ssh_1password` writes ssh config to use 1password for ssh keys
- `./setup.sh init_ssh_local` creates ssh key if you don't have one (from backup)

#### install

Also works as update, just rerun it to update apps and node. Python and ruby
versions are still manual in the script for now.

- Installs Brew and libs, tools and software from Hombrew and Mac App Store
  - See [`Brewfiles/`](/Brewfiles/) for commented lists of installed
    applications in Homebrew bundle format
  - Installed in order: `core, software`
- Sets default shell to Bash 5 instead of ancient Bash from macOS
- Installs lots of GNU utils to supplement and overwrite macOS builtins
- Installs language runtimes with Mise
  - Latest or LTS version
  - Node, Go, Rust, Zig, Ruby, Perl, Python (with uv)
  - PHP is installed through Homebrew due to problems in mise install (see
    Brewfiles/core comments)
- Installs crudini and aiven-client with uv tools
- Bootstraps [_dotfiles_ subrepo](https://github.com/tapppi/dotfiles)
  - Core dotfiles are in the [`dotfiles` subrepo](https://github.com/tapppi/dotfiles)
  - Personal and extra configs are in this repo at [`.extra`](/.extra),
    [`.path`](/.path) and [`.credentials.dist`](/.credentials.dist)

#### config

- Applies some application settings and terminal/duti settings
- Applies basic settings macos settings as defined in `.macos` dotfile

## Manual steps

Not all steps have been automated:

- Postico favourites are backed up to file-share encrypted. Need to be imported
  manually.
- set up iterm2 key binds if backups don't work correctly:
  https://medium.com/@jonnyhaynes/jump-forwards-backwards-and-delete-a-word-in-iterm2-on-mac-os-43821511f0a

## Thanks to...

- [Mathias Bynens](https://mths.be) for his [_dotfiles_ repository](https://github.com/mathiasbynens/dotfiles)
  which is the upstream for the fork used here
- @ptb and [his _macOS Setup_ repository](https://github.com/ptb/mac-setup)
  for inspiration and basis for installation scripts
- @bkuhlamnn and [his _mac_os(-config)_ repositories](https://github.com/bkuhlmann/mac_os-config)
  and [_dotfiles_ repository](https://github.com/bkuhlmann/dotfiles)
  for inspiration and prior-art as well as some useful utility functions
