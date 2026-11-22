"""Offline write-plan checker for recovery-mode WRITE_10 (V3 copy).

Source: copied from 30_TEST/20_HPPrime-Tool/hpprime/layout.py (plan_writes,
transfer_chunks, check_chunk; 20 tests there) and the Boot0 / WRITE_LIMIT rules
of 30_TEST/20_HPPrime-Tool/hpprime/vendor.py (load_image), 2026-11-17.
Copied so V3 does not import the 20_HPPrime-Tool package. Keep both in step.

Model: reference V16 static analysis (30_TEST/30_Boot0-Path-Survey/S1_write10-mapping.md).
  physical block = table[LBA >> 8]; erase happens only for a WRITE_10 that
  starts at page 0 of a block; a command crossing a block boundary continues
  into the next block without erase; LBA >= 0x7FE00 lands in block 0 (Boot0).
The real on-device table is unverified. This module never talks to a device.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SECTOR_SIZE = 512
PAGE_SECTORS = 4
PAGE_SIZE = SECTOR_SIZE * PAGE_SECTORS            # 2 KiB
BLOCK_PAGES = 64
BLOCK_SECTORS = PAGE_SECTORS * BLOCK_PAGES        # 256
BLOCK_SIZE = SECTOR_SIZE * BLOCK_SECTORS          # 128 KiB
TOTAL_BLOCKS = 2048
RESERVED_BLOCKS = (0, 1)                          # Boot0
CAPACITY_BLOCKS = 2046
LBA_LIMIT = CAPACITY_BLOCKS * BLOCK_SECTORS       # 0x7FE00
WRITE_LIMIT = 0x3EC0000                           # vendor.py: protected user-data boundary

MAX_CHUNK_SECTORS = BLOCK_SECTORS
# Real-device result 2026-11-17 22:25: Linux usb-storage caps this device at max_hw_sectors_kb=120,
# so a 128 KiB (256-sector) WRITE_10 is refused by the block layer with EIO before reaching the
# calculator. 64 sectors (32 KiB) divides the 128 KiB block exactly, stays under the cap, and keeps
# the rules: no chunk crosses a block, the first chunk of a block starts at page 0, 4-sector aligned.
DEFAULT_CHUNK_SECTORS = 64
HOST_MAX_SECTORS_KB = 120
MAPPING_NOTE = "model: reference V16 static analysis; real table unverified"

# Vendor control request shape (LBA 170, 30/31 sectors) that a data chunk must never take.
SPECIAL_CONTROL_LBA = 0xAA
UNLOCK_SECTOR_COUNT = 30
LOCK_SECTOR_COUNT = 31

KNOWN_BOOT0 = {
    "v13": "a882bb7de810708283921f73f52c25e99aa472a05bd05546e0e7cec4bbeb87e1",
    "v16": "87d04e01c59d7bf912eef38a644314a643a0c5921cb69c2c1f24b61cd0dc6248",
}


@dataclass(frozen=True)
class PlanImage:
    """One image to write (path, bytes, USB offset, kind "nand" or "boot0")."""

    path: Path
    data: bytes
    usb_offset: int
    kind: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def classify_image(path: Path, data: bytes) -> str:
    """vendor.load_image rule: Boot0 by file name BXCBOOT0[Vnn].BIN or known Boot0 sha256."""
    named_boot0 = re.fullmatch(r"BXCBOOT0(?:V\d+)?\.BIN", Path(path).name, re.IGNORECASE) is not None
    known_boot0 = hashlib.sha256(data).hexdigest() in KNOWN_BOOT0.values()
    return "boot0" if named_boot0 or known_boot0 else "nand"


def load_plan_image(path: Path, usb_offset: int) -> PlanImage:
    path = Path(path)
    if usb_offset < 0 or usb_offset % SECTOR_SIZE:
        raise ValueError(f"{path.name}: USB offset must be non-negative and 512-byte aligned")
    if not path.is_file():
        raise ValueError(f"Image is not a regular file: {path}")
    data = path.read_bytes()
    return PlanImage(path.resolve(), data, usb_offset, classify_image(path, data))


def build_table(bad_blocks=()):
    """Logical-block -> physical-block list (reference V16 0x7568-0x76dc). Unfilled entries are 0."""
    bad = set(int(b) for b in bad_blocks)
    for b in bad:
        if not 0 <= b < TOTAL_BLOCKS:
            raise ValueError("bad block out of range: %d" % b)
    reserved_end = len(RESERVED_BLOCKS)
    pb = 0
    while pb < reserved_end:
        if pb in bad:
            reserved_end += 1
        pb += 1
    table = [0] * TOTAL_BLOCKS
    lb = 0
    for pb in range(reserved_end, TOTAL_BLOCKS):
        if pb in bad:
            continue
        table[lb] = pb
        lb += 1
    return table


def good_block_count(table):
    return sum(1 for pb in table if pb not in RESERVED_BLOCKS)


def lba_to_physical(lba, table):
    """(physical_block, page, may_hit_boot0) for an LBA."""
    if lba < 0:
        raise ValueError("negative LBA")
    lb = lba >> 8
    page = (lba & 0xFF) >> 2
    if lba >= LBA_LIMIT or lb >= len(table) or lb >= good_block_count(table):
        return 0, page, True
    pb = table[lb]
    return pb, page, pb in RESERVED_BLOCKS


def padded_sectors(size):
    """Sectors written for `size` bytes, rounded up to a whole page (0xFF fill)."""
    sectors = (size + SECTOR_SIZE - 1) // SECTOR_SIZE
    return (sectors + PAGE_SECTORS - 1) // PAGE_SECTORS * PAGE_SECTORS


def plan_image(image, table, write_limit=WRITE_LIMIT):
    """Judge one image; raise ValueError with the reason on refusal."""
    name = Path(image.path).name
    if image.kind == "boot0":
        raise ValueError("%s: refused (e): Boot0 image kind" % name)
    if image.usb_offset % BLOCK_SIZE:
        raise ValueError("%s: refused (c): USB offset 0x%x is not 128 KiB block aligned; "
                         "first block would be programmed without erase" % (name, image.usb_offset))
    first_lba = image.usb_offset // SECTOR_SIZE
    sectors = padded_sectors(len(image.data))
    if sectors == 0:
        raise ValueError("%s: refused: empty image" % name)
    last_lba = first_lba + sectors - 1
    end_bytes = (last_lba + 1) * SECTOR_SIZE
    if last_lba >= LBA_LIMIT:
        raise ValueError("%s: refused (b): LBA 0x%x >= 0x%x (beyond 2046-block capacity, may reach Boot0)"
                         % (name, last_lba, LBA_LIMIT))
    if end_bytes > write_limit:
        raise ValueError("%s: refused (b): end 0x%x > WRITE_LIMIT 0x%x" % (name, end_bytes, write_limit))
    logical = range(first_lba >> 8, (last_lba >> 8) + 1)
    physical = []
    for lb in logical:
        pb, _, boot0 = lba_to_physical(lb * BLOCK_SECTORS, table)
        if boot0 or pb in RESERVED_BLOCKS:
            raise ValueError("%s: refused (a): logical block %d maps to Boot0 block %d" % (name, lb, pb))
        physical.append(pb)
    return {
        "path": str(image.path),
        "size": len(image.data),
        "sha256": image.sha256 if hasattr(image, "sha256") else None,
        "usb_offset": image.usb_offset,
        "lba_first": first_lba,
        "lba_last": last_lba,
        "sectors": sectors,
        "logical_blocks": [logical[0], logical[-1]],
        "physical_blocks": [min(physical), max(physical)],
        "physical_block_list": physical,
        "physical_contiguous": physical == list(range(physical[0], physical[0] + len(physical))),
    }


def plan_writes(images, bad_blocks=()):
    """Judge an image list. Returns one dict per image or raises ValueError."""
    images = list(images)
    if not images:
        raise ValueError("refused: no images")
    table = build_table(bad_blocks)
    plans = [plan_image(image, table) for image in images]
    ordered = sorted(plans, key=lambda plan: plan["lba_first"])
    for previous, following in zip(ordered, ordered[1:]):
        if previous["logical_blocks"][1] >= following["logical_blocks"][0]:
            raise ValueError("refused (d): %s and %s share logical block %d"
                             % (previous["path"], following["path"], following["logical_blocks"][0]))
    seen = {}
    for plan in plans:
        for pb in plan["physical_block_list"]:
            if pb in seen:
                raise ValueError("refused (d): %s and %s share physical block %d" % (seen[pb], plan["path"], pb))
            seen[pb] = plan["path"]
    return plans


def check_chunk(lba, sectors):
    """Refuse a WRITE_10 piece that breaks the block/page rules or looks like a control request."""
    if lba == SPECIAL_CONTROL_LBA or sectors in (UNLOCK_SECTOR_COUNT, LOCK_SECTOR_COUNT):
        raise ValueError("chunk refused: vendor control LBA 170 or 30/31-sector length")
    if sectors <= 0 or lba % PAGE_SECTORS or sectors % PAGE_SECTORS:
        raise ValueError("chunk refused: LBA %d / %d sectors not page (4-sector) aligned" % (lba, sectors))
    if sectors > MAX_CHUNK_SECTORS:
        raise ValueError("chunk refused: %d sectors > %d" % (sectors, MAX_CHUNK_SECTORS))
    if lba // BLOCK_SECTORS != (lba + sectors - 1) // BLOCK_SECTORS:
        raise ValueError("chunk refused: LBA %d + %d sectors crosses a block boundary" % (lba, sectors))
    if lba + sectors > LBA_LIMIT:
        raise ValueError("chunk refused: LBA %d + %d sectors beyond capacity" % (lba, sectors))


def transfer_chunks(image, max_sectors=DEFAULT_CHUNK_SECTORS):
    """WRITE_10 pieces (lba, sectors) for one image.

    Every piece stays inside one 128 KiB block, the first piece of each block
    starts at page 0 (so the firmware erases that block), and every piece is
    page aligned. The last page is padded with 0xFF (see chunk_payload).
    """
    if image.kind == "boot0":
        raise ValueError("refused (e): Boot0 image kind")
    if image.usb_offset % BLOCK_SIZE:
        raise ValueError("refused (c): USB offset is not 128 KiB block aligned")
    if int(max_sectors) > MAX_CHUNK_SECTORS:
        raise ValueError("max_sectors must be at most %d (128 KiB)" % MAX_CHUNK_SECTORS)
    step = int(max_sectors) // PAGE_SECTORS * PAGE_SECTORS
    if step <= 0:
        raise ValueError("max_sectors must be at least one page (4 sectors)")
    lba = image.usb_offset // SECTOR_SIZE
    end = lba + padded_sectors(len(image.data))
    chunks = []
    while lba < end:
        block_end = (lba // BLOCK_SECTORS + 1) * BLOCK_SECTORS
        count = min(step, block_end - lba, end - lba)
        if count in (UNLOCK_SECTOR_COUNT, LOCK_SECTOR_COUNT):
            count -= PAGE_SECTORS          # never emit a 30/31-sector piece (unreachable when 4-aligned)
        check_chunk(lba, count)
        chunks.append((lba, count))
        lba += count
    return chunks


def chunk_payload(image, lba, sectors):
    """Bytes for one chunk, 0xFF-filled past the end of the image."""
    start = lba * SECTOR_SIZE - image.usb_offset
    size = sectors * SECTOR_SIZE
    piece = image.data[start:start + size]
    return piece + b"\xFF" * (size - len(piece))


def parse_bad_blocks(text):
    """Parse "N,N,..." (decimal or 0x-hex) into a sorted list of block numbers."""
    if not text:
        return []
    return sorted({int(item, 0) for item in text.split(",") if item.strip()})
