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

1. The builder image is assembled from a base image digest, exact package versions, checksummed configuration archives, and a checksummed LLVM artifact. Once built, its image digest is immutable provenance. Cache reuse is controlled by the narrower environment compatibility identity defined in section 7, so harmless changes to tests or wrappers do not invalidate compiled output.
2. A networked `fetch` phase downloads immutable inputs into a content-addressed inbox and verifies them before they become eligible inputs. The separate `prepare` phase assembles source from that inbox plus image-resident configuration and patches with container networking disabled. All later configuration, compilation, testing, auditing, and packaging commands also run with networking disabled.

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

Stock Alpine ccache is an explicit build-only GNU/GPL exception. Its GPL-3.0 license and dynamic dependencies on `libstdc++.so.6` and `libgcc_s.so.1` are knowingly accepted because its compiler-output reuse is valuable for local iteration. It may execute only as a compiler wrapper, may never replace the Laputa compiler driver or LLVM binutils, and is excluded from every staged browser artifact. This permission does not allow GCC/G++, GNU binutils, libstdc++, or libgcc into a Chromium compile/link command or Chromium-owned ELF. Record ccache's exact APK version, dependency closure, configuration, and license in builder provenance.

Rust is an explicit, separately approved compiler path. Chromium's Rust crates use the pinned Alpine Rust toolchain selected below. Any crate build script that invokes a C/C++ compiler must inherit the Laputa `CC`, `CXX`, and LLVM-binutils environment. Rust compiler provenance is recorded, and Rust-linked final artifacts are subject to the same ELF/runtime audit as C++ output.

The shipped-artifact boundary does not claim that every host-owned library loaded at runtime is free of GNU runtime dependencies. This distinction is material: Alpine's Mesa and Rust packages currently use GNU runtime libraries internally. A stricter future requirement that the complete browser process closure load no GNU code would require custom LLVM-built Mesa, Rust, and other host packages and is outside v1.

Stock Alpine FFmpeg is the sole shipped-runtime exception; ccache is builder-only and therefore does not change this statement. The package lock currently resolves FFmpeg `8.1.2-r0`; relevant Alpine 3.24 aarch64 package metadata shows:

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
- DevTools frontend revision pinned by Chromium 150 `DEPS`: `ec97cf3bbeea2cb623fbf97c4e3f22f5acb4d568`.
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

Alpine 3.24 provides the matching Chromium 150 generation of GN, Rust, ccache, FFmpeg, and related libraries. Ninja 1.12.1 is built from its locked upstream source with the external musl toolchain. The current lock resolves Rust `1.96.0-r0` and ccache `4.13.6-r0`; the exact architecture-specific resolved locks remain authoritative if metadata changes. The Rust compiler package itself depends on Alpine GCC, libstdc++, and libgcc in the builder, which is the explicitly approved Rust-toolchain exception described above. Stock ccache independently uses the approved builder-only GNU/GPL exception. The custom C/C++ compiler remains LLVM 22.1.8 even if Alpine packages compiler-adjacent libraries built with another LLVM 22 patch release.

Do not request Alpine `build-base`, GCC, G++, binutils, Clang, or LLD as generic build dependencies. Install individual development packages. The approved Alpine Rust package currently pulls GCC/libstdc++/libgcc transitively into the builder; retain them only because APK dependency resolution requires them. Do not add their directories ahead of `/opt/llvm-musl` in `PATH`, do not set any compiler variable to them, and fail if a Chromium compile/link command uses them. A transitive builder runtime may enter the shipped bundle only through the exact FFmpeg-exception closure.

Builder tools include only what the source graph needs, grouped approximately as:

- source and scripting: Bash, ca-certificates, curl, patch, Python, Perl, tar, xz, zstd;
- generators and cache: Bison, Flex, gperf, Go, Node, GN, Ninja, and the exact locked Alpine ccache package;
- Rust: Alpine Rust, Cargo components required by Chromium, rust-bindgen, and rustfmt if the source graph requires it;
- native platform headers: musl-dev, Linux headers, pkgconf, NSS/NSPR, GLib/DBus where structurally required;
- selected system libraries and their `-dev` packages;
- test-time Mesa llvmpipe runtime in the builder image, even though it is not copied into the portable browser bundle.

Each architecture package lock has two explicit sections:

- `world`: the intentionally requested `name=version` packages;
- `resolved`: the complete sorted `apk info -v` result, including transitive packages.

The Dockerfile installs the exact `world` entries, then compares the resolved installed set byte-for-byte with `resolved`. This permits Alpine's solver to install dependencies while making any transitive change a hard failure. Lock regeneration is a separate maintainer operation that runs against the pinned base/repositories and produces a reviewable old/new package diff; an ordinary image build never regenerates its own lock.

### 4.3 Laputa LLVM toolchain

Record quirks and capability gaps discovered with the custom prebuilt LLVM toolchain in [LLVM-TOOLCHAIN.md](LLVM-TOOLCHAIN.md); keep this plan focused on the build contract and acceptance criteria.

Use the release artifacts from `laputa-systems/llvm-prebuilt-musl`:

| CLI architecture | Linux architecture | Chromium CPU | LLVM archive SHA-256 |
| --- | --- | --- | --- |
| `arm64` | `aarch64` | `arm64` | `675f9cf871313a5672a63882d4d30dd6dd55df0aa9caee70970542eb03a23da3` |
| `amd64` | `x86_64` | `x64` | `ac0bd443a1933bbd2c0efbedf6ebc97ff8ca2469e5ba65eadb966fb75f65dd1c` |

The canonical hermetic input is the published release archive plus its committed URL, filename, size, and SHA-256. This matches the release-artifact pattern demonstrated by `~/d/laputa-systems/mirror/Dockerfile`. The neighboring `~/d/laputa-systems/llvm-prebuilt-musl` checkout is provenance and a development reference only; an ordinary image build must not read mutable files from that checkout or require it as an implicit Docker build context.

An unreleased local prebuilt archive may be used only through an explicit image-build override. The override accepts one archive file, computes and prints its digest, requires a matching temporary lock entry, copies only that archive into the build context or a named BuildKit context, and records the non-release provenance in the image metadata. It must never compile LLVM from the neighboring source checkout as part of the Chromium image build.

For remote HTTP(S) archive ingestion, prefer Dockerfile `ADD --checksum=sha256:<digest>` when the selected Dockerfile frontend supports it, then retain the existing post-download size/hash and toolchain-content validation. Otherwise download and verify in one image layer so an unverified archive never survives into a later layer. BuildKit cache state is an acceleration only and is not trusted as verification.

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
  disk-budget.arm64.json
  disk-budget.amd64.json
  profiles/
    headless-debug.gn
    wayland-debug.gn
patches/
  chromium-runtime/
  conflicts/
  development/
