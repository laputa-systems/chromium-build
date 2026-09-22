# Native macOS arm64 ungoogled-chromium build


## 1. Outcome and scope

Add a native macOS backend that builds the latest released ungoogled-chromium into a usable, relocatable `Chromium.app` on a macOS 26-or-newer Apple Silicon host. The application must remain close to vanilla ungoogled-chromium, support normal desktop use and integrated headless operation, load extensions, and work with the actual `/Volumes/dev/d/shadowdriver` library.

All work in this plan is rooted under `/Volumes/dev`. Use absolute paths beneath `/Volumes/dev` for the repository, work root, downloads, tool installations, checkouts, caches, temporary files, logs, profiles, test outputs, and artifacts. The path spellings `~` and `/tmp` are forbidden in commands, configuration, metadata, and documentation; do not rely on shell expansion or the host temporary directory. Read-only host prerequisites such as Xcode may remain outside this tree, but every project-controlled write must stay under `/Volumes/dev`.

The macOS build must not invoke Docker, a Linux VM, Rosetta, remote compilation, or a prebuilt browser. Build Chromium itself from pinned sources. This milestone supports native arm64 only; normalize `aarch64` to Chromium's `arm64` spelling. Cross-compilation, universal binaries, CI infrastructure, automatic updates, publishing, Developer ID enrollment, and notarization are outside the required scope.

Preserve the existing Linux Docker backend, its default command behavior, architecture support, source lock, patches, and meaningful tests. Do not upgrade Linux's Chromium version incidentally. Add a small platform boundary rather than generalizing every Linux-specific gate into a framework. Prefer existing stdlib-only Python utilities and a thin shell entry point. Do not introduce a second build system or an orchestration framework.

The deliverable is the complete application bundle, not `headless_shell`, a copied Mach-O executable, a wrapper around an installed browser, or an application whose libraries remain in the build tree. Keep upstream Chromium/ungoogled identity and behavior. Do not create a new browser brand.

For the native macOS release profile, package US English only. Use Chromium's
normal `en.lproj` output for `en-US`, omit non-English and pseudolocale bundle
resources, and disable redundant gender-suffixed English pak variants. Keep the
broader translation catalog only where shared GN inputs require it; do not
remove unrelated source translations or break upstream locale selection logic.

## 2. References and research baseline

Treat `/Volumes/dev/d/helium` and `/Volumes/dev/d/shadowdriver` as read-only references. Record their actual revisions and whether they are dirty before isolating HOME. Do not reset, clean, update, install into, or otherwise mutate them. Determine whether `/Volumes/dev/d/helium` contains the core Helium repository, its macOS packaging repository, or both. The core repository alone is not the complete macOS builder. Fetch any missing reference repositories at explicit revisions under this build's work directory.

Read these implementation areas before selecting reusable machinery:

- This repository: `chromium-build`, `Makefile`, `config/inputs.lock`, patch inventory/disposition, `scripts/script_support.py`, fetch/preparation/configuration/build/staging/test drivers, and their tests. Historical plans are context, not authority over current code.
- Helium core: `utils/clone.py`, `utils/install_cipd_deps.py`, downloader/patch/pruning utilities, and their upstream ancestry and licenses.
- `imputnet/helium-macos`: `devutils/shared.sh`, `devutils/build_tools.sh`, `env.sh`, retrieval, GN configuration, signing, and development documentation.
- `ungoogled-software/ungoogled-chromium-macos`: core submodule revision, retrieval/cache implementation, `flags.macos.gn`, platform patch series, signing, and entitlements.
- Shadowdriver: `src/chromium.rs`, `src/lib.rs`, `src/ublock.rs`, `src/process.rs`, public crawler/render APIs, and browser-test helpers.

Research snapshot, September 21, 2026: this builder currently pins Chromium `150.0.7871.114` and a Linux-specific source archive. Its entry point unconditionally checks Docker and accepts only `headless-debug`. Core ungoogled-chromium's latest published non-prerelease was `153.0.8010.52-1`. The macOS packaging repository's core submodule was `e71b91c6e336d0f25cfc6b9ef09298a9d2506e24`, whose Chromium version is `152.0.7977.82`. Therefore, blindly combining current branches does not produce a version-matched build. Recheck these facts at implementation time.

