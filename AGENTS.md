# chromium-build

Builds ungoogled Chromium through an amd64-only Linux musl backend and a
native macOS arm64 backend. `./chromium-build`, the input locks, profiles,
patch inventories, and phase reports define current behavior.

## Working rules

- Read the relevant command, callers, lock inputs, patches, and focused tests
  before changing a build contract.
- Keep Linux and macOS inputs and phases separate. Do not weaken source,
  signature, sandbox, runtime, or browser acceptance checks.
- Do not run formatters, linters, hooks, or CI. Verify locally with a focused
  check. Preserve resumable state when diagnosing failures.
- Do not install or update host tools as part of a normal build. Keep macOS
  generated state under this repository's `.work/` directory.

## Guides

- Roadmap: [docs/roadmap.md](docs/roadmap.md) holds all current and future work;
  its Status is the resume point.
- Linux musl builds (amd64-only; Linux/arm64 is out of scope), profiles,
  gates, packaging, and installation: [Linux](docs/linux.md).
- Native macOS builds, cache selection, staged acceptance, and packaging:
  [macOS](docs/macos.md).
- macOS compiler cache selector: `MACOS_COMPILER_CACHE=off|ccache|sccache|auto`;
  `none` and `disabled` also turn compiler caching off.
- Gate F's independent graph audit and generated evidence:
  [Gate F oracle](docs/gate-f-oracle.md).
- Helium's acceptance matrix and the adblock-rust audit:
  [Helium acceptance](docs/helium-acceptance.md),
  [Helium adblock-rust](docs/helium-adblock-rust.md).

The Makefile contains command shortcuts. `config/inputs.lock`,
`config/helium.inputs.lock`, `config/packages.amd64.lock`,
`config/macos.inputs.lock`, and the profile files are authoritative for pinned
inputs. `sources.lock.json` lists every pinned download with its SHA-256 for the
release contract and is derived from those locks: after changing one, run
`python3 scripts/sources_lock.py write --fetch` and commit the result; `make lint`
fails when the two disagree.

## Commands

- Build and run browser acceptance with `./chromium-build`; use the platform
  guides for required flags and phase order.
- `make build`, `make verify`, `make lint`, `make test`, and `make clean` follow the
  build-repository contract. `TARGET` is a Rust triple: `aarch64-apple-darwin` builds
  the macOS app, `x86_64-unknown-linux-musl` runs `release-linux`, and
  `aarch64-unknown-linux-musl` selects Helium's arm64 builder for the explicit steps.
  `make build-target` takes the Ninja output as `NINJA_TARGET`. `make clean` removes
  the root Ruff cache and macOS build, stage, release, and verification outputs. It keeps installed
  dependencies, fetched inputs, source, and Docker volumes.
- Run the Python static checks with `make check-py` (alias of `make lint`).
- Releases: `.github/workflows/release.yml` publishes
  `chromium-<version>-aarch64-apple-darwin.tar.xz` and its manifest from a manual dispatch.
  `macos-26-smoke.yml` runs the same build without publishing. Linux builds run in
  Docker on the maintainer's machine and are not released from CI.
- The `rust-toolchain-<revision>-aarch64-linux-musl` GitHub releases are the input mirror
  that `config/helium.inputs.lock` pins; they sit outside the release contract.

## Verification

- Costs on an M-series Mac, warm: `make test` 14s to 17s (194 tests, 1 skipped; first run adds about 7s for the venv), `make lint` 1.3s, `make coverage` 30s to 60s (host group only: 35.5% line, 27.8% branch). CI runs both in `ci.yml` on Linux arm64 and macOS; `make test-ci` runs the Linux builder group in the image. No test builds Chromium.
- `make test` runs `tests/run_controller_tests.py`: group `macos` on Darwin, `linux` on Linux, `linux-builder` only inside the builder image. Modules outside the host's group do not run.
- `./chromium-build` argument validation, usage and exit codes (0, 1, 2): `tests/test_chromium_build_cli.py`; `image` and `--jobs`: `tests/test_image_jobs.py`; the docker phases and `install` are `none`.
- macOS backend commands, work-root ownership, sandbox profiles, build identity, release manifest: `tests/test_macos_backend.py`; its `main` entry and exit codes are `none`.
- Release assets and `sources.lock.json`: `tests/test_release_assets.py`, `tests/test_sources_lock.py` (`make lint` runs the same check). Release workflow shape: `tests/test_workflow.py`.
- macOS CI policy checks `scripts/verify_macos_ci.py`, used by `release.yml`: `tests/test_verify_macos_ci.py` (bounded mutation run in the deslop doc).
- Linux package, install, qualification: `tests/test_helium_runtime.py`, `tests/test_qualify_package.py`; profiles and GN contracts: `tests/test_helium_gn.py`; fixtures and CDP transport: `tests/test_browser_test_support.py`, `tests/test_helium_wayland.py`.
- Gates A to F, `prepare`, `build`, `test-functional` scripts: image-only, `none` on the host; they run in `release-linux`.
- Performance workload: none. The only hot path is Chromium's own build, which a full build measures and CI cannot repeat.
- Vendored upstream sources: none. `config/patches/` holds first-party patches against the pinned Chromium, ungoogled-chromium and Helium trees (revisions in `config/*.inputs.lock`); `config/glibc/` is first-party assembly.
- Deslop findings, coverage and mutation records: [docs/deslop-build-scripts.md](docs/deslop-build-scripts.md).

