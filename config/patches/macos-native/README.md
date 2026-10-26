# Native macOS automation compatibility

The ordered `series` targets Chromium `156.0.8078.47`, commit
`1d17a2e2ee147bb7d6ae2ab6abdb3a2510c17775`. Preparation rejects another source
revision, missing patches, and non-exact hunks. Linux preparation does not load
this directory.

`--native-automation-compatibility` enables the overlay. Without the switch,
Chromium retains its ordinary behavior. Renderer launch propagation carries the
policy into new renderer processes, including cross-site documents.

| Patch | Purpose and affected source | Origin | Regression and removal condition |
| --- | --- | --- | --- |
| `001-desktop-user-agent.patch` | Normalize the desktop headless product token in `components/embedder_support/user_agent_utils.cc`. Propagate the switch in `content/browser/renderer_host/render_process_host_impl.cc`. Version derivation, reduction, brands, and metadata remain native. | Locally authored against the locked Chromium source. No donor code imported. Chromium BSD notices remain in the source files. | `ts/tests/browser/native_stealth_test.ts` ("native macOS automation signal differential acceptance"): red control `headless-base` exposes `HeadlessChrome`; green controls `native-*` match `headed-base` desktop `Chrome/156.0.0.0` across HTTP, document, frame, worker, reload, target, and restart observations. Remove when the upstream desktop implementation provides equivalent opt-in behavior. |
| `002-native-webdriver.patch` | Return false through the existing native getter in `third_party/blink/renderer/core/frame/navigator.cc` when compatibility is enabled. Leave internal automation state and inspector handling intact. | Locally authored. The original file's copyright and LGPL notices remain in place. No donor implementation imported. | `ts/tests/browser/native_stealth_test.ts`: red control `automation-control` (`--enable-automation`) returns `webdriver=true`; green control `native-automation-control` (same switch plus compatibility) returns `webdriver=false`. Native descriptor and getter shape must match the `headed-base` control. Remove when upstream provides the same browser-owned policy. |
| `003-native-screen.patch` | Use the host's `NSScreen` bounds, work areas, scale factors, and internal-display identity in `ui/display/mac/screen_mac_headless.mm`. Explicit `--screen-info` retains precedence. | Locally authored using Chromium's existing AppKit and display interfaces. Chromium BSD notices remain in place. | `ts/tests/browser/native_stealth_test.ts`: red control `headless-base` reports default headless geometry (800x600); green controls `native-*` report host geometry (1728x1117, dpr 1) matching `headed-base` under the identical 1200x667 viewport policy. Remove when upstream headless macOS uses native display geometry by default or offers equivalent behavior. |

The screen overlay snapshots displays at browser startup. Display hot-plug
behavior is outside the current acceptance coverage. Capture viewport dimensions
remain caller policy and can differ from physical screen dimensions.
Compatibility requires a display exposed by AppKit; startup fails if none is
available.

Preparation hashes the ordered patch contents and the existing source-mutating
profile compatibility code. Configure, build, staging, acceptance, promotion,
and packaging carry that digest. Changed inputs invalidate downstream phases.
An appended patch can extend a verified prepared prefix in the existing source
tree. Editing an applied patch requires an explicit source reset or a reviewed
reversal. Preparation never treats skipped or fuzz-applied hunks as success.

The native compatibility series is separate from the ungoogled series. Counts
of ungoogled patches are not counts of automation compatibility fixes.
