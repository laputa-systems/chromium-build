# syntax=docker/dockerfile:1.7

ARG GLIBC_IMAGE=debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
ARG BASE_IMAGE=alpine@sha256:d56c381f961d307a21b3ca004cf1e3910f106644aefb1f43e654c8a56c4fd395
FROM ${GLIBC_IMAGE} AS compiler-host-runtime
FROM --platform=linux/amd64 ${GLIBC_IMAGE} AS compiler-host-runtime-x64
FROM ${BASE_IMAGE}

ARG TARGET_ARCH=amd64
ARG PRODUCT=chromium
ARG PACKAGE_WORLD=
ARG PACKAGE_LOCK=config/packages.amd64.lock
ARG INPUTS_LOCK=config/inputs.lock
ARG SYSTEM_LIBRARY_POLICY=config/system-library-preflight.schema.json
ARG LLVM_URL=https://commondatastorage.googleapis.com/chromium-browser-clang/Linux_x64/clang-llvmorg-24-init-3796-g20e97c4b-2.tar.xz
ARG LLVM_SHA256=86d53b33007a35695645e3867589a20f6f8aeedd146331e1a2ec8fd371e05ea2
ARG LLVM_SIZE=59131228
ARG LLVM_REVISION=llvmorg-24-init-3796-g20e97c4b-2
ARG GLIBC_TRIPLET=x86_64-linux-gnu
ARG LD_LINUX=ld-linux-x86-64.so.2
ARG CCACHE_MAXSIZE=10G

COPY --from=compiler-host-runtime-x64 /usr/lib/x86_64-linux-gnu/ /usr/lib/x86_64-linux-gnu/
RUN mkdir -p /lib64 && \
    ln -s /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /lib64/ld-linux-x86-64.so.2

