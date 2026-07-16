#!/usr/bin/env python3
"""Render the locked GN profile and record only proven optional arguments."""

import argparse
import json
import os
import re
from pathlib import Path


OPTIONAL_ARGS = ("devtools_skip_typecheck", "devtools_bundle")


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def declarations(source_root, name):
    pattern = re.compile(rf"\b{re.escape(name)}\s*=")
    results = []
    for root, directories, files in os.walk(source_root):
        directories[:] = [item for item in directories if item not in (".git", "out")]
        for filename in files:
            if not filename.endswith((".gn", ".gni")):
                continue
            path = Path(root) / filename
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in pattern.finditer(text):
                prefix = text[max(0, match.start() - 4096):match.start()]
                declaration = prefix.rfind("declare_args")
                closing = prefix.rfind("}")
                if declaration < 0 or closing > declaration:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                results.append({"file": str(path), "line": line})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--target-cpu", required=True)
    parser.add_argument("--clang-major", required=True)
    parser.add_argument("--args-output", type=Path, required=True)
    parser.add_argument("--probe-output", type=Path, required=True)
    args = parser.parse_args()

    profile = args.profile.read_text(encoding="utf-8")
    rendered = profile.replace("@TARGET_CPU@", args.target_cpu).replace("@CLANG_MAJOR@", args.clang_major)
    probe = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "source_root": str(args.source_root),
        "arguments": {},
    }
    optional_lines = []
    for name in OPTIONAL_ARGS:
        locations = declarations(args.source_root, name)
        probe["arguments"][name] = {
            "supported": bool(locations),
            "locations": locations,
        }
        if locations:
            optional_lines.append(f"{name} = false")
    if optional_lines:
        rendered = rendered.rstrip() + "\n\n" + "\n".join(optional_lines) + "\n"

    write_atomic(args.args_output, rendered)
    write_atomic(args.probe_output, json.dumps(probe, indent=2) + "\n")


if __name__ == "__main__":
    main()
