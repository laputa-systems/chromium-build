#!/usr/bin/env python3
"""Read and set the PT_GNU_STACK size that musl uses as the default thread stack."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

PT_GNU_STACK = 0x6474E551
LLD_THREAD_STACK_SIZE = 8 * 1024 * 1024


def _gnu_stack_memsz_offset(data: bytes | bytearray, path: Path) -> int:
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError(f"{path} is not a little-endian ELF64 file")
    phoff = struct.unpack_from("<Q", data, 0x20)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    for index in range(phnum):
        entry = phoff + index * phentsize
        if struct.unpack_from("<I", data, entry)[0] == PT_GNU_STACK:
            return entry + 40
    raise ValueError(f"{path} has no PT_GNU_STACK header")


def thread_stack_size(path: Path) -> int:
    data = path.read_bytes()
    return struct.unpack_from("<Q", data, _gnu_stack_memsz_offset(data, path))[0]


def set_thread_stack_size(path: Path, size: int) -> None:
    data = bytearray(path.read_bytes())
    struct.pack_into("<Q", data, _gnu_stack_memsz_offset(data, path), size)
    temporary = path.with_name(f".{path.name}.stack")
    temporary.write_bytes(data)
    temporary.chmod(path.stat().st_mode)
    temporary.replace(path)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: elf_stack.py <lld>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    set_thread_stack_size(path, LLD_THREAD_STACK_SIZE)
    if thread_stack_size(path) != LLD_THREAD_STACK_SIZE:
        print(f"elf_stack: {path} did not keep the thread stack size", file=sys.stderr)
        return 1
    print(f"elf_stack: {path} thread stack size {LLD_THREAD_STACK_SIZE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
