#!/usr/bin/env python3
"""Build and run the hermetic smoke target."""

from __future__ import annotations

import os
from pathlib import Path

from script_support import ScriptFailure, atomic_json, command_path, fail_main, require_directory, require_file, run


def resolve_output(gn: str, out: Path, source: Path, label: str) -> Path:
    result = run([gn, "desc", str(out), label, "outputs", f"--root={source}"], capture_output=True)
    for raw in result.stdout.splitlines():
        candidate = raw.strip()
        if not candidate:
            continue
        for path in (Path(candidate), out / candidate):
            if path.is_file() and os.access(path, os.X_OK):
                return path
    raise ScriptFailure("GN did not report a runnable hermetic smoke binary")


def main() -> None:
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    source = Path(os.environ.get("CHROMIUM_SOURCE_ROOT", work / "src"))
    out = Path(os.environ.get("GN_OUT", work / "out/headless-debug"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    ninja = os.environ.get("NINJA", "ninja")
    gn = os.environ.get("GN", "gn")
    readelf = os.environ.get("READELF", "/opt/llvm-musl/bin/llvm-readelf")
    timeout = float(os.environ.get("GATE_E_TIMEOUT", "300"))
    if os.environ.get("NETWORK_MODE", "none") != "none":
        raise ScriptFailure("smoke build requires NETWORK_MODE=none")
    require_file(metadata / "gate-d-report.json", "Gate D has not passed")
    require_directory(out, f"missing GN output {out}")
    command_path(ninja)
    command_path(gn)
    require_file(Path(readelf), "LLVM readelf is unavailable")
    metadata.mkdir(parents=True, exist_ok=True)
    build_log = metadata / "gate-e-build.log"
    commands = metadata / "gate-e-commands.txt"
    run_log = metadata / "gate-e-run.log"
    desc = metadata / "gate-e-desc.txt"
    target_label = "//tools/hermetic_smoke:hermetic_smoke"
    ninja_target = "tools/hermetic_smoke:hermetic_smoke"
    built = run([ninja, "-C", str(out), ninja_target], check=False, capture_output=True, timeout=timeout)
    build_log.write_text((built.stdout or "") + (built.stderr or ""), encoding="utf-8")
    if built.returncode:
        raise ScriptFailure("hermetic smoke target failed to build")
    command_result = run([ninja, "-C", str(out), "-t", "commands", ninja_target], capture_output=True)
    commands.write_text(command_result.stdout, encoding="utf-8")
    for required in ("-fexceptions", "-pthread", "/opt/llvm-musl/lib/libc++.a", "/opt/llvm-musl/lib/libc++abi.a", "/opt/llvm-musl/lib/libunwind.a"):
        if required not in command_result.stdout:
            raise ScriptFailure(f"smoke commands omit {required}")
    description = run([gn, "desc", str(out), target_label, "outputs", f"--root={source}"], capture_output=True)
    desc.write_text(description.stdout, encoding="utf-8")
    binary = resolve_output(gn, out, source, target_label)
    headers = run([readelf, "-l", str(binary)], capture_output=True).stdout
    if "/lib/ld-musl-" not in headers:
        raise ScriptFailure("smoke binary does not use the musl dynamic loader")
    needed = run([readelf, "-d", str(binary)], capture_output=True).stdout
    if any(value in needed.lower() for value in ("libc.so.6", "ld-linux", "libstdc++", "libgcc", "libgcc_s")):
        raise ScriptFailure("smoke binary has a forbidden GNU runtime dependency")
    executed = run([str(binary)], check=False, capture_output=True, timeout=timeout)
    run_log.write_text((executed.stdout or "") + (executed.stderr or ""), encoding="utf-8")
    if executed.returncode:
        raise ScriptFailure("hermetic smoke target failed at runtime")
    atomic_json(
        metadata / "gate-e-report.json",
        {
            "schema": 1,
            "status": "complete",
            "network": "none",
            "target": target_label,
            "ninja_target": ninja_target,
            "binary": str(binary),
            "build_log": str(build_log),
            "commands": str(commands),
            "run_log": str(run_log),
            "checks": {
                "target_built": True,
                "exceptions_enabled": True,
                "threads_enabled": True,
                "external_static_libcxx": True,
                "musl_interpreter": True,
                "no_forbidden_gnu_runtime": True,
                "target_ran": True,
            },
        },
    )
    print("Gate E: hermetic smoke target built and ran")


if __name__ == "__main__":
    raise SystemExit(fail_main("Gate E", main))
