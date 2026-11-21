#!/usr/bin/env python3
"""Bind a passing Helium browser report to the package archive it exercised."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

import linux_product
import runtime_install
from script_support import JsonObject


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def section(report: JsonObject, key: str) -> JsonObject:
    value = report.get(key)
    return value if isinstance(value, dict) else {}


def headless_renderers_sandboxed(renderers: object) -> bool:
    return (
        isinstance(renderers, list)
        and bool(renderers)
        and all(
            isinstance(item, dict)
            and item.get("no_new_privs") == "1"
            and item.get("seccomp") == "2"
            and len(item.get("nspid", [])) > 1
            for item in renderers
        )
    )


def headless_checks(headless: JsonObject, desktop: linux_product.Desktop | None = None) -> dict[str, bool]:
    desktop = desktop or runtime_install.desktop_for("helium")
    version_info = section(headless, "version_info")
    headless_exit = section(headless, "exit")
    return {
        "headless-devtools": str(headless.get("devtools_endpoint", "")).startswith("ws://"),
        "headless-page": section(headless, "page").get("evaluated") == {"status": "ready", "layout": True},
        "headless-sandbox-renderer": headless_renderers_sandboxed(section(headless, "sandbox").get("renderers")),
        "headless-clean-exit": headless_exit.get("returncode") == 0 and headless_exit.get("survivors") == [],
        "headless-identity": str(version_info.get("Browser", "")).startswith(f"{desktop.browser_prefix}/"),
        "headless-user-agent": "Chrome/" in str(version_info.get("User-Agent", ""))
        and "Helium" not in str(version_info.get("User-Agent", "")),
    }


def report_checks(report: JsonObject, desktop: linux_product.Desktop | None = None) -> dict[str, bool]:
    desktop = desktop or runtime_install.desktop_for("helium")
    renderers = section(report, "sandbox").get("renderers")
    headless = section(report, "headless")
    headless_version = section(headless, "version_info")
    checks = {
        "product-identity": bool(report.get("version") and report.get("version_info")),
        "wayland-window": bool(report.get("window") and report.get("compositor")),
        "page-rendering": bool(report.get("screenshot")),
        "sandbox-renderer": isinstance(renderers, list) and bool(renderers),
        "clean-shutdown": section(report, "cleanup").get("ok") is True,
        **headless_checks(headless, desktop),
    }
    checks["headless-identity"] = (
        checks["headless-identity"]
        and desktop.name.capitalize() in str(report.get("version", ""))
        and headless_version.get("Browser") == section(report, "version_info").get("Browser")
    )
    if not all(checks.values()):
        failed = sorted(name for name, value in checks.items() if not value)
        raise runtime_install.InstallError(f"browser report does not prove: {', '.join(failed)}")
    return checks


def host_headless_checks(report: JsonObject, product_version: str, desktop: linux_product.Desktop | None = None) -> dict[str, bool]:
    desktop = desktop or runtime_install.desktop_for("helium")
    if report.get("status") != "complete" or report.get("product") != desktop.name or report.get("scope") != "headless":
        raise runtime_install.InstallError(f"host headless report is not a complete {desktop.name.capitalize()} headless result")
    if report.get("product_version") != product_version:
        raise runtime_install.InstallError("host headless report version does not match the archive")
    headless_browser = section(section(report, "headless"), "version_info").get("Browser")
    checks = {
        "host-executable-identity": desktop.name.capitalize() in str(report.get("version", "")),
        "host-devtools-browser": headless_browser == f"{desktop.browser_prefix}/{product_version}",
    }
    checks.update({f"host-{name}": value for name, value in headless_checks(section(report, "headless"), desktop).items()})
    load = section(report, "headless_load")
    results = load.get("results")
    checks["host-headless-load"] = (
        isinstance(results, list)
        and len(results) >= 3
        and load.get("crashes") == 0
        and load.get("sigsys_exits") == 0
        and all(isinstance(item, dict) and item.get("iterations", 0) > 0 and "error" not in item for item in results)
    )
    if not all(checks.values()):
        failed = sorted(name for name, value in checks.items() if not value)
        raise runtime_install.InstallError(f"host headless report does not prove: {', '.join(failed)}")
    return checks


def read_manifest(archive: Path) -> JsonObject:
    archive_digest = runtime_install.sidecar_digest(archive)
    root_name = archive.name.removesuffix(".tar.xz")
    with tempfile.TemporaryDirectory(prefix="helium-qualify-", dir=os.environ.get("TMPDIR") or None) as directory:
        root = Path(directory) / root_name
        root.mkdir()
        with tarfile.open(archive, "r:xz") as bundle:
            names = set()
            for member in bundle.getmembers():
                relative = runtime_install.safe_member(member, root_name)
                if str(relative) in names:
                    raise runtime_install.InstallError(f"archive contains a duplicate entry: {member.name}")
                names.add(str(relative))
            bundle.extractall(root.parent, filter="data")
        manifest_path = root / "package-manifest.json"
        if not manifest_path.is_file():
            raise runtime_install.InstallError("archive has no package manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identity = {
            "product": manifest.get("product"),
            "product_version": manifest.get("product_version"),
            "architecture": manifest.get("architecture"),
        }
        runtime_install.manifest_for(root, identity, archive)
    manifest["archive_sha256"] = archive_digest
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--host-headless-report", type=Path, help="helium_test_fast.py --headless-only result from the extracted archive on a host")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        manifest = read_manifest(args.archive)
        desktop = runtime_install.desktop_for(manifest.get("product"))
        if report.get("status") != "complete" or report.get("product") != desktop.name:
            raise runtime_install.InstallError(f"browser report is not a complete {desktop.name.capitalize()} result")
        if report.get("product_version") != manifest.get("product_version"):
            raise runtime_install.InstallError("browser report version does not match the archive")
        if report.get("archive_sha256") not in (None, manifest["archive_sha256"]):
            raise runtime_install.InstallError("browser report was not produced from this archive")
        checks = report_checks(report, desktop)
        output = args.output or args.archive.with_name(args.archive.name + ".acceptance.json")
        acceptance = {
            "schema": 1,
            "status": "complete",
            "product": desktop.name,
            "product_version": manifest["product_version"],
            "architecture": manifest["architecture"],
            "archive": args.archive.name,
            "archive_root": args.archive.name.removesuffix(".tar.xz"),
            "archive_sha256": manifest["archive_sha256"],
            "report_sha256": digest(args.report),
            "checks": checks,
            "identities": {
                "executable_version": report["version"],
                "devtools_browser": section(section(report, "headless"), "version_info")["Browser"],
            },
        }
        if args.host_headless_report:
            host_report = json.loads(args.host_headless_report.read_text(encoding="utf-8"))
            host_checks = host_headless_checks(host_report, manifest["product_version"], desktop)
            acceptance["host_headless"] = {
                "report_sha256": digest(args.host_headless_report),
                "executable_version": host_report["version"],
                "devtools_browser": section(section(host_report, "headless"), "version_info")["Browser"],
                "user_namespaces": section(section(host_report, "headless"), "user_namespaces"),
                "load": {key: host_report["headless_load"][key] for key in ("instances", "seconds", "iterations", "crashes", "sigsys_exits")},
                "checks": host_checks,
            }
        temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
        temporary.write_text(json.dumps(acceptance, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, output)
        print(f"qualify-package: wrote {output}")
        return 0
    except (runtime_install.InstallError, OSError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"qualify-package: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
