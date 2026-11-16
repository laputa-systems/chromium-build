import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImageJobsTests(unittest.TestCase):
    def test_image_forwards_job_limit_without_changing_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / 'docker'
            docker.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == 'info':
    print('aarch64' if 'Architecture' in args[-1] else 8 * 1024**3)
elif args[0] == 'build':
    Path(os.environ['BUILD_ARGS']).write_text(json.dumps(args))
elif args[:2] == ['image', 'inspect']:
    print('image=sha256:' + '0' * 64)
else:
    sys.exit('unexpected docker command')
''')
            docker.chmod(0o755)
            environment = os.environ | {'PATH': str(root) + os.pathsep + os.environ['PATH'],
                                       'CHROMIUM_PYTHON': sys.executable,
                                       'BUILD_ARGS': str(root / 'args.json')}
            for options, expected in ((['--jobs', '2'], '2'), ([], '')):
                result = subprocess.run(['bash', str(ROOT / 'chromium-build'), '--product', 'helium',
                                         '--arch', 'arm64', 'image', *options], env=environment,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                args = json.loads((root / 'args.json').read_text())
                index = args.index(f'PURE_RUNTIME_JOBS={expected}')
                self.assertEqual(args[index - 1], '--build-arg')


if __name__ == '__main__':
    unittest.main()
