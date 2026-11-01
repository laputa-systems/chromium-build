# Build Helium for Linux amd64 musl

## Status

Goal: deliver a relocatable Helium desktop browser for native Linux amd64 musl,
usable through the owner's `~/usr/bin/helium` in the Alpine dwl setup.
Preserve Chromium Linux and native macOS behavior.

Readiness: the bounded execution procedure is specified. The automated image,
musl compile, stage, package, and package-acceptance milestones are **complete**
for the current archive. The target dwl session and live installation remain
unproven. The implementation baseline is `506fbac`. The existing plan was
committed by the owner as `5b0ff53`; this revision changes documentation only.

Next: S14 and S15 on the owner's desktop. No technical decision is deferred to
an owner question for the automated path. Execution and live installation still
require their respective owner requests.
Resume prompt: `Execute campaign-helium.md from S00. Follow each acceptance and
recovery rule. Do not push or install into my real home.`
First command: `git status --short`.
Current environment: the owner identifies this machine as a **headless server**.
Use it for builds and isolated software-Wayland checks. Do not expect an active
dwl session, physical input/display, or a live desktop install here.

| Order | Outcome | Prerequisite |
| --- | --- | --- |
| S00 | preserved starting state and focused baseline | execution request |
| S01 | product selection and unchanged Chromium dispatch | S00 |
| S02 | isolated work state and artifact-bound evidence | S01 |
| S03 | frozen verified Helium source/dependency inputs | S02 |
| S04 | real pinned image and complete fetch | S03 |
| S05 | complete offline upstream preparation | S04 |
| S06 | reviewed musl integration stack | S05 |
| S07 | strict effective desktop GN contract | S06 structural checks |
| S08 | real compiler, target, and graph proofs | S07 |
| S09 | owned software Wayland test fixture | S04; may unblock a parked S06 |
| S10 | actual Helium Wayland browser pilot | S08 and S09 |
| S11 | full desktop stage and every-ELF audit | S10 |
| S12 | relocatable candidate archive | S11 |
| S13 | complete build/stage/package behavior checks | S12 |
| S14 | normal-user target Alpine/dwl qualification | S13 and target session |
| S15 | tested offline installer and usr launcher integration | S12 locally; S13 before activation |
| S16 | regression evidence and honest closeout | every available local item |

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
- On 2026-11-01, the owner chose a lean, performance-oriented first package:
  omit the DevTools frontend (tracked by `CHB-2`), disable D-Bus, enable
  ThinLTO, and use Chromium's bundled Linux PGO profile. Keep GTK3 and the
  native file chooser. Sway is only the automated builder fixture; dwl is the
  target desktop.
- The owner requires eventual use in the Alpine setup maintained under `~/usr`.
  Include target dwl qualification and an installer that needs no Docker on
  the target. Preserve the existing Chromium launcher and personal profiles.
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
  Use `config/helium.system-library-preflight.schema.json` for desktop roots
  when the headless policy cannot describe them without changing Chromium.
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
- Keep browser UI, raw CDP, and functioning desktop backends. The first lean
  package omits the DevTools frontend resources. Do not inherit headless stubs
  or Crashpad removals. Build the `//chrome:chrome` target and resolve its real
  `helium` output from GN. Retain the upstream Crashpad helper when the selected
  graph requires it.
- Start with native Wayland, ALSA, Mesa GPU rendering, and Intel VA-API decode.
  Prefer no PulseAudio, PipeWire, systemd, or D-Bus dependency. Allow a
  necessary desktop library when source and a functional test demonstrate need.
  Do not require an X server, Vulkan, a hardware GPU, a VA-API device, or a
  remote build service; VA-API is optional at runtime and falls back to
  software.
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

Use S07's explicit initial desktop profile and S04's dependency consumers.
Resolve exact versions through the pinned Alpine package solver. Change a
prescribed value only for a reproduced failure, using the recovery table.
Document and test any intentional feature exclusion. No defining Helium feature
may be excluded. Package-library presence is distinct from requiring an X
server or a session D-Bus daemon.

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

The implementation inspected during planning is `506fbac`; `5b0ff53` adds only
this plan. No prior `docs/CAMPAIGN-*` or `DECISIONS.md` was found. The evidence
below is from planning, not a promise about a future execution environment.

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

The host inspected during planning is native Linux amd64 with about 60 GiB RAM and 332 GiB free
on the repository filesystem at inspection. This is not proof of enough final
link or build space. Preserve existing volumes. Before a full build, inspect
its filesystem and memory requirements and report the expensive run. If
resources run short, retain Ninja state and give the exact resume command.
No full build was started during planning.

## Run the campaign in this order

Run one writer and one item at a time. Do not start dependent items after a
failed prerequisite. An item is accepted only after its named checks pass.
A passing test double cannot accept a real-image or real-browser milestone.

Use the existing scripts named below. Introduce these narrow shared owners:
`scripts/linux_product.py` for Linux product configuration and identity, and
`scripts/helium_wayland.py` for compositor ownership and Wayland observations.
Keep the existing command, build, and acceptance drivers. Add
`scripts/install-runtime.py` for archive/volume installation so the target
machine needs no Docker. Do not create another build CLI or a workflow engine.

Use Bash for the examples. Define these helpers from the repository root
during implementation:

```sh
set -euo pipefail
export TMPDIR="$PWD/.work/tmp"
export CHROMIUM_TEST_TMPDIR="$PWD/.work/tmp"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=scripts:tests
mkdir -p "$TMPDIR"
ut() { python3 -m unittest "$@"; }
h() {
    ./chromium-build --product helium --platform linux --arch amd64 \
        --profile helium-wayland-release "$@"
}
export HELIUM_IMAGE=helium-builder:0.20.3.1-amd64
export HELIUM_WORK=helium-0.20.3.1-work-amd64
export HELIUM_CACHE=ungoogled-chromium-153-ccache-amd64
hr() {
    docker run --rm --network=none \
        -e CHROMIUM_PRODUCT=helium -e CHROMIUM_ARCH=amd64 \
        -e CHROMIUM_PROFILE=helium-wayland-release \
        -e CHROMIUM_SOURCE_ROOT=/work/src \
        -e GN_OUT=/work/out/helium-wayland-release \
        -v "$HELIUM_WORK:/work:ro" "$HELIUM_IMAGE" "$@"
}
```

Before using them, confirm `.work/` is in `.git/info/exclude`. These helpers
are local conveniences, not files to add to the product. `h` becomes usable
in S01. `hr` requires S04's real image. `ut` uses modules introduced by the
corresponding item. Never report one of these future checks as already run.

Rebuild `h image` after changing image-resident scripts, config, fixtures, or
Dockerfile. Otherwise the ordinary CLI runs stale copies baked into the image.
An image rebuild must preserve work and cache volumes. Reuse source preparation
when its recipe is unchanged, then rerun affected environment and graph gates.

### S00 — establish the starting state

**Prerequisite:** a separate request to execute this campaign. **Size:** small.
**Files:** read-only, except the local exclude and scratch directories.

1. Read AGENTS.md and this document. Run `git status --short`,
   `git log -1 --oneline`, and `git worktree list`.
2. Preserve all unrelated changes. Verify no other process is writing the same
   work volume before any source mutation. Do not stop somebody else's process.
3. Run the existing focused baseline below. Do not run `make check-py`, full
   unittest discovery, formatters, hooks, or CI.
4. Check Docker's native architecture, available disk on its data filesystem,
   and available memory. Do not install tools if the host lacks a prerequisite.

```sh
ut test_fetch_inputs test_browser_test_support -q
docker info --format '{{.Architecture}} {{.DockerRootDir}}'
df -h .
free -h
```

**Accept:** existing focused tests pass or a reproduced baseline failure is
identified before implementation. Docker must be native x86-64 for real builds.
No existing volume may be deleted, reset, or repurposed.

**Recover:** if Docker is absent, continue test-driven local implementation
through S03 and record real-image proof as unavailable. Do not substitute QEMU
or emulation for native build qualification.

### S01 — add product selection without changing Chromium dispatch

**Prerequisite:** S00. **Size:** small. **Files:** `chromium-build`,
`scripts/linux_product.py`, `Makefile`, `tests/test_product_selection.py`.

1. Add `--product chromium|helium`. Default to Chromium. Validate product,
   platform, architecture, and profile before `docker info`, image lookup,
   volume creation, downloads, or filesystem writes.
2. Accept existing `aarch64` normalization, then reject Helium arm64. Reject
   Helium macOS, unknown products, missing option values, Chromium profiles on
   Helium, and the Helium profile on Chromium. Return a nonzero exit with the
   offending value in the diagnostic. Add `--archive PATH` only for Helium
   `install`. Route archive installation before Docker discovery. Reject this
   option for other products/commands. Reserve Helium `install --rollback`
   for S15 and reject it with `--archive`. Both offline actions bypass Docker.
3. Preserve omitted-product and explicit-Chromium image names, volumes, profile
   defaults, native macOS arguments, and existing environment overrides.
4. Give Helium the defaults in Decisions. Read its version from its lock through
   the Python product owner once S03 creates the lock. Until then fail clearly
   for real commands that need that absent lock. Tests may provide fixture locks.
5. Pass `CHROMIUM_PRODUCT` to every Linux container. Resolve Linux lock paths in
   `linux_product.py`. Keep existing `INPUTS_LOCK` support where it exists, but
   reject an override whose product/version disagrees with the invocation.
6. Keep the Linux Chromium CLI's existing host requirements. Helium may require
   host Python 3 for locked configuration resolution. Declare that requirement
   in usage/docs and fail before effects if it is missing. Do not install it.
7. If Bash consumes Python output, use a checked subprocess and fixed named
   fields. Treat output as data. Do not `eval` shell generated from a lock.
8. Make `PRODUCT=helium` select the Helium Makefile defaults. Leave explicit
   command-line image, volume, profile, and jobs overrides effective. Do not
   let Makefile's old Chromium defaults override a Helium invocation.

Write these tests first in `ProductSelectionTests`:

