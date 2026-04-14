#!/usr/bin/env bash
set -uo pipefail

config() {
	# Keep-alive: update existing `sudo` time stamp until macOS config has finished
	while true; do
		sudo -n true
		sleep 60
		kill -0 "$$" || exit
	done 2>/dev/null &

	p1 "Configuring software"
	config_admin_req
	config_vlc
	config_istatmenus
	config_alfred
	config_stts
	config_amphetamine
	config_claudebar
	config_google_drive
	config_hammerspoon
	config_ice
	config_karabiner_elements
	config_obsidian
	config_resolutionator
	config_claude_code
	config_spotify

	p1 "Customising various launch options"
	custom_loginitems
	custom_terminal
	custom_duti

	p1 "Configuring macOS"
	./tasks/macos.sh
	p1 "Done. Some changes require a reboot."
}

# Mark Applications Requiring Administrator Account

_admin_req='iStat Menus.app
Wireshark.app'

config_admin_req() {
	printf "%s\n" "${_admin_req}" |
		while IFS=$'\t' read -r app; do
			sudo tag -a "Red, admin" "/Applications/${app}"
		done
}

# Define Function =config_defaults=

config_defaults() {
	printf "%s\n" "${1}" |
		while IFS=$'\t' read -r domain key type value host; do
			# shellcheck disable=SC2086
			${2} defaults ${host} write "${domain}" "${key}" ${type} "${value}"
		done
}

# Define Function =config_plist=

config_plist() {
	printf "%s\n" "$1" |
		while IFS=$'\t' read -r command entry type value; do
			case "$value" in
			\$*)
				# shellcheck disable=SC2086
				$4 /usr/libexec/PlistBuddy "$2" \
					-c "$command '${3}${entry}' $type '$(eval echo \"$value\")'" 2>/dev/null
				;;
			*)
				$4 /usr/libexec/PlistBuddy "$2" \
					-c "$command '${3}${entry}' $type '$value'" 2>/dev/null
				;;
			esac
		done
}

# Define Function =config_launchd=

# NOTE: launchctl load/unload are legacy on Sequoia; bootstrap/bootout are
# the modern replacements, but require service labels and domain targets.
# Keeping load/unload for now as they still function for user-level plists.
config_launchd() {
	test -d "$(dirname "$1")" ||
		$3 mkdir -p "$(dirname "$1")"

	test -f "$1" &&
		$3 launchctl unload "$1" &&
		$3 rm -f "$1"

	config_plist "$2" "$1" "$4" "$3" &&
		$3 plutil -convert xml1 "$1" &&
		$3 launchctl load "$1"
}

# Configure iStat Menus
config_istatmenus() {
	test -d "/Applications/iStat Menus.app" &&
		open "/Applications/iStat Menus.app"
}

# Configure Alfred
config_alfred() {
	test -d "/Applications/Alfred 5.app" &&
		open "/Applications/Alfred 5.app"
}

# Configure Amphetamine
config_amphetamine() {
	test -d "/Applications/Amphetamine.app" &&
		open "/Applications/Amphetamine.app"
	test -d "/Applications/Amphetamine Enhancer.app" &&
		open "/Applications/Amphetamine Enhancer.app"
}

# Configure ClaudeBar
config_claudebar() {
	test -d "/Applications/ClaudeBar.app" &&
		open "/Applications/ClaudeBar.app"
}

# Configure Google Drive
config_google_drive() {
	test -d "/Applications/Google Drive.app" &&
		open "/Applications/Google Drive.app"
}

# Configure Hammerspoon
config_hammerspoon() {
	test -d "/Applications/Hammerspoon.app" &&
		open "/Applications/Hammerspoon.app"
}

# Configure Ice
config_ice() {
	test -d "/Applications/Ice.app" &&
		open "/Applications/Ice.app"
}

