from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

import macos_backend as backend
from script_support import JsonObject


def local_patches(layout: backend.WorkLayout) -> list[str]:
    path = layout.repo_root / "config" / "macos-helium.patch-disposition.json"
    disposition = backend.read_json(path, "macOS Helium patch disposition")
    rows = disposition["local_patches"]
    if any(row.get("state") not in {"apply", "exclude"} or not row.get("reason") for row in rows):
        backend.fail("invalid macOS Helium patch disposition")
    names = [row["name"] for row in rows if row["state"] == "apply"]
    if len(names) != len(set(names)) or any(Path(name).name != name for name in names):
        backend.fail("invalid macOS Helium local patch names")
    return names


def load_lock(layout: backend.WorkLayout) -> JsonObject:
    path = layout.repo_root / backend.LOCK_RELATIVE
    lock = backend.read_json(path, "macOS Helium input lock")
    if (lock.get("status"), lock.get("platform"), lock.get("architecture")) != ("resolved", "macos", "arm64"):
        backend.fail(f"macOS Helium lock is not resolved: {path}")
    if lock.get("product", {}).get("name") != "helium" or not lock.get("chromium", {}).get("commit"):
        backend.fail(f"macOS Helium lock has no product or Chromium identity: {path}")
    for entry in [lock["chromium"]["source"], *lock["source_inputs"].values(), *lock["adblock_inputs"].values(), lock["pgo_profile"]]:
        if not entry.get("filename") or not entry.get("url") or not entry.get("size") or not re.fullmatch(r"[0-9a-f]{128}", entry.get("sha512", "")):
            backend.fail(f"macOS Helium lock has an incomplete input: {entry.get('filename')}")
    return lock


def local_overlay_identity(layout: backend.WorkLayout) -> JsonObject:
    root = layout.repo_root / "config" / "patches" / "helium"
    patches = [{"name": name, "sha256": backend.sha256(root / name)} for name in local_patches(layout)]
    disposition = layout.repo_root / "config" / "macos-helium.patch-disposition.json"
    identity = {"patches": patches, "disposition_sha256": backend.sha256(disposition), "preparation_sha256": backend.sha256(Path(__file__))}
    return {**identity, "sha256": backend.stable_digest(identity)}