| Case | Required observation |
| --- | --- |
| omitted product versus explicit Chromium | identical effective Docker command, mounts, environment, and defaults |
| macOS omitted product versus explicit Chromium | identical native backend argv |
| valid Helium phase | Helium image, work volume, profile, product environment, and lock selection |
| each invalid combination above | nonzero result and zero recorded Docker/native-backend calls |
| image/work/cache overrides | respected, subject to S02 ownership checks |
| Makefile dry-run for each product | correct command, no unexpected execution |

Use a fake Docker executable on PATH that records argv and returns the minimal
image/info responses. It must not call real Docker. Compare parsed argv, not
fragile shell whitespace. Test `image`, `fetch`, `prepare`, all six gates,
`poc`, `build`, `build-target`, `test-fast`, `test`, `stage-runtime`,
`test-staged`, `package`, `cache-stats`, `ensure-source`, and `install`.

```sh
ut test_product_selection.ProductSelectionTests -q
```

**Accept:** every dispatch case passes and the existing two focused modules
remain green. Do not claim that Helium builds yet.

### S02 — bind state and phase evidence to its consumer

**Prerequisite:** S01. **Size:** medium. **Files:** `linux_product.py`,
`chromium-build`, phase readers/writers listed in the contract table below,
`tests/test_product_selection.py` (`ProductStateTests`).

Implement this contract before fetching a real Helium source:

| Boundary | Required identity and invalidation |
| --- | --- |
| work-volume ownership | product, Chromium version, product version, architecture; reject conflicting reuse before writes |
| fetch | selected lock digest and each fetched input digest; no profile-dependent network fetch |
| source | fetch identity, selected inventories and dispositions, local patch bytes, preparation recipe bytes |
| environment/probes | toolchain and package/policy identity, image identity, prepared-source identity where consumed |
| GN and A–F authorization | source identity, profile bytes, toolchain/environment identity, architecture, product |
| build | matching GN and F/oracle evidence, profile, real target/output, current source identity |
| stage/package/acceptance | build identity and content digests of the artifact being tested; artifact kind must match |

Use existing reports to carry these fields. Do not create a second provenance
registry. A small volume-ownership record is necessary because a user can point
Helium at a Chromium work volume. Treat that record as a lock on the state, not
a reason to overwrite mismatched contents.

Missing identity in old reports means unverified evidence. Fail with the exact
phase needed to refresh it. Do not erase legacy source/output or infer Helium
ownership from a directory name. A legacy nonempty Chromium volume may be read
by Chromium under the old contract until its next successful gate refresh.
Helium must refuse it without writing an ownership record.

Invalidate only downstream evidence. Editing browser fixtures must not force
source unpacking. Editing a patch must invalidate preparation and downstream
gates. A successful image rebuild alone must not authorize stale graph reports.
When identity disagrees, fail before Ninja or replacement of an existing stage.

Tests must feed reports through the actual phase entry points, using fixtures
and fake external tools. Cover wrong product, version, profile, architecture,
lock, patch, incomplete report, missing oracle, modified staged file, and old
unqualified metadata. A wrong report must cause zero build/stage invocations.
Also prove changing only Helium inputs does not invalidate Chromium evidence.

```sh
ut test_product_selection.ProductStateTests -q
```

**Recover:** refresh only the named stale phase when its inputs are unchanged.
If preparation inputs changed, preserve the old tree and stop before mutation.
Use a separate scratch work volume only for a deliberate changed-recipe pilot;
never silently replace the user's expensive source tree.

### S03 — freeze all Helium input bytes

**Prerequisite:** S02. **Size:** medium. **Files:**
`config/helium.inputs.lock`, `scripts/fetch-inputs.py`, `linux_product.py`,
`tests/test_helium_inputs.py`.

Use the exact source revisions in Baseline. Do not bump them while resolving
build errors. Extend the existing JSON lock schema for Helium as follows:

- Keep `chromium.version`, its prepared base archive URL, filename, byte size,
  SHA-512, and actual DevTools revision. Read the latter from the prepared
  archive metadata/source. Do not copy Chromium 153's revision.
- Add the prepared Linux overlay as a named `source_inputs` entry with its
  URL, byte size, SHA-512, and extraction order after the base archive.
- Add core/Linux repository archives at their exact commits. Record commit
  identity and archive digests. Do not depend on a live Git submodule checkout.
- Add onboarding, nonfree search data, and bundled Imput uBlock as named inputs.
  Preserve upstream destination and strip-directory semantics. Verify the
  upstream SHA-256 pins and record the SHA-512 used by the existing fetcher.
- Add only the Alpine/Copium/portable archives actually used for inherited-patch
  disposition. Pin immutable revisions and bytes. Resolve a tag to a commit
  before treating it as a revision. Do not use `main` or an unpinned `156.0` tag.
- Retain the pinned compiler/archive, build-only glibc image, and ccache policy
  initially. Every field must match its actual consumer.

Upstream source hash metadata is a bootstrap check, not a runtime dependency.
During lock creation fetch the exact `.hashes` release assets, compare the
archive's bytes, and freeze those digests. If the upstream hash disagrees, stop.
Do not resolve hash metadata afresh during ordinary `fetch` or `prepare`.

Existing `.work/tmp/probe-dl` archives may save network transfer. Treat them as
untrusted cache entries until each matches the upstream pin and the new lock.
Do not make the production recipe depend on their abbreviated filenames.

Run `fetch-inputs.py` through the selected lock. Reuse `fetch_one` and HTTPS
redirect validation. A reused file gets reverified. Downloads go to temporary
files and become visible only after size and digest checks. Write fetch success
only after all required inputs verify. No network call belongs to later phases.

Test valid fetch/reuse, byte corruption, wrong size, non-HTTPS redirect, missing
input, interrupted download, and product-specific lock resolution. Use a local
fixture or mocked transport, not public-network tests. Check that neither a
partial input nor a failed report authorizes preparation.

```sh
ut test_helium_inputs -q
```

**Accept:** lock entries have real verified bytes and exact revisions. Zero
placeholder digests, mutable aliases, or unresolved source identities remain.
A lock that only parses is not accepted.

### S04 — build the pinned desktop-capable image

**Prerequisite:** S03. **Size:** medium. **Files:** `Dockerfile`,
`config/helium.packages.amd64.lock`, product-selected image/policy consumers,
`tests/test_helium_inputs.py`.

1. Keep the existing Chromium image path and default package set unchanged.
   Parameterize the selected inputs/package lock and product in the shared
   Dockerfile. Preserve compiler host/runtime separation and content checks.
   For Helium, use the checked host Python resolver to read the exact `world`
   list and pass its validated package tokens as a build argument. Reject
   non-package tokens before Docker. Do not require new host tools in the
   unchanged Chromium branch or duplicate the Helium package list in Dockerfile.
2. Resolve Helium packages in the pinned Alpine base. Start from the existing
   package names and add demonstrated desktop/preparation/acceptance consumers:
   `py3-pillow`, `gtk+3.0-dev`, `wayland-dev`, `wayland-protocols`,
   `libxkbcommon-dev`, `alsa-lib-dev`, `sway`, `grim`, `wtype`, `wl-clipboard`,
   `nss-tools`, and `openssl`. Resolve exact available names/versions with `apk`
   inside the container. Do not guess versions from the older Chromium lock.
3. Keep downloads and apk resolution confined to image creation and `fetch`.
   Install no packages in preparation, GN, compile, or acceptance commands.
4. Freeze the world list and complete resolved package list in the existing
   package-lock format. Use `lockcheck.py packages` to compare the image's actual
   package manifest, including desktop additions. Never regenerate the lock
   automatically during ordinary image creation to accept drift.
5. Copy the new relevant Linux test modules into the image alongside the
   existing two Linux modules. Do not add macOS/workflow tests to this image.
   Point `write-environment-metadata.py`, `preflight-system-libraries.py`, and
   `verify-environment.py` at the selected lock. Preserve zero glibc entries
   and zero unclassified edges. Normal GTK/Wayland libraries are desktop roots,
   not an excuse to broaden GNU-runtime exceptions beyond FFmpeg.
6. Use lld for this Chromium toolchain as the existing gates require. The user
   workspace's Rust/musl mold rule does not authorize changing Chromium's locked
   linker or bypassing its command audit. Keep Rust wrapper/linker behavior
   consistent with the pinned toolchain and source recipe.
7. Keep all image build scratch under its `/work/.work/tmp`, and set its TMPDIR
   accordingly. Do not add more large `/tmp` archives to the Dockerfile.

```sh
ut test_helium_inputs -q
h image
docker image inspect "$HELIUM_IMAGE" --format '{{.Id}}'
h fetch
```

**Accept:** a real image builds, its environment/package preflight passes, and
real fetch completes with every locked entry verified. Report image creation
before starting it because it downloads packages and the compiler.

**Recover:** for an unavailable exact apk, preserve the failed solver output,
resolve a new Helium lock in an isolated container, and rebuild. Do not loosen
`=version`, change the Chromium lock, or install gcompat. If an unchanged locked
input is no longer obtainable, park this item with its exact URL/package.

### S05 — prepare upstream Helium offline before musl integration

**Prerequisite:** S04. **Size:** medium. **Files:** preparation drivers,
Helium patch inventories/dispositions, `tests/test_helium_prepare.py`.

Add a Helium recipe selected by the existing preparation driver. Leave the
Chromium recipe intact. Reuse narrow shared extraction/report helpers only
where both recipes actually call them. Work on extracted copies of Helium core
and Linux tooling. Preserve their relative layouts for scripts that resolve
resources from their own location.

Run every command with checked status. The exact upstream order is:

