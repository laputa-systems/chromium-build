"""lld gets a thread stack large enough for ThinLTO backends on musl."""

import struct
import tempfile
import unittest
from pathlib import Path

import elf_stack


def elf64_with_gnu_stack() -> bytes:
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x20, 64)
    struct.pack_into("<HH", header, 0x36, 56, 2)
    load = bytearray(56)
    struct.pack_into("<I", load, 0, 1)
    stack = bytearray(56)
    struct.pack_into("<I", stack, 0, elf_stack.PT_GNU_STACK)
    return bytes(header + load + stack)


class ThreadStackSizeTest(unittest.TestCase):
    def test_set_size_is_read_back_and_other_headers_are_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lld"
            original = elf64_with_gnu_stack()
            path.write_bytes(original)
            path.chmod(0o755)
            self.assertEqual(elf_stack.thread_stack_size(path), 0)

            elf_stack.set_thread_stack_size(path, elf_stack.LLD_THREAD_STACK_SIZE)

            self.assertEqual(elf_stack.thread_stack_size(path), elf_stack.LLD_THREAD_STACK_SIZE)
            patched = path.read_bytes()
            self.assertEqual(patched[: 64 + 56], original[: 64 + 56])
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_binary_without_gnu_stack_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lld"
            data = bytearray(elf64_with_gnu_stack())
            struct.pack_into("<H", data, 0x38, 1)
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "no PT_GNU_STACK"):
                elf_stack.set_thread_stack_size(path, elf_stack.LLD_THREAD_STACK_SIZE)


if __name__ == "__main__":
    unittest.main()
