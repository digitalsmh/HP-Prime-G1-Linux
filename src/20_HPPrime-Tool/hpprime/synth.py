"""Offline check: does a recovery-disk image contain only the sectors that V16 Boot0 synthesizes?

Moved from 30_TEST/20_ALL-Backup/check_synth_sectors.py. Read-only. Takes a Boot0 image
(reference BXCBOOT0V16.bin) and a recovery-disk image. It rebuilds the MBR and FAT boot sector
the way the mode-2 READ_10 handler does (functions 0xbcc4 and 0xbd4c), compares them with the
image, and counts the non-zero sectors. No device access.
Prints only match results and counts, never volume label or serial bytes.
"""
import struct
from pathlib import Path

PART_LBA = 0x20000
PART_SECTORS = 0x4000
FAT_HEAD_LBAS = (PART_LBA + 8, PART_LBA + 24)
BOOT0_TAG = b"ARMUboot V16"


def synth_mbr() -> bytes:
    m = bytearray(512)
    m[446:452] = bytes([0x00, 0x02, 0x00, 0x00, 0x01, 0x02])
    m[452:454] = struct.pack("<H", 0x4000)
    m[454:458] = struct.pack("<I", PART_LBA)
    m[458:462] = struct.pack("<I", PART_SECTORS)
    m[510:512] = b"\x55\xaa"
    return bytes(m)


def synth_boot(fw: bytes) -> bytes:
    b = bytearray(512)
    b[0:3] = b"\xeb\x3c\x90"
    b[3:11] = fw[0xbe84:0xbe8c]
    b[11:28] = struct.pack("<HBHBHHBHHH", 512, 4, 8, 2, 512, 0x4000, 0xF8, 16, 63, 255)
    b[28:36] = struct.pack("<II", 1, 0)
    b[36:39] = bytes([0x80, 0x00, 0x29])
    b[39:43] = fw[0xbe90:0xbe94]
    b[43:54] = fw[0xbe94:0xbe9f]
    b[54:62] = fw[0xbea0:0xbea8]
    b[510:512] = b"\x55\xaa"
    return bytes(b)


def check(boot0: Path, disk: Path) -> dict:
    """Return match flags and counts. Raises ValueError if `boot0` is not an ARMUboot V16 build."""
    fw = Path(boot0).read_bytes()
    if fw[0x3FFF4:0x40000] != BOOT0_TAG:
        raise ValueError("Boot0 image is not an ARMUboot V16 build")
    with open(disk, "rb") as stream:
        def sector(lba: int) -> bytes:
            stream.seek(lba * 512)
            return stream.read(512)

        mbr_ok = sector(0) == synth_mbr()
        boot_ok = sector(PART_LBA) == synth_boot(fw)
        fat_ok = all(sector(l)[:4] == b"\xf8\xff\xff\xff" and not any(sector(l)[4:]) for l in FAT_HEAD_LBAS)
        stream.seek(0)
        nonzero = []
        lba = 0
        while chunk := stream.read(512 * 2048):
            for i in range(0, len(chunk), 512):
                if any(chunk[i:i + 512]):
                    nonzero.append(lba + i // 512)
            lba += len(chunk) // 512
    expected = [0, PART_LBA, *FAT_HEAD_LBAS]
    return {"mbr_match": mbr_ok, "boot_sector_match": boot_ok, "fat_heads_match": fat_ok,
            "nonzero_sectors": len(nonzero), "only_synthesized": nonzero == expected, "total_sectors": lba}


def report(result: dict) -> int:
    """Print the original two result lines; exit code 0 when every check passes, else 3."""
    print(f"MBR_MATCH={result['mbr_match']} BOOT_SECTOR_MATCH={result['boot_sector_match']} "
          f"FAT_HEADS_MATCH={result['fat_heads_match']}")
    print(f"NONZERO_SECTORS={result['nonzero_sectors']} ONLY_SYNTHESIZED={result['only_synthesized']} "
          f"TOTAL_SECTORS={result['total_sectors']}")
    passed = result["mbr_match"] and result["boot_sector_match"] and result["fat_heads_match"] and result["only_synthesized"]
    return 0 if passed else 3
