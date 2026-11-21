"""The policy checks the macOS release workflow runs between build phases."""

import contextlib
import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import macos_backend
import verify_macos_ci
from script_support import JsonObject

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def doctor_report() -> JsonObject:
    return {
        "status": "complete",
        "preflight_scope": "host-only-full-build",
        "checks": {
            "macos": {"version": "26.1"},
            "xcode": {"sdk_version": "26.0"},
            "llvm": {
                "source": "bundled",
                "selection_reason": "bundled-chromium-llvm-only",
                "tools": {name: "/bin/" + name for name in ("llvm-ar", "llvm-nm", "llvm-objcopy", "llvm-strip", "llvm-lipo")},
            },
            "rust": {
                "status": "complete",
                "toolchain": macos_backend.RUST_TOOLCHAIN,
                "components": ["rust-src", "llvm-tools-preview"],
            },
            "compiler_cache": {"status": "disabled"},
            "filesystem": {"minimum_free_bytes": 80 * GIB},
        },
        "references": {"shadowdriver": {"status": "skipped"}, "ublock": {"status": "skipped"}},
    }


class PolicyTestCase(unittest.TestCase):
    def setUp(self):
        work_area = ROOT / ".work" / "tmp"
        work_area.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="verify-macos-ci-", dir=work_area))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_check(self, *arguments: object) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(verify_macos_ci.main([str(argument) for argument in arguments]), 0)
        return output.getvalue().strip()

    def assertRejected(self, *arguments: object, message: str, report: object = None) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_check(*arguments)
        self.assertPolicyFailure(raised.exception, message, report)

    def assertPolicyFailure(self, error: SystemExit, message: str, report: object = None) -> None:
        text = str(error)
        if message.startswith("^"):
            self.assertRegex(text, message)
            return
        expected = f"macOS CI policy check failed: {message}"
        if report is not None:
            expected += "\nreport=" + json.dumps(report, indent=2, sort_keys=True)
        self.assertEqual(text, expected)


