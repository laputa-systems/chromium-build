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
# downloaded, which is the same revision. The archive also carries the host
# clang-format Crubit runs on generated headers, and std's profiler_builtins links
# compiler-rt's profile runtime; both come from the LLVM release the package was
# built from. Writes
# <out-dir>/rust-toolchain-<rust-rev>-<arch>-linux-musl.tar.xz and its .sha256.
set -euo pipefail

LLVM_SOURCE_VERSION=23.1.5
LLVM_SOURCE_SHA256=fb50b1eac93ddeb3aa8dab4cfe324dff74f4a9c2bdee6160c9e36188f8d06a6e

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

llvm_version=$("${llvm}/bin/llvm-config" --version)
[ "$llvm_version" = "$LLVM_SOURCE_VERSION" ] || die "${llvm} is LLVM ${llvm_version}, the pinned compiler-rt source is ${LLVM_SOURCE_VERSION}"

echo "== rust ${rust_rev}, crubit ${crubit_rev}, bindgen ${bindgen_rev}, host ${triple}, LLVM ${llvm_version}"

llvm_source="${work}/llvm-project-${llvm_version}.src.tar.xz"
if [ ! -f "$llvm_source" ]; then
    curl -fsSL "https://github.com/llvm/llvm-project/releases/download/llvmorg-${llvm_version}/llvm-project-${llvm_version}.src.tar.xz" -o "${llvm_source}.tmp"
    mv "${llvm_source}.tmp" "$llvm_source"
fi
echo "${LLVM_SOURCE_SHA256}  ${llvm_source}" | sha256sum -c -
rm -rf "${work}/llvm-project" "${work}/compiler-rt-build"
mkdir -p "${work}/llvm-project"
rm -rf "${work}/clang-format-build"
tar -xJf "$llvm_source" -C "${work}/llvm-project" --strip-components=1 \
    "llvm-project-${llvm_version}.src/compiler-rt" "llvm-project-${llvm_version}.src/cmake" \
    "llvm-project-${llvm_version}.src/llvm" "llvm-project-${llvm_version}.src/clang" \
    "llvm-project-${llvm_version}.src/third-party" "llvm-project-${llvm_version}.src/libc"
cmake -G "Unix Makefiles" -S "${work}/llvm-project/compiler-rt" -B "${work}/compiler-rt-build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="${llvm}/bin/clang" \
    -DCMAKE_CXX_COMPILER="${llvm}/bin/clang++" \
    -DCMAKE_AR="${llvm}/bin/llvm-ar" \
    -DCMAKE_RANLIB="${llvm}/bin/llvm-ranlib" \
    -DCMAKE_C_COMPILER_TARGET="$triple" \
    -DCMAKE_CXX_COMPILER_TARGET="$triple" \
    -DLLVM_CONFIG_PATH="${llvm}/bin/llvm-config" \
    -DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON \
    -DCOMPILER_RT_BUILD_PROFILE=ON \
    -DCOMPILER_RT_BUILD_BUILTINS=OFF \
    -DCOMPILER_RT_BUILD_SANITIZERS=OFF \
    -DCOMPILER_RT_BUILD_XRAY=OFF \
    -DCOMPILER_RT_BUILD_LIBFUZZER=OFF \
    -DCOMPILER_RT_BUILD_MEMPROF=OFF \
    -DCOMPILER_RT_BUILD_ORC=OFF \
    -DCOMPILER_RT_BUILD_CTX_PROFILE=OFF \
    -DCOMPILER_RT_BUILD_GWP_ASAN=OFF
make -C "${work}/compiler-rt-build" -j"$(nproc)" clang_rt.profile-"${arch}"
profiler_runtime=$(find "${work}/compiler-rt-build" -name "libclang_rt.profile*.a" | head -n 1)
[ -n "$profiler_runtime" ] || die "compiler-rt did not produce the profile runtime"

cmake -G "Unix Makefiles" -S "${work}/llvm-project/llvm" -B "${work}/clang-format-build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="${llvm}/bin/clang" \
    -DCMAKE_CXX_COMPILER="${llvm}/bin/clang++" \
    -DCMAKE_AR="${llvm}/bin/llvm-ar" \
    -DCMAKE_RANLIB="${llvm}/bin/llvm-ranlib" \
    -DLLVM_ENABLE_PROJECTS=clang \
    -DLLVM_TARGETS_TO_BUILD=host \
    -DLLVM_ENABLE_LIBCXX=ON \
    -DLLVM_ENABLE_ZLIB=OFF \
    -DLLVM_ENABLE_ZSTD=OFF \
    -DLLVM_ENABLE_LIBXML2=OFF \
    -DLLVM_ENABLE_LIBEDIT=OFF \
    -DLLVM_ENABLE_TERMINFO=OFF \
    -DLLVM_INCLUDE_TESTS=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DCLANG_INCLUDE_TESTS=OFF
make -C "${work}/clang-format-build" -j"$(nproc)" clang-format
clang_format="${work}/clang-format-build/bin/clang-format"
"$clang_format" --version | grep -qF "clang-format version ${llvm_version}" || die "clang-format is not LLVM ${llvm_version}"

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
profiler = "${profiler_runtime}"
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
cp "$clang_format" "${stage}/bin/clang-format"
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
