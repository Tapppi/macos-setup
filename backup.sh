#!/bin/bash

backup_path="${backup_path:=$1}"
: "${backup_path:?Missing backup_path, provide as argument (relative to home dir)}"
d=$(dirname "$0")
files_from=$(greadlink -f "$d")/restore.bom

shift

# Run in home folder
cd || exit;

function doIt() {
	echo "Backing up $(pwd) to ${backup_path} as a gzipped tar ball...";
	echo "";
	rsync \
    --rsync-path="sudo rsync" \
    --perms \
    --recursive \
    --compress \
    --numeric-ids \
    --links \
    --hard-links \
    --files-from="$files_from" \
		-avh . ./.tmp-backup-dir;
	cd ./.tmp-backup-dir && tar czf ../.tmp-backup-dir.tgz . && cd -;
  mv .tmp-backup-dir.tgz $backup_path;

	echo "Backed up successfully";
	rm -rf ./.tmp-backup-dir;
}

if [ "$1" == "--force" ] || [ "$1" == "-f" ]; then
	doIt;
else
	read -rp "Do you want to backup $(pwd) to ${backup_path}? (y/n) " -n 1;
	echo "";
	if [[ "$REPLY" =~ ^[Yy]$ ]]; then
		doIt;
	fi;
fi;
unset doIt;
