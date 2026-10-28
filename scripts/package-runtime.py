#!/usr/bin/env python3
"""Package a staged headless runtime as a self-contained, relocatable bundle.

The staged runtime links against the image's NSS, NSPR, FFmpeg, and their
transitive closure, and needs a fontconfig configuration with at least one
font. A bundle carries all of that so the host needs only the musl loader and
libc: shared libraries go in ``lib/``, the pinned Noto fonts in ``fonts/``, and
``chromium`` is a launcher that points the loader and fontconfig at them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# musl's libc is also its loader; the host must provide it.
HOST_LIBRARIES = {"libc.musl-x86_64.so.1", "libc.musl-aarch64.so.1", "ld-musl-x86_64.so.1", "ld-musl-aarch64.so.1"}
# stage-runtime keeps libstdc++/libgcc_s out of chrome's own requirements, but
# the audited FFmpeg closure reaches Alpine's musl builds of them, so they are
# bundled. glibc itself can never load under musl.
GLIBC = {"libc.so.6", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1"}
LIBRARY_DIRECTORIES = (Path("/usr/lib"), Path("/lib"))
# NSS opens its PKCS#11 soft token, freebl, and the built-in roots with dlopen,
# relative to libnss3's own directory, so they never appear as DT_NEEDED.
NSS_MODULES = ("libsoftokn3.so", "libfreeblpriv3.so", "libfreebl3.so", "libnssckbi.so")
FONT_ROOT = Path("/usr/share/fonts/noto")
FONTCONFIG_CONFD = Path("/etc/fonts/conf.d")

# Chromium's bundled fontconfig honours prefix="relative" on <dir> but not on
# <include>; a plain relative include resolves against FONTCONFIG_PATH, which
# the launcher sets. Without conf.d there are no generic-family aliases and
# every family falls back to one sans face.
FONTS_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir prefix="relative">../fonts</dir>
  <cachedir prefix="xdg">fontconfig</cachedir>
  <include ignore_missing="yes">conf.d</include>
</fontconfig>
"""

LAUNCHER = """#!/bin/sh
# Launch the bundled Chromium with its own libraries and fonts. Child
# processes re-execute chrome and inherit this environment.
set -eu
self=$(readlink -f -- "$0")
root=${self%/*}
LD_LIBRARY_PATH="$root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
FONTCONFIG_FILE="$root/fontconfig/fonts.conf"
FONTCONFIG_PATH="$root/fontconfig"
export LD_LIBRARY_PATH FONTCONFIG_FILE FONTCONFIG_PATH
exec "$root/chrome" "$@"
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
    """Copy the transitive DT_NEEDED closure of chrome and the NSS modules."""
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
    """Every ELF in the bundle must resolve inside it, except musl itself."""
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    staged = json.loads((args.stage / "runtime-manifest.json").read_text(encoding="utf-8"))
    if staged.get("status") != "complete":
        raise RuntimeError("staged runtime is incomplete; run stage-runtime first")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.output.name}.", dir=args.output.parent))
    try:
        temporary.chmod(0o755)
        for entry in staged["files"]:
            destination = temporary / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.stage / entry["path"], destination)
            if digest(destination) != entry["sha256"]:
                raise RuntimeError(f"staged file changed after staging: {entry['path']}")
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
        manifest = {
            "schema": 1,
            "status": "complete",
            "launcher": "chromium",
            "chrome": "chrome",
            "stage": str(args.stage),
            "host_requirements": sorted(HOST_LIBRARIES & {name for name in needed(temporary / "chrome")}),
            "bundled_libraries": libraries,
            "files": [
                {"path": str(path.relative_to(temporary)), "size": path.stat().st_size, "sha256": digest(path)}
                for path in files
            ],
        }
        (temporary / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if args.output.exists():
            shutil.rmtree(args.output)
        os.replace(temporary, args.output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(f"package: {len(libraries)} libraries and {len(files)} files bundled at {args.output}")


if __name__ == "__main__":
    main()
