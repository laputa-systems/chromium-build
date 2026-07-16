#!/usr/bin/env python3
"""Check the effective headless-debug GN values and canonical listing."""

import argparse
import ast
import json
from pathlib import Path


def parse_args(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        if value == "true":
            parsed = True
        elif value == "false":
            parsed = False
        else:
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                parsed = value
        values[name] = parsed
    return values


def parse_value(value):
    if value == "true":
        return True
    if value == "false":
        return False
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--effective-json", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--target-cpu", required=True)
    parser.add_argument("--clang-major", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    values = parse_args(args.args)
    expected = {
        "target_os": "linux",
        "target_cpu": args.target_cpu,
        "host_cpu": args.target_cpu,
        "root_extra_deps": ["//tools/hermetic_smoke"],
        "is_clang": True,
        "is_musl": True,
        "use_sysroot": False,
        "rust_sysroot_absolute": "/usr",
        "rust_bindgen_root": "/usr",
        "use_lld": True,
        "clang_base_path": "/opt/llvm-musl",
        "clang_version": args.clang_major,
        "custom_toolchain": "//build/toolchain/linux/unbundle:default",
        "host_toolchain": "//build/toolchain/linux/unbundle:default",
        "cc_wrapper": "/usr/bin/ccache",
        "is_component_build": False,
        "is_debug": True,
        "is_official_build": False,
        "safe_browsing_mode": 0,
        "symbol_level": 0,
        "blink_symbol_level": 0,
        "v8_symbol_level": 0,
        "chrome_pgo_phase": 0,
        "is_cfi": False,
        "use_thin_lto": False,
        "use_custom_libcxx": True,
        "use_laputa_libcxx": True,
        "use_safe_libstdcxx": False,
        "use_ozone": True,
        "ozone_platform": "headless",
        "ozone_platform_headless": True,
        "use_xkbcommon": False,
        "ozone_platform_wayland": False,
        "use_gtk": False,
        "use_qt6": False,
        "use_alsa": False,
        "use_pulseaudio": False,
        "rtc_use_pipewire": False,
        "rtc_link_pipewire": False,
        "use_vaapi": False,
        "enable_vulkan": False,
        "enable_swiftshader": False,
        "enable_printing": False,
        "ffmpeg_branding": "Chrome",
        "proprietary_codecs": True,
        "enable_widevine": False,
        "optimize_webui": False,
    }
    for name, value in expected.items():
        if values.get(name) != value:
            raise SystemExit(f"GN arg {name}: expected {value!r}, got {values.get(name)!r}")

    probe = json.loads(args.probe.read_text(encoding="utf-8"))
    canonical = args.canonical.read_text(encoding="utf-8")
    effective = json.loads(args.effective_json.read_text(encoding="utf-8"))
    effective_values = {
        item["name"]: parse_value(item["current"]["value"])
        for item in effective
        if "name" in item and "current" in item
    }
    if "clang_version" not in values:
        raise SystemExit("GN arg clang_version is missing")
    for name, value in expected.items():
        if effective_values.get(name) != value:
            raise SystemExit(
                f"effective GN arg {name}: expected {value!r}, "
                f"got {effective_values.get(name)!r}"
            )
    canonical_names = {
        line.split("=", 1)[0].strip()
        for line in canonical.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    for name, result in probe["arguments"].items():
        if result["supported"]:
            expected_value = {
                "devtools_skip_typecheck": False,
                "devtools_bundle": False,
            }[name]
            if values.get(name) != expected_value:
                raise SystemExit(f"proven optional GN arg {name} has the wrong value")
            if effective_values.get(name) != expected_value:
                raise SystemExit(f"effective optional GN arg {name} has the wrong value")
        elif name in values:
            raise SystemExit(f"unproven optional GN arg {name} was supplied")
    for name in expected:
        if name not in canonical_names:
            raise SystemExit(f"canonical GN args omit {name}")
    for name, result in probe["arguments"].items():
        if result["supported"] and name not in canonical_names:
            raise SystemExit(f"canonical GN args omit proven optional arg {name}")

    report = {
        "schema": 1,
        "status": "complete",
        "target_cpu": args.target_cpu,
        "values": values,
        "optional_arguments": probe["arguments"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