Helium's useful patterns are source/DEPS-derived tool versions, targeted CIPD installation, separate preparation/configuration/build phases, and GN's fail-on-unused-arguments check. Its wrappers also contain destructive cleanup, global dependency assumptions, product transformations, and remote-service integration. Reuse selectively, not by invoking its entire build script.

Preserve licenses and attribution for copied or modified code. Helium-specific code is not automatically covered by ungoogled-chromium's BSD license. Prefer an appropriate original upstream implementation when available; record provenance for actual borrowed files and patches.

## 3. Platform interface and directory ownership

Extend the existing executable with `--platform linux|macos`, leaving omitted platform selection equivalent to today's Linux behavior. Select `macos-release` as the default profile only when macOS is explicitly selected; never apply `headless-debug` defaults to the app build. Reuse existing commands where their meaning fits. Provide a small, coherent interface for preflight, explicit lock update, fetch, prepare, configure, build, stage, and staged acceptance. Unsupported Linux gates on macOS must fail clearly before trying Docker.

All controllable generated state must live beneath one canonical absolute work root, defaulting to a gitignored directory such as `<repo>/.work/macos-arm64`. Honor the existing `CHROMIUM_WORK_ROOT` environment variable as the work-root override; do not introduce a competing root setting. Put downloads, tool installations, dependency checkouts, source, `src/out`, caches, virtual environments, temporary files, logs, metadata, stage/dist, test fixtures, browser profiles, and integration-test build outputs beneath that root. Version-controlled implementation/configuration files remain in the repository.

Use explicit ownership markers and a per-work-root lock. Reject dangerous roots and source paths unsupported by Chromium, including spaces, before expensive work. Distinguish permitted read-only references to host tools/Xcode from generated-output paths. Validate destructive operations after canonicalization; never follow a symlink into an unrelated directory during cleanup. Do not rely on a lexical string prefix as a containment check.

Repeated commands must be resumable. An unchanged prepare operation must not reapply patches or erase compiled output. An unchanged build must use incremental compilation. When source, patches, tools, or effective configuration change, invalidate the correct downstream stages and explain why. Keep candidate and accepted artifacts distinct. Preserve the previous accepted application until `test-staged` passes all required checks for the new candidate; then atomically update the last-known-good pointer. Do not silently overwrite user edits in an owned source tree; detect them and require an explicit reset operation limited to that tree.

Use atomic metadata publication, verified downloads with temporary filenames, and bounded retries. Never write a success stamp after partial failure. Keep useful failure logs and resumable build output. Implement only the state tracking needed to make these properties reliable, not a generic workflow database.

## 4. Host prerequisites and environment hygiene

Preflight the real host: macOS 26 or newer, native arm64 execution, a usable full Xcode installation and SDK including the Metal Toolchain, required Apple C tools, writable appropriate filesystem, free disk space, and sufficient build resources. Require a native LLVM/Clang distribution at least 23.1.0, preferably 23.1.1; prefer the selected Chromium revision's matching LLVM, then an explicit or already-installed Homebrew LLVM for the local builder without installing or unlinking packages. Record the Xcode Apple clang identity separately because Apple clang's version scheme is not the LLVM distribution version. Provision and use exactly Rust `nightly-2026-09-15` in the work root. Detect translated execution. Report missing prerequisites before downloading the large source tree where possible.

The workflow-dispatch-only CI smoke job runs on `macos-26`, performs the same
host-only preflight, disables compiler caching, and compiles a tiny native arm64
C++ program. It may provision ephemeral LLVM/Rust prerequisites on that
disposable runner; it must not add `actions/cache` or any compiler-cache service.
Dispatch and watch it with the authenticated `gh` CLI after the workflow commit,
repeating only to diagnose and correct failures until the run succeeds.

