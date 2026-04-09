#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
files_from="${script_dir}/restore.bom"

resolve_rsync_bin() {
	if command -v brew >/dev/null 2>&1; then
		local rsync_prefix
		rsync_prefix=$(brew --prefix rsync 2>/dev/null || true)
		if [[ -n "${rsync_prefix}" && -x "${rsync_prefix}/bin/rsync" ]]; then
			printf '%s\n' "${rsync_prefix}/bin/rsync"
			return
		fi
	fi

	printf "Homebrew rsync is required. Install it with: brew install rsync\n" >&2
	exit 1
}

backup_path="${1:-}"
force_flag="${2:-}"

: "${backup_path:?Missing backup_path, provide as first argument}"

if [[ ! -f "${backup_path}" ]]; then
	printf "Backup archive does not exist: %s\n" "${backup_path}" >&2
	exit 1
fi

backup_dir_resolved=$(cd "$(dirname "${backup_path}")" && pwd -P)
backup_path_resolved="${backup_dir_resolved}/$(basename "${backup_path}")"
rsync_bin=$(resolve_rsync_bin)
temp_root=""

cleanup() {
	if [[ -n "${temp_root}" && -d "${temp_root}" ]]; then
		rm -rf "${temp_root}"
	fi
}

do_restore() {
	case "${backup_path_resolved}" in
		*.tar.gz|*.tgz)
			;;
		*)
			printf "Unsupported backup archive format: %s\n" "${backup_path_resolved}" >&2
			exit 1
			;;
	esac

	echo "Restoring gzip-compressed tar archive ${backup_path_resolved} to ${HOME}..."
	echo ""

	temp_root=$(mktemp -d "${TMPDIR:-/tmp}/macos-restore.XXXXXX")
	tar -xzf "${backup_path_resolved}" -C "${temp_root}"

	"${rsync_bin}" \
		--archive \
		--human-readable \
		--verbose \
		--ignore-missing-args \
		--files-from="${files_from}" \
		"${temp_root}/" "${HOME}"

	if command -v gpg >/dev/null && [[ -f "${temp_root}/key_public.asc" ]]; then
		gpg --import --armor "${temp_root}/key_public.asc"
		rm "${temp_root}/key_public.asc"
	fi

	if command -v gpg >/dev/null && [[ -f "${temp_root}/key_secret.asc" ]]; then
		gpg --import --armor "${temp_root}/key_secret.asc"
		rm "${temp_root}/key_secret.asc"
	fi

	echo "Restored successfully from ${backup_path_resolved}"
}

trap cleanup EXIT

if [[ "${force_flag}" == "--force" || "${force_flag}" == "-f" ]]; then
	do_restore
else
	read -rp "Do you want to restore ${backup_path_resolved} to ${HOME}? (y/n) " -n 1
	echo ""
	if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
		do_restore
	fi
fi
