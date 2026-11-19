import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class HeliumCrtTest(unittest.TestCase):
    def test_alpine_target_uses_pinned_musl_startup_objects(self):
        llvm = Path('/opt/chromium-llvm')
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            root = Path(directory)
            source = root / 'probe.c'
            output = root / 'probe'
            source.write_text(
                'static int ready;\n'
                '__attribute__((constructor)) static void init(void) { ready = 1; }\n'
                'int main(int argc, char **argv) { return !(ready && argc == 2 && argv[1][0] == 120); }\n'
            )
            for driver in ('clang', 'clang++'):
                with self.subTest(driver=driver):
                    link = subprocess.run(
                        [str(llvm / 'bin' / driver), '--target=aarch64-alpine-linux-musl',
                         '-x', 'c', '-v', str(source), '-o', str(output)],
                        text=True, capture_output=True, check=True,
                    )
                    self.assertIn('/lib/musl/Scrt1.o', link.stderr)
                    self.assertIn('/lib/musl/crti.o', link.stderr)
                    self.assertIn('/lib/musl/crtn.o', link.stderr)
                    comment = subprocess.run(
                        [str(llvm / 'bin/llvm-readelf'), '-p', '.comment', str(output)],
                        text=True, capture_output=True, check=True,
                    ).stdout
                    self.assertIn('clang version', comment)
                    self.assertNotIn('GCC:', comment)
                    subprocess.run([str(output), 'x'], check=True)


if __name__ == '__main__':
    unittest.main()
