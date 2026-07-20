#!/usr/bin/env python3
"""Resolve a GN label to the first executable output."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

from script_support import ScriptFailure, command_path, fail_main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("label")
    args = parser.parse_args()
    gn = os.environ.get("GN", "gn")
    command_path(gn)
    source_root = os.environ.get("CHROMIUM_SOURCE_ROOT", "/work/src")
    result = subprocess.run(
        [gn, "desc", str(args.out), args.label, "outputs", f"--root={source_root}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        if args.label == "//chrome:chrome":
            chrome = args.out / "chrome"
            if chrome.is_file() and os.access(chrome, os.X_OK):
                print(chrome)
                return
        raise ScriptFailure(f"GN could not describe {args.label}")
    for raw in result.stdout.splitlines():
        candidate = raw.strip()
        if not candidate:
            continue
        for path in (Path(candidate), args.out / candidate):
            if path.is_file() and os.access(path, os.X_OK):
                print(path)
                return
    raise ScriptFailure(f"label did not resolve to an executable output: {args.label}")


if __name__ == "__main__":
    raise SystemExit(fail_main("resolve-output", main))