scripts/
  container-entrypoint.sh
  fetch-inputs.sh
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
7. Create a fixed non-root build user and an entrypoint that initializes new named-volume ownership without changing host files.
8. Run environment/toolchain validation during the stable image stage.
9. Record the image input hash and package manifest under `/opt/chromium-build-metadata`.
10. In a final thin layer, copy repository scripts, local patches, smoke tests, and fixtures into `/opt/chromium-build`.

Order Dockerfile layers from least to most frequently changed: pinned Alpine base and packages, custom LLVM, immutable upstream inputs, user/environment setup, and repository-owned orchestration last. A change to a test or shell wrapper should rebuild only the final thin layer. Keep the Docker build context minimal with a strict `.dockerignore`; never send source archives, named-volume exports, local build output, or unrelated parent-directory contents to the daemon.

Use BuildKit's normal layer cache, and where useful its cache mounts, only to accelerate image construction downloads. Every downloaded input is still content-verified and the completed image must be correct with an empty cache. Cache contents are neither an input lock nor evidence of hermeticity.

Do not place Chromium source in an image layer. It belongs in the named volume so source preparation, patch iteration, and Ninja output do not duplicate enormous Docker layers.

“Architecture-ready for amd64” means the Dockerfile branches, input mappings, package lock, LLVM digest, GN CPU mapping, volume naming, and artifact naming exist and can be statically validated from arm64. It does not mean v1 runs an amd64 container under emulation. The host CLI compares the requested architecture to the native Linux architecture reported by the container runtime and refuses a mismatch. Native amd64 image execution/build validation waits for an amd64 Linux/OrbStack/Docker host.

## 7. Named-volume model and state machine

Use one primary work volume per architecture:

- `ungoogled-chromium-work-arm64`
- `ungoogled-chromium-work-amd64`

Allow an explicit environment override for experiments, but never derive a volume from the current directory or mount a host path.

Reserve separate compiler-cache volumes so resetting source/output does not destroy cached compilation results:

- `ungoogled-chromium-ccache-arm64`
- `ungoogled-chromium-ccache-amd64`

An optional architecture-independent `ungoogled-chromium-downloads` volume may retain only verified compressed source/input archives during active patch development. It is a speed-for-disk feature, is never required for correctness, and has an explicit prune command. It must not contain an extracted source tree or build output.

Suggested internal layout:

