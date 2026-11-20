from __future__ import annotations

import argparse
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import helium_test_fast
import linux_product
from test_helium_runtime import write_stage_inputs

ROOT = Path(__file__).resolve().parents[1]
STAGE = runpy.run_path(str(ROOT / "scripts/stage-runtime.py"))
PACKAGE = runpy.run_path(str(ROOT / "scripts/package-runtime.py"))
DRIVER = runpy.run_path(str(ROOT / "scripts/test-fast-driver.py"))
FUNCTIONAL = runpy.run_path(str(ROOT / "scripts/test-functional-driver.py"))


class DesktopProductTests(unittest.TestCase):
    def test_chromium_desktop_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            out, source, deps = write_stage_inputs(Path(directory))
            (out / "helium").rename(out / "chrome")
            (out / "helium_crashpad_handler").rename(out / "chrome_crashpad_handler")
            deps.write_text("chrome\nlocales/en-US.pak\nlocales/de.pak\n")
            desktop = linux_product.desktop("chromium", "chromium-wayland-release")
            files, generated, _ = STAGE["collect_helium_files"](out / "chrome", deps, source, desktop)
            names = {str(path) for path in files}
            self.assertTrue({"chrome", "chrome_crashpad_handler", "chrome_sandbox", "chromium.png", "locales/en-US.pak"} <= names)
            self.assertFalse({"helium", "helium_crashpad_handler", "locales/de.pak"} & names)
            self.assertIn("Exec=chromium %U", generated[Path("chromium.desktop")])

    def test_stage_dispatches_desktop(self):
        main = STAGE["main"]
        with patch.dict(os.environ, {"CHROMIUM_PROFILE": "chromium-wayland-release"}), patch("sys.argv", ["stage-runtime", "--chrome", "/out/chrome", "--output", "/stage"]), patch.dict(main.__globals__, {"stage_helium": Mock(), "stage_chromium": Mock(), "find_source_root": Mock(return_value=Path("/src"))}):
            main()
            self.assertEqual(main.__globals__["stage_helium"].call_args.args[-1].executable, "chrome")
            main.__globals__["stage_chromium"].assert_not_called()

    def test_package_desktop_launcher_executes_chrome(self):
        main = PACKAGE["main"]
        with patch.dict(os.environ, {"CHROMIUM_PROFILE": "chromium-wayland-release"}), patch("sys.argv", ["package-runtime", "--stage", "/stage", "--output", "/package"]), patch.dict(main.__globals__, {"package_helium": Mock(), "package_chromium": Mock()}):
            main()
            self.assertEqual(main.__globals__["package_helium"].call_args.args[-1].executable, "chrome")
            main.__globals__["package_chromium"].assert_not_called()
        self.assertIn('exec "$root/chrome" "$@"', PACKAGE["LAUNCHER"])

    def test_driver_dispatch(self):
        for module in (DRIVER, FUNCTIONAL):
            for product, profile, script in (("chromium", "chromium-wayland-release", "helium_test_fast.py"), ("helium", "helium-wayland-release", "helium_test_fast.py"), ("chromium", "headless-release", "test-fast.py" if module is DRIVER else "test-functional.py")):
                if module is FUNCTIONAL and profile == "headless-release":
                    continue
                with self.subTest(product=product, profile=profile, driver=module["__file__"]), tempfile.TemporaryDirectory() as directory:
                    work = Path(directory)
                    (work / "metadata").mkdir()
                    (work / "metadata" / f"build-{profile}.json").write_text("{}")
                    calls = []
                    def fake_run(command, *, calls=calls, **kwargs):
                        calls.append(command)
                        return SimpleNamespace(stdout="/out/chrome\n")
                    env = {"CHROMIUM_BUILD_ROOT": str(ROOT), "CHROMIUM_WORK_ROOT": str(work), "CHROMIUM_PRODUCT": product, "CHROMIUM_PROFILE": profile, "NETWORK_MODE": "none"}
                    with patch.dict(os.environ, env, clear=True), patch.dict(module["main"].__globals__, {"run": fake_run}), patch("os.geteuid", return_value=1000):
                        module["main"]()
                    command = calls[-1]
                    self.assertEqual(Path(command[1]).name, script)
                    if product == "chromium" and profile == "chromium-wayland-release":
                        self.assertIn("--product", command)
                        self.assertIn(str(ROOT / "config/inputs.lock"), command)

    def test_harness_runs_headless_before_owned_sway(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            args = argparse.Namespace(output=output, inputs_lock=ROOT / "config/inputs.lock", product="chromium", helium=output / "chrome", headless_only=False)
            headless = Mock(return_value={"passed": True})
            sway = Mock()
            sway.prepare.side_effect = RuntimeError("sway started")
            sway.log_path = output / "sway.log"
            with patch.object(helium_test_fast, "run_headless", headless), patch.object(helium_test_fast, "HeadlessSway", return_value=sway) as constructor, patch.object(helium_test_fast.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="Chromium 156.0.8078.79", stderr="")), self.assertRaisesRegex(RuntimeError, "sway started"):
                helium_test_fast.run(args)
            self.assertEqual(headless.call_args.args[-1], "Chrome/156.0.8078.79")
            constructor.assert_called_once()
            sway.prepare.assert_called_once()
            sway.stop.assert_called_once()

    def test_desktop_identity_and_fixture(self):
        chromium = linux_product.desktop("chromium", "chromium-wayland-release")
        helium = linux_product.desktop("helium", "helium-wayland-release")
        assert chromium is not None and helium is not None
        lock = json.loads((ROOT / chromium.inputs).read_text())
        self.assertEqual(f"{chromium.browser_prefix}/{lock['chromium']['version']}", "Chrome/156.0.8078.79")
        self.assertEqual(helium.browser_prefix, "Helium")
        self.assertEqual((ROOT / "tests/fixtures" / chromium.fixture).read_bytes(), (ROOT / "tests/fixtures" / helium.fixture).read_bytes())


if __name__ == "__main__":
    unittest.main()
