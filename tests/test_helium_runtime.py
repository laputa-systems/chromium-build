"""Runtime collection and ELF audit contracts for the Helium package."""

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import linux_runtime

STAGE = runpy.run_path(str(SCRIPTS / "stage-runtime.py"))
PACKAGE = runpy.run_path(str(SCRIPTS / "package-runtime.py"))
INSTALL = runpy.run_path(str(SCRIPTS / "runtime_install.py"))
CC = os.environ.get("CC", "cc")
GLIBC_ROOT = Path("/opt/chromium-glibc/lib")


def compile_program(
    directory: Path,
    name: str,
    source: str,
    arguments: list[str] | None = None,
    compiler: str | None = None,
) -> Path:
    source_path = directory / f"{name}.c"
    source_path.write_text(source, encoding="utf-8")
    output = directory / name
    subprocess.run(
        [compiler or CC, str(source_path), "-o", str(output), *(arguments or [])],
        check=True,
        capture_output=True,
    )
    return output


def compile_shared(directory: Path, name: str, source: str, arguments: list[str] | None = None) -> Path:
    source_path = directory / f"{name}.c"
    source_path.write_text(source, encoding="utf-8")
    output = directory / f"{name}.so"
    subprocess.run(
        [CC, "-shared", "-fPIC", str(source_path), "-o", str(output), *(arguments or [])],
        check=True,
        capture_output=True,
    )
    return output


