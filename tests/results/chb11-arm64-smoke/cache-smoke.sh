#!/bin/sh
set -eu
cd /work/chb11-cache-smoke
pass=$1
ccache --print-stats > "$pass.before.tsv"
rm -f smoke-c.o smoke-cpp.o smoke-c smoke-cpp
ccache "$CC" -O2 -c smoke.c -o smoke-c.o
ccache "$CXX" -O2 -c smoke.cc -o smoke-cpp.o
"$CC" smoke-c.o -o smoke-c
"$CXX" smoke-cpp.o -o smoke-cpp
test "$(./smoke-c)" = 42
test "$(./smoke-cpp)" = 42
ccache --print-stats > "$pass.after.tsv"
sha256sum smoke-c.o smoke-cpp.o smoke-c smoke-cpp > "$pass.sha256"
ccache --show-config > "$pass.config.txt"
printf 'pass=%s outputs=42,42\n' "$pass"