```text
/work/
  metadata/
    fetch-inputs.json
    fetch-complete.stamp
    source-inputs.json
    source-complete.stamp
    patches.json
    phases/
  inputs/
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

`fetch` downloads to a temporary file, verifies expected size and digest, and atomically renames it to a content-addressed filename only after verification. Without the optional download volume, that inbox is `/work/inputs`; with it, the download volume is mounted at `/downloads` and holds the canonical verified archive while `/work/metadata/fetch-inputs.json` records its exact digest/path. `prepare` mounts the selected inbox read-only wherever the container runtime permits and independently re-verifies the archive before extraction. A fetch stamp proves only byte acquisition and verification, never source preparation.

Preparation uses sibling temporary paths such as `src.prepare.<pid>` and atomically renames the completed tree to `src`. If an unstamped final `src` exists, `prepare` refuses and reports it instead of guessing whether it is reusable. Test and package staging use the same pattern: build into a temporary directory, validate it, then replace the profile's completed directory. Ninja's `out/` is the exception; it is intentionally resumable in place and is never transactionally replaced.

Separate provenance identity from cache compatibility:

- Source identity includes Chromium, ungoogled, portable, Alpine/copium, committed clean preparation patches under `patches/chromium-runtime` and `patches/conflicts`, architecture-sensitive source transformations, and an explicit preparation-compatibility schema version. It does not hash the preparation script wholesale, so a comment or logging-only edit does not invalidate source.
- Environment compatibility identity includes the architecture, pinned Alpine base and resolved package lock, LLVM artifact digest, sysroot/header/runtime contents, compiler-affecting environment, and an explicit build-driver compatibility schema version.
- Profile identity includes exact GN arguments and profile-owned source overlay files.
- Development-dirty identity is the clean source identity plus the ordered SHA-256 hashes, strip levels, and deterministic application options of every development patch applied after preparation. Wall-clock timestamps are provenance only and never change cache identity.
- Provenance includes the complete builder image digest, repository revision or dirty-state description, and all script/test/package metadata even when those values do not affect compatibility.

The complete image digest is recorded but must not by itself invalidate `src/` or `out/`. Editing a smoke test, packaging script, log message, comment, or host wrapper must leave compatible source and Ninja output reusable. A change that can affect prepared source or compiler output must deliberately bump the corresponding source, environment, or profile compatibility input. Do not approximate this with a hash of every file copied into the image.

A profile change creates a new `out/<profile>` without duplicating `src`. An environment or source mismatch causes an immediate refusal with a field-by-field difference. It must not delete or mutate the volume automatically. The user may select another volume name or explicitly run `reset --yes`.

A clean source initially has no development-dirty identity. `dev-apply-patch` may transition only from a matching clean base or an existing dirty state whose recorded clean base still matches. It appends the new patch digest to the ordered dirty identity. `poc`, `build-target`, `build`, `test-fast`, and `test` accept that dirty identity and key their phase results to it; `audit`, `package`, and publishable `export` reject it. A source-affecting image/input change invalidates the dirty chain rather than attempting to replay it implicitly. Only a clean reset/prepare removes dirty state for acceptance purposes.

Files under `patches/development/` are copied into the thin orchestration image layer but are never applied by clean `prepare` and are excluded from clean source compatibility identity until selected by `dev-apply-patch`. Editing one changes image provenance without invalidating the matching clean base, allowing rapid rebuild-and-apply experiments. Promoting a successful experiment means moving/reworking it into the clean patch series, updating patch disposition/provenance and source compatibility identity, then proving it through a fresh clean preparation; the dirty chain itself is never promoted as acceptance evidence.

The persistent `out/` directory is the primary incremental cache and handles normal edit/rebuild cycles without duplicating objects. Stock Alpine ccache is the approved secondary recovery/reuse cache for volume resets, clean source preparation, reverted edits, compatible output-directory changes, and unchanged translation units across nearby source versions. Its correctness is never part of artifact identity, and disabling or deleting it must not change the build result.

Install the exact locked Alpine ccache package as the explicit build-only GNU/GPL exception defined in section 2.2. Audit its builder dependency closure and keep it outside every runtime stage and export. Configure Chromium through its supported `cc_wrapper` GN argument using the absolute ccache executable path; do not use compiler-name masquerade directories or allow ccache to select a different compiler implicitly.

Use a separate cache per architecture, stable `/work` paths, content-based compiler checking, compression at a low level, no unsafe sloppiness settings, and an initial 10 GiB limit. Put ccache temporary data on the cache volume, print `ccache --show-config` and before/after statistics, and expose explicit cache status/prune/disable controls. Namespace or invalidate entries when architecture or Laputa toolchain content changes even though ccache also hashes the compiler. Measure hit rate, eviction rate, miss overhead, and duplicated disk before increasing the bound. Do not add sccache, remote cache storage, or distributed compilation in v1.

The initial effective configuration is equivalent to:

```text
CCACHE_DIR=/ccache
CCACHE_TEMPDIR=/ccache/tmp
CCACHE_COMPILERCHECK=content
CCACHE_COMPRESS=true
CCACHE_COMPRESSLEVEL=1
CCACHE_MAXSIZE=10G
CCACHE_NAMESPACE=<architecture>-<laputa-toolchain-digest>
CCACHE_SLOPPINESS=<unset>
```

Mount the selected compiler-cache named volume at `/ccache` and keep source/output at fixed `/work/src` and `/work/out/...` paths in every container. Leave `CCACHE_BASEDIR`/`base_dir` unset initially: stable container paths make rewriting unnecessary, and rewriting can alter `__FILE__` and debug-path behavior. Measure misses before considering it. Any later enablement requires an A/B cache-hit report plus compile/runtime comparison and becomes a profile/environment compatibility input.

Verify the exact environment-variable names against the locked ccache version and store `ccache --show-config` output; the semantic requirements above prevail if the package changes spelling. Set GN `cc_wrapper` to the verified absolute Alpine ccache executable, expected to be `/usr/bin/ccache`, while the GN compiler itself remains an absolute `/opt/llvm-musl/bin/clang` or `clang++` path. Do not enable hard-link mode, compiler masquerade directories, time-based sloppiness, `compiler_check=none`, or path rewriting by default.

Use ccache only for Chromium C/C++ compiler invocations. Rust and other language actions rely on their normal persistent Ninja outputs; do not add sccache merely to cache them. A cold cache can make a clean build slightly slower and consumes storage in addition to `out/`, which is why the size and statistics remain visible and bounded.

Never recursively `chown` or `chmod` `/work` at container startup. Initialize ownership once when a volume is empty, then inspect only the volume root and expected top-level paths. Avoid any startup or stamp check that walks the complete Chromium tree.

## 8. Host command contract

The host CLI defaults to `--arch arm64` and `--profile headless-debug`. Every command prints the selected image, work/cache/download volumes, architecture, profile, fetch state, clean/development-dirty/untracked-dirty source state, effective identity, ccache enabled/disabled state, and networking mode before starting. Dirty state is never hidden in verbose-only output.

### `image`

- Builds or locates the immutable builder image.
- Accepts `--arch arm64|amd64`.
- Refuses to execute a non-native architecture in v1; it never enables QEMU/Rosetta emulation implicitly.
- Does not create or change the work volume.
- Prints the final image digest and input-lock hash.

### `preflight`

- Runs after `image` and before `prepare`; it does not require Chromium source.
- Runs with `--network=none`; it audits only the immutable installed image and committed policies.
- Audits the complete `DT_NEEDED` closure of every proposed system library using the installed Alpine packages.
- Classifies every soname as private, host-owned, FFmpeg-exception, or forbidden using the same policy later used by packaging.
- Prints every path to GNU, X11, audio, VA-API, VDPAU, TLS, and other normally excluded libraries, marking each as a valid FFmpeg exception or a failure.
- Records a content-addressed completion report under the environment compatibility identity and records the full image digest separately as provenance.
- Is a hard prerequisite for `prepare` and `poc`.
- Accepts GNU/X11/VA-API/VDPAU exception paths only when they are descendants of the exact locked stock-Alpine `libav*` roots.
- Fails if the exception leaks into a different candidate library, if any closure entry is unclassified, or if glibc appears anywhere.

### `fetch`

- Runs after `image` with network access and before `prepare`.
- Downloads only the exact locked Chromium archive and any other input explicitly classified as a fetch-phase input; small ungoogled/portable/Alpine/copium archives and repository patches normally remain image-resident.
- Uses temporary filenames, expected-size checks, and the locked digest, then atomically publishes verified bytes to `/work/inputs` or the optional `/downloads` volume.
- Writes `fetch-inputs.json` and the atomic fetch-complete stamp only after every requested byte is verified.
- Is idempotent for a matching content-addressed archive and re-verifies cached content before reporting success.
- Does not extract, patch, generate source metadata, invoke upstream hooks, or execute any downloaded program.
- Refuses redirects to an unapproved scheme and records the final resolved URL for provenance; correctness is determined by the digest, not the server response metadata.

### `prepare`

- Creates/initializes the named volume if needed.
- Requires a matching successful `fetch` and runs with `--network=none` for the entire container lifetime.
- Reads only the verified Chromium archive from the selected inbox; small patch/configuration inputs are already in the image.
- Re-verifies the source archive before extraction.
- Prepares the tree transactionally.
- Deletes the compressed archive only after successful complete preparation in the disk-minimal mode.
- With the explicit download-cache option, preserves the verified compressed archive in the download volume; cached bytes are verified again before every extraction.
- Is idempotent when stamps match.

### `dev-apply-patch`

- Is an explicitly non-hermetic patch-development escape hatch, disabled unless requested by name.
- Runs with `--network=none`.
- Applies one selected repository patch to the existing prepared source without rebuilding the tree and records the patch, prior source identity, deterministic application options, provenance timestamp, and resulting development-dirty state.
- Computes a new development-dirty identity from the clean base plus the ordered development-patch hash chain; it never overwrites the clean source identity or claims a clean source stamp.
- Allows `poc`, `build-target`, `build`, `test-fast`, and `test` to operate under the exact dirty identity while making `audit`, `package`, and publishable `export` unavailable.
- Allows Ninja to rebuild only files affected by a patch experiment.
- Refuses if the recorded clean base no longer matches, a prior dirty transition is incomplete, the patch was already applied at another chain position, or patch fuzz/offset exceeds the explicit development allowance.
- Requires `reset --yes`, clean `prepare`, and all normal gates before any artifact can be accepted.

Arbitrary manual source edits are not assigned a reproducible development-dirty identity. Any diagnostic shell capable of modifying `/work/src` must first mark the volume `untracked-dirty`; builds/tests may continue for diagnosis, but all identity-bearing phase reuse, audit, package, and publishable export are blocked until reset/prepare. The supported fast iteration path is to edit a repository file under `patches/development/`, rebuild the thin image layer, and invoke `dev-apply-patch`.

### `shell`

- Defaults to mounting the work volume read-only for inspection.
- Always runs with `--network=none`; use dedicated `fetch` for any permitted download.
- Requires `--write-source --yes` to mount it read-write, writes the atomic `untracked-dirty` marker before starting the shell, and prints that the session cannot produce acceptance evidence.
- Never clears or synthesizes clean/development identities after a writable session.
- Permits explicitly diagnostic `build-target`, `build`, `test-fast`, or `test` invocations afterward, but their outputs remain untrusted/untracked and no reusable completion stamp is written.

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

### `build-target`

- Accepts one explicit Ninja output or a GN label resolved to its outputs.
- Runs offline against the existing profile output and never broadens the request to `all`.
- Exists for rebuilding the failing object, generated-resource target, archive, or other narrow dependency before paying for a `chrome` relink.
- Prints `gn desc`/`ninja -t query` guidance when a supplied label has several outputs or is not directly buildable.

### `test-fast`

- Runs the shortest deterministic browser loop: launch sandboxed modern headless Chrome, complete a raw CDP handshake, navigate to one loopback fixture, evaluate JavaScript, capture and validate a screenshot, and shut down cleanly.
- Uses a fresh small user-data directory and writes concise failure logs.
- Must remain independent of extension, media, complete DevTools-resource, dependency-audit, and packaging checks so its expected runtime is seconds rather than minutes.

### `test`

- Runs the complete repository-owned smoke/CDP/DevTools/extension/media/rendering tests offline.
- Runs Chrome as a non-root user and never adds `--no-sandbox`.
- Writes logs, screenshots, and downloads under `test-output/<profile>`.

### `audit`

- Runs post-build compile-command, response-file, ELF, runtime-closure, GNU-boundary, and provenance audits without packaging.
- Is explicit and is never an automatic consequence of an ordinary `build` or `test-fast` iteration.
- Refuses development-dirty source; focused compile-command inspection during dirty development belongs to `poc`/`build`, not an acceptance audit stamp.

### `package`

- Is unavailable until the functional browser and ELF audits pass.
- Refuses development-dirty source and every phase stamp derived from a dirty identity.
- Builds a staging directory inside the volume.
- Never copies Mesa drivers, fonts, or a host musl loader into the bundle.

### `export`

- Produces the final `tar.zst`, checksums, and adjacent metadata.
- Creates a temporary container with the named volume mounted, keeps it running while the mount is active, uses `docker cp` to copy a completed export directory to the host, and removes the temporary container afterward.
- Does not bind-mount an output directory.
- Refuses publishable artifact export from development-dirty source; diagnostic logs may still be copied explicitly but are labeled dirty and are not packaged as a browser artifact.

### `verify-cold`

- Is the expensive clean-room acceptance command, not part of the normal edit loop.
- Creates uniquely named temporary work and compiler-cache volumes and refuses to reuse an existing `src/`, `out/`, stage, or ccache entry.
- Runs `fetch` into the fresh work metadata, optionally satisfying it from the independently re-verified compressed download-cache archive; networking is permitted only for this fetch step and is unnecessary on a cache hit.
- Executes clean offline `prepare`, `poc`, full `build` with `CCACHE_DISABLE=1`, complete `test`, `audit`, `package`, staged-runtime retest, and `export`.
- Records that the build started from empty source/output/cache state, along with volume identifiers and before/after sizes.
- Deletes temporary volumes only with an explicit cleanup option after reports/artifacts have been exported; failure preserves them for diagnosis.
- Is required once for each materially changed environment/toolchain/source/profile compatibility tuple before that tuple is considered release-eligible. Ordinary patch iteration and the first warm browser milestone do not wait for it.

### `status`, `clean`, and `reset`

- `status` reports phase stamps, compatibility and provenance identities, tool versions, source/input/output/stage/cache sizes, compiler-cache statistics, dirty-source state, measured disk budget/peak projection, and incomplete phases.
- `clean --profile <name>` removes only that profile's `out`, stage, and test output.
- `clean --all-profiles` preserves prepared source.
- `reset` refuses without `--yes`, then removes the complete architecture volume.
- `cache-status` reports cache configuration, hits/misses, evictions, compression, and disk use without changing it.
- `cache-prune` independently bounds or removes the compiler/download cache and never touches the work volume.
- `build --no-compiler-cache` sets ccache's supported disable control for a diagnostic build without changing GN arguments or deleting entries; use it to reproduce suspected cache faults.

## 9. Source preparation pipeline

Preparation is deterministic and uses this exact conceptual order:

1. With networking disabled, open the content-addressed Chromium archive produced by `fetch` from `/work/inputs` or the optional download volume.
2. Re-verify size and SHA-512 immediately before extraction.
3. Extract into a temporary source directory and validate expected Chromium version files and top-level layout.
4. Validate every source-control-free revision/version input required by Chromium generators, including applicable `LASTCHANGE`, `LASTCHANGE.committime`, version, revision, and generated metadata files. Prove the selected GN/Ninja graph does not require a Git checkout.
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
14. In disk-minimal mode, remove the `/work/inputs` archive only after the completed source stamp is durable and mark the fetch record `consumed`; retain it only in the optional download cache otherwise.

Preparation must preserve timestamps and avoid rewriting unchanged files wherever the upstream tools permit it. Generated profile files are written through compare-and-replace so identical content retains its previous timestamp. A script/test-only image rebuild must not cause preparation to touch the source tree.

Do not install Git merely to satisfy version-generation scripts. The trimmed archive must contain or deterministically generate the required revision metadata from locked values. Run preparation and the representative generators under `--network=none`; any attempt to consult Git, depot_tools, a remote revision service, or an undeclared host path is a source-preparation failure. If a small repository-owned metadata overlay is required, its exact contents and Chromium-version derivation belong to source identity and patch provenance.

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

Request only the `chrome` target and its implicit dependencies. Do not request `all`, documentation groups, test suites, benchmarks, examples, chromedriver, headless shell, or packaging groups. There is no separate global “disable docs/tests” switch required: those products are excluded by target selection. Generated protocol descriptions, GRIT data, DevTools resources, and other apparent documentation/resource steps that are dependencies of `chrome` are runtime inputs and must not be suppressed indiscriminately.

PDF is not an acceptance capability. Do not test `Page.printToPDF`, package the PDF viewer, or install CUPS. If a small amount of PDF implementation remains structurally required by the full Chrome graph, removing it is not allowed to block the first browser.

The profile source file contains only arguments verified to exist in Chromium 150. At minimum, verify the effective values corresponding to this behavior matrix rather than assuming every desired capability has a same-named GN argument:

| Area | Required effective behavior |
| --- | --- |
| Toolchain | Clang/LLD, native musl, no sysroot, Laputa external libc++, compiler-rt |
| Build shape | debug, monolithic, zero symbol levels, no PGO/LTO/CFI/Chrome plugins |
| Compiler cache | absolute Alpine ccache wrapper, bounded per-architecture named volume, Laputa compiler remains the wrapped executable |
| Ozone/UI | headless enabled; Wayland, X11, GTK, and Qt disabled |
| Audio/capture | ALSA, PulseAudio, PipeWire, and WebRTC capture disabled |
| Graphics | host Mesa/llvmpipe path; VA-API, Vulkan, and SwiftShader disabled |
| Media | locked stock-Alpine system FFmpeg with common codecs; Widevine disabled |
| Product | ungoogled defaults, local extensions and DevTools retained, en-US only |
| Web frontend iteration | `optimize_webui=false`; verify and use `devtools_skip_typecheck=true` and `devtools_bundle=false` when supported by Chromium 150 |

For each row, the PoC stores both the controlling GN values and evidence from the generated target graph. A runtime-only command-line flag is not accepted as proof that an unwanted backend was excluded from compilation.

Chromium tag `150.0.7871.114` is already known to define `cc_wrapper` and to define `optimize_webui = !is_debug`; the profile still records their effective generated values rather than trusting documentation. The fast profile explicitly sets and verifies `optimize_webui=false`, which avoids WebUI minification/bundling work and makes WebUI code-cache generation effectively false for this graph.

The exact rolled DevTools frontend revision is `ec97cf3bbeea2cb623fbf97c4e3f22f5acb4d568`. Probe the extracted source from that revision for `devtools_skip_typecheck` and `devtools_bundle` before placing either argument in the profile. Record the defining file/line and effective GN value in PoC metadata. When present, set both as shown above: DevTools remains built and packaged, but TypeScript checking and frontend bundling are skipped for the development browser. The CDP and DevTools-resource tests must prove that this unbundled frontend remains functional in the integrated Chrome build. If either argument is absent, has been renamed, is unused, or breaks the frontend, omit only that verified-incompatible argument rather than patching it back in or disabling DevTools.

Run DevTools TypeScript checking as a separate explicit validation target or command at milestone boundaries if Chromium exposes one. Do not create another full C++ output directory merely to typecheck the frontend unless the generated graph makes that unavoidable. The ordinary C++ edit loop never performs frontend typechecking implicitly.

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

After the monolithic browser works, publish a new pinned Laputa toolchain artifact containing validated shared musl builds of libc++, libc++abi, and the selected unwind runtime. Only then add a `headless-component-debug` iteration profile with `is_component_build=true`. It must use those intentional toolchain release artifacts, not an ad hoc shared library synthesized inside this project, and must pass the same compiler/runtime/ELF checks. Component mode is development-only and may have behavior differences; monolithic `headless-debug` remains the artifact-producing validation profile. This is the largest planned improvement to repeated C++ relink time because ccache does not cache links.

Do not enable stale jumbo/unity-build switches, Clang modules, remote execution, distcc, Icecc, or another cache service merely because older Chromium guidance mentions them. Each adds correctness or dependency risk and must be reconsidered only from Chromium 150's actual supported graph and measured local bottlenecks.

## 13. Proof-of-concept gates

The PoC is intentionally much cheaper than building Chrome. It must catch toolchain, runtime, patch, GN, and dependency failures early.

### Gate A: environment identity

- Architecture inside the container matches the selected native architecture.
- Builder image digest is recorded as provenance; the environment compatibility fields and package manifest match their locks.
- Network is absent for all PoC subprocesses.
- All expected source stamps match.
- `which` and resolved symlinks for compiler/binutils variables point under `/opt/llvm-musl`.
- ccache configuration names the absolute Laputa compiler wrapper path, the selected per-architecture cache volume, content-based compiler checking, the committed size bound, and no unsafe sloppiness settings.
- ccache's installed GNU runtime dependencies are classified builder-only and no cache executable/library is eligible for staging.
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
- Required source-control-free revision metadata exists and matches the locked Chromium/DevTools revisions; representative version generators run without Git or network access.
- Binary pruning completes.
- Every common, portable, Alpine/copium, and local patch is accounted for.
- Domain substitution completes without unprocessed entries.
- No forbidden bundled toolchain/sysroot binary remains selected by the build configuration.
- System-unbundled library GN replacements exist and their removed source trees are not referenced by GN.

### Gate D: GN generation

- Generate `out/headless-debug` with `--fail-on-unused-args`.
- Save the final canonical args listing.
- Assert target CPU, musl, no sysroot, custom host/default toolchains, LLD, monolithic debug, symbol levels, headless Ozone, no X11/Wayland/GTK/audio, stock system FFmpeg, ccache wrapper, `optimize_webui=false`, only those DevTools fast-build arguments proven to exist at the locked revision, and disabled optimization features.
- Run `gn check` only where it is useful and bounded; do not let a full unrelated upstream check become the PoC.

### Gate E: small GN build

Build and run the repository-owned `//tools/hermetic_smoke:hermetic_smoke` target through the generated Ninja graph. It:

