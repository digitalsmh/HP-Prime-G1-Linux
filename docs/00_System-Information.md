# 00. HP Prime G1 (`G8X92AA`) Hardware & Linux Runtime System Information

- **Target Device**: HP Prime G1 Graphing Calculator (`G8X92AA` Hardware Revision C)
- **Board Name**: `HPPRIMEV2`
- **SoC / Processor**: Samsung S3C2416XH-40 (`ARM926EJ-S rev 5 (v5l)` @ 400 MHz, ARMv5TEJ, Soft-float)
- **Chip ID (`GSTATUS1 0x560000B0`)**: `0x32450003`

---

## 1. Operating System & Kernel Environment

| Item | Specification |
| :--- | :--- |
| **OS Distribution** | **Buildroot 2023.02.6** (LTS) |
| **Linux Kernel** | **Linux 6.1.35 (`#6`)** `armv5tejl` |
| **Cross Compiler** | `arm-linux-gcc 12.2.0` (`arm-buildroot-linux-gnueabi`, `-msoft-float`) |
| **C Standard Library** | **GNU C Library (`glibc`) v2.35** |
| **Core Utilities / Shell** | **BusyBox v1.36.1** (`/bin/sh` ash, Unicode enabled) |
| **Hostname** | `HP-Prime-V2` |
| **Kernel Boot Cmdline** | `console=tty0 console=ttySAC0,115200n8 root=mtd:rootfs rw rootfstype=jffs2 rootwait init=/linuxrc loglevel=7` |

---

## 2. CPU & Memory Architecture (`32 MB SDRAM` + `12 MiB zram0`)

Out of the physical **32 MB SDRAM (`0x30000000 ~ 0x32000000`)**, the top **512 KB (`0x31F80000 ~ 0x31FFFFFF`)** is reserved by BOOT1 (`ATAG_MEM`) as the dedicated LCD framebuffer VRAM, and **23.4 MB (`23,976 kB`)** is managed by the Linux kernel page allocator.

```text
processor       : 0
model name      : ARM926EJ-S rev 5 (v5l)
BogoMIPS        : 199.06 (~400 MHz core clock)
Features        : swp half thumb fastmult edsp java
CPU implementer : 0x41
CPU architecture: 5TEJ
Hardware        : HPPRIMEV2
```

### Runtime Memory & Compressed Swap (`/proc/meminfo`, `/proc/swaps`)
```text
MemTotal:          23976 kB (~23.4 MiB)
MemFree:             876 kB
MemAvailable:       3380 kB ~ 4484 kB (with Xorg, XTerm, and hpp-vkbd running)
SwapTotal:         12284 kB (12.0 MiB /dev/zram0 LZO/LZ4 compressed RAM swap)
SwapFree:          11516 kB
```

---

## 3. NAND Flash Storage & MTD Partition Layout (`256 MB NAND`)

The on-board 256 MB NAND flash uses `128 KB (0x20000)` eraseblocks:

| MTD Device | Offset | Size | Eraseblock | Name | Access / Purpose |
| :--- | :---: | :---: | :---: | :--- | :--- |
| **`mtd0`** | `0x00000000` | `256 MB` | `128 KB` | `"NAND"` | Read-Only full master device (for safe `nanddump`) |
| **`mtd1`** | `0x00000000` | `256 KB` | `128 KB` | `"HP Boot Code"` | **Read-Only** (`MTD_WRITEABLE` masked; Blocks 0~1 `BXCBOOT0.BIN` v16) |
| **`mtd2`** | `0x00040000` | `512 KB` | `128 KB` | `"Linux Bootloader"` | Blocks 2~5 (`BOOT1.ROM`) |
| **`mtd3`** | `0x00100000` | `8.0 MB` | `128 KB` | `"kernel"` | Blocks 8~71 (`zImage`) |
| **`mtd4`** | `0x00900000` | `54.0 MB` | `128 KB` | `"rootfs"` | Blocks 72~503 (`JFFS2` read/write root filesystem `/`) |
| **`mtd5`** | `0x03F00000` | `112.0 MB`| `128 KB` | `"data"` | Blocks 504~1399 |
| **`mtd6`** | `0x0AF00000` | `64.0 MB` | `128 KB` | `"swap"` | Blocks 1400~1911 (Unused; replaced by `/dev/zram0`) |
| **`mtd7`** | `0x0EFF8000` | `32 KB` | `128 KB` | `"reserved"` | Reserved tail |

---

## 4. Display, Input Devices & Hardware Interrupts

- **Display Panel**: 3.5-inch QVGA TFT LCD (`320 x 240`, 32 bpp `x8r8g8b8`, physical VRAM `0x31F80000`, `simpledrmdrmfb` `/dev/fb0`, Backlight `GPB1`, FIMD controller `0x4C800000`).
- **Input 0 (`matrix-keypad`, `/dev/input/event0`)**: 8x8 GPIO matrix (`GPD0..7` columns, `GPG0..7` rows), plus direct active-high `[On]` key on `GPG0` (`EINT8`).
- **Input 1 (`Goodix Capacitive TouchScreen`, `/dev/input/event1`)**: `GDIX1001:00` on I2C0 (`IRQ 18`, `s3c-ext0`), bound to Xorg via `tslib`.
- **Input 2 (`USB HID Keyboard`)**: Hot-pluggable via USB OTG hub (`s3c2410-ohci`).

---

## 5. Physical Memory & MMIO Register Map

| Physical Address Range | Module / Peripheral | Description |
| :--- | :--- | :--- |
| `0x30000000 - 0x31F7FFFF` | `System RAM` | 31.5 MB Linux kernel memory (`ATAG_MEM`) |
| `0x31F80000 - 0x31FFFFFF` | `Simple Framebuffer` | 512 KB dedicated LCD VRAM (`320x240x4` = 307,200 B) |
| `0x49000000 - 0x490000FF` | `s3c2410-ohci` | USB 1.1 Host Controller (`IRQ 42`) |
| `0x49800000 - 0x49800FFF` | `s3c-hsudc` | USB 2.0 High-Speed Device Controller (`IRQ 41`) |
| `0x4A000000 - 0x4A0000FF` | `S3C2416 INTC` | Interrupt Controller (`SRCPND`, `INTMSK`, `INTPND`) |
| `0x4C000000 - 0x4C0000FF` | `S3C2416 SYSCON` | System & Power Controller (`PWRMODE`, `PWRCFG`, `INFORM0..3`, `PHYPWR`, `UCLKCON`) |
| `0x4C800000 - 0x4C800FFF` | `S3C2416 FIMD` | LCD Controller (`VIDCON0`) |
| `0x4E000000 - 0x4E0FFFFF` | `s3c2412-nand` | NAND Flash Controller |
| `0x54000000 - 0x54000FFF` | `s3c2410-i2c.0` | I2C0 Controller (Goodix Touchscreen) |
| `0x56000000 - 0x560000FF` | `S3C2416 GPIO` | GPIO Ports `GPB`..`GPH`, `MISCCR`, `EXTINT0/1`, `EINTMASK`, `EINTPEND`, `GSTATUS1` |
| `0x58000000 - 0x5800001F` | `S3C2416 ADC` | 12-bit On-Chip ADC (`AIN0` battery voltage sense) |

