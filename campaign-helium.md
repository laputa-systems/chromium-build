# Build Helium for Linux amd64 musl

## Status

Goal: deliver a relocatable Helium desktop browser for native Linux amd64 musl,
while preserving Chromium Linux and native macOS builds.

Preflight verdict: ready for a separately requested implementation run, against
`506fbac`. No implementation slices
are accepted. No browser, sandbox, or final-package gate has run.
Next: H0, product isolation. Execution requires a separate owner instruction.
Resume prompt: `Execute campaign-helium.md, starting with H0. Do not push.`
First command: `git status --short`.

## Charter

Execute only after the owner requests implementation. This document does not
authorize its own execution. Keep all integration changes in this repository.
Follow AGENTS.md. Do not run formatters, linters, hooks, or CI. Do not install
host tools, change macOS behavior, push, publish, or send patches upstream.
Commit only when the owner explicitly requests commits.
Preserve source/signature verification and all existing acceptance gates.

Use one writer. Shared orchestration, locks, and reports make the initial
slices dependent. Do not add parallel agents without owner authorization.
Use `.work/wt/<name>` for any worktree and `.work/tmp` for scratch, with
`.work/` excluded through `.git/info/exclude`. Preserve source trees, Ninja
outputs, caches, and incomplete preparation evidence.

Choose the smallest change that delivers each slice. After each accepted
slice, reconcile the result with the goal and select the next dependency.
Write failing behavior tests before implementing clear contracts. For a bug,
keep the minimal reproduction as a regression test.
After two failed fixes, establish the cause before another attempt. After
three attempts without new evidence, park the slice with its cause and retry
condition. Continue independent useful work. An unavailable release gate
remains unverified even when all local implementation work is finished.

Feature parity precedes optimization. Do not rewrite the framework, create a
second CLI, add speculative configuration, or build a new acceptance framework
before a real browser pilot. Repair a broken in-scope prerequisite separately
when it prevents verification. Leave unrelated baseline failures alone.

## Decisions

### Owner decisions

- Target Linux x86-64 musl only. Preserve both existing products and platforms.
- Produce actual Helium, including UI, vertical tabs, privacy defaults,
  onboarding, bundled uBlock Origin, and extension support.
- No shipped glibc, gcompat, static-browser requirement, or sandbox bypass.
  Pinned glibc for the build-time upstream compiler remains permitted.
- On 2026-10-31, the owner excluded Shadowdriver from Helium builds.
  Extend the Linux CDP harness. Leave macOS acceptance unchanged.
- The source request's implementation mission defines the eventual outcome.
  The active `/campaign-plan campaign-helium.md` request limits this task to
  planning and preflight.

### Prescribed decisions

- Add `--product chromium|helium`, defaulting to `chromium`. Reject unknown
  products and Helium on macOS or arm64 before Docker, downloads, or mutation.
  Preserve existing Chromium image, volume, path, and profile defaults.
- Default Helium to `helium-wayland-release`. Keep its work volume, image tag,
  source, outputs, metadata, staging, packages, and stamps separate. Explicit
  volume overrides must also validate product ownership before any mutation.
  Use `helium-builder:0.20.3.1-amd64` and `helium-0.20.3.1-work-amd64` as
  defaults. Keep existing `/work` subpaths inside that isolated volume. Reuse
  the existing ccache volume only after its compiler/configuration checks pass.
- Keep the existing Chromium and macOS locks unchanged. Introduce
  `config/helium.inputs.lock`, `config/helium.packages.amd64.lock`,
  `config/helium.patch-inventory.json`, and
  `config/helium.patch-disposition.json`. Use the existing formats where they
  express the product. Add only fields required by actual consumers.
- Reuse the Dockerfile with product-selected inputs and pinned package sets.
  Existing Alpine package pins are no longer all downloadable from current
  repositories. Resolve Helium pins independently. Do not refresh Chromium
  pins or silently use latest packages to make a new image succeed.
- Put product identity and input identity in every phase authorization.
  Bind reports to product, version, architecture, profile, preparation inputs,
  configuration, and toolchain where relevant. Reject stale or mismatched
  reports. A filename or `status=complete` alone never authorizes a build.
- Share only verified immutable downloads and compatible compiler cache data.
  Use the existing compiler-content identity and correct source/configuration
  keys. Do not force cache hits through sloppiness settings. Do not invent
  shared-download infrastructure if separate verified caches suffice initially.
