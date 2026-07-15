#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
PYTHON=${PYTHON:-python3}

[ "${NETWORK_MODE:-fetch}" = fetch ] || {
    echo "Fetch requires NETWORK_MODE=fetch" >&2
    exit 1
}

exec "$PYTHON" "$ROOT/scripts/fetch-inputs.py" \
    --lock "$ROOT/config/inputs.lock" \
    --inputs-root "${CHROMIUM_INPUTS_ROOT:-$WORK/inputs}" \
    --metadata "${CHROMIUM_METADATA_ROOT:-$WORK/metadata}"