The installed OS, Xcode, SDK, and explicitly documented bootstrap executables are read-only host prerequisites. Select Xcode for this process using `DEVELOPER_DIR`; never run a global `xcode-select --switch`. Derive compatible SDK/tool requirements from the selected Chromium revision. Host OS version, SDK version, compiler version, and minimum deployment target are separate concepts. Do not set the deployment target or `LSMinimumSystemVersion` to 27 merely because the build host runs macOS 27. Prefer upstream defaults unless a concrete compatibility requirement justifies an override.

Do not run `sudo`, install/unlink Homebrew packages, use global/user-site pip installs, edit shell startup files, accept licenses, install Xcode components, change firewall/security policy, create launch agents, or install into `/Applications`. Missing Xcode/Metal components are explicit host prerequisites, not permission to modify the host automatically. Bootstrap other dependencies into the work root using pinned, verified distributions or local builds. Project-authored Python should remain stdlib-only; genuine upstream Python dependencies belong in a local, pinned environment.

Construct the child environment before the first bootstrap/download subprocess. Redirect HOME, temporary directories, XDG directories, Python/pip/vpython caches and environments, Git cache, CIPD cache, compiler module caches, optional compiler cache, and any used Node/Go/Cargo/Rust/uv caches into the work root. Audit the actual variables and behavior of the pinned tools rather than assuming a list of environment variables guarantees containment. Set `TMPDIR` before starting Rust integration tests. Disable depot_tools auto-update and avoid user-site Python imports.

Use a controlled PATH containing selected host prerequisites and workspace tools. Do not accidentally discover Homebrew LLVM, global Node, a different Rust compiler, compiler wrappers, or remote-execution clients. Clear inherited compilation overrides and remote Siso/RBE/sccache endpoints unless a documented local setting deliberately permits them. Do not copy credentials into the isolated HOME. Redirect newly downloaded integration-test Rust tooling too; do not break toolchain resolution by pointing an existing rustup shim at an empty home and calling that isolation.

Document the boundary honestly: this is a directory-contained native build, not a fully hermetic operating system. OS-owned logging and platform services can have side effects outside the work root. Identify unavoidable exceptions; eliminate avoidable tool-owned writes. Do not promise bit-identical rebuilds across different Xcode/SDK/host installations.

## 5. Source locking, fetch, and offline boundaries

Give macOS its own explicit lock data without destabilizing the Linux lock. An explicit update operation resolves the newest non-prerelease core ungoogled release, reads its `chromium_version.txt` and revision, resolves immutable commits, and chooses/audits the macOS adaptation. Ordinary fetch/build commands consume that lock and never resolve floating `main`, `master`, or `latest`.

Record Chromium version and commit; ungoogled release and commit; macOS packaging reference; selected patch identities/order; depot_tools revision; relevant DEPS identities; toolchain versions and immutable artifact identities; archive hashes/sizes; GN/Siso/CIPD packages; PGO inputs; and test-extension inputs. Resolve mutable CIPD references to immutable instances where applicable. Keep machine-specific paths in local environment metadata, not portable lock entries. Do not invent checksums or equate a release's `target_commitish` field with a verified peeled tag.

The selected core ungoogled release determines the Chromium source version. Verify `chrome/VERSION`, DEPS, patches, generated metadata, and profile inputs agree. A platform repository that lags core is a reference requiring a reviewed port, not a reason to quietly build an older version. Port the necessary small platform delta and test it. Report an actual unresolved incompatibility explicitly rather than substituting Helium or mislabeling an older build as latest.

Fetch complete macOS-capable sources. Do not reuse this repository's `*-linux.tar.xz` archive. Prefer a pinned Chromium checkout and its native macOS/arm64 DEPS closure using supported upstream tooling. In Helium's reviewed `clone.py`, `-p mac-arm` selects the PGO profile; its embedded gclient template is tarball-oriented and specifies Unix/x64. That argument alone is not proof of a correct native macOS checkout. Inspect and implement the proper checkout conditions for the pinned revision.