def glibc_program(directory: Path, name: str = "glibc-helper") -> Path:
    if not (GLIBC_ROOT / "libc.so.6").exists():
        raise unittest.SkipTest("the pinned glibc host sysroot is unavailable")
    source = directory / f"{name}.c"
    source.write_text("void _start(void){for(;;);}\n", encoding="utf-8")
    output = directory / name
    subprocess.run(
        [
            CC,
            "-nostdlib",
            "-Wl,-e,_start",
            "-Wl,--no-as-needed",
            "-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2",
            str(source),
            f"-L{GLIBC_ROOT}",
            "-l:libc.so.6",
            "-o",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def glibc_shared(directory: Path, name: str = "libglibc.so", soname: str | None = None) -> Path:
    if not (GLIBC_ROOT / "libc.so.6").exists():
        raise unittest.SkipTest("the pinned glibc host sysroot is unavailable")
    source = directory / f"{name}.c"
    source.write_text("int glibc_helper(void){return 0;}\n", encoding="utf-8")
    output = directory / name
    arguments = [
        "-nostdlib",
        "-shared",
        "-fPIC",
        "-Wl,--no-as-needed",
        f"-L{GLIBC_ROOT}",
        "-l:libc.so.6",
    ]
    if soname:
        arguments.append(f"-Wl,-soname,{soname}")
    subprocess.run([CC, *arguments, str(source), "-o", str(output)], check=True, capture_output=True)
    return output


def write_stage_inputs(root: Path) -> tuple[Path, Path, Path]:
    out = root / "out"
    source = root / "src"
    out.mkdir()
    (source / "chrome/app/theme/chromium/linux").mkdir(parents=True)
    (source / "chrome/app/theme/chromium/linux/product_logo_256.png").write_bytes(b"icon")
    (out / "helium").write_bytes(b"browser")
    for name in ("helium_crashpad_handler", "chrome_sandbox", "libEGL.so", "libGLESv2.so"):
        (out / name).write_bytes(name.encode())
    for name in ("chrome_100_percent.pak", "chrome_200_percent.pak", "resources.pak", "icudtl.dat", "v8_context_snapshot.bin"):
        (out / name).write_bytes(name.encode())
    (out / "locales").mkdir()
    for name in ("en-US.pak", "de.pak"):
        (out / "locales" / name).write_bytes(name.encode())
    runtime_deps = out / "helium.runtime_deps"
    runtime_deps.write_text("helium\nlocales/en-US.pak\nlocales/de.pak\n", encoding="utf-8")
    return out, source, runtime_deps


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_archive(
    root: Path,
    version: str = "0.20.3.1",
    *,
    product: str = "helium",
    manifest_schema: int = 1,
    include_manifest: bool = True,
    acceptance_product: str = "helium",
    corrupt_file: bool = False,
    extra: dict[str, bytes] | None = None,
    symlink: str | None = None,
) -> Path:
    root_name = f"helium-{version}-linux-x86_64-musl"
    payload = root / root_name
    payload.mkdir(parents=True)
    (payload / "helium").write_bytes(Path("/bin/true").read_bytes())
    (payload / "helium").chmod(0o755)
    (payload / "helium-wrapper").write_text("#!/bin/sh\nprintf 'Helium 0.20.3.1\\n'\n", encoding="utf-8")
    (payload / "helium-wrapper").chmod(0o755)
    (payload / "helium.desktop").write_text("[Desktop Entry]\nName=Helium\n", encoding="utf-8")
    for name, content in (extra or {}).items():
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if symlink:
        (payload / symlink).symlink_to("../../outside")
    files = []
    for path in sorted(payload.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(payload).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": file_digest(path),
                    "mode": oct(path.stat().st_mode & 0o7777),
                }
            )
    manifest = {
        "schema": manifest_schema,
        "status": "complete",
        "product": product,
        "product_version": version,
        "architecture": "amd64",
        "launcher": "helium-wrapper",
        "executable": "helium",
        "files": files,
    }
    if include_manifest:
        (payload / "package-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if corrupt_file:
        (payload / "helium").write_bytes(b"corrupt")
    archive = root / f"{root_name}.tar.xz"
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(payload, arcname=root_name)
    archive_digest = file_digest(archive)
    (archive.with_name(archive.name + ".sha256")).write_text(f"{archive_digest}  {archive.name}\n", encoding="utf-8")
    (archive.with_name(archive.name + ".acceptance.json")).write_text(
        json.dumps(
            {
                "schema": 1,
                "status": "complete",
                "product": acceptance_product,
                "product_version": version,
                "architecture": "amd64",
                "archive": archive.name,
                "archive_root": root_name,
                "archive_sha256": archive_digest,
                "checks": {"package_audit": True},
            }
        ),
        encoding="utf-8",
    )
    return archive


def fake_home(root: Path) -> tuple[Path, Path, Path]:
    home = root / "home"
    (home / ".local/opt").mkdir(parents=True)
    (home / ".local/bin").mkdir(parents=True)
    return home, home / ".local/opt/helium-wayland-release", home / ".local/bin"


class RuntimeCollectionTests(unittest.TestCase):
    def test_all_locales_required_entries_and_metadata_are_collected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, source, runtime_deps = write_stage_inputs(root)
            files, generated, omitted = STAGE["collect_helium_files"](out / "helium", runtime_deps, source)
        self.assertEqual(files[Path("locales/en-US.pak")], out / "locales/en-US.pak")
        self.assertIn(Path("locales/de.pak"), files)
        self.assertIn(Path("helium.png"), files)
        self.assertIn(Path("helium.desktop"), generated)
        reasons = {entry["path"]: entry["reason"] for entry in omitted}
        self.assertIn("chromedriver", reasons)
        self.assertIn("libqt6_shim.so", reasons)
        self.assertIn("libvulkan.so.1", reasons)

    def test_missing_runtime_dependency_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, source, runtime_deps = write_stage_inputs(root)
            runtime_deps.unlink()
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "runtime dependency list"):
                STAGE["collect_helium_files"](out / "helium", runtime_deps, source)

    def test_absolute_and_traversal_runtime_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deps"
            for value in ("/etc/passwd", "../outside", "locales/../../outside"):
                path.write_text(value + "\n", encoding="utf-8")
                with self.assertRaises(linux_runtime.RuntimeAuditError):
                    linux_runtime.read_runtime_deps(path)

    def test_escaping_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, source, runtime_deps = write_stage_inputs(root)
            outside = root / "outside"
            outside.write_bytes(b"outside")
            (out / "linked").symlink_to(outside)
            runtime_deps.write_text("linked\n", encoding="utf-8")
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "escapes"):
                STAGE["collect_helium_files"](out / "helium", runtime_deps, source)

    def test_conflicting_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            files = {}
            STAGE["add_file"](files, Path("browser"), first, (root,))
            with self.assertRaisesRegex(RuntimeError, "duplicate destination"):
                STAGE["add_file"](files, Path("browser"), second, (root,))


