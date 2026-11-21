#!/usr/bin/env python3
"""Upload every locked builder APK to the alpine-apks-v<series> release.

fetch-inputs.py tries that release after the locked dl-cdn URL, because
Alpine removes a superseded revision from all of its mirrors. Run this after
refreshing a builder package lock, while upstream still serves the archives.
Each archive is verified against its locked size and SHA-512 before upload;
assets already on the release are skipped. Requires an authenticated gh.

Usage: scripts/mirror-builder-apks.py SCRATCH_DIR
"""

import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "laputa-systems/chromium-build"
LOCKS = ("helium.packages.amd64.lock", "helium.packages.arm64.lock", "packages.amd64.lock")


def locked_archives():
    archives = {}
    for name in LOCKS:
        lock = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
        series = lock["base_release"].rsplit(".", 1)[0]
        for package, archive in lock["archives"].items():
            arch = archive["url"].rsplit("/", 2)[1]
            key = (series, f"{arch}-{package}.apk")
            if key in archives and archives[key]["sha512"] != archive["sha512"]:
                raise SystemExit(f"{key[1]}: locks disagree on its digest")
            archives[key] = archive
    return archives


def released(tag):
    found = subprocess.run(["gh", "release", "view", tag, "-R", REPOSITORY, "--json", "assets"],
                           capture_output=True, text=True, check=False)
    if found.returncode != 0:
        subprocess.run(["gh", "release", "create", tag, "-R", REPOSITORY, "--prerelease",
                        "--title", f"Alpine builder APKs ({tag})",
                        "--notes", "Exact APKs pinned by the builder package locks; see docs/linux.md."], check=True)
        return set()
    return {asset["name"] for asset in json.loads(found.stdout)["assets"]}


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    scratch = Path(sys.argv[1])
    scratch.mkdir(parents=True, exist_ok=True)
    by_tag = {}
    for (series, asset), archive in sorted(locked_archives().items()):
        by_tag.setdefault(f"alpine-apks-v{series}", []).append((asset, archive))
    for tag, assets in by_tag.items():
        present = released(tag)
        pending = []
        for asset, archive in assets:
            if asset in present:
                continue
            path = scratch / asset
            if not path.is_file():
                request = urllib.request.Request(archive["url"], headers={"User-Agent": "curl/8.0"})
                with urllib.request.urlopen(request) as response:
                    path.write_bytes(response.read())
            data = path.read_bytes()
            if len(data) != archive["size"] or hashlib.sha512(data).hexdigest() != archive["sha512"]:
                raise SystemExit(f"{asset}: does not match its locked size and SHA-512")
            pending.append(str(path))
        for start in range(0, len(pending), 50):
            subprocess.run(["gh", "release", "upload", tag, "-R", REPOSITORY, *pending[start:start + 50]], check=True)
        print(f"{tag}: {len(assets)} locked, {len(pending)} uploaded")


if __name__ == "__main__":
    main()
