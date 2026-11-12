"""The root sources.lock.json must agree with the per-product input locks."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import sources_lock

ROOT = Path(__file__).resolve().parents[1]


def copy_inputs(destination: Path) -> None:
    for relative in (*sources_lock.LOCKS.values(), sources_lock.RUST_TOOLCHAIN_SCRIPT, sources_lock.SOURCES_LOCK):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)


class SourcesLockTests(unittest.TestCase):
    def test_sources_lock_matches_the_input_locks(self):
        self.assertEqual(sources_lock.check(ROOT), [])

    def test_an_unpinned_or_insecure_input_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_inputs(root)
            path = root / sources_lock.SOURCES_LOCK
            document = json.loads(path.read_text(encoding="utf-8"))
            name = document["inputs"][0]["name"]
            document["inputs"][0]["sha256"] = "ABC"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(sources_lock.check(root), [f"input {name!r}: sha256 must be 64 lowercase hex digits"])
            document["inputs"][0]["sha256"] = "0" * 64
            document["inputs"][0]["url"] = "http://example.com/input"
            path.write_text(json.dumps(document), encoding="utf-8")
            (problem,) = sources_lock.check(root)
            self.assertIn("https://", problem)

    def test_a_changed_digest_in_an_input_lock_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_inputs(root)
            lock = root / "config/macos.inputs.lock"
            document = json.loads(lock.read_text(encoding="utf-8"))
            document["chromium"]["source"]["sha512"] = "0" * 128
            lock.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(
                sources_lock.check(root),
                ["macos/chromium/source: sha512 differs from config/macos.inputs.lock"],
            )

    def test_an_input_missing_from_sources_lock_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_inputs(root)
            path = root / sources_lock.SOURCES_LOCK
            document = json.loads(path.read_text(encoding="utf-8"))
            document["inputs"] = [item for item in document["inputs"] if item["name"] != "macos/ungoogled/source"]
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(
                sources_lock.check(root),
                ["macos/ungoogled/source: pinned by config/macos.inputs.lock but absent from sources.lock.json"],
            )

    def test_write_requires_fetch_for_a_new_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_inputs(root)
            (root / sources_lock.SOURCES_LOCK).unlink()
            with self.assertRaisesRegex(SystemExit, "rerun with --fetch"):
                sources_lock.write(root, fetch=False)


if __name__ == "__main__":
    unittest.main()
