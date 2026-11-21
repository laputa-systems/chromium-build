"""Argument validation of ./chromium-build, which runs before any Docker or backend work."""

import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def chromium_build(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "chromium-build"), *arguments],
        stdin=subprocess.DEVNULL,
        env=os.environ | {"CHROMIUM_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def run_all(cases: list[list[str]]) -> list[subprocess.CompletedProcess[str]]:
    with ThreadPoolExecutor(max_workers=8) as executor:
        return list(executor.map(lambda arguments: chromium_build(*arguments), cases))


class UsageTests(unittest.TestCase):
    def test_help_prints_usage_and_succeeds(self):
        for flag in ("--help", "-h"):
            result = chromium_build(flag)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout.startswith("usage: chromium-build "))
            self.assertEqual(result.stderr, "")

    def test_a_missing_command_prints_usage_to_stderr_and_exits_2(self):
        result = chromium_build("--product", "chromium")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("usage: chromium-build "))


class RejectionTests(unittest.TestCase):
    def assertRejected(self, arguments: list[str], message: str) -> None:
        self.assertRejectedAll(((arguments, message),))

    def assertRejectedAll(self, cases: tuple[tuple[list[str], str], ...]) -> None:
        for (arguments, message), result in zip(cases, run_all([arguments for arguments, _ in cases]), strict=True):
            with self.subTest(arguments):
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stderr.strip(), f"chromium-build: {message}")
                self.assertEqual(result.stdout, "")

    def test_malformed_command_lines(self):
        self.assertRejectedAll((
            (["--bogus"], "unknown argument --bogus"),
            (["build", "image"], "only one command may be selected"),
            (["--arch"], "--arch needs a value"),
            (["--jobs"], "--jobs needs a value"),
            (["build-target"], "build-target needs an output or label"),
        ))

    def test_product_platform_and_architecture_are_closed_sets(self):
        self.assertRejectedAll((
            (["--product", "ungoogled", "build"], "unknown product ungoogled"),
            (["--platform", "bsd", "build"], "unsupported platform bsd"),
            (["--arch", "x86", "build"], "unsupported architecture x86"),
            (["--platform", "macos", "--arch", "amd64", "build"], "macOS supports native arm64 only"),
            (["--platform", "macos", "--product", "helium", "build"], "product helium is not supported on macOS"),
        ))

    def test_profiles_belong_to_their_product_and_platform(self):
        self.assertRejectedAll((
            (
                ["--profile", "nope", "build"],
                "Chromium profile nope is not supported; expected headless-debug, headless-release, or chromium-wayland-release",
            ),
            (
                ["--product", "helium", "--profile", "headless-debug", "build"],
                "Helium profile headless-debug is not supported; expected helium-wayland-release",
            ),
            (
                ["--platform", "macos", "--profile", "headless-debug", "build"],
                "macOS profile headless-debug is not supported; expected macos-release or macos-helium-release",
            ),
        ))

    def test_install_options_are_limited_to_helium_install(self):
        self.assertRejectedAll((
            (["--archive", "/a.tar.xz", "install"], "--archive is only supported by Helium install"),
            (["--product", "helium", "--archive", "/a.tar.xz", "build"], "--archive is only supported by Helium install"),
            (["--product", "helium", "--archive", "a.tar.xz", "install"], "--archive must be an absolute path: a.tar.xz"),
            (
                ["--product", "helium", "--archive", "/a.tar.xz", "--rollback", "install"],
                "--archive and --rollback are mutually exclusive",
            ),
            (["--rollback", "install"], "--rollback is only supported by Helium install"),
            (["--product", "helium", "--rollback", "build"], "--rollback is only supported by Helium install"),
            (["--platform", "macos", "--archive", "/a.tar.xz", "doctor"], "--archive is Linux Helium only"),
            (["--platform", "macos", "--rollback", "doctor"], "--rollback is Linux Helium install only"),
        ))

    def test_headed_reference_is_macos_only(self):
        self.assertRejected(["--headed-reference", "observations.json", "test-staged"], "--headed-reference is macOS-only")

    def test_the_chromium_backend_has_no_native_arm64_headless_line(self):
        result = chromium_build("--arch", "arm64", "build")
        self.assertEqual(result.returncode, 1)
        self.assertIn("the Chromium backend currently supports amd64 only", result.stderr)

    def test_macos_build_target_is_linux_only(self):
        self.assertRejected(
            ["--platform", "macos", "build-target", "obj/x.o"],
            "build-target is currently Linux-only; use macOS build to produce the release target",
        )

    def test_the_macos_backend_rejects_a_headed_reference_for_other_commands(self):
        result = chromium_build("--platform", "macos", "--headed-reference", "observations.json", "doctor")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), "macos-build: --headed-reference applies only to test-staged")


if __name__ == "__main__":
    unittest.main()
