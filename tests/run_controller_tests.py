import platform
import sys
import unittest
from pathlib import Path

MACOS = {"test_macos_backend", "test_release_assets", "test_verify_macos_ci"}
LINUX_BUILDER = {"test_helium_crt", "test_helium_runtime", "test_helium_wayland"}


def main() -> int:
    group = sys.argv[1]
    expected = {"macos": "Darwin", "linux": "Linux", "linux-builder": "Linux"}
    if group not in expected or platform.system() != expected[group]:
        raise SystemExit(f"invalid host for controller test group: {group}")
    directory = Path(__file__).resolve().parent
    modules = {path.stem for path in directory.glob("test_*.py")}
    selected = {
        "macos": modules - LINUX_BUILDER,
        "linux": modules - MACOS - LINUX_BUILDER,
        "linux-builder": LINUX_BUILDER if platform.machine() == "aarch64" else LINUX_BUILDER - {"test_helium_crt"},
    }[group]
    if not MACOS | LINUX_BUILDER <= modules:
        raise SystemExit("controller test group references missing modules")
    print(f"{group}: {', '.join(sorted(selected))}", flush=True)
    suite = unittest.defaultTestLoader.loadTestsFromNames(sorted(selected))
    return not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(main())
