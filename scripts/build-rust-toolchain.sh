#!/usr/bin/env bash
# Build Chromium's pinned Rust toolchain natively for a musl host, GCC-free.
#
#   build-rust-toolchain.sh <chromium-src> <llvm-prefix> <out-dir>
#
# Runs in a linux container of the target architecture with network access.
# <llvm-prefix> is llvm-prebuilt-musl's compiler package with its -dev package
# extracted over it: rustc links that LLVM (one LLVM for clang, lld, and rustc),
# C and C++ are compiled by its clang against libc++, unwinding is LLVM's
# libunwind, and the bundled musl startup objects are the ones Rust ships for its
# musl target. The revisions come from <chromium-src>/tools/rust/update_rust.py
# and third_party/rust-toolchain/VERSION, so the result is the toolchain Chromium
# pins, rebuilt for this host. Architecture-independent parts (rust-src, vendored
# std crates, Crubit's GN support files) are taken from the toolchain Chromium
# downloaded, which is the same revision. Writes
# <out-dir>/rust-toolchain-<rust-rev>-<arch>-linux-musl.tar.xz and its .sha256.
set -euo pipefail

src=${1:?chromium src}
llvm=${2:?llvm prefix}
out=${3:?out dir}

die() { echo "build-rust-toolchain: $*" >&2; exit 1; }

arch=$(uname -m)
case "$arch" in
    aarch64|x86_64) ;;
    *) die "unsupported host $arch" ;;
esac
triple="${arch}-unknown-linux-musl"

rust_rev=$(sed -n "s/^RUST_REVISION = '\([0-9a-f]*\)'/\1/p" "${src}/tools/rust/update_rust.py")
crubit_rev=$(sed -n "s/^CRUBIT_REVISION = '\([0-9a-f]*\)'/\1/p" "${src}/tools/rust/update_rust.py")
bindgen_rev=$(sed -n 's/^bindgen: \([0-9a-f]*\)$/\1/p' "${src}/third_party/rust-toolchain/VERSION")
[ -n "$rust_rev" ] && [ -n "$crubit_rev" ] && [ -n "$bindgen_rev" ] || die "could not read the pinned revisions from ${src}"
chromium_toolchain="${src}/third_party/rust-toolchain"
[ -d "${chromium_toolchain}/lib/rustlib/src/rust/library" ] || die "${chromium_toolchain} has no rust-src"
[ -x "${llvm}/bin/llvm-config" ] || die "${llvm} lacks llvm-config: extract the -dev package over it"

work=${WORK_DIR:-/build}
mkdir -p "$work" "$out"
prefix="${work}/install"
musl_root="${work}/musl-root"
mkdir -p "${musl_root}"
ln -sfn "${llvm}/lib/musl" "${musl_root}/lib"

# The stage-0 compiler's musl std links its unwinder as -lgcc_s when it builds
# dynamic programs (bootstrap's build scripts). Resolve that name to the
# package's LLVM libunwind so no GNU unwinder is linked anywhere.
gcc_s_shim="${work}/gcc_s-shim"
mkdir -p "$gcc_s_shim"
printf 'INPUT ( %s )\n' "${llvm}/lib/libunwind.a" > "${gcc_s_shim}/libgcc_s.so"
export LIBRARY_PATH="${gcc_s_shim}"

echo "== rust ${rust_rev}, crubit ${crubit_rev}, bindgen ${bindgen_rev}, host ${triple}"

if [ ! -d "${work}/rust/.git" ]; then
    git init -q "${work}/rust"
    git -C "${work}/rust" remote add origin https://github.com/rust-lang/rust.git
fi
git -C "${work}/rust" fetch -q --depth 1 origin "$rust_rev"
git -C "${work}/rust" checkout -q --force FETCH_HEAD

cat > "${work}/rust/bootstrap.toml" <<TOML
change-id = "ignore"

[llvm]
download-ci-llvm = false
link-shared = false
static-libstdcpp = false
use-libcxx = true

[rust]
channel = "nightly"
download-rustc = false
llvm-libunwind = "system"
llvm-tools = false

