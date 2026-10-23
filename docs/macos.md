# Native macOS arm64 builds

The macOS backend builds the locked ungoogled release as a desktop
`Chromium.app` on Apple Silicon. It requires macOS 26 or newer, an Xcode SDK and
Metal Toolchain, LLVM 23.1.0 or newer, and the Rust toolchain pinned by the
repository. The input lock is [`config/macos.inputs.lock`](../config/macos.inputs.lock).

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
