"""Permissions for verified inputs consumed by the unprivileged browser tests."""

import hashlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FETCH = runpy.run_path(str(ROOT / "scripts/fetch-inputs.py"))
FETCH_ONE = FETCH["fetch_one"]
LOCKED_INPUTS = FETCH["locked_inputs"]


class FetchInputsTest(unittest.TestCase):
    def test_reused_verified_extension_is_readable_by_browser_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "extension.zip"
            content = b"verified extension archive"
            archive.write_bytes(content)
            archive.chmod(0o600)
            item = {
                "name": "extension",
                "filename": archive.name,
                "size": len(content),
                "sha512": hashlib.sha512(content).hexdigest(),
                "url": "https://example.invalid/extension.zip",
            }

            self.assertEqual(FETCH_ONE(item, root)["action"], "reused")
            self.assertEqual(archive.stat().st_mode & 0o444, 0o444)

    def test_only_arm64_helium_fetches_native_host_tools(self):
        lock = json.loads((ROOT / "config/helium.inputs.lock").read_text(encoding="utf-8"))
        arm64 = {item["name"]: item for item in LOCKED_INPUTS(lock, "arm64")}
        amd64 = {item["name"] for item in LOCKED_INPUTS(lock, "amd64")}

        self.assertEqual(arm64["rust_toolchain"]["destination"], "third_party/rust-toolchain")
        self.assertTrue(arm64["rust_toolchain"]["filename"].endswith("-aarch64-linux-musl.tar.xz"))
        self.assertEqual(set(arm64) - amd64, {"rust_toolchain", "typescript_native", "esbuild_native"})

    def test_architecture_inputs_reject_an_unlisted_architecture(self):
        lock = json.loads((ROOT / "config/helium.inputs.lock").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(RuntimeError, "no architecture inputs"):
            LOCKED_INPUTS(lock, "")
