"""Offline write-plan checker for recovery-mode WRITE_10.

Model: reference V16 static analysis (S/S1_write10-mapping.md).
The real on-device table is unverified.  This module only computes and
judges; it never talks to a device.
"""

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

DEFAULT_CHUNK_SECTORS = BLOCK_SECTORS            # one WRITE_10 per block
MAPPING_NOTE = "model: reference V16 static analysis; real table unverified"


def build_table(bad_blocks=()):
    """Return logical-block -> physical-block list (length TOTAL_BLOCKS).

    Reference V16 0x7568-0x76dc: the reserved area is the first 0x40000 bytes
    (blocks 0-1) and grows by one block for each bad block found inside it.
    After it, good blocks are assigned in order.  Unfilled entries stay 0,
    which on the device means Boot0 block 0.
    """
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
    """Number of filled table entries (device B+0x16, reported capacity)."""
    return sum(1 for pb in table if pb not in RESERVED_BLOCKS)


def lba_to_physical(lba, table):
    """Map an LBA to (physical_block, page, may_hit_boot0).

    may_hit_boot0 is True when the LBA is beyond capacity or its table
    entry is outside / unfilled, i.e. the device could land in block 0.
    """
    if lba < 0:
        raise ValueError("negative LBA")
    lb = lba >> 8
    page = (lba & 0xFF) >> 2
    if lba >= LBA_LIMIT or lb >= len(table) or lb >= good_block_count(table):
        return 0, page, True
    pb = table[lb]
    return pb, page, pb in RESERVED_BLOCKS


# Kernel MTD partitions (mach-hpprime.c 186-216), physical NAND bytes [start, end).
MIB = 1024 * 1024
MTD_PARTITIONS = (
    ("mtd0", "HP Boot Code", 0, 256 * 1024),
    ("mtd1", "Linux Bootloader", 256 * 1024, 768 * 1024),
    ("mtd2", "kernel", 1 * MIB, 9 * MIB),
    ("mtd3", "rootfs", 9 * MIB, 63 * MIB),
    ("mtd4", "data", 63 * MIB, 175 * MIB),
    ("mtd5", "swap", 175 * MIB, 239 * MIB),
)


def mtd_partitions_for_blocks(physical_blocks):
    """Names of MTD partitions touched by any of the given physical blocks."""
    names = []
    for name, label, start, end in MTD_PARTITIONS:
        for pb in physical_blocks:
            if pb * BLOCK_SIZE < end and start < (pb + 1) * BLOCK_SIZE:
                names.append("%s (%s)" % (name, label))
                break
    return names


def _padded_sectors(size):
    """Sectors written for `size` bytes, rounded up to a whole page (0xFF fill assumed)."""
    sectors = (size + SECTOR_SIZE - 1) // SECTOR_SIZE
    return (sectors + PAGE_SECTORS - 1) // PAGE_SECTORS * PAGE_SECTORS


def plan_image(image, table, write_limit=None):
    """Compute and judge one image; raise ValueError with the reason on refusal."""
    if write_limit is None:
        from .vendor import WRITE_LIMIT as write_limit
    name = getattr(image.path, "name", str(image.path))
    if image.kind == "boot0":
        raise ValueError("%s: refused (e): Boot0 image kind" % name)
    if image.usb_offset % BLOCK_SIZE:
        raise ValueError("%s: refused (c): USB offset 0x%x is not 128 KiB block aligned; "
                         "first block would be programmed without erase" % (name, image.usb_offset))
    first_lba = image.usb_offset // SECTOR_SIZE
    sectors = _padded_sectors(len(image.data))
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
        "usb_offset": image.usb_offset,
        "lba_first": first_lba,
        "lba_last": last_lba,
        "sectors": sectors,
        "logical_blocks": [logical[0], logical[-1]],
        "physical_blocks": [min(physical), max(physical)],
        "physical_block_list": physical,
        "physical_contiguous": physical == list(range(physical[0], physical[0] + len(physical))),
        "mtd_partitions": mtd_partitions_for_blocks(physical),
    }


def plan_writes(images, bad_blocks=()):
    """Judge a vendor.read_ini image list.  Returns one dict per image or raises ValueError."""
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
    from .transport import LOCK_SECTOR_COUNT, SPECIAL_CONTROL_LBA, UNLOCK_SECTOR_COUNT
    if lba == SPECIAL_CONTROL_LBA or sectors in (UNLOCK_SECTOR_COUNT, LOCK_SECTOR_COUNT):
        raise ValueError("chunk refused: vendor control LBA 170 or 30/31-sector length")
    if sectors <= 0 or lba % PAGE_SECTORS or sectors % PAGE_SECTORS:
        raise ValueError("chunk refused: LBA %d / %d sectors not page (4-sector) aligned" % (lba, sectors))
    if lba // BLOCK_SECTORS != (lba + sectors - 1) // BLOCK_SECTORS:
        raise ValueError("chunk refused: LBA %d + %d sectors crosses a block boundary" % (lba, sectors))


def transfer_chunks(image, max_sectors):
    """WRITE_10 pieces (lba, sectors) for one image.

    Every piece stays inside one 128 KiB block, the first piece of each block
    starts at page 0 (so the firmware erases that block), and every piece is
    page aligned.  The last page is assumed padded with 0xFF.
    """
    if image.kind == "boot0":
        raise ValueError("refused (e): Boot0 image kind")
    if image.usb_offset % BLOCK_SIZE:
        raise ValueError("refused (c): USB offset is not 128 KiB block aligned")
    step = int(max_sectors) // PAGE_SECTORS * PAGE_SECTORS
    if step <= 0:
        raise ValueError("max_sectors must be at least one page (4 sectors)")
    lba = image.usb_offset // SECTOR_SIZE
    end = lba + _padded_sectors(len(image.data))
    chunks = []
    while lba < end:
        block_end = (lba // BLOCK_SECTORS + 1) * BLOCK_SECTORS
        count = min(step, block_end - lba, end - lba)
        check_chunk(lba, count)
        chunks.append((lba, count))
        lba += count
    return chunks


def parse_bad_blocks(text):
    """Parse "N,N,..." (decimal or 0x-hex) into a sorted list of block numbers."""
    if not text:
        return []
    return sorted({int(item, 0) for item in text.split(",") if item.strip()})


def summarize(images, bad_blocks=(), max_sectors=DEFAULT_CHUNK_SECTORS):
    """vendor summaries plus a "layout" entry per image (used by `cli.py plan`)."""
    plans = plan_writes(images, bad_blocks)
    result = []
    for image, plan in zip(images, plans):
        chunks = transfer_chunks(image, max_sectors)
        entry = image.summary()
        entry["mapping"] = MAPPING_NOTE
        entry["bad_blocks"] = list(bad_blocks)
        entry["layout"] = {
            "lba": [plan["lba_first"], plan["lba_last"]],
            "logical_blocks": plan["logical_blocks"],
            "physical_blocks": plan["physical_blocks"],
            "physical_contiguous": plan["physical_contiguous"],
            "mtd_partitions": plan["mtd_partitions"],
            "chunks": len(chunks),
            "chunk_max_sectors": max_sectors,
        }
        result.append(entry)
    return result
