#!/usr/bin/env python3
"""
HP Prime G1 Linux-For-HPPrime-V3 Image Builder & Inspector (mkimg_v3.py).

Replaces the legacy V2 `mkimg.sh` with:
  - Strict size & header validation for BOOT1.ROM, zImage, and rootfs.jffs2
  - Fast 0xFF-padded LINUX.DAT generation without `dd | tr` overhead
  - Image inspection (`inspect`) and extraction (`extract`) subcommands
"""

import argparse
import hashlib
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

# Allow running both as `python3 -m tools.mkimg_v3` and `python3 tools/mkimg_v3.py`
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.nand_layout import (
    BOOT1_ENTRY_ADDR,
    BOOT1_FILL_WORD,
    BOOT1_HEADER_SIZE,
    BOOT1_MAGIC_SOC,
    BOOT1_MAGIC_V5J,
    BOOT1_ROM_SIZE,
    BOOT1_TRAILER_BYTE,
    JFFS2_MAGIC_LE,
    LINUX_DAT_KERNEL_MAX_SIZE,
    LINUX_DAT_KERNEL_OFFSET,
    LINUX_DAT_ROOTFS_MAX_SIZE,
    LINUX_DAT_ROOTFS_OFFSET,
    LINUX_DAT_START_LBA,
    LINUX_DAT_TOTAL_SIZE,
    LINUX_DAT_USB_OFFSET,
    NAND_BLOCK_SIZE,
    ZIMAGE_MAGIC,
    ZIMAGE_MAGIC_OFFSET,
    phys_offset_to_block,
    usb_offset_to_phys_offset,
)


@dataclass
class Boot1Info:
    size: int
    entry_addr: int
    magic_v5j: bytes
    magic_soc: bytes
    trailer_byte: int
    payload_bytes: int
    sha256: str


@dataclass
class LinuxDatReport:
    output_path: Path
    total_size: int
    zimage_size: int
    zimage_sha256: str
    rootfs_size: int
    rootfs_sha256: str
    linux_dat_sha256: str


def _read_bytes(data_or_path: Union[bytes, Path, str]) -> bytes:
    if isinstance(data_or_path, bytes):
        return data_or_path
    return Path(data_or_path).read_bytes()


# S3b 2026-11-17: BOOT1 header layout = V2 boot.c lines 73..80 = stock PRIME_OS.ROM:
#   +0x00 entry 0x30000020, +0x04 0, +0x08 load size 0x100000,
#   +0x0C load address 0x30000000, +0x10 size 0x100000, +0x14 "V5J\0", +0x18 "2416", +0x1C 0
# Boot0 V16 0x65cc..0x65e8 requires +0x0C in [0x30000000, 0x40000000) and
# 0x6604..0x6664 requires +0x10 >= 0x100 and a multiple of the NAND block size.
# (The old V3 layout put "V5J" at +0x10 and left +0x08/+0x0C/+0x10 zero.)
BOOT1_OFF_ENTRY = 0x00
BOOT1_OFF_LOAD_SIZE = 0x08
BOOT1_OFF_LOAD_ADDR = 0x0C
BOOT1_OFF_SIZE = 0x10
BOOT1_OFF_MAGIC_V5J = 0x14
BOOT1_OFF_MAGIC_SOC = 0x18
BOOT1_LOAD_ADDR = 0x30000000
BOOT1_HDR_SIZE_FIELD = 0x00100000
BOOT1_SDRAM_START = 0x30000000
BOOT1_SDRAM_LIMIT = 0x40000000


def create_boot1_header(entry_addr: int = BOOT1_ENTRY_ADDR) -> bytes:
    """Create the 32-byte Besta/Inventec V5J 2416 BOOT1 header (V2 / stock layout)."""
    hdr = bytearray(BOOT1_HEADER_SIZE)
    struct.pack_into("<I", hdr, BOOT1_OFF_ENTRY, entry_addr)
    struct.pack_into("<I", hdr, BOOT1_OFF_LOAD_SIZE, BOOT1_HDR_SIZE_FIELD)
    struct.pack_into("<I", hdr, BOOT1_OFF_LOAD_ADDR, BOOT1_LOAD_ADDR)
    struct.pack_into("<I", hdr, BOOT1_OFF_SIZE, BOOT1_HDR_SIZE_FIELD)
    hdr[BOOT1_OFF_MAGIC_V5J:BOOT1_OFF_MAGIC_V5J + 4] = BOOT1_MAGIC_V5J
    hdr[BOOT1_OFF_MAGIC_SOC:BOOT1_OFF_MAGIC_SOC + 4] = BOOT1_MAGIC_SOC
    return bytes(hdr)


