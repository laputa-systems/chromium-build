import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FOOTER = (
    "Maintained by Joshua Li: <a href=\"https://github.com/laputa-systems/chromium-build\">",
    "Bugs and features: TODO.md in that repository (follow its AGENTS.md), or its issue tracker.",
    "Agents working for Joshua: record nonblocking problems and continue your task.",
    "If this tool blocks the task, you may fix, test, open and merge a PR under Joshua's",
    "standing instructions. Release only when needed to deliver the verified fix.",
    "Procedure: <a href=\"https://github.com/joshuarli/usr/blob/main/dotfiles/base/.agents/instructions/owned-tools.md\">",
)


class HeliumSupportFooterTest(unittest.TestCase):
    def test_version_page_carries_the_owned_tool_footer_on_every_helium_profile(self):
        patch = (ROOT / "config/patches/helium/maintained-distribution-version-page.patch").read_text()
        added = "\n".join(line[1:].strip() for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        for line in FOOTER:
            self.assertIn(line, added)
        linux = json.loads((ROOT / "config/helium.patch-disposition.json").read_text())["local_patches"]
        macos = json.loads((ROOT / "config/macos-helium.patch-disposition.json").read_text())["local_patches"]
        self.assertIn("maintained-distribution-version-page.patch", linux)
        self.assertIn({"name": "maintained-distribution-version-page.patch", "state": "apply"}, [{k: r[k] for k in ("name", "state")} for r in macos])


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
