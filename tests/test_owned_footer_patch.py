"""Check both Chromium preparation paths select the same About-page patch."""

import runpy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import macos_backend


class OwnedFooterPatchTest(unittest.TestCase):
    def test_linux_and_macos_select_the_same_patch(self):
        driver = runpy.run_path(str(ROOT / "scripts" / "prepare-source-driver.py"))
        relative = "macos-native/004-owned-footer.patch"
        self.assertEqual(driver["LOCAL_PATCHES"].count(relative), 1)
        layout = macos_backend.WorkLayout(ROOT / ".work" / "macos-arm64", ROOT)
        names = [entry["name"] for entry in macos_backend.local_overlay_identity(layout)["patches"]]
        self.assertEqual(names.count(Path(relative).name), 1)
        self.assertTrue((ROOT / "config" / "patches" / relative).is_file())
