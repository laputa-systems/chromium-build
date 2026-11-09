#!/usr/bin/env python3
"""Run the Helium fake-audio persona checks for the arm64 FakeAudioManager."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from browser_test_support import CdpConnection, WebSocket, terminate_process
from helium_wayland import HeadlessSway


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def wait_cdp(port: int, process: subprocess.Popen, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Helium exited before DevTools started with {process.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("Helium DevTools endpoint did not start")


def navigate(connection: CdpConnection, url: str) -> None:
    connection.call("Page.enable")
    connection.call("Runtime.enable")
    navigation = connection.call("Page.navigate", {"url": url})
    frame_id = navigation.get("frameId")
    if not frame_id:
        raise RuntimeError("navigation returned no frame ID")
    connection.wait_event(
        "Page.frameNavigated",
        predicate=lambda event: event.get("params", {}).get("frame", {}).get("id") == frame_id,
        timeout=30,
    )
    connection.wait_event("Page.domContentEventFired", timeout=30)


def evaluate_await(connection: CdpConnection, expression: str):
    result = connection.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    if "exceptionDetails" in result:
        raise RuntimeError(f"fixture evaluation failed: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def evaluate(connection: CdpConnection, expression: str):
    result = connection.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    if "exceptionDetails" in result:
        raise RuntimeError(f"fixture evaluation failed: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "test-audio.json"
    profile = Path(tempfile.mkdtemp(prefix="helium-audio-profile-", dir=output))
    report: dict = {
        "schema": 1,
        "status": "running",
        "persona": args.persona,
        "expected": args.expected,
    }
    write_json(report_path, report)
    fixture = None
    process = None
    connection = None
    started = time.monotonic()
    try:
        fixture = HeadlessSway(output, "sway", "swaymsg", "grim", "wl-copy", "wl-paste")
        fixture.prepare()
        fixture.start()
        assert fixture.wayland_socket is not None
        port_socket = socket.socket()
        port_socket.bind(("127.0.0.1", 0))
        debug_port = port_socket.getsockname()[1]
        port_socket.close()
        environment = fixture.environment()
        environment.update(
            WAYLAND_DISPLAY=fixture.wayland_socket.name,
            LIBGL_ALWAYS_SOFTWARE="1",
            GALLIUM_DRIVER="llvmpipe",
        )
        library_dir = args.helium.parent / "lib"
        if library_dir.is_dir():
            environment["LD_LIBRARY_PATH"] = str(library_dir) + (
                f":{environment['LD_LIBRARY_PATH']}" if environment.get("LD_LIBRARY_PATH") else ""
            )
        command = [
            str(args.helium),
            "--ozone-platform=wayland",
            f"--user-data-dir={profile}",
            "--window-size=1280,900",
            "--no-first-run",
            "--no-default-browser-check",
            "--enable-logging=stderr",
            "--use-gl=angle",
            "--use-angle=gl",
            f"--helium-fake-audio-persona={args.persona}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={debug_port}",
            "about:blank",
        ]
        log_path = output / "test-audio-helium.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True
            )
        wait_cdp(debug_port, process, 30)
        server = urllib.request.Request(
            f"http://127.0.0.1:{debug_port}/json/new?{urllib.parse.quote(args.fixture.as_uri(), safe=':/?=&')}",
            method="PUT",
        )
        with urllib.request.urlopen(server, timeout=10) as response:
            target = json.load(response)
        connection = CdpConnection(WebSocket(target["webSocketDebuggerUrl"]))
        navigate(connection, args.fixture.as_uri())
        if evaluate(connection, "document.querySelector('#status').textContent") != "ready":
            raise RuntimeError("fixture DOM was not updated")
        result = evaluate_await(connection, "window.audioTest()")
        report["result"] = result
        expected = args.expected
        errors = []
        if result.get("audioContextState") != "running":
            errors.append(f"AudioContext state is {result.get('audioContextState')!r}")
        if result.get("sampleRate") != expected["sample_rate"]:
            errors.append(f"sampleRate {result.get('sampleRate')} != {expected['sample_rate']}")
        if result.get("maxChannelCount") != expected["max_channel_count"]:
            errors.append(f"maxChannelCount {result.get('maxChannelCount')} != {expected['max_channel_count']}")
        expected_base = expected["frames_per_buffer"] / expected["sample_rate"]
        base = result.get("baseLatency")
        if base is None or abs(base - expected_base) > 1e-6:
            errors.append(f"baseLatency {base!r} != {expected_base}")
        if result.get("audioOutputCount") != expected["output_devices"]:
            errors.append(
                f"audiooutput count {result.get('audioOutputCount')} != {expected['output_devices']}"
            )
        if result.get("audioInputCount") != expected["input_devices"]:
            errors.append(
                f"audioinput count {result.get('audioInputCount')} != {expected['input_devices']}"
            )
        if result.get("hasFakeMarker"):
            errors.append("a device id/group/label contains fake")
        if expected["input_devices"] > 0 and result.get("getUserMediaError"):
            errors.append(f"getUserMedia failed: {result.get('getUserMediaError')}")
        if not result.get("videoEnded"):
            errors.append("video did not end")
        if not result.get("videoTimeAdvanced"):
            errors.append("video timeupdate did not advance")
        if errors:
            raise RuntimeError("; ".join(errors))
        report["status"] = "complete"
        connection.call("Browser.close")
        connection.close()
        connection = None
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        if connection is not None:
            connection.close()
        if process is not None:
            terminate_process(process)
        if fixture is not None:
            fixture.stop()
        report["duration_seconds"] = time.monotonic() - started
        write_json(report_path, report)
    return 0


def parse_persona(value: str) -> dict:
    parsed: dict = {}
    for part in value.split(","):
        key, _, field = part.partition("=")
        parsed[key.strip()] = int(field.strip())
    for key in ("sample_rate", "frames_per_buffer", "max_channel_count", "output_devices", "input_devices"):
        if key not in parsed:
            raise ValueError(f"persona is missing {key}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Helium fake-audio persona checks")
    parser.add_argument("--helium", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--persona",
        default="sample_rate=48000,frames_per_buffer=2048,max_channel_count=2,output_devices=1,input_devices=1",
    )
    args = parser.parse_args()
    args.fixture = args.fixtures / "helium-audio.html"
    if not args.fixture.is_file():
        parser.error(f"missing fixture {args.fixture}")
    if os.geteuid() == 0:
        parser.error("test-audio refuses root; run the browser as the build/test user")
    try:
        args.expected = parse_persona(args.persona)
    except ValueError as error:
        parser.error(str(error))
    try:
        return run(args)
    except Exception as error:  # noqa: BLE001
        print(f"helium-test-audio: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
