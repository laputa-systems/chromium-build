#!/usr/bin/env python3
"""Run the small offline browser loop using only the Python standard library."""

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path


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


class WebSocket:
    def __init__(self, url):
        parsed = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=10)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = parsed.path or "/"
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        response = self._read_http_headers()
        if b" 101 " not in response:
            raise RuntimeError(f"WebSocket handshake failed: {response!r}")
        self.buffer = b""

    def _read_http_headers(self):
        while b"\r\n\r\n" not in self.buffer:
            self.buffer += self.sock.recv(4096)
        headers, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        return headers

    def send(self, value):
        payload = json.dumps(value, separators=(",", ":")).encode()
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = bytes((0x81, 0x80 | length))
        elif length < 65536:
            header = bytes((0x81, 0xFE)) + length.to_bytes(2, "big")
        else:
            header = bytes((0x81, 0xFF)) + length.to_bytes(8, "big")
        self.sock.sendall(header + mask + masked)

    def receive(self):
        while len(self.buffer) < 2:
            self.buffer += self.sock.recv(4096)
        first, second = self.buffer[:2]
        self.buffer = self.buffer[2:]
        length = second & 0x7F
        if length == 126:
            while len(self.buffer) < 2:
                self.buffer += self.sock.recv(4096)
            length = int.from_bytes(self.buffer[:2], "big")
            self.buffer = self.buffer[2:]
        elif length == 127:
            while len(self.buffer) < 8:
                self.buffer += self.sock.recv(4096)
            length = int.from_bytes(self.buffer[:8], "big")
            self.buffer = self.buffer[8:]
        mask = b""
        if second & 0x80:
            while len(self.buffer) < 4:
                self.buffer += self.sock.recv(4096)
            mask, self.buffer = self.buffer[:4], self.buffer[4:]
        while len(self.buffer) < length:
            self.buffer += self.sock.recv(4096)
        payload, self.buffer = self.buffer[:length], self.buffer[length:]
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if first & 0x0F == 8:
            raise RuntimeError("Chrome closed the CDP socket")
        return json.loads(payload)

    def close(self):
        self.sock.close()


def cdp_call(ws, method, params=None, identifier=[0]):
    identifier[0] += 1
    current = identifier[0]
    ws.send({"id": current, "method": method, "params": params or {}})
    while True:
        message = ws.receive()
        if message.get("id") == current:
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            return message.get("result", {})


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
    process = None
    ws = None
    started = time.monotonic()
    try:
        with FixtureServer(args.fixtures) as server:
            port = socket.socket()
            port.bind(("127.0.0.1", 0))
            debug_port = port.getsockname()[1]
            port.close()
            process = subprocess.Popen([
                str(args.chrome), "--headless=new", "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={debug_port}", f"--user-data-dir={user_data}",
                "--no-first-run", "--no-default-browser-check", "about:blank",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"Chrome DevTools endpoint did not start: {stderr[-2000:]}")
            new_target = urllib.request.Request(
                f"http://127.0.0.1:{debug_port}/json/new?{urllib.parse.quote(server.url, safe=':/?=&')}",
                method="PUT",
            )
            with urllib.request.urlopen(new_target) as response:
                target = json.load(response)
            ws = WebSocket(target["webSocketDebuggerUrl"])
            cdp_call(ws, "Runtime.enable")
            cdp_call(ws, "Page.enable")
            cdp_call(ws, "Network.enable")
            cdp_call(ws, "Page.navigate", {"url": server.url})
            result = cdp_call(ws, "Runtime.evaluate", {"expression": "document.querySelector('#status').textContent", "returnByValue": True})
            if result["result"]["value"] != "ready":
                raise RuntimeError("fixture DOM was not updated")
            screenshot = cdp_call(ws, "Page.captureScreenshot", {"format": "png"})["data"]
            screenshot_path = args.output / "fast-screenshot.png"
            screenshot_path.write_bytes(base64.b64decode(screenshot))
            if screenshot_path.stat().st_size < 100 or screenshot_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError("Chrome returned an invalid screenshot")
            cdp_call(ws, "Browser.close")
        process.wait(timeout=10)
        report = {"schema": 1, "status": "complete", "network": "loopback-only", "duration_seconds": time.monotonic() - started, "version": version, "screenshot": str(screenshot_path)}
        (args.output / "test-fast.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        if ws:
            ws.close()
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        shutil.rmtree(user_data, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"test-fast: {error}", file=sys.stderr)
        raise
