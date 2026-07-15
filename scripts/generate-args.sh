#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
WORK=${CHROMIUM_WORK_ROOT:-/work}
SOURCE_ROOT=${CHROMIUM_SOURCE_ROOT:-$WORK/src}
METADATA=${CHROMIUM_METADATA_ROOT:-$WORK/metadata}
PROFILE=${CHROMIUM_PROFILE:-headless-debug}
ARCH=${CHROMIUM_ARCH:-arm64}
PYTHON=${PYTHON:-python3}

fail() {
    echo "Gate D: $*" >&2
    exit 1
}

case "$ARCH" in
    arm64) target_cpu=arm64 ;;
    amd64) target_cpu=x64 ;;
    *) fail "unsupported architecture $ARCH" ;;
esac

profile="$ROOT/config/profiles/$PROFILE.gn"
args_output=${GN_ARGS_OUTPUT:-$METADATA/$PROFILE.args.gn}
probe_output=${GN_ARG_PROBE_OUTPUT:-$METADATA/gn-arg-probe.json}

[ -d "$SOURCE_ROOT" ] || fail "missing source root $SOURCE_ROOT"
[ -f "$profile" ] || fail "missing GN profile $profile"
mkdir -p "$METADATA"

clang_major=$($PYTHON - "$ROOT/config/inputs.lock" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["toolchain"]["version"].split(".")[0])
PY
)

exec "$PYTHON" "$ROOT/scripts/generate-gn-args.py" \
    --source-root "$SOURCE_ROOT" \
    --profile "$profile" \
    --target-cpu "$target_cpu" \
    --clang-major "$clang_major" \
    --args-output "$args_output" \
    --probe-output "$probe_output"
