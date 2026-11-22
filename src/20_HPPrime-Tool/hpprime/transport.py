"""SG_IO structure, constants, READ_10 CDB builder and sysfs-only device detection.

Copied from 30_TEST/Linux-For-HPPrime-V3/tools/usbtool_v3.py so this tool has no
dependency on that file. Only the pieces used here are copied.
"""

import ctypes
import struct
from pathlib import Path

# Linux SG_IO constants
SG_IO = 0x2285
SG_DXFER_NONE = -1
SG_DXFER_TO_DEV = -2
SG_DXFER_FROM_DEV = -3

SCSI_OP_READ_10 = 0x28
SCSI_OP_WRITE_10 = 0x2A

# Vendor control requests that the recovery firmware interprets. Never sent by this tool.
SPECIAL_CONTROL_LBA = 0xAA
UNLOCK_SECTOR_COUNT = 30
LOCK_SECTOR_COUNT = 31

HP_PRIME_VID = "03f0"
HP_PRIME_PIDS = ("0441", "1541")
HP_PRIME_USB_IDS = frozenset(f"{HP_PRIME_VID}:{pid}" for pid in HP_PRIME_PIDS)
BLKGETSIZE64 = 0x80081272


class SgIoHdr(ctypes.Structure):
    """Linux `sg_io_hdr_t` structure for `ioctl(fd, SG_IO, ...)`."""

    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


def build_scsi_rw10_cdb(opcode: int, lba: int, sectors: int) -> bytes:
    """Build a 10-byte SCSI READ_10 (0x28) or WRITE_10 (0x2A) CDB (Big-Endian LBA & length)."""
    if opcode not in (SCSI_OP_READ_10, SCSI_OP_WRITE_10):
        raise ValueError(f"Unsupported SCSI RW10 opcode: 0x{opcode:02X}")
    if not (0 <= lba <= 0xFFFFFFFF):
        raise ValueError(f"LBA out of 32-bit range: {lba}")
    if not (0 < sectors <= 0xFFFF):
        raise ValueError(f"Sector count out of 16-bit range: {sectors}")
    return struct.pack(">BBIBHB", opcode, 0, lba, 0, sectors, 0)


def check_sg_result(result: int, header: SgIoHdr) -> bool:
    """True only when the ioctl and every completion field report success."""
    return not (result != 0 or header.status or header.host_status or header.driver_status
                or header.resid or header.info & 1)


def detect_hp_prime_usb(sys_usb: Path = Path("/sys/bus/usb/devices")) -> list[dict]:
    """Scan sysfs (read-only) for HP Prime USB devices. Sends no USB commands."""
    found: list[dict] = []
    if not sys_usb.exists():
        return found
    for dev_dir in sorted(sys_usb.iterdir()):
        vid_file = dev_dir / "idVendor"
        pid_file = dev_dir / "idProduct"
        if not (vid_file.exists() and pid_file.exists()):
            continue
        vid = vid_file.read_text(errors="ignore").strip().lower()
        pid = pid_file.read_text(errors="ignore").strip().lower()
        if vid != HP_PRIME_VID or pid not in HP_PRIME_PIDS:
            continue
        product = (dev_dir / "product").read_text(errors="ignore").strip() if (dev_dir / "product").exists() else ""
        manufacturer = ((dev_dir / "manufacturer").read_text(errors="ignore").strip()
                        if (dev_dir / "manufacturer").exists() else "")
        interfaces = []
        block_devs = []
        for sub in sorted(dev_dir.glob(f"{dev_dir.name}:*")):
            cls_file = sub / "bInterfaceClass"
            if cls_file.exists():
                interfaces.append(cls_file.read_text(errors="ignore").strip().lower())
            for blk in sub.rglob("block/*"):
                if blk.is_dir():
                    block_devs.append(f"/dev/{blk.name}")
        if "08" in interfaces:
            mode = "RECOVERY_MSC (BXCBOOT0 USB Mass Storage - Ready for SG_IO)"
        elif "03" in interfaces:
            mode = "NORMAL_HID (Calculator OS Running - Not in Recovery Mode)"
        else:
            mode = f"UNKNOWN (Interfaces: {interfaces})"
        found.append({
            "sysfs_node": str(dev_dir),
            "usb_id": f"{vid}:{pid}",
            "manufacturer": manufacturer,
            "product": product,
            "interfaces": interfaces,
            "mode": mode,
            "block_devices": block_devs,
        })
    return found
