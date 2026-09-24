#!/usr/bin/env python3
"""Audit Chrome dry-run commands and generated command descriptions."""

import argparse
import json
import multiprocessing
import os
import shlex
import sys
from pathlib import Path


TOOLCHAIN = "/opt/chromium-llvm"
CCACHE = "/usr/bin/ccache"
COMPILERS = {"clang", "clang++"}
ARCHIVERS = {"ar", "llvm-ar", "ranlib", "llvm-ranlib"}
FORBIDDEN_TEXT = {
    "--sysroot": "sysroot flag",
    "--gcc-toolchain": "GCC toolchain flag",
    "-stdlib=libstdc++": "libstdc++ selection",
    "-fuse-ld=gold": "Gold linker selection",
    "-fuse-ld=mold": "mold linker selection",
    "-rtlib=libgcc": "libgcc runtime selection",
    "-unwindlib=libgcc": "libgcc unwind selection",
    "buildtools/third_party/clang": "in-tree Clang path",
    "third_party/llvm/": "in-tree LLVM path",
    "third_party/llvm-build/": "in-tree LLVM build path",
    "sysroot/": "Chromium sysroot path",
    "/usr/lib/gcc/": "host GCC path",
    "/usr/include/c++/": "host libstdc++ headers",
}


def read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def tokenize(line):
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return line.split()


def tool_tokens(tokens, names):
    return [
        token
        for token in tokens
        if (
            Path(token.split("=", 1)[-1].strip('"')).name in names
            and ("/" in token or token.startswith("-Clinker="))
        )
    ]


def is_compile(tokens):
    return any(flag in tokens for flag in ("-c", "-S", "-E"))


def is_assembler(tokens):
    return any(token.lower().endswith((".s", ".asm")) for token in tokens)


def scan_text(text, label, failures):
    text = text.replace("prebuilt_rustc_sysroot", "")
    for needle, description in FORBIDDEN_TEXT.items():
        if needle in text:
            failures.append(f"{label}: {description}: {needle}")


def audit_command_lines(lines):
    counts = {
        "compile_commands": 0,
        "link_commands": 0,
        "archive_commands": 0,
        "ranlib_commands": 0,
        "assembly_commands": 0,
    }
    ccache_failures = []
    compiler_failures = []
    linker_failures = []
    archive_failures = []
    direct_failures = []

    for number, line in lines:
        if not line.strip():
            continue
        tokens = tokenize(line)
        compilers = tool_tokens(tokens, COMPILERS)
        archives = tool_tokens(tokens, ARCHIVERS)
        if archives:
            if any(Path(token).name in {"ar", "llvm-ar"} for token in archives):
                counts["archive_commands"] += 1
                if any(token != f"{TOOLCHAIN}/bin/llvm-ar" for token in archives):
                    archive_failures.append(f"line {number}: archive tool is not Chromium llvm-ar")
            if any(Path(token).name in {"ranlib", "llvm-ranlib"} for token in archives):
                counts["ranlib_commands"] += 1
                if any(token != f"{TOOLCHAIN}/bin/llvm-ranlib" for token in archives):
                    archive_failures.append(f"line {number}: ranlib tool is not Chromium llvm-ranlib")
        if not compilers:
            continue
        compiler = compilers[0]
        compiler_path = compiler.split("=", 1)[-1].strip('"')
        if compiler.startswith("-Clinker="):
            if compiler_path not in (f"{TOOLCHAIN}/bin/clang", f"{TOOLCHAIN}/bin/clang++"):
                compiler_failures.append(f"line {number}: Rust linker is not absolute Chromium Clang: {compiler}")
            continue
        if compiler_path not in (f"{TOOLCHAIN}/bin/clang", f"{TOOLCHAIN}/bin/clang++"):
            compiler_failures.append(f"line {number}: compiler is not absolute Chromium Clang: {compiler}")
        if is_compile(tokens):
            if is_assembler(tokens):
                counts["assembly_commands"] += 1
            else:
                counts["compile_commands"] += 1
            if not is_assembler(tokens) and CCACHE not in tokens:
                ccache_failures.append(f"line {number}: compile command omits {CCACHE}")
        else:
            counts["link_commands"] += 1
            if CCACHE in tokens:
                linker_failures.append(f"line {number}: link command is wrapped by ccache")
            if not any(
                flag in tokens
                for flag in (
                    "-fuse-ld=lld",
                    f"-fuse-ld={TOOLCHAIN}/bin/ld.lld",
                    f"{TOOLCHAIN}/bin/ld.lld",
                )
            ):
                linker_failures.append(f"line {number}: link command does not select LLD")
        if "third_party/ffmpeg" in line:
            direct_failures.append(f"line {number}: bundled FFmpeg path in a compiler/link command")

    return counts, direct_failures, ccache_failures, compiler_failures, linker_failures, archive_failures


_COMMAND_LINES = []


def configured_jobs(line_count):
    default_jobs = min(8, os.cpu_count() or 1)
    try:
        requested_jobs = int(os.environ.get("GATE_F_JOBS", default_jobs))
    except ValueError:
        requested_jobs = default_jobs
    return max(1, min(requested_jobs, line_count or 1))


