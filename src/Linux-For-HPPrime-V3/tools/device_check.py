"""Sysfs preflight for the HP Prime G1 recovery-mode block device (V3 copy). Never opens the device.

Source: copied from 30_TEST/20_HPPrime-Tool/hpprime/device.py (inspect_device),
2026-11-17, so V3 does not import the 20_HPPrime-Tool package.
Narrowed for the V3 writer: only the recovery USB ID 03f0:0441 and only the
recovery capacity 268173312 bytes (2046 blocks + reserved, 512 B sectors) pass.
Checks: block device, whole disk (not partition), HP VID/recovery PID, USB
mass-storage interface, 512 B logical sector, capacity, sysfs identity,
not mounted, no holders, not active swap.
"""

import os
import stat
from pathlib import Path

RECOVERY_USB_IDS = frozenset({"03f0:0441"})
RECOVERY_CAPACITIES = frozenset({268173312})
SECTOR_SIZE = 512


def block_number(path: Path) -> int:
    information = Path(path).stat()
    if not stat.S_ISBLK(information.st_mode):
        raise ValueError("Target must be a whole block device, not a file, partition or sg node")
    return information.st_rdev


def _read_optional(path: Path):
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def inspect_device(
    path,
    sys_devices: Path = Path("/sys/dev/block"),
    mountinfo: Path = Path("/proc/self/mountinfo"),
    swaps: Path = Path("/proc/swaps"),
    *,
    usb_ids=RECOVERY_USB_IDS,
    capacities=RECOVERY_CAPACITIES,
) -> dict:
    """Validate `path` through sysfs/procfs text only and return identifying metadata."""
    path = Path(path)
    number = block_number(path)
    text_number = f"{os.major(number)}:{os.minor(number)}"
    node = (Path(sys_devices) / text_number).resolve(strict=True)
    if (node / "partition").exists():
        raise ValueError("A partition cannot be selected; use the whole disk")
    parents = (node, *node.parents)
    usb = next((parent for parent in parents if (parent / "idVendor").exists()), None)
    if usb is None:
        raise ValueError("Device is not attached through an identifiable USB device")
    usb_id = f"{(usb / 'idVendor').read_text().strip()}:{(usb / 'idProduct').read_text().strip()}".lower()
    if usb_id not in usb_ids:
        raise ValueError(f"Device USB identity {usb_id} is not the HP Prime recovery ID "
                         f"({', '.join(sorted(usb_ids))})")
    classes = [(parent / "bInterfaceClass").read_text().strip().lower()
               for parent in parents if (parent / "bInterfaceClass").exists()]
    if "08" not in classes:
        raise ValueError("Selected device is not on a USB mass-storage interface")
    capacity = int((node / "size").read_text()) * 512
    sector_size = int((node / "queue/logical_block_size").read_text())
    if capacity not in capacities or sector_size != SECTOR_SIZE:
        allowed = ", ".join(str(value) for value in sorted(capacities))
        raise ValueError(f"Unexpected capacity or logical sector size: found {capacity} / {sector_size}; "
                         f"allowed {allowed} / {SECTOR_SIZE}")
    members = [node, *(child for child in node.iterdir() if (child / "partition").exists())]
    numbers = {(member / "dev").read_text().strip() for member in members}
    if text_number not in numbers:
        raise ValueError("Device/sysfs identity changed")
    mounted = {line.split()[2] for line in Path(mountinfo).read_text().splitlines() if line.strip()}
    if numbers & mounted:
        raise ValueError("Device or partition is mounted; refusing")
    for member in members:
        holders = member / "holders"
        if holders.exists() and any(holders.iterdir()):
            raise ValueError("Device or partition has an active holder")
    for line in Path(swaps).read_text().splitlines()[1:]:
        if not line.strip():
            continue
        try:
            information = Path(line.split()[0]).stat()
        except OSError:
            continue
        if stat.S_ISBLK(information.st_mode):
            if f"{os.major(information.st_rdev)}:{os.minor(information.st_rdev)}" in numbers:
                raise ValueError("Device or partition is active swap")
    return {"kind": "device", "path": str(path.resolve()), "usb_id": usb_id,
            "sysfs_node": str(node), "device_number": number, "capacity": capacity,
            "sector_size": sector_size,
            "usb_busnum": _read_optional(usb / "busnum"), "usb_devnum": _read_optional(usb / "devnum")}
