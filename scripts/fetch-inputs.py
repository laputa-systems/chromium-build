#!/usr/bin/env python3
"""Fetch and verify the locked source inputs."""

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPMessage
from pathlib import Path
from typing import IO
from urllib.request import Request

from script_support import ScriptFailure, fail_main, require_file, require_network


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request | None:
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise RuntimeError(f"redirected to a non-HTTPS URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def architecture_inputs(lock, arch):
    if "architecture_inputs" not in lock:
        return {}
    if arch not in lock["architecture_inputs"]:
        raise RuntimeError(f"input lock has no architecture inputs for {arch!r}")
    return lock["architecture_inputs"][arch]


def locked_inputs(lock, arch):
    chromium = dict(lock["chromium"])
    chromium.update({
        "name": "chromium",
        "filename": chromium["archive_filename"],
        "url": chromium["archive_url"],
        "size": chromium["archive_size"],
        "sha512": chromium["archive_sha512"],
    })
    return [chromium] + [
        dict(value, name=name)
        for name, value in lock["source_inputs"].items()
    ] + [
        dict(value, name=name)
        for name, value in lock.get("test_inputs", {}).items()
    ] + [
        dict(value, name=name)
        for name, value in lock.get("adblock_inputs", {}).items()
    ] + [
        dict(value, name=name)
        for name, value in architecture_inputs(lock, arch).items()
    ]


def lock_identity(lock, lock_path):
    product = lock.get("product", {})
    return {
        "product": product.get("name", "chromium"),
        "chromium_version": lock["chromium"]["version"],
        "product_version": product.get("version"),
        "architecture": lock.get("architecture"),
        "inputs_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    }


def verify(path, item):
    digest = hashlib.sha512()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    if size != item["size"]:
        raise RuntimeError(f'{item["name"]}: expected {item["size"]} bytes, got {size}')
    if digest.hexdigest() != item["sha512"]:
        raise RuntimeError(f'{item["name"]}: SHA-512 mismatch')


def write_atomic(path, text):
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def fetch_one(item, inputs_root):
    filename = item["filename"]
    if not filename or Path(filename).name != filename:
        raise RuntimeError(f'{item["name"]}: unsafe input filename {filename!r}')
    destination = inputs_root / filename
    if destination.is_file():
        verify(destination, item)
        final_url = item["url"]
        action = "reused"
    else:
        with tempfile.NamedTemporaryFile(
            dir=inputs_root, prefix=f".{item['name']}.", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            opener = urllib.request.build_opener(HTTPSRedirectHandler())
            request = Request(item["url"], headers={"User-Agent": "curl/8.0"})
            with opener.open(request) as response, temporary_path.open("wb") as output:
                final_url = response.geturl()
                if urllib.parse.urlparse(final_url).scheme != "https":
                    raise RuntimeError(f'{item["name"]}: final URL is not HTTPS')
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            verify(temporary_path, item)
            temporary_path.replace(destination)
            action = "downloaded"
        finally:
            temporary_path.unlink(missing_ok=True)
    # The browser acceptance phase drops root privileges before reading the
    # verified extension archive from this same input directory.
    destination.chmod(0o644)
    return {
        "name": item["name"],
        "filename": item["filename"],
        "path": str(destination),
        "url": item["url"],
        "final_url": final_url,
        "size": item["size"],
        "sha512": item["sha512"],
        "action": action,
    }


def stage_builder_apks(lock, lock_path, architecture, cache_root):
    archives = lock.get("archives")
    release = lock.get("base_release")
    resolved = lock.get("resolved")
    if (lock.get("status") != "resolved" or lock.get("architecture") != architecture
            or not isinstance(release, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", release)
            or not isinstance(resolved, list) or not resolved
            or not isinstance(archives, dict) or not archives):
        raise ScriptFailure(f"{lock_path}: no resolved {architecture} builder APK archives")

    series = release.rsplit(".", 1)[0]
    apk_arch = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    repositories = tuple(f"https://dl-cdn.alpinelinux.org/alpine/v{series}/{section}" for section in ("main", "community"))
    items = []
    for name, archive in sorted(archives.items()):
        filename = f"{name}.apk"
        if (not re.fullmatch(r"[A-Za-z0-9+._-]+", name) or name not in resolved
                or not isinstance(archive, dict) or archive.get("url") not in {
                    f"{repository}/{apk_arch}/{filename}" for repository in repositories
                }):
            raise ScriptFailure(f"{lock_path}: invalid builder APK archive {name!r}")
        digest = archive.get("sha512")
        size = archive.get("size")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{128}", digest) or type(size) is not int or size <= 0:
            raise ScriptFailure(f"{lock_path}: invalid digest or size for {name}")
        items.append(dict(archive, name=name, filename=filename))

    identity = json.dumps(archives, sort_keys=True, separators=(",", ":"))
    destination = cache_root / hashlib.sha256(identity.encode()).hexdigest()
    destination.mkdir(parents=True, exist_ok=True)
    allowed = {item["filename"] for item in items}
    for archive in destination.glob("*.apk"):
        if archive.name not in allowed or archive.is_symlink() or not archive.is_file():
            raise ScriptFailure(f"unexpected builder APK in {destination}: {archive.name}")
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(items))) as executor:
            list(executor.map(fetch_one, items, [destination] * len(items)))
    except (OSError, RuntimeError) as error:
        raise ScriptFailure(f"{lock_path}: could not verify builder APKs: {error}") from error
    return destination


def main():
    parser = argparse.ArgumentParser()
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    product = os.environ.get("CHROMIUM_PRODUCT", "chromium")
    lock_default = {
        "chromium": root / "config/inputs.lock",
        "helium": root / "config/helium.inputs.lock",
    }.get(product)
    if lock_default is None:
        raise SystemExit(f"unknown product {product}")
    parser.add_argument("--lock", type=Path, default=Path(os.environ.get("INPUTS_LOCK", lock_default)))
    parser.add_argument("--inputs-root", type=Path, default=work / "inputs")
    parser.add_argument("--builder-apks", choices=("amd64", "arm64"))
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata")),
    )
    args = parser.parse_args()

    require_network("fetch", "Fetch")
    require_file(args.lock, f"missing {args.lock}")

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if args.builder_apks:
        print(stage_builder_apks(lock, args.lock, args.builder_apks, args.inputs_root))
        return
    identity = lock_identity(lock, args.lock)
    if identity["product"] != product:
        raise SystemExit(f"{args.lock} belongs to product {identity['product']}, not {product}")
    args.inputs_root.mkdir(parents=True, exist_ok=True)
    items = locked_inputs(lock, os.environ.get("CHROMIUM_ARCH", ""))
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as executor:
        entries = list(executor.map(fetch_one, items, [args.inputs_root] * len(items)))

    report = {
        "schema": 1,
        "status": "complete",
        "network": "fetch-only",
        "chromium_version": lock["chromium"]["version"],
        **identity,
        "entries": entries,
    }
    args.metadata.mkdir(parents=True, exist_ok=True)
    write_atomic(args.metadata / "fetch-inputs.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_atomic(
        args.metadata / "fetch-complete.stamp",
        f"status=complete\nnetwork=fetch-only\nproduct={product}\n"
        f"inputs_lock_sha256={identity['inputs_lock_sha256']}\n",
    )
    print(f"Fetched and verified {len(entries)} locked inputs")


if __name__ == "__main__":
    raise SystemExit(fail_main("Fetch", main))
