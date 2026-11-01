#!/usr/bin/env python3
"""Resolve a GN label to the first executable output."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from script_support import ScriptFailure, command_path, fail_main


def describe(gn: str, out: Path, label: str, source_root: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [gn, "desc", str(out), label, "outputs", f"--root={source_root}"],
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("label")
    args = parser.parse_args()
    gn = os.environ.get("GN", "gn")
    command_path(gn)
    product = os.environ.get("CHROMIUM_PRODUCT", "chromium")
    source_root = os.environ.get("CHROMIUM_SOURCE_ROOT", "/work/src")
    if args.label == "//chrome:chrome" and product == "helium":
        result = describe(gn, args.out, "//chrome:chrome_initial", source_root)
        if result.returncode:
            raise ScriptFailure("GN could not describe //chrome:chrome_initial for Helium")
        candidates = []
        for raw in result.stdout.splitlines():
            candidate = raw.strip()
            if not candidate:
                continue
            for path in (Path(candidate), args.out / candidate):
                if path.is_file() and os.access(path, os.X_OK):
                    candidates.append(path)
        if len(candidates) != 1:
            raise ScriptFailure(f"//chrome:chrome did not resolve to one executable: {candidates}")
        if candidates[0].name != "helium":
            raise ScriptFailure(f"Helium graph produced {candidates[0].name}, expected helium")
        print(candidates[0])
        return
    result = describe(gn, args.out, args.label, source_root)
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
