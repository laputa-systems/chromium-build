"""Permissions for verified inputs consumed by the unprivileged browser tests."""

import hashlib
import io
import json
import runpy
import tempfile
import unittest
import urllib.error
from http.client import HTTPMessage
from pathlib import Path
from unittest import mock

from script_support import ScriptFailure

ROOT = Path(__file__).resolve().parents[1]
FETCH = runpy.run_path(str(ROOT / "scripts/fetch-inputs.py"))
FETCH_ONE = FETCH["fetch_one"]
LOCKED_INPUTS = FETCH["locked_inputs"]
STAGE_BUILDER_APKS = FETCH["stage_builder_apks"]


class FetchInputsTest(unittest.TestCase):
    def test_builder_context_rechecks_exact_archive_bytes_and_rejects_unlocked_packages(self):
        package = "openssl-3.5.9-r1"
        content = b"test package bytes"
        archive = {
            "url": f"https://dl-cdn.alpinelinux.org/alpine/v3.24/main/aarch64/{package}.apk",
            "sha512": hashlib.sha512(content).hexdigest(), "size": len(content),
        }
        lock = {"status": "resolved", "architecture": "arm64", "base_release": "3.24.2",
                "archives": {package: archive}, "resolved": [package]}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            identity = json.dumps(lock["archives"], sort_keys=True, separators=(",", ":"))
            staged = cache / hashlib.sha256(identity.encode()).hexdigest()
            staged.mkdir()
            path = staged / f"{package}.apk"
            path.write_bytes(content)
            self.assertEqual(STAGE_BUILDER_APKS(lock, "fixture lock", "arm64", cache), staged)

            path.write_bytes(b"?" * len(content))
            with self.assertRaisesRegex(ScriptFailure, "SHA-512 mismatch"):
                STAGE_BUILDER_APKS(lock, "fixture lock", "arm64", cache)
            path.write_bytes(content)
            (staged / "extra.apk").touch()
            with self.assertRaisesRegex(ScriptFailure, "unexpected builder APK"):
                STAGE_BUILDER_APKS(lock, "fixture lock", "arm64", cache)

        archive["url"] = archive["url"].replace("/v3.24/", "/edge/")
        with self.assertRaisesRegex(ScriptFailure, "invalid builder APK archive"):
            STAGE_BUILDER_APKS(lock, "fixture lock", "arm64", cache)

    def test_builder_apks_fall_back_to_another_alpine_mirror_on_http_errors(self):
        package = "openssl-3.5.9-r1"
        content = b"test package bytes"
        locked = f"https://dl-cdn.alpinelinux.org/alpine/v3.24/main/aarch64/{package}.apk"
        archive = {"url": locked, "sha512": hashlib.sha512(content).hexdigest(), "size": len(content)}
        mirror = f"https://github.com/laputa-systems/chromium-build/releases/download/alpine-apks-v3.24/aarch64-{package}.apk"
        lock = {"status": "resolved", "architecture": "arm64", "base_release": "3.24.2",
                "archives": {package: archive}, "resolved": [package]}
        requested = []

        class Response(io.BytesIO):
            def __init__(self, url, body):
                super().__init__(body)
                self.url = url

            def geturl(self):
                return self.url

        def opener(body):
            class Opener:
                def open(self, request):
                    requested.append(request.full_url)
                    if "dl-cdn." in request.full_url or "dl-4." in request.full_url:
                        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", HTTPMessage(), None)
                    return Response(request.full_url, body)
            return lambda *handlers: Opener()

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("urllib.request.build_opener", opener(content)):
            staged = STAGE_BUILDER_APKS(lock, "fixture lock", "arm64", Path(directory))
            self.assertEqual((staged / f"{package}.apk").read_bytes(), content)
        self.assertEqual(requested, [locked, mirror])

        requested.clear()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("urllib.request.build_opener", opener(b"?" * len(content))), \
                self.assertRaisesRegex(ScriptFailure, "SHA-512 mismatch"):
            STAGE_BUILDER_APKS(lock, "fixture lock", "arm64", Path(directory))
        self.assertEqual(requested, [locked, mirror])

    def test_a_builder_apk_no_mirror_serves_names_the_package_and_each_url(self):
        package = "openssl-3.5.9-r1"
        locked = f"https://dl-cdn.alpinelinux.org/alpine/v3.24/main/aarch64/{package}.apk"
        archive = {"url": locked, "sha512": "0" * 128, "size": 1}
        lock = {"status": "resolved", "architecture": "arm64", "base_release": "3.24.2",
                "archives": {package: archive}, "resolved": [package]}

        class Opener:
            def open(self, request):
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", HTTPMessage(), None)

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("urllib.request.build_opener", lambda *handlers: Opener()), \
                self.assertRaises(ScriptFailure) as caught:
            STAGE_BUILDER_APKS(lock, "fixture lock", "arm64", Path(directory))
        message = str(caught.exception)
        self.assertIn(package, message)
        self.assertIn(f"releases/download/alpine-apks-v3.24/aarch64-{package}.apk", message)
        for host in ("dl-cdn", "dl-4", "dl-5"):
            self.assertIn(locked.replace("dl-cdn.", f"{host}."), message)

    def test_reused_verified_extension_is_readable_by_browser_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "extension.zip"
            content = b"verified extension archive"
            archive.write_bytes(content)
            archive.chmod(0o600)
            item = {
                "name": "extension",
                "filename": archive.name,
                "size": len(content),
                "sha512": hashlib.sha512(content).hexdigest(),
                "url": "https://example.invalid/extension.zip",
            }

            self.assertEqual(FETCH_ONE(item, root)["action"], "reused")
            self.assertEqual(archive.stat().st_mode & 0o444, 0o444)

    def test_only_arm64_helium_fetches_native_host_tools(self):
        lock = json.loads((ROOT / "config/helium.inputs.lock").read_text(encoding="utf-8"))
        arm64 = {item["name"]: item for item in LOCKED_INPUTS(lock, "arm64")}
        amd64 = {item["name"] for item in LOCKED_INPUTS(lock, "amd64")}

        self.assertEqual(arm64["rust_toolchain"]["destination"], "third_party/rust-toolchain")
        self.assertTrue(arm64["rust_toolchain"]["filename"].endswith("-aarch64-linux-musl.tar.xz"))
        self.assertEqual(set(arm64) - amd64, {"rust_toolchain", "typescript_native", "esbuild_native"})

    def test_architecture_inputs_reject_an_unlisted_architecture(self):
        lock = json.loads((ROOT / "config/helium.inputs.lock").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(RuntimeError, "no architecture inputs"):
            LOCKED_INPUTS(lock, "")
