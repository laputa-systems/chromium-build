"""Helium GN contracts: product-branded resolution and profile authorization."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVE = ROOT / "scripts/resolve-output.py"
VERIFY_ARGS = ROOT / "scripts/verify-gn-args.py"


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


if __name__ == "__main__":
    unittest.main()
