#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
NINJA=${NINJA:-ninja}
TARGET=${1:?build-target needs an output or label}
JOBS=${JOBS:-1}

fail() { echo "build-target: $*" >&2; exit 1; }
[ "${NETWORK_MODE:-none}" = none ] || fail "build-target requires NETWORK_MODE=none"
[ -f "$METADATA/gate-d-report.json" ] || fail "Gate D has not passed"
[ -d "$OUT" ] || fail "missing GN output $OUT"
mkdir -p "$METADATA"

if [[ "$TARGET" == //*:* ]]; then
    resolved=$($ROOT/scripts/resolve-output.sh "$OUT" "$TARGET" 2>/dev/null || true)
    [ -n "$resolved" ] || fail "label did not resolve to one output: $TARGET"
    TARGET=${resolved#"$OUT/"}
fi
log="$METADATA/build-target.log"
echo "build-target: target=$TARGET jobs=$JOBS" | tee "$log"
if ! "$NINJA" -C "$OUT" -j "$JOBS" "$TARGET" 2>&1 | tee -a "$log"; then
    echo "build-target: Ninja state was preserved for resume" >&2
    exit 1
fi
echo "build-target: completed $TARGET"
