# Deslop: first-party build scripts and tooling

Component: `chromium-build`, `scripts/`, `tests/`, `Makefile`, `pyproject.toml`
(Python 3.15 standard library plus `laputa-buildlib`; 16.1k lines of scripts and
3.9k of tests). Patches under `config/patches/` and the input locks are reviewed
only for local divergence. Base `fe2308d8` on `master`. Host: M-series Mac, macOS,
Python 3.15.2 through uv, ruff 0.17.4, ty 0.0.101.

## DSLP-1 Ground

Commands, run from the worktree with `TMPDIR=$PWD/.work/tmp`:

| Command | Result | Cost |
| --- | --- | --- |
| `make test` (cold, creates `.venv`) | 149 tests, 1 skipped, exit 0 | 24.1s |
| `make test` (warm) | 149 tests, 1 skipped, exit 0 | 17.0s |
| `make lint` | ruff, ty and `sources_lock.py check` clean, exit 0 | 1.3s |

The skipped test is `test_workflow`'s real-archive qualification, which needs
`CHROMIUM_RELEASE_TEST_WORK_VOLUME` and a Docker runner. `make test`
selects the `macos` group here; the `linux` and `linux-builder` groups need a
Linux host and the builder image (`Needs environment`).

Seeded facts corrected:

- The ticket says no pull request workflow runs `make test`. `ci.yml` already runs
  it in the `lint` job (Linux arm64) and `macos-tests`, and `linux-builder-tests`
  runs `make test-ci` in the image. `tests/test_workflow.py` pins the lint
  workflow. AGENTS.md gained a `## Verification` section.
- Fleet findings: `python-standard` wants uv 0.13.8, ruff 0.17.5, ty 0.0.104 (the
  repository pins 0.13.7, 0.17.4, 0.0.101) and `.python-version`; `repomod fix`
  owns those bumps. `workflow-redundancy` is real: `lint.yml` and the `lint` job
  of `ci.yml` both run `make lint`, and they pin different uv versions (0.13.7 and
  0.13.8). Both are CI workflow changes and out of scope here (filed).
- The base named in the ticket (3922c8d) is stale; the pass starts at `fe2308d8`.
- AGENTS.md says not to run linters yet documents `make lint` as the static check;
  this pass ran `ruff check` and `ty check`, which only read files.

## DSLP-2 Diagnose

Method: read the release path end to end (`chromium-build`, `macos_backend.py`
phases and `main`, `verify_macos_ci.py`, `release_assets.py`, `sources_lock.py`,
`script_support.py`, install and package scripts), then ran ruff `S`, `PLW`,
`RUF`, `B`, shellcheck, vulture and ty at `--error all` for leads. Each defect
below was reproduced against the built code before filing.

| Finding | Evidence | Disposition |
| --- | --- | --- |
| `macos_backend.stream_command` timeouts never fire for a command that prints nothing | `stream_command(["sleep","5"], timeout=1)` returned normally after 5.0s; the 30 minute to 24 hour phase limits were checked only after an output line | Fixed with a timer, regression test (DSLP-4) |
| `verify_macos_ci.py`, the release workflow's gate, had no test (0% covered) | `coverage report` | Tests added (DSLP-4) |
| `setup-chromium-adblock.py`, the Linux Chromium prepare path, never executed by a test; it differs from the Helium script by one constant | `diff` of the two files; 0% covered | Both scripts run the same cases (DSLP-4) |
| `except TypeError: extractall(root)` fell back to unfiltered extraction | `runtime_install.py`, `qualify-package.py`; the builder image runs Python 3.14 | Removed (DSLP-5) |
| `./chromium-build` flags and exit codes untested beyond `image` | `tests/test_image_jobs.py` only | Tests added (DSLP-4) |
| Duplicated helpers: `digest` 8 times, `read_json` 6, near-identical `stream_command` | `rg '^def (digest|read_json)'` | Filed |
| `DEFAULT_PROFILE` and `LOCK_RELATIVE` are module globals that `main` rebinds and `WorkLayout` reads | `macos_backend.py` `main` | Filed |
| `stream_command` kills only the direct child; a grandchild holding the pipe still blocks the reader | design, not reproduced | Filed |
| `lint.yml` and `ci.yml`'s `lint` job both run `make lint` with different uv pins | workflows; fleet `workflow-redundancy`, `python-standard` | Filed (CI out of scope) |

