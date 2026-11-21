Prefix: CHB
Next: 23


### CHB-11 · P1 · bound back-to-back begin frames sent to the browser UI compositor in unthrottled headless

- **Area:** Performance
- **Where:** `components/viz/common/frame_sinks/begin_frame_source.cc` (`BackToBackBeginFrameSource::DidFinishFrame`, `OnTimerTick`), `cc/scheduler/scheduler.cc` (frames skipped with `kRecoverLatency`), `config/patches/helium/`, `config/helium.patch-disposition.json`; consumer evidence in laputa-systems/bctl `tests/bench/results/2026-11-15-ui-begin-frame-flood/` and the switch list in bctl `guest/appliance/src/browser.rs`
- **Problem:** bctl runs headless Helium with `--disable-frame-rate-limit`, which makes Viz use `BackToBackBeginFrameSource`. That source re-ticks as soon as every observer finishes a frame. During each navigation and click, the browser process's own `ui::Compositor` on Linux Aura/Ozone needs a main frame while an earlier frame awaits acknowledgement, so its cc scheduler drops every begin frame with `kRecoverLatency` and the next arrives at once. In five warm open-and-click cycles of the Speedometer news fixture, the browser main thread receives 10k–28k `viz.mojom.CompositorFrameSinkClient` messages and 10k–13k `DidNotProduceFrame` events, against about 200 messages for native macOS Chromium, which composites its UI through Cocoa. Renderer main-thread task time is at parity, yet guest news open and click are 39.4/22.0 ms against 22.4/12.6 ms for native Helium, on a two-vCPU VM where this spin competes with the renderer. Kiosk mode removes browser UI paints but not the flood, and no switch or feature bounds the source. That the flood causes the latency gap is unproven; it is consistent with warm news falling from 388.9 to 245.9 to 204.9 ms at one, two and four vCPUs.
- **Recommendation:** Add a Helium patch that stops the back-to-back source spinning on an observer that did not produce a frame: either re-arm that observer only after its pending frame is acknowledged or new damage arrives, or apply a small minimum interval (for example 1 ms) between back-to-back ticks. Keep screenshot and input latency unthrottled, since bctl adopted the unbounded source for those. Build an archive, then A/B it in bctl with `tests/bench/kernel_experiment.py` on warm news open/click and VM CPU, and confirm with a trace that the browser-main `CompositorFrameSinkClient` count drops to native scale.
- **Fix with:** make-it-fast
- **Effort:** L


### CHB-21 · P1 · Replace the Helium build with ungoogled Chromium for Wayland on arm64 and amd64

- **Area:** Build
- **Where:** config/inputs.lock (Chromium 153), config/helium.inputs.lock (Helium 0.20.3.1, Chromium 156), config/patches/helium/, scripts/linux_product.py PRODUCTS and PROFILES, scripts/prepare-helium.py, config/profiles/helium-wayland-release.gn
- **Problem:** Two browser lines track two Chromium versions (153 for ungoogled Chromium, 156 through Helium), and only the Helium line builds the linux/arm64 Wayland desktop that joshuarli/bctl runs. Most of Helium (256 of 346 patches) is branding, UI, settings, services and features that bctl disables or that local patches undo, and its rounded contents cost bctl a render pass per headed frame (bctl BCTL-48). The owner accepted retiring the Helium build on 2026-11-19 and made it the browser-build priority.
- **Recommendation:** Follow docs/rfc/CHB-21-chromium-replaces-helium.md: advance the chromium product to Chromium 156 or newer with matching ungoogled-chromium, add a chromium-wayland-release profile and the linux/arm64 builder from the Helium product, carry the adblock-rust series, the begin-frame fix and the needed musl patches, qualify the package in bctl, then remove the helium product and re-target its tickets.
- **Fix with:** none
- **Effort:** L
- **Design:** docs/rfc/CHB-21-chromium-replaces-helium.md

### CHB-8 · P2 · complete the Helium behavior acceptance matrix

- **Area:** Tests
- **Where:** `scripts/helium_test_fast.py`, `scripts/test-functional.py`, `docs/helium-acceptance.md`
- **Problem:** Helium package acceptance checks only product identity, the Wayland window, page rendering, renderer sandboxing, and clean shutdown. Tabs and vertical tabs, settings, privacy defaults, bundled uBlock, extension loading, download/upload, profile restart, native keyboard and clipboard, fonts, media, TLS trust, and non-renderer sandbox observations are unexecuted. The Chromium functional harness covers several of these but has no Helium package path.
- **Recommendation:** Extend the Helium harness with the remaining rows from `docs/helium-acceptance.md`, reusing the Chromium fixture and CDP transport where it fits and the compositor input path for keyboard and clipboard. Keep the negative controls, reject certificate and sandbox bypasses, and bind each result to the archive digest. Add the rows to `scripts/qualify-package.py` only when they actually pass.
- **Fix with:** none
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


### CHB-16 · P2 · make HarfRust Hydrogen's shaper, then remove HarfBuzz

