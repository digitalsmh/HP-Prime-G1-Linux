"""Unified sysfs preflight for an HP Prime block device. Never opens the device.

Merges the two former `inspect_device` functions (20_ALL-Backup/backup.py and
50_Recorvery/usbtool.py). All checks of both are kept: whole disk only, HP USB
VID/PID allowlist, mass-storage interface, allowed capacity, 512-byte sectors,
sysfs identity, not mounted, no holders, not active swap.
"""

import os
import stat
from pathlib import Path

from .transport import HP_PRIME_USB_IDS


def block_number(path: Path) -> int:
    information = path.stat()
    if not stat.S_ISBLK(information.st_mode):
        raise ValueError("Source must be a whole block device, not a file, partition or sg node")
    return information.st_rdev


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def inspect_device(
    path: Path,
    sys_devices: Path = Path("/sys/dev/block"),
    mountinfo: Path = Path("/proc/self/mountinfo"),
    swaps: Path = Path("/proc/swaps"),
    *,
    capacities: frozenset[int] | set[int] | tuple[int, ...],
) -> dict:
    """Validate `path` through sysfs/procfs text only and return identifying metadata."""
    number = block_number(path)
    text_number = f"{os.major(number)}:{os.minor(number)}"
    node = (sys_devices / text_number).resolve(strict=True)
    if (node / "partition").exists():
        raise ValueError("A partition cannot be selected; use the whole disk")
    parents = (node, *node.parents)
    usb = next((parent for parent in parents if (parent / "idVendor").exists()), None)
    if usb is None:
        raise ValueError("Device is not attached through an identifiable USB device")
    usb_id = f"{(usb / 'idVendor').read_text().strip()}:{(usb / 'idProduct').read_text().strip()}".lower()
    if usb_id not in HP_PRIME_USB_IDS:
        raise ValueError("Device USB identity is not in the HP Prime allowlist")
    classes = [(parent / "bInterfaceClass").read_text().strip().lower()
               for parent in parents if (parent / "bInterfaceClass").exists()]
    if "08" not in classes:
        raise ValueError("Selected device is not on a USB mass-storage interface")
    capacity = int((node / "size").read_text()) * 512
    sector_size = int((node / "queue/logical_block_size").read_text())
    if capacity not in capacities or sector_size != 512:
        allowed = ", ".join(str(value) for value in sorted(capacities))
        raise ValueError(f"Unexpected capacity or logical sector size: found {capacity} / {sector_size}; "
                         f"allowed {allowed} / 512")
    members = [node, *(child for child in node.iterdir() if (child / "partition").exists())]
    numbers = {(member / "dev").read_text().strip() for member in members}
    if text_number not in numbers:
        raise ValueError("Device/sysfs identity changed")
    mounted = {line.split()[2] for line in mountinfo.read_text().splitlines() if line.strip()}
    if numbers & mounted:
        raise ValueError("Device or partition is mounted; refusing")
    for member in members:
        if any((member / "holders").iterdir()):
            raise ValueError("Device or partition has an active holder")
    for line in swaps.read_text().splitlines()[1:]:
        if not line.strip():
            continue
        information = Path(line.split()[0]).stat()
        if stat.S_ISBLK(information.st_mode):
            if f"{os.major(information.st_rdev)}:{os.minor(information.st_rdev)}" in numbers:
                raise ValueError("Device or partition is active swap")
    return {"kind": "device", "path": str(path.resolve()), "usb_id": usb_id,
            "sysfs_node": str(node), "device_number": number, "capacity": capacity,
            "usb_busnum": _read_optional(usb / "busnum"), "usb_devnum": _read_optional(usb / "devnum")}


def require_kernel_read_only(metadata: dict) -> None:
    """Refuse unless the disk and every partition carry the kernel read-only flag (BLKROSET)."""
    node = Path(metadata["sysfs_node"])
    related = [node, *(child for child in node.iterdir() if (child / "partition").exists())]
    for child in related:
        try:
            flag = (child / "ro").read_text().strip()
        except OSError as error:
            raise ValueError(f"Kernel read-only flag is unreadable for {child.name}") from error
        if flag != "1":
            raise ValueError(f"{child.name} is not kernel read-only; install the udev no-probe/read-only rule before connecting")
