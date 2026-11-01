"""Collect and audit the ELF closure of a staged Linux browser package."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class RuntimeAuditError(RuntimeError):
    pass


MUSL_INTERPRETERS = {
    "amd64": "/lib/ld-musl-x86_64.so.1",
    "arm64": "/lib/ld-musl-aarch64.so.1",
}
MUSL_LIBRARIES = {
    "libc.musl-x86_64.so.1",
    "libc.musl-aarch64.so.1",
    "ld-musl-x86_64.so.1",
    "ld-musl-aarch64.so.1",
}
MACHINES = {
    "amd64": {"Advanced Micro Devices X86-64", "x86-64"},
    "arm64": {"AArch64"},
}
GLIBC_LIBRARIES = {
    "libc.so.6",
    "libm.so.6",
    "libpthread.so.0",
    "libdl.so.2",
    "librt.so.1",
    "libresolv.so.2",
    "libutil.so.1",
    "libnsl.so.1",
    "libanl.so.1",
}
GNU_RUNTIME_LIBRARIES = {"libstdc++.so.6", "libgcc_s.so.1"}
GRAPHICS_GNU_PREFIXES = (
    "libEGL.so.1",
    "libgbm.so.1",
    "libgallium-",
    "libLLVM.so.",
    "libSPIRV-Tools.so",
    "libglycin-",
    "libstdc++.so.6",
)
READELF = os.environ.get("LLVM_READELF", "llvm-readelf")


@dataclass(frozen=True)
class ElfInfo:
    path: Path
    relative: PurePosixPath
    elf_class: str
    machine: str
    elf_type: str
    interpreter: str | None
    needed: tuple[str, ...]
    soname: str | None
    rpath: tuple[str, ...]
    runpath: tuple[str, ...]


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError:
        return False


def readelf_output(path: Path, readelf: str = READELF) -> str:
    try:
        result = subprocess.run(
            [readelf, "-h", "-l", "-d", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeAuditError(f"cannot run {readelf}: {error}") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeAuditError(f"cannot read ELF {path}: {detail}")
    return result.stdout


def parse_readelf(path: Path, relative: PurePosixPath, output: str) -> ElfInfo:
    def field(label: str) -> str:
        match = re.search(rf"^\s*{label}:\s+(.+?)\s*$", output, re.MULTILINE)
        if not match:
            raise RuntimeAuditError(f"{path}: readelf output has no {label}")
        return match.group(1)

    interpreter_match = re.search(r"\[Requesting program interpreter: ([^\]]+)\]", output)
    needed = tuple(re.findall(r"\(NEEDED\).*?Shared library: \[([^\]]+)\]", output))
    soname_match = re.search(r"\(SONAME\).*?Library soname: \[([^\]]+)\]", output)
    rpath_match = re.search(r"\(RPATH\).*?Library rpath: \[([^\]]*)\]", output)
    runpath_match = re.search(r"\(RUNPATH\).*?Library runpath: \[([^\]]*)\]", output)

    def entries(match: re.Match[str] | None) -> tuple[str, ...]:
        if match is None or not match.group(1):
            return ()
        return tuple(match.group(1).split(":"))

    return ElfInfo(
        path=path,
        relative=relative,
        elf_class=field("Class"),
        machine=field("Machine"),
        elf_type=field("Type"),
        interpreter=interpreter_match.group(1) if interpreter_match else None,
        needed=needed,
        soname=soname_match.group(1) if soname_match else None,
        rpath=entries(rpath_match),
        runpath=entries(runpath_match),
    )


def inspect_elf(path: Path, root: Path, readelf: str = READELF) -> ElfInfo:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    return parse_readelf(path, relative, readelf_output(path, readelf))


def iter_elfs(root: Path) -> list[Path]:
    resolved_root = root.resolve()
    found = []
    for directory, names, files in os.walk(root, followlinks=False):
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                target = candidate.resolve()
                if not target.is_relative_to(resolved_root):
                    raise RuntimeAuditError(f"directory symlink escapes the runtime root: {candidate} -> {target}")
        for name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                target = candidate.resolve()
                if not target.is_relative_to(resolved_root):
                    raise RuntimeAuditError(f"file symlink escapes the runtime root: {candidate} -> {target}")
            if candidate.is_file() and is_elf(candidate):
                found.append(candidate)
    return sorted(found)


def graphics_gnu_allowlist(root: Path) -> set[PurePosixPath]:
    lib = root / "lib"
    if not lib.is_dir():
        return set()
    return {
        PurePosixPath("lib") / path.name
        for path in lib.iterdir()
        if path.name.startswith(GRAPHICS_GNU_PREFIXES)
    }


def validate_relative_path(value: str) -> PurePosixPath:
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise RuntimeAuditError(f"invalid relative path: {value!r}")
    if "\\" in value or "\x00" in value:
        raise RuntimeAuditError(f"invalid relative path: {value!r}")
    return pure


def read_runtime_deps(path: Path) -> list[PurePosixPath]:
    if not path.is_file():
        raise RuntimeAuditError(f"missing GN runtime dependency list: {path}")
    entries = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.strip()
        if not value:
            continue
        try:
            entries.append(validate_relative_path(value))
        except RuntimeAuditError as error:
            raise RuntimeAuditError(f"{path}:{number}: {error}") from error
    if not entries:
        raise RuntimeAuditError(f"GN runtime dependency list is empty: {path}")
    return entries


def resolved_source(source: Path, allowed_roots: tuple[Path, ...]) -> Path:
    resolved = source.resolve(strict=True)
    if not any(resolved.is_relative_to(root.resolve()) for root in allowed_roots):
        raise RuntimeAuditError(f"runtime source escapes the allowed roots: {source} -> {resolved}")
    if not resolved.is_file():
        raise RuntimeAuditError(f"runtime source is not a file: {source}")
    return resolved


def _expand_origin(entry: str, elf: ElfInfo) -> Path:
    if "$" in entry.replace("$ORIGIN", "").replace("${ORIGIN}", ""):
        raise RuntimeAuditError(f"{elf.relative}: unsupported dynamic path variable: {entry}")
    value = entry.replace("${ORIGIN}", str(elf.path.parent)).replace("$ORIGIN", str(elf.path.parent))
    if not os.path.isabs(value):
        value = str(elf.path.parent / value)
    return Path(value)


def _dynamic_paths(elf: ElfInfo, root: Path, forbidden_roots: tuple[Path, ...]) -> list[Path]:
    directories = []
    for entry in (*elf.rpath, *elf.runpath):
        if not entry:
            continue
        if os.path.isabs(entry):
            if any(Path(entry).resolve().is_relative_to(path.resolve()) for path in forbidden_roots):
                raise RuntimeAuditError(f"{elf.relative}: absolute build dynamic path is not relocatable: {entry}")
            continue
        expanded = _expand_origin(entry, elf).resolve()
        if not expanded.is_relative_to(root.resolve()):
            raise RuntimeAuditError(f"{elf.relative}: dynamic path escapes the runtime root: {entry}")
        directories.append(expanded)
    return directories


def _resolve_needed(
    elf: ElfInfo,
    name: str,
    root: Path,
    search_dirs: tuple[Path, ...],
    forbidden_roots: tuple[Path, ...],
) -> Path | None:
    if "/" in name or entry_is_unsafe(name):
        raise RuntimeAuditError(f"{elf.relative}: unsafe NEEDED entry: {name}")
    candidates = []
    for directory in (elf.path.parent, root / "lib", root, *search_dirs, *_dynamic_paths(elf, root, forbidden_roots)):
        candidate = directory / name
        if candidate.exists():
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise RuntimeAuditError(f"{elf.relative}: {name} resolves outside the runtime root: {resolved}")
            candidates.append(resolved)
    unique = sorted(set(candidates))
    if len(unique) > 1:
        raise RuntimeAuditError(
            f"{elf.relative}: {name} has conflicting providers: {', '.join(str(path) for path in unique)}"
        )
    return unique[0] if unique else None


def entry_is_unsafe(name: str) -> bool:
    pure = PurePosixPath(name)
    return pure.is_absolute() or ".." in pure.parts or name in {"", ".", ".."}


def audit_tree(
    root: Path,
    architecture: str,
    *,
    search_dirs: tuple[Path, ...] = (),
    readelf: str = READELF,
    gnu_runtime_allowed: set[PurePosixPath] | None = None,
    forbidden_path_roots: tuple[Path, ...] = (Path("/work"), Path("/opt/chromium")),
) -> dict[str, object]:
    if architecture not in MUSL_INTERPRETERS:
        raise RuntimeAuditError(f"unsupported runtime architecture: {architecture}")
    root = root.resolve()
    if not root.is_dir():
        raise RuntimeAuditError(f"missing runtime root: {root}")
    expected_interpreter = MUSL_INTERPRETERS[architecture]
    expected_machine = MACHINES[architecture]
    allowed_gnu = gnu_runtime_allowed or set()
    failures: list[str] = []
    infos = []
    by_soname: dict[str, Path] = {}
    for path in iter_elfs(root):
        try:
            info = inspect_elf(path, root, readelf)
        except RuntimeAuditError as error:
            failures.append(str(error))
            continue
        infos.append(info)
        if info.elf_class != "ELF64":
            failures.append(f"{info.relative}: expected ELF64, found {info.elf_class}")
        if info.machine not in expected_machine:
            failures.append(f"{info.relative}: expected {architecture} ELF machine, found {info.machine}")
        if info.interpreter is not None and info.interpreter != expected_interpreter:
            failures.append(f"{info.relative}: wrong ELF interpreter {info.interpreter}")
        if info.soname:
            previous = by_soname.get(info.soname)
            if previous is not None and previous != info.path:
                failures.append(f"{info.relative}: SONAME {info.soname} is also provided by {previous.relative_to(root)}")
            by_soname[info.soname] = info.path
        for name in info.needed:
            if name in GLIBC_LIBRARIES or name.startswith("ld-linux"):
                failures.append(f"{info.relative}: requires glibc library {name}")
                continue
            if name in MUSL_LIBRARIES:
                continue
            if name in GNU_RUNTIME_LIBRARIES and info.relative not in allowed_gnu:
                failures.append(f"{info.relative}: requires GNU C++ runtime {name} outside the allowed closure")
                continue
            try:
                resolved = _resolve_needed(info, name, root, search_dirs, forbidden_path_roots)
            except RuntimeAuditError as error:
                failures.append(str(error))
                continue
            if resolved is None:
                failures.append(f"{info.relative}: missing runtime library {name}")
    report = {
        "status": "failed" if failures else "complete",
        "architecture": architecture,
        "root": str(root),
        "files": [
            {
                "path": str(info.relative),
                "class": info.elf_class,
                "machine": info.machine,
                "type": info.elf_type,
                "interpreter": info.interpreter,
                "needed": list(info.needed),
                "soname": info.soname,
                "rpath": list(info.rpath),
                "runpath": list(info.runpath),
            }
            for info in infos
        ],
        "gnu_runtime_edges": [
            {"path": str(info.relative), "libraries": [name for name in info.needed if name in GNU_RUNTIME_LIBRARIES]}
            for info in infos
            if any(name in GNU_RUNTIME_LIBRARIES for name in info.needed)
        ],
        "failures": failures,
    }
    if failures:
        raise RuntimeAuditError("runtime audit failed:\n  " + "\n  ".join(failures))
    return report