| Order | Operation | Required result |
| --- | --- | --- |
| 1 | unpack prepared base archive | one Chromium 156 tree, exact expected root |
| 2 | unpack prepared Linux-x64 overlay | overlay belongs to that same version |
| 3 | `utils/downloads.py unpack -i <core>/deps.ini -c <verified-cache> <src>` | all three dependency trees populated from locked local bytes |
| 4 | `utils/prune_binaries.py --keep-build-dependencies <src> <core>/pruning.list` | upstream pruning completes |
| 5 | core `patches/series`, then Linux `patches/series` | every listed patch applies once, zero fuzz |
| 6 | `utils/domain_substitution.py apply -r <core>/domain_regex.list -f <core>/domain_substitution.list <src>` | substitutions complete |
| 7 | `utils/name_substitution.py --sub -t <src> --backup-path <attempt>/namesubs.tar` | actual Helium symbols/names generated |
| 8 | `utils/i18n_apply.py -t <src>` | translations applied |
| 9 | `utils/helium_version.py --tree <core> --platform-tree <linux> --chromium-tree <src>` | both Chromium and Helium version fields match the lock |
| 10 | `utils/generate_resources.py <core>/resources/generate_resources.txt <core>/resources` | generated resources in this attempt's core copy |
| 11 | `utils/replace_resources.py <core>/resources/helium_resources.txt <core>/resources <src>` | branded resources installed in source |

Angle-bracket paths are recipe variables inside the driver. They are not shell
commands to paste literally. Do not call upstream `scripts/build.sh`: it deletes
`out/` and chooses its own network, sysroot, PGO, and build orchestration.

Use upstream patch utilities only if their invocation enforces zero fuzz and
checked failures. Otherwise reuse the existing strict patch primitive while
preserving both upstream series exactly. Offsets must be observed and recorded.
A missing patch, duplicate application, or unreviewed skip is fatal.

Extend existing source/provenance reports with the actual generators and input
identity. Do not invent successful generator records to satisfy Gate C's
Chromium-specific names. Make Gate C verify the Helium recipe's real operations,
version metadata, prepared DevTools, bundled uBlock, onboarding, and resources.

Prepare in `/work/src.prepare.<unique>` and keep the failed attempt for diagnosis.
Do not expose `/work/src` or source success until the entire selected recipe
succeeds. Permit verified reuse of an identical complete recipe. On mismatch,
refuse before changing existing source or output. A partially transformed tree
is never safe to resume by replaying all upstream substitutions in place.

In a tiny fixture, record observable tool calls and assert exact order. Inject
failure at unpack, patch, translation, and resource generation. Each failure
must leave no source success marker or accepted tree. Run a second identical
successful invocation and prove no transformer runs again.

```sh
ut test_helium_prepare.PreparationTests -q
h prepare
```

**Accept:** real offline upstream preparation completes. S06 will add the musl
recipe before production GN/build acceptance. If S06 changes the recipe, preserve
this initial prepared tree as diagnostic evidence and use a deliberate fresh
preparation attempt. Do not claim its earlier stamp accepts the added patches.

### S06 — select and port the musl patch layer

**Prerequisite:** S05. **Size:** several small, serial subsystem slices.
**Files:** Helium inventory/disposition, `config/patches/helium/`, preparation
integration, `tests/test_helium_prepare.py` (`PatchContractTests`).

Use the decision procedure below for every inventory entry and every local
patch in `prepare-source-driver.py:LOCAL_PATCHES`. Cover old portablelinux,
Alpine, and Copium entries as well as the pinned 156 candidates. Keep all results
in the existing inventory/disposition model. Do not create a parallel register.

1. Read the patch's affected code in the Helium-prepared 156 tree.
2. Determine whether the affected feature exists in this desktop graph.
3. Check the patch's precondition and expected postcondition in that source.
4. Run forward and reverse dry runs with `--fuzz=0` against the correct preceding
   layers. A reverse dry run is supporting evidence, not a proof of semantic
   equivalence. Inspect the actual implementation before marking integrated.
5. Choose `apply`, `superseded`, or `not-applicable`, the states the existing
   verifier accepts. Express integrated/headless-only/incompatible outcomes in
   the reason and affected paths. Do not invent new states without updating
   all readers and tests. A superseded entry names its real replacement.
6. Apply selected patches in dependency order and rerun the complete stack on
   a clean attempt. Independent dry runs against one unmodified tree do not
   prove the stack composes.

Use this order and default policy:

| Subsystem | Candidate sources | Default and required proof |
| --- | --- | --- |
| GNU-specific headers/libc interfaces | Alpine musl patches; local cdefs, execinfo, CMSG, sockets, allocator patches | retain only code still requiring the fix; compile the affected object |
| sandbox/thread/signal/syscalls | `musl-sandbox`, `musl-tid-caching`, `no-sandbox-settls`, prctl; local sandbox/GPU patches | inspect ABI and syscall behavior; never replace a check with success; prove runtime sandbox later |
| Rust/bindgen/libc++ | Copium is-musl/libcxx and Rust patches; local bindgen and bootstrap patches | compile the affected Rust action; stable bootstrap must remain scoped to its wrapper |
| GN/system tools | version checks, no-Python-vendoring, Node, Dawn Go path | use locked installed tools and retain checks; update expected version to the actual pin |
| Crashpad | local cdefs/CMSG plus relevant upstream 156 fixes | keep functioning handler and child execution; no headless Crashpad-removal series |
| graphics | musl native EGL/GLES share-group and GPU syscall patches | Wayland renderer and llvmpipe evidence; headless Ozone EGL patch is not a Wayland implementation |
| system FFmpeg | actual 156 FFmpeg compatibility patches | retain existing media fixture and scoped runtime exception |
| UI/WebUI | full Helium upstream resources and TypeScript pipeline | no headless desktop stubs; DevTools frontend resources are omitted by the Helium no-devtools patch |
| hermetic gate target | `gate-e-hermetic-smoke.patch` | retain/port so Gate E still builds exceptions, threads, and in-tree libc++ |

Start with every `headless-*` patch excluded from the Helium recipe. This
includes headless no-desktop/no-D-Bus stubs, Crashpad removals, DevTools stubs,
printing removals, webcam removals, and headless native EGL. The current Helium
profile instead uses repository-owned `helium-no-devtools-frontend.patch` and
`helium-no-dbus.patch` for its explicit lean and no-D-Bus contract. If a
generically necessary fix hides in a headless patch, extract the smallest
correct desktop fix into a Helium patch and name its source in the disposition.
Do not enable the whole headless patch to fix one link error.

For a failing or fuzzy required patch, regenerate only its changed context
against the exact preimage. Compare the resulting postimage to the original
intended change. Require `patch --dry-run --fuzz=0` and the affected compiler
command to pass. A patch that merely makes the compiler quiet is insufficient.
Do not globally suppress new warnings, switch off sandboxing, or change the
Rust nightly to get past an error.

S06 accepts structural patch selection/application. Compile proof follows in
S08. Mark a patch `apply` only when its required source transformation and
zero-fuzz application are established. That state does not claim a successful
compile. Every candidate must have a justified structural disposition before
S07. Return to the identified entry if S07/S08 exposes a missing or incorrect
fix. Never mark a required failing patch not-applicable merely to pass Gate C.

```sh
ut test_helium_prepare.PatchContractTests -q
```

**Accept structural selection:** complete dispositions, zero unexplained skipped
patches, and a clean full-stack application. Track required compile observations
as pending until S08 accepts them. The scratch classification is a starting
index only. S08 cannot accept an unresolved required patch.

Finish by building the image with the final recipe and running a fresh real
`h prepare`. S05's upstream-only tree has no expensive compiled objects yet.
Preserve it under `/work/.work/tmp/upstream-only` with its source-success receipt
before creating the final `/work/src`. Implement that preservation as a checked
preparation-driver transition: require the exact upstream-only identity, refuse
an occupied preservation destination, move the old tree/receipt, and create no
new success marker until the complete musl recipe passes. Never apply this
initial transition to a source tree with GN/Ninja output or a different recipe.
Later compiler fixes use only the integration-update procedure below.

### S07 — generate a desktop graph without headless argument leakage

**Prerequisite:** S06 structural acceptance and a matching prepared-source receipt.
**Size:** medium. **Files:** `config/profiles/helium-wayland-release.gn`,
`generate-gn-args.py`, `generate-gn.py`, `verify-gn-args.py`, source gate consumers,
`tests/test_helium_gn.py`.

Start from upstream `flags.gn` and `flags.linux.gn`, then apply the explicit
musl overrides below in the local profile. Do not import `build/args/headless.gn`.
Do not append conflicting duplicate assignments. Check declared arguments in
the exact source before rendering and require strict GN generation.

| Area | Required initial values |
| --- | --- |
| identity/toolchain | Linux/x64 host and target, `is_musl=true`, `use_sysroot=false`, existing unbundle host/custom toolchain, locked Clang path/version, `cc_wrapper="/usr/bin/ccache"` |
| C++/Rust | in-tree libc++/libunwind, no safe libstdc++, `/usr` Rust sysroot/bindgen, actual pinned musl Rust triple |
| build loop | `is_debug=false`, `is_component_build=false`, `is_official_build=true`, `dcheck_always_on=false`, PGO=2, CFI=false, thin-LTO=true, thin-LTO optimizations=true, symbol levels=0, Siso=false |
| Wayland | `use_ozone=true`, `ozone_auto_platforms=false`, `ozone_platform="wayland"`, Wayland=true, X11=false, headless=false, `use_xkbcommon=true` |
| desktop toolkit | `use_gtk=true`, Qt disabled, D-Bus disabled, printing retained without requiring CUPS |
| audio/media | ALSA=true, PulseAudio=false, PipeWire capture/link=false, VA-API=true, proprietary codecs and Chrome FFmpeg branding retained |
| rendering | Mesa/EGL GPU path retained with software fallback; Intel VA-API decode enabled but optional at runtime; no required Vulkan; do not disable GPU globally; SwiftShader optional only when its real runtime is built and audited |
| Helium behavior | retain UI, extensions, onboarding, bundled uBlock, privacy flags, raw CDP, WebUI build pipeline, and upstream version/branding; omit the DevTools frontend for the first lean package |

The lean build uses Chromium's bundled Linux PGO profile at
`chrome/build/pgo_profiles/`, selected by `chrome/build/linux.pgo.txt`; no
network fetch is required. `chrome_pgo_phase=2` and `is_official_build=true`
also enable the official ThinLTO optimization level. This is a deliberate
change from the initial PGO=0/ThinLTO-off baseline and requires a fresh build
after Gate D.

