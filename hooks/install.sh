#!/usr/bin/env bash
# Point this repo's core.hooksPath at the tracked hooks/ directory so that
# post-checkout (and any future hooks) are picked up automatically.
# Run once from the repo root:  bash hooks/install.sh
#
# The path is deliberately RELATIVE. `core.hooksPath` lives in `.git/config`,
# which git shares across every worktree, so an absolute path there makes every
# worktree run the *main checkout's* hook scripts rather than its own branch's.
# Git resolves a relative hooksPath against the working tree it is acting on —
# and it chdir's into a newly created worktree before running post-checkout —
# so `hooks` gives each worktree its own committed copy. That also means a
# change to a hook can be tested on a branch instead of only after it lands.

set -euo pipefail

current="$(git config --local core.hooksPath 2>/dev/null || true)"

if [[ "${current}" == "hooks" ]]; then
	echo "core.hooksPath already set — nothing to do."
	exit 0
fi

git config --local core.hooksPath "hooks"

if [[ -n "${current}" ]]; then
	echo "Migrated core.hooksPath: ${current} → hooks (relative, per-worktree)"
else
	echo "Set core.hooksPath → hooks (relative, per-worktree)"
fi