Separate network-bearing source/tool acquisition from local preparation and compilation. Fetch all required tool packages, profiles, hook inputs, and integration-test dependencies beforehand. Generate required Chromium revision metadata using supported upstream procedures; preserve sufficient history where those procedures require it.

Preparation, configuration, compilation, staging, and deterministic acceptance must not silently download dependencies. The current `require_network()` helper only checks an environment value; Docker supplies the actual Linux network isolation. `NETWORK_MODE=none` alone is not isolation on macOS. Verify an offline build path and document whether native enforcement or observation supports the claim. Do not install a system-wide firewall or turn this project into a sandbox implementation. Test fixtures may use loopback without depending on external websites.

## 6. Toolchain and source preparation

Prefer Chromium's revision-matched Clang/LLVM and Rust toolchains, including compatible bindgen/libclang support, rather than this project's musl LLVM or unrelated system packages. Reuse source-controlled update scripts where appropriate, locking and validating what they retrieve. Do not build a new LLVM distribution as part of this assignment.

Adapt the useful Helium CIPD-selection pattern: evaluate the selected source's DEPS using its supported machinery and install only the required native packages. The reviewed helper selects GN, Siso, native TypeScript tooling, and Dawn's Go toolchain, including nested Dawn DEPS. Paths and package conditions must be validated against the selected Chromium revision. Do not assume old hardcoded paths remain correct. Do not link Dawn's Go executable to an arbitrary host `go`.

Prefer the supported upstream build runner, using Siso in genuinely local-only mode when appropriate for this source. Do not inherit Helium's remote backend configuration or require Google infrastructure credentials. A Ninja path is acceptable when supported and verified for the selected revision; do not hardcode the Linux builder's old Ninja just for superficial reuse. Fetch only necessary packages, not every developer utility from the donor scripts.

Prepare from known clean sources. Apply ungoogled binary pruning, the complete selected core patch series, the audited applicable macOS patch series, and ungoogled domain substitutions in the appropriate order. Arrange tool restoration/acquisition so pruning does not delete the required build tools or trigger hidden downloads later. Record any local compatibility patches separately with a reason and regression evidence.

Audit macOS patches individually: several upstream platform patches accommodate its alternate LLVM/Rust packaging. They are not automatically necessary with Chromium's matching toolchain. Do not import version-check bypasses, blanket warning suppression, or Rust capability lies to conceal a tool mismatch. Explain important inclusions and omissions, and fail on unapplied/rejected patches. Do not use automatic fuzzy conflict resolution or partial application as success.

Exclude Helium's complete product patch/resource layer: branding, name/version substitutions, product translations, icons, onboarding, UI changes, service endpoints, search behavior, built-in extension injection, updater/Sparkle integration, special provisioning, and branded entitlements. Keep ordinary upstream Cocoa/AppKit integration. “No Helium-specific macOS stuff” does not mean stripping the native Chromium UI.

The Mac patch composition must exclude the existing Linux/musl/headless patch set, portablelinux adaptation, and Copium unless a specific independently demonstrated requirement warrants a narrowly reviewed exception. In particular, do not reuse patches that strip the desktop UI, DevTools frontend, printing, or native graphics stack.

## 7. Release configuration and build

Build the normal `chrome` target into a non-component release app. ChromeDriver is not required for Shadowdriver's direct CDP integration; do not make it a mandatory extra target.

Construct effective GN arguments from the selected ungoogled core and audited macOS settings with explicit, reviewable overrides. Preserve upstream product behavior and media policy. Validate all arguments with `gn gen --fail-on-unused-args` and inspect effective values. Never use string substitution to turn a release configuration into a development configuration.

Required properties include native macOS/arm64, Clang, `is_debug=false`, `is_component_build=false`, unbranded Chromium identity, full extension capability, desktop UI, DevTools frontend and protocol, and working native graphics/sandbox support. Use installed Xcode through the supported system-Xcode configuration. Do not confuse `is_official_build` with Google branding, or FFmpeg codec branding with browser branding. Do not import Helium-only GN declarations.

