# syntax=docker/dockerfile:1.7

ARG GLIBC_IMAGE=debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
ARG BASE_IMAGE=alpine@sha256:d56c381f961d307a21b3ca004cf1e3910f106644aefb1f43e654c8a56c4fd395
ARG TARGET_ARCH=amd64
FROM scratch AS locked-apks
FROM ${GLIBC_IMAGE} AS host-runtime-amd64
RUN mkdir -p /rootfs/usr/lib /rootfs/lib64 \
    && cp -a /usr/lib/x86_64-linux-gnu /rootfs/usr/lib/ \
    && ln -s /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /rootfs/lib64/ld-linux-x86-64.so.2
FROM ${BASE_IMAGE} AS host-runtime-arm64
RUN mkdir /rootfs
FROM host-runtime-${TARGET_ARCH} AS host-runtime
FROM ${BASE_IMAGE} AS builder-base

ARG TARGET_ARCH
ARG PRODUCT=chromium
ARG PACKAGE_LOCK=config/packages.amd64.lock
ARG INPUTS_LOCK=config/inputs.lock
ARG SYSTEM_LIBRARY_POLICY=config/system-library-preflight.schema.json
ARG LLVM_URL=https://commondatastorage.googleapis.com/chromium-browser-clang/Linux_x64/clang-llvmorg-24-init-3796-g20e97c4b-2.tar.xz
ARG LLVM_SHA256=86d53b33007a35695645e3867589a20f6f8aeedd146331e1a2ec8fd371e05ea2
ARG LLVM_SIZE=59131228
ARG LLVM_REVISION=llvmorg-24-init-3796-g20e97c4b-2
ARG RUST_HOST_LINKER=
ARG CCACHE_MAXSIZE=10G

COPY --from=host-runtime /rootfs/ /