- is installed by the committed source overlay and built by Chromium's generated Ninja graph;
- consumes the generated `compiler_buildflags.h` configuration header;
- exercises C++ standard library, exceptions, TLS, and threads;
- uses the same external static libc++/libc++abi/libunwind boundary as Chromium;
- runs successfully inside the container;
- remains tiny and does not depend on `//base`, `//content`, or another large Chromium library merely for symbolism.

Gate B continues to exercise the profile's Mesa and system-FFmpeg link/runtime boundaries directly; Gate E deliberately does not add those libraries to the smoke target or to the `chrome` dependency closure.

### Gate F: Chrome graph dry-run and audit

The reproducible graph-oracle procedure, generated artifacts, regeneration commands, and verification checklist are documented in [ORACLE.md](ORACLE.md).

- Run `ninja -n chrome` and record whether it reports regeneration, pending work, or no work.
- Generate and inspect the complete target-scoped command list with `ninja -t commands chrome`; use the procedure in [ORACLE.md](ORACLE.md) to cross-check its counts.
- Prove all C/C++ commands use the approved ccache wrapper over absolute custom LLVM compiler paths, while archive/link-only tools use custom LLVM directly.
- Prove Chromium's in-tree libc++ targets are not scheduled.
- Prove the generated graph uses the system-FFmpeg shims and does not schedule Chromium's bundled FFmpeg sources.
- Prove no forbidden compiler/runtime flags or archive paths appear.