The native lightweight profile must set the packaged locale list to `en-US`,
retain Chromium's macOS `en.lproj` naming, remove explicit non-English Grit
outputs, and set `translate_genders=false`. Validate the result by inspecting
the generated GN action list and the staged bundle's locale directories, then
run the real browser acceptance harness against that bundle.

Prefer an optimized release configuration consistent with vanilla ungoogled macOS: ThinLTO and revision-matched upstream PGO where supported. Fetch and lock the proper arm64 Chrome and V8 profile inputs that the selected configuration needs. Never reuse profiles from a different Chromium release or silently disable PGO after a download failure. If the selected Xcode SDK contains TAPI inputs that the bundled lld cannot parse, record the compatibility decision, use the Apple linker, and disable ThinLTO for that explicitly identified bring-up profile. A temporary reduced-cost bring-up configuration must be explicitly identified and must not be reported as the completed release artifact.

Allow a jobs/resource override and choose a conservative local default based on actual host resources. Retain compiler diagnostics and logs. Compiler caching is optional, but the backend must make an existing local `ccache` or `sccache` work without manual PATH or environment setup: prefer `ccache`, select only an explicitly discovered local executable, write its cache and temporary state under the work root, set content-sensitive compiler checking, and record the selection in the phase reports and generated GN arguments. `MACOS_COMPILER_CACHE=off|ccache|sccache|auto` controls the choice; `auto` disables caching with an explicit report when neither wrapper is already available. Do not install or unlink Homebrew packages, copy `time_macros` sloppiness, or inherit ambient remote-cache configuration. Build correctness takes precedence over cache hit rate.

## 8. Bundle staging, signing, and relocation

Stage the entire `Chromium.app` into an owned candidate destination, preserving executable permissions, frameworks, helper bundles, resources, and internal symlinks. Publish its candidate metadata atomically after bundle/signature verification; promote it to the accepted location only after staged acceptance succeeds. Expose both app and executable paths in machine-readable metadata; the executable must be the actual `Contents/MacOS/Chromium` file.

Inspect every runtime Mach-O and bundled library, not just the launcher. Require native arm64 support with no x86-only runtime dependency. Resolve loader paths/rpaths and verify bundle dependencies are either included in the bundle or legitimate Apple system dependencies. Reject dangling or escaping runtime symlinks and dependencies on Homebrew, user directories, the source tree, or build output. Build-time Xcode links are not permission for runtime links back to Xcode.

Use ad-hoc signing for local use by default. Reuse the applicable Chromium/ungoogled signing structure and entitlements, signing nested code before enclosing bundles. Determine the actual helper/framework layout from the built artifact and upstream metadata; do not silently skip missing expected components or assume a donor's historical helper list is complete.

Retain necessary JIT/renderer/GPU entitlements and validate real execution. Do not import Helium provisioning profiles, private certificates, restricted branded entitlements, or its update machinery. Do not disable Chromium's sandbox, SIP, Gatekeeper, or library/security checks simply to make the app launch. `codesign --deep` is acceptable for verification, not as a substitute for correct inside-out signing.

Verify signatures and entitlements after the final mutation. Ad-hoc signing is not notarization; a Gatekeeper distribution assessment is a separate matter. Do not require an Apple account, modify keychains, submit the app, or claim notarized distribution support. Do not broadly strip quarantine/xattrs from unrelated files.

The staged `.app` is the required artifact. A symlink-preserving archive and checksum are useful; a DMG is optional and should not introduce unnecessary dependencies. Copy or extract the final artifact to another location under the work root and run it there with no DYLD search-path tricks and without relying on the source/output tree. Verify both programmatic executable launch and normal GUI operation. Do not install anything into `/Applications`.

## 9. Browser and extension acceptance

Use per-run disposable profiles and temporary directories entirely under the work root. Enable debugging only for these test launches, bind the endpoint to loopback, and do not reuse a user's existing browser process. Preserve normal app defaults. Use supported test-only keychain/dialog avoidance where necessary; never weaken the shipped application's settings to make tests unattended.

