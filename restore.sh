#!/bin/bash

backup_path="${backup_path:=$1}"
: "${backup_path:?Missing backup_path, provide as argument (relative to home dir)}"
d=$(dirname "$0")
files_from=$(greadlink -f "$d")/restore.bom
backup_path_resolved=$(greadlink -f "$backup_path")

shift

function doIt() {
	echo "Restoring from ${backup_path_resolved} to ~...";
	echo "";

	mkdir .tmp-backup-dir
	cd .tmp-backup-dir && tar -xzf ${backup_path_resolved};
	rsync \
    --rsync-path="sudo rsync" \
    --perms \
    --recursive \
    --compress \
    --numeric-ids \
    --links \
    --hard-links \
    --files-from="$files_from" \
		-avh . ~;

	if which gpg > /dev/null && [ -f "~/.gnupg/key_public.asc" ]; then
		gpg --import --armor ~/.gnupg/key_public.asc
		rm ~/.gnupg/key_public.asc
	fi
	if which gpg > /dev/null && [ -f "~/.gnupg/key_secret.asc" ]; then
		gpg --import --armor ~/.gnupg/key_secret.asc
		rm ~/.gnupg/key_secret.asc
	fi
  cd - && rm -rf .tmp-backup-dir;

	echo "Restored";
}

if [ "$1" == "--force" ] || [ "$1" == "-f" ]; then
	doIt;
else
	read -rp "Do you want to restore ${backup_path} to ~? (y/n) " -n 1;
	echo "";
	if [[ "$REPLY" =~ ^[Yy]$ ]]; then
		doIt;
	fi;
fi;
unset doIt;
