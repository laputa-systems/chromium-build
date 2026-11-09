#!/usr/bin/env python3
"""Extract locked inputs and invoke deterministic source preparation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from script_support import (
    ScriptFailure,
    atomic_write,
    fail_main,
    require_file,
    require_network,
    run,
)

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
    "musl-pwritev2-sandbox.patch",
    "musl-seccomp-sched-get.patch",
    "musl-abseil-clang23-lifetime-capture.patch",
    "musl-abseil-clang23-unused-template.patch",
    "musl-clang23-unused-template-global.patch",
    "musl-clang23-lifetime-capture.patch",
    "musl-clang23-function-view-pointer-bool.patch",
    "musl-clang23-omnibox-nodiscard.patch",
)


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zst":
        with subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE) as decompressor:
            result = subprocess.run(
                ["tar", "-xf", "-", "-C", str(destination)],
                stdin=decompressor.stdout,
                check=False,
            )
            decompressor.wait()
        if result.returncode or decompressor.returncode:
            raise ScriptFailure(f"could not extract {archive}")
    else:
        run(["tar", "-xf", str(archive), "-C", str(destination)])


def source_root(root: Path) -> Path:
    candidates = [path.parent.parent for path in root.rglob("chrome/VERSION")]
    if len(candidates) != 1:
        raise ScriptFailure(f"expected one Chromium source root under {root}, found {len(candidates)}")
    return candidates[0]


def find_root(root: Path, marker: str) -> Path:
    candidates = [path.parent.parent for path in root.rglob(marker)]
    if len(candidates) != 1:
        raise ScriptFailure(f"expected one {marker} root under {root}, found {len(candidates)}")
    return candidates[0]


def archive_root(root: Path) -> Path:
    entries = list(root.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise ScriptFailure(f"expected one top-level directory under {root}, found {len(entries)}")
    return entries[0]


def lock_path(root: Path, product: str) -> Path:
    selected = os.environ.get("INPUTS_LOCK")
    if selected:
        return Path(selected)
    return root / ("config/helium.inputs.lock" if product == "helium" else "config/inputs.lock")


def prepare_chromium(root: Path, work: Path, inputs: Path, metadata: Path, source: Path, arch: str) -> None:
    lock = json.loads((root / "config/inputs.lock").read_text(encoding="utf-8"))
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
        source_dir = source_root(temp)
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


def prepare_helium(root: Path, work: Path, inputs: Path, metadata: Path, source: Path, arch: str) -> None:
    selected_lock = lock_path(root, "helium")
    require_file(selected_lock, f"missing {selected_lock}")
    lock = json.loads(selected_lock.read_text(encoding="utf-8"))
    identity = {
        "product": "helium",
        "chromium_version": lock["chromium"]["version"],
        "product_version": lock["product"]["version"],
        "architecture": arch,
        "inputs_lock_sha256": __import__("hashlib").sha256(selected_lock.read_bytes()).hexdigest(),
    }
    receipt = metadata / "source-complete.stamp"
    if source.exists():
        if not receipt.is_file():
            raise ScriptFailure("prepared Helium source exists without a completion receipt")
        recorded = dict(
            line.split("=", 1)
            for line in receipt.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        if recorded.get("product") == identity["product"] and recorded.get("inputs_lock_sha256") == identity["inputs_lock_sha256"]:
            print(f"Prepare: verified source reuse at {source}")
            return
        raise ScriptFailure("prepared Helium source does not match the selected inputs; preserve or move it before preparing")
    fetch_report = json.loads(require_file(metadata / "fetch-inputs.json").read_text(encoding="utf-8"))
    if fetch_report.get("product") != "helium" or fetch_report.get("inputs_lock_sha256") != identity["inputs_lock_sha256"]:
        raise ScriptFailure("fetch report does not match the selected Helium inputs; rerun fetch")
    archive = inputs / lock["chromium"]["archive_filename"]
    require_file(archive, "Helium Chromium base archive is missing")
    temp = Path(tempfile.mkdtemp(prefix="src.prepare.", dir=work))
    config = Path(tempfile.mkdtemp(prefix="prepare-inputs.", dir=work))
    try:
        extract(archive, temp)
        source_dir = source_root(temp)
        overlay_archive = inputs / lock["source_inputs"]["linux_overlay"]["filename"]
        overlay_dir = config / "platform"
        extract(require_file(overlay_archive, "missing Helium Linux overlay"), overlay_dir)
        overlay_root = archive_root(overlay_dir)
        shutil.copytree(overlay_root, source_dir, dirs_exist_ok=True, symlinks=True)
        for key in ("helium_core", "helium_linux", "alpine", "copium"):
            item = lock["source_inputs"][key]
            extract(require_file(inputs / item["filename"], f"missing {key} input"), config / "sources" / key)
        core = find_root(config / "sources/helium_core", "utils/downloads.py")
        linux = find_root(config / "sources/helium_linux", "patches/series")
        patches = config / "patches"
        (patches / "aports").mkdir(parents=True)
        (patches / "copium").mkdir(parents=True)
        shutil.copytree(next((config / "sources/alpine").iterdir()), patches / "aports", dirs_exist_ok=True)
        shutil.copytree(next((config / "sources/copium").iterdir()), patches / "copium", dirs_exist_ok=True)
        command = [
            "python3",
            str(root / "scripts/prepare-helium.py"),
            "--source",
            str(source_dir),
            "--core",
            str(core),
            "--platform",
            str(linux),
            "--inputs",
            str(inputs),
            "--metadata",
            str(metadata),
            "--lock",
            str(selected_lock),
            "--inventory",
            str(Path(os.environ.get("PATCH_INVENTORY", root / "config/helium.patch-inventory.json"))),
            "--disposition",
            str(Path(os.environ.get("PATCH_DISPOSITION", root / "config/helium.patch-disposition.json"))),
            "--patches",
            str(patches),
            "--local-patches",
            str(root / "config/patches/helium"),
            "--local-patches",
            str(root / "config/patches"),
            "--arch",
            arch,
        ]
        run(command)
        source_dir.rename(source)
        atomic_write(
            metadata / "source-complete.stamp",
            "status=complete\nnetwork=none\n"
            f"product={identity['product']}\n"
            f"chromium_version={identity['chromium_version']}\n"
            f"product_version={identity['product_version']}\n"
            f"architecture={identity['architecture']}\n"
            f"inputs_lock_sha256={identity['inputs_lock_sha256']}\n",
        )
        print(f"Prepare: Helium source prepared at {source}")
    except BaseException:
        print(f"Prepare: failed attempt preserved at {temp}", file=os.sys.stderr)
        raise
    finally:
        shutil.rmtree(config, ignore_errors=True)


def update_integration(root: Path, work: Path, metadata: Path, source: Path, arch: str) -> None:
    product = os.environ.get("CHROMIUM_PRODUCT", "chromium")
    if product != "helium":
        raise ScriptFailure("--update-integration is Helium-only")
    selected_lock = lock_path(root, "helium")
    json.loads(require_file(selected_lock, f"missing {selected_lock}").read_text(encoding="utf-8"))
    receipt = metadata / "source-complete.stamp"
    recorded = dict(
        line.split("=", 1)
        for line in require_file(receipt, "prepared Helium source receipt is missing").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    if recorded.get("product") != "helium" or recorded.get("inputs_lock_sha256") != __import__("hashlib").sha256(selected_lock.read_bytes()).hexdigest():
        raise ScriptFailure("prepared Helium source does not match the selected inputs")
    if recorded.get("architecture") not in (None, arch):
        raise ScriptFailure("prepared Helium source architecture does not match")
    report_path = metadata / "patches.json"
    report = json.loads(require_file(report_path, "patch report is missing").read_text(encoding="utf-8"))
    disposition = json.loads(
        Path(os.environ.get("PATCH_DISPOSITION", root / "config/helium.patch-disposition.json")).read_text(encoding="utf-8")
    )
    existing = [entry["name"] for entry in report.get("local_patches", [])]
    configured = disposition["local_patches"]
    if configured[: len(existing)] != existing:
        raise ScriptFailure("configured local patch order changed; a fresh preparation is required")
    new_names = configured[len(existing) :]
    if not new_names:
        print("update-integration: no new local patches")
        return
    for entry in report["local_patches"]:
        path = Path(entry["patch_path"])
        if not path.is_file() or __import__("hashlib").sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ScriptFailure(f"recorded local patch changed: {path}")
    backup = Path(tempfile.mkdtemp(prefix="integration.", dir=work))
    applied: list[Path] = []
    try:
        for name in new_names:
            candidates = [root / "config/patches/helium" / name, root / "config/patches" / name]
            patch = next((candidate for candidate in candidates if candidate.is_file()), None)
            if patch is None:
                raise ScriptFailure(f"missing local Helium patch {name}")
            dry = subprocess.run(
                ["patch", "--batch", "--fuzz=0", "--ignore-whitespace", "-p1", "--dry-run", "-i", str(patch)],
                cwd=source,
                check=False,
                capture_output=True,
                text=True,
            )
            if dry.returncode:
                raise ScriptFailure(f"patch dry-run failed with zero fuzz: {patch}\n{dry.stdout}{dry.stderr}")
            affected = []
            for line in dry.stdout.splitlines():
                if line.startswith("checking file "):
                    affected.append(line.removeprefix("checking file ").strip())
            for relative in affected:
                origin = source / relative
                if origin.is_file():
                    destination = backup / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(origin, destination)
            result = subprocess.run(
                ["patch", "--batch", "--fuzz=0", "--ignore-whitespace", "-p1", "-i", str(patch)],
                cwd=source,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise ScriptFailure(f"patch failed: {patch}\n{result.stdout}{result.stderr}")
            applied.append(patch)
            recorded_patch = metadata / "patches/local" / name
            recorded_patch.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(patch, recorded_patch)
            report["local_patches"].append(
                {
                    "name": name,
                    "sha256": __import__("hashlib").sha256(recorded_patch.read_bytes()).hexdigest(),
                    "conflict_record": {"upstream_sets": ["alpine", "copium"]},
                    "result": "applied",
                    "fuzz": 0,
                    "offset": 0,
                    "dry_run": "passed",
                    "deterministic_options": {"batch": True, "fuzz_limit": 0},
                    "patch_path": str(recorded_patch),
                }
            )
        atomic_write(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
        for name in ("gate-c-report.json", "gate-d-report.json", "gate-f-report.json"):
            (metadata / name).unlink(missing_ok=True)
        for path in metadata.glob("build-*.json"):
            path.unlink()
        print(f"update-integration: applied {len(applied)} patch(es); rerun Gate C and downstream gates")
    except BaseException:
        for relative in sorted({str(path.relative_to(backup)) for path in backup.rglob("*") if path.is_file()}):
            shutil.copy2(backup / relative, source / relative)
        report["local_patches"] = report["local_patches"][: len(existing)]
        atomic_write(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def main() -> None:
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    inputs = Path(os.environ.get("CHROMIUM_INPUTS_ROOT", work / "inputs"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    source = Path(os.environ.get("CHROMIUM_SOURCE_ROOT", work / "src"))
    arch = os.environ.get("CHROMIUM_ARCH", "arm64")
    product = os.environ.get("CHROMIUM_PRODUCT", "chromium")
    if "--update-integration" in os.sys.argv[1:]:
        update_integration(root, work, metadata, source, arch)
        return
    require_network("none", "preparation")
    require_file(metadata / "fetch-complete.stamp", "fetch has not completed")
    if product == "helium":
        prepare_helium(root, work, inputs, metadata, source, arch)
    elif product == "chromium":
        prepare_chromium(root, work, inputs, metadata, source, arch)
    else:
        raise ScriptFailure(f"unknown product {product}")


if __name__ == "__main__":
    raise SystemExit(fail_main("Prepare", main))
