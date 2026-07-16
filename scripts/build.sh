#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
PROFILE=${CHROMIUM_PROFILE:-headless-debug}
NINJA=${NINJA:-ninja}
JOBS=${JOBS:-}
LOAD_LIMIT=${NINJA_LOAD_LIMIT:-}

fail() { echo "build: $*" >&2; exit 1; }
[ "${NETWORK_MODE:-none}" = none ] || fail "build requires NETWORK_MODE=none"
[ -f "$METADATA/gate-d-report.json" ] || fail "Gate D has not passed"
[ -f "$METADATA/gate-f-report.json" ] || fail "Gate F has not passed"
[ -d "$OUT" ] || fail "missing GN output $OUT"
command -v "$NINJA" >/dev/null 2>&1 || fail "Ninja is unavailable"

if [ -z "$JOBS" ]; then
    cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
    JOBS=$((cpus < 8 ? cpus : 8))
    [ "$JOBS" -gt 0 ] || JOBS=1
fi
case "$JOBS" in *[!0-9]*|'') fail "JOBS must be a positive integer" ;; esac
[ "$JOBS" -gt 0 ] || fail "JOBS must be positive"

mkdir -p "$METADATA"
log="$METADATA/build-$PROFILE.log"
before="$METADATA/build-ccache-before.txt"
after="$METADATA/build-ccache-after.txt"
report="$METADATA/build-$PROFILE.json"
command=("$NINJA" -C "$OUT" -j "$JOBS")
[ -n "$LOAD_LIMIT" ] && command+=( -l "$LOAD_LIMIT" )
command+=(chrome)

ccache --show-stats >"$before" 2>&1 || true
start=$(date +%s)
echo "build: profile=$PROFILE target=chrome jobs=$JOBS load=${LOAD_LIMIT:-unlimited}" | tee "$log"
echo "build: source=${CHROMIUM_SOURCE_ROOT:-$WORK/src} out=$OUT" | tee -a "$log"
df -Pk "$WORK" | tee -a "$log"
if ! "${command[@]}" 2>&1 | tee -a "$log"; then
    echo "build: failed; Ninja output was preserved for resume" >&2
    echo "build: command=${command[*]}" >&2
    echo "build: source=${CHROMIUM_SOURCE_ROOT:-$WORK/src} profile=$PROFILE" >&2
    exit 1
fi
end=$(date +%s)
ccache --show-stats >"$after" 2>&1 || true
df -Pk "$WORK" | tee -a "$log"

chrome=$($ROOT/scripts/resolve-output.sh "$OUT" //chrome:chrome 2>>"$log") || fail "could not resolve //chrome:chrome output"
[ -x "$chrome" ] || fail "resolved Chrome is not executable: $chrome"
python3 - "$report" "$PROFILE" "$OUT" "$chrome" "$JOBS" "${LOAD_LIMIT:-}" "$start" "$end" <<'PY'
import json
import sys
from pathlib import Path

report, profile, out, chrome, jobs, load, start, end = sys.argv[1:]
Path(report).write_text(json.dumps({
    "schema": 1,
    "build_driver_schema": 1,
    "status": "complete",
    "network": "none",
    "profile": profile,
    "target": "chrome",
    "output": out,
    "chrome": chrome,
    "jobs": int(jobs),
    "load_limit": load or None,
    "started": int(start),
    "finished": int(end),
    "duration_seconds": int(end) - int(start),
    "ccache_before": str(Path(report).with_name("build-ccache-before.txt")),
    "ccache_after": str(Path(report).with_name("build-ccache-after.txt")),
}, indent=2) + "\n", encoding="utf-8")
PY
echo "build: Chrome built successfully in $((end - start))s: $chrome"
