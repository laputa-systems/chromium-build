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

import runtime_install


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def section(report: dict, key: str) -> dict:
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


def report_checks(report: dict) -> dict[str, bool]:
    renderers = section(report, "sandbox").get("renderers")
    headless = section(report, "headless")
    headless_version = section(headless, "version_info")
    headless_exit = section(headless, "exit")
    checks = {
        "product-identity": bool(report.get("version") and report.get("version_info")),
        "wayland-window": bool(report.get("window") and report.get("compositor")),
        "page-rendering": bool(report.get("screenshot")),
        "sandbox-renderer": isinstance(renderers, list) and bool(renderers),
        "clean-shutdown": section(report, "cleanup").get("ok") is True,
        "headless-devtools": str(headless.get("devtools_endpoint", "")).startswith("ws://"),
        "headless-identity": "Helium" in f"{headless_version.get('Browser', '')} {headless_version.get('User-Agent', '')}",
        "headless-page": section(headless, "page").get("evaluated") == {"status": "ready", "layout": True},
        "headless-sandbox-renderer": headless_renderers_sandboxed(section(headless, "sandbox").get("renderers")),
        "headless-clean-exit": headless_exit.get("returncode") == 0 and headless_exit.get("survivors") == [],
    }
    if not all(checks.values()):
        failed = sorted(name for name, value in checks.items() if not value)
        raise runtime_install.InstallError(f"browser report does not prove: {', '.join(failed)}")
    return checks


def read_manifest(archive: Path) -> dict:
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
            try:
                bundle.extractall(root.parent, filter="data")
            except TypeError:
                bundle.extractall(root.parent)
        manifest_path = root / "package-manifest.json"
        if not manifest_path.is_file():
            raise runtime_install.InstallError("archive has no package manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identity = {
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
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        if report.get("status") != "complete" or report.get("product") != "helium":
            raise runtime_install.InstallError("browser report is not a complete Helium result")
        manifest = read_manifest(args.archive)
        if report.get("product_version") != manifest.get("product_version"):
            raise runtime_install.InstallError("browser report version does not match the archive")
        if report.get("archive_sha256") not in (None, manifest["archive_sha256"]):
            raise runtime_install.InstallError("browser report was not produced from this archive")
        checks = report_checks(report)
        output = args.output or args.archive.with_name(args.archive.name + ".acceptance.json")
        acceptance = {
            "schema": 1,
            "status": "complete",
            "product": "helium",
            "product_version": manifest["product_version"],
            "architecture": manifest["architecture"],
            "archive": args.archive.name,
            "archive_root": args.archive.name.removesuffix(".tar.xz"),
            "archive_sha256": manifest["archive_sha256"],
            "report_sha256": digest(args.report),
            "checks": checks,
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
