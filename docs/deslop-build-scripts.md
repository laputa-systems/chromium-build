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

The skipped test is the sandbox probe that needs a loopback bind. `make test`
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
