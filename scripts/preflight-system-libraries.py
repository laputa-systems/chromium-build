#!/usr/bin/env python3
"""Validate the builder's dynamic-library closure against the locked policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from script_support import atomic_json, command_path, fail_main, require_file, run, ScriptFailure


def main() -> None:
    parser = argparse.ArgumentParser()
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    metadata = Path(os.environ.get("CHROMIUM_METADATA_ROOT", "/opt/chromium-build-metadata"))
    arch = os.environ.get("CHROMIUM_ARCH", "arm64")
    parser.add_argument("--lock", type=Path, default=root / f"config/packages.{arch}.lock")
    parser.add_argument("--policy", type=Path, default=root / "config/system-library-preflight.schema.json")
    parser.add_argument("--report", type=Path, default=Path(os.environ.get("SYSTEM_LIBRARY_PREFLIGHT", metadata / "system-library-preflight.json")))
    parser.add_argument("--architecture", default=arch)
    args = parser.parse_args()

    require_file(args.lock, f"missing {args.lock}")
    require_file(args.policy, f"missing {args.policy}")
    command_path("scanelf")
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    package_digest = hashlib.sha256(args.lock.read_bytes()).hexdigest()
    policy_digest = hashlib.sha256(args.policy.read_bytes()).hexdigest()
    lines = run(
        ["scanelf", "-RBF", "%F|%S|%n", "/lib", "/usr/lib"],
        capture_output=True,
    ).stdout.splitlines()
    objects = {}
    by_soname = {}
    for line in lines:
        if not line.strip():
            continue
        path, soname, needed_text = line.split("|", 2)
        path = path.strip()
        soname = soname.strip() or Path(path).name
        entry = {"path": path, "soname": soname, "needed": [value for value in needed_text.strip().split(",") if value]}
        objects[path] = entry
        by_soname.setdefault(soname, path)

    roots = {
        path: entry
        for path, entry in objects.items()
        if any(Path(path).name.startswith(root_name) for root_name in policy["proposed_roots"])
    }
    if not roots:
        raise ScriptFailure("preflight found no proposed system-library roots")

    edges = []
    glibc_entries = []
    unclassified_edges = []
    exception_leaks = []
    visited = set()
    pending = [(path, path, True) for path in roots]
    while pending:
        parent, path, ffmpeg_descendant = pending.pop()
        if (parent, path) in visited:
            continue
        visited.add((parent, path))
        for soname in objects[path]["needed"]:
            child = by_soname.get(soname)
            child_path = child or ""
            child_is_ffmpeg = ffmpeg_descendant or any(
                Path(parent).name.startswith(root_name) for root_name in policy["ffmpeg_roots"]
            )
            if soname in ("libc.so.6", "ld-linux.so.2", "ld-linux-x86-64.so.2"):
                classification = "forbidden-glibc"
                glibc_entries.append({"path": parent, "soname": soname})
            elif child is None:
                classification = "unclassified"
                unclassified_edges.append({"path": parent, "soname": soname})
            elif soname in policy["exception_sonames"]:
                classification = "ffmpeg-exception" if child_is_ffmpeg else "exception-leak"
                if classification == "exception-leak":
                    exception_leaks.append({"path": parent, "soname": soname})
            elif soname.startswith(tuple(policy["ffmpeg_roots"])):
                classification = "ffmpeg-root"
            else:
                classification = "host-owned"
            edges.append({"path": parent, "soname": soname, "classification": classification})
            if child is not None:
                pending.append((parent, child_path, child_is_ffmpeg))

    classifications = {}
    for edge in edges:
        key = edge["classification"]
        classifications[key] = classifications.get(key, 0) + 1
    report = {
        "schema": policy["schema"],
        "status": "complete",
        "architecture": args.architecture,
        "package_lock_sha256": package_digest,
        "policy_sha256": policy_digest,
        "environment_compatibility_id": f"{args.architecture}-{package_digest[:16]}-{policy_digest[:16]}",
        "unclassified_edges": unclassified_edges,
        "glibc_entries": glibc_entries,
        "exception_leaks": exception_leaks,
        "ccache_runtime_classification": "builder-only",
        "classification_counts": classifications,
        "edges": edges,
    }
    if glibc_entries or unclassified_edges or exception_leaks:
        raise ScriptFailure(
            f"preflight failed: glibc={len(glibc_entries)} unclassified={len(unclassified_edges)} exception_leaks={len(exception_leaks)}"
        )
    atomic_json(args.report, report)


if __name__ == "__main__":
    raise SystemExit(fail_main("preflight", main))
