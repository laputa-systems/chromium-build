#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
INPUTS=${CHROMIUM_INPUTS_ROOT:-$WORK/inputs}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
SOURCE=${CHROMIUM_SOURCE_ROOT:-$WORK/src}
PYTHON=${PYTHON:-python3}

fail() {
    echo "Prepare: $*" >&2
    exit 1
}

[ "${NETWORK_MODE:-none}" = none ] || fail "preparation requires NETWORK_MODE=none"
[ -f "$METADATA/fetch-complete.stamp" ] || fail "fetch has not completed"
[ -f "$INPUTS/chromium-150.0.7871.114-linux.tar.xz" ] || fail "Chromium archive is missing"
[ ! -e "$SOURCE" ] || fail "prepared source already exists; reset the work volume first"

case "${CHROMIUM_ARCH:-arm64}" in
    arm64) rust_target_triple=aarch64-alpine-linux-musl ;;
    amd64) rust_target_triple=x86_64-alpine-linux-musl ;;
    *) fail "unsupported architecture ${CHROMIUM_ARCH:-arm64}" ;;
esac

TEMP=$(mktemp -d "$WORK/src.prepare.XXXXXX")
CONFIG=$(mktemp -d "$WORK/prepare-inputs.XXXXXX")
mkdir -p "$METADATA"
trap 'rm -rf "$CONFIG"' EXIT

tar -xJf "$INPUTS/chromium-150.0.7871.114-linux.tar.xz" -C "$TEMP"
tar -xzf "$INPUTS/ungoogled-2d89b04e1b68385c9086efab0df1e3679b35246e.tar.gz" -C "$CONFIG"
tar -xzf "$INPUTS/portablelinux-0033e274f91ec6aa57a36a486f41f46d801e381d.tar.gz" -C "$CONFIG"
tar -xzf "$INPUTS/alpine-aports-bc56128509194816d5cd3441d17c20ca2d71cc68.tar.gz" -C "$CONFIG"
tar -xzf "$INPUTS/copium-150.0.tar.gz" -C "$CONFIG"

SOURCE_DIR=$(find "$TEMP" -mindepth 3 -maxdepth 3 -type f -path '*/chrome/VERSION' -print -quit | sed 's#/chrome/VERSION##')
[ -n "$SOURCE_DIR" ] || fail "Chromium archive has no expected source root"

"$PYTHON" "$ROOT/scripts/prepare-source.py" \
    --source "$SOURCE_DIR" \
    --ungoogled-root "$CONFIG" \
    --portable-root "$CONFIG" \
    --alpine-root "$CONFIG" \
    --copium-root "$CONFIG" \
    --lock "$ROOT/config/inputs.lock" \
    --inventory "$ROOT/config/patch-inventory.json" \
    --disposition "$ROOT/config/patch-disposition.json" \
    --metadata "$METADATA" \
    --patches "$METADATA/patches" \
    --local-patch "$ROOT/config/patches/laputa-external-libcxx.patch" \
    --local-patch "$ROOT/config/patches/gate-e-hermetic-smoke.patch" \
    --local-patch "$ROOT/config/patches/headless-no-devtools.patch" \
    --local-patch "$ROOT/config/patches/musl-allocator-cdefs.patch" \
    --local-patch "$ROOT/config/patches/musl-allocator-cpp-noexcept.patch" \
    --local-patch "$ROOT/config/patches/musl-allocator-libc-noexcept.patch" \
    --local-patch "$ROOT/config/patches/musl-perfetto-cmsg-sign-compare.patch" \
    --local-patch "$ROOT/config/patches/musl-unix-domain-socket-types.patch" \
    --local-patch "$ROOT/config/patches/musl-bindgen-clang22.patch" \
    --local-patch "$ROOT/config/patches/alpine-rust-bootstrap.patch" \
    --rust-target-triple "$rust_target_triple"

mv "$SOURCE_DIR" "$SOURCE"
printf '%s\n' 'status=complete' 'network=none' >"$METADATA/source-complete.stamp"
echo "Prepare: source prepared at $SOURCE"