Because a Ninja dry-run does not necessarily expand every response file until its generating edge has run, audit both `ninja -t commands chrome` and the response files/command descriptions that exist after the small GN build. Repeat the complete command/response-file audit after the actual Chrome build before declaring the compiler boundary proven.

Passing the PoC must never automatically launch the full build.

## 14. Full build behavior

The `build` command invokes Ninja for `chrome` only. It runs offline and uses the persistent profile output directory.

The intended development loop is:

1. edit repository configuration or a patch under `patches/development/`, rebuild only the thin orchestration image layer, and apply it through `dev-apply-patch` when source changes are required;
2. run `build-target` for the failing or directly affected object/generated target where practical;
3. run `build` only when a complete `chrome` executable or relink is required;
4. run `test-fast`;
5. run the complete `test` at dirty or clean milestones;
6. after promoting changes and performing clean preparation, run `audit` only at clean acceptance milestones;
7. run `package` only for an accepted clean candidate.

Use `gn desc`, `ninja -t query`, `ninja -t commands`, and generated compilation data to identify the narrowest useful target. Do not repeatedly run GN generation when neither GN inputs nor build files changed; allow Ninja's dependency graph to request regeneration when required. Never clean as a speculative remedy for an ordinary compile or link failure.

Expose Ninja job count and load limit independently. Begin with a conservative host-derived default, then benchmark nearby values rather than assuming maximum visible CPUs is optimal. On the current 10-core/32-GiB development machine, start with `-j8` and compare `-j10` using the same build state. Do not oversubscribe memory merely to keep every CPU busy. Mount only disposable `/tmp` and `/dev/shm` as tmpfs where useful; never put persistent `src/`, `out/`, or the compiler cache on tmpfs.

