Prefix: CHB
Next: 7

### CHB-1 · P2 · verify the native GTK file chooser in the packaged browser

- **Area:** Tests
- **Where:** `config/profiles/helium-wayland-release.gn:42`, `scripts/stage-runtime.py:53`, `campaign-helium.md:1308`
- **Problem:** Helium keeps `use_gtk = true` and stages `libgtk-3.so.0`, so no from-scratch picker is currently required. The native file chooser is still unproven in the musl archive: the S13 upload row uses CDP `DOM.setFileInputFiles`, which bypasses the picker, and no automated test opens the GTK dialog in the target session. If the GTK runtime or its modules are incomplete, upload and “save as” flows would be inert despite passing the CDP checks.
- **Recommendation:** In the owner's existing dwl session, run the packaged browser, open an upload control and the download save-as dialog, and confirm the GTK chooser appears and returns a path. The headless Sway fixture is only an automated builder check and is not required on the desktop. If the chooser fails, prefer bundling the exact missing GTK modules, gdk-pixbuf loaders, schemas, and icon data over requiring a host `gtk+3.0` package; a host package is only a documented fallback if relocation cannot work. Do not implement a new picker first.
- **Fix with:** none
- **Effort:** S

### CHB-2 · P2 · restore the DevTools frontend after the first lean package

- **Area:** Build
- **Where:** `config/patches/helium/helium-no-devtools-frontend.patch`, `config/profiles/helium-wayland-release.gn`, `campaign-helium.md`
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
- **Fix with:** none
- **Effort:** S
