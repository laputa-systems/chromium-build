#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
METADATA=${CHROMIUM_METADATA_ROOT:-/work/metadata}
OUT=${PROBE_ROOT:-/work/test-output/gate-b}
ARCH=${CHROMIUM_ARCH:-arm64}
LLVM_ROOT=${LLVM_ROOT:-/opt/llvm-musl}
CC=${CC:-$LLVM_ROOT/bin/clang}
CXX=${CXX:-$LLVM_ROOT/bin/clang++}
AR=${AR:-$LLVM_ROOT/bin/llvm-ar}
RANLIB=${RANLIB:-$LLVM_ROOT/bin/llvm-ranlib}
LLD=${LLD:-$LLVM_ROOT/bin/ld.lld}
READELF=${READELF:-$LLVM_ROOT/bin/llvm-readelf}
PKG_CONFIG=${PKG_CONFIG:-pkg-config}
SRC=$ROOT/tests/probes

fail() {
    echo "Gate B: $*" >&2
    exit 1
}

need_file() {
    [ -f "$1" ] || fail "missing $1"
}

need_command() {
    command -v "$1" >/dev/null 2>&1 || fail "missing command $1"
}

[ -f "$METADATA/gate-a-summary" ] || fail "Gate A has not passed"
for file in c_runtime.c cpp_runtime.cc archive_member.c archive_main.c shared.c shared_main.c mesa_link.c egl_renderer.c nss_probe.c ffmpeg_probe.c; do
    need_file "$SRC/$file"
done
for tool in "$CC" "$CXX" "$AR" "$RANLIB" "$LLD" "$READELF"; do
    need_file "$tool"
done
need_command "$PKG_CONFIG"

case "$ARCH" in
    arm64) expected_interp=/lib/ld-musl-aarch64.so.1 ;;
    amd64) expected_interp=/lib/ld-musl-x86_64.so.1 ;;
    *) fail "unsupported architecture $ARCH" ;;
esac

rm -rf "$OUT"
mkdir -p "$OUT/objects" "$OUT/pkg" "$OUT/logs"

run() {
    local name=$1
    shift
    echo "Gate B: $name"
    "$@" >"$OUT/logs/$name.log" 2>&1 || {
        cat "$OUT/logs/$name.log" >&2
        fail "$name failed"
    }
}

compile_c() {
    local name=$1
    shift
    run "$name-compile" "$CC" -std=c11 -Wall -Wextra -Werror -O0 "$@"
}

compile_cpp() {
    local name=$1
    shift
    run "$name-compile" "$CXX" -std=c++20 -Wall -Wextra -Werror -O0 -fexceptions -frtti -L"$LLVM_ROOT/lib" "$@"
}

run_program() {
    local name=$1
    shift
    run "$name-run" "$@"
}

needed_entries() {
    "$READELF" -d "$1" | sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p'
}

check_elf() {
    local name=$1
    local file=$2
    local interpreter
    interpreter=$("$READELF" -l "$file" | sed -n 's/.*Requesting program interpreter: \([^]]*\)].*/\1/p')
    [ "$interpreter" = "$expected_interp" ] || fail "$name uses interpreter ${interpreter:-<none>}, expected $expected_interp"
    if needed_entries "$file" | grep -Eiq '(^|/)(libc\.so\.6|ld-linux|libstdc\+\+|libgcc(_s)?)(\.so|\.a|$)'; then
        fail "$name has a forbidden GNU/glibc DT_NEEDED entry"
    fi
}

pkg_flags() {
    local mode=$1
    shift
    "$PKG_CONFIG" --exists "$@" || fail "pkg-config packages unavailable: $*"
    "$PKG_CONFIG" "$mode" "$@"
}

read_flags() {
    local output=$1
    local -n destination=$2
    if [ -n "$output" ]; then
        read -r -a destination <<< "$output"
    else
        destination=()
    fi
}

compile_c c-runtime "$SRC/c_runtime.c" -o "$OUT/c-runtime" -pthread
run_program c-runtime "$OUT/c-runtime"
check_elf c-runtime "$OUT/c-runtime"

compile_cpp cpp-runtime "$SRC/cpp_runtime.cc" -o "$OUT/cpp-runtime" -pthread
run_program cpp-runtime "$OUT/cpp-runtime"
check_elf cpp-runtime "$OUT/cpp-runtime"

