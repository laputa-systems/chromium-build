#!/usr/bin/env python3
"""Policy checks for the workflow files."""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def read_workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def job_block(text: str, job: str) -> str:
    match = re.search(rf"(?ms)^  {job}:\s*\n(.*?)(?=^  \w[\w-]*:\s*$|\Z)", text)
    assert match is not None, f"workflow has no {job} job"
    return match.group(1)


class WorkflowTests(unittest.TestCase):
    def test_uses_pins_are_immutable_shas(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            uses = re.findall(r"(?m)^\s*(?:-\s+)?uses:\s*(\S+)", path.read_text(encoding="utf-8"))
            for entry in uses:
                self.assertRegex(entry, r"^[^@]+@[0-9a-f]{40}$", f"unpinned action in {path.name}: {entry}")

    def test_smoke_build_never_publishes(self):
        text = read_workflow("macos-26-smoke.yml")
        self.assertNotIn("gh release", text)
        self.assertNotRegex(text, r"contents:\s*write")
        self.assertIn("GITHUB_SHA", text)
        self.assertIn("rev-parse HEAD", text)

    def test_no_homebrew_llvm_or_rustup_installs(self):
        for name in ("macos-26-smoke.yml", "release.yml"):
            text = read_workflow(name)
            self.assertNotIn("brew install", text)
            self.assertNotIn("brew upgrade", text)
            self.assertNotIn("rustup toolchain install", text)
            self.assertNotIn("rustup-init", text)

    def test_release_runs_only_on_manual_dispatch_from_the_default_branch(self):
        text = read_workflow("release.yml")
        self.assertRegex(text, r"(?ms)^on:\s*\n  workflow_dispatch:\s*\n\npermissions: \{\}")
        self.assertIn("Validate release ref", job_block(text, "preflight"))
        self.assertRegex(job_block(text, "build"), r"needs:\s*preflight")
        self.assertRegex(job_block(text, "release"), r"needs:\s*\[preflight, build\]")

    def test_release_build_reads_and_release_job_writes(self):
        text = read_workflow("release.yml")
        self.assertRegex(job_block(text, "build"), r"contents:\s*read")
        self.assertNotRegex(job_block(text, "build"), r"contents:\s*write")
        self.assertRegex(job_block(text, "release"), r"contents:\s*write")

    def test_release_assets_run_in_the_locked_environment(self):
        build = job_block(read_workflow("release.yml"), "build")
        self.assertIn("uv run --frozen --no-dev python scripts/release_assets.py macos", build)
        self.assertLess(build.index("astral-sh/setup-uv@"), build.index("release_assets.py"))

    def test_release_checks_the_commit_before_compiling(self):
        build = job_block(read_workflow("release.yml"), "build")
        self.assertIn("rev-parse HEAD", build)
        self.assertLess(build.index("rev-parse HEAD"), build.index("verify-preflight"))

    def test_release_tag_and_assets_follow_the_build_contract(self):
        release = job_block(read_workflow("release.yml"), "release")
        self.assertIn("RELEASE_TAG: release-${{ github.sha }}", release)
        self.assertIn("chromium-$VERSION-aarch64-apple-darwin.tar.xz", release)
        self.assertIn("chromium-$VERSION-aarch64-apple-darwin.manifest.json", release)
        self.assertIn("sha256sum -c SHA256SUMS", release)
        self.assertIn("--prerelease --verify-tag", release)
        self.assertLess(release.index("sha256sum -c SHA256SUMS"), release.index("gh release create"))
        self.assertLess(release.index("git/refs"), release.index("gh release create"))

    def test_lint_workflow_runs_make_lint(self):
        text = read_workflow("lint.yml")
        self.assertIn("run: make lint", text)
        self.assertNotIn("ubuntu-latest", text)


if __name__ == "__main__":
    unittest.main()
