#!/usr/bin/env bash
# Fixture stand-in for macos-setup's install task. It installs nothing.
install_libpq() {
	brew install libpq
	export PGSERVICEFILE="${HOME}/.pg_service.conf"
}
