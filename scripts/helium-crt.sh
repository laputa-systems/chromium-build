#!/bin/sh
set -eu

llvm=${1:?LLVM root required}
for crt in Scrt1.o crt1.o crti.o crtn.o rcrt1.o; do
    test -f "$llvm/lib/musl/$crt"
done
test ! -e "$llvm/bin/aarch64-alpine-linux-musl.cfg"
printf '%s\n' '-B<CFGDIR>/../lib/musl' > "$llvm/bin/aarch64-alpine-linux-musl.cfg"
