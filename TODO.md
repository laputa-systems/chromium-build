Prefix: CHB
Next: 2

### CHB-1 · P2 · verify the native GTK file chooser in the packaged browser

- **Area:** Tests
- **Where:** `config/profiles/helium-wayland-release.gn:42`, `scripts/stage-runtime.py:53`, `campaign-helium.md:1308`
- **Problem:** Helium keeps `use_gtk = true` and stages `libgtk-3.so.0`, so no from-scratch picker is currently required. The native file chooser is still unproven in the musl archive: the S13 upload row uses CDP `DOM.setFileInputFiles`, which bypasses the picker, and no automated test opens the GTK dialog in the target session. If the GTK runtime or its modules are incomplete, upload and “save as” flows would be inert despite passing the CDP checks.
- **Recommendation:** In the owner's existing dwl session, run the packaged browser, open an upload control and the download save-as dialog, and confirm the GTK chooser appears and returns a path. The headless Sway fixture is only an automated builder check and is not required on the desktop. If the chooser fails, prefer bundling the exact missing GTK modules, gdk-pixbuf loaders, schemas, and icon data over requiring a host `gtk+3.0` package; a host package is only a documented fallback if relocation cannot work. Do not implement a new picker first.
- **Fix with:** none
- **Effort:** S