def extract_single(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    backend.safe_extract_tar(archive, destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        backend.fail(f"expected one archive root in {archive}, got {len(roots)}")
    return roots[0]


def fetch_inputs(layout: backend.WorkLayout) -> JsonObject:
    layout.ensure()
    lock = load_lock(layout)
    environment = backend.isolated_environment(layout, network="fetch")
    entries = [
        {**value, "name": name}
        for name, value in [("chromium", lock["chromium"]["source"]), *lock["source_inputs"].items(), *lock["adblock_inputs"].items(), ("pgo_profile", lock["pgo_profile"])]
    ]
    downloaded = backend.download_locked_entries(entries, layout.inputs)
    depot_tools = backend.clone_depot_tools(layout, lock, environment)
    rust = backend.ensure_rust_toolchain(layout, environment)
    acquisition = layout.metadata / "source-acquired.json"
    lock_digest = backend.sha256(layout.repo_root / backend.LOCK_RELATIVE)
    if acquisition.is_file():
        source_status = backend.read_json(acquisition, "source acquisition")
        if source_status.get("lock_sha256") != lock_digest:
            backend.fail("source acquisition identity differs from the macOS Helium lock; use reset --yes")
        if not (layout.source / "chrome" / "VERSION").is_file():
            backend.fail("acquired source is missing")
    else:
        if layout.source.exists() and any(layout.source.iterdir()):
            backend.fail(f"source exists without owned acquisition metadata: {layout.source}")
        source_status = {**backend.extract_official_source(layout, lock), "lock_sha256": lock_digest}
        backend.atomic_json(acquisition, source_status)
    install_pgo_profile(layout, lock["pgo_profile"])
    archive_worktree = backend.ensure_archive_worktree(layout.source, environment)
    hooks = backend.run_native_hooks(layout, lock, environment, depot_tools, str(source_status["source_method"]))
    report = {
        "schema": 1,
        "status": "complete",
        "network": "fetch",
        "source": str(layout.source),
        "entries": downloaded,
        "depot_tools": {"path": str(depot_tools), "commit": lock["depot_tools"]["commit"]},
        "rust": rust,
        "archive_worktree": archive_worktree,
        "hooks": hooks,
    }
    backend.atomic_json(layout.metadata / "fetch-macos.json", report)
    backend.atomic_write(layout.metadata / "fetch-complete.stamp", "status=complete\nnetwork=fetch\n")
    print(f"fetch: verified {len(entries)} macOS Helium inputs; source is {layout.source}")
    return report


def install_pgo_profile(layout: backend.WorkLayout, entry: JsonObject) -> None:
    pinned = (layout.source / "chrome" / "build" / "mac-arm.pgo.txt").read_text(encoding="utf-8").strip()
    if pinned != entry["filename"]:
        backend.fail(f"chrome/build/mac-arm.pgo.txt pins {pinned}, the lock provides {entry['filename']}")
    destination = layout.source / entry["destination"] / entry["filename"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(layout.inputs / entry["filename"], destination)


def excluded_upstream_patches(layout: backend.WorkLayout) -> set[str]:
    disposition = backend.read_json(layout.repo_root / "config" / "macos-helium.patch-disposition.json", "macOS Helium patch disposition")
    rows = disposition["upstream_excluded"]
    if any(not row.get("name") or not row.get("reason") for row in rows):
        backend.fail("invalid macOS Helium upstream patch exclusion")
    return {row["name"] for row in rows}


def scale_resources(listing: Path, resources: Path) -> None:
    """Scale Helium's branding icons with sips, replacing upstream's Pillow-based generate_resources.py."""

    sips = Path("/usr/bin/sips")
    for line in listing.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) == 2:
            source, size, output = fields[0], None, fields[1]
        elif len(fields) == 3:
            source, size, output = fields[0], fields[1], fields[2]
        else:
            backend.fail(f"invalid Helium resource line in {listing}: {line}")
        target = resources / output
        target.parent.mkdir(parents=True, exist_ok=True)
        resize = ["-Z", size] if size else []
        backend.command_output([str(sips), "-s", "format", "png", *resize, str(resources / source), "--out", str(target)], env=os.environ)


def prepare_source(layout: backend.WorkLayout) -> JsonObject:
    layout.ensure()
    lock = load_lock(layout)
    environment = backend.isolated_environment(layout, network="none")
    backend.require_sandboxed_environment(environment)
    acquisition = backend.read_json(layout.metadata / "source-acquired.json", "source acquisition")
    if acquisition.get("lock_sha256") != backend.sha256(layout.repo_root / backend.LOCK_RELATIVE):
        backend.fail("source acquisition does not match the macOS Helium lock")
    identity = local_overlay_identity(layout)
    marker = layout.metadata / "prepare-macos.json"
    if marker.exists():
        previous = backend.read_json(marker, "source preparation")
        if previous.get("status") == "complete" and previous.get("local_overlay_sha256") == identity["sha256"]:
            print(f"prepare: unchanged prepared source reused at {layout.source}")
            return previous
        backend.fail("prepared source identity changed; recover source before preparing again")
    version = dict(re.findall(r"^(MAJOR|MINOR|BUILD|PATCH)=(\d+)$", (layout.source / "chrome" / "VERSION").read_text(), re.MULTILINE))
    if list(version.values()) != lock["chromium"]["version"].split("."):
        backend.fail("Chromium source version does not match macOS Helium lock")
    extraction = layout.tmp / f"helium-config-{os.getpid()}-{time.time_ns()}"
    extraction.mkdir(parents=True, exist_ok=False)
    try:
        inputs = lock["source_inputs"]
        core = extract_single(layout.inputs / inputs["helium_core"]["filename"], extraction / "core")
        platform = extract_single(layout.inputs / inputs["helium_macos"]["filename"], extraction / "platform")
        python = sys.executable
        def run(*arguments: str, cwd: Path | None = None) -> None:
            backend.command_output([python, *arguments], env=environment, cwd=cwd)
        run(str(core / "utils/downloads.py"), "unpack", "-i", str(core / "deps.ini"), "-c", str(layout.inputs), str(layout.source), "--components", "ublock_origin")
        pruning = [line for line in (core / "pruning.list").read_text().splitlines() if not line.startswith("components/helium_onboarding/")]
        pruning_path = layout.metadata / "helium-pruning.list"
        backend.atomic_write(pruning_path, "\n".join(pruning) + "\n")
        run(str(core / "utils/prune_binaries.py"), "--keep-build-dependencies", str(layout.source), str(pruning_path))
        excluded = excluded_upstream_patches(layout)
        records = []
        skipped = []
        for label, root in (("core", core), ("macos", platform)):
            for patch in backend.read_series(root):
                name = f"{label}/{patch.relative_to(root / 'patches').as_posix()}"
                if name in excluded:
                    skipped.append(name)
                    continue
                records.append(backend.patch_file(layout.source, patch, layout.metadata / "patches" / label, environment))
        if missing := excluded - set(skipped):
            backend.fail(f"excluded Helium patches are not in the upstream series: {sorted(missing)}")
        run(str(core / "utils/domain_substitution.py"), "apply", "-r", str(core / "domain_regex.list"), "-f", str(core / "domain_substitution.list"), str(layout.source))
        run(str(core / "utils/name_substitution.py"), "--sub", "-t", str(layout.source), "--backup-path", str(extraction / "helium-namesubs.tar"))
        run(str(core / "utils/i18n_apply.py"), "-t", str(layout.source))
        run(str(core / "utils/helium_version.py"), "--tree", str(core), "--platform-tree", str(platform), "--chromium-tree", str(layout.source))
        scale_resources(core / "resources/generate_resources.txt", core / "resources")
        for tree, resource in ((platform, "platform_resources.txt"), (core, "helium_resources.txt")):
            run(str(core / "utils/replace_resources.py"), str(tree / "resources" / resource), str(tree / "resources"), str(layout.source))
        for name in local_patches(layout):
            records.append(backend.patch_file(layout.source, layout.repo_root / "config" / "patches" / "helium" / name, layout.metadata / "patches" / "local", environment))
        filters = lock["adblock_inputs"]["helium_filters"]
        run(
            str(layout.repo_root / "scripts" / "setup-helium-adblock.py"),
            "vendor",
            "--source", str(layout.source),
            "--inputs", str(layout.inputs),
            "--filters", filters["filename"],
            "--filters-sha256", filters["sha256"],
            "--crate-patches", str(layout.repo_root / "config" / "patches" / "helium" / "adblock-crates"),
            "--record", str(layout.metadata / "helium-adblock.json"),
        )
        flags = (core / "flags.gn").read_text()
        backend.atomic_write(layout.metadata / "ungoogled-flags.gn", flags)
        report = {"schema": 1, "status": "complete", "network": "none", "source": str(layout.source), "local_overlay_sha256": identity["sha256"], "patches": records, "excluded_upstream_patches": skipped}
        backend.atomic_json(marker, report)
        backend.atomic_write(layout.metadata / "source-complete.stamp", "status=complete\nnetwork=none\n")
        print(f"prepare: applied {len(records)} Helium patches")
        return report
    finally:
        shutil.rmtree(extraction, ignore_errors=True)
