"""Headless Sway fixture contract tests with owned fake processes."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/helium_wayland.py"

SWAY = """\
#!/bin/sh
echo $$ > "$XDG_RUNTIME_DIR/sway.pid"
: > "$XDG_RUNTIME_DIR/wayland-1"
: > "$XDG_RUNTIME_DIR/sway-ipc.0.$$.sock"
trap 'exit 0' TERM INT
while :; do sleep 1; done
"""

SWAY_EXIT = """\
#!/bin/sh
echo "no backend" >&2
exit 1
"""

SWAYMSG = """\
#!/bin/sh
case "$*" in
  *get_outputs*)
    if [ -f "$XDG_RUNTIME_DIR/mode" ]; then
      read -r width height < "$XDG_RUNTIME_DIR/mode"
    else
      width=1280
      height=720
    fi
    printf '[{"name":"HEADLESS-1","active":true,"current_mode":{"width":%s,"height":%s}}]' "$width" "$height"
    ;;
  *resolution*)
    echo "1280 900" > "$XDG_RUNTIME_DIR/mode"
    ;;
  *)
    exit 2
    ;;
esac
"""

SWAYMSG_HANG = """\
#!/bin/sh
sleep 30
"""

WL_COPY = """\
#!/bin/sh
case "$1" in
  --foreground) shift ;;
esac
printf '%s' "$1" > "$XDG_RUNTIME_DIR/clipboard"
echo $$ > "$XDG_RUNTIME_DIR/clipboard.pid"
trap 'exit 0' TERM INT
while :; do sleep 1; done
"""

WL_PASTE = """\
#!/bin/sh
cat "$XDG_RUNTIME_DIR/clipboard"
"""

GRIM = """\
#!/bin/sh
case "$1" in
  -o) shift 2 ;;
esac
python3 - "$1" <<'PY'
import pathlib, struct, sys
width, height = 1280, 900
data = b"\\x89PNG\\r\\n\\x1a\\n" + struct.pack(">I", 13) + b"IHDR"
data += struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
pathlib.Path(sys.argv[1]).write_bytes(data)
PY
"""


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o755)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()

    def write(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def run_fixture(self, *, timeout: float = 10, extra: list[str] | None = None) -> subprocess.CompletedProcess:
        command = [
            sys.executable,
            str(SCRIPT),
            "--check-fixture",
            "--output",
            str(self.output),
            "--sway",
            str(self.bin / "sway"),
            "--swaymsg",
            str(self.bin / "swaymsg"),
            "--grim",
            str(self.bin / "grim"),
            "--wl-copy",
            str(self.bin / "wl-copy"),
            "--wl-paste",
            str(self.bin / "wl-paste"),
            "--ready-timeout",
            "3",
            "--operation-timeout",
            "3",
            "--terminate-timeout",
            "1",
        ]
        command.extend(extra or [])
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)

    def report(self) -> dict:
        return json.loads((self.output / "fixture-report.json").read_text(encoding="utf-8"))

    def install_success(self):
        self.write("sway", SWAY)
        self.write("swaymsg", SWAYMSG)
        self.write("grim", GRIM)
        self.write("wl-copy", WL_COPY)
        self.write("wl-paste", WL_PASTE)

    def test_observes_output_clipboard_and_screenshot(self):
        self.install_success()
        result = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["observations"]["output"], {"name": "HEADLESS-1", "width": 1280, "height": 900, "active": True})
        self.assertTrue(report["observations"]["clipboard"]["round_trip"])
        self.assertEqual(report["observations"]["screenshot"]["width"], 1280)
        self.assertEqual(report["observations"]["screenshot"]["height"], 900)

    def test_compositor_exit_before_readiness_fails(self):
        self.install_success()
        self.write("sway", SWAY_EXIT)
        result = self.run_fixture()
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "failed")
        self.assertIn("exited before readiness", report["error"])

    def test_unresponsive_ipc_times_out(self):
        self.install_success()
        self.write("swaymsg", SWAYMSG_HANG)
        started = time.monotonic()
        result = self.run_fixture(timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("timed out", self.report()["error"])

    def test_missing_executable_fails_the_fixture(self):
        self.install_success()
        (self.bin / "grim").unlink()
        result = self.run_fixture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing: grim", self.report()["error"])

    def test_cleanup_terminates_owned_children(self):
        self.install_success()
        result = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        attempt = Path(self.report()["attempt"])
        for name in ("sway.pid", "clipboard.pid"):
            pid = int((attempt / "runtime" / name).read_text(encoding="utf-8").strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