Keep upstream values outside the explicit overrides. Retain inherited diagnostic
policy only when it comes from pinned upstream flags. Do not add new global
suppression flags. Initial release resources must be complete before optimizing
WebUI or dropping unused libraries.

GTK is the initial desktop implementation because upstream's Linux build uses
it. Set `GDK_BACKEND=wayland` for GTK consumers. Test with no DISPLAY and no
XWayland or session bus. GTK's linked X libraries do not by themselves imply
an X server requirement. Bundle their real closure if required. Never broaden
the GNU runtime exception because a desktop library has a normal dependency.

If disabled D-Bus causes a concrete unresolved symbol or feature failure, port
the smallest correct feature guard and provide a minimal non-D-Bus fallback.
Do not require a D-Bus daemon in the owner's dwl session. Record any fallback
limitation, such as native notifications or external URI launch, as a TODO
rather than silently disabling the feature. Optional missing-bus errors are
acceptable only when every required browser feature still passes.

The current shared renderer appends `devtools_skip_typecheck=false` and
`devtools_bundle=false` when supported. Keep its Chromium behavior but do not
inject these headless settings into Helium. `verify-gn-args.py` currently accepts
only headless profiles and hardcodes their expected values. Add a distinct
Helium contract and test actual effective GN values, not just profile text.

After upstream transforms and selected patches, perform the existing builder
links, Rust target declaration, and FFmpeg system-unbundle transformation for
Helium where their real paths still exist. Fail on an unexpected missing path.
Do not symlink a glibc Node or generator executable into the target runtime.

```sh
ut test_helium_gn -q
h image
h gate-a
h gate-b
h gate-c
h gate-d
```

**Accept:** GN succeeds with `--fail-on-unused-args`, the effective values match
the desktop contract, and a test proves the headless profile cannot authorize
Helium. Wrong X11/Wayland values, missing resources, stale source identity, and
headless optional-argument injection must all fail their tests.

**Recover:** for unused arguments, inspect their declaration and remove only
an obsolete assignment after proving the required behavior another way. Do
not remove strict validation. For missing packages, update only Helium's pinned
image inputs and rerun S04. For failed source patches, return to S06.

### S08 — prove the actual compiler graph before the full browser build

**Prerequisite:** S07. **Size:** small subsystem slices. **Files:** graph and
oracle consumers, resolver/build consumers, selected compatibility patches,
`tests/test_helium_gn.py`.

```sh
h gate-e
h gate-f
hr gn desc /work/out/helium-wayland-release //chrome:chrome outputs --root=/work/src
hr ninja -C /work/out/helium-wayland-release -t targets all
```

Gate E must compile and run the real hermetic target with musl, exceptions,
threads, and in-tree libc++. Gate F and its independent oracle must select the
same real `chrome` graph and agree on command classification. Require direct
ccache C/C++ hits in A, no ccache on link commands, and no glibc target leakage.
Do not replace gate failure with an allowlist entry that hides the error.

Resolve the upstream GN target `//chrome:chrome` to output `helium`. The current
resolver falls back to a file named `chrome` after a failed GN command. Preserve
that legacy Chromium behavior where required, but forbid the fallback for
Helium. Require the real graph output, one executable, and correct product
branding. Update `build.py`, `build-target.py`, staging, and tests consistently.

For each representative still-unproven subsystem, select an existing object
output from `ninja -t targets all` and compile it with:

```sh
h build-target '<exact-output-from-the-current-graph>' --jobs 1
```

The placeholder must be replaced with a real target before running. Select
at least sandbox/libc, Crashpad, allocator, Rust/bindgen, Wayland/EGL, and one
Helium WebUI/resource action. Record accepted target names in the patch
application report when they justify a patch. No invented target names.

**Accept:** E and F/oracle pass, output resolves to `helium`, selected actions
succeed, and S06 has no remaining unsupported patch decision required by the
graph. A graph dry run is not a successful compile.

### S09 — own a software Wayland test session

**Prerequisite:** S04. This independent harness item may run while S06 is parked.
**Size:** small. **Files:** `scripts/helium_wayland.py`,
`tests/test_helium_wayland.py`, image test-tool pins.

Use Sway's headless wlroots backend for automation. Set `WLR_BACKENDS=headless`,
`WLR_RENDERER=pixman`, and `WLR_LIBINPUT_NO_DEVICES=1` for the compositor only.
Use Mesa llvmpipe for the browser. Do not apply the compositor renderer setting
to Chromium and treat it as proof of Chromium's renderer.

Sway is selected over Weston for its packaged IPC tree, screenshot support,
and compatible virtual-keyboard/clipboard tools. This changes only the test
fixture. The delivered browser must work in the owner's dwl session.
Do not require Sway, Weston, their daemons, or test tools in the final package.

Use a private HOME and mode-0700 XDG_RUNTIME_DIR under the attempt directory.
Run as a nonroot user. Generate a minimal config with no user includes, session
commands, bars, wallpaper tools, or XWayland. Set `xwayland disable`, a solid
background, and one 1280x900 virtual output. Discover actual socket/output names
from that owned process. Do not assume another user's `SWAYSOCK` or DISPLAY.

The helper owns the compositor process group, log, private sockets, and all
clipboard-owner helper processes. Wait for readiness with a 15-second deadline.
Use Sway IPC to require an active virtual output. Give every IPC/input/screenshot
operation a 10-second deadline. On failure save the relevant logs and report.
Terminate only owned children, send TERM, wait at most 5 seconds, then KILL
remaining owned children and reap them. Never use global `pkill`/`killall`.

Test timeout, compositor death, invalid socket, cancellation, and cleanup with
owned fake processes. Then run one real compositor exercise in the image that
queries outputs, transfers a unique clipboard string with `wl-copy`/`wl-paste`,
and captures a valid 1280x900 PNG with `grim`. This is an effectful fixture
contract check, not browser acceptance.

```sh
ut test_helium_wayland -q
```

For the real fixture check, add a narrow `--check-fixture` entry point to
`helium_wayland.py`. It must perform the output/clipboard/screenshot assertions
above and fail on any missing observation. It may drop root to the image's
existing chromium user after creating owned writable scratch. Run it explicitly:

```sh
h image
docker run --rm --network=none -v "$HELIUM_WORK:/work" \
    "$HELIUM_IMAGE" python3 /opt/chromium-build/scripts/helium_wayland.py \
    --check-fixture --output /work/test-output/helium-wayland-release/compositor
```

This command is separate from mocked unit tests. Do not count a skipped real
fixture test as success.

**Accept:** real pinned compositor/tools work without a GPU device, DISPLAY,
XWayland, session D-Bus, or host session access. If a required protocol/tool is
missing, correct its pinned package set. Do not silently replace compositor
input with DOM assignment or omit screenshots.

### S10 — build and pilot the real Helium browser

**Prerequisite:** S08 and S09. **Size:** expensive milestone with small repairs.
**Files:** build/resolver consumers, `test-fast.py`, `test-fast-driver.py`,
`browser_test_support.py`, `tests/test_helium_wayland.py`.

Announce the full compile before starting. Start with `--jobs 4` on the observed
60-GiB host. On a different host, use one job per 8 GiB of available RAM,
clamped to 1–8 and the native CPU count. This is a conservative starting policy,
not a measured memory guarantee. Check actual peak memory and disk pressure.
Do not start a release build earlier merely for a local patch check.

```sh
h build --jobs 4
h test-fast
```

`test-fast` must run the headed Helium branch under the owned compositor:

1. Require `helium --version` to report the pinned Helium and Chromium identity.
   Cross-check generated version metadata if the text omits a component.
2. Launch without `--headless`, `--ozone-platform=headless`, `--disable-gpu`, or
   any sandbox-disable switch. Select `--ozone-platform=wayland`. Set DISPLAY
   absent, `GDK_BACKEND=wayland`, `LIBGL_ALWAYS_SOFTWARE=1`, and
   `GALLIUM_DRIVER=llvmpipe`. Bind CDP to loopback only.
3. Open the existing local fixture. Assert HTML, CSS layout, JS result, and
   a screenshot's known rendered pixel regions through the existing CDP helper.
4. Independently require a mapped browser node in the compositor tree with the
   expected PID/app identity. Capture the whole output with `grim`. Check the
   known content region and visible window/chrome region. A CDP screenshot alone
   cannot prove the window exists.
5. Collect sandbox observations while an actual renderer is alive, using the
   rules below. Shut down and prove no owned children remain.

Write a real result before beginning, with status running, then complete or
failed. Delete no earlier successful report until its replacement is atomic.
Bind the result to the executable and product identity. A killed test must
never leave a complete report from a different binary.

**Recover:** use the first-failure table. Fix one root cause and rebuild only
the failed output. Resume the existing Ninja tree. Do not clean `out/`, fetch
again, or reconstruct source for an ordinary compiler/link failure.

### S11 — stage the desktop graph and audit every ELF

**Prerequisite:** S10. **Size:** medium. **Files:** staging and packaging audit
helpers, `tests/test_helium_runtime.py` (`RuntimeCollectionTests`, `ElfAuditTests`).

1. Replace the Helium hardcoded six-file headless manifest with GN's target
   runtime-dependency output plus the pinned upstream packaging requirements.
   Generate runtime deps using GN's supported `--runtime-deps-list-file` flow
   for `//chrome:chrome` and require the resulting file. Verify the pinned GN
   supports that flag in S07. Never fall back to an empty list.
2. Include `helium`, branded resource packs, all generated locales, required
   ICU and V8 data, icon/desktop metadata, dynamic graphics components,
   `helium_crashpad_handler`, and any target-required helpers. Account for
   upstream chromedriver/Qt/Vulkan entries explicitly. Omit them only when
   excluded from the chosen graph and not needed by a runtime consumer.
3. Validate every source path stays under the selected output or an explicit
   pinned metadata/resource root. Reject traversal, escaping symlinks, duplicate
   conflicting destinations, and absolute build paths in the shipped layout.
