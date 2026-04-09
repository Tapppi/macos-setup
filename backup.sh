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

archive_dir="${1:-}"
archive_name="${2:-}"
force_flag="${3:-}"

: "${archive_dir:?Missing archive directory, provide as first argument}"
: "${archive_name:?Missing archive name, provide as second argument}"

if [[ ! -d "${archive_dir}" ]]; then
	printf "Archive directory does not exist: %s\n" "${archive_dir}" >&2
	exit 1
fi

if [[ "${archive_name}" == */* ]]; then
	printf "Archive name must not contain path separators: %s\n" "${archive_name}" >&2
	exit 1
fi

archive_dir_resolved=$(cd "${archive_dir}" && pwd -P)
timestamp=$(date +"%Y%m%d-%H%M%S")
archive_file_name="${archive_name}.${timestamp}.tar.gz"
archive_path="${archive_dir_resolved}/${archive_file_name}"
rsync_bin=$(resolve_rsync_bin)
temp_root=""

cleanup() {
	if [[ -n "${temp_root}" && -d "${temp_root}" ]]; then
		rm -rf "${temp_root}"
	fi
}

do_backup() {
	local payload_dir

	echo "Backing up ${HOME} to ${archive_path} as a gzip-compressed tar archive..."
	echo ""

	temp_root=$(mktemp -d "${TMPDIR:-/tmp}/macos-backup.XXXXXX")
	payload_dir="${temp_root}/payload"
	mkdir -p "${payload_dir}"

	(
		cd "${HOME}" || exit 1
		"${rsync_bin}" \
			--archive \
			--human-readable \
			--verbose \
			--ignore-missing-args \
			--files-from="${files_from}" \
			./ "${payload_dir}"
	)

	tar -czf "${archive_path}" -C "${payload_dir}" .

	echo "Backed up successfully to ${archive_path}"
}

trap cleanup EXIT

if [[ "${force_flag}" == "--force" || "${force_flag}" == "-f" ]]; then
	do_backup
else
	read -rp "Do you want to backup ${HOME} to ${archive_path}? (y/n) " -n 1
	echo ""
	if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
		do_backup
	fi
fi
