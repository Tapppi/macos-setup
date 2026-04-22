#!/usr/bin/env bash
# Point this repo's core.hooksPath at the tracked hooks/ directory so that
# post-checkout (and any future hooks) are picked up automatically.
# Run once from the repo root:  bash hooks/install.sh

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="${repo_root}/hooks"

if [[ "$(git config --local core.hooksPath 2>/dev/null)" == "${hooks_dir}" ]]; then
	echo "core.hooksPath already set — nothing to do."
	exit 0
fi

git config --local core.hooksPath "${hooks_dir}"
echo "Set core.hooksPath → ${hooks_dir}"
