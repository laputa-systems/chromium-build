#!/usr/bin/env python3
"""Run the complete offline functional browser acceptance scenario."""

import argparse
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from browser_test_support import (
    CdpConnection,
    WebSocket,
    chromium_args,
    launch_chromium,
    process_sandbox_evidence,
    temporary_profile,
    terminate_process,
)


UBLOCK_SHA512 = "ce98b58145bc1263ca919e9e329b2b402257d8b8eef325ec2b83392812f69ed62aba41866a3a7daecc166f2b5a18392467e9aae5af9d208b51cf1b02cc20d121"
DOWNLOAD_BYTES = b"hermetic-download-ok\n"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class FixtureServer:
    def __init__(self, root):
        self.root = Path(root)
        self.hits = []
        self.lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self):
                with owner.lock:
                    owner.hits.append({"host": self.headers.get("Host", ""), "path": self.path})
                path = urllib.parse.urlparse(self.path).path
                if path == "/functional.html":
                    self._send(200, "text/html; charset=utf-8", owner.page())
                elif path == "/download.txt":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Disposition", 'attachment; filename="fixture-download.txt"')
                    self.send_header("Content-Length", str(len(DOWNLOAD_BYTES)))
                    self.end_headers()
                    self.wfile.write(DOWNLOAD_BYTES)
                elif path == "/media.mp4":
                    self._send_file(owner.root / "media.mp4", "video/mp4")
                else:
                    file_path = owner.root / path.lstrip("/")
                    if path == "/js/doubleclick.min.js":
                        self._send(200, "application/javascript", b"window.blockedResourceExecuted = true;")
                    elif file_path.is_file():
                        self._send_file(file_path, "application/javascript")
                    else:
                        self._send(404, "text/plain", b"not found")

            def _send(self, status, content_type, body):
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            def _send_file(self, path, content_type):
                self._send(200, content_type, path.read_bytes())

        class Server(http.server.ThreadingHTTPServer):
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self):
        return self.server.server_port

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/functional.html"

    def page(self):
        template = (self.root / "functional.html").read_text(encoding="utf-8")
        values = {
            "__ALLOWED_URL__": f"http://127.0.0.1:{self.port}/allowed-resource.js",
            "__BLOCKED_URL__": f"http://doubleclick.net:{self.port}/js/doubleclick.min.js",
            "__MEDIA_URL__": f"http://127.0.0.1:{self.port}/media.mp4",
            "__DOWNLOAD_URL__": f"http://127.0.0.1:{self.port}/download.txt",
        }
        for marker, value in values.items():
            template = template.replace(marker, value)
        return template

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class HttpsServer:
    def __init__(self, root):
        self.root = Path(root)

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self):
                body = b"<!doctype html><title>Hermetic HTTPS</title><main id=status>https-ok</main>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Server(http.server.ThreadingHTTPServer):
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.root / "https-cert.pem", self.root / "https-key.pem")
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"https://localhost:{self.server.server_port}/https.html"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def verify_archive(path):
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != UBLOCK_SHA512:
        raise RuntimeError(f"uBlock archive SHA-512 mismatch: {path}")


def verify_media_fixture(fixtures):
    metadata = json.loads((fixtures / "media.json").read_text(encoding="utf-8"))
    media = fixtures / metadata["filename"]
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    if media.stat().st_size != metadata["size"] or digest != metadata["sha256"]:
        raise RuntimeError(f"media fixture does not match its manifest: {media}")


