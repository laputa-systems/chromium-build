# Linux musl builds

The Linux backend builds headless Chromium and the Helium desktop browser in
an Alpine container on the native host architecture (amd64, or arm64 for
Helium). Headless Chromium remains amd64-only; Helium also builds for
linux/arm64. Chromium source and Ninja output use Docker named volumes. Only
`fetch` needs network access; later phases run offline.

## Inputs and profiles

The pinned Chromium revision, tool archives, and digests are in
[`config/inputs.lock`](../config/inputs.lock) and
[`config/packages.amd64.lock`](../config/packages.amd64.lock). The
`headless-debug` and `headless-release` profiles differ in debug checks.
Chromium's compiler runs against a pinned glibc runtime in the image and targets
`x86_64-unknown-linux-musl`. Staged Chrome must use the musl loader and must not
directly require glibc, libstdc++, or libgcc. Chromium's in-tree libc++ provides
the C++ runtime. The audited FFmpeg runtime closure is the only bundled GNU
runtime exception.

## Build and acceptance

Run phases in order. `build` requires Gate D to have passed for the same
profile.

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

Gate A checks direct cache reuse for C and C++ objects. Gate F checks the
target-scoped compiler and link commands; its separate oracle is described in
[Gate F oracle](gate-f-oracle.md). Linux GN uses `/usr/bin/ccache` for compile
commands and does not wrap link commands.

### Helium Rust toolchain

Helium uses Chromium's bundled Rust 1.100 toolchain and Crubit instead of the
Alpine Rust packages. The target ABI is `x86_64-unknown-linux-musl`; the target
standard library and Crubit bindings are compiled in-tree. Rust host tools,
build scripts, and procedural macros are glibc host binaries, so they link
against the Debian runtime already copied for the compiler through
`/opt/chromium-glibc` and `scripts/rust-host-linker`. The browser and every
shipped shared object still target musl. Alpine Rust, Cargo, rust-src, and
rustfmt are not part of the Helium package world.

The Helium Wayland fixture owns a headless Sway session with the pixman
renderer and Mesa llvmpipe for the browser. `scripts/helium_wayland.py
--check-fixture` proves output, clipboard, and screenshot ownership without a
GPU device, XWayland, or the host session.

`stage-runtime` audits Chrome's ELF interpreter and direct dependencies.
`package` collects Chrome, its runtime libraries, font configuration, and
fonts under `/work/package/<profile>`. `install` verifies that package and
copies it to the host prefix. `make release-linux` runs the resumable
headless-release path through installation.

## Helium desktop package

The Helium product is a Linux musl desktop build (amd64 and arm64). The image, source
inputs, package world, patch inventory, and executable name come from
`config/helium.inputs.lock`; the only supported profile is
`helium-wayland-release`. Browser acceptance uses the Linux CDP and Sway
harness; Shadowdriver remains macOS-only. The package ships no glibc, gcompat,
static browser, or sandbox bypass, and needs no X server, session bus, or
hardware GPU; VA-API is optional at runtime.

The build ships no Helium onboarding component and no first-run setup. The
input lock carries no onboarding payload, `prepare` unpacks only the search
engine data and uBlock components, and `helium-remove-onboarding.patch` drops
the `chrome://setup` page and its startup redirect.

Autoupdates are fully off: `helium-disable-autoupdates.patch` forces the
Helium services browser/component update gates off, defaults the browser-updates
preference off, removes its settings toggle, and stops the component updater
(`CheckForUpdates` returns early; component and background-downloader switches
are off, with `UpdateUrl` already empty). Extension updates (uBlock and
extensions) still run. On Linux, browser updates stay with the distro package
manager.

The profile uses an official-style optimized build:
`is_official_build=true`, `dcheck_always_on=false`, `chrome_pgo_phase=2`,
`use_thin_lto=true`, and `thin_lto_enable_optimizations=true`. Chromium's
bundled Linux PGO profile is selected from `chrome/build/pgo_profiles` through
`chrome/build/linux.pgo.txt`, so PGO needs no network fetch. The profile
enables VA-API video decode, disables D-Bus and GTK, and omits the DevTools
frontend resources while retaining raw CDP. `helium-no-devtools-frontend.patch`
and `helium-no-dbus.patch` carry those exclusions; `TODO.md` tracks restoring
the frontend and completing the non-D-Bus fallbacks. With `use_dbus=false` and
`use_gtk=false`, the packaged browser does not require a session bus or GTK:
native file choosers, GTK theming, and native dialogs are gone, and uploads
go through the web content or CDP. Native notifications are
disabled and external URI or file-manager launch falls back to `xdg-open` and
`xdg-email`. Accessibility is disabled by default; `TODO.md`
tracks restoring it without requiring a session bus.