RUN --network=none --mount=type=bind,from=locked-apks,target=/locked-apks set -eux; \
    set -- /locked-apks/*.apk; \
    if [ ! -f "$1" ]; then \
        echo 'Missing verified Alpine packages. Run ./chromium-build image.' >&2; \
        exit 1; \
    fi; \
    apk add --no-cache --repositories-file /dev/null "$@"
COPY scripts/helium-crt.sh /opt/chromium-build/scripts/helium-crt.sh
RUN set -eux; \
    if [ "$PRODUCT" = helium ]; then \
        mkdir -p /opt/chromium-llvm; \
        curl -fsSL "$LLVM_URL" -o /tmp/llvm.tar.xz; \
        test "$(stat -c %s /tmp/llvm.tar.xz)" = "$LLVM_SIZE"; \
        echo "$LLVM_SHA256  /tmp/llvm.tar.xz" | sha256sum -c -; \
        tar -xJf /tmp/llvm.tar.xz -C /opt/chromium-llvm --strip-components=1; \
        rm -f /tmp/llvm.tar.xz; \
        printf '%s\n' "$LLVM_REVISION" > /opt/chromium-llvm/cr_build_revision; \
        if [ "$TARGET_ARCH" = "arm64" ]; then \
            linux_arch=aarch64; builtins_arch=aarch64; clang_major=23; \
            sh /opt/chromium-build/scripts/helium-crt.sh /opt/chromium-llvm; \
        else \
            linux_arch=x86_64; builtins_arch=x86_64; clang_major=23; \
        fi; \
        test "$(cat /opt/chromium-llvm/cr_build_revision)" = "$LLVM_REVISION"; \
        mkdir -p "/opt/chromium-llvm/lib/clang/${clang_major}/lib/${linux_arch}-unknown-linux-musl"; \
        ln -sf "../linux/libclang_rt.builtins-${builtins_arch}.a" "/opt/chromium-llvm/lib/clang/${clang_major}/lib/${linux_arch}-unknown-linux-musl/libclang_rt.builtins.a"; \
        if [ "$TARGET_ARCH" = amd64 ]; then \
            mkdir -p "/opt/chromium-llvm/lib/clang/${clang_major}/lib/x86_64-unknown-linux-gnu"; \
            ln -sf "../linux/libclang_rt.builtins-${builtins_arch}.a" "/opt/chromium-llvm/lib/clang/${clang_major}/lib/x86_64-unknown-linux-gnu/libclang_rt.builtins.a"; \
        fi; \
        test "$(/opt/chromium-llvm/bin/clang -dumpmachine)" = "${linux_arch}-unknown-linux-musl"; \
        /opt/chromium-llvm/bin/clang --version | grep -F 23.1.5; \
        ln -sf /usr/lib/ninja-build/bin/ninja /usr/local/bin/ninja; \
        test "$(/usr/local/bin/ninja --version)" = 1.13.2; \
    else \
        test "$TARGET_ARCH" = amd64; \
        mkdir -p /opt/chromium-llvm; \
        curl -fsSL "$LLVM_URL" -o /tmp/llvm.tar.xz; \
        test "$(stat -c %s /tmp/llvm.tar.xz)" = "$LLVM_SIZE"; \
        echo "$LLVM_SHA256  /tmp/llvm.tar.xz" | sha256sum -c -; \
        tar -xJf /tmp/llvm.tar.xz -C /opt/chromium-llvm; \
        rm -f /tmp/llvm.tar.xz; \
        test "$(cat /opt/chromium-llvm/cr_build_revision)" = llvmorg-24-init-3796-g20e97c4b-2; \
        ln -s x86_64-unknown-linux-gnu /opt/chromium-llvm/lib/clang/24/lib/x86_64-unknown-linux-musl; \
        ln -s ../x86_64-unknown-linux-gnu/libclang_rt.builtins.a /opt/chromium-llvm/lib/clang/24/lib/linux/libclang_rt.builtins-x86_64.a; \
        ln -s llvm-ar /opt/chromium-llvm/bin/llvm-ranlib; \
        printf '%s\n' '--target=x86_64-unknown-linux-musl' '-rtlib=compiler-rt' '-fuse-ld=lld' > /opt/chromium-llvm/bin/clang.cfg; \
        cp /opt/chromium-llvm/bin/clang.cfg /opt/chromium-llvm/bin/clang++.cfg; \
        test "$(/opt/chromium-llvm/bin/clang -dumpmachine)" = x86_64-unknown-linux-musl; \
        /opt/chromium-llvm/bin/clang --version | grep -F 24.0.0git; \
        ln -s /usr/lib/ninja-build/bin/ninja /usr/local/bin/ninja; \
        test "$(/usr/local/bin/ninja --version)" = 1.13.2; \
    fi

COPY config/glibc /opt/chromium-glibc-src
RUN set -eux; \
    if [ "$TARGET_ARCH" = amd64 ]; then \
        mkdir -p /opt/chromium-glibc/lib; \
        /opt/chromium-llvm/bin/clang --target=x86_64-unknown-linux-gnu \
            -c /opt/chromium-glibc-src/crt1.S -o /opt/chromium-glibc/lib/Scrt1.o; \
        cp /opt/chromium-glibc/lib/Scrt1.o /opt/chromium-glibc/lib/crt1.o; \
        /opt/chromium-llvm/bin/clang --target=x86_64-unknown-linux-gnu \
            -c /opt/chromium-glibc-src/crti.S -o /opt/chromium-glibc/lib/crti.o; \
        /opt/chromium-llvm/bin/clang --target=x86_64-unknown-linux-gnu \
            -c /opt/chromium-glibc-src/crtn.S -o /opt/chromium-glibc/lib/crtn.o; \
        cp /opt/chromium-glibc-src/libc.so /opt/chromium-glibc/lib/libc.so; \
        ln -s /usr/lib/x86_64-linux-gnu/libc.so.6 /opt/chromium-glibc/lib/libc.so.6; \
        ln -s /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /opt/chromium-glibc/lib/ld-linux-x86-64.so.2; \
        for pair in \
            libm.so.6:libm.so \
            libdl.so.2:libdl.so \
            libpthread.so.0:libpthread.so \
            librt.so.1:librt.so \
            libutil.so.1:libutil.so \
            libgcc_s.so.1:libgcc_s.so; do \
            source=${pair%%:*}; link=${pair##*:}; \
            ln -s "/usr/lib/x86_64-linux-gnu/$source" "/opt/chromium-glibc/lib/$link"; \
        done; \
        if [ "$PRODUCT" = helium ]; then \
            crt_dir=/opt/chromium-llvm/lib/clang/23/lib/linux; \
        else \
            crt_dir=/opt/chromium-llvm/lib/clang/24/lib/x86_64-unknown-linux-gnu; \
        fi; \
        for name in crtbegin crtend; do \
            ln -s "$crt_dir/clang_rt.$name.o" "/opt/chromium-glibc/lib/$name.o"; \
            ln -s "$crt_dir/clang_rt.$name.o" "/opt/chromium-glibc/lib/${name}S.o"; \
        done; \
    fi

RUN addgroup -S chromium && adduser -S -D -H -G chromium chromium

COPY config /opt/chromium-build/config
COPY scripts /opt/chromium-build/scripts
COPY tests/probes /opt/chromium-build/tests/probes
COPY tests/fixtures /opt/chromium-build/tests/fixtures
COPY tests/test_browser_test_support.py /opt/chromium-build/tests/test_browser_test_support.py
COPY tests/test_fetch_inputs.py /opt/chromium-build/tests/test_fetch_inputs.py
COPY tests/test_helium_runtime.py /opt/chromium-build/tests/test_helium_runtime.py

ENV PATH="/opt/chromium-llvm/bin:$PATH" \
    LIBRARY_PATH=/opt/chromium-llvm/lib \
    CC=/opt/chromium-llvm/bin/clang \
    CXX=/opt/chromium-llvm/bin/clang++ \
    AR=/opt/chromium-llvm/bin/llvm-ar \
    RANLIB=/opt/chromium-llvm/bin/llvm-ranlib \
    NM=/opt/chromium-llvm/bin/llvm-nm \
    STRIP=/opt/chromium-llvm/bin/llvm-strip \
    OBJCOPY=/opt/chromium-llvm/bin/llvm-objcopy \
    BUILD_CC=/opt/chromium-llvm/bin/clang \
    BUILD_CXX=/opt/chromium-llvm/bin/clang++ \
    BUILD_AR=/opt/chromium-llvm/bin/llvm-ar \
    BUILD_RANLIB=/opt/chromium-llvm/bin/llvm-ranlib \
    BUILD_NM=/opt/chromium-llvm/bin/llvm-nm \
    BUILD_STRIP=/opt/chromium-llvm/bin/llvm-strip \
    BUILD_OBJCOPY=/opt/chromium-llvm/bin/llvm-objcopy \
    CCACHE_DIR=/ccache \
    CCACHE_TEMPDIR=/ccache/tmp \
    CCACHE_COMPILERCHECK=content \
    CCACHE_COMPRESS=true \
    CCACHE_COMPRESSLEVEL=1 \
    CCACHE_MAXSIZE=${CCACHE_MAXSIZE} \
    CCACHE_NAMESPACE=${TARGET_ARCH}-${LLVM_SHA256}-compiler-command-v2 \
    RUST_HOST_LINKER=${RUST_HOST_LINKER} \
    CFLAGS=-Wno-unknown-warning-option \
    CXXFLAGS=-Wno-unknown-warning-option \
    NETWORK_MODE=none

WORKDIR /work

FROM builder-base AS pure-runtime
ARG TARGET_ARCH
ARG PRODUCT
ARG PURE_RUNTIME_JOBS=
ARG PURE_NSS_URL=
ARG PURE_NSS_SHA512=
ARG PURE_NSS_SIZE=
ARG PURE_NSPR_URL=
ARG PURE_NSPR_SHA512=
ARG PURE_NSPR_SIZE=
ARG PURE_SQLITE_URL=
ARG PURE_SQLITE_SHA512=
ARG PURE_SQLITE_SIZE=
ARG PURE_EXPAT_URL=
ARG PURE_EXPAT_SHA512=
ARG PURE_EXPAT_SIZE=
ARG PURE_XKBCOMMON_URL=
ARG PURE_XKBCOMMON_SHA512=
ARG PURE_XKBCOMMON_SIZE=
ARG PURE_ZLIB_URL=
ARG PURE_ZLIB_SHA512=
ARG PURE_ZLIB_SIZE=
ARG PURE_LIBFFI_URL=
ARG PURE_LIBFFI_SHA512=
ARG PURE_LIBFFI_SIZE=
RUN /opt/chromium-build/scripts/build-pure-runtime.sh

FROM builder-base
ARG TARGET_ARCH
ARG PRODUCT
ARG INPUTS_LOCK=config/inputs.lock
ARG PACKAGE_LOCK=config/packages.amd64.lock
ARG SYSTEM_LIBRARY_POLICY=config/system-library-preflight.schema.json
COPY --from=pure-runtime /opt/pure-runtime /opt/pure-runtime
RUN set -eux; \
    if [ "$PRODUCT" = "helium" ] && [ "$TARGET_ARCH" = "arm64" ]; then \
        for library in /opt/pure-runtime/lib/*.so*; do \
            rm -f "/usr/lib/$(basename -- "$library")"; \
        done; \
        cp -a /opt/pure-runtime/lib/*.so* /usr/lib/; \
        cp -a /opt/pure-runtime/lib/libffi_pic.a /usr/lib/; \
    fi; \
    rm -rf /opt/pure-runtime
RUN set -eux; \
    mkdir -p /opt/chromium-build-metadata; \
    apk info -v | sort > "/opt/chromium-build-metadata/packages.$TARGET_ARCH.txt"; \
    python3 /opt/chromium-build/scripts/lockcheck.py packages \
        "/opt/chromium-build/$PACKAGE_LOCK" "/opt/chromium-build-metadata/packages.$TARGET_ARCH.txt"
RUN python3 /opt/chromium-build/scripts/write-environment-metadata.py "$TARGET_ARCH" \
    --inputs-lock "/opt/chromium-build/$INPUTS_LOCK" \
    --package-lock "/opt/chromium-build/$PACKAGE_LOCK" \
    --policy "/opt/chromium-build/$SYSTEM_LIBRARY_POLICY" \
    --output /opt/chromium-build-metadata/environment.json
RUN CHROMIUM_ARCH="$TARGET_ARCH" CHROMIUM_PRODUCT="$PRODUCT" \
    SYSTEM_LIBRARY_POLICY="/opt/chromium-build/$SYSTEM_LIBRARY_POLICY" \
    CHROMIUM_METADATA_ROOT=/opt/chromium-build-metadata \
    python3 /opt/chromium-build/scripts/preflight-system-libraries.py \
    --lock "/opt/chromium-build/$PACKAGE_LOCK" \
    --policy "/opt/chromium-build/$SYSTEM_LIBRARY_POLICY"

WORKDIR /work
