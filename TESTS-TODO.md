# Remaining functional test gates

The stdlib harness, transport tests, fixed-fd pipe launch, cleanup reporting, exact assertions, and SPKI-scoped HTTPS checks are implemented and verified against local macOS Chromium. They do not need to be repeated here.

These are the remaining gates that require the completed musl/Alpine Chromium build.

## 1. Run the strict Linux functional test

Run the final non-root Alpine environment with networking disabled:

```sh
scripts/test-functional.sh
```

The wrapper must run the suite with `--require-media` and `--require-llvmpipe` and produce a schema-2 `test.json` with `status: complete`.

Acceptance:

- H.264/AAC metadata is available.
- A decoded video frame has nonzero pixels.
- Audio metadata is available.
- Exactly three repeated media cycles complete.
- uBlock blocks the pinned test request without the fixture server receiving it.
- TCP and pipe phases pass.
- HTTPS succeeds before and after media without a `Security.certificateError` event.
- No Chromium process survives cleanup.

## 2. Review the runtime media audit

The strict run must retain `media-runtime.json`. Confirm that:

- `libav` libraries are loaded by the browser process tree;
- no unexpected bundled `libffmpeg.so` is loaded;
- renderer/GPU process mappings are present;
- symbol intersections between Chrome and the loaded media libraries are understood;
- high-risk collisions involving OpenSSL/BoringSSL, allocators, exception support, and unwind/runtime symbols are explicitly reviewed.

Preserve the reviewed report as the runtime baseline. On later rebuilds, provide it through `MEDIA_RUNTIME_BASELINE`; unexplained symbol drift must fail the audit.

## 3. Confirm Linux sandbox evidence

The strict run must record and pass:

- enabled user namespaces;
- no `--no-sandbox` in the observed Chromium process tree;
- GPU sandbox evidence;
- renderer process `NoNewPrivs` or seccomp evidence.

macOS verification does not substitute for these Linux checks.

## 4. Re-run from the staged runtime

After the build is staged as it will be consumed, repeat the strict test from that staged runtime. Retain:

- `test.json`;
- TCP and pipe result artifacts;
- Chromium logs and screenshots;
- the media manifest and `media-runtime.json`;
- the reviewed runtime baseline.

The staged run is the final acceptance evidence. Only after it passes should this file be deleted and the result recorded in `plan.md`.
