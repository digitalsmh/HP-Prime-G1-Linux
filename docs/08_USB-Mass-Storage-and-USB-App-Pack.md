# Kernel USB Mass Storage (`FAT32`/`ext4`), OTG Power Stability & Portable USB App Pack

- **Target Hardware**: HP Prime G1 (`G8X92AA` Rev C, Samsung S3C2416, 32 MB SDRAM, 256 MB NAND)
- **Kernel & RootFS**: Linux `6.1.35 #7` (`armv5tejl`), Buildroot `2023.02.6` (JFFS2)
- **Related Docs**: [03_USB-OTG-Hotplug-and-Ethernet-LAN.md](03_USB-OTG-Hotplug-and-Ethernet-LAN.md), [04_Memory-OOM-Zram-and-X11-Optimization.md](04_Memory-OOM-Zram-and-X11-Optimization.md), [09_Htop-and-LCD-Capture.md](09_Htop-and-LCD-Capture.md)

---

## 1. Kernel Built-In USB Mass Storage & Filesystem Support

Earlier builds (`#6`) lacked `CONFIG_SCSI`, causing USB flash drives to enumerate over `s3c2410-ohci` without creating `/dev/sd*` nodes. Standalone `.ko` insertion (`insmod`) is also impossible against `#6` because `CONFIG_SCSI` selects static kernel helper **`CONFIG_SG_POOL`** (`lib/sg_pool.c`).

Rebuilt and flashed [kernel/arch/arm/configs/hpprimev2_defconfig](../kernel/arch/arm/configs/hpprimev2_defconfig) (`zImage` -> `LINUX.DAT`) with built-in (`=y`) storage and Korean filename support:

| Subsystem | Enabled `=y` Options | Purpose |
| :--- | :--- | :--- |
| **USB / SCSI Block** | `CONFIG_SCSI`, `CONFIG_BLK_DEV_SD`, `CONFIG_USB_STORAGE` | Automatically binds USB Mass Storage (`usb-storage`) to `/dev/sdX` |
| **FAT32 / LFN** | `CONFIG_FAT_FS`, `CONFIG_VFAT_FS` | Mounts FAT32/FAT16/VFAT partitions with long filenames |
| **Linux ext4/2/3** | `CONFIG_EXT4_FS`, `CONFIG_EXT4_USE_FOR_EXT2` | Mounts ext4/ext3/ext2 partitions (`JBD2` journaling) |
| **Korean NLS** | `CONFIG_NLS_UTF8`, `CONFIG_NLS_CODEPAGE_949` | Built-in UTF-8 and CP949 filename support (`iocharset=utf8`) |

### Binary Size & Live RAM Impact (`#6` -> `#7`)
- **`zImage` size**: `3,639,600` B -> **`4,112,232` B** (`+472,632` B / ~3.92 MiB; `4.08 MiB` headroom inside 8 MiB `mtd3`).
- **`vmlinux` static footprint**: `8,288,602` B -> **`9,329,935` B** (`+0.99 MiB`).
- **Live calculator RAM (`#7`)**: `MemTotal: 22,972 kB`, `MemAvailable: 7,692 kB` idle (`8,988 kB` during read-only mount), confirming healthy headroom despite the larger kernel.

---

## 2. Real Hardware Verification (`/dev/sda` GPT 4-Partition Test)

Tested with a 14.3 GiB SanDisk Cruzer Force (`0781:557d`, `30,031,872` 512-byte sectors) attached through an OTG hub alongside Realtek RTL8153 Ethernet LAN and USB HID input:

| Partition | Size | Filesystem / Label | Live Calculator Result |
| :--- | :---: | :--- | :--- |
| `/dev/sda1` | `6211M` | `ISO9660` (Live ISO) | Not mounted (`CONFIG_ISO9660_FS` omitted; single-partition FAT32/ext4 recommended) |
| `/dev/sda2` | `5148K` | `vfat` (`LABEL="ESP"`) | **Mounted cleanly** (`-o ro,iocharset=utf8`, `5.0M` total / `980.0K` free, `EFI/` verified) |
| `/dev/sda3` | `300K` | `Gap1` | Protective GPT gap |
| **`/dev/sda4`** | **`8445M`** | **`ext4` (`LABEL="writable"`)** | **Mounted `ro` & `rw` cleanly** (`8.0G` total, **`7.4G` free**; verified `um`/`uu` aliases) |

