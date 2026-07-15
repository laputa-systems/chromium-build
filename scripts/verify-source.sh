#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
SOURCE_ROOT=${CHROMIUM_SOURCE_ROOT:-$WORK/src}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
PYTHON=${PYTHON:-python3}

fail() {
    echo "Gate C: $*" >&2
    exit 1
}

need_file() {
    [ -f "$1" ] || fail "missing $1"
}

[ "${NETWORK_MODE:-none}" = none ] || fail "Gate C requires NETWORK_MODE=none"
need_file "$ROOT/scripts/verify-source.py"
need_file "$ROOT/config/inputs.lock"
need_file "$ROOT/config/patch-inventory.json"
need_file "$ROOT/config/patch-disposition.json"
need_file "$METADATA/source-inputs.json"
need_file "$METADATA/patches.json"
need_file "$METADATA/pruning.json"
need_file "$METADATA/domain-substitution.json"
need_file "$METADATA/toolchain-selection.json"
need_file "$METADATA/system-unbundle.json"

exec "$PYTHON" "$ROOT/scripts/verify-source.py" \
    --source-root "$SOURCE_ROOT" \
    --inputs-lock "$ROOT/config/inputs.lock" \
    --source-inputs "$METADATA/source-inputs.json" \
    --patch-inventory "$ROOT/config/patch-inventory.json" \
    --patch-disposition "$ROOT/config/patch-disposition.json" \
    --patch-provenance "$METADATA/patches.json" \
    --pruning-report "$METADATA/pruning.json" \
    --domain-report "$METADATA/domain-substitution.json" \
    --toolchain-selection "$METADATA/toolchain-selection.json" \
    --unbundle-report "$METADATA/system-unbundle.json" \
    --output "$METADATA/gate-c-report.json"
