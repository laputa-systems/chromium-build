#!/usr/bin/env python3
"""Validate the locked builder environment for Gate A."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile

from script_support import ScriptFailure, atomic_write, fail_main, require_file, run


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def loopback_only() -> bool:
    route = Path("/proc/net/route")
    if route.is_file() and any(line.split() and line.split()[0] != "lo" for line in route.read_text().splitlines()[1:]):
        return False
    ipv6 = Path("/proc/net/ipv6_route")
    return not ipv6.is_file() or not any(
        line.split() and line.split()[0] not in {"0" * 32, "0" * 31 + "1"}
        for line in ipv6.read_text().splitlines()
    )


def verify_ccache_hit(ccache: Path, cc: str, cxx: str) -> None:
    """Prove both GN compilers are selected and served by the mounted cache."""
    with tempfile.TemporaryDirectory(prefix="chromium-ccache-gate-a-") as directory:
        probe = Path(directory)
        for compiler, suffix in ((cc, "c"), (cxx, "cc")):
            source = probe / f"probe.{suffix}"
            output = probe / f"probe-{suffix}.o"
            log = probe / f"ccache-{suffix}.log"
            source.write_text("int chromium_ccache_probe(void) { return 153; }\n", encoding="utf-8")
            command = [str(ccache), compiler, "-c", str(source), "-o", str(output)]
            environment = {**os.environ, "CCACHE_LOGFILE": str(log)}
            run(command, env=environment)
            if f"Executing {compiler} " not in log.read_text(encoding="utf-8"):
                raise ScriptFailure(f"ccache did not invoke the selected {suffix} compiler")
            output.unlink()
            before = dict(line.split("\t", 1) for line in run([str(ccache), "--print-stats"], capture_output=True).stdout.splitlines())
            run(command, env=environment)
            after = dict(line.split("\t", 1) for line in run([str(ccache), "--print-stats"], capture_output=True).stdout.splitlines())
            if int(after.get("direct_cache_hit", "0")) <= int(before.get("direct_cache_hit", "0")):
                raise ScriptFailure(f"ccache did not return a direct hit for an unchanged {suffix} compile")


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    arch = os.environ.get("CHROMIUM_ARCH", "arm64")
    lock_path = Path(os.environ.get("INPUTS_LOCK", root / "config/inputs.lock"))
    stage = Path(os.environ.get("STAGE_ROOT", work / "stage"))
    if arch not in {"amd64"}:
        raise ScriptFailure(f"unsupported architecture {arch}")
    lock = json.loads(require_file(lock_path).read_text(encoding="utf-8"))
    expected_linux = lock["toolchain"]["architectures"][arch]["linux_arch"]
    actual_linux = platform.machine()
    if actual_linux != expected_linux:
        raise ScriptFailure(f"container architecture is {actual_linux}, expected {expected_linux}")
    image_digest = os.environ.get("BUILDER_IMAGE_DIGEST", "").strip()
    if not image_digest:
        image_digest = (metadata / "image-digest").read_text(encoding="utf-8").strip() if (metadata / "image-digest").is_file() else ""
    if not image_digest.startswith("sha256:") or len(image_digest) != 71:
        raise ScriptFailure("builder image digest is not recorded as sha256")
    if os.environ.get("NETWORK_MODE", "none") != "none":
        raise ScriptFailure("Gate A requires NETWORK_MODE=none")
    if not loopback_only():
        raise ScriptFailure("non-loopback route is present")
    for stamp in (metadata / "source-complete.stamp", metadata / "fetch-complete.stamp"):
        require_file(stamp, f"missing {stamp}")
    source_report = json.loads(require_file(metadata / "source-inputs.json").read_text(encoding="utf-8"))
    fetch_report = json.loads(require_file(metadata / "fetch-inputs.json").read_text(encoding="utf-8"))
    expected_identity = lock["source_identity"]
    if source_report.get("status") != "complete" or any(source_report.get(key) != expected_identity[key] for key in ("schema", "chromium_version", "devtools_revision")):
        raise ScriptFailure("source-inputs.json does not match the source lock")
    if fetch_report.get("status") != "complete" or fetch_report.get("chromium_version") != lock["chromium"]["version"]:
        raise ScriptFailure("fetch-inputs.json does not match the input lock")
    chromium = next((item for item in fetch_report.get("entries", []) if item.get("name") == "chromium"), None)
    if chromium is None or chromium.get("sha512") != lock["chromium"]["archive_sha512"]:
        raise ScriptFailure("source archive digest does not match input lock")

    toolchain = lock["toolchain"]
    arch_toolchain = toolchain["architectures"][arch]
    llvm_root = Path(toolchain["root"])
    if toolchain.get("distribution") != "chromium-clang" or llvm_root != Path("/opt/chromium-llvm"):
        raise ScriptFailure("toolchain is not the locked Chromium Clang distribution")
    for variable in ("CC", "CXX", "AR", "RANLIB", "NM", "STRIP", "OBJCOPY", "BUILD_CC", "BUILD_CXX", "BUILD_AR", "BUILD_RANLIB", "BUILD_NM", "BUILD_STRIP", "BUILD_OBJCOPY"):
        value = os.environ.get(variable, "")
        if not value or not Path(value).is_file() or not str(Path(value)).startswith(f"{llvm_root}/"):
            raise ScriptFailure(f"{variable} is unset or outside {llvm_root}")
        resolved = Path(os.path.realpath(value))
        if not str(resolved).startswith(f"{llvm_root}/"):
            raise ScriptFailure(f"{variable} resolves outside {llvm_root}: {resolved}")
    cc = os.environ["CC"]
    if toolchain["version"] not in run([cc, "--version"], capture_output=True).stdout:
        raise ScriptFailure(f"clang version is not {toolchain['version']}")
    if run([cc, "-dumpmachine"], capture_output=True).stdout.strip() != arch_toolchain["target_triple"]:
        raise ScriptFailure("compiler target does not match the lock")
    for tool in ("clang", "clang++", "ld.lld", "llvm-ar", "llvm-ranlib", "llvm-nm", "llvm-strip", "llvm-objcopy"):
        require_file(llvm_root / "bin" / tool, f"missing toolchain tool {tool}")
    ninja = Path("/usr/local/bin/ninja")
    require_file(ninja, "missing /usr/local/bin/ninja")
    if run([str(ninja), "--version"], capture_output=True).stdout.strip() != "1.13.2":
        raise ScriptFailure("Ninja is not the locked Alpine version")
    lld_version = toolchain.get("lld_version", toolchain["version"])
    if lld_version not in run([str(llvm_root / "bin/ld.lld"), "--version"], capture_output=True).stdout:
        raise ScriptFailure("LLD version is not locked")
    config_hashes = []
    for name in ("clang.cfg", "clang++.cfg"):
        path = require_file(llvm_root / "bin" / name)
        config_hashes.append(f"{digest(path)}  {path}")
    expected_hashes = (toolchain["clang_cfg_sha256"], toolchain["clangxx_cfg_sha256"])
    if tuple(line.split()[0] for line in config_hashes) != expected_hashes:
        raise ScriptFailure("Clang config hash differs from lock")
    atomic_write(metadata / "toolchain-config-sha256", "\n".join(config_hashes) + "\n")
    atomic_write(metadata / "toolchain-configs", "".join((llvm_root / "bin" / name).read_text(encoding="utf-8") for name in ("clang.cfg", "clang++.cfg")))
    if (llvm_root / "cr_build_revision").read_text(encoding="utf-8").strip() != toolchain["revision"]:
        raise ScriptFailure("Chromium Clang revision stamp differs from the lock")
    if not (work / "src/buildtools/third_party/libc++/__config_site").is_file():
        raise ScriptFailure("prepared Chromium libc++ configuration is missing")
    resource_dir = Path(run([cc, "-print-resource-dir"], capture_output=True).stdout.strip())
    if not str(resource_dir).startswith(f"{llvm_root}/lib/clang/") or not (resource_dir / "include").is_dir():
        raise ScriptFailure("Clang resource directory is outside the locked toolchain")
    if not (resource_dir / "lib/x86_64-unknown-linux-musl/libclang_rt.builtins.a").is_file():
        raise ScriptFailure("missing compiler-rt builtins for the musl target")
    if not (resource_dir / "lib/linux/libclang_rt.builtins-x86_64.a").is_file():
        raise ScriptFailure("missing Chromium GN compiler-rt builtins path")
    if not Path("/lib64/ld-linux-x86-64.so.2").is_file():
        raise ScriptFailure("Chromium Clang's build-only glibc loader is missing")
    ccache = Path(os.environ.get("CCACHE_BIN", "/usr/bin/ccache"))
    require_file(ccache, "missing ccache")
    if os.environ.get("CCACHE_DIR") != "/ccache" or os.environ.get("CCACHE_TEMPDIR") != "/ccache/tmp" or os.environ.get("CCACHE_MAXSIZE") != "10G":
        raise ScriptFailure("ccache directory or size policy is invalid")
    if os.environ.get("CCACHE_COMPILERCHECK") != "content" or os.environ.get("CCACHE_COMPRESS") != "true" or os.environ.get("CCACHE_COMPRESSLEVEL") != "1":
        raise ScriptFailure("ccache policy is invalid")
    namespace = f"{arch}-{arch_toolchain['archive_sha256']}-compiler-command-v2"
    if os.environ.get("CCACHE_NAMESPACE") != namespace:
        raise ScriptFailure("ccache namespace is invalid")
    if "CCACHE_CC" in os.environ or "CCACHE_COMPILER" in os.environ:
        raise ScriptFailure("ccache compiler override would bypass GN's selected compiler")
    if "CCACHE_SLOPPINESS" in os.environ or "CCACHE_BASEDIR" in os.environ:
        raise ScriptFailure("ccache sloppiness and basedir must be unset")
    ccache_config = run([str(ccache), "--show-config"], capture_output=True).stdout
    atomic_write(metadata / "ccache-config", ccache_config)
    for text in ("compiler = \n", "compiler_check = content", "compression = true", f"namespace = {namespace}"):
        if text not in ccache_config:
            raise ScriptFailure(f"ccache config does not report {text}")
    verify_ccache_hit(ccache, cc, os.environ["CXX"])
    run([str(ccache), "-z"])
    package_lock = root / f"config/packages.{arch}.lock"
    package_manifest = Path(os.environ.get("APK_MANIFEST", metadata / f"packages.{arch}.txt"))
    run(["python3", str(root / "scripts/lockcheck.py"), "packages", str(package_lock), str(package_manifest)])
    run(["python3", str(root / "scripts/lockcheck.py"), "environment", str(lock_path), os.environ.get("ENVIRONMENT_METADATA", str(metadata / "environment.json")), arch])
    policy = root / "config/system-library-preflight.schema.json"
    preflight = Path(os.environ.get("SYSTEM_LIBRARY_PREFLIGHT", metadata / "system-library-preflight.json"))
    run(["python3", str(root / "scripts/lockcheck.py"), "preflight", str(policy), str(preflight), str(policy), str(package_lock)])
    if stage.exists() and any("ccache" in path.parts for path in stage.rglob("*")):
        raise ScriptFailure("ccache executable or cache data is eligible for staging")
    deps = run(["ldd", str(ccache)], check=False, capture_output=True).stdout
    atomic_write(metadata / "ccache-runtime-dependencies.txt", deps)
    if "not found" in deps or "libc.so.6" in deps:
        raise ScriptFailure("ccache runtime dependency closure is invalid")
    if "ccache-4.13.6-r0" not in package_manifest.read_text(encoding="utf-8"):
        raise ScriptFailure("ccache package version is not locked")
    run(["apk", "info", "-a", "ccache"], check=False, capture_output=True)
    atomic_write(metadata / "ccache-license", "GPL-3.0-or-later\n")
    atomic_write(metadata / "ccache-runtime-classification", "builder-only\n")
    atomic_write(metadata / "gate-a-image-digest", image_digest + "\n")
    atomic_write(metadata / "gate-a-summary", f"architecture={arch}\nlinux_arch={actual_linux}\ntoolchain={llvm_root}\ntoolchain_sha256={arch_toolchain['archive_sha256']}\nccache_volume={os.environ.get('CCACHE_VOLUME', f'ungoogled-chromium-153-ccache-{arch}')}\nnetwork=none\n")
    print(f"Gate A: environment identity passed for {arch}")


if __name__ == "__main__":
    raise SystemExit(fail_main("Gate A", main))
