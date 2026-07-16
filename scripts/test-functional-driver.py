#!/usr/bin/env python3
"""Run the functional browser tests after resolving the built Chrome binary."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from script_support import ScriptFailure, fail_main, require_directory, require_file, run


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    out = Path(os.environ.get("GN_OUT", work / "out/headless-debug"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    profile = os.environ.get("CHROMIUM_PROFILE", "headless-debug")
    output = Path(os.environ.get("TEST_OUTPUT_ROOT", work / f"test-output/{profile}"))
    inputs = Path(os.environ.get("CHROMIUM_INPUTS_ROOT", work / "inputs"))
    timeout = float(os.environ.get("FUNCTIONAL_TEST_TIMEOUT", "180"))
    if os.environ.get("NETWORK_MODE", "none") != "none":
        raise ScriptFailure("functional test requires NETWORK_MODE=none")
    require_file(metadata / f"build-{profile}.json", "Chrome build has not passed")
    require_directory(root / "tests/fixtures", "fixtures are missing")
    run(["python3", "-m", "unittest", "discover", "-s", str(root / "tests"), "-p", "test_*.py"])
    chrome = run(["python3", str(root / "scripts/resolve-output.py"), str(out), "//chrome:chrome"], capture_output=True).stdout.strip()
    if not chrome:
        raise ScriptFailure("cannot resolve Chrome")
    ublock = inputs / "uBlock0_1.72.0.chromium.zip"
    require_file(ublock, f"pinned uBlock archive is missing: {ublock}")
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o777)
    for name in ("test.json", "functional-tcp.png", "functional-pipe.png", "media-runtime.json", "functional-tcp-result.json", "functional-pipe-result.json"):
        (output / name).unlink(missing_ok=True)
    command = [
        "python3",
        str(root / "scripts/test-functional.py"),
        "--chrome",
        chrome,
        "--fixtures",
        str(root / "tests/fixtures"),
        "--ublock-archive",
        str(ublock),
        "--output",
        str(output),
        "--require-llvmpipe",
        "--require-media",
    ]
    if os.geteuid() == 0 and shutil.which("su"):
        command = ["su", "chromium", "-s", "/bin/sh", "-c", "exec " + " ".join(subprocess.list2cmdline([item]) for item in command)]
    run(command, timeout=timeout)


if __name__ == "__main__":
    raise SystemExit(fail_main("test", main))
