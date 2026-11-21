# Fil-C Hydrogen feasibility campaign

## Status

- **Goal:** determine whether Hydrogen can run with meaningful Fil-C memory
  protection, and produce a reproducible Linux musl candidate if it can.
- **State:** open; source review complete; runtime feasibility unproven.
- **Backlog:** [CHB-35](../TODO.md), P3 research priority (assumed; no
  production defect or urgency was asserted).
- **Accepted code baseline:** `10b7bec505eea27394aa50cb987436d538f64043`.
  No Fil-C implementation slice, build, benchmark, or browser run is accepted.
- **Next:** FC0, an isolated native-Linux runtime pilot and control inventory.
- **Red / parked:** no experiments run; native Linux build execution is needed.
- **Execution branch:** create or resume `campaign/filc-hydrogen` from current
  master. Preserve concurrent work and refresh the baseline before editing.
- **Resume:** read this document and AGENTS.md, inspect the branch and retained
  evidence, then execute the next unaccepted slice. First command:
  `git status --short --branch`.

Rewrite this block in place after accepted work. Git history records earlier
checkpoints; do not append a second chronological campaign log.

## Charter

The owner requested this campaign on 2026-11-21 to find out whether Fil-C
Hydrogen is feasible, despite the expected difficulty. Pursue an actual port.
A compiler error is a problem to reduce and repair, not a no-go verdict.

Work autonomously within an isolated experimental build. Local patches to
Fil-C, its runtime, the Rust integration, Chromium, and V8 are in scope when
needed for this goal. Keep dependency patches and exact upstream revisions
reproducible from this repository. Continue from runtime probes to V8 and
then to the browser as evidence permits. Do not expand evaluation tooling
before delivering the first executable pilot.

Distinguish these outcomes:

| Outcome | Required evidence |
| --- | --- |
| Executable experiment | A real artifact runs; instrumented coverage, native code, disabled features, and unresolved boundaries are listed. This is useful progress. |
| Protected pilot | The selected runtime/FFI/generated-code path has a documented protection design, behavior tests, and negative controls for its specific safety claims. |
| Feasible with constraints | A reproducible browser completes the declared workload under documented protections, with explicit platform, performance, and feature limitations. This does not qualify it as the production replacement. |
| Qualified native candidate | The Linux musl product satisfies required Hydrogen behavior, sandbox, dependency, and artifact checks, with measured resource costs and no unresolved gaps in the predeclared protection scope. |
| Not feasible within the investigated scope | Reproducers, attempted fixes, alternatives, and the remaining architectural or resource requirement justify that conclusion. Do not generalize it to mathematical impossibility. |

Tests alone do not prove every memory access safe. State the mechanism,
trusted runtime assumptions, and exercised coverage alongside results.
A source audit, successful link, page load, or emitted JSON report does not
substitute for the next outcome's evidence.

### Continuation and ownership

One integrator owns this document, TODO.md, profile routing, locks, common
build scripts, and the proof entrypoint. After FC1, Rust bridge work and V8
work may run concurrently in disjoint patch/test files and separate
`.work/wt/<lane>` worktrees. Each lane owns its dependency checkout, output,
and evidence. Integrate shared changes serially; a failed integration check
stops new lanes until repaired. Keep failing experiments on their branch.

After each accepted slice, on resume, or after two failed attempts, reassess
the smallest useful next experiment. Two attempts at the same hypothesis,
or two hours of diagnosis without a new distinguishing observation, require
a reduced reproducer or a different hypothesis. This is a steering trigger,
not automatic campaign cancellation. A progressing long compilation is not
a failed diagnosis attempt. Record its wall-clock budget and next inspection
deadline before starting it; preserve resumable output on expiry.

If two checkpoints produce no accepted slice, stop adding lanes and finish
the smallest remaining proof serially. Park only affected work when
hardware, authority, or a reproducible architectural blocker prevents it.
Continue independent work. Conclude only after delivering the declared
outcome or accounting for every remaining path and retry condition.

Use Conventional Commits, with the slice in the commit body. Keep code
comments about behavior, not campaign IDs. Commit accepted changes in small
slices; do not mark red experiments as accepted product changes.

