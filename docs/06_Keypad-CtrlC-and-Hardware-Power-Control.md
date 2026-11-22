# 06. Physical Keypad (`Help=Ctrl+C`) & Hardware Power-On/Off Control (`0x2BED` SLEEP + USB Charging Mode)

- **Target Device**: HP Prime G1 (`G8X92AA` Rev C, Samsung S3C2416)
- **Related Sources**:
  - [src/hpp-poweroff/hpp_poweroff.c](../src/hpp-poweroff/hpp_poweroff.c)
  - [src/input-event-daemon/input-event-daemon.c](../src/input-event-daemon/input-event-daemon.c)
  - [kernel/arch/arm/mach-s3c/mach-hpprime.c](../kernel/arch/arm/mach-s3c/mach-hpprime.c)
  - [rootfs-overlay/etc/input-event-daemon.conf](../rootfs-overlay/etc/input-event-daemon.conf)
  - [rootfs-overlay/etc/X11/hpp-keymap.xkb](../rootfs-overlay/etc/X11/hpp-keymap.xkb)

---

## 1. `[Help] + [C]` as Terminal `Ctrl+C` (`SIGINT`)

The HP Prime G1 physical keypad lacks a dedicated `Ctrl` key. We mapped the top-left **`[Help]`** key (`Row 5, Col 7`) to `Control_L`:
1. **X11 Runtime Mapping ([rootfs-overlay/etc/X11/hpp-keymap.xkb](../rootfs-overlay/etc/X11/hpp-keymap.xkb))**:
   - Mapped `<HELP>` (X11 keycode 146) to `key <HELP> { [ Control_L ] };` and registered `modifier_map Control { <LCTL>, <RCTL>, <HELP> };`.
   - Loaded automatically on Xorg startup by `/etc/init.d/S99xterm` via `xkbcomp /etc/X11/hpp-keymap.xkb :0`.
2. **Kernel Matrix Definition ([kernel/arch/arm/mach-s3c/mach-hpprime.c](../kernel/arch/arm/mach-s3c/mach-hpprime.c))**:
   - Updated `KEY(5, 7, KEY_HELP)` to `KEY(5, 7, KEY_LEFTCTRL)` so both TTY console and X11 recognize `[Help]` natively as Left Control.

---

## 2. True Hardware Power-Off (`[Shift] + [On]`) & Cold-Boot Wake-Up (`[On]`)

### 2.1 Why Standard `/sbin/poweroff` Failed in Legacy Kernel
The original `powercut()` in `mach-hpprime.c` only cleared a few USB PHY bits and returned into an infinite `while (1);` loop, leaving the LCD backlight (`GPB1`), FIMD controller (`VIDCON0`), 400 MHz CPU clock, and 5V OTG boost active.

### 2.2 Reverse-Engineered S3C2416 Hardware Shutdown Sequence (`PRIME_OS.ROM` & `BXCBOOT0.BIN`)

By disassembling `PRIME_OS.ROM` (`0x300056c8`, `0x3007ea5c`) and Boot0 (`BXCBOOT0.BIN` `0x1cc`), we implemented the complete 3-part hardware shutdown specification in [src/hpp-poweroff/hpp_poweroff.c](../src/hpp-poweroff/hpp_poweroff.c) (`/usr/sbin/hpp-poweroff`) and kernel `powercut()`:

#### ① LCD Backlight (`GPB1`) & Display (`VIDCON0`) OFF
- **FIMD Stop**: Clear `VIDCON0 (0x4C800000)` Bits `[1:0]` (`ENVID`, `ENVID_F` = `00b`).
- **Backlight Off**: Clear `GPBDAT (0x56000014)` Bit 1 (`GPB1 = 0`, `0x00000060`), set `GPBCON = 0x00141405`, `GPBUDP = 0x00014010`.
- **Critical Kernel Oops Avoidance (`0x31F80000` `/dev/mem` Prohibition)**:
  - `BOOT1` excludes the top 512 KB framebuffer (`0x31F80000 ~ 0x32000000`, `PFN 0x31F80`) from `ATAG_MEM`. In Linux Flatmem, `pfn_valid(0x31f80)` is `true`, but its `struct page` metadata is uninitialized (`0x55555555`).
  - Mapping `0x31F80000` via `/dev/mem` and writing to it from userspace causes `handle_mm_fault` -> `update_mmu_cache` to dereference `0x55555554` (Kernel Data Abort / Oops).
  - By controlling only MMIO registers (`0x4A000000`, `0x4C000000`, `0x4C800000`, `0x56000000`, where `pfn_valid() == false`), the display turns off instantaneously with zero kernel faults.

