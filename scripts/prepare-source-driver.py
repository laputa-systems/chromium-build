#!/usr/bin/env python3
"""Extract locked inputs and invoke the deterministic source preparation."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

from script_support import ScriptFailure, atomic_write, fail_main, require_file, require_network, run


LOCAL_PATCHES = (
    "gate-e-hermetic-smoke.patch",
    "headless-no-devtools.patch",
    "devtools-use-bundled-typescript.patch",
    "headless-devtools-protocol-without-frontend.patch",
    "headless-devtools-browser-without-frontend.patch",
    "headless-devtools-runtime-without-frontend.patch",
    "headless-devtools-frontend-host-stub.patch",
    "headless-no-desktop-backend-stubs.patch",
    "headless-no-networking-private-lifetime.patch",
    "headless-no-gcm-dead-code.patch",
    "headless-no-domain-reliability-unreachable.patch",
    "headless-no-assist-ranker-unreachable.patch",
    "headless-no-translate-ranker-unreachable.patch",
    "headless-no-advanced-protection-unreachable.patch",
    "headless-no-ntp-background-unreachable.patch",
    "headless-no-printing-linux-context.patch",
    "headless-no-printing-pref.patch",
    "headless-no-printing-pdf-helpers.patch",
    "headless-no-printing-flag.patch",
    "headless-no-one-google-bar-unreachable.patch",
    "musl-extension-toolbar-shadow.patch",
    "musl-profile-lookup-const.patch",
    "headless-no-safe-browsing-command.patch",
    "musl-sched-param-initializer.patch",
    "musl-allocator-cdefs.patch",
    "musl-allocator-cpp-noexcept.patch",
    "musl-allocator-libc-noexcept.patch",
    "musl-perfetto-cmsg-sign-compare.patch",
    "musl-unix-domain-socket-types.patch",
    "musl-stack-trace-noexecinfo.patch",
    "musl-cookie-string-view-iterator.patch",
    "musl-udp-cmsg-sign-compare.patch",
    "musl-mojo-cmsg-sign-compare.patch",
    "musl-mojo-vector-bool-clone.patch",
    "musl-raw-ref-transparent-const.patch",
    "musl-private-key-find-iterator.patch",
    "musl-webrtc-physical-socket.patch",
    "musl-sandbox-cmsg-sign-compare.patch",
    "musl-sys-poll-compat-overlay.patch",
    "musl-v8-sanitizer-header.patch",
    "headless-link-dawn-drm-and-variations.patch",
    "headless-no-suspicious-site-bubble.patch",
    "headless-dawn-vulkan-headers.patch",
    "headless-pdf-without-printing.patch",
    "headless-no-printing-test-dep.patch",
    "headless-no-linux-v4l2.patch",
    "headless-no-linux-v4l2-tracker.patch",
    "headless-no-dbus-backend-linkage.patch",
    "headless-no-web-app-dbus-portal.patch",
    "headless-global-accelerator-no-dbus-return.patch",
    "musl-fontconfig-no-nls.patch",
    "musl-fontconfig-random.patch",
    "musl-expat-hash-salt-2-8.patch",
    "musl-libsync-no-cdefs.patch",
    "musl-bindgen-clang22.patch",
    "alpine-rust-bootstrap.patch",
    "alpine-node-version.patch",
    "musl-crashpad-cdefs.patch",
    "musl-crashpad-cmsg.patch",
    "headless-no-crashpad-handler.patch",
    "headless-no-crashpad-runtime.patch",
    "headless-no-crashpad-guards.patch",
    "headless-no-crashpad-zygote.patch",
    "headless-native-egl-llvmpipe.patch",
    "musl-native-egl-share-group.patch",
    "musl-native-egl-semaphore-share-group.patch",
    "musl-gpu-sandbox-tsync.patch",
    "musl-gpu-sched-affinity-errno.patch",
    "musl-gpu-sandbox-broker-tsync.patch",
    "musl-gpu-pwritev2-sandbox.patch",
    "musl-abseil-clang23-lifetime-capture.patch",
    "musl-abseil-clang23-unused-template.patch",
    "musl-clang23-unused-template-global.patch",
    "musl-clang23-lifetime-capture.patch",
    "musl-clang23-function-view-pointer-bool.patch",
    "musl-clang23-omnibox-nodiscard.patch",
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
    lock = json.loads(require_file(root / "config/inputs.lock").read_text(encoding="utf-8"))
    archive = inputs / lock["chromium"]["archive_filename"]
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
        for name in (entry["filename"] for entry in lock["source_inputs"].values()):
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
