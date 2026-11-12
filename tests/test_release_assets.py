"""Release archive assembly for the macOS package."""

import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import macos_backend
import release_assets

TARGET = "aarch64-apple-darwin"
REVISION = "0123456789abcdef0123456789abcdef01234567"


def make_app(root: Path) -> Path:
    app = root / "Chromium.app"
    executable = app / "Contents/MacOS/Chromium"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (app / "Contents/Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict><key>CFBundleExecutable</key><string>Chromium</string>'
        "<key>CFBundleIdentifier</key><string>test.chromium</string></dict></plist>\n",
        encoding="utf-8",
    )
    (app / "Contents/Current").symlink_to("MacOS")
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)], check=True, capture_output=True)
    return app


@unittest.skipUnless(sys.platform == "darwin", "release assembly needs ditto and codesign")
class ReleaseAssetsTests(unittest.TestCase):
    def test_macos_assets_follow_the_build_contract(self):
        version = json.loads(release_assets.MACOS_LOCK.read_text(encoding="utf-8"))["chromium"]["version"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            (work / "metadata").mkdir(parents=True)
            (work / "release").mkdir()
            app = make_app(root / "build")
            package = work / "release" / "Chromium.zip"
            subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(package)], check=True)
            fingerprint = macos_backend.bundle_fingerprint(app)
            report = {"status": "complete", "archive": str(package), "fingerprint": fingerprint, "acceptance": "skipped"}
            (work / "metadata/package-macos.json").write_text(json.dumps(report), encoding="utf-8")
            (work / "release/release-manifest.json").write_text(json.dumps({"xcode": "26", "extra": 1}), encoding="utf-8")
            output = root / "assets"

            release_assets.assemble_macos(work, output, REVISION)

            stem = f"chromium-{version}-{TARGET}"
            self.assertEqual(sorted(path.name for path in output.iterdir()), [f"{stem}.manifest.json", f"{stem}.tar.xz"])
            manifest = json.loads((output / f"{stem}.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                manifest,
                {
                    "schema_version": 1,
                    "product": "chromium",
                    "version": version,
                    "target": TARGET,
                    "source_revision": REVISION,
                    "sources_lock_sha256": release_assets.sha256_file(release_assets.SOURCES_LOCK),
                    "acceptance": "skipped",
                    "payload_sha256": fingerprint,
                    "xcode": "26",
                },
            )
            with tarfile.open(output / f"{stem}.tar.xz", "r:xz") as bundle:
                names = bundle.getnames()
            self.assertIn(f"{stem}/Chromium.app/Contents/MacOS/Chromium", names)
            self.assertIn(f"{stem}/Chromium.app/Contents/Current", names)

    def test_incomplete_package_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "metadata").mkdir()
            (work / "metadata/package-macos.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "not complete"):
                release_assets.assemble_macos(work, work / "assets", REVISION)

    def test_revision_must_be_a_full_commit(self):
        with self.assertRaisesRegex(SystemExit, "full lowercase commit"):
            release_assets.assemble_macos(Path("/nonexistent"), Path("/nonexistent"), "main")


if __name__ == "__main__":
    unittest.main()