Not defects: shellcheck's four warnings on `chromium-build` (a comma inside a
docker `--tmpfs` word, an unused loop counter, a deliberate empty `CDPATH=`);
ruff `S` (129, subprocess calls with list arguments and local `/tmp` refusals);
`B905` `zip()` sites, where truncation is intended or the lengths are fixed.
The diff for this step is docs and the backlog.

## DSLP-3 Types

Commands: `PYTHONPATH=scripts:tests uv run ty check scripts tests --error all
--output-format concise` (234 diagnostics) and `uv run ruff check scripts tests
--select FAMILY --no-cache --output-format concise`.

| Candidate | Count | Decision |
| --- | ---: | --- |
| ty `missing-type-argument` | 77 | Adopted. Bare `dict`, `Popen`, `CompletedProcess`; all true. |
| ty `unused-ignore-comment` | 0 | Adopted. Turns a `# ty: ignore[code]` into a checker witness. |
| ty `unsound-return-statement` | 23 | Rejected. 6 are typeshed's `re.Match.group` returning `str \| Any`; 17 are `Any` from `json.loads` that need per-document models (filed). |
| ty `unsound-assignment` | 2 | Rejected. Arithmetic on `struct.unpack_from` results. |
| ty `possibly-missing-attribute` | 50 | Rejected. `python-platform = "all"` flags `os.getuid` and friends. |
| ty `truthiness-test-of-none-union` | 52 | Rejected. Intentional `if value` on `str \| None`. |
| ty `redundant-condition-strict` | 14 | Rejected. `finally` narrowing in the test drivers. |
| ty `missing-override-decorator` | 12 | Rejected. |
| ty `possibly-unresolved-reference` | 4 | Rejected. Names bound under `if pipe:` and used under the same test. |
| Ruff `ANN` / `ANN401` | 745 / 5 | Rejected globally; two `ANN401` sites (`atomic_json`, `stable_digest`) tightened to `object`. |
| Ruff `B` | 7 | Rejected. `B905` sites are intended truncation or fixed lengths; the two `B904` are style. |
| Ruff `TC` | 9 | Rejected. |
| Ruff `PGH003`, `RET` | 0, 0 | Nothing to adopt. |

Escape-hatch count (`cast(`, `type: ignore`, `ty: ignore`, `Any`, `noqa`, lines
in `scripts` and `tests`): 113 at base, 106 after excluding
`tests/typing_witnesses.py`, 111 with it. The JSON-document alias
`type JsonObject = dict[str, Any]` in `script_support.py` names the one
untyped boundary once; the dead `text` parameter and untyped `**kwargs` of
`script_support.run` (which made its `CompletedProcess[str]` return a lie) are
gone, and a `type: ignore` that suppressed nothing is removed.

Witnesses: `tests/typing_witnesses.py` holds one rejected program per adopted
rule. With `missing-type-argument` set to `ignore` in `pyproject.toml`, `ty
check` reports three `unused-ignore-comment` errors (exit 1); restoring the file
passed `cmp`. Reintroducing a bare `dict` in `script_support.py` fails with
`missing-type-argument` at that line (restored, `cmp` clean).

## DSLP-4 Tests

Commands, from the worktree with `TMPDIR=$PWD/.work/tmp`: `make coverage`
(branch coverage, `patch = ["subprocess"]`), `uv run --with coverage coverage json`,
and `mutmut` 3.8.1 from the stuff mirror on a scratch copy (`scripts` renamed
`src`, `pytest` runner, `source_paths = ["src/verify_macos_ci.py"]`).

