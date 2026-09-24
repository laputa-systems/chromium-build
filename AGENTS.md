# Repository working contract

This repository builds ungoogled Chromium through two separate backends. Use
`./chromium-build` as the host entry point. `Makefile` provides shortcuts, and
`scripts/macos_backend.py` owns the native macOS phases. The lock files, profile
files, patch inventory, command implementation, and phase reports are the source
of truth for the current build; historical artifact paths and image digests are
not build instructions.

## Working rules

- Before editing, trace the intended behavior through the command, its callers,
  locks, patches, tests, and nearby comments. Use one domain name per concept and
  keep contract changes explicit in code, tests, and documentation.
- For a bug fix, add the smallest isolated failing regression test first, then
  fix the cause. Start verification with the narrowest useful compiler, schema,
  test, or runtime check. Keep existing comments that explain constraints.
- Do not add a dependency without consulting the user. Do not run formatters,
  linters, or pre-commit hooks. Never push unless explicitly asked.
- Preserve resumable build state and logs when diagnosing failures. Keep version
  updates and build-contract changes reviewable; do not weaken sandboxing,
  signature checks, source checksums, or browser acceptance to make a build pass.

## Linux musl backend

Omitting `--platform` selects Linux. The implemented profile is
`headless-debug`; the pinned Chromium and toolchain identities are in
`config/inputs.lock` and the architecture-specific package locks. Chromium 153's
GN graph requires printing targets to exist, so this profile enables printing
while disabling CUPS (`use_cups=false`). `Dockerfile`
builds a native Alpine image. Source and Ninja output live in Docker named
volumes, not host bind mounts. Only `fetch` may use networking; preparation,
generation, compilation, testing, and staging run without it. Do not silently
replace pinned archives with Git clones, depot_tools, or Chromium toolchain
downloads on this backend.

The Linux amd64 image uses Chromium 153's pinned LLVM 24 archive at
`/opt/chromium-llvm`. Chromium's compiler is a glibc host executable, so the
image copies a pinned Debian glibc runtime solely to run build tools. Its Clang
configuration targets `x86_64-unknown-linux-musl`; browser outputs must use the
musl interpreter and must not gain glibc, libstdc++, or libgcc direct ELF
requirements. Chromium's in-tree libc++/libc++abi/libunwind provide the C++
runtime. The pinned Alpine `ninja-build` package, ccache, and Rust toolchain are
build-only tools. The copied stock-Alpine FFmpeg runtime dependency closure is
the narrowly audited shipped exception, including its transitive GNU runtime
libraries. Do not broaden that exception by editing an allowlist alone. See
`LLVM-TOOLCHAIN.md` for the compiler boundary and validation.

The amd64 compiler cache lives in the persistent Docker volume
`ungoogled-chromium-153-ccache-amd64`, is limited to 10 GB, and is namespaced
by architecture, LLVM archive digest, and compiler-command policy. Ccache must
use GN's selected `clang` or `clang++` rather than a forced compiler override.
Gate A compiles C and C++ musl objects twice and requires direct cache hits;
Gate F confirms that C/C++ compile
commands use `/usr/bin/ccache` while link commands bypass it. Use
`./chromium-build --arch amd64 cache-stats` to inspect the cache. A first full
build mostly records misses because each source is compiled for the first time.
`stage-runtime` audits the built Chrome ELF interpreter and direct shared
library requirements before copying it; its manifest records both.

The earlier arm64/Chromium 150 headless milestone passed its full `chrome`
build, Gates A–F, `test-fast`, functional `test`, `stage-runtime`, and
`test-staged`; these are historical results and do not establish Chromium 153
amd64 acceptance. The current Linux 153 lock supports native amd64 only:
Chromium publishes no native Linux arm64 LLVM 24 archive at this revision.
Check current work-volume reports before claiming any new phase passed.

On the native amd64 host, the Chromium 153 headless build passed a clean
source preparation and Gates A–F in the `ungoogled-chromium-153-final-proof-amd64`
volume. The built and staged Chrome passed `test-fast`, the full functional
scenario, and the staged functional scenario in the main work volume. An
isolated Shadowdriver harness opened 32 distinct pages with the pinned uBlock
extension policy against the staged Chrome. These results establish this
host's headless acceptance; they do not establish relocation or a portable
package.