Every meaningful clean and incremental timing run records wall time, CPU count, job/load settings, peak memory when observable, free/used volume space, Ninja statistics/critical-path information, final-link duration, and ccache hits, misses, evictions, cacheable/non-cacheable calls, and size. Keep a small benchmark log keyed by source/environment/profile compatibility identities. Optimization decisions must be based on these measurements, not cache folklore.

After the first successful native arm64 build, write `config/disk-budget.arm64.json` from observed high-water measurements rather than estimates. Record, separately:

- verified compressed input bytes;
- extracted/prepared source bytes and inode count;
- profile `out/` bytes before and after the final link;
- maximum temporary bytes observed during the final link;
- ccache bytes, configured maximum, and effective compression ratio;
- test-output bytes;
- staged runtime bytes;
- uncompressed and compressed export bytes;
- peak aggregate bytes and a documented safety margin;
- the source/environment/profile identities and measurement command that produced the numbers.

Generate the amd64 budget only from a native amd64 measurement; never copy arm64 values. `status` compares current free space and phase sizes with the applicable measured high-water projection and emits an early warning before a multi-hour build. Initially this is advisory because the first measurement does not yet exist and filesystem behavior varies. Promote it to a hard low-water gate only after repeated measurements establish a conservative bound, always leaving an explicit override for diagnosis. Report which optional data—download archive, old profiles, test output, stage, or ccache entries—can be pruned and the bytes each action would recover; never prune automatically.

Behavior on failure:

- Preserve all completed object files and Ninja state.
- Print the failing command and the environment/source/profile identities.
- Do not re-run source preparation.
- Do not clean automatically after compiler, linker, OOM, ENOSPC, or interrupted-build failures.
- A rerun resumes through Ninja normally.

The final monolithic link is expected to be the slowest incremental operation. This is accepted because safely using the toolchain's static libc++ takes precedence over component-link speed. Wild, mold, or another experimental linker must not be introduced until LLD produces a working audited browser.

## 15. Functional headless tests

Tests use only repository-owned fixtures and local loopback services. No test requires Internet access, Google services, Puppeteer, Playwright, Selenium, or chromedriver.

Tests are tiered by iteration cost:

- `test-fast` performs only launch, raw CDP handshake, one local navigation, JavaScript evaluation, screenshot validation, and clean shutdown. It is the default post-link loop and must not invoke extension, media, complete DevTools, ELF, provenance, or package checks.
- `test` runs one Python standard-library crawl-like scenario that covers the functional subsections below, with a TCP/WebSocket phase and a compact CDP-pipe phase inside the same runner. It is required at milestones and before `audit`/`package`.
- Upstream Chromium unit, browser, Web, layout, performance, and end-to-end suites are not built or run in v1. Repository-owned focused tests provide acceptance evidence for this deliberately narrow product.

### 15.1 Test server and profile isolation

- Start a Python standard-library HTTP server on loopback serving deterministic HTML, JavaScript, download, cookie, console, and extension-interaction fixtures.
- Create a fresh user-data directory under `test-output/headless-debug` for each test run.
- Run Chrome as the non-root build/test user.
- Do not pass `--no-sandbox`.
- Set only required deterministic flags such as headless mode, user-data-dir, no-first-run, and remote-debugging transport.
- Mount an explicit `/dev/shm` tmpfs for every browser-test container, initially `rw,nosuid,nodev,noexec,size=1g`, and record its configured size. Do not rely on Docker's small default shared-memory allocation or work around exhaustion with `--disable-dev-shm-usage`.
- Give `/tmp` a separate bounded disposable tmpfs where useful; browser profiles, downloads, screenshots, and diagnostics remain in the named work volume so failures survive container exit.

### 15.2 CDP pipe test

Use a small standard-library client for Chromium's NUL-delimited JSON remote-debugging pipe protocol over the required inherited file descriptors. Validate:

The consolidated runner uses the pipe phase for transport, target, navigation, evaluation, and screenshot checks; the TCP phase below owns the shared browser-behavior assertions so the same scenario is not duplicated.

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

Deferred for the initial headless milestone. The first profile disables DevTools frontend generation and validates raw CDP only; bundled frontend resources and local frontend loading return with the later DevTools scope.

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

### 15.8 Current implementation status

Covered by the repository-owned runner:

- 15.1 profile isolation, loopback fixture serving, non-root launch, deterministic flags, bounded `/dev/shm`, and disposable `/tmp` are wired through `test-fast`/`test`.
- 15.2 pipe transport, 15.3 TCP transport, storage/network/console/DOM/download/screenshot behavior, and target cleanup are exercised in one full run.
- 15.4 real pinned uBlock plus a local Manifest V3 extension are loaded offline; CDP observes the extension context and target.
- 15.6 has a checksummed H.264/AAC fixture, repeated media lifecycle cycles, codec/frame assertions, local HTTPS navigation before and after media, `/proc` library-map collection, and a dynamic-symbol intersection report in strict Linux runs.
- 15.7 has GPU diagnostics, non-empty screenshot checks, sandbox evidence, no-`--no-sandbox` enforcement, and an unprivileged-user-namespace check.

Completed against the native arm64 Alpine build:

- Strict `test` passed with Alpine system FFmpeg and Mesa llvmpipe. The staged result is `/work/test-output/headless-debug/test.json` with TCP and pipe phases complete, H.264/AAC metadata and playback, three media lifecycle cycles, nonzero video pixels, uBlock enforcement, HTTPS before and after media, and no surviving Chromium processes.
- The reviewed `/work/test-output/headless-debug/media-runtime.json` baseline records the three loaded Alpine `libav*` libraries, no bundled `libffmpeg.so`, and no high-risk Chrome/media symbol intersections. Later runs compare against `/work/metadata/media-runtime-baseline.json` and report no drift.
- `make stage-runtime` transactionally stages the six required headless runtime files with checksums in `runtime-manifest.json`; `make test-staged` passed the same strict test from `/work/stage/headless-debug/chrome`.
- The final GPU evidence is native Mesa `(gl=egl-gles2,angle=none)` llvmpipe, `sandboxed=true`, `processCrashCount=0`, and renderer processes with `Seccomp=2`. No `--no-sandbox` flag was observed.