# Configure Karabiner-Elements
config_karabiner_elements() {
	test -d "/Applications/Karabiner-Elements.app" &&
		open "/Applications/Karabiner-Elements.app"
}

# Configure Obsidian CLI
config_obsidian() {
	local obsidian_app="/Applications/Obsidian.app"
	local obsidian_cli="${obsidian_app}/Contents/MacOS/obsidian-cli"
	local obsidian_link="/usr/local/bin/obsidian"
	local zprofile_file="${HOME}/.zprofile"

	if [[ -x "${obsidian_cli}" ]]; then
		test -d "/usr/local/bin" ||
			sudo mkdir -p "/usr/local/bin"

		if [[ ! -L "${obsidian_link}" ]] || [[ "$(readlink "${obsidian_link}" 2>/dev/null)" != "${obsidian_cli}" ]]; then
			sudo ln -sf "${obsidian_cli}" "${obsidian_link}"
		fi
	fi

	if [[ -f "${zprofile_file}" ]]; then
		python3 - "${zprofile_file}" <<'PYEOF'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
lines = path.read_text().splitlines()
filtered = []
skip_next_blank = False

for line in lines:
	if line == "# Added by Obsidian" or "/Applications/Obsidian.app/Contents/MacOS" in line:
		skip_next_blank = True
		continue

	if skip_next_blank and line == "":
		skip_next_blank = False
		continue

	skip_next_blank = False
	filtered.append(line)

path.write_text("\n".join(filtered) + ("\n" if filtered else ""))
PYEOF
	fi
}

# Configure Resolutionator
config_resolutionator() {
	local resolutionator_app="/Applications/Resolutionator.app"
	local resolutionator_domain="com.manytricks.Resolutionator"
	local host_name current_resolution width height

	if [[ ! -d "${resolutionator_app}" ]]; then
		return 0
	fi

	open "${resolutionator_app}"
	defaults write "${resolutionator_domain}" "Stealth Mode" -bool true
	defaults write "${resolutionator_domain}" "Keyboard Menu Trigger" -dict \
		keyCode -int 35 \
		modifierFlags -int 1966080

	host_name="$(hostname -s 2>/dev/null || printf '%s' unknown)"
	case "${host_name}" in
	bellona)
		current_resolution="$(python3 - <<'PY'
import re
import subprocess

try:
	result = subprocess.run(
		[
			'osascript',
			'-e', 'tell application "Resolutionator" to activate',
			'-e', 'delay 1',
			'-e', 'tell application "System Events" to tell process "Resolutionator" to get name of every menu item of menu 1 of menu bar item 3 of menu bar 1'
		],
		capture_output=True,
		text=True,
		check=True,
		timeout=10,
	)
except Exception:
	print('')
	raise SystemExit(0)

match = re.search(r'(\d+)\s*[×x]\s*(\d+)', result.stdout)
if match:
	print(f"{match.group(1)} {match.group(2)}")
else:
	print('')
PY
		)"

		if [[ -n "${current_resolution}" ]]; then
			read -r width height <<<"${current_resolution}"
			python3 - "${width}" "${height}" <<'PY'
import subprocess
import sys

width, height = sys.argv[1:3]
try:
	subprocess.run(
		['osascript', '-e', f'tell application "Resolutionator" to set resolution {width} x {height} for display 1'],
		check=True,
		timeout=10,
	)
except subprocess.TimeoutExpired:
	pass
except subprocess.CalledProcessError:
	pass
PY
		else
			p3 "Could not capture the current bellona Resolutionator resolution"
		fi
		;;
	tmopro18)
		p3 "Resolutionator default resolution still needs a tmopro18-specific value"
		;;
	esac
}

# Configure Claude Code MCP servers
config_claude_code() {
	if command -v claude >/dev/null; then
		# context7: library/framework documentation lookup (not built into Claude Code)
		claude mcp add --scope user --transport stdio context7 -- npx -y @upstash/context7-mcp
	fi
}

