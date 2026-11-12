# Helium acceptance

[Linux](linux.md) covers the Linux amd64 musl Helium build, package, and install
commands. This document is the acceptance contract for the browser and for the
owner's Alpine dwl target.

## Current automated coverage

`test-fast` and `test-staged` run `scripts/helium_test_fast.py` in an owned
headless Sway session. On amd64 the browser renders with Mesa llvmpipe; on arm64
it uses Chromium's in-tree ANGLE/SwiftShader, and the GPU information reports
SwiftShader's vendor and renderer. `scripts/qualify-package.py` binds a passing
report to the archive digest, and archive installation requires that
`.acceptance.json` sidecar.

For `helium-0.20.3.1-linux-x86_64-musl.tar.xz` the automated run covered:

| Check | Observation |
| --- | --- |
| product-identity | `Helium 0.20.3.1 (Chromium 156.0.8078.79)` from the executable and the CDP endpoint |
| wayland-window | a native surface for the browser PID mapped in the Sway tree, with the fixture pixels present |
| page-rendering | fixture JavaScript, computed style, viewport, and CDP screenshot dimensions matched |
| sandbox-renderer | three live renderers with `NoNewPrivs=1` and `Seccomp=2` |
| clean-shutdown | browser and owned children exited after `Browser.close` |

From package revision 2 onward the same run first starts the browser with
`--headless=new --remote-debugging-port=0 --user-data-dir=<dir> about:blank`
and no Wayland or X server, and the acceptance sidecar adds these checks:

| Check | Pass condition |
| --- | --- |
| headless-devtools | the browser stays up and prints `DevTools listening on ws://…` |
| headless-identity | `/json/version` reports `Browser` as `Helium/<Helium version>`, the same product as the headed browser, for the executable whose `--version` names Helium; the web User-Agent stays Chrome's |
| headless-page | a page served over loopback HTTP loads and `Runtime.evaluate` returns the values its script computed |
| headless-sandbox-renderer | every live renderer has `NoNewPrivs=1`, `Seccomp=2`, and more than one `NSpid` field |
| headless-clean-exit | `Browser.close` exits the browser with status 0 and leaves no process in its group |
| host-headless-load | on the host, three concurrent headless browsers repeat the loopback page evaluation for the run's duration with no crashed target and no renderer `SIGSYS` exit |

The headless run refuses root and requires unprivileged user namespaces. No
check may use `--no-sandbox`, `--single-process`, or `--no-zygote`.

For package revision 3 the arm64 archive must also pass the repository's purity
gate: extract it and run `check-purity.sh <dir> aarch64 <llvm-tools-dir>`. It
must report `0 impure`, which proves every shipped ELF and archive names only
musl's loader/libc or a library the package ships, carries no GCC `.comment`,
and references no glibc, libgcc, or libstdc++ symbols.

These checks are the whole automated package acceptance. Every other row
below is not covered by the package path; some need new harness support and
others need the target session. `TODO.md` carries the remaining work.

## Required observations

Run every row on the build, the stage, and the extracted package before a full
release. Repeat session-dependent rows in the owner's dwl session. A result is
`passed`, `failed`, or `not-executed`; missing data is not a pass. The aggregate
may be complete only if every mandatory row for that artifact passed.

