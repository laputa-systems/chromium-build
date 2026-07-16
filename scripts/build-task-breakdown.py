#!/usr/bin/env python3
"""Report Ninja build commands grouped by their output subsystem."""

import argparse
import json
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path


def command_tokens(command):
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def path_category(path):
    path = path.lstrip("./")
    parts = path.split("/")
    if parts and parts[0] in {"obj", "gen"}:
        parts = parts[1:]
    if not parts:
        return "other"
    if parts[0] == "third_party" and len(parts) > 1:
        return f"third_party/{parts[1]}"
    return parts[0]


def output_path(tokens):
    for index, token in enumerate(tokens[:-1]):
        if token in {"-o", "--output"}:
            return tokens[index + 1]
    for token in tokens:
        if token.startswith(("obj/", "gen/")):
            return token
    return ""


def source_path(tokens):
    for token in tokens:
        marker = "../../src/"
        if marker in token:
            return token[token.index(marker) + len(marker) :]
    return ""


def label_path(tokens):
    for token in tokens:
        if token.startswith("//"):
            return token[2:].split(":", 1)[0]
    return ""


def classify(command):
    tokens = command_tokens(command)
    output = output_path(tokens)
    if output:
        return path_category(output)
    source = source_path(tokens)
    if source:
        return path_category(source)
    label = label_path(tokens)
    if label:
        return path_category(label)
    return "other"


def ninja_commands(ninja, out, target, mode):
    if mode == "all":
        command = [ninja, "-C", str(out), "-t", "commands", target]
    else:
        command = [ninja, "-C", str(out), "-n", target]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("ninja:")
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target", default="chrome")
    parser.add_argument("--ninja", default="ninja")
    parser.add_argument("--mode", choices=("pending", "all"), default="pending")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")

    try:
        commands = ninja_commands(args.ninja, args.out, args.target, args.mode)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"build-task-breakdown: could not query Ninja: {error}", file=sys.stderr)
        return 1

    counts = Counter(classify(command) for command in commands)
    report = {
        "schema": 1,
        "mode": args.mode,
        "target": args.target,
        "total_tasks": len(commands),
        "subsystems": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print(f"Ninja task breakdown: target={args.target} mode={args.mode}")
    print(f"Total tasks: {len(commands)}")
    for subsystem, count in list(report["subsystems"].items())[: args.limit]:
        percentage = count * 100 / len(commands) if commands else 0
        print(f"{count:6d} {percentage:5.1f}% {subsystem}")
    if len(counts) > args.limit:
        print(f"... {len(counts) - args.limit} smaller subsystems omitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
