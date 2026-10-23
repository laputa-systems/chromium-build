# Linux musl builds

The Linux backend builds headless Chromium in an Alpine container. The current
locked configuration supports native amd64. Chromium source and Ninja output
use Docker named volumes. Only `fetch` needs network access; later phases run
offline.

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
commands and does not wrap link commands. Inspect the persistent cache with
`./chromium-build --arch amd64 cache-stats`.

`stage-runtime` audits Chrome's ELF interpreter and direct dependencies.
`package` collects Chrome, its runtime libraries, font configuration, and
fonts under `/work/package/<profile>`. `install` verifies that package and
copies it to the host prefix. `make release-linux` runs the resumable
headless-release path through installation.
