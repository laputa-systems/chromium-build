#!/usr/bin/env python3
"""Unit tests for the standard-library CDP transports and process helpers."""

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import browser_test_support as support


def temporary_directory():
    parent = os.environ.get("CHROMIUM_TEST_TMPDIR")
    if parent:
        return tempfile.TemporaryDirectory(dir=parent)
    return tempfile.TemporaryDirectory()


def websocket_frame(opcode, payload, *, final=True, masked=False):
    first = (0x80 if final else 0) | opcode
    length = len(payload)
    if length < 126:
        header = bytes((first, (0x80 if masked else 0) | length))
    elif length < 65536:
        header = bytes((first, (0x80 if masked else 0) | 126)) + length.to_bytes(2, "big")
    else:
        header = bytes((first, (0x80 if masked else 0) | 127)) + length.to_bytes(8, "big")
    if not masked:
        return header + payload
    mask = b"test"
    masked_payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return header + mask + masked_payload


class FakeSocket:
    def __init__(self, incoming=b""):
        self.incoming = incoming
        self.sent = []

    def settimeout(self, _timeout):
        return None

    def sendall(self, value):
        self.sent.append(value)

    def recv(self, count):
        if not self.incoming:
            return b""
        value, self.incoming = self.incoming[:count], self.incoming[count:]
        return value

    def close(self):
        return None


class HandshakeSocket(FakeSocket):
    def __init__(self, valid=True):
        super().__init__()
        self.valid = valid

    def recv(self, count):
        if not self.incoming:
            request = self.sent[0].decode("ascii")
            key = next(line.split(":", 1)[1].strip() for line in request.split("\r\n") if line.lower().startswith("sec-websocket-key:"))
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
            ).decode("ascii")
            if not self.valid:
                accept = "invalid"
            self.incoming = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: keep-alive, Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")
        return super().recv(count)


class FakeTransport:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []
        self.closed = False

    def send(self, value):
        self.sent.append(value)

    def receive(self):
        if not self.incoming:
            raise TimeoutError("empty fake transport")
        return self.incoming.pop(0)

    def close(self):
        self.closed = True


