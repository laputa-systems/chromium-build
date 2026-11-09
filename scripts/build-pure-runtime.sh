#!/usr/bin/env bash
set -euo pipefail

if [ "${PRODUCT:-}" != helium ] || [ "${TARGET_ARCH:-}" != arm64 ]; then
    mkdir -p /opt/pure-runtime
    exit 0
fi

for variable in \
    PURE_NSS_URL PURE_NSS_SHA512 PURE_NSS_SIZE \
    PURE_NSPR_URL PURE_NSPR_SHA512 PURE_NSPR_SIZE \
    PURE_SQLITE_URL PURE_SQLITE_SHA512 PURE_SQLITE_SIZE \
    PURE_EXPAT_URL PURE_EXPAT_SHA512 PURE_EXPAT_SIZE \
    PURE_XKBCOMMON_URL PURE_XKBCOMMON_SHA512 PURE_XKBCOMMON_SIZE \
    PURE_ZLIB_URL PURE_ZLIB_SHA512 PURE_ZLIB_SIZE \
    PURE_LIBFFI_URL PURE_LIBFFI_SHA512 PURE_LIBFFI_SIZE; do
    [ -n "${!variable:-}" ] || { echo "build-pure-runtime: missing $variable" >&2; exit 1; }
done

prefix=/opt/pure-runtime
build=/tmp/pure-runtime-build
rm -rf "$prefix" "$build"
mkdir -p "$prefix" "$build"

apk add --no-cache make=4.4.1-r4 meson=1.11.1-r0

export CC=/opt/chromium-llvm/bin/clang
export CXX=/opt/chromium-llvm/bin/clang++
export AR=/opt/chromium-llvm/bin/llvm-ar
export RANLIB=/opt/chromium-llvm/bin/llvm-ranlib

fetch() {
    local name=$1 url=$2 sha512=$3 size=$4
    curl -fsSL "$url" -o "$build/$name"
    test "$(stat -c %s "$build/$name")" = "$size"
    printf '%s  %s\n' "$sha512" "$build/$name" | sha512sum -c -
}

fetch nss.tar.gz "$PURE_NSS_URL" "$PURE_NSS_SHA512" "$PURE_NSS_SIZE"
fetch nspr.tar.gz "$PURE_NSPR_URL" "$PURE_NSPR_SHA512" "$PURE_NSPR_SIZE"
fetch sqlite.tar.gz "$PURE_SQLITE_URL" "$PURE_SQLITE_SHA512" "$PURE_SQLITE_SIZE"
fetch expat.tar.xz "$PURE_EXPAT_URL" "$PURE_EXPAT_SHA512" "$PURE_EXPAT_SIZE"
fetch xkbcommon.tar.gz "$PURE_XKBCOMMON_URL" "$PURE_XKBCOMMON_SHA512" "$PURE_XKBCOMMON_SIZE"
fetch zlib.tar.gz "$PURE_ZLIB_URL" "$PURE_ZLIB_SHA512" "$PURE_ZLIB_SIZE"
fetch libffi.tar.gz "$PURE_LIBFFI_URL" "$PURE_LIBFFI_SHA512" "$PURE_LIBFFI_SIZE"

tar -xzf "$build/nss.tar.gz" -C "$build"
tar -xzf "$build/nspr.tar.gz" -C "$build"
tar -xzf "$build/sqlite.tar.gz" -C "$build"
tar -xJf "$build/expat.tar.xz" -C "$build"
tar -xzf "$build/xkbcommon.tar.gz" -C "$build"
tar -xzf "$build/zlib.tar.gz" -C "$build"
tar -xzf "$build/libffi.tar.gz" -C "$build"

nss_src=$build/nss-3.124
nspr_src=$build/nspr-4.38.2
sqlite_src=$build/sqlite-autoconf-3530400
expat_src=$build/expat-2.10.0
xkbcommon_src=$build/libxkbcommon-xkbcommon-1.13.1
zlib_src=$build/zlib-1.4
libffi_src=$build/libffi-3.5.2
for directory in "$nss_src" "$nspr_src" "$sqlite_src" "$expat_src" "$xkbcommon_src" "$zlib_src" "$libffi_src"; do
    test -d "$directory"
done

sqlite_features="-DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_GEOPOLY -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_PERCENTILE -DSQLITE_ENABLE_PREUPDATE_HOOK -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_SESSION -DSQLITE_ENABLE_UNLOCK_NOTIFY -DSQLITE_USE_URI -DSQLITE_SOUNDEX -DSQLITE_MAX_VARIABLE_NUMBER=250000 -DSQLITE_THREADSAFE=1"
(
    cd "$sqlite_src"
    CFLAGS="-O2 -fPIC" ./configure \
        --prefix="$prefix" \
        --enable-threadsafe \
        --enable-session \
        --enable-static \
        --enable-fts3 \
        --enable-fts4 \
        --enable-fts5 \
        --soname=legacy
    make -j"$(nproc)" libsqlite3.so CFLAGS="-O2 -fPIC" OPT_FEATURE_FLAGS="$sqlite_features" LDFLAGS.zlib=
    mkdir -p "$prefix/lib" "$prefix/include"
    cp -L libsqlite3.so "$prefix/lib/libsqlite3.so.0"
    ln -sf libsqlite3.so.0 "$prefix/lib/libsqlite3.so"
    cp sqlite3.h sqlite3ext.h "$prefix/include/"
)

(
    cd "$expat_src"
    CFLAGS="-O2 -fPIC" ./configure --prefix="$prefix" --disable-static --without-docbook
    make -j"$(nproc)"
    make install
)

(
    cd "$zlib_src"
    CFLAGS="-O2 -fPIC" ./configure --prefix="$prefix" --shared
    make -j"$(nproc)"
    make install
)

(
    cd "$libffi_src"
    CFLAGS="-O2 -fPIC" ./configure --prefix="$prefix" --disable-shared --enable-static --with-pic
    make -j"$(nproc)"
    make install
    cp "$prefix/lib/libffi.a" "$prefix/lib/libffi_pic.a"
)

(
    cd "$xkbcommon_src"
    meson setup build \
        --prefix="$prefix" \
        --libdir=lib \
        -Denable-x11=false \
        -Denable-wayland=false \
        -Denable-docs=false \
        -Denable-tools=false \
        -Denable-xkbregistry=false
    ninja -C build
    meson install -C build --no-rebuild
)

rm -rf "$nss_src/nspr"
cp -a "$nspr_src/nspr" "$nss_src/nspr"
(
    cd "$nss_src/nss"
    make -j"$(nproc)" nss_build_all \
        USE_64=1 \
        BUILD_OPT=1 \
        OS_TARGET=Linux \
        NS_USE_GCC=1 \
        NSS_ENABLE_WERROR=0 \
        NSS_DISABLE_GTESTS=1 \
        NSS_DISABLE_NSPR_TESTS=1 \
        NSS_USE_SYSTEM_SQLITE=1 \
        SQLITE_INCLUDE_DIR="$prefix/include" \
        SQLITE_LIB_DIR="$prefix/lib"
)

nss_lib=$(find "$nss_src/dist" -mindepth 2 -maxdepth 2 -type d -name lib -print -quit)
test -n "$nss_lib"
for name in \
    libnss3.so libnssutil3.so libsmime3.so libssl3.so \
    libsoftokn3.so libfreebl3.so libfreeblpriv3.so libnssckbi.so \
    libnspr4.so libplc4.so libplds4.so; do
    cp -L "$nss_lib/$name" "$prefix/lib/$name"
done

rm -rf "$build"
