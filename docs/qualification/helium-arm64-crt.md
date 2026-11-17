# Helium arm64 startup objects

At chromium-build `edf9e844ed8d28e0f69840482b9ccef7b3a9d613`, the r7 package purity report found GCC provenance in `chrome_sandbox`, `helium`, and `helium_crashpad_handler` (23 checked, 3 impure). The Helium target patch supplies `--target=aarch64-alpine-linux-musl`; LLVM 23.1.5's bundled configuration is named `aarch64-unknown-linux-musl.cfg`, so Clang does not load its CRT search path for that target. The linker then selects `/usr/lib/Scrt1.o`, owned by Alpine `musl-dev-1.2.6-r3`, whose `.comment` names GCC 15.2.0.

The locked LLVM release `10dbf21a8bc2ec9a7f60d2f15eed2307c52c290f` bundles musl 1.2.6 CRTs built from musl source with Clang. Its `scripts/build.sh` builds and installs `Scrt1.o`, `crt1.o`, `rcrt1.o`, `crti.o`, and `crtn.o` into `lib/musl`; its `sources.lock.json` pins musl 1.2.6. The builder adds a target-specific Clang configuration containing only `-B<CFGDIR>/../lib/musl`, preserving the Alpine target's existing library search order and using the pinned CRTs. No provenance section is removed.

The following ran on the exact image `helium-builder:0.20.3.1-arm64` (image `sha256:08913849f0646fb5737209f72453753240b826ce17c4c500fa92522065eb40c7`). `check-purity.sh` was the unchanged `llvm-prebuilt-musl/scripts/check-purity.sh`, SHA-256 `c21957a0fe32f99147cf9ead9672cc299a5b07654684624dca352b9e4f607fcb`. From this worktree, with that repository at `/Users/josh/repos/laputa-systems/llvm-prebuilt-musl`:

```sh
mkdir -p .work/tmp/helium-crt
docker run --rm --cpus=2 --memory=4g --entrypoint /bin/sh \
  --mount type=bind,source="$PWD",target=/repo,readonly \
  --mount type=bind,source="$PWD/.work/tmp/helium-crt",target=/scratch \
  --mount type=bind,source=/Users/josh/repos/laputa-systems/llvm-prebuilt-musl/scripts/check-purity.sh,target=/purity/check-purity.sh,readonly \
  -e TMPDIR=/scratch helium-builder:0.20.3.1-arm64 -c '
    set -eu
    mkdir -p /scratch/before /scratch/after
    printf "int main(void) { return 0; }\n" > /scratch/probe.c
    clang --target=aarch64-alpine-linux-musl -v /scratch/probe.c -o /scratch/before/probe 2>/scratch/before.link
    set +e
    bash /purity/check-purity.sh /scratch/before aarch64 /opt/chromium-llvm/bin
    rc=$?
    set -e
    echo before_gate_exit=$rc
    test "$rc" -eq 1
    sh /repo/scripts/helium-crt.sh /opt/chromium-llvm
    clang --target=aarch64-alpine-linux-musl -v /scratch/probe.c -o /scratch/after/probe 2>/scratch/after.link
    bash /purity/check-purity.sh /scratch/after aarch64 /opt/chromium-llvm/bin
    llvm-readelf -p .comment /scratch/before/probe /scratch/after/probe
    python3 /repo/tests/test_helium_crt.py
    /scratch/after/probe
    echo probe_exit=$?
    grep "ld.lld" /scratch/before.link /scratch/after.link
  '
```

The command exited 0. The first gate exited 1 with `GCC: (Alpine 15.2.0) 15.2.0`, 1 checked and 1 impure; the second exited 0 with 1 checked and 0 impure. The before link used `/usr/lib/Scrt1.o`, `/usr/lib/crti.o`, and `/usr/lib/crtn.o`; the after link used the corresponding `/opt/chromium-llvm/bin/../lib/musl/` objects. The after `.comment` contained LLD and Clang 23.1.5, and the executable exited 0. The focused test passed for both Clang drivers, including constructor and argument handling.

The r7 work volume was mounted read-only for inspection. No browser relink, new builder image, package qualification, or full rebuild was performed; those remain for the parent to authorize and verify.
