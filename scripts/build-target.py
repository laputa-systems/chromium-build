#!/usr/bin/env python3
"""Build one Ninja output or GN label while preserving the graph for resume."""

from __future__ import annotations

import os
from pathlib import Path

from script_support import ScriptFailure, command_path, fail_main, require_directory, require_file, run, stream_command


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    args = parser.parse_args()
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    out = Path(os.environ.get("GN_OUT", work / "out/headless-debug"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    ninja = os.environ.get("NINJA", "ninja")
    if os.environ.get("NETWORK_MODE", "none") != "none":
        raise ScriptFailure("build-target requires NETWORK_MODE=none")
    require_file(metadata / "gate-d-report.json", "Gate D has not passed")
    require_directory(out, f"missing GN output {out}")
    command_path(ninja)
    target = args.target
    if target.startswith("//") and ":" in target:
        resolved = run(
            ["python3", str(root / "scripts/resolve-output.py"), str(out), target],
            check=False,
            capture_output=True,
        ).stdout.strip()
        if not resolved:
            raise ScriptFailure(f"label did not resolve to one output: {target}")
        target = str(Path(resolved).relative_to(out))
    jobs = os.environ.get("JOBS", "1")
    if not jobs.isdecimal() or int(jobs) <= 0:
        raise ScriptFailure("JOBS must be a positive integer")
    metadata.mkdir(parents=True, exist_ok=True)
    log = metadata / "build-target.log"
    print(f"build-target: target={target} jobs={jobs}")
    if stream_command([ninja, "-C", str(out), "-j", jobs, target], log) != 0:
        raise ScriptFailure(f"Ninja state was preserved in {log}")
    print(f"build-target: completed {target}")


if __name__ == "__main__":
    raise SystemExit(fail_main("build-target", main))
