#!/usr/bin/env python3
"""
Unit & Integration Test Suite for Linux-For-HPPrime-V3.

Tests:
  1. NAND / Recovery Mode USB Mass Storage / MTD partition offset conversions
  2. Real V2 artifact inspection (BOOT1.ROM, zImage, rootfs.jffs2, usbtool.ini)
  3. Image builder (mkimg_v3.py) validation and 0xFF-padded LINUX.DAT construction
  4. BXCBOOT0 SCSI protocol & Dry-Run flashing simulation (usbtool_v3.py)
  5. V3 Bootloader (bootloader/boot.c) static bug-fix verification
"""

import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_V2_DIR = PROJECT_ROOT.parent.parent / "20_Repo" / "G1-Hardware" / "Linux-For-HPPrime-V2"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.mkimg_v3 import (
    build_linux_dat,
    create_boot1_header,
    inspect_file,
    verify_boot1_rom,
    verify_rootfs_jffs2,
    verify_zimage,
)
from tools.nand_layout import (
    BOOT1_ENTRY_ADDR,
    BOOT1_FILL_WORD,
    BOOT1_ROM_SIZE,
    BOOT1_TRAILER_BYTE,
    HPPRIME_MTD_PARTITIONS,
    JFFS2_MAGIC_LE,
    LINUX_DAT_KERNEL_MAX_SIZE,
    LINUX_DAT_ROOTFS_OFFSET,
    LINUX_DAT_START_LBA,
    LINUX_DAT_USB_OFFSET,
    NAND_BLOCK_SIZE,
    RECOVERY_MSC_BASE_OFFSET,
    USB_SECTOR_SIZE,
    ZIMAGE_MAGIC,
    ZIMAGE_MAGIC_OFFSET,
    block_to_first_page,
    block_to_phys_offset,
    find_partitions_for_phys_range,
    lba_to_phys_offset,
    phys_offset_to_block,
    phys_offset_to_usb_offset,
    usb_offset_to_lba,
    usb_offset_to_phys_offset,
)
from tools.usbtool_v3 import (
    ARG_HANDSHAKE,
    BXC_MAGIC_1,
    BXC_MAGIC_2,
    BXC_PACKET_SIZE,
    CMD_HANDSHAKE,
    CMD_PROGRESS,
    CMD_REBOOT,
    LOCK_SECTOR_COUNT,
    SCSI_OP_READ_10,
    SCSI_OP_WRITE_10,
    SPECIAL_CONTROL_LBA,
    UNLOCK_SECTOR_COUNT,
    BxcBoot0Controller,
    DryRunTransport,
    build_bxc_control_packet,
    build_scsi_rw10_cdb,
    parse_bxc_control_packet,
    parse_usbtool_ini,
)


class TestNandLayout(unittest.TestCase):
    """Verify exact alignment between BXCBOOT0 USB MSC, boot.c, and mach-hpprime.c."""

    def test_recovery_base_shift_and_boot1_mapping(self):
        # UpdateItem00: .\BOOT1.ROM at USB offset 0x00000000 -> Physical 0x00040000 (Block 2)
        self.assertEqual(RECOVERY_MSC_BASE_OFFSET, 0x00040000)
        self.assertEqual(usb_offset_to_lba(0x00000000), 0)
        phys_boot1 = usb_offset_to_phys_offset(0x00000000)
        self.assertEqual(phys_boot1, 0x00040000)
        self.assertEqual(phys_offset_to_block(phys_boot1), 2)

        parts = find_partitions_for_phys_range(phys_boot1, BOOT1_ROM_SIZE)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].name, "Linux Bootloader")
        self.assertEqual(parts[0].start_block, 2)
        self.assertEqual(parts[0].end_block_inclusive, 5)

    def test_linux_dat_kernel_and_rootfs_mapping(self):
        # UpdateItem01: .\LINUX.DAT at USB offset 0x000C0000 (LBA 1536) -> Physical 0x00100000 (1 MiB, Block 8)
        self.assertEqual(LINUX_DAT_USB_OFFSET, 0x000C0000)
        self.assertEqual(usb_offset_to_lba(LINUX_DAT_USB_OFFSET), 1536)
        self.assertEqual(LINUX_DAT_START_LBA, 1536)

        phys_kernel = usb_offset_to_phys_offset(LINUX_DAT_USB_OFFSET)
        self.assertEqual(phys_kernel, 0x00100000)  # 1 MiB
        self.assertEqual(phys_offset_to_block(phys_kernel), 8)
        self.assertEqual(block_to_first_page(8), 512)  # matches boot.c: page = 8 * 64

        # RootFS at LINUX.DAT + 8 MiB -> Physical 0x00900000 (9 MiB, Block 72)
        phys_rootfs = phys_kernel + LINUX_DAT_ROOTFS_OFFSET
        self.assertEqual(phys_rootfs, 0x00900000)
        self.assertEqual(phys_offset_to_block(phys_rootfs), 72)
        self.assertEqual(phys_offset_to_usb_offset(phys_rootfs), 0x008C0000)
        self.assertEqual(lba_to_phys_offset(17920), 0x00900000)

        # Verify MTD partition alignment
        mtd_kernel = HPPRIME_MTD_PARTITIONS[2]
        mtd_rootfs = HPPRIME_MTD_PARTITIONS[3]
        self.assertEqual(mtd_kernel.phys_offset, phys_kernel)
        self.assertEqual(mtd_kernel.size, 8 * 1024 * 1024)
        self.assertEqual(mtd_rootfs.phys_offset, phys_rootfs)
        self.assertEqual(mtd_rootfs.size, 54 * 1024 * 1024)


