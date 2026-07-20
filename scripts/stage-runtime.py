#!/usr/bin/env python3
"""Stage the minimal headless runtime files from a completed Chrome output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


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
