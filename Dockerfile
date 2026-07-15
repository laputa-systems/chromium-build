# syntax=docker/dockerfile:1.7

ARG BASE_IMAGE=alpine@sha256:e7a1a92a5bfeee40966aea60f0796b0e7917cc35591542701834f03a68fa3d18
FROM ${BASE_IMAGE}

ARG TARGET_ARCH=arm64
ARG LLVM_URL=https://github.com/laputa-systems/llvm-prebuilt-musl/releases/download/llvm-musl-22.1.8/clang+llvm-22.1.8-aarch64-linux-musl.tar.xz
ARG LLVM_SHA256=675f9cf871313a5672a63882d4d30dd6dd55df0aa9caee70970542eb03a23da3
ARG LLVM_SIZE=73611888

RUN apk add --no-cache \
    bash=5.3.9-r1 \
    bison=3.8.2-r3 \
    ca-certificates=20260611-r0 \
    cargo=1.96.0-r0 \
    ccache=4.13.6-r0 \
    curl=8.21.0-r0 \
    ffmpeg-dev=8.1.2-r0 \
    file=5.47-r2 \
    findutils=4.10.0-r1 \
    flex=2.6.4-r8 \
    gn=0_git20260401-r2 \
    go=1.26.3-r0 \
    gperf=3.3-r0 \
    libdrm-dev=2.4.134-r0 \
    linux-headers=7.0.0-r1 \
    mesa-dev=26.1.1-r1 \
    musl-dev=1.2.6-r2 \
    nodejs=24.17.0-r0 \
    nspr-dev=4.38.2-r0 \
    nss-dev=3.124-r0 \
    patch=2.8-r0 \
    perl=5.42.2-r0 \
    pkgconf=2.5.1-r0 \
    python3=3.14.5-r0 \
    rust=1.96.0-r0 \
    rust-bindgen=0.72.1-r1 \
    rustfmt=1.96.0-r0 \
    samurai=1.2-r8 \
    tar=1.35-r5 \
    xz=5.8.3-r0 \
    zstd=1.5.7-r2

RUN set -eux; \
    mkdir -p /opt/llvm-musl; \
    curl -fsSL "$LLVM_URL" -o /tmp/llvm.tar.xz; \
    test "$(stat -c %s /tmp/llvm.tar.xz)" = "$LLVM_SIZE"; \
    echo "$LLVM_SHA256  /tmp/llvm.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/llvm.tar.xz -C /opt/llvm-musl --strip-components=1; \
    rm -f /tmp/llvm.tar.xz; \
    expected_triple=$([ "$TARGET_ARCH" = arm64 ] && echo aarch64-unknown-linux-musl || echo x86_64-unknown-linux-musl); \
    test "$(/opt/llvm-musl/bin/clang -dumpmachine)" = "$expected_triple"; \
    /opt/llvm-musl/bin/clang --version | grep -F 22.1.8; \
    test -f /opt/llvm-musl/lib/libc++.a; \
    test -f /opt/llvm-musl/lib/libc++abi.a; \
    test -f /opt/llvm-musl/lib/libunwind.a

COPY config /opt/chromium-build/config
COPY scripts /opt/chromium-build/scripts
COPY tests/probes /opt/chromium-build/tests/probes

RUN set -eux; \
    mkdir -p /opt/chromium-build-metadata; \
    apk info -v | sort > "/opt/chromium-build-metadata/packages.$TARGET_ARCH.txt"

RUN python3 /opt/chromium-build/scripts/write-environment-metadata.py "$TARGET_ARCH" \
    --inputs-lock /opt/chromium-build/config/inputs.lock \
    --package-lock "/opt/chromium-build/config/packages.$TARGET_ARCH.lock" \
    --policy /opt/chromium-build/config/system-library-preflight.schema.json \
    --output /opt/chromium-build-metadata/environment.json

RUN CHROMIUM_ARCH="$TARGET_ARCH" CHROMIUM_METADATA_ROOT=/opt/chromium-build-metadata \
    /opt/chromium-build/scripts/preflight-system-libraries.sh

ENV PATH="/opt/llvm-musl/bin:$PATH" \
    CC=/opt/llvm-musl/bin/clang \
    CXX=/opt/llvm-musl/bin/clang++ \
    AR=/opt/llvm-musl/bin/llvm-ar \
    RANLIB=/opt/llvm-musl/bin/llvm-ranlib \
    NM=/opt/llvm-musl/bin/llvm-nm \
    STRIP=/opt/llvm-musl/bin/llvm-strip \
    OBJCOPY=/opt/llvm-musl/bin/llvm-objcopy \
    BUILD_CC=/opt/llvm-musl/bin/clang \
    BUILD_CXX=/opt/llvm-musl/bin/clang++ \
    BUILD_AR=/opt/llvm-musl/bin/llvm-ar \
    BUILD_RANLIB=/opt/llvm-musl/bin/llvm-ranlib \
    BUILD_NM=/opt/llvm-musl/bin/llvm-nm \
    BUILD_STRIP=/opt/llvm-musl/bin/llvm-strip \
    BUILD_OBJCOPY=/opt/llvm-musl/bin/llvm-objcopy \
    CCACHE_DIR=/ccache \
    CCACHE_TEMPDIR=/ccache/tmp \
    CCACHE_COMPILERCHECK=content \
    CCACHE_COMPRESS=true \
    CCACHE_COMPRESSLEVEL=1 \
    CCACHE_MAXSIZE=10G \
    CCACHE_CC=/opt/llvm-musl/bin/clang \
    CCACHE_NAMESPACE=${TARGET_ARCH}-${LLVM_SHA256} \
    NETWORK_MODE=none

WORKDIR /work
