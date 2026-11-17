Prefix: CHB
Next: 19

### CHB-7 · P1 · qualify the Helium package on the owner's Alpine dwl desktop

- **Area:** Tests
- **Where:** `docs/helium-acceptance.md`, `scripts/test-functional.py`, `scripts/install-runtime.py`
- **Problem:** The published prerelease passed only the five automated checks in the isolated Sway fixture. The direct-session harness mode (`--wayland-session existing`, `--runtime-kind package`) does not exist, the package has not run in the owner's dwl session, and it has not been installed live. The release stays a prerelease until target evidence exists.
- **Recommendation:** Implement the direct-session and package runtime modes, then follow `docs/helium-acceptance.md`: verify-extract the archive, run the session-dependent rows in the active dwl session with a disposable HOME and profile, and after an explicit live-install request install the archive and repeat them through `~/usr/bin/helium`. Record passed, failed, and not-executed rows; never promote the Sway result to target proof.
- **Fix with:** CHB-6
- **Effort:** M

### CHB-11 · P1 · bound back-to-back begin frames sent to the browser UI compositor in unthrottled headless

- **Area:** Performance
- **Where:** `components/viz/common/frame_sinks/begin_frame_source.cc` (`BackToBackBeginFrameSource::DidFinishFrame`, `OnTimerTick`), `cc/scheduler/scheduler.cc` (frames skipped with `kRecoverLatency`), `config/patches/helium/`, `config/helium.patch-disposition.json`; consumer evidence in laputa-systems/bctl `tests/bench/results/2026-11-15-ui-begin-frame-flood/` and the switch list in bctl `guest/appliance/src/browser.rs`
- **Problem:** bctl runs headless Helium with `--disable-frame-rate-limit`, which makes Viz use `BackToBackBeginFrameSource`. That source re-ticks as soon as every observer finishes a frame. During each navigation and click, the browser process's own `ui::Compositor` on Linux Aura/Ozone needs a main frame while an earlier frame awaits acknowledgement, so its cc scheduler drops every begin frame with `kRecoverLatency` and the next arrives at once. In five warm open-and-click cycles of the Speedometer news fixture, the browser main thread receives 10k–28k `viz.mojom.CompositorFrameSinkClient` messages and 10k–13k `DidNotProduceFrame` events, against about 200 messages for native macOS Chromium, which composites its UI through Cocoa. Renderer main-thread task time is at parity, yet guest news open and click are 39.4/22.0 ms against 22.4/12.6 ms for native Helium, on a two-vCPU VM where this spin competes with the renderer. Kiosk mode removes browser UI paints but not the flood, and no switch or feature bounds the source. That the flood causes the latency gap is unproven; it is consistent with warm news falling from 388.9 to 245.9 to 204.9 ms at one, two and four vCPUs.
- **Recommendation:** Add a Helium patch that stops the back-to-back source spinning on an observer that did not produce a frame: either re-arm that observer only after its pending frame is acknowledged or new damage arrives, or apply a small minimum interval (for example 1 ms) between back-to-back ticks. Keep screenshot and input latency unthrottled, since bctl adopted the unbounded source for those. Build an archive, then A/B it in bctl with `tests/bench/kernel_experiment.py` on warm news open/click and VM CPU, and confirm with a trace that the browser-main `CompositorFrameSinkClient` count drops to native scale.
- **Fix with:** make-it-fast
- **Effort:** L



### CHB-2 · P2 · restore the DevTools frontend after the first lean package

- **Area:** Build
- **Where:** `config/patches/helium/helium-no-devtools-frontend.patch`, `config/profiles/helium-wayland-release.gn`, `docs/linux.md`
- **Problem:** The current Helium profile disables the DevTools frontend and drops its resources to reduce graph size and build time. Raw CDP remains, but users cannot open the built-in DevTools UI. The removal is deliberate for the first package and is not proven against future Helium frontend changes.
- **Recommendation:** When DevTools is required, remove `helium-no-devtools-frontend.patch` from `config/helium.patch-disposition.json`, restore the upstream `enable_devtools_frontend` default, rebuild, and qualify the packaged frontend under dwl. Measure the package and build-time delta before keeping it enabled by default.
- **Fix with:** none
- **Effort:** M

### CHB-3 · P2 · restore native notifications and external-item handling without D-Bus

