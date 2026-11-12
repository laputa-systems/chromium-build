"""Helium GN contracts: product-branded resolution and profile authorization."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVE = ROOT / "scripts/resolve-output.py"
VERIFY_ARGS = ROOT / "scripts/verify-gn-args.py"
GENERATE_ARGS = ROOT / "scripts/generate-gn-args.py"
HELIUM_PROFILE = ROOT / "config/profiles/helium-wayland-release.gn"
PURE_ARM64_VALUES = {
    "use_system_libffi": False,
    "use_glib": False,
    "use_gio": False,
    "use_udev": False,
    "use_system_minigbm": False,
    "use_vaapi": False,
    "enable_swiftshader": True,
}


def load_verify_args():
    spec = importlib.util.spec_from_file_location("verify_gn_args", VERIFY_ARGS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gn_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[ " + ", ".join(json.dumps(item) for item in value) + " ]"
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


class ResolveOutputTest(unittest.TestCase):
    def run_resolver(self, product: str, gn_body: str, outputs: dict[str, str]) -> subprocess.CompletedProcess[str]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        out = root / "out"
        source = root / "src"
        out.mkdir()
        source.mkdir()
        for name, content in outputs.items():
            path = out / name
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755)
        gn = root / "gn"
        write_executable(gn, gn_body)
        environment = dict(os.environ)
        environment.update({
            "GN": str(gn),
            "CHROMIUM_PRODUCT": product,
            "CHROMIUM_SOURCE_ROOT": str(source),
        })
        return subprocess.run(
            [sys.executable, str(RESOLVE), str(out), "//chrome:chrome"],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_helium_resolves_the_branded_graph_output(self):
        result = self.run_resolver("helium", 'print("helium")', {"helium": "elf"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).name, "helium")

    def test_helium_resolves_an_absolute_graph_output_once(self):
        result = self.run_resolver(
            "helium",
            'import os, sys\nprint(os.path.join(sys.argv[2], "helium"))\nprint(os.path.join(sys.argv[2], "helium"))',
            {"helium": "elf"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertTrue(Path(result.stdout.strip()).is_absolute())
        self.assertEqual(Path(result.stdout.strip()).name, "helium")

    def test_helium_rejects_unbranded_graph_output(self):
        result = self.run_resolver("helium", 'print("chrome")', {"chrome": "elf"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected helium", result.stderr)
        self.assertNotIn("chrome\n", result.stdout)

    def test_helium_forbids_the_legacy_fallback(self):
        result = self.run_resolver("helium", "raise SystemExit(1)", {"chrome": "elf"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chrome_initial", result.stderr)
        self.assertNotIn("chrome\n", result.stdout)

    def test_chromium_keeps_the_legacy_fallback(self):
        result = self.run_resolver("chromium", "raise SystemExit(1)", {"chrome": "elf"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).name, "chrome")


class VerifyArgsTest(unittest.TestCase):
    def test_headless_values_do_not_satisfy_the_helium_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(
                [
                    sys.executable,
                    str(VERIFY_ARGS),
                    "--args", str(ROOT / "config/profiles/headless-release.gn"),
                    "--canonical", str(root / "canonical.gn"),
                    "--effective-json", str(root / "effective.json"),
                    "--probe", str(root / "probe.json"),
                    "--target-cpu", "x64",
                    "--clang-major", "24",
                    "--profile", "helium-wayland-release",
                    "--output", str(root / "verify.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GN arg", result.stderr)

    def run_contract(self, root: Path, target_cpu: str) -> subprocess.CompletedProcess[str]:
        verify_args = load_verify_args()
        args_file = root / f"{target_cpu}-args.gn"
        probe = root / f"{target_cpu}-probe.json"
        subprocess.run(
            [
                sys.executable,
                str(GENERATE_ARGS),
                "--source-root", str(ROOT),
                "--profile", str(HELIUM_PROFILE),
                "--target-cpu", target_cpu,
                "--clang-major", "24",
                "--use-alsa", "false" if target_cpu == "arm64" else "true",
                "--args-output", str(args_file),
                "--probe-output", str(probe),
            ],
            check=True,
        )
        values = verify_args.parse_args(args_file)
        canonical = root / f"{target_cpu}-canonical.gn"
        canonical.write_text(
            "\n".join(f"{name} = {gn_value(value)}" for name, value in values.items()) + "\n",
            encoding="utf-8",
        )
        effective = root / f"{target_cpu}-effective.json"
        effective.write_text(
            json.dumps([
                {"name": name, "current": {"value": gn_value(value)}}
                for name, value in values.items()
            ]) + "\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [
                sys.executable,
                str(VERIFY_ARGS),
                "--args", str(args_file),
                "--canonical", str(canonical),
                "--effective-json", str(effective),
                "--probe", str(probe),
                "--target-cpu", target_cpu,
                "--clang-major", "24",
                "--profile", "helium-wayland-release",
                "--output", str(root / f"{target_cpu}-verify.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_helium_profile_matches_the_effective_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_contract(Path(directory), "x64")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_arm64_profile_matches_the_pure_runtime_contract(self):
        verify_args = load_verify_args()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_contract(root, "arm64")
            self.assertEqual(result.returncode, 0, result.stderr)
            values = verify_args.parse_args(root / "arm64-args.gn")
        for name, expected in PURE_ARM64_VALUES.items():
            self.assertEqual(values.get(name), expected, name)
        self.assertFalse(values.get("use_alsa"))


if __name__ == "__main__":
    unittest.main()