def verify_boot1_rom(data_or_path: Union[bytes, Path, str]) -> Boot1Info:
    """
    Validate a BOOT1.ROM binary against BXCBOOT0.BIN and ldr_ld.script requirements.
    Raises ValueError if any check fails.
    """
    raw = _read_bytes(data_or_path)
    if len(raw) != BOOT1_ROM_SIZE:
        raise ValueError(
            f"BOOT1.ROM size mismatch: expected {BOOT1_ROM_SIZE} bytes (512 KiB), got {len(raw)}"
        )

    entry_addr = struct.unpack_from("<I", raw, 0x00)[0]
    if entry_addr != BOOT1_ENTRY_ADDR:
        raise ValueError(
            f"BOOT1.ROM entry address mismatch: expected 0x{BOOT1_ENTRY_ADDR:08X}, "
            f"got 0x{entry_addr:08X}"
        )

    load_addr = struct.unpack_from("<I", raw, BOOT1_OFF_LOAD_ADDR)[0]
    if not (BOOT1_SDRAM_START <= load_addr < BOOT1_SDRAM_LIMIT):
        raise ValueError(
            f"BOOT1.ROM load address at 0x0C out of SDRAM range: got 0x{load_addr:08X} "
            f"(Boot0 requires 0x{BOOT1_SDRAM_START:08X}..0x{BOOT1_SDRAM_LIMIT - 1:08X})"
        )

    size_field = struct.unpack_from("<I", raw, BOOT1_OFF_SIZE)[0]
    if size_field < 0x100 or size_field % NAND_BLOCK_SIZE != 0:
        raise ValueError(
            f"BOOT1.ROM size field at 0x10 invalid: got 0x{size_field:08X} "
            f"(Boot0 requires >= 0x100 and a multiple of 0x{NAND_BLOCK_SIZE:X})"
        )

    magic_v5j = raw[BOOT1_OFF_MAGIC_V5J:BOOT1_OFF_MAGIC_V5J + 4]
    if magic_v5j != BOOT1_MAGIC_V5J:
        raise ValueError(
            f"BOOT1.ROM platform magic mismatch at 0x14: expected {BOOT1_MAGIC_V5J!r}, "
            f"got {magic_v5j!r}"
        )

    magic_soc = raw[BOOT1_OFF_MAGIC_SOC:BOOT1_OFF_MAGIC_SOC + 4]
    if magic_soc != BOOT1_MAGIC_SOC:
        raise ValueError(
            f"BOOT1.ROM SoC magic mismatch at 0x18: expected {BOOT1_MAGIC_SOC!r}, "
            f"got {magic_soc!r}"
        )

    trailer = raw[-1]
    if trailer != BOOT1_TRAILER_BYTE:
        raise ValueError(
            f"BOOT1.ROM trailer byte at 0x7FFFF mismatch: expected 0x{BOOT1_TRAILER_BYTE:02X}, "
            f"got 0x{trailer:02X}"
        )

    # Calculate active code/data payload before the 0xDEADBEEF fill region
    fill_pattern = struct.pack("<I", BOOT1_FILL_WORD)
    pos = len(raw) - 1  # Exclude trailing 0xAA byte
    # Align down to 4-byte boundary
    pos -= pos % 4
    while pos >= 4 and raw[pos - 4 : pos] == fill_pattern:
        pos -= 4

    return Boot1Info(
        size=len(raw),
        entry_addr=entry_addr,
        magic_v5j=magic_v5j,
        magic_soc=magic_soc,
        trailer_byte=trailer,
        payload_bytes=pos,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def verify_zimage(
    data_or_path: Union[bytes, Path, str],
    require_magic: bool = True,
) -> int:
    """
    Validate an ARM Linux zImage file for HP Prime G1 (`mtd2` max 8 MiB).
    Returns size in bytes. Raises ValueError on invalid size or magic.
    """
    raw = _read_bytes(data_or_path)
    size = len(raw)
    if size == 0:
        raise ValueError("zImage is empty (0 bytes)")
    if size > LINUX_DAT_KERNEL_MAX_SIZE:
        raise ValueError(
            f"zImage size ({size:,} bytes) exceeds 8 MiB kernel partition limit "
            f"({LINUX_DAT_KERNEL_MAX_SIZE:,} bytes) and would overwrite rootfs!"
        )
    if require_magic:
        if size < ZIMAGE_MAGIC_OFFSET + 12:
            raise ValueError(
                f"zImage is too small ({size} bytes) to contain an ARM zImage header"
            )
        magic, start, end = struct.unpack_from("<III", raw, ZIMAGE_MAGIC_OFFSET)
        if magic != ZIMAGE_MAGIC:
            raise ValueError(
                f"ARM zImage magic mismatch at 0x24: expected 0x{ZIMAGE_MAGIC:08X}, "
                f"got 0x{magic:08X}"
            )
    return size


def verify_rootfs_jffs2(
    data_or_path: Union[bytes, Path, str],
    require_magic: bool = True,
) -> int:
    """
    Validate a JFFS2 root filesystem image for HP Prime G1 (`mtd3` max 54 MiB).
    Returns size in bytes. Raises ValueError on invalid size or magic.
    """
    raw = _read_bytes(data_or_path)
    size = len(raw)
    if size == 0:
        raise ValueError("rootfs.jffs2 is empty (0 bytes)")
    if size > LINUX_DAT_ROOTFS_MAX_SIZE:
        raise ValueError(
            f"rootfs.jffs2 size ({size:,} bytes) exceeds 54 MiB rootfs partition limit "
            f"({LINUX_DAT_ROOTFS_MAX_SIZE:,} bytes)!"
        )
    if require_magic:
        if size < 2:
            raise ValueError("rootfs.jffs2 is smaller than 2 bytes")
        magic = struct.unpack_from("<H", raw, 0)[0]
        if magic != JFFS2_MAGIC_LE:
            raise ValueError(
                f"JFFS2 magic mismatch at offset 0: expected 0x{JFFS2_MAGIC_LE:04X}, "
                f"got 0x{magic:04X}"
            )
    return size


def build_linux_dat(
    zimage_path: Union[Path, str],
    rootfs_path: Union[Path, str],
    output_path: Union[Path, str],
    boot1_path: Optional[Union[Path, str]] = None,
    require_magic: bool = True,
    total_size: int = LINUX_DAT_TOTAL_SIZE,
) -> LinuxDatReport:
    """
    Build the 63 MiB 0xFF-padded LINUX.DAT image from zImage and rootfs.jffs2.
    Optionally verifies BOOT1.ROM if boot1_path is supplied.
    """
    zimage_p = Path(zimage_path)
    rootfs_p = Path(rootfs_path)
    out_p = Path(output_path)

    if boot1_path is not None:
        verify_boot1_rom(Path(boot1_path))

    zimage_bytes = zimage_p.read_bytes()
    rootfs_bytes = rootfs_p.read_bytes()

    verify_zimage(zimage_bytes, require_magic=require_magic)
    verify_rootfs_jffs2(rootfs_bytes, require_magic=require_magic)

    min_required = LINUX_DAT_ROOTFS_OFFSET + len(rootfs_bytes)
    if total_size < min_required:
        raise ValueError(
            f"total_size ({total_size:,}) is smaller than required image span ({min_required:,})"
        )

    out_p.parent.mkdir(parents=True, exist_ok=True)

    # Stream-write 0xFF-padded container with zImage @ +0 and rootfs.jffs2 @ +8 MiB
    hasher = hashlib.sha256()
    ff_chunk = b"\xFF" * (1024 * 1024)  # 1 MiB chunk

    with out_p.open("wb") as f:
        # 1. Write zImage at offset 0
        f.write(zimage_bytes)
        hasher.update(zimage_bytes)

        # 2. Pad with 0xFF up to LINUX_DAT_ROOTFS_OFFSET (8 MiB)
        pad1_len = LINUX_DAT_ROOTFS_OFFSET - len(zimage_bytes)
        while pad1_len > 0:
            step = min(pad1_len, len(ff_chunk))
            f.write(ff_chunk[:step])
            hasher.update(ff_chunk[:step])
            pad1_len -= step

        # 3. Write rootfs.jffs2 at offset +8 MiB
        f.write(rootfs_bytes)
        hasher.update(rootfs_bytes)

        # 4. Pad with 0xFF up to total_size (63 MiB)
        pad2_len = total_size - (LINUX_DAT_ROOTFS_OFFSET + len(rootfs_bytes))
        while pad2_len > 0:
            step = min(pad2_len, len(ff_chunk))
            f.write(ff_chunk[:step])
            hasher.update(ff_chunk[:step])
            pad2_len -= step

    return LinuxDatReport(
        output_path=out_p,
        total_size=total_size,
        zimage_size=len(zimage_bytes),
        zimage_sha256=hashlib.sha256(zimage_bytes).hexdigest(),
        rootfs_size=len(rootfs_bytes),
        rootfs_sha256=hashlib.sha256(rootfs_bytes).hexdigest(),
        linux_dat_sha256=hasher.hexdigest(),
    )


def inspect_file(path: Union[Path, str]) -> dict:
    """Inspect a BOOT1.ROM, zImage, rootfs.jffs2, or LINUX.DAT file and return metadata."""
    p = Path(path)
    size = p.stat().st_size

    with p.open("rb") as f:
        head64 = f.read(64)

    # Case 1: 512 KiB BOOT1.ROM
    if size == BOOT1_ROM_SIZE and head64[BOOT1_OFF_MAGIC_V5J:BOOT1_OFF_MAGIC_V5J + 4] == BOOT1_MAGIC_V5J:
        info = verify_boot1_rom(p)
        return {
            "type": "BOOT1.ROM",
            "path": str(p),
            "size": info.size,
            "entry_addr": f"0x{info.entry_addr:08X}",
            "magic_v5j": info.magic_v5j.decode("ascii", errors="replace").rstrip("\x00"),
            "magic_soc": info.magic_soc.decode("ascii", errors="replace"),
            "payload_bytes": info.payload_bytes,
            "trailer_byte": f"0x{info.trailer_byte:02X}",
            "usb_offset": "0x00000000 (LBA 0)",
            "phys_nand_range": "0x00040000..0x000BFFFF (Block 2..5, mtd1)",
            "sha256": info.sha256,
        }

    # Case 2: Combined LINUX.DAT (>= 8 MiB and starts with zImage magic)
    if size > LINUX_DAT_ROOTFS_OFFSET and len(head64) >= 0x28:
        magic = struct.unpack_from("<I", head64, ZIMAGE_MAGIC_OFFSET)[0]
        if magic == ZIMAGE_MAGIC:
            with p.open("rb") as f:
                f.seek(LINUX_DAT_ROOTFS_OFFSET)
                r_head = f.read(4)
            r_magic = struct.unpack_from("<H", r_head, 0)[0] if len(r_head) >= 2 else 0
            return {
                "type": "LINUX.DAT",
                "path": str(p),
                "size": size,
                "zimage_magic": f"0x{magic:08X}",
                "rootfs_offset": f"0x{LINUX_DAT_ROOTFS_OFFSET:08X} (+8 MiB)",
                "rootfs_jffs2_magic": f"0x{r_magic:04X}",
                "usb_offset": f"0x{LINUX_DAT_USB_OFFSET:08X} (LBA {LINUX_DAT_START_LBA})",
                "phys_nand_start": f"0x{usb_offset_to_phys_offset(LINUX_DAT_USB_OFFSET):08X} (Block 8, mtd2)",
            }

    # Case 3: Standalone ARM zImage
    if len(head64) >= 0x28:
        magic, z_start, z_end = struct.unpack_from("<III", head64, ZIMAGE_MAGIC_OFFSET)
        if magic == ZIMAGE_MAGIC:
            blocks_needed = (size + NAND_BLOCK_SIZE - 1) // NAND_BLOCK_SIZE
            return {
                "type": "zImage",
                "path": str(p),
                "size": size,
                "zimage_magic": f"0x{magic:08X}",
                "zimage_span": z_end - z_start,
                "nand_blocks_used": f"{blocks_needed} / 64 blocks (Block 8..{8 + blocks_needed - 1})",
            }

    # Case 4: Standalone JFFS2 image
    if len(head64) >= 2:
        j_magic = struct.unpack_from("<H", head64, 0)[0]
        if j_magic == JFFS2_MAGIC_LE:
            blocks_needed = (size + NAND_BLOCK_SIZE - 1) // NAND_BLOCK_SIZE
            return {
                "type": "rootfs.jffs2",
                "path": str(p),
                "size": size,
                "jffs2_magic": f"0x{j_magic:04X}",
                "nand_blocks_used": f"{blocks_needed} / 432 blocks (Block 72..{72 + blocks_needed - 1})",
            }

    return {
        "type": "unknown",
        "path": str(p),
        "size": size,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="HP Prime G1 Linux-For-HPPrime-V3 Image Builder & Inspector"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Build 63 MiB LINUX.DAT from zImage and rootfs.jffs2")
    p_build.add_argument("--zimage", default="zImage", help="Path to ARM Linux zImage")
    p_build.add_argument("--rootfs", default="rootfs.jffs2", help="Path to JFFS2 rootfs image")
    p_build.add_argument("--boot1", default=None, help="Optional path to BOOT1.ROM to verify")
    p_build.add_argument("--output", default="LINUX.DAT", help="Output LINUX.DAT path")
    p_build.add_argument(
        "--no-magic-check",
        action="store_true",
        help="Disable zImage/JFFS2 magic header checks (still enforces partition size limits)",
    )

    p_inspect = sub.add_parser(
        "inspect", help="Inspect BOOT1.ROM, zImage, rootfs.jffs2, or LINUX.DAT"
    )
    p_inspect.add_argument("files", nargs="+", help="One or more image files to inspect")

    args = parser.parse_args(argv)

    if args.cmd == "build":
        report = build_linux_dat(
            zimage_path=args.zimage,
            rootfs_path=args.rootfs,
            output_path=args.output,
            boot1_path=args.boot1,
            require_magic=not args.no_magic_check,
        )
        phys_kernel = usb_offset_to_phys_offset(LINUX_DAT_USB_OFFSET)
        phys_rootfs = phys_kernel + LINUX_DAT_ROOTFS_OFFSET
        print("============================================================")
        print(" HP Prime G1 Linux-For-HPPrime-V3 Image Build Complete")
        print("============================================================")
        print(f" Output File    : {report.output_path} ({report.total_size:,} bytes / 63 MiB)")
        print(f" LINUX.DAT SHA256: {report.linux_dat_sha256}")
        print("------------------------------------------------------------")
        print(
            f" [mtd2: kernel] zImage       : {report.zimage_size:,} / {LINUX_DAT_KERNEL_MAX_SIZE:,} bytes"
        )
        print(
            f"   LINUX.DAT Offset          : +0x{LINUX_DAT_KERNEL_OFFSET:08X}"
        )
        print(
            f"   USB MSC Offset / LBA      : 0x{LINUX_DAT_USB_OFFSET:08X} (LBA {LINUX_DAT_START_LBA})"
        )
        print(
            f"   Physical NAND             : 0x{phys_kernel:08X} (Block {phys_offset_to_block(phys_kernel)}..71)"
        )
        print("------------------------------------------------------------")
        print(
            f" [mtd3: rootfs] rootfs.jffs2 : {report.rootfs_size:,} / {LINUX_DAT_ROOTFS_MAX_SIZE:,} bytes"
        )
        print(
            f"   LINUX.DAT Offset          : +0x{LINUX_DAT_ROOTFS_OFFSET:08X} (+8 MiB)"
        )
        print(
            f"   USB MSC Offset / LBA      : 0x{LINUX_DAT_USB_OFFSET + LINUX_DAT_ROOTFS_OFFSET:08X} "
            f"(LBA {LINUX_DAT_START_LBA + (LINUX_DAT_ROOTFS_OFFSET // 512)})"
        )
        print(
            f"   Physical NAND             : 0x{phys_rootfs:08X} (Block {phys_offset_to_block(phys_rootfs)}..503)"
        )
        print("============================================================")
        return 0

    if args.cmd == "inspect":
        for fpath in args.files:
            meta = inspect_file(fpath)
            print(f"[{meta['type']}] {meta['path']}")
            for k, v in meta.items():
                if k not in ("type", "path"):
                    print(f"  {k:20s}: {v}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

