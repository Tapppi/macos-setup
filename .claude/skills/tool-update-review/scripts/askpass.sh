#!/usr/bin/env bash
# askpass.sh — SUDO_ASKPASS helper for tool-update-review's apply step.
#
# Pops a native macOS password dialog via osascript and prints the entered
# text to stdout, which is exactly what sudo expects from an askpass helper.
# The password never touches this script's own state — osascript reads it
# directly from the user via the OS's own secure dialog, and it's only ever
# relayed to sudo's own stdin-reading pipe, never written to disk or logged.
#
# Set ASKPASS_PROMPT to the command being authorized so the user knows
# exactly what they're approving before typing their password.
set -euo pipefail

prompt="${ASKPASS_PROMPT:-a privileged command}"
# Escape double quotes and backslashes for safe embedding in the AppleScript string.
escaped_prompt="${prompt//\\/\\\\}"
escaped_prompt="${escaped_prompt//\"/\\\"}"

osascript <<EOF
try
	set userInput to display dialog "tool-update-review needs admin rights to run:\n\n${escaped_prompt}" with title "tool-update-review" with icon caution with hidden answer default answer "" buttons {"Cancel", "OK"} default button "OK"
	return text returned of userInput
on error
	return ""
end try
EOF
