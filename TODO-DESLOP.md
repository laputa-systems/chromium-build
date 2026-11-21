Prefix: DSLP
Next: 18

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md), tier B). One agent works this file with the `deslop` skill: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

**Scope and base.** First-party build scripts and tooling (`chromium-build`, `scripts/`, `tests/`, `Makefile`, `pyproject.toml`); patches and locks only for divergence. Base `fe2308d8` (the ticket's 3922c8d was stale). Branch `deslop/pass`; no PRs were opened, the parent integrates. Full record: `docs/deslop-build-scripts.md`.

**Tickets.** Closed DSLP-1 to DSLP-5. Filed DSLP-6 to DSLP-17 (P3: 8, P4: 4); none is P1 or P2. Open `Needs owner ·`: DSLP-17 (the AGENTS.md no-linters rule versus `make lint`). Bench ran as a decision, not a ticket: no benchmarks exist and none is warranted.

**Scorecard** (macOS group, this host):

| Measure | Before | After |
| --- | ---: | ---: |
| Python script lines / test lines | 15.5k / 3.9k | 15.5k / 4.5k |
| Tests (`make test`) | 149, 1 skipped | 194, 1 skipped |
| Warm `make test` (paired, alternating runs) | 13.8 to 21.7s | 13.7 to 22.5s |
| `make lint` | 1.3s | 1.3s |
| Line / branch coverage of `scripts/` | 32.0% / 25.9% | 35.5% / 27.8% |
| Mutants in `verify_macos_ci.py` (killed / timeout / survived) | 666 / 2 / 227 | 853 / 2 / 40 (all 40 equivalent or unobservable) |
| Escape-hatch lines (`cast`, `ignore`, `Any`, `noqa`) | 113 | 106 (111 with the witness file) |
| Dead code removed | none | 7 removals (3 functions, 1 constant, 1 field, 1 attribute, 1 parameter) plus 2 unfiltered-extract fallbacks |
| Findings fixed, by priority | | P2: 3 (silent-hang timeout, untested release gate, untested Chromium adblock path); P3: 2 (CLI validation, extraction fallback) |
| Perf verdict | | no workload; unaffected |
| Escaped regressions | | none found |

**Per step.** Ground (about 10 min): `make test` 24s cold and 17s warm, `make lint` 1.3s; corrected the stale "CI does not run make test" claim and added AGENTS.md `## Verification`. Diagnose: read the release path and ran ruff `S`/`PLW`/`RUF`/`B`, shellcheck, vulture and ty at `--error all`; reproduced the `stream_command` timeout defect (`sleep 5` with timeout 1 returned after 5.0s). Types: adopted ty `missing-type-argument` (77, true) and `unused-ignore-comment`, rejected the rest with counts, added `tests/typing_witnesses.py`. Tests: `make coverage`, 4 new test modules or classes (45 tests), 4 injected faults with `cmp` restoration, a bounded mutmut run (about 20s of mutmut time). Dead code: vulture and deptry; 7 removals. Not run: coverage or mutation of the `linux` and `linux-builder` groups and the image-only scripts (`Needs environment`: Linux host, Docker, builder image).

**Method feedback.** Substring assertions on failure messages let `XX`-wrapping mutants survive: assert the full text. mutmut 3 needs pytest and a module named by its path, so an unittest repository with a flat `scripts/` needs a scratch copy with the directory renamed `src`; `do_not_mutate` globs did not apply there, `source_paths` pointing at files did. Case-flipping path mutants are equivalent on APFS and should be excluded by the tool's config. ty `unsound-return-statement` mixes real `Any` leaks with typeshed `re.Match.group` noise; the one-false-positive-per-5k-lines bar rejects it until the JSON documents are modeled (DSLP-9). The ticket's "CI does not run make test" seed was wrong: check the workflows before filing.

**Shared-file gaps.** None blocking. The fleet `python-standard` approved pins (uv 0.13.8, ruff 0.17.5, ty 0.0.104) are ahead of this repository; `repomod fix` owns the bump (DSLP-13). The skill's mutation recipe should say how to run mutmut on a flat unittest `scripts/` directory (scratch `src/` copy, file-level `source_paths`).

## Tickets

### DSLP-6 · P3 · Share the helpers the build scripts copy

- **Area:** deslop · dedupe
- **Where:** scripts/*.py (digest in 8 files, read_json in 6, sha256 in 3, stream_command/run/atomic_json in script_support.py and macos_backend.py)
- **Problem:** The same file digest, JSON read and process-streaming helpers are written out in each entrypoint. The two stream_command copies already diverged and both had the silent-hang timeout defect (one fixed in this pass, one removed as dead). script_support.py ships in the builder image beside every entrypoint, so image scripts can share it; only the macOS and host-only scripts may import buildlib.
- **Recommendation:** Move digest, read_json and atomic JSON writes into script_support.py and make macos_backend use its own typed variants only where the error type differs. Keep the image scripts stdlib-only. Accept: one definition of each helper; make test and make test-ci pass.
- **Fix with:** deduplicate
- **Effort:** M

### DSLP-7 · P3 · Stop macos_backend.main rebinding module globals

- **Area:** deslop · types
- **Where:** scripts/macos_backend.py (main, DEFAULT_PROFILE, LOCK_RELATIVE, WorkLayout.__init__)
- **Problem:** main declares global DEFAULT_PROFILE, LOCK_RELATIVE and rebinds them from --profile before any WorkLayout is built; WorkLayout.output and the lock loader read the globals. Importing the module and calling a phase function behaves differently from running main, and the tests only pass because the default profile is the one they use.
- **Recommendation:** Carry the profile and lock path on WorkLayout (or a small frozen profile value) and drop the globals. Accept: no global statement in macos_backend.py; test_macos_backend covers both macos-release and macos-helium-release layouts.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

### DSLP-8 · P3 · Kill the whole process group when stream_command times out

- **Area:** deslop · lifecycle
- **Where:** scripts/macos_backend.py (stream_command)
- **Problem:** The timeout now fires on a silent command, but it terminates only the direct child. A phase such as pnpm run test:browser spawns descendants that keep the stdout pipe open, so the reader loop can still block until they exit. Not reproduced with a real tree; the design leaves it open. Moving the child to a new session would stop Ctrl-C reaching it, so the fix needs an explicit interrupt path.
- **Recommendation:** Start the child in its own process group, kill the group on timeout and on KeyboardInterrupt, and test with a shell that backgrounds a sleeper. Accept: the test fails before and passes after, in under 15s.
- **Fix with:** lifecycle-concurrency
- **Effort:** S

### DSLP-9 · P3 · Replace dict[str, Any] build documents with typed models

- **Area:** deslop · types
- **Where:** scripts/macos_backend.py (about 90 dict[str, Any] sites), scripts/verify_macos_ci.py, scripts/macos_helium.py, JsonObject in scripts/script_support.py
- **Problem:** Phase reports, locks and manifests are untyped JSON dicts, so ty at unsound-return-statement reports 17 sites where Any from json.loads is returned as str, list[str] or tuple[int, int, int]. The rule stays off for that reason (6 more hits are typeshed re.Match.group noise).
- **Recommendation:** Model the lock, doctor report, package report and release manifest as TypedDict or dataclasses validated at the read boundary, then adopt unsound-return-statement. Accept: the 17 sites fixed with a witness each; rule at error.
- **Fix with:** typing-audit
- **Effort:** L

### DSLP-10 · P3 · Test the macOS backend entry point and its exit codes

- **Area:** deslop · tests
- **Where:** scripts/macos_backend.py (parse_arguments, main), tests/test_macos_backend.py
- **Problem:** main maps MacOSFailure to exit 1, KeyboardInterrupt to 130 and unknown commands to a failure, rejects --headed-reference outside test-staged and any profile but the two macOS ones. Only ./chromium-build forwarding of one flag is tested now; no test calls main.
- **Recommendation:** Add main-level tests with a temporary work root: each rejected argument, exit codes 0, 1 and 130, and the work-lock release. Prove one injected fault. Accept: the Verification section lists the test for the macOS backend entry.
- **Fix with:** test-audit
- **Effort:** S

### DSLP-11 · P3 · Measure and test the Linux image scripts on a Linux host

- **Area:** deslop · tests
- **Where:** scripts/ gates A to F, prepare-source*.py, prepare-helium.py, stage-runtime.py, package-runtime.py, test-functional*.py, verify-*.py; tests/run_controller_tests.py linux and linux-builder groups
- **Problem:** About 20 scripts run only inside the builder image and sit at 0% to 25% in the macOS group coverage; the linux and linux-builder groups cannot run on Darwin, so this pass measured nothing for them and could not mutate them. Needs environment: Linux host, Docker and the builder image.
- **Recommendation:** On a Linux builder run make coverage for the linux group and make test-ci with coverage inside the image; add tests for the 0% gate scripts (verify-environment, run-probes, generate-gn) that need no browser. Accept: per-script coverage recorded; each public gate has a guarding test or a stated reason.
- **Fix with:** test-audit
- **Effort:** L

### DSLP-12 · P3 · Add a mutation lane for the release-gating scripts

- **Area:** deslop · ci
- **Where:** .github/workflows/, Makefile, tests/
- **Problem:** A bounded mutmut run on verify_macos_ci.py is reproducible only by hand (scratch copy with scripts renamed src and a pytest runner) because mutmut 3 needs pytest and a tracked source package; the repository uses unittest. CI workflow changes are out of scope for this pass. No lane or diff-scoped target exists.
- **Recommendation:** Add pytest as a dev dependency from the stuff mirror or a mutmut runner for unittest, qualify the recipe with negative controls in CI, then wire it for verify_macos_ci.py, sources_lock.py and release_assets.py. Accept: lane green with a negative control failing for the intended reason.
- **Fix with:** test-audit
- **Effort:** M

### DSLP-13 · P3 · Drop the duplicate lint workflow and align the uv pin

- **Area:** deslop · ci
- **Where:** .github/workflows/lint.yml, .github/workflows/ci.yml, pyproject.toml, uv.lock
- **Problem:** lint.yml and the lint job of ci.yml both run make lint on ubuntu-26.04-arm (fleet workflow-redundancy), with uv 0.13.7 and 0.13.8. The fleet python-standard rule wants uv 0.13.8, ruff 0.17.5 and ty 0.0.104; the repository pins 0.13.7, 0.17.4 and 0.0.101 and has no .python-version. test_workflow.test_lint_workflow_runs_make_lint pins lint.yml. The fleet owned-tool-support finding was not examined.
- **Recommendation:** Run repomod fix for the python-standard pins, delete lint.yml (update the test to read ci.yml), keep one uv version. CI changes are outside this pass. Accept: repomod lint shows no workflow-redundancy or python-standard finding for this repository.
- **Fix with:** none
- **Effort:** S

### DSLP-14 · P4 · Share the Helium and Chromium adblock setup scripts or retire one

- **Area:** deslop · dedupe
- **Where:** scripts/setup-chromium-adblock.py, scripts/setup-helium-adblock.py, tests/test_setup_adblock.py
- **Problem:** Two 237-line scripts differ by the FILTERS directory constant. The shared tests now cover both, so a fix to one is caught only if made in both. CHB-21 retires the Helium product.
- **Recommendation:** Delete setup-helium-adblock.py with the Helium product, or turn the pair into one module parametrised by the filters directory. Accept: one script, both callers (prepare-source.py, prepare-helium.py, macos_helium.py) pass tests.
- **Fix with:** deduplicate
- **Effort:** S

### DSLP-15 · P4 · Lint the shell entrypoints

- **Area:** deslop · tooling
- **Where:** Makefile (lint), chromium-build, scripts/*.sh, scripts/rust-host-linker
- **Problem:** make lint runs ruff and ty on Python only. shellcheck over chromium-build and the shell scripts reports 5 warnings (SC1007, two SC2054 on docker --tmpfs words, SC2034 on an unused loop counter, SC2046 in build-rust-toolchain.sh); all benign today, but nothing stops a real one.
- **Recommendation:** Add shellcheck to make lint with the five warnings fixed or annotated, if shellcheck is available in CI. Accept: make lint fails on an injected SC2086.
- **Fix with:** none
- **Effort:** S

### DSLP-16 · P4 · Read the Rust components list from the backend in the policy check

- **Area:** deslop · single-source
- **Where:** scripts/verify_macos_ci.py (verify_preflight), scripts/macos_backend.py
- **Problem:** The nightly now comes from macos_backend.RUST_TOOLCHAIN, but the component list [rust-src, llvm-tools-preview] is still spelled in both files and in the input locks.
- **Recommendation:** Export one constant from macos_backend and use it in the policy check and test fixture. Accept: one definition outside the locks.
- **Fix with:** single-source-of-truth
- **Effort:** S

### DSLP-17 · P4 · Needs owner · reconcile the no-linters rule with make lint

- **Area:** Needs owner · AGENTS.md rules
- **Where:** AGENTS.md Working rules and Commands
- **Problem:** The working rules say not to run formatters, linters, hooks or CI, while Commands documents make check-py and make lint as the Python static checks and CI runs them. ruff check and ty check only read files; only formatters rewrite them.
- **Recommendation:** Decide whether the rule means formatters and hooks only, and reword it; this pass ran ruff check and ty check and no formatter.
- **Fix with:** none
- **Effort:** S