| Check | Procedure and exact pass condition | Negative control or regression case |
| --- | --- | --- |
| product-identity | executable, generated version, runtime manifest and CDP executable path identify Helium 0.20.3.1 from Chromium 156.0.8078.79 | renamed Chromium or wrong-version metadata fails before browser acceptance |
| wayland-window | native Wayland surface maps for the launched PID; compositor output contains window chrome and the fixture's known colored/content regions | no mapped surface or uniform-background screenshot fails even when CDP responds |
| page-rendering | local fixture JS returns its expected value; CSS bounding boxes and known screenshot pixels match the fixture | missing JS execution, missing stylesheet, or wrong screenshot dimensions fails |
| navigation-tabs | open local A, navigate to B, back to A; create a second tab, switch, and close it; require expected target URLs and active content | target creation alone cannot pass tab switching |
| vertical-tabs | use the real Helium UI command/shortcut to switch layouts; create two tabs and select both; require observable layout and content changes | preference-file edit without a visible layout change fails |
| settings | navigate to settings, wait for actual settings controls, change one harmless preference and read it back | a nonempty HTML shell or blank WebUI cannot pass |
| no-onboarding | with a fresh disposable profile and normal first-run behavior, require the expected start surface with no setup tab; `chrome://setup` must not resolve and the installed resources must contain no `helium_onboarding` payload | a setup tab, a `chrome://setup` handler, or any bundled onboarding resource fails |
| privacy-defaults | compare the new profile's defining defaults to the pinned Helium patches; verify blocked Google/service behavior through local observations where possible | inherited Chromium defaults cannot be normalized away |
| bundled-ublock | component metadata shows the bundled Imput 1.75.0 build enabled; load one deterministic blocked URL and one allowed sibling URL; blocked URL never reaches server or executes, allowed URL does both | disable the component in a separate disposable test profile and prove the blocked fixture reaches the server; then restore an enabled fresh profile |
| extension | load the existing separate test extension through the supported Helium extension path and require its page-visible action | a prepopulated fixture variable without a live extension cannot pass |
| download | download fixture bytes, wait for CDP completion, compare actual file bytes | missing/partial/wrong bytes fail |
| file-upload | populate an actual file input with the fixture and submit to the loopback server; compare received bytes | DOM filename text without uploaded bytes fails |
| profile-restart | set a harmless preference and local persistent storage, close, restart with the same disposable profile, and read both back | a fresh-profile rerun cannot satisfy persistence |
| keyboard | focus the real browser and fixture input, send text with Wayland virtual-keyboard input, assert actual input/change events and final text | CDP `Runtime.evaluate` assignment or CDP keyboard dispatch alone cannot satisfy native Wayland input |
| clipboard | copy a unique sentinel with `wl-copy`, paste with native keyboard into fixture input; copy selected browser text and require `wl-paste` to return it | permission errors, empty text, or setting clipboard only through a CDP override fails |
| fonts | render existing serif/sans/monospace samples and verify distinct measured widths/families plus visible glyphs; inspect bundled fontconfig aliases | a single fallback face for every family fails |
| software-graphics | CDP GPU information identifies software rendering and the browser renders the fixture without a passed-through GPU device: Mesa llvmpipe on amd64, ANGLE/SwiftShader on arm64 | `--disable-gpu`, the compositor's pixman setting alone, or a non-software renderer cannot pass |
| hardware-graphics (target only) | on the Broadwell desktop, GPU information identifies Mesa iris, `vainfo` lists H.264/VP8 profiles, and `chrome://media-internals` selects `VaapiVideoDecoder` for an H.264 or VP8 fixture | llvmpipe, missing VA-API profiles, or a software decoder for the fixture fails; VP9/HEVC software fallback is expected |
| media | existing pinned local media reaches the required playback state and decoded frames/audio-track observations; retain its dynamic-library audit | renaming the codec fixture or disabling media checks fails |
| tls-trust | the normal verifier accepts the local chain after importing its CA into a disposable NSS store; rejects the same chain without that CA, a wrong hostname, and an expired leaf | no ignore-certificate switch, SPKI exception, CDP security override, or navigation through an interstitial may authorize success |
| sandbox-renderer | observe a live page renderer, enabled Chromium namespace/seccomp sandbox status, and process evidence meeting the rules below | a container-provided seccomp filter or absence of `--no-sandbox` alone cannot pass |
| sandbox-other-processes | retain process-type-specific GPU/utility sandbox observations and upstream policy; GPU aux info reports sandboxed where required | do not apply renderer rules indiscriminately to every helper or exempt all utilities |
| clean-shutdown | browser, renderer, Crashpad and owned test helpers exit; no owned descendants remain after the cleanup deadline | timeout and abnormal exit cases must exercise TERM/KILL/reap behavior |

For native UI observations, inspect the pinned Helium source to select actual
commands and accessible controls. Put source-derived selectors/shortcuts next
to the behavior in the test, not in a generic guessed-UI layer. If the local
UI differs from the source, diagnose the missing feature instead of weakening
the observation. Use CDP for page content and WebUI only where it reaches that
real control. Use compositor input for keyboard/clipboard and native chrome.

The fixture's network resolver maps ad-like domains to loopback. Keep fixed
local content and record server hits. Do not require live filter-list downloads
for a deterministic uBlock test. Use its bundled lists when available; if a
specific test rule is needed, add that explicit rule to the real bundled
component in the disposable profile through its supported settings. Verify
the negative control. Never substitute an unrelated test extension as the blocker.

### Verify TLS without a bypass

The existing Chromium fixture passes an SPKI exception. Preserve legacy
Chromium behavior where required, but do not count that test as Helium trust
qualification. Add a test-only CA and a locally served leaf chain with localhost
and 127.0.0.1 SANs, plus wrong-hostname and expired-leaf cases. Generate
short-lived valid fixtures within the attempt using the pinned OpenSSL tool, or
validate committed fixture dates before use. Never use wall-clock changes to
make a certificate pass. Keep test keys confined to public test fixtures or
scratch.

The pinned Chromium 156 source's Linux certificate-management guide (`cert_management.md`) says the normal
NSS store is under `.local/share/pki/nssdb`, with legacy `.pki/nssdb` used if
already present. Use a fresh isolated HOME so there is no legacy ambiguity.
The harness must create and import only inside that HOME:

```sh
mkdir -p "$HOME/.local/share/pki/nssdb"
certutil -N --empty-password -d "sql:$HOME/.local/share/pki/nssdb"
certutil -A -d "sql:$HOME/.local/share/pki/nssdb" \
    -n helium-local-test-ca -t 'C,,' -i "$TEST_CA_PEM"
```

These are harness setup commands, never commands against the owner's real HOME.
`TEST_CA_PEM` names that attempt's test CA. Restart the browser after trust-store
changes. Require the trusted page's expected JS/content and `Security` state.
Require the untrusted/wrong-host/expired cases to yield an actual certificate
failure and no successful fixture content. Inspect every launched process argv
and reject all certificate-verification overrides in Helium acceptance.

### Prove Chromium's sandbox rather than the container's

Run the browser as a nonroot user. Reject `CHROMIUM_TEST_NO_SANDBOX=1` for
Helium instead of silently ignoring it. Reject sandbox-disable flags in the
launcher, browser, renderer, GPU and utility argv. Do not count a failed sandbox
check as a pass because the host is inconvenient.

For an alive renderer record PID, parent identity, argv, `NoNewPrivs`, `Seccomp`,
`Seccomp_filters` when available, and `/proc/<pid>/ns/{user,pid,net}` identities.
Record browser and harness/container baselines for comparison. Require
`NoNewPrivs=1` AND `Seccomp=2`, Chromium's own sandbox status, and evidence of
Chromium-created namespace/filter isolation relative to those baselines. The
current helper records only status fields and accepts NoNewPrivs OR seccomp in
one legacy check. That condition is insufficient for the new Helium gate.

Use the sandbox's real reported mechanism to interpret a zygote/renderer shared
namespace. Do not require every renderer to have a namespace distinct from its
zygote. An unchanged inherited container filter by itself does not prove a
Chromium filter. If `/proc` or status cannot supply the necessary distinction,
leave that check unverified and require the normal-user target-host test.

The existing privileged Docker test path may be used for diagnosis, with the
privilege level recorded. It never replaces a normal-user Alpine/dwl run. Do not
add privilege to the delivered launcher or require a setuid-root browser
installation as a fallback.

## Owner dwl qualification

The target is the owner's `~/usr/host/alpine` desktop: Alpine edge, amd64,
OpenRC, seatd, dwl, and ghostty-custom. It has no XWayland, display manager,
systemd, session D-Bus, or PulseAudio/PipeWire service requirement. Read its
current `AGENTS.md` and `README` read-only before qualification. Do not edit or
provision that repository or run its setup/sync scripts as part of browser
qualification.

The direct-session harness mode does not exist yet. Add it before running the
target gate: `--wayland-session existing` must require the active socket, start
no compositor, and clean up only the browser children it owns.
`--runtime-kind package` must run the extracted archive rather than a builder
output. The default automated mode stays `--wayland-session isolated`.

First verify and extract the archive without installing it:

```sh
python3 scripts/install-runtime.py \
    --archive "$PWD/.work/tmp/helium-0.20.3.1-linux-x86_64-musl.tar.xz" \
    --verify-extract "$PWD/.work/tmp/helium-dwl"
```

Then run in the logged-in user's real dwl session, using the active runtime
socket and a disposable HOME/profile. Do not import the test CA into the
owner's NSS store.

```sh
python3 scripts/test-functional.py --product helium --runtime-kind package \
    --wayland-session existing \
    --chrome "$PWD/.work/tmp/helium-dwl/helium-wrapper" \
    --fixtures "$PWD/tests/fixtures" \
    --output "$PWD/.work/tmp/helium-dwl-results" \
    --require-llvmpipe --require-media
```

`--require-llvmpipe` is the amd64 software-rendering assertion. On arm64 use
the same run without it and require CDP GPU information to name SwiftShader
instead.

Those options are the target contract, not current behavior. Until the harness
implements them, leave the affected rows `not-executed` and use the manual
steps: launch the package, open a local fixture, create and switch two tabs,
use vertical tabs, visit settings, type and paste unique fixture
text, upload and download the fixture, restart with the same disposable
profile, and confirm persisted state. Confirm that `chrome://setup` does not
resolve and that no setup tab opens. Capture the browser in dwl and record the
actual outcomes in the acceptance report. Sway IPC observations do not apply to
dwl.

## Live installation

Live installation is a separate owner request. The installer is already
validated in scratch and in a disposable `alpine:edge` container. After the
request, install the archive as described in [Linux](linux.md), then rerun the
session-dependent rows through `$HOME/usr/bin/helium` with a disposable
profile. Do not edit personal profiles, default-browser MIME handling, dwl
keybindings, or the `~/usr` source tree. Roll back with the same installer if
the installed-path check fails.