Scorecard (macOS group; image-only scripts cannot run on this host):

| Measure | Base `fe2308d8` | Now |
| --- | ---: | ---: |
| Tests (macOS group) | 149 | 194 |
| Line coverage of `scripts/` | 32.0% (3056 of 9537) | 35.5% (3380 of 9524) |
| Branch coverage | 25.9% (912 of 3526) | 27.8% (977 of 3514) |
| `verify_macos_ci.py` | 0% | 99% |
| Warm `make test`, paired alternating runs | 21.7, 15.9, 13.8s | 22.5, 14.2, 13.7s |

The run-to-run spread on this busy host (14s to 22s) exceeds the difference;
CPU time is unchanged (6.5s vs 6.8s).

Contract, guarding tests, disposition:

| Contract | Guarded by | Disposition |
| --- | --- | --- |
| Release gates: cache key, LLVM floor, preflight, configure, cache-off, package checks | `tests/test_verify_macos_ci.py` | Added; mutation run below |
| `./chromium-build` usage, exit 0/1/2, closed product, platform, arch and profile sets, install-only options | `tests/test_chromium_build_cli.py` | Added |
| `stream_command` timeout and failure status | `tests/test_macos_backend.py` (`test_stream_command_*`) | Added with the fix |
| Linux Chromium adblock vendoring | `tests/test_setup_adblock.py` (`ChromiumVendorTest`) | Added |
| macOS backend `main` argument errors and exit codes | `none` | Open: filed |
| docker phases, `install`, gates A to F | `none` on the host | `Needs environment`: Docker and the builder image |
| `linux` and `linux-builder` test groups, ELF audits | not run on Darwin | `Needs environment`: Linux host and image |

Injected faults, each restored and verified with `cmp`:

| Fault | Failing test | Exit |
| --- | --- | --- |
| `>=` to `>` in the free-space floor of `verify_macos_ci.py` | `test_a_complete_host_only_report_passes`, `test_the_free_space_floor_is_the_argument`, 2 `subTest`s of `test_each_policy_violation_is_rejected` | 1 |
| macOS branch of `chromium-build` accepts `--product helium` | `test_product_platform_and_architecture_are_closed_sets` | 1 |
| Crate checksum comparison removed from `setup-chromium-adblock.py` | `ChromiumVendorTest.test_a_crate_that_does_not_match_the_lock_is_refused` | 1 |
| `stream_command` watchdog disabled | `test_stream_command_timeout_stops_a_command_that_prints_nothing` (30.2s, the full sleep) | 1 |

The same `stream_command` test failed before the fix ("MacOSFailure not raised"
after 30.3s) and passes in 0.8s.

Bounded mutation run, `verify_macos_ci.py` only (the critical scope: release
gating), 895 mutants, about 20s of mutmut time:

| Run | Killed | Timeout | Survived |
| --- | ---: | ---: | ---: |
| First tests (17 tests, substring assertions) | 666 | 2 | 227 |
| Exact failure messages (17 tests) | 701 | 2 | 192 |
| Final (25 tests) | 853 | 2 | 40 |

The survivors were grouped by behavior and killed with tests for the report dump
in failures, inclusive version and free-space floors, missing report sections,
argparse required options and unreadable-report labels. All 40 remaining
survivors are dispositioned:

