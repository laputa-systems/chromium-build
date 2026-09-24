#!/usr/bin/env python3
"""Stage the minimal headless runtime files from a completed Chrome output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def audit_browser_elf(chrome: Path) -> dict[str, object]:
    arch = os.environ.get("CHROMIUM_ARCH", "amd64")
    interpreters = {
        "amd64": "/lib/ld-musl-x86_64.so.1",
        "arm64": "/lib/ld-musl-aarch64.so.1",
    }
    if arch not in interpreters:
        raise RuntimeError(f"unsupported browser architecture: {arch}")
    output = subprocess.run(
        ["llvm-readelf", "-l", "-d", str(chrome)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    match = re.search(r"\[Requesting program interpreter: ([^\]]+)\]", output)
    interpreter = match.group(1) if match else None
    if interpreter != interpreters[arch]:
        raise RuntimeError(f"Chrome uses the wrong ELF interpreter: {interpreter}")
    needed = re.findall(r"\(NEEDED\).*?Shared library: \[([^\]]+)\]", output)
    if not needed:
        raise RuntimeError("Chrome has no readable ELF library requirements")
    forbidden = {"libc.so.6", "libstdc++.so.6", "libgcc_s.so.1"}
    leaked = sorted(forbidden.intersection(needed))
    if leaked:
        raise RuntimeError(f"Chrome requires build-only GNU runtime libraries: {leaked}")
    return {"interpreter": interpreter, "needed": needed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.chrome.parent
    required = (
        "chrome",
        "chrome_100_percent.pak",
        "chrome_200_percent.pak",
        "resources.pak",
        "headless_command_resources.pak",
        "locales/en-US.pak",
    )
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise RuntimeError(f"Chrome runtime inputs are missing: {missing}")
    elf = audit_browser_elf(args.chrome)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.output.name}.", dir=args.output.parent))
    try:
        temporary.chmod(0o755)
        for name in required:
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
        for path in temporary.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o755 if path.name == "chrome" else 0o644)
        manifest = {
            "schema": 1,
            "status": "complete",
            "chrome": "chrome",
            "source": str(args.chrome),
            "elf": elf,
            "files": [
                {"path": name, "size": (temporary / name).stat().st_size, "sha256": digest(temporary / name)}
                for name in required
            ],
        }
        (temporary / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if args.output.exists():
            shutil.rmtree(args.output)
        os.replace(temporary, args.output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(f"stage-runtime: staged {len(required)} files at {args.output}")


if __name__ == "__main__":
    main()
