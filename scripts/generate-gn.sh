#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
SOURCE_ROOT=${CHROMIUM_SOURCE_ROOT:-$WORK/src}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
PROFILE=${CHROMIUM_PROFILE:-headless-debug}
GN=${GN:-gn}

fail() {
    echo "Gate D: $*" >&2
    exit 1
}

[ "${NETWORK_MODE:-none}" = none ] || fail "GN generation requires NETWORK_MODE=none"
[ -f "$METADATA/gate-c-report.json" ] || fail "Gate C has not passed"
[ -f "$METADATA/gate-a-summary" ] || fail "Gate A has not passed"
grep -Eq '"status"[[:space:]]*:[[:space:]]*"complete"' "$METADATA/gate-c-report.json" || fail "Gate C report is incomplete"
grep -Eq '"network"[[:space:]]*:[[:space:]]*"none"' "$METADATA/gate-c-report.json" || fail "Gate C report is not offline"
[ -d "$SOURCE_ROOT" ] || fail "missing source root $SOURCE_ROOT"
command -v "$GN" >/dev/null 2>&1 || fail "GN is unavailable"

if awk 'NR > 1 && $1 != "lo" { found=1 } END { exit found ? 0 : 1 }' /proc/net/route 2>/dev/null; then
    fail "non-loopback IPv4 route is present"
fi
if awk '$1 != "00000000000000000000000000000000" && $1 != "00000000000000000000000000000001" { found=1 } END { exit found ? 0 : 1 }' /proc/net/ipv6_route 2>/dev/null; then
    fail "non-loopback IPv6 route is present"
fi

"$ROOT/scripts/generate-args.sh"
args_file=${GN_ARGS_OUTPUT:-$METADATA/$PROFILE.args.gn}
[ -s "$args_file" ] || fail "GN args file is empty"
mkdir -p "$OUT"
args=$(tr '\n' ' ' < "$args_file")

gn_log=$METADATA/gn-gen.log
if ! "$GN" gen "$OUT" --root="$SOURCE_ROOT" --fail-on-unused-args --args="$args" >"$gn_log" 2>&1; then
    cat "$gn_log" >&2
    fail "gn gen failed"
fi

canonical_tmp="$METADATA/.gn-args.canonical.txt.tmp-$$"
if ! "$GN" args "$OUT" --list --short >"$canonical_tmp" 2>"$METADATA/gn-args.log"; then
    cat "$METADATA/gn-args.log" >&2
    rm -f "$canonical_tmp"
    fail "cannot read canonical GN args"
fi
mv "$canonical_tmp" "$METADATA/gn-args.canonical.txt"

json_tmp="$METADATA/.gn-args.effective.json.tmp-$$"
if ! "$GN" args "$OUT" --list --json >"$json_tmp" 2>>"$METADATA/gn-args.log"; then
    rm -f "$json_tmp"
    fail "cannot read effective GN args JSON"
fi
mv "$json_tmp" "$METADATA/gn-args.effective.json"

target_cpu=arm64
[ "${CHROMIUM_ARCH:-arm64}" = amd64 ] && target_cpu=x64
clang_major=$(awk -F'"' '$1 ~ /^clang_version/ { print $2; exit }' "$args_file")
[ -n "$clang_major" ] || fail "GN args omit clang_version"
"$ROOT/scripts/verify-gn-args.py" \
    --args "$OUT/args.gn" \
    --canonical "$METADATA/gn-args.canonical.txt" \
    --effective-json "$METADATA/gn-args.effective.json" \
    --probe "$METADATA/gn-arg-probe.json" \
    --target-cpu "$target_cpu" \
    --clang-major "$clang_major" \
    --output "$METADATA/gn-args-report.json"

check_status=skipped
check_reason="bounded GN check is disabled; set GN_CHECK=1 to run //chrome:chrome"
if [ "${GN_CHECK:-0}" = 1 ]; then
    check_status=passed
    check_reason="//chrome:chrome"
    if ! timeout "${GN_CHECK_TIMEOUT:-120}" "$GN" check "$OUT" //chrome:chrome >"$METADATA/gn-check.log" 2>&1; then
        cat "$METADATA/gn-check.log" >&2
        fail "bounded gn check failed"
    fi
fi

cat > "$METADATA/.gate-d-report.tmp-$$" <<EOF
{
  "schema": 1,
  "status": "complete",
  "network": "none",
  "profile": "$PROFILE",
  "output": "$OUT",
  "args_file": "$args_file",
  "canonical_args": "$METADATA/gn-args.canonical.txt",
  "effective_args": "$METADATA/gn-args.effective.json",
  "gn_check": "$check_status",
  "gn_check_detail": "$check_reason"
}
EOF
mv "$METADATA/.gate-d-report.tmp-$$" "$METADATA/gate-d-report.json"
echo "Gate D: GN generation passed for $PROFILE"
