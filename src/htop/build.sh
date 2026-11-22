#!/usr/bin/env bash
set -euo pipefail

PROJ_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$PROJ_DIR/../../.." && pwd)"
BR_HOST="$ROOT/30_TEST/40_Build/buildroot/out-b/host"
SYSROOT="$ROOT/30_TEST/40_Build/buildroot/out-b/staging"

export CC="$BR_HOST/bin/arm-linux-gcc"
export AR="$BR_HOST/bin/arm-linux-ar"
export RANLIB="$BR_HOST/bin/arm-linux-ranlib"
export STRIP="$BR_HOST/bin/arm-linux-strip"
export READELF="$BR_HOST/bin/arm-linux-readelf"

export PKG_CONFIG="/usr/bin/pkg-config"
export PKG_CONFIG_DIR=""
export PKG_CONFIG_LIBDIR="$SYSROOT/usr/lib/pkgconfig:$SYSROOT/usr/share/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"

export CFLAGS="-Os -pipe"

SRC_DIR="$PROJ_DIR/src"
BUILD_DIR="$PROJ_DIR/build"
OUT_DIR="$PROJ_DIR/out"

mkdir -p "$BUILD_DIR" "$OUT_DIR"

if [ ! -x "$SRC_DIR/configure" ]; then
  (cd "$SRC_DIR" && ./autogen.sh)
fi

cd "$BUILD_DIR"
"$SRC_DIR/configure" \
  --host=arm-buildroot-linux-gnueabi \
  --prefix=/usr \
  --disable-unicode \
  --with-curses=ncurses \
  --disable-hwloc \
  --disable-capabilities \
  --disable-delayacct \
  --disable-sensors \
  --disable-pcp \
  --disable-backtrace \
  --without-libunwind \
  --disable-demangling \
  --disable-werror

make -j"$(nproc)"
cp -f "$BUILD_DIR/htop" "$OUT_DIR/htop"
"$STRIP" "$OUT_DIR/htop"

