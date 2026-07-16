#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
OUT=${GN_OUT:-$WORK/out/headless-debug}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
PROFILE=${CHROMIUM_PROFILE:-headless-debug}
TEST_OUTPUT=${TEST_OUTPUT_ROOT:-$WORK/test-output/headless-debug}

fail() { echo "test-fast: $*" >&2; exit 1; }
[ "${NETWORK_MODE:-none}" = none ] || fail "test-fast requires NETWORK_MODE=none"
[ -f "$METADATA/build-$PROFILE.json" ] || fail "Chrome build has not passed"
[ -d "$ROOT/tests/fixtures" ] || fail "fixtures are missing"
python3 -m unittest discover -s "$ROOT/tests" -p 'test_*.py'
chrome=$($ROOT/scripts/resolve-output.sh "$OUT" //chrome:chrome 2>/dev/null) || fail "cannot resolve Chrome"
mkdir -p "$TEST_OUTPUT"
chmod 0777 "$TEST_OUTPUT"
rm -f "$TEST_OUTPUT/test-fast.json" "$TEST_OUTPUT/fast-screenshot.png"
if [ "$(id -u)" = 0 ] && command -v su >/dev/null 2>&1; then
    exec su chromium -s /bin/sh -c "exec python3 '$ROOT/scripts/test-fast.py' --chrome '$chrome' --fixtures '$ROOT/tests/fixtures' --output '$TEST_OUTPUT'"
fi
exec python3 "$ROOT/scripts/test-fast.py" --chrome "$chrome" --fixtures "$ROOT/tests/fixtures" --output "$TEST_OUTPUT"