- **Area:** Build
- **Where:** config/patches/ (chromium product), config/inputs.lock, config/packages.arm64.lock, config/packages.amd64.lock, config/system-library-preflight.schema.json; Chromium third_party/blink/renderer/platform/fonts/shaping/ and third_party/harfbuzz-ng/; consumer qualification in laputa-systems/bctl
- **Problem:** The owner decided on 2026-11-20 to adopt HarfRust. Hydrogen (Chromium 156) already compiles HarfRust and carries the `HarfRustShaping` feature (the string is in the packaged `chrome` of bctl's nanos-hydrogen arm64 image), but Blink's default shaping still selects HarfBuzz's OpenType shaper, and HarfBuzz stays compiled and required by its other callers.
- **Recommendation:** In three steps. First, qualify the existing feature without a rebuild: run bctl sessions with `--enable-features=HarfRustShaping` against the default, and compare complex scripts (Arabic, Devanagari, Thai, Hebrew, CJK), ligatures, emoji and fallback fonts, variable fonts and real headed and headless pages by screenshot and text metrics, plus shaping-heavy layout time. Second, once that matches, enable the feature by default with a chromium-product patch in the next Hydrogen rebuild. Third, audit the remaining HarfBuzz callers and fallback shaping paths and remove HarfBuzz compilation and dependencies only when replacement coverage is proven. Enabling the feature is not eliminating HarfBuzz. Preserve the sandbox and browser acceptance gates.
- **Fix with:** `reference-parity`
- **Effort:** L

### CHB-18 · P3 · qualify browser payload reductions against required appliance capabilities

- **Area:** Build
- **Where:** scripts/stage-runtime.py; scripts/package-runtime.py; Helium GN profiles; bctl BCTL-32 release census
- **Problem:** The appliance browser includes printing, Crashpad, SwiftShader/graphics libraries, broad locales and fonts. Runtime-disable flags do not prove their code or files are unused. Current size/load contributions and feature requirements are not fully qualified; Crashpad remains useful for the current real-site crash investigation.
- **Recommendation:** Use a fresh digest-bound image size/runtime-load census to select one cut at a time. Separate print UI from automation PDF output, retain diagnostics until a replacement is proven, establish graphics-library loading/web-feature coverage, and preserve multilingual shaping/fallback/screenshot behavior when considering font/locales. Keep required licenses and provenance. Do not remove components solely because a launcher flag disables behavior.
- **Fix with:** `blast-radius`
- **Effort:** L


### CHB-20 · P3 · native adblock refreshes its filter catalog from Helium's services at startup

- **Area:** Helium
- **Where:** components/helium_adblock/core/helium_adblock_service_refresh.cc (adblock-service-refresh.patch); components/helium_services/helium_services_helpers.cc GetUBlockAssetsURL
- **Problem:** With default prefs, a fresh profile of the macOS Helium candidate fetches https://services.helium.imput.net/ubo/assets.json soon after launch (seen in a net log). The filter refresh calls GetUBlockAssetsURL whenever kHeliumServicesEnabled and kHeliumUBlockAssetsEnabled are on. So the build has no Helium-infrastructure dependency but the running browser does, and the lists it blocks with can drift from the pinned helium-filters bundle. The Linux package carries the same patches; that it makes the same request there is unproven.
- **Recommendation:** Decide where refreshed lists come from: bundled-only, which disables the remote refresh source, or the upstream list URLs fetched directly. Then default the Helium services prefs accordingly in a local patch, and prove with a net log that a fresh profile makes no request to imput.net.
- **Fix with:** none
- **Effort:** S

### CHB-22 · P4 · Hydrogen's allocator is unguarded and alternatives are unmeasured; keep PartitionAlloc unless a workload shows a large win

- **Area:** Browser / Allocator
- **Where:** config/profiles/chromium-wayland-release.gn; scripts/verify-gn-args.py (LEAN_DESKTOP, helium_expected: no allocator keys); Gate D metadata gn-args.canonical.txt for chromium-wayland-release (use_partition_alloc, use_partition_alloc_as_malloc, use_allocator_shim, enable_backup_ref_ptr_support, use_raw_ptr_backup_ref_impl all true, is_musl true); config/patches/helium/helium-partitionalloc-musl-ifunc.patch
- **Problem:** Hydrogen builds against musl but does not use musl's malloc: the effective GN args of the first chromium-wayland-release build keep PartitionAlloc-Everywhere with the allocator shim and BackupRefPtr (MiraclePtr) use-after-free mitigation. Nothing asserts this, so a future profile or patch change could silently drop to musl's mallocng and lose BackupRefPtr. Separately, mimalloc 3.7.0 is a credible challenger for ordinary allocations, but replacing PartitionAlloc would lose or weaken BackupRefPtr's protection, and Blink's direct PartitionAlloc use, Oilpan and V8's heaps would be unaffected, so a win must be large and measured on real workloads to justify it.
- **Recommendation:** Now: add use_partition_alloc_as_malloc, use_allocator_shim and enable_backup_ref_ptr_support = true to the chromium-wayland-release contract in scripts/verify-gn-args.py so Gate D fails if they change. Later, only after BCTL-59 qualifies Hydrogen and only if bctl attributes browser memory or CPU to the general allocator: build three variants from one pinned revision (A production PartitionAlloc; B use_partition_alloc_as_malloc=false and use_allocator_shim=false, musl mallocng for ordinary allocations; C as B with mimalloc 3.7.0 linked or preloaded, confirming actual malloc/free dispatch in browser, zygote and renderer processes, in default and MI_SECURE=CHECKED), compare natively on Linux x86_64 and then in Reactor on bctl's workflow, headed and density benchmarks (workflow time, browser and renderer CPU, p95 action latency, host VM footprint with corrected accounting, retained memory after cycles, renderer churn and stability), and validate sandbox, CDP, adblock and site isolation. Change the default only for a substantial, repeatable per-workload win that justifies giving up BackupRefPtr.
- **Fix with:** none
- **Effort:** M