class TestMkimgAndRealV2Artifacts(unittest.TestCase):
    """Test mkimg_v3.py against both synthetic data and real V2 artifacts."""

    def test_v2_repo_ini_and_synthetic_artifacts_inspection(self):
        ini_v2 = REPO_V2_DIR / "usbtool.ini"
        self.assertTrue(ini_v2.exists(), f"Missing reference {ini_v2}")
        cfg_v2 = parse_usbtool_ini(ini_v2)
        self.assertEqual(len(cfg_v2.items), 2)
        self.assertEqual(cfg_v2.items[0].usb_offset, 0x00000000)
        self.assertEqual(cfg_v2.items[1].usb_offset, 0x000C0000)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            boot1_p = tmp / "BOOT1.ROM"
            zimage_p = tmp / "zImage"
            rootfs_p = tmp / "rootfs.jffs2"

            # 1. Construct a valid 512 KiB BOOT1.ROM (header + ARM payload + 0xDEADBEEF fill + 0xAA)
            hdr = create_boot1_header()
            payload = b"\x00\xF0\x20\xE3" * 256  # 1 KiB ARM NOP instructions
            rem_words = (BOOT1_ROM_SIZE - len(hdr) - len(payload) - 4) // 4
            fill = struct.pack("<I", BOOT1_FILL_WORD) * rem_words
            tail = b"\xEF\xBE\xAD\xAA"
            boot1_p.write_bytes(hdr + payload + fill + tail)

            # 2. Construct a valid ARM zImage
            z_buf = bytearray(64 * 1024)
            struct.pack_into("<III", z_buf, ZIMAGE_MAGIC_OFFSET, ZIMAGE_MAGIC, 0, len(z_buf))
            zimage_p.write_bytes(z_buf)

            # 3. Construct a valid JFFS2 rootfs
            r_buf = bytearray(128 * 1024)
            struct.pack_into("<H", r_buf, 0, JFFS2_MAGIC_LE)
            rootfs_p.write_bytes(r_buf)

            b_info = verify_boot1_rom(boot1_p)
            self.assertEqual(b_info.size, BOOT1_ROM_SIZE)
            self.assertEqual(b_info.entry_addr, BOOT1_ENTRY_ADDR)
            self.assertEqual(b_info.trailer_byte, BOOT1_TRAILER_BYTE)
            self.assertEqual(b_info.payload_bytes, 32 + 1024)

            self.assertEqual(verify_zimage(zimage_p), 64 * 1024)
            self.assertEqual(verify_rootfs_jffs2(rootfs_p), 128 * 1024)

            self.assertEqual(inspect_file(boot1_p)["type"], "BOOT1.ROM")
            self.assertEqual(inspect_file(zimage_p)["type"], "zImage")
            self.assertEqual(inspect_file(rootfs_p)["type"], "rootfs.jffs2")

    def test_boot1_validation_rejects_corrupt_header(self):
        hdr = bytearray(create_boot1_header())
        fill = struct.pack("<I", BOOT1_FILL_WORD) * ((BOOT1_ROM_SIZE - 32 - 4) // 4)
        tail = b"\xEF\xBE\xAD\xAA"
        valid_rom = bytes(hdr) + fill + tail
        info = verify_boot1_rom(valid_rom)
        self.assertEqual(info.entry_addr, BOOT1_ENTRY_ADDR)

        # S3b: V2/stock header layout (V2 boot.c 73..80)
        self.assertEqual(
            struct.unpack_from("<8I", valid_rom, 0),
            (0x30000020, 0, 0x00100000, 0x30000000, 0x00100000, 0x004A3556, 0x36313432, 0),
        )
        self.assertEqual(valid_rom[0x14:0x18], b"V5J\x00")
        self.assertEqual(valid_rom[0x18:0x1C], b"2416")

        # Corrupt V5J magic (+0x14)
        bad_rom = bytearray(valid_rom)
        bad_rom[0x14:0x18] = b"BAD\x00"
        with self.assertRaises(ValueError):
            verify_boot1_rom(bytes(bad_rom))

        # Old V3 layout: load address +0x0C = 0 must be rejected (Boot0 0x65cc..0x65e8)
        bad_rom = bytearray(valid_rom)
        struct.pack_into("<I", bad_rom, 0x0C, 0)
        with self.assertRaises(ValueError):
            verify_boot1_rom(bytes(bad_rom))

        # Size field +0x10 not block-aligned must be rejected (Boot0 0x6604..0x6664)
        bad_rom = bytearray(valid_rom)
        struct.pack_into("<I", bad_rom, 0x10, 0x1234)
        with self.assertRaises(ValueError):
            verify_boot1_rom(bytes(bad_rom))

    def test_zimage_oversize_rejected(self):
        # Create a fake >8 MiB zImage buffer
        oversized = bytearray(LINUX_DAT_KERNEL_MAX_SIZE + 4)
        struct.pack_into("<I", oversized, ZIMAGE_MAGIC_OFFSET, ZIMAGE_MAGIC)
        with self.assertRaises(ValueError):
            verify_zimage(bytes(oversized))

    def test_build_linux_dat_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            z_path = tmp / "zImage"
            r_path = tmp / "rootfs.jffs2"
            out_path = tmp / "LINUX.DAT"

            # Fake valid 4 KiB zImage and 4 KiB JFFS2
            z_buf = bytearray(4096)
            struct.pack_into("<III", z_buf, ZIMAGE_MAGIC_OFFSET, ZIMAGE_MAGIC, 0, 4096)
            z_buf[0:4] = b"KERN"
            z_path.write_bytes(z_buf)

            r_buf = bytearray(4096)
            struct.pack_into("<H", r_buf, 0, JFFS2_MAGIC_LE)
            r_buf[2:6] = b"ROOT"
            r_path.write_bytes(r_buf)

            # Use a compact 9 MiB total_size for fast unit test
            compact_total = 9 * 1024 * 1024
            report = build_linux_dat(
                zimage_path=z_path,
                rootfs_path=r_path,
                output_path=out_path,
                total_size=compact_total,
            )
            self.assertEqual(report.total_size, compact_total)
            self.assertEqual(out_path.stat().st_size, compact_total)

            with out_path.open("rb") as f:
                self.assertEqual(f.read(4), b"KERN")
                f.seek(4096)
                self.assertEqual(f.read(16), b"\xFF" * 16)
                f.seek(LINUX_DAT_ROOTFS_OFFSET)
                self.assertEqual(struct.unpack("<H", f.read(2))[0], JFFS2_MAGIC_LE)
                self.assertEqual(f.read(4), b"ROOT")
                f.seek(LINUX_DAT_ROOTFS_OFFSET + 4096)
                self.assertEqual(f.read(16), b"\xFF" * 16)


class TestUsbToolProtocolAndSimulation(unittest.TestCase):
    """Verify BXCBOOT0 SCSI CDBs, control packets, INI parser, and DryRun flashing."""

    def test_bxc_control_packet_roundtrip(self):
        pkt = build_bxc_control_packet(CMD_HANDSHAKE, ARG_HANDSHAKE)
        self.assertEqual(len(pkt), BXC_PACKET_SIZE)
        m1, m2, sz, cmd, arg = struct.unpack_from("<IIIII", pkt, 0)
        self.assertEqual(m1, BXC_MAGIC_1)
        self.assertEqual(m2, BXC_MAGIC_2)
        self.assertEqual(sz, 512)
        self.assertEqual((cmd, arg), parse_bxc_control_packet(pkt))

    def test_scsi_cdb_encoding(self):
        # Unlock CDB: READ_10 (0x28) at LBA 170 (0xAA), 30 sectors (0x001E)
        cdb_unlock = build_scsi_rw10_cdb(SCSI_OP_READ_10, SPECIAL_CONTROL_LBA, UNLOCK_SECTOR_COUNT)
        self.assertEqual(cdb_unlock, bytes([0x28, 0x00, 0x00, 0x00, 0x00, 0xAA, 0x00, 0x00, 0x1E, 0x00]))

        # Write LINUX.DAT start: WRITE_10 (0x2A) at LBA 1536 (0x00000600), 32 sectors (0x0020)
        cdb_write = build_scsi_rw10_cdb(SCSI_OP_WRITE_10, 1536, 32)
        self.assertEqual(cdb_write, bytes([0x2A, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x20, 0x00]))

    def test_parse_usbtool_ini(self):
        ini_path = PROJECT_ROOT / "usbtool.ini"
        cfg = parse_usbtool_ini(ini_path, base_dir=REPO_V2_DIR)
        self.assertEqual(len(cfg.items), 2)
        self.assertEqual(cfg.items[0].usb_offset, 0x00000000)
        self.assertEqual(cfg.items[0].start_lba, 0)
        self.assertEqual(cfg.items[0].phys_offset, 0x00040000)
        self.assertEqual(cfg.items[1].usb_offset, 0x000C0000)
        self.assertEqual(cfg.items[1].start_lba, 1536)
        self.assertEqual(cfg.items[1].phys_offset, 0x00100000)

    def test_dry_run_flash_simulation_and_write_lock_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            boot1 = tmp / "BOOT1.ROM"
            linux_dat = tmp / "LINUX.DAT"
            ini = tmp / "usbtool.ini"
            mirror = tmp / "virtual_msc.bin"

            boot1.write_bytes(b"B1" * 512)      # 1024 bytes = 2 sectors @ LBA 0
            linux_dat.write_bytes(b"LX" * 512)  # 1024 bytes = 2 sectors @ LBA 1536
            ini.write_text(
                "[Update Infomation]\n"
                "UT Mode = 1\n"
                "Reset = 1\n"
                "UpdateItems = 2\n"
                "UpdateItem00 = .\\BOOT1.ROM 0 0x00000000\n"
                "UpdateItem01 = .\\LINUX.DAT 0 0x000C0000\n",
                encoding="utf-8",
            )

            transport = DryRunTransport(mirror_file=mirror)
            try:
                # 1. A data page written while locked is marked write-protected and not mirrored
                #    (S6: V16 WRITE_10 mode 2 0xc578 writes nothing while the lock flag is 1)
                transport.write_sectors(0, b"\x00" * USB_SECTOR_SIZE * 4)
                self.assertEqual(transport.history[-1].status, "write-protected")

                # 2. Run full INI flash flow (S5: >= 4-sector chunks; Reset=1 needs explicit reboot)
                cfg = parse_usbtool_ini(ini)
                ctrl = BxcBoot0Controller(transport, allow_reboot=True)
                results = ctrl.flash_from_ini(cfg, chunk_sectors=4, reboot=cfg.reset_after)

                self.assertEqual(len(results), 2)
                # 1024 bytes -> one 2 KiB page (0xFF filled)
                self.assertEqual(results[0][1], 2048)
                self.assertEqual(results[1][1], 2048)
                # Progress 100% locks writes again, and the explicit reboot was sent
                self.assertEqual(transport.last_progress, 100)
                self.assertFalse(transport.unlocked)
                self.assertTrue(transport.rebooted)
            finally:
                transport.close()

            # 3. Verify virtual MSC mirror contents at LBA 0 and LBA 1536 (0xC0000)
            mirror_bytes = mirror.read_bytes()
            self.assertEqual(mirror_bytes[0:1024], b"B1" * 512)
            self.assertEqual(mirror_bytes[1024:2048], b"\xFF" * 1024)
            self.assertEqual(mirror_bytes[0x000C0000 : 0x000C0000 + 1024], b"LX" * 512)


class TestScanBadBlocksBlocked(unittest.TestCase):
    """0xBEC2 erases and rewrites Boot0 block 0 in reference V16 (0x7988); it must never be sent."""

    def test_scan_bad_blocks_raises_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = DryRunTransport(mirror_file=Path(tmp) / "mirror.bin")
            ctrl = BxcBoot0Controller(transport)
            for arg in (0, 1):
                with self.assertRaises(PermissionError):
                    ctrl.scan_bad_blocks(arg)
            self.assertEqual(len(transport.history), 0)


class TestBootloaderV3SourceFixes(unittest.TestCase):
    """Verify V3 bootloader source code contains all required bug fixes."""

    def test_boot_c_fixes_present(self):
        boot_c = (PROJECT_ROOT / "bootloader" / "boot.c").read_text(encoding="utf-8")
        # 1. Buggy powu32 function definition must be removed and replaced with (1U << sdiv)
        self.assertNotIn("static uint32_t powu32", boot_c)
        # S3b: get_FCLK now computes ((12 MHz / pdiv) * mdiv) >> sdiv (same as / (1U << sdiv),
        # without 32-bit overflow); check real code, not comments.
        code = re.sub(r"/\*.*?\*/", "", boot_c, flags=re.S)
        self.assertRegex(code, r"\* mdiv\) >> sdiv|\(1U << sdiv\)")
        # 2. Soft-lock boundary must start at Block 8 (not 10)
        self.assertIn("#define BOOTLOADER_LOCK_START_BLK   8U", boot_c)
        self.assertIn(
            "set_flash_lock_boundary(BOOTLOADER_LOCK_START_BLK, BOOTLOADER_LOCK_END_BLK);",
            boot_c,
        )
        # 3. Duplicate rootwait must be fixed
        self.assertNotIn("rootwait rootwait", boot_c)

    # ---- S3b 2026-11-17 source checks (regex over boot.c) ----

    def _boot_c(self):
        return (PROJECT_ROOT / "bootloader" / "boot.c").read_text(encoding="utf-8")

    @staticmethod
    def _strip_comments(src):
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return re.sub(r"//[^\n]*", "", src)

    def test_cmdline_has_no_bare_earlycon(self):
        # 2026-11-18 hardware: ATAG boot + bare "earlycon" hung kernel 6.1.35 in parse_early_param.
        code = self._strip_comments(self._boot_c())
        m = re.search(r"static\s+char\s*\*\s*cmdline\s*=\s*((?:\s*\"[^\"]*\")+)\s*;", code)
        self.assertIsNotNone(m, "cmdline initializer not found")
        cmdline = "".join(re.findall(r'"([^"]*)"', m.group(1)))
        self.assertNotIn("earlycon", cmdline.split())
        self.assertIn("root=mtd:rootfs", cmdline)

    def test_kernel_patches_present(self):
        pdir = PROJECT_ROOT / "kernel" / "patches"
        p1 = (pdir / "0001-hpprime-v3-boot-fixes.patch").read_text(encoding="utf-8")
        self.assertIn("if (!fdt)", p1)
        self.assertIn(".mask_flags = MTD_WRITEABLE", p1)
        self.assertIn("mtd->flags &= ~MTD_WRITEABLE", p1)
        self.assertTrue((pdir / "0002-hpprime-diag-screen-markers.patch").is_file())

    def test_mach_type_hpprimev2_1686(self):
        code = self._strip_comments(self._boot_c())
        m = re.search(r"#define\s+MACH_TYPE_HPPRIMEV2\s+(\d+)U?", code)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 1686)
        self.assertRegex(code, r"kernel_entry\(\s*0\s*,\s*MACH_TYPE_HPPRIMEV2\s*,")
        self.assertNotRegex(code, r"\b1685\b")

    def test_boot1_header_v2_layout(self):
        code = self._strip_comments(self._boot_c())
        m = re.search(r"head_info\s*\[\s*8\s*\][^=]*=\s*\{([^}]*)\}", code)
        self.assertIsNotNone(m, "head_info[8] initializer not found")
        words = [int(w.strip().rstrip("uU"), 0) for w in m.group(1).split(",") if w.strip()]
        self.assertEqual(len(words), 8)
        self.assertEqual(words[0], 0x30000020)
        self.assertEqual(words[0x0C // 4], 0x30000000)
        self.assertEqual(words[0x10 // 4], 0x00100000)
        self.assertEqual(struct.pack("<I", words[0x14 // 4]), b"V5J\x00")
        self.assertEqual(struct.pack("<I", words[0x18 // 4]), b"2416")

    def test_stack_outside_kernel_load_window(self):
        code = self._strip_comments(self._boot_c())
        m = re.search(r"ldr\s+sp\s*,\s*=\s*(0x[0-9A-Fa-f]+)", code)
        self.assertIsNotNone(m)
        sp = int(m.group(1), 16)
        self.assertFalse(0x30008000 <= sp < 0x30808000, f"sp 0x{sp:08X} inside kernel window")
        self.assertFalse(0x31F80000 <= sp <= 0x32000000, f"sp 0x{sp:08X} inside framebuffer")

    def test_no_libc_vsnprintf(self):
        code = self._strip_comments(self._boot_c())
        self.assertNotRegex(code, r"\bv?s?n?printf\s*\(\s*buf")
        self.assertNotRegex(code, r"\bvsnprintf\b")
        self.assertNotRegex(code, r"\bvsprintf\b")
        self.assertNotRegex(code, r"#include\s*<stdio\.h>")

    def test_nand_lock_uses_row_address(self):
        code = self._strip_comments(self._boot_c())
        m = re.search(r"static void set_flash_lock_boundary\([^)]*\)\s*\{(.*?)\n\}", code, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertRegex(body, r"\*NFSBLK\s*=\s*nand_block_to_row\(")
        self.assertRegex(body, r"\*NFEBLK\s*=\s*nand_block_to_row\(")
        self.assertRegex(code, r"\(block\s*\*\s*NAND_PAGES_PER_BLOCK\)")
        self.assertRegex(code, r"#define\s+NAND_PAGES_PER_BLOCK\s+64U")
        # blocks 8..2039 writable -> rows 0x200 .. 0x1FE00
        self.assertEqual(8 * 64, 0x200)
        self.assertEqual(2040 * 64, 0x1FE00)

    def test_atag_and_lcd_addresses(self):
        code = self._strip_comments(self._boot_c())
        self.assertNotIn("0x30000100", code)
        self.assertNotIn("0x30600000", code)
        self.assertRegex(code, r"static uint32_t atag_buf\[")
        self.assertRegex(code, r"ATAG_MEM_SIZE_BYTES\s+\(SDRAM_SIZE_BYTES\s*-\s*FB_RESERVED_BYTES\)")
        self.assertRegex(code, r"#define\s+FB_RESERVED_BYTES\s+\(512U \* 1024U\)")
        self.assertNotRegex(code, r"rGPDCON\s*=")
        self.assertNotRegex(code, r"rGPCCON\s*=")

    def test_clock_mpll_pdiv_bit5(self):
        code = self._strip_comments(self._boot_c())
        self.assertRegex(code, r"MPLLCON_OFS\s*=\s*\(1U << 0\)\s*\|\s*\(3U << 5\)\s*\|\s*\(400U << 14\)")
        self.assertRegex(code, r"\(mpll_con >> 5\) & 0x3FU")
        self.assertIn("HCLKCON_OFS", code)
        self.assertIn("CLKDIV1_OFS", code)
        self.assertIn("CLKDIV2_OFS", code)

    def test_root_by_mtd_name(self):
        # Whole-NAND master (CONFIG_MTD_PARTITIONED_MASTER) shifts numbering; mount rootfs by name.
        code = self._strip_comments(self._boot_c())
        self.assertIn("root=mtd:rootfs", code)
        self.assertNotIn("root=/dev/mtdblock", code)


if __name__ == "__main__":
    unittest.main()
