#!/usr/bin/env python3
"""Generate GN output and validate the hermetic toolchain configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from script_support import (
    ScriptFailure,
    atomic_json,
    command_path,
    fail_main,
    require_directory,
    require_file,
    run,
)


def loopback_only() -> bool:
    route = Path("/proc/net/route")
    if route.is_file():
        for line in route.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            fields = line.split()
            if fields and fields[0] != "lo":
                return False
    ipv6 = Path("/proc/net/ipv6_route")
    if ipv6.is_file():
        for line in ipv6.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if fields and (fields[0] not in {"0" * 32, "0" * 31 + "1"}):
                return False
    return True


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    source_root = Path(os.environ.get("CHROMIUM_SOURCE_ROOT", work / "src"))
    out = Path(os.environ.get("GN_OUT", work / "out/headless-debug"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    profile_name = os.environ.get("CHROMIUM_PROFILE", "headless-debug")
    gn = os.environ.get("GN", "gn")
    if os.environ.get("NETWORK_MODE", "none") != "none":
        raise ScriptFailure("GN generation requires NETWORK_MODE=none")
    require_file(metadata / "gate-c-report.json", "Gate C has not passed")
    require_file(metadata / "gate-a-summary", "Gate A has not passed")
    require_directory(source_root, f"missing source root {source_root}")
    command_path(gn)
    gate_c = json.loads((metadata / "gate-c-report.json").read_text(encoding="utf-8"))
    if gate_c.get("status") != "complete" or gate_c.get("network") != "none":
        raise ScriptFailure("Gate C report is incomplete or not offline")
    if not loopback_only():
        raise ScriptFailure("non-loopback route is present")

    args_file = Path(os.environ.get("GN_ARGS_OUTPUT", metadata / f"{profile_name}.args.gn"))
    probe_file = Path(os.environ.get("GN_ARG_PROBE_OUTPUT", metadata / "gn-arg-probe.json"))
    profile = root / "config/profiles" / f"{profile_name}.gn"
    lock = json.loads((root / "config/inputs.lock").read_text(encoding="utf-8"))
    arch = os.environ.get("CHROMIUM_ARCH", "arm64")
    target_cpu = {"arm64": "arm64", "amd64": "x64"}.get(arch)
    if target_cpu is None:
        raise ScriptFailure(f"unsupported architecture {arch}")
    clang_major = lock["toolchain"]["version"].split(".", 1)[0]
    run(
        [
            "python3",
            str(root / "scripts/generate-gn-args.py"),
            "--source-root",
            str(source_root),
            "--profile",
            str(profile),
            "--target-cpu",
            target_cpu,
            "--clang-major",
            clang_major,
            "--args-output",
            str(args_file),
            "--probe-output",
            str(probe_file),
        ]
    )
    # GN comments end at a newline; preserve the rendered profile's line breaks.
    args = args_file.read_text(encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    gn_log = metadata / "gn-gen.log"
    generated = run(
        [gn, "gen", str(out), f"--root={source_root}", "--fail-on-unused-args", f"--args={args}"],
        check=False,
        capture_output=True,
    )
    gn_log.write_text((generated.stdout or "") + (generated.stderr or ""), encoding="utf-8")
    if generated.returncode:
        raise ScriptFailure("gn gen failed; see gn-gen.log")

    overlay_header = source_root / "build/config/musl-compat/include/sys/poll.h"
    overlay_token = "-isystem ../../src/build/config/musl-compat/include"
    require_file(overlay_header, "musl sys/poll.h overlay header is missing")
    toolchain_ninja = out / "toolchain.ninja"
    require_file(toolchain_ninja, "generated toolchain.ninja is missing")
    overlay_rules = [line for line in toolchain_ninja.read_text(encoding="utf-8").splitlines() if overlay_token in line]
    if len(overlay_rules) < 3:
        raise ScriptFailure(f"generated compiler rules do not carry the musl poll overlay (found {len(overlay_rules)})")
    if not any("clang++" in line for line in overlay_rules):
        raise ScriptFailure("C++ compiler rule lacks the musl poll overlay")
    if not any("clang " in line for line in overlay_rules):
        raise ScriptFailure("C compiler rule lacks the musl poll overlay")

    canonical = run([gn, "args", str(out), f"--root={source_root}", "--list", "--short"], capture_output=True).stdout
    (metadata / "gn-args.canonical.txt").write_text(canonical, encoding="utf-8")
    effective = run([gn, "args", str(out), f"--root={source_root}", "--list", "--json"], capture_output=True).stdout
    (metadata / "gn-args.effective.json").write_text(effective, encoding="utf-8")
    run(
        [
            "python3",
            str(root / "scripts/verify-gn-args.py"),
            "--args",
            str(out / "args.gn"),
            "--canonical",
            str(metadata / "gn-args.canonical.txt"),
            "--effective-json",
            str(metadata / "gn-args.effective.json"),
            "--probe",
            str(probe_file),
            "--target-cpu",
            target_cpu,
            "--clang-major",
            clang_major,
            "--output",
            str(metadata / "gn-args-report.json"),
        ]
    )
    check_status = "skipped"
    check_reason = "bounded GN check is disabled; set GN_CHECK=1 to run //chrome:chrome"
    if os.environ.get("GN_CHECK", "0") == "1":
        timeout = float(os.environ.get("GN_CHECK_TIMEOUT", "120"))
        run([gn, "check", str(out), "//chrome:chrome"], timeout=timeout)
        check_status = "passed"
        check_reason = "//chrome:chrome"
    atomic_json(
        metadata / "gate-d-report.json",
        {
            "schema": 1,
            "status": "complete",
            "network": "none",
            "profile": profile_name,
            "output": str(out),
            "args_file": str(args_file),
            "canonical_args": str(metadata / "gn-args.canonical.txt"),
            "effective_args": str(metadata / "gn-args.effective.json"),
            "gn_check": check_status,
            "gn_check_detail": check_reason,
            "musl_poll_overlay": {
                "header": str(overlay_header),
                "ninja_token": overlay_token,
                "compiler_rule_count": len(overlay_rules),
            },
        },
    )
    print(f"Gate D: GN generation passed for {profile_name}")


if __name__ == "__main__":
    raise SystemExit(fail_main("Gate D", main))
