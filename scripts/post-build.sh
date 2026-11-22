#!/bin/sh
# 40_Build dump-mode post-build: remove NAND write/erase tools and set executable permissions.
set -eu
TARGET_DIR="$1"
for f in nandwrite flash_erase flash_eraseall flashcp flash_unlock flash_lock nandtest mtd_debug ubiformat; do
    rm -f "$TARGET_DIR/usr/sbin/$f" "$TARGET_DIR/sbin/$f"
done
chmod 0755 "$TARGET_DIR/usr/sbin/hpp-dump" \
           "$TARGET_DIR/usr/sbin/hpp-poweroff" \
           "$TARGET_DIR/usr/bin/hpp-doom" \
           "$TARGET_DIR/usr/bin/input-event-daemon" \
           "$TARGET_DIR/usr/bin/hpp-vkbd" \
           "$TARGET_DIR/usr/bin/hpp-vkbd-toggle" \
           "$TARGET_DIR/etc/init.d/S03swapon" \
           "$TARGET_DIR/etc/init.d/S04usb" \
           "$TARGET_DIR/etc/init.d/S99xterm"
echo "post-build: permissions updated and NAND write tools removed"