Typical Linux sequence:

```sh
./chromium-build --arch amd64 --profile headless-debug image
./chromium-build --arch amd64 --profile headless-debug fetch
./chromium-build --arch amd64 --profile headless-debug prepare
./chromium-build --arch amd64 --profile headless-debug poc
./chromium-build --arch amd64 --profile headless-debug build
./chromium-build --arch amd64 --profile headless-debug test-fast
./chromium-build --arch amd64 --profile headless-debug test
./chromium-build --arch amd64 --profile headless-debug stage-runtime
./chromium-build --arch amd64 --profile headless-debug test-staged
```

`package` is unimplemented for Linux. Exported runtime closure, clean-host
relocation, cold-build acceptance, and the Wayland/ALSA profile also lack
current evidence. Do not call a staged runtime a portable release package.

## Native macOS arm64 backend

Select `--platform macos --arch arm64 --profile macos-release`. The backend
builds the locked ungoogled release in `config/macos.inputs.lock` as a desktop
`Chromium.app`. It uses native Apple Silicon and macOS 26 or newer, a usable Xcode
SDK and Metal Toolchain, compatible LLVM 23.1.0 or newer, and the locked Rust
nightly. The work root must be an absolute path under `/Volumes/dev`; keep all
project-controlled downloads, tools, caches, source, output, logs, test profiles,
and artifacts there. Do not alter Xcode, Homebrew installations, keychains,
`/Applications`, or the read-only Helium and Shadowdriver reference checkouts as
part of an ordinary local build.

Mac source preparation applies the version-matched ungoogled core series. Do not
mix in the Linux musl/headless patch stack or Helium branding/product changes.
The release profile keeps the normal desktop UI, extension support, DevTools,
native sandbox, and only the packaged `en-US` locale. The checked-in profile
uses Apple's linker and disables ThinLTO because the selected bundled lld does
not parse the Xcode 27 SDK TAPI inputs; revisit this only with a demonstrated
compatible toolchain. Compiler caching is optional and local through
`MACOS_COMPILER_CACHE=off|ccache|sccache|auto`; `auto` uses an already installed
wrapper if available.

Run local phases in order. `update-lock --latest` is an explicit maintainer
operation when selecting a new release; ordinary runs use the committed lock.

```sh
export CHROMIUM_WORK_ROOT=/Volumes/dev/d/chromium-build/.work/macos-arm64
./chromium-build --platform macos --arch arm64 --profile macos-release doctor
./chromium-build --platform macos --arch arm64 --profile macos-release fetch
./chromium-build --platform macos --arch arm64 --profile macos-release prepare
./chromium-build --platform macos --arch arm64 --profile macos-release configure
./chromium-build --platform macos --arch arm64 --profile macos-release build
./chromium-build --platform macos --arch arm64 --profile macos-release stage-runtime
./chromium-build --platform macos --arch arm64 --profile macos-release test-staged
./chromium-build --platform macos --arch arm64 --profile macos-release package
```

`stage-runtime` signs and audits a candidate app. `test-staged` runs the actual
Shadowdriver acceptance harness against the staged executable, loads the pinned
uBlock extension, and evaluates 32 distinct data pages before promoting the
candidate. `test-staged --app
/absolute/path/Chromium.app` tests a supplied or relocated app. `package` writes
the candidate zip and checksum under the work root's `release` directory.
Use `status` and the JSON phase reports under the work root to establish what
actually passed on a given host. `reset --yes` removes owned generated state;
use it only when that loss is intended.

`.github/workflows/macos-26-smoke.yml` is a manually dispatched full-build and
prerelease-packaging path. It signs and audits the app, but intentionally skips
local Shadowdriver acceptance and packages the candidate without an accepted
marker. A CI artifact alone does not establish that consumer gate. The native
build, signing, packaging, and Shadowdriver integration paths are implemented,
but the original acceptance scope is not fully covered: the current harness
does not demonstrate normal GUI operation, independent MV2/MV3 extension
behavior, uBlock request blocking, or screenshots. The default acceptance path
also does not exercise the supported `test-staged --app` relocation check.
Real build and acceptance claims require corresponding phase reports or CI run
evidence; do not infer those results from passing unit tests or the workflow
definition.
