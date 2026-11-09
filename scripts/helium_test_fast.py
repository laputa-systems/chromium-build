#!/usr/bin/env python3
"""Run Helium headless without a display server, then headed under an owned Sway session."""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from browser_test_support import (
    CdpConnection,
    WebSocket,
    pid_is_alive,
    process_group_pids,
    terminate_process,
)
from helium_wayland import FIXTURE_SIZE, HeadlessSway, verify_png

CONTENT_COLOR = (51, 204, 102)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def child_pids(root: int) -> set[int]:
    pending = [root]
    found = set()
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        try:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="utf-8").split()
        except OSError:
            continue
        pending.extend(int(value) for value in children if value.isdigit())
    return found


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise RuntimeError("screenshot is not a PNG")
    return struct.unpack(">II", data[16:24])


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


def evaluate(connection: CdpConnection, expression: str):
    result = connection.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    if "exceptionDetails" in result:
        raise RuntimeError(f"fixture evaluation failed: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def find_window(tree: dict, pids: set[int]) -> dict:
    def walk(node):
        yield node
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            yield from walk(child)

    candidates = []
    for node in walk(tree):
        if node.get("type") != "con" or not node.get("visible"):
            continue
        if node.get("pid") in pids:
            candidates.append(node)
    if not candidates:
        raise RuntimeError("no mapped Sway node belongs to the Helium process")
    return candidates[0]


def sample_pixels(path: Path, window: dict) -> dict:
    from PIL import Image

    image = Image.open(path).convert("RGB")
    if image.size != FIXTURE_SIZE:
        raise RuntimeError(f"compositor screenshot is {image.size}, expected {FIXTURE_SIZE}")
    rect = window.get("rect", {})
    x, y, width, height = rect.get("x"), rect.get("y"), rect.get("width"), rect.get("height")
    if not all(isinstance(value, int) for value in (x, y, width, height)) or width <= 0 or height <= 0:
        raise RuntimeError("Sway window has no usable rectangle")
    background = image.getpixel((1, 1))
    center = image.getpixel((x + width // 2, y + height // 2))
    chrome = image.getpixel((x + width // 2, y + min(20, max(0, height - 1))))
    distance = sum(abs(center[index] - CONTENT_COLOR[index]) for index in range(3))
    if distance > 60:
        raise RuntimeError(f"content pixel {center} does not match the fixture color {CONTENT_COLOR}")
    if chrome == center:
        raise RuntimeError("browser chrome region is indistinguishable from fixture content")
    return {"background": background, "content": center, "chrome": chrome, "window": rect}


def sandbox_observations(connection: CdpConnection, require_pid_namespace: bool = False) -> dict:
    try:
        processes = connection.call("SystemInfo.getProcessInfo").get("processInfo", [])
    except Exception as error:
        raise RuntimeError(f"could not enumerate renderer processes: {error}") from error
    renderers = [item for item in processes if item.get("type") == "renderer"]
    if not renderers:
        raise RuntimeError("no live renderer process was reported")
    observations = []
    for item in renderers:
        pid = item.get("id")
        if not isinstance(pid, int) or pid <= 0:
            continue
        status = Path(f"/proc/{pid}/status")
        if not status.is_file():
            continue
        values = {}
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            name, separator, value = line.partition(":")
            if separator:
                values[name.strip()] = value.strip()
        if values.get("NoNewPrivs") != "1" or values.get("Seccomp") != "2":
            raise RuntimeError(f"renderer {pid} lacks the Chromium namespace/seccomp sandbox: {values.get('NoNewPrivs')}, {values.get('Seccomp')}")
        nspid = values.get("NSpid", "").split()
        if require_pid_namespace and len(nspid) < 2:
            raise RuntimeError(f"renderer {pid} is not in its own PID namespace: NSpid {values.get('NSpid')!r}")
        observations.append(
            {
                "pid": pid,
                "no_new_privs": values.get("NoNewPrivs"),
                "seccomp": values.get("Seccomp"),
                "nspid": nspid,
                "cmdline": Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip(),
            }
        )
    if not observations:
        raise RuntimeError("renderer process evidence was not readable")
    return {"renderers": observations}


def unprivileged_user_namespaces() -> dict:
    values = {}
    for name in ("user/max_user_namespaces", "kernel/unprivileged_userns_clone"):
        path = Path("/proc/sys") / name
        if path.is_file():
            values[name] = path.read_text(encoding="utf-8").strip()
    if values.get("user/max_user_namespaces", "0") == "0" or values.get("kernel/unprivileged_userns_clone", "1") != "1":
        raise RuntimeError(f"unprivileged user namespaces are unavailable: {values}")
    return values


def serve_fixtures(directory: Path) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait_devtools_line(log_path: Path, process: subprocess.Popen, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = re.search(r"DevTools listening on (ws://\S+)", log_path.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1)
        if process.poll() is not None:
            raise RuntimeError(f"headless Helium exited before DevTools started with {process.returncode}")
        time.sleep(0.1)
    raise RuntimeError("headless Helium never printed its DevTools endpoint")


def run_headless(args: argparse.Namespace, output: Path, environment: dict, devtools_product: str) -> dict:
    environment = {name: value for name, value in environment.items() if name not in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_SESSION_TYPE")}
    profile = Path(tempfile.mkdtemp(prefix="helium-headless-profile-", dir=output))
    home = profile / "home"
    home.mkdir(mode=0o700)
    environment.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_DATA_HOME=str(home / ".local/share"),
    )
    user_data = profile / "user-data"
    result: dict = {"user_namespaces": unprivileged_user_namespaces(), "profile": str(profile)}
    server = serve_fixtures(args.fixtures)
    log_path = output / "test-fast-headless.log"
    process = None
    browser_connection = None
    connection = None
    try:
        command = [str(args.helium), "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={user_data}", "about:blank"]
        result["command"] = command
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        process_group = os.getpgid(process.pid)
        process._chromium_process_group = process_group
        endpoint = wait_devtools_line(log_path, process, 30)
        port = urllib.parse.urlparse(endpoint).port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10) as response:
            version_info = json.load(response)
        if version_info.get("Browser") != devtools_product:
            raise RuntimeError(f"headless /json/version is not {devtools_product}: {version_info}")
        result.update({"devtools_endpoint": endpoint, "version_info": version_info})
        browser_connection = CdpConnection(WebSocket(version_info["webSocketDebuggerUrl"]))
        page_url = f"http://127.0.0.1:{server.server_address[1]}/{args.fixture.name}"
        request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(request, timeout=10) as response:
            target = json.load(response)
        connection = CdpConnection(WebSocket(target["webSocketDebuggerUrl"]))
        navigate(connection, page_url)
        page = evaluate(connection, "({status: document.querySelector('#status').textContent, layout: window.fixture.layout})")
        if page != {"status": "ready", "layout": True}:
            raise RuntimeError(f"headless page did not compute its fixture values: {page}")
        result.update({"page": {"url": page_url, "evaluated": page}, "sandbox": sandbox_observations(browser_connection, require_pid_namespace=True)})
        connection.close()
        connection = None
        browser_connection.call("Browser.close")
        browser_connection.close()
        browser_connection = None
        returncode = process.wait(timeout=30)
        deadline = time.monotonic() + 10
        survivors = [pid for pid in process_group_pids(process_group) if pid_is_alive(pid)]
        while survivors and time.monotonic() < deadline:
            time.sleep(0.2)
            survivors = [pid for pid in process_group_pids(process_group) if pid_is_alive(pid)]
        exited_unreaped = [pid for pid in process_group_pids(process_group) if pid not in survivors]
        result["exit"] = {"returncode": returncode, "survivors": survivors, "exited_unreaped": exited_unreaped}
        if returncode != 0 or survivors:
            commands = {pid: Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")[:200] for pid in survivors if Path(f"/proc/{pid}/cmdline").exists()}
            raise RuntimeError(f"headless Browser.close left status {returncode} and processes {commands or survivors}")
        process = None
        shutil.rmtree(profile, ignore_errors=True)
        result.pop("profile")
        return result
    except BaseException as error:
        result["error"] = str(error)
        result["log"] = str(log_path)
        raise RuntimeError(f"headless: {error}") from error
    finally:
        if connection is not None:
            connection.close()
        if browser_connection is not None:
            browser_connection.close()
        if process is not None:
            result["cleanup"] = terminate_process(process)
        server.shutdown()
        args.headless_result = result


def chromium_version(lock: dict) -> tuple[str, str]:
    return lock["product"]["version"], lock["chromium"]["version"]


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "test-fast.json"
    lock = json.loads(args.inputs_lock.read_text(encoding="utf-8"))
    product_version, browser_version = chromium_version(lock)
    profile = Path(tempfile.mkdtemp(prefix="helium-profile-", dir=output))
    report = {
        "schema": 2,
        "status": "running",
        "network": "loopback-only",
        "product": "helium",
        "product_version": product_version,
        "chromium_version": browser_version,
        "browser": str(args.helium),
        "profile": str(profile),
    }
    write_json(report_path, report)
    fixture = None
    process = None
    connection = None
    browser_connection = None
    cleanup = None
    started = time.monotonic()
    try:
        version_environment = dict(os.environ)
        library_dir = args.helium.parent / "lib"
        if library_dir.is_dir():
            version_environment["LD_LIBRARY_PATH"] = str(library_dir) + (
                f":{version_environment['LD_LIBRARY_PATH']}" if version_environment.get("LD_LIBRARY_PATH") else ""
            )
        version = subprocess.run(
            [str(args.helium), "--version"],
            env=version_environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        version_text = (version.stdout + version.stderr).strip()
        if version.returncode or product_version not in version_text or browser_version not in version_text:
            raise RuntimeError(f"Helium version mismatch: {version_text!r}")
        report["version"] = version_text
        headless_environment = dict(version_environment)
        try:
            report["headless"] = run_headless(args, output, headless_environment, f"Helium/{product_version}")
        except BaseException:
            report["headless"] = args.headless_result
            raise
        if args.headless_only:
            report["status"] = "complete"
            report["scope"] = "headless"
            return 0
        fixture = HeadlessSway(
            output,
            "sway",
            "swaymsg",
            "grim",
            "wl-copy",
            "wl-paste",
        )
        fixture.prepare()
        fixture.start()
        window_data = fixture.select_output()
        report["output"] = window_data
        port_socket = socket.socket()
        port_socket.bind(("127.0.0.1", 0))
        debug_port = port_socket.getsockname()[1]
        port_socket.close()
        environment = fixture.environment()
        environment.update(WAYLAND_DISPLAY=fixture.wayland_socket.name)
        if os.environ.get("CHROMIUM_ARCH", "amd64") == "arm64":
            angle_args = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        else:
            environment.update(LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe")
            angle_args = ["--use-gl=angle", "--use-angle=gl"]
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
            *angle_args,
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={debug_port}",
            "about:blank",
        ]
        log_path = output / "test-fast-helium.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        process._chromium_process_group = os.getpgid(process.pid)
        version_info = wait_cdp(debug_port, process, 30)
        browser = version_info.get("Browser", "")
        if browser != f"Helium/{product_version}":
            raise RuntimeError(f"DevTools browser identity is not Helium/{product_version}: {browser!r}")
        browser_connection = CdpConnection(WebSocket(version_info["webSocketDebuggerUrl"]))
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
        color = evaluate(connection, "getComputedStyle(document.querySelector('#content')).backgroundColor")
        if color != "rgb(51, 204, 102)":
            raise RuntimeError(f"fixture content style is not applied: {color}")
        dimensions = tuple(evaluate(connection, "[Math.round(window.visualViewport.width * window.devicePixelRatio), Math.round(window.visualViewport.height * window.devicePixelRatio)]"))
        screenshot = base64.b64decode(connection.call("Page.captureScreenshot", {"format": "png", "clip": {"x": 0, "y": 0, "width": dimensions[0], "height": dimensions[1], "scale": 1}})["data"])
        screenshot_path = output / "fast-screenshot.png"
        screenshot_path.write_bytes(screenshot)
        if png_dimensions(screenshot_path) != dimensions:
            raise RuntimeError(f"CDP screenshot {png_dimensions(screenshot_path)} does not match the viewport {dimensions}")
        tree = json.loads(fixture.run_ipc(["-t", "get_tree", "-r"]).stdout)
        window = find_window(tree, child_pids(process.pid))
        compositor_path = output / "fast-compositor.png"
        fixture.run([str(fixture.grim), "-o", window_data["name"], str(compositor_path)], 10, environment)
        verify_png(compositor_path)
        pixels = sample_pixels(compositor_path, window)
        sandbox = sandbox_observations(browser_connection)
        report.update(
            {
                "status": "complete",
                "version_info": version_info,
                "window": {"name": window.get("name"), "app_id": window.get("app_id"), "pid": window.get("pid"), "rect": window.get("rect")},
                "screenshot": {"path": str(screenshot_path), "sha256": hashlib.sha256(screenshot).hexdigest(), "dimensions": list(dimensions)},
                "compositor": {"path": str(compositor_path), "pixels": pixels},
                "sandbox": sandbox,
            }
        )
        connection.call("Browser.close")
        connection.close()
        connection = None
        browser_connection.close()
        browser_connection = None
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        if fixture is not None and fixture.log_path.is_file():
            report["log"] = str(fixture.log_path)
            report["log_tail"] = fixture.log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        if process is not None and fixture is not None:
            report["browser_log"] = str(output / "test-fast-helium.log")
        raise
    finally:
        if connection is not None:
            connection.close()
        if browser_connection is not None:
            browser_connection.close()
        if process is not None:
            cleanup = terminate_process(process)
            report["cleanup"] = cleanup
        if fixture is not None:
            fixture.stop()
        report["duration_seconds"] = time.monotonic() - started
        if report["status"] == "complete" and cleanup and cleanup.get("ok"):
            shutil.rmtree(profile, ignore_errors=True)
            report.pop("profile", None)
        write_json(report_path, report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helium", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inputs-lock", type=Path, default=Path("/opt/chromium-build/config/helium.inputs.lock"))
    parser.add_argument("--headless-only", action="store_true", help="run only the display-free headless checks, e.g. directly on a host")
    args = parser.parse_args()
    args.fixture = args.fixtures / "helium-fast.html"
    if not args.fixture.is_file():
        parser.error(f"missing fixture {args.fixture}")
    if os.geteuid() == 0:
        parser.error("test-fast refuses root; run the browser as the build/test user")
    try:
        return run(args)
    except Exception as error:  # noqa: BLE001
        print(f"helium-test-fast: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
