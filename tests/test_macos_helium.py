import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MacOSHeliumInputsTest(unittest.TestCase):
    def test_shared_sources_and_complete_patch_disposition(self):
        linux = json.loads((ROOT / "config/helium.inputs.lock").read_text())
        macos = json.loads((ROOT / "config/macos-helium.inputs.lock").read_text())
        disposition = json.loads((ROOT / "config/macos-helium.patch-disposition.json").read_text())
        linux_disposition = json.loads((ROOT / "config/helium.patch-disposition.json").read_text())
        self.assertEqual(macos["chromium"]["version"], linux["chromium"]["version"])
        self.assertEqual(macos["chromium"]["source"]["filename"], f"chromium-{linux['chromium']['version']}.tar.xz")
        self.assertEqual(macos["product"]["core_revision"], linux["product"]["core_revision"])
        self.assertEqual(macos["product"]["version"], linux["product"]["version"])
        self.assertEqual([row["name"] for row in disposition["local_patches"]], linux_disposition["local_patches"])
        for row in disposition["local_patches"]:
            if row["name"].startswith("adblock-"):
                self.assertEqual(row["state"], "apply")
        self.assertIn("enable_native_adblock = true", (ROOT / "config/profiles/macos-helium-release.gn").read_text())
        self.assertEqual(macos["adblock_inputs"], linux["adblock_inputs"])
        self.assertEqual(set(macos["source_inputs"]), {"helium_core", "helium_macos", "ublock_origin"})


if __name__ == "__main__":
    unittest.main()
