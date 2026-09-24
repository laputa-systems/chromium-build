#!/usr/bin/env python3
"""Validate the prepared Chromium tree for Gate C without network or Git."""

import argparse
import json
import os
import re
import sys
from pathlib import Path



VERSION_RE = re.compile(r"^(MAJOR|MINOR|BUILD|PATCH)=(\d+)\s*$", re.MULTILINE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STATES = {"apply", "superseded", "not-applicable"}


class Failure(Exception):
    pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Failure(f"cannot read JSON {path}: {error}") from error


def require(condition, message):
    if not condition:
        raise Failure(message)


def require_complete(report, label, version):
    require(report.get("status") == "complete", f"{label}: status is not complete")
    require(report.get("network") == "none", f"{label}: network was not disabled")
    require(
        report.get("chromium_version") == version,
        f"{label}: Chromium version does not match {version}",
    )


def source_identity_checks(args, lock, source):
    version = lock["chromium"]["version"]
    devtools = lock["chromium"]["devtools_revision"]
    require_complete(source, "source-inputs", version)
    require(source.get("git_used") is False, "source-inputs: Git was used")
    require(source.get("git_required") is False, "source-inputs: Git is required")
    require(source.get("devtools_revision") == devtools, "source-inputs: DevTools revision differs")

    generator_runs = source.get("generator_runs")
    require(isinstance(generator_runs, list) and generator_runs, "source-inputs: no generator runs recorded")
    names = set()
    for run in generator_runs:
        require(run.get("status") == "passed", "source-inputs: a generator did not pass")
        require(run.get("network") == "none", "source-inputs: generator ran with network")
        require(run.get("git_used") is False, "source-inputs: generator used Git")
        names.add(run.get("name"))
    require("version" in names and "revision" in names, "source-inputs: version/revision generators are missing")

    revision_files = source.get("revision_files")
    require(isinstance(revision_files, list) and revision_files, "source-inputs: revision files are missing")
    for item in revision_files:
        path = args.source_root / item["path"]
        require(path.is_file(), f"source-inputs: missing revision file {item['path']}")
        expected = item.get("contains")
        if expected is not None:
            require(expected in path.read_text(encoding="utf-8", errors="replace"), f"source-inputs: {item['path']} has unexpected content")


def version_checks(source_root, lock):
    version = lock["chromium"]["version"]
    major, minor, build, patch = version.split(".")
    version_file = source_root / "chrome/VERSION"
    require(version_file.is_file(), "missing chrome/VERSION")
    values = dict(VERSION_RE.findall(version_file.read_text(encoding="utf-8")))
    require(values == {"MAJOR": major, "MINOR": minor, "BUILD": build, "PATCH": patch}, "chrome/VERSION does not match the lock")


def patch_checks(args, lock):
    inventory = read_json(args.patch_inventory)
    disposition = read_json(args.patch_disposition)
    require(inventory.get("schema") == disposition.get("schema"), "patch manifests use different schemas")
    patch_application = disposition.get("patch_application", {})
    max_fuzz = patch_application.get("max_fuzz", 0)
    require(isinstance(max_fuzz, int) and max_fuzz >= 0, "patch application has an invalid fuzz limit")

    expected_layers = [(layer["project"], layer["revision"], layer["patches"]) for layer in inventory["layers"]]
    actual_layers = []
    entries = []
    for layer in disposition.get("layers", []):
        actual_names = []
        for entry in layer.get("patches", []):
            name = entry.get("name")
            require(name not in actual_names, f"patch disposition duplicates {name}")
            actual_names.append(name)
            require(entry.get("state") in STATES, f"patch {name} has an invalid state")
            if entry["state"] == "superseded":
                require(entry.get("replacement"), f"superseded patch {name} has no replacement")
            if entry["state"] == "not-applicable":
                require(entry.get("affected_paths"), f"not-applicable patch {name} has no affected paths")
            entries.append((layer["project"], name, entry))
        actual_layers.append((layer.get("project"), layer.get("revision"), actual_names))
    require(actual_layers == expected_layers, "patch disposition does not exactly match the pinned inventory")

    provenance = read_json(args.patch_provenance)
    require_complete(provenance, "patch provenance", lock["chromium"]["version"])
    provenance_entries = provenance.get("entries", [])
    require(len(provenance_entries) == len(entries), "patch provenance does not cover every inventory entry")
    by_key = {(item.get("project"), item.get("name")): item for item in provenance_entries}
    require(len(by_key) == len(provenance_entries), "patch provenance contains duplicate entries")
    for project, name, disposition_entry in entries:
        item = by_key.get((project, name))
        if item is None:
            raise Failure(f"patch provenance is missing {project}:{name}")
        require(SHA256_RE.fullmatch(item.get("sha256", "")), f"patch {name} has an invalid checksum")
        require(isinstance(item.get("strip_level"), int) and item["strip_level"] >= 0, f"patch {name} has no strip level")
        require(isinstance(item.get("offset"), int) and isinstance(item.get("fuzz"), int), f"patch {name} has no offset/fuzz record")
        require(isinstance(item.get("deterministic_options"), dict), f"patch {name} has no deterministic options")
        require(item["deterministic_options"].get("fuzz_limit") == max_fuzz, f"patch {name} used an unexpected fuzz limit")
        state = disposition_entry["state"]
        if state == "apply":
            require(item.get("result") == "applied", f"patch {name} was not applied")
            require(item.get("dry_run") == "passed", f"patch {name} has no successful dry-run")
            patch_path = Path(item.get("patch_path", ""))
            require(patch_path.is_file(), f"applied patch file is missing: {patch_path}")
            require(item["fuzz"] == 0 or item.get("allowed_fuzz") is not None, f"patch {name} has unapproved fuzz")
        elif state == "superseded":
            require(item.get("result") == "superseded", f"patch {name} is not recorded as superseded")
            require(item.get("replacement") == disposition_entry["replacement"], f"patch {name} names the wrong replacement")
        else:
            require(item.get("result") == "not-applicable", f"patch {name} is not recorded as not-applicable")
            require(item.get("affected_paths") == disposition_entry["affected_paths"], f"patch {name} has wrong affected paths")
    for local in provenance.get("local_patches", []):
        require(local.get("sha256") and SHA256_RE.fullmatch(local["sha256"]), "local patch has no checksum")
        require(local.get("conflict_record", {}).get("upstream_sets"), "local conflict patch lacks upstream conflict provenance")


def pruning_checks(args, lock):
    report = read_json(args.pruning_report)
    require_complete(report, "pruning report", lock["chromium"]["version"])
    requested = report.get("requested", [])
    removed = set(report.get("removed", []))
    already_absent = set(report.get("already_absent", []))
    allowlisted = set(report.get("allowlisted_absent", []))
    require(requested, "pruning report contains no requested targets")
    require(not report.get("unexpected_absent"), "pruning found an unallowlisted missing target")
    require(already_absent <= allowlisted, "pruning accepted an absent target outside the committed allowlist")
    require(set(requested) == removed | already_absent, "pruning report does not account for every requested target")
    for path in removed | already_absent:
        require(not (args.source_root / path).exists(), f"pruned target remains in source: {path}")


def domain_checks(args, lock):
    report = read_json(args.domain_report)
    require_complete(report, "domain substitution report", lock["chromium"]["version"])
    require(report.get("unprocessed") == [], "domain substitution has unprocessed entries")
    require(report.get("unresolved") == [], "domain substitution has unresolved entries")
    require(isinstance(report.get("processed"), int) and report["processed"] >= 0, "domain substitution count is missing")


def toolchain_checks(args):
    selection = read_json(args.toolchain_selection)
    require(selection.get("status") == "complete", "toolchain selection report is incomplete")
    require(selection.get("network") == "none", "toolchain selection used network")
    require(selection.get("chromium_prebuilt_selected") is True, "the Chromium prebuilt compiler is not selected")
    require(selection.get("sysroot_selected") is False, "a Chromium sysroot is selected")
    require(selection.get("forbidden_selected") == [], "a forbidden toolchain or download script is selected")
    for key in ("compiler", "cxx", "linker", "ar", "rust_linker"):
        value = selection.get(key, "")
        require(value.startswith("/opt/chromium-llvm/"), f"toolchain selection {key} is outside Chromium LLVM")
    require(selection.get("rust_target_triple") in {"aarch64-alpine-linux-musl", "x86_64-alpine-linux-musl"}, "Rust target triple is not an approved Alpine musl target")


def unbundle_checks(args):
    report = read_json(args.unbundle_report)
    require(report.get("status") == "complete", "system-unbundle report is incomplete")
    require(report.get("network") == "none", "system-unbundle validation used network")
    libraries = report.get("libraries", [])
    require(libraries, "system-unbundle report contains no libraries")
    for library in libraries:
        replacement = args.source_root / library["gn_replacement"]
        require(replacement.exists(), f"missing GN replacement for {library['name']}: {replacement}")
        for removed in library.get("removed_source_trees", []):
            needle = removed.rstrip("/")
            for root, _, files in os.walk(args.source_root):
                root_path = Path(root)
                if ".git" in root_path.parts or "out" in root_path.parts:
                    continue
                for filename in files:
                    if not (filename.endswith(".gn") or filename.endswith(".gni")):
                        continue
                    path = root_path / filename
                    if needle in path.read_text(encoding="utf-8", errors="replace"):
                        raise Failure(f"removed source tree {removed} is still referenced by GN: {path}")


def main():
    parser = argparse.ArgumentParser()
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata"))
    parser.add_argument("--source-root", type=Path, default=Path(os.environ.get("CHROMIUM_SOURCE_ROOT", work / "src")))
    parser.add_argument("--inputs-lock", type=Path, default=root / "config/inputs.lock")
    parser.add_argument("--source-inputs", type=Path, default=metadata / "source-inputs.json")
    parser.add_argument("--patch-inventory", type=Path, default=root / "config/patch-inventory.json")
    parser.add_argument("--patch-disposition", type=Path, default=root / "config/patch-disposition.json")
    parser.add_argument("--patch-provenance", type=Path, default=metadata / "patches.json")
    parser.add_argument("--pruning-report", type=Path, default=metadata / "pruning.json")
    parser.add_argument("--domain-report", type=Path, default=metadata / "domain-substitution.json")
    parser.add_argument("--toolchain-selection", type=Path, default=metadata / "toolchain-selection.json")
    parser.add_argument("--unbundle-report", type=Path, default=metadata / "system-unbundle.json")
    parser.add_argument("--output", type=Path, default=metadata / "gate-c-report.json")
    args = parser.parse_args()

    require(args.source_root.is_dir(), f"missing prepared source root {args.source_root}")
    require(not (args.source_root / ".git").exists(), "prepared source contains Git metadata")
    lock = read_json(args.inputs_lock)
    source = read_json(args.source_inputs)
    source_identity_checks(args, lock, source)
    version_checks(args.source_root, lock)
    patch_checks(args, lock)
    pruning_checks(args, lock)
    domain_checks(args, lock)
    toolchain_checks(args)
    unbundle_checks(args)

    report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": lock["chromium"]["version"],
        "devtools_revision": lock["chromium"]["devtools_revision"],
        "checks": [
            "version-files",
            "source-control-free-revision-metadata",
            "binary-pruning",
            "patch-disposition-and-provenance",
            "domain-substitution",
            "bundled-toolchain-and-sysroot-selection",
            "system-unbundle-gn-replacements",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    try:
        main()
    except Failure as error:
        print(f"Gate C: {error}", file=sys.stderr)
        raise SystemExit(1)