4. Enumerate recursively by ELF magic, not extension or executable bit. Audit
   architecture and DT_NEEDED of every ELF, including helper directories.
   Every dynamically linked executable needs `/lib/ld-musl-x86_64.so.1`.
   Shared libraries normally have no PT_INTERP. Reject glibc interpreters,
   `libc.so.6`, unexpected GNU ABI requirements, and absolute build RPATHs.
5. Resolve dependencies using the actual bundle search paths, RPATH/RUNPATH,
   and explicit known system musl providers. A same basename somewhere in
   the archive does not prove the loader can find it. Reject conflicting SONAME
   providers and dependencies whose real path escapes the allowed roots.
6. Seed dlopen dependencies separately: existing NSS modules, Wayland/Mesa
   renderer and DRI modules actually used, plus required GTK modules. Copy and
   audit the recursive closure. Check libraries observed in browser process
   maps to catch a missing dlopen seed. Do not bundle a whole `/usr/lib` tree.
7. Attribute the GNU C++ runtime exception only to the actual FFmpeg descendant
   edges. A second GTK/graphics/browser path to `libstdc++` is a policy failure.

Use small compiled ELF fixtures from the pinned image to prove rejection of a
nested wrong-loader helper, transitive glibc dependency, missing library,
misplaced SONAME, direct browser GNU C++ runtime, and escaping symlink. Use
real readelf output for these integration cases. Unit tests may cover parser
errors, but mocked readelf alone cannot prove the release audit works.

```sh
ut test_helium_runtime.RuntimeCollectionTests test_helium_runtime.ElfAuditTests -q
h stage-runtime
```

**Accept:** complete desktop manifest and every-ELF audit pass. Reuse existing
manifest schemas and digests. Preserve the old valid stage on any failure.
Staged browser behavior remains pending until S13 runs its artifact matrix.

### S12 — package a relocatable candidate

**Prerequisite:** S11. **Size:** medium. **Files:** `package-runtime.py`,
product-aware launcher data, `tests/test_helium_runtime.py` (`PackageTests`).

Keep the real executable named `helium`. Add `helium-wrapper` as the relocatable
launcher. Do not replace the executable with a renamed headless Chromium.
Resolve the bundle root from the launcher's own resolved location. Set only
the library/font/resource paths needed by this package. Preserve all user argv.
Do not append test-only renderer or certificate flags to the normal launcher.
Do not require a host shell beyond POSIX sh or a host glibc loader.

Package the existing staged manifest, recursive libraries, NSS modules, fonts,
fontconfig aliases, icons, and desktop metadata. Child processes must re-execute
the correct browser and helpers with the same relocatable environment. The
upstream wrapper and AppArmor/AppImage metadata are references, not reasons to
install system profiles or gcompat.

Build `/work/package/helium-wayland-release` atomically, then write
`/work/artifacts/helium-0.20.3.1-linux-x86_64-musl.tar.xz` and its `.sha256`.
Use one archive root named `helium-0.20.3.1-linux-x86_64-musl`. Finish the archive
before replacing its visible name. Include package identity and file digests.
Label it a candidate until S13 and the target-environment gates pass.
After testing that exact archive, S13 exports the existing artifact-bound
qualification result as `<archive>.acceptance.json`. This sidecar has a concrete
consumer: the offline installer. It names the archive digest, product, version,
architecture, and actual package checks. Do not put an archive's own digest
inside that archive or repack it after the tested digest is frozen.

```sh
ut test_helium_runtime.PackageTests -q
h package
hr cat /work/package/helium-wayland-release/package-manifest.json | jaq .
```

**Accept:** package tests, all-ELF audit on the actual package, correct archive
layout and checksum, and structural validation of a fresh extraction at a
path containing spaces. S13 supplies the real relocated-browser proof,
including a symlinked launcher that resolves to the same bundle.
No writes may land in the source, build output, or installed application tree.

### S13 — complete behavior and final-package acceptance

**Prerequisite:** S12. **Size:** one behavior family per iteration. **Files:**
Linux functional drivers/fixtures/support, `tests/test_helium_acceptance.py`.

Add `--product chromium|helium` to the existing fast and functional harnesses.
For direct qualification also accept `--runtime-kind build|stage|package`.
Default existing calls to Chromium/build. For Helium, select the headed
Wayland path and bundled uBlock. Keep `--ublock-archive` required for Chromium
and reject it for Helium. Do not install an unpacked second copy of uBlock.

The current drivers run broad unittest discovery and the functional driver
hardcodes `uBlock0_1.72.0.chromium.zip`. Make Helium run only its relevant local
modules and its actual bundled component. Preserve Chromium and macOS harness
contracts. Do not import macOS platform tests into Helium's container acceptance.

Use the behavior table below as the executable checklist. Give each row a
named check in the existing result report and a regression test that rejects
missing/failing observations. Do not make tests that merely assert a check
name exists. Validate a real scenario, negative control, or report consumer.

```sh
ut test_helium_acceptance -q
h test
h test-staged
```

`test` accepts the build tree. `test-staged` must run the same required matrix
on the stage and a fresh package extraction. The extracted-package run must
have no `/work/src` or `out` mount. Keep the harness/fixtures available read-only
and all HOME/cache/profile state in a disposable directory. Require every
browser/helper library mapping to come from the extracted package or the
explicit host musl runtime. Finding a required library in the builder's `/usr/lib`
is a failure even if that library happens to be installed there.

After package acceptance succeeds, export the exact archive and its two
sidecars from the work volume. No publishing or live install is involved:

```sh
for suffix in '' .sha256 .acceptance.json; do
    hr cat "/work/artifacts/helium-0.20.3.1-linux-x86_64-musl.tar.xz$suffix" \
        > "$TMPDIR/helium-0.20.3.1-linux-x86_64-musl.tar.xz$suffix"
done
```

Copy these three files and this checkout to the target machine when available.
The SHA-256 sidecar must use only the archive basename, so it remains valid
after transfer. Verify it from the directory containing the archive.

Use a total 15-minute deadline for each full artifact run and a 30-second
readiness deadline per navigation/feature. Do not increase deadlines repeatedly
to mask a hang. Run the failed behavior alone through its test case or existing
harness check selection while diagnosing, then rerun the complete affected
artifact matrix after fixing it.

**Accept:** all mandatory checks pass independently on build, stage, and package.
A package is not qualified by a previous build-tree result. Keep S14's dwl and
S15's installation acceptance separate from automated compositor success.

### S14 — qualify the owner's Alpine dwl desktop

**Prerequisite:** S13 candidate. **Size:** environment milestone.
**Files:** `docs/linux.md`, existing harness direct-runtime entry point only.

The target is the owner's `~/usr/host/alpine` desktop: Alpine edge, amd64,
OpenRC, seatd, dwl, and ghostty-custom. It has no XWayland, display manager,
systemd, session D-Bus, or PulseAudio/PipeWire service requirement.
Read its current AGENTS.md and README read-only before qualification. Do not
edit/provision that repository or run its setup/sync scripts as part of this
campaign. Do not add browser/Wayland packages to its server provisioning mode.

On that machine, extract the candidate under this repository's `.work/tmp`.
Run inside the logged-in user's real dwl session. Use the active runtime
socket. Do not start Sway or replace the session for this gate. Add a direct
harness mode `--wayland-session existing` that requires the supplied socket,
starts no compositor, and cleans up only the browser children it owns.
The default automated mode remains `--wayland-session isolated`.

First validate and extract the archive with the shared package verifier.
Implement/test its internal `--verify-extract DIR` action in S12 so this step
needs no live installer. Strip only the single expected archive root. Require
an absent or owned empty destination and valid sidecars/manifest files:

```sh
python3 scripts/install-runtime.py \
    --archive "$PWD/.work/tmp/helium-0.20.3.1-linux-x86_64-musl.tar.xz" \
    --verify-extract "$PWD/.work/tmp/helium-dwl"
```

Then use the direct-runtime options implemented by S13:

```sh
python3 scripts/test-functional.py --product helium --runtime-kind package \
    --wayland-session existing \
    --chrome "$PWD/.work/tmp/helium-dwl/helium-wrapper" \
    --fixtures "$PWD/tests/fixtures" \
    --output "$PWD/.work/tmp/helium-dwl-results" \
    --require-llvmpipe --require-media
```

Never substitute a downloaded upstream glibc browser for this artifact. Keep
HOME/profile/trust/cache state disposable while preserving only the real session
socket and required Wayland environment. Never import the test CA into the
owner's actual NSS store.

Require navigation, tabs/vertical tabs, settings, onboarding, bundled uBlock,
extension loading, download/upload, profile restart, native keyboard and
clipboard, fonts, software rendering, TLS, sandboxing, and clean shutdown in
this real session. Sway IPC observations do not apply to dwl. Use the existing
dwl/Wayland capture and input facilities when available. If automation lacks
a protocol, leave only that observation as an explicit manual target gate and
provide the steps below. Do not call it passed from the isolated Sway run.

Manual target steps, when needed: launch the candidate normally, open a local
fixture, create two tabs, enable/use vertical tabs, visit settings and onboarding,
paste and type the unique fixture text, upload the fixture file, download it,
restart with the same disposable profile, and check persisted state. Capture
the browser in dwl and record actual outcomes in the acceptance report. Do not
read or migrate the owner's personal browser profile.

**Accept:** actual target observations pass. If this session is unavailable,
park `dwl-target` with the exact command and manual observations outstanding.
Do not ask the executor to invent a VM or claim generic Alpine is the dwl host.

### S15 — integrate installation with ~/usr safely

**Prerequisite:** S12 package for installer development, S13 for a releasable
candidate. **Size:** medium. **Files:** repository installer helper/CLI,
`tests/test_helium_runtime.py` (`InstallTests`), `docs/linux.md`.

Extend existing `install` with a Helium branch. Keep Chromium's install behavior
and its launcher untouched. Do not run the existing headless `smoke_cdp` helper
as Helium's installation criterion.

