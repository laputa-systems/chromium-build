# Chromium LLVM 24 on Alpine musl

The Linux amd64 backend pins the compiler archive published for Chromium
153.0.8010.52 in `config/inputs.lock`: revision
`llvmorg-24-init-3796-g20e97c4b-2`, installed at `/opt/chromium-llvm`.
`Dockerfile` verifies its archive size and SHA-256. The archive's Clang and LLD
report Clang `24.0.0git` and LLD `24.0.0`; Gate A checks the revision, tools, configuration hashes,
and target triple. Alpine `ninja-build=1.13.2-r1` is the pinned build-only Ninja.

Chromium's Linux x64 compiler is a glibc host program. The builder copies a
pinned Debian runtime to run it on Alpine. `clang.cfg` and `clang++.cfg` target
`x86_64-unknown-linux-musl`, select compiler-rt and LLD, and map Chromium's
x86_64 GNU compiler-rt builtins to the musl target path. These changes let the
compiler emit musl ELF files; the compiler's own glibc libraries must not enter
the staged browser. Gate B and the ELF audit check the musl interpreter and GNU
runtime exclusion. The Chromium source tree supplies libc++, libc++abi, and
libunwind through the normal `use_custom_libcxx` GN path. No Laputa external
C++ archives are selected.

The archive does not currently supply sanitizer interface headers. The normal
headless profile uses trap-only hardening rather than a sanitizer runtime.
Alpine's `cr148-v8-no-san-trap.patch` removes V8 callback registration, and
`musl-v8-sanitizer-header.patch` removes its now-unused sanitizer include.
A sanitizer profile would require matching headers and runtimes plus runtime
verification; the present profile does not establish that support.

When updating Chromium, read `tools/clang/scripts/update.py` in that exact
Chromium tag, pin its LLVM revision and archive digest, and repeat the direct
compiler, C++ runtime, GN graph, and browser ELF checks. Upstream LLVM archives
for other host architectures cannot be inferred from the Linux x64 archive.
