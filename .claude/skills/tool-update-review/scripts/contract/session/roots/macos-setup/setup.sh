#!/usr/bin/env bash
# Fixture stand-in for macos-setup's own setup.sh. It exists so the contract
# fixture's structural preconditions (`task_add`, `task_change`) have a real
# file to read subcommand tokens out of. It configures nothing.
set -euo pipefail

if [[ "${1}" = "install" ]]; then
	echo "install"
elif [[ "${1}" = "dotfiles" ]]; then
	echo "dotfiles"
elif [[ "${1}" = "herdr" ]]; then
	echo "herdr"
else
	echo "usage: setup.sh install|dotfiles|herdr" >&2
	exit 1
fi
