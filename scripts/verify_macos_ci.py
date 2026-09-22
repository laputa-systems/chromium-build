#!/usr/bin/env python3
"""Reusable policy checks used by the workflow-dispatch macOS build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))
import macos_backend  # noqa: E402


VERSION_RE = re.compile(r"version\s+(\d+)\.(\d+)(?:\.(\d+))?")
SHA512_RE = re.compile(r"[0-9a-f]{128}")


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"{label} is not readable JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def require(condition: bool, message: str, report: Mapping[str, Any] | None = None) -> None:
    if condition:
        return
    detail = f"\nreport={json.dumps(report, indent=2, sort_keys=True)}" if report else ""
    raise SystemExit(f"macOS CI policy check failed: {message}{detail}")


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", value)
    if not match:
        raise SystemExit(f"could not parse a version from {value!r}")
    return tuple(int(match.group(index) or 0) for index in range(1, 4))  # type: ignore[return-value]


def claim_work_root(arguments: argparse.Namespace) -> None:
    repo = Path(arguments.repo_root).resolve()
    work_root = Path(arguments.work_root)
    layout = macos_backend.WorkLayout(work_root, repo)
    layout.ensure()
    print(layout.root)


def locked_input_entries(lock: Mapping[str, Any], include_test_inputs: bool) -> list[tuple[str, Mapping[str, Any]]]:
    entries = [
        ("chromium", lock["chromium"]["source"]),
        ("ungoogled", lock["ungoogled"]["source"]),
    ]
    if include_test_inputs:
        entries.append(("ublock-origin", lock["test_inputs"]["ublock_origin"]))
    return entries


def print_cache_key(arguments: argparse.Namespace) -> None:
    lock = read_json(Path(arguments.lock), "macOS input lock")
    include_test_inputs = arguments.include_test_inputs
    parts = []
    for name, entry in locked_input_entries(lock, include_test_inputs):
        digest = str(entry.get("sha512", ""))
        require(bool(SHA512_RE.fullmatch(digest)), f"{name} has no valid SHA-512 lock digest")
        parts.extend((name, str(entry["filename"]), digest))
    scope = "with-test-inputs" if include_test_inputs else "no-test-inputs"
    print("macos-26-arm64-locked-inputs-" + scope + "-" + "-".join(parts))


def print_chromium_version(arguments: argparse.Namespace) -> None:
    lock = read_json(Path(arguments.lock), "macOS input lock")
    version = lock.get("chromium", {}).get("version")
    require(isinstance(version, str) and bool(version), "macOS lock has no Chromium version")
    print(version)


def verify_llvm(arguments: argparse.Namespace) -> None:
    try:
        result = subprocess.run(
            [str(arguments.clang), "--version"],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as error:
        raise SystemExit(f"could not execute LLVM clang: {error}") from error
    require(result.returncode == 0, f"LLVM clang failed: {result.stderr.strip()}")
    output = result.stdout or result.stderr
    match = VERSION_RE.search(output)
    require(match is not None, f"LLVM clang version is not parseable: {output.strip()}")
    actual = tuple(int(match.group(index) or 0) for index in range(1, 4))
    minimum = version_tuple(arguments.minimum)
    require(actual >= minimum, f"LLVM {actual} is older than {minimum}")
    print(".".join(str(part) for part in actual))


def verify_preflight(arguments: argparse.Namespace) -> None:
    report = read_json(Path(arguments.work_root) / "metadata" / "doctor.json", "doctor report")
    require(report.get("status") == "complete", "doctor did not complete", report)
    require(report.get("preflight_scope") == "host-only-full-build", "wrong preflight scope", report)
    checks = report.get("checks", {})
    macos = checks.get("macos", {})
    xcode = checks.get("xcode", {})
    llvm = checks.get("llvm", {})
    rust = checks.get("rust", {})
    filesystem = checks.get("filesystem", {})
    cache = checks.get("compiler_cache", {})
    require(version_tuple(str(macos.get("version", "0"))) >= (26, 0, 0), "macOS is older than 26", report)
    require(version_tuple(str(xcode.get("sdk_version", "0"))) >= (26, 0, 0), "SDK is older than 26", report)
    require(version_tuple(str(llvm.get("version", "0"))) >= (23, 1, 0), "LLVM is older than 23.1.0", report)
    require(rust.get("status") == "complete", "Rust toolchain is incomplete", report)
    require(rust.get("toolchain") == "nightly-2026-09-15", "wrong Rust nightly", report)
    require(rust.get("components") == ["rust-src", "llvm-tools-preview"], "wrong Rust components", report)
    require(cache.get("status") == "disabled", "compiler caching is enabled", report)
    require(
        int(filesystem.get("minimum_free_bytes", 0)) >= int(arguments.minimum_free_gb) * 1024**3,
        "free disk space is below the full-build floor",
        report,
    )
    required_tools = {"llvm-ar", "llvm-nm", "llvm-objcopy", "llvm-strip"}
    require(required_tools <= set(llvm.get("tools", {})), "required LLVM tools are missing", report)
    require(all(item.get("status") == "skipped" for item in report.get("references", {}).values()), "reference checkout was used", report)
    print("macOS full-build preflight policy: passed")


def verify_configure(arguments: argparse.Namespace) -> None:
    root = Path(arguments.work_root)
    report = read_json(root / "metadata" / "configure-macos.json", "configure report")
    require(report.get("status") == "complete", "configure did not complete", report)
    args_path = root / "metadata" / "macos-release.args.gn"
    try:
        args = args_path.read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"could not read generated GN args: {error}") from error
    require('macos_single_locale = "en-US"' in args, "configure did not select en-US only", report)
    require("is_component_build = false" in args, "configure enabled a component build", report)
    print("macOS configure policy: passed")


def verify_cache_disabled(arguments: argparse.Namespace) -> None:
    report = read_json(
        Path(arguments.work_root) / "metadata" / "compiler-cache-stats.json",
        "compiler cache report",
    )
    require(report.get("cache", {}).get("status") == "disabled", "compiler caching is enabled", report)
    print("compiler-cache policy: disabled")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package(arguments: argparse.Namespace) -> None:
    report = read_json(Path(arguments.work_root) / "metadata" / "package-macos.json", "package report")
    require(report.get("status") == "complete", "package did not complete", report)
    archive = Path(str(report.get("archive", ""))).resolve()
    release = (Path(arguments.work_root) / "release").resolve()
    require(release in archive.parents, "package escaped the release directory", report)
    require(archive.is_file() and archive.stat().st_size > 0, "package archive is missing", report)
    digest = sha256(archive)
    require(digest == report.get("sha256"), "package digest does not match its report", report)
    checksum = archive.with_name(f"{archive.name}.sha256")
    require(checksum.is_file(), "package checksum is missing", report)
    require(digest in checksum.read_text(encoding="utf-8"), "package checksum is incorrect", report)
    print(f"package policy: passed ({archive})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    claim = commands.add_parser("claim-work-root")
    claim.add_argument("--repo-root", type=Path, required=True)
    claim.add_argument("--work-root", type=Path, required=True)
    claim.set_defaults(function=claim_work_root)

    cache_key = commands.add_parser("cache-key")
    cache_key.add_argument("--lock", type=Path, required=True)
    cache_key.add_argument("--include-test-inputs", action="store_true")
    cache_key.set_defaults(function=print_cache_key)

    version = commands.add_parser("chromium-version")
    version.add_argument("--lock", type=Path, required=True)
    version.set_defaults(function=print_chromium_version)

    llvm = commands.add_parser("verify-llvm")
    llvm.add_argument("--clang", type=Path, required=True)
    llvm.add_argument("--minimum", required=True)
    llvm.set_defaults(function=verify_llvm)

    for name, function in (
        ("verify-preflight", verify_preflight),
        ("verify-configure", verify_configure),
        ("verify-cache-disabled", verify_cache_disabled),
        ("verify-package", verify_package),
    ):
        command = commands.add_parser(name)
        command.add_argument("--work-root", type=Path, required=True)
        if name == "verify-preflight":
            command.add_argument("--minimum-free-gb", type=int, default=80)
        command.set_defaults(function=function)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    arguments.function(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