## Decisions and boundaries

**Owner-decided:** open a serious Fil-C feasibility campaign for Hydrogen.
Rustification of bctl/Reactor remains separate. Opening the campaign does
not claim a working port or start a browser build.

**Prescribed:**

- Begin on native Linux amd64. Linux arm64 follows a credible native pilot;
  Reactor integration follows native Linux qualification. macOS and Windows
  are outside this campaign.
- The destination is the existing `chromium` product with its Wayland
  appliance behavior, not a replacement browser engine. Hydrogen's executable
  remains `chrome`; Reactor is bctl's kernel.
- The final target/runtime closure must meet the musl product contract.
  A glibc diagnostic build is permitted and must be labeled diagnostic.
  Native host tools may use the existing supported host environment.
- Use an opt-in `chromium-wayland-filc` profile, separate compiler/runtime
  inputs, and separate Docker image, work volume, and compiler cache.
  This profile does **not exist yet**. FC1 implements it.
- Initially disable PGO and LTO only in the experimental configuration and
  its matched native control. Preserve production defaults and checks.
  Report all other configuration differences explicitly.
- A jitless V8 configuration is an initial research route. Generated builtins
  still need protection and ABI integration. Feature reductions are recorded
  experiments; they never silently redefine Hydrogen parity.
- Retain native adblock, HarfRust shaping, CDP, required rendering behavior,
  and isolation requirements in the qualification target. Inventory other
  Rust and generated-code dependencies from the actual graph.
- Allow native host compilers, generators, and Rust proc-macros. Classify
  their emitted code separately. Fil-C's documented native runtime substrate
  is a trusted dependency to inventory, not an accidental ABI violation.
  Do not relabel V8 or another large unprotected dependency as trusted merely
  to pass the campaign.
- Fetch pinned inputs through the repository's fetch phase; later build
  phases remain offline. Ordinary lock changes follow the existing
  sources.lock.json contract. Experimental inputs need verified identities
  and must not silently alter production inputs.

**Delegated:** choose compiler fixes, newer pinned Fil-C revisions, Fil-Rust
musl repairs, V8 code-generation changes, allocator adaptations, or supported
ABI bridges. Prefer the smallest change that preserves the claimed
protection and can survive an upstream rebase. Reproduce any public bridge
or backend at the selected revisions before relying on it. Record choices
on which later work depends.

**Effects:** use local checkouts and isolated builder containers; commit
campaign changes. The campaign does not authorize installing over the user's
browser/toolchain, deleting shared caches or volumes, replacing production
artifacts, production promotion, publishing releases, dispatching CI,
creating upstream issues/PRs, or messaging maintainers. Do not modify sibling
repositories or push dependency forks as a side effect; keep patch series
here for later integration. Follow the explicit execution grant for remote
publication; this plan supplies no missing grant.

Follow AGENTS.md: no formatters, linters, hooks, or CI; focused local checks.
Do not run host setup or install global tools. Missing builder infrastructure
is an environment requirement, not a reason to alter the host blindly.

## Baseline and known constraints

The source review used the baseline above. Refresh these facts at execution
start; avoid silently advancing Chromium and changing its compiler in the
same comparison.

| Item | Observed source state |
| --- | --- |
| Hydrogen | Chromium 156.0.8078.79, package revision 2 in config/inputs.lock. This is a recipe, not evidence that r2 was built or deployed. |
| V8 | Chromium DEPS pins `c9b3cfcab30ace06c21f9b48a0f0a34805e81e9d`. |
| Native toolchain | Linux amd64 selects Chromium Clang 24.0.0git; arm64 selects Laputa LLVM 23.1.5. GN generation derives the compiler version from the ordinary lock. |
| Product profile | chromium-wayland-release selects musl, a fixed compiler path, in-tree libc++/libunwind, PGO, ThinLTO, and native adblock. |
| Rust | Adblock transfers owned objects, strings, vectors, and callbacks. HarfRustShaping is stable by local patch. Audit the current Crubit/font and other Rust graph too. |
| Fil-C evidence | Upstream source snapshot `6db2b56416c79376b1dcad65247afc700af3574b`, based on release 0.687. Fil-Rust notes document Rust 1.90, musl std link failures, and unsupported ThinLTO. These are upstream observations, not local reproductions. |
| Local execution | Fil-C build/run status, build duration, memory overhead, jitless compatibility, and browser workload timings are all **unmeasured**. |

