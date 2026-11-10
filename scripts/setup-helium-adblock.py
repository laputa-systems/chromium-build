#!/usr/bin/env python3
"""Vendor Helium's native adblock crates and filter lists from locked inputs, or regenerate their GN files.

`vendor` runs in prepare without network: it checks every crate against the workspace Cargo.lock, extracts
it where gnrt's generated BUILD.gn files expect it, and unpacks the bundled filter lists. `generate` is a
developer step for a changed crate set: it needs a working cargo and a gnrt binary, and its output is
committed as config/patches/helium/adblock-rust-gn.patch.
"""

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
import tomllib
from pathlib import Path

WORKSPACE_SOURCE = "components/helium_adblock/rs/crates"
WORKSPACE = "third_party/rust/helium/chromium_crates_io"
CHROMIUM_WORKSPACE = "third_party/rust/chromium_crates_io"
FILTERS = "third_party/helium_filters"


def epoch(version: str) -> str:
    major, minor, patch = version.split(".", 2)
    if major != "0":
        return f"v{major}"
    if minor != "0":
        return f"v0_{minor}"
    return f"v0_0_{patch.split('-', 1)[0]}"


def registry_packages(lock_text: str) -> list[dict[str, str]]:
    packages = []
    for block in lock_text.split("[[package]]")[1:]:
        fields = dict(re.findall(r'^(name|version|source|checksum) = "([^"]*)"', block, re.MULTILINE))
        if fields.get("source", "").startswith("registry+"):
            if "checksum" not in fields:
                raise SystemExit(f"Cargo.lock has no checksum for {fields['name']} {fields['version']}")
            packages.append(fields)
    return packages


def safe_members(archive: tarfile.TarFile, top: str | None) -> list[tarfile.TarInfo]:
    members = []
    for member in archive.getmembers():
        parts = Path(member.name).parts
        if member.issym() or member.islnk() or ".." in parts or Path(member.name).is_absolute():
            raise SystemExit(f"{archive.name}: refusing archive member {member.name}")
        if top is not None and (not parts or parts[0] != top):
            raise SystemExit(f"{archive.name}: member {member.name} is outside {top}/")
        members.append(member)
    return members


def vendor_crates(source: Path, inputs: Path) -> list[dict[str, str]]:
    lock_text = (source / WORKSPACE_SOURCE / "Cargo.lock").read_text(encoding="utf-8")
    config = tomllib.loads((source / WORKSPACE_SOURCE / "gnrt_config.toml").read_text(encoding="utf-8"))
    vendor = source / WORKSPACE / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    records = []
    for package in registry_packages(lock_text):
        name, version = package["name"], package["version"]
        crate = inputs / f"{name}-{version}.crate"
        if not crate.is_file():
            raise SystemExit(f"missing locked crate {crate.name} in {inputs}")
        digest = hashlib.sha256(crate.read_bytes()).hexdigest()
        if digest != package["checksum"]:
            raise SystemExit(f"{crate.name}: sha256 {digest} does not match Cargo.lock {package['checksum']}")
        destination = vendor / f"{name}-{epoch(version)}"
        if destination.exists():
            shutil.rmtree(destination)
        top = f"{name}-{version}"
        with tarfile.open(crate, "r:gz") as archive:
            archive.extractall(vendor, members=safe_members(archive, top), filter="data")
        (vendor / top).rename(destination)
        for relative in config.get("crate", {}).get(name, {}).get("remove_vendored_files", []):
            target = destination / relative
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        (destination / ".cargo-checksum.json").write_text('{"files":{}}', encoding="utf-8")
        records.append({"name": name, "version": version, "sha256": digest, "path": str(destination.relative_to(source))})
    return records


