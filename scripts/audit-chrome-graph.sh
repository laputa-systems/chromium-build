#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
NINJA=${NINJA:-samu}
PYTHON=${PYTHON:-python3}
TIMEOUT=${GATE_F_TIMEOUT:-600}

fail() {
    echo "Gate F: $*" >&2
    exit 1
}

[ "${NETWORK_MODE:-none}" = none ] || fail "Chrome graph audit requires NETWORK_MODE=none"
[ -f "$METADATA/gate-d-report.json" ] || fail "Gate D has not passed"
[ -d "$OUT" ] || fail "missing GN output $OUT"
command -v "$NINJA" >/dev/null 2>&1 || fail "Ninja/Samurai is unavailable"

if awk 'NR > 1 && $1 != "lo" { found=1 } END { exit found ? 0 : 1 }' /proc/net/route 2>/dev/null; then
    fail "non-loopback IPv4 route is present"
fi
if awk '$1 != "00000000000000000000000000000000" && $1 != "00000000000000000000000000000001" { found=1 } END { exit found ? 0 : 1 }' /proc/net/ipv6_route 2>/dev/null; then
    fail "non-loopback IPv6 route is present"
fi

mkdir -p "$METADATA"
commands="$METADATA/chrome-commands.txt"
dry_run="$METADATA/chrome-dry-run.txt"
if ! timeout "$TIMEOUT" "$NINJA" -C "$OUT" -t commands chrome >"$commands" 2>"$METADATA/chrome-commands.log"; then
    cat "$METADATA/chrome-commands.log" >&2
    fail "could not enumerate Chrome commands"
fi
dry_run_mode=passed
if timeout "$TIMEOUT" "$NINJA" -C "$OUT" -n chrome >"$dry_run" 2>"$METADATA/chrome-dry-run.log"; then
    :
else
    dry_run_status=$?
    if [ "$dry_run_status" -eq 139 ] || grep -qi "segmentation fault" "$METADATA/chrome-dry-run.log"; then
        cp "$commands" "$dry_run"
        dry_run_mode=command-list-fallback
    else
        cat "$METADATA/chrome-dry-run.log" >&2
        fail "Chrome dry-run failed"
    fi
fi

if ! "$PYTHON" "$ROOT/scripts/audit-chrome-graph.py" \
    --commands "$commands" \
    --dry-run "$dry_run" \
    --dry-run-mode "$dry_run_mode" \
    --out "$OUT" \
    --output "$METADATA/gate-f-report.json"; then
    fail "Chrome graph audit failed"
fi
if [ "$dry_run_mode" = passed ]; then
    echo "Gate F: Chrome graph dry-run and toolchain audit passed"
else
    echo "Gate F: Chrome graph command audit passed; Ninja dry-run unavailable ($dry_run_mode)"
fi
