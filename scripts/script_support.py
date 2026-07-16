"""Shared stdlib-only helpers for the build entrypoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


class ScriptFailure(RuntimeError):
    """An expected entrypoint failure with a user-facing message."""


def value(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def required_path(path: Path, message: str) -> Path:
    if not path.exists():
        raise ScriptFailure(message)
    return path


def require_file(path: Path, message: str | None = None) -> Path:
    if not path.is_file():
        raise ScriptFailure(message or f"missing {path}")
    return path


def require_directory(path: Path, message: str | None = None) -> Path:
    if not path.is_dir():
        raise ScriptFailure(message or f"missing {path}")
    return path


def require_network(expected: str, label: str) -> None:
    actual = os.environ.get("NETWORK_MODE", "none")
    if actual != expected:
        raise ScriptFailure(f"{label} requires NETWORK_MODE={expected}, got {actual}")


def command_path(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ScriptFailure(f"{name} is unavailable")
    return path


def run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    text: bool = True,
    timeout: float | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=capture_output,
            text=text,
            errors="replace" if text else None,
            timeout=timeout,
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScriptFailure(f"command failed: {' '.join(command)}: {error}") from error
    if check and result.returncode:
        detail = result.stderr.strip() if capture_output and result.stderr else ""
        suffix = f": {detail}" if detail else ""
        raise ScriptFailure(f"command failed ({result.returncode}): {' '.join(command)}{suffix}")
    return result


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def atomic_json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def stream_command(command: list[str], log_path: Path, timeout: float | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            deadline = None if timeout is None else time.monotonic() + timeout
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="")
                log.write(line)
                log.flush()
                if deadline is not None and time.monotonic() > deadline:
                    process.kill()
                    process.wait()
                    raise ScriptFailure(f"command timed out: {' '.join(command)}")
            returncode = process.wait()
    except OSError as error:
        raise ScriptFailure(f"command failed: {' '.join(command)}: {error}") from error
    return returncode


def fail_main(prefix: str, function) -> int:
    try:
        function()
    except ScriptFailure as error:
        print(f"{prefix}: {error}", file=sys.stderr)
        return 1
    return 0
