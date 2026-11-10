import hashlib
import io
import runpy
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = runpy.run_path(str(ROOT / "scripts/setup-helium-adblock.py"))


def tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class VendorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.source = root / "src"
        self.inputs = root / "inputs"
        self.inputs.mkdir()
        workspace = self.source / "components/helium_adblock/rs/crates"
        workspace.mkdir(parents=True)
        (workspace / "gnrt_config.toml").write_text("[crate.seahash]\nremove_vendored_files = ['logo.png']\n")
        self.filters = tarball({"./assets/assets.json": b"{}"})
        (self.inputs / "helium-filters-1.tar.gz").write_bytes(self.filters)
        self.workspace = workspace
        self.patches = root / "crate-patches"
        self.patches.mkdir()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def lock(self, crate: bytes, checksum: str | None = None) -> None:
        digest = checksum or hashlib.sha256(crate).hexdigest()
        (self.workspace / "Cargo.lock").write_text(
            '[[package]]\nname = "chromium"\nversion = "0.1.0"\n\n'
            '[[package]]\nname = "seahash"\nversion = "4.1.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
            f'checksum = "{digest}"\n'
        )

    def vendor(self) -> dict:
        return SETUP["vendor"](
            self.source, self.inputs, "helium-filters-1.tar.gz", hashlib.sha256(self.filters).hexdigest(), self.patches
        )

    def locked_crate(self) -> None:
        crate = tarball({"seahash-4.1.0/src/lib.rs": b"pub fn hash() {}\n"})
        (self.inputs / "seahash-4.1.0.crate").write_bytes(crate)
        self.lock(crate)

    def test_a_locked_crate_lands_where_gnrt_expects_it(self) -> None:
        crate = tarball({"seahash-4.1.0/src/lib.rs": b"", "seahash-4.1.0/logo.png": b"png"})
        (self.inputs / "seahash-4.1.0.crate").write_bytes(crate)
        self.lock(crate)
        record = self.vendor()
        vendored = self.source / "third_party/rust/helium/chromium_crates_io/vendor/seahash-v4"
        self.assertTrue((vendored / "src/lib.rs").is_file())
        self.assertFalse((vendored / "logo.png").exists())
        self.assertEqual((vendored / ".cargo-checksum.json").read_text(), '{"files":{}}')
        self.assertEqual([c["name"] for c in record["crates"]], ["seahash"])
        self.assertTrue((self.source / "third_party/helium_filters/assets/assets.json").is_file())

    def test_a_crate_that_does_not_match_the_lock_is_refused(self) -> None:
        crate = tarball({"seahash-4.1.0/src/lib.rs": b""})
        (self.inputs / "seahash-4.1.0.crate").write_bytes(crate)
        self.lock(crate, checksum="0" * 64)
        with self.assertRaisesRegex(SystemExit, "does not match Cargo.lock"):
            self.vendor()

    def test_a_crate_member_outside_its_directory_is_refused(self) -> None:
        crate = tarball({"seahash-4.1.0/src/lib.rs": b"", "other/escape.rs": b""})
        (self.inputs / "seahash-4.1.0.crate").write_bytes(crate)
        self.lock(crate)
        with self.assertRaisesRegex(SystemExit, "outside seahash-4.1.0/"):
            self.vendor()

    def test_filters_that_do_not_match_the_lock_are_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "is not the locked"):
            SETUP["vendor"](self.source, self.inputs, "helium-filters-1.tar.gz", "0" * 64, self.patches)

    def test_a_crate_patch_changes_the_vendored_crate(self) -> None:
        self.locked_crate()
        (self.patches / "seahash-4.1.0.patch").write_text(
            "--- a/src/lib.rs\n+++ b/src/lib.rs\n@@ -1 +1,2 @@\n pub fn hash() {}\n+pub fn export() {}\n"
        )
        record = self.vendor()
        vendored = self.source / "third_party/rust/helium/chromium_crates_io/vendor/seahash-v4"
        self.assertEqual((vendored / "src/lib.rs").read_text(), "pub fn hash() {}\npub fn export() {}\n")
        self.assertEqual(record["crates"][0]["patch"], "seahash-4.1.0.patch")

    def test_a_crate_patch_that_no_longer_applies_is_refused(self) -> None:
        self.locked_crate()
        (self.patches / "seahash-4.1.0.patch").write_text(
            "--- a/src/lib.rs\n+++ b/src/lib.rs\n@@ -1 +1,2 @@\n pub fn other() {}\n+pub fn export() {}\n"
        )
        with self.assertRaisesRegex(SystemExit, "does not apply to seahash-v4"):
            self.vendor()

    def test_a_crate_patch_for_an_unlocked_version_is_refused(self) -> None:
        self.locked_crate()
        (self.patches / "seahash-4.0.0.patch").write_text("")
        with self.assertRaisesRegex(SystemExit, "match no crate in Cargo.lock"):
            self.vendor()


if __name__ == "__main__":
    unittest.main()