# Configure Spotify
config_spotify() {
	test -d "/Applications/Spotify.app" &&
		open "/Applications/Spotify.app"
}

# Configure stts
config_stts() {
	test -d "/Applications/stts.app" &&
		open "/Applications/stts.app"
}

# Configure VLC
_vlc_defaults='org.videolan.vlc	SUEnableAutomaticChecks	-bool	true
org.videolan.vlc	SUHasLaunchedBefore	-bool	true
org.videolan.vlc	SUSendProfileInfo	-bool	true	'
_vlcrc='macosx	macosx-nativefullscreenmode	1
macosx	macosx-video-autoresize	0
macosx	macosx-appleremote	0
macosx	macosx-pause-minimized	1
macosx	macosx-continue-playback	1
core	metadata-network-access	1
core	volume-save	0
core	spdif	1
core	sub-language	English
core	medium-jump-size	30
subsdec	subsdec-encoding	UTF-8
avcodec	avcodec-hw	videotoolbox'

config_vlc() {
	config_defaults "${_vlc_defaults}"
	if command -v crudini >/dev/null; then
		test -d "${HOME}/Library/Preferences/org.videolan.vlc" ||
			mkdir -p "${HOME}/Library/Preferences/org.videolan.vlc"
		printf "%s\n" "${_vlcrc}" |
			while IFS=$'\t' read -r section key value; do
				crudini --set "${HOME}/Library/Preferences/org.videolan.vlc/vlcrc" "${section}" "${key}" "${value}"
			done
	fi
}

# Customize Login Items

_loginitems='/Applications/1Password.app
/Applications/Alfred 5.app
/Applications/Amphetamine.app
/Applications/ClaudeBar.app
/Applications/Google Drive.app
/Applications/Hammerspoon.app
/Applications/Ice.app
/Applications/iStat Menus.app
/Applications/Karabiner-Elements.app
/Applications/Resolutionator.app
/Applications/Slack.app
/Applications/Spotify.app
/Applications/stts.app
/Applications/WhatsApp.app'
custom_loginitems() {
	printf "%s\n" "${_loginitems}" |
		while IFS=$'\t' read -r app; do
			if test -e "$app"; then
				osascript - "$app" <<EOF >/dev/null
        on run { _app }
          tell app "System Events"
            make new login item with properties { hidden: true, path: _app }
          end tell
        end run
EOF
			fi
		done
}

# Customize Terminal

