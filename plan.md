# Hermetic musl ungoogled-chromium build implementation specification

## 1. Purpose

Build a repeatable, Docker/OrbStack-contained ungoogled-chromium environment that:

- builds natively for Linux musl;
- uses the Laputa LLVM 22.1.8 musl toolchain for every C and C++ compilation, archive operation, and link;
- produces Chromium-owned ELF artifacts with no glibc, libstdc++, libgcc, or other GNU runtime dependency, with one narrowly audited exception for the copied stock-Alpine FFmpeg dependency closure;
- uses direct, checksummed source archives rather than Git clones or Chromium `depot_tools` checkouts;
- persists the prepared source tree and Ninja output in Docker named volumes, never performance-sensitive host bind mounts;
- minimizes source, output, environment, and runtime-bundle disk usage where doing so does not make the build substantially more fragile;
- proves a headless automation browser first, before adding interactive Wayland and ALSA support;
- remains architecture-ready for native amd64 builds while using native arm64 under macOS/OrbStack for the first proof of concept.

The browser is an automation browser first. The first functional artifact must be the full `chrome` target running Chromium's modern headless mode, not the legacy `headless_shell`, with raw Chrome DevTools Protocol (CDP), the bundled DevTools frontend, local extensions, downloads, screenshots, networking, cookies/storage, and JavaScript/DOM inspection working.

The first functional milestone deliberately excludes interactive Wayland and ALSA. Those become a second build profile after headless Chrome works. This is an iteration-speed choice, not a change to the eventual browser goal.

## 2. Definitions and hard boundaries

### 2.1 Meaning of hermetic

Hermeticity has two boundaries:

1. The builder image is assembled from a base image digest, exact package versions, checksummed configuration archives, and a checksummed LLVM artifact. Once built, its image digest is the immutable environment identity.
2. The prepared source tree is assembled from checksummed source/configuration inputs and a committed patch set. Once prepared, all configuration, compilation, testing, auditing, and packaging commands run with container networking disabled.

The initial project does not promise:

- bit-for-bit reproducible Chromium binaries;
- an indefinitely reproducible cold image build after Alpine removes old package files from its repositories;
- a locally archived copy of every Alpine APK;
- a source tree that can be updated in place without invalidating its identity.

A fully cached builder image plus a stamped prepared-source volume is the practical hermetic boundary. Package versions must still be locked so that an uncached rebuild fails on unavailable or changed inputs instead of silently drifting.

### 2.2 Meaning of no GNU

The no-GNU requirement remains strict for the compiler path and Chromium-owned output:

- No GCC or G++ driver may compile or link Chromium code.
- No GNU `ar`, `ranlib`, `nm`, `strip`, `objcopy`, or linker may process Chromium outputs.
- Chrome, Chromium-built helper executables, and Chromium-built shared libraries may not directly depend on glibc, libstdc++, libgcc, or another GNU runtime.
- Link command logs must prove that no static libstdc++ or libgcc archive was embedded either.
- The custom LLVM toolchain must itself retain its existing musl-only dynamic dependency property.

Incidental builder utilities such as Bash, Bison, gperf, tar, or findutils are allowed when Chromium's build scripts need them. Alpine system libraries are selected by compatibility and runtime linkage rather than by auditing the authorship of every source file in those libraries. The prohibition is specifically against the GNU compiler/binutils/runtime path, not against every utility ever published by a GNU project.

Rust is an explicit, separately approved compiler path. Chromium's Rust crates use the pinned Alpine Rust toolchain selected below. Any crate build script that invokes a C/C++ compiler must inherit the Laputa `CC`, `CXX`, and LLVM-binutils environment. Rust compiler provenance is recorded, and Rust-linked final artifacts are subject to the same ELF/runtime audit as C++ output.

The shipped-artifact boundary does not claim that every host-owned library loaded at runtime is free of GNU runtime dependencies. This distinction is material: Alpine's Mesa and Rust packages currently use GNU runtime libraries internally. A stricter future requirement that the complete browser process closure load no GNU code would require custom LLVM-built Mesa, Rust, and other host packages and is outside v1.

Stock Alpine FFmpeg is the sole shipped-runtime exception. The package lock currently resolves FFmpeg `8.1.2-r0`; relevant Alpine 3.24 aarch64 package metadata shows:

- `ffmpeg-libavcodec` depends on x265 `4.1-r0` and libjxl `0.11.2-r1`, which depend on `libstdc++.so.6`;
- `ffmpeg-libavcodec` depends on rav1e `0.8.1-r0`, which depends on `libgcc_s.so.1`;
- `ffmpeg-libavutil` depends on X11, VA-API, VDPAU, and DRM libraries;
- `ffmpeg-libavformat` brings a broad TLS/network/media dependency closure.

The selected practical resolution is to copy the complete, exact stock-Alpine FFmpeg closure, including `libstdc++.so.6`, `libgcc_s.so.1`, and otherwise-disabled X11/VA-API/VDPAU dependencies when the package graph requires them. This exception is acceptable only because Chromium consumes FFmpeg through its C shim/API and Alpine already proves the combination of Chromium's custom libc++ with its system FFmpeg package.

Here “complete closure” means every recursively resolved ELF `DT_NEEDED` dependency plus a reviewed set of modules/data that FFmpeg actually loads in the tested Chromium path. It does not mean copying optional Mesa/VA-API drivers, command-line FFmpeg tools, documentation, or every package-level recommendation merely because Alpine groups them with FFmpeg.

The closure also includes OpenSSL through Alpine `libavformat`. Chromium itself continues using bundled BoringSSL. Loading both implementations is allowed only inside this FFmpeg exception and requires dynamic-symbol/interposition checks plus an HTTPS regression test; it is not permission to replace BoringSSL or link Chrome directly to OpenSSL.

Containment rules are mandatory:

- glibc remains forbidden everywhere; the exception does not permit `libc.so.6`;
- GCC/G++, GNU binutils, libstdc++, and libgcc remain forbidden in every Chromium compile/link command;
- Chrome or another Chromium-owned ELF may directly need `libavcodec`, `libavformat`, or `libavutil`, but may not directly need `libstdc++` or `libgcc_s`;
- every allowed path to `libstdc++`, `libgcc_s`, X11, VA-API, VDPAU, OpenSSL, or another exception library must pass through a pinned `libav*` root in the FFmpeg closure;
- a non-FFmpeg private library that independently needs an exception soname is a failure, even if the same file is already present for FFmpeg;
- exception libraries are copied unmodified from exact locked APKs; never delete `DT_NEEDED` entries, patch out unresolved symbols, or pretend a partial closure is valid;
- the dependency report preserves every exception path and labels the package/version/license that introduced it;
- expanding the exception to another subsystem requires a plan change, not an allowlist-only edit.

This deliberately trades runtime-bundle size and purity for iteration speed. A future minimal LLVM-built FFmpeg stack can remove the exception after the browser works, but it is not v1 work.

### 2.3 Source-control and mount boundary

- Never clone Chromium, ungoogled-chromium, portablelinux, aports, copium, or the LLVM project.
- Do not install or invoke `depot_tools`, `gclient`, or Chromium's toolchain download scripts.
- Download release/commit archives or individual pinned files only.
- Do not use a bind mount for source or `out/`.
- The small repository build context is copied into the builder image when the image is built.
- Docker named volumes hold all hot build state.
- Host export uses `docker cp` from a temporary container attached to the named volume.

## 3. Why upstream portablelinux is not the build environment

`ungoogled-chromium-portablelinux` is useful for its Linux-specific source patches and as a reference for the ungoogled source pipeline. It is not the environment to inherit wholesale because it currently:

- builds on Debian rather than musl;
- installs a broad X11, GTK, Qt, PulseAudio, PipeWire, VA-API, printing, and desktop-integration dependency set;
- downloads or builds Chromium's LLVM/Rust toolchains instead of using the Laputa toolchain;
- enables Debian sysroots;
- is oriented around AppImage and portable desktop packaging;
- includes features outside the automation-first scope.

