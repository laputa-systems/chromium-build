#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
SOURCE_ROOT=${CHROMIUM_SOURCE_ROOT:-$WORK/src}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
NINJA=${NINJA:-ninja}
GN=${GN:-gn}
READELF=${READELF:-/opt/llvm-musl/bin/llvm-readelf}
TIMEOUT=${GATE_E_TIMEOUT:-300}
TARGET_LABEL=//tools/hermetic_smoke:hermetic_smoke
NINJA_TARGET=tools/hermetic_smoke:hermetic_smoke

fail() {
    echo "Gate E: $*" >&2
    exit 1
}

[ "${NETWORK_MODE:-none}" = none ] || fail "smoke build requires NETWORK_MODE=none"
[ -f "$METADATA/gate-d-report.json" ] || fail "Gate D has not passed"
[ -d "$OUT" ] || fail "missing GN output $OUT"
command -v "$NINJA" >/dev/null 2>&1 || fail "Ninja is unavailable"
command -v "$GN" >/dev/null 2>&1 || fail "GN is unavailable"
[ -x "$READELF" ] || fail "LLVM readelf is unavailable"

if awk 'NR > 1 && $1 != "lo" { found=1 } END { exit found ? 0 : 1 }' /proc/net/route 2>/dev/null; then
    fail "non-loopback IPv4 route is present"
fi
if awk '$1 != "00000000000000000000000000000000" && $1 != "00000000000000000000000000000001" { found=1 } END { exit found ? 0 : 1 }' /proc/net/ipv6_route 2>/dev/null; then
    fail "non-loopback IPv6 route is present"
fi

mkdir -p "$METADATA"
build_log="$METADATA/gate-e-build.log"
commands="$METADATA/gate-e-commands.txt"
run_log="$METADATA/gate-e-run.log"
desc="$METADATA/gate-e-desc.txt"

if ! timeout "$TIMEOUT" "$NINJA" -C "$OUT" "$NINJA_TARGET" >"$build_log" 2>&1; then
    cat "$build_log" >&2
    fail "hermetic smoke target failed to build"
fi
if ! "$NINJA" -C "$OUT" -t commands "$NINJA_TARGET" >"$commands" 2>"$METADATA/gate-e-commands.log"; then
    cat "$METADATA/gate-e-commands.log" >&2
    fail "could not enumerate hermetic smoke commands"
fi
grep -Fq -- "-fexceptions" "$commands" || fail "smoke compile command does not enable exceptions"
grep -Fq -- "-pthread" "$commands" || fail "smoke commands do not select pthreads"
grep -Fq -- "/opt/llvm-musl/lib/libc++.a" "$commands" || fail "smoke link omits external libc++"
grep -Fq -- "/opt/llvm-musl/lib/libc++abi.a" "$commands" || fail "smoke link omits external libc++abi"
grep -Fq -- "/opt/llvm-musl/lib/libunwind.a" "$commands" || fail "smoke link omits external libunwind"

if ! "$GN" desc "$OUT" "$TARGET_LABEL" outputs --root="$SOURCE_ROOT" >"$desc" 2>"$METADATA/gate-e-desc.log"; then
    cat "$METADATA/gate-e-desc.log" >&2
    fail "could not resolve hermetic smoke output"
fi
binary=
while IFS= read -r candidate; do
    candidate=${candidate#"${candidate%%[![:space:]]*}"}
    candidate=${candidate%"${candidate##*[![:space:]]}"}
    [ -n "$candidate" ] || continue
    if [ -x "$candidate" ]; then
        binary=$candidate
        break
    fi
    if [ -x "$OUT/$candidate" ]; then
        binary=$OUT/$candidate
        break
    fi
done <"$desc"
[ -n "$binary" ] || fail "GN did not report a runnable hermetic smoke binary"

if ! "$READELF" -l "$binary" | grep -Fq '/lib/ld-musl-'; then
    fail "smoke binary does not use the musl dynamic loader"
fi
if "$READELF" -d "$binary" | grep -Eiq '(^|/)(libc\.so\.6|ld-linux|libstdc\+\+|libgcc(_s)?)(\.so|\.a|$)'; then
    fail "smoke binary has a forbidden GNU runtime dependency"
fi
if ! timeout "$TIMEOUT" "$binary" >"$run_log" 2>&1; then
    cat "$run_log" >&2
    fail "hermetic smoke target failed at runtime"
fi

cat > "$METADATA/.gate-e-report.tmp-$$" <<EOF
{
  "schema": 1,
  "status": "complete",
  "network": "none",
  "target": "$TARGET_LABEL",
  "ninja_target": "$NINJA_TARGET",
  "binary": "$binary",
  "build_log": "$build_log",
  "commands": "$commands",
  "run_log": "$run_log",
  "checks": {
    "target_built": true,
    "exceptions_enabled": true,
    "threads_enabled": true,
    "external_static_libcxx": true,
    "musl_interpreter": true,
    "no_forbidden_gnu_runtime": true,
    "target_ran": true
  }
}
EOF
mv "$METADATA/.gate-e-report.tmp-$$" "$METADATA/gate-e-report.json"
echo "Gate E: hermetic smoke target built and ran"