def verify_https_fixture(fixtures):
    metadata = json.loads((fixtures / "https.json").read_text(encoding="utf-8"))
    for item in (metadata["certificate"], metadata["key"]):
        path = fixtures / item["filename"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError(f"HTTPS fixture does not match its manifest: {path}")
    certificate = metadata["certificate"]
    certificate_der = ssl.PEM_cert_to_DER_cert(
        (fixtures / certificate["filename"]).read_text(encoding="ascii")
    )
    if hashlib.sha256(certificate_der).hexdigest() != certificate.get("der_sha256"):
        raise RuntimeError("HTTPS certificate DER does not match its manifest")
    if not certificate.get("spki_sha256"):
        raise RuntimeError("HTTPS manifest does not contain an SPKI pin")
    return metadata


def unpack_extension(archive, output):
    verify_archive(archive)
    destination = output / "ublock-origin"
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as archive_file:
        for member in archive_file.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe extension archive member: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive_file.read(member))
    manifests = list(destination.rglob("manifest.json"))
    if not manifests:
        raise RuntimeError("uBlock archive contains no manifest.json")
    return manifests[0].parent


def reserve_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


def wait_for_version(port, process, log_path):
    deadline = time.monotonic() + 15
    url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError):
            if process.poll() is not None:
                break
            time.sleep(0.1)
    log = log_path.read_text(encoding="utf-8", errors="replace")
    raise RuntimeError(f"Chrome DevTools endpoint did not start: {log[-4000:]}")


def evaluate(connection, expression, session_id=None):
    result = connection.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        session_id=session_id,
    )
    if "exceptionDetails" in result:
        raise RuntimeError(f"JavaScript evaluation failed: {result['exceptionDetails']}")
    return result["result"].get("value")


