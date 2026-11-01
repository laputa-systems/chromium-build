#!/usr/bin/env python3
"""Verify and install a Helium archive without Docker or build state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import linux_runtime


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedArchive:
    archive: Path
    digest: str
    root_name: str
    version: str
    architecture: str
    manifest: dict


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def require_regular_file(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise InstallError(f"{label} is missing or not a regular file: {path}")


def sidecar_digest(archive: Path) -> str:
    sidecar = archive.with_name(archive.name + ".sha256")
    require_regular_file(sidecar, "archive checksum")
    fields = sidecar.read_text(encoding="utf-8").split()
    if not fields or len(fields[0]) != 64 or any(character not in "0123456789abcdef" for character in fields[0].lower()):
        raise InstallError(f"archive checksum is malformed: {sidecar}")
    actual = digest(archive)
    if fields[0].lower() != actual:
        raise InstallError(f"archive checksum does not match: {archive}")
    return actual


def acceptance(archive: Path, archive_digest: str) -> dict:
    path = archive.with_name(archive.name + ".acceptance.json")
    require_regular_file(path, "archive acceptance sidecar")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != 1 or report.get("status") != "complete":
        raise InstallError("archive acceptance report is incomplete or has an unsupported schema")
    if report.get("product") != "helium":
        raise InstallError(f"archive acceptance is for {report.get('product')!r}, not helium")
    if report.get("archive_sha256") != archive_digest:
        raise InstallError("archive acceptance does not bind this archive digest")
    if report.get("archive") not in (None, archive.name):
        raise InstallError("archive acceptance names a different archive")
    if not isinstance(report.get("product_version"), str) or not report["product_version"]:
        raise InstallError("archive acceptance has no product version")
    if report.get("architecture") != "amd64":
        raise InstallError(f"archive acceptance architecture is {report.get('architecture')!r}, not amd64")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks or any(value is not True for value in checks.values()):
        raise InstallError("archive acceptance has no successful package checks")
    return report


def safe_member(member: tarfile.TarInfo, root_name: str) -> PurePosixPath:
    relative = PurePosixPath(member.name)
    if member.name == root_name and member.isdir():
        return relative
    if relative.is_absolute() or ".." in relative.parts or not member.name.startswith(root_name + "/"):
        raise InstallError(f"archive entry escapes its root: {member.name}")
    if member.isdev() or member.isfifo():
        raise InstallError(f"archive contains an unsupported special file: {member.name}")
    if member.issym() or member.islnk():
        link = PurePosixPath(member.linkname)
        if link.is_absolute() or ".." in link.parts:
            raise InstallError(f"archive link escapes its root: {member.name} -> {member.linkname}")
        base = relative.parent if member.issym() else PurePosixPath(root_name)
        resolved = (base / link)
        normalized = os.path.normpath(str(resolved))
        if normalized.startswith("..") or not normalized.startswith(root_name + "/"):
            raise InstallError(f"archive link escapes its root: {member.name} -> {member.linkname}")
    return relative


def manifest_for(root: Path, report: dict, archive: Path) -> dict:
    manifest_path = root / "package-manifest.json"
    require_regular_file(manifest_path, "package manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("status") != "complete":
        raise InstallError("package manifest is incomplete or has an unsupported schema")
    if manifest.get("product") != "helium":
        raise InstallError(f"package manifest is for {manifest.get('product')!r}, not helium")
    if manifest.get("product_version") != report["product_version"]:
        raise InstallError("package manifest version does not match the acceptance report")
    if manifest.get("architecture") != report["architecture"]:
        raise InstallError("package manifest architecture does not match the acceptance report")
    if manifest.get("launcher") != "helium-wrapper" or manifest.get("executable") != "helium":
        raise InstallError("package manifest has the wrong Helium entry points")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise InstallError("package manifest has no files")
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise InstallError("package manifest has an invalid file entry")
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative) in names:
            raise InstallError(f"package manifest has an unsafe or duplicate path: {entry['path']}")
        names.add(str(relative))
        path = root / relative
        require_regular_file(path, f"package file {relative}")
        if digest(path) != entry.get("sha256"):
            raise InstallError(f"package file digest does not match: {relative}")
    for required in ("helium", "helium-wrapper", "package-manifest.json"):
        if required != "package-manifest.json" and required not in names:
            raise InstallError(f"package manifest does not list {required}")
    actual = set()
    for directory, _, files in os.walk(root):
        for name in files:
            actual.add(str((Path(directory) / name).relative_to(root).as_posix()))
    unexpected = sorted(actual - names - {"package-manifest.json"})
    if unexpected:
        raise InstallError(f"package contains files outside its manifest: {unexpected[:5]}")
    try:
        linux_runtime.audit_tree(
            root,
            manifest["architecture"],
            gnu_runtime_allowed=linux_runtime.graphics_gnu_allowlist(root),
        )
    except linux_runtime.RuntimeAuditError as error:
        raise InstallError(str(error)) from error
    return manifest


@contextmanager
def verified_archive(archive: Path):
    require_regular_file(archive, "archive")
    archive_digest = sidecar_digest(archive)
    report = acceptance(archive, archive_digest)
    root_name = report.get("archive_root") or archive.name.removesuffix(".tar.xz")
    with tempfile.TemporaryDirectory(prefix="helium-verify-", dir=os.environ.get("TMPDIR") or None) as directory:
        root = Path(directory) / root_name
        root.mkdir()
        names = set()
        with tarfile.open(archive, "r:xz") as bundle:
            members = bundle.getmembers()
            for member in members:
                relative = safe_member(member, root_name)
                if str(relative) in names:
                    raise InstallError(f"archive contains a duplicate entry: {member.name}")
                names.add(str(relative))
            try:
                bundle.extractall(root.parent, filter="data")
            except TypeError:
                bundle.extractall(root.parent)
        manifest = manifest_for(root, report, archive)
        yield VerifiedArchive(archive, archive_digest, root_name, report["product_version"], report["architecture"], manifest), root


def owned(path: Path, euid: int) -> bool:
    try:
        return path.stat().st_uid == euid
    except OSError:
        return False


def validate_managed_directory(path: Path, label: str) -> None:
    if path.is_symlink():
        raise InstallError(f"{label} is a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise InstallError(f"{label} is not a directory: {path}")
    if path.is_dir() and not owned(path, os.geteuid()):
        raise InstallError(f"{label} is not owned by the current user: {path}")


def validate_prefix(path: Path) -> None:
    if path.is_symlink():
        target = path.resolve()
        versions = Path(str(path) + ".versions").resolve()
        if not target.is_relative_to(versions):
            raise InstallError(f"prefix symlink points outside the owned versions directory: {path} -> {target}")
        if not target.is_dir() or not owned(target, os.geteuid()):
            raise InstallError(f"prefix symlink target is not an owned directory: {target}")
    elif path.exists():
        raise InstallError(f"prefix exists but is not a managed symlink: {path}")


def validate_previous(path: Path, versions: Path) -> None:
    if path.is_symlink():
        target = path.resolve()
        if not target.is_dir() or not target.is_relative_to(versions.resolve()) or not owned(target, os.geteuid()):
            raise InstallError(f"previous slot is not an owned version: {target}")
    elif path.exists():
        raise InstallError(f"previous slot is not a managed symlink: {path}")


def validate_launcher(path: Path, prefix: Path) -> None:
    if path.is_symlink():
        target = path.resolve()
        expected = (prefix / "helium-wrapper").resolve()
        if target != expected:
            raise InstallError(f"launcher points to a foreign target: {path} -> {target}")
    elif path.exists():
        raise InstallError(f"launcher exists and is not managed: {path}")


def validate_usr_bin(home: Path, bin_dir: Path) -> None:
    usr_bin = home / "usr/bin"
    if usr_bin.is_symlink() and usr_bin.resolve() != bin_dir.resolve():
        raise InstallError(f"usr/bin points to a different managed path: {usr_bin} -> {usr_bin.resolve()}")


def validate_parent(path: Path, label: str) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise InstallError(f"{label} parent does not exist: {parent}")
    if not owned(parent, os.geteuid()):
        raise InstallError(f"{label} parent is not owned by the current user: {parent}")
    if path.parent.is_symlink():
        raise InstallError(f"{label} parent is a symlink: {parent}")


def probe(path: Path, home: Path, environment: dict[str, str], probe_command: Path | None) -> None:
    command = [str(probe_command), str(path), "--version"] if probe_command else [str(path), "--version"]
    env = {
        key: value
        for key, value in environment.items()
        if key in {"PATH", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DISPLAY", "LANG", "LC_ALL"}
    }
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"))
    result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise InstallError(f"installed browser probe failed ({result.returncode}): {detail}")


def symlink_atomic(target: Path, link: Path) -> None:
    temporary = link.with_name(f".{link.name}.tmp-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def remove_owned(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def install(archive: Path, prefix: Path, bin_dir: Path, home: Path, probe_command: Path | None) -> None:
    for path, label in ((prefix, "prefix"), (bin_dir, "launcher directory")):
        if not path.is_absolute():
            raise InstallError(f"{label} must be absolute: {path}")
    if bin_dir == home / ".local/bin":
        validate_usr_bin(home, bin_dir)
    for directory in (prefix.parent, bin_dir):
        if not directory.exists():
            directory.mkdir(parents=True, mode=0o755)
    validate_parent(prefix, "prefix")
    validate_parent(bin_dir / "helium", "launcher")
    versions = Path(str(prefix) + ".versions")
    validate_managed_directory(versions, "versions directory")
    validate_previous(Path(str(prefix) + ".previous"), versions)
    validate_prefix(prefix)
    validate_launcher(bin_dir / "helium", prefix)
    if not (prefix.is_symlink() or not prefix.exists()):
        raise InstallError(f"prefix is not a managed installation: {prefix}")
    with verified_archive(archive) as (package, source):
        slot_name = f"{package.version}-{package.digest[:12]}"
        versions = Path(str(prefix) + ".versions")
        versions.mkdir(mode=0o755, exist_ok=True)
        slot = versions / slot_name
        created_slot = not slot.exists()
        if slot.exists():
            existing = json.loads((slot / "package-manifest.json").read_text(encoding="utf-8"))
            if existing.get("files") != package.manifest.get("files"):
                raise InstallError(f"existing version slot has different contents: {slot}")
        else:
            temporary = versions / f".{slot_name}.tmp-{os.getpid()}"
            remove_owned(temporary)
            shutil.copytree(source, temporary, symlinks=True)
            for entry in package.manifest["files"]:
                path = temporary / entry["path"]
                require_regular_file(path, f"installed file {entry['path']}")
                if digest(path) != entry["sha256"]:
                    raise InstallError(f"installed file digest does not match: {entry['path']}")
            os.replace(temporary, slot)
        try:
            probe(slot / "helium-wrapper", home / ".install-probe", os.environ, probe_command)
        except BaseException:
            if created_slot:
                remove_owned(slot)
            raise
        previous = Path(str(prefix) + ".previous")
        old_target = prefix.resolve() if prefix.is_symlink() else None
        if old_target is not None and old_target != slot:
            symlink_atomic(old_target, previous)
        launcher = bin_dir / "helium"
        old_launcher = launcher.resolve() if launcher.is_symlink() else None
        symlink_atomic(slot, prefix)
        symlink_atomic(prefix / "helium-wrapper", launcher)
        try:
            probe(launcher, home / ".install-probe-final", os.environ, probe_command)
        except BaseException:
            if old_target is not None:
                symlink_atomic(old_target, prefix)
            else:
                prefix.unlink(missing_ok=True)
            if previous.is_symlink() and previous.resolve() == old_target:
                previous.unlink(missing_ok=True)
            if old_launcher is not None:
                symlink_atomic(old_launcher, launcher)
            else:
                launcher.unlink(missing_ok=True)
            if created_slot and (not prefix.is_symlink() or prefix.resolve() != slot):
                remove_owned(slot)
            raise


def rollback(prefix: Path, bin_dir: Path, home: Path, probe_command: Path | None) -> None:
    if bin_dir == home / ".local/bin":
        validate_usr_bin(home, bin_dir)
    previous = Path(str(prefix) + ".previous")
    if not previous.is_symlink():
        raise InstallError(f"no previous owned slot is available: {previous}")
    previous_target = previous.resolve()
    versions = Path(str(prefix) + ".versions").resolve()
    if not previous_target.is_dir() or not previous_target.is_relative_to(versions) or not owned(previous_target, os.geteuid()):
        raise InstallError(f"previous slot is not an owned version: {previous_target}")
    current = prefix.resolve() if prefix.is_symlink() else None
    validate_launcher(bin_dir / "helium", prefix)
    symlink_atomic(previous_target, prefix)
    launcher = bin_dir / "helium"
    symlink_atomic(prefix / "helium-wrapper", launcher)
    try:
        probe(launcher, home / ".rollback-probe", os.environ, probe_command)
    except BaseException:
        if current is not None:
            symlink_atomic(current, prefix)
        symlink_atomic(prefix / "helium-wrapper", launcher)
        raise
    if current is not None and current != previous_target:
        symlink_atomic(current, previous)


def verify_extract(archive: Path, destination: Path) -> None:
    if not destination.is_absolute():
        raise InstallError(f"destination must be absolute: {destination}")
    if destination.exists():
        if not destination.is_dir() or not owned(destination, os.geteuid()) or any(destination.iterdir()):
            raise InstallError(f"destination is not an owned empty directory: {destination}")
    validate_parent(destination, "destination")
    with verified_archive(archive) as (_, source):
        if destination.exists():
            destination.rmdir()
        shutil.move(str(source), str(destination))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--verify-extract", type=Path)
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--bin-dir", type=Path)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("HOME", "")))
    parser.add_argument("--arch", choices=("amd64",), default=os.environ.get("CHROMIUM_ARCH", "amd64"))
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--probe", type=Path)
    args = parser.parse_args()
    try:
        if args.verify_extract is not None:
            if args.archive is None:
                raise InstallError("--verify-extract requires --archive")
            verify_extract(args.archive, args.verify_extract)
            return 0
        if args.rollback and args.archive is not None:
            raise InstallError("--archive and --rollback are mutually exclusive")
        if not args.rollback and args.archive is None:
            raise InstallError("--archive is required unless --rollback is selected")
        home = args.home
        prefix = args.prefix or home / ".local/opt/helium-wayland-release"
        bin_dir = args.bin_dir or home / ".local/bin"
        if args.rollback:
            rollback(prefix, bin_dir, home, args.probe)
        else:
            install(args.archive, prefix, bin_dir, home, args.probe)
        return 0
    except (InstallError, OSError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"install-runtime: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