Use its pinned source patch series, but do not import its Dockerfile, Debian package list, AppImage scripts, sysroot behavior, GN flag bundle, or toolchain setup.

Alpine's Chromium package is the primary musl reference. It supplies a current musl patch stack, arm64 fixes, Rust compatibility fixes, system-library replacement logic, and a known working Chromium 150 build on Alpine 3.24.

## 4. Pinned input set

All values below live in one committed machine-readable lock file. The lock also records the actual archive filename, URL, size, digest algorithm, digest, upstream project, and provenance note. Scripts must never scrape “latest” releases.

### 4.1 Chromium and ungoogled configuration

- Chromium version: `150.0.7871.114`.
- Ungoogled release: `150.0.7871.114-1`.
- Chromium source archive: Alpine's trimmed `chromium-150.0.7871.114-linux.tar.xz` from `chromium-linux-tarballs`.
- Chromium archive SHA-512: `c6ab88182810bbf9423f01c1c4d02abfb3dcec8c182095ddfae5a49bb3817aef21a43eb743dcc95a6418ffaa54f3a8a4686795fb2e7be67f4c3d0ab489f0ee66`.
- Ungoogled common commit: `2d89b04e1b68385c9086efab0df1e3679b35246e`.
- Portablelinux commit for the matching release: `0033e274f91ec6aa57a36a486f41f46d801e381d`.
- Alpine `3.24-stable` Chromium patch snapshot: `bc56128509194816d5cd3441d17c20ca2d71cc68`.
- Copium series: tag `150.0`, with its archive hash copied from the pinned Alpine APKBUILD.

The trimmed Chromium archive is preferred because it omits multi-gigabyte material that is irrelevant here, including embedded Git metadata, Debian sysroots, bundled LLVM/GCC trees, and bundled toolchain binaries. The upstream archive checksum is mandatory because this adds a third-party repackaging trust boundary.

### 4.2 Alpine base and packages

- Base release: Alpine `3.24.1`.
- Pin the multi-architecture base reference and resolve/record the arm64 and amd64 manifest digests independently.
- Resolve direct and transitive APK versions separately for aarch64 and x86_64.
- Commit the resulting installed-package manifest and compare it during image validation.
- Use only Alpine 3.24 repositories. Do not mix edge packages into the environment.

Alpine 3.24 provides the matching Chromium 150 generation of GN, Rust, samurai, FFmpeg, and related libraries. The current lock resolves Rust `1.96.0-r0`; that compiler package itself depends on Alpine GCC, libstdc++, and libgcc in the builder, which is the explicitly approved Rust-toolchain exception described above. The custom C/C++ compiler remains LLVM 22.1.8 even if Alpine packages compiler-adjacent libraries built with another LLVM 22 patch release.

Do not request Alpine `build-base`, GCC, G++, binutils, Clang, or LLD as generic build dependencies. Install individual development packages. The approved Alpine Rust package currently pulls GCC/libstdc++/libgcc transitively into the builder; retain them only because APK dependency resolution requires them. Do not add their directories ahead of `/opt/llvm-musl` in `PATH`, do not set any compiler variable to them, and fail if a Chromium compile/link command uses them. A transitive builder runtime may enter the shipped bundle only through the exact FFmpeg-exception closure.

Builder tools include only what the source graph needs, grouped approximately as:

- source and scripting: Bash, ca-certificates, curl, patch, Python, Perl, tar, xz, zstd;
- generators: Bison, Flex, gperf, Go, Node, GN, samurai;
- Rust: Alpine Rust, Cargo components required by Chromium, rust-bindgen, and rustfmt if the source graph requires it;
- native platform headers: musl-dev, Linux headers, pkgconf, NSS/NSPR, GLib/DBus where structurally required;
- selected system libraries and their `-dev` packages;
- test-time Mesa llvmpipe runtime in the builder image, even though it is not copied into the portable browser bundle.

Each architecture package lock has two explicit sections:

- `world`: the intentionally requested `name=version` packages;
- `resolved`: the complete sorted `apk info -v` result, including transitive packages.

The Dockerfile installs the exact `world` entries, then compares the resolved installed set byte-for-byte with `resolved`. This permits Alpine's solver to install dependencies while making any transitive change a hard failure. Lock regeneration is a separate maintainer operation that runs against the pinned base/repositories and produces a reviewable old/new package diff; an ordinary image build never regenerates its own lock.

### 4.3 Laputa LLVM toolchain

Use the release artifacts from `laputa-systems/llvm-prebuilt-musl`:

| CLI architecture | Linux architecture | Chromium CPU | LLVM archive SHA-256 |
| --- | --- | --- | --- |
| `arm64` | `aarch64` | `arm64` | `675f9cf871313a5672a63882d4d30dd6dd55df0aa9caee70970542eb03a23da3` |
| `amd64` | `x86_64` | `x64` | `ac0bd443a1933bbd2c0efbedf6ebc97ff8ca2469e5ba65eadb966fb75f65dd1c` |

Extract to `/opt/llvm-musl`. The environment sets absolute paths for:

- `CC=/opt/llvm-musl/bin/clang`
- `CXX=/opt/llvm-musl/bin/clang++`
- `AR=/opt/llvm-musl/bin/llvm-ar`
- `RANLIB=/opt/llvm-musl/bin/llvm-ranlib`
- `NM=/opt/llvm-musl/bin/llvm-nm`
- `STRIP=/opt/llvm-musl/bin/llvm-strip`
- `OBJCOPY=/opt/llvm-musl/bin/llvm-objcopy`
- the corresponding `BUILD_*` variables used by Chromium's host toolchain

The builder image must fail validation unless `clang --version`, `ld.lld --version`, the target triple, Clang resource directory, libc++ headers, compiler-rt builtins, libc++.a, libc++abi.a, and libunwind.a all match the expected artifact.

The toolchain currently installs `clang.cfg`/`clang++.cfg` defaults for LLD and the C++ ABI/unwind libraries. Treat these files as inputs: record their hashes, print their contents during validation, and account for their injected flags when constructing Chromium's runtime-library config. Do not add duplicate `-lc++abi`/`-lunwind` flags merely because the same libraries are named in this plan.

## 5. Proposed repository structure

Keep the implementation small and explicit:

```text
Dockerfile
chromium-build
config/
  inputs.lock
  packages.arm64.lock
  packages.amd64.lock
  profiles/
    headless-debug.gn
    wayland-debug.gn
patches/
  chromium-runtime/
  conflicts/
scripts/
  container-entrypoint.sh
  prepare-source.sh
  generate-args.sh
  verify-environment.sh
  verify-toolchain.sh
  verify-system-libraries.sh
  verify-elf.sh
  package-runtime.sh
tests/
  cdp/
  extension/
  fixtures/
  smoke/
```

`chromium-build` is the only supported host entrypoint. Container scripts are not expected to infer host paths or call Docker recursively.

Avoid framework code, extra package managers, YAML task runners, and generated wrapper layers. POSIX shell is preferred for orchestration unless Bash behavior is materially simpler and already required by upstream scripts. Python standard library code is appropriate for structured lock/stamp comparison and CDP pipe handling.

## 6. Image construction

Build a separate native image per architecture. A representative tag is:

```text
ungoogled-chromium-builder:150.0.7871.114-arm64-<environment-lock-hash>
```

Image construction performs these operations:

1. Select the pinned Alpine base by architecture-specific digest.
2. Configure only Alpine 3.24 repositories.
3. Install exact direct package versions and validate the full installed package manifest.
4. Download and verify the matching LLVM archive.
5. Extract LLVM to `/opt/llvm-musl` and remove its archive in the same image layer.
6. Download/verify or copy the small ungoogled, portable, Alpine patch, and copium configuration inputs into `/opt/chromium-inputs`.
7. Copy repository scripts, local patches, smoke tests, and fixtures into `/opt/chromium-build`.
8. Create a fixed non-root build user and an entrypoint that can initialize named-volume ownership without changing host files.
9. Run environment/toolchain validation during the image build.
10. Record the image input hash and package manifest under `/opt/chromium-build-metadata`.