| Group | Count | Disposition |
| --- | ---: | --- |
| `encoding="utf-8"` to `None` or `"UTF-8"` | 6 | Equivalent: Python 3.15 defaults to UTF-8 |
| Upper-cased path (`METADATA`, `MACOS-RELEASE.ARGS.GN`) | 2 | Unobservable on a case-insensitive APFS volume |
| Absent-key default `""` to `None` or `"XXXX"` | 10 | Equivalent: every variant yields the same rejection |
| `subprocess.run` `check`, `text` | 5 | Equivalent: `errors="replace"` already selects text mode and the code reads `returncode` |
| `sha256` chunk size or `read(None)` | 3 | Equivalent: only changes memory use (the `iter` sentinel mutants hang and count as timeouts) |
| argparse `type=` removed or `None` | 14 | Equivalent: every consumer wraps the value in `Path` or `int` |

Advisory: `sources_lock.py` under the same tests and its existing 5 tests:
223 killed, 95 survived, 126 without covering tests (the `--fetch` network path
and `resolve_sha256`); not dispositioned. No mutation lane or `make mutants-diff`
was added: the repository uses `unittest`, mutmut needs pytest, and a lane
needs a CI workflow, which is out of scope here.

## DSLP-5 Dead code

Commands: `uv run --with vulture vulture scripts tests --min-confidence 60`
(vulture 2.16), `uv run --with deptry deptry . --extend-exclude '\.work|tests'`
(deptry 0.25.1), and ruff `F401`/`F841` through `make lint`. `cargo-machete`,
`udeps` and bloat tools do not apply: there is no Rust, and the repository's
size is Chromium, not these scripts.

Removed, each with a workspace-wide `rg` over `scripts`, `tests`, `docs`,
`Dockerfile`, `Makefile`, `chromium-build`, `.github` and the config JSON, no
reference outside the definition:

| Item | Entry points, registrations, consumers checked | Result |
| --- | --- | --- |
| `linux_product.require_report` | no CLI command, test or doc names it; `resolve_main` does not call it | removed |
| `macos_backend.macho_has_pie`, `MH_BUNDLE` | `audit_bundle` reads `MH_PIE_FLAG` directly; tests use only `MH_PIE_FLAG` | removed |
| `script_support.required_path` | no importer in any script | removed |
| `script_support.stream_command(timeout=)` | `build.py` and `build-target.py` pass no timeout; its deadline check had the silent-hang defect | parameter removed |
| `helium_wayland.HeadlessSway._owner` | written once, never read | removed |
| `linux_product.Product.devtools_revision` | constructed from the lock, never read; `verify-source.py` reads the lock itself | removed |
| `except TypeError` unfiltered `extractall` in `runtime_install.py` and `qualify-package.py` | `filter="data"` exists on 3.14 (builder image) and 3.15 (hosts) | removed |

Platforms and consumers: all are private script helpers; `make test` (194 tests,
macOS group) and `make lint` pass after each removal. The Linux groups (image
tests that import `linux_product`, `runtime_install`, `qualify-package`) are
`Needs environment`; the removed names have no references in them.

Remaining vulture output is framework hooks (`do_GET`, `log_message`,
`allow_reuse_address` on `http.server` subclasses), tests' `except ... as e`
bindings, and the `unittest` class `ToolchainChecks`: not dead. deptry reports
only DEP001 for sibling modules in the flat `scripts/` directory, `PIL`
(optional, image-only) and `gclient_eval` (depot_tools); no unused or missing
registry dependency (`laputa-buildlib` is the only one and is used). Allowlist:
none is checked in, because neither tool runs in `make lint`; no lane exists
for them (`Needs environment`: a CI lane).

## Bench decision

No benchmarks exist and none is warranted. The tooling's own cost is `make test`
(14s to 17s) and `make lint` (1.3s); the user-visible performance is Chromium's
build time, which only a full build measures and no check here can repeat. No
workload, entrypoint or Edit/Change run was added; `bench-pyramid` outcome:
no benchmarks, nothing to prove.

## Findings filed

See `TODO-DESLOP.md`. Fixed in this pass: silent-hang timeout (P2), untested
release policy gate (P2), untested Linux Chromium adblock script (P2), untested
CLI validation (P3), unfiltered extraction fallback (P3).
