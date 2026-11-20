"""Package qualification requires the headed Wayland and headless browser evidence."""

import copy
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import linux_product
import runtime_install
from test_helium_runtime import HOST_ARCH, INSTALL, fake_home, file_digest, make_archive

QUALIFY = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/qualify-package.py"))
REPORT_CHECKS = QUALIFY["report_checks"]
HOST_HEADLESS_CHECKS = QUALIFY["host_headless_checks"]

PASSING = {
    "version": "Helium 0.20.3.1 (Chromium 156.0.8078.79)",
    "version_info": {"Browser": "Helium/0.20.3.1", "User-Agent": "Mozilla/5.0 Chrome/156.0.0.0"},
    "window": {"pid": 10},
    "compositor": {"pixels": {}},
    "screenshot": {"sha256": "0" * 64},
    "sandbox": {"renderers": [{"pid": 11, "no_new_privs": "1", "seccomp": "2", "nspid": ["11"]}]},
    "cleanup": {"ok": True},
    "headless": {
        "devtools_endpoint": "ws://127.0.0.1:4444/devtools/browser/x",
        "version_info": {"Browser": "Helium/0.20.3.1", "User-Agent": "Mozilla/5.0 HeadlessChrome/156.0.0.0"},
        "page": {"evaluated": {"status": "ready", "layout": True}},
        "sandbox": {"renderers": [{"pid": 21, "no_new_privs": "1", "seccomp": "2", "nspid": ["21", "2"]}]},
        "exit": {"returncode": 0, "survivors": []},
    },
}


class ReportChecksTest(unittest.TestCase):
    def test_complete_headed_and_headless_report_qualifies(self):
        self.assertTrue(all(REPORT_CHECKS(PASSING).values()))

    def test_headless_failures_block_qualification(self):
        cases = {
            "headless-sandbox-renderer": lambda report: report["headless"]["sandbox"]["renderers"][0].update(nspid=["21"]),
            "headless-clean-exit": lambda report: report["headless"]["exit"].update(survivors=[22]),
            "headless-identity": lambda report: report["headless"]["version_info"].update(Browser="Chrome/156.0.8078.79"),
            "headless-page": lambda report: report["headless"]["page"].update(evaluated={"status": "not-ready"}),
            "headless-user-agent": lambda report: report["headless"]["version_info"].update({"User-Agent": "Mozilla/5.0 Helium/0.20.3.1"}),
        }
        for check, damage in cases.items():
            report = copy.deepcopy(PASSING)
            damage(report)
            with self.subTest(check), self.assertRaisesRegex(runtime_install.InstallError, check):
                REPORT_CHECKS(report)

    def test_wayland_only_report_does_not_qualify(self):
        report = copy.deepcopy(PASSING)
        del report["headless"]
        with self.assertRaisesRegex(runtime_install.InstallError, "headless-devtools"):
            REPORT_CHECKS(report)


HOST_PASSING = {
    "status": "complete",
    "scope": "headless",
    "product": "helium",
    "product_version": "0.20.3.1",
    "chromium_version": "156.0.8078.79",
    "version": PASSING["version"],
    "headless": copy.deepcopy(PASSING["headless"]),
    "headless_load": {
        "instances": 3,
        "seconds": 180.0,
        "iterations": 90,
        "crashes": 0,
        "sigsys_exits": 0,
        "results": [{"iterations": 30, "crashes": []} for _ in range(3)],
    },
}


class HostHeadlessChecksTest(unittest.TestCase):
    def test_complete_host_headless_report_qualifies(self):
        self.assertTrue(all(HOST_HEADLESS_CHECKS(HOST_PASSING, "0.20.3.1").values()))

    def test_host_headless_failures_block_qualification(self):
        cases = {
            "host-headless-sandbox-renderer": lambda report: report["headless"]["sandbox"]["renderers"][0].update(seccomp="0"),
            "host-headless-clean-exit": lambda report: report["headless"]["exit"].update(returncode=1),
            "host-devtools-browser": lambda report: report["headless"]["version_info"].update(Browser="Helium/0.20.3.0"),
            "host-executable-identity": lambda report: report.update(version="Chromium 156.0.8078.79"),
            "host-headless-load": lambda report: report["headless_load"].update(sigsys_exits=1, crashes=1),
        }
        for check, damage in cases.items():
            report = copy.deepcopy(HOST_PASSING)
            damage(report)
            with self.subTest(check), self.assertRaisesRegex(runtime_install.InstallError, check):
                HOST_HEADLESS_CHECKS(report, "0.20.3.1")

    def test_host_report_without_concurrent_load_does_not_qualify(self):
        for damage in (
            lambda report: report.pop("headless_load"),
            lambda report: report["headless_load"].update(results=[{"iterations": 30, "crashes": []}]),
            lambda report: report["headless_load"]["results"][1].update(iterations=0),
        ):
            report = copy.deepcopy(HOST_PASSING)
            damage(report)
            with self.assertRaisesRegex(runtime_install.InstallError, "host-headless-load"):
                HOST_HEADLESS_CHECKS(report, "0.20.3.1")

    def test_headed_report_is_not_a_host_headless_report(self):
        report = copy.deepcopy(HOST_PASSING)
        del report["scope"]
        with self.assertRaisesRegex(runtime_install.InstallError, "not a complete Helium headless result"):
            HOST_HEADLESS_CHECKS(report, "0.20.3.1")

    def test_other_version_does_not_qualify(self):
        with self.assertRaisesRegex(runtime_install.InstallError, "does not match the archive"):
            HOST_HEADLESS_CHECKS(HOST_PASSING, "0.20.3.2")