- Follow upstream preparation ordering, using upstream utilities offline.
  Do not apply the Chromium 153 ungoogled series to Helium. Add only reviewed
  musl and local integration patches after the Helium layers.
- Apply Helium patches and retained compatibility patches with zero fuzz.
  Record and review offsets. Never silently skip a failed patch. Classify every
  inherited patch as integrated, required, superseded, incompatible, or
  headless-only, with source evidence. A clean dry run does not prove need.
- Keep browser UI, DevTools resources, and functioning desktop backends.
  Do not inherit headless stubs or Crashpad removals by default. Build the
  `//chrome:chrome` target and resolve its real `helium` output from GN.
  Retain the upstream Crashpad helper when the selected graph requires it.
- Start with native Wayland, ALSA, and Mesa software rendering. Prefer no
  PulseAudio, PipeWire, systemd, or D-Bus dependency. Allow a necessary desktop
  library when source and a functional test demonstrate need. Do not require
  an X server, Vulkan, VA-API, hardware GPU, or remote build service.
- Disable upstream sysroot and Siso use in favor of the existing musl toolchain
  and Ninja. Validate every override against Chromium 156 declarations using
  `gn gen --fail-on-unused-args`. Preserve meaningful diagnostics. No global
  new error suppression to get a compile through.
- Package `helium-0.20.3.1-linux-x86_64-musl.tar.xz`. Derive required files from
  GN runtime dependencies and the upstream packaging manifest. Include all
  locales and required helpers, metadata, icons, fonts, ICU, snapshots, graphics
  libraries, and resources. Do not rename a Chromium binary to imitate Helium.
- Audit every shipped ELF and recursively close dependencies, including NSS
  modules and dynamically loaded graphics libraries. Keep any musl-built GNU
  C++ runtime exception limited to the existing audited FFmpeg closure. Never
  allow glibc or that exception in the browser's direct dependencies.
- Prefer additions to existing reports and harnesses over new registries.
  Include release checksums because the package consumer needs them.

### Delegated decisions and bounds

Select the smallest necessary desktop dependency set using GN declarations,
link errors, and the Wayland pilot. Document any intentional feature exclusion
and test the resulting behavior. Do not exclude any defining Helium feature.

Try the existing pinned Clang 24 toolchain first. Change Helium's toolchain
only for a reproduced incompatibility and pin every replacement artifact.
Verify Rust, bindgen, Node, GN, and Ninja compatibility through generation and
focused compile targets. Do not change a pinned Rust nightly without an
explicit owner request.

Additional dependencies must have a maintained source, acceptable license,
locked digest or package version, and a demonstrated consumer. Prefer existing
tools and the standard library. Measure build-time and package-size impact
when adding one. Product performance tuning belongs after functional parity.

## Baseline and foundation evidence

The checkout is `master` at `506fbac`. The initial staged file is this mission
document. No prior `docs/CAMPAIGN-*` or `DECISIONS.md` was found.

Current `./chromium-build --help` succeeds. A current invocation with
`--product helium` exits 1 with `unknown argument --product`, before Docker.
There is no existing product selector.

Current focused command:

```sh
TMPDIR="$PWD/.work/tmp" CHROMIUM_TEST_TMPDIR="$PWD/.work/tmp" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:tests \
  python3 -m unittest test_fetch_inputs test_browser_test_support -q
```

Result: 10 tests pass; unittest reports 2.679 seconds. This is a loop observation,
not a performance comparison or gate.

The recovered baseline run of 85 unit tests reports 3 failures and 14 errors,
all in macOS tests. Missing Linux-host facilities include `sandbox-exec`,
`ditto`, `codesign`, Node acceptance tooling, and a sibling Shadowdriver tree.
These results do not establish a macOS regression or native macOS parity.
Do not delete or weaken those tests.

The recovered `make check-py` failed before checking code because pinned Ruff
has no wheel for this musl host. Do not rerun it: it invokes a linter forbidden
by current project instructions. Use focused Python tests instead.

The source-preparation probe under `.work/tmp` completed. Its retained results
show dependency unpacking, pruning, both patch layers, domain substitutions,
name substitutions, translations, version generation, resource generation,
and replacement all returning zero. All 353 Helium patches applied with
`--fuzz=0`. The prepared `chrome/VERSION` contains Chromium `156.0.8078.79`
and Helium `0.20.3.1`. This proves a viable preparation foundation, not a
compiled browser or a verified release pipeline.

Independent dry-run classification against that prepared tree:

