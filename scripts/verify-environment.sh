#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
METADATA=${CHROMIUM_METADATA_ROOT:-/work/metadata}
ARCH=${CHROMIUM_ARCH:-${1:-arm64}}
LOCK=${INPUTS_LOCK:-$ROOT/config/inputs.lock}
PYTHON=${PYTHON:-python3}
STAGE_ROOT=${STAGE_ROOT:-$WORK/stage}

mkdir -p "$METADATA"

fail() {
    echo "Gate A: $*" >&2
    exit 1
}

need_file() {
    [ -f "$1" ] || fail "missing $1"
}

case "$ARCH" in
    arm64|amd64) ;;
    *) fail "unsupported architecture $ARCH" ;;
esac

need_file "$LOCK"
need_file "$ROOT/scripts/lockcheck.py"

expected_linux_arch=$($PYTHON - "$LOCK" "$ARCH" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    lock = json.load(stream)
print(lock["toolchain"]["architectures"][sys.argv[2]]["linux_arch"])
PY
)
actual_linux_arch=$(uname -m)
[ "$actual_linux_arch" = "$expected_linux_arch" ] || fail "container architecture is $actual_linux_arch, expected $expected_linux_arch"

image_digest=${BUILDER_IMAGE_DIGEST:-}
if [ -z "$image_digest" ] && [ -f "$METADATA/image-digest" ]; then
    image_digest=$(tr -d '[:space:]' < "$METADATA/image-digest")
fi
printf '%s\n' "$image_digest" | grep -Eq '^sha256:[0-9a-f]{64}$' || fail "builder image digest is not recorded as sha256"

network_mode=${NETWORK_MODE:-none}
[ "$network_mode" = none ] || fail "PoC requires NETWORK_MODE=none, got $network_mode"

if awk 'NR > 1 && $1 != "lo" { found=1 } END { exit found ? 0 : 1 }' /proc/net/route 2>/dev/null; then
    fail "non-loopback IPv4 route is present"
fi
if awk '$1 != "00000000000000000000000000000000" && $1 != "00000000000000000000000000000001" { found=1 } END { exit found ? 0 : 1 }' /proc/net/ipv6_route 2>/dev/null; then
    fail "non-loopback IPv6 route is present"
fi

source_stamp=${SOURCE_STAMP:-$WORK/metadata/source-complete.stamp}
fetch_stamp=${FETCH_STAMP:-$WORK/metadata/fetch-complete.stamp}
need_file "$source_stamp"
need_file "$fetch_stamp"
source_report=${SOURCE_INPUTS_REPORT:-$METADATA/source-inputs.json}
fetch_report=${FETCH_INPUTS_REPORT:-$METADATA/fetch-inputs.json}
need_file "$source_report"
need_file "$fetch_report"
$PYTHON - "$LOCK" "$source_report" "$fetch_report" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    lock = json.load(stream)
expected = lock["source_identity"]
with open(sys.argv[2], encoding="utf-8") as stream:
    source = json.load(stream)
if source.get("status") != "complete":
    raise SystemExit(f"{sys.argv[2]}: status is not complete")
for key in ("schema", "chromium_version", "devtools_revision"):
    if source.get(key) != expected[key]:
        raise SystemExit(f"{sys.argv[2]}: {key} does not match source lock")
with open(sys.argv[3], encoding="utf-8") as stream:
    fetch = json.load(stream)
if fetch.get("status") != "complete":
    raise SystemExit(f"{sys.argv[3]}: status is not complete")
if fetch.get("chromium_version") != lock["chromium"]["version"]:
    raise SystemExit(f"{sys.argv[3]}: Chromium version does not match the input lock")
chromium_entry = next((item for item in fetch.get("entries", []) if item.get("name") == "chromium"), None)
if chromium_entry is None or chromium_entry.get("sha512") != lock["chromium"]["archive_sha512"]:
    raise SystemExit(f"{sys.argv[3]}: source archive digest does not match input lock")
for name, expected in lock.get("test_inputs", {}).items():
    entry = next((item for item in fetch.get("entries", []) if item.get("name") == name), None)
    if entry is None:
        raise SystemExit(f"{sys.argv[3]}: test input is missing: {name}")
    for key in ("filename", "size", "sha512"):
        if entry.get(key) != expected[key]:
            raise SystemExit(f"{sys.argv[3]}: test input {name} does not match lock field {key}")
PY

architecture_toolchain_json=$($PYTHON - "$LOCK" "$ARCH" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    lock = json.load(stream)
toolchain = lock["toolchain"]
arch = toolchain["architectures"][sys.argv[2]]
print(toolchain["root"])
print(toolchain["version"])
print(arch["target_triple"])
print(arch["archive_sha256"])
PY
)
mapfile -t toolchain_values <<< "$architecture_toolchain_json"
LLVM_ROOT=${toolchain_values[0]}
LLVM_VERSION=${toolchain_values[1]}
TARGET_TRIPLE=${toolchain_values[2]}
TOOLCHAIN_SHA256=${toolchain_values[3]}
[ "$LLVM_ROOT" = "/opt/llvm-musl" ] || fail "toolchain root is not /opt/llvm-musl"

