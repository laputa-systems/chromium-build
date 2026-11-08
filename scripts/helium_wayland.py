#!/usr/bin/env python3
"""Own a headless Sway session for the Helium Wayland fixture."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

READY_TIMEOUT = 15.0
OPERATION_TIMEOUT = 10.0
TERMINATE_TIMEOUT = 5.0
FIXTURE_SIZE = (1280, 900)
SWAY_CONFIG = """\
xwayland disable
output * resolution 1280x900
output * bg #000000 solid_color
"""


class FixtureError(RuntimeError):
    pass


class HeadlessSway:
    def __init__(
        self,
        output: Path,
        sway: str,
        swaymsg: str,
        grim: str,
        wl_copy: str,
        wl_paste: str,
        ready_timeout: float = READY_TIMEOUT,
        operation_timeout: float = OPERATION_TIMEOUT,
        terminate_timeout: float = TERMINATE_TIMEOUT,
    ):
        self.output = output
        self.sway = sway
        self.swaymsg = swaymsg
        self.grim = grim
        self.wl_copy = wl_copy
        self.wl_paste = wl_paste
        self.ready_timeout = ready_timeout
        self.operation_timeout = operation_timeout
        self.terminate_timeout = terminate_timeout
        self.attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=output))
        self.home = self.attempt / "home"
        self.runtime = self.attempt / "runtime"
        self.config = self.attempt / "sway.conf"
        self.log_path = self.attempt / "sway.log"
        self.wayland_socket: Path | None = None
        self.ipc_socket: Path | None = None
        self.sway_process: subprocess.Popen | None = None
        self.clipboard_process: subprocess.Popen | None = None
        self._log = None
        self._cancelled = False

    def environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DISPLAY", "WAYLAND_DISPLAY", "SWAYSOCK"}
        }
        environment.update(
            HOME=str(self.home),
            XDG_RUNTIME_DIR=str(self.runtime),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            XDG_DATA_HOME=str(self.home / ".local/share"),
            WLR_BACKENDS="headless",
            WLR_RENDERER="pixman",
            WLR_LIBINPUT_NO_DEVICES="1",
            WLR_HEADLESS_OUTPUTS="1",
        )
        return environment

    def prepare(self) -> None:
        self.home.mkdir(mode=0o700)
        self.runtime.mkdir(mode=0o700)
        self.config.write_text(SWAY_CONFIG, encoding="utf-8")
        self.config.chmod(0o600)
        sway_path = Path(self.sway)
        try:
            attributes = os.listxattr(sway_path)
        except OSError:
            attributes = []
        if "security.capability" in attributes:
            local_sway = self.attempt / "sway"
            shutil.copyfile(sway_path, local_sway)
            local_sway.chmod(0o755)
            self.sway = str(local_sway)
        owner = None
        if os.geteuid() == 0:
            account = pwd.getpwnam("chromium")
            owner = (account.pw_uid, account.pw_gid)
            for path in (self.output, *self.output.rglob("*"), self.attempt, *self.attempt.rglob("*")):
                try:
                    os.chown(path, *owner)
                except FileNotFoundError:
                    pass
            os.setgroups([])
            os.setgid(owner[1])
            os.setuid(owner[0])
        self._owner = owner
        self.generate_config()

    def generate_config(self) -> None:
        self.config.write_text(SWAY_CONFIG, encoding="utf-8")

    def start(self) -> None:
        self._log = self.log_path.open("wb")
        self.sway_process = subprocess.Popen(
            [self.sway, "--config", str(self.config), "--debug"],
            env=self.environment(),
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if self._cancelled:
                raise FixtureError("fixture cancelled")
            if self.sway_process.poll() is not None:
                raise FixtureError(f"sway exited before readiness with code {self.sway_process.returncode}")
            self.wayland_socket = next(iter(sorted(self.runtime.glob("wayland-*"))), None)
            self.ipc_socket = next(iter(sorted(self.runtime.glob("sway-ipc.*.sock"))), None)
            if self.wayland_socket and self.ipc_socket:
                outputs = self.query_outputs()
                if outputs:
                    return
            time.sleep(0.1)
        raise FixtureError("sway did not become ready with an active output")

    def query_outputs(self) -> list[dict]:
        result = self.run_ipc(["-t", "get_outputs", "-r"])
        try:
            outputs = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise FixtureError("swaymsg returned invalid output JSON") from error
        if not isinstance(outputs, list):
            raise FixtureError("swaymsg output list is not an array")
        return [item for item in outputs if isinstance(item, dict) and item.get("active")]

    def run_ipc(self, arguments: list[str]) -> subprocess.CompletedProcess:
        if self.ipc_socket is None:
            raise FixtureError("sway IPC socket is unavailable")
        return self.run(
            [self.swaymsg, "-s", str(self.ipc_socket), *arguments],
            self.operation_timeout,
        )

    def run(self, command: list[str], timeout: float, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                command,
                env=environment or self.environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise FixtureError(f"timed out after {timeout:g}s: {command[0]}") from error
        if result.returncode:
            raise FixtureError(f"{command[0]} failed with {result.returncode}: {result.stderr.strip()}")
        return result

    def select_output(self) -> dict:
        outputs = self.query_outputs()
        if not outputs:
            raise FixtureError("no active Sway output")
        output = outputs[0]
        name = output.get("name")
        if not isinstance(name, str) or not name:
            raise FixtureError("active Sway output has no name")
        self.run_ipc(["output", name, "resolution", f"{FIXTURE_SIZE[0]}x{FIXTURE_SIZE[1]}"])
        deadline = time.monotonic() + self.operation_timeout
        while time.monotonic() < deadline:
            for candidate in self.query_outputs():
                if candidate.get("name") != name:
                    continue
                width, height = output_size(candidate)
                if (width, height) == FIXTURE_SIZE:
                    return candidate
            time.sleep(0.1)
        raise FixtureError(f"Sway output {name} did not reach {FIXTURE_SIZE[0]}x{FIXTURE_SIZE[1]}")

    def check_clipboard(self) -> str:
        if self.wayland_socket is None:
            raise FixtureError("Wayland socket is unavailable")
        value = f"helium-fixture-{os.getpid()}-{time.time_ns()}"
        environment = self.environment()
        environment["WAYLAND_DISPLAY"] = self.wayland_socket.name
        self.clipboard_process = subprocess.Popen(
            [self.wl_copy, "--foreground", value],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + self.operation_timeout
        while time.monotonic() < deadline:
            if self._cancelled:
                raise FixtureError("fixture cancelled")
            if self.clipboard_process.poll() is not None:
                raise FixtureError("wl-copy exited before the selection was readable")
            result = self.run([self.wl_paste, "--no-newline"], self.operation_timeout, environment)
            if result.stdout == value:
                return value
            time.sleep(0.1)
        raise FixtureError("clipboard round-trip did not observe the copied value")

    def capture(self, output: dict) -> Path:
        if self.wayland_socket is None:
            raise FixtureError("Wayland socket is unavailable")
        name = output["name"]
        path = self.attempt / "frame.png"
        environment = self.environment()
        environment["WAYLAND_DISPLAY"] = self.wayland_socket.name
        self.run([self.grim, "-o", name, str(path)], self.operation_timeout, environment)
        verify_png(path)
        return path

    def stop(self) -> None:
        for process in (self.clipboard_process, self.sway_process):
            if process is not None and process.poll() is None:
                terminate_group(process, self.terminate_timeout)
        for process in (self.clipboard_process, self.sway_process):
            if process is not None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        self.clipboard_process = None
        self.sway_process = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def cancel(self, _signum, _frame) -> None:
        self._cancelled = True


def output_size(output: dict) -> tuple[int, int]:
    mode = output.get("current_mode")
    if isinstance(mode, dict):
        width, height = mode.get("width"), mode.get("height")
    else:
        width, height = output.get("width"), output.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise FixtureError("Sway output has no integer size")
    return width, height


def verify_png(path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise FixtureError("grim did not produce a PNG")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if (width, height) != FIXTURE_SIZE:
        raise FixtureError(f"grim produced {width}x{height}, expected {FIXTURE_SIZE[0]}x{FIXTURE_SIZE[1]}")


def terminate_group(process: subprocess.Popen, timeout: float) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


def executable(name: str, override: str | None = None) -> str:
    value = override or shutil.which(name)
    if not value or not os.access(value, os.X_OK):
        raise FixtureError(f"required fixture executable is missing: {name}")
    return value


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_fixture(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "fixture-report.json"
    report = {
        "schema": 1,
        "status": "failed",
        "backend": "sway-headless",
        "renderer": "pixman",
        "output_size": list(FIXTURE_SIZE),
        "network": "none",
        "observations": {},
    }
    fixture = None
    try:
        fixture = HeadlessSway(
            output,
            executable("sway", args.sway),
            executable("swaymsg", args.swaymsg),
            executable("grim", args.grim),
            executable("wl-copy", args.wl_copy),
            executable("wl-paste", args.wl_paste),
            ready_timeout=args.ready_timeout,
            operation_timeout=args.operation_timeout,
            terminate_timeout=args.terminate_timeout,
        )
        report["attempt"] = str(fixture.attempt)
        signal.signal(signal.SIGTERM, fixture.cancel)
        signal.signal(signal.SIGINT, fixture.cancel)
        fixture.prepare()
        fixture.start()
        output_data = fixture.select_output()
        width, height = output_size(output_data)
        name = output_data["name"]
        clipboard = fixture.check_clipboard()
        screenshot = fixture.capture(output_data)
        report["observations"] = {
            "output": {"name": name, "width": width, "height": height, "active": True},
            "clipboard": {"value": clipboard, "round_trip": True},
            "screenshot": {"path": str(screenshot), "bytes": screenshot.stat().st_size, "width": width, "height": height},
        }
        report["status"] = "complete"
        write_report(report_path, report)
        return 0
    except Exception as error:  # noqa: BLE001
        report["error"] = str(error)
        if fixture is not None:
            report["attempt"] = str(fixture.attempt)
            log = fixture.log_path
            if log.is_file():
                report["log"] = str(log)
                report["log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-4000:]
        write_report(report_path, report)
        print(f"helium-wayland: {error}", file=sys.stderr)
        return 1
    finally:
        if fixture is not None:
            fixture.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-fixture", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sway")
    parser.add_argument("--swaymsg")
    parser.add_argument("--grim")
    parser.add_argument("--wl-copy")
    parser.add_argument("--wl-paste")
    parser.add_argument("--ready-timeout", type=float, default=READY_TIMEOUT)
    parser.add_argument("--operation-timeout", type=float, default=OPERATION_TIMEOUT)
    parser.add_argument("--terminate-timeout", type=float, default=TERMINATE_TIMEOUT)
    args = parser.parse_args()
    if not args.check_fixture:
        parser.error("--check-fixture is required")
    return check_fixture(args)


if __name__ == "__main__":
    sys.exit(main())