Two foundations are deliberately unproven: Fil-C's target/runtime closure
under the musl contract, and preservation of protections across V8 and Rust
boundaries. FC0-FC3 establish them. Source planning does not pass runtime
preflight.

### Existing control commands

On an already provisioned native Linux amd64 Docker host, these existing
commands prepare an **ordinary-Clang stock control**, not a Fil-C build.
They retain production PGO/LTO settings; FC1 adds the configuration-matched
native control before compiler-overhead comparisons:

```sh
export CHROMIUM_BUILDER_IMAGE=hydrogen-filc-control:amd64
export CHROMIUM_WORK_VOLUME=hydrogen-filc-control-work-amd64
export CHROMIUM_CACHE_VOLUME=hydrogen-filc-control-ccache-amd64

./chromium-build --product chromium --arch amd64 \
  --profile chromium-wayland-release image
./chromium-build --product chromium --arch amd64 \
  --profile chromium-wayland-release ensure-source
./chromium-build --product chromium --arch amd64 \
  --profile chromium-wayland-release poc
```

`poc` runs Gates A-F, including runtime probes, hermetic smoke and graph
checks. It does not build the whole browser. Reuse valid retained state.

A known Ninja target after the graph exists:

```sh
./chromium-build --product chromium --arch amd64 \
  --profile chromium-wayland-release \
  build-target tools/hermetic_smoke:hermetic_smoke --jobs 2
```

Do not use generic `make build` or `release-linux` as the first experiment:
the current release sequence reaches installation. Use explicit phases.

### Resumption hazards to repair or respect

- GN output is profile-specific, but `/work/metadata` is shared within a
  work volume. Separate output directories alone do not separate evidence.
  Give the candidate its own work volume and image/cache identities.
- `build.py` checks Gate D's profile; `build-target.py` currently only
  requires the report to exist. FC1 makes narrow-build reuse validate the
  selected profile and compiler/runtime identity.
- `scripts/resolve-output.py` selects already existing executable outputs.
  A first `build-target //v8:d8` can fail before Ninja. Discover the actual
  Ninja target/output in the generated graph and record that first-build
  command; do not guess a universal label.
- `metadata/build-target.log` is overwritten by the next target. Archive it
  under the attempt identity before another invocation.

## Sequence and proof

The proof commands below are **planned interfaces to implement**, except
the existing control commands above. FC0 introduces only the smallest
`tests/filc/verify.py runtime RUN` proof; extend this thin runner when a
later real artifact needs verification. Do not build a generic campaign
framework or fabricate green reports for absent binaries.

`RUN` is an example retained directory, `.work/filc/run-001`. The runner
reads its captured identities and artifact locations, executes real probes,
writes raw results, and exits nonzero for missing artifacts, wrong identity,
failed checks, or unexpected controls. Distinguish not-run from pass. A new
run gets a new directory.

