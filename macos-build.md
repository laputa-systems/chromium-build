# Native macOS arm64 build

The native backend builds the locked ungoogled Chromium release as a desktop
`Chromium.app`, then signs and accepts only a bundle that passes the native
bundle audit and the Shadowdriver acceptance harness.

All builder-controlled state belongs under `/Volumes/dev`. Set an explicit
work root before starting:

```sh
export CHROMIUM_WORK_ROOT=/Volumes/dev/d/chromium-build/.work/macos-arm64
```

The preflight requires native Apple Silicon execution, macOS 26 or newer, a
usable Xcode macOS SDK and Metal Toolchain, and an arm64 LLVM/Clang toolchain
at least 23.1.0. LLVM 23.1.1 is preferred. The backend considers the
revision-matched Chromium LLVM, an explicit toolchain, and already-installed
Homebrew LLVM, preferring exact 23.1.1 and otherwise the newest compatible
candidate; it never installs or unlinks Homebrew packages. The exact Rust
toolchain is `nightly-2026-09-15`, installed into the work root by the fetch
phase. Apple clang and the selected LLVM are recorded separately because
Xcode's Apple clang version is not the same versioning scheme as the LLVM
distribution used for Chromium compilation.

If `doctor` reports a missing Metal Toolchain, install that host component
manually and rerun the preflight; the backend does not modify Xcode:

```sh
xcodebuild -downloadComponent MetalToolchain
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release doctor
```

Run the phases in order:

```sh
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

`stage-runtime` creates a signed candidate under the work root. `test-staged`
uses the read-only `/Volumes/dev/d/shadowdriver` checkout, explicitly loads the
locked uBlock Origin extension with browser-supplied extensions disabled, and
opens 32 distinct local pages concurrently. Only a passing candidate is
copied to the accepted bundle directory. The bundle audit uses an owned,
temporary work-root alias when Xcode's `otool` would interpret a helper name
such as `Chromium Helper (Alerts)` as an archive member.

The source phase downloads the locked full Chromium archive, creates empty Git
metadata for archive DEPS roots, and uses a shallow, blobless one-commit fetch
only for the pinned depot_tools checkout. The subsequent `gclient` sync is
restricted to non-Git GCS/CIPD inputs needed by the archive; it does not fetch
Chromium Git history or blobs. This is intentionally the lightest Git path
compatible with Chromium's native hooks, while preserving a complete source
archive for the build.

The native profile sets `macos_single_locale = "en-US"`. The compatibility hook
uses that value for `platform_pak_locales` and the Apple bundle output list, so
the shipped app contains only Chromium's normal `en.lproj` resource directory;
the profile also disables redundant gender-suffixed English pak variants. The
broader translation catalog remains available to GN inputs that are shared with
other desktop targets. To run the host checks without the read-only Helium and
Shadowdriver references, use the explicit smoke scope:

```sh
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release smoke-preflight
```

The repository also contains a workflow-dispatch-only macOS 26 full-build job at
`.github/workflows/macos-26-smoke.yml`. It provisions the exact Rust nightly,
checks the macOS 26+/SDK/Apple-toolchain/LLVM/disk contract, disables compiler
caching, fetches and builds the locked Chromium source, signs and audits the
bundle, and uploads it as a GitHub prerelease asset. Remote CI intentionally
does not clone or run Shadowdriver; local `test-staged` remains the consumer
acceptance gate.

Xcode 27 SDK TAPI inputs are not currently understood by the bundled lld, so
the checked-in `macos-release` profile uses the Apple linker and disables
ThinLTO. The generated GN arguments and configure report record this explicit
compatibility decision; it can be revisited when the selected Chromium lld
supports the SDK.

Compiler caching is local and optional. `auto` (the default) prefers an already
available `ccache`, then `sccache`; the selected wrapper is written into GN by
absolute path, while its cache and temporary directories live under
`/Volumes/dev/d/chromium-build/.work/macos-arm64/cache/compiler`. The backend
sets content-sensitive compiler checking for ccache and clears inherited remote
cache variables. Use `MACOS_COMPILER_CACHE=off`, `ccache`, or `sccache` to make
the choice explicit. The backend never installs or unlinks Homebrew packages;
if no wrapper is present, Ninja's normal incremental output cache remains the
active build cache and the doctor/configure reports say so. Re-run `configure`
after making a cache wrapper available so the generated toolchain includes it.

Use `status` to inspect phase reports. To discard owned source, outputs, stages,
and metadata while retaining locked downloads and tool caches, run:

```sh
/Volumes/dev/d/chromium-build/chromium-build --platform macos --arch arm64 --profile macos-release reset --yes
```

The Helium and Shadowdriver checkouts are read-only references. The backend
records their revision and dirty state during preflight and keeps generated
source, caches, profiles, logs, harness files, and artifacts in the selected
work root.