class BrowserTestSupportTests(unittest.TestCase):
    def test_websocket_validates_handshake_accept(self):
        sock = HandshakeSocket(valid=True)
        with mock.patch.object(support.socket, "create_connection", return_value=sock):
            support.WebSocket("ws://127.0.0.1:9222/devtools/page/1")

        invalid = HandshakeSocket(valid=False)
        with mock.patch.object(support.socket, "create_connection", return_value=invalid):
            with self.assertRaisesRegex(support.CdpError, "Sec-WebSocket-Accept"):
                support.WebSocket("ws://127.0.0.1:9222/devtools/page/1")

    def test_websocket_sends_masked_short_extended_and_large_frames(self):
        sock = FakeSocket()
        websocket = object.__new__(support.WebSocket)
        websocket.sock = sock
        for size in (125, 126, 65536):
            websocket._send_frame(1, b"x" * size)

        self.assertEqual(len(sock.sent), 3)
        for frame, size in zip(sock.sent, (125, 126, 65536)):
            self.assertEqual(frame[1] & 0x80, 0x80)
            length = frame[1] & 0x7F
            offset = 2
            if length == 126:
                length = int.from_bytes(frame[offset : offset + 2], "big")
                offset += 2
            elif length == 127:
                length = int.from_bytes(frame[offset : offset + 8], "big")
                offset += 8
            self.assertEqual(length, size)
            mask = frame[offset : offset + 4]
            payload = frame[offset + 4 :]
            self.assertEqual(
                bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)),
                b"x" * size,
            )

    def test_websocket_handles_ping_and_fragmentation(self):
        message = json.dumps({"id": 7, "result": {"ok": True}}).encode("utf-8")
        split = len(message) // 2
        sock = FakeSocket(
            websocket_frame(9, b"ping")
            + websocket_frame(1, message[:split], final=False)
            + websocket_frame(0, message[split:])
        )
        websocket = object.__new__(support.WebSocket)
        websocket.sock = sock
        websocket.buffer = b""
        value = websocket.receive()
        self.assertEqual(value, {"id": 7, "result": {"ok": True}})
        self.assertEqual(sock.sent[0][0] & 0x0F, 10)

    def test_websocket_rejects_invalid_frames(self):
        for frame, message in (
            (websocket_frame(0, b"x"), "continuation"),
            (websocket_frame(9, b"x" * 126), "control"),
            (websocket_frame(2, b"x"), "binary"),
        ):
            websocket = object.__new__(support.WebSocket)
            websocket.sock = FakeSocket(frame)
            websocket.buffer = b""
            with self.subTest(message=message):
                with self.assertRaises(support.CdpError):
                    websocket.receive()

    def test_cdp_connection_preserves_events_and_reports_protocol_errors(self):
        transport = FakeTransport(
            [
                {"method": "Runtime.consoleAPICalled", "params": {"type": "log"}},
                {"id": 1, "result": {"ok": True}},
                {"id": 2, "error": {"code": -32000, "message": "bad command"}},
            ]
        )
        connection = support.CdpConnection(transport)
        self.assertEqual(connection.call("Runtime.enable"), {"ok": True})
        self.assertEqual(connection.wait_event("Runtime.consoleAPICalled")["params"]["type"], "log")
        with self.assertRaisesRegex(support.CdpError, "bad command"):
            connection.call("Runtime.evaluate")
        connection.close()
        self.assertTrue(transport.closed)

    def test_pipe_transport_handles_split_and_multiple_messages(self):
        read_fd, write_fd = os.pipe()
        response = b'{"id":1,"result":{"ok":true}}\0{"method":"event"}\0'
        try:
            os.write(write_fd, response[:9])
            os.write(write_fd, response[9:])
            transport = support.PipeTransport(read_fd, os.dup(write_fd), timeout=1)
            self.assertEqual(transport.receive(), {"id": 1, "result": {"ok": True}})
            self.assertEqual(transport.receive(), {"method": "event"})
            transport.close()
        finally:
            for fd in (read_fd, write_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_pipe_transport_rejects_invalid_json_and_times_out(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"not-json\0")
            transport = support.PipeTransport(read_fd, os.dup(write_fd), timeout=0.05)
            with self.assertRaises(support.CdpError):
                transport.receive()
            with self.assertRaises(TimeoutError):
                transport.receive()
            transport.close()
        finally:
            for fd in (read_fd, write_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_pipe_launch_does_not_inherit_unrelated_descriptor(self):
        with temporary_directory() as directory:
            root = Path(directory)
            child = root / "fake-chrome"
            child.write_text(
                "#!/usr/bin/env python3\n"
                "import os, time\n"
                "expected = tuple(int(value) for value in os.environ['EXPECTED_FD_STAT'].split(','))\n"
                "try:\n"
                "    actual = os.fstat(int(os.environ['SENTINEL_FD']))\n"
                "    leaked = (actual.st_dev, actual.st_ino) == expected\n"
                "except OSError:\n"
                "    leaked = False\n"
                "with open(os.environ['REPORT'], 'w') as report:\n"
                "    report.write('leaked=' + str(leaked) + ';pipe3=' + str(os.fstat(3).st_mode) + ';pipe4=' + str(os.fstat(4).st_mode))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            child.chmod(child.stat().st_mode | stat.S_IXUSR)
            sentinel = os.open(root / "sentinel", os.O_CREAT | os.O_RDWR, 0o600)
            report = root / "report"
            old_environment = os.environ.copy()
            os.environ.update(
                {
                    "SENTINEL_FD": str(sentinel),
                    "EXPECTED_FD_STAT": f"{os.fstat(sentinel).st_dev},{os.fstat(sentinel).st_ino}",
                    "REPORT": str(report),
                }
            )
            profile = root / "profile"
            log = root / "chrome.log"
            process = connection = None
            try:
                process, connection = support.launch_chromium(
                    child, profile, support.chromium_args(profile, pipe=True), log, pipe=True
                )
                deadline = time.monotonic() + 5
                while (not report.exists() or not report.read_text(encoding="utf-8")) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(report.exists(), log.read_text(encoding="utf-8", errors="replace"))
                self.assertIn(
                    "leaked=False",
                    report.read_text(encoding="utf-8"),
                    log.read_text(encoding="utf-8", errors="replace"),
                )
                cleanup = support.terminate_process(process)
                self.assertTrue(cleanup["ok"], cleanup)
            finally:
                if connection:
                    connection.close()
                if process:
                    support.terminate_process(process)
                os.close(sentinel)
                os.environ.clear()
                os.environ.update(old_environment)

    def test_terminate_process_escalates_and_cleans_process_group(self):
        with temporary_directory() as directory:
            root = Path(directory)
            child = root / "stubborn-chrome"
            child.write_text(
                "#!/usr/bin/env python3\n"
                "import os, signal, subprocess, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "worker = subprocess.Popen(['/bin/sleep', '30'])\n"
                "with open(os.environ['REPORT'], 'w') as report:\n"
                "    report.write(str(worker.pid))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            child.chmod(child.stat().st_mode | stat.S_IXUSR)
            report = root / "child-pid"
            old_environment = os.environ.copy()
            os.environ["REPORT"] = str(report)
            process = None
            try:
                process, _ = support.launch_chromium(
                    child, root / "profile", support.chromium_args(root / "profile", port=1), root / "chrome.log"
                )
                deadline = time.monotonic() + 5
                while not report.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(report.exists())
                cleanup = support.terminate_process(process)
                self.assertTrue(cleanup["ok"], cleanup)
                self.assertTrue(cleanup["escalated"], cleanup)
            finally:
                if process:
                    support.terminate_process(process)
                os.environ.clear()
                os.environ.update(old_environment)


if __name__ == "__main__":
    unittest.main()
