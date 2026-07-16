#!/usr/bin/env python3
"""Build Chrome with the prepared Ninja graph and preserve resume state."""

from __future__ import annotations

import os
from pathlib import Path
import time

from script_support import (
    ScriptFailure,
    atomic_json,
    command_path,
    fail_main,
    require_directory,
    require_file,
    run,
    stream_command,
)


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    out = Path(os.environ.get("GN_OUT", work / "out/headless-debug"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    profile = os.environ.get("CHROMIUM_PROFILE", "headless-debug")
    ninja = os.environ.get("NINJA", "ninja")
    require_network = os.environ.get("NETWORK_MODE", "none")
    if require_network != "none":
        raise ScriptFailure("build requires NETWORK_MODE=none")
    require_file(metadata / "gate-d-report.json", "Gate D has not passed")
    require_file(metadata / "gate-f-report.json", "Gate F has not passed")
    require_directory(out, f"missing GN output {out}")
    command_path(ninja)

    jobs_text = os.environ.get("JOBS", "")
    if not jobs_text:
        jobs = min(os.cpu_count() or 1, 8)
    elif jobs_text.isdecimal():
        jobs = int(jobs_text)
    else:
        raise ScriptFailure("JOBS must be a positive integer")
    if jobs <= 0:
        raise ScriptFailure("JOBS must be positive")

    metadata.mkdir(parents=True, exist_ok=True)
    log = metadata / f"build-{profile}.log"
    before = metadata / "build-ccache-before.txt"
    after = metadata / "build-ccache-after.txt"
    report = metadata / f"build-{profile}.json"
    ccache = os.environ.get("CCACHE", "ccache")
    before.write_text(run([ccache, "--show-stats"], check=False, capture_output=True).stdout or "", encoding="utf-8")
    command = [ninja, "-C", str(out), "-j", str(jobs)]
    load_limit = os.environ.get("NINJA_LOAD_LIMIT", "")
    if load_limit:
        command.extend(["-l", load_limit])
    command.append("chrome")
    start = int(time.time())
    print(f"build: profile={profile} target=chrome jobs={jobs} load={load_limit or 'unlimited'}")
    print(f"build: source={os.environ.get('CHROMIUM_SOURCE_ROOT', work / 'src')} out={out}")
    run(["df", "-Pk", str(work)], check=False)
    if stream_command(command, log) != 0:
        raise ScriptFailure(f"Ninja failed; output preserved in {log}")
    end = int(time.time())
    after.write_text(run([ccache, "--show-stats"], check=False, capture_output=True).stdout or "", encoding="utf-8")
    run(["df", "-Pk", str(work)], check=False)
    resolver = root / "scripts/resolve-output.py"
    resolved = run(["python3", str(resolver), str(out), "//chrome:chrome"], capture_output=True).stdout.strip()
    chrome = Path(resolved)
    if not chrome.is_file() or not os.access(chrome, os.X_OK):
        raise ScriptFailure(f"resolved Chrome is not executable: {chrome}")
    atomic_json(
        report,
        {
            "schema": 1,
            "build_driver_schema": 1,
            "status": "complete",
            "network": "none",
            "profile": profile,
            "target": "chrome",
            "output": str(out),
            "chrome": str(chrome),
            "jobs": jobs,
            "load_limit": load_limit or None,
            "started": start,
            "finished": end,
            "duration_seconds": end - start,
            "ccache_before": str(before),
            "ccache_after": str(after),
        },
    )
    print(f"build: Chrome built successfully in {end - start}s: {chrome}")


if __name__ == "__main__":
    raise SystemExit(fail_main("build", main))