Test both the normal GUI browser and integrated headless mode. Verify JavaScript execution, navigation/redirects, resource fetching, screenshots, native rendering, and the presence of functional extensions and DevTools. A process that merely stays alive or prints a version is not sufficient. Verify ordinary browser launch without `--no-sandbox`; do not make that switch a product requirement.

Extension acceptance has three independent parts: a deterministic local MV3 extension exercising a content script and service worker; a minimal MV2 extension exercising a background context; and genuine vanilla uBlock Origin through Shadowdriver's supported path. Preserve `chrome://extensions` and unpacked loading through the actual launcher. Exercise installation/loading in headful and headless modes, and a restart where persistent installation applies. Do not claim that vanilla ungoogled gains seamless Chrome Web Store installation.

The reviewed core release includes `core/ungoogled-chromium/extensions-manifestv2.patch`; retain the corresponding protection in the chosen release and verify it behaviorally. Shadowdriver currently pins uBlock Origin `1.72.0`, archive `uBlock0_1.72.0.chromium.zip`, SHA-256 `c2900bbe9a783e645389cdeb19be632a033ad11d8bee7a94b0d0c316a20a2b05`. Recheck the local library's actual pin. Fetch/verify the extension in the acquisition stage and populate only the isolated test cache. Do not substitute uBlock Origin Lite or Helium's modified/built-in extension.

Prove expected extension IDs, manifests/versions, and observable behavior. For uBlock, configure a deterministic local blocking rule and verify the blocked request using a local fixture and server-side evidence. Include an unblocked control so a failed server, browser network policy, or general network outage cannot masquerade as successful ad blocking. Do not depend on a current public advertisement or remotely updated filter list. Account for sleeping MV3 workers rather than equating permanent target presence with correctness.

## 10. Actual Shadowdriver integration

Create a small builder-owned acceptance harness using the actual `/Volumes/dev/d/shadowdriver` public API, without changing that reference checkout. Keep harness build directories and dependencies under the work root. A minimal external Rust crate/path dependency is appropriate; preserve Shadowdriver's no-Tokio architecture. Do not replace integration with Selenium, Playwright, ChromeDriver, or only a handwritten WebSocket test.

Read the current API and configure the exact staged executable explicitly. The reviewed library supports `SHADOWDRIVER_CHROMIUM_BINARY` and `ChromiumStartupOptions.binary`. However, its Rust test helper subsequently assigns `DEFAULT_CHROMIUM_BINARY`, which on macOS is `/Applications/Chromium.app/Contents/MacOS/Chromium`. Merely setting the environment variable does not make every existing test use this build. Never overwrite or symlink the system application to work around this.

Use explicit binary fields in startup and session/render options, and assert the resolved launch path and process identity. Configure fresh managed profiles; disable accidental attachment/fallback to an unrelated installed browser. Keep `keep_browser_extensions=false` and load the requested vanilla extensions explicitly. Do not rely on a browser-bundled uBlock.

Run real public-API navigation, evaluation, response-body/resource collection, screenshot capture, target/session lifecycle, extension verification, and cleanup. Verify browser/version/protocol provenance comes from this process. Exercise multiple concurrent distinct local pages, including a repeatable 32-page workload with bounded startup, and check results do not cross targets. This is a correctness acceptance run, not a performance claim or a request to redesign Shadowdriver.

Reuse suitable existing test fixtures and tests only after verifying that they actually select the staged binary. Ordinary `cargo test` skips many live-browser tests. The Rust helper also derives its cache from `std::env::temp_dir()` and overwrites `SHADOWDRIVER_CACHE_DIR`; set `TMPDIR` appropriately before launching it. Isolate Cargo/uv/Python outputs for any additional binding smoke test. Never run mutating donor lint/release commands against the reference checkout.

On success, failure, timeout, and interruption, terminate only owned browser processes, close sockets, and clean disposable profiles according to the harness's retention policy. Do not use `killall Chromium` or broad profile cleanup. A genuine upstream Shadowdriver incompatibility must be diagnosed and reported separately, not hidden by silently changing the library or weakening acceptance.