def wait_for_ready(connection, session_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if evaluate(connection, "window.functionalReady === true", session_id):
            return
        time.sleep(0.1)
    raise RuntimeError("functional fixture did not become ready")


def wait_for_text(connection, session_id, expression, expected):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if evaluate(connection, expression, session_id) == expected:
            return
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {expression} to equal {expected!r}")


def navigate_and_wait(connection, url, session_id=None):
    result = connection.call("Page.navigate", {"url": url}, session_id=session_id)
    if result.get("errorText"):
        raise RuntimeError(f"navigation to {url} failed: {result['errorText']}")
    frame_id = result.get("frameId")
    if frame_id is None:
        raise RuntimeError(f"navigation to {url} returned no frame ID")
    connection.wait_event(
        "Page.frameNavigated",
        session_id=session_id,
        predicate=lambda event: event.get("params", {}).get("frame", {}).get("id") == frame_id,
        timeout=15,
    )
    connection.wait_event("Page.domContentEventFired", session_id=session_id, timeout=15)
    return result


def valid_png(value, expected_dimensions):
    data = base64.b64decode(value)
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False, data
    length = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR" or length != 13 or len(data) < 33:
        return False, data
    dimensions = struct.unpack(">II", data[16:24])
    return dimensions == expected_dimensions, data


def wait_for_download(path):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.is_file() and path.read_bytes() == DOWNLOAD_BYTES:
            return
        time.sleep(0.1)
    raise RuntimeError(f"download did not complete correctly: {path}")


def check_user_namespaces(required):
    setting = Path("/proc/sys/kernel/unprivileged_userns_clone")
    value = setting.read_text(encoding="utf-8").strip() if setting.is_file() else "not-exposed"
    if required and value == "0":
        raise RuntimeError("unprivileged user namespaces are disabled")
    return value


def run_tcp(binary, fixtures, server_url, https_url, fixture_hits, output, ublock, https_metadata):
    user_namespace_setting = check_user_namespaces(os.environ.get("REQUIRE_LLVMPIPE", "0") == "1")
    profile = temporary_profile(output, "functional-tcp-")
    downloads = output / "functional-downloads"
    shutil.rmtree(downloads, ignore_errors=True)
    downloads.mkdir()
    log_path = output / "functional-tcp-chromium.log"
    extension = fixtures / "extension"
    process = None
    connection = None
    result = None
    run_error = None
    cleanup = None
    artifact_path = output / "functional-tcp-result.json"
    try:
        port = reserve_port()
        args = chromium_args(
            profile,
            port=port,
            extensions=[ublock, extension],
            download_dir=downloads,
            certificate_spki=https_metadata["certificate"]["spki_sha256"],
        )
        process, _ = launch_chromium(binary, profile, args, log_path)
        version = wait_for_version(port, process, log_path)
        if not version.get("webSocketDebuggerUrl", "").startswith(f"ws://127.0.0.1:{port}/"):
            raise RuntimeError("DevTools endpoint is not loopback-only")
        connection = CdpConnection(WebSocket(version["webSocketDebuggerUrl"]))
        browser_version = connection.call("Browser.getVersion")
        connection.call("Target.setDiscoverTargets", {"discover": True})
        targets = connection.call("Target.getTargets")["targetInfos"]
        target = connection.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        attached = connection.call("Target.attachToTarget", {"targetId": target, "flatten": True})
        session_id = attached["sessionId"]
        for domain in ("Runtime", "Page", "Network"):
            connection.call(f"{domain}.enable", session_id=session_id)
        connection.call("Security.enable", session_id=session_id)
        connection.call(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(downloads)},
        )
        navigate_and_wait(connection, https_url, session_id)
        wait_for_text(connection, session_id, "document.title", "Hermetic HTTPS")
        navigate_and_wait(connection, server_url, session_id)
        wait_for_ready(connection, session_id)
        state = evaluate(connection, "window.functionalState", session_id)
        if state.get("allowed") is not True or state.get("blocked") is not True:
            raise RuntimeError(f"unexpected adblock state: {state}")
        if state.get("blockedExecuted") is not False:
            raise RuntimeError(f"blocked resource executed unexpectedly: {state}")
        if state.get("extension") is not True or state.get("mutation") is not True:
            raise RuntimeError(f"extension or mutation check failed: {state}")
        if os.environ.get("REQUIRE_MEDIA", "0") == "1":
            if not state["media"].get("metadata") or state["media"].get("framePixels", 0) <= 0:
                raise RuntimeError(f"media did not decode a visible frame: {state['media']}")
            if (
                not state["media"].get("canPlay")
                or not state["media"].get("audioMetadata")
                or state["media"].get("repeatCycles", 0) != 3
            ):
                raise RuntimeError("Chromium reported no H.264/AAC support")
        navigate_and_wait(connection, https_url, session_id)
        wait_for_text(connection, session_id, "document.title", "Hermetic HTTPS")
        navigate_and_wait(connection, server_url, session_id)
        wait_for_ready(connection, session_id)
        final_state = evaluate(connection, "window.functionalState", session_id)
        storage = evaluate(
            connection,
            "document.cookie='fixture_cookie=ok; SameSite=Lax'; localStorage.fixture='ok'; sessionStorage.fixture='ok'; JSON.stringify({cookie:document.cookie, local:localStorage.fixture, session:sessionStorage.fixture})",
            session_id,
        )
        if json.loads(storage) != {"cookie": "fixture_cookie=ok", "local": "ok", "session": "ok"}:
            raise RuntimeError(f"storage check failed: {storage}")
        screenshot_dimensions = tuple(evaluate(connection, "[window.innerWidth, window.innerHeight]", session_id))
        screenshot_ok, screenshot = valid_png(
            connection.call("Page.captureScreenshot", {"format": "png"}, session_id=session_id)["data"],
            screenshot_dimensions,
        )
        if not screenshot_ok:
            raise RuntimeError("invalid TCP screenshot")
        screenshot_path = output / "functional-tcp.png"
        screenshot_path.write_bytes(screenshot)
        status_text = evaluate(connection, "document.querySelector('#status').textContent", session_id)
        try:
            status_state = json.loads(status_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"fixture status sentinel was not JSON: {status_text!r}") from error
        for key in ("allowed", "blocked", "blockedExecuted", "extension", "media"):
            if status_state.get(key) != final_state.get(key):
                raise RuntimeError(f"fixture status sentinel disagreed for {key}: {status_state!r} != {final_state!r}")
        if final_state.get("mutation") is not True or status_state.get("mutation") not in (False, True):
            raise RuntimeError(f"fixture mutation sentinel was unexpected: {status_state!r} != {final_state!r}")
        evaluate(connection, "document.querySelector('#download').click()", session_id)
        wait_for_download(downloads / "fixture-download.txt")
        events = list(connection.events)
        console_messages = [
            argument.get("value")
            for event in events
            if event.get("method") == "Runtime.consoleAPICalled"
            for argument in event.get("params", {}).get("args", [])
            if isinstance(argument.get("value"), str)
        ]
        expected_console = {"functional-fixture-start", "functional-fixture-ready"}
        if not expected_console.issubset(console_messages):
            raise RuntimeError(f"expected console messages were not received: {console_messages}")
        exceptions = [event for event in events if event.get("method") == "Runtime.exceptionThrown"]
        if exceptions:
            raise RuntimeError(f"fixture raised JavaScript exceptions: {exceptions}")
        network_events = [event for event in events if event.get("method", "").startswith("Network.")]
        if not network_events:
            raise RuntimeError("network events were not received")
        blocked_request_ids = {
            event.get("params", {}).get("requestId")
            for event in events
            if event.get("method") == "Network.requestWillBeSent"
            and "doubleclick.net:" in event.get("params", {}).get("request", {}).get("url", "")
        }
        blocked_request = any(
            event.get("method") == "Network.requestWillBeSent"
            and "doubleclick.net:" in event.get("params", {}).get("request", {}).get("url", "")
            for event in events
        )
        if not blocked_request:
            raise RuntimeError("blocked resource request was not observed")
        blocked_failures = [
            event.get("params", {}).get("errorText", "")
            for event in events
            if event.get("method") == "Network.loadingFailed"
            and event.get("params", {}).get("requestId") in blocked_request_ids
        ]
        if blocked_failures and any("BLOCKED_BY_CLIENT" not in error for error in blocked_failures):
            raise RuntimeError(f"blocked request had an unexpected failure: {blocked_failures}")
        if any("doubleclick.net" in hit.get("host", "") for hit in fixture_hits):
            raise RuntimeError("blocked resource reached the fixture server")
        allowed_paths = {"/functional.html", "/allowed-resource.js", "/media.mp4", "/download.txt"}
        unexpected_hits = [hit for hit in fixture_hits if hit.get("path", "").split("?", 1)[0] not in allowed_paths]
        if unexpected_hits:
            raise RuntimeError(f"fixture received unexpected requests: {unexpected_hits}")
        extension_targets = [
            info for info in connection.call("Target.getTargets")["targetInfos"]
            if info.get("url", "").startswith("chrome-extension://")
        ]
        extension_context = any(
            event.get("method") == "Runtime.executionContextCreated"
            and event.get("params", {}).get("context", {}).get("origin", "").startswith("chrome-extension://")
            for event in events
        )
        if not extension_context:
            raise RuntimeError("CDP did not expose the extension execution context")
        if not extension_targets:
            raise RuntimeError("CDP did not expose an extension target")
        gpu_info = connection.call("SystemInfo.getInfo")
        gpu_text = json.dumps(gpu_info, sort_keys=True)
        if os.environ.get("REQUIRE_LLVMPIPE", "0") == "1" and "llvmpipe" not in gpu_text.lower():
            raise RuntimeError(f"GPU diagnostics do not identify Mesa llvmpipe: {gpu_text}")
        command_line = browser_version.get("commandLine", "")
        if "--no-sandbox" in command_line:
            raise RuntimeError("Chromium started with --no-sandbox")
        if "--ignore-certificate-errors" in command_line and "--ignore-certificate-errors-spki-list=" not in command_line:
            raise RuntimeError("Chromium started with an unscoped certificate ignore flag")
        if not gpu_info.get("gpu", {}).get("auxAttributes", {}).get("sandboxed", False):
            raise RuntimeError("GPU process does not report sandboxing")
        sandbox_evidence = process_sandbox_evidence(process.pid)
        if any("--no-sandbox" in record.get("cmdline", "") for record in sandbox_evidence):
            raise RuntimeError("a Chromium process started with --no-sandbox")
        if os.environ.get("REQUIRE_LLVMPIPE", "0") == "1":
            renderer_evidence = [
                record
                for record in sandbox_evidence
                if "--type=renderer" in record.get("cmdline", "")
                and (record.get("NoNewPrivs") == "1" or record.get("Seccomp") == "2")
            ]
            if not renderer_evidence:
                raise RuntimeError(f"no sandboxed renderer process evidence: {sandbox_evidence}")
        certificate_errors = [event for event in events if event.get("method") == "Security.certificateError"]
        if certificate_errors:
            raise RuntimeError(f"HTTPS certificate validation reported errors: {certificate_errors}")
        runtime_audit = None
        if os.environ.get("REQUIRE_MEDIA", "0") == "1":
            runtime_audit = output / "media-runtime.json"
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("media-runtime-audit.py")),
                    "--root-pid",
                    str(process.pid),
                    "--chrome",
                    str(binary),
                    "--output",
                    str(runtime_audit),
                    "--nm",
                    os.environ.get("LLVM_NM", "llvm-nm"),
                ] + (["--baseline", os.environ["MEDIA_RUNTIME_BASELINE"]] if os.environ.get("MEDIA_RUNTIME_BASELINE") else []),
                check=True,
            )
        connection.call("Target.closeTarget", {"targetId": target})
        connection.call("Browser.close")
        result = {
            "browser_version": browser_version,
            "initial_targets": len(targets),
            "extension_targets": len(extension_targets),
            "extension_context": extension_context,
            "state": state,
            "storage": json.loads(storage),
            "screenshot": str(screenshot_path),
            "download": str(downloads / "fixture-download.txt"),
            "gpu": gpu_info,
            "events": sorted({event.get("method") for event in events if event.get("method")}),
            "blocked_request_observed": blocked_request,
            "https": {"before_media": True, "after_media": True},
            "sandboxed": True,
            "user_namespace_setting": user_namespace_setting,
            "media_runtime_audit": str(runtime_audit) if runtime_audit else None,
            "sandbox_evidence": sandbox_evidence,
            "console_messages": sorted(set(console_messages)),
            "blocked_failure_errors": blocked_failures,
            "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
            "screenshot_dimensions": screenshot_dimensions,
            "target_id": target,
            "session_id": session_id,
            "profile": str(profile),
        }
    except BaseException as error:
        run_error = error
        raise
    finally:
        if connection:
            connection.close()
        cleanup = terminate_process(process)
        artifact = {
            "schema": 1,
            "status": "complete" if run_error is None and cleanup["ok"] else "failed",
            "error": str(run_error) if run_error else None,
            "profile": str(profile),
            "log": str(log_path),
            "downloads": str(downloads),
            "cleanup": cleanup,
        }
        write_json(artifact_path, artifact)
        if run_error is None and result is not None and cleanup["ok"]:
            shutil.rmtree(profile)
    if not cleanup["ok"]:
        raise RuntimeError(f"Chromium cleanup failed: {cleanup}")
    result["cleanup"] = cleanup
    result["artifact"] = str(artifact_path)
    return result


