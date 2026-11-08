#!/usr/bin/env python3
"""Package a staged runtime as a self-contained, relocatable bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import linux_runtime

HOST_LIBRARIES = linux_runtime.MUSL_LIBRARIES
GLIBC = linux_runtime.GLIBC_LIBRARIES
LIBRARY_DIRECTORIES = (Path("/usr/lib"), Path("/lib"))
NSS_MODULES = ("libsoftokn3.so", "libfreeblpriv3.so", "libfreebl3.so", "libnssckbi.so")
FONT_ROOT = Path("/usr/share/fonts/noto")
FONTCONFIG_CONFD = Path("/etc/fonts/conf.d")

FONTS_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir prefix="relative">../fonts</dir>
  <cachedir prefix="xdg">fontconfig</cachedir>
  <include ignore_missing="yes">conf.d</include>
</fontconfig>
"""

LAUNCHER = """#!/bin/sh
set -eu
self=$(readlink -f -- "$0")
root=${self%/*}
LD_LIBRARY_PATH="$root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
FONTCONFIG_FILE="$root/fontconfig/fonts.conf"
FONTCONFIG_PATH="$root/fontconfig"
export LD_LIBRARY_PATH FONTCONFIG_FILE FONTCONFIG_PATH
exec "$root/chrome" "$@"
"""

HELIUM_LAUNCHER = """#!/bin/sh
set -eu
self=$(readlink -f -- "$0")
root=${self%/*}
LD_LIBRARY_PATH="$root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
FONTCONFIG_FILE="$root/fontconfig/fonts.conf"
FONTCONFIG_PATH="$root/fontconfig"
GDK_BACKEND=wayland
GTK_USE_PORTAL=0
NO_AT_BRIDGE=1
export LD_LIBRARY_PATH FONTCONFIG_FILE FONTCONFIG_PATH GDK_BACKEND GTK_USE_PORTAL NO_AT_BRIDGE
exec "$root/helium" "$@"
"""


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def needed(path: Path) -> list[str]:
    output = subprocess.run(["llvm-readelf", "-d", str(path)], check=True, capture_output=True, text=True).stdout
    return re.findall(r"\(NEEDED\).*?Shared library: \[([^\]]+)\]", output)