class InputLockTests(PolicyTestCase):
    def lock(self) -> JsonObject:
        digest = "ab" * 64
        return {
            "chromium": {"version": "156.0.1.2", "source": {"filename": "chromium.tar.xz", "sha512": digest}},
            "ungoogled": {"source": {"filename": "ungoogled.tar.gz", "sha512": "cd" * 64}},
            "test_inputs": {"ublock_origin": {"filename": "ublock.zip", "sha512": "ef" * 64}},
        }

    def test_cache_key_joins_every_locked_input_and_names_its_scope(self):
        lock = write_json(self.root / "lock.json", self.lock())
        self.assertEqual(
            self.run_check("cache-key", "--lock", lock),
            "macos-26-arm64-locked-inputs-no-test-inputs-chromium-chromium.tar.xz-" + "ab" * 64
            + "-ungoogled-ungoogled.tar.gz-" + "cd" * 64,
        )
        self.assertEqual(
            self.run_check("cache-key", "--lock", lock, "--include-test-inputs"),
            "macos-26-arm64-locked-inputs-with-test-inputs-chromium-chromium.tar.xz-" + "ab" * 64
            + "-ungoogled-ungoogled.tar.gz-" + "cd" * 64 + "-ublock-origin-ublock.zip-" + "ef" * 64,
        )

    def test_cache_key_rejects_a_missing_or_malformed_digest(self):
        for digest in ("", "AB" * 64, "ab" * 63):
            document = self.lock()
            document["ungoogled"]["source"]["sha512"] = digest
            lock = write_json(self.root / "lock.json", document)
            self.assertRejected("cache-key", "--lock", lock, message="ungoogled has no valid SHA-512 lock digest")
        del document["ungoogled"]["source"]["sha512"]
        lock = write_json(self.root / "lock.json", document)
        self.assertRejected("cache-key", "--lock", lock, message="ungoogled has no valid SHA-512 lock digest")

    def test_unreadable_or_non_object_locks_are_rejected(self):
        self.assertRejected("cache-key", "--lock", self.root / "missing.json", message=f"^{re.escape('macOS input lock is not readable JSON at ' + str(self.root / 'missing.json'))}")
        lock = write_json(self.root / "list.json", [])
        self.assertRejected("cache-key", "--lock", lock, message=f"^macOS input lock must be a JSON object: {re.escape(str(lock))}$")

    def test_chromium_version_prints_the_locked_version_and_requires_one(self):
        lock = write_json(self.root / "lock.json", self.lock())
        self.assertEqual(self.run_check("chromium-version", "--lock", lock), "156.0.1.2")
        for document in ({}, {"chromium": {}}, {"chromium": {"version": ""}}, {"chromium": {"version": 156}}):
            lock = write_json(self.root / "bad.json", document)
            self.assertRejected("chromium-version", "--lock", lock, message="macOS lock has no Chromium version")

    def test_every_phase_report_names_itself_when_it_cannot_be_read(self):
        metadata = self.root / "metadata"
        for command, report, label in (
            ("verify-preflight", "doctor.json", "doctor report"),
            ("verify-configure", "configure-macos.json", "configure report"),
            ("verify-cache-disabled", "compiler-cache-stats.json", "compiler cache report"),
            ("verify-package", "package-macos.json", "package report"),
        ):
            with self.subTest(command):
                self.assertRejected(
                    command, "--work-root", self.root,
                    message=f"^{re.escape(f'{label} is not readable JSON at {metadata / report}: ')}",
                )
                write_json(metadata / report, [])
                self.assertRejected(
                    command, "--work-root", self.root,
                    message=f"^{re.escape(f'{label} must be a JSON object: {metadata / report}')}$",
                )
                (metadata / report).unlink()

    def test_chromium_version_names_the_lock_it_cannot_read(self):
        missing = self.root / "missing.json"
        self.assertRejected(
            "chromium-version", "--lock", missing, message=f"^{re.escape(f'macOS input lock is not readable JSON at {missing}: ')}"
        )

    def test_the_committed_macos_lock_yields_a_cache_key(self):
        key = self.run_check("cache-key", "--lock", ROOT / "config" / "macos.inputs.lock")
        self.assertTrue(key.startswith("macos-26-arm64-locked-inputs-no-test-inputs-chromium-chromium-"))