def parallel_line_results(lines, direct_worker, range_worker):
    jobs = configured_jobs(len(lines))
    if jobs == 1 or len(lines) < 2 or "fork" not in multiprocessing.get_all_start_methods():
        return [direct_worker(lines)]

    global _COMMAND_LINES
    _COMMAND_LINES = lines
    ranges = []
    for index in range(jobs):
        start = len(lines) * index // jobs
        end = len(lines) * (index + 1) // jobs
        if start < end:
            ranges.append((start, end))
    context = multiprocessing.get_context("fork")
    try:
        with context.Pool(processes=len(ranges)) as pool:
            return pool.map(range_worker, ranges)
    finally:
        _COMMAND_LINES = []


def audit_command_range(bounds):
    start, end = bounds
    return audit_command_lines(_COMMAND_LINES[start:end])


def audit_commands(commands, failures):
    lines = list(enumerate(commands.splitlines(), 1))
    results = parallel_line_results(lines, audit_command_lines, audit_command_range)

    counts = {
        name: 0
        for name in (
            "compile_commands",
            "link_commands",
            "archive_commands",
            "ranlib_commands",
            "assembly_commands",
        )
    }
    direct_failures = []
    ccache_failures = []
    compiler_failures = []
    linker_failures = []
    archive_failures = []
    for result in results:
        result_counts, *result_failures = result
        for name, value in result_counts.items():
            counts[name] += value
        direct, ccache, compiler, linker, archive = result_failures
        direct_failures.extend(direct)
        ccache_failures.extend(ccache)
        compiler_failures.extend(compiler)
        linker_failures.extend(linker)
        archive_failures.extend(archive)

    failures.extend(direct_failures)
    failures.extend(ccache_failures)
    failures.extend(compiler_failures)
    failures.extend(linker_failures)
    failures.extend(archive_failures)
    return counts


def response_file_candidates(lines):
    candidates = set()
    for _, line in lines:
        for token in tokenize(line):
            if not token.startswith("@") or token == "@":
                continue
            candidate = token[1:]
            if not candidate.startswith("-"):
                candidates.add(candidate)
    return candidates


def response_file_range(bounds):
    start, end = bounds
    return response_file_candidates(_COMMAND_LINES[start:end])


def response_files(out, command_text):
    lines = list(enumerate(command_text.splitlines(), 1))
    candidates = set()
    for result in parallel_line_results(lines, response_file_candidates, response_file_range):
        candidates.update(result)
    paths = set()
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            path = out / path
        if path.is_file():
            paths.add(path)
    return sorted(paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commands", type=Path, required=True)
    parser.add_argument("--dry-run", type=Path, required=True)
    parser.add_argument("--dry-run-mode", default="passed")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    command_text = read_text(args.commands)
    dry_run_text = read_text(args.dry_run)
    failures = []
    scan_text(command_text, str(args.commands), failures)
    if args.dry_run_mode == "passed":
        scan_text(dry_run_text, str(args.dry_run), failures)

    counts = audit_commands(command_text, failures)
    if not command_text.strip():
        failures.append("command list is empty")
    if args.dry_run_mode not in {"passed", "no-work"}:
        failures.append(f"unknown Ninja dry-run mode: {args.dry_run_mode}")
    if not dry_run_text.strip():
        failures.append("Ninja dry-run output is empty")
    if args.dry_run_mode == "no-work" and "ninja: no work to do" not in dry_run_text:
        failures.append("Ninja dry-run was marked no-work without Ninja's no-work result")
    if counts["compile_commands"] == 0:
        failures.append("no C/C++ compile commands found")
    if counts["link_commands"] == 0:
        failures.append("no link commands found")
    if counts["archive_commands"] == 0:
        failures.append("no archive commands found")
    if "media/ffmpeg" not in command_text and "ffmpeg_stub" not in command_text:
        failures.append("system FFmpeg shim commands were not found")

    response_report = []
    for path in response_files(args.out, command_text):
        content = read_text(path)
        scan_text(content, str(path), failures)
        response_report.append({"path": str(path), "bytes": path.stat().st_size})

    failures[:] = list(dict.fromkeys(failures))

    report = {
        "schema": 1,
        "status": "failed" if failures else "complete",
        "network": "none",
        "scope": "chrome-dry-run",
        "dry_run_mode": args.dry_run_mode,
        "complete_build_reaudit_required": True,
        "commands": counts,
        "response_files": response_report,
        "checks": {
            "ccache_compile_wrapper": not any("ccache" in item for item in failures),
            "absolute_chromium_compilers": not any("compiler is not" in item for item in failures),
            "direct_chromium_archives_and_ranlib": not any("archive tool" in item or "ranlib tool" in item for item in failures),
            "system_ffmpeg_shim": "media/ffmpeg" in command_text or "ffmpeg_stub" in command_text,
            "no_bundled_ffmpeg": not any(
                "bundled FFmpeg path in a compiler/link command" in item for item in failures
            ),
            "no_forbidden_flags_or_paths": not failures,
        },
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failures:
        for failure in failures[:40]:
            print(f"Gate F: {failure}", file=sys.stderr)
        if len(failures) > 40:
            print(f"Gate F: {len(failures) - 40} additional failures are recorded in the report", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
