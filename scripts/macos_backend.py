#!/usr/bin/env python3
"""Native macOS arm64 orchestration for the Chromium build.

The Linux entrypoint deliberately remains Docker-specific.  This module owns
the smaller native boundary: an absolute, builder-owned work root, a resolved
macOS input lock, offline preparation/configuration/build phases, and
candidate-versus-accepted bundle publication.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform as host_platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Iterator, Mapping, Optional, Sequence
import urllib.error
import urllib.parse
import urllib.request
import zipfile


WORK_PREFIX = Path("/Volumes/dev")
MIN_MACOS = (26, 0, 0)
MIN_LLVM = (23, 1, 0)
PREFERRED_LLVM = (23, 1, 1)
# macos-26 hosted runners currently expose about 92 GiB free on /Volumes/dev.
# Keep a 12 GiB headroom margin while allowing the real fetch/configure/build
# phases to be the capacity proof rather than rejecting every hosted runner.
FULL_BUILD_MIN_FREE_GB = 80
POST_FETCH_MIN_FREE_GB = 40
RUST_TOOLCHAIN = "nightly-2026-09-15"
DEFAULT_PROFILE = "macos-release"
LOCK_RELATIVE = Path("config/macos.inputs.lock")
OWNERSHIP_SCHEMA = 1
VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?(?!\d)")
RELEASE_RE = re.compile(r"^(\d+\.\d+\.\d+\.\d+)-(\d+)$")
COMPILER_CACHE_NAMES = ("ccache", "sccache")
COMPILER_CACHE_DISABLED = {"off", "none", "disabled"}
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
RANGE_DOWNLOAD_THRESHOLD_BYTES = 256 * 1024 * 1024
RANGE_DOWNLOAD_CHUNK_BYTES = 64 * 1024 * 1024
DEFAULT_INPUT_DOWNLOAD_WORKERS = 16
MAX_INPUT_DOWNLOAD_WORKERS = 32


class MacOSFailure(RuntimeError):
    """An expected native-build failure with an actionable message."""


def fail(message: str) -> None:
    raise MacOSFailure(message)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def is_within(path: Path, root: Path) -> bool:
    """Use canonical path components, never a lexical string prefix."""

    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def canonical_path(raw: str | Path, label: str, *, must_exist: bool = False) -> Path:
    value = str(raw)
    if not value:
        fail(f"{label} is empty")
    if "\x00" in value:
        fail(f"{label} contains a NUL byte")
    if "~" in value:
        fail(f"{label} must not use ~; spell it as an absolute path under /Volumes/dev")
    if " " in value or "\t" in value or "\n" in value:
        fail(f"{label} contains whitespace, which Chromium does not support here: {value}")
    path = Path(value)
    if not path.is_absolute():
        fail(f"{label} must be absolute: {value}")
    if must_exist and not path.exists():
        fail(f"{label} does not exist: {path}")
    return path.resolve(strict=False)


def validate_work_root(raw: str | Path, repo_root: Path) -> Path:
    root = canonical_path(raw, "CHROMIUM_WORK_ROOT")
    if not is_within(root, WORK_PREFIX):
        fail(f"CHROMIUM_WORK_ROOT must be under /Volumes/dev: {root}")
    if root in (WORK_PREFIX, Path("/"), repo_root, repo_root.parent):
        fail(f"refusing dangerous work root: {root}")
    if root == Path("/tmp") or is_within(root, Path("/tmp")):
        fail(f"/tmp is forbidden; choose a work root under /Volumes/dev: {root}")
    if root.exists() and root.is_symlink():
        fail(f"work root must not be a symlink: {root}")
    return root


def validate_owned_path(path: Path, root: Path, label: str) -> Path:
    resolved = canonical_path(path, label)
    if not is_within(resolved, root) or resolved == root:
        fail(f"{label} escapes the owned work root: {resolved}")
    return resolved


def normalize_arch(value: str) -> str:
    normalized = {"aarch64": "arm64", "arm64": "arm64"}.get(value.lower())
    if normalized != "arm64":
        fail(f"macOS supports native arm64 only, got {value}")
    return normalized


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.search(value)
    if not match:
        fail(f"could not parse a semantic tool version from: {value.strip()}")
    return tuple(int(match.group(index) or 0) for index in range(1, 4))  # type: ignore[return-value]


def version_string(value: Sequence[int]) -> str:
    return ".".join(str(part) for part in value)


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_path(name: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    return shutil.which(name, path=(env or os.environ).get("PATH"))


def compiler_cache_report(environment: Mapping[str, str]) -> dict[str, Any]:
    """Return the cache selection made for a controlled child environment."""

    status = environment.get("CHROMIUM_COMPILER_CACHE_STATUS", "disabled")
    report: dict[str, Any] = {
        "status": status,
        "requested": environment.get("CHROMIUM_COMPILER_CACHE_REQUESTED", "auto"),
        "root": environment.get("CHROMIUM_COMPILER_CACHE_ROOT", ""),
    }
    if status == "enabled":
        report.update(
            {
                "kind": environment.get("CHROMIUM_COMPILER_CACHE_KIND", ""),
                "path": environment.get("CHROMIUM_COMPILER_CACHE_WRAPPER", ""),
                "version": environment.get("CHROMIUM_COMPILER_CACHE_VERSION", ""),
                "directory": environment.get("CCACHE_DIR")
                or environment.get("SCCACHE_DIR", ""),
            }
        )
    else:
        report["reason"] = environment.get(
            "CHROMIUM_COMPILER_CACHE_REASON", "no local compiler cache wrapper was found"
        )
    return report


def select_compiler_cache(
    layout: WorkLayout,
    environment: Mapping[str, str],
    *,
    requested: Optional[str] = None,
) -> dict[str, Any]:
    """Select an existing local compiler cache without changing host packages.

    The selected wrapper is written into GN as an absolute path so the
    controlled PATH cannot accidentally pick up a different cache or a remote
    execution client.  The cache itself is always rooted under the work root.
    """

    choice = (requested or environment.get("MACOS_COMPILER_CACHE", "auto")).strip().lower()
    if choice in COMPILER_CACHE_DISABLED:
        return {
            "status": "disabled",
            "requested": choice,
            "reason": f"disabled by MACOS_COMPILER_CACHE={choice}",
            "root": str(layout.cache / "compiler"),
        }
    if choice not in {"auto", *COMPILER_CACHE_NAMES}:
        fail(
            "MACOS_COMPILER_CACHE must be auto, off, ccache, or sccache; "
            f"got {choice!r}"
        )

    names = (choice,) if choice in COMPILER_CACHE_NAMES else COMPILER_CACHE_NAMES
    inspected: list[str] = []
    for name in names:
        candidate_paths = [
            layout.tools / "bin" / name,
            Path("/opt/homebrew/bin") / name,
            Path("/opt/homebrew/opt") / name / "bin" / name,
            Path("/usr/local/bin") / name,
            Path("/usr/local/opt") / name / "bin" / name,
            Path("/usr/bin") / name,
        ]
        resolved_from_path = command_path(name, environment)
        if resolved_from_path:
            candidate_paths.append(Path(resolved_from_path))
        seen: set[str] = set()
        for raw_path in candidate_paths:
            path = raw_path.resolve(strict=False)
            if str(path) in seen:
                continue
            seen.add(str(path))
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            if " " in str(path) or "\t" in str(path) or "\n" in str(path):
                fail(f"compiler cache executable contains whitespace: {path}")
            if not (
                is_within(path, WORK_PREFIX)
                or is_within(path, Path("/opt/homebrew"))
                or is_within(path, Path("/usr/local"))
                or is_within(path, Path("/usr/bin"))
            ):
                continue
            inspected.append(str(path))
            version = command_output([str(path), "--version"], env=environment, check=False)
            if version.returncode:
                continue
            cache_directory = layout.cache / "compiler" / name
            cache_temp = cache_directory / "tmp"
            cache_directory.mkdir(parents=True, exist_ok=True)
            cache_temp.mkdir(parents=True, exist_ok=True)
            return {
                "status": "enabled",
                "requested": choice,
                "kind": name,
                "path": str(path),
                "version": (version.stdout or version.stderr or "").strip().splitlines()[0]
                if (version.stdout or version.stderr).strip()
                else "unknown",
                "root": str(layout.cache / "compiler"),
                "directory": str(cache_directory),
                "temporary_directory": str(cache_temp),
            }

    reason = f"no usable local {choice} compiler cache wrapper was found" if choice != "auto" else (
        "no usable local ccache or sccache compiler wrapper was found"
    )
    return {
        "status": "disabled",
        "requested": choice,
        "reason": reason,
        "root": str(layout.cache / "compiler"),
        "inspected": inspected,
    }


def command_output(
    command: Sequence[str],
    *,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env else None,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"command failed to start: {' '.join(command)}: {error}")
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail[-1200:]}" if detail else ""
        fail(f"command failed ({result.returncode}): {' '.join(command)}{suffix}")
    return result


def stream_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Optional[Path],
    log_path: Path,
    timeout: Optional[float] = None,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd) if cwd else None,
                env=dict(env),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="")
                log.write(line)
                log.flush()
                if timeout is not None and time.monotonic() - started > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    fail(f"command timed out; see {log_path}")
            status = process.wait()
    except OSError as error:
        fail(f"command failed: {' '.join(command)}: {error}")
    if status:
        fail(f"command failed ({status}); see {log_path}")


class WorkLayout:
    """Canonical directories owned by one macOS work root."""

    def __init__(self, root: Path, repo_root: Path):
        self.root = validate_work_root(root, repo_root)
        self.repo_root = repo_root
        self.metadata = self.root / "metadata"
        self.inputs = self.root / "inputs"
        self.source = self.root / "src"
        self.output = self.root / "out" / DEFAULT_PROFILE
        self.stage = self.root / "stage"
        self.candidates = self.stage / "candidates"
        self.accepted = self.stage / "accepted"
        self.release = self.root / "release"
        self.logs = self.root / "logs"
        self.tmp = self.root / "tmp"
        self.home = self.root / "home"
        self.cache = self.root / "cache"
        self.tools = self.root / "tools"
        self.harness = self.root / "harness"
        self.lock_dir = self.root / "locks"
        self.marker = self.root / ".chromium-build-owned.json"

    @property
    def all_directories(self) -> tuple[Path, ...]:
        return (
            self.metadata,
            self.inputs,
            self.source.parent,
            self.output,
            self.candidates,
            self.accepted,
            self.release,
            self.logs,
            self.tmp,
            self.home,
            self.cache,
            self.tools,
            self.harness,
            self.lock_dir,
        )

    def ensure(self) -> None:
        if self.root.exists() and not self.root.is_dir():
            fail(f"work root is not a directory: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.marker.exists():
            try:
                marker = json.loads(self.marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                fail(f"cannot read work-root ownership marker {self.marker}: {error}")
            if marker.get("schema") != OWNERSHIP_SCHEMA or marker.get("root") != str(self.root):
                fail(f"work root ownership marker does not match {self.root}")
        else:
            entries = [item for item in self.root.iterdir() if item.name != self.marker.name]
            if entries:
                fail(f"refusing to claim non-empty unmarked work root: {self.root}")
            atomic_json(
                self.marker,
                {
                    "schema": OWNERSHIP_SCHEMA,
                    "root": str(self.root),
                    "repository": str(self.repo_root),
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
            )
        for directory in self.all_directories:
            directory.mkdir(parents=True, exist_ok=True)

    def assert_owned(self, path: Path, label: str) -> Path:
        return validate_owned_path(path, self.root, label)


@contextmanager
def work_lock(layout: WorkLayout) -> Iterator[None]:
    layout.lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = layout.lock_dir / "build.lock"
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except OSError as error:
            fail(f"cannot acquire work-root lock {lock_path}: {error}")
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def isolated_environment(
    layout: WorkLayout,
    *,
    network: str = "none",
    extra: Optional[Mapping[str, str]] = None,
    llvm_root: Optional[Path] = None,
    compiler_cache: Optional[str] = None,
) -> dict[str, str]:
    """Build the child environment before invoking any project tool."""

    layout.ensure()
    environment = dict(os.environ)
    environment["DEVELOPER_DIR"] = environment.get("DEVELOPER_DIR") or (
        "/Applications/Xcode.app/Contents/Developer"
    )
    for key in tuple(environment):
        if key.startswith(
            ("RBE_", "GOMA_", "RECLIENT_", "SISO_", "SCCACHE_", "CCACHE_", "DISTCC_")
        ) or key.startswith("CHROMIUM_COMPILER_CACHE_"):
            environment.pop(key, None)
    for key in (
        "CC",
        "CXX",
        "CPP",
        "CFLAGS",
        "CXXFLAGS",
        "LDFLAGS",
        "AR",
        "AS",
        "LD",
        "NM",
        "OBJCOPY",
        "RANLIB",
        "STRIP",
        "DYLD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_FRAMEWORK_PATH",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "PIP_USER",
    ):
        environment.pop(key, None)

    environment.update(
        {
            "HOME": str(layout.home),
            "TMPDIR": str(layout.tmp),
            "TMP": str(layout.tmp),
            "TEMP": str(layout.tmp),
            "XDG_CONFIG_HOME": str(layout.home / ".config"),
            "XDG_CACHE_HOME": str(layout.cache / "xdg"),
            "XDG_DATA_HOME": str(layout.home / ".local" / "share"),
            "XDG_STATE_HOME": str(layout.home / ".local" / "state"),
            "CARGO_HOME": str(layout.cache / "cargo"),
            "RUSTUP_HOME": str(layout.tools / "rustup"),
            "CIPD_CACHE_DIR": str(layout.cache / "cipd"),
            "PIP_CACHE_DIR": str(layout.cache / "pip"),
            "PYTHONPYCACHEPREFIX": str(layout.cache / "python"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(layout.home / ".gitconfig"),
            "GIT_TERMINAL_PROMPT": "0",
            "DEPOT_TOOLS_UPDATE": "0",
            "DEPOT_TOOLS_METRICS": "0",
            "VPYTHON_BYPASS": "0",
            "PYTHONNOUSERSITE": "1",
            "NETWORK_MODE": network,
            "CHROMIUM_BUILD_ROOT": str(layout.repo_root),
            "CHROMIUM_WORK_ROOT": str(layout.root),
            "CHROMIUM_METADATA_ROOT": str(layout.metadata),
            "CHROMIUM_INPUTS_ROOT": str(layout.inputs),
            "CHROMIUM_SOURCE_ROOT": str(layout.source),
            "CHROMIUM_PROFILE": DEFAULT_PROFILE,
            "CHROMIUM_ARCH": "arm64",
            "RUSTUP_TOOLCHAIN": RUST_TOOLCHAIN,
            "CHROMIUM_COMPILER_CACHE_ROOT": str(layout.cache / "compiler"),
        }
    )
    selected_path = [str(layout.tools / "bin"), str(layout.tools / "depot_tools")]
    if llvm_root:
        selected_path.append(str(llvm_root / "bin"))
    for host_tool in ("rustup", "cargo"):
        resolved_host_tool = command_path(host_tool, os.environ)
        if resolved_host_tool:
            host_tool_dir = str(Path(resolved_host_tool).resolve().parent)
            if host_tool_dir not in selected_path:
                selected_path.append(host_tool_dir)
    developer_bin = Path(environment.get("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")) / "usr" / "bin"
    if developer_bin.is_dir():
        selected_path.append(str(developer_bin))
    selected_path.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    environment["PATH"] = os.pathsep.join(selected_path)
    for directory in (
        layout.tmp,
        layout.home,
        layout.cache,
        layout.tools / "rustup",
        layout.cache / "cargo",
        layout.cache / "cipd",
        layout.cache / "pip",
        layout.cache / "python",
        layout.home / ".config",
        layout.home / ".local" / "share",
        layout.home / ".local" / "state",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if llvm_root:
        environment["CHROMIUM_LLVM_ROOT"] = str(llvm_root)
    if extra:
        environment.update({str(key): str(value) for key, value in extra.items()})
    selected_cache = select_compiler_cache(layout, environment, requested=compiler_cache)
    environment["CHROMIUM_COMPILER_CACHE_STATUS"] = selected_cache["status"]
    environment["CHROMIUM_COMPILER_CACHE_REQUESTED"] = selected_cache["requested"]
    if selected_cache["status"] == "enabled":
        environment.update(
            {
                "CHROMIUM_COMPILER_CACHE_KIND": selected_cache["kind"],
                "CHROMIUM_COMPILER_CACHE_WRAPPER": selected_cache["path"],
                "CHROMIUM_COMPILER_CACHE_VERSION": selected_cache["version"],
                "CHROMIUM_COMPILER_CACHE_ROOT": selected_cache["root"],
            }
        )
        if selected_cache["kind"] == "ccache":
            environment.update(
                {
                    "CCACHE_DIR": selected_cache["directory"],
                    "CCACHE_TEMPDIR": selected_cache["temporary_directory"],
                    "CCACHE_COMPILERCHECK": "content",
                }
            )
        else:
            environment["SCCACHE_DIR"] = selected_cache["directory"]
    else:
        environment["CHROMIUM_COMPILER_CACHE_REASON"] = selected_cache["reason"]
    return environment


def parse_macos_version(environment: Mapping[str, str]) -> tuple[int, int, int]:
    result = command_output(["sw_vers", "-productVersion"], env=environment, check=False)
    text = (result.stdout or "").strip()
    if result.returncode or not text:
        text = host_platform.mac_ver()[0]
    if not text:
        fail("unable to determine macOS product version")
    return parse_version(text)


def xcode_identity(environment: Mapping[str, str]) -> dict[str, str]:
    xcode = command_output(["xcodebuild", "-version"], env=environment, check=False)
    if xcode.returncode:
        fail("Xcode is unavailable; finish the Xcode installation and ensure xcodebuild is usable")
    sdk = command_output(["xcrun", "--sdk", "macosx", "--show-sdk-version"], env=environment, check=False)
    clang = command_output(["xcrun", "--find", "clang"], env=environment, check=False)
    clangxx = command_output(["xcrun", "--find", "clang++"], env=environment, check=False)
    linker = command_output(["xcrun", "--find", "ld"], env=environment, check=False)
    if sdk.returncode or clang.returncode or clangxx.returncode or linker.returncode:
        fail("the selected Xcode installation cannot provide the macOS SDK and C compiler")
    clang_path = Path(clang.stdout.strip())
    clangxx_path = Path(clangxx.stdout.strip())
    linker_path = Path(linker.stdout.strip())
    if not clang_path.is_file() or not clangxx_path.is_file() or not linker_path.is_file():
        fail("the selected Xcode installation returned missing Apple C toolchain paths")
    clang_version = command_output([str(clang_path), "--version"], env=environment, check=False)
    metal = command_output(["xcrun", "--sdk", "macosx", "--find", "metal"], env=environment, check=False)
    if metal.returncode or not metal.stdout.strip() or not Path(metal.stdout.strip()).is_file():
        detail = (metal.stderr or metal.stdout or "").strip()
        suffix = f": {detail[-600:]}" if detail else ""
        fail(
            "the Xcode Metal Toolchain is unavailable; install it with "
            "`xcodebuild -downloadComponent MetalToolchain` and rerun the macOS preflight"
            f"{suffix}"
        )
    metal_version = command_output(
        ["xcrun", "--sdk", "macosx", "metal", "--version"],
        env=environment,
        check=False,
    )
    if metal_version.returncode:
        detail = (metal_version.stderr or metal_version.stdout or "").strip()
        suffix = f": {detail[-600:]}" if detail else ""
        fail(
            "the Xcode Metal Toolchain is unavailable; install it with "
            "`xcodebuild -downloadComponent MetalToolchain` and rerun the macOS preflight"
            f"{suffix}"
        )
    return {
        "xcode": (xcode.stdout or "").strip(),
        "sdk": (sdk.stdout or "").strip(),
        "clang": str(clang_path),
        "clangxx": str(clangxx_path),
        "linker": str(linker_path),
        "clang_version": (clang_version.stdout or "").strip(),
        "metal": metal.stdout.strip(),
        "metal_version": (metal_version.stdout or "").strip(),
    }


def translated_execution(environment: Mapping[str, str]) -> bool:
    sysctl = command_output(["sysctl", "-in", "sysctl.proc_translated"], env=environment, check=False)
    if sysctl.returncode == 0 and sysctl.stdout.strip() in {"1", "true", "yes"}:
        return True
    arch = command_output(["arch"], env=environment, check=False)
    return arch.returncode == 0 and arch.stdout.strip() not in {"arm64", "aarch64"}


def brew_candidates(environment: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    brew = command_path("brew", environment)
    if brew:
        for formula in ("llvm", "llvm@23"):
            result = command_output([brew, "--prefix", formula], env=environment, check=False)
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(Path(result.stdout.strip()))
    candidates.extend(
        Path(item)
        for item in (
            "/opt/homebrew/opt/llvm",
            "/opt/homebrew/opt/llvm@23",
            "/usr/local/opt/llvm",
            "/usr/local/opt/llvm@23",
        )
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if str(resolved) not in seen:
            seen.add(str(resolved))
            unique.append(resolved)
    return unique


def llvm_candidate(root: Path, environment: Mapping[str, str], source: str) -> Optional[dict[str, Any]]:
    clang = root / "bin" / "clang"
    cxx = root / "bin" / "clang++"
    if not clang.is_file() or not cxx.is_file():
        return None
    required_tools = {
        name: root / "bin" / name
        for name in ("llvm-ar", "llvm-nm", "llvm-objcopy", "llvm-strip")
    }
    if any(not path.is_file() for path in required_tools.values()):
        return None
    optional_tools = {
        name: root / "bin" / name
        for name in ("llvm-ranlib", "llvm-config")
        if (root / "bin" / name).is_file()
    }
    version_result = command_output([str(clang), "--version"], env=environment, check=False)
    if version_result.returncode:
        return None
    try:
        version = parse_version(version_result.stdout)
    except MacOSFailure:
        return None
    target = command_output([str(clang), "-dumpmachine"], env=environment, check=False)
    target_text = target.stdout.strip()
    if target.returncode or (target_text and "arm64" not in target_text and "aarch64" not in target_text):
        return None
    llvm_config = root / "bin" / "llvm-config"
    llvm_version = ""
    if llvm_config.is_file():
        config_result = command_output([str(llvm_config), "--version"], env=environment, check=False)
        llvm_version = (config_result.stdout or "").strip()
    return {
        "root": str(root),
        "source": source,
        "clang": str(clang),
        "clangxx": str(cxx),
        "version": version_string(version),
        "version_tuple": version,
        "clang_output": version_result.stdout.strip(),
        "llvm_config_version": llvm_version,
        "tools": {name: str(path) for name, path in {**required_tools, **optional_tools}.items()},
        "target": target_text,
        "preferred": version == PREFERRED_LLVM,
    }


def select_llvm(environment: Mapping[str, str], source_root: Optional[Path] = None) -> dict[str, Any]:
    """Select exact preferred LLVM, otherwise the newest native compatible candidate."""

    candidates: list[tuple[Path, str]] = []
    xcode_clang = Path(environment.get("XCODE_CLANG", ""))
    if xcode_clang.is_file():
        candidates.append((xcode_clang.parent.parent, "xcode"))
    explicit = environment.get("CHROMIUM_LLVM_ROOT") or environment.get("LLVM_ROOT")
    if explicit:
        candidates.insert(0, (canonical_path(explicit, "LLVM_ROOT", must_exist=True), "explicit"))
    if source_root:
        for relative in ("third_party/llvm-build/Release+Asserts", "third_party/llvm-build/Release"):
            candidates.append((source_root / relative, "chromium-deps"))
    candidates.extend((item, "homebrew") for item in brew_candidates(environment))

    inspected: list[dict[str, Any]] = []
    for root, source in candidates:
        candidate = llvm_candidate(root, environment, source)
        if candidate:
            inspected.append(candidate)
    acceptable = [item for item in inspected if item["version_tuple"] >= MIN_LLVM]
    if not acceptable:
        details = ", ".join(f"{item['source']}={item['version']}" for item in inspected) or "no usable LLVM found"
        fail(
            "LLVM/Clang 23.1.0 or newer is required; "
            f"inspected {details}. Install or expose an existing Homebrew llvm@23/llvm 23.1.x "
            "or set LLVM_ROOT to a native arm64 toolchain; this builder will not install packages."
        )
    acceptable.sort(key=lambda item: (item["version_tuple"] == PREFERRED_LLVM, item["version_tuple"]), reverse=True)
    selected = dict(acceptable[0])
    selected["minimum"] = version_string(MIN_LLVM)
    selected["preferred"] = version_string(PREFERRED_LLVM)
    selected["inspected"] = inspected
    selected["selection_reason"] = (
        "preferred LLVM 23.1.1" if selected["version_tuple"] == PREFERRED_LLVM else "newest compatible LLVM"
    )
    return selected


def reference_state(path: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    if not path.is_dir():
        fail(f"required read-only reference is missing: {path}")
    revision = command_output(["git", "-C", str(path), "rev-parse", "HEAD"], env=environment, check=False)
    if revision.returncode:
        fail(f"reference is not a Git checkout: {path}")
    dirty = command_output(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"],
        env=environment,
        check=False,
    )
    return {
        "path": str(path),
        "revision": revision.stdout.strip(),
        "dirty": bool(dirty.stdout.strip()),
    }


def run_doctor(
    layout: WorkLayout,
    *,
    require_references: bool = True,
    minimum_free_gb: Optional[int] = None,
    enforce_full_build_floor: bool = True,
) -> dict[str, Any]:
    layout.ensure()
    environment = isolated_environment(layout)
    full_build_preflight = os.environ.get("MACOS_REQUIRE_FULL_BUILD", "0").lower() in {
        "1",
        "true",
        "yes",
    }
    report: dict[str, Any] = {
        "schema": 1,
        "platform": "macos",
        "architecture": host_platform.machine(),
        "work_root": str(layout.root),
        "paths": {"forbidden": ["/tmp", "~"], "root": str(WORK_PREFIX)},
        "preflight_scope": (
            "full"
            if require_references
            else "host-only-full-build"
            if full_build_preflight
            else "host-only-smoke"
        ),
        "checks": {},
        "status": "failed",
    }
    try:
        if sys.platform != "darwin":
            fail(f"macOS backend requires Darwin, got {sys.platform}")
        machine = host_platform.machine().lower()
        if machine not in {"arm64", "aarch64"}:
            fail(f"macOS backend requires native arm64 execution, got {machine}")
        if translated_execution(environment):
            fail("translated execution was detected; run this backend natively on Apple Silicon")
        os_version = parse_macos_version(environment)
        if os_version < MIN_MACOS:
            fail(f"macOS {version_string(MIN_MACOS)} or newer is required, got {version_string(os_version)}")
        report["checks"]["macos"] = {
            "version": version_string(os_version),
            "minimum": version_string(MIN_MACOS),
            "architecture": machine,
            "translated": False,
        }
        xcode = xcode_identity(environment)
        sdk_version = parse_version(xcode["sdk"])
        if sdk_version < MIN_MACOS:
            fail(
                f"macOS SDK {version_string(MIN_MACOS)} or newer is required, "
                f"got {version_string(sdk_version)}"
            )
        apple_clang_version = parse_version(xcode["clang_version"])
        environment["XCODE_CLANG"] = xcode["clang"]
        report["checks"]["xcode"] = {
            **xcode,
            "sdk_version": version_string(sdk_version),
            "apple_clang_version": version_string(apple_clang_version),
            "apple_clang_meets_llvm_floor": apple_clang_version >= MIN_LLVM,
        }
        report["checks"]["compiler_cache"] = compiler_cache_report(environment)
        source_root = layout.source if layout.source.is_dir() else None
        llvm = select_llvm(environment, source_root)
        report["checks"]["llvm"] = {
            key: value
            for key, value in llvm.items()
            if key != "version_tuple"
        }
        free = shutil.disk_usage(WORK_PREFIX).free
        configured_minimum_gb = (
            minimum_free_gb
            if minimum_free_gb is not None
            else int(os.environ.get("MACOS_MIN_FREE_GB", str(FULL_BUILD_MIN_FREE_GB)))
        )
        minimum_free = configured_minimum_gb * 1024**3
        full_build_minimum = FULL_BUILD_MIN_FREE_GB * 1024**3
        if enforce_full_build_floor and (require_references or full_build_preflight) and minimum_free < full_build_minimum:
            fail(
                "full preflight cannot lower the free-space floor below "
                f"{FULL_BUILD_MIN_FREE_GB} GiB"
            )
        if free < minimum_free:
            fail(f"insufficient free space under /Volumes/dev: {free / 1024**3:.1f} GiB; need {minimum_free / 1024**3:.1f} GiB")
        report["checks"]["filesystem"] = {
            "mount": str(WORK_PREFIX),
            "free_bytes": free,
            "minimum_free_bytes": minimum_free,
            "full_build_minimum_free_bytes": full_build_minimum,
        }
        refs: dict[str, Any] = {}
        for name, path in (
            ("helium", Path("/Volumes/dev/d/helium")),
            ("shadowdriver", Path("/Volumes/dev/d/shadowdriver")),
        ):
            if path.is_dir():
                refs[name] = reference_state(path, environment)
            elif require_references:
                fail(f"required read-only reference is missing: {path}")
            else:
                refs[name] = {
                    "path": str(path),
                    "status": "skipped",
                    "reason": "host-only smoke preflight does not require donor checkouts",
                }
        report["references"] = refs
        rustup = command_path("rustup", environment) or "/opt/homebrew/bin/rustup"
        if not Path(rustup).is_file():
            fail("rustup is required to provision the isolated nightly-2026-09-15 toolchain")
        require_rust = os.environ.get("MACOS_REQUIRE_RUST_TOOLCHAIN", "0").lower() in {
            "1",
            "true",
            "yes",
        }
        if require_rust:
            listed = command_output([rustup, "toolchain", "list"], env=environment, check=False)
            if listed.returncode or not re.search(rf"(?m)^{re.escape(RUST_TOOLCHAIN)}(?:-|\s|$)", listed.stdout or ""):
                fail(f"required Rust toolchain {RUST_TOOLCHAIN} is not installed in the isolated rustup home")
            components = command_output(
                [rustup, "component", "list", "--toolchain", RUST_TOOLCHAIN, "--installed"],
                env=environment,
                check=False,
            )
            installed_components = components.stdout or ""
            required_components = {
                "rust-src": r"(?m)^rust-src(?:\s|$)",
                # rustup reports the preview component using its target-specific
                # published name, for example llvm-tools-aarch64-apple-darwin.
                "llvm-tools-preview": r"(?m)^llvm-tools(?:-preview)?(?:-|\s|$)",
            }
            missing_components = [
                component
                for component, pattern in required_components.items()
                if not re.search(pattern, installed_components)
            ]
            if components.returncode or missing_components:
                fail(
                    f"Rust {RUST_TOOLCHAIN} is missing required components: "
                    + ", ".join(missing_components)
                )
            rustc = command_output(
                [rustup, "run", RUST_TOOLCHAIN, "rustc", "--version", "--verbose"],
                env=environment,
                check=False,
            )
            if rustc.returncode:
                fail(f"Rust {RUST_TOOLCHAIN} cannot run from the isolated rustup home")
            rust = {
                "rustup": rustup,
                "toolchain": RUST_TOOLCHAIN,
                "status": "complete",
                "install_root": str(layout.tools / "rustup"),
                "components": ["rust-src", "llvm-tools-preview"],
                "rustc": (rustc.stdout or "").strip(),
            }
        else:
            rust = {
                "rustup": rustup,
                "toolchain": RUST_TOOLCHAIN,
                "status": "bootstrap-required",
                "install_root": str(layout.tools / "rustup"),
                "components": ["rust-src", "llvm-tools-preview"],
            }
        report["checks"]["rust"] = rust
        report["status"] = "complete"
    except MacOSFailure as error:
        report["failure"] = str(error)
        atomic_json(layout.metadata / "doctor.json", report)
        raise
    atomic_json(layout.metadata / "doctor.json", report)
    atomic_json(layout.metadata / "host.json", report)
    print(f"doctor: macOS {report['checks']['macos']['version']} arm64; LLVM {report['checks']['llvm']['version']} ({report['checks']['llvm']['source']})")
    return report


def request_bytes(url: str, *, timeout: float = 60) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        fail(f"network input is not HTTPS: {url}")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "chromium-build-native-macos"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme != "https":
                fail(f"HTTPS input redirected to a non-HTTPS URL: {response.geturl()}")
            return response.read()
    except (OSError, urllib.error.URLError) as error:
        fail(f"download failed for {url}: {error}")


def request_text(url: str, *, timeout: float = 60) -> str:
    return request_bytes(url, timeout=timeout).decode("utf-8", errors="replace")


def request_json(url: str) -> Any:
    try:
        return json.loads(request_text(url))
    except json.JSONDecodeError as error:
        fail(f"invalid JSON from {url}: {error}")


def request_size(url: str) -> int:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "chromium-build-native-macos"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme != "https":
                fail(f"HEAD redirected to a non-HTTPS URL: {response.geturl()}")
            value = response.headers.get("Content-Length")
            if not value:
                fail(f"source server did not provide Content-Length: {url}")
            return int(value)
    except (OSError, ValueError, urllib.error.URLError) as error:
        fail(f"could not inspect source size {url}: {error}")


def verify_locked_file(path: Path, entry: Mapping[str, Any]) -> None:
    if not path.is_file():
        fail(f"locked input is missing: {path}")
    expected_size = int(entry["size"])
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        fail(f"{entry['filename']}: expected {expected_size} bytes, got {actual_size}")
    actual_digest = sha512(path)
    if actual_digest != entry["sha512"]:
        fail(f"{entry['filename']}: SHA-512 mismatch: expected {entry['sha512']}, got {actual_digest}")
    if entry.get("sha256") and sha256(path) != entry["sha256"]:
        fail(f"{entry['filename']}: SHA-256 mismatch")


def configured_positive_int(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        fail(f"{name} must be an integer, got {raw!r}")
    if value < 1 or value > maximum:
        fail(f"{name} must be between 1 and {maximum}, got {value}")
    return value


def _open_locked_response(url: str, headers: Optional[Mapping[str, str]] = None):
    request_headers = {"User-Agent": "chromium-build-native-macos"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    response = urllib.request.urlopen(request, timeout=120)
    final_url = urllib.parse.urlparse(response.geturl())
    if final_url.scheme != "https":
        response.close()
        fail(f"locked input redirected to a non-HTTPS URL: {response.geturl()}")
    return response


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    return int(status or 0)


def _validate_range_response(
    response: Any,
    *,
    start: int,
    end: int,
    expected_size: int,
) -> None:
    if _response_status(response) != 206:
        fail(f"range request returned HTTP {_response_status(response)} instead of 206")
    content_range = str(response.headers.get("Content-Range", ""))
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
    if not match:
        fail(f"range response has an invalid Content-Range header: {content_range!r}")
    actual_start, actual_end, actual_size = (int(value) for value in match.groups())
    if (actual_start, actual_end, actual_size) != (start, end, expected_size):
        fail(
            "range response does not match the locked request: "
            f"expected bytes {start}-{end}/{expected_size}, got {content_range}"
        )


def _supports_byte_ranges(entry: Mapping[str, Any]) -> bool:
    try:
        response = _open_locked_response(
            str(entry["url"]),
            {"Range": "bytes=0-0"},
        )
    except urllib.error.HTTPError as error:
        if error.code in {400, 405, 501}:
            error.close()
            return False
        raise
    try:
        if _response_status(response) != 206:
            return False
        _validate_range_response(
            response,
            start=0,
            end=0,
            expected_size=int(entry["size"]),
        )
        if len(response.read(2)) != 1:
            fail("range probe returned an unexpected payload length")
        return True
    finally:
        response.close()


def _write_at(file_descriptor: int, offset: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.pwrite(file_descriptor, view, offset)
        if written <= 0:
            fail("parallel locked-input download made no write progress")
        offset += written
        view = view[written:]


def _download_range(
    entry: Mapping[str, Any],
    file_descriptor: int,
    start: int,
    end: int,
) -> None:
    response = _open_locked_response(
        str(entry["url"]),
        {"Range": f"bytes={start}-{end}"},
    )
    try:
        _validate_range_response(
            response,
            start=start,
            end=end,
            expected_size=int(entry["size"]),
        )
        offset = start
        remaining = end - start + 1
        while remaining:
            payload = response.read(min(DOWNLOAD_CHUNK_BYTES, remaining))
            if not payload:
                fail(
                    f"range download ended early for {entry['filename']} "
                    f"at byte {offset}"
                )
            if len(payload) > remaining:
                fail(f"range download returned too many bytes for {entry['filename']}")
            _write_at(file_descriptor, offset, payload)
            offset += len(payload)
            remaining -= len(payload)
        if response.read(1):
            fail(f"range download returned too many bytes for {entry['filename']}")
    finally:
        response.close()


def _download_ranges(entry: Mapping[str, Any], partial: Path, workers: int) -> None:
    expected_size = int(entry["size"])
    ranges = [
        (start, min(start + RANGE_DOWNLOAD_CHUNK_BYTES, expected_size) - 1)
        for start in range(0, expected_size, RANGE_DOWNLOAD_CHUNK_BYTES)
    ]
    file_descriptor = os.open(str(partial), os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.ftruncate(file_descriptor, expected_size)

        def fetch_range(span: tuple[int, int]) -> None:
            _download_range(entry, file_descriptor, span[0], span[1])

        with ThreadPoolExecutor(max_workers=min(workers, len(ranges))) as executor:
            list(executor.map(fetch_range, ranges))
    finally:
        os.close(file_descriptor)


def _download_stream(entry: Mapping[str, Any], partial: Path) -> None:
    response = _open_locked_response(str(entry["url"]))
    try:
        with partial.open("wb") as output:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                output.write(chunk)
    finally:
        response.close()


def download_locked(entry: Mapping[str, Any], inputs: Path) -> dict[str, Any]:
    inputs.mkdir(parents=True, exist_ok=True)
    destination = inputs / str(entry["filename"])
    if destination.exists() and not destination.is_file():
        fail(f"locked input destination is not a regular file: {destination}")
    if destination.is_file():
        verify_locked_file(destination, entry)
        return {"name": entry.get("name", entry["filename"]), "path": str(destination), "action": "reused"}
    partial = inputs / f".{entry['filename']}.partial-{os.getpid()}"
    partial.unlink(missing_ok=True)
    size = int(entry["size"])
    workers = configured_positive_int(
        "MACOS_INPUT_DOWNLOAD_WORKERS",
        DEFAULT_INPUT_DOWNLOAD_WORKERS,
        MAX_INPUT_DOWNLOAD_WORKERS,
    )
    download_mode = "stream"
    try:
        if size >= RANGE_DOWNLOAD_THRESHOLD_BYTES and workers > 1 and _supports_byte_ranges(entry):
            _download_ranges(entry, partial, workers)
            download_mode = f"parallel-ranges-{workers}"
        else:
            _download_stream(entry, partial)
        verify_locked_file(partial, entry)
        os.replace(partial, destination)
    except (OSError, urllib.error.URLError) as error:
        partial.unlink(missing_ok=True)
        fail(f"download failed for {entry['url']}: {error}")
    finally:
        partial.unlink(missing_ok=True)
    return {
        "name": entry.get("name", entry["filename"]),
        "path": str(destination),
        "action": "downloaded",
        "download_mode": download_mode,
        "workers": workers if download_mode.startswith("parallel-ranges") else 1,
    }


def download_locked_entries(entries: Sequence[Mapping[str, Any]], inputs: Path) -> list[dict[str, Any]]:
    """Fetch independent locked entries concurrently; large archives split into ranges."""

    if not entries:
        return []
    workers = configured_positive_int(
        "MACOS_INPUT_ENTRY_WORKERS",
        min(4, len(entries)),
        MAX_INPUT_DOWNLOAD_WORKERS,
    )
    with ThreadPoolExecutor(max_workers=min(workers, len(entries))) as executor:
        return list(executor.map(lambda entry: download_locked(entry, inputs), entries))


def core_series_digest(archive: Path) -> Optional[str]:
    try:
        with tarfile.open(archive, "r:gz") as stream:
            member = next((item for item in stream.getmembers() if item.name.endswith("/patches/series")), None)
            if member is None:
                return None
            payload = stream.extractfile(member)
            if payload is None:
                return None
            return hashlib.sha256(payload.read()).hexdigest()
    except (OSError, tarfile.TarError):
        return None


def chromium_hashes(url: str, filename: str) -> dict[str, str]:
    lines = request_text(url + ".hashes").splitlines()
    result: dict[str, str] = {}
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and fields[-1] == filename:
            result[fields[0].lower()] = fields[1]
    if "sha512" not in result:
        fail(f"Chromium hash document has no SHA-512 for {filename}")
    return result


def latest_ungoogled_release() -> dict[str, Any]:
    releases = request_json("https://api.github.com/repos/ungoogled-software/ungoogled-chromium/releases?per_page=30")
    if not isinstance(releases, list):
        fail("ungoogled release API returned an unexpected value")
    candidates = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name", ""))
        match = RELEASE_RE.fullmatch(tag)
        if match:
            candidates.append((tuple(int(part) for part in match.group(1).split(".")), int(match.group(2)), release))
    if not candidates:
        fail("no released non-prerelease ungoogled-chromium tag was found")
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def update_lock(layout: WorkLayout, *, latest: bool = True) -> dict[str, Any]:
    if not latest:
        fail("macOS lock updates require --latest so the selected release is explicit")
    layout.ensure()
    release = latest_ungoogled_release()
    tag = str(release["tag_name"])
    release_commit = str(release.get("target_commitish", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", release_commit):
        tag_data = request_json(f"https://api.github.com/repos/ungoogled-software/ungoogled-chromium/git/ref/tags/{urllib.parse.quote(tag, safe='')}")
        release_commit = str(tag_data.get("object", {}).get("sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", release_commit):
        fail(f"release {tag} did not resolve to an immutable commit")

    raw_base = f"https://raw.githubusercontent.com/ungoogled-software/ungoogled-chromium/{release_commit}"
    chromium_version = request_text(raw_base + "/chromium_version.txt").strip()
    release_match = RELEASE_RE.fullmatch(tag)
    if not release_match or chromium_version != release_match.group(1):
        fail(f"ungoogled release tag/version mismatch: {tag} vs {chromium_version}")
    revision_text = request_text(raw_base + "/revision.txt").strip()
    try:
        ungoogled_revision = int(revision_text)
    except ValueError:
        fail(f"invalid ungoogled revision.txt: {revision_text}")
    downloads = request_text(raw_base + "/downloads.ini")
    chromium_url_match = re.search(r"(?ms)^\[chromium\].*?^url\s*=\s*(\S+)", downloads)
    filename_match = re.search(r"(?ms)^\[chromium\].*?^download_filename\s*=\s*(\S+)", downloads)
    if not chromium_url_match or not filename_match:
        fail("ungoogled downloads.ini has no Chromium source entry")
    chromium_url = chromium_url_match.group(1).replace("%(_chromium_version)s", chromium_version)
    chromium_filename = filename_match.group(1).replace("%(_chromium_version)s", chromium_version)
    if chromium_filename.endswith("-lite.tar.xz"):
        chromium_filename = chromium_filename.replace("-lite.tar.xz", ".tar.xz")
        chromium_url = chromium_url.replace("-lite.tar.xz", ".tar.xz")
    hashes = chromium_hashes(chromium_url, chromium_filename)
    chromium_size = request_size(chromium_url)

    chromium_releases = request_json(
        "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=100"
    )
    chromium_record = next(
        (item for item in chromium_releases if item.get("version") == chromium_version), None
    )
    if not chromium_record or not chromium_record.get("hashes", {}).get("chromium"):
        fail(f"Chromium Dash has no immutable macOS commit for {chromium_version}")
    chromium_commit = chromium_record["hashes"]["chromium"]
    source_entry = {
        "filename": chromium_filename,
        "url": chromium_url,
        "size": chromium_size,
        "sha512": hashes["sha512"],
    }

    ungoogled_filename = f"ungoogled-{release_commit}.tar.gz"
    ungoogled_entry = {
        "filename": ungoogled_filename,
        "url": f"https://github.com/ungoogled-software/ungoogled-chromium/archive/{release_commit}.tar.gz",
        "size": 0,
        "sha512": "",
    }
    existing = layout.inputs / ungoogled_filename
    if existing.is_file():
        ungoogled_entry["size"] = existing.stat().st_size
        ungoogled_entry["sha512"] = sha512(existing)
    else:
        # This archive is small; materialize it in the owned input cache so
        # the lock records the bytes that ordinary fetch will consume.
        partial = layout.inputs / f".{ungoogled_filename}.lock-partial"
        partial.unlink(missing_ok=True)
        payload = request_bytes(str(ungoogled_entry["url"]))
        partial.write_bytes(payload)
        ungoogled_entry["size"] = len(payload)
        ungoogled_entry["sha512"] = sha512(partial)
        os.replace(partial, existing)

    mac_releases = request_json(
        "https://api.github.com/repos/ungoogled-software/ungoogled-chromium-macos/commits?per_page=1"
    )
    if not isinstance(mac_releases, list) or not mac_releases:
        fail("could not resolve the macOS packaging reference commit")
    mac_commit = str(mac_releases[0].get("sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", mac_commit):
        fail("macOS packaging reference did not resolve to an immutable commit")
    mac_message = str(mac_releases[0].get("commit", {}).get("message", ""))
    mac_version_match = VERSION_RE.search(mac_message)
    mac_version = mac_version_match.group(0) if mac_version_match else "unknown"

    depot_refs = request_text(
        "https://chromium.googlesource.com/chromium/tools/depot_tools.git/+refs/heads/main?format=JSON"
    )
    depot_json = json.loads(depot_refs[depot_refs.find("{") :])
    depot_commit = depot_json.get("refs/heads/main", {}).get("value")
    if not re.fullmatch(r"[0-9a-f]{40}", str(depot_commit)):
        fail("depot_tools main did not resolve to an immutable commit")

    lock = {
        "schema": 1,
        "status": "resolved",
        "platform": "macos",
        "architecture": "arm64",
        "resolved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "chromium": {
            "version": chromium_version,
            "commit": chromium_commit,
            "main_branch_position": chromium_record.get("chromium_main_branch_position"),
            "devtools_revision": chromium_record.get("hashes", {}).get("devtools"),
            "source": source_entry,
        },
        "ungoogled": {
            "release": tag,
            "commit": release_commit,
            "revision": ungoogled_revision,
            "source": ungoogled_entry,
            "patch_series_sha256": core_series_digest(existing),
        },
        "macos_reference": {
            "repository": "https://github.com/ungoogled-software/ungoogled-chromium-macos.git",
            "commit": mac_commit,
            "version": mac_version,
            "compatible_with_chromium": mac_version == chromium_version,
            "patches_applied": [],
            "adaptation": "reference only; version-mismatched packaging changes are not imported",
        },
        "depot_tools": {
            "repository": "https://chromium.googlesource.com/chromium/tools/depot_tools.git",
            "commit": depot_commit,
        },
        "rust": {"toolchain": RUST_TOOLCHAIN, "components": ["rust-src", "llvm-tools-preview"]},
        "llvm": {
            "minimum": version_string(MIN_LLVM),
            "preferred": version_string(PREFERRED_LLVM),
            "selection": "prefer-exact-23.1.1-among-chromium-explicit-homebrew-candidates",
        },
        "test_inputs": {
            "ublock_origin": {
                "version": "1.72.0",
                "filename": "uBlock0_1.72.0.chromium.zip",
                "url": "https://github.com/gorhill/uBlock/releases/download/1.72.0/uBlock0_1.72.0.chromium.zip",
                "size": 4617915,
                "sha512": "ce98b58145bc1263ca919e9e329b2b402257d8b8eef325ec2b83392812f69ed62aba41866a3a7daecc166f2b5a18392467e9aae5af9d208b51cf1b02cc20d121",
                "sha256": "c2900bbe9a783e645389cdeb19be632a033ad11d8bee7a94b0d0c316a20a2b05",
            }
        },
        "patch_policy": {
            "core_ungoogled": "apply complete series in declared order",
            "macos_reference": "do not apply version-mismatched packaging patches",
            "excluded": ["portablelinux", "alpine", "copium", "musl", "headless"],
        },
    }
    atomic_json(layout.repo_root / LOCK_RELATIVE, lock)
    print(f"update-lock: resolved ungoogled {tag}, Chromium {chromium_version} at {chromium_commit}")
    return lock


def load_lock(layout: WorkLayout) -> dict[str, Any]:
    path = layout.repo_root / LOCK_RELATIVE
    if not path.is_file():
        fail(f"macOS lock is missing: {path}; run update-lock --latest")
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read macOS lock {path}: {error}")
    if lock.get("status") != "resolved" or lock.get("platform") != "macos" or lock.get("architecture") != "arm64":
        fail(f"macOS lock is not a resolved native arm64 lock: {path}")
    required = (
        lock.get("chromium", {}).get("source"),
        lock.get("ungoogled", {}).get("source"),
        lock.get("depot_tools", {}).get("commit"),
        lock.get("rust", {}).get("toolchain"),
    )
    if any(not item for item in required):
        fail(f"macOS lock is incomplete: {path}")
    for entry in (lock["chromium"]["source"], lock["ungoogled"]["source"], lock["test_inputs"]["ublock_origin"]):
        if not entry.get("filename") or not entry.get("url") or not entry.get("size") or not re.fullmatch(r"[0-9a-f]{128}", entry.get("sha512", "")):
            fail(f"macOS lock has an incomplete or unverifiable input entry: {entry.get('filename')}")
    return lock


def locked_entries(lock: Mapping[str, Any], *, include_test_inputs: bool = True) -> list[dict[str, Any]]:
    entries = []
    for name, value in (
        ("chromium", lock["chromium"]["source"]),
        ("ungoogled", lock["ungoogled"]["source"]),
    ):
        item = dict(value)
        item["name"] = name
        entries.append(item)
    if include_test_inputs:
        item = dict(lock["test_inputs"]["ublock_origin"])
        item["name"] = "ublock_origin"
        entries.append(item)
    return entries


def safe_extract_tar(
    archive: Path,
    destination: Path,
    *,
    allow_conflicting_regular_duplicates: bool = False,
) -> None:
    """Validate a locked archive, then let the native tar reader materialize it."""

    tar = Path("/usr/bin/tar")
    if not tar.is_file():
        fail("native tar is unavailable for locked source extraction")
    if allow_conflicting_regular_duplicates and archive.stat().st_size > 512 * 1024 * 1024:
        listing = command_output([str(tar), "-tf", str(archive)], env=os.environ).stdout
        root = destination.resolve()
        for name in listing.splitlines():
            target = (destination / name).resolve(strict=False)
            if not is_within(target, root):
                fail(f"archive member escapes extraction root: {name}")
        command_output([str(tar), "-xpf", str(archive), "-C", str(destination)], env=os.environ)
        for directory, directory_names, file_names in os.walk(destination, followlinks=False):
            base = Path(directory)
            for name in list(directory_names):
                candidate = base / name
                if candidate.is_symlink():
                    directory_names.remove(name)
                    target = candidate.resolve(strict=False)
                    if not is_within(target, root) or not target.exists():
                        fail(f"extracted symlink escapes or is dangling: {candidate}")
            for name in file_names:
                candidate = base / name
                if candidate.is_symlink():
                    target = candidate.resolve(strict=False)
                    if not is_within(target, root) or not target.exists():
                        fail(f"extracted symlink escapes or is dangling: {candidate}")
        return

    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as stream:
        members = stream.getmembers()
        seen: dict[str, str] = {}
        for member in members:
            target = (destination / member.name).resolve(strict=False)
            if not is_within(target, destination.resolve()):
                fail(f"archive member escapes extraction root: {member.name}")
            kind = (
                "directory"
                if member.isdir()
                else "symlink"
                if member.issym()
                else "hardlink"
                if member.islnk()
                else "file"
                if member.isfile()
                else "other"
            )
            previous = seen.get(str(target))
            if previous is not None:
                compatible = kind == previous == "directory" or (
                    allow_conflicting_regular_duplicates and kind == previous == "file"
                )
                if not compatible:
                    fail(f"archive contains a conflicting duplicate path: {member.name}")
            else:
                seen[str(target)] = kind
            if member.issym() or member.islnk():
                link_target = Path(member.linkname)
                link_path = (target.parent / link_target).resolve(strict=False)
                if not is_within(link_path, destination.resolve()):
                    fail(f"archive link escapes extraction root: {member.name} -> {member.linkname}")
            if kind == "other":
                fail(f"unsupported archive member type: {member.name}")
    command_output([str(tar), "-xpf", str(archive), "-C", str(destination)], env=os.environ)


def find_chromium_root(extracted: Path) -> Path:
    direct = extracted / "chrome" / "VERSION"
    if direct.is_file():
        return extracted
    matches = [path.parent.parent for path in extracted.rglob("chrome/VERSION")]
    if len(matches) != 1:
        fail(f"expected one Chromium source root in {extracted}, found {len(matches)}")
    return matches[0]


def clone_depot_tools(layout: WorkLayout, lock: Mapping[str, Any], environment: Mapping[str, str]) -> Path:
    destination = layout.tools / "depot_tools"
    expected = str(lock["depot_tools"]["commit"])
    if destination.exists():
        if not (destination / ".git").is_dir():
            fail(f"owned depot_tools path is not a Git checkout: {destination}")
        dirty = command_output(
            ["git", "-C", str(destination), "status", "--porcelain", "--untracked-files=all"],
            env=environment,
            check=False,
        )
        if dirty.stdout.strip():
            fail(f"depot_tools has local edits; reset only this owned checkout before retrying: {destination}")
        actual = command_output(["git", "-C", str(destination), "rev-parse", "HEAD"], env=environment, check=False)
        if actual.returncode == 0 and actual.stdout.strip() == expected:
            return destination
        command_output(
            [
                "git",
                "-C",
                str(destination),
                "fetch",
                "--filter=blob:none",
                "--depth=1",
                "--no-tags",
                "origin",
                expected,
            ],
            env=environment,
        )
        command_output(["git", "-C", str(destination), "checkout", "--detach", expected], env=environment)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    command_output(
        ["git", "-C", str(destination), "init", "--initial-branch=locked"],
        env=environment,
    )
    command_output(
        [
            "git",
            "-C",
            str(destination),
            "remote",
            "add",
            "origin",
            str(lock["depot_tools"]["repository"]),
        ],
        env=environment,
    )
    command_output(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--filter=blob:none",
            "--depth=1",
            "--no-tags",
            "origin",
            expected,
        ],
        env=environment,
    )
    command_output(["git", "-C", str(destination), "checkout", "--detach", expected], env=environment)
    return destination


def ensure_rust_toolchain(layout: WorkLayout, environment: Mapping[str, str]) -> dict[str, Any]:
    rustup = command_path("rustup", environment) or "/opt/homebrew/bin/rustup"
    if not Path(rustup).is_file():
        fail("rustup is unavailable; install rustup without modifying this builder or set PATH to it")
    listed = command_output([rustup, "toolchain", "list"], env=environment, check=False)
    if RUST_TOOLCHAIN not in (listed.stdout or ""):
        stream_command(
            [
                rustup,
                "toolchain",
                "install",
                RUST_TOOLCHAIN,
                "--profile",
                "minimal",
                "--component",
                "rust-src",
                "--component",
                "llvm-tools-preview",
            ],
            env=environment,
            cwd=layout.root,
            log_path=layout.logs / "rust-toolchain-install.log",
            timeout=1800,
        )
        listed = command_output([rustup, "toolchain", "list"], env=environment, check=False)
    if RUST_TOOLCHAIN not in (listed.stdout or ""):
        fail(f"required Rust toolchain {RUST_TOOLCHAIN} is not available in {layout.tools / 'rustup'}")
    rustc = command_output([rustup, "run", RUST_TOOLCHAIN, "rustc", "--version", "--verbose"], env=environment)
    return {
        "toolchain": RUST_TOOLCHAIN,
        "rustup": rustup,
        "rustc": (rustc.stdout or "").strip(),
        "home": str(layout.tools / "rustup"),
        "status": "complete",
    }


def deps_variable(source: Path, name: str) -> str:
    """Read one simple, pinned string variable from the checked source DEPS."""

    deps = source / "DEPS"
    if not deps.is_file():
        fail(f"the Chromium source is missing DEPS: {deps}")
    match = re.search(
        rf"(?m)^\s*['\"]{re.escape(name)}['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        deps.read_text(encoding="utf-8"),
    )
    if not match:
        fail(f"the locked Chromium DEPS has no string variable {name!r}: {deps}")
    return match.group(1)


def is_native_arm64_binary(path: Path, environment: Mapping[str, str]) -> bool:
    """Return true only for a single-architecture macOS arm64 executable."""

    if not path.is_file() or path.is_symlink():
        return False
    file_result = command_output(["/usr/bin/file", "-b", str(path)], env=environment, check=False)
    if file_result.returncode or "Mach-O" not in file_result.stdout:
        return False
    architectures = command_output(["/usr/bin/lipo", "-archs", str(path)], env=environment, check=False)
    return architectures.returncode == 0 and architectures.stdout.strip().split() == ["arm64"]


def ensure_native_cipd_tool(
    layout: WorkLayout,
    environment: Mapping[str, str],
    *,
    relative_root: str,
    binary_relative_path: str,
    package: str,
    version: str,
) -> dict[str, Any]:
    """Replace an archive's host-tool placeholder with the exact native CIPD package."""

    target = layout.source / relative_root
    binary = target / binary_relative_path
    if is_native_arm64_binary(binary, environment):
        return {
            "status": "reused",
            "relative_root": relative_root,
            "binary": str(binary),
            "package": package,
            "version": version,
            "architecture": "arm64",
        }
    if target.is_symlink():
        fail(f"native tool path is a symlink: {target}")
    if target.exists():
        if not target.is_dir():
            fail(f"native tool path is not a directory: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    cipd = layout.tools / "depot_tools" / ".cipd_client"
    if not cipd.is_file():
        fail(f"depot_tools CIPD client is missing: {cipd}")
    ensure_file = layout.tmp / f"{Path(relative_root).name}-{os.getpid()}-{time.time_ns()}.ensure"
    atomic_write(
        ensure_file,
        "$ParanoidMode CheckPresence\n"
        "$OverrideInstallMode copy\n\n"
        f"{package} {version}\n",
    )
    try:
        command_output(
            [
                str(cipd),
                "ensure",
                "-log-level",
                "error",
                "-root",
                str(target),
                "-ensure-file",
                str(ensure_file),
            ],
            env=environment,
            cwd=layout.root,
            timeout=3600,
        )
    finally:
        ensure_file.unlink(missing_ok=True)
    if not is_native_arm64_binary(binary, environment):
        fail(f"CIPD did not install a native arm64 tool: {binary}")
    return {
        "status": "complete",
        "relative_root": relative_root,
        "binary": str(binary),
        "package": package,
        "version": version,
        "architecture": "arm64",
    }


def ensure_native_host_tools(layout: WorkLayout, environment: Mapping[str, str]) -> dict[str, Any]:
    """Repair host binaries that official source archives may carry for Linux."""

    cpython_version = deps_variable(layout.source, "cpython3_version")
    gn_version = deps_variable(layout.source, "gn_version")
    ninja_version = deps_variable(layout.source, "ninja_version")
    tools = [
        ensure_native_cipd_tool(
            layout,
            environment,
            relative_root="third_party/cpython3/host",
            binary_relative_path="bin/python3",
            package="infra/3pp/tools/cpython3/mac-arm64",
            version=cpython_version,
        ),
        ensure_native_cipd_tool(
            layout,
            environment,
            relative_root="third_party/ninja",
            binary_relative_path="ninja",
            package="infra/3pp/tools/ninja/mac-arm64",
            version=ninja_version,
        ),
        ensure_native_cipd_tool(
            layout,
            environment,
            relative_root="buildtools/mac",
            binary_relative_path="gn",
            package="gn/gn/mac-arm64",
            version=gn_version,
        ),
    ]
    return {"status": "complete", "tools": tools}


def run_native_hooks(
    layout: WorkLayout,
    lock: Mapping[str, Any],
    environment: Mapping[str, str],
    depot_tools: Path,
    source_method: str,
) -> dict[str, Any]:
    """Run source hooks only during fetch, never during offline preparation."""

    if os.environ.get("MACOS_SKIP_HOOKS") == "1":
        return {"status": "skipped", "reason": "MACOS_SKIP_HOOKS=1", "network": "fetch"}
    gclient = depot_tools / "gclient"
    if not gclient.is_file():
        fail(f"depot_tools checkout has no gclient entrypoint: {gclient}")
    gclient_config = layout.root / ".gclient"
    hook_environment = dict(environment)
    hook_environment["PATH"] = os.pathsep.join([str(depot_tools), hook_environment["PATH"]])
    sync_stamp = layout.metadata / "gclient-sync-complete.stamp"
    archive_has_deps_closure = source_method == "official-native-macos-source-archive"
    archive_git_metadata = (
        materialize_archive_dependency_metadata(layout.source, depot_tools, hook_environment)
        if archive_has_deps_closure
        else {"status": "not-applicable", "method": "git-checkout"}
    )
    custom_deps = {
        f"src/{relative}": None
        for relative in archive_git_metadata.get("all_paths", [])
    }
    custom_deps_text = "{\n" + "".join(
        f"        {key!r}: None,\n" for key in sorted(custom_deps)
    ) + "      }"
    config = (
        "solutions = [\n"
        "  {\n"
        "    'name': 'src',\n"
        "    'url': 'https://chromium.googlesource.com/chromium/src.git@%s',\n"
        "    'managed': False,\n"
        "    'custom_deps': %s,\n"
        "  },\n"
        "]\n"
    ) % (lock["chromium"]["commit"], custom_deps_text)
    atomic_write(gclient_config, config)
    if archive_has_deps_closure:
        if sync_stamp.is_file():
            sync_record = {"status": "reused", "network": "fetch", "mode": "non-git-deps-only"}
        else:
            stream_command(
                [str(gclient), "sync", "--nohooks", "--no-history"],
                env=hook_environment,
                cwd=layout.root,
                log_path=layout.logs / "gclient-sync.log",
                timeout=4 * 60 * 60,
            )
            atomic_write(sync_stamp, "status=complete\nnetwork=fetch\nmode=non-git-deps-only\n")
            sync_record = {"status": "complete", "network": "fetch", "mode": "non-git-deps-only"}
    elif sync_stamp.is_file():
        sync_record = {"status": "reused", "network": "fetch"}
    else:
        stream_command(
            [str(gclient), "sync", "--nohooks", "--no-history"],
            env=hook_environment,
            cwd=layout.root,
            log_path=layout.logs / "gclient-sync.log",
            timeout=4 * 60 * 60,
        )
        atomic_write(sync_stamp, "status=complete\nnetwork=fetch\n")
        sync_record = {"status": "complete", "network": "fetch"}
    native_tools = ensure_native_host_tools(layout, hook_environment)
    stream_command(
        [str(gclient), "runhooks", "--force"],
        env=hook_environment,
        cwd=layout.root,
        log_path=layout.logs / "gclient-runhooks.log",
        timeout=3600,
    )
    return {
        "status": "complete",
        "network": "fetch",
        "sync": sync_record,
        "native_tools": native_tools,
        "archive_git_metadata": archive_git_metadata,
        "command": [str(gclient), "runhooks", "--force"],
    }


def ensure_archive_worktree(source: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    """Give an official source archive the Git metadata expected by gclient hooks."""

    git_directory = source / ".git"
    if git_directory.is_dir():
        head = command_output(["git", "-C", str(source), "rev-parse", "--verify", "HEAD"], env=environment, check=False)
        if head.returncode == 0 and head.stdout.strip():
            return {
                "status": "reused",
                "path": str(git_directory),
                "method": "archive-worktree",
                "commit": head.stdout.strip(),
            }
        command_output(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=chromium-build archive",
                "-c",
                "user.email=chromium-build@localhost",
                "commit",
                "--allow-empty",
                "-m",
                "materialized pinned Chromium source archive",
            ],
            env=environment,
        )
        head = command_output(["git", "-C", str(source), "rev-parse", "HEAD"], env=environment)
        return {
            "status": "complete",
            "path": str(git_directory),
            "method": "archive-worktree",
            "commit": head.stdout.strip(),
        }
    if git_directory.exists() or git_directory.is_symlink():
        fail(f"source .git path is not a directory: {git_directory}")
    command_output(["git", "-C", str(source), "init", "--initial-branch=archive"], env=environment)
    command_output(
        ["git", "-C", str(source), "config", "user.name", "chromium-build archive"],
        env=environment,
    )
    command_output(
        ["git", "-C", str(source), "config", "user.email", "chromium-build@localhost"],
        env=environment,
    )
    command_output(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=chromium-build archive",
            "-c",
            "user.email=chromium-build@localhost",
            "commit",
            "--allow-empty",
            "-m",
            "materialized pinned Chromium source archive",
        ],
        env=environment,
    )
    head = command_output(["git", "-C", str(source), "rev-parse", "HEAD"], env=environment)
    return {
        "status": "complete",
        "path": str(git_directory),
        "method": "archive-worktree",
        "commit": head.stdout.strip(),
    }


