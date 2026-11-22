#!/usr/bin/env bash
# HP Prime G1 Linux-For-HPPrime-V3 Image Wrapper (compatible with V2 mkimg.sh workflow)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZIMAGE_PATH="${1:-zImage}"
ROOTFS_PATH="${2:-rootfs.jffs2}"
OUTPUT_PATH="${3:-LINUX.DAT}"

exec python3 "${SCRIPT_DIR}/tools/mkimg_v3.py" build \
    --zimage "${ZIMAGE_PATH}" \
    --rootfs "${ROOTFS_PATH}" \
    --output "${OUTPUT_PATH}"