Deferred by design:

- 15.5 DevTools frontend loading remains deferred because the current profile disables frontend generation. Raw CDP is covered.

## 16. ELF, link, and dependency audits

Use LLVM readelf/readobj/nm/strings for final audits. Resolve every executable and shared library produced for the runtime stage.

Hard failures include:

- glibc interpreter or `libc.so.6`;
- `libstdc++.so.6`, `libgcc_s.so.1`, or `GLIBCXX_*` requirements in a Chromium-owned ELF;
- static libstdc++/libgcc inputs in any recorded Chromium link command;
- a path to `libstdc++`, `libgcc_s`, X11, VA-API, VDPAU, or another exception soname that does not descend from a locked FFmpeg `libav*` root;
- a resolved compiler/linker path outside `/opt/llvm-musl` in the recorded Chrome graph, except for the single approved absolute ccache wrapper executable in compile commands;
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

- ccache, its cache contents, or any library present only for the builder-side ccache exception;
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
- environment compatibility identity and build-driver schema version;
- ccache APK/dependency/license provenance, effective configuration, and final statistics, while excluding cached object contents;
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

Container deployments must also provide adequate `/dev/shm`; the tested baseline is a 1 GiB tmpfs with `rw,nosuid,nodev,noexec`. The required-host documentation explains how to size it from workload concurrency and treats shared-memory exhaustion as an environment failure, not a reason to disable Chromium features or redirect shared memory silently.

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
7. Use a new named work volume or explicitly reset the old one; never patch a stamped old source tree in place.
8. Run `fetch`, then offline `prepare`, and re-run every PoC gate before starting Chrome.
9. Re-run functional, ELF, and packaging tests.
10. Complete `verify-cold` for the new compatibility tuple before publishing an artifact.

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

### Compiler-cache correctness, disk growth, and GNU containment

Risk: ccache duplicates object storage, cache misses add overhead, unsafe sloppiness can return stale output, and Alpine's ccache executable brings the deliberately approved builder-side `libstdc++`/`libgcc_s` exception. Mitigation: bound each architecture cache initially to 10 GiB, use content-based compiler checks and stable paths, prohibit sloppiness initially, collect hit/miss/eviction and disk statistics, and make cache deletion/disable harmless to correctness. Audit the wrapper's resolved compiler on every PoC, reject GCC/G++ or GNU binutils in generated commands, and prohibit ccache plus its runtime closure from staging. A suspected false hit requires disabling/clearing the cache and reproducing from normal Ninja inputs before diagnosing Chromium.

### Compatibility-key under- or over-invalidation

Risk: hashing the whole image discards valuable output after harmless wrapper/test changes, while omitting a true compiler-affecting input can incorrectly reuse Ninja or ccache results. Mitigation: record the complete image digest for provenance, define compatibility fields explicitly, version the build-driver compatibility schema deliberately, compare field-by-field, and test representative harmless and material changes. When uncertain, bump the narrow relevant compatibility field; never delete a volume automatically.

### Network leakage during source preparation

Risk: if preparation shared a network-capable phase, a source helper, version generator, or upstream hook could silently download an undeclared input. Mitigation: confine networking to `fetch`, which only downloads and verifies inert locked files; run the entire preparation container with `--network=none`; do not execute downloaded programs; and re-verify the content-addressed archive immediately before extraction. Any new network requirement must become an explicit locked fetch input and cannot be hidden inside a patching hook.

### Development-dirty state confusion

Risk: an in-place patch experiment is mistaken for the clean stamped source, a PoC result is reused across a different dirty chain, or an experimental binary reaches packaging. Mitigation: preserve the immutable clean base identity, derive an ordered dirty identity from every patch digest, key allowed phase stamps to that identity, print `DEVELOPMENT-DIRTY` prominently on every command, and hard-block audit/package/publishable export. A clean acceptance run always begins from reset/prepare rather than attempting to reverse patches heuristically.

### Cold-build proof hidden by caches

Risk: persistent Ninja or ccache outputs conceal a missing input, a broken generator, or a compiler-wrapper error. Mitigation: require `verify-cold` once per release-eligible compatibility tuple using fresh volumes, empty output, ccache disabled, and no network from preparation onward. Preserve failed cold volumes for diagnosis and record their identities; cache-assisted iterative success alone is not hermetic acceptance evidence.

### Disk-budget drift

Risk: source, object, link-temporary, ccache, or packaging growth causes a late ENOSPC failure after hours of compilation. Mitigation: commit architecture-specific measured high-water budgets, compare them in `status`, retain a safety margin, report reclaimable optional data, and update budgets on material version/profile changes. Do not turn first-run guesses into an inflexible hard gate.

## 22. Completion criteria by phase

### Environment complete

- Both architecture mappings, input locks, and image definitions pass static validation.
- ARM64 image builds natively under OrbStack.
- Package and LLVM locks match the installed environment.
- The image digest is recorded separately from the environment compatibility identity; a test/wrapper-only image change has been proven not to invalidate compatible `src/` or `out/`.
- Exact Alpine ccache provenance and dependency closure are locked, its per-architecture named volume is bounded/configured, and its resolved wrapped compiler is under `/opt/llvm-musl`.
- No generic compiler/binutils path can accidentally override `/opt/llvm-musl`.
- Candidate system-library closures have been audited and their private/host/FFmpeg-exception/forbidden classifications are recorded.
- The exact stock-FFmpeg exception graph is accepted only within its committed roots and contains no glibc.

### Fetch complete

- At fetch completion, the exact locked Chromium archive exists under its content-addressed name in `/work/inputs` or the optional download volume; disk-minimal preparation may subsequently consume the `/work/inputs` copy only after stamping source complete.
- Expected size and SHA-512 have been verified, the final resolved URL is recorded, and no downloaded program was executed.
- The atomic fetch stamp distinguishes an available archive from a disk-minimal archive already consumed by successful preparation.
- No extraction, patching, or source generation occurred in the networked phase.

### Source preparation complete

- No Git clone exists.
- Trimmed source is verified and extracted; its archive is removed in disk-minimal mode or retained only as a verified compressed download-cache entry in iteration mode.
- All patch layers and domain substitution succeed.
- Source identity/provenance is complete and repeatable.
- Preparation and representative version generators ran with networking disabled and did not require Git; locked revision/version metadata is complete.
- Any `dev-apply-patch` use leaves an unmistakable development-dirty state that blocks audit and packaging until a clean reset/prepare.

### PoC complete

