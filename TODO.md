Prefix: CHB
Next: 35

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

### CHB-2 · P2 · restore the DevTools frontend after the first lean package

- **Area:** Build
- **Where:** `config/patches/helium/helium-no-devtools-frontend.patch`, `config/profiles/helium-wayland-release.gn`, `docs/linux.md`
- **Problem:** The current Helium profile disables the DevTools frontend and drops its resources to reduce graph size and build time. Raw CDP remains, but users cannot open the built-in DevTools UI. The removal is deliberate for the first package and is not proven against future Helium frontend changes.
- **Recommendation:** When DevTools is required, remove `helium-no-devtools-frontend.patch` from `config/helium.patch-disposition.json`, restore the upstream `enable_devtools_frontend` default, rebuild, and qualify the packaged frontend under dwl. Measure the package and build-time delta before keeping it enabled by default.
- **Fix with:** none
- **Effort:** M

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

### CHB-20 · P3 · native adblock refreshes its filter catalog from Helium's services at startup

- **Area:** Helium
- **Where:** components/helium_adblock/core/helium_adblock_service_refresh.cc (adblock-service-refresh.patch); components/helium_services/helium_services_helpers.cc GetUBlockAssetsURL
- **Problem:** With default prefs, a fresh profile of the macOS Helium candidate fetches https://services.helium.imput.net/ubo/assets.json soon after launch (seen in a net log). The filter refresh calls GetUBlockAssetsURL whenever kHeliumServicesEnabled and kHeliumUBlockAssetsEnabled are on. So the build has no Helium-infrastructure dependency but the running browser does, and the lists it blocks with can drift from the pinned helium-filters bundle. The Linux package carries the same patches; that it makes the same request there is unproven.
- **Recommendation:** Decide where refreshed lists come from: bundled-only, which disables the remote refresh source, or the upstream list URLs fetched directly. Then default the Helium services prefs accordingly in a local patch, and prove with a net log that a fresh profile makes no request to imput.net.
- **Fix with:** none
- **Effort:** S

### CHB-23 · P3 · Share the helpers the build scripts copy (was DSLP-6)