for variable in CC CXX AR RANLIB NM STRIP OBJCOPY BUILD_CC BUILD_CXX BUILD_AR BUILD_RANLIB BUILD_NM BUILD_STRIP BUILD_OBJCOPY; do
    value=${!variable:-}
    [ -n "$value" ] || fail "$variable is unset"
    case "$value" in
        "$LLVM_ROOT"/*) ;;
        *) fail "$variable=$value is outside $LLVM_ROOT" ;;
    esac
    resolved=$(command -v "$value" 2>/dev/null || true)
    [ -n "$resolved" ] || fail "$variable does not resolve: $value"
    resolved=$(readlink -f "$resolved")
    case "$resolved" in
        "$LLVM_ROOT"/*) ;;
        *) fail "$variable resolves outside $LLVM_ROOT: $resolved" ;;
    esac
done

clang_version=$($CC --version 2>&1) || fail "cannot execute CC"
printf '%s\n' "$clang_version" | grep -Fq "$LLVM_VERSION" || fail "clang version is not $LLVM_VERSION"
dumpmachine=$($CC -dumpmachine 2>/dev/null) || fail "cannot query compiler target"
[ "$dumpmachine" = "$TARGET_TRIPLE" ] || fail "compiler target is $dumpmachine, expected $TARGET_TRIPLE"

for tool in clang clang++ ld.lld llvm-ar llvm-ranlib llvm-nm llvm-strip llvm-objcopy; do
    need_file "$LLVM_ROOT/bin/$tool"
done
need_file /usr/local/bin/ninja
[ "$(/usr/local/bin/ninja --version)" = 1.12.1 ] || fail "Ninja is not version 1.12.1"
file /usr/local/bin/ninja | grep -Fq 'statically linked' || fail "Ninja is not statically linked"
ld_version=$($LLVM_ROOT/bin/ld.lld --version 2>&1) || fail "cannot execute LLD"
printf '%s\n' "$ld_version" | grep -Fq "$LLVM_VERSION" || fail "LLD version is not $LLVM_VERSION"
: > "$METADATA/toolchain-config-sha256"
: > "$METADATA/toolchain-configs"
for config_file in clang.cfg clang++.cfg; do
    need_file "$LLVM_ROOT/bin/$config_file"
    sha256sum "$LLVM_ROOT/bin/$config_file" >> "$METADATA/toolchain-config-sha256"
    cat "$LLVM_ROOT/bin/$config_file" >> "$METADATA/toolchain-configs"
done
expected_cfg=$($PYTHON - "$LOCK" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    lock = json.load(stream)
print(lock["toolchain"]["clang_cfg_sha256"])
print(lock["toolchain"]["clangxx_cfg_sha256"])
PY
)
mapfile -t expected_cfg_values <<< "$expected_cfg"
actual_cfg_values=("$(sha256sum "$LLVM_ROOT/bin/clang.cfg" | awk '{print $1}')" "$(sha256sum "$LLVM_ROOT/bin/clang++.cfg" | awk '{print $1}')")
[ "${actual_cfg_values[0]}" = "${expected_cfg_values[0]}" ] || fail "clang.cfg hash differs from lock"
[ "${actual_cfg_values[1]}" = "${expected_cfg_values[1]}" ] || fail "clang++.cfg hash differs from lock"
for required in \
    "$LLVM_ROOT/include/c++/v1" \
    "$LLVM_ROOT/lib/libc++.a" \
    "$LLVM_ROOT/lib/libc++abi.a" \
    "$LLVM_ROOT/lib/libunwind.a"; do
    [ -e "$required" ] || fail "missing toolchain content $required"
done
resource_dir=$($CC -print-resource-dir 2>/dev/null) || fail "cannot query Clang resource directory"
case "$resource_dir" in
    "$LLVM_ROOT"/lib/clang/*) ;;
    *) fail "Clang resource directory is outside $LLVM_ROOT: $resource_dir" ;;
esac
[ -d "$resource_dir/include" ] || fail "missing Clang resource headers $resource_dir/include"
find "$resource_dir" -path '*/lib/linux/libclang_rt.builtins-*.a' -type f -print -quit | grep -q . || fail "missing compiler-rt builtins"
find "$LLVM_ROOT/include/c++/v1" -name __config_site -type f -print -quit | grep -q . || fail "missing libc++ __config_site"

