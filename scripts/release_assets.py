#!/usr/bin/env python3
"""Assemble the standard release archive and manifest from a finished macOS package."""

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))
import macos_backend
from buildlib import hashing, release

REPOSITORY = SCRIPT_ROOT.parent
PRODUCT = "chromium"
MACOS_TARGET = "aarch64-apple-darwin"
MACOS_LOCK = REPOSITORY / "config" / "macos.inputs.lock"
SOURCES_LOCK = REPOSITORY / "sources.lock.json"
ARCHIVE_MTIME = 0
BUILD_FIELDS = (
    "build_identity_digest",
    "chromium_commit",
    "clang_version",
    "rust_toolchain",
    "sdk",
    "signing_status",
    "skipped_capabilities",
    "ungoogled_commit",
    "xcode",
)


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"{path} must be a JSON object")
    return value


def extract_app(package, destination):
    subprocess.run(["/usr/bin/ditto", "-x", "-k", str(package), str(destination)], check=True)
    apps = sorted(destination.rglob("Chromium.app"))
    if len(apps) != 1:
        raise SystemExit(f"{package} holds {len(apps)} Chromium.app bundles, expected 1")
    return apps[0]


def verify_archive(archive, root_name, fingerprint):
    with tempfile.TemporaryDirectory(dir=archive.parent, prefix=".verify-") as scratch:
        with tarfile.open(archive, "r:xz") as bundle:
            bundle.extractall(scratch, filter="tar")
        app = Path(scratch) / root_name / "Chromium.app"
        if macos_backend.bundle_fingerprint(app) != fingerprint:
            raise SystemExit(f"{archive.name} does not reproduce the packaged bundle")
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def build_manifest(version, revision, package_report, legacy_manifest):
    extra = {"acceptance": package_report.get("acceptance"), "payload_sha256": package_report.get("fingerprint")}
    extra.update({key: legacy_manifest[key] for key in BUILD_FIELDS if key in legacy_manifest})
    try:
        return release.build_manifest(
            product=PRODUCT,
            version=version,
            target=MACOS_TARGET,
            source_revision=revision,
            sources_lock_sha256=hashing.sha256_file(SOURCES_LOCK),
            extra=extra,
        )
    except release.ReleaseError as error:
        raise SystemExit(str(error)) from error


def assemble_macos(work_root, output, revision):
    version = read_json(MACOS_LOCK)["chromium"]["version"]
    package_report = read_json(work_root / "metadata" / "package-macos.json")
    if package_report.get("status") != "complete":
        raise SystemExit("package report is not complete; run package")
    legacy_manifest = read_json(work_root / "release" / "release-manifest.json")
    manifest = build_manifest(version, revision, package_report, legacy_manifest)
    archive = output / release.archive_name(PRODUCT, version, MACOS_TARGET)
    root_name = archive.name.removesuffix(".tar.xz")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output, prefix=".extract-") as scratch:
        app = extract_app(Path(package_report["archive"]), Path(scratch))
        try:
            release.create_archive(app, archive, mtime=ARCHIVE_MTIME, prefix=f"{root_name}/{app.name}")
        except release.ReleaseError as error:
            raise SystemExit(str(error)) from error
    verify_archive(archive, root_name, package_report["fingerprint"])
    written = release.write_manifest(output, manifest)
    print(f"release assets: {archive} {written}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    macos = commands.add_parser("macos")
    macos.add_argument("--work-root", type=Path, required=True)
    macos.add_argument("--output", type=Path, required=True)
    macos.add_argument("--source-revision", required=True)
    arguments = parser.parse_args(argv)
    assemble_macos(arguments.work_root, arguments.output, arguments.source_revision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