[build]
description = "${rust_rev} musl native (chromium pin)"
build = "${triple}"
host = ["${triple}"]
target = ["${triple}"]
locked-deps = true
docs = false
profiler = false
extended = true
tools = ["cargo", "clippy", "rustc-dev", "rustdoc", "rustfmt", "src"]
cargo-native-static = true

[install]
prefix = "${prefix}"
sysconfdir = "etc"

[target.${triple}]
llvm-config = "${llvm}/bin/llvm-config"
cc = "${llvm}/bin/clang"
cxx = "${llvm}/bin/clang++"
ar = "${llvm}/bin/llvm-ar"
ranlib = "${llvm}/bin/llvm-ranlib"
linker = "${llvm}/bin/clang"
crt-static = false
musl-root = "${musl_root}"
TOML

rm -rf "$prefix"
(cd "${work}/rust" && python3 x.py install --stage 2)
[ -x "${prefix}/bin/rustc" ] || die "x.py did not install rustc"
"${prefix}/bin/rustc" -vV

export PATH="${prefix}/bin:${llvm}/bin:${PATH}"
export CC="${llvm}/bin/clang" CXX="${llvm}/bin/clang++" AR="${llvm}/bin/llvm-ar"
export CARGO_TARGET_$(echo "$triple" | tr a-z- A-Z_)_LINKER="${llvm}/bin/clang"
export RUSTC_BOOTSTRAP=1

if [ ! -d "${work}/crubit/.git" ]; then
    git init -q "${work}/crubit"
    git -C "${work}/crubit" remote add origin https://chromium.googlesource.com/external/github.com/google/crubit
fi
git -C "${work}/crubit" fetch -q --depth 1 origin "$crubit_rev"
git -C "${work}/crubit" checkout -q --force FETCH_HEAD
RUSTFLAGS="-Ctarget-feature=-crt-static -Clink-args=-Wl,-z,origin -Clink-args=-Wl,-rpath,\$ORIGIN/../lib -L ${prefix}/lib" \
    cargo build --release --locked -p cc_bindings_from_rs --bin cc_bindings_from_rs \
    --manifest-path "${work}/crubit/Cargo.toml" --target-dir "${work}/crubit-target"

if [ ! -d "${work}/bindgen/.git" ]; then
    git init -q "${work}/bindgen"
    git -C "${work}/bindgen" remote add origin https://github.com/rust-lang/rust-bindgen.git
fi
git -C "${work}/bindgen" fetch -q --depth 1 origin "$bindgen_rev"
git -C "${work}/bindgen" checkout -q --force FETCH_HEAD
RUSTFLAGS="-Ctarget-feature=-crt-static" \
    cargo build --release --locked -p bindgen-cli --bin bindgen \
    --manifest-path "${work}/bindgen/Cargo.toml" --target-dir "${work}/bindgen-target"

name="rust-toolchain-${rust_rev}-${arch}-linux-musl"
stage="${work}/${name}"
rm -rf "$stage"
mkdir -p "$stage"
cp -a "${prefix}/." "$stage/"
cp "${work}/crubit-target/release/cc_bindings_from_rs" "${stage}/bin/"
cp "${work}/bindgen-target/release/bindgen" "${stage}/bin/"
cp -a "${llvm}"/lib/libclang.so* "${stage}/lib/"
rm -rf "${stage}/lib/rustlib/src"
mkdir -p "${stage}/lib/rustlib"
cp -a "${chromium_toolchain}/lib/rustlib/src" "${stage}/lib/rustlib/src"
mkdir -p "${stage}/lib/third_party"
cp -a "${chromium_toolchain}/lib/third_party/crubit" "${stage}/lib/third_party/crubit"
cp "${chromium_toolchain}/VERSION" "${stage}/VERSION"

tarball="${out}/${name}.tar.xz"
tar -C "$work" --sort=name --owner=0 --group=0 --numeric-owner -cf - "$name" | xz -T0 -6 > "${tarball}.tmp"
mv "${tarball}.tmp" "$tarball"
(cd "$out" && sha256sum "${name}.tar.xz" > "${name}.tar.xz.sha256")
ls -l "$tarball"
cat "${tarball}.sha256"
