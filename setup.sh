#!/bin/bash

# Ask for the administrator password upfront and keep-alive
sudo -v

# Keep-alive: update existing `sudo` time stamp until has finished
while true; do
	sudo -n true
	sleep 120
	kill -0 "$$" || exit
done 2>/dev/null &

# Define Function =ask=
ask() {
	osascript - "${1}" "${2}" "${3}" <<EOF 2>/dev/null
    on run { _title, _action, _default }
      tell app "System Events" to return text returned of (display dialog _title with title _title buttons { "Cancel", _action } default answer _default)
    end run
EOF
}

# Define Function =ask2=
ask2() {
	osascript - "$1" "$2" "$3" "$4" "$5" "$6" <<EOF 2>/dev/null
on run { _text, _title, _cancel, _action, _default, _hidden }
  tell app "Terminal" to return text returned of (display dialog _text with title _title buttons { _cancel, _action } cancel button _cancel default button _action default answer _default hidden answer _hidden)
end run
EOF
}

# Define Function =p=
p1() {
	printf "\n\033[1m\033[34m==> %s\033[0m\n" "${1}"
}

# Define Function =p2= (darker blue version)
p2() {
	printf "\033[34m=> %s\033[0m\n" "${1}"
}

# Define Function =p3= (gray version)
p3() {
	printf "\033[90m=> %s\033[0m\n" "${1}"
}

# Define Function =run=
run() {
	osascript - "${1}" "${2}" "${3}" <<EOF 2>/dev/null
    on run { _title, _cancel, _action }
      tell app "Terminal" to return button returned of (display dialog _title with title _title buttons { _cancel, _action } cancel button 1 default button 2 giving up after 5)
    end run
EOF
}

if [ "${1}" = "init" ]; then
	. tasks/init.sh
	init
elif [ "${1}" = "init_user" ]; then
	. tasks/init.sh
	init_user
elif [ "${1}" = "init_ssh_local" ]; then
	. tasks/init.sh
	init_ssh_local
elif [ "${1}" = "init_ssh_1password" ]; then
	. tasks/init.sh
	init_ssh_1password
elif [ "${1}" = "install" ]; then
	. tasks/install.sh
	install
elif [ "${1}" = "dotfiles" ]; then
	. tasks/install.sh
	install_dotfiles
elif [ "${1}" = "config" ]; then
	. tasks/config.sh
	config
else
	echo "Usage: $0 [init | init_user | init_ssh_local | init_ssh_1password | install | dotfiles | config]"
	echo "See README.md for more information."
fi