#### ② USB Port Charging Mode
- **Disable 5V VBUS Reverse Boost**: `GPFDAT` Bit 7 (`GPF7`) = `0`, `GPHDAT` Bit 13 (`GPH13`) = `0`.
- **Disable D+ Pull-Up**: `GPHDAT` Bit 14 (`GPH14`) = `0`.
- **Suspend USB PHY & Clocks**: `PHYPWR (0x4C000084) |= 0x0F`, `PWRCFG (0x4C000060) &= ~0x10`, `UCLKCON (0x4C00008C) = 0x00000000`, `MISCCR (0x56000080) |= (1 << 12)`.
- **Result**: All SoC USB current draw stops, while the dedicated Li-Ion battery charger IC charges the battery safely whenever external 5V USB power is connected.

#### ③ S3C2416 Hardware SLEEP Mode (`0x2BED`) & `[On]` Key (`GPG0`/`EINT8`) Wake-Up
1. **`[On]` Key Release Debouncing**: Configure `GPDCON = 0`, `GPGCON = 0` (Input + Pull-down) and wait until `GPG0 == 0` continuously for **100 ms** (prevents immediate wake-up while the user is still releasing `[On]`).
2. **Configure `EINT8` Rising-Edge Wake-Up**:
   - `EXTINT0 = 0x466464E4`, `EXTINT1 = 0x44444444` (`EINT8` Rising Edge)
   - `GPGCON = 0x00000002` (`GPG0 = EINT8`)
   - `EINTMASK = 0x00FFFEFF` (Unmask `EINT8` only), `EINTPEND = 0x00FFFFFF`
   - `INTMSK = 0xFFFFFFDF` (Unmask `IRQ_EINT8_23` only), clear `SRCPND`/`INTPND`/`WKUPSTAT`.
3. **Cold-Boot Flags & CPU OFF**:
   - Set `INFORM0..3 (0x4C000070..7C) = 0x00000000` (Boot0 checks `INFORM2 != 0x55AA` on wake-up and performs a clean Cold Boot into `BOOT1` -> Linux).
   - Write `RSTCON = 0x0001FE80`, `PWRCFG = 0x00008101`, and **`PWRMODE (0x4C000040) = 0x00002BED` ("Go to BED")** -> cuts `VDDARM` CPU core power and halts all PLLs.

---

## 3. Four Bug Fixes in `input-event-daemon` ([src/input-event-daemon/input-event-daemon.c](../src/input-event-daemon/input-event-daemon.c))

To make `SHIFT+OK` (`[Shift]+[On]`), `CAPSLOCK+OK` (`[Alpha]+[On]`), and `MENU` work reliably on the physical matrix keypad, we fixed four bugs in `input-event-daemon 0.1.3`:
1. **Modifier Duplicate Check Mismatch**: Compared translated `"SHIFT"` against `"SHIFT"` instead of raw `"LEFTSHIFT"`, preventing `"SHIFT"` from being appended multiple times.
2. **Kernel Autorepeat (`event->value == 2`) Filtering**: Ignored autorepeat events at the top of `input_parse_event()` so holding `[Shift]` for >250 ms does not corrupt `modifiers[]`.
3. **Immediate Key-Press (`value == 1`) Triggering & `key_down[]` State Tracking**: Fired shortcuts immediately upon key press rather than waiting for key release.
4. **`qsort` Comparator Pointer Dereference Bug**: Replaced direct `strcmp` callback in `qsort(modifiers, ...)` with a proper `const char **` wrapper `str_ptr_compare()`.

