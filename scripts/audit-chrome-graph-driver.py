#!/usr/bin/env python3
"""Run Gate F's Ninja audit and independent command oracle."""

from __future__ import annotations

import os
from pathlib import Path

from script_support import ScriptFailure, command_path, fail_main, require_directory, require_file, run


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    out = Path(os.environ.get("GN_OUT", work / "out/headless-debug"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    ninja = os.environ.get("NINJA", "ninja")
    timeout = float(os.environ.get("GATE_F_TIMEOUT", "600"))
    if os.environ.get("NETWORK_MODE", "none") != "none":
        raise ScriptFailure("Chrome graph audit requires NETWORK_MODE=none")
    require_file(metadata / "gate-d-report.json", "Gate D has not passed")
    require_directory(out, f"missing GN output {out}")
    command_path(ninja)
    commands = metadata / "chrome-commands.txt"
    dry_run = metadata / "chrome-dry-run.txt"
    commands_log = metadata / "chrome-commands.log"
    dry_log = metadata / "chrome-dry-run.log"
    for path, command in (
        (commands, [ninja, "-C", str(out), "-t", "commands", "chrome"]),
        (dry_run, [ninja, "-C", str(out), "-n", "chrome"]),
    ):
        result = run(command, check=False, capture_output=True, timeout=timeout)
        log = commands_log if path is commands else dry_log
        log.write_text(result.stderr or "", encoding="utf-8")
        if result.returncode:
            raise ScriptFailure(f"Ninja failed while generating {path.name}")
        path.write_text(result.stdout or "", encoding="utf-8")
    dry_mode = "no-work" if "ninja: no work to do" in dry_run.read_text(encoding="utf-8") else "passed"
    run(
        [
            "python3",
            str(root / "scripts/audit-chrome-graph.py"),
            "--commands",
            str(commands),
            "--dry-run",
            str(dry_run),
            "--dry-run-mode",
            dry_mode,
            "--out",
            str(out),
            "--output",
            str(metadata / "gate-f-report.json"),
        ]
    )
    oracle_commands = metadata / "chrome-oracle-commands.txt"
    result = run(
        [ninja, "-C", str(out), "-t", "commands", "chrome"],
        check=False,
        capture_output=True,
        timeout=timeout,
    )
    (metadata / "chrome-oracle-commands.log").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode:
        raise ScriptFailure("could not enumerate Chrome commands for the independent oracle")
    oracle_commands.write_text(result.stdout or "", encoding="utf-8")
    if commands.read_bytes() != oracle_commands.read_bytes():
        raise ScriptFailure("Ninja command enumeration was not stable")
    run(
        [
            "python3",
            str(root / "scripts/audit-chrome-oracle.py"),
            "--commands",
            str(oracle_commands),
            "--gate-report",
            str(metadata / "gate-f-report.json"),
            "--ninja-version",
            run([ninja, "--version"], capture_output=True).stdout.strip(),
            "--output",
            str(metadata / "gate-f-oracle-report.json"),
        ]
    )
    print("Gate F: Chrome graph dry-run and toolchain audit passed")


if __name__ == "__main__":
    raise SystemExit(fail_main("Gate F", main))
