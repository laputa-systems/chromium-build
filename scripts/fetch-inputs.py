#!/usr/bin/env python3
"""Fetch and verify the locked source inputs."""

import argparse
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise RuntimeError(f"redirected to a non-HTTPS URL: {newurl}")
        return super().redirect_request(request, response, code, msg, headers, newurl)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--inputs-root", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    args.inputs_root.mkdir(parents=True, exist_ok=True)
    entries = []
    opener = urllib.request.build_opener(HTTPSRedirectHandler())
    for item in locked_inputs(lock):
        destination = args.inputs_root / item["filename"]
        if destination.is_file():
            verify(destination, item)
            final_url = item["url"]
            action = "reused"
        else:
            with tempfile.NamedTemporaryFile(
                dir=args.inputs_root, prefix=f".{item['name']}.", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            try:
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
        entries.append({
            "name": item["name"],
            "filename": item["filename"],
            "path": str(destination),
            "url": item["url"],
            "final_url": final_url,
            "size": item["size"],
            "sha512": item["sha512"],
            "action": action,
        })

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
    main()
