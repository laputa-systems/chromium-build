#!/usr/bin/env python3
"""Focused tests for the native macOS path and its ownership boundary."""

import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

        with mock.patch.object(backend, "command_output", return_value=version), \
             self.assertRaisesRegex(backend.MacOSFailure, "compiler cache directory escapes"):
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
            selected = backend.select_llvm(environment, source)
        self.assertEqual(selected["root"], str(bundled))
        self.assertEqual(selected["source"], "bundled")

    def test_native_stealth_harness_uses_vanilla_node_not_deno(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        (layout.logs).mkdir(parents=True, exist_ok=True)
        (layout.logs / "native-stealth-acceptance.log").write_text(
            "Test Files  1 passed (1)\nTests  1 passed (1)\n", encoding="utf-8"
        )
        fake_manifest = self.repo.parent / "shadowdriver" / "Cargo.toml"
        with mock.patch.object(backend, "isolated_environment", return_value={}), \
             mock.patch.object(backend, "load_lock", return_value={}), \
             mock.patch.object(backend, "shadowdriver_acceptance_target", return_value=(fake_manifest, fake_manifest.parent / "tests" / "chromium_acceptance.rs")), \
             mock.patch.object(backend, "shadowdriver_test_identity", return_value={"revision": "r", "source_sha256": "s"}), \
             mock.patch.object(backend, "command_path", side_effect=lambda name, env=None: "/opt/homebrew/bin/pnpm" if name == "pnpm" else ("/Users/josh/.local/bin/node" if name == "node" else None)), \
             mock.patch.object(backend, "stream_command", return_value=None) as stream, \
             mock.patch.object(backend, "read_json", return_value={"status": "passed", "compatibility_required": True, "observations": [{"mode": name} for name in ["headed-base", "headless-base", "automation-control", "native-automation-control", "native-direct", "native-passive", "native-passive-restart", "native-instrumented"]]}), \
             mock.patch.object(backend, "sha256", return_value="0" * 64):
            report = backend.run_native_stealth_harness(layout, app)
        command_text = " ".join(str(part) for part in report["command"])
        self.assertNotIn("deno", command_text)
        self.assertIn("pnpm", command_text)
        self.assertIn("native_stealth_test.ts", command_text)
        self.assertIn("--pm-on-fail=ignore", command_text)
        self.assertIn("--testTimeout=600000", command_text)
        self.assertNotIn(" -- ", f" {command_text} ")
        stealth_env = stream.call_args[1]["env"]
        stealth_path = stealth_env["PATH"].split(os.pathsep)
        self.assertEqual(stealth_path[0], "/Users/josh/.local/bin")
        self.assertIn("/opt/homebrew/bin", stealth_path)
        self.assertNotIn("CHROMIUM_SANDBOX_PROFILE", stealth_env)

    def test_fetch_acceptance_uses_node_toolchain_without_deno_lock(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        lock_without_deno = {"test_inputs": {"ublock_origin": {"filename": "u.x", "url": "https://example.invalid/u.x", "size": 1, "sha512": "a" * 128}}}
        def fake_path(name, env=None):
            if name == "pnpm":
                return "/opt/homebrew/bin/pnpm"
            if name == "node":
                return "/Users/josh/.local/bin/node"
            return None
        with mock.patch.object(backend, "load_lock", return_value=lock_without_deno), \
             mock.patch.object(backend, "shadowdriver_acceptance_target", return_value=(self.root / "Cargo.toml", self.root / "t.rs")), \
             mock.patch.object(backend, "cargo_executable", return_value=self.root / "cargo"), \
             mock.patch.object(backend, "shadowdriver_rust_wrapper", return_value="kache"), \
             mock.patch.object(backend, "stream_command", return_value=None), \
             mock.patch.object(backend, "command_path", side_effect=fake_path):
            harness = backend.fetch_acceptance_dependencies(layout, {})
        self.assertEqual(harness["status"], "complete")
        self.assertNotIn("deno", harness)

    def test_sealed_profile_blocks_loopback(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            layout = backend.WorkLayout(self.root, self.repo)
            layout.ensure()
            sealed_env = backend.isolated_environment(layout, network="none")
            self.assertEqual(sealed_env["NETWORK_MODE"], "none")
            with self.assertRaises(backend.MacOSFailure):
                backend.command_output(
                    ["python3", "-c", f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/', timeout=5).read()"],
                    env=sealed_env,
                    timeout=15,
                )
        finally:
            server.shutdown()
            server.server_close()

    def test_loopback_profile_allows_loopback(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            layout = backend.WorkLayout(self.root, self.repo)
            layout.ensure()
            loop_env = backend.isolated_environment(layout, network="loopback")
            self.assertEqual(loop_env["NETWORK_MODE"], "loopback")
            result = backend.command_output(
                ["python3", "-c", f"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{port}/', timeout=5).read().decode())"],
                env=loop_env,
                timeout=15,
            )
            self.assertIn("ok", result.stdout)
        finally:
            server.shutdown()
            server.server_close()

    def test_both_profiles_block_non_loopback(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        for mode in ("none", "loopback"):
            env = backend.isolated_environment(layout, network=mode)
            with self.assertRaises(backend.MacOSFailure, msg=mode):
                backend.command_output(
                    ["python3", "-c", "import socket; socket.create_connection(('192.0.2.1', 80), timeout=3)"],
                    env=env,
                    timeout=15,
                )

    def test_prewarm_boots_browser_and_terminates_cleanly(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        profile = self.root / "prewarm-profile"
        child = mock.Mock()
        child.poll.return_value = None
        with mock.patch.object(backend.subprocess, "Popen", return_value=child) as popen, \
             mock.patch.object(backend.urllib.request, "urlopen") as urlopen, \
             mock.patch.object(backend.time, "sleep", return_value=None), \
             mock.patch.object(backend, "EXTENSION_PREWARM_SECONDS", 0):
            response = mock.MagicMock()
            response.geturl.return_value = ""
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"webSocketDebuggerUrl": "ws://127.0.0.1:1/x"}'
            with mock.patch.object(backend.json, "load", return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:1/x"}):
                backend.prewarm_extension_profile(layout, app, self.root / "ext", profile, {"NETWORK_MODE": "loopback"})
        launched = popen.call_args[0][0]
        self.assertIn("--load-extension=" + str(self.root / "ext"), launched)
        self.assertIn("--no-sandbox", launched)
        self.assertIn("--headless=new", launched)
        child.terminate.assert_called_once()
        child.wait.assert_called_once()

    def test_prewarm_fails_when_browser_exits_early(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        child = mock.Mock()
        child.poll.return_value = 1
        with mock.patch.object(backend.subprocess, "Popen", return_value=child), \
             mock.patch.object(backend.time, "sleep", return_value=None), \
             self.assertRaisesRegex(backend.MacOSFailure, "exited before serving"):
            backend.prewarm_extension_profile(layout, app, self.root / "ext", self.root / "p", {})

    def test_sealed_entry_rejects_unwrapped_environment(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        layout.source.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(backend, "load_lock", return_value={"chromium": {"version": "1.2.3.4"}}), \
             mock.patch.object(backend, "isolated_environment", return_value={}), \
             self.assertRaisesRegex(backend.MacOSFailure, "wrapper"):
            backend.prepare_source(layout)

    def test_loopback_profile_allows_bind_sealed_denies_it(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        loop_env = backend.isolated_environment(layout, network="loopback")
        result = backend.command_output(
            ["python3", "-c", "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(128); print(s.getsockname()[1])"],
            env=loop_env,
            timeout=15,
        )
        self.assertTrue(result.stdout.strip().isdigit())
        sealed_env = backend.isolated_environment(layout, network="none")
        with self.assertRaises(backend.MacOSFailure):
            backend.command_output(
                ["python3", "-c", "import socket; s=socket.socket(); s.bind(('127.0.0.1',0))"],
                env=sealed_env,
                timeout=15,
            )

    def test_phase_recording_network_none_without_wrapper_fails(self):
        with self.assertRaisesRegex(backend.MacOSFailure, "wrapper|NETWORK_MODE|sandbox"):
            backend.require_sandboxed_environment({}, expected="none")
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        sealed_env = backend.isolated_environment(layout, network="none")
        backend.require_sandboxed_environment(sealed_env, expected="none")

    def test_build_identity_rejects_stale_report(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        with self.assertRaisesRegex(backend.MacOSFailure, "not complete|stale|status"):
            backend.require_build_identity(layout, {"status": "failed", "build_identity": {"digest": "x"}})

    def test_build_identity_rejects_altered_lock(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        current = {"digest": "good", "lock_sha256": "aaa", "overlay_sha256": "bbb", "clang_version": "1", "clang_sha256": "ccc", "xcode": "x", "sdk": "s", "linker": "l", "rust": "r", "gn_args_sha256": "g"}
        altered = dict(current)
        altered["lock_sha256"] = "zzz"
        with self.assertRaisesRegex(backend.MacOSFailure, "lock|identity|mismatch|stale"):
            backend.require_build_identity(layout, {"status": "complete", "build_identity": altered}, current=current)

    def test_build_identity_rejects_altered_overlay(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        current = {"digest": "good", "lock_sha256": "aaa", "overlay_sha256": "bbb", "clang_version": "1", "clang_sha256": "ccc", "xcode": "x", "sdk": "s", "linker": "l", "rust": "r", "gn_args_sha256": "g"}
        stale = dict(current)
        stale["overlay_sha256"] = "zzz"
        with self.assertRaisesRegex(backend.MacOSFailure, "overlay|identity|mismatch|stale"):
            backend.require_build_identity(layout, {"status": "complete", "build_identity": stale}, current=current)

    def test_build_identity_rejects_altered_gn_args(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        current = {"digest": "good", "lock_sha256": "aaa", "overlay_sha256": "bbb", "clang_version": "1", "clang_sha256": "ccc", "xcode": "x", "sdk": "s", "linker": "l", "rust": "r", "gn_args_sha256": "g"}
        stale = dict(current)
        stale["gn_args_sha256"] = "zzz"
        with self.assertRaisesRegex(backend.MacOSFailure, "GN|identity|mismatch|stale"):
            backend.require_build_identity(layout, {"status": "complete", "build_identity": stale}, current=current)

    def test_build_identity_preserves_incremental_on_unchanged(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        current = {"digest": "good", "lock_sha256": "aaa", "overlay_sha256": "bbb", "clang_version": "1", "clang_sha256": "ccc", "xcode": "x", "sdk": "s", "linker": "l", "rust": "r", "gn_args_sha256": "g"}
        backend.require_build_identity(layout, {"status": "complete", "build_identity": dict(current)}, current=current)

    def test_build_identity_rejects_mismatched_clang(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        current = {"digest": "good", "lock_sha256": "aaa", "overlay_sha256": "bbb", "clang_version": "1", "clang_sha256": "ccc", "xcode": "x", "sdk": "s", "linker": "l", "rust": "r", "gn_args_sha256": "g"}
        stale = dict(current)
        stale["clang_sha256"] = "zzz"
        with self.assertRaisesRegex(backend.MacOSFailure, "clang|identity|mismatch|stale"):
            backend.require_build_identity(layout, {"status": "complete", "build_identity": stale}, current=current)

    def test_build_identity_rejects_mismatched_xcode(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        current = {"digest": "good", "lock_sha256": "aaa", "overlay_sha256": "bbb", "clang_version": "1", "clang_sha256": "ccc", "xcode": "x", "sdk": "s", "linker": "l", "rust": "r", "gn_args_sha256": "g"}
        stale = dict(current)
        stale["xcode"] = "zzz"
        with self.assertRaisesRegex(backend.MacOSFailure, "xcode|Xcode|identity|mismatch|stale"):
            backend.require_build_identity(layout, {"status": "complete", "build_identity": stale}, current=current)

    def test_build_identity_rejects_wrong_source_version(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        current = {"digest": "good", "lock_sha256": "aaa", "overlay_sha256": "bbb", "clang_version": "1", "clang_sha256": "ccc", "xcode": "x", "sdk": "s", "linker": "l", "rust": "r", "gn_args_sha256": "g"}
        stale = dict(current)
        stale["lock_sha256"] = "different-source-version"
        with self.assertRaisesRegex(backend.MacOSFailure, "lock|source|identity|mismatch|stale"):
            backend.require_build_identity(layout, {"status": "complete", "build_identity": stale}, current=current)

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
        with mock.patch.object(backend, "command_output", side_effect=responses), \
             self.assertRaisesRegex(backend.MacOSFailure, "Metal Toolchain is unavailable"):
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

    def test_build_release_manifest_records_payload_digest(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        archive = layout.release / "Chromium-1.0-macos-arm64.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"zip-placeholder")
        lock = {"chromium": {"version": "1.2.3.4", "commit": "c" * 40}, "ungoogled": {"commit": "u" * 40}}
        candidate = {"local_overlay_sha256": "o" * 64, "build_identity": {"digest": "d", "clang_version": "c", "clang_sha256": "s", "xcode": "x", "sdk": "sdk", "linker": "l", "rust": "r", "gn_args_sha256": "g"}, "signing": {"status": "ad-hoc"}}
        report = {"status": "complete", "fingerprint": "f" * 64}
        def fake_git(command, **kwargs):
            args = list(command)
            if args[0] == "git":
                return mock.Mock(returncode=0, stdout="c" * 40 + "\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(backend, "command_output", side_effect=fake_git):
            manifest = backend.build_release_manifest(layout, lock, candidate, report, app, archive, "a" * 64, "local-stage-only")
        self.assertEqual(manifest["payload_sha256"], "f" * 64)
        self.assertEqual(manifest["archive_sha256"], "a" * 64)
        self.assertEqual(manifest["chromium_version"], "1.2.3.4")
        self.assertEqual(manifest["source_commit"], "c" * 40)
        for key in backend.REQUIRED_MANIFEST_FIELDS:
            self.assertIn(key, manifest)

    def test_build_release_manifest_rejects_unknown_source_commit(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        archive = layout.release / "Chromium-1.0-macos-arm64.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"zip-placeholder")
        lock = {"chromium": {"version": "1.2.3.4", "commit": "c" * 40}, "ungoogled": {"commit": "u" * 40}}
        candidate = {"local_overlay_sha256": "o" * 64, "build_identity": {}, "signing": {"status": "ad-hoc"}}
        report = {"status": "complete", "fingerprint": "f" * 64}
        with mock.patch.object(backend, "command_output", return_value=mock.Mock(returncode=1, stdout="", stderr="no repo")), \
             self.assertRaisesRegex(backend.MacOSFailure, "source commit"):
            backend.build_release_manifest(layout, lock, candidate, report, app, archive, "a" * 64, "local-stage-only")

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
            "build_identity": {"digest": "test", "lock_sha256": "a", "overlay_sha256": "b", "clang_version": "c", "clang_sha256": "d", "xcode": "e", "sdk": "f", "linker": "g", "rust": "h", "gn_args_sha256": "i"},
            "app": str(app),
            "audit": {"status": "complete"},
        }
        backend.atomic_json(layout.stage / "candidate.json", candidate)
        backend.atomic_json(layout.stage / "accepted.json", {
            "schema": 1, "status": "complete", "fingerprint": fingerprint,
            "local_overlay_sha256": candidate["local_overlay_sha256"],
            "build_identity": dict(candidate["build_identity"]),
            "app": str(app), "executable": str(executable),
        })

        def fake_command(command, **kwargs):
            self.assertEqual(command[0], "/usr/bin/ditto")
            Path(command[-1]).write_bytes(b"zip-placeholder")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(backend, "isolated_environment", return_value={}),
            mock.patch.object(backend, "command_output", side_effect=fake_command),
            mock.patch.object(backend, "require_build_identity", return_value="test"),
            mock.patch.object(backend, "audit_bundle", return_value={"status": "complete"}),
            mock.patch.object(backend, "require_sandboxed_environment", return_value=None),
            mock.patch.object(backend, "build_release_manifest", return_value={key: "v" for key in backend.REQUIRED_MANIFEST_FIELDS}),
            mock.patch.object(backend, "write_release_files", return_value=None),
        ):
            report = backend.package_bundle(layout)

        archive = Path(report["archive"])
        self.assertTrue(archive.is_file())
        self.assertTrue(archive.with_name(f"{archive.name}.sha256").is_file())
        self.assertEqual(report["sha256"], backend.sha256(archive))
        self.assertFalse((layout.release / "build-manifest.json").exists())
        self.assertFalse((layout.release / "version").exists())

    def test_package_requires_accepted_record(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = layout.candidates / ("b" * 64) / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        fingerprint = backend.bundle_fingerprint(app)
        candidate = {
            "schema": 1, "status": "complete", "fingerprint": fingerprint,
            "local_overlay_sha256": backend.local_overlay_identity(layout)["sha256"],
            "build_identity": {"digest": "d1", "lock_sha256": "a", "overlay_sha256": "b", "clang_version": "c", "clang_sha256": "d", "xcode": "e", "sdk": "f", "linker": "g", "rust": "h", "gn_args_sha256": "i"},
            "app": str(app), "audit": {"status": "complete"},
        }
        backend.atomic_json(layout.stage / "candidate.json", candidate)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MACOS_SKIP_ACCEPTANCE", None)
            with mock.patch.object(backend, "isolated_environment", return_value={}), \
                 mock.patch.object(backend, "require_build_identity", return_value="d1"), \
                 mock.patch.object(backend, "audit_bundle", return_value={"status": "complete"}), \
                 mock.patch.object(backend, "require_sandboxed_environment", return_value=None), \
                 self.assertRaisesRegex(backend.MacOSFailure, "accepted|acceptance"):
                backend.package_bundle(layout)

    def test_package_rejects_fingerprint_mismatch_with_accepted(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = layout.candidates / ("c" * 64) / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        fingerprint = backend.bundle_fingerprint(app)
        candidate = {
            "schema": 1, "status": "complete", "fingerprint": fingerprint,
            "local_overlay_sha256": backend.local_overlay_identity(layout)["sha256"],
            "build_identity": {"digest": "d1", "lock_sha256": "a", "overlay_sha256": "b", "clang_version": "c", "clang_sha256": "d", "xcode": "e", "sdk": "f", "linker": "g", "rust": "h", "gn_args_sha256": "i"},
            "app": str(app), "audit": {"status": "complete"},
        }
        backend.atomic_json(layout.stage / "candidate.json", candidate)
        backend.atomic_json(layout.stage / "accepted.json", {
            "schema": 1, "status": "complete", "fingerprint": "different",
            "local_overlay_sha256": candidate["local_overlay_sha256"],
            "build_identity": candidate["build_identity"],
            "app": str(app), "executable": str(app / "Contents" / "MacOS" / "Chromium"),
        })
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MACOS_SKIP_ACCEPTANCE", None)
            with mock.patch.object(backend, "isolated_environment", return_value={}), \
                 mock.patch.object(backend, "require_build_identity", return_value="d1"), \
                 mock.patch.object(backend, "audit_bundle", return_value={"status": "complete"}), \
                 mock.patch.object(backend, "require_sandboxed_environment", return_value=None), \
                 self.assertRaisesRegex(backend.MacOSFailure, "fingerprint|accepted"):
                backend.package_bundle(layout)

    def test_package_rejects_identity_mismatch_with_accepted(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = layout.candidates / ("d" * 64) / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        fingerprint = backend.bundle_fingerprint(app)
        candidate_identity = {"digest": "d1", "lock_sha256": "a", "overlay_sha256": "b", "clang_version": "c", "clang_sha256": "d", "xcode": "e", "sdk": "f", "linker": "g", "rust": "h", "gn_args_sha256": "i"}
        candidate = {
            "schema": 1, "status": "complete", "fingerprint": fingerprint,
            "local_overlay_sha256": backend.local_overlay_identity(layout)["sha256"],
            "build_identity": candidate_identity,
            "app": str(app), "audit": {"status": "complete"},
        }
        backend.atomic_json(layout.stage / "candidate.json", candidate)
        accepted_identity = dict(candidate_identity)
        accepted_identity["lock_sha256"] = "different"
        backend.atomic_json(layout.stage / "accepted.json", {
            "schema": 1, "status": "complete", "fingerprint": fingerprint,
            "local_overlay_sha256": candidate["local_overlay_sha256"],
            "build_identity": accepted_identity,
            "app": str(app), "executable": str(app / "Contents" / "MacOS" / "Chromium"),
        })
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MACOS_SKIP_ACCEPTANCE", None)
            with mock.patch.object(backend, "isolated_environment", return_value={}), \
                 mock.patch.object(backend, "require_build_identity", return_value="d1"), \
                 mock.patch.object(backend, "audit_bundle", return_value={"status": "complete"}), \
                 mock.patch.object(backend, "require_sandboxed_environment", return_value=None), \
                 self.assertRaisesRegex(backend.MacOSFailure, "identity|mismatch"):
                backend.package_bundle(layout)

    def test_skipped_acceptance_yields_only_experimental_archive(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = layout.candidates / ("e" * 64) / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        fingerprint = backend.bundle_fingerprint(app)
        candidate = {
            "schema": 1, "status": "complete", "fingerprint": fingerprint,
            "local_overlay_sha256": backend.local_overlay_identity(layout)["sha256"],
            "build_identity": {"digest": "d1", "lock_sha256": "a", "overlay_sha256": "b", "clang_version": "c", "clang_sha256": "d", "xcode": "e", "sdk": "f", "linker": "g", "rust": "h", "gn_args_sha256": "i"},
            "app": str(app), "audit": {"status": "complete"},
        }
        backend.atomic_json(layout.stage / "candidate.json", candidate)

        def fake_command(command, **kwargs):
            Path(command[-1]).write_bytes(b"zip-placeholder")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.dict(os.environ, {"MACOS_SKIP_ACCEPTANCE": "1"}), \
             mock.patch.object(backend, "isolated_environment", return_value={}), \
             mock.patch.object(backend, "command_output", side_effect=fake_command), \
             mock.patch.object(backend, "require_build_identity", return_value="d1"), \
             mock.patch.object(backend, "audit_bundle", return_value={"status": "complete"}), \
             mock.patch.object(backend, "require_sandboxed_environment", return_value=None), \
             mock.patch.object(backend, "build_release_manifest", return_value={key: "v" for key in backend.REQUIRED_MANIFEST_FIELDS}), \
             mock.patch.object(backend, "write_release_files", return_value=None), \
             mock.patch.object(backend, "load_lock", return_value={"chromium": {"version": "1.2.3.4", "commit": "c" * 40}, "ungoogled": {"commit": "u" * 40}}):
            report = backend.package_bundle(layout)
        self.assertIn("-experimental.zip", report["archive"])
        self.assertEqual(report["acceptance"], "skipped")
        self.assertNotIn("Chromium-1.2.3.4-macos-arm64.zip", report["archive"])

    def _write_macho(self, path, *, pie=True, minos=(11, 0, 0), sdk=None, commands=None, filetype=2):
        import struct
        if commands is None:
            commands = []
        payload = b""
        for cmd, body in commands:
            cmdsize = 8 + len(body)
            payload += struct.pack("<2I", cmd, cmdsize) + body
        # Prepend LC_BUILD_VERSION for minos unless caller supplied its own minos command
        has_minos = any(cmd in (backend.LC_BUILD_VERSION, backend.LC_VERSION_MIN_MACOSX) for cmd, _ in commands)
        prefix = b""
        if not has_minos and minos is not None:
            raw = (minos[0] << 16) | (minos[1] << 8) | minos[2]
            sdk_raw = raw if sdk is None else (sdk[0] << 16) | (sdk[1] << 8) | sdk[2]
            prefix = struct.pack("<8I", backend.LC_BUILD_VERSION, 32, 1, raw, sdk_raw, 1, 0, 0)
        body_all = prefix + payload
        ncmds = (1 if prefix else 0) + len(commands)
        flags = backend.MH_PIE_FLAG if pie else 0
        header = struct.pack("<8I", backend.MH_MAGIC_64, backend.CPU_TYPE_ARM64, 0, filetype, ncmds, len(body_all), flags, 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header + body_all)

    def _minimal_app(self, name="Chromium.app", *, pie=True, minos=(11, 0, 0), commands=None, second_minos=None):
        from pathlib import Path as _P
        app = _P(str(self.root)) / name
        exe = app / "Contents" / "MacOS" / "Chromium"
        self._write_macho(exe, pie=pie, minos=minos, commands=commands)
        if second_minos is not None:
            helper = app / "Contents" / "MacOS" / "helper"
            self._write_macho(helper, pie=True, minos=second_minos, commands=commands)
        (app / "Contents" / "Info.plist").parent.mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "Info.plist").write_bytes(
            b'<?xml version="1.0"?><!DOCTYPE plist><plist version="1.0"><dict>'
            b'<key>CFBundleExecutable</key><string>Chromium</string>'
            b'<key>CFBundleIdentifier</key><string>test</string></dict></plist>'
        )
        return app

    def test_audit_rejects_missing_pie(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self._minimal_app(pie=False)
        with mock.patch.object(backend, "command_output") as run, \
             mock.patch.object(backend, "macho_architectures", side_effect=lambda p, e: ["arm64"] if "MacOS" in str(p) else None):
            def fake(command, **kwargs):
                args = list(command)
                if args[0] == "/usr/bin/otool":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if args[0] == "/usr/bin/codesign":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                return mock.Mock(returncode=0, stdout="", stderr="")
            run.side_effect = fake
            with self.assertRaisesRegex(backend.MacOSFailure, "MH_PIE|PIE"):
                backend.audit_bundle(app, layout, {})

    def test_audit_rejects_non_uniform_min_os(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self._minimal_app(minos=(11, 0, 0), second_minos=(12, 0, 0))
        with mock.patch.object(backend, "command_output") as run, \
             mock.patch.object(backend, "macho_architectures", side_effect=lambda p, e: ["arm64"] if "MacOS" in str(p) else None):
            def fake(command, **kwargs):
                args = list(command)
                if args[0] == "/usr/bin/otool":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if args[0] == "/usr/bin/codesign":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                return mock.Mock(returncode=0, stdout="", stderr="")
            run.side_effect = fake
            with mock.patch.object(backend, "xcode_identity", return_value={"xcode": "Xcode 27.0", "sdk": "27.0", "clang": "/c", "clangxx": "/c++", "linker": "/l", "clang_version": "1", "metal": "/m", "metal_version": "1"}), \
                 self.assertRaisesRegex(backend.MacOSFailure, "uniform|minimum OS"):
                backend.audit_bundle(app, layout, {})

    def test_audit_rejects_inadmissible_load_command(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        # 0x99 is not in the admissible set
        app = self._minimal_app(commands=[(0x99, b"12345678")])
        with mock.patch.object(backend, "command_output") as run, \
             mock.patch.object(backend, "macho_architectures", side_effect=lambda p, e: ["arm64"] if "MacOS" in str(p) else None):
            def fake(command, **kwargs):
                args = list(command)
                if args[0] == "/usr/bin/otool":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if args[0] == "/usr/bin/codesign":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                return mock.Mock(returncode=0, stdout="", stderr="")
            run.side_effect = fake
            with mock.patch.object(backend, "xcode_identity", return_value={"xcode": "Xcode 27.0", "sdk": "27.0", "clang": "/c", "clangxx": "/c++", "linker": "/l", "clang_version": "1", "metal": "/m", "metal_version": "1"}), \
                 self.assertRaisesRegex(backend.MacOSFailure, "inadmissible|load command"):
                backend.audit_bundle(app, layout, {})

    def test_audit_rejects_invalid_signature(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self._minimal_app()
        with mock.patch.object(backend, "command_output") as run, \
             mock.patch.object(backend, "macho_architectures", side_effect=lambda p, e: ["arm64"] if "MacOS" in str(p) else None):
            def fake(command, **kwargs):
                args = list(command)
                if args[0] == "/usr/bin/otool":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if args[0] == "/usr/bin/codesign":
                    return mock.Mock(returncode=1, stdout="", stderr="invalid signature")
                return mock.Mock(returncode=0, stdout="", stderr="")
            run.side_effect = fake
            with mock.patch.object(backend, "xcode_identity", return_value={"xcode": "Xcode 27.0", "sdk": "27.0", "clang": "/c", "clangxx": "/c++", "linker": "/l", "clang_version": "1", "metal": "/m", "metal_version": "1"}), \
                 self.assertRaisesRegex(backend.MacOSFailure, "signature|invalid"):
                backend.audit_bundle(app, layout, {})

    def test_signing_preserves_entitlements(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Entitled.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        binary = app / "Contents" / "MacOS" / "bin"
        binary.write_bytes(b"x")
        calls = {"n": 0}
        def fake(command, **kwargs):
            args = list(command)
            if args[0] == "/usr/bin/file":
                return mock.Mock(returncode=0, stdout="Mach-O 64-bit executable arm64\n", stderr="")
            if args[0] == "/usr/bin/lipo":
                return mock.Mock(returncode=0, stdout="arm64", stderr="")
            if args[:2] == ["/usr/bin/codesign", "--force"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:2] == ["/usr/bin/codesign", "--verify"]:
                return mock.Mock(returncode=0, stdout="valid", stderr="")
            if args[:2] == ["/usr/bin/codesign", "-d"]:
                calls["n"] += 1
                # First round (before) returns A; second round (after) returns B to simulate loss
                if calls["n"] <= 2:
                    return mock.Mock(returncode=0, stdout="entitlements-A", stderr="")
                return mock.Mock(returncode=0, stdout="entitlements-B", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(backend, "command_output", side_effect=fake), \
             self.assertRaisesRegex(backend.MacOSFailure, "entitlements"):
            backend.sign_bundle(app, layout, {})

    def test_signing_fails_when_entitlements_unreadable(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self.root / "Unreadable.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "bin").write_bytes(b"x")
        def fake(command, **kwargs):
            args = list(command)
            if args[:2] == ["/usr/bin/codesign", "-d"]:
                return mock.Mock(returncode=1, stdout="", stderr="oops")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(backend, "command_output", side_effect=fake), \
             self.assertRaisesRegex(backend.MacOSFailure, "cannot read entitlements"):
            backend.sign_bundle(app, layout, {})

    def test_verify_release_rejects_mutated_archive(self):
        import zipfile
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = layout.candidates / ("f" * 64) / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"x")
        (app / "Contents" / "Info.plist").write_bytes(
            b'<?xml version="1.0"?><plist version="1.0"><dict>'
            b'<key>CFBundleExecutable</key><string>Chromium</string></dict></plist>'
        )
        archive = layout.release / "Chromium-1.0-macos-arm64.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as zf:
            for path in sorted(app.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(app.parent))
        digest = backend.sha256(archive)
        manifest = {key: "v" for key in backend.REQUIRED_MANIFEST_FIELDS}
        manifest.update({"archive": str(archive), "archive_sha256": digest})
        backend.atomic_json(layout.metadata / "package-macos.json", {
            "schema": 1, "status": "complete", "archive": str(archive), "sha256": digest,
        })
        backend.atomic_json(layout.release / "release-manifest.json", manifest)
        # Mutate one byte of a copy
        mutated = layout.release / "mutated.zip"
        data = bytearray(archive.read_bytes())
        data[10] ^= 0xFF
        mutated.write_bytes(bytes(data))
        backend.atomic_json(layout.metadata / "package-macos.json", {
            "schema": 1, "status": "complete", "archive": str(mutated), "sha256": digest,
        })
        with self.assertRaisesRegex(backend.MacOSFailure, "digest mismatch|mismatch"):
            backend.verify_release_bundle(layout)
        leftovers = list((layout.root / "verify").rglob("*")) if (layout.root / "verify").exists() else []
        self.assertEqual(leftovers, [])

    def test_verify_release_rejects_missing_manifest_field(self):
        import zipfile
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = layout.candidates / ("f" * 64) / "Chromium.app"
        (app / "Contents").mkdir(parents=True)
        archive = layout.release / "Chromium-1.0-macos-arm64.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("empty", b"x")
        digest = backend.sha256(archive)
        backend.atomic_json(layout.metadata / "package-macos.json", {
            "schema": 1, "status": "complete", "archive": str(archive), "sha256": digest,
        })
        manifest = {key: "v" for key in backend.REQUIRED_MANIFEST_FIELDS if key != "archive_sha256"}
        backend.atomic_json(layout.release / "release-manifest.json", manifest)
        with self.assertRaisesRegex(backend.MacOSFailure, "missing fields|missing"):
            backend.verify_release_bundle(layout)
        leftovers = list((layout.root / "verify").rglob("*")) if (layout.root / "verify").exists() else []
        self.assertEqual(leftovers, [])

    def test_verify_release_rejects_digest_mismatch_and_cleans_up(self):
        import zipfile
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        archive = layout.release / "Chromium-1.0-macos-arm64.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("empty", b"x")
        manifest = {key: "v" for key in backend.REQUIRED_MANIFEST_FIELDS}
        manifest.update({"archive": str(archive), "archive_sha256": "0" * 64})
        backend.atomic_json(layout.metadata / "package-macos.json", {
            "schema": 1, "status": "complete", "archive": str(archive), "sha256": "0" * 64,
        })
        backend.atomic_json(layout.release / "release-manifest.json", manifest)
        with self.assertRaisesRegex(backend.MacOSFailure, "digest mismatch|mismatch"):
            backend.verify_release_bundle(layout)
        leftovers = list((layout.root / "verify").rglob("*")) if (layout.root / "verify").exists() else []
        self.assertEqual(leftovers, [])

    def test_admissible_load_command_set_is_pinned(self):
        self.assertEqual(
            backend.ADMISSIBLE_MACHO_LOAD_COMMANDS,
            frozenset({
                0x19, 0x2, 0xB, 0xC, 0xD, 0xE, 0x1B, 0x24, 0x32,
                0x2A, 0x28, 0x18, 0x26, 0x29, 0x1D, 0x1E, 0x2B,
                0x2C, 0x2D, 0x80000022 & ~0x80000000, 0x16, 0x11,
                0x1A, 0x33, 0x34,
            }),
        )

    def test_audit_allows_req_dyld_marked_main(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self._minimal_app(commands=[(0x80000028, b"12345678")])
        with mock.patch.object(backend, "command_output") as run, \
             mock.patch.object(backend, "macho_architectures", side_effect=lambda p, e: ["arm64"] if "MacOS" in str(p) else None):
            def fake(command, **kwargs):
                return mock.Mock(returncode=0, stdout="", stderr="")
            run.side_effect = fake
            with mock.patch.object(backend, "xcode_identity", return_value={"xcode": "Xcode 27.0", "sdk": "27.0", "clang": "/c", "clangxx": "/c++", "linker": "/l", "clang_version": "1", "metal": "/m", "metal_version": "1"}):
                report = backend.audit_bundle(app, layout, {})
        self.assertEqual(report["status"], "complete")

    def test_macho_min_os_reads_minos_not_sdk(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        binary = self.root / "versioned"
        self._write_macho(binary, pie=True, minos=(11, 0, 0), sdk=(27, 0, 0))
        self.assertEqual(backend.macho_min_os(binary), (11, 0, 0))

    def test_audit_allows_dylib_without_pie(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self._minimal_app()
        dylib = app / "Contents" / "MacOS" / "libtest.dylib"
        self._write_macho(dylib, pie=False, filetype=backend.MH_DYLIB)
        with mock.patch.object(backend, "command_output") as run, \
             mock.patch.object(backend, "macho_architectures", side_effect=lambda p, e: ["arm64"] if "MacOS" in str(p) else None):
            run.side_effect = lambda command, **kwargs: mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.object(backend, "xcode_identity", return_value={"xcode": "Xcode 27.0", "sdk": "27.0", "clang": "/c", "clangxx": "/c++", "linker": "/l", "clang_version": "1", "metal": "/m", "metal_version": "1"}):
                report = backend.audit_bundle(app, layout, {})
        self.assertEqual(report["status"], "complete")

    def test_audit_presign_skips_signature_verify(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        app = self._minimal_app()
        seen = []
        with mock.patch.object(backend, "command_output") as run, \
             mock.patch.object(backend, "macho_architectures", side_effect=lambda p, e: ["arm64"] if "MacOS" in str(p) else None):
            def fake(command, **kwargs):
                seen.append(next(iter(command)))
                return mock.Mock(returncode=0, stdout="", stderr="")
            run.side_effect = fake
            with mock.patch.object(backend, "xcode_identity", return_value={"xcode": "Xcode 27.0", "sdk": "27.0", "clang": "/c", "clangxx": "/c++", "linker": "/l", "clang_version": "1", "metal": "/m", "metal_version": "1"}):
                report = backend.audit_bundle(app, layout, {}, require_signatures=False)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["signature"], "unchecked-pre-sign")
        self.assertNotIn("/usr/bin/codesign", seen)

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