Support the builder-volume source and `--archive PATH` through the same
verification/installation transaction. Archive mode needs only the checkout,
Python standard library with lzma support, and the package/sidecars. It must
not query Docker or require compiler/build volumes on the Alpine target.
Require `<archive>.sha256` and `<archive>.acceptance.json`. Verify the archive
digest and matching qualification, then validate every extracted file.
Reject absolute/traversing entries, device files, escaping links, duplicates,
and unsupported schemas before creating installation paths. The checksum
establishes content identity, not upstream signature authenticity.

Use these Helium defaults:

| Item | Path |
| --- | --- |
| package installation prefix | `${HOME}/.local/opt/helium-wayland-release` |
| executable launcher | `${HOME}/.local/bin/helium` |
| launcher target | `<prefix>/helium-wrapper` |
| usual usr entry point | `${HOME}/usr/bin/helium`, only when usr/bin resolves to .local/bin |
| normal browser profile | Helium's upstream XDG default; never a test or Chromium profile |

`~/usr/bin` currently points to `../.local/bin` in the inspected setup. Validate
that relationship on the target instead of creating or replacing that symlink.
Reject a different managed path with a diagnostic. Keep `--prefix` and
`--bin-dir` overrides working, require absolute paths, and validate symlink
parents and package ownership before any replacement.

Use versioned slots under `<prefix>.versions/<version>-<archive-digest-prefix>`.
Use a stable `<prefix>` symlink to the active slot and `<prefix>.previous` to
its previous owned slot. The public launcher points to the stable prefix.
Derive slot names from the verified manifest/archive. Reject slots outside
that controlled parent. Do not modify an existing slot in place.

Use this installation transaction:

1. Require a complete, matching package manifest and successful package-local
   structural/acceptance reports. A staged Chromium report cannot authorize it.
2. Verify the candidate in repository-local scratch with a disposable HOME and
   profile. Do not touch the user's real profile or default browser settings.
3. Check prefix and launcher conflicts before writes. Refuse any unmanaged
   existing file, directory, or symlink. Existing owned installations must
   identify Helium and match their manifest. A broken foreign symlink is still
   a conflict. Do not replace the existing `chromium` entry point.
4. Prepare a complete versioned installation under the installation parent on
   the same filesystem. Temporary names here are part of atomic installation,
   not a general scratch area. Verify copied digests and owned permissions.
   Run the relocated candidate before switching the visible entry point.
5. Publish the stable prefix with atomic symlink replacement. For first install,
   publish the launcher only after the prefix points to the verified slot. Keep
   the previous owned version reachable until the installed-path probe passes.
   On any failure restore the old visible prefix and launcher before exiting.
6. If no graphical session is available, verify only the candidate and refuse
   final activation with an actionable command. Do not install a browser whose
   required final host check was skipped. Do not fall back to `--no-sandbox`.
7. Remove only the failed attempt's owned temporary install paths. Keep a
   previous working version for explicit rollback. Do not delete user profiles,
   unrelated dotfiles, `dl` ownership records, or the old Chromium package.

Tests must cover first install, identical rerun, upgrade, digest corruption,
missing manifest, wrong product, foreign prefix/link, broken foreign link,
symlink escape, failure before activation, failure after activation, and rollback.
Archive installation and rollback must both make zero Docker calls.
Assert the previous launcher still runs after each injected failure. Use real
filesystem operations in a fake HOME under `.work/tmp`, with a controlled browser
probe. No test writes into the actual `~/usr` or `~/.local`.

```sh
ut test_helium_runtime.InstallTests -q
```

After the owner explicitly requests a live installation, the target command is:

```sh
./chromium-build --product helium --platform linux --arch amd64 \
    --profile helium-wayland-release install \
    --archive "$PWD/.work/tmp/helium-0.20.3.1-linux-x86_64-musl.tar.xz" \
    --prefix "$HOME/.local/opt/helium-wayland-release" \
    --bin-dir "$HOME/.local/bin"
"$HOME/usr/bin/helium" --version
```

Add Helium `install --rollback`, mutually exclusive with `--archive`, using
the same ownership checks. It atomically switches to the verified previous
owned slot and checks the installed path. It needs no Docker. A missing/foreign
previous slot fails without changing the active link. The recovery command is:

```sh
./chromium-build --product helium --platform linux --arch amd64 \
    --profile helium-wayland-release install --rollback \
    --prefix "$HOME/.local/opt/helium-wayland-release" \
    --bin-dir "$HOME/.local/bin"
```

Then rerun S14 through `$HOME/usr/bin/helium` with a disposable profile and
confirm normal interactive launch from ghostty in dwl. Do not run this live
install during a plan update or infer publication/host changes from the plan.

Keep default-browser MIME changes, new dwl keybindings, `dl` downloader
integration, and automatic provisioning outside this repository's scope. They
are not needed for a working `helium` command in the owner's current PATH.
Ship desktop metadata in the package and document optional registration without
silently editing the `usr` source-of-truth tree or home dotfiles.

### S16 — document, verify regressions, and close honestly

**Prerequisite:** all available local slices. **Size:** small.
**Files:** `docs/linux.md`, CLI help, Makefile, this document's Status.

Document a clean-checkout path using the exact locked image/fetch/prepare/gate/
build/test/stage/package/install order. List tested host prerequisites, disk,
memory, native architecture, Docker, optional test tools, and the distinction
between an isolated fixture and the owner's dwl session. Include cache statistics,
compile resume, failed-preparation recovery, package extraction, target verification,
installation conflicts, upgrades, and rollback. Do not call documentation a
substitute for executing a command.

Run the integrated local suite after the implementation reaches each milestone
(S05, S10, S13, S15) using only modules introduced by then. At close run:

```sh
ut test_product_selection test_helium_inputs test_helium_prepare \
    test_helium_gn test_helium_wayland test_helium_runtime \
    test_helium_acceptance test_fetch_inputs test_browser_test_support -q
```

Also run the existing Chromium runtime and native macOS checks in Needs
environment when their environments are available. Do not weaken those checks,
remove existing tests, or treat unavailable native proof as a pass.

Perform a focused completion review of the real diff and fix demonstrated
high-severity correctness/security defects. No release optimizations or extra
framework work may precede complete functionality. Measure memory and package
size. Apply the time-based performance rules below only when making performance
claims or qualifying required performance gates.

Update Status in place with accepted items, the exact first uncompleted item,
failed/parked gates, and resume command. Do not append chronological logs.
Report input/toolchain revisions, significant compatibility fixes, real commands,
cache evidence, ELF audit, behavior/sandbox matrix by artifact, archive contents,
size/checksum, Chromium/macOS status, dwl status, and installation status.

Local implementation may finish while an environment gate remains parked.
A fully qualified release requires the real package, enabled sandbox,
representative Alpine/dwl behavior, and installed-path acceptance when live
installation is authorized. If the owner has not requested live installation,
report the installer as tested in scratch and the target installation as pending.

## Diagnose a first failure without losing state

Do not launch a broad rebuild to diagnose a narrow error. Use the first failing
command and preserve its full stderr in the existing phase log.

| Symptom | First action | Fix boundary and resume |
| --- | --- | --- |
| missing old apk version | reproduce the solver result in the pinned Alpine base | update only Helium's lock, rebuild image, rerun package/environment validation |
| source digest mismatch | compare cached bytes to frozen lock and upstream hash pin | quarantine only that unverified download; refetch exact input; never edit expected digest to match it |
| interrupted preparation | inspect the attempt and first failed transformer | fix recipe and create one fresh attempt; no success stamp or in-place replay |
| fuzzy/failed patch | inspect hunk preimage and upstream 156 postcondition | regenerate one required patch with zero fuzz; rerun the complete stack |
| unused GN arg | inspect declaration and effective args | correct that profile entry; keep strict generation |
| Node/Rust/generator failure | capture the exact generated action and pinned version | port its scoped source/tool wrapper; retry that action with build-target |
| missing header/library | inspect compile argv and pkg-config in image | add exact required pinned package or correct include/target dependency |
| C/C++ compile diagnostic | rerun the failed Ninja output with one job | source-level fix and targeted regression; no new global warning suppression |
| unresolved symbol | identify defining object/library with llvm-nm and GN deps | add/fix the real feature/target edge; no dummy symbol or desktop stub |
| OOM or disk pressure | inspect owned build log and host resources | lower jobs, free only proven disposable owned scratch, rerun Ninja; keep outputs |
| no Wayland window | inspect owned compositor log/tree, browser stderr, runtime socket and permissions | fix session/socket/toolkit path; never change to headless success |
| crashpad child cannot run | inspect handler path, ELF interpreter and dependencies | fix graph/stage/launcher; do not remove crashpad initialization |
| sandbox EPERM | inspect userns/seccomp policy and process evidence | park environment if restricted; no sandbox-disable flag |
| uBlock does not block | inspect bundled component/version, settings, rule and fixture hits | fix actual component/rule readiness; no second test-only blocker |
| HTTPS rejects trusted test chain | inspect test CA import, SAN, dates, server chain and NSS database | fix normal trust setup; no certificate ignore flag |
| package runs only in builder | inspect every browser/helper mapping and resource lookup | add missing closure/resource or fix relative lookup; no system-package workaround |
| install conflicts or fails | inspect prefix/launcher ownership and the failed stage | preserve old installation, repair candidate in scratch, rerun explicit install |

Limit each individual diagnosis to three fixes that produce new evidence.
Then park it with the exact failing command, error, preserved state, affected
items, and retry condition. Do not label a dependency-blocked browser release
complete. Do not ask the owner to solve a technical fact that source or a safe
probe can establish.

## Required browser observations

Run every mandatory row on build, stage, and extracted package in S13. Repeat
session-dependent rows in the real dwl session in S14. A result is one of
`passed`, `failed`, or `not-executed`. Missing data is not a pass. The aggregate
may be complete only if every mandatory row for that artifact passed.