def unpack_filters(source: Path, filters: Path, expected_sha256: str) -> str:
    digest = hashlib.sha256(filters.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise SystemExit(f"{filters.name}: sha256 {digest} is not the locked {expected_sha256}")
    destination = source / FILTERS
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(filters, "r:gz") as archive:
        archive.extractall(destination, members=safe_members(archive, None), filter="data")
    return digest


def vendor(source: Path, inputs: Path, filters: str, filters_sha256: str) -> dict:
    return {
        "filters": {"path": FILTERS, "archive": filters, "sha256": unpack_filters(source, inputs / filters, filters_sha256)},
        "crates": vendor_crates(source, inputs),
        "network": "none",
    }


def copy_workspace_inputs(source: Path) -> None:
    origin = source / WORKSPACE_SOURCE
    workspace = source / WORKSPACE
    chromium = source / CHROMIUM_WORKSPACE
    (workspace / ".cargo").mkdir(parents=True, exist_ok=True)
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    for relative in ("Cargo.toml", "gnrt_config.toml", "src/lib.rs"):
        shutil.copyfile(origin / relative, workspace / relative)
    lock = (origin / "Cargo.lock").read_text(encoding="utf-8")
    (workspace / "Cargo.lock").write_text(re.sub(r'^checksum = "[^"]*"\n', "", lock, flags=re.MULTILINE), encoding="utf-8")
    for relative in (".cargo/config.toml", "README.chromium.hbs", "BUILD.gn.hbs"):
        shutil.copyfile(chromium / relative, workspace / relative)


def cargo_metadata(source: Path, environment: dict[str, str]) -> dict:
    workspace = source / WORKSPACE
    result = subprocess.run(
        [sys.executable, str(source / "tools/crates/run_cargo.py"), "metadata", "--locked", "--offline",
         "--format-version", "1", "--manifest-path", str(workspace / "Cargo.toml")],
        cwd=workspace, env=environment, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"cargo metadata failed with exit {result.returncode}:\n{result.stderr}")
    return json.loads(result.stdout)


def write_forwarders(source: Path, metadata: dict) -> None:
    members = set(metadata["workspace_members"])
    for package in metadata["packages"]:
        if package["source"] is not None or package["id"] in members:
            continue
        target = package["name"].replace("-", "_")
        crate_epoch = epoch(package["version"])
        upstream = source / "third_party/rust" / target / crate_epoch
        if not (upstream / "BUILD.gn").is_file():
            raise SystemExit(f"missing Chromium GN target for {target} {crate_epoch}")
        destination = source / "third_party/rust/helium" / target / crate_epoch
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "BUILD.gn").write_text(
            "# Copyright 2026 The Helium Authors\n"
            "# Use of this source code is governed by a BSD-style license that can be\n"
            "# found in the LICENSE file.\n\n"
            'group("lib") {\n'
            f'  public_deps = [ "//third_party/rust/{target}/{crate_epoch}:lib" ]\n'
            "}\n",
            encoding="utf-8",
        )
        if (upstream / "README.chromium").is_file():
            shutil.copyfile(upstream / "README.chromium", destination / "README.chromium")


def write_engine_version(source: Path, metadata: dict) -> str:
    versions = {package["version"] for package in metadata["packages"] if package["name"] == "adblock"}
    if len(versions) != 1:
        raise SystemExit(f"cargo metadata resolved adblock versions {sorted(versions)}, want exactly one")
    version = versions.pop()
    (source / WORKSPACE / "adblock_engine_version.h").write_text(
        "// Copyright 2026 The Helium Authors\n"
        "// Use of this source code is governed by a BSD-style license that can be\n"
        "// found in the LICENSE file.\n\n"
        "#ifndef THIRD_PARTY_RUST_HELIUM_CHROMIUM_CRATES_IO_ADBLOCK_ENGINE_VERSION_H_\n"
        "#define THIRD_PARTY_RUST_HELIUM_CHROMIUM_CRATES_IO_ADBLOCK_ENGINE_VERSION_H_\n\n"
        "namespace helium_adblock {\n\n"
        f'inline constexpr char kEngineVersion[] = "adblock-{version}";\n\n'
        "}  // namespace helium_adblock\n\n"
        "#endif  // THIRD_PARTY_RUST_HELIUM_CHROMIUM_CRATES_IO_ADBLOCK_ENGINE_VERSION_H_\n",
        encoding="utf-8",
    )
    return version


def generate(source: Path, gnrt: Path) -> str:
    copy_workspace_inputs(source)
    environment = {**os.environ, "CARGO_NET_OFFLINE": "true"}
    metadata = cargo_metadata(source, environment)
    subprocess.run([str(gnrt), "--third-party-workspace", "helium", "gen"], cwd=source, env=environment, check=True)
    write_forwarders(source, metadata)
    return write_engine_version(source, metadata)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("vendor", "generate"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--inputs", type=Path, required=True)
        command.add_argument("--filters", required=True)
        command.add_argument("--filters-sha256", required=True)
        command.add_argument("--record", type=Path, required=True)
        if name == "generate":
            command.add_argument("--gnrt", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    record = vendor(source, args.inputs, args.filters, args.filters_sha256)
    if args.command == "generate":
        record["engine"] = f"adblock-{generate(source, args.gnrt)}"
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