class LlvmTests(PolicyTestCase):
    def clang(self, version: str, status: int = 0, diagnostic: str = "") -> Path:
        path = self.root / "clang"
        path.write_text(
            f"#!/bin/sh\n[ \"$1\" = --version ] || exit 64\nprintf '{diagnostic}' >&2\n"
            f"echo 'clang version {version}'\nexit {status}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def test_version_at_or_above_the_minimum_is_printed(self):
        self.assertEqual(self.run_check("verify-llvm", "--clang", self.clang("24.0.1"), "--minimum", "24.0.1"), "24.0.1")
        self.assertEqual(self.run_check("verify-llvm", "--clang", self.clang("25.1"), "--minimum", "24.9.9"), "25.1.0")
        self.assertEqual(self.run_check("verify-llvm", "--clang", self.clang("24.0.0"), "--minimum", "24.0"), "24.0.0")

    def test_older_unparseable_failing_or_missing_clang_is_rejected(self):
        self.assertRejected("verify-llvm", "--clang", self.clang("23.9.9"), "--minimum", "24.0.0", message="LLVM (23, 9, 9) is older than (24, 0, 0)")
        self.assertRejected("verify-llvm", "--clang", self.clang("unknown"), "--minimum", "24", message="LLVM clang version is not parseable: clang version unknown")
        self.assertRejected(
            "verify-llvm", "--clang", self.clang("24.0.0", status=1, diagnostic="boom\\n"), "--minimum", "24",
            message="LLVM clang failed: boom",
        )
        self.assertRejected(
            "verify-llvm", "--clang", self.clang("24.0.0", status=1, diagnostic="\\377"), "--minimum", "24",
            message="LLVM clang failed: \ufffd",
        )
        self.assertRejected(
            "verify-llvm", "--clang", self.clang("24.0.0"), "--minimum", "junk", message="^could not parse a version from 'junk'$"
        )
        self.assertRejected("verify-llvm", "--clang", self.root / "absent", "--minimum", "24", message="^could not execute LLVM clang: ")


class PreflightTests(PolicyTestCase):
    def verify(self, report: JsonObject, *extra: object) -> str:
        write_json(self.root / "metadata" / "doctor.json", report)
        return self.run_check("verify-preflight", "--work-root", self.root, *extra)

    def test_a_complete_host_only_report_passes(self):
        self.assertEqual(self.verify(doctor_report()), "macOS full-build preflight policy: passed")

    def test_each_policy_violation_is_rejected(self):
        cases = {
            "doctor did not complete": lambda r: r.update(status="partial"),
            "wrong preflight scope": lambda r: r.update(preflight_scope="smoke"),
            "macOS is older than 26": lambda r: r["checks"]["macos"].update(version="25.6"),
            "SDK is older than 26": lambda r: r["checks"]["xcode"].update(sdk_version="15.4"),
            "LLVM is not the bundled Chromium clang": lambda r: r["checks"]["llvm"].update(source="homebrew"),
            "wrong LLVM selection": lambda r: r["checks"]["llvm"].update(selection_reason="path"),
            "Rust toolchain is incomplete": lambda r: r["checks"]["rust"].update(status="missing"),
            "wrong Rust nightly": lambda r: r["checks"]["rust"].update(toolchain="nightly-2020-01-01"),
            "wrong Rust components": lambda r: r["checks"]["rust"].update(components=["rust-src"]),
            "compiler caching is enabled": lambda r: r["checks"]["compiler_cache"].update(status="enabled"),
            "free disk space is below the full-build floor": lambda r: r["checks"]["filesystem"].update(minimum_free_bytes=80 * GIB - 1),
            "required LLVM tools are missing": lambda r: r["checks"]["llvm"]["tools"].pop("llvm-strip"),
            "reference checkout was used": lambda r: r["references"]["ublock"].update(status="used"),
        }
        for message, break_policy in cases.items():
            report = doctor_report()
            break_policy(report)
            with self.subTest(message):
                with self.assertRaises(SystemExit) as raised:
                    self.verify(report)
                self.assertPolicyFailure(raised.exception, message, report)

    def test_version_floors_are_inclusive(self):
        for section, key in (("macos", "version"), ("xcode", "sdk_version")):
            for version, accepted in (("26", False), ("26.0", True), ("26.0.0", True), ("25.99.9", False)):
                report = doctor_report()
                report["checks"][section][key] = version
                with self.subTest(section=section, version=version):
                    if accepted:
                        self.verify(report)
                    else:
                        with self.assertRaises(SystemExit):
                            self.verify(report)

    def test_the_required_llvm_tools_are_enough_and_the_report_may_omit_references(self):
        report = doctor_report()
        report["checks"]["llvm"]["tools"] = {name: "/bin/" + name for name in ("llvm-ar", "llvm-nm", "llvm-objcopy", "llvm-strip")}
        del report["references"]
        self.assertEqual(self.verify(report), "macOS full-build preflight policy: passed")

    def test_missing_sections_fail_the_policy_instead_of_crashing(self):
        report = doctor_report()
        for section, message in (
            ("filesystem", "free disk space is below the full-build floor"),
            ("compiler_cache", "compiler caching is enabled"),
            ("rust", "Rust toolchain is incomplete"),
            ("llvm", "LLVM is not the bundled Chromium clang"),
        ):
            broken = json.loads(json.dumps(report))
            del broken["checks"][section]
            with self.subTest(section):
                with self.assertRaises(SystemExit) as raised:
                    self.verify(broken)
                self.assertPolicyFailure(raised.exception, message, broken)
        broken = json.loads(json.dumps(report))
        del broken["checks"]["llvm"]["tools"]
        with self.assertRaises(SystemExit) as raised:
            self.verify(broken)
        self.assertPolicyFailure(raised.exception, "required LLVM tools are missing", broken)
        for section in ("macos", "xcode"):
            broken = json.loads(json.dumps(report))
            del broken["checks"][section]
            with self.subTest(section):
                with self.assertRaises(SystemExit) as raised:
                    self.verify(broken)
                self.assertEqual(str(raised.exception), "could not parse a version from '0'")
        broken = {"status": "complete", "preflight_scope": "host-only-full-build"}
        with self.assertRaises(SystemExit) as raised:
            self.verify(broken)
        self.assertEqual(str(raised.exception), "could not parse a version from '0'")

    def test_the_free_space_floor_is_the_argument(self):
        report = doctor_report()
        report["checks"]["filesystem"]["minimum_free_bytes"] = 40 * GIB
        self.assertEqual(self.verify(report, "--minimum-free-gb", 40), "macOS full-build preflight policy: passed")
        with self.assertRaises(SystemExit):
            self.verify(report)

    def test_the_doctor_report_must_be_readable(self):
        self.assertRejected("verify-preflight", "--work-root", self.root, message=f"^doctor report is not readable JSON at {re.escape(str(self.root / 'metadata' / 'doctor.json'))}: ")


class PhaseReportTests(PolicyTestCase):
    def test_configure_requires_a_single_locale_non_component_graph(self):
        metadata = self.root / "metadata"
        write_json(metadata / "configure-macos.json", {"status": "complete"})
        (metadata / "macos-release.args.gn").write_text('macos_single_locale = "en-US"\nis_component_build = false\n', encoding="utf-8")
        self.assertEqual(self.run_check("verify-configure", "--work-root", self.root), "macOS configure policy: passed")
        (metadata / "macos-release.args.gn").write_text('is_component_build = false\n', encoding="utf-8")
        self.assertRejected(
            "verify-configure", "--work-root", self.root, message="configure did not select en-US only", report={"status": "complete"}
        )
        (metadata / "macos-release.args.gn").write_text('macos_single_locale = "en-US"\nis_component_build = true\n', encoding="utf-8")
        self.assertRejected(
            "verify-configure", "--work-root", self.root, message="configure enabled a component build", report={"status": "complete"}
        )
        (metadata / "macos-release.args.gn").unlink()
        self.assertRejected("verify-configure", "--work-root", self.root, message="^could not read generated GN args: ")
        write_json(metadata / "configure-macos.json", {"status": "failed"})
        self.assertRejected(
            "verify-configure", "--work-root", self.root, message="configure did not complete", report={"status": "failed"}
        )

    def test_cache_disabled_requires_the_stats_report_to_say_so(self):
        path = self.root / "metadata" / "compiler-cache-stats.json"
        write_json(path, {"cache": {"status": "disabled"}})
        self.assertEqual(self.run_check("verify-cache-disabled", "--work-root", self.root), "compiler-cache policy: disabled")
        report = {"cache": {"status": "enabled"}}
        write_json(path, report)
        self.assertRejected("verify-cache-disabled", "--work-root", self.root, message="compiler caching is enabled", report=report)
        write_json(path, {"other": 1})
        self.assertRejected(
            "verify-cache-disabled", "--work-root", self.root, message="compiler caching is enabled", report={"other": 1}
        )


class PackageTests(PolicyTestCase):
    def package(self, *, acceptance: str, name: str, content: bytes = b"archive") -> Path:
        archive = self.root / "release" / name
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        self.report = {"status": "complete", "archive": str(archive), "acceptance": acceptance, "sha256": digest}
        write_json(self.root / "metadata" / "package-macos.json", self.report)
        return archive

    def verify(self) -> str:
        return self.run_check("verify-package", "--work-root", self.root)

    def test_accepted_and_experimental_packages_pass_under_their_own_names(self):
        archive = self.package(acceptance="complete", name="Chromium-1.zip")
        self.assertEqual(self.verify(), f"package policy: passed ({archive.resolve()})")
        self.package(acceptance="local-stage-only", name="Chromium-1.zip")
        self.verify()
        self.package(acceptance="skipped", name="Chromium-1-experimental.zip")
        self.verify()

    def test_acceptance_and_archive_name_must_agree(self):
        self.package(acceptance="skipped", name="Chromium-1.zip")
        self.assertRejected("verify-package", "--work-root", self.root, message="skipped acceptance must use experimental archive", report=self.report)
        self.package(acceptance="complete", name="Chromium-1-experimental.zip")
        self.assertRejected("verify-package", "--work-root", self.root, message="production package must not use experimental name", report=self.report)
        self.package(acceptance="unknown", name="Chromium-1.zip")
        self.assertRejected("verify-package", "--work-root", self.root, message="package acceptance is not recorded", report=self.report)

    def test_digest_checksum_and_location_are_verified(self):
        archive = self.package(acceptance="complete", name="Chromium-1.zip")
        archive.write_bytes(b"tampered")
        self.assertRejected("verify-package", "--work-root", self.root, message="package digest does not match its report", report=self.report)
        archive = self.package(acceptance="complete", name="Chromium-1.zip")
        archive.with_name(archive.name + ".sha256").write_text("0" * 64 + "\n", encoding="utf-8")
        self.assertRejected("verify-package", "--work-root", self.root, message="package checksum is incorrect", report=self.report)
        archive.with_name(archive.name + ".sha256").unlink()
        self.assertRejected("verify-package", "--work-root", self.root, message="package checksum is missing", report=self.report)
        archive.write_bytes(b"")
        self.assertRejected("verify-package", "--work-root", self.root, message="package archive is missing", report=self.report)
        outside = self.root / "elsewhere.zip"
        outside.write_bytes(b"archive")
        for changes, message in (
            ({"archive": str(outside)}, "package escaped the release directory"),
            ({"archive": None}, "package escaped the release directory"),
            ({"status": "failed"}, "package did not complete"),
        ):
            report = {key: value for key, value in {**self.report, **changes}.items() if value is not None}
            write_json(self.root / "metadata" / "package-macos.json", report)
            self.assertRejected("verify-package", "--work-root", self.root, message=message, report=report)
        archive.write_bytes(b"a")
        report = {**self.report, "sha256": hashlib.sha256(b"a").hexdigest()}
        archive.with_name(archive.name + ".sha256").write_text(report["sha256"], encoding="utf-8")
        write_json(self.root / "metadata" / "package-macos.json", report)
        self.assertIn("package policy: passed", self.verify())
        del report["acceptance"]
        write_json(self.root / "metadata" / "package-macos.json", report)
        self.assertRejected("verify-package", "--work-root", self.root, message="package acceptance is not recorded", report=report)


class CommandLineTests(PolicyTestCase):
    def usage_error(self, *arguments: str) -> str:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            verify_macos_ci.main(list(arguments))
        self.assertEqual(raised.exception.code, 2)
        return errors.getvalue().strip().splitlines()[-1].split(": error: ", 1)[1]

    def test_help_describes_the_module(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            verify_macos_ci.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(str(verify_macos_ci.__doc__), output.getvalue())

    def test_a_command_is_required(self):
        self.assertEqual(self.usage_error(), "the following arguments are required: command")

    def test_each_command_names_its_required_options(self):
        for command, required in (
            ("claim-work-root", "--repo-root, --work-root"),
            ("cache-key", "--lock"),
            ("chromium-version", "--lock"),
            ("verify-llvm", "--clang, --minimum"),
            ("verify-preflight", "--work-root"),
            ("verify-configure", "--work-root"),
            ("verify-cache-disabled", "--work-root"),
            ("verify-package", "--work-root"),
        ):
            with self.subTest(command):
                self.assertEqual(self.usage_error(command), f"the following arguments are required: {required}")


class WorkRootTests(PolicyTestCase):
    def test_claim_work_root_creates_the_owned_layout_and_prints_it(self):
        repository = self.root / "checkout"
        repository.mkdir()
        work_root = repository / ".work" / "macos-arm64"
        printed = self.run_check("claim-work-root", "--repo-root", repository, "--work-root", work_root)
        self.assertEqual(Path(printed), work_root.resolve())
        self.assertTrue((work_root / ".chromium-build-owned.json").is_file())
        self.assertEqual(self.run_check("claim-work-root", "--repo-root", repository, "--work-root", work_root), printed)


if __name__ == "__main__":
    unittest.main()
