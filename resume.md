# Current handoff

The native arm64 Alpine/musl `headless-debug` Chrome build was reverified with the Laputa LLVM 23.1.0-rc2 release. The pinned arm64 archive is `clang+llvm-23.1.0-rc2-aarch64-linux-musl.tar.xz` from release `llvm-musl-23.1.0-rc2-6eb5fb9`, SHA-256 `0c9bd6f0fefa26dbdb7d6ed568f3799b558428b1ce1264656aa328fc6fd9e32d`.

- Full `chrome` target: passed with `JOBS=32`, networking disabled; build metadata records 4,665 seconds and `/work/out/headless-debug/chrome`.
- Canonical builder image: `sha256:2c6a09e4299c413d3429ff05502e7cd30802c24e907e2918c304c03f19becb71`.
- Gates A–F: all passed, including direct runtime probes, source provenance, GN generation, hermetic smoke, and the complete Chrome toolchain audit.
- `make test-fast`: passed, 9 tests.
- `make test-functional`: passed, 9 strict tests over TCP and pipe CDP. Evidence includes extension/uBlock behavior, HTTPS, cookies/storage, downloads, screenshots, H.264/AAC playback, native Mesa llvmpipe, GPU sandboxing, and cleanup.
- `make stage-runtime`: passed; `/work/stage/headless-debug/runtime-manifest.json` checksums six staged runtime files.
- `make test-staged`: passed, 9 tests directly against the staged Chrome, with the media-runtime baseline enforced.
- `make check-py`: the host's current Ruff reports the pre-existing 48-violation repository baseline; targeted Python compilation, shell validation, JSON validation, and `git diff --check` pass.

Key functional evidence remains `(gl=egl-gles2,angle=none)`, Mesa `llvmpipe (LLVM 22.1.3, 128 bits)` (the host Mesa version, not the compiler toolchain), `GPU sandboxed=true`, `GPU processCrashCount=0`, renderer `Seccomp=2`, media `framePixels=143104`, and `repeatCycles=3`. The staged Chrome SHA-256 is `2d05b6ad06d64d0f67b20f2fb948038d6c0b302fc2b1d3834bf15becb84567dc`.

The six Clang 23 compatibility patches, including the final Omnibox `[[nodiscard]]` fix, are in the deterministic local patch list and generated provenance. `LLVM-TOOLCHAIN.md` records the release cfg overlay and Clang 23 quirks. `TESTS-TODO.md` remains removed after acceptance.