- **Area:** deslop · dedupe
- **Where:** scripts/*.py (digest in 8 files, read_json in 6, sha256 in 3, stream_command/run/atomic_json in script_support.py and macos_backend.py)
- **Problem:** The same file digest, JSON read and process-streaming helpers are written out in each entrypoint. The two stream_command copies already diverged and both had the silent-hang timeout defect (one fixed in this pass, one removed as dead). script_support.py ships in the builder image beside every entrypoint, so image scripts can share it; only the macOS and host-only scripts may import buildlib.
- **Recommendation:** Move digest, read_json and atomic JSON writes into script_support.py and make macos_backend use its own typed variants only where the error type differs. Keep the image scripts stdlib-only. Accept: one definition of each helper; make test and make test-ci pass.
- **Fix with:** deduplicate
- **Effort:** M

### CHB-24 · P3 · Stop macos_backend.main rebinding module globals (was DSLP-7)

- **Area:** deslop · types
- **Where:** scripts/macos_backend.py (main, DEFAULT_PROFILE, LOCK_RELATIVE, WorkLayout.__init__)
- **Problem:** main declares global DEFAULT_PROFILE, LOCK_RELATIVE and rebinds them from --profile before any WorkLayout is built; WorkLayout.output and the lock loader read the globals. Importing the module and calling a phase function behaves differently from running main, and the tests only pass because the default profile is the one they use.
- **Recommendation:** Carry the profile and lock path on WorkLayout (or a small frozen profile value) and drop the globals. Accept: no global statement in macos_backend.py; test_macos_backend covers both macos-release and macos-helium-release layouts.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

### CHB-25 · P3 · Kill the whole process group when stream_command times out (was DSLP-8)

- **Area:** deslop · lifecycle
- **Where:** scripts/macos_backend.py (stream_command)
- **Problem:** The timeout now fires on a silent command, but it terminates only the direct child. A phase such as pnpm run test:browser spawns descendants that keep the stdout pipe open, so the reader loop can still block until they exit. Not reproduced with a real tree; the design leaves it open. Moving the child to a new session would stop Ctrl-C reaching it, so the fix needs an explicit interrupt path.
- **Recommendation:** Start the child in its own process group, kill the group on timeout and on KeyboardInterrupt, and test with a shell that backgrounds a sleeper. Accept: the test fails before and passes after, in under 15s.
- **Fix with:** lifecycle-concurrency
- **Effort:** S

### CHB-26 · P3 · Replace dict[str, Any] build documents with typed models (was DSLP-9)

- **Area:** deslop · types
- **Where:** scripts/macos_backend.py (about 90 dict[str, Any] sites), scripts/verify_macos_ci.py, scripts/macos_helium.py, JsonObject in scripts/script_support.py
- **Problem:** Phase reports, locks and manifests are untyped JSON dicts, so ty at unsound-return-statement reports 17 sites where Any from json.loads is returned as str, list[str] or tuple[int, int, int]. The rule stays off for that reason (6 more hits are typeshed re.Match.group noise).
- **Recommendation:** Model the lock, doctor report, package report and release manifest as TypedDict or dataclasses validated at the read boundary, then adopt unsound-return-statement. Accept: the 17 sites fixed with a witness each; rule at error.
- **Fix with:** typing-audit
- **Effort:** L

### CHB-27 · P3 · Test the macOS backend entry point and its exit codes (was DSLP-10)

- **Area:** deslop · tests
- **Where:** scripts/macos_backend.py (parse_arguments, main), tests/test_macos_backend.py
- **Problem:** main maps MacOSFailure to exit 1, KeyboardInterrupt to 130 and unknown commands to a failure, rejects --headed-reference outside test-staged and any profile but the two macOS ones. Only ./chromium-build forwarding of one flag is tested now; no test calls main.
- **Recommendation:** Add main-level tests with a temporary work root: each rejected argument, exit codes 0, 1 and 130, and the work-lock release. Prove one injected fault. Accept: the Verification section lists the test for the macOS backend entry.
- **Fix with:** test-audit
- **Effort:** S

### CHB-28 · P3 · Measure and test the Linux image scripts on a Linux host (was DSLP-11)

- **Area:** deslop · tests
- **Where:** scripts/ gates A to F, prepare-source*.py, prepare-helium.py, stage-runtime.py, package-runtime.py, test-functional*.py, verify-*.py; tests/run_controller_tests.py linux and linux-builder groups
- **Problem:** About 20 scripts run only inside the builder image and sit at 0% to 25% in the macOS group coverage; the linux and linux-builder groups cannot run on Darwin, so this pass measured nothing for them and could not mutate them. Needs environment: Linux host, Docker and the builder image.
- **Recommendation:** On a Linux builder run make coverage for the linux group and make test-ci with coverage inside the image; add tests for the 0% gate scripts (verify-environment, run-probes, generate-gn) that need no browser. Accept: per-script coverage recorded; each public gate has a guarding test or a stated reason.
- **Fix with:** test-audit
- **Effort:** L

### CHB-29 · P3 · Add a mutation lane for the release-gating scripts (was DSLP-12)

- **Area:** deslop · ci
- **Where:** .github/workflows/, Makefile, tests/
- **Problem:** A bounded mutmut run on verify_macos_ci.py is reproducible only by hand (scratch copy with scripts renamed src and a pytest runner) because mutmut 3 needs pytest and a tracked source package; the repository uses unittest. CI workflow changes are out of scope for this pass. No lane or diff-scoped target exists.
- **Recommendation:** Add pytest as a dev dependency from the stuff mirror or a mutmut runner for unittest, qualify the recipe with negative controls in CI, then wire it for verify_macos_ci.py, sources_lock.py and release_assets.py. Accept: lane green with a negative control failing for the intended reason.
- **Fix with:** test-audit
- **Effort:** M

### CHB-30 · P3 · Drop the duplicate lint workflow and align the uv pin (was DSLP-13)

- **Area:** deslop · ci
- **Where:** .github/workflows/lint.yml, .github/workflows/ci.yml, pyproject.toml, uv.lock
- **Problem:** lint.yml and the lint job of ci.yml both run make lint on ubuntu-26.04-arm (fleet workflow-redundancy), with uv 0.13.7 and 0.13.8. The fleet python-standard rule wants uv 0.13.8, ruff 0.17.5 and ty 0.0.104; the repository pins 0.13.7, 0.17.4 and 0.0.101 and has no .python-version. test_workflow.test_lint_workflow_runs_make_lint pins lint.yml. The fleet owned-tool-support finding was not examined.
- **Recommendation:** Run repomod fix for the python-standard pins, delete lint.yml (update the test to read ci.yml), keep one uv version. CI changes are outside this pass. Accept: repomod lint shows no workflow-redundancy or python-standard finding for this repository.
- **Fix with:** none
- **Effort:** S

### CHB-22 · P4 · Hydrogen's allocator is unguarded and alternatives are unmeasured; keep PartitionAlloc unless a workload shows a large win

- **Area:** Browser / Allocator
- **Where:** config/profiles/chromium-wayland-release.gn; scripts/verify-gn-args.py (LEAN_DESKTOP, helium_expected: no allocator keys); Gate D metadata gn-args.canonical.txt for chromium-wayland-release (use_partition_alloc, use_partition_alloc_as_malloc, use_allocator_shim, enable_backup_ref_ptr_support, use_raw_ptr_backup_ref_impl all true, is_musl true); config/patches/helium/helium-partitionalloc-musl-ifunc.patch
- **Problem:** Hydrogen builds against musl but does not use musl's malloc: the effective GN args of the first chromium-wayland-release build keep PartitionAlloc-Everywhere with the allocator shim and BackupRefPtr (MiraclePtr) use-after-free mitigation. Nothing asserts this, so a future profile or patch change could silently drop to musl's mallocng and lose BackupRefPtr. Separately, mimalloc 3.7.0 is a credible challenger for ordinary allocations, but replacing PartitionAlloc would lose or weaken BackupRefPtr's protection, and Blink's direct PartitionAlloc use, Oilpan and V8's heaps would be unaffected, so a win must be large and measured on real workloads to justify it.
- **Recommendation:** Now: add use_partition_alloc_as_malloc, use_allocator_shim and enable_backup_ref_ptr_support = true to the chromium-wayland-release contract in scripts/verify-gn-args.py so Gate D fails if they change. Later, only after BCTL-59 qualifies Hydrogen and only if bctl attributes browser memory or CPU to the general allocator: build three variants from one pinned revision (A production PartitionAlloc; B use_partition_alloc_as_malloc=false and use_allocator_shim=false, musl mallocng for ordinary allocations; C as B with mimalloc 3.7.0 linked or preloaded, confirming actual malloc/free dispatch in browser, zygote and renderer processes, in default and MI_SECURE=CHECKED), compare natively on Linux x86_64 and then in Reactor on bctl's workflow, headed and density benchmarks (workflow time, browser and renderer CPU, p95 action latency, host VM footprint with corrected accounting, retained memory after cycles, renderer churn and stability), and validate sandbox, CDP, adblock and site isolation. Change the default only for a substantial, repeatable per-workload win that justifies giving up BackupRefPtr.
- **Fix with:** none
- **Effort:** M

### CHB-31 · P4 · Share the Helium and Chromium adblock setup scripts or retire one (was DSLP-14)

- **Area:** deslop · dedupe
- **Where:** scripts/setup-chromium-adblock.py, scripts/setup-helium-adblock.py, tests/test_setup_adblock.py
- **Problem:** Two 237-line scripts differ by the FILTERS directory constant. The shared tests now cover both, so a fix to one is caught only if made in both. CHB-21 retires the Helium product.
- **Recommendation:** Delete setup-helium-adblock.py with the Helium product, or turn the pair into one module parametrised by the filters directory. Accept: one script, both callers (prepare-source.py, prepare-helium.py, macos_helium.py) pass tests.
- **Fix with:** deduplicate
- **Effort:** S

### CHB-32 · P4 · Lint the shell entrypoints (was DSLP-15)

- **Area:** deslop · tooling
- **Where:** Makefile (lint), chromium-build, scripts/*.sh, scripts/rust-host-linker
- **Problem:** make lint runs ruff and ty on Python only. shellcheck over chromium-build and the shell scripts reports 5 warnings (SC1007, two SC2054 on docker --tmpfs words, SC2034 on an unused loop counter, SC2046 in build-rust-toolchain.sh); all benign today, but nothing stops a real one.
- **Recommendation:** Add shellcheck to make lint with the five warnings fixed or annotated, if shellcheck is available in CI. Accept: make lint fails on an injected SC2086.
- **Fix with:** none
- **Effort:** S

### CHB-33 · P4 · Read the Rust components list from the backend in the policy check (was DSLP-16)

- **Area:** deslop · single-source
- **Where:** scripts/verify_macos_ci.py (verify_preflight), scripts/macos_backend.py
- **Problem:** The nightly now comes from macos_backend.RUST_TOOLCHAIN, but the component list [rust-src, llvm-tools-preview] is still spelled in both files and in the input locks.
- **Recommendation:** Export one constant from macos_backend and use it in the policy check and test fixture. Accept: one definition outside the locks.
- **Fix with:** single-source-of-truth
- **Effort:** S

### CHB-34 · P4 · Needs owner · reconcile the no-linters rule with make lint (was DSLP-17)

- **Area:** Needs owner · AGENTS.md rules
- **Where:** AGENTS.md Working rules and Commands
- **Problem:** The working rules say not to run formatters, linters, hooks or CI, while Commands documents make check-py and make lint as the Python static checks and CI runs them. ruff check and ty check only read files; only formatters rewrite them.
- **Recommendation:** Decide whether the rule means formatters and hooks only, and reword it; this pass ran ruff check and ty check and no formatter.
- **Fix with:** none
- **Effort:** S