- Direct compiler/runtime/system-library probes pass.
- GN generation passes with no unused args.
- ccache is the configured compiler wrapper, reports the intended Laputa compiler, and remains a builder-only exception.
- `optimize_webui=false` and every supported DevTools fast-build argument have their effective values recorded.
- Small GN smoke executable builds/runs.
- Chrome dry-run and command audit pass.
- No in-tree libc++, GCC, libstdc++, or libgcc compiler/link input is scheduled for Chromium-owned targets, and the system-FFmpeg graph matches the locked exception policy.

### Functional browser complete

- Monolithic Chrome builds from the persistent volume.
- Modern headless mode runs with sandboxing and host llvmpipe.
- Raw CDP pipe/TCP, downloads, screenshots, storage/network events, and local extension tests pass offline. DevTools frontend resources are deferred from the initial headless profile.
- `test-fast` supplies the short post-link acceptance loop; the complete focused `test` suite passes at the milestone without building upstream test suites.
- The strict Linux `test` run also emits `media-runtime.json` with loaded process-tree maps and reviewed Chrome/FFmpeg dynamic-symbol intersections.
- System-FFmpeg H.264/AAC decode/containment tests pass without disturbing Chrome's static-libc++ identity or Mesa llvmpipe rendering.
- ELF/link audits pass.

This is the first full-build success milestone. Packaging is not required to call the browser functional.

### Packaging complete

- Minimal runtime closure is staged and tested.
- Tar.zst and all manifests/audits/checksums are exported without a bind mount.
- A clean Alpine 3.24-compatible host can run the bundle using documented host dependencies.
- The copied FFmpeg exception closure exactly matches preflight, and its package/license/source provenance accompanies the artifact.

Packaging success from an ordinary cache-assisted build produces a candidate artifact. It is not release-eligible until the cold acceptance criterion below passes for the same compatibility tuple.

### Cold acceptance complete

- `verify-cold` used fresh work and cache volumes, empty source/output/stage state, and `CCACHE_DISABLE=1`.
- Any reused bytes were limited to the independently re-verified compressed fetch archive; a cache miss may download it normally during the isolated fetch phase. `prepare` and every later phase ran with networking disabled.
- PoC, full Chrome build, complete focused tests, audits, package creation, staged-runtime retest, and export all passed.
- Cold-build timing, peak disk/memory, Ninja/link statistics, the empty ccache directory before/after proof, and the effective `CCACHE_DISABLE=1` environment are stored as zero-cache-use evidence with the artifact metadata.
- The cold result matches the same environment/source/profile compatibility tuple as the candidate intended for release.

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
- Remote compiler caches, sccache, distributed compilation, and remote execution; the approved v1 cache is local stock Alpine ccache only.
- Component builds until a pinned Laputa toolchain release provides validated shared musl libc++/libc++abi/unwind artifacts.

## 24. Current handoff state

The native arm64 `headless-debug` MVP is complete in the persistent work volume. The full `chrome` target passed Gates E/F and is cached at `/work/out/headless-debug/chrome`. `make check-py`, `make test-fast`, strict `make test-functional`, `make stage-runtime`, and strict `make test-staged` all pass. The final staged test is the acceptance result recorded in `/work/test-output/headless-debug/test.json`.

The final browser evidence is native Mesa `(gl=egl-gles2,angle=none)` llvmpipe, GPU `sandboxed=true`, GPU `processCrashCount=0`, renderer `Seccomp=2`, H.264/AAC playback with `framePixels=143104` and `repeatCycles=3`, TCP and pipe CDP, uBlock/extension behavior, HTTPS before and after media, downloads, screenshots, and cleanup. The reviewed media baseline is `/work/metadata/media-runtime-baseline.json`.

`make stage-runtime` creates a fresh six-file runtime directory with checksummed `runtime-manifest.json`; `make test-staged` runs the same strict acceptance directly against the staged Chrome. `LLVM-TOOLCHAIN.md` records custom prebuilt LLVM quirks. The active work and ccache volumes remain reusable for later packaging/audit work.

After the first full build succeeds, record the measured disk high-water mark before pruning anything. Capture at minimum:

- source size and inode count;
- profile `out/` size before and after the final link;
- temporary bytes and aggregate filesystem usage during the final link;
- ccache size, configured limit, compression ratio, and before/after statistics;
- test-output, staging, and export sizes;
- peak aggregate bytes, free-space margin, architecture, source/environment/profile identities, job/load settings, and the command used to measure them.

Write the native arm64 result to `config/disk-budget.arm64.json`. Generate `config/disk-budget.amd64.json` only after a native amd64 measurement; never copy arm64 values. The first measurement is advisory, but later status checks should use it to warn before starting a multi-hour build and identify recoverable bytes from old profiles, test output, staging, download archives, and ccache.

## 25. Overnight MVP execution prompt

Use the following prompt when handing this repository to an autonomous coding agent:

> Continue the current native arm64 headless-debug build toward the first functional MVP. Preserve the active Docker/OrbStack build container, named work volume, Ninja output, and ccache volume; never restart from a clean source tree or delete resumable output merely because the build is slow. Poll the build regularly and report meaningful progress, CPU utilization, cache behavior, disk margin, and exact failures.
>
> First complete the full Chromium `chrome` target. A successful full build means the build driver writes its completion report, the resolved executable exists in the persistent work volume, and the existing Gates A–F, Python checks, compiler/toolchain audits, and ELF/link audits remain passing. If compilation fails, diagnose the first real error, make the smallest source/configuration fix consistent with this plan, run the narrowest proof that reproduces the failure, rerun the relevant gate, and resume the same cached build. Do not disable security, sandboxing, runtime checks, or required browser functionality just to bypass an error. Record any custom LLVM-toolchain quirk in `LLVM-TOOLCHAIN.md` and update the plan only when the build contract changes.
>
> After the full build succeeds, run the short post-link acceptance path with `./chromium-build --arch arm64 --profile headless-debug test-fast`. Fix deterministic failures and repeat until it passes. Then run the complete offline functional headless path with `./chromium-build --arch arm64 --profile headless-debug test`; it must pass as the strict Linux test described in `TESTS-TODO.md`, including TCP and pipe CDP, screenshots, downloads, local extension/uBlock behavior, H.264/AAC media, HTTPS before and after media, sandbox evidence, cleanup, and `media-runtime.json`. Iterate on real failures until this functional headless milestone passes.
>
> Only after the functional headless tests pass, work through the remaining acceptance items in `TESTS-TODO.md`: review the media runtime and symbol-intersection baseline, rerun from the staged runtime, preserve all required reports, and update `plan.md` with evidence. Do not expand into Wayland/ALSA or unrelated deferred scope before the functional headless milestone is complete. Keep networking disabled for preparation, builds, tests, audits, and packaging; use only the locked fetch inputs when network access is genuinely required.