def materialize_archive_dependency_metadata(
    source: Path,
    depot_tools: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    """Give archive-populated Git DEPS roots empty local metadata.

    Official source archives contain the dependency files but omit the Git
    directories. Some Chromium hooks use Git only to ask whether a dependency
    has a submodule diff. Empty local commits satisfy that read-only contract
    without downloading dependency history or replacing the archive contents.
    """

    parser_path = depot_tools / "gclient_eval.py"
    if not parser_path.is_file():
        fail(f"depot_tools DEPS parser is missing: {parser_path}")
    parser_text = parser_path.read_text(encoding="utf-8")
    if "def Parse(content, filename" not in parser_text:
        fail(f"unexpected depot_tools DEPS parser: {parser_path}")

    original_sys_path = list(sys.path)
    try:
        sys.path.insert(0, str(depot_tools))
        import gclient_eval  # type: ignore[import-not-found]

        common_variables = {
            "checkout_mac": True,
            "checkout_linux": False,
            "checkout_win": False,
            "checkout_android": False,
            "checkout_ios": False,
            "checkout_chromeos": False,
            "checkout_x64": False,
            "checkout_x86": False,
            "checkout_arm": False,
            "checkout_arm64": True,
            "checkout_mips": False,
            "checkout_mips64": False,
            "checkout_ppc": False,
            "checkout_s390": False,
            "checkout_riscv64": False,
            "host_os": "mac",
            "host_cpu": "arm64",
            "target_os": "mac",
            "target_cpu": "arm64",
            "non_git_source": True,
        }
        queue = [source]
        visited_deps: set[Path] = set()
        materialized: list[str] = []
        archive_paths: list[str] = []
        while queue:
            deps_root = queue.pop()
            deps_root = deps_root.resolve(strict=False)
            if deps_root in visited_deps:
                continue
            visited_deps.add(deps_root)
            deps_file = deps_root / "DEPS"
            if not deps_file.is_file():
                continue
            try:
                parsed = gclient_eval.Parse(
                    str(deps_file.read_text(encoding="utf-8")),
                    str(deps_file),
                    common_variables,
                    common_variables,
                )
            except Exception as error:  # depot_tools exposes its own parser error type.
                fail(f"could not parse locked DEPS file {deps_file}: {error}")
            variables = dict(common_variables)
            variables.update(parsed.get("vars", {}))
            use_relative_paths = parsed.get("use_relative_paths") is True
            for name, info in parsed.get("deps", {}).items():
                if not isinstance(info, Mapping) or info.get("dep_type", "git") != "git":
                    continue
                condition = info.get("condition")
                if condition:
                    try:
                        if not gclient_eval.EvaluateCondition(condition, variables):
                            continue
                    except Exception:
                        # A new condition is safer to materialize than to let
                        # a hook fail because its archive checkout lacks .git.
                        pass
                relative = str(name)
                if use_relative_paths:
                    candidate = deps_root / relative
                elif relative.startswith("src/"):
                    candidate = source / relative[4:]
                else:
                    candidate = source / relative
                candidate = candidate.resolve(strict=False)
                if not is_within(candidate, source.resolve()):
                    fail(f"locked DEPS path escapes the source archive: {name}")
                if not candidate.is_dir():
                    continue
                relative_path = str(candidate.relative_to(source))
                if relative_path not in archive_paths:
                    archive_paths.append(relative_path)
                if not (candidate / ".git").exists():
                    ensure_archive_worktree(candidate, environment)
                    materialized.append(relative_path)
                queue.append(candidate)
    finally:
        sys.path[:] = original_sys_path

    return {
        "status": "complete",
        "method": "empty-archive-git-metadata",
        "roots": len(materialized),
        "paths": materialized,
        "all_paths": archive_paths,
    }


def replace_archive_with_git_source(
    layout: WorkLayout,
    lock: Mapping[str, Any],
    source_status: Mapping[str, Any],
    environment: Mapping[str, str],
) -> dict[str, Any]:
    """Replace the lite archive materialization with the lock's real Git tree."""

    if source_status.get("source_method") != "official-native-macos-lite-archive":
        return dict(source_status)
    commit = str(lock["chromium"]["commit"])
    archive_backup = layout.root / "source-archive"
    if layout.source.exists():
        if layout.source.is_symlink():
            fail(f"source path is a symlink: {layout.source}")
        if archive_backup.exists():
            fail(f"both archive source and archive backup exist; inspect before retrying: {layout.source}")
        os.replace(layout.source, archive_backup)
    elif not archive_backup.is_dir():
        fail(f"archive source is unavailable for replacement: {layout.source}")

    checkout = layout.tmp / f"chromium-git-{os.getpid()}-{time.time_ns()}"
    checkout.mkdir(parents=True, exist_ok=False)
    try:
        command_output(["git", "-C", str(checkout), "init", "--initial-branch=archive"], env=environment)
        command_output(
            [
                "git",
                "-C",
                str(checkout),
                "remote",
                "add",
                "origin",
                "https://chromium.googlesource.com/chromium/src.git",
            ],
            env=environment,
        )
        stream_command(
            [
                "git",
                "-C",
                str(checkout),
                "fetch",
                "--filter=blob:none",
                "--depth=1",
                "--no-tags",
                "origin",
                commit,
            ],
            env=environment,
            cwd=layout.root,
            log_path=layout.logs / "chromium-git-fetch.log",
            timeout=4 * 60 * 60,
        )
        command_output(["git", "-C", str(checkout), "checkout", "--detach", commit], env=environment)
        os.replace(checkout, layout.source)
        result = {
            "schema": 1,
            "status": "complete",
            "network": "fetch",
            "chromium_version": lock["chromium"]["version"],
            "chromium_commit": commit,
            "source_archive": source_status.get("source_archive"),
            "source_archive_backup": str(archive_backup),
            "source_method": "chromium-git-checkout",
            "git_remote": "https://chromium.googlesource.com/chromium/src.git",
        }
        atomic_json(layout.metadata / "source-acquired.json", result)
        return result
    finally:
        shutil.rmtree(checkout, ignore_errors=True)


def fetch_inputs(layout: WorkLayout) -> dict[str, Any]:
    layout.ensure()
    lock = load_lock(layout)
    environment = isolated_environment(layout, network="fetch")
    skip_acceptance = os.environ.get("MACOS_SKIP_ACCEPTANCE", "0").lower() in {
        "1",
        "true",
        "yes",
    }
    entries = download_locked_entries(
        locked_entries(lock, include_test_inputs=not skip_acceptance),
        layout.inputs,
    )
    depot_tools = clone_depot_tools(layout, lock, environment)
    rust = ensure_rust_toolchain(layout, environment)
    if skip_acceptance:
        harness = {
            "status": "skipped",
            "reason": "remote full-build workflow packages the audited bundle without Shadowdriver acceptance",
        }
    else:
        harness = fetch_acceptance_dependencies(layout, environment)

    source_state = layout.metadata / "source-acquired.json"
    if layout.source.exists() and any(layout.source.iterdir()):
        if not source_state.is_file():
            fail(f"source exists without owned acquisition metadata; use reset --yes before retrying: {layout.source}")
        existing = json.loads(source_state.read_text(encoding="utf-8"))
        if existing.get("chromium_commit") != lock["chromium"]["commit"]:
            fail("source acquisition identity differs from the macOS lock; use reset --yes")
        source_status = existing
    else:
        chromium_archive = layout.inputs / lock["chromium"]["source"]["filename"]
        extraction = layout.tmp / f"source-acquire-{os.getpid()}-{time.time_ns()}"
        extraction.mkdir(parents=True, exist_ok=False)
        try:
            safe_extract_tar(
                chromium_archive,
                extraction,
                allow_conflicting_regular_duplicates=True,
            )
            source_root = find_chromium_root(extraction)
            if layout.source.exists():
                if layout.source.is_symlink():
                    fail(f"source path is a symlink: {layout.source}")
                shutil.rmtree(layout.source)
            os.replace(source_root, layout.source)
            source_status = {
                "schema": 1,
                "status": "complete",
                "network": "fetch",
                "chromium_version": lock["chromium"]["version"],
                "chromium_commit": lock["chromium"]["commit"],
                "source_archive": str(chromium_archive),
                "source_method": "official-native-macos-source-archive",
            }
            atomic_json(source_state, source_status)
        finally:
            shutil.rmtree(extraction, ignore_errors=True)

    source_status = replace_archive_with_git_source(layout, lock, source_status, environment)
    archive_worktree = ensure_archive_worktree(layout.source, environment)
    hooks = run_native_hooks(
        layout,
        lock,
        environment,
        depot_tools,
        str(source_status.get("source_method", "")),
    )
    report = {
        "schema": 1,
        "status": "complete",
        "network": "fetch",
        "chromium_version": lock["chromium"]["version"],
        "chromium_commit": lock["chromium"]["commit"],
        "source_method": source_status.get("source_method"),
        "entries": entries,
        "depot_tools": {"path": str(depot_tools), "commit": lock["depot_tools"]["commit"]},
        "rust": rust,
        "harness": harness,
        "archive_worktree": archive_worktree,
        "hooks": hooks,
        "work_root": str(layout.root),
    }
    atomic_json(layout.metadata / "fetch-macos.json", report)
    atomic_write(layout.metadata / "fetch-complete.stamp", "status=complete\nnetwork=fetch\n")
    modes = ", ".join(
        f"{item['name']}={item['action']}/{item.get('download_mode', 'existing')}"
        for item in entries
    )
    print(f"fetch: verified {len(entries)} locked inputs ({modes}); native source is {layout.source}")
    return report


def patch_file(source: Path, patch_path: Path, patch_dir: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    if not patch_path.is_file():
        fail(f"declared patch is missing: {patch_path}")
    patch_dir.mkdir(parents=True, exist_ok=True)
    copied = patch_dir / patch_path.name
    shutil.copy2(patch_path, copied)
    patch_command = command_path("patch", environment) or "/usr/bin/patch"
    dry = command_output(
        [patch_command, "--batch", "--forward", "--fuzz=0", "-p1", "--dry-run", "-i", str(patch_path)],
        env=environment,
        cwd=source,
        check=False,
    )
    if dry.returncode:
        detail = (dry.stdout or dry.stderr or "").strip()
        fail(f"macOS patch failed dry-run: {patch_path.name}: {detail[-1600:]}")
    applied = command_output(
        [patch_command, "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch_path)],
        env=environment,
        cwd=source,
        check=False,
    )
    if applied.returncode:
        fail(f"macOS patch application failed: {patch_path.name}")
    offsets = [int(item) for item in re.findall(r"offset (-?\d+) lines?", dry.stdout or "")]
    return {
        "name": str(patch_path.name),
        "path": str(patch_path),
        "sha256": sha256(patch_path),
        "dry_run": "passed",
        "result": "applied",
        "fuzz": 0,
        "offsets": offsets,
        "deterministic_options": {"batch": True, "forward": True, "fuzz_limit": 0},
    }


def read_series(root: Path) -> list[Path]:
    series = root / "patches" / "series"
    if not series.is_file():
        fail(f"ungoogled patch series is missing: {series}")
    patches = []
    for raw in series.read_text(encoding="utf-8").splitlines():
        name = raw.split("#", 1)[0].strip()
        if name:
            patches.append(root / "patches" / name)
    return patches


def apply_ungoogled_patches(
    source: Path,
    core_root: Path,
    layout: WorkLayout,
    environment: Mapping[str, str],
) -> list[dict[str, Any]]:
    records = []
    for patch in read_series(core_root):
        records.append(patch_file(source, patch, layout.metadata / "patches" / "ungoogled", environment))
    return records


def apply_pruning(source: Path, pruning_file: Path, version: str) -> dict[str, Any]:
    requested = [
        line.strip()
        for line in pruning_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    removed: list[str] = []
    absent: list[str] = []
    for relative in requested:
        path = source / relative
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                target = path.resolve(strict=False)
                if not is_within(target, source.resolve()):
                    fail(f"pruning target symlink escapes source: {relative}")
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(relative)
        else:
            absent.append(relative)
    return {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": version,
        "requested": requested,
        "removed": removed,
        "already_absent": absent,
        "unexpected_absent": [],
    }


def apply_domain_substitution(source: Path, files: Path, regex_file: Path) -> dict[str, Any]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for raw in regex_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        expression, replacement = line.split("#", 1)
        patterns.append((re.compile(expression), replacement))
    processed = 0
    changed = 0
    unresolved: list[str] = []
    for raw in files.read_text(encoding="utf-8").splitlines():
        relative = raw.strip()
        if not relative:
            continue
        path = source / relative
        if not path.is_file():
            unresolved.append(relative)
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        updated = original
        for expression, replacement in patterns:
            updated = expression.sub(replacement, updated)
        processed += 1
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    if unresolved:
        fail(f"domain substitution has unresolved files, first entries: {unresolved[:8]}")
    return {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "processed": processed,
        "changed": changed,
        "unresolved": [],
    }


def _apply_disabled_screen_ai_compatibility(source: Path) -> dict[str, Any]:
    """Guard the optional Screen AI sandbox path for the lightweight profile."""

    path = source / "chrome" / "browser" / "chrome_content_browser_client.cc"
    if not path.is_file():
        fail(f"macOS compatibility target is missing: {path}")
    original = path.read_text(encoding="utf-8")
    buildflags_include = '#include "services/screen_ai/buildflags/buildflags.h"\n'
    screen_ai_include = '#include "chrome/browser/screen_ai/screen_ai_install_state.h"\n'
    guarded_include = (
        "#if BUILDFLAG(ENABLE_SCREEN_AI_SERVICE)\n"
        f"{screen_ai_include}"
        "#endif  // BUILDFLAG(ENABLE_SCREEN_AI_SERVICE)\n"
    )
    if guarded_include in original:
        include_updated = original
    elif screen_ai_include in original:
        include_updated = original.replace(screen_ai_include, guarded_include, 1)
    else:
        fail(f"unexpected Screen AI include shape in {path}")

    if buildflags_include not in include_updated:
        buildflags_anchors = (
            '#include "services/network/public/mojom/websocket.mojom.h"\n',
            '#include "services/network/public/cpp/features.h"\n',
        )
        buildflags_anchor = next(
            (anchor for anchor in buildflags_anchors if anchor in include_updated),
            None,
        )
        if buildflags_anchor is None:
            fail(f"cannot place Screen AI buildflags include in {path}")
        include_updated = include_updated.replace(
            buildflags_anchor,
            buildflags_anchor + buildflags_include,
            1,
        )

    screen_ai_start = "  if (sandbox_type == sandbox::mojom::Sandbox::kScreenAI) {\n"
    next_sandbox_case = "  if (sandbox_type == sandbox::mojom::Sandbox::kOnDeviceTranslation) {\n"
    guarded_body_prefix = "#if BUILDFLAG(ENABLE_SCREEN_AI_SERVICE)\n" + screen_ai_start
    guarded_body_suffix = "#endif  // BUILDFLAG(ENABLE_SCREEN_AI_SERVICE)\n"
    body_updated = include_updated
    if guarded_body_prefix in body_updated:
        body_status = "reused"
    else:
        start = body_updated.find(screen_ai_start)
        end = body_updated.find(next_sandbox_case, start + len(screen_ai_start))
        if start < 0 or end < 0:
            fail(f"unexpected Screen AI sandbox branch shape in {path}")
        body = body_updated[start:end]
        if "ScreenAIInstallState::GetInstance" not in body:
            fail(f"Screen AI sandbox branch is not the expected implementation in {path}")
        body_updated = (
            body_updated[:start]
            + guarded_body_prefix
            + body
            + guarded_body_suffix
            + body_updated[end:]
        )
        body_status = "applied"

    if body_updated == original:
        return {
            "status": "reused",
            "name": "screen-ai-disabled-macos-sandbox-branch",
            "path": str(path),
            "reason": "enable_screen_ai_service=false",
        }
    path.write_text(body_updated, encoding="utf-8")
    return {
        "status": "applied",
        "name": "screen-ai-disabled-macos-sandbox-branch",
        "path": str(path),
        "reason": "enable_screen_ai_service=false",
        "include_status": "applied" if include_updated != original else "reused",
        "body_status": body_status,
        "sha256_before": hashlib.sha256(original.encode()).hexdigest(),
        "sha256_after": hashlib.sha256(body_updated.encode()).hexdigest(),
    }


def _apply_english_only_locale_compatibility(source: Path) -> dict[str, Any]:
    """Expose a profile-controlled single locale to Chromium's GN locale lists."""

    path = source / "build" / "config" / "locales.gni"
    if not path.is_file():
        fail(f"macOS compatibility target is missing: {path}")
    original = path.read_text(encoding="utf-8")
    updated = original
    argument = '  macos_single_locale = ""\n'
    if argument not in updated:
        declaration = "declare_args() {\n"
        if declaration not in updated:
            fail(f"unexpected locale argument block shape in {path}")
        updated = updated.replace(
            declaration,
            declaration
            + "  # The native macOS profile may restrict packaged resources to one locale.\n"
            + argument,
            1,
        )

    override = (
        "# The native macOS profile can keep the desktop resource set lean.\n"
        'if (macos_single_locale != "") {\n'
        "  platform_pak_locales = []\n"
        "  platform_pak_locales += [ macos_single_locale ]\n"
        "}\n"
    )
    if override not in updated:
        legacy_overrides = (
            (
                "# The native macOS profile can keep the desktop resource set lean.\n"
                'if (macos_single_locale != "") {\n'
                "  all_chrome_locales = [ macos_single_locale ]\n"
                "  platform_pak_locales = [ macos_single_locale ]\n"
                "}\n"
            ),
            (
                "# The native macOS profile can keep the desktop resource set lean.\n"
                'if (macos_single_locale != "") {\n'
                "  all_chrome_locales = []\n"
                "  all_chrome_locales += [ macos_single_locale ]\n"
                "  platform_pak_locales = []\n"
                "  platform_pak_locales += [ macos_single_locale ]\n"
                "}\n"
            ),
            (
                "# The native macOS profile can keep the desktop resource set lean.\n"
                'if (macos_single_locale != "") {\n'
                "  pseudolocales = []\n"
                "  platform_pak_locales = []\n"
                "  platform_pak_locales += [ macos_single_locale ]\n"
                "}\n"
            ),
            (
                "# The native macOS profile can keep the desktop resource set lean.\n"
                'if (macos_single_locale != "") {\n'
                "  platform_pak_locales = []\n"
                "  platform_pak_locales += [ macos_single_locale ]\n"
                "}\n"
            ),
        )
        legacy = next((candidate for candidate in legacy_overrides if candidate in updated), None)
        if legacy is not None:
            updated = updated.replace(legacy, override, 1)
        else:
            anchor = "platform_pak_locales = all_chrome_locales\n"
            if anchor not in updated:
                fail(f"unexpected platform locale list shape in {path}")
            updated = updated.replace(anchor, anchor + "\n" + override, 1)

    locale_without_pseudolocales = (
        "# A single packaged locale does not need a list subtraction.\n"
        'if (macos_single_locale != "") {\n'
        "  locales_without_pseudolocales = platform_pak_locales\n"
        "} else {\n"
        "  locales_without_pseudolocales = platform_pak_locales - pseudolocales\n"
        "}\n"
    )
    if locale_without_pseudolocales not in updated:
        locale_anchor = "locales_without_pseudolocales = platform_pak_locales - pseudolocales\n"
        if locale_anchor not in updated:
            fail(f"unexpected derived locale list shape in {path}")
        updated = updated.replace(
            locale_anchor,
            "\n" + locale_without_pseudolocales,
            1,
        )

    grd_path = source / "chrome" / "app" / "resources" / "locale_settings_mac.grd"
    if not grd_path.is_file():
        fail(f"macOS compatibility target is missing: {grd_path}")
    grd_original = grd_path.read_text(encoding="utf-8")
    output_line = re.compile(
        r'^[ \t]*<output filename="platform_locale_settings_[^"]+\.pak"[^>]*/>\n?',
        re.MULTILINE,
    )
    grd_updated = output_line.sub(
        lambda match: match.group(0)
        if "platform_locale_settings_en-US.pak" in match.group(0)
        else "",
        grd_original,
    )
    if grd_updated == grd_original:
        grd_outputs = {
            "status": "reused",
            "name": "english-only-macos-platform-grit-outputs",
            "path": str(grd_path),
            "locale": "en-US",
        }
    else:
        grd_path.write_text(grd_updated, encoding="utf-8")
        grd_outputs = {
            "status": "applied",
            "name": "english-only-macos-platform-grit-outputs",
            "path": str(grd_path),
            "locale": "en-US",
            "sha256_before": hashlib.sha256(grd_original.encode()).hexdigest(),
            "sha256_after": hashlib.sha256(grd_updated.encode()).hexdigest(),
        }

    if updated == original and grd_outputs["status"] == "reused":
        return {
            "status": "reused",
            "name": "english-only-macos-locale-set",
            "path": str(path),
            "locale": "en-US",
            "grd_outputs": grd_outputs,
        }
    path.write_text(updated, encoding="utf-8")
    return {
        "status": "applied",
        "name": "english-only-macos-locale-set",
        "path": str(path),
        "locale": "en-US",
        "sha256_before": hashlib.sha256(original.encode()).hexdigest(),
        "sha256_after": hashlib.sha256(updated.encode()).hexdigest(),
        "grd_outputs": grd_outputs,
    }


def apply_macos_profile_compatibility(source: Path) -> dict[str, Any]:
    """Apply source fixes required by the lightweight macOS profile."""

    path = source / "chrome" / "test" / "BUILD.gn"
    if not path.is_file():
        fail(f"macOS compatibility target is missing: {path}")
    original = path.read_text(encoding="utf-8")
    stale_edges = (
        '      "//chrome/common/safe_browsing:archive_analyzer_results",\n'
        '      "//chrome/common/safe_browsing:disk_image_type_sniffer_mac",\n'
    )
    if stale_edges in original:
        updated = original.replace(stale_edges, "", 1)
        path.write_text(updated, encoding="utf-8")
        safe_browsing = {
            "status": "applied",
            "name": "safe-browsing-disabled-macos-unit-test-edges",
            "path": str(path),
            "removed_edges": [
                "//chrome/common/safe_browsing:archive_analyzer_results",
                "//chrome/common/safe_browsing:disk_image_type_sniffer_mac",
            ],
            "sha256_before": hashlib.sha256(original.encode()).hexdigest(),
            "sha256_after": hashlib.sha256(updated.encode()).hexdigest(),
        }
    elif all(edge not in original for edge in stale_edges.splitlines()):
        safe_browsing = {
            "status": "reused",
            "name": "safe-browsing-disabled-macos-unit-test-edges",
            "path": str(path),
            "removed_edges": [],
        }
    else:
        fail(f"unexpected Safe Browsing test dependency shape in {path}")

    return {
        "status": "complete",
        "safe_browsing": safe_browsing,
        "screen_ai": _apply_disabled_screen_ai_compatibility(source),
        "english_only_locale": _apply_english_only_locale_compatibility(source),
    }


def stable_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare_source(layout: WorkLayout) -> dict[str, Any]:
    layout.ensure()
    lock = load_lock(layout)
    if not layout.source.is_dir():
        fail(f"source acquisition has not completed: {layout.source}; run fetch")
    environment = isolated_environment(layout, network="none")
    prepare_marker = layout.metadata / "prepare-macos.json"
    identity = {
        "lock": {
            "chromium_version": lock["chromium"]["version"],
            "chromium_commit": lock["chromium"]["commit"],
            "ungoogled_commit": lock["ungoogled"]["commit"],
            "patch_series_sha256": lock["ungoogled"].get("patch_series_sha256"),
        },
        "profile": DEFAULT_PROFILE,
        "macos_compatibility": "safe-browsing-disabled-macos-unit-test-edges-v1",
        "excluded_layers": ["portablelinux", "alpine", "copium", "musl", "headless"],
    }
    fingerprint = stable_digest(identity)
    if prepare_marker.is_file():
        previous = json.loads(prepare_marker.read_text(encoding="utf-8"))
        if previous.get("fingerprint") == fingerprint and previous.get("status") == "complete":
            compatibility = apply_macos_profile_compatibility(layout.source)
            if previous.get("macos_compatibility") != compatibility:
                previous["macos_compatibility"] = compatibility
                atomic_json(prepare_marker, previous)
            print(f"prepare: unchanged prepared source reused at {layout.source}")
            return previous
        fail("prepared source identity differs from the lock; use reset --yes before preparing again")

    ungoogled_archive = layout.inputs / lock["ungoogled"]["source"]["filename"]
    verify_locked_file(ungoogled_archive, lock["ungoogled"]["source"])
    config_extract = layout.tmp / f"ungoogled-{os.getpid()}-{time.time_ns()}"
    config_extract.mkdir(parents=True, exist_ok=False)
    try:
        safe_extract_tar(ungoogled_archive, config_extract)
        roots = [path for path in config_extract.iterdir() if path.is_dir()]
        if len(roots) != 1:
            fail(f"expected one ungoogled source root, found {len(roots)}")
        core_root = roots[0]
        version_file = layout.source / "chrome" / "VERSION"
        expected_parts = lock["chromium"]["version"].split(".")
        actual = dict(
            re.findall(r"^(MAJOR|MINOR|BUILD|PATCH)=(\d+)$", version_file.read_text(encoding="utf-8"), re.MULTILINE)
        )
        expected = dict(zip(("MAJOR", "MINOR", "BUILD", "PATCH"), expected_parts))
        if actual != expected:
            fail(f"chrome/VERSION mismatch: expected {expected}, got {actual}")
        patch_records = apply_ungoogled_patches(layout.source, core_root, layout, environment)
        pruning = apply_pruning(layout.source, core_root / "pruning.list", lock["chromium"]["version"])
        domains = apply_domain_substitution(
            layout.source,
            core_root / "domain_substitution.list",
            core_root / "domain_regex.list",
        )
        compatibility = apply_macos_profile_compatibility(layout.source)
        flags = core_root / "flags.gn"
        flags_copy = layout.metadata / "ungoogled-flags.gn"
        shutil.copy2(flags, flags_copy)
        report = {
            "schema": 1,
            "status": "complete",
            "network": "none",
            "fingerprint": fingerprint,
            "identity": identity,
            "chromium_version": lock["chromium"]["version"],
            "chromium_commit": lock["chromium"]["commit"],
            "source": str(layout.source),
            "patches": patch_records,
            "pruning": pruning,
            "domain_substitution": domains,
            "macos_compatibility": compatibility,
            "flags": str(flags_copy),
            "macos_patch_policy": {
                "reference_commit": lock["macos_reference"]["commit"],
                "reference_version": lock["macos_reference"].get("version"),
                "applied": [],
                "omitted_reason": "the available packaging reference is version-mismatched; native Chromium macOS files are retained",
            },
        }
        atomic_json(prepare_marker, report)
        atomic_write(layout.metadata / "source-complete.stamp", "status=complete\nnetwork=none\n")
        print(f"prepare: applied {len(patch_records)} ungoogled patches; source is {layout.source}")
        return report
    except Exception:
        # Preserve the partial source for diagnosis, but never publish success.
        raise
    finally:
        shutil.rmtree(config_extract, ignore_errors=True)


def read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        fail(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {label} {path}: {error}")
    if not isinstance(value, dict):
        fail(f"{label} is not a JSON object: {path}")
    return value


def find_executable(candidates: Sequence[Path], name: str, environment: Mapping[str, str]) -> Path:
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    resolved = command_path(name, environment)
    if resolved:
        return Path(resolved)
    fail(f"{name} was not found in the prepared Chromium source or controlled PATH")


def toolchain_environment(
    layout: WorkLayout,
    source: Path,
    environment: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, Any], dict[str, str]]:
    xcode = xcode_identity(environment)
    child_environment = dict(environment)
    child_environment["XCODE_CLANG"] = xcode["clang"]
    llvm = select_llvm(child_environment, source)
    child_environment = isolated_environment(layout, network="none", llvm_root=Path(llvm["root"]))
    child_environment["XCODE_CLANG"] = xcode["clang"]
    child_environment["DEVELOPER_DIR"] = environment.get(
        "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer"
    )
    sdk = command_output(
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
        env=child_environment,
        check=False,
    )
    if sdk.returncode or not sdk.stdout.strip():
        fail("the selected Xcode installation cannot provide a macOS SDK path")
    child_environment["SDKROOT"] = sdk.stdout.strip()
    clang = Path(llvm["clang"])
    clangxx = Path(llvm["clangxx"])
    child_environment.update({"CC": str(clang), "CXX": str(clangxx)})
    for variable, executable in (
        ("AR", "llvm-ar"),
        ("NM", "llvm-nm"),
        ("OBJCOPY", "llvm-objcopy"),
        ("RANLIB", "llvm-ranlib"),
        ("STRIP", "llvm-strip"),
    ):
        candidate = Path(llvm["root"]) / "bin" / executable
        if candidate.is_file():
            child_environment[variable] = str(candidate)
    return child_environment, llvm, xcode


def configure_source(layout: WorkLayout) -> dict[str, Any]:
    layout.ensure()
    lock = load_lock(layout)
    prepare = read_json(layout.metadata / "prepare-macos.json", "source preparation report")
    if prepare.get("status") != "complete":
        fail("source preparation is not complete; run prepare")
    source = layout.source
    environment = isolated_environment(layout, network="none")
    post_fetch_minimum = int(
        os.environ.get("MACOS_POST_FETCH_MIN_FREE_GB", str(POST_FETCH_MIN_FREE_GB))
    )
    run_doctor(
        layout,
        require_references=False,
        minimum_free_gb=post_fetch_minimum,
        enforce_full_build_floor=False,
    )
    environment, llvm, xcode = toolchain_environment(layout, source, environment)
    profile_path = layout.repo_root / "config" / "profiles" / f"{DEFAULT_PROFILE}.gn"
    if not profile_path.is_file():
        fail(f"macOS GN profile is missing: {profile_path}")
    flags_path = layout.metadata / "ungoogled-flags.gn"
    if not flags_path.is_file():
        fail(f"prepared ungoogled flags are missing: {flags_path}")
    profile = profile_path.read_text(encoding="utf-8")
    profile = profile.replace("@TARGET_CPU@", "arm64")
    profile = profile.replace("@LLVM_ROOT@", str(llvm["root"]))
    profile = profile.replace("@CLANG_MAJOR@", str(llvm["version_tuple"][0]))
    cache = compiler_cache_report(environment)
    cache_wrapper = cache.get("path", "") if cache.get("status") == "enabled" else ""
    args_text = (
        "# Generated by the macOS backend; do not hand-edit this output.\n"
        f"# Chromium lock: {lock['chromium']['commit']}\n"
        f"# LLVM: {llvm['version']} from {llvm['source']}\n\n"
        + flags_path.read_text(encoding="utf-8")
        + "\n\n"
        + profile
        + "\n\n"
        + "# Use only an explicitly selected local cache wrapper; remote execution is disabled.\n"
        + f"cc_wrapper = {json.dumps(cache_wrapper)}\n"
    )
    args_path = layout.metadata / f"{DEFAULT_PROFILE}.args.gn"
    atomic_write(args_path, args_text.rstrip() + "\n")
    layout.output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args_path, layout.output / "args.gn")
    gn = find_executable(
        [
            source / "buildtools" / "mac" / "gn",
            source / "buildtools" / "gn" / "gn",
            layout.tools / "depot_tools" / "gn",
        ],
        "gn",
        environment,
    )
    gn_command = [str(gn), "gen", str(layout.output), "--fail-on-unused-args"]
    stream_command(
        gn_command,
        env=environment,
        cwd=source,
        log_path=layout.logs / "gn-gen.log",
        timeout=1800,
    )
    listed = command_output(
        [str(gn), "args", str(layout.output), "--list", "--json"],
        env=environment,
        cwd=source,
        check=False,
    )
    args_listing: Any
    try:
        args_listing = json.loads(listed.stdout) if listed.stdout.strip() else {}
    except json.JSONDecodeError:
        args_listing = {"raw": listed.stdout.strip()}
    report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "profile": DEFAULT_PROFILE,
        "source": str(source),
        "output": str(layout.output),
        "args": str(args_path),
        "gn": str(gn),
        "gn_command": gn_command,
        "gn_args_listing_status": listed.returncode,
        "gn_args_listing": args_listing,
        "llvm": {key: value for key, value in llvm.items() if key != "version_tuple"},
        "xcode": xcode,
        "compiler_cache": cache,
        "pgo": {
            "phase": 0,
            "status": "not-qualified-no-profile-input",
            "reason": "no locked Chromium macOS profile-guided data was supplied",
        },
    }
    atomic_json(layout.metadata / "configure-macos.json", report)
    atomic_write(layout.metadata / "configure-complete.stamp", "status=complete\nnetwork=none\n")
    print(f"configure: GN generated {layout.output} with LLVM {llvm['version']}")
    return report


def clean_single_locale_outputs(layout: WorkLayout) -> dict[str, Any]:
    """Remove stale non-English generated outputs after a locale-profile change."""

    args_path = layout.metadata / f"{DEFAULT_PROFILE}.args.gn"
    if not args_path.is_file():
        return {"status": "not-applicable", "removed": 0}
    args = args_path.read_text(encoding="utf-8")
    if 'macos_single_locale = "en-US"' not in args:
        return {"status": "not-applicable", "removed": 0}

    removed = 0

    def remove_path(path: Path) -> None:
        nonlocal removed
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed += 1
        elif path.is_dir():
            shutil.rmtree(path)
            removed += 1

    for path in (layout.output / "gen" / "chrome").glob("platform_locale_settings_*.pak*"):
        remove_path(path)
    for path in (layout.output / "gen" / "repack" / "locales").glob("*.pak*"):
        remove_path(path)

    allowed_locale_outputs = {
        "en.lproj",
    }
    for resources in (
        layout.output / "Chromium.app" / "Contents" / "Resources",
        layout.output / "Chromium Framework.framework" / "Resources",
        layout.output
        / "Chromium.app"
        / "Contents"
        / "Frameworks"
        / "Chromium Framework.framework"
        / "Resources",
    ):
        if not resources.is_dir():
            continue
        for path in resources.glob("*.lproj"):
            if path.name not in allowed_locale_outputs:
                remove_path(path)

    return {
        "status": "complete",
        "locale": "en-US",
        "removed": removed,
        "allowed_outputs": sorted(allowed_locale_outputs),
    }


def build_source(layout: WorkLayout, jobs: Optional[str]) -> dict[str, Any]:
    layout.ensure()
    configure = read_json(layout.metadata / "configure-macos.json", "configuration report")
    if configure.get("status") != "complete":
        fail("configuration is not complete; run configure")
    source = layout.source
    if not source.is_dir():
        fail(f"prepared source is missing: {source}")
    configured_cache = configure.get("compiler_cache", {})
    cache_mode = (
        str(configured_cache.get("kind"))
        if configured_cache.get("status") == "enabled"
        else "off"
    )
    environment = isolated_environment(
        layout,
        network="none",
        llvm_root=Path(configure["llvm"]["root"]),
        compiler_cache=cache_mode,
    )
    actual_cache = compiler_cache_report(environment)
    if configured_cache.get("status") == "enabled" and actual_cache.get("path") != configured_cache.get("path"):
        fail(
            "the configured compiler cache wrapper is no longer available at the selected path; "
            "rerun configure to refresh the cache selection"
        )
    environment["CC"] = configure["llvm"]["clang"]
    environment["CXX"] = configure["llvm"]["clangxx"]
    ninja = find_executable(
        [
            source / "third_party" / "ninja" / "ninja",
            source / "buildtools" / "mac" / "ninja",
            layout.tools / "depot_tools" / "ninja",
        ],
        "ninja",
        environment,
    )
    requested_jobs = jobs or os.environ.get("MACOS_BUILD_JOBS")
    if requested_jobs:
        try:
            build_jobs = int(requested_jobs)
        except ValueError:
            fail(f"--jobs must be a positive integer: {requested_jobs}")
    else:
        cpu_count = os.cpu_count() or 2
        build_jobs = max(1, min(8, cpu_count - 1))
    if build_jobs < 1:
        fail(f"--jobs must be a positive integer: {build_jobs}")
    locale_cleanup = clean_single_locale_outputs(layout)
    command = [str(ninja), "-C", str(layout.output), "-j", str(build_jobs), "chrome"]
    stream_command(
        command,
        env=environment,
        cwd=source,
        log_path=layout.logs / "ninja-chrome.log",
        timeout=24 * 60 * 60,
    )
    app = layout.output / "Chromium.app"
    executable = app / "Contents" / "MacOS" / "Chromium"
    if not app.is_dir() or not executable.is_file():
        fail(f"ninja reported success but the macOS application is missing: {executable}")
    report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "source": str(source),
        "output": str(layout.output),
        "app": str(app),
        "executable": str(executable),
        "ninja": str(ninja),
        "jobs": build_jobs,
        "compiler_cache": actual_cache,
        "locale_cleanup": locale_cleanup,
        "pgo": configure.get("pgo", {}),
    }
    atomic_json(layout.metadata / "build-macos.json", report)
    atomic_write(layout.metadata / "build-complete.stamp", "status=complete\nnetwork=none\n")
    print(f"build: produced {app}")
    return report


def compiler_cache_stats(layout: WorkLayout) -> dict[str, Any]:
    """Show local compiler-cache statistics without changing cache contents."""

    layout.ensure()
    configured_cache: dict[str, Any] = {}
    configure_path = layout.metadata / "configure-macos.json"
    if configure_path.is_file():
        configured_cache = read_json(configure_path, "configuration report").get("compiler_cache", {})
    cache_mode = (
        str(configured_cache.get("kind"))
        if configured_cache.get("status") == "enabled"
        else ("off" if configured_cache else None)
    )
    environment = isolated_environment(layout, network="none", compiler_cache=cache_mode)
    cache = compiler_cache_report(environment)
    report: dict[str, Any] = {
        "schema": 1,
        "status": "complete",
        "cache": cache,
        "output": "",
        "ninja_incremental": {
            "status": "unavailable",
            "reason": "the configured output directory is not present",
        },
    }
    if cache.get("status") == "enabled":
        result = command_output([str(cache["path"]), "--show-stats"], env=environment, check=False)
        report["status"] = "complete" if result.returncode == 0 else "failed"
        report["output"] = (result.stdout or result.stderr or "").strip()
        if result.returncode:
            report["returncode"] = result.returncode
    else:
        report["output"] = "compiler cache disabled; Ninja incremental outputs are the active local cache"
    if layout.source.is_dir() and layout.output.is_dir():
        try:
            ninja = find_executable(
                [
                    layout.source / "third_party" / "ninja" / "ninja",
                    layout.source / "buildtools" / "mac" / "ninja",
                    layout.tools / "depot_tools" / "ninja",
                ],
                "ninja",
                environment,
            )
            dry_run = command_output(
                [str(ninja), "-C", str(layout.output), "-n", "chrome"],
                env=environment,
                cwd=layout.source,
                check=False,
            )
            dry_output = (dry_run.stdout or dry_run.stderr or "").strip()
            report["ninja_incremental"] = {
                "status": (
                    "clean"
                    if dry_run.returncode == 0 and "no work to do" in dry_output
                    else ("pending" if dry_run.returncode == 0 else "failed")
                ),
                "ninja": str(ninja),
                "target": "chrome",
                "returncode": dry_run.returncode,
                "dry_run_output": dry_output[-4000:],
            }
        except MacOSFailure as error:
            report["ninja_incremental"] = {
                "status": "unavailable",
                "reason": str(error),
            }
    atomic_json(layout.metadata / "compiler-cache-stats.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def validate_app_path(raw: str | Path, layout: WorkLayout, label: str, *, built: bool = False) -> Path:
    app = canonical_path(raw, label, must_exist=True)
    if app.suffix != ".app" or not app.is_dir():
        fail(f"{label} must be an existing .app directory: {app}")
    if built:
        layout.assert_owned(app, label)
    elif not is_within(app, WORK_PREFIX):
        fail(f"{label} must be under /Volumes/dev: {app}")
    return app


def path_is_forbidden_dependency(value: str, layout: WorkLayout) -> Optional[str]:
    forbidden = (
        "/opt/homebrew",
        "/usr/local",
        "/Users",
        str(layout.repo_root),
        str(layout.root),
        str(layout.source),
        str(layout.output),
    )
    for prefix in forbidden:
        if value == prefix or value.startswith(prefix + "/"):
            return prefix
    return None


@contextmanager
def macho_audit_path(path: Path, layout: WorkLayout) -> Iterator[Path]:
    """Give Apple's otool a safe alias for Mach-O names that look like archives."""

    if " (" not in str(path):
        yield path
        return
    alias_directory = layout.tmp / "macho-audit"
    alias_directory.mkdir(parents=True, exist_ok=True)
    alias = alias_directory / hashlib.sha256(str(path).encode()).hexdigest()
    target = path.resolve(strict=False)
    created = False
    if alias.exists() or alias.is_symlink():
        if not alias.is_symlink() or alias.resolve(strict=False) != target:
            fail(f"unexpected Mach-O audit alias collision: {alias}")
    else:
        os.symlink(target, alias)
        created = True
    try:
        yield alias
    finally:
        if created:
            alias.unlink(missing_ok=True)


def macho_architectures(path: Path, environment: Mapping[str, str]) -> Optional[list[str]]:
    file_command = "/usr/bin/file"
    lipo_command = "/usr/bin/lipo"
    if not Path(file_command).is_file() or not Path(lipo_command).is_file():
        fail("macOS bundle auditing requires /usr/bin/file and /usr/bin/lipo")
    file_result = command_output([file_command, "-b", str(path)], env=environment, check=False)
    if file_result.returncode or "Mach-O" not in file_result.stdout:
        return None
    architectures = command_output([lipo_command, "-archs", str(path)], env=environment, check=False)
    if architectures.returncode:
        fail(f"could not inspect Mach-O architectures: {path}")
    values = architectures.stdout.strip().split()
    if values != ["arm64"]:
        fail(f"bundle contains a non-native or universal Mach-O ({' '.join(values)}): {path}")
    return values


def audit_bundle(app: Path, layout: WorkLayout, environment: Mapping[str, str]) -> dict[str, Any]:
    """Audit a bundle before signing or acceptance promotion."""

    app = validate_app_path(app, layout, "Chromium application")
    executable = app / "Contents" / "MacOS" / "Chromium"
    if not executable.is_file() or executable.is_symlink():
        fail(f"Chromium.app has no regular native executable: {executable}")
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        fail(f"Chromium.app is missing Info.plist: {plist_path}")
    try:
        with plist_path.open("rb") as stream:
            plist = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        fail(f"cannot read Chromium.app Info.plist: {error}")
    if not isinstance(plist, dict):
        fail(f"Chromium.app Info.plist is not a dictionary: {plist_path}")
    bundle_executable = plist.get("CFBundleExecutable")
    if bundle_executable and bundle_executable != "Chromium":
        fail(f"unexpected CFBundleExecutable {bundle_executable!r} in {plist_path}")

    mach_o: list[str] = []
    dependencies: list[dict[str, str]] = []
    symlinks: list[dict[str, str]] = []
    files = 0
    for directory, directory_names, file_names in os.walk(app, followlinks=False):
        base = Path(directory)
        for name in list(directory_names):
            candidate = base / name
            if candidate.is_symlink():
                directory_names.remove(name)
                target = candidate.resolve(strict=False)
                if not is_within(target, app.resolve()) or not target.exists():
                    fail(f"bundle symlink escapes or is dangling: {candidate} -> {os.readlink(candidate)}")
                symlinks.append({"path": str(candidate.relative_to(app)), "target": os.readlink(candidate)})
        for name in file_names:
            candidate = base / name
            if candidate.is_symlink():
                target = candidate.resolve(strict=False)
                if not is_within(target, app.resolve()) or not target.exists():
                    fail(f"bundle symlink escapes or is dangling: {candidate} -> {os.readlink(candidate)}")
                symlinks.append({"path": str(candidate.relative_to(app)), "target": os.readlink(candidate)})
                continue
            if not candidate.is_file():
                continue
            files += 1
            architectures = macho_architectures(candidate, environment)
            if architectures is None:
                continue
            relative = str(candidate.relative_to(app))
            mach_o.append(relative)
            with macho_audit_path(candidate, layout) as inspect_path:
                otool = command_output(
                    ["/usr/bin/otool", "-L", str(inspect_path)],
                    env=environment,
                    check=False,
                )
                if otool.returncode:
                    fail(f"could not inspect Mach-O dependencies: {candidate}")
                for line in otool.stdout.splitlines()[1:]:
                    dependency = line.strip().split(" (", 1)[0]
                    if not dependency:
                        continue
                    forbidden = path_is_forbidden_dependency(dependency, layout)
                    if forbidden:
                        fail(
                            "bundle dependency points at forbidden host path "
                            f"{forbidden}: {candidate} -> {dependency}"
                        )
                    dependencies.append({"binary": relative, "path": dependency})
                load_commands = command_output(
                    ["/usr/bin/otool", "-l", str(inspect_path)],
                    env=environment,
                    check=False,
                )
                if load_commands.returncode:
                    fail(f"could not inspect Mach-O load commands: {candidate}")
                for line in load_commands.stdout.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("path "):
                        continue
                    rpath = stripped[len("path ") :].split(" (", 1)[0]
                    forbidden = path_is_forbidden_dependency(rpath, layout)
                    if forbidden:
                        fail(
                            "bundle rpath points at forbidden host path "
                            f"{forbidden}: {candidate} -> {rpath}"
                        )

    if str(executable.relative_to(app)) not in mach_o:
        fail(f"Chromium.app executable is not an arm64 Mach-O: {executable}")
    return {
        "status": "complete",
        "app": str(app),
        "executable": str(executable),
        "bundle_identifier": plist.get("CFBundleIdentifier", ""),
        "bundle_version": plist.get("CFBundleShortVersionString", ""),
        "files": files,
        "mach_o": mach_o,
        "dependencies": dependencies,
        "symlinks": symlinks,
        "architectures": {relative: ["arm64"] for relative in mach_o},
    }


def sign_bundle(app: Path, layout: WorkLayout, environment: Mapping[str, str]) -> dict[str, Any]:
    app = validate_app_path(app, layout, "bundle to sign")
    codesign = Path("/usr/bin/codesign")
    if not codesign.is_file():
        fail("macOS bundle signing requires /usr/bin/codesign")
    code_objects: set[Path] = {app}
    for candidate in app.rglob("*"):
        if candidate.is_symlink():
            continue
        if candidate.is_dir() and candidate.suffix.lower() in {".app", ".framework", ".xpc", ".appex", ".plugin"}:
            code_objects.add(candidate)
        elif candidate.is_file() and (
            candidate.suffix.lower() in {".dylib", ".so", ".bundle"}
            or macho_architectures(candidate, environment) is not None
        ):
            code_objects.add(candidate)
    ordered = sorted(code_objects, key=lambda path: len(path.parts), reverse=True)
    signed: list[str] = []
    for code_object in ordered:
        result = command_output(
            [str(codesign), "--force", "--sign", "-", "--timestamp=none", str(code_object)],
            env=environment,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            fail(f"ad-hoc signing failed for {code_object}: {detail[-1600:]}")
        signed.append(str(code_object.relative_to(app)))
    verification = command_output(
        [str(codesign), "--verify", "--deep", "--strict", "--verbose=2", str(app)],
        env=environment,
        check=False,
    )
    if verification.returncode:
        detail = (verification.stderr or verification.stdout or "").strip()
        fail(f"signed bundle failed codesign verification: {detail[-1600:]}")
    entitlements = command_output(
        [str(codesign), "-d", "--entitlements", ":-", str(app)],
        env=environment,
        check=False,
    )
    return {
        "status": "complete",
        "identity": "-",
        "timestamp": False,
        "signed_objects": signed,
        "verification": (verification.stderr or verification.stdout or "").strip(),
        "entitlements": (entitlements.stdout or entitlements.stderr or "").strip(),
    }


def bundle_fingerprint(app: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(app.rglob("*"), key=lambda value: str(value.relative_to(app))):
        relative = str(path.relative_to(app)).encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8") + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0" + str(path.stat().st_size).encode() + b"\0")
            digest.update(bytes.fromhex(sha256(path)))
        elif path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
    return digest.hexdigest()


def copy_bundle_atomic(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        fail(f"refusing to overwrite an existing staged bundle: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.copy-{os.getpid()}-{time.time_ns()}"
    try:
        shutil.copytree(source, temporary, symlinks=True)
        os.replace(temporary, destination)
    finally:
        if temporary.exists() or temporary.is_symlink():
            shutil.rmtree(temporary, ignore_errors=True)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as stream:
        for member in stream.infolist():
            target = (destination / member.filename).resolve(strict=False)
            if not is_within(target, destination.resolve()):
                fail(f"extension archive member escapes extraction root: {member.filename}")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                fail(f"extension archive contains a symlink: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                fail(f"extension archive contains a duplicate path: {member.filename}")
            with stream.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def prepare_extension(layout: WorkLayout, lock: Mapping[str, Any]) -> Path:
    entry = lock["test_inputs"]["ublock_origin"]
    archive = layout.inputs / entry["filename"]
    verify_locked_file(archive, entry)
    destination = layout.cache / "extensions" / "ublock-origin" / str(entry["version"]) / "chromium"
    manifest = destination / "manifest.json"
    if not manifest.is_file():
        if destination.exists() or destination.is_symlink():
            fail(f"owned extension destination is not a valid directory: {destination}")
        extraction = destination.parent / f".{destination.name}.extract-{os.getpid()}-{time.time_ns()}"
        try:
            safe_extract_zip(archive, extraction)
            roots = [path for path in extraction.iterdir() if path.is_dir()]
            root = roots[0] if len(roots) == 1 and (roots[0] / "manifest.json").is_file() else extraction
            if not (root / "manifest.json").is_file():
                fail(f"uBlock archive has no manifest.json: {archive}")
            os.replace(root, destination)
        finally:
            shutil.rmtree(extraction, ignore_errors=True)
    return destination


def stage_runtime(layout: WorkLayout) -> dict[str, Any]:
    layout.ensure()
    build = read_json(layout.metadata / "build-macos.json", "build report")
    if build.get("status") != "complete":
        fail("the build report is not complete; run build")
    source_app = validate_app_path(build.get("app", layout.output / "Chromium.app"), layout, "built Chromium.app", built=True)
    environment = isolated_environment(layout, network="none")
    source_audit = audit_bundle(source_app, layout, environment)
    pending = layout.candidates / f".pending-{os.getpid()}-{time.time_ns()}" / "Chromium.app"
    pending.parent.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copytree(source_app, pending, symlinks=True)
        signing = sign_bundle(pending, layout, environment)
        staged_audit = audit_bundle(pending, layout, environment)
        fingerprint = bundle_fingerprint(pending)
        destination = layout.candidates / fingerprint / "Chromium.app"
        if destination.exists():
            shutil.rmtree(pending.parent)
            staged_audit = audit_bundle(destination, layout, environment)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending.parent, destination.parent)
        candidate = {
            "schema": 1,
            "status": "complete",
            "fingerprint": fingerprint,
            "app": str(destination),
            "executable": str(destination / "Contents" / "MacOS" / "Chromium"),
            "source_app": str(source_app),
            "source_audit": source_audit,
            "signing": signing,
            "audit": staged_audit,
            "build": build,
        }
        atomic_json(layout.stage / "candidate.json", candidate)
        print(f"stage-runtime: signed and audited candidate {destination}")
        return candidate
    finally:
        if pending.parent.exists():
            shutil.rmtree(pending.parent, ignore_errors=True)


def package_bundle(layout: WorkLayout) -> dict[str, Any]:
    """Package the same signed, audited candidate used by local staging."""

    layout.ensure()
    candidate = read_json(layout.stage / "candidate.json", "staged candidate")
    if candidate.get("status") != "complete":
        fail("staged candidate is not complete; run stage-runtime")
    if candidate.get("audit", {}).get("status") != "complete":
        fail("staged candidate has no complete bundle audit; run stage-runtime")
    app = validate_app_path(candidate.get("app", ""), layout, "staged candidate", built=True)
    candidates_root = layout.candidates.resolve()
    if candidates_root not in app.parents or app.name != "Chromium.app":
        fail(f"staged candidate escapes the candidate root: {app}")
    fingerprint = bundle_fingerprint(app)
    if candidate.get("fingerprint") != fingerprint:
        fail(
            "staged candidate fingerprint changed: "
            f"expected {candidate.get('fingerprint')}, got {fingerprint}"
        )
    lock = load_lock(layout)
    version = str(lock["chromium"]["version"])
    release = layout.release
    archive = release / f"Chromium-{version}-macos-arm64.zip"
    temporary = release / f".{archive.name}.tmp-{os.getpid()}-{time.time_ns()}"
    environment = isolated_environment(layout, network="none")
    ditto = Path("/usr/bin/ditto")
    if not ditto.is_file():
        fail("macOS packaging requires /usr/bin/ditto")
    try:
        command_output(
            [
                str(ditto),
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(app),
                str(temporary),
            ],
            env=environment,
            cwd=layout.root,
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            fail(f"ditto did not create a non-empty package: {temporary}")
        digest = sha256(temporary)
        os.replace(temporary, archive)
        checksum = archive.with_name(f"{archive.name}.sha256")
        atomic_write(checksum, f"{digest}  {archive.name}\n")
    finally:
        temporary.unlink(missing_ok=True)
    report = {
        "schema": 1,
        "status": "complete",
        "version": version,
        "architecture": "arm64",
        "app": str(app),
        "fingerprint": fingerprint,
        "archive": str(archive),
        "sha256": digest,
        "size": archive.stat().st_size,
        "acceptance": "not-run-in-remote-ci" if os.environ.get("MACOS_SKIP_ACCEPTANCE") else "local-stage-only",
    }
    atomic_json(layout.metadata / "package-macos.json", report)
    print(f"package: created {archive} ({report['size']} bytes)")
    return report


def acceptance_harness_manifest(layout: WorkLayout) -> tuple[Path, Path]:
    harness = layout.harness / "shadowdriver"
    source = harness / "src" / "main.rs"
    manifest = harness / "Cargo.toml"
    source.parent.mkdir(parents=True, exist_ok=True)
    template = layout.repo_root / "scripts" / "shadowdriver_acceptance.rs"
    if not template.is_file():
        fail(f"Shadowdriver acceptance source is missing: {template}")
    shutil.copy2(template, source)
    atomic_write(
        manifest,
        """[package]
name = "chromium-build-shadowdriver-acceptance"
version = "0.1.0"
edition = "2024"

[dependencies]
async-io = "2"
futures-lite = "2"
shadowdriver = { path = "/Volumes/dev/d/shadowdriver" }
""",
    )
    return manifest, source


def cargo_executable(environment: Mapping[str, str]) -> Path:
    cargo = command_path("cargo", environment)
    if cargo:
        return Path(cargo)
    for candidate in (
        Path("/opt/homebrew/opt/rustup/bin/cargo"),
        Path("/opt/homebrew/bin/cargo"),
    ):
        if candidate.is_file():
            return candidate
    fail("cargo is unavailable in the controlled toolchain PATH")


def fetch_acceptance_dependencies(layout: WorkLayout, environment: Mapping[str, str]) -> dict[str, Any]:
    manifest, source = acceptance_harness_manifest(layout)
    cargo = cargo_executable(environment)
    fetch_environment = dict(environment)
    fetch_environment["CARGO_TARGET_DIR"] = str(layout.harness / "target")
    fetch_environment["SHADOWDRIVER_CACHE_DIR"] = str(layout.cache / "shadowdriver")
    stream_command(
        [str(cargo), "fetch", "--manifest-path", str(manifest)],
        env=fetch_environment,
        cwd=layout.harness,
        log_path=layout.logs / "cargo-fetch.log",
        timeout=3600,
    )
    return {"manifest": str(manifest), "source": str(source), "cargo": str(cargo), "status": "complete"}


def run_shadowdriver_harness(layout: WorkLayout, lock: Mapping[str, Any], app: Path) -> dict[str, Any]:
    environment = isolated_environment(layout, network="none")
    extension = prepare_extension(layout, lock)
    manifest, source = acceptance_harness_manifest(layout)
    cargo = cargo_executable(environment)
    profile = layout.harness / "profiles" / f"acceptance-{os.getpid()}-{time.time_ns()}"
    harness_environment = dict(environment)
    harness_environment.update(
        {
            "CARGO_TARGET_DIR": str(layout.harness / "target"),
            "SHADOWDRIVER_CACHE_DIR": str(layout.cache / "shadowdriver"),
            "CHROMIUM_TEST_EXECUTABLE": str(app / "Contents" / "MacOS" / "Chromium"),
            "CHROMIUM_TEST_PROFILE": str(profile),
            "CHROMIUM_TEST_EXTENSION": str(extension),
        }
    )
    command = [str(cargo), "run", "--offline", "--manifest-path", str(manifest), "--locked"]
    stream_command(
        command,
        env=harness_environment,
        cwd=layout.harness,
        log_path=layout.logs / "shadowdriver-acceptance.log",
        timeout=3600,
    )
    return {
        "status": "complete",
        "network": "none",
        "command": command,
        "manifest": str(manifest),
        "source": str(source),
        "cargo": str(cargo),
        "profile": str(profile),
        "extension": str(extension),
        "pages": 32,
        "architecture": "arm64",
    }


def promote_accepted(layout: WorkLayout, candidate: Path, fingerprint: str, environment: Mapping[str, str]) -> Path:
    destination = layout.accepted / fingerprint / "Chromium.app"
    if destination.exists():
        audit_bundle(destination, layout, environment)
    else:
        copy_bundle_atomic(candidate, destination)
        audit_bundle(destination, layout, environment)
    accepted = {
        "schema": 1,
        "status": "complete",
        "fingerprint": fingerprint,
        "app": str(destination),
        "executable": str(destination / "Contents" / "MacOS" / "Chromium"),
        "accepted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    atomic_json(layout.stage / "accepted.json", accepted)
    return destination


def test_staged(layout: WorkLayout, supplied_app: Optional[str]) -> dict[str, Any]:
    layout.ensure()
    lock = load_lock(layout)
    candidate_record: Optional[dict[str, Any]] = None
    if supplied_app:
        app = validate_app_path(supplied_app, layout, "--app Chromium.app")
        input_kind = "supplied"
    else:
        candidate_record = read_json(layout.stage / "candidate.json", "staged candidate")
        if candidate_record.get("status") != "complete":
            fail("staged candidate is not complete; run stage-runtime")
        app = validate_app_path(candidate_record["app"], layout, "staged candidate", built=True)
        input_kind = "candidate"
    environment = isolated_environment(layout, network="none")
    report: dict[str, Any] = {
        "schema": 1,
        "status": "failed",
        "network": "none",
        "input": input_kind,
        "app": str(app),
    }
    try:
        report["audit"] = audit_bundle(app, layout, environment)
        report["shadowdriver"] = run_shadowdriver_harness(layout, lock, app)
        fingerprint = bundle_fingerprint(app)
        report["fingerprint"] = fingerprint
        if candidate_record:
            expected = candidate_record.get("fingerprint")
            if expected != fingerprint:
                fail(f"staged candidate fingerprint changed: expected {expected}, got {fingerprint}")
            accepted = promote_accepted(layout, app, fingerprint, environment)
            report["accepted"] = str(accepted)
        report["status"] = "complete"
        atomic_json(layout.metadata / "test-staged-macos.json", report)
        if candidate_record:
            print(f"test-staged: Shadowdriver accepted {app}; promoted {report['accepted']}")
        else:
            print(f"test-staged: Shadowdriver accepted supplied bundle {app}")
        return report
    except MacOSFailure as error:
        report["failure"] = str(error)
        atomic_json(layout.metadata / "test-staged-macos.json", report)
        raise


def remove_owned(path: Path, layout: WorkLayout, label: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    target = layout.assert_owned(path, label)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    else:
        fail(f"cannot reset unknown owned path: {target}")


def reset_work(layout: WorkLayout, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        fail("reset is destructive within the owned work root; pass --yes")
    layout.ensure()
    for path, label in (
        (layout.source, "Chromium source"),
        (layout.root / "out", "GN/Ninja outputs"),
        (layout.stage, "staged bundles"),
        (layout.release, "release artifacts"),
        (layout.metadata, "build metadata"),
        (layout.root / ".gclient", "depot_tools configuration"),
        (layout.root / ".gclient_entries", "depot_tools state"),
        (layout.root / ".gclient_previous_sync_commits", "depot_tools state"),
        (layout.root / ".gcs_entries", "depot_tools state"),
        (layout.root / "_bad_scm", "depot_tools quarantine"),
    ):
        remove_owned(path, layout, label)
    layout.ensure()
    report = {"schema": 1, "status": "complete", "reset": True, "work_root": str(layout.root)}
    atomic_json(layout.metadata / "reset.json", report)
    print(f"reset: removed owned source, outputs, stages, and metadata under {layout.root}")
    return report


def status_work(layout: WorkLayout) -> dict[str, Any]:
    layout.ensure()
    reports: dict[str, Any] = {}
    for name, path in (
        ("doctor", layout.metadata / "doctor.json"),
        ("fetch", layout.metadata / "fetch-macos.json"),
        ("prepare", layout.metadata / "prepare-macos.json"),
        ("configure", layout.metadata / "configure-macos.json"),
        ("build", layout.metadata / "build-macos.json"),
        ("package", layout.metadata / "package-macos.json"),
        ("test_staged", layout.metadata / "test-staged-macos.json"),
        ("candidate", layout.stage / "candidate.json"),
        ("accepted", layout.stage / "accepted.json"),
    ):
        if path.is_file():
            reports[name] = read_json(path, name)
    failed = any(isinstance(report, dict) and report.get("status") == "failed" for report in reports.values())
    result = {
        "schema": 1,
        "status": "failed" if failed else "complete",
        "work_root": str(layout.root),
        "reports": reports,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="native macOS arm64 Chromium builder")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--platform", required=True, choices=["macos"])
    parser.add_argument("--arch", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--app")
    parser.add_argument("--jobs")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--latest", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parse_arguments(argv)
    try:
        repo_root = canonical_path(arguments.repo_root, "repository root", must_exist=True)
        if not is_within(repo_root, WORK_PREFIX) or repo_root == WORK_PREFIX:
            fail(f"repository root must be under /Volumes/dev: {repo_root}")
        arch = normalize_arch(arguments.arch)
        if arch != "arm64" or arguments.profile != DEFAULT_PROFILE:
            fail("the native macOS backend supports arm64 and macos-release only")
        configured_root = os.environ.get("CHROMIUM_WORK_ROOT")
        work_root = configured_root or str(repo_root / ".work" / "macos-arm64")
        layout = WorkLayout(validate_work_root(work_root, repo_root), repo_root)
        with work_lock(layout):
            command = arguments.command
            if command == "doctor":
                run_doctor(layout)
            elif command == "smoke-preflight":
                run_doctor(layout, require_references=False)
            elif command == "update-lock":
                update_lock(layout, latest=arguments.latest)
            elif command == "fetch":
                fetch_inputs(layout)
            elif command == "prepare":
                prepare_source(layout)
            elif command == "configure":
                configure_source(layout)
            elif command == "build":
                build_source(layout, arguments.jobs)
            elif command == "cache-stats":
                compiler_cache_stats(layout)
            elif command == "stage-runtime":
                stage_runtime(layout)
            elif command == "package":
                package_bundle(layout)
            elif command == "test-staged":
                test_staged(layout, arguments.app)
            elif command == "reset":
                reset_work(layout, arguments.yes)
            elif command == "status":
                status_work(layout)
            else:
                fail(f"unsupported macOS command: {command}")
        return 0
    except MacOSFailure as error:
        print(f"macos-build: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("macos-build: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