# shellcheck disable=SC2016
_term_plist='add	:name	string	tapani
add	:type	string	Window Settings
add	:ProfileCurrentVersion	real	2.05
add	:BackgroundBlur	real	0
add	:BackgroundSettingsForInactiveWindows	bool	false
add	:BackgroundAlphaInactive	real	1
add	:BackgroundBlurInactive	real	0
add	:Font	data	<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>$archiver</key><string>NSKeyedArchiver</string><key>$objects</key><array><string>$null</string><dict><key>$class</key><dict><key>CF$UID</key><integer>3</integer></dict><key>NSName</key><dict><key>CF$UID</key><integer>2</integer></dict><key>NSSize</key><real>13</real><key>NSfFlags</key><integer>16</integer></dict><string>InconsolataLGC</string><dict><key>$classes</key><array><string>NSFont</string><string>NSObject</string></array><key>$classname</key><string>NSFont</string></dict></array><key>$top</key><dict><key>root</key><dict><key>CF$UID</key><integer>1</integer></dict></dict><key>$version</key><integer>100000</integer></dict></plist>
add	:FontWidthSpacing	real	1
add	:FontHeightSpacing	real	1
add	:FontAntialias	bool	true
add	:UseBoldFonts	bool	true
add	:BlinkText	bool	false
add	:UseBrightBold	bool	false
add	:CursorType	integer	0
add	:CursorBlink	bool	false
add	:ShowRepresentedURLInTitle	bool	true
add	:ShowRepresentedURLPathInTitle	bool	true
add	:ShowActiveProcessInTitle	bool	true
add	:ShowActiveProcessArgumentsInTitle	bool	false
add	:ShowShellCommandInTitle	bool	false
add	:ShowWindowSettingsNameInTitle	bool	false
add	:ShowTTYNameInTitle	bool	false
add	:ShowDimensionsInTitle	bool	false
add	:ShowCommandKeyInTitle	bool	false
add	:columnCount	integer	121
add	:rowCount	integer	35
add	:ShouldLimitScrollback	integer	0
add	:ScrollbackLines	integer	0
add	:ShouldRestoreContent	bool	false
add	:ShowRepresentedURLInTabTitle	bool	false
add	:ShowRepresentedURLPathInTabTitle	bool	false
add	:ShowActiveProcessInTabTitle	bool	true
add	:ShowActiveProcessArgumentsInTabTitle	bool	false
add	:ShowTTYNameInTabTitle	bool	false
add	:ShowComponentsWhenTabHasCustomTitle	bool	true
add	:ShowActivityIndicatorInTab	bool	true
add	:shellExitAction	integer	1
add	:warnOnShellCloseAction	integer	1
add	:useOptionAsMetaKey	bool	false
add	:ScrollAlternateScreen	bool	true
add	:TerminalType	string	xterm-256color
add	:deleteSendsBackspace	bool	false
add	:EscapeNonASCIICharacters	bool	true
add	:ConvertNewlinesOnPaste	bool	true
add	:StrictVTKeypad	bool	true
add	:scrollOnInput	bool	true
add	:Bell	bool	false
add	:VisualBell	bool	false
add	:VisualBellOnlyWhenMuted	bool	false
add	:BellBadge	bool	false
add	:BellBounce	bool	false
add	:BellBounceCritical	bool	false
add	:CharacterEncoding	integer	4
add	:SetLanguageEnvironmentVariables	bool	true
add	:EastAsianAmbiguousWide	bool	false'
_term_defaults='com.apple.Terminal	Startup Window Settings	-string	tapani
com.apple.Terminal	Default Window Settings	-string	tapani	'

custom_terminal() {
	local _term_plist_file="${HOME}/Library/Preferences/com.apple.Terminal.plist"
	local _term_plist_key=":Window Settings:tapani"
	/usr/libexec/PlistBuddy "${_term_plist_file}" \
		-c "delete '${_term_plist_key}'" 2>/dev/null
	/usr/libexec/PlistBuddy "${_term_plist_file}" \
		-c "add '${_term_plist_key}' dict" 2>/dev/null
	config_plist "${_term_plist}" \
		"${_term_plist_file}" \
		"${_term_plist_key}"
	config_defaults "${_term_defaults}"
}

# Customize Default UTIs
# Uses duti -s directly; extensions (.ext) preferred over obsolete UTIs.
# App-defined UTIs (com.barebones.bbedit.*) require the app to be installed.
# NOTE: duti is unmaintained and uses deprecated LaunchServices C APIs.
# Consider switching to dutix (https://github.com/jackchuka/dutix) which
# uses the modern NSWorkspace API, once it matures (no batch mode or
# bundle-ID input yet).

