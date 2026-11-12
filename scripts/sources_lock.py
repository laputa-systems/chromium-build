#!/usr/bin/env python3
"""Derive and check sources.lock.json from the per-product input locks."""

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from buildlib import locks

ROOT = Path(__file__).resolve().parents[1]
SOURCES_LOCK = "sources.lock.json"
LOCKS = {
    "linux": "config/inputs.lock",
    "helium": "config/helium.inputs.lock",
    "macos": "config/macos.inputs.lock",
}
RUST_TOOLCHAIN_SCRIPT = "scripts/build-rust-toolchain.sh"
TARGETS = {"amd64": "x86_64-unknown-linux-musl", "arm64": "aarch64-unknown-linux-musl"}
SCOPE_TARGETS = {"linux": "x86_64-unknown-linux-musl", "macos": "aarch64-apple-darwin"}
ROLES = {
    "chromium": "source",
    "ungoogled": "source",
    "source_inputs": "source",
    "test_inputs": "test",
    "adblock_inputs": "dependency",
    "architecture_inputs": "toolchain",
    "runtime_sources": "runtime",
    "toolchain": "toolchain",
}
FIELD_ORDER = ("name", "version", "url", "sha256", "sha512", "size", "role", "target", "lock")
SHA512 = re.compile(r"[0-9a-f]{128}")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read JSON {path}: {error}") from error


def locked_version(node, ancestors):
    if isinstance(node.get("version"), str):
        return node["version"]
    if isinstance(node.get("revision"), str):
        return node["revision"]
    for parent in reversed(ancestors):
        for key in ("version", "release"):
            if isinstance(parent.get(key), str):
                return parent[key]
    raise SystemExit("locked input has no version")


def walk(scope, lock_path, node, path, ancestors, found):
    if "url" in node or "archive_url" in node:
        target = next((TARGETS[part] for part in path if part in TARGETS), SCOPE_TARGETS.get(scope))
        entry = {
            "name": f"{scope}/{'/'.join(path)}",
            "version": locked_version(node, ancestors),
            "url": node.get("url") or node["archive_url"],
            "sha256": node.get("sha256") or node.get("archive_sha256"),
            "sha512": node.get("sha512") or node.get("archive_sha512"),
            "size": node.get("size") or node.get("archive_size"),
            "role": ROLES[path[0]],
            "target": target,
            "lock": lock_path,
        }
        found.append({key: value for key, value in entry.items() if value is not None})
        return
    for key, value in node.items():
        if isinstance(value, dict):
            walk(scope, lock_path, value, [*path, key], [*ancestors, node], found)


def rust_toolchain_script_input(root):
    text = (root / RUST_TOOLCHAIN_SCRIPT).read_text(encoding="utf-8")
    version = re.search(r"(?m)^LLVM_SOURCE_VERSION=(\S+)$", text)
    digest = re.search(r"(?m)^LLVM_SOURCE_SHA256=(\S+)$", text)
    if version is None or digest is None:
        raise SystemExit(f"{RUST_TOOLCHAIN_SCRIPT} does not pin the LLVM source archive")
    release = version.group(1)
    return {
        "name": "toolchain/llvm-project-source",
        "version": release,
        "url": f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{release}/llvm-project-{release}.src.tar.xz",
        "sha256": digest.group(1),
        "role": "toolchain",
        "lock": RUST_TOOLCHAIN_SCRIPT,
    }


def derive(root):
    found = []
    for scope, lock_path in LOCKS.items():
        walk(scope, lock_path, read_json(root / lock_path), [], [], found)
    found.append(rust_toolchain_script_input(root))
    return sorted(found, key=lambda entry: entry["name"])


def download_digests(entry):
    url = entry["url"]
    if urllib.parse.urlparse(url).scheme != "https":
        raise SystemExit(f"{entry['name']}: input is not HTTPS: {url}")
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(request) as response:
        if urllib.parse.urlparse(response.geturl()).scheme != "https":
            raise SystemExit(f"{entry['name']}: redirected to a non-HTTPS URL")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            sha256.update(chunk)
            sha512.update(chunk)
    if "size" in entry and size != entry["size"]:
        raise SystemExit(f"{entry['name']}: expected {entry['size']} bytes, got {size}")
    if "sha512" in entry and sha512.hexdigest() != entry["sha512"]:
        raise SystemExit(f"{entry['name']}: SHA-512 mismatch")
    print(f"hashed {entry['name']} ({size} bytes)", file=sys.stderr)
    return sha256.hexdigest()


def resolve_sha256(entries, previous, fetch):
    known = {entry["name"]: entry for entry in previous}
    missing = []
    for entry in entries:
        if "sha256" in entry:
            continue
        old = known.get(entry["name"])
        if old and all(old.get(key) == entry.get(key) for key in ("url", "size", "sha512")):
            entry["sha256"] = old["sha256"]
        else:
            missing.append(entry)
    if missing and not fetch:
        names = ", ".join(entry["name"] for entry in missing)
        raise SystemExit(f"no SHA-256 for {names}; rerun with --fetch to download and hash them")
    with ThreadPoolExecutor(max_workers=4) as executor:
        for entry, digest in zip(missing, executor.map(download_digests, missing), strict=True):
            entry["sha256"] = digest


def render(entries):
    ordered = [{key: entry[key] for key in FIELD_ORDER if key in entry} for entry in entries]
    return json.dumps({"schema_version": 1, "inputs": ordered}, indent=2) + "\n"


def write(root, fetch):
    path = root / SOURCES_LOCK
    previous = read_json(path)["inputs"] if path.is_file() else []
    entries = derive(root)
    resolve_sha256(entries, previous, fetch)
    path.write_text(render(entries), encoding="utf-8")


def check(root):
    path = root / SOURCES_LOCK
    try:
        inputs = locks.load_sources(path).inputs
    except locks.LockError as error:
        return [str(error.reason)]
    if not inputs:
        return ["inputs must be a non-empty list"]
    problems = []
    names = [entry.name for entry in inputs]
    if names != sorted(names):
        problems.append("inputs are not sorted by name")
    for entry in inputs:
        sha512 = entry.extra.get("sha512")
        if sha512 is not None and not SHA512.fullmatch(sha512):
            problems.append(f"{entry.name}: sha512 is not lowercase hex")
    recorded = {entry["name"]: entry for entry in read_json(path)["inputs"]}
    derived = {entry["name"]: entry for entry in derive(root)}
    for name in sorted(set(derived) - set(recorded)):
        problems.append(f"{name}: pinned by {derived[name]['lock']} but absent from {SOURCES_LOCK}")
    for name in sorted(set(recorded) - set(derived)):
        problems.append(f"{name}: not pinned by any input lock")
    for name in sorted(set(derived) & set(recorded)):
        for key, value in derived[name].items():
            if recorded[name].get(key) != value:
                problems.append(f"{name}: {key} differs from {derived[name]['lock']}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    writer = commands.add_parser("write")
    writer.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.command == "write":
        write(ROOT, args.fetch)
        return 0
    problems = check(ROOT)
    for problem in problems:
        print(f"{SOURCES_LOCK}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
