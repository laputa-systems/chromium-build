#!/usr/bin/env python3
"""Write image-resident environment metadata without embedding Python in Dockerfile."""

import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "amd64"))
    parser.add_argument("--inputs-lock", type=Path, required=True)
    parser.add_argument("--package-lock", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock = json.loads(args.inputs_lock.read_text(encoding="utf-8"))
    arch = lock["toolchain"]["architectures"][args.architecture]
    package_digest = digest(args.package_lock)
    policy_digest = digest(args.policy)
    metadata = {
        "schema": lock["schema"],
        "architecture": args.architecture,
        "linux_arch": arch["linux_arch"],
        "chromium_cpu": arch["chromium_cpu"],
        "target_triple": arch["target_triple"],
        "toolchain_version": lock["toolchain"]["version"],
        "toolchain_root": lock["toolchain"]["root"],
        "toolchain_sha256": arch["archive_sha256"],
        "environment_compatibility_id": (
            f"{args.architecture}-{package_digest[:16]}-{policy_digest[:16]}"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