compile_c archive-member -c "$SRC/archive_member.c" -o "$OUT/objects/archive_member.o"
run archive-create "$AR" rcs "$OUT/libarchive-probe.a" "$OUT/objects/archive_member.o"
run archive-index "$RANLIB" "$OUT/libarchive-probe.a"
archive_listing=$("$AR" t "$OUT/libarchive-probe.a")
printf '%s\n' "$archive_listing" | grep -Fxq archive_member.o || fail "LLVM archive does not contain archive_member.o"
compile_c archive-main "$SRC/archive_main.c" "$OUT/libarchive-probe.a" -o "$OUT/archive-main"
run_program archive-main "$OUT/archive-main"
check_elf archive-main "$OUT/archive-main"

compile_c shared -fPIC -c "$SRC/shared.c" -o "$OUT/objects/shared.o"
run shared-link "$LLD" -shared -o "$OUT/libshared-probe.so" "$OUT/objects/shared.o"
run shared-executable-link "$CC" -fuse-ld="$LLD" "$SRC/shared_main.c" "$OUT/libshared-probe.so" -Wl,-rpath,"$OUT" -o "$OUT/shared-main"
run_program shared-main env LD_LIBRARY_PATH="$OUT" "$OUT/shared-main"
check_elf shared-main "$OUT/shared-main"

mesa_cflags=$(pkg_flags --cflags egl glesv2 gbm libdrm)
mesa_libs=$(pkg_flags --libs egl glesv2 gbm libdrm)
read_flags "$mesa_cflags" mesa_cflag_args
read_flags "$mesa_libs" mesa_lib_args
run mesa-link "$CC" -std=c11 -Wall -Wextra -Werror -O0 "${mesa_cflag_args[@]}" "$SRC/mesa_link.c" "${mesa_lib_args[@]}" -o "$OUT/pkg/mesa-link"
check_elf mesa-link "$OUT/pkg/mesa-link"

egl_cflags=$(pkg_flags --cflags egl glesv2)
egl_libs=$(pkg_flags --libs egl glesv2)
read_flags "$egl_cflags" egl_cflag_args
read_flags "$egl_libs" egl_lib_args
run egl-renderer-link "$CC" -std=c11 -Wall -Wextra -Werror -O0 "${egl_cflag_args[@]}" "$SRC/egl_renderer.c" "${egl_lib_args[@]}" -o "$OUT/pkg/egl-renderer"
run_program egl-renderer env EGL_PLATFORM=surfaceless "$OUT/pkg/egl-renderer"
check_elf egl-renderer "$OUT/pkg/egl-renderer"

nss_cflags=$(pkg_flags --cflags nss)
nss_libs=$(pkg_flags --libs nss)
read_flags "$nss_cflags" nss_cflag_args
read_flags "$nss_libs" nss_lib_args
run nss-link "$CC" -std=c11 -Wall -Wextra -Werror -O0 "${nss_cflag_args[@]}" "$SRC/nss_probe.c" "${nss_lib_args[@]}" -o "$OUT/pkg/nss"
run_program nss "$OUT/pkg/nss"
check_elf nss "$OUT/pkg/nss"

ffmpeg_cflags=$(pkg_flags --cflags libavcodec libavformat libavutil)
ffmpeg_libs=$(pkg_flags --libs libavcodec libavformat libavutil)
read_flags "$ffmpeg_cflags" ffmpeg_cflag_args
read_flags "$ffmpeg_libs" ffmpeg_lib_args
run ffmpeg-link "$CC" -std=c11 -Wall -Wextra -Werror -O0 "${ffmpeg_cflag_args[@]}" "$SRC/ffmpeg_probe.c" "${ffmpeg_lib_args[@]}" -o "$OUT/pkg/ffmpeg"
run_program ffmpeg "$OUT/pkg/ffmpeg"
check_elf ffmpeg "$OUT/pkg/ffmpeg"

python3 - "$OUT" "$ARCH" "$expected_interp" <<'PY'
import json
import sys
from pathlib import Path

output, architecture, interpreter = sys.argv[1:]
programs = sorted(
    str(path.relative_to(Path(output)))
    for path in Path(output).rglob("*")
    if path.is_file() and path.parent.name in ("", "pkg") and path.suffix not in (".log", ".a", ".so")
)
report = {
    "schema": 1,
    "status": "complete",
    "architecture": architecture,
    "interpreter": interpreter,
    "network": "none",
    "programs": programs,
    "checks": [
        "c",
        "cxx-containers-strings-exceptions-rtti-unwind-tls-threads",
        "llvm-ar-llvm-ranlib",
        "lld-shared-object",
        "musl-interpreter-and-direct-needed",
        "pkg-config-egl-gles-gbm-libdrm",
        "pkg-config-nss",
        "pkg-config-ffmpeg",
        "surfaceless-egl-renderer",
    ],
}
Path(output, "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
PY
echo "Gate B: direct compiler/runtime probes passed"
