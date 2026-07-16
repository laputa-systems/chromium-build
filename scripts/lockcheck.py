#!/usr/bin/env python3
"""Small, dependency-free checks shared by the environment gate."""

import hashlib
import json
import sys


def read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read JSON {path}: {error}") from error


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def subset(expected, actual, path="lock"):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ValueError(f"{path}: expected object")
        for key, value in expected.items():
            if key not in actual:
                raise ValueError(f"{path}.{key}: missing")
            subset(value, actual[key], f"{path}.{key}")
    elif expected != actual:
        raise ValueError(f"{path}: expected {expected!r}, got {actual!r}")


def package_manifest(lock_path, manifest_path):
    lock = read_json(lock_path)
    if lock.get("status") != "resolved":
        raise SystemExit(f"{lock_path}: package lock is not resolved")
    resolved = lock.get("resolved")
    if not isinstance(resolved, list) or not resolved:
        raise SystemExit(f"{lock_path}: resolved package list is empty")
    with open(manifest_path, encoding="utf-8") as stream:
        actual = sorted(line.strip() for line in stream if line.strip())
    expected = sorted(resolved)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise SystemExit(
            f"package manifest differs from {lock_path}: missing={missing[:8]} extra={extra[:8]}"
        )
    expected_hash = lock.get("manifest_sha256")
    actual_hash = hashlib.sha256(("\n".join(actual) + "\n").encode()).hexdigest()
    if expected_hash != actual_hash:
        raise SystemExit(f"{lock_path}: manifest_sha256 does not match resolved entries")


def environment(lock_path, metadata_path, architecture):
    lock = read_json(lock_path)
    metadata = read_json(metadata_path)
    toolchain = lock["toolchain"]
    arch = toolchain["architectures"][architecture]
    expected = {
        "schema": lock["schema"],
        "architecture": architecture,
        "linux_arch": arch["linux_arch"],
        "chromium_cpu": arch["chromium_cpu"],
        "target_triple": arch["target_triple"],
        "toolchain_version": toolchain["version"],
        "toolchain_root": toolchain["root"],
        "toolchain_sha256": arch["archive_sha256"],
    }
    try:
        subset(expected, metadata)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def preflight(schema_path, report_path, policy_path, package_lock_path):
    schema = read_json(schema_path)
    report = read_json(report_path)
    for key in schema["required"]:
        if key not in report:
            raise SystemExit(f"{report_path}: missing {key}")
    for key in schema["zero_fields"]:
        if report[key] != []:
            raise SystemExit(f"{report_path}: {key} is not empty")
    if report["ccache_runtime_classification"] != schema["ccache_runtime_classification"]:
        raise SystemExit(f"{report_path}: ccache is not classified builder-only")
    report["policy_sha256"] == digest(policy_path) or sys.exit(
        f"{report_path}: policy digest mismatch"
    )
    report["package_lock_sha256"] == digest(package_lock_path) or sys.exit(
        f"{report_path}: package lock digest mismatch"
    )


def main():
    command = sys.argv[1]
    if command == "environment":
        environment(sys.argv[2], sys.argv[3], sys.argv[4])
    elif command == "packages":
        package_manifest(sys.argv[2], sys.argv[3])
    elif command == "preflight":
        preflight(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