| Slice | Size / prerequisite | Owned change set | Local acceptance after any required build |
| --- | --- | --- | --- |
| FC0: runtime pilot | S/M; provisioned Linux host | isolated Fil-C pins/bootstrap/fetch plumbing; tests/filc/runtime fixtures; initial runtime proof; evidence/status | `python3 tests/filc/verify.py runtime .work/filc/run-001`; valid C/C++ runs, spatial and temporal controls produce the expected Fil-C diagnostic, and compiler/loader/runtime identities are captured. |
| FC1: isolated GN toolchain | M; FC0 | experimental profile/pins; CLI/profile routing; GN generation/verification; narrow-build identity checks; focused controller tests | Focused profile/CLI tests, candidate `poc`, and runtime proof on its own volume; changing compiler/profile identity invalidates reuse. |
| FC2: real Rust boundary | M-sized slices; FC1 | dedicated Fil-Rust/musl patches; adblock-derived fixture; bridge proof through integrator | `python3 tests/filc/verify.py bridge .work/filc/run-001`; both call directions, ownership/lifetime, strings/vectors, callbacks and pointer/bounds controls match explicit expected outcomes. |
| FC3: protected V8 paths | M-sized slices; FC1; parallel with FC2 until browser integration | V8/code-generation/heap patches; d8 fixtures; V8 proof through integrator | `python3 tests/filc/verify.py v8 .work/filc/run-001`; pinned d8 executes the differential corpus, GC/callback cases and generated-code protection controls. |
| FC4: browser integration | M-sized slices; FC2 and FC3 | Chromium patches; allocator/IPC/sandbox adaptations; browser proof | `python3 tests/filc/verify.py browser .work/filc/run-001`; content_shell first, then Hydrogen, with actual process/feature/protection evidence and declared gaps. |
| FC5: native qualification | M-sized slices; FC4 | staging/package isolation; applicable existing acceptance; package proof | `python3 tests/filc/verify.py package .work/filc/run-001`, invoking existing applicable staged/browser/package checks; artifact digest, required-feature parity, sandbox and dependency closure match. |
| FC6: measure and decide | S/M; best executable result, including a blocked earlier slice | result section here; durable evidence summary; independent deferred backlog work | Reproduce accepted proof cases from a clean candidate build and retained inputs; classify the result under Acceptance. Identify arm64/Reactor proof separately. |

Break FC2-FC5 into independently checkable patches. The first generated-code
milestone may be one protected builtin or interpreter handler before the
full d8 corpus. An upstream-compatible fix and reduced regression case are
accepted progress even before a browser exists.

### FC0 and FC1: executable evidence

FC0 owns the minimum isolated toolchain pins, verified downloads and
bootstrap plumbing needed for its standalone pilot. Prefer a suitable
pinned upstream binary when available; otherwise build in the isolated
builder. Bring required downloads into fetch and verify offline reuse.
FC1 integrates those proven inputs into GN; its later profile registration
does not block FC0's compiler acquisition. Do not build a general installer.

Start with a small standalone Fil-C program before a full Chromium compile.
Exercise C/C++, allocations across translation units, function pointers,
threads/TLS, and exceptions/unwinding. Include valid-access controls and
deliberate out-of-bounds/forged-pointer cases. A timeout or generic crash
does not count as the expected safety diagnostic.

Require post-free access and double-free negative controls as well. Fil-C's
documented `free` semantics invalidate the object's bounds immediately,
while physical reclamation can happen later. Both invalid operations must
produce the expected Fil-C panic; deferred collection does not excuse a
successful post-free access. Reconfirm the contract at the pinned revision.

Capture actual compiler invocations, ELF interpreter/NEEDED entries, loaded
libraries, runtime versions and object provenance. Distinguish host tools
from shipped dependencies. Check the native substrate as well as the user
libc before claiming musl-only output. A glibc-only pilot remains useful
evidence while its musl closure is repaired.

FC1 registers the profile in `chromium-build`,
`scripts/linux_product.py`, `scripts/verify-gn-args.py`, and its GN file,
and teaches `scripts/generate-gn.py` the actual experimental compiler
version. Do not make Fil-C claim the ordinary Clang lock's version. Choose
compatible C++/unwind/runtime libraries explicitly.

Register the profile as the Chromium desktop product through image/package
selection, `linux_product.desktop()`, preparation, staging, and test routing.
The CLI also has exact-name desktop routing. Adding only a profile allowlist
entry must not select the headless-only harness; preserve the desktop CDP
and owned-Sway acceptance path.

Extend Gates A/B/E/F with a profile-specific compiler/runtime contract where
needed. Gate E currently requires the native `/lib/ld-musl-` interpreter
spelling and reports Chromium's in-tree libc++ unconditionally. A Fil-C
loader such as `ld-fil1` needs its own verified pinned dependency/runtime
policy, including the musl closure. Preserve the native contracts; the
candidate cannot pass by skipping provenance, dependency or runtime checks.

