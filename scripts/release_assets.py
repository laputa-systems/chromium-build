#!/usr/bin/env python3
"""Assemble the standard release archive and manifest from a finished macOS package."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))
import macos_backend

REPOSITORY = SCRIPT_ROOT.parent
PRODUCT = "chromium"
MACOS_TARGET = "aarch64-apple-darwin"
MACOS_LOCK = REPOSITORY / "config" / "macos.inputs.lock"
SOURCES_LOCK = REPOSITORY / "sources.lock.json"
REVISION = re.compile(r"[0-9a-f]{40}")
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


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"{path} must be a JSON object")
    return value


def asset_stem(version, target):
    return f"{PRODUCT}-{version}-{target}"


def extract_app(package, destination):
    subprocess.run(["/usr/bin/ditto", "-x", "-k", str(package), str(destination)], check=True)
    apps = sorted(destination.rglob("Chromium.app"))
    if len(apps) != 1:
        raise SystemExit(f"{package} holds {len(apps)} Chromium.app bundles, expected 1")
    return apps[0]


def normalize(member):
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    return member


def write_archive(app, archive, root_name):
    temporary = archive.with_name(f".{archive.name}.tmp")
    try:
        with tarfile.open(temporary, "w:xz") as bundle:
            bundle.add(app, arcname=f"{root_name}/{app.name}", filter=normalize)
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)


def verify_archive(archive, root_name, fingerprint):
    with tempfile.TemporaryDirectory(dir=archive.parent, prefix=".verify-") as scratch:
        with tarfile.open(archive, "r:xz") as bundle:
            bundle.extractall(scratch, filter="tar")
        app = Path(scratch) / root_name / "Chromium.app"
        if macos_backend.bundle_fingerprint(app) != fingerprint:
            raise SystemExit(f"{archive.name} does not reproduce the packaged bundle")
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def build_manifest(version, revision, package_report, legacy_manifest):
    manifest = {
        "schema_version": 1,
        "product": PRODUCT,
        "version": version,
        "target": MACOS_TARGET,
        "source_revision": revision,
        "sources_lock_sha256": sha256_file(SOURCES_LOCK),
        "acceptance": package_report.get("acceptance"),
        "payload_sha256": package_report.get("fingerprint"),
    }
    manifest.update({key: legacy_manifest[key] for key in BUILD_FIELDS if key in legacy_manifest})
    return manifest


def assemble_macos(work_root, output, revision):
    if not REVISION.fullmatch(revision):
        raise SystemExit("source revision must be a full lowercase commit SHA")
    version = read_json(MACOS_LOCK)["chromium"]["version"]
    package_report = read_json(work_root / "metadata" / "package-macos.json")
    if package_report.get("status") != "complete":
        raise SystemExit("package report is not complete; run package")
    legacy_manifest = read_json(work_root / "release" / "release-manifest.json")
    stem = asset_stem(version, MACOS_TARGET)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{stem}.tar.xz"
    with tempfile.TemporaryDirectory(dir=output, prefix=".extract-") as scratch:
        app = extract_app(Path(package_report["archive"]), Path(scratch))
        write_archive(app, archive, stem)
    verify_archive(archive, stem, package_report["fingerprint"])
    manifest = output / f"{stem}.manifest.json"
    manifest.write_text(
        json.dumps(build_manifest(version, revision, package_report, legacy_manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"release assets: {archive} {manifest}")


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
