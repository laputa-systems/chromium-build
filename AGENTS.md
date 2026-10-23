# chromium-build

Builds ungoogled Chromium through a containerized Linux musl backend and a
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
- Do not push or commit unless requested.

## Guides

- Linux musl builds, profiles, gates, packaging, and installation:
  [Linux](docs/linux.md).
- Native macOS builds, cache selection, staged acceptance, and packaging:
  [macOS](docs/macos.md).
- macOS compiler cache selector: `MACOS_COMPILER_CACHE=off|ccache|sccache|auto`;
  `none` and `disabled` also turn compiler caching off.
- Gate F's independent graph audit and generated evidence:
  [Gate F oracle](docs/gate-f-oracle.md).

The Makefile contains command shortcuts. `config/inputs.lock`,
`config/macos.inputs.lock`, the architecture package locks, and the profile
files are authoritative for pinned inputs.

## Commands

- Build and run browser acceptance with `./chromium-build`; use the platform
  guides for required flags and phase order.
- Run the Python static checks with `make check-py`.
