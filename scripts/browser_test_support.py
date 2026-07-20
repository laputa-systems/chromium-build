"""Shared standard-library browser test helpers."""

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import tempfile
import time
import urllib.parse


class CdpError(RuntimeError):
    pass


class WebSocket:
    def __init__(self, url, timeout=10):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws" or parsed.hostname is None or parsed.port is None:
            raise CdpError(f"unsupported CDP WebSocket URL: {url}")
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
        self.timeout = timeout
        self.sock.settimeout(min(timeout, 1))
        self.buffer = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {parsed.hostname}:{parsed.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii")
        )
        response = self._read_until(b"\r\n\r\n")
        try:
            header_lines = response.decode("ascii").split("\r\n")
        except UnicodeDecodeError as error:
            raise CdpError(f"invalid WebSocket handshake response: {response!r}") from error
        if not header_lines or header_lines[0].split()[:2] != ["HTTP/1.1", "101"]:
            raise CdpError(f"WebSocket handshake failed: {response!r}")
        headers = {}
        for line in header_lines[1:]:
            if not line:
                continue
            name, separator, value = line.partition(":")
            if not separator:
                raise CdpError(f"invalid WebSocket handshake header: {line!r}")
            headers[name.lower()] = value.strip()
        if headers.get("upgrade", "").lower() != "websocket":
            raise CdpError("WebSocket handshake omitted Upgrade: websocket")
        if not any(token.strip() == "upgrade" for token in headers.get("connection", "").lower().split(",")):
            raise CdpError("WebSocket handshake omitted Connection: Upgrade")
        expected_accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise CdpError("WebSocket handshake has an invalid Sec-WebSocket-Accept")

    def _read_until(self, marker):
        deadline = time.monotonic() + self.timeout
        while marker not in self.buffer:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out reading WebSocket handshake")
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                raise CdpError("CDP socket closed during WebSocket handshake")
            self.buffer += chunk
        value, self.buffer = self.buffer.split(marker, 1)
        return value

    def _read_bytes(self, count):
        while len(self.buffer) < count:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                raise TimeoutError("timed out reading CDP WebSocket")
            if not chunk:
                raise CdpError("Chrome closed the CDP socket")
            self.buffer += chunk
        value, self.buffer = self.buffer[:count], self.buffer[count:]
        return value

    def send(self, value):
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._send_frame(1, payload)

    def receive(self):
        fragments = []
        while True:
            first, second = self._read_bytes(2)
            if first & 0x70:
                raise CdpError("WebSocket frame has unsupported RSV bits")
            opcode = first & 0x0F
            final = bool(first & 0x80)
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self._read_bytes(2), "big")
            elif length == 127:
                length = int.from_bytes(self._read_bytes(8), "big")
                if length & (1 << 63):
                    raise CdpError("WebSocket frame length has the reserved high bit set")
            is_control = opcode >= 8
            if is_control and (not final or length > 125):
                raise CdpError("invalid fragmented or oversized WebSocket control frame")
            mask = self._read_bytes(4) if second & 0x80 else b""
            payload = self._read_bytes(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 9:
                self._send_control(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode == 8:
                raise CdpError("Chrome closed the CDP socket")
            if opcode == 1:
                if fragments:
                    raise CdpError("new WebSocket data frame interrupted a fragmented message")
                fragments = [payload]
            elif opcode == 0:
                if not fragments:
                    raise CdpError("WebSocket continuation frame has no starting message")
                fragments.append(payload)
            elif opcode == 2:
                raise CdpError("binary WebSocket frames are not supported for CDP")
            else:
                raise CdpError(f"unsupported WebSocket opcode: {opcode}")
            if final:
                try:
                    return json.loads(b"".join(fragments))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise CdpError("WebSocket data was not valid JSON") from error

    def _send_control(self, opcode, payload):
        self._send_frame(opcode, payload)

    def _send_frame(self, opcode, payload):
        if len(payload) > 125 and opcode >= 8:
            raise ValueError("WebSocket control frame payload is too large")
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + length.to_bytes(2, "big")
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + length.to_bytes(8, "big")
        self.sock.sendall(header + mask + masked)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class CdpConnection:
    def __init__(self, transport):
        self.transport = transport
        self.next_id = 0
        self.events = []

    def call(self, method, params=None, session_id=None, timeout=15):
        self.next_id += 1
        command_id = self.next_id
        message = {"id": command_id, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        self.transport.send(message)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = self.transport.receive()
            except TimeoutError:
                continue
            if not isinstance(value, dict):
                raise CdpError("CDP transport returned a non-object message")
            if value.get("id") != command_id:
                self.events.append(value)
                continue
            if "error" in value:
                raise CdpError(f"{method}: {value['error']}")
            return value.get("result", {})
        raise TimeoutError(f"timed out waiting for {method}")

    def wait_event(self, method, session_id=None, predicate=None, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, value in enumerate(self.events):
                if value.get("method") != method:
                    continue
                if session_id is not None and value.get("sessionId") != session_id:
                    continue
                if predicate is not None and not predicate(value):
                    continue
                return self.events.pop(index)
            try:
                value = self.transport.receive()
            except TimeoutError:
                continue
            if not isinstance(value, dict):
                raise CdpError("CDP transport returned a non-object message")
            if value.get("method") == method and (
                session_id is None or value.get("sessionId") == session_id
            ) and (predicate is None or predicate(value)):
                return value
            self.events.append(value)
        raise TimeoutError(f"timed out waiting for {method}")

    def close(self):
        self.transport.close()


class PipeTransport:
    def __init__(self, read_fd, write_fd, timeout=15):
        self.read_fd = read_fd
        self.write_fd = write_fd
        self.timeout = timeout
        self.read_buffer = b""

    def send(self, value):
        payload = json.dumps(value, separators=(",", ":")).encode() + b"\0"
        offset = 0
        deadline = time.monotonic() + self.timeout
        while offset < len(payload):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out writing CDP pipe")
            _, writable, _ = select.select([], [self.write_fd], [], remaining)
            if not writable:
                raise TimeoutError("timed out writing CDP pipe")
            try:
                offset += os.write(self.write_fd, payload[offset:])
            except InterruptedError:
                continue

    def receive(self):
        deadline = time.monotonic() + self.timeout
        while b"\0" not in self.read_buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out reading CDP pipe")
            readable, _, _ = select.select([self.read_fd], [], [], remaining)
            if not readable:
                raise TimeoutError("timed out reading CDP pipe")
            chunk = os.read(self.read_fd, 65536)
            if not chunk:
                raise CdpError("Chrome closed the CDP pipe")
            self.read_buffer += chunk
        value, self.read_buffer = self.read_buffer.split(b"\0", 1)
        try:
            return json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CdpError("CDP pipe message was not valid JSON") from error

    def close(self):
        for fd in (self.read_fd, self.write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def chromium_args(
    profile_dir,
    *,
    port=None,
    pipe=False,
    extensions=(),
    download_dir=None,
    allow_insecure_localhost=False,
    certificate_spki=None,
):
    args = [
        "--headless=new",
        "--ozone-platform=headless",
        "--window-size=1680,1050",
        "--user-data-dir=" + str(profile_dir),
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-logging=stderr",
        "--use-gl=egl",
        "--use-mock-keychain",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=Translate,DialMediaRouteProvider,MediaRouter,OptimizationHints,GlobalMediaControls,PaintHolding,AvoidUnnecessaryBeforeUnloadCheckSync,HttpsUpgrades",
        "--allow-running-insecure-content",
        "--disable-hang-monitor",
        "--disable-prompt-on-repost",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-infobars",
        "--disable-field-trial-config",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-breakpad",
        "--disable-sync",
        "--force-webrtc-ip-handling-policy=default_public_interface_only",
        "--host-resolver-rules=MAP ad.doubleclick.net 127.0.0.1",
        "about:blank",
    ]
    if pipe:
        args.append("--remote-debugging-pipe")
    else:
        args.extend(["--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}"])
    if extensions:
        args.append("--load-extension=" + ",".join(str(path) for path in extensions))
    else:
        args.extend(
            [
                "--disable-background-networking",
                "--disable-component-extensions-with-background-pages",
                "--disable-component-update",
                "--disable-extensions",
            ]
        )
    if download_dir is not None:
        args.append("--download.default_directory=" + str(download_dir))
    if allow_insecure_localhost:
        args.append("--allow-insecure-localhost")
    if certificate_spki:
        args.append("--ignore-certificate-errors-spki-list=" + certificate_spki)
    return args


def _descendant_pids(root_pid):
    parent_by_pid = {pid: values["ppid"] for pid, values in _process_table().items()}
    descendants = []
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        children = [pid for pid, ppid in parent_by_pid.items() if ppid == parent]
        descendants.extend(children)
        pending.extend(children)
    return descendants


def _process_table():
    processes = {}
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                status = (entry / "status").read_text(encoding="utf-8")
                stat = (entry / "stat").read_text(encoding="utf-8")
            except OSError:
                continue
            parent = next((line for line in status.splitlines() if line.startswith("PPid:")), None)
            stat_tail = stat[stat.rfind(") ") + 2 :].split()
            if parent and len(stat_tail) >= 3:
                processes[int(entry.name)] = {
                    "ppid": int(parent.split()[1]),
                    "pgid": int(stat_tail[2]),
                }
    else:
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,ppid=,pgid="], text=True)
        except (OSError, subprocess.CalledProcessError):
            output = ""
        for line in output.splitlines():
            fields = line.split()
            if len(fields) == 3 and all(field.isdigit() for field in fields):
                processes[int(fields[0])] = {"ppid": int(fields[1]), "pgid": int(fields[2])}
    return processes


def _process_group_pids(process_group):
    return [pid for pid, values in _process_table().items() if values["pgid"] == process_group]


def terminate_process(process):
    if process is None:
        return {"status": "not-started", "ok": True, "survivors": []}
    root_pid = process.pid
    descendants = _descendant_pids(root_pid)
    process_group = getattr(process, "_chromium_process_group", None)
    if process_group is None:
        try:
            process_group = os.getpgid(root_pid)
        except OSError:
            process_group = None
    if process_group == os.getpgrp():
        process_group = None
    escalated = False
    if process.poll() is None:
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except OSError:
                pass
        else:
            try:
                process.terminate()
            except OSError:
                pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        escalated = True
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except OSError:
                pass
        try:
            process.kill()
        except OSError:
            pass
        process.wait(timeout=5)
    group_pids = _process_group_pids(process_group) if process_group is not None else []
    for pid in descendants + _descendant_pids(root_pid) + group_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        escalated = True
    survivors = []
    for _ in range(10):
        survivors = [pid for pid in _descendant_pids(root_pid) if _pid_is_alive(pid)]
        if process_group is not None:
            survivors.extend(pid for pid in _process_group_pids(process_group) if _pid_is_alive(pid))
        if not survivors:
            break
        for pid in set(survivors):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.1)
    if process.poll() is None:
        survivors.insert(0, root_pid)
    return {
        "status": "complete" if not survivors else "failed",
        "ok": not survivors,
        "root_pid": root_pid,
        "root_returncode": process.returncode,
        "process_group": process_group,
        "initial_descendants": descendants,
        "survivors": sorted(set(survivors)),
        "escalated": escalated,
    }


def _pid_is_alive(pid):
    try:
        status = (Path(f"/proc/{pid}") / "status").read_text(encoding="utf-8")
        if any(line.startswith("State:") and "\tZ" in line for line in status.splitlines()):
            return False
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_sandbox_evidence(root_pid):
    if not Path("/proc").is_dir():
        return []
    records = []
    for pid in [root_pid, *_descendant_pids(root_pid)]:
        try:
            status_lines = (Path(f"/proc/{pid}") / "status").read_text(encoding="utf-8").splitlines()
            cmdline = (Path(f"/proc/{pid}") / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            ).strip()
        except OSError:
            continue
        values = {}
        for line in status_lines:
            name, separator, value = line.partition(":")
            if separator and name in {"Name", "NoNewPrivs", "Seccomp", "Seccomp_filters"}:
                values[name] = value.strip()
        values.update({"pid": pid, "cmdline": cmdline})
        records.append(values)
    return records


def launch_chromium(binary, profile_dir, args, log_path, *, pipe=False):
    profile_dir.mkdir(parents=True, exist_ok=True)
    pipe_fds = []
    pipe_transport = None
    if pipe:
        parent_read, child_write = os.pipe()
        child_read, parent_write = os.pipe()
        pipe_fds = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 5) for fd in (parent_read, child_write, child_read, parent_write)]
        os.close(parent_read)
        os.close(child_write)
        os.close(child_read)
        os.close(parent_write)
        parent_read, child_write, child_read, parent_write = pipe_fds
        pipe_transport = PipeTransport(parent_read, parent_write)
    log = log_path.open("w", encoding="utf-8")
    backups = []
    try:
        if pipe:
            for target in (3, 4):
                try:
                    backup = fcntl.fcntl(target, fcntl.F_DUPFD_CLOEXEC, 5)
                except OSError:
                    backup = None
                backups.append(backup)
            os.dup2(child_read, 3)
            os.dup2(child_write, 4)
        process = subprocess.Popen(
            [str(binary), *args],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            pass_fds=(3, 4) if pipe else (),
            start_new_session=True,
        )
    except Exception:
        if pipe_transport is not None:
            pipe_transport.close()
        log.close()
        raise
    finally:
        if pipe:
            for target, backup in zip((3, 4), backups):
                if backup is None:
                    try:
                        os.close(target)
                    except OSError:
                        pass
                else:
                    os.dup2(backup, target)
                    os.close(backup)
            for fd in (child_read, child_write):
                try:
                    os.close(fd)
                except OSError:
                    pass
    try:
        setattr(process, "_chromium_process_group", os.getpgid(process.pid))
    except OSError:
        setattr(process, "_chromium_process_group", None)
    log.close()
    if pipe_transport is not None:
        return process, CdpConnection(pipe_transport)
    return process, None


def temporary_profile(output, prefix):
    return Path(tempfile.mkdtemp(prefix=prefix, dir=output))
