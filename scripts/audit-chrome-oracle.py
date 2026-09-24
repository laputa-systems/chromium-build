#!/usr/bin/env python3
"""Cross-check Gate F counts against Ninja's target-scoped command list."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


COMPILER = re.compile(r"(?:^|[\s\"])/opt/chromium-llvm/bin/clang(?:\+\+)?(?=[\s\"]|$)")
ARCHIVE = re.compile(r"(?:^|[\s\"])/opt/chromium-llvm/bin/llvm-ar(?=[\s\"]|$)")
RANLIB = re.compile(r"(?:^|[\s\"])/opt/chromium-llvm/bin/llvm-ranlib(?=[\s\"]|$)")


def classify(text):
    counts = {
        "compile_commands": 0,
        "link_commands": 0,
        "archive_commands": 0,
        "ranlib_commands": 0,
        "assembly_commands": 0,
    }
    for line in text.splitlines():
        if not line.strip():
            continue
        if ARCHIVE.search(line):
            counts["archive_commands"] += 1
        if RANLIB.search(line):
            counts["ranlib_commands"] += 1
        if not COMPILER.search(line):
            continue
        if re.search(r"-Clinker=[\"]?/opt/chromium-llvm/bin/clang(?:\+\+)?", line):
            continue
        compile_command = bool(re.search(r"(?:^|\s)-(?:c|S|E)(?:\s|$)", line))
        if not compile_command:
            counts["link_commands"] += 1
            continue
        if re.search(r"(?:^|\s)[^\s]+\.(?:s|asm)(?:\s|$)", line, re.IGNORECASE):
            counts["assembly_commands"] += 1
        else:
            counts["compile_commands"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commands", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--ninja-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    command_bytes = args.commands.read_bytes()
    command_text = command_bytes.decode("utf-8", errors="replace")
    oracle_counts = classify(command_text)
    gate_report = json.loads(args.gate_report.read_text(encoding="utf-8"))
    gate_counts = gate_report.get("commands", {})
    mismatches = {
        name: {"gate": gate_counts.get(name), "oracle": value}
        for name, value in oracle_counts.items()
        if gate_counts.get(name) != value
    }
    failures = []
    if gate_report.get("status") != "complete":
        failures.append("Gate F report is not complete")
    if mismatches:
        failures.append("oracle command counts do not match Gate F")
    if not command_text.strip():
        failures.append("Ninja command list is empty")

    report = {
        "schema": 1,
        "status": "failed" if failures else "complete",
        "network": "none",
        "scope": "chrome-target-command-oracle",
        "ninja_version": args.ninja_version,
        "command_list_sha256": hashlib.sha256(command_bytes).hexdigest(),
        "commands": oracle_counts,
        "gate_f_commands": gate_counts,
        "mismatches": mismatches,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failures:
        for failure in failures:
            print(f"Gate F oracle: {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