| Check | Procedure and exact pass condition | Negative control or regression case |
| --- | --- | --- |
| product-identity | executable, generated version, runtime manifest and CDP executable path identify Helium 0.20.3.1 from Chromium 156.0.8078.79 | renamed Chromium or wrong-version metadata fails before browser acceptance |
| wayland-window | native Wayland surface maps for the launched PID; compositor output contains window chrome and the fixture's known colored/content regions | no mapped surface or uniform-background screenshot fails even when CDP responds |
| page-rendering | local fixture JS returns its expected value; CSS bounding boxes and known screenshot pixels match the fixture | missing JS execution, missing stylesheet, or wrong screenshot dimensions fails |
| navigation-tabs | open local A, navigate to B, back to A; create a second tab, switch, and close it; require expected target URLs and active content | target creation alone cannot pass tab switching |
| vertical-tabs | use the real Helium UI command/shortcut to switch layouts; create two tabs and select both; require observable layout and content changes | preference-file edit without a visible layout change fails |
| settings | navigate to settings, wait for actual settings controls, change one harmless preference and read it back | a nonempty HTML shell or blank WebUI cannot pass |
| onboarding | with a fresh disposable profile and normal first-run behavior, require actual onboarding resources and controls; complete a harmless step and verify the persisted onboarding state | the pilot's no-first-run flags must not suppress this check |
| privacy-defaults | compare the new profile's defining defaults to the pinned Helium patches; verify blocked Google/service behavior through local observations where possible | inherited Chromium defaults cannot be normalized away |
| bundled-ublock | component metadata shows the bundled Imput 1.75.0 build enabled; load one deterministic blocked URL and one allowed sibling URL; blocked URL never reaches server or executes, allowed URL does both | disable the component in a separate disposable test profile and prove the blocked fixture reaches the server; then restore an enabled fresh profile |
| extension | load the existing separate test extension through the supported Helium extension path and require its page-visible action | a prepopulated fixture variable without a live extension cannot pass |
| download | download fixture bytes, wait for CDP completion, compare actual file bytes | missing/partial/wrong bytes fail |
| file-upload | populate an actual file input with the fixture and submit to the loopback server; compare received bytes | DOM filename text without uploaded bytes fails |
| profile-restart | set a harmless preference and local persistent storage, close, restart with the same disposable profile, and read both back | a fresh-profile rerun cannot satisfy persistence |
| keyboard | focus the real browser and fixture input, send text with Wayland virtual-keyboard input, assert actual input/change events and final text | CDP `Runtime.evaluate` assignment or CDP keyboard dispatch alone cannot satisfy native Wayland input |
| clipboard | copy a unique sentinel with `wl-copy`, paste with native keyboard into fixture input; copy selected browser text and require `wl-paste` to return it | permission errors, empty text, or setting clipboard only through a CDP override fails |
| fonts | render existing serif/sans/monospace samples and verify distinct measured widths/families plus visible glyphs; inspect bundled fontconfig aliases | a single fallback face for every family fails |
| software-graphics | CDP GPU information identifies Mesa llvmpipe/software rendering; browser renders the fixture without a passed-through GPU device | `--disable-gpu` or the compositor's pixman setting alone cannot pass |
| hardware-graphics (S14 only) | on the Broadwell desktop, GPU information identifies Mesa iris, `vainfo` lists H.264/VP8 profiles, and `chrome://media-internals` selects `VaapiVideoDecoder` for an H.264 or VP8 fixture | llvmpipe, missing VA-API profiles, or a software decoder for the fixture fails; VP9/HEVC software fallback is expected |
| media | existing pinned local media reaches the required playback state and decoded frames/audio-track observations; retain its dynamic-library audit | renaming the codec fixture or disabling media checks fails |
| tls-trust | the normal verifier accepts the local chain after importing its CA into a disposable NSS store; rejects the same chain without that CA, a wrong hostname, and an expired leaf | no ignore-certificate switch, SPKI exception, CDP security override, or navigation through an interstitial may authorize success |
| sandbox-renderer | observe a live page renderer, enabled Chromium namespace/seccomp sandbox status, and process evidence meeting the rules below | a container-provided seccomp filter or absence of `--no-sandbox` alone cannot pass |
| sandbox-other-processes | retain process-type-specific GPU/utility sandbox observations and upstream policy; GPU aux info reports sandboxed where required | do not apply renderer rules indiscriminately to every helper or exempt all utilities |
| clean-shutdown | browser, renderer, Crashpad and owned test helpers exit; no owned descendants remain after the cleanup deadline | timeout and abnormal exit cases must exercise TERM/KILL/reap behavior |

For native UI observations, inspect the pinned Helium source to select actual
commands and accessible controls. Put source-derived selectors/shortcuts next
to the behavior in the test, not in a generic guessed-UI layer. If the local
UI differs from the source, diagnose the missing feature instead of weakening
the observation. Use CDP for page content and WebUI only where it reaches that
real control. Use compositor input for keyboard/clipboard and native chrome.

The fixture's network resolver maps ad-like domains to loopback. Keep fixed
local content and record server hits. Do not require live filter-list downloads
for a deterministic uBlock test. Use its bundled lists when available; if a
specific test rule is needed, add that explicit rule to the real bundled
component in the disposable profile through its supported settings. Verify
the negative control. Never substitute an unrelated test extension as the blocker.

### Verify TLS without a bypass

The existing functional fixture passes an SPKI exception. Preserve legacy
Chromium behavior where required, but do not count that test as Helium trust
qualification. Add a test-only CA and a locally served leaf chain with localhost
and 127.0.0.1 SANs, plus wrong-hostname and expired-leaf cases. Generate short-lived
valid fixtures within the attempt using the pinned OpenSSL tool, or validate
committed fixture dates before use. Never use wall-clock changes to make a
certificate pass. Keep test keys confined to public test fixtures or scratch.

The pinned Chromium 156 source's `docs/linux/cert_management.md` says the normal
NSS store is under `.local/share/pki/nssdb`, with legacy `.pki/nssdb` used if
already present. Use a fresh isolated HOME so there is no legacy ambiguity.
The harness must create and import only inside that HOME:

```sh
mkdir -p "$HOME/.local/share/pki/nssdb"
certutil -N --empty-password -d "sql:$HOME/.local/share/pki/nssdb"
certutil -A -d "sql:$HOME/.local/share/pki/nssdb" \
    -n helium-local-test-ca -t 'C,,' -i "$TEST_CA_PEM"
```

These are harness setup commands, never commands against the owner's real HOME.
`TEST_CA_PEM` names that attempt's test CA. Restart the browser after trust-store
changes. Require the trusted page's expected JS/content and `Security` state.
Require the untrusted/wrong-host/expired cases to yield an actual certificate
failure and no successful fixture content. Inspect every launched process argv
and reject all certificate-verification overrides in Helium acceptance.

### Prove Chromium's sandbox rather than the container's

Run the browser as a nonroot user. Reject `CHROMIUM_TEST_NO_SANDBOX=1` for Helium
instead of silently ignoring it. Reject sandbox-disable flags in the launcher,
browser, renderer, GPU and utility argv. Do not count a failed sandbox check as
a pass because the host is inconvenient.

For an alive renderer record PID, parent identity, argv, `NoNewPrivs`, `Seccomp`,
`Seccomp_filters` when available, and `/proc/<pid>/ns/{user,pid,net}` identities.
Record browser and harness/container baselines for comparison. Require
`NoNewPrivs=1` AND `Seccomp=2`, Chromium's own sandbox status, and evidence of
Chromium-created namespace/filter isolation relative to those baselines.
The current helper records only status fields and accepts NoNewPrivs OR seccomp
in one legacy check. That condition is insufficient for the new Helium gate.

Use the sandbox's real reported mechanism to interpret a zygote/renderer shared
namespace. Do not require every renderer to have a namespace distinct from its
zygote. An unchanged inherited container filter by itself does not prove a
Chromium filter. If `/proc` or status cannot supply the necessary distinction,
leave that check unverified and require the normal-user target-host test.

The existing privileged Docker test path may be used for diagnosis, with the
privilege level recorded. It never replaces a normal-user Alpine/dwl run.
Do not add privilege to the delivered launcher or require a setuid-root browser
installation as a fallback.

## Keep the phase and package contracts consistent

Change every actual consumer of a product fact. These are mandatory integration
points, not suggestions for a repository-wide audit:

| Owner | Required adjustment |
| --- | --- |
| `chromium-build` | selection before effects; image/volume defaults; product/env forwarding; output-aware stage/test/install dispatch |
| `Makefile` | PRODUCT defaults; no forced Chromium image on Helium; actual target-aware diagnostics |
| `fetch-inputs.py` | selected lock, multiple source archives, normal digest/redirect verification |
| `prepare-source-driver.py` and `prepare-source.py` | distinct Helium recipe, strict patch ownership, atomic completion, safe integration updates |
| `verify-environment.py` | input/package/policy selection, actual product source inputs, existing ccache/toolchain policy |
| `write-environment-metadata.py`, `preflight-system-libraries.py`, `lockcheck.py` | same selected locks/policy and real installed closure |
| `verify-source.py` | real Helium generators and version fields, compatible existing disposition states, provenance complete for every selected layer |
| `generate-gn-args.py`, `generate-gn.py`, `verify-gn-args.py` | distinct desktop effective-argument contract; no headless optional-argument injection |
| `run-probes.py`, `run-gate-e.py` | selected toolchain/source evidence and unchanged musl exception/thread/libc++ gates |
| graph driver/auditor/oracle | target closure and matching current product/graph identities |
| `resolve-output.py`, `build.py`, `build-target.py` | actual Helium executable, no Chromium-name fallback for Helium, preserved Ninja state |
| `stage-runtime.py`, `package-runtime.py` | graph runtime files, every-ELF closure, atomic candidate/manifest/archive, relocated child execution |
| fast/functional drivers and harnesses | product-specific args/extension source, bounded compositor lifecycle, artifact-bound results, explicit direct-runtime options |
| `media-runtime-audit.py` | product/artifact identity and scoped FFmpeg runtime evidence |
| installation branch/helper | manifest ownership, conflict rejection, transaction/rollback, usr launcher integration |
| `macos_backend.py` and macOS locks | preserve; reject Helium before native dispatch |

Retain existing schema fields required by Chromium readers. Add product identity
and artifact kind to existing reports rather than changing unrelated formats.
Reject unsupported schemas and missing required Helium observations explicitly.
Do not add backward-compatibility aliases without a current consumer.