Do not place Chromium source in an image layer. It belongs in the named volume so source preparation, patch iteration, and Ninja output do not duplicate enormous Docker layers.

“Architecture-ready for amd64” means the Dockerfile branches, input mappings, package lock, LLVM digest, GN CPU mapping, volume naming, and artifact naming exist and can be statically validated from arm64. It does not mean v1 runs an amd64 container under emulation. The host CLI compares the requested architecture to the native Linux architecture reported by the container runtime and refuses a mismatch. Native amd64 image execution/build validation waits for an amd64 Linux/OrbStack/Docker host.

## 7. Named-volume model and state machine

Use one volume per architecture:

- `ungoogled-chromium-work-arm64`
- `ungoogled-chromium-work-amd64`

Allow an explicit environment override for experiments, but never derive a volume from the current directory or mount a host path.

Suggested internal layout:

```text
/work/
  metadata/
    source-inputs.json
    source-complete.stamp
    patches.json
    phases/
  src/
  out/
    headless-debug/
    wayland-debug/
  stage/
    headless-debug/
  test-output/
    headless-debug/
```

Stamps are written atomically only after a phase succeeds. A partially downloaded archive, partially extracted source directory, or partially applied patch set never receives a completion stamp.

Preparation uses sibling temporary paths such as `src.prepare.<pid>` and atomically renames the completed tree to `src`. If an unstamped final `src` exists, `prepare` refuses and reports it instead of guessing whether it is reusable. Test and package staging use the same pattern: build into a temporary directory, validate it, then replace the profile's completed directory. Ninja's `out/` is the exception; it is intentionally resumable in place and is never transactionally replaced.

Separate source identity from profile identity:

- Source identity includes Chromium, ungoogled, portable, Alpine/copium, local patches, architecture-sensitive source transformations, and preparation script version.
- Environment identity includes the builder image digest, package lock, LLVM artifact, and architecture.
- Profile identity includes exact GN arguments and profile-owned source overlay files.

A profile change creates a new `out/<profile>` without duplicating `src`. An environment or source mismatch causes an immediate refusal with a field-by-field difference. It must not delete or mutate the volume automatically. The user may select another volume name or explicitly run `reset --yes`.

Do not add ccache or sccache initially. The persistent `out/` directory is the primary incremental cache. Compiler caching can be reconsidered only after measuring rebuild patterns and accounting for duplicated object storage.

## 8. Host command contract

The host CLI defaults to `--arch arm64` and `--profile headless-debug`. Every command prints the selected image, volume, architecture, profile, source identity, and networking mode before starting.

### `image`

- Builds or locates the immutable builder image.
- Accepts `--arch arm64|amd64`.
- Refuses to execute a non-native architecture in v1; it never enables QEMU/Rosetta emulation implicitly.
- Does not create or change the work volume.
- Prints the final image digest and input-lock hash.

### `preflight`

- Runs after `image` and before `prepare`; it does not require Chromium source.
- Audits the complete `DT_NEEDED` closure of every proposed system library using the installed Alpine packages.
- Classifies every soname as private, host-owned, FFmpeg-exception, or forbidden using the same policy later used by packaging.
- Prints every path to GNU, X11, audio, VA-API, VDPAU, TLS, and other normally excluded libraries, marking each as a valid FFmpeg exception or a failure.
- Records a content-addressed completion report under the environment identity.
- Is a hard prerequisite for `prepare` and `poc`.
- Accepts GNU/X11/VA-API/VDPAU exception paths only when they are descendants of the exact locked stock-Alpine `libav*` roots.
- Fails if the exception leaks into a different candidate library, if any closure entry is unclassified, or if glibc appears anywhere.

### `prepare`

- Creates/initializes the named volume if needed.
- Runs with network access.
- Downloads only the Chromium source archive; small patch/configuration inputs should already be in the image.
- Verifies the source archive before extraction.
- Prepares the tree transactionally.
- Deletes the compressed archive after successful extraction to minimize disk use.
- Is idempotent when stamps match.

### `poc`

- Requires a prepared source tree.
- Runs with `--network=none`.
- Executes environment probes, GN generation, small compile/link targets, and the Chrome dry-run.
- Never starts the full Chrome build.

### `build`

- Requires a successful PoC for the same environment/source/profile identity.
- Runs with `--network=none`.
- Builds only `chrome` and implicit dependencies. Do not request `chromedriver`, `headless_shell`, upstream unit tests, packaging targets, or unrelated examples.
- Accepts `JOBS` and an optional Ninja load-limit override. Default jobs may follow the CPU count visible inside the container. Do not enforce guessed memory/disk thresholds; print available volume space for observability and preserve normal OOM/ENOSPC failures for diagnosis.
- Leaves partial Ninja output intact after failure so rerunning resumes normally.

### `test`

- Runs repository-owned smoke/CDP/extension tests offline.
- Runs Chrome as a non-root user and never adds `--no-sandbox`.
- Writes logs, screenshots, and downloads under `test-output/<profile>`.

### `package`

- Is unavailable until the functional browser and ELF audits pass.
- Builds a staging directory inside the volume.
- Never copies Mesa drivers, fonts, or a host musl loader into the bundle.

### `export`

- Produces the final `tar.zst`, checksums, and adjacent metadata.
- Creates a temporary container with the named volume mounted, keeps it running while the mount is active, uses `docker cp` to copy a completed export directory to the host, and removes the temporary container afterward.
- Does not bind-mount an output directory.

### `status`, `clean`, and `reset`

- `status` reports phase stamps, identities, tool versions, source/output/stage sizes, and incomplete phases.
- `clean --profile <name>` removes only that profile's `out`, stage, and test output.
- `clean --all-profiles` preserves prepared source.
- `reset` refuses without `--yes`, then removes the complete architecture volume.

## 9. Source preparation pipeline

Preparation is deterministic and uses this exact conceptual order:

1. Download the trimmed Chromium archive to a temporary path in the volume.
2. Verify size and SHA-512 before extraction.
3. Extract into a temporary source directory and validate expected Chromium version files and top-level layout.
4. Remove the archive after successful extraction.
5. Run ungoogled binary pruning from the pinned common configuration.
6. Apply ungoogled common patches in their declared order.
7. Apply the matching portablelinux source patches in their declared order:
   - Node version-check adjustment;
   - arm64 compilation fix;
   - OAuth2 client-switch default behavior.
8. Apply the pinned Alpine base patch list and copium patch list in APKBUILD order.
9. Apply committed repository-owned conflict/runtime patches.
10. Apply ungoogled domain substitution last, so distro patches match their original Chromium contexts and newly introduced domains are transformed too.
11. Install the repository-owned GN smoke target/fixture overlay.
12. Remove source files for libraries selected for system unbundling only after GN replacement files have been installed and validated.
13. Write patch provenance, tree identity, and the atomic completion stamp.

The pinned Alpine base patch inventory is:

```text
0001-hotfix-ignore-a-new-warning-in-rust-1.89.patch
compiler.patch
disable-dns_config_service.patch
disable-failing-tests.patch
fc-cache-version.patch
fix-ffmpeg-codec-list.patch
fstatat-32bit.patch
gdbinit.patch
generic-sensor-include.patch
headless-shell-no-license.patch
musl-sandbox.patch
musl-tid-caching.patch
net-test-no-vpython.patch
net-test-pyws3-py3.12.patch
no-execinfo.patch
no-mallinfo.patch
no-res-ninit-nclose.patch
no-sandbox-settls.patch
partalloc-no-tagging-arm64.patch
pvalloc.patch
temp-failure-retry.patch
```

The pinned copium `150.0` inventory is:

```text
cr133-ffmpeg-no-noh264parse.patch
cr138-node-version-check.patch
cr140-musl-prctl.patch
cr143-libsync-__BEGIN_DECLS.patch
cr145-iwyu-dev_t.patch
cr145-musl-unfortify-SkDescriptor.patch
cr146-swiftshader-unfortify-memset-memcpy.patch
cr146-unfortify-blink-display_item_list.patch
cr147-is-musl-libcxx.patch
cr147-simdutf-8.0-base-char.patch
cr148-rust-1.95-bytemuck.patch
cr148-v8-no-san-trap.patch
cr149-musl-alloc-shim-dispatch.patch
cr149-rust-toolchain-var.patch
cr149-unbundle-minizip-undo-unicode.patch
cr150-empty-ar.patch
cr150-ffmpeg-no-agtm.patch
cr150-no-sysroot-modules.patch
```

Do not blindly apply the entire inventory. Create a committed disposition manifest with exactly one state per patch:

- `apply`: required by the selected Chrome graph or runtime;
- `superseded`: behavior is provided by another pinned patch, with the replacement named;
- `not-applicable`: affects only an excluded target such as upstream tests, GDB helpers, or legacy `headless_shell`, with affected paths recorded.

Preparation applies only `apply` entries, in original APKBUILD order. Every inventory entry must appear in the disposition manifest; an unknown new patch or missing old patch is fatal. This keeps test/headless-shell packaging changes out of the source tree without silently discarding musl build knowledge. The implementation never invokes Alpine's APK build/package functions. Copium patches come from the pinned archive, not a moving branch.

Every patch application logs its source project, upstream path, upstream commit/tag, checksum, strip level, and result. Local conflict patches must state which two upstream patch sets conflicted and why the chosen resolution preserves both intents. Do not silently edit upstream patch files during preparation.

Before applying a layer, dry-run its patches against the output of the previous layer. If portablelinux and Alpine/copium contain identical or semantically superseding fixes, do not accept `patch`'s “already applied” heuristic as success. Commit a reconciliation record that names the superseded patch, retains its provenance/checksum, explains which later patch provides the behavior, and lists it as deliberately satisfied without a second application. Any other failure or fuzz beyond the committed allowance is fatal. Patch offsets/fuzz are recorded because they are relevant to future version bumps.

The trimmed tarball may already omit some binaries named by ungoogled's pruning list. Treat “already absent because the pinned archive excludes it” as valid only for a committed allowlist generated during initial integration. Any newly missing pruning target outside that allowlist is a source-integrity failure.

## 10. Custom GN toolchain and C++ runtime

Use Chromium's Linux unbundled/custom toolchain mechanism for both default and host toolchains, with all environment variables set to absolute `/opt/llvm-musl` paths.

Required high-level GN/toolchain behavior:

- `is_clang=true`
- `is_musl=true`
- `use_sysroot=false`
- `use_lld=true`
- target and host are the same native architecture
- no Chromium-downloaded LLVM, Rust, sysroot, Node, GN, or binary tool
- no QEMU or implicit cross-architecture execution

### 10.1 External static libc++ decision

The toolchain's prebuilt static libc++, libc++abi, and libunwind are mandatory. Chromium's in-tree libc++ must not be compiled.

This requires a small, explicit Chromium build-configuration patch. Introduce a version-local argument such as `use_laputa_libcxx=true` rather than overloading an unrelated Chromium flag or relying only on ambient `CXXFLAGS`. With that argument enabled:

- add `-nostdinc++` and the toolchain libc++ header directory;
- prevent `//buildtools/third_party/libc++` and libc++abi targets from entering common dependencies;
- add `-nostdlib++` and the toolchain archive search path/order to C++ executable/shared-library links that need the runtime;
- keep compiler-rt as the compiler runtime;
- disable any `use_safe_libstdcxx` fallback;
- preserve Chromium's allocator/export requirements for its non-component Linux executable;
- ensure host generators built by the same toolchain also receive the external C++ runtime.

The library order must be derived from the toolchain's validated invocation and the existing `clang++.cfg`. Linker response files must show a consistent, intentional archive order without accidental double injection. Static archives may appear in an explicit LLD rescan group if required for circular references; “exactly once” is not required when the linker semantics require a group. Do not introduce a synthesized shared libc++.

The patch must also supply the generated/configured libc++ headers expected by the toolchain artifact, including its `__config_site`, rather than mixing toolchain implementation archives with Chromium's in-tree libc++ headers. Compile a header/runtime revision probe that reports `_LIBCPP_VERSION` and exercises ABI-bearing types before GN generation.

Chromium 150 normally makes libc++ shared in component builds. Therefore the profile is deliberately monolithic:

- `is_component_build=false`
- `is_debug=true`
- `symbol_level=0`
- `blink_symbol_level=0`
- `v8_symbol_level=0`

If the static external runtime cannot pass the PoC and later monolithic Chrome link, this is a hard stop. The allowed response is to diagnose and report the incompatibility. The implementation must not quietly compile Chromium's libc++, link libstdc++, derive a shared runtime, or switch to component mode.

### 10.2 Compile-command enforcement

After GN generation, inspect the Ninja graph and compilation database. Fail on any Chromium C/C++ command containing:

- `gcc`, `g++`, or an Alpine Clang path;
- GNU `ar`, `nm`, `ranlib`, `strip`, or `ld`;
- `/usr/lib/gcc`;
- `-lstdc++`, `-lgcc`, `-lgcc_s`, or static libgcc/libstdc++ archive paths;
- Chromium in-tree libc++ source paths in scheduled compilation steps;
- Chromium toolchain or sysroot download/update scripts.

Do not reject `.note.gnu.build-id` merely because of its historical ELF section name; it is not a GNU runtime dependency.

## 11. System-library policy

Prefer Alpine's proven system replacements where they reduce bundled compilation without creating known Chromium ABI problems. Initial candidates are:

- brotli;
- crc32c;
- dav1d;
- double-conversion;
- FFmpeg;
- FLAC;
- fontconfig;
- FreeType;
- HarfBuzz;
- libdrm;
- libjpeg-turbo;
- libwebp;
- libxml2;
- libxslt;
- Opus;
- zlib;
- zstd.

This list is a candidate set, not a blanket approval. `preflight` computes each candidate's full runtime closure before source preparation:

- If a nonessential candidate reaches a forbidden runtime, do not unbundle it; leave Chromium's bundled copy to be compiled by the Laputa toolchain.
- If a candidate reaches only explicitly host-owned libraries, its GN/pkg-config integration may remain while packaging records the host requirement.
- If a required candidate reaches a forbidden runtime, stop at preflight and require a policy/input change.
- FFmpeg is the single exception: paths rooted at the locked Alpine `libav*` packages may reach the committed FFmpeg-exception closure.

Use Chromium's `build/linux/unbundle/replace_gn_files.py` and the pinned Alpine preparation logic rather than inventing new pkg-config shims where upstream ones exist.

Do not initially unbundle tightly coupled or high-risk libraries such as:

- BoringSSL;
- ICU;
- Skia;
- V8;
- Chromium's allocator;
- protobuf/Abseil families unless the pinned Alpine recipe already proves the exact replacement safe for this configuration.

Chromium uses BoringSSL rather than OpenSSL. There is no plan to compile or substitute OpenSSL. BoringSSL stays bundled because it is tightly coupled to Chromium.

Use stock Alpine FFmpeg. Chromium's bundled FFmpeg source must not be compiled. Use common-codec behavior from the locked Alpine FFmpeg packages; Widevine remains disabled. OpenH264 and WebRTC-specific Chromium features remain disabled even if similarly named libraries happen to exist inside FFmpeg's package closure.

“Common codecs” means the system-FFmpeg integration is configured to expose the normal H.264/AAC-capable browser codec set where the Alpine FFmpeg build supplies it, while still omitting Widevine/DRM. Record the resolved `ffmpeg_branding` and `proprietary_codecs` GN values in the profile audit. This is a technical capability decision, not a claim about distribution licensing in every jurisdiction.

