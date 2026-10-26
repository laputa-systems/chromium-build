#!/usr/bin/env python3
"""Focused tests for the native macOS path and its ownership boundary."""

from pathlib import Path
import hashlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock


import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import macos_backend as backend


class MacOSBackendTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        test_work_area = self.repo / ".work" / "tmp"
        test_work_area.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="macos-backend-test-", dir=test_work_area))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_native_overlay_content_invalidates_downstream_identity(self):
        repository = self.root / "checkout"
        overlay = repository / "config" / "patches" / "macos-native"
        overlay.mkdir(parents=True)
        (overlay / "series").write_text("001-test.patch\n")
        patch = overlay / "001-test.patch"
        patch.write_text("first")
        layout = backend.WorkLayout(repository / ".work" / "macos-arm64", repository)
        first = backend.local_overlay_identity(layout)["sha256"]
        backend.require_overlay_identity(layout, {"local_overlay_sha256": first})
        patch.write_text("second")
        self.assertNotEqual(first, backend.local_overlay_identity(layout)["sha256"])
        with self.assertRaisesRegex(backend.MacOSFailure, "overlay changed"):
            backend.require_overlay_identity(layout, {"local_overlay_sha256": first})

    def test_native_patch_rejects_offset_hunks_without_mutating_source(self):
        source = self.root / "source"
        source.mkdir()
        target = source / "file"
        target.write_text("prefix\nfirst\nsecond\nthird\n")
        patch = self.root / "001-test.patch"
        patch.write_text("--- a/file\n+++ b/file\n@@ -1,3 +1,3 @@\n first\n-second\n+replacement\n third\n")
        with self.assertRaisesRegex(backend.MacOSFailure, "failed dry-run"):
            backend.patch_file(source, patch, self.root / "patch-records", os.environ, exact=True)
        self.assertEqual(target.read_text(), "prefix\nfirst\nsecond\nthird\n")

    def test_acceptance_rejects_successful_zero_test_selection(self):
        log = self.root / "acceptance.log"
        log.write_text("test result: ok. 0 passed; 0 failed; 1 filtered out\n")
        with self.assertRaisesRegex(backend.MacOSFailure, "exactly one passing"):
            backend.require_acceptance_execution(log)
        log.write_text("test staged_chromium_acceptance ... ok\ntest result: ok. 1 passed; 0 failed; 0 ignored\n")
        backend.require_acceptance_execution(log)

    def test_headed_reference_keeps_product_acceptance_headless_and_records_scope(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Chromium.app"
        app.mkdir()
        reference = self.root / "headed.json"
        reference.write_text("{}")
        with mock.patch.object(backend, "load_lock", return_value={}), \
             mock.patch.object(backend, "validate_app_path", return_value=app), \
             mock.patch.object(backend, "isolated_environment", return_value={}), \
             mock.patch.object(backend, "audit_bundle", return_value={"status": "complete"}), \
             mock.patch.object(backend, "bundle_fingerprint", return_value="candidate"), \
             mock.patch.object(backend, "run_shadowdriver_harness", return_value={"status": "complete"}) as product, \
             mock.patch.object(backend, "run_native_stealth_harness", return_value={"status": "complete"}) as stealth:
            report = backend.test_staged(layout, str(app), str(reference))
            product.assert_called_once_with(layout, {}, app, headless_only=True)
            stealth.assert_called_once_with(layout, app, reference)
            self.assertEqual(report["acceptance_scope"], "headless-with-historical-headed-reference")
            product.reset_mock()
            stealth.reset_mock()
            report = backend.test_staged(layout, str(app))
            product.assert_called_once_with(layout, {}, app, headless_only=False)
            stealth.assert_called_once_with(layout, app, None)
            self.assertEqual(report["acceptance_scope"], "headed-and-headless")

    def test_work_root_rejects_forbidden_and_ambiguous_paths(self):
        for value in ("/tmp/chromium", "~/chromium", "/Volumes/dev/with space", str(self.repo / ".work")):
            with self.subTest(value=value), self.assertRaises(backend.MacOSFailure):
                backend.validate_work_root(value, self.repo)

        default_root = self.repo / ".work" / "macos-arm64"
        self.assertEqual(backend.validate_work_root(default_root, self.repo), default_root)
        self.assertEqual(backend.validate_work_root(self.root, self.repo), self.root)

    def test_work_root_rejects_a_repository_work_area_symlink_escape(self):
        repository = self.root / "checkout"
        repository.mkdir()
        escaped_work = self.root / "escaped-work"
        escaped_work.mkdir()
        (repository / ".work").symlink_to(escaped_work, target_is_directory=True)

        with self.assertRaisesRegex(backend.MacOSFailure, "repository .work directory must not be a symlink"):
            backend.validate_work_root(repository / ".work" / "macos-arm64", repository)

    def test_work_root_rejects_a_symlinked_root(self):
        target = self.root / "real-work-root"
        target.mkdir()
        alias = self.root / "linked-work-root"
        alias.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(backend.MacOSFailure, "work root must not be a symlink"):
            backend.validate_work_root(alias, self.repo)

    def test_acceptance_target_uses_the_sibling_shadowdriver_checkout(self):
        layout = backend.WorkLayout(self.root, self.repo)
        manifest, test_source = backend.shadowdriver_acceptance_target(layout)
        self.assertEqual(manifest, self.repo.parent / "shadowdriver" / "Cargo.toml")
        self.assertEqual(test_source, self.repo.parent / "shadowdriver" / "tests" / "chromium_acceptance.rs")

    def test_required_references_use_sibling_checkouts_and_fail_when_missing(self):
        repository = self.root / "checkout"
        layout = backend.WorkLayout(repository / ".work" / "macos-arm64", repository)
        references = dict(backend.required_reference_paths(layout))
        self.assertEqual(references["helium"], self.root / "helium")
        self.assertEqual(references["shadowdriver"], self.root / "shadowdriver")
        with self.assertRaisesRegex(backend.MacOSFailure, "required read-only reference is missing"):
            backend.reference_state(references["helium"], {})

    def test_work_lock_initializes_a_fresh_root_before_creating_its_lock_file(self):
        fresh_root = self.root / "fresh"
        layout = backend.WorkLayout(fresh_root, self.repo)
        with backend.work_lock(layout):
            self.assertTrue(layout.marker.is_file())
            self.assertTrue((layout.lock_dir / "build.lock").is_file())

    def test_supplied_app_path_stays_within_repository_work_area(self):
        layout = backend.WorkLayout(self.root, self.repo)
        app = self.root / "relocated" / "Chromium.app"
        app.mkdir(parents=True)
        self.assertEqual(backend.validate_app_path(app, layout, "Chromium app"), app)

    def test_layout_claims_only_an_empty_root_and_is_repeatable(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        self.assertTrue(layout.marker.is_file())
        layout.ensure()
        environment = backend.isolated_environment(layout)
        self.assertEqual(environment["HOME"], str(layout.home))
        self.assertEqual(environment["TMPDIR"], str(layout.tmp))
        self.assertEqual(environment["DEVELOPER_DIR"], "/Applications/Xcode.app/Contents/Developer")
        self.assertEqual(environment["NETWORK_MODE"], "none")
        self.assertNotIn("RBE_service", environment)

    def test_layout_rejects_a_nonempty_unmarked_root(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "orphan").write_text("not owned", encoding="utf-8")
        with self.assertRaises(backend.MacOSFailure):
            backend.WorkLayout(foreign, self.repo).ensure()

    def test_compiler_cache_wrapper_is_explicit_and_work_root_owned(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        wrapper = layout.tools / "bin" / "ccache"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        version = mock.Mock(returncode=0, stdout="ccache version 4.14\n", stderr="")
        with mock.patch.object(backend, "command_output", return_value=version) as run:
            environment = backend.isolated_environment(layout, compiler_cache="ccache")

        self.assertEqual(environment["CHROMIUM_COMPILER_CACHE_STATUS"], "enabled")
        self.assertEqual(environment["CHROMIUM_COMPILER_CACHE_KIND"], "ccache")
        self.assertEqual(environment["CHROMIUM_COMPILER_CACHE_WRAPPER"], str(wrapper))
        self.assertEqual(environment["CCACHE_DIR"], str(layout.cache / "compiler" / "ccache"))
        self.assertEqual(environment["CCACHE_COMPILERCHECK"], "content")
        self.assertTrue((layout.cache / "compiler" / "ccache" / "tmp").is_dir())
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], "--version")

    def test_compiler_cache_can_be_explicitly_disabled(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        environment = backend.isolated_environment(layout, compiler_cache="off")
        report = backend.compiler_cache_report(environment)
        self.assertEqual(report["status"], "disabled")
        self.assertNotIn("CCACHE_DIR", environment)
        self.assertNotIn("SCCACHE_DIR", environment)

    def test_compiler_cache_rejects_a_workroot_cache_symlink_escape(self):
        root = self.root / "build-root"
        layout = backend.WorkLayout(root, self.repo)
        layout.ensure()
        escaped = self.root / "escaped-cache"
        escaped.mkdir()
        (layout.cache / "compiler").symlink_to(escaped, target_is_directory=True)
        wrapper = layout.tools / "bin" / "ccache"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        version = mock.Mock(returncode=0, stdout="ccache version 4.14\n", stderr="")

        with mock.patch.object(backend, "command_output", return_value=version):
            with self.assertRaisesRegex(backend.MacOSFailure, "compiler cache directory escapes"):
                backend.isolated_environment(layout, compiler_cache="ccache")

        self.assertFalse((escaped / "ccache").exists())

    def test_documented_compiler_cache_choices_match_the_backend(self):
        documented = f"`MACOS_COMPILER_CACHE=off|{'|'.join(backend.COMPILER_CACHE_NAMES)}|auto`"
        instructions = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(documented, instructions)
        aliases = "`" + "` and `".join(backend.COMPILER_CACHE_DISABLED[1:]) + "`"
        self.assertIn(f"{aliases} also turn compiler caching off.", instructions)

    def test_ccache_stats_uses_the_local_store_report(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        wrapper = layout.tools / "bin" / "ccache"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        ninja = layout.source / "third_party" / "ninja" / "ninja"
        ninja.parent.mkdir(parents=True)
        ninja.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        ninja.chmod(0o755)
        layout.source.mkdir(parents=True, exist_ok=True)
        backend.atomic_json(
            layout.metadata / "configure-macos.json",
            {"compiler_cache": {"status": "enabled", "kind": "ccache"}},
        )
        version = mock.Mock(returncode=0, stdout="ccache version 4.14\n", stderr="")
        stats = mock.Mock(returncode=0, stdout="local stats\n", stderr="")
        ninja_error = mock.Mock(returncode=1, stdout="ninja: no build.ninja\n", stderr="")

        def command_result(command, **kwargs):
            if command[-1] == "--version":
                return version
            if "--show-stats" in command:
                return stats
            return ninja_error

        with mock.patch.object(backend, "command_output", side_effect=command_result) as run:
            report = backend.compiler_cache_stats(layout)

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["cache"]["directory"], str(layout.cache / "compiler" / "ccache"))
        self.assertEqual(report["output"], "local stats")
        self.assertEqual(report["ninja_incremental"]["status"], "unavailable")
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(
            run.call_args_list[1].args[0],
            [str(wrapper), "--show-stats"],
        )
        self.assertEqual(
            run.call_args_list[1].kwargs["env"]["CCACHE_DIR"],
            str(layout.cache / "compiler" / "ccache"),
        )

    def test_architecture_normalization_is_native_only(self):
        self.assertEqual(backend.normalize_arch("aarch64"), "arm64")
        self.assertEqual(backend.normalize_arch("arm64"), "arm64")
        with self.assertRaises(backend.MacOSFailure):
            backend.normalize_arch("x86_64")

    def test_select_llvm_requires_prepared_bundled_clang(self):
        with self.assertRaisesRegex(backend.MacOSFailure, "run fetch"):
            backend.select_llvm({}, None)
        empty_source = self.root / "empty-src"
        empty_source.mkdir()
        with self.assertRaisesRegex(backend.MacOSFailure, "run fetch"):
            backend.select_llvm({}, empty_source)

    def test_select_llvm_ignores_explicit_homebrew_and_xcode_roots(self):
        source = self.root / "src"
        bundled = source / "third_party" / "llvm-build" / "Release+Asserts"
        (bundled / "bin").mkdir(parents=True)
        bundled_candidate = {
            "root": str(bundled),
            "source": "bundled",
            "version": "21.1.0",
            "version_tuple": (21, 1, 0),
            "clang": str(bundled / "bin" / "clang"),
            "clangxx": str(bundled / "bin" / "clang++"),
        }
        explicit = self.root / "explicit-llvm"
        (explicit / "bin").mkdir(parents=True)
        explicit_candidate = {
            "root": str(explicit),
            "source": "explicit",
            "version": "23.1.1",
            "version_tuple": (23, 1, 1),
            "clang": str(explicit / "bin" / "clang"),
            "clangxx": str(explicit / "bin" / "clang++"),
        }

        def fake_candidate(root, environment, source):
            if str(root) == str(bundled):
                return dict(bundled_candidate)
            if str(root) == str(explicit):
                return dict(explicit_candidate)
            return None

        environment = {
            "CHROMIUM_LLVM_ROOT": str(explicit),
            "XCODE_CLANG": "/fake/xcode/clang",
            "PATH": os.environ.get("PATH", ""),
        }
        with mock.patch.object(backend, "llvm_candidate", side_effect=fake_candidate):
            with mock.patch.object(backend, "brew_candidates", return_value=[Path("/fake/homebrew/llvm")]):
                selected = backend.select_llvm(environment, source)
        self.assertEqual(selected["root"], str(bundled))
        self.assertEqual(selected["source"], "bundled")

    def test_locked_deno_hash_mismatch_fails(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        entry = {
            "version": "2.9.8",
            "filename": "deno-aarch64-apple-darwin.zip",
            "url": "https://example.invalid/deno.zip",
            "size": 4,
            "sha256": "0" * 64,
            "sha512": "1" * 128,
        }
        archive = layout.inputs / entry["filename"]
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"bad!")
        lock = {"test_inputs": {"deno": entry}}
        with self.assertRaisesRegex(backend.MacOSFailure, "mismatch|SHA"):
            backend.ensure_locked_deno(layout, lock)

    def test_deno_resolves_only_from_tools_bin(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        tools_deno = layout.tools / "bin" / "deno"
        tools_deno.parent.mkdir(parents=True, exist_ok=True)
        tools_deno.write_bytes(b"tools-deno")
        tools_deno.chmod(0o755)
        fake_path_deno = self.root / "path-deno"
        fake_path_deno.write_bytes(b"path-deno")
        fake_path_deno.chmod(0o755)
        homebrew_deno = Path("/opt/homebrew/bin/deno")
        environment = {"PATH": f"{self.root}:{os.environ.get('PATH', '')}"}
        lock = {
            "test_inputs": {
                "deno": {
                    "version": "test",
                    "filename": "deno.zip",
                    "url": "https://example.invalid/deno.zip",
                    "size": 1,
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                    "sha512": hashlib.sha512(b"x").hexdigest(),
                }
            }
        }
        with mock.patch.object(backend, "command_path", return_value=str(fake_path_deno)):
            resolved = backend.deno_executable(layout, lock)
        self.assertEqual(resolved, tools_deno)
        self.assertNotEqual(str(resolved), str(fake_path_deno))
        self.assertNotEqual(str(resolved), str(homebrew_deno))

    def test_xcode_identity_rejects_a_present_but_uninstalled_metal_toolchain(self):
        def result(stdout="", stderr="", returncode=0):
            return mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)

        missing_metal = "error: cannot execute tool 'metal' due to missing Metal Toolchain"
        responses = [
            result(stdout="Xcode 27.0\nBuild version 27A266a\n"),
            result(stdout="27.0\n"),
            result(stdout="/usr/bin/clang\n"),
            result(stdout="/usr/bin/clang++\n"),
            result(stdout="/usr/bin/ld\n"),
            result(stdout="Apple clang version 21.0.0\n"),
            result(stdout="/usr/bin/clang\n"),
            result(stderr=missing_metal, returncode=1),
        ]
        with mock.patch.object(backend, "command_output", side_effect=responses):
            with self.assertRaisesRegex(backend.MacOSFailure, "Metal Toolchain is unavailable"):
                backend.xcode_identity({})

    def test_llvm_candidate_requires_native_binutils(self):
        llvm = self.root / "llvm"
        bin_dir = llvm / "bin"
        bin_dir.mkdir(parents=True)
        for name in ("clang", "clang++", "llvm-ar", "llvm-nm", "llvm-objcopy", "llvm-strip"):
            (bin_dir / name).write_text("", encoding="utf-8")

        def result(stdout="", stderr="", returncode=0):
            return mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)

        with mock.patch.object(
            backend,
            "command_output",
            side_effect=[
                result(stdout="clang version 23.1.1\n"),
                result(stdout="arm64-apple-darwin\n"),
            ],
        ):
            candidate = backend.llvm_candidate(llvm, {}, "test")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["tools"]["llvm-ar"], str(bin_dir / "llvm-ar"))
        (bin_dir / "llvm-strip").unlink()
        with mock.patch.object(backend, "command_output"):
            self.assertIsNone(backend.llvm_candidate(llvm, {}, "test"))

    def test_depot_tools_fetch_is_one_filtered_depth_one_commit(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        lock = {
            "depot_tools": {
                "repository": "https://chromium.googlesource.com/chromium/tools/depot_tools.git",
                "commit": "a" * 40,
            }
        }
        with mock.patch.object(
            backend,
            "command_output",
            return_value=mock.Mock(returncode=0, stdout="", stderr=""),
        ) as run:
            backend.clone_depot_tools(layout, lock, {})
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(command[1] == "clone" for command in commands))
        fetches = [command for command in commands if "fetch" in command]
        self.assertEqual(len(fetches), 1)
        self.assertIn("--filter=blob:none", fetches[0])
        self.assertIn("--depth=1", fetches[0])
        self.assertIn("--no-tags", fetches[0])

    def test_large_locked_download_assembles_parallel_https_ranges(self):
        data = b"0123456789"
        entry = {
            "name": "chromium",
            "filename": "chromium.tar.xz",
            "url": "https://example.test/chromium.tar.xz",
            "size": len(data),
            "sha512": hashlib.sha512(data).hexdigest(),
        }
        requests = []

        class Response:
            def __init__(self, payload, content_range):
                self.stream = io.BytesIO(payload)
                self.status = 206
                self.headers = {"Content-Range": content_range}

            def geturl(self):
                return entry["url"]

            def read(self, size=-1):
                return self.stream.read(size)

            def close(self):
                self.stream.close()

        def open_url(request, timeout):
            self.assertEqual(timeout, 120)
            range_header = request.get_header("Range")
            self.assertIsNotNone(range_header)
            requests.append(range_header)
            start, end = (
                int(value)
                for value in range_header.removeprefix("bytes=").split("-", 1)
            )
            return Response(data[start : end + 1], f"bytes {start}-{end}/{len(data)}")

        with (
            mock.patch.object(backend, "RANGE_DOWNLOAD_THRESHOLD_BYTES", 1),
            mock.patch.object(backend, "RANGE_DOWNLOAD_CHUNK_BYTES", 3),
            mock.patch.dict(os.environ, {"MACOS_INPUT_DOWNLOAD_WORKERS": "3"}, clear=False),
            mock.patch.object(backend.urllib.request, "urlopen", side_effect=open_url),
        ):
            result = backend.download_locked(entry, self.root / "inputs")

        self.assertEqual(result["download_mode"], "parallel-ranges-3")
        self.assertEqual(result["workers"], 3)
        self.assertEqual((self.root / "inputs" / entry["filename"]).read_bytes(), data)
        self.assertIn("bytes=0-0", requests)
        self.assertEqual(
            {value for value in requests if value != "bytes=0-0"},
            {"bytes=0-2", "bytes=3-5", "bytes=6-8", "bytes=9-9"},
        )

    def test_bundle_fingerprint_changes_with_owned_content(self):
        app = self.root / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"arm64-test")
        first = backend.bundle_fingerprint(app)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"arm64-test-2")
        self.assertNotEqual(first, backend.bundle_fingerprint(app))

    def test_package_reuses_audited_candidate_and_publishes_only_archive_assets(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = layout.candidates / ("a" * 64) / "Chromium.app"
        executable = app / "Contents" / "MacOS" / "Chromium"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"arm64-test")
        fingerprint = backend.bundle_fingerprint(app)
        candidate = {
            "schema": 1,
            "status": "complete",
            "fingerprint": fingerprint,
            "local_overlay_sha256": backend.local_overlay_identity(layout)["sha256"],
            "app": str(app),
            "audit": {"status": "complete"},
        }
        backend.atomic_json(layout.stage / "candidate.json", candidate)

        def fake_command(command, **kwargs):
            self.assertEqual(command[0], "/usr/bin/ditto")
            Path(command[-1]).write_bytes(b"zip-placeholder")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(backend, "isolated_environment", return_value={}),
            mock.patch.object(backend, "command_output", side_effect=fake_command),
        ):
            report = backend.package_bundle(layout)

        archive = Path(report["archive"])
        self.assertTrue(archive.is_file())
        self.assertTrue(archive.with_name(f"{archive.name}.sha256").is_file())
        self.assertEqual(report["sha256"], backend.sha256(archive))
        self.assertFalse((layout.release / "build-manifest.json").exists())
        self.assertFalse((layout.release / "version").exists())

    def test_macho_audit_alias_handles_archive_like_bundle_names(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        binary = self.root / "Chromium Helper (Alerts)"
        binary.write_bytes(b"macho-placeholder")
        with backend.macho_audit_path(binary, layout) as alias:
            self.assertTrue(alias.is_symlink())
            self.assertEqual(alias.resolve(), binary.resolve())
        self.assertFalse(alias.exists())
        self.assertFalse(alias.is_symlink())

    def test_single_locale_cleanup_removes_stale_outputs_but_keeps_english(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        (layout.metadata / "macos-release.args.gn").write_text(
            'macos_single_locale = "en-US"\n',
            encoding="utf-8",
        )
        stale_generated = layout.output / "gen" / "chrome" / "platform_locale_settings_fr.pak"
        stale_generated.parent.mkdir(parents=True)
        stale_generated.write_bytes(b"stale")
        stale_repack = layout.output / "gen" / "repack" / "locales" / "fr.pak"
        stale_repack.parent.mkdir(parents=True)
        stale_repack.write_bytes(b"stale")
        resources = layout.output / "Chromium.app" / "Contents" / "Resources"
        (resources / "fr.lproj").mkdir(parents=True)
        (resources / "fr.lproj" / "locale.pak").write_bytes(b"stale")
        (resources / "en.lproj").mkdir(parents=True)

        cleanup = backend.clean_single_locale_outputs(layout)

        self.assertEqual(cleanup["status"], "complete")
        self.assertEqual(cleanup["removed"], 3)
        self.assertFalse(stale_generated.exists())
        self.assertFalse(stale_repack.exists())
        self.assertFalse((resources / "fr.lproj").exists())
        self.assertTrue((resources / "en.lproj").is_dir())

    def test_deps_variables_are_read_from_locked_deps(self):
        source = self.root / "src"
        source.mkdir()
        (source / "DEPS").write_text(
            "'cpython3_version': 'version:3@3.11.9.chromium.38',\n"
            "'ninja_version': 'version:3@1.12.1.chromium.4',\n",
            encoding="utf-8",
        )
        self.assertEqual(
            backend.deps_variable(source, "cpython3_version"),
            "version:3@3.11.9.chromium.38",
        )
        self.assertEqual(
            backend.deps_variable(source, "ninja_version"),
            "version:3@1.12.1.chromium.4",
        )

    def test_macos_compatibility_patch_is_idempotent(self):
        source = self.root / "src"
        target = source / "chrome" / "test"
        target.mkdir(parents=True)
        build_file = target / "BUILD.gn"
        build_file.write_text(
            "source_set(\"mac_tests\") {\n"
            "  deps = [\n"
            "      \"//chrome/common/safe_browsing:archive_analyzer_results\",\n"
            "      \"//chrome/common/safe_browsing:disk_image_type_sniffer_mac\",\n"
            "  ]\n"
            "}\n",
            encoding="utf-8",
        )
        locale_file = source / "build" / "config" / "locales.gni"
        locale_file.parent.mkdir(parents=True)
        locale_file.write_text(
            "declare_args() {\n"
            "  translate_genders = !is_ios\n"
            "}\n\n"
            "pseudolocales = [ \"ar-XB\" ]\n"
            "all_chrome_locales = [ \"en-US\", \"fr\" ]\n"
            "platform_pak_locales = all_chrome_locales\n"
            "locales_without_pseudolocales = platform_pak_locales - pseudolocales\n",
            encoding="utf-8",
        )
        grd_file = source / "chrome" / "app" / "resources" / "locale_settings_mac.grd"
        grd_file.parent.mkdir(parents=True)
        grd_file.write_text(
            "<outputs>\n"
            '  <output filename="grit/platform_locale_settings.h" type="rc_header" />\n'
            '  <output filename="platform_locale_settings_en-US.pak" type="data_package" lang="en" />\n'
            '  <output filename="platform_locale_settings_fr.pak" type="data_package" lang="fr" />\n'
            "</outputs>\n",
            encoding="utf-8",
        )
        browser = source / "chrome" / "browser"
        browser.mkdir(parents=True)
        enterprise = browser / "enterprise"
        enterprise.mkdir()
        enterprise_build = enterprise / "BUILD.gn"
        enterprise_build.write_text(
            'source_set("impl") {\n'
            '  if (is_linux || is_win || is_mac) {\n'
            '    sources += [\n'
            '      "signin/enterprise_signin_service_factory.cc",\n'
            '      "signin/managed_profile_creator.cc",\n'
            '    ]\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )
        client = browser / "chrome_content_browser_client.cc"
        client.write_text(
            '#include "chrome/browser/screen_ai/screen_ai_install_state.h"\n'
            '#include "services/network/public/cpp/features.h"\n'
            "\n"
            "#if BUILDFLAG(IS_MAC)\n"
            "bool Setup() {\n"
            "  if (sandbox_type == sandbox::mojom::Sandbox::kScreenAI) {\n"
            "    base::FilePath screen_ai_binary_path =\n"
            "        screen_ai::ScreenAIInstallState::GetInstance()\n"
            "            ->get_component_binary_path();\n"
            "    return serializer->SetParameter(\n"
            "        sandbox::policy::kParamScreenAiComponentPath,\n"
            "        screen_ai_binary_path.value());\n"
            "  }\n"
            "  if (sandbox_type == sandbox::mojom::Sandbox::kOnDeviceTranslation) {\n"
            "    return false;\n"
            "  }\n"
            "  return false;\n"
            "}\n"
            "#endif\n",
            encoding="utf-8",
        )
        applied = backend.apply_macos_profile_compatibility(source)
        self.assertEqual(applied["status"], "complete")
        self.assertEqual(applied["safe_browsing"]["status"], "applied")
        self.assertEqual(applied["screen_ai"]["status"], "applied")
        self.assertEqual(applied["english_only_locale"]["status"], "applied")
        self.assertEqual(applied["managed_profile_controller"]["status"], "applied")
        self.assertNotIn("safe_browsing:archive_analyzer_results", build_file.read_text(encoding="utf-8"))
        self.assertNotIn("safe_browsing:disk_image_type_sniffer_mac", build_file.read_text(encoding="utf-8"))
        self.assertIn(
            '"signin/managed_profile_creation_controller.cc"',
            enterprise_build.read_text(encoding="utf-8"),
        )
        patched_locales = locale_file.read_text(encoding="utf-8")
        self.assertIn('macos_single_locale = ""', patched_locales)
        self.assertIn('if (macos_single_locale != "")', patched_locales)
        self.assertIn("platform_pak_locales += [ macos_single_locale ]", patched_locales)
        self.assertIn("locales_without_pseudolocales = platform_pak_locales\n", patched_locales)
        patched_grd = grd_file.read_text(encoding="utf-8")
        self.assertIn("platform_locale_settings_en-US.pak", patched_grd)
        self.assertNotIn("platform_locale_settings_fr.pak", patched_grd)
        self.assertEqual(applied["english_only_locale"]["grd_outputs"]["status"], "applied")
        patched_client = client.read_text(encoding="utf-8")
        self.assertIn('#include "services/screen_ai/buildflags/buildflags.h"', patched_client)
        self.assertIn("#if BUILDFLAG(ENABLE_SCREEN_AI_SERVICE)", patched_client)
        self.assertEqual(patched_client.count("#if BUILDFLAG(ENABLE_SCREEN_AI_SERVICE)"), 2)
        reused = backend.apply_macos_profile_compatibility(source)
        self.assertEqual(reused["status"], "complete")
        self.assertEqual(reused["safe_browsing"]["status"], "reused")
        self.assertEqual(reused["screen_ai"]["status"], "reused")
        self.assertEqual(reused["english_only_locale"]["status"], "reused")
        self.assertEqual(reused["managed_profile_controller"]["status"], "reused")
        self.assertEqual(reused["english_only_locale"]["grd_outputs"]["status"], "reused")

    def test_reset_requires_confirmation_and_preserves_inputs(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        layout.source.mkdir(parents=True)
        (layout.source / "owned.txt").write_text("owned", encoding="utf-8")
        (layout.inputs / "locked.bin").write_bytes(b"locked")
        (layout.release / "old.zip").write_bytes(b"old")
        with self.assertRaises(backend.MacOSFailure):
            backend.reset_work(layout, False)
        backend.reset_work(layout, True)
        self.assertFalse(layout.source.exists())
        self.assertFalse((layout.release / "old.zip").exists())
        self.assertTrue((layout.inputs / "locked.bin").is_file())


if __name__ == "__main__":
    unittest.main()
