# Native macOS arm64 builds

The macOS backend builds the locked ungoogled release as a desktop
`Chromium.app` on Apple Silicon. It requires macOS 26 or newer, an Xcode SDK and
Metal Toolchain, the bundled Chromium clang from the prepared source, the Rust
toolchain pinned by the repository, and the locked `deno` test input resolved
only from `.work/macos-arm64/tools/bin`. The input lock is [`config/macos.inputs.lock`](../config/macos.inputs.lock).

Set the work root to an absolute child of this checkout's `.work/` directory.
Downloads, tools, cache stores, source, build output, profiles, logs, and
packages stay there. The backend does not install host packages or modify
`/Applications`, Xcode, keychains, or sibling checkouts.

## Phases

`update-lock --latest` is a maintainer action for selecting new inputs. Normal
runs use the committed lock.

```sh
export CHROMIUM_WORK_ROOT="$PWD/.work/macos-arm64"
./chromium-build --platform macos --arch arm64 --profile macos-release doctor
./chromium-build --platform macos --arch arm64 --profile macos-release smoke-preflight
./chromium-build --platform macos --arch arm64 --profile macos-release fetch
./chromium-build --platform macos --arch arm64 --profile macos-release prepare
./chromium-build --platform macos --arch arm64 --profile macos-release configure
./chromium-build --platform macos --arch arm64 --profile macos-release build
./chromium-build --platform macos --arch arm64 --profile macos-release stage-runtime
./chromium-build --platform macos --arch arm64 --profile macos-release test-staged
./chromium-build --platform macos --arch arm64 --profile macos-release package
```

Compiler caching is optional and local. `auto` selects an installed ccache or
sccache. Cache data and configuration remain under the work root. GN does not
use Kache as a compiler wrapper.

`stage-runtime` signs and audits a candidate app. `test-staged` invokes
Shadowdriver's ignored browser acceptance integration test in the sibling
checkout, passing it the staged executable, profile, and uBlock extension.
Shadowdriver owns the test source; chromium-build owns the phase orchestration.
Acceptance covers BrowserNative identity, CDP capture, 32- and 64-page waves,
MV2 and MV3 extensions, uBlock blocking, screenshots, PDF, and headed and
headless Metal rendering. A supplied app can be tested with
`test-staged --app /absolute/path/Chromium.app`.

`package` writes an archive and checksum under the work root's `release`
directory. Use `status` and the work root's JSON phase reports to determine
which phases passed. A CI package skips local Shadowdriver acceptance and does
not establish consumer acceptance.

## Native automation compatibility

The macOS-only overlay is documented in
[`config/patches/macos-native/README.md`](../config/patches/macos-native/README.md).
`--native-automation-compatibility` enables desktop headless UA normalization,
the native webdriver policy, and host display geometry. Ordinary behavior
remains available with the switch disabled. Linux inputs are unchanged.

Preparation records the digest of the actual overlay and source-mutating
profile compatibility code. Later phases reject stale digests. Appending an
exact patch to a prepared prefix preserves the existing source and build tree.
Changing an applied patch requires source recovery before preparation continues.

`test-staged` now requires two independent results: Shadowdriver product
compatibility and native automation signal acceptance. The second result
compares headed, headless, explicit automation, direct native CDP, passive
Shadowdriver, passive restart, and instrumented capture. Its report records
page-authored values, structured request hints, worker and OOPIF observations,
and fixture pixels and HTML. Test selection must execute one passing named
test. The phase records Shadowdriver's revision and a digest of its relevant
source, tests, generated scripts, and lockfile. Changed acceptance source during
a run prevents promotion.

The native tests use the staged executable. They neither install Chromium nor
use an application under `/Applications`. Native compatibility does not promise
universal resistance to detection. Passive automation omits the capture
instrumentation. Instrumented capture remains observable.

To keep acceptance headless while using an already captured headed control,
pass `test-staged --headed-reference /absolute/path/to/observations.json`.
The reference must contain completed observations with the same browser version,
host, and fixture policy. No headed browser launches in this mode. The report
records the reference digest and marks the fresh headed product check unverified;
promotion records `headless-with-historical-headed-reference` as its scope.
