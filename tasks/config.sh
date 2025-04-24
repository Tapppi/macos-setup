#!/bin/bash

config () {
  # Keep-alive: update existing `sudo` time stamp until `.macos` has finished
  while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

  p1 "Configuring software"
  config_admin_req
  config_vlc
  config_istatmenus
  config_alfred
  config_stts
  config_amphetamine

  # Need to buy bartender 5..
  # config_bartender

  p1 "Customising various launch options"
  custom_loginitems
  custom_terminal
  custom_duti

  p1 "Configuring macOS"
  sudo ~/.macos
  p1 "Done. Some changes require a reboot."
}

# Mark Applications Requiring Administrator Account

_admin_req='Docker.app
iStat Menus.app
Wireshark.app'

config_admin_req () {
  printf "%s\n" "${_admin_req}" | \
  while IFS="$(printf '\t')" read app; do
    sudo tag -a "Red, admin" "/Applications/${app}"
  done
}

# Define Function =config_defaults=

config_defaults () {
  printf "%s\n" "${1}" | \
  while IFS="$(printf '\t')" read domain key type value host; do
    ${2} defaults ${host} write ${domain} "${key}" ${type} "${value}"
  done
}


# Define Function =config_plist=

T="$(printf '\t')"

config_plist () {
  printf "%s\n" "$1" | \
  while IFS="$T" read command entry type value; do
    case "$value" in
      (\$*)
        $4 /usr/libexec/PlistBuddy "$2" \
          -c "$command '${3}${entry}' $type '$(eval echo \"$value\")'" 2> /dev/null ;;
      (*)
        $4 /usr/libexec/PlistBuddy "$2" \
          -c "$command '${3}${entry}' $type '$value'" 2> /dev/null ;;
    esac
  done
}

# Define Function =config_launchd=

config_launchd () {
  test -d "$(dirname $1)" || \
    $3 mkdir -p "$(dirname $1)"

  test -f "$1" && \
    $3 launchctl unload "$1" && \
    $3 rm -f "$1"

  config_plist "$2" "$1" "$4" "$3" && \
    $3 plutil -convert xml1 "$1" && \
    $3 launchctl load "$1"
}

# Define Function =config_xcode=

config_xcode() {
  x="$(find '/Applications' -maxdepth 1 -regex '.*/Xcode[^ ]*.app' -print -quit)"
  if test -n "${x}"; then
    sudo xcode-select -s "${x}"
    sudo xcodebuild -license accept
  fi
}

# Configure iStat Menus

config_istatmenus () {
  test -d "/Applications/iStat Menus.app" && \
    open "/Applications/iStat Menus.app"
}

# Configure Bartender

config_bartender () {
  test -d "/Applications/Bartender 3.app" && \
    open "/Applications/Bartender 3.app"
}

# Configure Alfred

config_alfred () {
  test -d "/Applications/Alfred 5.app" && \
    open "/Applications/Alfred 5.app"
}

# Configure Amphetamine

config_amphetamine () {
  test -d "/Applications/Amphetamine.app" && \
    open "/Applications/Amphetamine.app"
  test -d "/Applications/Amphetamine Enhancer.app" && \
    open "/Applications/Amphetamine Enhancer.app"
}

# Configure stts

