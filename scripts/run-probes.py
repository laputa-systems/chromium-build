#!/usr/bin/env python3
"""Run Gate B's direct compiler, linker, runtime, and package probes."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex

from script_support import ScriptFailure, atomic_json, command_path, fail_main, require_file, run


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", "/work/metadata"))
    out = Path(os.environ.get("PROBE_ROOT", "/work/test-output/gate-b"))
    arch = os.environ.get("CHROMIUM_ARCH", "arm64")
    llvm = Path(os.environ.get("LLVM_ROOT", "/opt/llvm-musl"))
    cc = os.environ.get("CC", str(llvm / "bin/clang"))
    cxx = os.environ.get("CXX", str(llvm / "bin/clang++"))
    ar = os.environ.get("AR", str(llvm / "bin/llvm-ar"))
    ranlib = os.environ.get("RANLIB", str(llvm / "bin/llvm-ranlib"))
    lld = os.environ.get("LLD", str(llvm / "bin/ld.lld"))
    readelf = os.environ.get("READELF", str(llvm / "bin/llvm-readelf"))
    pkg_config = os.environ.get("PKG_CONFIG", "pkg-config")
    if os.environ.get("NETWORK_MODE", "none") != "none":
        raise ScriptFailure("Gate B requires NETWORK_MODE=none")
    require_file(metadata / "gate-a-summary", "Gate A has not passed")
    source = root / "tests/probes"
    files = (
        "c_runtime.c", "cpp_runtime.cc", "archive_member.c", "archive_main.c", "shared.c", "shared_main.c",
        "mesa_link.c", "egl_renderer.c", "nss_probe.c", "ffmpeg_probe.c",
    )
    for name in files:
        require_file(source / name, f"missing {source / name}")
    for tool in (cc, cxx, ar, ranlib, lld, readelf):
        require_file(Path(tool), f"missing {tool}")
    command_path(pkg_config)
    interpreter = {"arm64": "/lib/ld-musl-aarch64.so.1", "amd64": "/lib/ld-musl-x86_64.so.1"}.get(arch)
    if interpreter is None:
        raise ScriptFailure(f"unsupported architecture {arch}")
    if out.exists():
        import shutil
        shutil.rmtree(out)
    (out / "objects").mkdir(parents=True)
    (out / "pkg").mkdir()
    (out / "logs").mkdir()

    def step(name: str, command: list[str], env: dict[str, str] | None = None) -> str:
        print(f"Gate B: {name}")
        result = run(command, check=False, capture_output=True, env=env)
        log = out / "logs" / f"{name}.log"
        log.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
        if result.returncode:
            print(log.read_text(encoding="utf-8"), end="", file=__import__("sys").stderr)
            raise ScriptFailure(f"{name} failed")
        return result.stdout or ""

    def compile_c(name: str, *extra: str) -> None:
        step(f"{name}-compile", [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O0", *extra])

    def compile_cpp(name: str, *extra: str) -> None:
        step(f"{name}-compile", [cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-O0", "-fexceptions", "-frtti", f"-L{llvm / 'lib'}", *extra])

    def check_elf(name: str, file: Path) -> None:
        headers = run([readelf, "-l", str(file)], capture_output=True).stdout
        match = re.search(r"Requesting program interpreter: ([^]]+)\]", headers)
        actual = match.group(1) if match else ""
        if actual != interpreter:
            raise ScriptFailure(f"{name} uses interpreter {actual or '<none>'}, expected {interpreter}")
        needed = run([readelf, "-d", str(file)], capture_output=True).stdout
        if re.search(r"(^|/)(libc\.so\.6|ld-linux|libstdc\+\+|libgcc(?:_s)?)(?:\.so|\.a|$)", needed, re.IGNORECASE | re.MULTILINE):
            raise ScriptFailure(f"{name} has a forbidden GNU/glibc DT_NEEDED entry")

    def pkg_flags(mode: str, *packages: str) -> list[str]:
        step_name = f"pkg-config-{mode}-{ '-'.join(packages) }"
        step(step_name, [pkg_config, "--exists", *packages])
        return shlex.split(run([pkg_config, mode, *packages], capture_output=True).stdout)

    compile_c("c-runtime", str(source / "c_runtime.c"), "-o", str(out / "c-runtime"), "-pthread")
    step("c-runtime-run", [str(out / "c-runtime")])
    check_elf("c-runtime", out / "c-runtime")
    compile_cpp("cpp-runtime", str(source / "cpp_runtime.cc"), "-o", str(out / "cpp-runtime"), "-pthread")
    step("cpp-runtime-run", [str(out / "cpp-runtime")])
    check_elf("cpp-runtime", out / "cpp-runtime")
    compile_c("archive-member", "-c", str(source / "archive_member.c"), "-o", str(out / "objects/archive_member.o"))
    step("archive-create", [ar, "rcs", str(out / "libarchive-probe.a"), str(out / "objects/archive_member.o")])
    step("archive-index", [ranlib, str(out / "libarchive-probe.a")])
    if "archive_member.o" not in run([ar, "t", str(out / "libarchive-probe.a")], capture_output=True).stdout.split():
        raise ScriptFailure("LLVM archive does not contain archive_member.o")
    compile_c("archive-main", str(source / "archive_main.c"), str(out / "libarchive-probe.a"), "-o", str(out / "archive-main"))
    step("archive-main-run", [str(out / "archive-main")])
    check_elf("archive-main", out / "archive-main")
    compile_c("shared", "-fPIC", "-c", str(source / "shared.c"), "-o", str(out / "objects/shared.o"))
    step("shared-link", [lld, "-shared", "-o", str(out / "libshared-probe.so"), str(out / "objects/shared.o")])
    step("shared-executable-link", [cc, f"-fuse-ld={lld}", str(source / "shared_main.c"), str(out / "libshared-probe.so"), f"-Wl,-rpath,{out}", "-o", str(out / "shared-main")])
    step("shared-main-run", [str(out / "shared-main")], {**os.environ, "LD_LIBRARY_PATH": str(out)})
    check_elf("shared-main", out / "shared-main")
    for name, packages, source_name, output_name, run_command in (
        ("mesa-link", ("egl", "glesv2", "gbm", "libdrm"), "mesa_link.c", "mesa-link", None),
        ("egl-renderer-link", ("egl", "glesv2"), "egl_renderer.c", "egl-renderer", ["env", "EGL_PLATFORM=surfaceless"]),
        ("nss-link", ("nss",), "nss_probe.c", "nss", None),
        ("ffmpeg-link", ("libavcodec", "libavformat", "libavutil"), "ffmpeg_probe.c", "ffmpeg", None),
    ):
        cflags = pkg_flags("--cflags", *packages)
        libs = pkg_flags("--libs", *packages)
        step(name, [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O0", *cflags, str(source / source_name), *libs, "-o", str(out / "pkg" / output_name)])
        if run_command is not None:
            step(name.replace("-link", "-run"), run_command + [str(out / "pkg" / output_name)])
        elif name != "mesa-link":
            step(name.replace("-link", "-run"), [str(out / "pkg" / output_name)])
        check_elf(name, out / "pkg" / output_name)
    programs = sorted(
        str(path.relative_to(out))
        for path in out.rglob("*")
        if path.is_file() and path.parent.name in ("", "pkg") and path.suffix not in (".log", ".a", ".so")
    )
    atomic_json(out / "report.json", {"schema": 1, "status": "complete", "architecture": arch, "interpreter": interpreter, "network": "none", "programs": programs, "checks": ["c", "cxx-containers-strings-exceptions-rtti-unwind-tls-threads", "llvm-ar-llvm-ranlib", "lld-shared-object", "musl-interpreter-and-direct-needed", "pkg-config-egl-gles-gbm-libdrm", "pkg-config-nss", "pkg-config-ffmpeg", "surfaceless-egl-renderer"]})
    print("Gate B: direct compiler/runtime probes passed")


if __name__ == "__main__":
    raise SystemExit(fail_main("Gate B", main))
