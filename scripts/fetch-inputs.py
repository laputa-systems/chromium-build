#!/usr/bin/env python3
"""Fetch and verify the locked source inputs."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPMessage
import json
import os
import tempfile
from typing import IO
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.request import Request

from script_support import fail_main, require_file, require_network


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request | None:
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise RuntimeError(f"redirected to a non-HTTPS URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def locked_inputs(lock):
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
    ]


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
    destination = inputs_root / item["filename"]
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
            with opener.open(item["url"]) as response, temporary_path.open("wb") as output:
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


def main():
    parser = argparse.ArgumentParser()
    root = Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))
    work = Path(os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    parser.add_argument("--lock", type=Path, default=root / "config/inputs.lock")
    parser.add_argument("--inputs-root", type=Path, default=work / "inputs")
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path(os.environ.get("CHROMIUM_METADATA_ROOT", work / "metadata")),
    )
    args = parser.parse_args()

    require_network("fetch", "Fetch")
    require_file(args.lock, f"missing {args.lock}")

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    args.inputs_root.mkdir(parents=True, exist_ok=True)
    items = locked_inputs(lock)
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as executor:
        entries = list(executor.map(fetch_one, items, [args.inputs_root] * len(items)))

    report = {
        "schema": 1,
        "status": "complete",
        "network": "fetch-only",
        "chromium_version": lock["chromium"]["version"],
        "entries": entries,
    }
    args.metadata.mkdir(parents=True, exist_ok=True)
    write_atomic(args.metadata / "fetch-inputs.json", json.dumps(report, indent=2) + "\n")
    write_atomic(args.metadata / "fetch-complete.stamp", "status=complete\nnetwork=fetch-only\n")
    print(f"Fetched and verified {len(entries)} locked inputs")


if __name__ == "__main__":
    raise SystemExit(fail_main("Fetch", main))