- **Area:** Operability
- **Where:** `chrome/browser/notifications/notification_platform_bridge_no_dbus.cc`, `chrome/browser/platform_util_linux_no_dbus.cc`, `ui/base/clipboard/clipboard_util_linux.cc`
- **Problem:** With `use_dbus = false`, native Linux notifications return no bridge, XDG file-transfer clipboard paths return empty, and “show in folder”/external URL handling falls back to `xdg-open`/`xdg-email`, which the target Alpine root may not provide.
- **Recommendation:** Bundle a small non-D-Bus notification and URI-launch path, or document the host `xdg-utils` requirement, then qualify the packaged browser in dwl.
- **Fix with:** none
- **Effort:** M

### CHB-6 · P2 · verify VA-API decode on the Broadwell target

- **Area:** Tests
- **Where:** `config/profiles/helium-wayland-release.gn`, `docs/linux.md`, `~/usr/host/alpine/README.md`
- **Problem:** `use_vaapi=true` compiles Chromium's VA-API decoder, but the automated Sway fixture uses Mesa llvmpipe and no hardware decoder. Package acceptance therefore cannot prove that the Dell XPS 13 9343's Broadwell GPU decodes H.264 or VP8 through `libva-intel-driver`, or that `VaapiVideoDecoder` is selected under dwl.
- **Recommendation:** In the owner's dwl session, run `vainfo` and require `VAProfileH264Main`/`VAProfileH264High`, play a local H.264 or VP8 file in the packaged browser, and check `chrome://media-internals` for `VaapiVideoDecoder`. Force H.264 on VP9-only sites rather than treating a software VP9 fallback as failure. If initialization fails, capture the browser log and test `LIBVA_DRIVER_NAME=i965` before changing the build.
- **Fix with:** CHB-7
- **Effort:** S

### CHB-8 · P2 · complete the Helium behavior acceptance matrix

- **Area:** Tests
- **Where:** `scripts/helium_test_fast.py`, `scripts/test-functional.py`, `docs/helium-acceptance.md`
- **Problem:** Helium package acceptance checks only product identity, the Wayland window, page rendering, renderer sandboxing, and clean shutdown. Tabs and vertical tabs, settings, privacy defaults, bundled uBlock, extension loading, download/upload, profile restart, native keyboard and clipboard, fonts, media, TLS trust, and non-renderer sandbox observations are unexecuted. The Chromium functional harness covers several of these but has no Helium package path.
- **Recommendation:** Extend the Helium harness with the remaining rows from `docs/helium-acceptance.md`, reusing the Chromium fixture and CDP transport where it fits and the compositor input path for keyboard and clipboard. Keep the negative controls, reject certificate and sandbox bypasses, and bind each result to the archive digest. Add the rows to `scripts/qualify-package.py` only when they actually pass.
- **Fix with:** CHB-7
- **Effort:** L




### CHB-9 · P2 · forward-port Helium's native adblock-rust experiment

- **Area:** Build
- **Where:** `docs/helium-adblock-rust.md`, `scripts/prepare-helium.py`, `config/helium.inputs.lock`, `imputnet/helium` branch `adblock-rust` (`88f7dda1`)
- **Problem:** bctl disables Helium's bundled uBlock Origin because its per-profile filter compile cost about 4 s of startup and 850 MiB of VM memory, so packaged Helium blocks no ads. Helium's experimental native integration of the `adblock` crate is based on Chromium 155; our pin is 156. 13 of its 15 patches apply to our tree; three hunks need mechanical refreshes, and nothing has been compiled. Its crate vendoring downloads during setup, which our offline `prepare` phase forbids.
- **Recommendation:** Move the 41 crate downloads and `helium-filters` into `fetch` with lockfile checksums, refresh the three hunks, add the series behind `ENABLE_ADBLOCK` in `prepare-helium.py`, and prove it compiles incrementally on the existing `out/helium-wayland-release` before a full package build. Drop the `ublock_origin` component from the native build so only one engine runs; compare the current uBlock package with the native build, pref on and off (`helium.adblock.enabled`), rather than adding a build-time selector. Add the missing tests (frames and workers, redirects, scriptlets, cosmetic), and decide on WebTransport and CNAME uncloaking before it replaces uBlock. Status (2026-11-10): forward-ported and off by default (`8e21451`); a native `r7` candidate qualifies and blocks in bctl, measured in `docs/helium-adblock-rust.md`. Generic hiding now uses one native stylesheet and no isolated world, which cut the Speedometer cost from about 13 to about 6 percent (pending a quiet rerun). Open: the remaining 6 percent, most likely the per-request async engine hop; about 170 MiB of VM memory for the engine; `about:blank`/`data:` documents are unfiltered, hydrogen's `dev/helium-rootfs.sh` pins the r5 archive by digest, and no profile enables the flag.
- **Fix with:** none
- **Effort:** L