| Source | Applies with zero fuzz | Needs fuzz | Fails | Already applied |
| --- | ---: | ---: | ---: | ---: |
| Existing local patches | 52 | 13 | 11 | 0 |
| Alpine 156 candidates | 9 | 11 | 1 | 0 |
| Copium 156 candidates | 22 | 3 | 4 | 1 |

These counts are discovery evidence. They neither select patches nor prove
that patches compose correctly. The scratch classifier continued after
failures, so its final exit status is not a release gate.

The tagged Linux repository resolves to `83027a19267e526f08e25a3af1a30b45ee7e3e2e`.
Its `helium-chromium` gitlink is
`2f81b9cafa38a95071e20de3f9cd6b0aca6d3c65`, matching the checked-out core and
its `chromium_version.txt`. Pin these exact revisions. Lock the bytes of the
prepared base and Linux-x64 source archives and all dependencies before use:
onboarding `202610161403`, nonfree search data `202609082320`, and Imput's
uBlock Origin `1.75.0`. Upstream `deps.ini` supplies their SHA-256 digests.
Resolve source hash metadata during fetch, then freeze verified digests in
the lock. Preparation must never consult a mutable upstream hash URL.

The existing compiler is Chromium Clang `24.0.0git`, revision
`llvmorg-24-init-3796-g20e97c4b-2`. Reuse its locked archive and build-only glibc
setup first. Source preparation did not prove compiler or Rust compatibility.

The current host is native Linux amd64 with about 60 GiB RAM and 332 GiB free
on the repository filesystem at inspection. This is not proof of enough final
link or build space. Preserve existing volumes. Before a full build, inspect
its filesystem and memory requirements and report the expensive run. If
resources run short, retain Ninja state and give the exact resume command.
No full build was started during planning.

## Sequence

All items run serially because their consumers overlap. The executor may
subdivide an item by a demonstrated consumer, keeping its acceptance intact.
Commands below that name Helium support or new tests are implementation
acceptance contracts; they do not exist yet. Add each test only when its slice
introduces the behavior. Run tests with repository-local scratch as above.

| Item | Size | Prerequisite | Owned files | Local acceptance and threshold |
| --- | --- | --- | --- | --- |
| H0: isolate product selection end to end | M | execution grant | `chromium-build`, `Makefile`, shared path/lock consumers, `tests/test_product_selection.py` | `PYTHONPATH=scripts:tests python3 -m unittest test_product_selection -q`; default Chromium dispatch matches explicit Chromium, all unsupported combinations fail before effects, every Helium phase receives separate identity and paths, conflicting volume ownership fails |
| H1: fetch and prepare pinned Helium | M | H0 | Helium locks and inventories, fetch and preparation scripts, `tests/test_helium_prepare.py`, Dockerfile product inputs | `PYTHONPATH=scripts:tests python3 -m unittest test_helium_prepare -q`; corrupted bytes and interrupted preparation fail closed, offline ordering matches upstream, retries preserve valid outputs; run real `image`, `fetch`, `prepare` as milestone below |
| H2: generate desktop graph and compile musl probes | M | H1 | Helium profile, selected compatibility patches, GN and toolchain scripts, graph audit consumers, `tests/test_helium_gn.py` | `PYTHONPATH=scripts:tests python3 -m unittest test_helium_gn -q`; rejects unused arguments and stale/cross-product gates; then real Helium `poc` passes A through F and targeted failing objects compile |
| H3: pilot a real Helium Wayland browser | L | H2 | build and resolver consumers, minimal additions to Linux CDP harness, `tests/test_helium_browser.py` | `PYTHONPATH=scripts:tests python3 -m unittest test_helium_browser -q`; then real `build` and `test-fast` open a Wayland window, load a local rendered page, identify Helium, and collect enabled sandbox evidence |
| H4: stage and package the actual desktop graph | M | H3 | staging and packaging scripts, ELF closure audit, `tests/test_helium_runtime.py` | `PYTHONPATH=scripts:tests python3 -m unittest test_helium_runtime -q`; missing helpers/resources, wrong loaders, nested glibc dependencies, escaping symlinks, and incomplete closures fail; then real `stage-runtime` and `package` produce the relocatable archive |
| H5: complete browser and package acceptance | M | H4 | Linux functional fixtures/drivers, product report validation, `tests/test_helium_acceptance.py` | `PYTHONPATH=scripts:tests python3 -m unittest test_helium_acceptance -q`; each missing required observation fails; then real `test` and `test-staged` pass the behavior matrix on build, stage, and extracted package |
| H6: harden and document the supported path | M | H5 local tests | affected contract consumers/tests, `docs/linux.md`, CLI usage, Makefile, AGENTS.md only if necessary | Run all five Helium test modules plus `test_product_selection`, `test_fetch_inputs`, and `test_browser_test_support`; clean-checkout instructions reach the same locks and artifact, negative cases fail, existing Chromium dispatch remains unchanged |
| H7: qualify reuse and close | M | H6 | necessary cache/performance fixes and final report in this document | Run the affected tests after any fix; real repeat `prepare`/`build` preserves outputs and Ninja resumes; cache, size, memory, final-package and environment gates are reported distinctly |

