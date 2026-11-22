#!/usr/bin/env python3
"""
HP Prime G1 (S3C2416, Hynix H27U2G8F2CTR-BC 256 MiB NAND) Layout & Address Mapping.

Reconciles the address spaces across:
  1. BXCBOOT0.BIN Recovery Mode USB Mass Storage (base shift = +0x00040000 / Block 2)
  2. usbtool.ini UpdateItem byte offsets and 512-byte LBAs
  3. hpprimev2/v3_linux_loader (boot.c) NAND page/block numbers
  4. Linux Kernel MTD partition table (arch/arm/mach-s3c/mach-hpprime.c)
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

# Physical NAND geometry (Hynix H27U2G8F2CTR-BC: 2 Gigabit = 256 MiB)
NAND_PAGE_SIZE = 2048
NAND_OOB_SIZE = 64
NAND_PAGES_PER_BLOCK = 64
NAND_BLOCK_SIZE = NAND_PAGE_SIZE * NAND_PAGES_PER_BLOCK  # 131,072 bytes (128 KiB, 0x20000)
NAND_TOTAL_BLOCKS = 2048
NAND_TOTAL_SIZE = NAND_BLOCK_SIZE * NAND_TOTAL_BLOCKS    # 268,435,456 bytes (256 MiB, 0x10000000)

# USB Mass Storage geometry exposed by BXCBOOT0.BIN in Recovery Mode
USB_SECTOR_SIZE = 512
# BXCBOOT0V16.bin @ 0x0cb3c sets *(uint32_t*)0x30d6f614 = 0x00040000 (256 KiB = Block 2)
RECOVERY_MSC_BASE_OFFSET = 0x00040000
RECOVERY_MSC_BASE_BLOCK = RECOVERY_MSC_BASE_OFFSET // NAND_BLOCK_SIZE  # Block 2

# BOOT1.ROM specifications (ldr_ld.script & boot.c head_info)
BOOT1_ROM_SIZE = 512 * 1024                # 524,288 bytes (0x80000 = 4 NAND blocks)
BOOT1_ENTRY_ADDR = 0x30000020
BOOT1_MAGIC_V5J = b"V5J\x00"               # Offset 0x10..0x13
BOOT1_MAGIC_SOC = b"2416"                  # Offset 0x18..0x1B
BOOT1_HEADER_SIZE = 32
BOOT1_FILL_WORD = 0xDEADBEEF
BOOT1_TRAILER_BYTE = 0xAA

# LINUX.DAT specifications (mkimg.sh & usbtool.ini)
LINUX_DAT_USB_OFFSET = 0x000C0000          # 768 KiB in USB MSC -> 1 MiB physical (Block 8)
LINUX_DAT_START_LBA = LINUX_DAT_USB_OFFSET // USB_SECTOR_SIZE  # LBA 1536
LINUX_DAT_KERNEL_OFFSET = 0
LINUX_DAT_KERNEL_MAX_SIZE = 8 * 1024 * 1024   # 8 MiB (64 NAND blocks: Block 8..71)
LINUX_DAT_ROOTFS_OFFSET = 8 * 1024 * 1024     # +8 MiB inside LINUX.DAT (Physical 9 MiB, Block 72)
LINUX_DAT_ROOTFS_MAX_SIZE = 54 * 1024 * 1024  # 54 MiB (432 NAND blocks: Block 72..503)
LINUX_DAT_TOTAL_SIZE = 62 * 1024 * 1024       # 62 MiB total image container (8 MiB kernel + 54 MiB rootfs)

# ARM Linux zImage & JFFS2 signatures
ZIMAGE_MAGIC = 0x016F2818
ZIMAGE_MAGIC_OFFSET = 0x24
JFFS2_MAGIC_LE = 0x1985


@dataclass(frozen=True)
class MtdPartition:
    index: int
    name: str
    phys_offset: int
    size: int
    description: str

    @property
    def phys_end(self) -> int:
        return self.phys_offset + self.size

    @property
    def start_block(self) -> int:
        return self.phys_offset // NAND_BLOCK_SIZE

    @property
    def end_block_inclusive(self) -> int:
        return (self.phys_end // NAND_BLOCK_SIZE) - 1

    @property
    def block_count(self) -> int:
        return self.size // NAND_BLOCK_SIZE

    @property
    def usb_offset(self) -> Optional[int]:
        if self.phys_offset < RECOVERY_MSC_BASE_OFFSET:
            return None
        return self.phys_offset - RECOVERY_MSC_BASE_OFFSET

    @property
    def start_lba(self) -> Optional[int]:
        u_ofs = self.usb_offset
        return None if u_ofs is None else (u_ofs // USB_SECTOR_SIZE)


# Matches Kernel-6.1.35-HP-Prime-V2_G1/arch/arm/mach-s3c/mach-hpprime.c
HPPRIME_MTD_PARTITIONS: Tuple[MtdPartition, ...] = (
    MtdPartition(
        index=0,
        name="Prime Boot0",
        phys_offset=0x00000000,
        size=256 * 1024,
        description="First-stage bootloader & Recovery Mode (BXCBOOT0.BIN, hidden in normal MSC)",
    ),
    MtdPartition(
        index=1,
        name="Linux Bootloader",
        phys_offset=0x00040000,
        size=512 * 1024,
        description="Second-stage bootloader (BOOT1.ROM, USB offset 0x00000000 / LBA 0)",
    ),
    MtdPartition(
        index=2,
        name="kernel",
        phys_offset=0x00100000,
        size=8 * 1024 * 1024,
        description="Linux Kernel zImage (LINUX.DAT +0 MiB, USB offset 0x000C0000 / LBA 1536)",
    ),
    MtdPartition(
        index=3,
        name="rootfs",
        phys_offset=0x00900000,
        size=54 * 1024 * 1024,
        description="JFFS2 Root Filesystem (LINUX.DAT +8 MiB, USB offset 0x008C0000 / LBA 17920)",
    ),
    MtdPartition(
        index=4,
        name="prime_calc",
        phys_offset=0x03F00000,
        size=(256 - 63) * 1024 * 1024,
        description="Remaining NAND flash (193 MiB)",
    ),
)


def usb_offset_to_phys_offset(usb_offset: int) -> int:
    """Convert Recovery Mode USB Mass Storage byte offset to physical NAND byte offset."""
    if usb_offset < 0:
        raise ValueError(f"usb_offset must be non-negative, got {usb_offset}")
    phys = usb_offset + RECOVERY_MSC_BASE_OFFSET
    if phys >= NAND_TOTAL_SIZE:
        raise ValueError(
            f"usb_offset 0x{usb_offset:08X} exceeds 256 MiB NAND (phys 0x{phys:08X})"
        )
    return phys


def phys_offset_to_usb_offset(phys_offset: int) -> int:
    """Convert physical NAND byte offset (>= 0x40000) to Recovery Mode USB byte offset."""
    if phys_offset < RECOVERY_MSC_BASE_OFFSET:
        raise ValueError(
            f"Physical offset 0x{phys_offset:08X} is inside Boot0 (< 0x{RECOVERY_MSC_BASE_OFFSET:08X}) "
            "and is not accessible in normal Recovery Mode."
        )
    if phys_offset >= NAND_TOTAL_SIZE:
        raise ValueError(f"Physical offset 0x{phys_offset:08X} exceeds 256 MiB NAND")
    return phys_offset - RECOVERY_MSC_BASE_OFFSET


def usb_offset_to_lba(usb_offset: int) -> int:
    """Convert Recovery Mode USB byte offset to 512-byte sector LBA."""
    if usb_offset < 0 or (usb_offset % USB_SECTOR_SIZE) != 0:
        raise ValueError(
            f"usb_offset 0x{usb_offset:08X} is not aligned to {USB_SECTOR_SIZE}-byte sector"
        )
    return usb_offset // USB_SECTOR_SIZE


def lba_to_usb_offset(lba: int) -> int:
    """Convert 512-byte sector LBA to Recovery Mode USB byte offset."""
    if lba < 0:
        raise ValueError(f"LBA must be non-negative, got {lba}")
    return lba * USB_SECTOR_SIZE


def lba_to_phys_offset(lba: int) -> int:
    """Convert 512-byte sector LBA in Recovery Mode directly to physical NAND byte offset."""
    return usb_offset_to_phys_offset(lba_to_usb_offset(lba))


def phys_offset_to_block(phys_offset: int) -> int:
    """Return the physical 128 KiB NAND block number containing phys_offset."""
    if phys_offset < 0 or phys_offset >= NAND_TOTAL_SIZE:
        raise ValueError(f"Physical offset 0x{phys_offset:08X} out of range")
    return phys_offset // NAND_BLOCK_SIZE


def block_to_phys_offset(block: int) -> int:
    """Return physical NAND byte offset for the start of a 128 KiB block."""
    if block < 0 or block >= NAND_TOTAL_BLOCKS:
        raise ValueError(f"Block {block} out of range (0..{NAND_TOTAL_BLOCKS - 1})")
    return block * NAND_BLOCK_SIZE


def block_to_first_page(block: int) -> int:
    """Return 2048-byte NAND page index for the start of a 128 KiB block."""
    if block < 0 or block >= NAND_TOTAL_BLOCKS:
        raise ValueError(f"Block {block} out of range (0..{NAND_TOTAL_BLOCKS - 1})")
    return block * NAND_PAGES_PER_BLOCK


def find_partitions_for_phys_range(phys_start: int, size: int) -> List[MtdPartition]:
    """Return all MTD partitions overlapping [phys_start, phys_start + size)."""
    phys_end = phys_start + size
    overlapping: List[MtdPartition] = []
    for part in HPPRIME_MTD_PARTITIONS:
        if max(phys_start, part.phys_offset) < min(phys_end, part.phys_end):
            overlapping.append(part)
    return overlapping

