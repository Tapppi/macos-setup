#!/bin/bash

readlinkorreal() { readlink "$1" || echo "$1"; }

backup_path="${backup_path:=$1}"
: "${backup_path:?Missing backup_path, provide as argument (relative to home dir)}"
d=$(dirname "$0")
files_from=$(readlinkorreal "$d")/restore.bom
backup_path_resolved=$(readlinkorreal "$backup_path")

shift

function doIt() {
	echo "Restoring from ${backup_path_resolved} to $HOME..."
	echo ""

	mkdir .tmp-backup-dir
	cd .tmp-backup-dir && tar -xzf ${backup_path_resolved}
	rsync \
		--rsync-path="sudo rsync" \
		--perms \
		--recursive \
		--compress \
		--numeric-ids \
		--links \
		--hard-links \
		--files-from="$files_from" \
		-avh . ~

	if which gpg >/dev/null && [ -f "./key_public.asc" ]; then
		gpg --import --armor ./key_public.asc
		rm ./key_public.asc
	fi
	if which gpg >/dev/null && [ -f "./key_secret.asc" ]; then
		gpg --import --armor ./key_secret.asc
		rm ./key_secret.asc
	fi
	cd - && rm -rf .tmp-backup-dir

	echo "Restored"
}

if [ "$1" == "--force" ] || [ "$1" == "-f" ]; then
	doIt
else
	read -rp "Do you want to restore ${backup_path} to ~? (y/n) " -n 1
	echo ""
	if [[ "$REPLY" =~ ^[Yy]$ ]]; then
		doIt
	fi
fi
unset doIt