Do not attempt to slim stock FFmpeg by deleting codec libraries from its closure. Shared Alpine `libavcodec`/`libavformat` packages encode their enabled features in `DT_NEEDED`; even codecs unused by the browser remain load dependencies. Any future reduction requires a separately configured FFmpeg build and a new locked artifact.

Mesa is different from ordinary unbundled libraries because its loader and DRI driver must remain version-compatible. Treat the entire driver-facing Mesa stack as host-owned for the portable artifact: Mesa EGL/GLES/GBM/GL loader libraries, DRI drivers, and their LLVM closure come from the target Alpine host. They are installed in the builder/test image for compilation and testing but excluded from the private bundle closure. `libdrm` may also remain host-owned to keep the graphics stack coherent.

This host-owned classification is also why Mesa's Alpine dependencies on libgcc/libstdc++ and X11/XCB do not cause a shipped-bundle audit failure in v1. They remain visible in the host dependency report. They would fail a future whole-process no-GNU or zero-X closure requirement.

Chromium's own ANGLE code is not the same thing as SwiftShader. Do not attempt to unbundle ANGLE or patch it out solely for scope reduction if the full `chrome` graph requires it. Disable ANGLE's Vulkan/SwiftShader outputs, prefer Chromium's native EGL path to the host Mesa stack where supported, and allow the smallest remaining ANGLE GL/EGL path if Chromium requires it. The runtime renderer test, not an assumed GN flag, determines whether the selected path reaches Mesa llvmpipe.

## 12. Build profiles

### 12.1 `headless-debug`: initial required profile

This profile exists solely to prove the automation browser with the smallest practical integration surface.

Required capabilities:

- full `chrome` executable;
- modern `--headless` mode;
- Ozone headless platform;
- CDP over remote-debugging pipe and TCP;
- bundled DevTools frontend resources;
- local/unpacked Manifest V3 extensions;
- navigation, JavaScript evaluation, DOM access, network events, cookies/storage, downloads, screenshots, console events, and target creation;
- en-US resources;
- Mesa EGL/GLES/GBM/libdrm;
- host-provided llvmpipe for displayless software rendering;
- normal Chromium user-namespace sandbox under a non-root user.

Explicit exclusions:

- Ozone Wayland and Ozone X11;
- GTK and Qt;
- ALSA, PulseAudio, and PipeWire;
- WebRTC and screen capture;
- VA-API, Vulkan, and SwiftShader;
- Widevine;
- Bluetooth, USB, smart cards, speech, remoting, and service discovery;
- physical printing, CUPS, print preview, and PDF viewer resources;
- Chrome Web Store integration;
- desktop notifications, keyrings/password-store integration, spellcheck dictionaries, crash reporting, and enterprise integration;
- chromedriver, legacy headless shell, and upstream unit-test suites;
- locales other than en-US.

When an exclusion lacks a supported GN flag, prefer omitting the related build/package target and runtime resource. Do not maintain a large invasive patch merely to prove the dead code is physically absent unless it materially removes a dependency. `gn gen --fail-on-unused-args` is mandatory; never keep aspirational or obsolete arguments in the profile.

PDF is not an acceptance capability. Do not test `Page.printToPDF`, package the PDF viewer, or install CUPS. If a small amount of PDF implementation remains structurally required by the full Chrome graph, removing it is not allowed to block the first browser.

The profile source file contains only arguments verified to exist in Chromium 150. At minimum, verify the effective values corresponding to this behavior matrix rather than assuming every desired capability has a same-named GN argument:

| Area | Required effective behavior |
| --- | --- |
| Toolchain | Clang/LLD, native musl, no sysroot, Laputa external libc++, compiler-rt |
| Build shape | debug, monolithic, zero symbol levels, no PGO/LTO/CFI/Chrome plugins |
| Ozone/UI | headless enabled; Wayland, X11, GTK, and Qt disabled |
| Audio/capture | ALSA, PulseAudio, PipeWire, and WebRTC capture disabled |
| Graphics | host Mesa/llvmpipe path; VA-API, Vulkan, and SwiftShader disabled |
| Media | locked stock-Alpine system FFmpeg with common codecs; Widevine disabled |
| Product | ungoogled defaults, local extensions and DevTools retained, en-US only |

For each row, the PoC stores both the controlling GN values and evidence from the generated target graph. A runtime-only command-line flag is not accepted as proof that an unwanted backend was excluded from compilation.

### 12.2 `wayland-debug`: follow-up profile

This profile starts only after `headless-debug` passes its full functional and ELF audits.

Differences from `headless-debug`:

- enable Ozone Wayland while retaining Ozone headless;
- enable ALSA;
- add Wayland client, xkbcommon, Mesa window-system, and ALSA development/runtime dependencies;
- add compile/link probes for Wayland and ALSA;
- add a real Wayland launch test where an appropriate compositor is available.

Continue to disable X11, GTK, Qt, PulseAudio, PipeWire, VA-API, Vulkan, and SwiftShader. The first Wayland profile may tolerate transitive X/XCB libraries brought in by Alpine packages, but Chromium must not build or select its X11 backend.

### 12.3 Later profiles

VA-API is a separate later profile because it adds libva, driver discovery, render-node access, sandbox interaction, and hardware-specific testing. Do not mix it into the initial Wayland work.

## 13. Proof-of-concept gates

The PoC is intentionally much cheaper than building Chrome. It must catch toolchain, runtime, patch, GN, and dependency failures early.

### Gate A: environment identity

- Architecture inside the container matches the selected native architecture.
- Builder image digest and package manifest match the locks.
- Network is absent for all PoC subprocesses.
- All expected source stamps match.
- `which` and resolved symlinks for compiler/binutils variables point under `/opt/llvm-musl`.
- System-library preflight exists for the exact environment/package policy, matches the committed FFmpeg-exception rules, and contains no unclassified required-library edge.

### Gate B: direct compiler/runtime probes

Compile and run small native probes covering:

- C compilation and link;
- C++ standard library containers/strings;
- exceptions and catch behavior;
- libc++abi RTTI/dynamic cast behavior;
- libunwind stack unwinding;
- thread-local storage;
- atomics and threads;
- creation and use of a static archive with LLVM ar/ranlib;
- creation of a shared object and executable with LLD;
- musl interpreter and allowed `DT_NEEDED` entries.

Compile/link separate pkg-config probes for the headless profile's Mesa EGL/GLES/GBM, libdrm, NSS where required, and stock Alpine FFmpeg. The FFmpeg probe exercises the exact headers and `libavcodec`/`libavformat`/`libavutil` linkage Chromium's system shim will use.

Add a renderer probe that creates a minimal surfaceless/headless EGL context using the same host Mesa packages intended for Chrome tests. This distinguishes “headers and libraries link” from “llvmpipe can actually initialize in the container.”

### Gate C: source and patch validation

- Expected Chromium version files match `150.0.7871.114`.
- Binary pruning completes.
- Every common, portable, Alpine/copium, and local patch is accounted for.
- Domain substitution completes without unprocessed entries.
- No forbidden bundled toolchain/sysroot binary remains selected by the build configuration.
- System-unbundled library GN replacements exist and their removed source trees are not referenced by GN.

### Gate D: GN generation

- Generate `out/headless-debug` with `--fail-on-unused-args`.
- Save the final canonical args listing.
- Assert target CPU, musl, no sysroot, custom host/default toolchains, LLD, monolithic debug, symbol levels, headless Ozone, no X11/Wayland/GTK/audio, stock system FFmpeg, and disabled optimization features.
- Run `gn check` only where it is useful and bounded; do not let a full unrelated upstream check become the PoC.

### Gate E: small GN build

Add a repository-owned target under a clearly named source overlay such as `//tools/hermetic_smoke` that:

