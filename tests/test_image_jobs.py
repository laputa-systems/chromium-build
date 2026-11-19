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
    print(os.environ['DOCKER_ARCH'] if 'Architecture' in args[-1] else 8 * 1024**3)
elif args[0] == 'build':
    Path(os.environ['BUILD_ARGS']).write_text(json.dumps(args))
elif args[:2] == ['image', 'inspect']:
    print('image=sha256:' + '0' * 64)
else:
    sys.exit('unexpected docker command')
''')
            docker.chmod(0o755)
            python = root / 'python'
            python.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
if Path(sys.argv[1]).name == 'fetch-inputs.py':
    Path(os.environ['APK_FETCH_ARGS']).write_text(json.dumps(sys.argv[2:]))
    assert os.environ['NETWORK_MODE'] == 'fetch'
    print(os.environ['APK_CONTEXT'])
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
''')
            python.chmod(0o755)
            apk_context = root / 'verified apks'
            apk_context.mkdir()
            environment = os.environ | {'PATH': str(root) + os.pathsep + os.environ['PATH'],
                                       'CHROMIUM_PYTHON': str(python),
                                       'BUILD_ARGS': str(root / 'args.json'),
                                       'APK_FETCH_ARGS': str(root / 'apk-fetch.json'),
                                       'APK_CONTEXT': str(apk_context)}
            for product, arch, package_lock, options, expected in (
                ('helium', 'arm64', 'helium.packages.arm64.lock', ['--jobs', '2'], '2'),
                ('helium', 'arm64', 'helium.packages.arm64.lock', [], ''),
                ('chromium', 'amd64', 'packages.amd64.lock', [], ''),
            ):
                result = subprocess.run(['bash', str(ROOT / 'chromium-build'), '--product', product,
                                         '--arch', arch, 'image', *options],
                                        env=environment | {'DOCKER_ARCH': arch},
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                args = json.loads((root / 'args.json').read_text())
                index = args.index(f'PURE_RUNTIME_JOBS={expected}')
                self.assertEqual(args[index - 1], '--build-arg')
                context = args.index('--build-context')
                self.assertEqual(args[context + 1], f'locked-apks={apk_context}')
                self.assertIn(f'PACKAGE_LOCK=config/{package_lock}', args)
                self.assertEqual(json.loads((root / 'apk-fetch.json').read_text()), [
                    '--lock', str(ROOT / 'config' / package_lock),
                    '--inputs-root', str(ROOT / '.work/cache' / f'{product}-apks'),
                    '--builder-apks', arch,
                ])


if __name__ == '__main__':
    unittest.main()
