# CHB-11 arm64 builder and cache smoke

The arm64 builder image builds successfully, and its C and C++ compiler
cache survives container recreation. No full Chromium build, browser package
or macOS workflow comparison ran. The owner requested this bounded smoke
instead of a cold browser build while the Mac is in use.

The Mac's active OrbStack daemon initially had neither Helium's arm64 builder
image nor its named work and ccache volumes. The default Docker context points
to the same daemon. There was no warm local arm64 cache to reuse.

The prerequisite refresh updates only the arm64 package world: Go 1.26.11,
Node 24.22.3, NSS 3.133 and the resolved transitive package manifest on the
already pinned Alpine 3.24.2 image. The pure-musl NSS source also moves to
3.133 so the runtime matches the installed development headers.
The source archive's size, SHA-512 and SHA-256 are verified and recorded in
the input and source locks. Rust and LLVM pins are unchanged.
This refresh unblocks the current image build. CHB-10's durable package-mirror
or snapshot requirement remains open.

The image command was:

```sh
./chromium-build --product helium --arch arm64 image --jobs 2
```

`--jobs` now reaches the pure-runtime stage's make and Ninja invocations.
Its default remains the container's CPU count. The resulting image is
`helium-builder:0.20.3.1-arm64`, digest
`sha256:f15519f892f423b8e2e74674f1cb84673a3004149bccaf6cef1bac7c49b4b175`.
The committed source includes later help and documentation edits, so another
image build can produce a different digest without changing the compilers.

## Cache proof

The smoke uses the image's production `CC`, `CXX`, ccache executable,
compiler-command namespace and persistent volume
`ungoogled-chromium-153-ccache-arm64`, mounted at `/ccache`.
Each container has no network, a two-CPU cap and a 2 GiB memory limit.
It compiles, links and runs one C program and one C++ program.
Both programs must print `42`.

Before the second run, the first container has exited and the object files
and executables are deleted. The second container uses the same source paths
and the same named cache volume:

| Counter | Before first run | After first run | After resumed run |
| --- | --- | --- | --- |
| Direct cache hits | 0 | 0 | 2 |
| Cache misses | 0 | 2 | 2 |

The object and executable hashes match across both runs. This establishes
persistent C/C++ cache reuse. It does not establish that Chromium's full
dependency graph compiles or that a later browser rebuild will be mostly
cached. The cache now contains only this smoke's two compilation entries.
The named `helium-0.20.3.1-work-arm64` volume is created but contains no
prepared Chromium tree or browser build output.

[The proof files](../tests/results/chb11-arm64-smoke/) include the smoke sources,
runner, raw counters, ccache configuration, output hashes and compressed
image-build log. The retained image can run the same smoke without rebuilding.
From the repository root, mount those proof files at `/work/chb11-cache-smoke`
and the named ccache volume at `/ccache`, then run the script with a fresh pass
name. The script overwrites its object and executable outputs.

## Checks and deferred work

The new image-job forwarding test and five source-lock tests pass on macOS.
The Linux runtime suite runs inside the new image: 30 tests, 28 passed and
two environment-dependent skips. The initial attempt to run that ELF suite
on macOS failed because its Linux compiler/linker and `/bin/true` assumptions
do not hold there. The Linux result is the applicable evidence.
Shell syntax, package-manifest validation and source-lock consistency pass.

The refreshed NSS runtime and begin-frame candidate have no new browser
acceptance evidence from this smoke. A full arm64 build, isolated comparison
baseline, browser qualification and macOS CPU/workflow measurements remain
deferred at the owner's request. No archive, release, default bctl image or
consumer pin changed.