- is built by Chromium's generated Ninja graph;
- uses generated Chromium build configuration headers;
- exercises C++ standard library, exceptions, TLS, and threads;
- links the profile's Mesa dependencies and system-FFmpeg shim where it can remain small;
- runs successfully inside the container;
- remains tiny and does not depend on `//base`, `//content`, or another large Chromium library merely for symbolism.

### Gate F: Chrome graph dry-run and audit

- Run a full Ninja dry-run of `chrome`.
- Generate/inspect commands for the graph.
- Prove all C/C++ commands use the custom LLVM tools.
- Prove Chromium's in-tree libc++ targets are not scheduled.
- Prove the generated graph uses the system-FFmpeg shims and does not schedule Chromium's bundled FFmpeg sources.
- Prove no forbidden compiler/runtime flags or archive paths appear.

Because a Ninja dry-run does not necessarily expand every response file until its generating edge has run, audit both `ninja -t commands chrome` and the response files/command descriptions that exist after the small GN build. Repeat the complete command/response-file audit after the actual Chrome build before declaring the compiler boundary proven.

Passing the PoC must never automatically launch the full build.

## 14. Full build behavior

The `build` command invokes samurai/Ninja for `chrome` only. It runs offline and uses the persistent profile output directory.

Behavior on failure:

- Preserve all completed object files and Ninja state.
- Print the failing command and the environment/source/profile identities.
- Do not re-run source preparation.
- Do not clean automatically after compiler, linker, OOM, ENOSPC, or interrupted-build failures.
- A rerun resumes through Ninja normally.

The final monolithic link is expected to be the slowest incremental operation. This is accepted because safely using the toolchain's static libc++ takes precedence over component-link speed. Wild, mold, or another experimental linker must not be introduced until LLD produces a working audited browser.

## 15. Functional headless tests

Tests use only repository-owned fixtures and local loopback services. No test requires Internet access, Google services, Puppeteer, Playwright, Selenium, or chromedriver.

### 15.1 Test server and profile isolation

- Start a Python standard-library HTTP server on loopback serving deterministic HTML, JavaScript, download, cookie, console, and extension-interaction fixtures.
- Create a fresh user-data directory under `test-output/headless-debug` for each test run.
- Run Chrome as the non-root build/test user.
- Do not pass `--no-sandbox`.
- Set only required deterministic flags such as headless mode, user-data-dir, no-first-run, and remote-debugging transport.

### 15.2 CDP pipe test

Use a small standard-library client for Chromium's NUL-delimited JSON remote-debugging pipe protocol over the required inherited file descriptors. Validate:

- browser version and protocol handshake;
- creation and attachment to a page target;
- navigation to the local fixture;
- JavaScript evaluation;
- DOM query and mutation observation;
- console event receipt;
- network request/response events;
- cookie set/read and local/session storage;
- screenshot creation with a valid, non-empty PNG;
- download behavior and exact downloaded contents;
- target cleanup and clean browser shutdown.

### 15.3 CDP TCP test

- Start Chrome with a loopback-only remote debugging port.
- Verify `/json/version` and target enumeration.
- Connect using a raw WebSocket client available from the already installed Node runtime or a small repository-owned implementation; do not add an npm dependency.
- Repeat a minimal navigation/evaluation/screenshot subset.
- Ensure the endpoint is not bound to a non-loopback interface by default.

### 15.4 Extension test

Ship a tiny Manifest V3 unpacked extension fixture containing no external resources. Validate:

- `--load-extension` loads it in modern headless mode;
- its service worker starts;
- a content script modifies or reports data from the local fixture page;
- CDP can observe the extension target/context;
- no Chrome Web Store or Google update endpoint is required.

### 15.5 DevTools frontend test

- Verify bundled DevTools frontend resources exist in the output.
- Resolve/open the frontend URL associated with a local target.
- Confirm its primary HTML/JavaScript resources load without an external network request.
- Interactive human use is not automated beyond resource loading and protocol attachment.

### 15.6 System-FFmpeg containment and media tests

Use tiny, checksummed, locally served media fixtures with documented provenance. Include at least one H.264-in-MP4 video and one AAC-bearing fixture so the “common codecs” decision is tested rather than inferred from GN arguments.

Validate:

- an HTML media element reaches usable metadata and decoded-frame states;
- a decoded video frame can be drawn to canvas and produces non-empty pixels;
- repeated create/play/seek/destroy cycles do not crash the browser, GPU process, or media process;
- codec success/failure events and console output are captured through CDP;
- Chromium reports the expected codec support through `canPlayType`/MediaCapabilities where available;
- `/proc/<pid>/maps` and the dependency audit show the locked Alpine `libav*` libraries and expected exception libraries, not Chromium's bundled FFmpeg output or an untracked host copy;
- no C++ object, exception, allocator, or ownership crosses a newly invented C++ shim boundary; Chromium continues to use its existing system-FFmpeg C interface;
- the presence of libstdc++/libgcc for FFmpeg does not change the custom-libc++ identity checks for Chrome;
- the presence of OpenSSL for `libavformat` does not replace/interpose Chromium's BoringSSL network path; a local HTTPS navigation succeeds before and after media decoding using a pinned test certificate/SPKI allowance.

Generate a dynamic-symbol intersection report between Chrome and every FFmpeg-exception DSO. Highlight allocator operators, `__cxa_*`, `_Unwind_*`, OpenSSL/BoringSSL-like symbols, and any default-visible duplicate. Do not assume every duplicate is fatal—Chromium intentionally exports allocator symbols for loaded libraries—but require the initial baseline to be reviewed, stored, and unchanged on rebuild. A new high-risk collision after a package/version change invalidates preflight.

Do not require physical audio output in `headless-debug`; ALSA is intentionally disabled. Codec parsing/decoding is the acceptance target. Actual ALSA output is tested only in `wayland-debug`.

### 15.7 Rendering and sandbox tests

- Use Mesa llvmpipe installed in the builder/test image.
- Verify Chrome reports a usable software GL renderer rather than SwiftShader.
- Capture `SystemInfo.getInfo`/GPU diagnostics and require the reported renderer/vendor path to identify Mesa llvmpipe (or the pinned Alpine naming equivalent), not only a successful screenshot.
- Verify screenshot pixels are non-empty and deterministic enough for a coarse sanity assertion, not a brittle pixel-perfect upstream test.
- Confirm the browser and renderer processes start without `--no-sandbox`.
- Fail clearly if the runtime does not allow unprivileged user namespaces. Do not add a setuid sandbox fallback.

## 16. ELF, link, and dependency audits

Use LLVM readelf/readobj/nm/strings for final audits. Resolve every executable and shared library produced for the runtime stage.

Hard failures include:

- glibc interpreter or `libc.so.6`;
- `libstdc++.so.6`, `libgcc_s.so.1`, or `GLIBCXX_*` requirements in a Chromium-owned ELF;
- static libstdc++/libgcc inputs in any recorded Chromium link command;
- a path to `libstdc++`, `libgcc_s`, X11, VA-API, VDPAU, or another exception soname that does not descend from a locked FFmpeg `libav*` root;
- a compiler/linker path outside `/opt/llvm-musl` in the recorded Chrome graph;
- an RPATH/RUNPATH pointing into `/work`, `/opt/llvm-musl`, or another builder-only directory;
- PulseAudio, PipeWire, GTK, or direct X11 dependencies in `headless-debug`;
- Chromium bundled-FFmpeg objects or a stock-FFmpeg library/runtime path inconsistent with the locked APK/preflight closure;
- an unexpected dynamically linked libc++/libc++abi/libunwind;
- an architecture or musl-loader mismatch.

The audit report also records without initially failing:

- transitive X11/XCB sonames introduced by a host/system Mesa package;
- all dlopen-style runtime modules that cannot be discovered solely through `DT_NEEDED`;
- the complete system-library provenance and versions;
- binary sizes and stripped/unstripped state;
- every FFmpeg-exception edge, its root-to-leaf dependency path, APK owner/version, soname, file digest, and license metadata.

