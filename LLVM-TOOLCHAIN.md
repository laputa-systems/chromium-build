# Custom LLVM toolchain notes

This repository uses the pinned Laputa `llvm-prebuilt-musl` release rather than Alpine's compiler packages or Chromium's downloaded toolchain. The current arm64 input is LLVM 22.1.8, installed at `/opt/llvm-musl`, with archive SHA-256 `675f9cf871313a5672a63882d4d30dd6dd55df0aa9caee70970542eb03a23da3`.

Record quirks discovered in the custom prebuilt toolchain here. Keep build fixes in the source patch stack only when they are required to integrate Chromium with a deliberate property or omission of this toolchain; do not silently broaden the toolchain or weaken validation to make an isolated compile pass.

## Current properties

- `clang` and `clang++` report the musl target triple and live under `/opt/llvm-musl/bin`.
- The Clang resource directory is `/opt/llvm-musl/lib/clang/22`.
- The bundle provides the static libc++/libc++abi/unwind archives and compiler-rt builtins needed by the normal Chromium profile.
- The image uses Alpine's ccache only as a wrapper; the compiler and LLVM binutils remain the Laputa tools.
- The bundle does not provide compiler-rt sanitizer interface headers such as `sanitizer/common_interface_defs.h` or `sanitizer/asan_interface.h`.

The last point was verified in the builder image. The matching Alpine-installed Clang resource tree also did not provide those sanitizer headers. The custom bundle is therefore suitable for the normal musl build and its trap-only compiler hardening, but it is not currently a complete sanitizer development toolchain.

## V8 sanitizer-header interaction

Chromium's normal debug profile is not an UBSan build: `is_ubsan` remains false. However, Chromium's compiler hardening adds trap-only flags including:

```text
-fsanitize=array-bounds -fsanitize-trap=array-bounds
-fsanitize=return -fsanitize-trap=return
```

Clang consequently reports its undefined-behavior-sanitizer feature through `__has_feature`, which causes V8's `testing.cc` to see `V8_USE_UNDEFINED_BEHAVIOR_SANITIZER`. That source included `sanitizer/common_interface_defs.h` even though the build did not provide a sanitizer runtime.

Alpine's `cr148-v8-no-san-trap.patch` already removes V8's sanitizer death-callback registration because the partial trap configuration fails to link with sanitizer callbacks. The local `musl-v8-sanitizer-header.patch` removes the now-unused header include left behind by that patch. It does not remove the trap-only hardening flags, disable the sandbox, or turn off sanitizer instrumentation elsewhere in Chromium.

This is an integration fix for the current profile, not evidence that the custom LLVM bundle supports ASan, MSan, UBSan runtime reporting, or sanitizer-instrumented builds. A future sanitizer profile must first add and validate matching compiler-rt headers, runtimes, linker behavior, and musl execution tests.

## Preferred future improvements

The cleanest long-term solution is a custom LLVM release that includes the compiler-rt sanitizer headers and the compatible musl sanitizer runtimes. Installing or copying only headers would make compilation pass but would not prove that sanitizer binaries can link or run correctly.

If the toolchain remains intentionally minimal, retain the narrow V8 integration patch and add explicit resource-header checks to environment validation so a future sanitizer-related failure is diagnosed as a toolchain capability gap. Do not add a broad fake sanitizer-header shim: it could make sanitizer builds compile while silently removing their reporting or runtime guarantees.

When updating the LLVM archive, repeat the following checks before changing this note or removing the patch:

1. Confirm the target triple, Clang resource directory, libc++ archives, libc++abi, libunwind, and compiler-rt builtins.
2. Check whether the resource directory contains the sanitizer interface headers and matching runtime libraries.
3. Re-run the direct compiler probes and the exact V8 `testing.cc` compile in the headless profile.
4. Re-evaluate whether Alpine's no-sanitizer-trap patch is still needed and whether the local compatibility patch can be removed.
