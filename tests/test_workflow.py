#!/usr/bin/env python3
"""Policy checks for the macOS release workflow file."""

import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "macos-26-smoke.yml"


def read_workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


class WorkflowTests(unittest.TestCase):
    def test_uses_pins_are_immutable_shas(self):
        text = read_workflow()
        uses = re.findall(r"(?m)^\s*uses:\s*(\S+)", text)
        self.assertTrue(uses, "workflow has no uses: entries")
        for entry in uses:
            self.assertRegex(entry, r"^[^@]+@[0-9a-f]{40}$", f"unpinned action: {entry}")

    def test_build_and_publish_jobs_hold_read_and_write(self):
        text = read_workflow()
        self.assertRegex(text, r"(?m)^\s{2}build:\s*$")
        self.assertRegex(text, r"(?m)^\s{2}publish:\s*$")
        build_block = re.search(r"(?ms)^\s{2}build:\s*\n(.*?)(?=^\s{2}\w+:\s*$|\Z)", text)
        publish_block = re.search(r"(?ms)^\s{2}publish:\s*\n(.*?)(?=^\s{2}\w+:\s*$|\Z)", text)
        self.assertIsNotNone(build_block)
        self.assertIsNotNone(publish_block)
        self.assertRegex(build_block.group(1), r"contents:\s*read")
        self.assertRegex(publish_block.group(1), r"contents:\s*write")
        self.assertRegex(publish_block.group(1), r"needs:\s*\[?build\]?")

    def test_sha_is_checked_before_compile_and_before_publish(self):
        text = read_workflow()
        self.assertIn("GITHUB_SHA", text)
        self.assertIn("rev-parse HEAD", text)
        build_at = text.index("  build:")
        publish_at = text.index("  publish:")
        first_check = text.index("rev-parse HEAD")
        self.assertLess(build_at, first_check, "SHA check must be inside jobs")
        self.assertLess(first_check, publish_at, "build job must check SHA before publish job")
        publish_block = text[publish_at:]
        self.assertIn("rev-parse HEAD", publish_block)
        self.assertIn("gh release create", publish_block)
        self.assertLess(publish_block.index("rev-parse HEAD"), publish_block.index("gh release create"))

    def test_no_homebrew_llvm_or_rustup_installs(self):
        text = read_workflow()
        self.assertNotIn("brew install", text)
        self.assertNotIn("brew upgrade", text)
        self.assertNotIn("rustup toolchain install", text)
        self.assertNotIn("rustup-init", text)

    def test_ci_archive_is_experimental(self):
        text = read_workflow()
        self.assertIn("-experimental.zip", text)
        self.assertNotRegex(text, r"Chromium-\$\{version\}-macos-arm64\.zip")


if __name__ == "__main__":
    unittest.main()