_duti='com.apple.DiskImageMounter	com.apple.disk-image	all
com.apple.DiskImageMounter	public.disk-image	all
com.apple.DiskImageMounter	public.iso-image	all
com.apple.QuickTimePlayerX	com.apple.coreaudio-format	all
com.apple.QuickTimePlayerX	com.apple.quicktime-movie	all
com.apple.QuickTimePlayerX	public.mp3	all
com.apple.Preview	com.compuserve.gif	all
com.apple.Preview	.gif	all
com.apple.Terminal	com.apple.terminal.shell-script	all
com.apple.QuickTimePlayerX	com.apple.m4a-audio	all
com.apple.QuickTimePlayerX	com.apple.protected-mpeg-4-audio	all
com.apple.QuickTimePlayerX	public.mpeg-4-audio	all
com.apple.installer	com.apple.installer-package-archive	all
com.neovide.neovide	com.apple.binary-property-list	editor
com.neovide.neovide	com.apple.crashreport	editor
com.neovide.neovide	com.apple.dt.document.ascii-property-list	editor
com.neovide.neovide	com.apple.dt.document.script-suite-property-list	editor
com.neovide.neovide	com.apple.dt.document.script-terminology-property-list	editor
com.neovide.neovide	com.apple.log	editor
com.neovide.neovide	com.apple.property-list	editor
com.neovide.neovide	com.apple.rez-source	editor
com.neovide.neovide	com.apple.symbol-export	editor
com.neovide.neovide	public.ada-source	editor
com.neovide.neovide	public.bash-script	editor
com.neovide.neovide	.xcconfig	editor
com.neovide.neovide	public.fortran-source	editor
com.neovide.neovide	public.ksh-script	editor
com.neovide.neovide	public.make-source	editor
com.neovide.neovide	public.pascal-source	editor
com.neovide.neovide	.strings	editor
com.neovide.neovide	public.tcsh-script	editor
com.neovide.neovide	.y	editor
com.neovide.neovide	public.zsh-script	editor
com.neovide.neovide	com.apple.xml-property-list	editor
com.neovide.neovide	com.barebones.bbedit.actionscript-source	editor
com.neovide.neovide	com.barebones.bbedit.erb-source	editor
com.neovide.neovide	com.barebones.bbedit.ini-configuration	editor
com.neovide.neovide	com.barebones.bbedit.jsp-source	editor
com.neovide.neovide	com.barebones.bbedit.lasso-source	editor
com.neovide.neovide	com.barebones.bbedit.lua-source	editor
com.neovide.neovide	com.barebones.bbedit.setext-source	editor
com.neovide.neovide	com.barebones.bbedit.sql-source	editor
com.neovide.neovide	com.barebones.bbedit.tcl-source	editor
com.neovide.neovide	com.barebones.bbedit.tex-source	editor
com.neovide.neovide	com.barebones.bbedit.textile-source	editor
com.neovide.neovide	com.barebones.bbedit.vbscript-source	editor
com.neovide.neovide	com.barebones.bbedit.vectorscript-source	editor
com.neovide.neovide	com.barebones.bbedit.verilog-hdl-source	editor
com.neovide.neovide	com.barebones.bbedit.vhdl-source	editor
com.neovide.neovide	.yaml	editor
com.neovide.neovide	com.netscape.javascript-source	editor
com.neovide.neovide	com.sun.java-source	editor
com.neovide.neovide	.lock	all
com.neovide.neovide	.coffee	all
com.neovide.neovide	.conf	all
com.neovide.neovide	.jbuilder	all
com.neovide.neovide	.rdoc	all
com.neovide.neovide	.ru	all
com.neovide.neovide	.scss	all
com.neovide.neovide	.sass	all
com.neovide.neovide	net.daringfireball.markdown	editor
com.neovide.neovide	public.assembly-source	editor
com.neovide.neovide	public.c-header	editor
com.neovide.neovide	public.c-plus-plus-source	editor
com.neovide.neovide	public.c-source	editor
com.neovide.neovide	public.csh-script	editor
com.neovide.neovide	public.json	editor
com.neovide.neovide	public.lex-source	editor
com.neovide.neovide	public.log	editor
com.neovide.neovide	public.mig-source	editor
com.neovide.neovide	public.nasm-assembly-source	editor
com.neovide.neovide	public.objective-c-plus-plus-source	editor
com.neovide.neovide	public.objective-c-source	editor
com.neovide.neovide	public.patch-file	editor
com.neovide.neovide	public.perl-script	editor
com.neovide.neovide	public.php-script	editor
com.neovide.neovide	public.plain-text	editor
com.neovide.neovide	public.precompiled-c-header	editor
com.neovide.neovide	public.precompiled-c-plus-plus-header	editor
com.neovide.neovide	public.python-script	editor
com.neovide.neovide	public.ruby-script	editor
com.neovide.neovide	public.script	editor
com.neovide.neovide	public.shell-script	editor
com.neovide.neovide	public.source-code	editor
com.neovide.neovide	public.text	editor
com.neovide.neovide	public.utf16-external-plain-text	editor
com.neovide.neovide	public.utf16-plain-text	editor
com.neovide.neovide	public.utf8-plain-text	editor
com.neovide.neovide	public.xml	editor
cx.c3.theunarchiver	com.alcohol-soft.mdf-image	all
cx.c3.theunarchiver	com.allume.stuffit-archive	all
cx.c3.theunarchiver	com.altools.alz-archive	all
cx.c3.theunarchiver	com.amiga.adf-archive	all
cx.c3.theunarchiver	com.amiga.adz-archive	all
cx.c3.theunarchiver	com.apple.applesingle-archive	all
cx.c3.theunarchiver	com.apple.binhex-archive	all
cx.c3.theunarchiver	com.apple.bom-compressed-cpio	all
cx.c3.theunarchiver	com.apple.itunes.ipa	all
cx.c3.theunarchiver	com.apple.macbinary-archive	all
cx.c3.theunarchiver	com.apple.self-extracting-archive	all
cx.c3.theunarchiver	com.apple.xar-archive	all
cx.c3.theunarchiver	com.apple.xip-archive	all
cx.c3.theunarchiver	com.cyclos.cpt-archive	all
cx.c3.theunarchiver	com.microsoft.cab-archive	all
cx.c3.theunarchiver	com.microsoft.msi-installer	all
cx.c3.theunarchiver	com.nero.nrg-image	all
cx.c3.theunarchiver	com.network172.pit-archive	all
cx.c3.theunarchiver	com.nowsoftware.now-archive	all
cx.c3.theunarchiver	com.nscripter.nsa-archive	all
cx.c3.theunarchiver	com.padus.cdi-image	all
cx.c3.theunarchiver	com.pkware.zip-archive	all
cx.c3.theunarchiver	com.rarlab.rar-archive	all
cx.c3.theunarchiver	com.redhat.rpm-archive	all
cx.c3.theunarchiver	com.stuffit.archive.sit	all
cx.c3.theunarchiver	com.stuffit.archive.sitx	all
cx.c3.theunarchiver	com.sun.java-archive	all
cx.c3.theunarchiver	com.symantec.dd-archive	all
cx.c3.theunarchiver	com.winace.ace-archive	all
cx.c3.theunarchiver	com.winzip.zipx-archive	all
cx.c3.theunarchiver	cx.c3.arc-archive	all
cx.c3.theunarchiver	cx.c3.arj-archive	all
cx.c3.theunarchiver	cx.c3.dcs-archive	all
cx.c3.theunarchiver	cx.c3.dms-archive	all
cx.c3.theunarchiver	cx.c3.ha-archive	all
cx.c3.theunarchiver	cx.c3.lbr-archive	all
cx.c3.theunarchiver	cx.c3.lha-archive	all
cx.c3.theunarchiver	cx.c3.lhf-archive	all
cx.c3.theunarchiver	cx.c3.lzx-archive	all
cx.c3.theunarchiver	cx.c3.packdev-archive	all
cx.c3.theunarchiver	cx.c3.pax-archive	all
cx.c3.theunarchiver	cx.c3.pma-archive	all
cx.c3.theunarchiver	cx.c3.pp-archive	all
cx.c3.theunarchiver	cx.c3.xmash-archive	all
cx.c3.theunarchiver	cx.c3.zoo-archive	all
cx.c3.theunarchiver	cx.c3.zoom-archive	all
cx.c3.theunarchiver	org.7-zip.7-zip-archive	all
cx.c3.theunarchiver	org.archive.warc-archive	all
cx.c3.theunarchiver	org.debian.deb-archive	all
cx.c3.theunarchiver	org.gnu.gnu-tar-archive	all
cx.c3.theunarchiver	org.gnu.gnu-zip-archive	all
cx.c3.theunarchiver	org.gnu.gnu-zip-tar-archive	all
cx.c3.theunarchiver	org.tukaani.lzma-archive	all
cx.c3.theunarchiver	org.tukaani.xz-archive	all
cx.c3.theunarchiver	public.bzip2-archive	all
cx.c3.theunarchiver	public.cpio-archive	all
cx.c3.theunarchiver	public.tar-archive	all
cx.c3.theunarchiver	public.tar-bzip2-archive	all
cx.c3.theunarchiver	public.z-archive	all
cx.c3.theunarchiver	public.zip-archive	all
cx.c3.theunarchiver	public.zip-archive.first-part	all
org.videolan.vlc	com.apple.m4v-video	all
org.videolan.vlc	com.microsoft.windows-media-wmv	all
org.videolan.vlc	.3gp	all
org.videolan.vlc	.aac	all
org.videolan.vlc	.aiff	all
org.videolan.vlc	.amr	all
org.videolan.vlc	.aob	all
org.videolan.vlc	.ape	all
org.videolan.vlc	.asf	all
org.videolan.vlc	.axa	all
org.videolan.vlc	.axv	all
org.videolan.vlc	.divx	all
org.videolan.vlc	.dts	all
org.videolan.vlc	.dv	all
org.videolan.vlc	.flac	all
org.videolan.vlc	.flv	all
org.videolan.vlc	.gxf	all
org.videolan.vlc	.it	all
org.videolan.vlc	.mid	all
org.videolan.vlc	.mka	all
org.videolan.vlc	.mkv	all
org.videolan.vlc	.mlp	all
org.videolan.vlc	.mod	all
org.videolan.vlc	.mpc	all
org.videolan.vlc	.mp2	all
org.videolan.vlc	.mxf	all
org.videolan.vlc	.nsv	all
org.videolan.vlc	.nuv	all
org.videolan.vlc	.oga	all
org.videolan.vlc	.ogv	all
org.videolan.vlc	.oma	all
org.videolan.vlc	.opus	all
org.videolan.vlc	.rm	all
org.videolan.vlc	.rec	all
org.videolan.vlc	.rmi	all
org.videolan.vlc	.s3m	all
org.videolan.vlc	.spx	all
org.videolan.vlc	.tod	all
org.videolan.vlc	.tta	all
org.videolan.vlc	.vob	all
org.videolan.vlc	.voc	all
org.videolan.vlc	.vqf	all
org.videolan.vlc	.vro	all
org.videolan.vlc	.wav	all
org.videolan.vlc	.webm	all
org.videolan.vlc	.wma	all
org.videolan.vlc	.wtv	all
org.videolan.vlc	.wv	all
org.videolan.vlc	.xa	all
org.videolan.vlc	.xesc	all
org.videolan.vlc	.xm	all
org.videolan.vlc	public.ac3-audio	all
org.videolan.vlc	public.audiovisual-content	all
org.videolan.vlc	public.avi	all
org.videolan.vlc	public.movie	all
org.videolan.vlc	public.mpeg	all
org.videolan.vlc	public.mpeg-2-video	all
org.videolan.vlc	public.mpeg-4	all'
custom_duti() {
	if command -v duti >/dev/null; then
		printf "%s\n" "${_duti}" |
			while IFS=$'\t' read -r id uti role; do
				duti -s "${id}" "${uti}" "${role}" 2>/dev/null
			done

		p3 "duti: Verify associations via Finder Get Info → Open with"
	fi
}