H0 is the first useful slice: implement selection, ownership validation, and
propagation through real command consumers. Use a fake Docker executable in
the test to capture arguments and prove unsupported requests make no calls.
Test omitted versus explicit Chromium against the existing defaults. Do not
claim isolation from a helper test while callers still read the Chromium lock.

For H1, reproduce upstream's exact operation order: unpack prepared base then
Linux overlay, unpack dependencies, prune, apply core patches, apply Linux
patches, substitute domains, substitute names, apply translations, generate
version metadata, generate resources, replace resources. Then apply selected
musl/integration work at its verified ordering boundary and perform existing
required system-unbundle and builder setup. Mutate extracted copies of upstream
utilities/resources, never shared immutable input trees. Verify ordering of
any compatibility patch that touches substituted text with a real preparation.

Keep temporary preparation and completion atomic. Bind reuse to input and
recipe identity. A failed attempt must leave no success stamp. A changed input
must not silently authorize an existing tree. Preserve that tree and fail with
an actionable next step. Do not automatically delete it or an `out/` directory.

H2 must classify the complete existing inventories, including portability and
local patches, against the actual 156 source. Review sandbox, musl headers,
threads/signals, Crashpad, allocator interfaces, Rust/bindgen, LLVM, libc++, NSS,
fonts, EGL, shared memory, and syscalls. Retire redundant patches with evidence.
Do not replay every patch from Alpine or Copium. Port only required behavior.

H3 is the end-to-end pilot. A full browser build is an expensive milestone,
not a minutes-long slice test. Run compiler probes first and announce the full
build. Use the real Helium-branded GN output. Record cache statistics and build
duration when available, without treating probes or GN success as browser proof.
Expand the harness only after this pilot reaches a real window and renderer.

H4's archive may be an unqualified candidate. H5 must test a fresh extraction
at a different path with a scrubbed environment and no access to build-tree
libraries. A candidate archive never becomes a release merely because it exists.
Document the minimum tested RAM, disk, Docker, kernel sandbox, and native
architecture requirements with the clean-checkout build path in H6.

## Acceptance

Use this command prefix after H0:

```sh
./chromium-build --product helium --platform linux --arch amd64 \
  --profile helium-wayland-release COMMAND
```

Run `image`, `fetch`, `prepare`, `poc`, `build`, `test-fast`, `stage-runtime`,
`package`, `test`, and `test-staged` in dependency order. Make `test-staged`
validate the stage and a fresh package extraction when a candidate exists.
Require matching acceptance reports before labeling any artifact release-ready.

Each deliverable has a distinct failure condition:

1. **Product isolation:** H0 tests prove Linux/macOS rejection, profile and lock
   selection, independent version handling, paths, volume ownership, cache
   configuration, and report identity. Feed Chromium evidence to a Helium
   consumer and require failure. Change a Helium input and require Helium
   invalidation while unchanged Chromium remains reusable.
2. **Reproducible sources:** H1 tests and real offline `prepare` prove locked
   digests, dependency completeness, upstream ordering, zero-fuzz patch layers,
   reviewed provenance, resource/version generation, and resumability. Deny
   network after `fetch`. Corrupt one dependency and require failure before
   extraction. Interrupt preparation and require no completion marker.
3. **A real musl desktop graph:** `poc` validates toolchain/cache/source gates,
   strict GN generation, desktop feature flags, graph auditing and its independent
   oracle. Check the real selected target rather than headless-only allowlists.
   H2 tests must catch a link command incorrectly wrapped by ccache and a compile
   command missing the musl target. Never treat a headless gate as desktop proof.
4. **Browser identity and features:** `test-fast`, `test`, and `test-staged`
   observe the matrix below. A missing observation is a failed or unexecuted
   gate, never an implicit pass. Test the bundled Imput uBlock component without
   installing the Chromium 1.72.0 fixture as a substitute.