Reuse `tests/probes/c_runtime.c`, `tests/probes/cpp_runtime.cc`, hermetic
smoke, and existing gate evidence. Focus controller checks on
`tests/test_chromium_build_cli.py`, `tests/test_desktop_product.py`, and
`tests/test_helium_gn.py` where changed. Add a stale-report/wrong-toolchain
negative case; do not weaken the native profile to accept the candidate.

### FC2: Rust and musl

Extract the ownership-bearing part of
`config/patches/adblock-engine-bridge.patch`; an integer-only FFI example
is insufficient. Prove both directions with owned engine handles, borrowed
strings, vectors and the C++ callback. Identify allocator ownership and
destruction for each transferred object.

Try Fil-Rust and reduce its musl std/link failures. Repair target specs,
libc bindings, runtime symbols, or the bridge as required in pinned patches.
Check current Chromium Rust version, language/features, Crubit and generated
bindings against the chosen compiler; Rust 1.90 working on a toy crate does
not satisfy the current graph. Rebuild the relevant target Rust dependency
closure. Host proc-macros remain a separate host-tool case.

A supported explicit bridge is an alternative if its boundary semantics are
demonstrated. Silently linking ordinary Rust objects into a Fil-C process,
or compiling the bridge uninstrumented to bypass errors, cannot satisfy
protected-pilot acceptance.

### FC3: V8 code and heaps

Inventory compiled C++, assembly, CodeStubAssembler/Torque output, builtins,
interpreter handlers, snapshots, runtime JIT/Wasm paths and transitions.
For each, identify the protection mechanism and calling convention.
A native `mksnapshot` is permitted; its emitted instructions need their
own answer. `v8_use_external_startup_data=false` embeds snapshot data
rather than eliminating the snapshot problem.

Investigate the smallest supported route: checked generated code, a
capability-aware backend/bridge, or a reproducible upstream path. New V8 or
Fil-C integration work is in scope. Disabling runtime JIT reduces scope but
does not protect generated interpreter handlers automatically.

Validate flags against the pinned source. At the researched pin, DrumBrake
requires pointer compression; disabling compression while retaining Wasm
through DrumBrake is not an established working combination. Audit V8
sandbox/cage and external-code-space implications of each change.

Track allocation granularity/lifetime for V8 objects and backing stores,
PartitionAlloc, and Oilpan/cppgc. An arena capability can bound a heap while
leaving cross-object accesses inside it unprotected. Record that limitation;
do not claim object-level protection. Disabling PartitionAlloc-as-malloc
does not remove Blink's direct allocator use.

The corpus covers JS results, exceptions, allocation/collection, weak
references, typed arrays/ArrayBuffers, native callbacks and shutdown.
Compare with ordinary d8 from the same revision/configuration. Exercise a
generated-code memory-check control and a transition control; C++-only
controls cannot establish emitted-code coverage. Preserve reduced
reproducers and expected failure diagnostics in the repository.

The generated-code and FFI design must also preserve FUGC's live-root
tracking, capability metadata/store barriers, pollchecks/safepoints, and
runtime enter/exit transitions, including callbacks and unwinding. Bounds
checks and calling conventions alone are insufficient. Test an object
reachable only through generated-code state across forced and concurrent
collection, plus callback and pointer-write/barrier cases. Apply the same
invariants to FC2's bridge. Disabling collection cannot satisfy either gate.

### FC4 and FC5: product and containment

Before browser qualification, freeze the claimed protection scope and
required invariants in the run's evidence. Qualification requires no
unresolved gap within that scope. Documenting an exclusion does not satisfy
an invariant; a reduced or narrowed protection result remains an executable
experiment or feasible-with-constraints result. Do not rewrite the scope
after a failure to turn it into a qualified candidate.

Advance from content_shell to the actual Hydrogen graph, restoring required
capabilities removed during diagnosis. The parity inventory is the current
product profile and applicable acceptance checks: CDP navigation/evaluation/
screenshots, Wayland input/rendering, adblock on/off controls, HarfRust/font
fixtures, required WebGL/graphics and Wasm behavior, network/TLS, restart,
renderer churn, and teardown. Do not add requirements for capabilities the
lean product already omits.

