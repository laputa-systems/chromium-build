# Native macOS arm64 builds

The macOS backend builds the locked ungoogled release as a desktop
`Chromium.app` on Apple Silicon. It requires macOS 26 or newer, an Xcode SDK and
Metal Toolchain, the bundled Chromium clang from the prepared source, the Rust
toolchain pinned by the repository, and vanilla Node with pnpm for TypeScript
acceptance. The input lock is [`config/macos.inputs.lock`](../config/macos.inputs.lock).

Set the work root to an absolute child of this checkout's `.work/` directory.
Downloads, tools, cache stores, source, build output, profiles, logs, and
packages stay there. The backend does not install host packages or modify
`/Applications`, Xcode, keychains, or sibling checkouts.

Sealed phases (`prepare`, `configure`, `build`, `stage-runtime`, `package`) run
under `/usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)'`;
`test-staged` and the Rust product harness use the loopback profile, which
additionally allows localhost and unix-sockets. `NETWORK_MODE` is derived from
the wrapper actually applied (`none`, `loopback`, or `fetch`); if the wrapper
cannot start, the phase fails. The TypeScript native-stealth harness runs
without the sandbox wrapper but with the same controlled variables: its
vitest tree spawns Chromium and inspects processes with `ps`, both of which
Seatbelt refuses under `sandbox-exec` on this host. Its fixtures, browsers,
and DevTools endpoints are loopback-only by construction.

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

## Release configuration vs upstream official

Effective GN args are `config/profiles/macos-release.gn` plus the backend's
hermetic prefix (see `out/macos-release/args.gn` in a configured tree).
Security-relevant differences from Chromium's official release configuration:

- `is_official_build=false`, `is_chrome_branded=false` — intentional. This is
  an unbranded build with no Google services.
- Proprietary codecs off (no `proprietary_codecs`/`ffmpeg_branding` set;
  ungoogled default) and `enable_widevine=false` (profile-explicit, confirmed
  by the absent CDM in the audited bundle) — intentional. No DRM or
  proprietary binaries ship. Enabling them was never requested.
- `safe_browsing_mode=0` — intentional per campaign decision; no Safe Browsing
  or telemetry is restored for the checklist.
- `use_official_google_api_keys=false` with empty keys — intentional; no
  Google API identity.
- `use_thin_lto=false`, `chrome_pgo_phase=0` — intentional. The Xcode 27 SDK
  ships TAPI targets the bundled lld does not understand, and Chromium's LTO
  configuration requires lld.
- `disable_fieldtrial_testing_config=true` — intentional. No field-trial
  testing config is fetched; the build stays hermetic.
- `clang_use_chrome_plugins=false` — intentional. Hermetic toolchain; the
  plugins are build-time checks, not product mitigations.
- `enable_reporting`, `enable_remoting`, `enable_mdns`,
  `enable_service_discovery`, `enable_hangout_services_extension` all false —
  intentional attack-surface reduction; no remoting, discovery, or Hangout
  services in the unbranded product.
- `exclude_unwind_tables=true` — intentional. Smaller binary; diagnostics only.
- `v8_drumbrake_bounds_checks=true` — intentional extra hardening. The locked
  source defaults this to false on macOS arm64 clang non-component builds;
  the backend enables V8 sandbox software bounds checks.
- `symbol_level=1`, `blink_symbol_level=0`, `v8_symbol_level=0` —
  intentional. Smaller binary; diagnostics only, no mitigation change.
- V8 sandbox, pointer compression with shared cage, external code space,
  generated-code validator: not set in our args, so Chromium defaults apply
  identically to official builds — no difference.
- PartitionAlloc as malloc and the allocator shim: not set in our args, so
  Chromium defaults apply identically to official builds — no difference.
