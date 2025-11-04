#!/bin/bash

backup_path="${backup_path:=$1}"
: "${backup_path:?Missing backup_path, provide as argument (relative to home dir)}"
d=$(dirname "$0")
files_from=$(greadlink -f "$d")/restore.bom

shift

function doIt() {
	echo "Backing up $HOME to ${backup_path} as a gzipped tar ball..."
	echo ""

	# Run in home folder
	CURR_DIR=$(pwd)
	cd || exit

	rsync \
		--rsync-path="sudo rsync" \
		--perms \
		--recursive \
		--compress \
		--numeric-ids \
		--links \
		--hard-links \
		--files-from="$files_from" \
		-avh . ~/.tmp-backup-dir
	cd ~/.tmp-backup-dir && tar -czf ../.tmp-backup-dir.tgz . && cd -

	cd $CURR_DIR
	mv ~/.tmp-backup-dir.tgz $backup_path

	echo "Backed up successfully"
	rm -rf ~/.tmp-backup-dir
}

if [ "$1" == "--force" ] || [ "$1" == "-f" ]; then
	doIt
else
	read -rp "Do you want to backup $(pwd) to ${backup_path}? (y/n) " -n 1
	echo ""
	if [[ "$REPLY" =~ ^[Yy]$ ]]; then
		doIt
	fi
fi
unset doIt
cd -
