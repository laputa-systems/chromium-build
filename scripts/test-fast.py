#!/usr/bin/env python3
"""Run the small offline browser loop using only the Python standard library."""

import argparse
import base64
import hashlib
import http.server
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from browser_test_support import CdpConnection, WebSocket, chromium_args, launch_chromium, terminate_process


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def valid_png(value, expected_dimensions):
    data = base64.b64decode(value)
    if len(data) < 33 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False, data
    length = struct.unpack(">I", data[8:12])[0]
    dimensions = struct.unpack(">II", data[16:24]) if data[12:16] == b"IHDR" and length == 13 else None
    return dimensions == expected_dimensions, data


class FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


class FixtureServer:
    def __init__(self, root):
        handler = lambda *args, **kwargs: FixtureHandler(*args, directory=str(root), **kwargs)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/fast.html"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit("test-fast refuses root; run the browser as the build/test user")
    args.output.mkdir(parents=True, exist_ok=True)
    user_data = Path(tempfile.mkdtemp(prefix="fast-profile-", dir=args.output))
    report_path = args.output / "test-fast.json"
    report = {
        "schema": 2,
        "status": "running",
        "network": "loopback-only",
        "chrome": str(args.chrome),
        "platform": sys.platform,
    }
    write_json(report_path, report)
    process = None
    connection = None
    run_error = None
    result = None
    cleanup = None
    started = time.monotonic()
    try:
        with FixtureServer(args.fixtures) as server:
            port = socket.socket()
            port.bind(("127.0.0.1", 0))
            debug_port = port.getsockname()[1]
            port.close()
            log_path = args.output / "test-fast-chromium.log"
            process, _ = launch_chromium(
                args.chrome,
                user_data,
                chromium_args(user_data, port=debug_port),
                log_path,
            )
            version = None
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json/version", timeout=1) as response:
                        version = json.load(response)
                    break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
            if not version:
                log = log_path.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(f"Chrome DevTools endpoint did not start: {log[-2000:]}")
            new_target = urllib.request.Request(
                f"http://127.0.0.1:{debug_port}/json/new?{urllib.parse.quote(server.url, safe=':/?=&')}",
                method="PUT",
            )
            with urllib.request.urlopen(new_target) as response:
                target = json.load(response)
            connection = CdpConnection(WebSocket(target["webSocketDebuggerUrl"]))
            connection.call("Runtime.enable")
            connection.call("Page.enable")
            connection.call("Network.enable")
            navigation = connection.call("Page.navigate", {"url": server.url})
            frame_id = navigation.get("frameId")
            if not frame_id:
                raise RuntimeError("fast fixture navigation returned no frame ID")
            connection.wait_event(
                "Page.frameNavigated",
                predicate=lambda event: event.get("params", {}).get("frame", {}).get("id") == frame_id,
                timeout=15,
            )
            connection.wait_event("Page.domContentEventFired", timeout=15)
            result = connection.call("Runtime.evaluate", {"expression": "document.querySelector('#status').textContent", "returnByValue": True})
            if result["result"]["value"] != "ready":
                raise RuntimeError("fixture DOM was not updated")
            dimensions = tuple(connection.call("Runtime.evaluate", {"expression": "[window.innerWidth, window.innerHeight]", "returnByValue": True})["result"]["value"])
            screenshot = connection.call("Page.captureScreenshot", {"format": "png"})["data"]
            screenshot_path = args.output / "fast-screenshot.png"
            screenshot_ok, screenshot_bytes = valid_png(screenshot, dimensions)
            screenshot_path.write_bytes(screenshot_bytes)
            if not screenshot_ok:
                raise RuntimeError("Chrome returned an invalid screenshot")
            connection.call("Browser.close")
        process.wait(timeout=10)
        report.update(
            {
                "status": "complete",
                "version": version,
                "screenshot": str(screenshot_path),
                "screenshot_sha256": hashlib.sha256(screenshot_bytes).hexdigest(),
            }
        )
    except BaseException as error:
        run_error = error
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        if connection:
            connection.close()
        if process:
            cleanup = terminate_process(process)
        else:
            cleanup = terminate_process(None)
        report["cleanup"] = cleanup
        report["duration_seconds"] = time.monotonic() - started
        if run_error is None and report["status"] == "complete" and cleanup["ok"]:
            shutil.rmtree(user_data, ignore_errors=True)
        else:
            report["profile"] = str(user_data)
        if not cleanup["ok"] and run_error is None:
            report["status"] = "failed"
            report["error"] = f"Chromium cleanup failed: {cleanup}"
        write_json(report_path, report)
        if not cleanup["ok"] and run_error is None:
            raise RuntimeError(f"Chromium cleanup failed: {cleanup}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"test-fast: {error}", file=sys.stderr)
        raise
