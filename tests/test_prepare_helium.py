"""arm64 preparation leaves no foreign-architecture or glibc host binaries behind."""

import runpy
import tempfile
import unittest
from pathlib import Path

PREPARE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/prepare-helium.py"))
REMOVE_FOREIGN_HOST_BINARIES = PREPARE["remove_foreign_host_binaries"]


def elf(machine: int) -> bytes:
    return b"\x7fELF" + bytes(14) + machine.to_bytes(2, "little") + bytes(44)


class RemoveForeignHostBinariesTest(unittest.TestCase):
    def test_arm64_keeps_native_binaries_and_drops_x86_and_glibc_sysroots(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            files = {
                "third_party/rust-toolchain/bin/rustc": elf(0xB7),
                "buildtools/linux64-format/clang-format": elf(0x3E),
                "third_party/typescript/linux-amd64/src/lib/tsc": elf(0x3E),
                "build/linux/debian_bullseye_arm64-sysroot/lib/libc.so.6": elf(0xB7),
                "third_party/typescript/linux-amd64/src/lib/lib.d.ts": b"declare var x: number;\n",
            }
            for relative, content in files.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            report = REMOVE_FOREIGN_HOST_BINARIES(source, "arm64")

            remaining = sorted(str(path.relative_to(source)) for path in source.rglob("*") if path.is_file())
            self.assertEqual(
                remaining,
                ["third_party/rust-toolchain/bin/rustc", "third_party/typescript/linux-amd64/src/lib/lib.d.ts"],
            )
            self.assertFalse((source / "build/linux/debian_bullseye_arm64-sysroot").exists())
            self.assertEqual(sum(report["removed"].values()), 3)


if __name__ == "__main__":
    unittest.main()