ccache=${CCACHE_BIN:-/usr/bin/ccache}
need_file "$ccache"
ccache_resolved=$(readlink -f "$ccache")
ccache_volume=${CCACHE_VOLUME:-ungoogled-chromium-ccache-$ARCH}
[ "$CCACHE_DIR" = /ccache ] || fail "CCACHE_DIR must be /ccache"
[ "$CCACHE_TEMPDIR" = /ccache/tmp ] || fail "CCACHE_TEMPDIR must be /ccache/tmp"
[ "$CCACHE_MAXSIZE" = 10G ] || fail "CCACHE_MAXSIZE must be 10G"
[ "${CCACHE_COMPILERCHECK:-}" = content ] || fail "ccache compiler_check must be content"
[ "${CCACHE_COMPRESS:-}" = true ] || fail "ccache compression must be enabled"
[ "${CCACHE_COMPRESSLEVEL:-}" = 1 ] || fail "ccache compression level must be 1"
[ "${CCACHE_CC:-}" = "$CC" ] || fail "CCACHE_CC must name the absolute Laputa compiler"
[ "${CCACHE_NAMESPACE:-}" = "$ARCH-$TOOLCHAIN_SHA256" ] || fail "ccache namespace does not match architecture and toolchain"
[ "${CCACHE_SLOPPINESS+x}" != x ] || fail "CCACHE_SLOPPINESS must be unset"
[ "${CCACHE_BASEDIR+x}" != x ] || fail "CCACHE_BASEDIR must be unset"
[ "$ccache_volume" = "ungoogled-chromium-ccache-$ARCH" ] || fail "wrong per-architecture ccache volume $ccache_volume"

ccache_config=$($ccache --show-config 2>&1) || fail "ccache --show-config failed"
printf '%s\n' "$ccache_config" > "$METADATA/ccache-config"
printf '%s\n' "$ccache_config" | grep -Eq 'compiler_check[[:space:]]*=[[:space:]]*content' || fail "ccache config does not report content compiler checking"
printf '%s\n' "$ccache_config" | grep -Eq 'max_size[[:space:]]*=[[:space:]]*(10G|10\.0G|10\.0[[:space:]]+GB)' || fail "ccache config does not report 10G max size"
printf '%s\n' "$ccache_config" | grep -Eq 'compression[[:space:]]*=[[:space:]]*(true|1)' || fail "ccache config does not report compression"
printf '%s\n' "$ccache_config" | grep -Fq "namespace = $ARCH-$TOOLCHAIN_SHA256" || fail "ccache config does not report the locked namespace"
mkdir -p "$CCACHE_TEMPDIR"
ccache -z >/dev/null

package_lock=$ROOT/config/packages.$ARCH.lock
package_manifest=${APK_MANIFEST:-$METADATA/packages.$ARCH.txt}
need_file "$package_lock"
need_file "$package_manifest"
$PYTHON "$ROOT/scripts/lockcheck.py" packages "$package_lock" "$package_manifest"

environment_metadata=${ENVIRONMENT_METADATA:-$METADATA/environment.json}
need_file "$environment_metadata"
$PYTHON "$ROOT/scripts/lockcheck.py" environment "$LOCK" "$environment_metadata" "$ARCH"

policy=$ROOT/config/system-library-preflight.schema.json
preflight=${SYSTEM_LIBRARY_PREFLIGHT:-$METADATA/system-library-preflight.json}
need_file "$policy"
need_file "$preflight"
$PYTHON "$ROOT/scripts/lockcheck.py" preflight "$policy" "$preflight" "$policy" "$package_lock"

if [ -e "$STAGE_ROOT" ] && find "$STAGE_ROOT" -path '*/ccache*' -print -quit | grep -q .; then
    fail "ccache executable or cache data is eligible for staging"
fi
ccache_deps=$($ccache --version >/dev/null; ldd "$ccache_resolved" 2>/dev/null || true)
printf '%s\n' "$ccache_deps" > "$METADATA/ccache-runtime-dependencies.txt"
if printf '%s\n' "$ccache_deps" | grep -Eq 'not found|libc\.so\.6'; then
    fail "ccache runtime dependency closure is invalid"
fi
grep -Fxq 'ccache-4.13.6-r0' "$package_manifest" || fail "ccache package version is not locked"
apk info -a ccache > "$METADATA/ccache-apk-info.txt" 2>&1 || fail "cannot record ccache APK provenance"
printf '%s\n' 'GPL-3.0-or-later' > "$METADATA/ccache-license"
printf '%s\n' "builder-only" > "$METADATA/ccache-runtime-classification"

printf '%s\n' "$image_digest" > "$METADATA/gate-a-image-digest"
printf '%s\n' "architecture=$ARCH" "linux_arch=$actual_linux_arch" "toolchain=$LLVM_ROOT" "toolchain_sha256=$TOOLCHAIN_SHA256" "ccache_volume=$ccache_volume" "network=none" > "$METADATA/gate-a-summary"
echo "Gate A: environment identity passed for $ARCH"
