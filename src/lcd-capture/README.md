# HP Prime G1 Live LCD Screen Capture Tool (`lcd-capture`)

- **Target Hardware**: HP Prime G1 (`G8X92AA` Rev C), Samsung S3C2416 (ARM926EJ-S @ 400 MHz, ARMv5TEJ, soft-float)
- **Display Geometry & Format**: `320 x 240`, `32 bpp` (`x8r8g8b8`, stride `1,280` bytes)
- **Related Docs**: [../htop/README.md](../htop/README.md), [../../docs/09_Htop-and-LCD-Capture.md](../../docs/09_Htop-and-LCD-Capture.md), [../../kernel/arch/arm/mach-s3c/mach-hpprime.c](../../kernel/arch/arm/mach-s3c/mach-hpprime.c)

---

## 1. Hardware Background (`/dev/fb0` vs `0x31F80000`)

HP Prime G1 Linux 6.1.35 uses `simpledrm` (`CONFIG_DRM_SIMPLEDRM=y`, `CONFIG_DEVMEM=y`, `# CONFIG_STRICT_DEVMEM is not set`):

- Under Xorg (`modeset(0)`), `/dev/fb0` (`simpledrmdrmfb`) retains the stale early boot `fbcon` shadow buffer, whereas live rendered pixels from Xorg (`xterm`, `htop`) and SDL 1.2 apps (`loderunner-ng`, `prboom`) are written directly to physical scanout RAM **`0x31F80000`** (`simple-framebuffer` in `/proc/iomem`, `SZ_512K`).
- **`lcd-capture` Backends (`--source auto`)**:
  1. **`hw` (default priority)**: Parses `simple-framebuffer` physical address from `/proc/iomem` (fallback `0x31F80000`) and mmaps `/dev/mem` read-only (`O_RDONLY`) to capture the exact physical LCD scanout (Xorg, SDL games, and console).
  2. **`x11`**: Captures the X11 root window via `XGetImage`.
  3. **`fb`**: Captures `/dev/fb0` via `FBIOGET_VSCREENINFO`/`FBIOGET_FSCREENINFO` (`16`/`24`/`32` bpp).
  4. **`test`**: Generates a synthetic `320x240` `x8r8g8b8` color bar pattern for offline encoder validation.

---

## 2. Directory Layout

```text
lcd-capture/
├── Makefile             # ARMv5TE cross-build (all) and host encoder unit test (host-test)
├── build.sh             # Cross-compile + file/readelf ABI verification script
├── capture_from_pc.sh   # Zero-JFFS2-write SSH stdout streaming capture helper
├── README.md            # This document
├── src/
│   └── lcd_capture.c    # C99 implementation (hw/x11/fb/test, PNG/BMP/PPM, stdout '-')
└── out/
    └── lcd-capture      # Stripped ARMv5TEJ executable (67,188 B / ~66 KB)
```

---

## 3. Binary & Live Hardware Verification

| Metric | Observed Result |
| :--- | :--- |
| **Executable** | [out/lcd-capture](out/lcd-capture) — `67,188` B (~66 KB stripped ELF PIE, ARM EABI5 v5TEJ soft-float) |
| **Runtime `ldd` on Calculator** | `libpng16.so.16`, `libz.so.1`, `libX11.so.6`, `libxcb.so.1`, `libXau.so.6`, `libXdmcp.so.6`, `libm.so.6`, `libc.so.6` |
| **`--info` on Calculator** | `simple-framebuffer (/proc/iomem): FOUND (phys=0x31F80000)`, `/dev/mem: readable (O_RDONLY OK)`, `/dev/fb0: simpledrmdrmfb 320x240 32bpp`, `X11 (:0.0): 320x240 depth=24` |
| **`--source test -o -` PNG Size** | `801` bytes (identical on host unit test and live calculator) |
| **Live Captures Verified** | Captured `320x240` RGB PNGs of `loderunner-ng`, `htop`, and DOOM (`prboom`) title/gameplay/menu screens (`2.3 KB` ~ `85 KB`) |

---

## 4. Usage Examples

```bash
# 1. Stream directly to Host PC via SSH (writes 0 bytes to calculator NAND/USB)
./src/lcd-capture/capture_from_pc.sh shot.png
ssh prime "/mnt/usb/HP-Prime-G1-USB/lcd-capture/lcd-capture -d 3 -o -" > cap.png

# 2. Run locally on calculator (save to RAM /tmp or mounted USB)
/mnt/usb/HP-Prime-G1-USB/lcd-capture/lcd-capture --info
/mnt/usb/HP-Prime-G1-USB/lcd-capture/lcd-capture -d 3 -o /tmp/cap.png
```

