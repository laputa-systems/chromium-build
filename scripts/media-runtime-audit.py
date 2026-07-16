#!/usr/bin/env python3
"""Audit loaded FFmpeg exception libraries and their exported symbols."""

import argparse
import json
from pathlib import Path
import subprocess


def process_tree(root_pid):
    parents = {}
    proc = Path("/proc")
    if not proc.is_dir():
        raise RuntimeError("media runtime audit requires /proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8")
        except OSError:
            continue
        parent = next((line for line in status.splitlines() if line.startswith("PPid:")), None)
        if parent:
            parents[int(entry.name)] = int(parent.split()[1])
    result = [root_pid]
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        children = [pid for pid, ppid in parents.items() if ppid == parent]
        result.extend(children)
        pending.extend(children)
    return sorted(set(result))


def loaded_libraries(pids):
    paths = set()
    pid_maps = {}
    for pid in pids:
        maps_path = Path(f"/proc/{pid}/maps")
        try:
            lines = maps_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        mapped = set()
        for line in lines:
            fields = line.split(maxsplit=5)
            if len(fields) == 6 and fields[5].startswith("/"):
                path = fields[5].split(" ", 1)[0]
                mapped.add(path)
                paths.add(path)
        pid_maps[str(pid)] = sorted(mapped)
    return sorted(paths), pid_maps


def exported_symbols(nm, path):
    try:
        result = subprocess.run(
            [nm, "-D", "--defined-only", "--format=posix", path],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"could not read dynamic symbols from {path}: {error}") from error
    symbols = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields:
            symbols.add(fields[0])
    return symbols


def by_basename(values):
    normalized = {}
    for path, symbols in values.items():
        name = Path(path).name
        if name in normalized and normalized[name] != symbols:
            raise RuntimeError(f"multiple libraries share a basename with different symbols: {name}")
        normalized[name] = symbols
    return normalized


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-pid", required=True, type=int)
    parser.add_argument("--chrome", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--nm", default="llvm-nm")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()

    pids = process_tree(args.root_pid)
    paths, pid_maps = loaded_libraries(pids)
    ffmpeg = [path for path in paths if Path(path).name.startswith("libav")]
    if not ffmpeg:
        raise RuntimeError("no libav library was loaded by the browser process tree")
    bundled = [path for path in paths if Path(path).name == "libffmpeg.so"]
    chrome_symbols = exported_symbols(args.nm, str(args.chrome))
    intersections = {}
    high_risk = {}
    for path in ffmpeg:
        shared = sorted(chrome_symbols & exported_symbols(args.nm, path))
        intersections[path] = shared
        high_risk[path] = sorted(
            symbol
            for symbol in shared
            if symbol.startswith(("__cxa_", "_Unwind_", "malloc", "calloc", "realloc", "free", "operator"))
            or "SSL" in symbol
            or "OPENSSL" in symbol
            or "BORINGSSL" in symbol
        )
    report = {
        "schema": 1,
        "status": "complete",
        "root_pid": args.root_pid,
        "pids": pids,
        "pid_maps": pid_maps,
        "ffmpeg_libraries": ffmpeg,
        "bundled_ffmpeg_libraries": bundled,
        "chrome": str(args.chrome),
        "symbol_intersections": intersections,
        "high_risk_intersections": high_risk,
    }
    failure = None
    if bundled:
        failure = "bundled libffmpeg.so was loaded"
    if args.baseline:
        try:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            actual_symbols = by_basename(intersections)
            expected_symbols = by_basename(baseline.get("symbol_intersections", {}))
            actual_high_risk = by_basename(high_risk)
            expected_high_risk = by_basename(baseline.get("high_risk_intersections", {}))
            differences = {
                "symbol_intersections": {
                    "added": sorted(set(actual_symbols) - set(expected_symbols)),
                    "removed": sorted(set(expected_symbols) - set(actual_symbols)),
                    "changed": sorted(name for name in set(actual_symbols) & set(expected_symbols) if actual_symbols[name] != expected_symbols[name]),
                },
                "high_risk_intersections": {
                    "added": sorted(set(actual_high_risk) - set(expected_high_risk)),
                    "removed": sorted(set(expected_high_risk) - set(actual_high_risk)),
                    "changed": sorted(name for name in set(actual_high_risk) & set(expected_high_risk) if actual_high_risk[name] != expected_high_risk[name]),
                },
            }
            report["baseline"] = {"path": str(args.baseline), "status": "complete", "differences": differences}
            if any(values for section in differences.values() for values in section.values()):
                failure = f"runtime symbol report differs from baseline: {differences}"
                report["baseline"]["status"] = "failed"
        except (OSError, json.JSONDecodeError, RuntimeError) as error:
            failure = f"could not compare runtime audit baseline: {error}"
            report["baseline"] = {"path": str(args.baseline), "status": "failed", "error": str(error)}
    if failure:
        report["status"] = "failed"
        report["error"] = failure
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if failure:
        raise SystemExit(failure)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"media runtime audit: {error}") from error
