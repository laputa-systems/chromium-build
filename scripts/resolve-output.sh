#!/usr/bin/env bash
set -euo pipefail

OUT=$1
LABEL=$2
GN=${GN:-gn}

while IFS= read -r candidate; do
    candidate=${candidate#"${candidate%%[![:space:]]*}"}
    candidate=${candidate%"${candidate##*[![:space:]]}"}
    [ -n "$candidate" ] || continue
    [ -x "$candidate" ] && { printf '%s\n' "$candidate"; exit 0; }
    [ -x "$OUT/$candidate" ] && { printf '%s\n' "$OUT/$candidate"; exit 0; }
done < <("$GN" desc "$OUT" "$LABEL" outputs --root="${CHROMIUM_SOURCE_ROOT:-/work/src}" 2>/dev/null)
exit 1
