#!/usr/bin/env python3
"""Extract locked inputs and invoke the deterministic source preparation."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tarfile
import tempfile

from script_support import ScriptFailure, atomic_write, fail_main, require_file, require_network, run


LOCAL_PATCHES = (
    "laputa-external-libcxx.patch",
    "gate-e-hermetic-smoke.patch",
    "headless-no-devtools.patch",
    "musl-allocator-cdefs.patch",
    "musl-allocator-cpp-noexcept.patch",
    "musl-allocator-libc-noexcept.patch",
    "musl-perfetto-cmsg-sign-compare.patch",
    "musl-unix-domain-socket-types.patch",
    "musl-stack-trace-noexecinfo.patch",
    "musl-cookie-string-view-iterator.patch",
    "musl-udp-cmsg-sign-compare.patch",
    "musl-mojo-cmsg-sign-compare.patch",
    "musl-webrtc-physical-socket.patch",
    "musl-sys-poll-compat-overlay.patch",
    "musl-v8-sanitizer-header.patch",
    "headless-pdf-without-printing.patch",
    "headless-no-printing-test-dep.patch",
    "headless-no-linux-v4l2.patch",
    "musl-fontconfig-no-nls.patch",
    "musl-fontconfig-random.patch",
    "musl-bindgen-clang22.patch",
    "alpine-rust-bootstrap.patch",
    "alpine-node-version.patch",
    "musl-crashpad-cdefs.patch",
    "musl-crashpad-cmsg.patch",
    "headless-no-crashpad-handler.patch",
)


def extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as stream:
        stream.extractall(destination, filter="data")


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    inputs = Path(os.environ.get("CHROMIUM_INPUTS_ROOT", work / "inputs"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    source = Path(os.environ.get("CHROMIUM_SOURCE_ROOT", work / "src"))
    arch = os.environ.get("CHROMIUM_ARCH", "arm64")
    require_network("none", "preparation")
    require_file(metadata / "fetch-complete.stamp", "fetch has not completed")
    archive = inputs / "chromium-150.0.7871.114-linux.tar.xz"
    require_file(archive, "Chromium archive is missing")
    if source.exists():
        raise ScriptFailure("prepared source already exists; preserve or move it before preparing")
    rust_target = {"arm64": "aarch64-alpine-linux-musl", "amd64": "x86_64-alpine-linux-musl"}.get(arch)
    if rust_target is None:
        raise ScriptFailure(f"unsupported architecture {arch}")
    temp = Path(tempfile.mkdtemp(prefix="src.prepare.", dir=work))
    config = Path(tempfile.mkdtemp(prefix="prepare-inputs.", dir=work))
    try:
        extract(archive, temp)
        for name in (
            "ungoogled-2d89b04e1b68385c9086efab0df1e3679b35246e.tar.gz",
            "portablelinux-0033e274f91ec6aa57a36a486f41f46d801e381d.tar.gz",
            "alpine-aports-bc56128509194816d5cd3441d17c20ca2d71cc68.tar.gz",
            "copium-150.0.tar.gz",
        ):
            extract(require_file(inputs / name, f"missing preparation input {name}"), config)
        candidates = [path.parent.parent for path in temp.rglob("chrome/VERSION")]
        if not candidates:
            raise ScriptFailure("Chromium archive has no expected source root")
        source_dir = candidates[0]
        command = [
            "python3",
            str(root / "scripts/prepare-source.py"),
            "--source",
            str(source_dir),
            "--ungoogled-root",
            str(config),
            "--portable-root",
            str(config),
            "--alpine-root",
            str(config),
            "--copium-root",
            str(config),
            "--lock",
            str(root / "config/inputs.lock"),
            "--inventory",
            str(root / "config/patch-inventory.json"),
            "--disposition",
            str(root / "config/patch-disposition.json"),
            "--metadata",
            str(metadata),
            "--patches",
            str(metadata / "patches"),
            "--rust-target-triple",
            rust_target,
        ]
        for patch in LOCAL_PATCHES:
            command.extend(["--local-patch", str(root / "config/patches" / patch)])
        run(command)
        source_dir.rename(source)
        atomic_write(metadata / "source-complete.stamp", "status=complete\nnetwork=none\n")
        print(f"Prepare: source prepared at {source}")
    finally:
        shutil.rmtree(config, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(fail_main("Prepare", main))