Inventory other generated code or assembly in graphics/media dependencies.
Audit pointer-bearing shared memory/IPC metadata separately from serialized
bytes; do not infer that all cross-process data needs pointer capabilities.

Account for Fil-C GC/runtime threads in zygote/fork, seccomp, signals,
startup and shutdown. Inspect browser, renderer and relevant utility
processes. Preserve intended V8 containment and the OS process sandbox, or
qualify a different reviewed protection design explicitly; a disabled
protection remains a limitation until repaired.

No-sandbox, single-process, or deliberately unprotected dependency runs may
diagnose isolated fixtures but cannot satisfy native qualification.
Do not use authenticated personal accounts for acceptance fixtures.

Keep candidate archives, manifests and install paths distinct. Run existing
acceptance against the packaged binary and bind evidence to its digest.
A recipe change or a source-tree run is not package qualification.

## Acceptance and performance

Functional acceptance is zero unexplained failures in the declared corpus
and all required existing product checks. Maintain the exact baseline
failure list; a newly missing test/feature is a regression, not a green skip.
The runner must rerun checks and reject stale or substituted native artifacts.
Keep explicit trusted-code assumptions in the final inventory, with no
undocumented executable dependency.

Compare on one pinned Chromium revision and quiet host:

1. Current qualified native Hydrogen.
2. Native compiler with the candidate's feature/JIT and optimization
   configuration matched as closely as possible.
3. Fil-C candidate with that same declared configuration.

An early jitless run of the existing browser is useful cost/compatibility
triage. Poor early performance does not cancel feasibility work: establish
correctness, then optimize measured bottlenecks. Describe unmatchable
runtime/allocator differences; do not attribute every change to
instrumentation alone.

Record workload success, startup, task time p50/p95, CPU time, process and
whole-browser memory, idle cost, retained memory after cycles, and density.
Use at least five interleaved measured rounds after warmup; label p95
provisional if the sample count is too small. Reuse bctl's pinned fixtures/
protocol when available, recording their identity without changing bctl.
Report limitations of substitute workloads.

The owner has not set an acceptable slowdown or memory multiplier.
Performance is measured, not assigned an invented pass threshold.
Production promotion is outside this campaign. Recommend continued
integration, a constrained experimental offering, a narrower research
milestone, or parking the effort from actual results.

A blocked result needs the exact command/diagnostic, minimal reproducer,
affected protection/feature requirement, attempted repairs and alternatives,
and a concrete retry condition. Missing hardware, a download failure, a
generic compiler error, or the first V8 failure does not establish technical
infeasibility.

## Evidence and environment

Keep raw artifacts under `.work/filc/<run-id>/` and dependency checkouts
under `.work/`. Preserve builds, caches and failing binaries for resumption.
Commit patches, small deterministic fixtures and a concise durable summary;
do not commit browser archives or massive logs.

Each attempt records:

- Repository, Chromium/V8, Fil-C/runtime, Rust, patch and input digests;
  host architecture, image digest, and available resource limits.
- Exact command, working directory, environment overrides, effective GN args,
  compiler/link invocations, artifact hashes, target/loaded dependencies,
  work/cache volume names.
- Start/end time, exit status/signal, expected result, raw output location,
  acceptance decision, protection limitations and next experiment.
- Relevant Gate A-F reports, canonical/effective GN arguments, graph evidence
  and the copied build-target log before another attempt overwrites it.

Copy/hash the small evidence needed to audit the result into a durable
repository report; reference the raw artifact location and retention policy.
A disappearing worker's path without a durable summary cannot support
closeout. An upstream report is research, not a locally passed gate.

**Needs environment:** no builds ran in the campaign-opening session.
Execution needs a provisioned native Linux host, the established Docker
builder, source-download access, and enough measured RAM/disk for the chosen
target. Use the existing control commands and FC0 proof once implemented.
If unavailable, continue verifiable source/patch work and retain exact
commands for missing proof; mark it not-run.

Native arm64, Reactor integration and toolchain distribution follow useful
native feasibility evidence. Do not claim them from an amd64 Linux run.
Packaging Fil-C into llvm-prebuilt-musl follows a demonstrated consumer and
qualified runtime closure.

