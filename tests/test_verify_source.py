"""Gate C toolchain contract: approved musl Rust targets."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_source", ROOT / "scripts/verify-source.py")
assert spec is not None and spec.loader is not None
verify_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_source)


def selection(triple):
    return {
        "status": "complete",
        "network": "none",
        "chromium_prebuilt_selected": True,
        "sysroot_selected": False,
        "forbidden_selected": [],
        **{key: "/opt/chromium-llvm/bin/clang" for key in ("compiler", "cxx", "linker", "ar", "rust_linker")},
        "rust_target_triple": triple,
    }


class ToolchainChecks(unittest.TestCase):
    def check(self, triple):
        with tempfile.TemporaryDirectory(dir=ROOT / ".work/tmp") as scratch:
            path = Path(scratch) / "selection.json"
            path.write_text(json.dumps(selection(triple)), encoding="utf-8")
            verify_source.toolchain_checks(SimpleNamespace(toolchain_selection=path))

    def test_accepts_alpine_rust_targets_used_by_the_amd64_line(self):
        for triple in ("x86_64-alpine-linux-musl", "aarch64-alpine-linux-musl", "aarch64-unknown-linux-musl"):
            self.check(triple)

    def test_rejects_glibc_rust_target(self):
        with self.assertRaisesRegex(verify_source.Failure, "approved musl target"):
            self.check("x86_64-unknown-linux-gnu")


if __name__ == "__main__":
    unittest.main()