VA-API decode is compiled in but the GPU driver stays host-provided: the
browser loads the host's musl `libva` and Intel driver at runtime. On Intel
Broadwell, `libva-intel-driver` provides H.264 and VP8 decode through Mesa's
iris OpenGL driver. VP9 and HEVC fall back to software, as does the whole path
when no VA-API device is present.

```sh
./chromium-build --product helium --profile helium-wayland-release image
./chromium-build --product helium --profile helium-wayland-release fetch
./chromium-build --product helium --profile helium-wayland-release prepare
./chromium-build --product helium --profile helium-wayland-release poc
./chromium-build --product helium --profile helium-wayland-release build
./chromium-build --product helium --profile helium-wayland-release test-fast
./chromium-build --product helium --profile helium-wayland-release build-target chrome_sandbox --jobs 32
./chromium-build --product helium --profile helium-wayland-release stage-runtime
./chromium-build --product helium --profile helium-wayland-release test-staged
./chromium-build --product helium --profile helium-wayland-release package
```

`//chrome:chrome` does not build the `chrome_sandbox` data dependency, so
`build-target chrome_sandbox` must run before staging. `stage-runtime` also
closes the system FFmpeg libraries; their `libstdc++.so.6` and `libgcc_s.so.1`
dependencies are allowed only below the FFmpeg roots, matching the builder
policy's scoped exception.

`stage-runtime` takes the `//chrome:chrome` runtime dependency list from GN,
adds the upstream desktop packaging inputs, all generated locale packs and V8
startup data, and closes the NSS, Wayland, and Mesa GPU libraries. VA-API
is loaded from the host and is not part of the staged closure. It recursively
audits every shipped ELF, rejects glibc and absolute build RPATHs, and keeps
the graphics/NSS closure separate from the browser's direct dependencies. The Helium profile uses Torque's instance-type generation
because Chromium's prebuilt `llvm-libclang` is a glibc binary in this musl
builder; `v8_use_metagen_instance_types=false` selects the supported path.

`package` writes the relocatable bundle at `/work/package/<profile>` and the
candidate archive at `/work/artifacts/helium-0.20.3.1-linux-x86_64-musl.tar.xz`
with its `.sha256` sidecar. It does not publish or install it.

After a passing browser report for that archive, bind the report to the
archive digest before installation:

```sh
python3 scripts/qualify-package.py --archive "$ARCHIVE" --report "$REPORT"
```

The resulting `.acceptance.json` sidecar is required by archive installation.
It names the archive digest, product, version, architecture, and the actual
browser checks that passed.

The offline installer needs only Python's standard library and the package,
checksum, and acceptance sidecar; it makes no Docker calls:

```sh
python3 scripts/install-runtime.py --archive "$ARCHIVE" --verify-extract "$DIR"
./chromium-build --product helium --profile helium-wayland-release install \
    --archive "$PWD/.../helium-0.20.3.1-linux-x86_64-musl.tar.xz"
./chromium-build --product helium --profile helium-wayland-release install --rollback
```

The default installation prefix is `${HOME}/.local/opt/helium-wayland-release`;
the launcher is `${HOME}/.local/bin/helium`. The installer keeps versioned
slots and a `<prefix>.previous` symlink for rollback. It refuses unmanaged
prefixes, launchers, and archive links, and does not write through symlinks.

### Release evidence

The current archive is published as the prerelease
`https://github.com/laputa-systems/chromium-build/releases/tag/helium-0.20.3.1`
with its `.sha256` and `.acceptance.json`. The archive is
`helium-0.20.3.1-linux-x86_64-musl.tar.xz` (223.3 MB, 521 files, 147 bundled
libraries, SHA-256
`6853af22b60345c92e0cbe69181aebd8af3fa79842c7f0135d20c9656c5918c6`). It was
built from builder image
`sha256:160899ccbfcd29483806ae6a5787b2e29b7ef2aaa8c6f2c10bdc41ba6972a91b`,
and the acceptance sidecar binds that digest to the product-identity,
Wayland-window, page-rendering, sandbox-renderer, and clean-shutdown checks
under the isolated Sway fixture. The `~/usr` desktop provisioner later installed
the published archive in a disposable `alpine:edge` container: checksum and
sidecar validation, manifest digests, the setuid `chrome_sandbox`, the
`--version` probe, and a second idempotent run all passed.

That is automated fixture and container evidence, not target evidence. The
package has not run in the owner's dwl session, has not been installed live, and
has not been exercised against Broadwell VA-API hardware.
[Helium acceptance](helium-acceptance.md) defines the remaining observations
and the target qualification procedure.
