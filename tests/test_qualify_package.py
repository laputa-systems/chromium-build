"""Package qualification requires the headed Wayland and headless browser evidence."""

import copy
import runpy
import unittest
from pathlib import Path

import runtime_install

QUALIFY = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/qualify-package.py"))
REPORT_CHECKS = QUALIFY["report_checks"]

PASSING = {
    "version": "Helium 0.20.3.1 (Chromium 156.0.8078.79)",
    "version_info": {"Browser": "Chrome/156.0.8078.79", "User-Agent": "Mozilla/5.0 Chrome/156.0.0.0"},
    "window": {"pid": 10},
    "compositor": {"pixels": {}},
    "screenshot": {"sha256": "0" * 64},
    "sandbox": {"renderers": [{"pid": 11, "no_new_privs": "1", "seccomp": "2", "nspid": ["11"]}]},
    "cleanup": {"ok": True},
    "headless": {
        "devtools_endpoint": "ws://127.0.0.1:4444/devtools/browser/x",
        "version_info": {"Browser": "Chrome/156.0.8078.79", "User-Agent": "Mozilla/5.0 HeadlessChrome/156.0.0.0"},
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
            "headless-identity": lambda report: report["headless"]["version_info"].update(Browser="HeadlessChrome/156.0.8078.79"),
            "headless-page": lambda report: report["headless"]["page"].update(evaluated={"status": "not-ready"}),
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


if __name__ == "__main__":
    unittest.main()