### CHB-10 · P2 · stop exact Alpine package pins from rotting against the live v3.24 repository

- **Area:** Build
- **Where:** `config/helium.packages.amd64.lock`, `config/helium.packages.arm64.lock`, `config/packages.amd64.lock`, `config/packages.arm64.lock`, `Dockerfile:239`, `scripts/lockcheck.py`
- **Problem:** The builder image is digest-pinned, but `apk add` installs exact `name=version` pins from the live `dl-cdn.alpinelinux.org/alpine/v3.24` repositories, which keep only the newest revision. Each Alpine point update breaks a fresh image build: `libopenmpt` on 2026-11-09 (`7bc3a0f`), then `go=1.26.10-r0` and `nodejs=24.22.1-r1` on 2026-11-15, when a fresh amd64 build failed in `image` with `breaks: world[go=1.26.10-r0]`. The arm64 and headless locks pin the same superseded `go` and `nodejs`.
- **Recommendation:** Make the package world reproducible instead of re-bumping pins after each break: mirror the exact resolved `.apk` files as GitHub release assets, as the Rust toolchain mirror does, or pin a dated repository snapshot, and fetch them in `fetch` with checksums. Accept when a fresh `image` build succeeds from the lock after Alpine supersedes a pinned revision and Gate A's `lockcheck.py packages` still passes.
- **Fix with:** none
- **Effort:** M




### CHB-5 · P3 · offer accessibility without requiring a session bus

- **Area:** Operability
- **Where:** `scripts/package-runtime.py` (`HELIUM_LAUNCHER`)
- **Problem:** Screen readers and accessibility tooling do not work in the default packaged browser.
- **Recommendation:** Add an opt-in launch path for accessibility on systems that provide a session bus, or build a non-D-Bus accessibility path. Keep the D-Bus-free default documented.
- **Fix with:** none
- **Effort:** M


### CHB-16 · P3 · replace the default HarfBuzz OpenType shaping path with HarfRust

- **Area:** Build
- **Where:** config/helium.inputs.lock; config/helium.packages.arm64.lock; config/helium.packages.amd64.lock; config/helium.system-library-preflight.schema.json; Chromium third_party/blink/renderer/platform/fonts/shaping/ and third_party/harfbuzz-ng/
- **Problem:** HarfRust 0.13.3 is already compiled and linked in the recorded Helium arm64 build, but default Blink shaping selects HarfBuzz's OpenType shaper and bctl does not enable the experimental HarfRustShaping feature. HarfBuzz therefore remains compiled and required. Enabling the existing HarfRust path needs rendering qualification; eliminating HarfBuzz additionally requires auditing remaining callers and shaping fallbacks.
- **Recommendation:** Make HarfRust the supported default for the bctl Helium build, audit all HarfBuzz callers and fallback shaping paths, and remove HarfBuzz compilation and dependencies once replacement coverage is proven. Qualify complex scripts, ligatures, fallback fonts, variable fonts and real headed/headless pages; do not equate enabling the experimental feature with eliminating HarfBuzz. Preserve sandbox and browser acceptance gates.
- **Fix with:** `reference-parity`
- **Effort:** L


### CHB-18 · P3 · qualify browser payload reductions against required appliance capabilities

- **Area:** Build
- **Where:** scripts/stage-runtime.py; scripts/package-runtime.py; Helium GN profiles; bctl BCTL-32 release census
- **Problem:** The appliance browser includes printing, Crashpad, SwiftShader/graphics libraries, broad locales and fonts. Runtime-disable flags do not prove their code or files are unused. Current size/load contributions and feature requirements are not fully qualified; Crashpad remains useful for the current real-site crash investigation.
- **Recommendation:** Use a fresh digest-bound image size/runtime-load census to select one cut at a time. Separate print UI from automation PDF output, retain diagnostics until a replacement is proven, establish graphics-library loading/web-feature coverage, and preserve multilingual shaping/fallback/screenshot behavior when considering font/locales. Keep required licenses and provenance. Do not remove components solely because a launcher flag disables behavior.
- **Fix with:** `blast-radius`
- **Effort:** L