5. **Runtime and package closure:** `stage-runtime` and `package` enumerate all
   ELF files, check x86-64 architecture, musl interpreters on executables, GNU
   runtime policy, recursive dependencies and required dynamic modules. Verify
   resources against the real graph. Require no build-only absolute paths.
   Missing libraries, glibc loaders, `libc.so.6`, and unsafe archive entries fail.
6. **Regression protection:** H6 runs the narrow integrated Linux suites and
   tests existing Chromium omitted-product commands against explicit Chromium.
   At H6 and close, run the affected test set together. Native browser parity
   for Chromium and macOS requires their real environment checks below.
7. **Resumability and efficiency:** repeated `prepare` is a verified reuse or
   an explicit non-destructive refusal. Repeated `build` uses existing Ninja
   state. Record actual ccache counters, CPU/wall duration, peak memory, and
   archive size. A cache-stat command alone does not prove effective reuse.

### Browser behavior matrix

Reuse `browser_test_support.py`, local HTTP/HTTPS/media fixtures, CDP transports,
process cleanup, and sandbox inspection. Add a disposable Weston compositor
with its headless backend, a private socket, and software rendering. This is a
headed browser on a virtual Wayland display, not Chromium headless mode.
Pin compositor packages in the image. Reap compositor and browser children on
success, timeout, and cancellation.

For build-tree, staged, and extracted-package runs, require:

- A mapped Wayland window, local-page pixels, working HTML/CSS/JavaScript,
  navigation, ordinary tabs, and Helium vertical tabs.
- Helium version/branding, settings WebUI, onboarding, extension UI, and privacy
  defaults. Compare with the pinned source requirements and upstream tests.
- Bundled uBlock installed and active, one deterministic known blocked resource,
  and one allowed resource. Prove a disabled blocker makes the blocking test fail.
- A separate test extension works, downloads complete, and profile state survives
  browser restart. Missing downloaded files and lost state must fail.
- Real keyboard and Wayland clipboard interaction. CDP DOM assignment alone
  cannot prove compositor input works. Test file upload, fonts, and software
  graphics. Keep existing media checks where their contracts apply.
- Local HTTPS succeeds with a trusted fixture and rejects an untrusted chain.
  Preserve real runtime certificate validation.
- Renderer namespaces and seccomp satisfy the existing sandbox policy. Inspect
  relevant subprocesses while running a page. Never pass `--no-sandbox` or hide
  a bypass in a launcher. Record environment limitations as unverified gates.
- Clean shutdown leaves no owned browser, renderer, helper, or compositor process.

Use upstream source/tests as the initial behavior oracle. An upstream glibc
release may be a disposable reference only, never the shipped runtime. Record
intentional desktop integration differences alongside their tests. Keep any
remaining unverified behavior in Status until local proof or explicit environment
qualification closes it.

### Performance qualification

No speedup target is imposed before feature parity. Measure memory and binary
size even when the host is busy. Time-based performance gates run only after
`perf-gate-ready` succeeds and remain valid only if `perf-gate-ready -1` also
succeeds immediately afterward. Otherwise report `perf gate deferred: host busy`
and do not call release qualification complete. Use interleaved baseline and
candidate runs, CPU time for compute cost, and medians for typical durations.
Treat only differences larger than observed spread as regressions. Do not compare
full Helium desktop size directly with headless Chromium as an optimization gate.

## Needs environment

These gates do not block independent local implementation. They do block claims
of full release success. Run available proof during execution before declaring
an environment unavailable.

- **Representative Alpine:** after copying the artifact to a native amd64 Alpine
  host with Wayland and usable sandbox facilities, run its launcher against the
  same local browser fixtures and collect the full behavior/sandbox matrix.
  Planned command: `./chromium-build --product helium --platform linux --arch amd64 --profile helium-wayland-release test-staged` with the candidate imported into
  its isolated work volume. Document that import command in H4.
  For direct host acceptance, H5 must expose the existing Linux harness's
  executable override with explicit product and headed-Wayland selection.
  Container-only acceptance does not replace this normal-user host test.
- **Container sandbox constraints:** existing Linux test commands request
  `--privileged`. This cannot by itself qualify ordinary Alpine execution.
  Test the package as a normal user with the sandbox enabled. If namespace or
  seccomp restrictions prevent this, preserve the failure and require Alpine
  qualification rather than weakening policy.
- **Native macOS arm64 regression:** on the existing macOS checkout, run
  `./chromium-build --platform macos --arch arm64 doctor`, the affected macOS unit
  tests, and the existing staged acceptance command. Do not replace macOS
  Shadowdriver tests with Helium tests or claim Linux execution proves parity.
