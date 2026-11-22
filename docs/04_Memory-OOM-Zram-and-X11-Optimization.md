# 04. Memory (OOM / `zram`) Optimization, `320x240` X11 Tuning & Battery Monitoring

- **Target Device**: HP Prime G1 (`G8X92AA` Rev C, 32 MB SDRAM / 23.4 MB Linux RAM)
- **Related Files**:
  - [rootfs-overlay/etc/sysctl.conf](../rootfs-overlay/etc/sysctl.conf)
  - [rootfs-overlay/etc/init.d/S03swapon](../rootfs-overlay/etc/init.d/S03swapon)
  - [rootfs-overlay/etc/init.d/S99xterm](../rootfs-overlay/etc/init.d/S99xterm)
  - [src/hpp-battery/hpp_battery.c](../src/hpp-battery/hpp_battery.c)

---

## 1. Eliminating Early OOM & 81-Minute NAND Swap Livelock

### 1.1 Root Cause 1: 4 MB Pageblock Watermark Boost on a 23.4 MB RAM System
In Linux 6.1.35 on ARM, `pageblock_order = 10` (`4 MB`). With the default `vm.watermark_boost_factor = 15000`, a single high-order fragmentation event boosted the zone watermark by 4 MB (on top of a 4 MB `highatomic` reserve), locking over 8 MB of the 23.4 MB total RAM and triggering premature OOM kills (`Xorg` and games killed even with >3 MB free RAM).

- **Permanent Fix ([rootfs-overlay/etc/sysctl.conf](../rootfs-overlay/etc/sysctl.conf))**:
  ```ini
  vm.watermark_boost_factor = 0
  vm.swappiness = 20
  vm.vfs_cache_pressure = 150
  ```

### 1.2 Root Cause 2: NAND Flash Swap (`/dev/loop0`) Livelock on Non-Preemptible Kernel
Swapping 4 KB pages onto a 128 KB eraseblock NAND flash partition (`mtd6`) via `/dev/loop0` caused massive read-modify-write stalls. Because the kernel is compiled with `CONFIG_PREEMPT_NONE=y`, `kswapd0` monopolized 100% of the single 400 MHz ARM926 core waiting on NAND I/O locks, freezing SSH and console responsiveness for up to 81 minutes.

- **Permanent Fix ([rootfs-overlay/etc/init.d/S03swapon](../rootfs-overlay/etc/init.d/S03swapon))**:
  Disabled NAND flash swap and replaced it with a **12 MiB `/dev/zram0` compressed RAM swap** block device at boot (`0%` `kswapd0` I/O wait, microsecond swap latency).

---

## 2. `320x240` X11 / XTerm Geometry Optimization

### 2.1 Bottom-Line Clipping Fix
Running `xterm` with its default `80x24` geometry on a `6x13` font required `480x312` pixels, pushing the bottom 72 pixels (5~6 lines, including the shell prompt) off the `320x240` LCD screen.

- **English `fixed` Font (`6x13` cell)**: `-geometry 53x17+0+0` (`318x221` px <= `320x240` px).
- **Korean `GalmuriMono11` PCF Font (`6x16` half / `12x16` full cell)**: `-geometry 52x14+0+0` (`312x224` px <= `320x240` px).

---

## 3. Hardware Battery Voltage & Charging Status Monitor (`hpp-batt`)

Reverse-engineered from `PRIME_OS.ROM` (`0xd60c`):
1. **ADC Channel**: S3C2416 on-chip 12-bit ADC (`0x58000000`) channel **`AIN0`**.
2. **Clock Gate (`PCLKCON 0x4C000034`)**: `hpp-batt` automatically sets Bit 7 (`PCLK_ADC = 1`) before sampling.
3. **Voltage Conversion Formula**:
   $$\text{Voltage (V)} = \text{ADC\_RAW} \times 0.004556$$
   - `920` $\approx 4.19\text{ V}$ (100% Full)
   - `823` $\approx 3.75\text{ V}$ (~65%)
   - `757` $\approx 3.45\text{ V}$ (Low Battery)
4. **Charging Status Pin**: `GPF3` (`GPFDAT 0x56000054` Bit 3) == `1` when external USB power is charging the battery.

