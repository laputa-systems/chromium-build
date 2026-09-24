"""Permissions for verified inputs consumed by the unprivileged browser tests."""

import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest


FETCH_ONE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/fetch-inputs.py")
)["fetch_one"]


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