## 11. Tests, evidence, and completion

Add fast automated tests for platform dispatch; unchanged Linux defaults; native-path exclusion of Docker; work-root/path guards; environment construction; corrupt/partial downloads; version mismatches; patch failure; stale stage fingerprints; interrupted publication; repeated preparation/build; and cleanup ownership. Mock expensive external tools for these unit tests, but do not mistake mocks for real native acceptance.

Complete and record these gates:

| Gate | Required evidence |
| --- | --- |
| Existing functionality | Applicable existing builder tests pass; Linux command behavior and locks remain intact. |
| Source/tool acquisition | Exact matching source identity, verified pinned inputs, complete native dependency closure. |
| Configuration/build | Successful real macOS GN generation and native release `chrome` build. |
| Incrementality/offline use | Unchanged repeat build avoids a clean rebuild; local phases succeed without fetching missing dependencies. |
| Packaging | Complete signed bundle, runtime architecture/dependency inspection, and relocated artifact launch. |
| Browser/extensions | GUI and headless behavior, MV3, MV2, and genuine uBlock functional checks. |
| Consumer | Actual Shadowdriver API succeeds against the staged executable with recorded identity. |
| Hygiene | Disposable state remains contained; no unintended host/reference mutation or surviving owned processes. |

The CI smoke workflow is an additional fast gate: it must remain
`workflow_dispatch`-only, use `runs-on: macos-26`, write project state under
`/Volumes/dev`, pass macOS/Apple-toolchain/LLVM/Rust preflight, and produce a
successful tiny arm64 compile with caching explicitly disabled.

Produce machine-readable build and acceptance metadata, plus a readable summary. Include repository/source revisions, dirty-state indicators, source lock and patch hashes, host OS/Xcode/SDK identities, compiler/tool identities, effective GN arguments, profile identities, commands/log locations, signing details, artifact/bundle hashes, observed browser/CDP identity, extension identities, and individual test outcomes. Keep secrets and unnecessary environment contents out of reports.

Implement and document this explicit sequence from the repository root. Ordinary local commands do not refresh the lock or fetch implicitly:

```sh
export CHROMIUM_WORK_ROOT=/Volumes/dev/d/chromium-build/.work/macos-arm64
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release doctor
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release update-lock --latest
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release fetch
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release prepare
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release configure
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release cache-stats
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release build
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release stage-runtime
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release test-staged
```

Also support `test-staged --app /absolute/path/Chromium.app` to verify a relocated or supplied application without installing it. This must run the real Shadowdriver test, not just the packaging audit. Report the candidate/accepted paths explicitly. Document source updates, resumability, disk/cache cleanup, host prerequisites, exclusions, signing limitations, and how to diagnose missing tools or incompatible upstream patches. Keep documentation proportional to the actual interface.

Perform the actual build and acceptance on the available macOS host rather than stopping after scripts exist. If a concrete environment/resource/upstream blocker prevents a gate, preserve completed work, provide the failing command and diagnostic evidence, and mark that gate failed or not run. Never count skipped browser tests, a different installed browser, an unsigned/unlaunchable bundle, or a development build as the completed release.

Finish with changed files, exact selected versions, runnable commands, artifact paths, passed/failed/not-run gates, and remaining concrete issues. Do not end by merely proposing that someone else implement or test the work.

## Primary-source reference locations

These are starting points; record the immutable revisions actually used.

- https://github.com/laputa-systems/chromium-build
- https://github.com/ungoogled-software/ungoogled-chromium/releases/latest
- https://github.com/ungoogled-software/ungoogled-chromium-macos
- https://github.com/imputnet/helium
- https://github.com/imputnet/helium-macos
- https://github.com/joshuarli/shadowdriver
- https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md
- https://chromium.googlesource.com/chromium/src/+/main/docs/mac_arm64.md
- https://chromium.googlesource.com/chromium/src/+/main/build/config/mac/mac_sdk.gni
- https://developer.chrome.com/blog/remote-debugging-port
