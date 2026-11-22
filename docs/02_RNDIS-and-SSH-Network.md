# 02. USB RNDIS Gadget & SSH/SCP Network Environment

- **Target Device**: HP Prime G1 (`G8X92AA` Rev C)
- **Related Files**:
  - [rootfs-overlay/etc/init.d/S04usb](../rootfs-overlay/etc/init.d/S04usb)
  - [scripts/00_env.sh](../scripts/00_env.sh)
  - [scripts/ssh_lan.sh](../scripts/ssh_lan.sh)

---

## 1. Dual Network Paths to the Calculator

The calculator supports two network interfaces depending on the physical Micro-USB connection:

| Mode | Interface | Calculator IP | Physical Connection | Service Daemon |
| :--- | :---: | :---: | :--- | :--- |
| **USB Device Mode (RNDIS)** | `usb0` | `192.168.66.1/24` | Standard Micro-USB cable connected directly to Host PC (`ID=1`) | `s3c-hsudc` ConfigFS RNDIS + `dnsmasq` (assigns `192.168.66.10` to PC) |
| **USB OTG Host Mode (LAN)** | `eth0` | `192.168.123.165/24` (DHCP) | USB-OTG adapter + USB Hub + Realtek RTL8153 USB-to-Ethernet adapter (`ID=0`) | `s3c2410-ohci` + `r8152` + `udhcpc` via `/etc/udev/rules.d/99-eth.rules` |

---

## 2. Dropbear SSH & Passwordless Key Authentication

The calculator runs **Dropbear SSH Server v2022.83** on TCP port 22 (`/etc/init.d/S50dropbear`).

- **Root Password**: `root`
- **Persistent Authorized Keys**: Stored on writable JFFS2 flash at `/root/.ssh/authorized_keys` (`chmod 700 /root/.ssh`, `chmod 600 /root/.ssh/authorized_keys`).
- **SSH Config Alias (`~/.ssh/config` on Host PC)**:
  ```sshconfig
  Host prime
      HostName 192.168.123.165
      User root
      IdentityFile ~/.ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /dev/null
  ```

---

## 3. Mandatory `scp -O` Flag for File Transfers

Dropbear on the calculator does **not** include the `sftp-server` binary. Modern OpenSSH `scp` (OpenSSH 9.0+) defaults to the SFTP protocol and will fail unless the legacy SCP protocol flag **`-O`** is specified:

```bash
# Upload a file from Host PC to Calculator
scp -O my_binary root@192.168.123.165:/usr/bin/

# Download a file from Calculator to Host PC
scp -O root@192.168.123.165:/var/log/Xorg.0.0.log ./
```

