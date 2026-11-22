# 03. Dual-Role USB OTG Hot-Plug, 5V VBUS Boost & USB-to-Ethernet (LAN)

- **Target Device**: HP Prime G1 (`G8X92AA` Rev C, Samsung S3C2416)
- **Related Sources**:
  - [kernel/arch/arm/mach-s3c/mach-hpprime.c](../kernel/arch/arm/mach-s3c/mach-hpprime.c) (`hpprime_otg_init`, `hpprime_set_host_phy`, `hpprime_set_vbus_power`)
  - [kernel/arch/arm/configs/hpprimev2_defconfig](../kernel/arch/arm/configs/hpprimev2_defconfig)
  - [rootfs-overlay/etc/init.d/S04usb](../rootfs-overlay/etc/init.d/S04usb)
  - [rootfs-overlay/etc/udev/rules.d/99-eth.rules](../rootfs-overlay/etc/udev/rules.d/99-eth.rules)

---

## 1. Hardware Discovery: Single Micro-USB OTG Architecture

By reverse-engineering the stock firmware (`PRIME_OS.ROM`), we identified the exact S3C2416 GPIO and clock registers governing the single Micro-USB port:

| Signal / Function | GPIO / Register | Active Level / Value | Description |
| :--- | :---: | :---: | :--- |
| **OTG ID Sense** | `GPF6` (`EINT6`) | `0` = Host (OTG grounded)<br>`1` = Device (Floating) | Detects OTG adapter insertion/removal in real time via both-edge interrupt `IRQ_EINT6` (`IRQ 54`) |
| **5V VBUS Boost Stage 1** | `GPF7` (`VBUS_EN1`) | `1` = Enable | Enables internal 5V step-up DC-DC converter |
| **5V VBUS Rail Stage 2** | `GPH13` (`VBUS_EN2`) | `1` = Enable (after 50ms) | Connects boosted 5V power rail to Micro-USB VBUS pin |
| **USB Power / D+ Pull-Up** | `GPH14` (`USB_PWR`) | `1` = Enable | Auxiliary USB power & Device D+ pull-up control |
| **48 MHz Host Clock** | `EPLLCON` (`0x4C000028`) | `0x00200102` (96 MHz) | `CLKSRC |= 0x40`, `CLKDIV1` `/2` = 48 MHz to OHCI |
| **USB PHY Control** | `PHYCTRL` / `PHYPWR` / `UCLKCON` | Host: `0x0` / `0x0` / `0x2`<br>Device: `0x12` / `0x30` / `0x7` | Shared USB PHY multiplexed between OHCI 1.1 Host and HSUDC 2.0 Gadget |

---

## 2. Simultaneous USB Hub, External Keyboard & Gigabit Ethernet (LAN)

```text
[HP Prime G1 (S3C2416 OHCI 1.1 + 5V VBUS Boost)]
       │ Micro-USB OTG Adapter (GPF6 ID = 0)
       ▼
[USB 2.0 4-Port Hub (1a40:0101 / 0bda:5411)]
  ├──► [USB External Keyboard (258a:0013)] ──► Direct typing on LCD XTerm
  └──► [Realtek RTL8153 USB Gigabit Ethernet (0bda:8153)] ──► Router LAN (192.168.123.165)
```

### 2.1 Kernel Built-in USB Ethernet Drivers (`hpprimev2_defconfig`)
```ini
CONFIG_USB_NET_DRIVERS=y
CONFIG_USB_USBNET=y
CONFIG_USB_NET_AX8817X=y
CONFIG_USB_NET_AX88179_178A=y
CONFIG_USB_NET_CDCETHER=y
CONFIG_USB_NET_CDC_NCM=y
CONFIG_USB_RTL8152=y
CONFIG_USB_RTL8153_ECM=y
CONFIG_MII=y
```

### 2.2 Automatic Hot-Plug DHCP (`/etc/udev/rules.d/99-eth.rules`)
```udev
ACTION=="add", SUBSYSTEM=="net", KERNEL=="eth*", RUN+="/sbin/udhcpc -b -i %k"
```

---

## 3. Solving the OTG Cold-Boot Hang (`<smdk2416_hsudc_gpio_init>`)

### 3.1 Symptom
Hot-plugging the OTG hub after boot worked flawlessly, but **cold-booting with the OTG hub already plugged in (`GPF6 == 0`)** froze the system at the console message `<smdk2416_hsudc_gpio_init>`.

### 3.2 Root Cause
1. During kernel `late_initcall(hpprime_otg_init)`, the kernel detected `GPF6 == 0`, enabled 5V VBUS boost, configured the shared USB PHY for Host mode, and bound `s3c2410-ohci`.
2. Seconds later, `/etc/init.d/rcS` executed `/etc/init.d/S04usb start`, which unconditionally ran `echo "s3c-hsudc" > /sys/kernel/config/usb_gadget/g1/UDC`.
3. Binding `s3c-hsudc` invoked `s3c_hsudc_start()` -> `s3c_hsudc_init_phy()` (resetting the active Host PHY) followed by `smdk2416_hsudc_gpio_init()` (which saw `GPF6 == 0` and disabled `UCLKCON_FUNC_CLKEN` right before `s3c_hsudc_reconfig()` enabled `IRQ_USBD`), locking up the shared USB PHY and interrupt controller.

### 3.3 Solution ([rootfs-overlay/etc/init.d/S04usb](../rootfs-overlay/etc/init.d/S04usb))
`S04usb` now inspects physical `GPFDAT (0x56000054)` Bit 6 (`GPF6`) before touching ConfigFS UDC:
- If **`ID == 0` (OTG Host cable connected)**: `S04usb` skips `s3c-hsudc` UDC binding completely, preserving the active OHCI Host bus.
- If **`ID == 1` (Device mode / PC cable)**: `S04usb` binds `s3c-hsudc` and brings up `usb0` (`192.168.66.1`).