---

## 3. Root Causes Solved During USB Bulk Copy

### 3.1 Tiered USB Hub Power & EMI Reset Fix
When using a 2-tier USB topology, placing high-draw devices (keyboard `200mA`, USB flash drive `200mA`) on an **unpowered tier-1 bus hub** (`MaxPower=100mA`) while only the tier-2 hub had external 5V power caused `usb usb1-port1: disabled by hub (EMI?), re-enabling...` bus resets during enumeration/load. Moving the USB flash drive onto the **externally powered 5V hub** eliminated bus drops and I/O errors completely.

### 3.2 Eliminating NAND Swap Livelock During Bulk Transfers
When reflashing `LINUX.DAT` overwritten with a base `rootfs.jffs2`, the system briefly reverted to `/dev/loop0` (`mtdblock6` NAND swap) and `vm.watermark_boost_factor = 15000`, which spiked `pgscan_direct` to `80,599` and triggered OOM under bulk USB writes.

Synchronized permanent fixes across both [V3 overlays](../rootfs-overlay) and the live JFFS2:
1. **[rootfs-overlay/etc/init.d/S03swapon](../rootfs-overlay/etc/init.d/S03swapon)**: Enables **12 MiB `/dev/zram0`** first (`swapon /dev/zram0` — omitting unsupported BusyBox `-p` flag), flushes caches (`drop_caches = 3`), and safely disables `/dev/loop0`.
2. **[rootfs-overlay/etc/sysctl.conf](../rootfs-overlay/etc/sysctl.conf)**: Enforces `vm.vfs_cache_pressure = 150`, `vm.swappiness = 20`, and **`vm.watermark_boost_factor = 0`**.
3. **Observed `vmstat` delta across full 8.9 MB USB pack copy + `htop`/`lcd-capture` transfers**: `allocstall_normal` **+0**, `pgscan_direct_throttle` **+0**, additional `oom_kill` **0**, and zero USB/SCSI `dmesg` errors.

---

## 4. Terminal Profile (`/root/.profile`) & Portable USB App Pack (`/mnt/usb/HP-Prime-G1-USB/`)

Synchronized [rootfs-overlay/etc/profile](../rootfs-overlay/etc/profile) (`TZ=KST-9`, `HOME=/root`) and [rootfs-overlay/root/.profile](../rootfs-overlay/root/.profile) with one-touch USB mount aliases:
- `um` / `usb-mount`: `mkdir -p /mnt/usb && mount -t ext4 /dev/sda4 /mnt/usb`
- `uu` / `usb-umount`: `sync && umount /mnt/usb` (always run before unplugging to protect ext4 journals)

Populated **`/mnt/usb/HP-Prime-G1-USB/`** (`8.9M` total) as a self-contained portable software pack so large game WADs and tools live off internal NAND:

| Folder | Contents | Notes |
| :--- | :--- | :--- |
| **`doom/`** | `prboom`, `prboom-game-server`, `doom1.wad`, `prboom.wad`, `hpp-keys.cfg`, `run_doom.sh` | Standalone USB launcher (`stop` restores XTerm) |
| **`loderunner-ng/`** | `loderunner-ng`, `COPYING`, `run_loderunner.sh`, `share/loderunner-ng/` (`150` levels, `9` sounds, `14` textures) | Enforces `zram0` + `boost=0` before start |
| **`xlode/`** | `Xload`, `xlode -> Xload`, `LICENSE`, `VeraMono.ttf`, `320/` (`66` BMPs), `stage1~5.map`, `.wav`/`.xm`, `run_xload.sh` | Lightweight SDL 1.2 XLode port |
| **`htop/`** | `htop` (`263 KB`), `README.md` | Non-wide `ncurses` 6.4 ARMv5TEJ build |
| **`lcd-capture/`** | `lcd-capture` (`66 KB`), `README.md` | Physical `0x31F80000` LCD capture tool |
| **`README.md`** | Full USB pack manual (`156` lines) | Covers `um`/`uu`, launchers, and keypad maps |

