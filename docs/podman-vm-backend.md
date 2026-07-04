# Podman VM backend — arm vs Intel (recap for automation)

Recap of the podman/VM-backend state established manually on the Apple Silicon
Mac (`asterix`), and what `macos-setup` must codify so it reproduces on other
machines without conflict. **Intel Macs stay on podman 5.x; Apple Silicon uses
podman 6.x + the libkrun (krunkit) backend.**

## Wanted state

| | Apple Silicon | Intel Mac |
|---|---|---|
| podman | **6.x** (currently 6.0.0) | **newest 5.x** (6.x drops Intel-mac support) |
| VM backend | **libkrun** (krunkit) | applehv (vfkit) — podman default on Intel |
| Extra brew deps | `krunkit`, **`libepoxy`** | none |

## What was changed manually on this Mac (so it doesn't conflict)

1. `brew upgrade podman` → 6.0.0 (was 5.8.2; 5.8.2 crashed vfkit on macOS 26).
2. `brew install libepoxy` → **the key fix** (see gotcha below).
3. Wrote `~/.config/containers/containers.conf`:
   ```toml
   [machine]
   provider = "libkrun"
   ```
   Note: podman 6.x on arm already **defaults** to libkrun when krunkit is
   present, so this file is belt-and-suspenders. Either codify it via dotfiles
   or delete it and rely on the default — just pick one so there's no drift.
4. Created the machine: `podman machine init --cpus 8 --memory 20000 --disk-size 200 --now` → runs as `libkrun`.

The kokoro TTS container is unrelated to this backend work and will be
automated separately later.

## The gotcha (root cause, must fix in Brewfile)

krunkit aborted at launch with `krunkit was terminated by signal: abort trap`.
Real cause: `libkrun` couldn't load **`libepoxy.0.dylib`** — a runtime dep that
was not present. The arm `Brewfile` already has `tap "slp/krun"` +
`brew "krunkit"` but **not `libepoxy`**, and brew did not pull it transitively.

**Action:** add `brew "libepoxy"` to the arm `Brewfile` (near the krunkit line).
Without it, a fresh arm install reproduces the abort trap.

## macos-setup changes

### arm `Brewfile` (already has krunkit — just add the dep)
```ruby
tap "slp/krun"
# Podman (Apple Silicon: libkrun/krunkit backend)
brew "podman"          # 6.x
brew "podman-compose"
brew "krunkit"
brew "libepoxy"        # <-- ADD: libkrun runtime dep, else krunkit abort-traps
cask "podman-desktop"
```
Optional determinism: manage `~/.config/containers/containers.conf` with
`[machine] provider = "libkrun"` via the dotfiles mechanism.

### `intel.Brewfile` (pin to 5.x — the hard part)
Currently `brew "podman"`, which now resolves to 6.x. Must pin newest 5.x.
Homebrew has no `podman@5` formula, so options (pick one):
- `brew extract podman <yourtap> --version=5.<latest>` then
  `brew "yourtap/podman@5.<latest>"` in `intel.Brewfile`; or
- install 5.x and `brew pin podman` (blocks the 6.x upgrade), documented in a
  `tasks/` step rather than the Brewfile.

Leave Intel on the default **applehv/vfkit** backend (no krunkit, no libepoxy).

## Convergence on this Mac (avoid conflict when macos-setup runs here)

This Mac already has: podman 6.0.0, libepoxy, krunkit, a hand-written
`containers.conf` (provider=libkrun), and a running libkrun machine. When the
automated setup runs here it should be **idempotent** — installing the same
packages is a no-op. Only reconcile `containers.conf`: if macos-setup manages it
via dotfiles, let that file replace the hand-written one (identical content, so
harmless); if macos-setup relies on the podman default instead, delete the
hand-written file.
