#!/usr/bin/env python3
"""Policy checks for the workflow files."""

import json
import os
import re
import subprocess
import tempfile
import textwrap
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
    @unittest.skipUnless(os.environ.get("CHROMIUM_RELEASE_TEST_WORK_VOLUME"), "requires a packaged Helium CI volume and Docker runner")
    def test_linux_release_qualifies_a_real_archive_and_returns_runner_ownership(self):
        text = job_block(read_workflow("release-linux.yml"), "build")
        step = text.split("      - name: Qualify the archive\n", 1)[1].split("      - name:", 1)[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        workspace = WORKFLOWS.parents[1]
        with tempfile.TemporaryDirectory(prefix="release-qualification-", dir=os.environ["RUNNER_TEMP"]) as directory:
            environment = os.environ | {"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": directory,
                                        "CHROMIUM_WORK_VOLUME": os.environ["CHROMIUM_RELEASE_TEST_WORK_VOLUME"]}
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=workspace,
                                    env=environment, capture_output=True, text=True, check=False, timeout=600)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            qualify = Path(directory) / "qualify"
            reports = list(qualify.glob("*.acceptance.json"))
            self.assertEqual(len(reports), 1)
            acceptance = json.loads(reports[0].read_text())
            self.assertTrue(all(value is True for value in acceptance["checks"].values()))
            self.assertTrue(all(value is True for value in acceptance["host_headless"]["checks"].values()))
            self.assertIs(acceptance["host_headless"]["checks"]["host-headless-load"], True)
            for path in [qualify, *qualify.rglob("*")]:
                self.assertEqual(path.lstat().st_uid, os.getuid(), str(path))

    def test_linux_release_replaces_its_current_workspace_and_checks_out_the_requested_commit(self):
        text = job_block(read_workflow("release-linux.yml"), "build")
        clone = text.split("      - name: Clone the exact dispatched commit\n", 1)[1].split("      - name:", 1)[0]
        script = textwrap.dedent(clone.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "--allow-empty", "-m", "fixture"], check=True, capture_output=True)
            sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            subprocess.run(["git", "clone", "--bare", str(source), str(root / "origin.git")], check=True, capture_output=True)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "stale").write_text("old checkout")
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            environment = os.environ | {"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(runner_temp),
                                        "GITHUB_SERVER_URL": root.as_uri(), "GITHUB_REPOSITORY": "origin", "GITHUB_SHA": sha}
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=workspace,
                                    env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((workspace / "stale").exists())
            self.assertEqual(subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip(), sha)

    def test_linux_release_notes_include_checksums_and_install_commands(self):
        text = job_block(read_workflow("release-linux.yml"), "publish")
        notes = text.split("      - name: Write release notes\n", 1)[1].split("      - name:", 1)[0]
        script = textwrap.dedent(notes.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            archive = "helium-0.20.3.1-r6-linux-x86_64-musl.tar.xz"
            (root / "dist" / archive).write_bytes(b"fixture archive")
            sha = "1" * 40
            environment = os.environ | {"RUNNER_TEMP": str(root), "ARCHIVE": archive,
                                        "RELEASE_TAG": "helium-0.20.3.1-r6", "GITHUB_SHA": sha}
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=root,
                                    env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            body = (root / "release-notes.md").read_text()
            self.assertIn(sha, body)
            self.assertIn("docs/linux.md", body)
            self.assertIn(f"sha256sum -c {archive}.sha256", body)
            self.assertIn(f"./{archive[:-7]}/helium-wrapper --version", body)

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

    def test_ci_workflow_runs_make_lint_and_test(self):
        text = read_workflow("ci.yml")
        self.assertIn("run: make lint", text)
        self.assertIn("run: make test", text)
        self.assertNotIn("ubuntu-latest", text)


if __name__ == "__main__":
    unittest.main()