- **Existing Chromium runtime:** use its existing source/output and run
  `./chromium-build --product chromium --arch amd64 --profile headless-release test`
  and `test-staged`. If its build or image is unavailable, report that limitation.
  Do not rebuild unrelated Chromium merely to hide an unavailable baseline.
- **Full build resources:** if linking or disk capacity prevents completion,
  preserve state and give `./chromium-build --product helium --platform linux --arch amd64 --profile helium-wayland-release build --jobs N` with the justified actual
  job count. No package or acceptance claim follows a partial object build.

## Execution boundary and non-goals

The current task permits source inspection, bounded experiments, and this plan.
A future implementation instruction permits repository changes and local build,
fetch, and test effects needed above. Follow the owner's explicit commit policy.
No instruction here grants pushing, publishing, installing the application on
the host, upgrading host tools, modifying other repositories, or upstream contact.

Contract changes to surface during implementation are the CLI product selector,
product-specific profiles/locks, phase identity and invalidation, desktop runtime
manifest, complete ELF audit, archive format/name, and GUI/sandbox acceptance.
Update their callers, tests, and documentation together. Preserve old Chromium
commands, report readers, and macOS behavior. Do not add compatibility shims
without a demonstrated current consumer.

No Linux arm64, new macOS product, static browser, AppImage, Debian/RPM/Flatpak,
hardware-acceleration tuning, distribution service, CI work, or Shadowdriver
integration for Helium. Retain upstream-required helpers; building chromedriver
is not a requirement unless the chosen runtime graph or a concrete consumer
requires it. Any manifest difference from upstream must have a source or graph
reason.

At close, report passed, failed, and not-executed checks separately. Include
architecture reuse/new work, exact inputs and toolchain, patch dispositions,
commands/outcomes, cache evidence, ELF closure, GUI/sandbox proof, package contents,
size/checksum, limitations, and Chromium/macOS regression results. Report local
completion separately from release qualification. Leave no unreported manual
step. Preserve live fixtures and expensive resumable state. Remove only proven
obsolete campaign scratch or integrated worktrees.

## Code map

| Area | Existing owner | Treatment |
| --- | --- | --- |
| CLI, volumes, image, dispatch | `chromium-build:36`, `chromium-build:157`, `chromium-build:185` | change: product selection and identity propagation; preserve Chromium defaults |
| Build shortcuts | `Makefile:15` | change: product-aware defaults and Helium shortcuts without overriding CLI choices |
| Verified fetch | `scripts/fetch-inputs.py:34` | reuse verification/transport; change lock and multi-archive resolution |
| Atomic preparation | `scripts/prepare-source-driver.py:107` | reuse temporary-tree pattern; change product recipe and reuse identity |
| Legacy Chromium patch recipe | `scripts/prepare-source.py:268` | retain for Chromium; keep Helium layering distinct |
| GN generation | `scripts/generate-gn.py:37` | reuse strict generation; change profile/lock and desktop policy consumers |
| Ninja build and output | `scripts/build.py`, `scripts/resolve-output.py:14` | reuse build/resolution; remove product-specific executable assumptions |
| Graph verification | `scripts/audit-chrome-graph-driver.py`, `docs/gate-f-oracle.md` | reuse independent audit; inspect desktop target assumptions |
| Runtime collection | `scripts/stage-runtime.py:53` | change six-file headless manifest to product-derived runtime requirements |
| Runtime closure | `scripts/package-runtime.py:89`, `scripts/package-runtime.py:110` | reuse recursive collection; extend every-ELF and product checks |
| Browser fixtures/CDP/sandbox | `scripts/test-functional.py:321`, `scripts/browser_test_support.py:279` | reuse transports/fixtures; separate headed Helium arguments and bundled-extension checks |
| Native macOS | `scripts/macos_backend.py`, `config/macos.inputs.lock` | preserve; reject Helium before native dispatch |

Upstream references are the pinned local source counterparts of
[Helium Linux shared preparation](https://github.com/imputnet/helium-linux/blob/83027a19267e526f08e25a3af1a30b45ee7e3e2e/scripts/shared.sh),
[its packaging manifest](https://github.com/imputnet/helium-linux/blob/83027a19267e526f08e25a3af1a30b45ee7e3e2e/scripts/package.sh), and
[Helium dependency pins](https://github.com/imputnet/helium/blob/2f81b9cafa38a95071e20de3f9cd6b0aca6d3c65/deps.ini).