RUN set -eux; \
    if [ "$PRODUCT" = helium ]; then \
        test -n "$PACKAGE_WORLD"; \
        printf '%s\n' "$PACKAGE_WORLD" | tr ' ' '\n' | grep -Eq '^[A-Za-z0-9+._-]+=[A-Za-z0-9._-]+$' || \
            { echo "invalid Helium package token" >&2; exit 1; }; \
        apk add --no-cache $PACKAGE_WORLD; \
    else \
        test "$PRODUCT" = chromium; \
        apk add --no-cache \
            bash=5.3.9-r1 \
            bison=3.8.2-r3 \
            bsd-compat-headers=0.7.2-r6 \
            ca-certificates=20261020-r0 \
            cargo=1.96.1-r0 \
            ccache=4.13.6-r0 \
            clang22-libclang=22.1.3-r2 \
            curl=8.23.0-r0 \
            expat-dev=2.10.0-r0 \
            ffmpeg-dev=8.1.3-r0 \
            file=5.47-r2 \
            findutils=4.10.0-r1 \
            flex=2.6.4-r8 \
            font-noto=2026.11.01-r0 \
            gn=0_git20260924-r0 \
            go=1.26.10-r0 \
            gperf=3.3-r0 \
            libdrm-dev=2.4.134-r0 \
            linux-headers=7.0.0-r1 \
            mesa-dev=26.1.6-r0 \
            musl-dev=1.2.6-r3 \
            ninja-build=1.13.2-r1 \
            nodejs=24.22.1-r1 \
            nspr-dev=4.38.2-r0 \
            nss-dev=3.124-r0 \
            patch=2.8.1-r0 \
            perl=5.42.3-r0 \
            pkgconf=2.5.1-r0 \
            python3=3.14.10-r1 \
            rust=1.96.1-r0 \
            rust-bindgen=0.72.1-r1 \
            rustfmt=1.96.1-r0 \
            tar=1.35-r5 \
            util-linux-dev=2.42.3-r1 \
            xz=5.8.4-r0 \
            zstd=1.5.7-r2; \
    fi
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
        else \
            linux_arch=x86_64; builtins_arch=x86_64; clang_major=23; \
        fi; \
        test "$(cat /opt/chromium-llvm/cr_build_revision)" = "$LLVM_REVISION"; \
        mkdir -p "/opt/chromium-llvm/lib/clang/${clang_major}/lib/${linux_arch}-unknown-linux-musl" "/opt/chromium-llvm/lib/clang/${clang_major}/lib/${linux_arch}-unknown-linux-gnu"; \
        ln -sf "../linux/libclang_rt.builtins-${builtins_arch}.a" "/opt/chromium-llvm/lib/clang/${clang_major}/lib/${linux_arch}-unknown-linux-musl/libclang_rt.builtins.a"; \
        ln -sf "../linux/libclang_rt.builtins-${builtins_arch}.a" "/opt/chromium-llvm/lib/clang/${clang_major}/lib/${linux_arch}-unknown-linux-gnu/libclang_rt.builtins.a"; \
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
    mkdir -p /opt/chromium-glibc/lib; \
    if true; then \
        /opt/chromium-llvm/bin/clang --target=x86_64-unknown-linux-gnu \
            -c /opt/chromium-glibc-src/crt1.S -o /opt/chromium-glibc/lib/Scrt1.o; \
        cp /opt/chromium-glibc/lib/Scrt1.o /opt/chromium-glibc/lib/crt1.o; \
        /opt/chromium-llvm/bin/clang --target=x86_64-unknown-linux-gnu \
            -c /opt/chromium-glibc-src/crti.S -o /opt/chromium-glibc/lib/crti.o; \
        /opt/chromium-llvm/bin/clang --target=x86_64-unknown-linux-gnu \
            -c /opt/chromium-glibc-src/crtn.S -o /opt/chromium-glibc/lib/crtn.o; \
        cp /opt/chromium-glibc-src/libc.so /opt/chromium-glibc/lib/libc.so; \
        ln -s "/usr/lib/${GLIBC_TRIPLET}/libc.so.6" /opt/chromium-glibc/lib/libc.so.6; \
        ln -s "/usr/lib/${GLIBC_TRIPLET}/${LD_LINUX}" "/opt/chromium-glibc/lib/${LD_LINUX}"; \
        for pair in \
            libm.so.6:libm.so \
            libdl.so.2:libdl.so \
            libpthread.so.0:libpthread.so \
            librt.so.1:librt.so \
            libutil.so.1:libutil.so \
            libgcc_s.so.1:libgcc_s.so; do \
            source=${pair%%:*}; link=${pair##*:}; \
            ln -s "/usr/lib/${GLIBC_TRIPLET}/$source" "/opt/chromium-glibc/lib/$link"; \
        done; \
        if [ "$PRODUCT" = helium ]; then \
            for name in crtbegin crtend; do \
                ln -s "/opt/chromium-llvm/lib/clang/23/lib/linux/clang_rt.$name.o" \
                    "/opt/chromium-glibc/lib/$name.o"; \
                ln -s "/opt/chromium-llvm/lib/clang/23/lib/linux/clang_rt.$name.o" \
                    "/opt/chromium-glibc/lib/${name}S.o"; \
            done; \
        else \
            for name in crtbegin crtend; do \
                ln -s "/opt/chromium-llvm/lib/clang/24/lib/x86_64-unknown-linux-gnu/clang_rt.$name.o" \
                    "/opt/chromium-glibc/lib/$name.o"; \
                ln -s "/opt/chromium-llvm/lib/clang/24/lib/x86_64-unknown-linux-gnu/clang_rt.$name.o" \
                    "/opt/chromium-glibc/lib/${name}S.o"; \
            done; \
        fi; \
    fi

RUN addgroup -S chromium && adduser -S -D -H -G chromium chromium

COPY config /opt/chromium-build/config
COPY scripts /opt/chromium-build/scripts
COPY tests/probes /opt/chromium-build/tests/probes
COPY tests/fixtures /opt/chromium-build/tests/fixtures
COPY tests/test_browser_test_support.py /opt/chromium-build/tests/test_browser_test_support.py
COPY tests/test_fetch_inputs.py /opt/chromium-build/tests/test_fetch_inputs.py
COPY tests/test_helium_runtime.py /opt/chromium-build/tests/test_helium_runtime.py

RUN set -eux; \
    mkdir -p /opt/chromium-build-metadata; \
    apk info -v | sort > "/opt/chromium-build-metadata/packages.$TARGET_ARCH.txt"

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

ENV PATH="/opt/chromium-llvm/bin:$PATH" \
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
    RUST_HOST_LINKER=/opt/chromium-build/scripts/rust-host-linker \
    CFLAGS=-Wno-unknown-warning-option \
    CXXFLAGS=-Wno-unknown-warning-option \
    NETWORK_MODE=none

WORKDIR /work
