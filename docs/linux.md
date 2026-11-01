# Linux musl builds

The Linux backend builds headless Chromium in an Alpine container on native
amd64. Linux/arm64 is out of scope: the locked toolchain has no native Linux
arm64 LLVM archive. Chromium source and Ninja output use Docker named
volumes. Only `fetch` needs network access; later phases run offline.

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

The Helium product is a Linux amd64 musl desktop build. The image, source
inputs, package world, patch inventory, and executable name come from
`config/helium.inputs.lock`; the only supported profile is
`helium-wayland-release`.

```sh
./chromium-build --product helium --profile helium-wayland-release image
./chromium-build --product helium --profile helium-wayland-release fetch
./chromium-build --product helium --profile helium-wayland-release prepare
./chromium-build --product helium --profile helium-wayland-release poc
./chromium-build --product helium --profile helium-wayland-release build
./chromium-build --product helium --profile helium-wayland-release test-fast
./chromium-build --product helium --profile helium-wayland-release stage-runtime
./chromium-build --product helium --profile helium-wayland-release package
```

`stage-runtime` takes the `//chrome:chrome` runtime dependency list from GN,
adds the upstream desktop packaging inputs, all generated locale packs and V8
startup data, and closes the NSS, GTK, Wayland, and Mesa software-rendering
libraries. It recursively audits every shipped ELF, rejects glibc and absolute
build RPATHs, and keeps the graphics/NSS closure separate from the browser's
direct dependencies. The Helium profile uses Torque's instance-type generation
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
