#!/usr/bin/env python3
"""Stage the runtime files required by a completed browser build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

import linux_runtime

DESKTOP_ENTRY = """[Desktop Entry]
Version=1.0
Type=Application
Name=Helium
Comment=Helium web browser
Exec=helium %U
Icon=helium
Terminal=false
Categories=Network;WebBrowser;
MimeType=text/html;text/xml;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;
StartupNotify=true
"""

HELIUM_REQUIRED = (
    "helium",
    "helium_crashpad_handler",
    "chrome_sandbox",
    "libEGL.so",
    "libGLESv2.so",
    "chrome_100_percent.pak",
    "chrome_200_percent.pak",
    "resources.pak",
    "icudtl.dat",
)
HELIUM_OPTIONAL = (
    "v8_context_snapshot.bin",
    "snapshot_blob.bin",
    "MEIPreload/manifest.json",
    "MEIPreload/preloaded_data.pb",
)
SYSTEM_SEEDS = (
    "libsoftokn3.so",
    "libfreeblpriv3.so",
    "libfreebl3.so",
    "libnssckbi.so",
    "libwayland-egl.so.1",
    "libEGL.so.1",
    "libGLESv2.so.2",
    "libgbm.so.1",
)
MESA_SEED_GLOBS = ("libgallium-*.so", "libEGL_mesa.so*", "libGLX_mesa.so*")
EXCLUDED_UPSTREAM = (
    {
        "path": "chromedriver",
        "reason": "not part of the //chrome:chrome runtime graph",
    },
    {
        "path": "libqt5_shim.so",
        "reason": "use_qt6=false and Qt is not a runtime consumer",
    },
    {
        "path": "libqt6_shim.so",
        "reason": "use_qt6=false and Qt is not a runtime consumer",
    },
    {
        "path": "libvulkan.so.1",
        "reason": "enable_vulkan=false",
    },
    {
        "path": "libvk_swiftshader.so",
        "reason": "enable_swiftshader=false",
    },
    {
        "path": "vk_swiftshader_icd.json",
        "reason": "enable_swiftshader=false",
    },
    {
        "path": "headless_command_resources.pak",
        "reason": "headless-only resource pack",
    },
    {
        "path": "chrome_management_service",
        "reason": "enterprise management helper is not in the selected browser graph",
    },
)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def audit_browser_elf(chrome: Path) -> dict[str, object]:
    arch = os.environ.get("CHROMIUM_ARCH", "amd64")
    interpreters = {
        "amd64": "/lib/ld-musl-x86_64.so.1",
        "arm64": "/lib/ld-musl-aarch64.so.1",
    }
    if arch not in interpreters:
        raise RuntimeError(f"unsupported browser architecture: {arch}")
    info = linux_runtime.inspect_elf(chrome, chrome.parent)
    if info.interpreter != interpreters[arch]:
        raise RuntimeError(f"Chrome uses the wrong ELF interpreter: {info.interpreter}")
    if not info.needed:
        raise RuntimeError("Chrome has no readable ELF library requirements")
    forbidden = {"libc.so.6", "libstdc++.so.6", "libgcc_s.so.1"}
    leaked = sorted(forbidden.intersection(info.needed))
    if leaked:
        raise RuntimeError(f"Chrome requires build-only GNU runtime libraries: {leaked}")
    return {"interpreter": info.interpreter, "needed": list(info.needed)}


def stage_chromium(chrome: Path, output: Path) -> None:
    source = chrome.parent
    required = (
        "chrome",
        "chrome_100_percent.pak",
        "chrome_200_percent.pak",
        "resources.pak",
        "headless_command_resources.pak",
        "locales/en-US.pak",
    )
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise RuntimeError(f"Chrome runtime inputs are missing: {missing}")
    elf = audit_browser_elf(chrome)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        temporary.chmod(0o755)
        for name in required:
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
        for path in temporary.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o755 if path.name == "chrome" else 0o644)
        manifest = {
            "schema": 1,
            "status": "complete",
            "chrome": "chrome",
            "source": str(chrome),
            "elf": elf,
            "files": [
                {"path": name, "size": (temporary / name).stat().st_size, "sha256": digest(temporary / name)}
                for name in required
            ],
        }
        (temporary / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(f"stage-runtime: staged {len(required)} files at {output}")


def find_source_root(chrome: Path, explicit: Path | None) -> Path:
    candidates = []
    if explicit is not None:
        candidates.append(explicit)
    configured = os.environ.get("CHROMIUM_SOURCE_ROOT")
    if configured:
        candidates.append(Path(configured))
    candidates.extend((chrome.parents[2] / "src", chrome.parents[1] / "src", Path("/work/src")))
    for candidate in candidates:
        if (candidate / "chrome/installer/linux/BUILD.gn").is_file() and (candidate / "buildtools/linux64/gn").is_file():
            return candidate.resolve()
    raise RuntimeError("cannot find the Chromium source root for runtime metadata")


def add_file(
    files: dict[PurePosixPath, Path],
    destination: PurePosixPath,
    source: Path,
    allowed_roots: tuple[Path, ...],
) -> None:
    resolved = linux_runtime.resolved_source(source, allowed_roots)
    previous = files.get(destination)
    if previous is not None and previous.resolve() != resolved:
        raise RuntimeError(f"duplicate destination {destination} comes from {previous} and {resolved}")
    files[destination] = source


def runtime_deps_candidates(out: Path) -> tuple[Path, ...]:
    return (
        out / "helium.runtime_deps",
        out / "chrome.runtime_deps",
        out / "obj/chrome/chrome.runtime_deps",
    )


def ensure_runtime_deps(chrome: Path, runtime_deps: Path) -> None:
    if runtime_deps.is_file():
        return
    out = chrome.parent
    source_root = find_source_root(chrome, None)
    for candidate in runtime_deps_candidates(out):
        if candidate.is_file():
            if candidate.resolve() != runtime_deps.resolve():
                shutil.copy2(candidate, runtime_deps)
            return
    args_file = out / "args.gn"
    if not args_file.is_file():
        raise RuntimeError(f"cannot regenerate {runtime_deps.name}: {args_file} is missing")
    gn = os.environ.get("GN", str(source_root / "buildtools/linux64/gn"))
    targets = source_root / f".runtime-deps-targets-{os.getpid()}.txt"
    targets.write_text("//chrome:chrome\n", encoding="utf-8")
    arguments = args_file.read_text(encoding="utf-8")
    try:
        result = subprocess.run(
            [
                gn,
                "gen",
                str(out),
                f"--root={source_root}",
                "--fail-on-unused-args",
                f"--args={arguments}",
                f"--runtime-deps-list-file={targets}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        targets.unlink(missing_ok=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"gn runtime dependency generation failed: {detail}")
    restored = subprocess.run(
        [
            gn,
            "gen",
            str(out),
            f"--root={source_root}",
            "--fail-on-unused-args",
            f"--args={arguments}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if restored.returncode:
        detail = (restored.stderr or restored.stdout).strip()
        raise RuntimeError(f"gn regeneration after runtime deps failed: {detail}")
    for candidate in runtime_deps_candidates(out):
        if candidate.is_file():
            if candidate.resolve() != runtime_deps.resolve():
                shutil.copy2(candidate, runtime_deps)
            return
    raise RuntimeError(f"gn did not produce the required runtime dependency file: {runtime_deps}")


def system_library(name: str, directories: tuple[Path, ...]) -> Path | None:
    for directory in directories:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def copy_system_closure(stage: Path) -> list[str]:
    directories = (Path("/usr/lib"), Path("/lib"))
    seeds = []
    for name in SYSTEM_SEEDS:
        found = system_library(name, directories)
        if found is not None:
            seeds.append(found)
    for pattern in MESA_SEED_GLOBS:
        for directory in directories:
            seeds.extend(sorted(directory.glob(pattern)))
    dri_root = Path("/usr/lib/dri")
    if dri_root.is_dir():
        seeds.extend(sorted(dri_root.rglob("*_dri.so")))
    browser = linux_runtime.inspect_elf(stage / "helium", stage)
    for needed in browser.needed:
        if needed in linux_runtime.MUSL_LIBRARIES:
            continue
        found = system_library(needed, directories)
        if found is not None:
            seeds.append(found)
    if not seeds:
        raise RuntimeError("no system graphics or NSS libraries were found for the desktop runtime")

    lib = stage / "lib"
    lib.mkdir(exist_ok=True)
    copied: dict[str, Path] = {}
    pending = list(seeds)
    while pending:
        source = pending.pop()
        name = source.name
        if name in linux_runtime.MUSL_LIBRARIES:
            continue
        if name in linux_runtime.GLIBC_LIBRARIES or name.startswith("ld-linux"):
            raise RuntimeError(f"system library closure requires glibc: {source}")
        if name in copied:
            continue
        destination = lib / name
        if destination.exists():
            copied[name] = destination
            continue
        resolved = source.resolve(strict=True)
        if not resolved.is_file():
            raise RuntimeError(f"system library is not a file: {source}")
        shutil.copy2(resolved, destination)
        copied[name] = destination
        info = linux_runtime.inspect_elf(destination, stage)
        for needed in info.needed:
            if needed in linux_runtime.MUSL_LIBRARIES:
                continue
            found = system_library(needed, directories)
            if found is None:
                raise RuntimeError(f"{name} needs {needed}, which is not in the build image")
            pending.append(found)
    return sorted(copied)


def collect_helium_files(
    chrome: Path,
    runtime_deps: Path,
    source_root: Path,
) -> tuple[dict[PurePosixPath, Path], dict[PurePosixPath, str], list[dict[str, str]]]:
    out = chrome.parent
    files: dict[PurePosixPath, Path] = {}
    generated: dict[PurePosixPath, str] = {}
    omitted = [dict(entry) for entry in EXCLUDED_UPSTREAM]
    allowed = (out,)
    for relative in linux_runtime.read_runtime_deps(runtime_deps):
        add_file(files, relative, out / relative, allowed)
    for name in HELIUM_REQUIRED:
        add_file(files, PurePosixPath(name), out / name, allowed)
    for name in HELIUM_OPTIONAL:
        if (out / name).is_file():
            add_file(files, PurePosixPath(name), out / name, allowed)
        else:
            omitted.append({"path": name, "reason": "not produced by the selected //chrome:chrome graph"})
    locale_dir = out / "locales"
    locales = sorted(locale_dir.glob("*.pak")) if locale_dir.is_dir() else []
    if not any(path.name == "en-US.pak" for path in locales):
        raise RuntimeError("the generated runtime has no locales/en-US.pak")
    for locale in locales:
        add_file(files, PurePosixPath("locales") / locale.name, locale, allowed)
    snapshots = [name for name in ("v8_context_snapshot.bin", "snapshot_blob.bin") if (out / name).is_file()]
    if not snapshots:
        raise RuntimeError("the generated runtime has no V8 startup snapshot")
    icon = source_root / "chrome/app/theme/chromium/linux/product_logo_256.png"
    if not icon.is_file():
        raise RuntimeError(f"missing branding icon: {icon}")
    add_file(files, PurePosixPath("helium.png"), icon, (source_root,))
    generated[PurePosixPath("helium.desktop")] = DESKTOP_ENTRY
    return files, generated, omitted


def stage_helium(chrome: Path, output: Path, runtime_deps: Path, source_root: Path) -> None:
    architecture = os.environ.get("CHROMIUM_ARCH", "amd64")
    ensure_runtime_deps(chrome, runtime_deps)
    files, generated, omitted = collect_helium_files(chrome, runtime_deps, source_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        temporary.chmod(0o755)
        for destination, source in files.items():
            target = temporary / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for destination, content in generated.items():
            target = temporary / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        libraries = copy_system_closure(temporary)
        browser = linux_runtime.inspect_elf(temporary / "helium", temporary)
        if browser.interpreter != linux_runtime.MUSL_INTERPRETERS[architecture]:
            raise RuntimeError(f"helium uses the wrong ELF interpreter: {browser.interpreter}")
        audit = linux_runtime.audit_tree(
            temporary,
            architecture,
            gnu_runtime_allowed=linux_runtime.graphics_gnu_allowlist(temporary),
        )
        for path in temporary.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            elif path.name == "chrome_sandbox":
                path.chmod(0o4755)
            elif linux_runtime.is_elf(path) or path.name == "helium.desktop":
                path.chmod(0o755)
            else:
                path.chmod(0o644)
        manifest_files = sorted(path for path in temporary.rglob("*") if path.is_file())
        manifest = {
            "schema": 1,
            "status": "complete",
            "product": "helium",
            "chrome": "helium",
            "source": str(chrome),
            "runtime_deps": str(runtime_deps),
            "elf": {"interpreter": browser.interpreter, "needed": list(browser.needed)},
            "audit": audit,
            "bundled_system_libraries": libraries,
            "omitted": omitted,
            "files": [
                {
                    "path": path.relative_to(temporary).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": digest(path),
                    "mode": oct(path.stat().st_mode & 0o7777),
                }
                for path in manifest_files
            ],
        }
        (temporary / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(f"stage-runtime: staged {len(files) + len(generated)} files and {len(libraries)} libraries at {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--product", choices=("chromium", "helium"), default=os.environ.get("CHROMIUM_PRODUCT", "chromium"))
    parser.add_argument("--runtime-deps", type=Path)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    if args.product == "helium":
        runtime_deps = args.runtime_deps or args.chrome.parent / f"{args.chrome.name}.runtime_deps"
        stage_helium(args.chrome, args.output, runtime_deps, find_source_root(args.chrome, args.source_root))
    else:
        stage_chromium(args.chrome, args.output)


if __name__ == "__main__":
    main()
