Prefix: CHB
Next: 9

### CHB-2 · P2 · restore the DevTools frontend after the first lean package

- **Area:** Build
- **Where:** `config/patches/helium/helium-no-devtools-frontend.patch`, `config/profiles/helium-wayland-release.gn`, `docs/linux.md`
- **Problem:** The current Helium profile disables the DevTools frontend and drops its resources to reduce graph size and build time. Raw CDP remains, but users cannot open the built-in DevTools UI. The removal is deliberate for the first package and is not proven against future Helium frontend changes.
- **Recommendation:** When DevTools is required, remove `helium-no-devtools-frontend.patch` from `config/helium.patch-disposition.json`, restore the upstream `enable_devtools_frontend` default, rebuild, and qualify the packaged frontend under dwl. Measure the package and build-time delta before keeping it enabled by default.
- **Fix with:** none
- **Effort:** M

### CHB-3 · P2 · restore native notifications and external-item handling without D-Bus

- **Area:** Operability
- **Where:** `chrome/browser/notifications/notification_platform_bridge_no_dbus.cc`, `chrome/browser/platform_util_linux_no_dbus.cc`, `ui/base/clipboard/clipboard_util_linux.cc`
- **Problem:** With `use_dbus = false`, native Linux notifications return no bridge, XDG file-transfer clipboard paths return empty, and “show in folder”/external URL handling falls back to `xdg-open`/`xdg-email`, which the target Alpine root may not provide.
- **Recommendation:** Bundle a small non-D-Bus notification and URI-launch path, or document the host `xdg-utils` requirement, then qualify the packaged browser in dwl.
- **Fix with:** none
- **Effort:** M

### CHB-5 · P3 · offer accessibility without requiring a session bus

- **Area:** Operability
- **Where:** `scripts/package-runtime.py` (`HELIUM_LAUNCHER`)
- **Problem:** Screen readers and accessibility tooling do not work in the default packaged browser.
- **Recommendation:** Add an opt-in launch path for accessibility on systems that provide a session bus, or build a non-D-Bus accessibility path. Keep the D-Bus-free default documented.
- **Fix with:** none
- **Effort:** M

### CHB-6 · P2 · verify VA-API decode on the Broadwell target

- **Area:** Tests
- **Where:** `config/profiles/helium-wayland-release.gn`, `docs/linux.md`, `~/usr/host/alpine/README.md`
- **Problem:** `use_vaapi=true` compiles Chromium's VA-API decoder, but the automated Sway fixture uses Mesa llvmpipe and no hardware decoder. Package acceptance therefore cannot prove that the Dell XPS 13 9343's Broadwell GPU decodes H.264 or VP8 through `libva-intel-driver`, or that `VaapiVideoDecoder` is selected under dwl.
- **Recommendation:** In the owner's dwl session, run `vainfo` and require `VAProfileH264Main`/`VAProfileH264High`, play a local H.264 or VP8 file in the packaged browser, and check `chrome://media-internals` for `VaapiVideoDecoder`. Force H.264 on VP9-only sites rather than treating a software VP9 fallback as failure. If initialization fails, capture the browser log and test `LIBVA_DRIVER_NAME=i965` before changing the build.
- **Fix with:** CHB-7
- **Effort:** S

### CHB-7 · P1 · qualify the Helium package on the owner's Alpine dwl desktop

- **Area:** Tests
- **Where:** `docs/helium-acceptance.md`, `scripts/test-functional.py`, `scripts/install-runtime.py`
- **Problem:** The published prerelease passed only the five automated checks in the isolated Sway fixture. The direct-session harness mode (`--wayland-session existing`, `--runtime-kind package`) does not exist, the package has not run in the owner's dwl session, and it has not been installed live. The release stays a prerelease until target evidence exists.
- **Recommendation:** Implement the direct-session and package runtime modes, then follow `docs/helium-acceptance.md`: verify-extract the archive, run the session-dependent rows in the active dwl session with a disposable HOME and profile, and after an explicit live-install request install the archive and repeat them through `~/usr/bin/helium`. Record passed, failed, and not-executed rows; never promote the Sway result to target proof.
- **Fix with:** CHB-6
- **Effort:** M

### CHB-8 · P2 · complete the Helium behavior acceptance matrix

- **Area:** Tests
- **Where:** `scripts/helium_test_fast.py`, `scripts/test-functional.py`, `docs/helium-acceptance.md`
- **Problem:** Helium package acceptance checks only product identity, the Wayland window, page rendering, renderer sandboxing, and clean shutdown. Tabs and vertical tabs, settings, privacy defaults, bundled uBlock, extension loading, download/upload, profile restart, native keyboard and clipboard, fonts, media, TLS trust, and non-renderer sandbox observations are unexecuted. The Chromium functional harness covers several of these but has no Helium package path.
- **Recommendation:** Extend the Helium harness with the remaining rows from `docs/helium-acceptance.md`, reusing the Chromium fixture and CDP transport where it fits and the compositor input path for keyboard and clipboard. Keep the negative controls, reject certificate and sandbox bypasses, and bind each result to the archive digest. Add the rows to `scripts/qualify-package.py` only when they actually pass.
- **Fix with:** CHB-7
- **Effort:** L
