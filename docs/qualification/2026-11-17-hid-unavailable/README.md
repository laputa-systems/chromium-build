# Helium r8: unavailable HID backend

The pure arm64 r7 browser crashes when a page binds the HID manager. With
`use_udev=false`, `HidService::Create()` returned null and `HidManagerImpl`
unconditionally registered an observer on it. The native GDB capture in
`r7-fault.txt` reaches `DeviceService::BindHidManager` with the observer base
at address `0x40`. Identical-code folding makes the observer template's printed
name misleading; the caller, registers, source and regression establish the
null-backend path.

The repair supplies an empty backend: enumeration completes, device lists are
empty, and connection requests fail. It adds no hardware access or dependency
and leaves the browser sandbox enabled.

## Qualified artifact

Browser source/build inputs: commit `304730f6cd8ab8fc6b03d871d9e8f6ddb56d1382`,
Helium 0.20.3.1 / Chromium 156.0.8078.79, package revision 8, arm64 Linux musl.
Builder `helium-builder:0.20.3.1-arm64-hid-r8` has digest
`sha256:76e2f4b6b869118b9b669e71220133984d2af2cb7b965de086eb80b609aa3b63`.
`archive.sha256` binds the archive; `browser.sha256` proves the extracted
executable still matches the built executable after the test-only fixture fix.

Fresh preparation and gates A–F passed, followed by the full browser build.
Built, staged and extracted browser checks passed real Wayland rendering,
headless rendering, sandbox evidence, cleanup and the new
`navigator.hid.getDevices()` regression. The extracted report is
`extracted-browser.json`; purity checked 23 ELF files/archives with zero impure.
Gate receipts are under `metadata/` and the environment/compiler logs accompany
this record. These are correctness checks, not performance claims.

The same HID API check on the old r7 native package failed because Chrome
closed its CDP socket (`r7-hid-baseline.log`). The extracted r8 browser returned
an empty grant set. The C++ no-backend regression and six existing HID manager
tests also pass (`hid-unit.log`). Running those source tests first exposed a
separate test-only D-Bus teardown linkage defect in `DeviceServiceTestBase`.
`helium-device-tests-without-dbus.patch` guards that cleanup and its dependency
for the no-D-Bus build; it was applied after the r8 browser artifact was built.
Only the test fixture and test graph changed for this execution. The browser
archive above is the artifact from the recorded build commit.

## Reproduction

Use a separate work volume and the pinned arm64 builder; never overwrite an
accepted build volume. With the source/build environment configured:

```sh
./chromium-build --product helium --arch arm64 test-fast
./chromium-build --product helium --arch arm64 stage-runtime
./chromium-build --product helium --arch arm64 test-staged
./chromium-build --product helium --arch arm64 package
./chromium-build --product helium --arch arm64 build-target services_unittests --jobs 4
```

Run `services_unittests` as the ordinary build user with
`--gtest_filter='HidManagerWithoutBackendTest.*:HidManagerTest.*'
--test-launcher-jobs=1 --single-process-tests`. If building the historical
artifact commit, apply the test-only no-D-Bus patch before that test target.

After verified extraction, use the unchanged LLVM producer purity gate:
`check-purity.sh EXTRACT aarch64 LLVM_BIN`. Repeat `helium_test_fast.py` against
`EXTRACT/helium-wrapper`, using the checked-in fixtures and matching input lock.

`native-sites.py` is the bounded real-site probe. On native Linux, run it as
an ordinary user with `PYTHONPATH=scripts TMPDIR=OWNED_TMP`, supplying
`--browser EXTRACT/helium-wrapper --output OWNED_OUTPUT`. It uses fresh
profiles, leaves account state untouched, samples the rendered page for 60
seconds, checks renderer sandboxing and closes owned processes. Its JSON
records the actual outcomes; a browser error page is a failed site check.

## Real-site limits

Native r8 Instagram rendered for the full 60-second check and exited cleanly.
Native X returned HTTP 403, including on a separate retry; this is not a
successful X login-page check and not evidence of a browser crash.

The bctl r8 image separately passes actual macOS Nanos headed Instagram
navigation and clean stop, profile persistence across headed/headless/headed,
and downloads. X still fails in the Nanos kernel with `frame already full`;
bctl BCTL-25 owns that independent investigation. See bctl's headed campaign
and its digest-bound qualification record. No account login, authenticated
archive or unattended acceptance follows from these browser checks.
