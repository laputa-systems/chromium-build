# CHB-21: Replace the Helium build with ungoogled Chromium for Wayland on arm64 and amd64

Status: accepted

## Summary

Build one browser line instead of two. The `chromium` product gains a Wayland
desktop profile for linux/arm64 and linux/amd64, built from upstream Chromium,
the ungoogled-chromium series, and the local patches this repository already
carries. That desktop is the product bctl runs as a headless or headed
appliance. The patches worth keeping from the Helium line move onto it. When
bctl qualifies the new package, the `helium` product, its locks, its builder
and its documentation are removed. Helium itself stays an end-user browser,
used from upstream releases.

## Motivation

Two lines track two Chromium versions. `config/inputs.lock` pins Chromium
153.0.8010.52 with ungoogled-chromium `a638756e`; `config/helium.inputs.lock`
pins 156.0.8078.79 through Helium 0.20.3.1. Every Chromium update is done
twice, and the arm64 appliance build exists only on the Helium side.

Most of Helium is work this repository's consumers do not want. Helium
`2f81b9ca` carries 346 patches:

| Origin | Patches | Content |
| --- | ---: | --- |
| `helium/ui` | 104 | Branding, layout, vertical tabs, zen mode, rounded and inset contents |
| `helium/core` | 126 | Importers, bangs, search engines, update channel, onboarding, fingerprint noise, Helium services, and a few privacy changes |
| `helium/settings` | 26 | Settings pages for those features |
| `ungoogled-chromium` | 71 | The de-Googling layer |
| Brave, Bromite, Inox, Iridium, Debian, upstream fixes | 19 | Privacy and build fixes |

The repository already undoes Helium behavior with local patches:
`helium-remove-onboarding`, `helium-disable-autoupdates`,
`helium-devtools-product`, `helium-devtools-policy-prefs`,
`helium-no-devtools-frontend` and `helium-fake-audio-persona`. bctl also
disables `helium-noise` and `helium-split-clienthello` at launch. Helium's UI
also costs bctl measurable work. Its 8 px rounded, inset contents force an
intermediate full-viewport render pass on every software-composited frame,
about half of the guest's busy time while scrolling a headed session
(joshuarli/bctl BCTL-48).

The de-Googling layer stays. ungoogled-chromium removes background Google
services, field trials, component and variations fetches, and other
phone-home traffic. That reduces idle CPU, memory, network and startup work in
an appliance, and it is a thin series built to follow each Chromium release.

## Design

### One product, three platforms

- **The `chromium` product** keeps its `headless-debug` and `headless-release`
  profiles and adds `chromium-wayland-release`. That profile is derived from
  `config/profiles/helium-wayland-release.gn` without Helium's arguments: Ozone
  Wayland only, musl, ThinLTO with PGO phase 2, no D-Bus, and the hermetic
  smoke target.
- **linux/arm64 joins the `chromium` product.** The `helium` product already
  proves an aarch64 musl builder with a pinned Rust, Crubit and bindgen
  toolchain under `architecture_inputs.arm64`. Move that builder, its inputs
  and its pure-runtime build into the `chromium` product unchanged.
- **macOS** stays on its native backend and uses the same Chromium and
  ungoogled-chromium revisions, so bctl's native control is a same-build
  browser rather than stock Chromium.app.

### Chromium revision

Advance `config/inputs.lock` to the Chromium version the Helium line ships
(156.0.8078.79) or newer, with the matching ungoogled-chromium and
portablelinux revisions. Refresh the existing `chromium` local patches. The
headless amd64 line and the desktop line then share one revision and one
compiler cache.

### Local patch disposition

The `helium` layer in `config/patches/helium/` has 39 entries.

