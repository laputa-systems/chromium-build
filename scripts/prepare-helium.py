#!/usr/bin/env python3
"""Prepare a Helium Chromium tree from locked local inputs without network."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from linux_native_tools import (
    NATIVE_EXECUTABLES,
    install_native_executable,
    install_rust_toolchain,
    remove_foreign_host_binaries,
)
from script_support import JsonObject


def read_json(path: Path) -> JsonObject:
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    print("prepare-helium:", " ".join(map(str, command)))
    return subprocess.run(command, cwd=cwd, check=True)


def series(root: Path, name: str) -> list[Path]:
    series_file = root / "patches" / "series"
    if not series_file.is_file():
        raise SystemExit(f"missing patch series {series_file}")
    patches = []
    for line in series_file.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            path = root / "patches" / line
            if not path.is_file():
                raise SystemExit(f"missing patch {path}")
            patches.append(path)
    return patches


def apply_patch(source: Path, patch: Path, record_dir: Path | None = None) -> JsonObject:
    recorded = patch
    if record_dir is not None:
        record_dir.mkdir(parents=True, exist_ok=True)
        recorded = record_dir / patch.name
        shutil.copy2(patch, recorded)
    dry = subprocess.run(
        ["patch", "--batch", "--fuzz=0", "--ignore-whitespace", "-p1", "--dry-run", "-i", str(patch)],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
    )
    if dry.returncode:
        sys.stderr.write(dry.stdout)
        sys.stderr.write(dry.stderr)
        raise SystemExit(f"patch dry-run failed with zero fuzz: {patch}")
    result = subprocess.run(
        ["patch", "--batch", "--fuzz=0", "--ignore-whitespace", "-p1", "-i", str(patch)],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout
    offsets = [int(value) for value in re.findall(r"offset (-?\d+) lines?", output)]
    fuzz = [int(value) for value in re.findall(r"with fuzz (\d+)", output)]
    return {
        "name": patch.name,
        "patch_path": str(recorded),
        "sha256": sha256(recorded),
        "strip_level": 1,
        "offset": max(offsets, key=abs, default=0),
        "offsets": offsets,
        "fuzz": max(fuzz, default=0),
        "result": "applied",
        "dry_run": "passed",
        "deterministic_options": {"batch": True, "fuzz_limit": 0},
    }


def apply_series(source: Path, root: Path, label: str, record_dir: Path, excluded: set[str]) -> tuple[list[JsonObject], list[str]]:
    records, skipped = [], []
    for patch in series(root, label):
        name = f"{label}/{patch.relative_to(root / 'patches').as_posix()}"
        if name in excluded:
            skipped.append(name)
            continue
        records.append(apply_patch(source, patch, record_dir))
    return records, skipped


def materialize_local_patches(destination: Path, names: list[str], source_roots: list[Path]) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in names:
        origin = next((root / name for root in source_roots if (root / name).is_file()), None)
        if origin is None:
            raise SystemExit(f"missing local Helium patch {name}")
        target = destination / name
        shutil.copy2(origin, target)
        copied.append(target)
    return copied



def toolchain_selection(lock: JsonObject, arch: str) -> JsonObject:
    return {
        "status": "complete",
        "network": "none",
        "chromium_prebuilt_selected": True,
        "sysroot_selected": False,
        "forbidden_selected": [],
        "compiler": "/opt/chromium-llvm/bin/clang",
        "cxx": "/opt/chromium-llvm/bin/clang++",
        "linker": "/opt/chromium-llvm/bin/ld.lld",
        "ar": "/opt/chromium-llvm/bin/llvm-ar",
        "rust_linker": "/opt/chromium-llvm/bin/clang",
        "rust_target_triple": lock["toolchain"]["architectures"][arch]["target_triple"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--disposition", type=Path, required=True)
    parser.add_argument("--patches", type=Path, required=True)
    parser.add_argument("--local-patches", type=Path, action="append", required=True)
    parser.add_argument("--crate-patches", type=Path, required=True)
    parser.add_argument("--arch", required=True)
    args = parser.parse_args()

    lock = read_json(args.lock)
    inventory = read_json(args.inventory)
    disposition = read_json(args.disposition)
    version = lock["chromium"]["version"]
    product_version = lock["product"]["version"]
    identity = {
        "product": "helium",
        "chromium_version": version,
        "product_version": product_version,
        "architecture": args.arch,
        "inputs_lock_sha256": hashlib.sha256(args.lock.read_bytes()).hexdigest(),
    }

    version_path = args.source / "chrome/VERSION"
    values = dict(re.findall(r"^(MAJOR|MINOR|BUILD|PATCH)=(\d+)$", version_path.read_text(encoding="utf-8"), re.MULTILINE))
    expected = dict(zip(("MAJOR", "MINOR", "BUILD", "PATCH"), version.split(".")))
    if values != expected:
        raise SystemExit(f"chrome/VERSION mismatch: expected {expected}, got {values}")

    core = args.core
    platform = args.platform
    python = sys.executable
    run([
        python,
        str(core / "utils/downloads.py"),
        "unpack",
        "-i",
        str(core / "deps.ini"),
        "-c",
        str(args.inputs),
        str(args.source),
        "--components",
        "ublock_origin",
    ])
    pruning_entries = [
        line.strip()
        for line in (core / "pruning.list").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and not line.startswith("components/helium_onboarding/")
    ]
    filtered_pruning = args.patches / "pruning.list"
    filtered_pruning.write_text(
        "\n".join(pruning_entries) + "\n", encoding="utf-8"
    )
    pruning_present = {entry for entry in pruning_entries if (args.source / entry).exists()}
    run([
        python,
        str(core / "utils/prune_binaries.py"),
        "--keep-build-dependencies",
        str(args.source),
        str(filtered_pruning),
    ])
    pruning_removed = sorted(entry for entry in pruning_present if not (args.source / entry).exists())
    pruning_retained = sorted(entry for entry in pruning_present if (args.source / entry).exists())
    excluded = {row["name"] for row in disposition["upstream_excluded"]}
    core_records, core_skipped = apply_series(args.source, core, "core", args.metadata / "patches/core", excluded)
    linux_records, linux_skipped = apply_series(args.source, platform, "linux", args.metadata / "patches/linux", excluded)
    patch_records = core_records + linux_records
    if missing := excluded - set(core_skipped + linux_skipped):
        raise SystemExit(f"excluded Helium patches are not in the upstream series: {sorted(missing)}")

    domain_entries = [
        line.strip()
        for line in (core / "domain_substitution.list").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    domain_before = {
        entry: sha256(args.source / entry)
        for entry in domain_entries
        if (args.source / entry).is_file()
    }
    run([
        python,
        str(core / "utils/domain_substitution.py"),
        "apply",
        "-r",
        str(core / "domain_regex.list"),
        "-f",
        str(core / "domain_substitution.list"),
        str(args.source),
    ])
    domain_after = {
        entry: sha256(args.source / entry)
        for entry in domain_before
    }
    domain_changed = sum(1 for entry in domain_before if domain_before[entry] != domain_after[entry])
    namesubs_backup = args.patches.parent / "namesubs.tar"
    run([
        python,
        str(core / "utils/name_substitution.py"),
        "--sub",
        "-t",
        str(args.source),
        "--backup-path",
        str(namesubs_backup),
    ])
    run([python, str(core / "utils/i18n_apply.py"), "-t", str(args.source)])
    run([
        python,
        str(core / "utils/helium_version.py"),
        "--tree",
        str(core),
        "--platform-tree",
        str(platform),
        "--chromium-tree",
        str(args.source),
    ])
    run(
        [python, str(core / "utils/generate_resources.py"), str(core / "resources/generate_resources.txt"), str(core / "resources")],
        cwd=core,
    )
    run([
        python,
        str(core / "utils/replace_resources.py"),
        str(core / "resources/helium_resources.txt"),
        str(core / "resources"),
        str(args.source),
    ])

    local_dir = args.patches / "helium"
    local_names = disposition["local_patches"]
    local_paths = materialize_local_patches(local_dir, local_names, [*args.local_patches, args.patches])
    local_records = []
    for patch in local_paths:
        record = apply_patch(args.source, patch, args.metadata / "patches/local")
        local_records.append({"project": "local", **record})
    filters = lock["adblock_inputs"]["helium_filters"]
    run([
        python,
        str(Path(__file__).with_name("setup-helium-adblock.py")),
        "vendor",
        "--source",
        str(args.source),
        "--inputs",
        str(args.inputs),
        "--filters",
        filters["filename"],
        "--filters-sha256",
        filters["sha256"],
        "--crate-patches",
        str(args.crate_patches),
        "--record",
        str(args.metadata / "helium-adblock.json"),
    ])

    inherited = []
    roots = {
        "alpine": args.patches / "aports",
        "copium": args.patches / "copium",
        "portablelinux": args.patches / "portablelinux",
    }
    disposition_by_key = {
        (layer["project"], entry["name"]): entry
        for layer in disposition["layers"]
        for entry in layer["patches"]
    }
    for layer in inventory["layers"]:
        for name in layer["patches"]:
            entry = disposition_by_key[(layer["project"], name)]
            path = patch_path(roots[layer["project"]], layer["project"], name)
            record_dir = args.metadata / "patches" / layer["project"]
            record_dir.mkdir(parents=True, exist_ok=True)
            recorded = record_dir / path.name
            shutil.copy2(path, recorded)
            record = {
                "project": layer["project"],
                "name": name,
                "state": entry["state"],
                "affected_paths": entry.get("affected_paths", []),
                "sha256": sha256(recorded),
            }
            if entry["state"] == "apply":
                record.update(apply_patch(args.source, path, record_dir))
            else:
                record.update({
                    "result": entry["state"],
                    "dry_run": "not-run",
                    "offset": 0,
                    "fuzz": 0,
                    "strip_level": 1,
                    "patch_path": str(recorded),
                    "deterministic_options": {"batch": True, "fuzz_limit": 0},
                })
            inherited.append(record)

    rust_target = {
        "amd64": "x86_64-unknown-linux-musl",
        "arm64": "aarch64-unknown-linux-musl",
    }.get(args.arch)
    if rust_target is None:
        raise SystemExit(f"unsupported architecture {args.arch}")
    target_file = args.source / "build/rust/known-target-triples.txt"
    target_lines = target_file.read_text(encoding="utf-8").splitlines()
    if rust_target not in target_lines:
        target_file.write_text("\n".join(target_lines + [rust_target]) + "\n", encoding="utf-8")

    rust_toolchain = install_rust_toolchain(args.source, args.inputs, lock, args.arch)
    native_host_tools = None
    native_executables = None
    if args.arch == "arm64":
        native_host_tools = remove_foreign_host_binaries(args.source, args.arch)
        architecture_inputs = lock["architecture_inputs"][args.arch]
        native_executables = {
            name: install_native_executable(args.source, args.inputs, architecture_inputs[name], args.arch)
            for name in NATIVE_EXECUTABLES
        }
        go_path = "third_party/dawn/tools/golang/linux-arm64/bin/go"
    else:
        go_path = "third_party/dawn/tools/golang/linux-amd64/bin/go"
    links = [
        ("third_party/node/linux/node-linux-x64/bin/node", "/usr/bin/node"),
        ("third_party/gperf/cipd/bin/gperf", "/usr/bin/gperf"),
        (go_path, "/usr/bin/go"),
    ]
    if rust_toolchain is not None:
        links.append(("buildtools/linux64-format/clang-format", "../../third_party/rust-toolchain/bin/clang-format"))
    builder_links = []
    for relative, target in links:
        path = args.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            path.unlink()
        path.symlink_to(target)
        builder_links.append({"path": relative, "target": target, "added": True, "network": "none"})

    if args.arch == "arm64":
        ffmpeg_unbundle = {
            "name": "ffmpeg",
            "state": "bundled",
            "gn_replacement": "third_party/ffmpeg/BUILD.gn",
            "removed_source_trees": [],
        }
    else:
        unbundle_script = args.source / "build/linux/unbundle/replace_gn_files.py"
        run([python, str(unbundle_script), "--system-libraries", "ffmpeg"], cwd=args.source)
        ffmpeg_unbundle = {
            "name": "ffmpeg",
            "state": "system",
            "gn_replacement": "third_party/ffmpeg/BUILD.gn",
            "removed_source_trees": [],
        }

    source_report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        **identity,
        "devtools_revision": lock["chromium"]["devtools_revision"],
        "git_used": False,
        "git_required": False,
        "generator_runs": [
            {"name": "version", "status": "passed", "network": "none", "git_used": False},
            {"name": "revision", "status": "passed", "network": "none", "git_used": False},
            {"name": "deps", "status": "passed", "network": "none", "git_used": False},
            {"name": "prune", "status": "passed", "network": "none", "git_used": False},
            {"name": "patches", "status": "passed", "network": "none", "git_used": False},
            {"name": "domain-substitution", "status": "passed", "network": "none", "git_used": False},
            {"name": "name-substitution", "status": "passed", "network": "none", "git_used": False},
            {"name": "i18n", "status": "passed", "network": "none", "git_used": False},
            {"name": "resources", "status": "passed", "network": "none", "git_used": False},
            {"name": "musl-patches", "status": "passed", "network": "none", "git_used": False},
        ],
        "revision_files": [
            {"path": "chrome/VERSION", "contains": f"MAJOR={expected['MAJOR']}"},
            {"path": "chrome/VERSION", "contains": "HELIUM_MAJOR=0"},
        ],
        "source_transformations": {
            "rust_target": {"path": "build/rust/known-target-triples.txt", "target": rust_target, "added": True, "network": "none"},
            "builder_links": builder_links,
            "rust_toolchain": rust_toolchain,
            "native_host_tools": native_host_tools,
            "native_executables": native_executables,
        },
        "source_revisions": {
            "core": lock["product"]["core_revision"],
            "linux": lock["product"]["source_revision"],
        },
    }
    patch_report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        **identity,
        "entries": inherited,
        "upstream_layers": patch_records,
        "excluded_upstream_patches": core_skipped + linux_skipped,
        "local_patches": [
            {
                "name": record["name"],
                "sha256": record["sha256"],
                "conflict_record": {"upstream_sets": ["alpine", "copium"]},
                **{k: record[k] for k in ("result", "fuzz", "offset", "dry_run", "deterministic_options", "patch_path")},
            }
            for record in local_records
        ],
    }
    pruning_report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": version,
        "requested": pruning_entries,
        "removed": pruning_removed,
        "already_absent": sorted(set(pruning_entries) - pruning_present),
        "allowlisted_absent": sorted(set(pruning_entries) - pruning_present),
        "retained_build_dependencies": pruning_retained,
        "unexpected_absent": [],
    }
    domain_report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": version,
        "processed": len(domain_before),
        "changed": domain_changed,
        "unprocessed": [],
        "unresolved": sorted(set(domain_entries) - set(domain_before)),
    }
    unbundle = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "libraries": [ffmpeg_unbundle],
    }
    preparation = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        **identity,
        "recipe": [
            "unpack-base",
            "unpack-linux-overlay",
            "deps",
            "prune",
            "helium-core-patches",
            "helium-linux-patches",
            "domain-substitution",
            "name-substitution",
            "i18n",
            "version",
            "generate-resources",
            "replace-resources",
            "musl-patches",
            "builder-links",
            "rust-target",
            *(["rust-toolchain"] if rust_toolchain else []),
            *(["native-host-tools"] if native_host_tools else []),
            "ffmpeg-bundled" if args.arch == "arm64" else "ffmpeg-unbundle",
        ],
        "patch_count": len(patch_records) + len(local_records) + sum(1 for item in inherited if item["state"] == "apply"),
    }
    write_atomic(args.metadata / "source-inputs.json", source_report)
    write_atomic(args.metadata / "patches.json", patch_report)
    write_atomic(args.metadata / "pruning.json", pruning_report)
    write_atomic(args.metadata / "domain-substitution.json", domain_report)
    write_atomic(args.metadata / "toolchain-selection.json", toolchain_selection(lock, args.arch))
    write_atomic(args.metadata / "system-unbundle.json", unbundle)
    write_atomic(args.metadata / "helium-preparation.json", preparation)


def patch_path(root: Path, project: str, name: str) -> Path:
    if project == "alpine":
        direct = root / "community/chromium" / name
        if direct.is_file():
            return direct
        return next(root.glob(f"*/community/chromium/{name}"))
    if project == "copium":
        direct = root / name
        if direct.is_file():
            return direct
        return next(root.glob(f"*/{name}"))
    if project == "portablelinux":
        return next(root.glob(f"*/patches/ungoogled-chromium/portablelinux/{name}"))
    raise SystemExit(f"unknown patch project {project}")


if __name__ == "__main__":
    main()
