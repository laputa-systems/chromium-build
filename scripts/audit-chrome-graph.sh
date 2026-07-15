#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
NINJA=${NINJA:-ninja}
PYTHON=${PYTHON:-python3}
TIMEOUT=${GATE_F_TIMEOUT:-600}

fail() {
    echo "Gate F: $*" >&2
    exit 1
}

[ "${NETWORK_MODE:-none}" = none ] || fail "Chrome graph audit requires NETWORK_MODE=none"
[ -f "$METADATA/gate-d-report.json" ] || fail "Gate D has not passed"
[ -d "$OUT" ] || fail "missing GN output $OUT"
command -v "$NINJA" >/dev/null 2>&1 || fail "Ninja is unavailable"

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
if ! timeout "$TIMEOUT" "$NINJA" -C "$OUT" -n chrome >"$dry_run" 2>"$METADATA/chrome-dry-run.log"; then
    cat "$METADATA/chrome-dry-run.log" >&2
    fail "Chrome dry-run failed"
fi
if grep -Fq 'ninja: no work to do' "$dry_run"; then
    dry_run_mode=no-work
fi

if ! "$PYTHON" "$ROOT/scripts/audit-chrome-graph.py" \
    --commands "$commands" \
    --dry-run "$dry_run" \
    --dry-run-mode "$dry_run_mode" \
    --out "$OUT" \
    --output "$METADATA/gate-f-report.json"; then
    fail "Chrome graph audit failed"
fi
oracle_commands="$METADATA/chrome-oracle-commands.txt"
if ! timeout "$TIMEOUT" "$NINJA" -C "$OUT" -t commands chrome >"$oracle_commands" 2>"$METADATA/chrome-oracle-commands.log"; then
    cat "$METADATA/chrome-oracle-commands.log" >&2
    fail "could not enumerate Chrome commands for the independent oracle"
fi
cmp -s "$commands" "$oracle_commands" || fail "Ninja command enumeration was not stable"
if ! "$PYTHON" "$ROOT/scripts/audit-chrome-oracle.py" \
    --commands "$oracle_commands" \
    --gate-report "$METADATA/gate-f-report.json" \
    --ninja-version "$($NINJA --version)" \
    --output "$METADATA/gate-f-oracle-report.json"; then
    fail "Chrome graph oracle failed"
fi
echo "Gate F: Chrome graph dry-run and toolchain audit passed"
