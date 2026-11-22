#!/usr/bin/env bash
set -euo pipefail

PROJ_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$PROJ_DIR/../../.." && pwd)"
READELF="$ROOT/30_TEST/40_Build/buildroot/out-b/host/bin/arm-linux-readelf"

cd "$PROJ_DIR"
make clean
make

echo "=== BINARY SIZE ==="
ls -lh build/lcd-capture.unstripped out/lcd-capture
echo "=== FILE ==="
file out/lcd-capture
echo "=== ARM EABI ==="
"$READELF" -A out/lcd-capture
echo "=== NEEDED LIBS ==="
"$READELF" -d out/lcd-capture | grep NEEDED

