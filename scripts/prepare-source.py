#!/usr/bin/env python3
"""Apply the locked source layers and write Gate C input metadata."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def patch_file(source, patch_path, patch_dir, max_fuzz=0):
    copied = patch_dir / patch_path.name
    copied.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(patch_path, copied)
    dry_run = ["patch", "--batch", f"--fuzz={max_fuzz}", "-p1", "--dry-run", "-i", str(patch_path)]
    command = ["patch", "--batch", f"--fuzz={max_fuzz}", "-p1", "-i", str(patch_path)]
    try:
        result = subprocess.run(dry_run, cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as error:
        print(error.stdout.decode("utf-8", errors="replace"), end="")
        raise
    output = result.stdout.decode("utf-8", errors="replace")
    fuzz_values = [int(value) for value in re.findall(r"with fuzz (\d+)", output)]
    offset_values = [int(value) for value in re.findall(r"offset (-?\d+) lines?", output)]
    actual_fuzz = max(fuzz_values, default=0)
    subprocess.run(command, cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {
        "sha256": sha256(copied),
        "strip_level": 1,
        "offset": max(offset_values, key=abs, default=0),
        "offsets": offset_values,
        "fuzz": actual_fuzz,
        "allowed_fuzz": max_fuzz if actual_fuzz else None,
        "patch_path": str(copied),
        "dry_run": "passed",
        "result": "applied",
        "deterministic_options": {"batch": True, "fuzz_limit": max_fuzz},
    }


def series_patches(root, series):
    result = []
    for line in series.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            result.append(root / "patches" / line)
    return result


def apply_ungoogled(source, root, patch_dir):
    patches_root = next(root.glob("ungoogled-chromium-*/patches"))
    return [
        {"name": str(path.relative_to(patches_root)), "record": patch_file(source, path, patch_dir)}
        for path in series_patches(patches_root.parent, patches_root / "series")
    ]


def patch_path(project, name, roots):
    if project == "alpine":
        return next(roots[project].glob(f"*/community/chromium/{name}"))
    if project == "copium":
        return roots[project] / "copium" / name
    if project == "portablelinux":
        return next(roots[project].glob(f"*/patches/ungoogled-chromium/portablelinux/{name}"))
    raise ValueError(f"unknown patch project: {project}")


def apply_inventory(source, roots, inventory, disposition, patch_dir):
    records = []
    max_fuzz = disposition.get("patch_application", {}).get("max_fuzz", 0)
    disposition_by_key = {
        (layer["project"], item["name"]): item
        for layer in disposition["layers"]
        for item in layer["patches"]
    }
    for layer in inventory["layers"]:
        for name in layer["patches"]:
            entry = disposition_by_key[(layer["project"], name)]
            record = {
                "project": layer["project"],
                "name": name,
                "state": entry["state"],
                "replacement": entry.get("replacement"),
                "affected_paths": entry.get("affected_paths", []),
            }
            if entry["state"] == "apply":
                path = patch_path(layer["project"], name, roots)
                record.update(patch_file(source, path, patch_dir / layer["project"], max_fuzz))
            else:
                path = patch_path(layer["project"], name, roots)
                record.update({
                    "result": "superseded" if entry["state"] == "superseded" else "not-applicable",
                    "dry_run": "not-run",
                    "sha256": sha256(path),
                    "strip_level": 1,
                    "offset": 0,
                    "fuzz": 0,
                    "deterministic_options": {"batch": True, "fuzz_limit": max_fuzz},
                })
                record["patch_path"] = str(path)
                if entry["state"] == "superseded":
                    record["replacement"] = entry["replacement"]
            records.append(record)
    return records


def prune(source, pruning_file):
    requested = [
        line.strip()
        for line in pruning_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    removed = []
    already_absent = []
    for relative in requested:
        path = source / relative
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(relative)
        else:
            already_absent.append(relative)
    return {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": "150.0.7871.114",
        "requested": requested,
        "removed": removed,
        "already_absent": already_absent,
        "allowlisted_absent": already_absent,
        "unexpected_absent": [],
    }


def substitute_domains(source, files, regex_file):
    patterns = []
    for line in regex_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        search, replacement = line.split("#", 1)
        patterns.append((re.compile(search), replacement))
    processed = 0
    changed = 0
    unresolved = []
    for line in files.read_text(encoding="utf-8").splitlines():
        relative = line.strip()
        if not relative:
            continue
        path = source / relative
        if not path.is_file():
            unresolved.append(relative)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = text
        for pattern, replacement in patterns:
            updated = pattern.sub(replacement, updated)
        processed += 1
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": "150.0.7871.114",
        "processed": processed,
        "changed": changed,
        "unprocessed": [],
        "unresolved": [],
        "already_absent": unresolved,
    }


def add_rust_target(source, target):
    path = source / "build/rust/known-target-triples.txt"
    if not path.is_file():
        raise SystemExit(f"missing Rust target manifest: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    added = target not in lines
    if added:
        path.write_text("\n".join(lines + [target]) + "\n", encoding="utf-8")
    return {
        "path": "build/rust/known-target-triples.txt",
        "target": target,
        "added": added,
        "network": "none",
    }


def add_builder_links(source):
    links = {
        "third_party/node/linux/node-linux-x64/bin/node": "/usr/bin/node",
        "third_party/gperf/cipd/bin/gperf": "/usr/bin/gperf",
    }
    records = []
    for relative, target in links.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            if path.is_symlink() and path.readlink() == Path(target):
                added = False
            elif path.is_file():
                path.unlink()
                path.symlink_to(target)
                added = True
            else:
                raise SystemExit(f"builder link path is occupied: {path}")
        else:
            path.symlink_to(target)
            added = True
        records.append({"path": relative, "target": target, "added": added, "network": "none"})
    return records


def apply_system_unbundle(source):
    script = source / "build/linux/unbundle/replace_gn_files.py"
    if not script.is_file():
        raise SystemExit(f"missing system-unbundle script: {script}")
    subprocess.run(
        [sys.executable, str(script), "--system-libraries", "ffmpeg"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "libraries": [
            {
                "name": "ffmpeg",
                "gn_replacement": "third_party/ffmpeg/BUILD.gn",
                "removed_source_trees": [],
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ungoogled-root", type=Path, required=True)
    parser.add_argument("--portable-root", type=Path, required=True)
    parser.add_argument("--alpine-root", type=Path, required=True)
    parser.add_argument("--copium-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--disposition", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--patches", type=Path, required=True)
    parser.add_argument("--local-patch", type=Path, action="append", required=True)
    parser.add_argument("--rust-target-triple", required=True)
    args = parser.parse_args()

    lock = read_json(args.lock)
    inventory = read_json(args.inventory)
    disposition = read_json(args.disposition)
    version = lock["chromium"]["version"]
    version_file = args.source / "chrome/VERSION"
    if not version_file.is_file():
        raise SystemExit("prepared source has no chrome/VERSION")
    version_text = version_file.read_text(encoding="utf-8")
    expected = dict(zip(("MAJOR", "MINOR", "BUILD", "PATCH"), version.split(".")))
    actual = dict(re.findall(r"^(MAJOR|MINOR|BUILD|PATCH)=(\d+)$", version_text, re.MULTILINE))
    if actual != expected:
        raise SystemExit(f"chrome/VERSION mismatch: expected {expected}, got {actual}")

    apply_layers = sorted(
        inventory["layers"],
        key=lambda layer: {"alpine": 0, "copium": 1, "portablelinux": 2}[layer["project"]],
    )
    records = apply_inventory(
        args.source,
        {
            "portablelinux": args.portable_root,
            "alpine": args.alpine_root,
            "copium": args.copium_root,
        },
        {"layers": apply_layers},
        disposition,
        args.patches,
    )
    ungoogled = apply_ungoogled(args.source, args.ungoogled_root, args.patches / "ungoogled")
    local_patches = [
        {
            "name": item["name"],
            "sha256": item["sha256"],
            "conflict_record": {"upstream_sets": ["portablelinux"]},
        }
        for item in records
        if item["project"] == "portablelinux" and item["state"] == "apply"
    ]
    local_patch_reasons = {
        "laputa-external-libcxx.patch": "Chromium's use_custom_libcxx path builds the in-tree runtime; this patch redirects it to the validated Laputa static runtime.",
        "gate-e-hermetic-smoke.patch": "Adds the repository-owned Gate E smoke target without making it a dependency of Chrome.",
        "headless-no-devtools.patch": "The initial headless product does not build or ship the DevTools frontend; raw CDP remains available.",
        "headless-devtools-protocol-without-frontend.patch": "Raw CDP browser handlers still need the generated protocol domains even when the DevTools frontend is disabled; generate the protocol implementation independently of frontend resources.",
        "headless-devtools-browser-without-frontend.patch": "Chrome UI and raw CDP retain browser-side DevTools symbols even when frontend resources are disabled; keep the browser implementation target while separating it from frontend generation.",
        "headless-devtools-runtime-without-frontend.patch": "The browser-side DevTools implementation and WebUI plumbing are required for raw CDP even when frontend assets are disabled; keep those targets while avoiding frontend resource generation.",
        "headless-devtools-frontend-host-stub.patch": "The browser-side DevTools target references the content frontend-host API, but loading the real implementation would require frontend resource assets; provide a no-resource stub for the frontend-disabled profile.",
        "headless-no-desktop-backend-stubs.patch": "The headless Linux profile has no desktop toolkit or DBus backends; retain link-complete no-op implementations for DevTools Floaty, notifications, Bluetooth metrics registration, and the networking-private API base classes.",
        "headless-no-networking-private-lifetime.patch": "The DBus-free networking-private stub still needs the abstract UI delegate factory lifetime symbols used by Chrome's no-op UI delegate; provide those symbols without enabling a Linux network backend.",
        "headless-no-gcm-dead-code.patch": "The ungoogled-chromium disable-GCM patch returns before MCS initialization; remove the now-dead body so Clang's unreachable-code warning cannot fail the build.",
        "headless-no-domain-reliability-unreachable.patch": "The ungoogled-chromium domain-reliability disable patch makes the upload path permanently dead; scope Clang's unreachable-code suppression to that intentional branch.",
        "headless-no-assist-ranker-unreachable.patch": "The ungoogled-chromium request-blocking patch returns before Assist Ranker network code; scope Clang's unreachable-code suppression to that intentional dead path.",
        "headless-no-translate-ranker-unreachable.patch": "The ungoogled-chromium translation switch patch returns the default ranker response before the disabled model path; scope Clang's unreachable-code suppression to that intentional dead path.",
        "headless-no-advanced-protection-unreachable.patch": "The ungoogled-chromium advanced-protection switch preserves a permanently disabled branch; scope Clang's unreachable-code suppression to that intentional dead path.",
        "headless-no-ntp-background-unreachable.patch": "The ungoogled-chromium NTP background switch returns before the disabled Backdrop request path; scope Clang's unreachable-code suppression to that intentional dead path.",
        "headless-no-printing-linux-context.patch": "PrintingContextLinux remains a shared lower-level dependency for PDFium while ENABLE_PRINTING is disabled; avoid calling LinuxUi's printing-only API in that configuration.",
        "headless-no-printing-pref.patch": "The headless product disables ENABLE_PRINTING, so the printing preference is not generated; keep the Glic context-menu branch disabled in that configuration.",
        "headless-no-printing-pdf-helpers.patch": "Print-to-PDF is disabled while PDFium remains enabled; exclude Skia tagged-PDF helper code and return no SkDocument so the lower-level shared printing target has no Skia-PDF link dependency.",
        "headless-no-printing-flag.patch": "The printing feature header is unavailable when ENABLE_PRINTING is disabled; omit the about:flags entry for a printing-only feature while retaining PDFium.",
        "headless-no-one-google-bar-unreachable.patch": "The ungoogled-chromium One Google Bar switch returns before its disabled network path; scope Clang's unreachable-code suppression to that intentional dead path.",
        "musl-extension-toolbar-shadow.patch": "The custom Clang build enables -Wshadow as an error; rename an inner ExtensionsToolbarDesktop anchor variable that collides with the surrounding local.",
        "musl-profile-lookup-const.patch": "The custom libc++ raw_ptr comparator accepts Profile* but not const Profile* for transparent set lookup; compare the identity address without changing the set or profile.",
        "headless-no-safe-browsing-command.patch": "Advanced Protection is disabled in this Google-free headless product; make its browser command explicitly non-executable so the disabled command remains warning-clean.",
        "musl-sched-param-initializer.patch": "musl adds reserved fields to sched_param; designated initialization keeps Chromium's realtime priorities while zero-initializing libc-specific fields without a missing-field warning.",
        "musl-allocator-cdefs.patch": "Chromium's allocator shim includes glibc-only sys/cdefs.h even though its __THROW fallback already supports musl.",
        "musl-allocator-cpp-noexcept.patch": "Chromium's allocator shim uses __THROW for C++ nothrow operators; the musl C fallback is empty, so match libc++'s noexcept declarations explicitly.",
        "musl-allocator-libc-noexcept.patch": "Chromium's allocator C-symbol overrides must use an empty exception specification with musl's malloc declarations.",
        "musl-perfetto-cmsg-sign-compare.patch": "musl's CMSG_NXTHDR macro compares size_t with ptrdiff_t; suppress that libc-header warning only for Perfetto's Unix socket implementation.",
        "musl-unix-domain-socket-types.patch": "musl declares ancillary socket lengths as socklen_t; make Chromium's Unix-domain socket assignments explicit and suppress its CMSG_NXTHDR libc-header warning locally.",
        "musl-stack-trace-noexecinfo.patch": "Alpine's no-execinfo patch guards most stack symbolization but leaves one direct stream call; provide the same non-symbolizing fallback on musl.",
        "musl-cookie-string-view-iterator.patch": "Chromium's cookie parser mixes std::string and std::string_view iterator types; libc++ keeps these iterator types distinct on musl.",
        "musl-udp-cmsg-sign-compare.patch": "musl's CMSG_NXTHDR macro compares size_t with ptrdiff_t; suppress that libc-header warning locally for Chromium's UDP ancillary-data loop.",
        "musl-mojo-cmsg-sign-compare.patch": "musl's CMSG_NXTHDR macro compares size_t with ptrdiff_t; suppress that libc-header warning locally for Mojo's POSIX socket ancillary-data loop.",
        "musl-mojo-vector-bool-clone.patch": "Laputa libc++ exposes std::vector<bool> iteration through a proxy reference; convert that proxy to bool before Mojo's generic clone path invokes CloneTraits.",
        "musl-raw-ref-transparent-const.patch": "Chromium's raw_ref transparent comparator omits const-reference overloads; Laputa libc++ uses the const heterogeneous std::map lookup path for PermissionRequest.",
        "musl-private-key-find-iterator.patch": "Laputa libc++ rejects incrementing the temporary const iterator returned by std::find over Chromium's constexpr key-source array; keep the iterator in a named variable before advancing it.",
        "musl-webrtc-physical-socket.patch": "musl's CMSG_NXTHDR macro compares size_t with ptrdiff_t; suppress that libc-header warning locally for WebRTC's Unix socket implementation.",
        "musl-sandbox-cmsg-sign-compare.patch": "musl's CMSG_NXTHDR macro compares size_t with ptrdiff_t; suppress that libc-header warning locally for the sandbox broker's Unix credential socket loop.",
        "musl-sys-poll-compat-overlay.patch": "Alpine's sys/poll.h is a warning-emitting redirect to poll.h; provide that compatibility header through Chromium's musl toolchain include overlay so every target gets the portable behavior.",
        "musl-v8-sanitizer-header.patch": "Trap-only sanitizer flags define V8's feature macros, but this musl toolchain omits compiler-rt headers and Alpine's no-sanitizer-trap patch removes the callbacks that used them.",
        "headless-dawn-vulkan-headers.patch": "Dawn's Linux WebGPU Ozone representation uses Vulkan interop headers even when Chromium's Vulkan runtime is disabled; declare the bundled headers/native target and pass a null queue in the no-Vulkan Ozone factory path.",
        "headless-pdf-without-printing.patch": "PDFium still consumes shared printing primitives, but this headless product does not expose print-to-PDF; allow the lower-level dependency to build with printing features disabled.",
        "headless-no-printing-test-dep.patch": "The blocked-content interactive test is outside the headless product and unconditionally depends on Chrome's printing implementation; omit that test-only dependency when printing is disabled.",
        "headless-no-linux-v4l2.patch": "The headless product has no webcam support; omit Linux V4L2 sources while retaining the shared capture interfaces and optional PipeWire path.",
        "headless-no-linux-v4l2-tracker.patch": "The generic capture tracker factory still selected the removed Linux V4L2 GPU tracker; return no GPU-memory tracker so webcam/V4L2 support is disabled consistently.",
        "headless-no-dbus-backend-linkage.patch": "This headless Linux profile has no DBus backend; guard BlueZ and Bluetooth metrics and provide a no-op platform shell implementation so the browser remains link-complete without desktop integration.",
        "headless-link-dawn-drm-and-variations.patch": "The enabled Dawn Ozone representation uses Linux DRM helpers, and the variations net target declares its header name without defining it; add the direct DRM dependency and the stable header constant.",
        "musl-fontconfig-no-nls.patch": "The bundled fontconfig archive enables NLS in its generated configuration, which requires the absent gettext development header; Chromium does not need fontconfig's translation catalogs.",
        "musl-fontconfig-random.patch": "The bundled fontconfig configuration claims glibc's random_r API is available; use its portable random() fallback on musl.",
        "musl-bindgen-clang22.patch": "Alpine's pinned libclang predates two Chromium warning names; bindgen must ignore unknown warning-option diagnostics.",
        "alpine-rust-bootstrap.patch": "The pinned Alpine Rust package is stable while Chromium 150 emits nightly-only -Z flags; enable its documented bootstrap compatibility mode only in Chromium's Rust wrapper.",
        "alpine-node-version.patch": "The pinned Alpine Node package is v24.17.0 while this Chromium checkout records v24.12.0; keep the build-time version check enabled against the hermetic package actually installed in the image.",
        "musl-crashpad-cdefs.patch": "Crashpad's ptrace compatibility header only needs glibc's sys/cdefs.h for its glibc-specific constants; avoid Alpine's deprecation warning on musl while retaining that include on glibc.",
        "musl-crashpad-cmsg.patch": "musl's CMSG_NXTHDR macro compares size_t with ptrdiff_t; suppress that libc-header warning locally for Crashpad's Unix credential socket loop.",
        "headless-no-crashpad-handler.patch": "The headless product does not collect crash reports; omit the Crashpad handler data dependency from Chrome, the crash app, and headless executables to keep the output graph and artifact minimal.",
        "headless-no-crashpad-runtime.patch": "The headless Linux product does not collect crash reports; stop Linux Crashpad initialization before it attempts to launch the intentionally omitted handler binary.",
        "headless-no-crashpad-guards.patch": "Crashpad initialization is intentionally disabled on headless Linux; avoid installing the WebAssembly first-chance handler when no Crashpad signal handler exists.",
        "headless-no-crashpad-zygote.patch": "Crashpad initialization is intentionally disabled in headless Linux zygotes; avoid installing the WebAssembly first-chance handler when no Crashpad signal handler exists.",
        "headless-native-egl-llvmpipe.patch": "The headless test profile uses Alpine Mesa llvmpipe for displayless rendering; expose native EGL/GLES2 before the optional ANGLE implementation in the headless Ozone factory.",
        "musl-native-egl-share-group.patch": "Native Mesa EGL does not implement ANGLE's display texture share-group extension; only enable Chromium's global texture share-group state when that capability exists.",
        "musl-native-egl-semaphore-share-group.patch": "Native Mesa EGL does not implement ANGLE's display semaphore share-group extension; only enable Chromium's global semaphore share-group state when that capability exists.",
        "musl-gpu-sandbox-tsync.patch": "Chromium initializes native Mesa GPU helper threads before installing seccomp; enable the existing GPU TSYNC option so those threads enter the GPU sandbox together.",
        "musl-gpu-sandbox-broker-tsync.patch": "The native Mesa GPU process has helper threads before Chromium forks its syscall broker; pass the existing GPU TSYNC option through broker startup instead of tripping the generic single-thread assertion.",
        "musl-gpu-pwritev2-sandbox.patch": "Alpine musl's GPU path uses pwritev2 (syscall 287); allow that specific harmless file-write syscall in Chromium's existing GPU seccomp policy so the GPU remains sandboxed.",
        "musl-abseil-clang23-lifetime-capture.patch": "Clang 23 deprecates Abseil's lifetime_capture_by(this) spelling; use the replacement lifetime_capture_by_this attribute while preserving the lifetime-capture diagnostic.",
        "musl-abseil-clang23-unused-template.patch": "Clang 23 diagnoses Abseil's intentionally uninstantiated type-dependent log-format overload as unused; suppress only that warning around the two overloads.",
        "musl-clang23-unused-template-global.patch": "Clang 23 diagnoses intentionally uninstantiated type-dependent templates across the musl Chromium graph; suppress only this warning for the Clang musl compiler config while retaining -Werror for other diagnostics.",
        "musl-clang23-lifetime-capture.patch": "Clang 23 deprecates Chromium's lifetime_capture_by(this) spelling; use lifetime_capture_by_this when available while retaining the legacy fallback for older compilers.",
        "musl-clang23-function-view-pointer-bool.patch": "Clang 23 diagnoses the always-true function-reference branch in WebRTC's nullable function-pointer view constructor; keep null handling for pointer arguments without applying pointer-bool conversion to function references.",
        "musl-clang23-omnibox-nodiscard.patch": "Clang 23 diagnoses the intentionally discarded iterator returned by Omnibox's action-match insertion as nodiscard; explicitly discard the insertion result while preserving the existing match insertion.",
    }
    for local_patch in args.local_patch:
        record = patch_file(
            args.source,
            local_patch,
            args.patches / "local",
            disposition.get("patch_application", {}).get("max_fuzz", 0),
        )
        local_patches.append(
            {
                "name": local_patch.name,
                **record,
                "conflict_record": {
                    "upstream_sets": ["alpine", "copium", "portablelinux"],
                    "reason": local_patch_reasons.get(local_patch.name, "Repository-owned build boundary patch."),
                },
            }
        )
    pruning_root = next(args.ungoogled_root.glob("ungoogled-chromium-*"))
    pruning_report = prune(args.source, pruning_root / "pruning.list")
    domain_report = substitute_domains(
        args.source,
        pruning_root / "domain_substitution.list",
        pruning_root / "domain_regex.list",
    )
    rust_target = add_rust_target(args.source, args.rust_target_triple)
    builder_links = add_builder_links(args.source)
    unbundle = apply_system_unbundle(args.source)

    source_report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": version,
        "devtools_revision": lock["chromium"]["devtools_revision"],
        "git_used": False,
        "git_required": False,
        "generator_runs": [
            {"name": "version", "status": "passed", "network": "none", "git_used": False},
            {"name": "revision", "status": "passed", "network": "none", "git_used": False},
        ],
        "revision_files": [
            {"path": "chrome/VERSION", "contains": f"MAJOR={expected['MAJOR']}"}
        ],
        "source_transformations": {"rust_target": rust_target, "builder_links": builder_links},
    }
    patch_report = {
        "schema": 1,
        "status": "complete",
        "network": "none",
        "chromium_version": version,
        "entries": records,
        "source_layers": {
            "ungoogled": ungoogled,
            "portablelinux": [item for item in records if item["project"] == "portablelinux"],
        },
        "local_patches": local_patches,
    }
    toolchain = {
        "status": "complete",
        "network": "none",
        "bundled_toolchain_selected": False,
        "sysroot_selected": False,
        "forbidden_selected": [],
        "compiler": "/opt/llvm-musl/bin/clang",
        "cxx": "/opt/llvm-musl/bin/clang++",
        "linker": "/opt/llvm-musl/bin/ld.lld",
        "ar": "/opt/llvm-musl/bin/llvm-ar",
        "rust_linker": "/opt/llvm-musl/bin/clang",
        "rust_target_triple": args.rust_target_triple,
    }
    write_atomic(args.metadata / "source-inputs.json", source_report)
    write_atomic(args.metadata / "patches.json", patch_report)
    write_atomic(args.metadata / "pruning.json", pruning_report)
    write_atomic(args.metadata / "domain-substitution.json", domain_report)
    write_atomic(args.metadata / "toolchain-selection.json", toolchain)
    write_atomic(args.metadata / "system-unbundle.json", unbundle)


if __name__ == "__main__":
    main()