## Reference map

Existing local code:

- [Linux guide](linux.md), [product decision](rfc/CHB-21-chromium-replaces-helium.md),
  [product routing](../scripts/linux_product.py), [CLI](../chromium-build).
- [Native profile](../config/profiles/chromium-wayland-release.gn),
  [input lock](../config/inputs.lock), [GN generation](../scripts/generate-gn.py),
  [GN contract](../scripts/verify-gn-args.py).
- [Narrow builder](../scripts/build-target.py),
  [output resolver](../scripts/resolve-output.py),
  [Gate E](../scripts/run-gate-e.py), [Gate F oracle](gate-f-oracle.md).
- [C probe](../tests/probes/c_runtime.c),
  [C++ probe](../tests/probes/cpp_runtime.cc),
  [adblock bridge](../config/patches/adblock-engine-bridge.patch),
  [HarfRust default](../config/patches/desktop-harfrust-shaping-default.patch),
  [package qualification](../scripts/qualify-package.py).

Research snapshot, 2026-11-21; refresh relevant facts before implementation:

- [Chromium's V8 pin](https://github.com/chromium/chromium/blob/156.0.8078.79/DEPS).
  [V8 builtins build](https://github.com/v8/v8/blob/c9b3cfcab30ace06c21f9b48a0f0a34805e81e9d/BUILD.gn),
  [interpreter generator](https://github.com/v8/v8/blob/c9b3cfcab30ace06c21f9b48a0f0a34805e81e9d/src/interpreter/interpreter-generator.cc),
  [DrumBrake configuration](https://github.com/v8/v8/blob/c9b3cfcab30ace06c21f9b48a0f0a34805e81e9d/gni/v8.gni).
- [Fil-Rust state/musl failures](https://github.com/pizlonator/fil-c/blob/6db2b56416c79376b1dcad65247afc700af3574b/build_rustc-notes.md),
  [Fil-C LLVM JIT interface](https://github.com/pizlonator/fil-c/blob/6db2b56416c79376b1dcad65247afc700af3574b/filc/include/filc_llvm_jit.h),
  [Fil-C seccomp guidance](https://fil-c.org/seccomp).
- [Fil-C free and collection semantics](https://fil-c.org/fugc),
  [stdfil free contract](https://fil-c.org/stdfil).
- [Fil-C WebKitGTK recipe](https://github.com/pizlonator/fil-c/blob/6db2b56416c79376b1dcad65247afc700af3574b/pizlix/build_postlc10_chroot_project_webkitgtk.sh)
  uses a portable interpreter with JS JIT, Wasm and Bubblewrap sandbox
  disabled. [Author's WebKit/V8 comparison](https://news.ycombinator.com/item?id=49570669)
  explains the capability and portable-interpreter advantage.
- [Filnix scope exclusions](https://github.com/mbrock/filnix/blob/6b797d35f6550200cfebd7ac7ae3da7baa6e0531/experiment/scope.py)
  defer V8/Chromium-family packages. The search found no verified public
  reproducible Fil-C Chromium port; it did not prove nobody has tried.
- [CHERI Chromium](https://github.com/CTSRD-CHERI/chromium/blob/115.0.5790.100-cheriabi/cheri/cheri-uncompressed.gn)
  and [CHERI V8](https://github.com/CTSRD-CHERI/v8/blob/11.5.150.16_cheri_merge/cheri/cheri-uncompressed-cross.gn)
  are adjacent research with different ABIs and reduced configurations,
  not Fil-C recipes.

## Executor prompt

Execute docs/CAMPAIGN-FIL-C.md on campaign/filc-hydrogen. Start at its current
Status, establish the native Linux runtime pilot, and keep going through
the smallest verifiable fixes to Fil-C, the musl Rust bridge and V8 before
full browser builds. Preserve production behavior and separate experimental
identities. Commit accepted slices, retain exact failure evidence, and
continue independent work when one path is blocked. Finish with a
reproducible candidate or an evidence-backed feasibility verdict; never
substitute a successful link, unprotected generated code, disabled sandbox,
or missing product capability for the claimed result.