class ElfAuditTests(unittest.TestCase):
    def test_parser_rejects_missing_header_fields(self):
        with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "readelf output has no"):
            linux_runtime.parse_readelf(Path("x"), Path("x"), "Class: ELF64\n")

    def test_musl_runtime_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = compile_program(root, "browser", "int main(void){return 0;}\n")
            info = linux_runtime.inspect_elf(program, root)
            if info.interpreter != linux_runtime.MUSL_INTERPRETERS["amd64"]:
                self.skipTest("the local compiler does not target musl")
            report = linux_runtime.audit_tree(root, "amd64")
        self.assertEqual(report["status"], "complete")

    def test_nested_wrong_loader_helper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "helpers"
            helper.mkdir()
            glibc_program(helper)
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "wrong ELF interpreter"):
                linux_runtime.audit_tree(root, "amd64")

    def test_transitive_glibc_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lib = root / "lib"
            lib.mkdir()
            glibc_shared(lib)
            program = compile_program(
                root,
                "browser",
                "int main(void){return 0;}\n",
                [f"-L{lib}", "-Wl,--no-as-needed", "-lglibc", "-Wl,-rpath,$ORIGIN/lib"],
            )
            info = linux_runtime.inspect_elf(program, root)
            if info.interpreter != linux_runtime.MUSL_INTERPRETERS["amd64"]:
                self.skipTest("the local compiler does not target musl")
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "glibc library"):
                linux_runtime.audit_tree(root, "amd64")

    def test_missing_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lib = root / "lib"
            lib.mkdir()
            compile_shared(lib, "libmissing", "int missing(void){return 1;}\n")
            program = compile_program(
                root,
                "browser",
                "int main(void){return 0;}\n",
                [f"-L{lib}", "-Wl,--no-as-needed", "-lmissing", "-Wl,-rpath,$ORIGIN/lib"],
            )
            info = linux_runtime.inspect_elf(program, root)
            if info.interpreter != linux_runtime.MUSL_INTERPRETERS["amd64"]:
                self.skipTest("the local compiler does not target musl")
            (lib / "libmissing.so").unlink()
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "missing runtime library"):
                linux_runtime.audit_tree(root, "amd64")

    def test_misplaced_soname_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lib = root / "lib"
            nested = root / "helpers"
            lib.mkdir()
            nested.mkdir()
            shutil.copy2(compile_shared(lib, "one", "int one(void){return 1;}\n", ["-Wl,-soname,libshared.so"]), lib / "libone.so")
            shutil.copy2(compile_shared(nested, "two", "int two(void){return 2;}\n", ["-Wl,-soname,libshared.so"]), nested / "libtwo.so")
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "SONAME"):
                linux_runtime.audit_tree(root, "amd64")

    def test_direct_browser_gnu_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = compile_program(
                root,
                "browser",
                "int main(void){return 0;}\n",
                ["-Wl,--no-as-needed", "-l:libstdc++.so.6"],
                compiler=shutil.which("cc") or CC,
            )
            info = linux_runtime.inspect_elf(program, root)
            if info.interpreter != linux_runtime.MUSL_INTERPRETERS["amd64"]:
                self.skipTest("the local compiler does not target musl")
            if "libstdc++.so.6" not in info.needed:
                self.skipTest("the local C++ runtime is not dynamically linked")
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "GNU C\\+\\+ runtime"):
                linux_runtime.audit_tree(root, "amd64")

    def test_escaping_file_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_bytes(b"outside")
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "helper").symlink_to(outside)
            with self.assertRaisesRegex(linux_runtime.RuntimeAuditError, "symlink escapes"):
                linux_runtime.audit_tree(runtime, "amd64")


class PackageTests(unittest.TestCase):
    def test_archive_name_and_launcher_contract(self):
        self.assertEqual(PACKAGE["archive_name"]("0.20.3.1"), "helium-0.20.3.1-linux-x86_64-musl")
        launcher = PACKAGE["HELIUM_LAUNCHER"]
        self.assertIn('exec "$root/helium" "$@"', launcher)
        self.assertIn('LD_LIBRARY_PATH="$root/lib', launcher)
        self.assertNotIn("--disable-gpu", launcher)
        self.assertNotIn("--no-sandbox", launcher)

    def test_archive_verification_rejects_escaping_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.tar.xz"
            payload = root / "payload"
            payload.write_text("x", encoding="utf-8")
            with tarfile.open(archive, "w:xz") as bundle:
                bundle.add(payload, arcname="../escape")
            with self.assertRaisesRegex(RuntimeError, "escapes its root"):
                PACKAGE["verify_archive"](archive, "expected-root", {"files": [], "architecture": "amd64"})

    def test_staged_digest_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            (stage / "browser").write_bytes(b"browser")
            manifest = {"files": [{"path": "browser", "sha256": "0" * 64}]}
            with self.assertRaisesRegex(RuntimeError, "changed after staging"):
                PACKAGE["copy_staged"](stage, root / "package", manifest)