config_stts () {
  test -d "/Applications/stts.app" && \
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
avcodec	avcodec-hw	vda'

config_vlc () {
  config_defaults "${_vlc_defaults}"
  if which crudini > /dev/null; then
    test -d "${HOME}/Library/Preferences/org.videolan.vlc" || \
      mkdir -p "${HOME}/Library/Preferences/org.videolan.vlc"
    printf "%s\n" "${_vlcrc}" | \
    while IFS="$(printf '\t')" read section key value; do
      crudini --set "${HOME}/Library/Preferences/org.videolan.vlc/vlcrc" "${section}" "${key}" "${value}"
    done
  fi
}

# Customize Login Items

_disableditems='/Applications/autoping.app
/Applications/Bartender 4.app
/Applications/Coffitivity.app
/Applications/HardwareGrowler.app
'
_loginitems='/Applications/1Password.app
/Applications/Alfred 5.app
/Applications/Amphetamine.app
/Applications/Docker.app
/Applications/Google Drive File Stream.app
/Applications/iStat Menus.app
/Applications/iTunes.app/Contents/MacOS/iTunesHelper.app
/Applications/Menubar Countdown.app
/Applications/Muzzle.app
/Applications/Resolutionator.app
/Applications/Slack.app
/Applications/Spotify.app
/Applications/stts.app
/Applications/WhatsApp.app'
custom_loginitems () {
  printf "%s\n" "${_loginitems}" | \
  while IFS="$(printf '\t')" read app; do
    if test -e "$app"; then
      osascript - "$app" << EOF > /dev/null
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
_term_plist='delete
add	:dict
add	:name	string	tapani
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

custom_terminal () {
  config_plist "${_term_plist}" \
    "${HOME}/Library/Preferences/com.apple.Terminal.plist" \
    ":Window Settings:tapani"
  config_defaults "${_term_defaults}"
}

# Customize Default UTIs

_duti='com.apple.DiskImageMounter	com.apple.disk-image	all
com.apple.DiskImageMounter	public.disk-image	all
com.apple.DiskImageMounter	public.iso-image	all
com.apple.QuickTimePlayerX	com.apple.coreaudio-format	all
com.apple.QuickTimePlayerX	com.apple.quicktime-movie	all
com.apple.QuickTimePlayerX	com.microsoft.waveform-audio	all
com.apple.QuickTimePlayerX	public.aifc-audio	all
com.apple.QuickTimePlayerX	public.aiff-audio	all
com.apple.QuickTimePlayerX	public.audio	all
com.apple.QuickTimePlayerX	public.mp3	all
com.apple.Safari	com.compuserve.gif	all
com.apple.Terminal	com.apple.terminal.shell-script	all
com.apple.iTunes	com.apple.iTunes.audible	all
com.apple.iTunes	com.apple.iTunes.ipg	all
com.apple.iTunes	com.apple.iTunes.ipsw	all
com.apple.iTunes	com.apple.iTunes.ite	all
com.apple.iTunes	com.apple.iTunes.itlp	all
com.apple.iTunes	com.apple.iTunes.itms	all
com.apple.iTunes	com.apple.iTunes.podcast	all
com.apple.iTunes	com.apple.m4a-audio	all
com.apple.iTunes	com.apple.mpeg-4-ringtone	all
com.apple.iTunes	com.apple.protected-mpeg-4-audio	all
com.apple.iTunes	com.apple.protected-mpeg-4-video	all
com.apple.iTunes	com.audible.aa-audio	all
com.apple.iTunes	public.mpeg-4-audio	all
com.apple.installer	com.apple.installer-package-archive	all
com.todesktop.230313mzl4w4u92	com.apple.binary-property-list	editor
com.todesktop.230313mzl4w4u92	com.apple.crashreport	editor
com.todesktop.230313mzl4w4u92	com.apple.dt.document.ascii-property-list	editor
com.todesktop.230313mzl4w4u92	com.apple.dt.document.script-suite-property-list	editor
com.todesktop.230313mzl4w4u92	com.apple.dt.document.script-terminology-property-list	editor
com.todesktop.230313mzl4w4u92	com.apple.log	editor
com.todesktop.230313mzl4w4u92	com.apple.property-list	editor
com.todesktop.230313mzl4w4u92	com.apple.rez-source	editor
com.todesktop.230313mzl4w4u92	com.apple.symbol-export	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.ada-source	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.bash-script	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.configsettings	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.csh-script	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.fortran-source	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.ksh-script	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.lex-source	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.make-script	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.mig-source	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.pascal-source	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.strings-text	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.tcsh-script	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.yacc-source	editor
com.todesktop.230313mzl4w4u92	com.apple.xcode.zsh-script	editor
com.todesktop.230313mzl4w4u92	com.apple.xml-property-list	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.actionscript-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.erb-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.ini-configuration	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.javascript-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.json-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.jsp-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.lasso-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.lua-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.setext-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.sql-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.tcl-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.tex-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.textile-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.vbscript-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.vectorscript-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.verilog-hdl-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.vhdl-source	editor
com.todesktop.230313mzl4w4u92	com.barebones.bbedit.yaml-source	editor
com.todesktop.230313mzl4w4u92	com.netscape.javascript-source	editor
com.todesktop.230313mzl4w4u92	com.sun.java-source	editor
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge80255drq	all
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge80g55gq3w0n	all
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge80g55sq2	all
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge80y2xzrf0gk3pw	all
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge81e3dtqq	all
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge81e7k	all
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge81g25xsq	all
com.todesktop.230313mzl4w4u92	dyn.ah62d4rv4ge81g2pxsq	all
com.todesktop.230313mzl4w4u92	net.daringfireball.markdown	editor
com.todesktop.230313mzl4w4u92	public.assembly-source	editor
com.todesktop.230313mzl4w4u92	public.c-header	editor
com.todesktop.230313mzl4w4u92	public.c-plus-plus-source	editor
com.todesktop.230313mzl4w4u92	public.c-source	editor
com.todesktop.230313mzl4w4u92	public.csh-script	editor
com.todesktop.230313mzl4w4u92	public.json	editor
com.todesktop.230313mzl4w4u92	public.lex-source	editor
com.todesktop.230313mzl4w4u92	public.log	editor
com.todesktop.230313mzl4w4u92	public.mig-source	editor
com.todesktop.230313mzl4w4u92	public.nasm-assembly-source	editor
com.todesktop.230313mzl4w4u92	public.objective-c-plus-plus-source	editor
com.todesktop.230313mzl4w4u92	public.objective-c-source	editor
com.todesktop.230313mzl4w4u92	public.patch-file	editor
com.todesktop.230313mzl4w4u92	public.perl-script	editor
com.todesktop.230313mzl4w4u92	public.php-script	editor
com.todesktop.230313mzl4w4u92	public.plain-text	editor
com.todesktop.230313mzl4w4u92	public.precompiled-c-header	editor
com.todesktop.230313mzl4w4u92	public.precompiled-c-plus-plus-header	editor
com.todesktop.230313mzl4w4u92	public.python-script	editor
com.todesktop.230313mzl4w4u92	public.ruby-script	editor
com.todesktop.230313mzl4w4u92	public.script	editor
com.todesktop.230313mzl4w4u92	public.shell-script	editor
com.todesktop.230313mzl4w4u92	public.source-code	editor
com.todesktop.230313mzl4w4u92	public.text	editor
com.todesktop.230313mzl4w4u92	public.utf16-external-plain-text	editor
com.todesktop.230313mzl4w4u92	public.utf16-plain-text	editor
com.todesktop.230313mzl4w4u92	public.utf8-plain-text	editor
com.todesktop.230313mzl4w4u92	public.xml	editor
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
org.videolan.vlc	org.videolan.3gp	all
org.videolan.vlc	org.videolan.aac	all
org.videolan.vlc	org.videolan.ac3	all
org.videolan.vlc	org.videolan.aiff	all
org.videolan.vlc	org.videolan.amr	all
org.videolan.vlc	org.videolan.aob	all
org.videolan.vlc	org.videolan.ape	all
org.videolan.vlc	org.videolan.asf	all
org.videolan.vlc	org.videolan.avi	all
org.videolan.vlc	org.videolan.axa	all
org.videolan.vlc	org.videolan.axv	all
org.videolan.vlc	org.videolan.divx	all
org.videolan.vlc	org.videolan.dts	all
org.videolan.vlc	org.videolan.dv	all
org.videolan.vlc	org.videolan.flac	all
org.videolan.vlc	org.videolan.flash	all
org.videolan.vlc	org.videolan.gxf	all
org.videolan.vlc	org.videolan.it	all
org.videolan.vlc	org.videolan.mid	all
org.videolan.vlc	org.videolan.mka	all
org.videolan.vlc	org.videolan.mkv	all
org.videolan.vlc	org.videolan.mlp	all
org.videolan.vlc	org.videolan.mod	all
org.videolan.vlc	org.videolan.mpc	all
org.videolan.vlc	org.videolan.mpeg-audio	all
org.videolan.vlc	org.videolan.mpeg-stream	all
org.videolan.vlc	org.videolan.mpeg-video	all
org.videolan.vlc	org.videolan.mxf	all
org.videolan.vlc	org.videolan.nsv	all
org.videolan.vlc	org.videolan.nuv	all
org.videolan.vlc	org.videolan.ogg-audio	all
org.videolan.vlc	org.videolan.ogg-video	all
org.videolan.vlc	org.videolan.oma	all
org.videolan.vlc	org.videolan.opus	all
org.videolan.vlc	org.videolan.quicktime	all
org.videolan.vlc	org.videolan.realmedia	all
org.videolan.vlc	org.videolan.rec	all
org.videolan.vlc	org.videolan.rmi	all
org.videolan.vlc	org.videolan.s3m	all
org.videolan.vlc	org.videolan.spx	all
org.videolan.vlc	org.videolan.tod	all
org.videolan.vlc	org.videolan.tta	all
org.videolan.vlc	org.videolan.vob	all
org.videolan.vlc	org.videolan.voc	all
org.videolan.vlc	org.videolan.vqf	all
org.videolan.vlc	org.videolan.vro	all
org.videolan.vlc	org.videolan.wav	all
org.videolan.vlc	org.videolan.webm	all
org.videolan.vlc	org.videolan.wma	all
org.videolan.vlc	org.videolan.wmv	all
org.videolan.vlc	org.videolan.wtv	all
org.videolan.vlc	org.videolan.wv	all
org.videolan.vlc	org.videolan.xa	all
org.videolan.vlc	org.videolan.xesc	all
org.videolan.vlc	org.videolan.xm	all
org.videolan.vlc	public.ac3-audio	all
org.videolan.vlc	public.audiovisual-content	all
org.videolan.vlc	public.avi	all
org.videolan.vlc	public.movie	all
org.videolan.vlc	public.mpeg	all
org.videolan.vlc	public.mpeg-2-video	all
org.videolan.vlc	public.mpeg-4	all'
custom_duti () {
  if test -x "/usr/local/bin/duti"; then
    test -f "${HOME}/Library/Preferences/org.duti.plist" && \
      rm "${HOME}/Library/Preferences/org.duti.plist"

    printf "%s\n" "${_duti}" | \
    while IFS="$(printf '\t')" read id uti role; do
      defaults write org.duti DUTISettings -array-add \
        "{
          DUTIBundleIdentifier = '$a';
          DUTIUniformTypeIdentifier = '$b';
          DUTIRole = '$c';
        }"
    done

    duti "${HOME}/Library/Preferences/org.duti.plist" 2> /dev/null
  fi
}

