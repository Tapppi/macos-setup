# macos-setup
> Macos setup automation with basic settings, application installs and dotfiles

## Usage

1. Backup credentials and personal settings by running `backup.sh path/to/backup.tgz`
2. Copy backup and this repo to new mac
3. If need keys or creds from backup for install, use `restore.sh path/to/backup.tgz`
4. Run `setup.sh` to bootstrap env and then `init` and `install` to setup
5. Run `restore.sh path/to/backup.tgz`

### Manual steps

Not all steps have been automated:

* Postico favorites are encrypted backed up to file-share. Need to be imported
  manually.

## What it does

### Backup and restore

[`backup.sh`](/backup.sh) does a backup of the home directory files listed in
[`restore.bom`](/restore.bom). It includes hard to generalise plist settings
and credentials. Other files, such as documents are expected to be backed up
to file-sharing, e.g. google drive or dropbox.

[`restore.sh`](/restore.sh) syncs the backup tarball back to home folder.

The scripts take the path of the backup tarball as an argument.

### Setup

[`setup.sh`](/setup.sh) does the following:
* sets up basic information
* installs libs, tools and software from Homebrew, Homebrew Casks, App Store,
  Git and DMGs
* bootstraps [_dotfiles_ subrepo](https://github.com/tapppi/dotfiles) for
  configuration
    * also applies basic settings macos settings as defined in `.macos` dotfile

## Contents and customisation

All the contents listed here should fairly easy to customise without major
changes by forking and keeping upstream.

* See [`Brewfiles/`](/Brewfiles/) for commented lists of installed applications
  in Homebrew bundle format
    * Installed in order: `core, languages, software`
* Dotfiles
    * Core dotfiles are in the [`dotfiles` subrepo](https://github.com/tapppi/dotfiles)
    * Personal and extra configs are in this repo at [`.extra`](/.extra),
      [`.path`](/.path) and [`.credentials.dist`](/.credentials.dist)

## Thanks to...
* [Mathias Bynens](https://mths.be) for his [_dotfiles_ repository](https://github.com/mathiasbynens/dotfiles)
  which is the upstream for the fork used here
* @ptb and [his _macOS Setup_ repository](https://github.com/ptb/mac-setup)
  for inspiration and basis for installation scripts
* @bkuhlamnn and [his _mac\_os(-config)_ repositories](https://github.com/bkuhlmann/mac_os-config)
  and [_dotfiles_ repository](https://github.com/bkuhlmann/dotfiles)
  for inspiration and prior-art as well as some useful utility functions
