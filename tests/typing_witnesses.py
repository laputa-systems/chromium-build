"""Programs the strict ty profile must reject; `make lint` fails when a rule stops flagging its line."""

import subprocess
from typing import Any


def bare_dict(value: dict) -> None:  # ty: ignore[missing-type-argument]
    del value


def bare_popen(process: subprocess.Popen) -> None:  # ty: ignore[missing-type-argument]
    del process


def bare_completed_process(result: subprocess.CompletedProcess) -> None:  # ty: ignore[missing-type-argument]
    del result


def parametrized(value: dict[str, Any], process: subprocess.Popen[bytes], result: subprocess.CompletedProcess[str]) -> None:
    del value, process, result