| Disposition | Patches | Reason |
| --- | --- | --- |
| **Carry** | The 16 `adblock-*` patches and `adblock-crates` | Native adblock-rust (CHB-9, CHB-20) is the appliance's ad blocker. Rename its pref from `helium.adblock.enabled` to a product-neutral name, and source its filter lists from the locked inputs rather than Helium's services. |
| **Carry** | `helium-bounded-back-to-back-begin-frames` | The CHB-11 begin-frame fix is independent of Helium. Rename it. |
| **Carry if still needed** | `helium-no-dbus`, `helium-partitionalloc-musl-ifunc`, `helium-dawn-vulkan-headers`, `declare-is-musl`, `libcxx-musl-rune-table`, `musl-target-triple`, `musl-bindgen-libclang-flags`, `musl-mallinfo-guard`, `musl-fontconfig-strerror-r`, `musl-libsync-cdefs-include`, `musl-swiftshader-llvm-config`, `musl-v8-no-execinfo`, `rust-bytemuck-simd`, `rust-chromium-toolchain`, `alpine-node-version` | These are musl, arm64 toolchain and D-Bus fixes. Drop each one that the `chromium` series already covers or that no longer applies. |
| **Drop** | `helium-remove-onboarding`, `helium-disable-autoupdates`, `helium-devtools-product`, `helium-devtools-policy-prefs`, `helium-no-devtools-frontend`, `helium-fake-audio-persona` | They exist to undo Helium. The `chromium` product's headless DevTools patches handle DevTools. |

Port Helium-core patches one at a time, only where an acceptance check needs
one. Candidates: `reduce-accept-language-headers`,
`disable-ad-topics-and-etc`, `prefer-https-by-default` and
`global-privacy-control-pref`. Do not carry Helium UI, settings, services,
search, importer, update or noise patches.

### Acceptance

The existing gates still apply: source and patch application, system-library
preflight, purity, staged and packaged checks, and the hermetic smoke. The
package then qualifies in bctl the way Helium r7 and r9 did:

- **Correctness:** browser behavior, Wayland headed sessions, WebGL through
  SwiftShader, and native adblock blocking.
- **Benchmarks:** `make bench TIER=scenario` against the current Helium r9
  image, alternating rounds.
- **Outbound traffic:** every connection through bctl's egress proxy, idle
  and during the workflows, listed by host and byte count against Helium r9.
- **Resources:** startup, idle CPU and footprint against Helium r9.
- **Compositing:** a vCPU-sampled headed scroll showing one render pass per
  frame.

### Retirement

After bctl promotes the Chromium image, remove the `helium` product:

- `config/helium.*` and `config/patches/helium/`,
- `scripts/prepare-helium.py`, `scripts/setup-helium-adblock.py` and the
  Helium test scripts,
- Helium's Makefile branch, cache volumes and builder image,
- the Helium sections of `docs/linux.md`, plus `docs/helium-acceptance.md`
  and `docs/helium-adblock-rust.md`. Move the adblock design into a
  product-neutral doc first.

Re-target or close the Helium tickets:

- **CHB-8** (Helium behavior matrix) becomes the Chromium desktop acceptance
  matrix.
- **CHB-9** and **CHB-20** (adblock) carry over with the renamed pref and
  locked filter sources.
- **CHB-11** carries over with its patch.
- **CHB-7** (the owner's Alpine dwl desktop) depends on the open question
  below.

## Alternatives considered

- **Keep Helium and add more patches that undo it.** Rejected. Every Helium
  rebase brings back UI and service changes to undo, and the appliance stays a
  Chromium release behind ungoogled-chromium.
- **Build upstream Chromium without the ungoogled series.** Rejected.
  ungoogled-chromium is cheap to carry, and it removes background work an
  appliance pays for.
- **Rebase our local patches onto Helium's core without its UI.** Rejected.
  It keeps the Helium cadence and still has to separate Helium's core from
  its features patch by patch.

## Open questions

1. **The Alpine dwl desktop.** Upstream Helium ships only glibc Linux builds,
   and CHB-7 qualifies this repository's musl Helium package on that machine.
   Should the new Chromium Wayland package replace Helium there, or does that
   machine keep a Helium build?
2. **Helium-core privacy patches.** Which, if any, does acceptance show bctl
   needs? Decide by the outbound-traffic audit and the behavior matrix.
3. **The adblock pref and filter source.** What names and locked filter-list
   inputs replace `helium.adblock.enabled` and Helium's filter services?

## Decision

Accepted by the owner on 2026-11-19, as the priority for browser builds.
