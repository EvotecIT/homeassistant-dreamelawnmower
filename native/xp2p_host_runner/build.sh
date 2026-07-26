#!/usr/bin/env bash
set -euo pipefail

: "${AARCH64_GCC:?Set AARCH64_GCC to an aarch64 cross compiler}"
: "${AARCH64_LD:?Set AARCH64_LD to an aarch64 linker}"
: "${AARCH64_STRIP:?Set AARCH64_STRIP to an aarch64 strip tool}"
: "${AOSP_RUNTIME_ROOT:?Set AOSP_RUNTIME_ROOT to an extracted arm64 runtime APEX}"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-${SOURCE_DIR}/out}"
mkdir -p "${OUTPUT_DIR}"

"${AARCH64_GCC}" \
  -fPIE -fno-stack-protector -fno-builtin -ffreestanding -nostdlib \
  -Wall -Wextra -Werror \
  -c "${SOURCE_DIR}/dreame_xp2p_host_runner.c" \
  -o "${OUTPUT_DIR}/dreame-xp2p-host-runner.o"

"${AARCH64_LD}" \
  -pie --hash-style=both --allow-shlib-undefined \
  -z max-page-size=65536 -z common-page-size=65536 \
  --dynamic-linker /system/bin/linker64 -e _start \
  -o "${OUTPUT_DIR}/dreame-xp2p-host-runner" \
  "${OUTPUT_DIR}/dreame-xp2p-host-runner.o" \
  "${AOSP_RUNTIME_ROOT}/lib64/bionic/libdl.so" \
  "${AOSP_RUNTIME_ROOT}/lib64/bionic/libc.so" \
  "${AOSP_RUNTIME_ROOT}/lib64/ld-android.so"

"${AARCH64_STRIP}" --strip-unneeded \
  "${OUTPUT_DIR}/dreame-xp2p-host-runner"
gzip -9 -n -c "${OUTPUT_DIR}/dreame-xp2p-host-runner" \
  > "${OUTPUT_DIR}/dreame-xp2p-host-runner.gz"

sha256sum \
  "${OUTPUT_DIR}/dreame-xp2p-host-runner" \
  "${OUTPUT_DIR}/dreame-xp2p-host-runner.gz"