### Update integration patches without discarding Ninja output

A compiler fix during S08/S10 must not force an expensive clean rebuild. Add a
narrow internal preparation-driver operation `--update-integration` for this
specific workflow. Do not add a generic force/reset switch to the public CLI.
Test the operation in `PatchContractTests` before using it on the real tree.

Permit only an appended local Helium integration patch whose old source,
upstream inputs, substitutions, and earlier patch stack are unchanged. Require
the existing preparation identity and exact old preimages for every touched
file. A change to upstream inputs, an earlier patch, or recipe ordering requires
a separate fresh preparation and cannot use this path.

Copy the affected preimages into an owned diagnostic attempt. Apply the new
patch there with zero fuzz and verify its exact postimages. Apply the delta
transactionally to the existing prepared tree, with affected-file recovery copies under the owned
attempt directory. On failure restore all touched files and retain the old
identity. On success publish source/provenance identity from the actual checked
operations and invalidate only downstream gates. Preserve `out/` and all Ninja
depfiles. Rerun C/D/F, then the exact failed target. Before final package
qualification, replay the final full stack once on fresh source and compare
the changed postimages with the live tree. This checks replayability without
repeating a full source preparation for each compiler error. Do not hand-edit
reports or claim verification based on a patch file that was never applied to the live tree.

After rebuilding the image, invoke only against the owned Helium volume:

```sh
h image
docker run --rm --network=none \
    -e CHROMIUM_PRODUCT=helium -e CHROMIUM_ARCH=amd64 \
    -e CHROMIUM_PROFILE=helium-wayland-release \
    -e CHROMIUM_METADATA_ROOT=/work/metadata \
    -v "$HELIUM_WORK:/work" "$HELIUM_IMAGE" \
    python3 /opt/chromium-build/scripts/prepare-source-driver.py --update-integration
h gate-c
h gate-d
h gate-f
```

Then rerun the exact failed output with `h build-target`. The update operation
must acquire exclusive ownership for its mutation. Its unit tests reject a changed
old patch, wrong preimage, wrong product, changed upstream lock, and injected
failure halfway through mutation. If this safe operation is not implemented,
preserve state and park the fix rather than editing source and fabricating
new preparation evidence.

## Current status

- S00–S09 are complete for the current source and profile. Source preparation
  includes `helium-no-devtools-frontend.patch`, `helium-no-dbus.patch`,
  `helium-dawn-vulkan-headers.patch`, and the musl seccomp compatibility
  patches. Gate A, Gate C, Gate D, and Gate F pass for the final builder image
  `sha256:160899ccbfcd29483806ae6a5787b2e29b7ef2aaa8c6f2c10bdc41ba6972a91b`.
- S10 is complete. The profile built with `is_official_build=true`,
  `chrome_pgo_phase=2`, ThinLTO with optimizations, `use_dbus=false`,
  `use_vaapi=true`, and no DevTools frontend resources. The final build report
  records a 319-second gate-triggered relink after the source was already
  compiled; the initial full graph build is the long pole and is not claimed as
  a clean-build benchmark. `chrome_sandbox` is built separately with
  `build-target chrome_sandbox` because `ninja //chrome:chrome` does not build
  that data dependency.
- S12 and S13 are complete for this archive. `stage-runtime` collected 320
  files and 147 libraries. `package` produced
  `helium-0.20.3.1-linux-x86_64-musl.tar.xz` (223.3 MB, 521 files, SHA-256
  `6853af22b60345c92e0cbe69181aebd8af3fa79842c7f0135d20c9656c5918c6`).
  `test-fast` and `test-staged` passed 29 unit tests plus the headed Wayland
  browser matrix. A fresh extraction of that archive passed product identity,
  Wayland window, page rendering, three sandboxed renderers, and clean
  shutdown; `.acceptance.json` binds the tested archive digest.
- The raw-CDP path exposed three profile-specific runtime defects, each fixed
  at its source rather than with a launch flag: musl's `pthread_create`
  membarrier registration (`musl-seccomp-membarrier.patch`), musl's
  `CLONE_DETACHED` thread flags (`musl-seccomp-clone-detached.patch`), and the
  DevTools policy allowlist pref that raw CDP reads even without the frontend
  (`helium-devtools-policy-prefs.patch`). The system FFmpeg closure carries the
  scoped GNU C++ runtime exception for `libstdc++.so.6`/`libgcc_s.so.1`; the
  audit allows those edges only below the FFmpeg roots.
- S14 and S15 remain parked on the owner's dwl session and an explicit
  live-install request. The automated Sway fixture is only a builder check.
  Real Broadwell VA-API decode is `CHB-6`.

## Performance qualification

No speedup target is imposed before feature parity. Measure memory and binary
size even when the host is busy. Time-based performance gates run only after
`perf-gate-ready` succeeds and remain valid only if `perf-gate-ready -1` also
succeeds immediately afterward. Otherwise report `perf gate deferred: host busy`
and do not call release qualification complete. Use interleaved baseline and
candidate runs, CPU time for compute cost, and medians for typical durations.
Treat only differences larger than observed spread as regressions. Do not compare
full Helium desktop size directly with headless Chromium as an optimization gate.
The lean profile enables PGO and ThinLTO; measure its package size and runtime
behavior after S13, and do not attribute a speedup to either mechanism without
a matched baseline from the same source and acceptance setup.

## Needs environment

The current headless server cannot supply the owner's real desktop session.
S14 and live S15 activation are expected target-machine handoffs. Software
Wayland checks remain meaningful local proof, with their environment recorded.
Do not provision a desktop or alter this server to close those gates.

Keep these obligations separate from local test success. Attempt available proof
before classifying an environment as unavailable. Record the failed prerequisite
and the exact resume command. No unavailable check becomes passed by inference.

| Gate | Required environment | Procedure |
| --- | --- | --- |
| real image/source/compiler/browser | native amd64 Docker, locked inputs, sufficient disk/RAM | S04–S13, resuming at the first failed item |
| Alpine dwl target | owner's normal-user Alpine edge desktop and active dwl Wayland socket | S14 direct-runtime command and outstanding manual observations |
| installed usr entry point | same desktop, qualified archive/sidecars, explicit live-install request | S15 offline install then S14 through `~/usr/bin/helium` |
| existing Chromium | existing pinned image/source/output and compatible host sandbox | commands below |
| native macOS | arm64 macOS, its current locked tools and existing acceptance environment | commands below |
| time-based performance gates | `perf-gate-ready` succeeds before and after the measurement | Performance qualification rules above |

For existing Chromium, preserve its completed artifacts and run:

```sh
./chromium-build --product chromium --arch amd64 --profile headless-release test
./chromium-build --product chromium --arch amd64 --profile headless-release test-staged
```

If its image/build is missing, report unavailable runtime regression proof.
Do not rebuild unrelated Chromium merely to hide that limitation.

On native macOS arm64, use the existing checkout/environment and run:

```sh
./chromium-build --platform macos --arch arm64 doctor
PYTHONPATH=scripts:tests python3 -m unittest test_macos_backend -q
./chromium-build --platform macos --arch arm64 test-staged
```

Follow that platform's configured work root and staged app. Do not reset or
rebuild it automatically. Linux cannot establish native macOS behavior.
Shadowdriver remains part of the existing macOS contract and outside Helium.

For a resource-limited Helium compile, preserve the failed target/Ninja state
and report `h build --jobs 1` as the conservative next command, with `h` expanded
to its full CLI form in the final handoff. A partial object build cannot qualify
a package, sandbox, or installed launcher.

## Execution boundary and non-goals

The current task permits source inspection, bounded experiments, and this plan
revision, including the eventual usr integration requirement.
A future implementation instruction permits repository changes and local build,
fetch, and test effects needed above. Follow the owner's explicit commit policy.
No instruction here grants pushing, publishing, live installation, upgrading
host tools, modifying other repositories, or upstream contact. A later explicit
Helium install request authorizes only the S15 application prefix/launcher
transaction and its disposable host verification. It does not authorize edits
to `~/usr`, home dotfiles, defaults, provisioning, or personal browser profiles.

Contract changes to surface during implementation are the CLI product selector,
product-specific profiles/locks, phase identity and invalidation, desktop runtime
manifest, complete ELF audit, archive format/name, GUI/sandbox acceptance,
offline archive installation, and the usr launcher/rollback transaction.
Update their callers, tests, and documentation together. Preserve old Chromium
commands, report readers, and macOS behavior. Do not add compatibility shims
without a demonstrated current consumer.

No Linux arm64, new macOS product, static browser, AppImage, Debian/RPM/Flatpak,
GPU performance tuning beyond enabling Chromium's VA-API decode path,
distribution service, CI work, or Shadowdriver integration for Helium. Retain
upstream-required helpers; building chromedriver
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

## Procedure references

The source snapshots under `.work/tmp` support the pinned preparation and
branding facts. They remain disposable caches. The production locks and upstream
archive bytes must carry the actual reproducibility contract.

The automated compositor recipe uses the documented wlroots
[headless backend and renderer settings](https://github.com/swaywm/wlroots/blob/master/docs/env_vars.md).
The selected [wtype input tool](https://github.com/atx/wtype),
[grim capture tool](https://github.com/emersion/grim), and
[wl-clipboard utilities](https://github.com/bugaevc/wl-clipboard) supply the concrete
input, screenshot, and clipboard roles. S04/S09 must pin and exercise compatible
versions. These upstream documents establish the method, not a claim that this
repository's image has already passed it.

For GN runtime-dependency generation, use the pinned executable's
`gn help runtime_deps` and `gn help gen` to confirm the
[documented runtime-dependency interface](https://gn.googlesource.com/gn.git/+/9198ba2ced7aa5cece93e40e94553ceb621f0d96/docs/reference.md).
For certificate handling, the pinned Chromium source includes
`docs/linux/cert_management.md`, matching the
[Linux certificate management procedure](https://chromium.googlesource.com/chromium/src/+/ece3098a2d62b418a4ff4b59b37ddbcad2c13d1e/docs/linux/cert_management.md).