class ChromiumQualificationTest(unittest.TestCase):
    def test_chromium_archive_and_host_report_are_qualified(self):
        desktop = linux_product.desktop("chromium", "chromium-wayland-release")
        self.assertIsNotNone(desktop)
        root_path = Path(__file__).resolve().parents[1]
        version = json.loads((root_path / "config/inputs.lock").read_text())["chromium"]["version"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = make_archive(root, version, product="chromium", acceptance_product="chromium")
            report: dict[str, Any] = copy.deepcopy(PASSING)
            report.update(status="complete", product="chromium", product_version=version, version=f"Chromium {version}")
            report["version_info"]["Browser"] = f"Chrome/{version}"
            report["headless"]["version_info"]["Browser"] = f"Chrome/{version}"
            host: dict[str, Any] = copy.deepcopy(HOST_PASSING)
            host.update(product="chromium", product_version=version, version=f"Chromium {version}", headless=report["headless"])
            report_path, host_path = root / "report.json", root / "host.json"
            report_path.write_text(json.dumps(report))
            host_path.write_text(json.dumps(host))
            result = subprocess.run([sys.executable, str(root_path / "scripts/qualify-package.py"), "--archive", str(archive), "--report", str(report_path), "--host-headless-report", str(host_path)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            acceptance = json.loads(archive.with_name(archive.name + ".acceptance.json").read_text())
            self.assertEqual(acceptance["product"], "chromium")
            self.assertEqual(acceptance["product_version"], version)
            self.assertEqual(acceptance["architecture"], HOST_ARCH)
            self.assertEqual(acceptance["archive_sha256"], file_digest(archive))
            self.assertTrue(all(acceptance["host_headless"]["checks"].values()))


class ChromiumInstallTest(unittest.TestCase):
    def test_chromium_extract_install_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_archive(root / "first", "156.0.8078.79", product="chromium", acceptance_product="chromium")
            second = make_archive(root / "second", "156.0.8078.80", product="chromium", acceptance_product="chromium")
            destination = root / "extracted"
            installer = Path(__file__).resolve().parents[1] / "scripts/install-runtime.py"
            extract = subprocess.run([sys.executable, str(installer), "--archive", str(first), "--verify-extract", str(destination)], capture_output=True, text=True, check=False)
            self.assertEqual(extract.returncode, 0, extract.stderr)
            self.assertTrue((destination / "chrome").is_file())
            self.assertTrue((destination / "chromium").is_file())
            home, prefix, bin_dir = fake_home(root)
            installed = subprocess.run([sys.executable, str(installer), "--archive", str(first), "--home", str(home)], capture_output=True, text=True, check=False)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            prefix = home / ".local/opt/chromium-wayland-release"
            first_slot = prefix.resolve()
            self.assertEqual((bin_dir / "chromium").resolve(), (prefix / "chromium").resolve())
            INSTALL["install"](second, prefix, bin_dir, home, None)
            INSTALL["rollback"](prefix, bin_dir, home, None)
            self.assertEqual(prefix.resolve(), first_slot)

    def test_product_mismatches_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = make_archive(root, product="chromium", acceptance_product="helium")
            with self.assertRaisesRegex(INSTALL["InstallError"], "not helium"), INSTALL["verified_archive"](archive):
                pass
            archive = make_archive(root / "manifest", product="helium", acceptance_product="helium")
            sidecar = archive.with_name(archive.name + ".acceptance.json")
            report = json.loads(sidecar.read_text())
            report["product"] = "chromium"
            sidecar.write_text(json.dumps(report))
            renamed = archive.with_name(archive.name.replace("helium-", "chromium-", 1))
            archive.rename(renamed)
            archive.with_name(archive.name + ".sha256").rename(renamed.with_name(renamed.name + ".sha256"))
            report["archive"] = renamed.name
            renamed.with_name(renamed.name + ".acceptance.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(INSTALL["InstallError"], "manifest product"), INSTALL["verified_archive"](renamed):
                pass

if __name__ == "__main__":
    unittest.main()