“Direct X11 dependency” means a `DT_NEEDED` edge from Chrome or a Chromium-owned staged library to an X11/XCB soname. An X11/XCB edge reachable only through a host-owned Mesa package is reported as transitive during v1. An X11/XCB edge inside the copied stock-FFmpeg closure is an FFmpeg exception and must be reported as such. The dependency report preserves the complete path that introduced each soname so these cases remain reviewable.

The final link command and response file must be preserved in metadata so the absence of static GNU runtime archives is independently reviewable.

## 17. Runtime packaging

Packaging begins only after Chrome passes functional and ELF audits directly from the named volume.

### 17.1 Staged contents

Stage only what the automation browser needs:

- `chrome` under a stable bundle-relative name;
- required V8 snapshot/data files;
- required `.pak` resources;
- en-US locale resources;
- DevTools frontend resources;
- required local component/shared libraries that remain in the monolithic profile;
- the recursively resolved private Alpine shared-library closure, including the complete FFmpeg-exception closure and excluding the host-owned graphics stack except where an exact library is independently required by FFmpeg;
- launcher script;
- build, dependency, license, checksum, and audit metadata.

Stage into a fresh directory and strip only the staged copies with `/opt/llvm-musl/bin/llvm-strip` where safe. Never strip or otherwise mutate the cached Ninja outputs. Record pre-strip and post-strip hashes/sizes and keep files unstripped when stripping would invalidate a required resource or test.

Do not package:

- chromedriver;
- legacy headless shell;
- setuid `chrome-sandbox` fallback;
- SwiftShader or Vulkan libraries;
- Mesa EGL/GLES/GBM/GL loaders, Mesa DRI drivers, libdrm, or llvmpipe's LLVM dependency closure;
- fonts;
- Wayland compositor/server files;
- Alpine's musl loader or libc;
- locales other than en-US;
- PDF viewer, printing, spellcheck, or desktop-integration resources.

### 17.2 Dependency resolution

Build the closure by parsing `DT_NEEDED` entries with LLVM tools and resolving sonames against known Alpine library directories. Include an explicit reviewed list for libraries Chromium loads dynamically and therefore do not appear in `DT_NEEDED`, particularly NSS/trust-store modules if required.

The resolver classifies every dependency edge as `private`, `host`, `ffmpeg-exception`, or `forbidden`:

- `private`: copied into the bundle and recursively resolved;
- `host`: intentionally omitted and listed in the required-host manifest, including the graphics stack, musl, fonts, and trust data;
- `ffmpeg-exception`: copied into the bundle, but permitted to violate the ordinary GNU/X11/VA-API/VDPAU policy only on a dependency path rooted at the locked stock-Alpine FFmpeg libraries;
- `forbidden`: causes packaging to fail, including GNU runtimes and disabled backend libraries outside a valid FFmpeg-exception path.

Classification is by exact soname, APK owner/version, and dependency ancestry in a committed policy file, not by ad hoc copying during packaging. A soname is not globally exempt merely because FFmpeg also uses it. After staging, rerun the resolver against the staged tree plus declared host set and fail on an unclassified dependency or an exception library reachable from an unauthorized root.

The stock-FFmpeg closure is snapshotted during preflight as an exact directed graph. Packaging must reproduce that graph from the locked installed files. Any added/removed edge, package-owner change, or digest change invalidates preflight and requires package-lock review.

Dependency graphs and exception manifests are architecture-specific. The aarch64 evidence in this document must not be copied forward as amd64 truth; native amd64 preflight recomputes and reviews its closure from `packages.amd64.lock` before any amd64 artifact is eligible for packaging.

When the same soname is used both by FFmpeg and by a host-owned stack, packaging follows these rules:

- copy it if FFmpeg has a real `DT_NEEDED` path to it;
- record that the private copy will take precedence through `LD_LIBRARY_PATH`;
- run both FFmpeg media tests and Mesa renderer tests against the staged bundle to catch loader/version conflicts;
- fail if the private copy makes the host-owned Mesa path load a mismatched library or changes the validated renderer;
- never maintain two files with the same soname in different private directories and rely on search-order accidents.

Never copy a library merely because it exists in the builder. Every staged library must be justified by the dependency graph, a known dlopen path, or a test requirement.

The bundle targets an Alpine 3.24-compatible musl host. Do not attempt to make it executable on glibc hosts or arbitrary musl distributions in v1. Child Chromium processes use the host's normal absolute musl interpreter, which is why bundling a private loader/libc is explicitly out of scope.

### 17.3 Launcher

The launcher:

- locates the bundle relative to itself;
- prepends only the bundle's private library directory to `LD_LIBRARY_PATH`;
- leaves Mesa driver discovery pointed at the host;
- checks that the expected musl architecture is running;
- checks/report user-namespace availability where the kernel exposes a setting;
- refuses root execution by default;
- uses headless mode only when requested by the caller rather than hiding all Chrome arguments;
- never inserts `--no-sandbox`;
- forwards signals and exit status directly.

Because `LD_LIBRARY_PATH` also affects transitive lookups, verify that no private library shadows a host-owned Mesa/DRM soname. The launcher must not set `LIBGL_DRIVERS_PATH`, `GBM_BACKENDS_PATH`, or another driver path to a builder location.

### 17.4 Exported artifacts

Produce:

- `ungoogled-chromium-150.0.7871.114-headless-<arch>-musl.tar.zst`;
- SHA-256 and SHA-512 checksum files;
- input and package locks;
- canonical GN arguments;
- builder image digest;
- patch provenance;
- library/license manifest;
- ELF/link audit report;
- required-host-package and kernel-capability documentation.

Create the archive inside the named volume. Use `docker cp` to export it; do not use a host output bind mount.

### 17.5 FFmpeg licensing/provenance gate

Stock Alpine FFmpeg's broad codec closure can include LGPL, GPL, and separately licensed codec libraries. Packaging must not imply that a binary manifest alone satisfies redistribution obligations.

Always generate:

- exact APK package names, versions, file digests, declared licenses, project/source URLs, and Alpine source-package references for the complete FFmpeg-exception closure;
- copies of license/notice files installed by those packages where available;
- Chromium/system-FFmpeg configuration metadata, including codec branding flags;
- a machine-readable mapping from every staged exception file to its APK/source package;
- a warning in the artifact metadata that redistribution requires an independent license-compliance review and may require corresponding source/build material.

Local development export may proceed with this provenance bundle. Publishing or distributing the archive is a separate release gate and is not declared compliant by the build scripts.

## 18. Runtime host contract

The first artifact supports Alpine 3.24-compatible hosts of the matching architecture. The host supplies:

- the standard musl loader/libc;
- kernel namespaces/seccomp/procfs capabilities required by Chromium's user-namespace sandbox;
- a coherent Mesa EGL/GLES/GBM/GL/libdrm and DRI Gallium stack with llvmpipe for displayless software rendering;
- DRM/device access if a later hardware renderer is selected;
- usable fonts and fontconfig data;
- CA trust data and any required NSS trust-store integration;
- adequate shared memory and temporary storage.

The artifact does not support running as root with sandbox disabled. Container deployments must arrange a non-root user and permit Chromium's normal sandbox primitives.

## 19. Wayland/ALSA follow-up acceptance

After headless packaging works, add `wayland-debug` without changing the prepared source identity unless new patches are required.

Acceptance adds:

- GN graph proves Ozone Wayland enabled and Ozone X11 disabled;
- direct Wayland/xkbcommon/EGL/GBM probes;
- direct ALSA playback/device-enumeration probe where hardware is available;
- Chrome launches against a real Wayland compositor;
- CDP/headless tests continue to pass unchanged;
- no PulseAudio or PipeWire dependency appears;
- runtime audit reports any transitive X/XCB libraries separately.

