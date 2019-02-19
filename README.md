# macos-setup
> Macos setup automation with basic settings, application installs and dotfiles

## Usage
1. Backup credentials and personal settings by running `backup.sh path/to/backup.tgz`
2. Copy backup and this repo to new mac
3. Run `setup.sh path/to/backup.tgz` to setup

## What it does

### Backup

[`backup.sh`](/backup.sh) takes a backup of the files listed in [`restore.bom`](/restore.bom).
It includes hard to generalise plist settings and credentials.
Other files, such as documents are expected to be backed up to file-sharing,
e.g. google drive or dropbox.

The script takes the path of the resulting backup tarball as an argument. Note
that the path is relative to your home folder, or use an absolute path instead.

### Setup

[`setup.sh`](/setup.sh) does the following:
* sets up basic information
* installs applications from brew and mac app store
* installs dotfiles from [subrepo](http://) which is a customised fork of
  [Mathias Bynens' dotfiles](https://github.com/mathiasbynens/dotfiles)
  * also applies basic settings macos settings as defined in `.macos` dotfile
* recovers backup to home if path given as argument


