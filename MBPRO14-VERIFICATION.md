# MBPro14 plan verification

This file records the durable verification trail for the local plan at
`.ai/plans/2026-04-13-mbpro14-testing.md`.

## Plan checklist status

| Checklist item | Status | Verification note |
|---|---|---|
| `./setup.sh init` | N/A | No plan changes touched `tasks/init.sh` init flow. |
| `./setup.sh new_account` | N/A | Conditional step; no plan changes touched the account-creation path. |
| `./setup.sh clean_account` | N/A | No plan changes touched this path. |
| `./setup.sh init_ssh_1password` or `./setup.sh init_ssh_local` | N/A | No plan changes touched these flows. |
| `./setup.sh install` | Verified by affected-path checks | The changed install-side code in `tasks/install.sh`, `.path`, and dotfiles OpenCode config/docs was verified with `shellcheck`, `bash -n`, JSON validation, and targeted manual QA for Podman socket handling. The full setup script itself was not auto-run because repo policy forbids running setup scripts automatically. |
| `./setup.sh dotfiles` | Verified by affected-path checks | Dotfiles-owned changes were verified through submodule commits, JSON validation for OpenCode config, doc readback, and parent repo pointer updates. `dotfiles/bootstrap.sh` itself was not auto-run because repo policy forbids running setup scripts automatically. |
| `./setup.sh config` | Verified by affected-path checks | The changed config/macOS paths in `tasks/config.sh` and `tasks/macos.sh` were verified with `shellcheck`, `bash -n`, and targeted manual QA for Obsidian, Resolutionator, menu-bar defaults, and host-specific shell/default writes. The full config script itself was not auto-run because repo policy forbids running setup scripts automatically. |
| `restore.sh` flow and backup assumptions | Verified | `backup.sh` + `restore.sh` were round-tripped against a temp `HOME` with seeded files for `com.jordanbaird.Ice.plist`, `com.manytricks.Resolutionator.plist`, and `.config/tmuxinator/example.yml`; all restored successfully from the generated archive. |
| Manual steps from `README.md` | Verified as documentation changes | Manual-only items were reviewed as documentation updates, not auto-executed, because they require real user/app interaction by design. |

## Commit mapping

### Parent repo commits

- `babe4b7` Enable standard function keys during setup
- `a04679d` Open GIF files in Preview
- `c14e9af` Remove conflicting duti audio mappings
- `e500957` Use host-specific SSH signing keys
- `96748cd` Update docs for oh-my-openagent
- `798c748` Move macOS defaults into tasks
- `f6e6785` Back up Ice preferences in restore flow
- `95e61a7` Register the Obsidian CLI during config
- `d7a98dc` Hide default menu bar items during setup
- `bd9edcc` Document Teams menu bar setup
- `f0358da` Install krunkit for Podman on Apple Silicon
- `f309e1d` Create host-specific Podman machines
- `e2a5cf0` Use Podman socket for Docker compatibility
- `a7da277` Add tmopro18 charger-connected worker mode
- `c1d8004` Document safe tmopro18 worker tweaks
- `9e9d0bc` Document Resolutionator restore-backed defaults
- `479d5ab` Automate Resolutionator faceless mode and hotkey
- `da8c85d` Document OpenAgent config ownership

### Dotfiles submodule commits

- `b7e063d` Enable standard function keys by default
- `7c1b586` Rename OpenCode plugin to oh-my-openagent
- `f086208` Move macOS automation out of dotfiles
- `2269ab0` Document OpenCode OpenAgent config ownership

## Targeted manual QA highlights

- Resolutionator defaults readback confirmed:
  - `Stealth Mode = 1`
  - `Keyboard Menu Trigger = { keyCode = 35, modifierFlags = 1966080 }`
- Live Resolutionator menu parsing produced `1680x1050` from the built-in display entry.
- Obsidian CLI automation was exercised against temp files for both the symlink target and `~/.zprofile` cleanup logic.
- Podman Docker-compat handling was exercised by extracting a socket path from sample `podman machine inspect` JSON.
- Backup/restore round-trip restored all seeded test files from the generated archive.

## Safety note

`setup.sh`, `tasks/*.sh`, and `dotfiles/bootstrap.sh` were not auto-run end-to-end in this verification pass because the repository guidance explicitly forbids running setup scripts automatically. Verification therefore focuses on the affected code paths, committed outputs, and isolated/manual-safe checks instead of full unattended setup execution.
