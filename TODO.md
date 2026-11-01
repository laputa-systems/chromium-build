Prefix: CHB
Next: 9

### CHB-1 · P2 · verify the native GTK file chooser in the packaged browser

- **Area:** Tests
- **Where:** `config/profiles/helium-wayland-release.gn:42`, `scripts/stage-runtime.py:53`, `docs/helium-acceptance.md`
- **Problem:** Helium keeps `use_gtk = true` and stages `libgtk-3.so.0`, so no from-scratch picker is currently required. The native file chooser is still unproven in the musl archive: package acceptance only covers the five core checks, no Helium row opens the GTK dialog, and a CDP file-input assignment would bypass the picker. If the GTK runtime or its modules are incomplete, upload and “save as” flows would be inert despite passing the CDP checks.
- **Recommendation:** In the owner's existing dwl session, run the packaged browser, open an upload control and the download save-as dialog, and confirm the GTK chooser appears and returns a path. The headless Sway fixture is only an automated builder check and is not required on the desktop. If the chooser fails, prefer bundling the exact missing GTK modules, gdk-pixbuf loaders, schemas, and icon data over requiring a host `gtk+3.0` package; a host package is only a documented fallback if relocation cannot work. Do not implement a new picker first.
- **Fix with:** CHB-7
- **Effort:** S

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
- **Problem:** With `use_dbus = false`, native Linux notifications return no bridge, XDG file-transfer clipboard paths return empty, and “show in folder”/external URL handling falls back to `xdg-open`/`xdg-email`, which the target Alpine root may not provide. The GTK file chooser itself is unaffected.
- **Recommendation:** Bundle a small non-D-Bus notification and URI-launch path, or document the host `xdg-utils` requirement, then qualify the packaged browser in dwl.
- **Fix with:** none
- **Effort:** M

### CHB-4 · P3 · decide whether the GTK closure may contain libdbus

- **Area:** Dependencies
- **Where:** `scripts/stage-runtime.py`, `config/helium.packages.amd64.lock`
- **Problem:** `use_dbus = false` removes Chromium's D-Bus backend, but GTK3 links `libatk-bridge-2.0.so.0`, which links `libatspi.so.0`, which links `libdbus-1.so.3`. The staged GTK closure therefore contains libdbus even though the browser starts no session bus. This is unproven against the final package audit.
- **Recommendation:** Measure the staged closure first. If a libdbus-free package is required, build a minimal GTK without accessibility support or replace the GTK picker; otherwise document the transitive library as an accepted GTK accessibility dependency.
- **Fix with:** none
- **Effort:** S

### CHB-5 · P3 · offer accessibility without requiring a session bus

- **Area:** Operability
- **Where:** `scripts/package-runtime.py` (`HELIUM_LAUNCHER`)
- **Problem:** The launcher sets `NO_AT_BRIDGE=1` to keep GTK's accessibility bridge from opening a D-Bus connection. Screen readers and accessibility tooling therefore do not work in the default packaged browser.
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
- **Fix with:** CHB-1, CHB-6
- **Effort:** M

### CHB-8 · P2 · complete the Helium behavior acceptance matrix

- **Area:** Tests
- **Where:** `scripts/helium_test_fast.py`, `scripts/test-functional.py`, `docs/helium-acceptance.md`
- **Problem:** Helium package acceptance checks only product identity, the Wayland window, page rendering, renderer sandboxing, and clean shutdown. Tabs and vertical tabs, settings, onboarding, privacy defaults, bundled uBlock, extension loading, download/upload, profile restart, native keyboard and clipboard, fonts, media, TLS trust, and non-renderer sandbox observations are unexecuted. The Chromium functional harness covers several of these but has no Helium package path.
- **Recommendation:** Extend the Helium harness with the remaining rows from `docs/helium-acceptance.md`, reusing the Chromium fixture and CDP transport where it fits and the compositor input path for keyboard and clipboard. Keep the negative controls, reject certificate and sandbox bypasses, and bind each result to the archive digest. Add the rows to `scripts/qualify-package.py` only when they actually pass.
- **Fix with:** CHB-7
- **Effort:** L
