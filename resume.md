# Current handoff

The native arm64 Alpine/musl `headless-debug` Chrome build is complete and cached in `ungoogled-chromium-work-arm64`.

- Full `chrome` build: passed through Gates E/F; final output is `/work/out/headless-debug/chrome`.
- `make check-py`: Ruff and Ty both pass.
- `make test-fast`: passed; `/work/test-output/headless-debug/test-fast.json` is complete.
- `make test-functional`: passed strict Linux acceptance with native Mesa llvmpipe, H.264/AAC, TCP/pipe CDP, uBlock, extension, HTTPS, downloads, screenshots, sandbox evidence, and cleanup.
- `make stage-runtime`: passed; `/work/stage/headless-debug/runtime-manifest.json` checksums six staged runtime files.
- `make test-staged`: passed strict acceptance from `/work/stage/headless-debug/chrome` with the media baseline enforced.
- Media baseline: `/work/metadata/media-runtime-baseline.json`; no bundled `libffmpeg.so` and no high-risk symbol drift.

Key final evidence: `(gl=egl-gles2,angle=none)`, Mesa `llvmpipe (LLVM 22.1.3, 128 bits)`, GPU `sandboxed=true`, GPU `processCrashCount=0`, renderer `Seccomp=2`, media `framePixels=143104`, and `repeatCycles=3`.

The native-EGL/sandbox overlays are registered in the clean preparation patch list. `LLVM-TOOLCHAIN.md` records custom LLVM quirks. `TESTS-TODO.md` can be removed only after this handoff and `plan.md` evidence remain consistent.
