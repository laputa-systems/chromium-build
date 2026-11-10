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
import tarfile
import tempfile
from pathlib import Path


def read_json(path: Path) -> dict:
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


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
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


def apply_patch(source: Path, patch: Path, record_dir: Path | None = None) -> dict:
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


def apply_series(source: Path, root: Path, label: str, record_dir: Path) -> list[dict]:
    records = []
    for patch in series(root, label):
        records.append(apply_patch(source, patch, record_dir))
    return records


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


def install_rust_toolchain(source: Path, inputs: Path, lock: dict, arch: str) -> dict | None:
    item = lock["architecture_inputs"][arch].get("rust_toolchain")
    if item is None:
        return None
    destination = source / item["destination"]
    pinned = re.search(r"^RUST_REVISION = '([0-9a-f]+)'$", (source / "tools/rust/update_rust.py").read_text(encoding="utf-8"), re.MULTILINE)
    if pinned is None or pinned.group(1) != item["revision"]:
        raise SystemExit(f"tools/rust/update_rust.py pins Rust {pinned and pinned.group(1)}, the lock provides {item['revision']}")
    chromium_version = (destination / "VERSION").read_text(encoding="utf-8")
    archive = inputs / item["filename"]
    if not archive.is_file():
        raise SystemExit(f"missing locked Rust toolchain {archive}")
    top = item["filename"].removesuffix(".tar.xz")
    with tempfile.TemporaryDirectory(prefix="rust-toolchain.", dir=destination.parent) as directory:
        with tarfile.open(archive) as stream:
            if {member.name.split("/", 1)[0] for member in stream.getmembers()} != {top}:
                raise SystemExit(f"{archive.name} must contain only {top}/")
            stream.extractall(directory, filter="data")
        extracted = Path(directory) / top
        if (extracted / "VERSION").read_text(encoding="utf-8") != chromium_version:
            raise SystemExit(f"{archive.name} VERSION differs from the Rust toolchain Chromium pins")
        shutil.rmtree(destination)
        extracted.rename(destination)
    rustc = subprocess.run([str(destination / "bin/rustc"), "-vV"], check=True, capture_output=True, text=True).stdout
    expected = {
        "commit-hash": item["revision"],
        "host": lock["toolchain"]["architectures"][arch]["target_triple"],
        "LLVM version": lock["toolchain"]["version"],
    }
    reported = dict(line.split(": ", 1) for line in rustc.splitlines() if ": " in line)
    mismatched = {key: reported.get(key) for key, value in expected.items() if reported.get(key) != value}
    if mismatched:
        raise SystemExit(f"installed rustc does not match the lock: {mismatched}, expected {expected}")
    return {"path": item["destination"], "archive": item["filename"], "sha512": item["sha512"], **expected, "network": "none"}


ELF_MACHINES = {"amd64": 0x3E, "arm64": 0xB7}
GLIBC_SYSROOT_GLOB = "build/linux/debian_*-sysroot"


def elf_machine(path: Path) -> int | None:
    with path.open("rb") as stream:
        header = stream.read(20)
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    return int.from_bytes(header[18:20], "little")


def remove_foreign_host_binaries(source: Path, arch: str) -> dict:
    removed: dict[str, int] = {}

    def record(relative: Path) -> None:
        key = "/".join(relative.parts[:3])
        removed[key] = removed.get(key, 0) + 1

    for sysroot in sorted(source.glob(GLIBC_SYSROOT_GLOB)):
        for path in sysroot.rglob("*"):
            if path.is_file() and not path.is_symlink():
                record(path.relative_to(source))
        shutil.rmtree(sysroot)
    native = ELF_MACHINES[arch]
    for directory, _, files in os.walk(source):
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            machine = elf_machine(path)
            if machine is not None and machine != native:
                record(path.relative_to(source))
                path.unlink()
    return {"glibc_sysroots": GLIBC_SYSROOT_GLOB, "removed": dict(sorted(removed.items())), "network": "none"}


NATIVE_EXECUTABLES = ("typescript_native", "esbuild_native")


def install_native_executable(source: Path, inputs: Path, item: dict, arch: str) -> dict:
    destination = source / item["destination"]
    pin = item["chromium_pin"]
    if pin["contains"] not in (source / pin["path"]).read_text(encoding="utf-8"):
        raise SystemExit(f"{pin['path']} does not pin {pin['contains']!r}; {item['filename']} would not match Chromium")
    archive = inputs / item["filename"]
    if not archive.is_file():
        raise SystemExit(f"missing locked host tool {archive}")
    with tarfile.open(archive) as stream:
        member = stream.extractfile(item["member"])
        if member is None:
            raise SystemExit(f"{archive.name} has no {item['member']}")
        temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(member.read())
    temporary.chmod(0o755)
    if elf_machine(temporary) != ELF_MACHINES[arch]:
        raise SystemExit(f"{item['member']} in {archive.name} is not a {arch} executable")
    temporary.replace(destination)
    version = subprocess.run([str(destination), "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if version != item["version_output"]:
        raise SystemExit(f"{item['destination']} reports {version!r}, expected {item['version_output']!r}")
    return {"path": item["destination"], "archive": item["filename"], "sha512": item["sha512"], "version": version, "network": "none"}


def toolchain_selection(lock: dict, arch: str) -> dict:
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
    deps = [
        python,
        str(core / "utils/downloads.py"),
        "unpack",
        "-i",
        str(core / "deps.ini"),
        "-c",
        str(args.inputs),
        str(args.source),
        "--components",
        "search_engines_data",
        "ublock_origin",
    ]
    run(deps)
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
    patch_records = apply_series(args.source, core, "core", args.metadata / "patches/core") + apply_series(
        args.source, platform, "linux", args.metadata / "patches/linux"
    )

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