def is_elf(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def find_library(name: str) -> Path:
    for directory in LIBRARY_DIRECTORIES:
        candidate = directory / name
        if candidate.exists():
            return candidate
    raise RuntimeError(f"shared library {name} is not in the build image")


def bundle_libraries(root: Path) -> list[str]:
    lib = root / "lib"
    lib.mkdir()
    pending = [root / "chrome"]
    for name in NSS_MODULES:
        shutil.copy2(find_library(name), lib / name, follow_symlinks=True)
        pending.append(lib / name)
    copied = set(NSS_MODULES)
    while pending:
        for name in needed(pending.pop()):
            if name in GLIBC:
                raise RuntimeError(f"bundle would require glibc: {name}")
            if name in HOST_LIBRARIES or name in copied:
                continue
            shutil.copy2(find_library(name), lib / name, follow_symlinks=True)
            copied.add(name)
            pending.append(lib / name)
    return sorted(copied)


def audit_closure(root: Path) -> None:
    present = {path.name for path in (root / "lib").iterdir()}
    for path in [root / "chrome", *sorted((root / "lib").iterdir())]:
        missing = [name for name in needed(path) if name not in present and name not in HOST_LIBRARIES]
        if missing:
            raise RuntimeError(f"{path.name} needs libraries outside the bundle: {missing}")


def bundle_fonts(root: Path) -> None:
    if not any(FONT_ROOT.rglob("*.[ot]tf")):
        raise RuntimeError(f"no fonts under {FONT_ROOT}")
    shutil.copytree(FONT_ROOT, root / "fonts" / FONT_ROOT.name, symlinks=False)
    confd = root / "fontconfig" / "conf.d"
    confd.mkdir(parents=True)
    for path in sorted(FONTCONFIG_CONFD.glob("*.conf")):
        shutil.copy2(path, confd / path.name, follow_symlinks=True)
    (root / "fontconfig" / "fonts.conf").write_text(FONTS_CONF, encoding="utf-8")


def stage_manifest(stage: Path, product: str) -> dict:
    manifest = json.loads((stage / "runtime-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise RuntimeError("staged runtime is incomplete; run stage-runtime first")
    if product == "helium" and manifest.get("product") != "helium":
        raise RuntimeError("staged runtime is not a Helium runtime")
    return manifest


def copy_staged(stage: Path, destination: Path, manifest: dict) -> None:
    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        source = stage / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != entry["sha256"]:
            raise RuntimeError(f"staged file changed after staging: {relative}")


def package_chromium(stage: Path, output: Path) -> None:
    manifest = stage_manifest(stage, "chromium")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        temporary.chmod(0o755)
        copy_staged(stage, temporary, manifest)
        libraries = bundle_libraries(temporary)
        audit_closure(temporary)
        bundle_fonts(temporary)
        launcher = temporary / "chromium"
        launcher.write_text(LAUNCHER, encoding="utf-8")
        for path in temporary.rglob("*"):
            if path.is_dir() or path.name in {"chrome", "chromium"} or (path.parent.name == "lib" and is_elf(path)):
                path.chmod(0o755)
            else:
                path.chmod(0o644)
        files = sorted(path for path in temporary.rglob("*") if path.is_file())
        packaged = {
            "schema": 1,
            "status": "complete",
            "launcher": "chromium",
            "chrome": "chrome",
            "stage": str(stage),
            "host_requirements": sorted(HOST_LIBRARIES & {name for name in needed(temporary / "chrome")}),
            "bundled_libraries": libraries,
            "files": [
                {"path": str(path.relative_to(temporary)), "size": path.stat().st_size, "sha256": digest(path)}
                for path in files
            ],
        }
        (temporary / "package-manifest.json").write_text(json.dumps(packaged, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(f"package: {len(libraries)} libraries and {len(files)} files bundled at {output}")


def product_identity() -> tuple[str, str]:
    import linux_product

    product = linux_product.resolve(
        "helium",
        os.environ.get("CHROMIUM_ARCH", "amd64"),
        os.environ.get("CHROMIUM_PROFILE", "helium-wayland-release"),
    )
    if not product.product_version:
        raise RuntimeError("Helium product version is missing from the inputs lock")
    return product.product_version, product.arch


def archive_name(version: str) -> str:
    return f"helium-{version}-linux-x86_64-musl"


def archive_package(
    package: Path,
    archive_root: str,
    artifacts: Path,
    verify,
) -> tuple[Path, Path]:
    artifacts.mkdir(parents=True, exist_ok=True)
    archive = artifacts / f"{archive_root}.tar.xz"
    checksum = artifacts / f"{archive.name}.sha256"
    temporary = artifacts / f".{archive.name}.tmp-{os.getpid()}"
    try:
        with tarfile.open(temporary, "w:xz", preset=6) as bundle:
            bundle.add(package, arcname=archive_root, recursive=True)
        temporary.chmod(0o644)
        verify(temporary)
        os.replace(temporary, archive)
        checksum_temporary = checksum.with_name(f".{checksum.name}.tmp-{os.getpid()}")
        checksum_temporary.write_text(f"{digest(archive)}  {archive.name}\n", encoding="utf-8")
        os.replace(checksum_temporary, checksum)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return archive, checksum


def verify_archive(archive: Path, archive_root: str, expected: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="helium package verification ") as directory:
        root = Path(directory)
        with tarfile.open(archive, "r:xz") as bundle:
            for member in bundle.getmembers():
                relative = PurePosixPath(member.name)
                if member.name == archive_root and member.isdir():
                    continue
                if relative.is_absolute() or ".." in relative.parts or not member.name.startswith(f"{archive_root}/"):
                    raise RuntimeError(f"archive entry escapes its root: {member.name}")
                if member.isdev():
                    raise RuntimeError(f"archive contains a device: {member.name}")
            bundle.extractall(root, filter="data")
        extracted = root / archive_root
        for entry in expected["files"]:
            path = extracted / entry["path"]
            if not path.is_file() or digest(path) != entry["sha256"]:
                raise RuntimeError(f"archive verification failed for {entry['path']}")
        linux_runtime.audit_tree(
            extracted,
            expected["architecture"],
            gnu_runtime_allowed=linux_runtime.graphics_gnu_allowlist(extracted),
        )


def package_helium(stage: Path, output: Path, artifacts: Path) -> None:
    version, architecture = product_identity()
    manifest = stage_manifest(stage, "helium")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        temporary.chmod(0o755)
        copy_staged(stage, temporary, manifest)
        bundle_fonts(temporary)
        launcher = temporary / "helium-wrapper"
        launcher.write_text(HELIUM_LAUNCHER, encoding="utf-8")
        for path in temporary.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            elif path.name == "chrome_sandbox":
                path.chmod(0o4755)
            elif path.name == "helium-wrapper" or path.name == "helium" or is_elf(path):
                path.chmod(0o755)
            else:
                path.chmod(0o644)
        audit = linux_runtime.audit_tree(
            temporary,
            architecture,
            gnu_runtime_allowed=linux_runtime.graphics_gnu_allowlist(temporary),
        )
        browser = linux_runtime.inspect_elf(temporary / "helium", temporary)
        files = sorted(path for path in temporary.rglob("*") if path.is_file())
        packaged = {
            "schema": 1,
            "status": "complete",
            "product": "helium",
            "product_version": version,
            "architecture": architecture,
            "launcher": "helium-wrapper",
            "executable": "helium",
            "stage": str(stage),
            "host_requirements": sorted(HOST_LIBRARIES & set(browser.needed)),
            "bundled_libraries": sorted(
                path.name for path in (temporary / "lib").iterdir() if is_elf(path)
            ) if (temporary / "lib").is_dir() else [],
            "audit": audit,
            "files": [
                {
                    "path": path.relative_to(temporary).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": digest(path),
                    "mode": oct(path.stat().st_mode & 0o7777),
                }
                for path in files
            ],
        }
        (temporary / "package-manifest.json").write_text(json.dumps(packaged, indent=2) + "\n", encoding="utf-8")
        archive_root = archive_name(version)
        archive, checksum = archive_package(
            temporary,
            archive_root,
            artifacts,
            lambda path: verify_archive(path, archive_root, packaged),
        )
        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(f"package: {len(packaged['bundled_libraries'])} libraries and {len(files)} files at {output}")
    print(f"package: {archive} ({checksum})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--product", choices=("chromium", "helium"), default=os.environ.get("CHROMIUM_PRODUCT", "chromium"))
    parser.add_argument("--artifacts", type=Path, default=Path(os.environ.get("CHROMIUM_ARTIFACTS_ROOT", "/work/artifacts")))
    args = parser.parse_args()
    if args.product == "helium":
        package_helium(args.stage, args.output, args.artifacts)
    else:
        package_chromium(args.stage, args.output)


if __name__ == "__main__":
    main()