Zero X runtime libraries is a later explicit project. If Alpine's normal Mesa or UI packages introduce X libraries, solve it through a smaller/custom Wayland-only dependency closure rather than enabling Chromium's X backend.

## 20. Version-update process

A Chromium update is a controlled change, not an automatic build option:

1. Select a Chromium release for which ungoogled common, portablelinux, and Alpine musl patches align.
2. Update archive URLs and digests in the lock.
3. Pin new common, portable, Alpine, and copium commits/tags.
4. Review patch-series changes and system-unbundle compatibility.
5. Review Chromium's expected Clang and Rust versions against the custom LLVM and Alpine Rust packages.
6. Rebuild the immutable environment image if any package/tool input changes.
7. Use a new named volume or explicitly reset the old one; never patch a stamped old source tree in place.
8. Re-run every PoC gate before starting Chrome.
9. Re-run functional, ELF, and packaging tests before publishing an artifact.

amd64 follows the identical process using its own image digest, APK lock, LLVM archive, named volume, and output artifact. The scripts must be architecture-neutral even though only arm64 execution is required initially.

## 21. Known risks and mandatory stop conditions

### External static libc++ integration

Risk: Chromium's supported path strongly prefers its pinned in-tree libc++, and component builds require a shared runtime. Mitigation: monolithic profile, exact external header/archive patch, and early GN/link probes. Stop if the external runtime cannot safely replace Chromium's runtime without fallback.

### `use_gtk=false` full Chrome

Risk: GN may generate successfully while a later Linux desktop source assumes GTK. Mitigation: headless-only profile, no native dialog requirements, full Chrome dry-run, then actual build. Apply only small targeted fixes. Stop and reassess rather than importing GTK/X dependencies silently.

### Ungoogled/portable/Alpine patch conflicts

Risk: all patch sets target the same Chromium release but were not necessarily tested together. Mitigation: deterministic order, committed conflict resolutions, provenance logs, and source stamps. Do not omit a failed patch silently.

### Alpine Rust versus Chromium Rust expectations

Risk: Chromium pins Rust behavior and vendored crates while Alpine provides its own compiler. Mitigation: use Alpine's matching Chromium/Rust patches, validate Rust target configuration during GN, and compile a Rust-containing small graph target before Chrome if available. Do not invoke Chromium's Rust downloader.

### System FFmpeg API compatibility

Risk: Chromium's unbundle shim supports a constrained API while Alpine FFmpeg evolves, and Alpine's full FFmpeg closure reaches the exceptional GNU runtimes plus X11/VA-API/VDPAU libraries. Loading libstdc++ beside Chrome's static libc++ creates potential symbol-interposition/allocator/exception hazards even though the boundary is C. Loading OpenSSL through `libavformat` beside Chromium's BoringSSL creates a second interposition risk. Mitigation: exact package/patch versions, graph containment preflight, dynamic-symbol intersection baselines, compile/link probes, repeated runtime media tests, local HTTPS regression, process-map capture, and preservation of Chromium's existing C shim. Any direct Chromium dependency on the exception runtimes or evidence of an unsafe ABI/crypto boundary is a hard failure.

### FFmpeg exception growth and licensing

Risk: Alpine may enable another codec or dependency in a package revision, expanding disk size, licenses, or exception reach without a Chromium change. Mitigation: pin the full resolved APK set, snapshot the exact closure graph and file digests, fail on drift, report size deltas, and keep publishing behind a separate license/provenance release gate.

### Trimmed source versus ungoogled pruning

Risk: the space-saving source archive may already omit files listed by ungoogled's binary-pruning manifest. Mitigation: pin an explicit already-absent allowlist after inspecting the archive, fail on new omissions, and retain complete pruning provenance.

### Sandbox under OrbStack/container runtimes

Risk: a container runtime can restrict user namespaces even when the target Alpine host supports them. Mitigation: test without `--no-sandbox`, report the missing kernel/runtime primitive clearly, and keep sandbox validation distinct from compilation. Do not add setuid or sandbox-disabled fallbacks.

### Mesa llvmpipe portability

Risk: the staged bundle omits the complete Mesa/DRM loader-and-driver stack, so rendering depends on host package compatibility. Mitigation: keep that stack consistently host-owned, target Alpine 3.24-compatible hosts, document exact host packages, and validate renderer identity in tests.

### Chromium ANGLE/GL path

Risk: full Chrome may retain ANGLE even though SwiftShader/Vulkan are disabled, and the exact native-EGL path can differ from the assumed configuration. Mitigation: do not make removal of ANGLE a v1 goal, audit its generated targets, select the smallest path that reaches host Mesa, and require runtime renderer evidence.

### Unsupported or obsolete GN flags

Risk: feature-reduction flags change across Chromium versions. Mitigation: pin Chromium, keep profile files version-specific, use `--fail-on-unused-args`, and distinguish dependency-removing requirements from optional dead-code pruning.

## 22. Completion criteria by phase

### Environment complete

- Both architecture mappings, input locks, and image definitions pass static validation.
- ARM64 image builds natively under OrbStack.
- Package and LLVM locks match the installed environment.
- No generic compiler/binutils path can accidentally override `/opt/llvm-musl`.
- Candidate system-library closures have been audited and their private/host/FFmpeg-exception/forbidden classifications are recorded.
- The exact stock-FFmpeg exception graph is accepted only within its committed roots and contains no glibc.

### Source preparation complete

- No Git clone exists.
- Trimmed source is verified, extracted, and its archive removed.
- All patch layers and domain substitution succeed.
- Source identity/provenance is complete and repeatable.

### PoC complete

- Direct compiler/runtime/system-library probes pass.
- GN generation passes with no unused args.
- Small GN smoke executable builds/runs.
- Chrome dry-run and command audit pass.
- No in-tree libc++, GCC, libstdc++, or libgcc compiler/link input is scheduled for Chromium-owned targets, and the system-FFmpeg graph matches the locked exception policy.

### Functional browser complete

- Monolithic Chrome builds from the persistent volume.
- Modern headless mode runs with sandboxing and host llvmpipe.
- Raw CDP pipe/TCP, DevTools resources, downloads, screenshots, storage/network events, and local extension tests pass offline.
- System-FFmpeg H.264/AAC decode/containment tests pass without disturbing Chrome's static-libc++ identity or Mesa llvmpipe rendering.
- ELF/link audits pass.

This is the first full-build success milestone. Packaging is not required to call the browser functional.

### Packaging complete

- Minimal runtime closure is staged and tested.
- Tar.zst and all manifests/audits/checksums are exported without a bind mount.
- A clean Alpine 3.24-compatible host can run the bundle using documented host dependencies.
- The copied FFmpeg exception closure exactly matches preflight, and its package/license/source provenance accompanies the artifact.

### Wayland follow-up complete

- Wayland and ALSA profile builds in its own output directory.
- Interactive Wayland launch and ALSA probes pass.
- Headless automation remains intact.
- Chromium X11, GTK, PulseAudio, and PipeWire remain disabled.

## 23. Explicitly deferred work

- Native amd64 PoC and full build execution, while keeping amd64 structurally supported from the start.
- Wayland and ALSA until the headless browser passes.
- VA-API hardware video decoding.
- Wild linker evaluation; LLD is the only v1 linker.
- Chrome Web Store integration.
- PDF generation/viewer and all printing support.
- WebRTC, screen capture, Bluetooth, USB, smart-card, speech, and enterprise features.
- Wider locale support.
- GTK or another native desktop toolkit.
- Bundled Mesa/llvmpipe runtime.
- Replacing the stock-Alpine FFmpeg exception with a minimal LLVM/musl-built FFmpeg and codec closure.
- Zero transitive X/XCB libraries.
- Generic-musl or glibc-host portability.
- Bit-for-bit reproducible output.
- Prebuilt APK archival or a private Alpine snapshot repository.
- ccache/sccache until measured evidence justifies the duplicate disk usage.
