"""Install native Linux host tools from verified input archives."""

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


def install_rust_toolchain(source: Path, inputs: Path, lock: dict, arch: str) -> dict | None:
    item = lock["architecture_inputs"][arch].get("rust_toolchain")
    if item is None:
        return None
    destination = source / item["destination"]
    pinned = re.search(r"^RUST_REVISION = '([0-9a-f]+)'$", (source / "tools/rust/update_rust.py").read_text(encoding="utf-8"), re.MULTILINE)
    if pinned is None or pinned.group(1) != item["revision"]:
        raise SystemExit(f"tools/rust/update_rust.py pins Rust {pinned and pinned.group(1)}, the lock provides {item['revision']}")
    chromium_version = (destination / "VERSION").read_text(encoding="utf-8")
    archive = inputs / item["filename"]
    if not archive.is_file():
        raise SystemExit(f"missing locked Rust toolchain {archive}")
    top = item["filename"].removesuffix(".tar.xz")
    with tempfile.TemporaryDirectory(prefix="rust-toolchain.", dir=destination.parent) as directory:
        with tarfile.open(archive) as stream:
            if {member.name.split("/", 1)[0] for member in stream.getmembers()} != {top}:
                raise SystemExit(f"{archive.name} must contain only {top}/")
            stream.extractall(directory, filter="data")
        extracted = Path(directory) / top
        if (extracted / "VERSION").read_text(encoding="utf-8") != chromium_version:
            raise SystemExit(f"{archive.name} VERSION differs from the Rust toolchain Chromium pins")
        shutil.rmtree(destination)
        extracted.rename(destination)
    rustc = subprocess.run([str(destination / "bin/rustc"), "-vV"], check=True, capture_output=True, text=True).stdout
    expected = {
        "commit-hash": item["revision"],
        "host": lock["toolchain"]["architectures"][arch]["target_triple"],
        "LLVM version": lock["toolchain"]["architectures"][arch].get("version", lock["toolchain"]["version"]),
    }
    reported = dict(line.split(": ", 1) for line in rustc.splitlines() if ": " in line)
    mismatched = {key: reported.get(key) for key, value in expected.items() if reported.get(key) != value}
    if mismatched:
        raise SystemExit(f"installed rustc does not match the lock: {mismatched}, expected {expected}")
    return {"path": item["destination"], "archive": item["filename"], "sha512": item["sha512"], **expected, "network": "none"}


ELF_MACHINES = {"amd64": 0x3E, "arm64": 0xB7}
GLIBC_SYSROOT_GLOB = "build/linux/debian_*-sysroot"


def elf_machine(path: Path) -> int | None:
    with path.open("rb") as stream:
        header = stream.read(20)
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    return int.from_bytes(header[18:20], "little")


def remove_foreign_host_binaries(source: Path, arch: str) -> dict:
    removed: dict[str, int] = {}

    def record(relative: Path) -> None:
        key = "/".join(relative.parts[:3])
        removed[key] = removed.get(key, 0) + 1

    for sysroot in sorted(source.glob(GLIBC_SYSROOT_GLOB)):
        for path in sysroot.rglob("*"):
            if path.is_file() and not path.is_symlink():
                record(path.relative_to(source))
        shutil.rmtree(sysroot)
    native = ELF_MACHINES[arch]
    for directory, _, files in os.walk(source):
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            machine = elf_machine(path)
            if machine is not None and machine != native:
                record(path.relative_to(source))
                path.unlink()
    return {"glibc_sysroots": GLIBC_SYSROOT_GLOB, "removed": dict(sorted(removed.items())), "network": "none"}


NATIVE_EXECUTABLES = ("typescript_native", "esbuild_native")


def install_native_executable(source: Path, inputs: Path, item: dict, arch: str) -> dict:
    destination = source / item["destination"]
    pin = item["chromium_pin"]
    if pin["contains"] not in (source / pin["path"]).read_text(encoding="utf-8"):
        raise SystemExit(f"{pin['path']} does not pin {pin['contains']!r}; {item['filename']} would not match Chromium")
    archive = inputs / item["filename"]
    if not archive.is_file():
        raise SystemExit(f"missing locked host tool {archive}")
    with tarfile.open(archive) as stream:
        member = stream.extractfile(item["member"])
        if member is None:
            raise SystemExit(f"{archive.name} has no {item['member']}")
        temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(member.read())
    temporary.chmod(0o755)
    if elf_machine(temporary) != ELF_MACHINES[arch]:
        raise SystemExit(f"{item['member']} in {archive.name} is not a {arch} executable")
    temporary.replace(destination)
    version = subprocess.run([str(destination), "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if version != item["version_output"]:
        raise SystemExit(f"{item['destination']} reports {version!r}, expected {item['version_output']!r}")
    return {"path": item["destination"], "archive": item["filename"], "sha512": item["sha512"], "version": version, "network": "none"}

