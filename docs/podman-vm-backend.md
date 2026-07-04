# Podman VM backend — arm vs Intel (recap for automation)

Recap of the podman/VM-backend state established on the Apple Silicon Mac
(`asterix`), and what `macos-setup` still needs to codify for other machines.
**Intel Macs stay on podman 5.x; Apple Silicon uses podman 6.x + the libkrun
(krunkit) backend.**

## Wanted state

| | Apple Silicon | Intel Mac |
|---|---|---|
| podman | **6.x** (currently 6.0.0) | **newest 5.x** (6.x drops Intel-mac support) |
| VM backend | **libkrun** (krunkit) | applehv (vfkit) — podman default on Intel |
| Extra brew deps | `krunkit`, **`libepoxy`** | none |

## Status

- **arm Brewfile — DONE** (commit `d90fd65`): added `brew "libepoxy"` next to the
  existing `tap "slp/krun"` + `brew "krunkit"`. This is the whole fix (see gotcha).
- **Intel pin — TODO** (other machine): pin podman to newest 5.x.
- **This Mac** is fully in the wanted state: podman 6.0.0, libepoxy, krunkit, and
  a running `libkrun` machine. No manual config file remains (see below).

## The gotcha (root cause)

krunkit aborted at launch with `krunkit was terminated by signal: abort trap`.
Real cause: `libkrun` couldn't load **`libepoxy.0.dylib`** — a runtime dep that
was not installed. The arm `Brewfile` had `krunkit` but not `libepoxy`, and brew
did not pull it transitively. Adding `brew "libepoxy"` fixes it; without it a
fresh arm install reproduces the abort trap.

## No containers.conf needed

podman 6.x on Apple Silicon **defaults to libkrun** when krunkit is present, so
no `provider` config is required. A `~/.config/containers/containers.conf` with
`[machine] provider = "libkrun"` was briefly used during debugging and then
**removed** — the machine stays libkrun on the default. macos-setup should **not**
manage this file for arm (rely on the default). Only create it (with
`provider = "applehv"`) if you deliberately want to fall back to the applehv/vfkit
backend on Apple Silicon.

## What was done on this Mac

1. `brew upgrade podman` → 6.0.0 (5.8.2 crashed vfkit on macOS 26).
2. `brew install libepoxy` (now codified in the arm Brewfile).
3. `podman machine init --cpus 8 --memory 20000 --disk-size 200 --now` → runs as `libkrun`.

The kokoro TTS container is unrelated to this backend work and will be automated
separately once the server architecture (always-on host vs. Mac-local fallback)
is settled.

## `intel.Brewfile` — pin to 5.x (the hard part, TODO)

Currently `brew "podman"`, which now resolves to 6.x. Must pin newest 5.x.
Homebrew has no `podman@5` formula, so options (pick one):
- `brew extract podman <yourtap> --version=5.<latest>` then
  `brew "yourtap/podman@5.<latest>"` in `intel.Brewfile`; or
- install 5.x and `brew pin podman` (blocks the 6.x upgrade), documented in a
  `tasks/` step rather than the Brewfile.

Leave Intel on the default **applehv/vfkit** backend (no krunkit, no libepoxy).
libkrun is Apple-Silicon-only; applehv is the correct and best Intel option
(qemu is the only alternative and is inferior).

## Convergence

Re-running macos-setup on this Mac is idempotent — same packages, no config file
to reconcile. The only outstanding automation work is the Intel 5.x pin above.
