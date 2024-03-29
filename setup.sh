#!/bin/sh

# Define Function =ask=

ask () {
  osascript - "${1}" "${2}" "${3}" << EOF 2> /dev/null
    on run { _title, _action, _default }
      tell app "System Events" to return text returned of (display dialog _title with title _title buttons { "Cancel", _action } default answer _default)
    end run
EOF
}

# Define Function =ask2=

ask2 () {
  osascript - "$1" "$2" "$3" "$4" "$5" "$6" << EOF 2> /dev/null
on run { _text, _title, _cancel, _action, _default, _hidden }
  tell app "Terminal" to return text returned of (display dialog _text with title _title buttons { _cancel, _action } cancel button _cancel default button _action default answer _default hidden answer _hidden)
end run
EOF
}

# Define Function =p=

p1 () {
  printf "\n\033[1m\033[34m%s\033[0m\n\n" "${1}"
}

# Define Function =run=

run () {
  osascript - "${1}" "${2}" "${3}" << EOF 2> /dev/null
    on run { _title, _cancel, _action }
      tell app "Terminal" to return button returned of (display dialog _title with title _title buttons { _cancel, _action } cancel button 1 default button 2 giving up after 5)
    end run
EOF
}

. tasks/init.sh
. tasks/install.sh
. tasks/config.sh

if [ "${1}" = "init" ]; then
  init
elif [ "${1}" = "init_user" ]; then
  init_user
elif [ "${1}" = "init_ssh_local" ]; then
  init_ssh_local
elif [ "${1}" = "init_ssh_1password" ]; then
  init_ssh_1password
elif [ "${1}" = "install" ]; then
  install
elif [ "${1}" = "config" ]; then
  config
else
  echo "Usage: $0 [init | init_user | install | config]"
  echo "See README.md for more information."
fi
