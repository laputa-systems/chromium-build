#!/usr/bin/env python3
"""Focused tests for the native macOS path and its ownership boundary."""

from pathlib import Path
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
        self.repo = Path("/Volumes/dev/d/chromium-build")
        self.root = Path(tempfile.mkdtemp(prefix="macos-backend-test-", dir="/Volumes/dev"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_work_root_rejects_forbidden_and_ambiguous_paths(self):
        for value in ("/tmp/chromium", "~/chromium", "/Volumes/dev/with space"):
            with self.subTest(value=value), self.assertRaises(backend.MacOSFailure):
                backend.validate_work_root(value, self.repo)

        with self.assertRaises(backend.MacOSFailure):
            backend.validate_work_root("/Volumes/dev", self.repo)

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

    def test_architecture_normalization_is_native_only(self):
        self.assertEqual(backend.normalize_arch("aarch64"), "arm64")
        self.assertEqual(backend.normalize_arch("arm64"), "arm64")
        with self.assertRaises(backend.MacOSFailure):
            backend.normalize_arch("x86_64")

    def test_llvm_selection_prefers_2311_and_records_fallback_source(self):
        explicit = self.root / "llvm"
        explicit.mkdir()
        environment = {"CHROMIUM_LLVM_ROOT": str(explicit), "PATH": os.environ.get("PATH", "")}
        candidate = {
            "root": str(explicit),
            "source": "explicit",
            "version": "23.1.1",
            "version_tuple": (23, 1, 1),
            "clang": str(explicit / "bin" / "clang"),
            "clangxx": str(explicit / "bin" / "clang++"),
            "preferred": True,
        }
        with mock.patch.object(backend, "llvm_candidate", return_value=candidate):
            selected = backend.select_llvm(environment)
        self.assertEqual(selected["version"], "23.1.1")
        self.assertEqual(selected["selection_reason"], "preferred LLVM 23.1.1")
        self.assertEqual(selected["minimum"], "23.1.0")

    def test_xcode_identity_rejects_a_present_but_uninstalled_metal_toolchain(self):
        def result(stdout="", stderr="", returncode=0):
            return mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)

        missing_metal = "error: cannot execute tool 'metal' due to missing Metal Toolchain"
        responses = [
            result(stdout="Xcode 27.0\nBuild version 27A266a\n"),
            result(stdout="27.0\n"),
            result(stdout="/usr/bin/clang\n"),
            result(stdout="Apple clang version 21.0.0\n"),
            result(stdout="/usr/bin/clang\n"),
            result(stderr=missing_metal, returncode=1),
        ]
        with mock.patch.object(backend, "command_output", side_effect=responses):
            with self.assertRaisesRegex(backend.MacOSFailure, "Metal Toolchain is unavailable"):
                backend.xcode_identity({})

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

    def test_bundle_fingerprint_changes_with_owned_content(self):
        app = self.root / "Chromium.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"arm64-test")
        first = backend.bundle_fingerprint(app)
        (app / "Contents" / "MacOS" / "Chromium").write_bytes(b"arm64-test-2")
        self.assertNotEqual(first, backend.bundle_fingerprint(app))

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
        self.assertNotIn("safe_browsing:archive_analyzer_results", build_file.read_text(encoding="utf-8"))
        self.assertNotIn("safe_browsing:disk_image_type_sniffer_mac", build_file.read_text(encoding="utf-8"))
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
        self.assertEqual(reused["english_only_locale"]["grd_outputs"]["status"], "reused")

    def test_reset_requires_confirmation_and_preserves_inputs(self):
        layout = backend.WorkLayout(self.root, self.repo)
        layout.ensure()
        layout.source.mkdir(parents=True)
        (layout.source / "owned.txt").write_text("owned", encoding="utf-8")
        (layout.inputs / "locked.bin").write_bytes(b"locked")
        with self.assertRaises(backend.MacOSFailure):
            backend.reset_work(layout, False)
        backend.reset_work(layout, True)
        self.assertFalse(layout.source.exists())
        self.assertTrue((layout.inputs / "locked.bin").is_file())


if __name__ == "__main__":
    unittest.main()