def run_pipe(binary, server_url, output):
    profile = temporary_profile(output, "functional-pipe-")
    log_path = output / "functional-pipe-chromium.log"
    process = None
    connection = None
    result = None
    run_error = None
    cleanup = None
    artifact_path = output / "functional-pipe-result.json"
    try:
        process, connection = launch_chromium(
            binary,
            profile,
            chromium_args(profile, pipe=True),
            log_path,
            pipe=True,
        )
        version = connection.call("Browser.getVersion")
        target = connection.call("Target.createTarget", {"url": server_url})["targetId"]
        session_id = connection.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        connection.call("Runtime.enable", session_id=session_id)
        connection.call("Page.enable", session_id=session_id)
        navigate_and_wait(connection, server_url, session_id)
        value = evaluate(connection, "document.title", session_id)
        if value != "Hermetic browser acceptance":
            raise RuntimeError(f"pipe navigation failed: {value!r}")
        screenshot_dimensions = tuple(evaluate(connection, "[window.innerWidth, window.innerHeight]", session_id))
        screenshot_ok, screenshot = valid_png(
            connection.call("Page.captureScreenshot", {"format": "png"}, session_id=session_id)["data"],
            screenshot_dimensions,
        )
        if not screenshot_ok:
            raise RuntimeError(f"invalid pipe screenshot for viewport {screenshot_dimensions}")
        screenshot_path = output / "functional-pipe.png"
        screenshot_path.write_bytes(screenshot)
        connection.call("Target.closeTarget", {"targetId": target})
        connection.call("Browser.close")
        result = {
            "browser_version": version,
            "evaluation": value,
            "screenshot": str(screenshot_path),
            "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
            "screenshot_dimensions": screenshot_dimensions,
            "target_id": target,
            "session_id": session_id,
            "profile": str(profile),
        }
    except BaseException as error:
        run_error = error
        raise
    finally:
        if connection:
            connection.close()
        cleanup = terminate_process(process)
        write_json(
            artifact_path,
            {
                "schema": 1,
                "status": "complete" if run_error is None and cleanup["ok"] else "failed",
                "error": str(run_error) if run_error else None,
                "profile": str(profile),
                "log": str(log_path),
                "cleanup": cleanup,
            },
        )
        if run_error is None and result is not None and cleanup["ok"]:
            shutil.rmtree(profile)
    if not cleanup["ok"]:
        raise RuntimeError(f"Chromium cleanup failed: {cleanup}")
    result["cleanup"] = cleanup
    result["artifact"] = str(artifact_path)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--ublock-archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-llvmpipe", action="store_true")
    parser.add_argument("--require-media", action="store_true")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit("functional test refuses root; run Chromium as the build/test user")
    if args.require_llvmpipe:
        os.environ["REQUIRE_LLVMPIPE"] = "1"
    elif os.environ.get("REQUIRE_LLVMPIPE") is None:
        os.environ["REQUIRE_LLVMPIPE"] = "0"
    os.environ["REQUIRE_MEDIA"] = "1" if args.require_media else "0"
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report_path = args.output / "test.json"
    report = {
        "schema": 2,
        "status": "running",
        "network": "loopback-only",
        "chrome": str(args.chrome),
        "platform": sys.platform,
        "started_monotonic": started,
        "checks": [],
    }
    write_json(report_path, report)
    fixture_hits = []
    unpack_root = args.output / "unpacked-extension"
    try:
        verify_media_fixture(args.fixtures)
        https_metadata = verify_https_fixture(args.fixtures)
        with FixtureServer(args.fixtures) as server:
            with HttpsServer(args.fixtures) as https_server:
                ublock = unpack_extension(args.ublock_archive, unpack_root)
                tcp = run_tcp(
                    args.chrome,
                    args.fixtures,
                    server.url,
                    https_server.url,
                    server.hits,
                    args.output,
                    ublock,
                    https_metadata,
                )
                report["tcp"] = tcp
                report["checks"].append({"name": "tcp", "status": "complete", "artifact": tcp["artifact"]})
                write_json(report_path, report)
                pipe = run_pipe(args.chrome, server.url, args.output)
            fixture_hits = server.hits
        report["pipe"] = pipe
        report["checks"].append({"name": "pipe", "status": "complete", "artifact": pipe["artifact"]})
        report["fixture_hits"] = fixture_hits
        report["status"] = "complete"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        report["fixture_hits"] = fixture_hits
        raise
    finally:
        report["duration_seconds"] = time.monotonic() - started
        report["artifacts"] = sorted(str(path) for path in args.output.glob("*-result.json"))
        write_json(report_path, report)
    print(f"functional headless test passed: {report_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"functional test: {error}")
        raise
