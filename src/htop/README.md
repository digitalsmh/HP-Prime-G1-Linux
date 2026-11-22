# `htop` Cross-Build for HP Prime G1 (`ARMv5TEJ`)

- **Target Hardware**: HP Prime G1 (`G8X92AA` Rev C), Samsung S3C2416 (ARM926EJ-S @ 400 MHz, ARMv5TEJ, soft-float)
- **Target OS**: Linux 6.1.35, Buildroot 2023.02.6 (`glibc 2.35`, `libncurses.so.6.4`)
- **Upstream Source**: [htop-dev/htop](https://github.com/htop-dev/htop) (`htop 3.6.0-dev` / `3.5.3` base, GPL-2.0+)
- **Related Docs**: [docs/09_Htop-and-LCD-Capture.md](../../docs/09_Htop-and-LCD-Capture.md), [../lcd-capture/README.md](../lcd-capture/README.md)

---

## 1. Directory Layout

```text
htop/
├── build.sh      # Out-of-tree ARMv5TEJ cross-compile script (Buildroot toolchain & staging sysroot)
├── README.md     # This document
└── out/          # build output (not included in this repository)
```

> Upstream `htop` source files and intermediate `.o` build artifacts are intentionally omitted to keep the repository compact. Place the upstream `htop` tree under `src/` and run `build.sh` inside the workspace to rebuild from source.

---

## 2. Build Configuration & Design Choices

[build.sh](build.sh) configures `htop` out-of-tree using the Buildroot ARMv5TEJ soft-float wrapper (`arm-linux-gcc`) and staging `pkg-config` sysroot:

| Configure Flag | Rationale |
| :--- | :--- |
| `--host=arm-buildroot-linux-gnueabi` | Targets Samsung S3C2416 ARM926EJ-S soft-float ABI |
| **`--disable-unicode --with-curses=ncurses`** | Buildroot staging & target ship non-wide `libncurses.so.6.4` (`libncursesw` is absent) |
| `--disable-hwloc --disable-capabilities` | Drops `libhwloc` / `libcap` unnecessary on single-core ARM9 |
| `--disable-delayacct --disable-sensors --disable-pcp` | Removes `libnl-3`, `libsensors`, and PCP dependencies |
| `--disable-backtrace --without-libunwind --disable-demangling` | Minimizes footprint for 23.4 MB SDRAM & JFFS2 flash |
| `CFLAGS="-Os -pipe"` | Optimizes binary code size |

---

## 3. Binary Verification (Real Hardware & Toolchain)

| Metric | Observed Result |
| :--- | :--- |
| **Executable** | `out/htop` (build output, not included) — `269,204` B (~263 KB stripped, down from `427 KB` unstripped) |
| **ELF Format (`file`)** | `ELF 32-bit LSB pie executable, ARM, EABI5 version 1 (SYSV), dynamically linked, interpreter /lib/ld-linux.so.3, for GNU/Linux 3.2.0, stripped` |
| **Runtime `ldd` on Calculator** | `libncurses.so.6`, `libm.so.6`, `libc.so.6`, `/lib/ld-linux.so.3` (all resolved cleanly) |
| **`htop --version` on Calculator** | `htop 3.6.0-dev` |
| **Live Hardware Run (`lcd-htop.png`)** | Verified on `320x240` LCD (`Mem: 14.3M/22.4M`, `Swp: 5.23M/12.0M` zram0, `CPU: 9.4%`) |

---

## 4. Deployment & Usage

Dropbear SSH on HP Prime does not include an SFTP subsystem; always pass **`scp -O`**:

```bash
# Deploy to calculator internal JFFS2 or USB flash drive
scp -O out/htop prime:/mnt/usb/HP-Prime-G1-USB/htop/htop

# Run on calculator XTerm or over SSH
TERM=xterm-256color /mnt/usb/HP-Prime-G1-USB/htop/htop
```

- **Quit**: `q`, `F10`, or calculator keypad **`[Help] + [C]`** (`Ctrl+C`).