class InstallTests(unittest.TestCase):
    def test_verify_extract_strips_the_archive_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = make_archive(root)
            destination = root / "extracted"
            INSTALL["verify_extract"](archive, destination)
            self.assertTrue((destination / "helium").is_file())
            self.assertTrue((destination / "helium-wrapper").is_file())
            self.assertFalse((destination / "package-manifest.json").is_dir())

    def test_first_install_and_identical_rerun_keep_one_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = make_archive(root)
            home, prefix, bin_dir = fake_home(root)
            INSTALL["install"](archive, prefix, bin_dir, home, None)
            self.assertTrue((prefix / "helium-wrapper").is_file())
            self.assertEqual((bin_dir / "helium").resolve(), (prefix / "helium-wrapper").resolve())
            first_slot = prefix.resolve()
            INSTALL["install"](archive, prefix, bin_dir, home, None)
            self.assertEqual(prefix.resolve(), first_slot)
            self.assertEqual(len(list((Path(str(prefix) + ".versions")).iterdir())), 1)

    def test_upgrade_and_rollback_switch_owned_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_archive(root / "first", "0.20.3.1")
            second = make_archive(root / "second", "0.20.3.2")
            home, prefix, bin_dir = fake_home(root)
            INSTALL["install"](first, prefix, bin_dir, home, None)
            old_slot = prefix.resolve()
            INSTALL["install"](second, prefix, bin_dir, home, None)
            new_slot = prefix.resolve()
            self.assertNotEqual(old_slot, new_slot)
            self.assertEqual((Path(str(prefix) + ".previous")).resolve(), old_slot)
            INSTALL["rollback"](prefix, bin_dir, home, None)
            self.assertEqual(prefix.resolve(), old_slot)

    def test_checksum_corruption_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = make_archive(root)
            archive.write_bytes(archive.read_bytes() + b"changed")
            home, prefix, bin_dir = fake_home(root)
            with self.assertRaisesRegex(INSTALL["InstallError"], "checksum"):
                INSTALL["install"](archive, prefix, bin_dir, home, None)
            self.assertFalse(prefix.exists())
            self.assertFalse((bin_dir / "helium").exists())

    def test_missing_manifest_and_wrong_product_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(INSTALL["InstallError"], "manifest"):
                with INSTALL["verified_archive"](make_archive(root / "missing", include_manifest=False)):
                    pass
            with self.assertRaisesRegex(INSTALL["InstallError"], "not helium"):
                with INSTALL["verified_archive"](make_archive(root / "wrong", acceptance_product="chromium")):
                    pass

    def test_unsupported_schema_and_digest_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(INSTALL["InstallError"], "schema"):
                with INSTALL["verified_archive"](make_archive(root / "schema", manifest_schema=2)):
                    pass
            with self.assertRaisesRegex(INSTALL["InstallError"], "digest"):
                with INSTALL["verified_archive"](make_archive(root / "digest", corrupt_file=True)):
                    pass

    def test_archive_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(INSTALL["InstallError"], "link escapes"):
                with INSTALL["verified_archive"](make_archive(root, symlink="escape")):
                    pass

    def test_foreign_prefix_and_launcher_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = make_archive(root)
            home, prefix, bin_dir = fake_home(root)
            prefix.mkdir()
            with self.assertRaisesRegex(INSTALL["InstallError"], "not a managed symlink"):
                INSTALL["install"](archive, prefix, bin_dir, home, None)
            prefix.rmdir()
            (bin_dir / "helium").write_text("foreign", encoding="utf-8")
            with self.assertRaisesRegex(INSTALL["InstallError"], "launcher exists"):
                INSTALL["install"](archive, prefix, bin_dir, home, None)

    def test_foreign_usr_bin_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, prefix, bin_dir = fake_home(root)
            (home / "usr").mkdir()
            (home / "usr/bin").symlink_to(home / "elsewhere")
            with self.assertRaisesRegex(INSTALL["InstallError"], "usr/bin"):
                INSTALL["install"](make_archive(root), prefix, bin_dir, home, None)

    def test_broken_foreign_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, prefix, bin_dir = fake_home(root)
            prefix.symlink_to(root / "outside")
            with self.assertRaisesRegex(INSTALL["InstallError"], "outside"):
                INSTALL["install"](make_archive(root), prefix, bin_dir, home, None)

    def test_failed_final_probe_restores_the_previous_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_archive(root / "first", "0.20.3.1")
            second = make_archive(root / "second", "0.20.3.2")
            home, prefix, bin_dir = fake_home(root)
            INSTALL["install"](first, prefix, bin_dir, home, None)
            old = prefix.resolve()
            probe = root / "probe"
            probe.write_text("#!/bin/sh\ncase \"$1\" in */bin/helium) exit 1;; esac\nexit 0\n", encoding="utf-8")
            probe.chmod(0o755)
            with self.assertRaisesRegex(INSTALL["InstallError"], "probe failed"):
                INSTALL["install"](second, prefix, bin_dir, home, probe)
            self.assertEqual(prefix.resolve(), old)
            self.assertEqual((bin_dir / "helium").resolve(), (old / "helium-wrapper").resolve())
            self.assertFalse((Path(str(prefix) + ".previous")).exists())

    def test_rollback_without_previous_slot_fails_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = make_archive(root)
            home, prefix, bin_dir = fake_home(root)
            INSTALL["install"](archive, prefix, bin_dir, home, None)
            active = prefix.resolve()
            with self.assertRaisesRegex(INSTALL["InstallError"], "no previous"):
                INSTALL["rollback"](prefix, bin_dir, home, None)
            self.assertEqual(prefix.resolve(), active)


if __name__ == "__main__":
    unittest.main()
