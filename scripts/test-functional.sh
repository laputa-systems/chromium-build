#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
PROFILE=${CHROMIUM_PROFILE:-headless-debug}
TEST_OUTPUT=${TEST_OUTPUT_ROOT:-$WORK/test-output/$PROFILE}
INPUTS=${CHROMIUM_INPUTS_ROOT:-$WORK/inputs}
TIMEOUT=${FUNCTIONAL_TEST_TIMEOUT:-180}

fail() { echo "test: $*" >&2; exit 1; }
[ "${NETWORK_MODE:-none}" = none ] || fail "functional test requires NETWORK_MODE=none"
[ -f "$METADATA/build-$PROFILE.json" ] || fail "Chrome build has not passed"
[ -d "$ROOT/tests/fixtures" ] || fail "fixtures are missing"
python3 -m unittest discover -s "$ROOT/tests" -p 'test_*.py'
chrome=$($ROOT/scripts/resolve-output.sh "$OUT" //chrome:chrome 2>/dev/null) || fail "cannot resolve Chrome"
ublock="$INPUTS/uBlock0_1.72.0.chromium.zip"
[ -f "$ublock" ] || fail "pinned uBlock archive is missing: $ublock"
mkdir -p "$TEST_OUTPUT"
chmod 0777 "$TEST_OUTPUT"
rm -f "$TEST_OUTPUT/test.json" "$TEST_OUTPUT/functional-tcp.png" "$TEST_OUTPUT/functional-pipe.png" \
    "$TEST_OUTPUT/media-runtime.json" "$TEST_OUTPUT/functional-tcp-result.json" "$TEST_OUTPUT/functional-pipe-result.json"

if [ "$(id -u)" = 0 ] && command -v su >/dev/null 2>&1; then
    exec su chromium -s /bin/sh -c "exec timeout '$TIMEOUT' python3 '$ROOT/scripts/test-functional.py' --chrome '$chrome' --fixtures '$ROOT/tests/fixtures' --ublock-archive '$ublock' --output '$TEST_OUTPUT' --require-llvmpipe --require-media"
fi
exec timeout "$TIMEOUT" python3 "$ROOT/scripts/test-functional.py" \
    --chrome "$chrome" \
    --fixtures "$ROOT/tests/fixtures" \
    --ublock-archive "$ublock" \
    --output "$TEST_OUTPUT" \
    --require-llvmpipe \
    --require-media
