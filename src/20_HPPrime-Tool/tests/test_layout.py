"""S2 write-plan checker tests (hpprime.layout). Offline only; images are 0xFF tempfiles."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import cli  # noqa: E402
from hpprime import layout, vendor  # noqa: E402

MIB = 1024 * 1024


class Images:
    """Context manager: writes 0xFF images and a usbtool.ini into a TemporaryDirectory."""

    def __init__(self, *items):
        self.items = items  # (name, size, usb_offset)

    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        lines = ["[Update Infomation]", f"UpdateItems = {len(self.items)}"]
        for index, (name, size, offset) in enumerate(self.items):
            (self.root / name).write_bytes(b"\xff" * size)
            lines.append(f"UpdateItem{index:02d} = .\\{name} 0 0x{offset:08X}")
        self.ini = self.root / "usbtool.ini"
        self.ini.write_text("\n".join(lines) + "\n")
        return self

    def __exit__(self, *exc):
        self.temporary.cleanup()

    def images(self):
        return vendor.read_ini(self.ini)


def nand(size, offset, name="X.BIN", kind="nand"):
    return vendor.UpdateImage(Path(name), b"\xff" * size, offset, kind)


class TableTests(unittest.TestCase):
    def test_no_bad_blocks_is_lb_plus_two(self):
        table = layout.build_table()
        self.assertEqual(table[:3], [2, 3, 4])
        self.assertEqual(table[2045], 2047)
        self.assertEqual(table[2046], 0)
        self.assertEqual(layout.good_block_count(table), layout.CAPACITY_BLOCKS)
        self.assertEqual(layout.LBA_LIMIT, 0x7FE00)

    def test_bad_block_shifts_following_entries(self):
        table = layout.build_table([3])
        self.assertEqual(table[:4], [2, 4, 5, 6])

    def test_bad_block_inside_reserved_area_extends_it(self):
        self.assertEqual(layout.build_table([1])[:2], [3, 4])

    def test_lba_to_physical(self):
        table = layout.build_table()
        self.assertEqual(layout.lba_to_physical(0, table), (2, 0, False))
        self.assertEqual(layout.lba_to_physical(0x7FDFF, table), (2047, 63, False))
        self.assertTrue(layout.lba_to_physical(0x7FE00, table)[2])
        self.assertTrue(layout.lba_to_physical(0x400000, table)[2])
        self.assertTrue(layout.lba_to_physical(0, [0] * 4)[2])


class PlanTests(unittest.TestCase):
    def test_boot1_is_blocks_2_to_5_in_mtd1(self):
        with Images(("BOOT1.ROM", 512 * 1024, 0)) as files:
            plan, = layout.plan_writes(files.images())
        self.assertEqual(plan["physical_blocks"], [2, 5])
        self.assertEqual(plan["logical_blocks"], [0, 3])
        self.assertEqual(plan["mtd_partitions"], ["mtd1 (Linux Bootloader)"])

    def test_boot1_and_linux_62mib_pass(self):
        with Images(("BOOT1.ROM", 512 * 1024, 0), ("LINUX.DAT", 62 * MIB, 0xC0000)) as files:
            boot1, linux = layout.plan_writes(files.images())
        self.assertEqual(linux["physical_blocks"], [8, 503])
        self.assertEqual(linux["lba_first"], 0xC0000 // 512)
        self.assertEqual([name.split()[0] for name in linux["mtd_partitions"]], ["mtd2", "mtd3"])
        self.assertTrue(linux["physical_contiguous"])

    def test_linux_63mib_exceeds_limit(self):
        # read_ini already refuses it; plan_writes refuses it independently as well.
        with Images(("LINUX.DAT", 63 * MIB, 0xC0000)) as files, self.assertRaises(ValueError):
            files.images()
        with self.assertRaisesRegex(ValueError, r"\(b\).*WRITE_LIMIT"):
            layout.plan_writes([nand(63 * MIB, 0xC0000, "LINUX.DAT")])

    def test_unaligned_start_is_refused(self):
        with Images(("BOOT1.ROM", 4096, 0x1000)) as files:
            with self.assertRaisesRegex(ValueError, r"\(c\)"):
                layout.plan_writes(files.images())

    def test_bad_block_3_shifts_physical_range(self):
        with Images(("BOOT1.ROM", 512 * 1024, 0), ("LINUX.DAT", 62 * MIB, 0xC0000)) as files:
            boot1, linux = layout.plan_writes(files.images(), bad_blocks=[3])
        self.assertEqual(boot1["physical_blocks"], [2, 6])
        self.assertEqual(boot1["physical_block_list"], [2, 4, 5, 6])
        self.assertEqual(linux["physical_blocks"], [9, 504])

    def test_lba_beyond_capacity_is_refused(self):
        offset = 0x7FE00 * 512
        with self.assertRaisesRegex(ValueError, r"\(b\)"):
            layout.plan_writes([nand(BLOCK, offset)])
        with self.assertRaisesRegex(ValueError, r"\(b\).*0x7fe00"):
            layout.plan_image(nand(BLOCK, offset), layout.build_table(), write_limit=1 << 40)

    def test_too_many_bad_blocks_reach_boot0(self):
        table = layout.build_table(range(10, 1900))
        with self.assertRaisesRegex(ValueError, r"\(a\)"):
            layout.plan_image(nand(BLOCK, 200 * BLOCK), table)

    def test_block_overlap_is_refused(self):
        images = [nand(1000, 0, "A"), nand(1000, BLOCK, "B")]
        layout.plan_writes(images)
        with self.assertRaisesRegex(ValueError, r"\(d\)"):
            layout.plan_writes([nand(BLOCK + 512, 0, "A"), nand(1000, BLOCK, "B")])

    def test_boot0_image_is_refused(self):
        with self.assertRaisesRegex(ValueError, r"\(e\)"):
            layout.plan_writes([nand(0x40000, 0, "BXCBOOT0V16.BIN", kind="boot0")])
        with self.assertRaisesRegex(ValueError, r"\(e\)"):
            layout.transfer_chunks(nand(0x40000, 0, kind="boot0"), 64)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "BXCBOOT0V16.BIN"
            path.write_bytes(b"\xff" * 0x40000)
            with self.assertRaisesRegex(ValueError, r"\(e\)"):
                layout.plan_writes([vendor.load_image(path)])


BLOCK = layout.BLOCK_SIZE


class ChunkTests(unittest.TestCase):
    SIZES = (4, 29, 30, 31, 32, 64, 120, 128, 240, 255, 256, 1024)

    def assert_rules(self, image, chunks):
        expected = list(range(image.usb_offset // 512,
                              image.usb_offset // 512 + layout._padded_sectors(len(image.data))))
        self.assertEqual([lba for start, count in chunks for lba in range(start, start + count)], expected)
        blocks_seen = set()
        for lba, count in chunks:
            self.assertEqual(lba // 256, (lba + count - 1) // 256)
            self.assertEqual(lba % 4, 0)
            self.assertEqual(count % 4, 0)
            self.assertNotEqual(lba, 170)
            self.assertNotIn(count, (30, 31))
            if lba // 256 not in blocks_seen:
                self.assertEqual(lba % 256, 0, "first chunk of a block must start at page 0")
                blocks_seen.add(lba // 256)

    def test_chunks_never_cross_blocks(self):
        for size in (1, 2048, 120 * 1024, BLOCK, BLOCK + 1, 512 * 1024, 3 * BLOCK - 700):
            for max_sectors in self.SIZES:
                with self.subTest(size=size, max_sectors=max_sectors):
                    image = nand(size, BLOCK * 6)
                    self.assert_rules(image, layout.transfer_chunks(image, max_sectors))

    def test_linux_62mib_chunks(self):
        image = nand(62 * MIB, 0xC0000)
        chunks = layout.transfer_chunks(image, 256)
        self.assertEqual(len(chunks), 496)
        self.assert_rules(image, chunks)

    def test_last_page_is_padded(self):
        self.assertEqual(layout.transfer_chunks(nand(513, 0), 256), [(0, 4)])

    def test_check_chunk_refusals(self):
        for lba, count in [(170, 4), (0, 30), (0, 31), (2, 4), (0, 6), (252, 8), (0, 0)]:
            with self.subTest(lba=lba, count=count), self.assertRaises(ValueError):
                layout.check_chunk(lba, count)
        layout.check_chunk(168, 4)
        layout.check_chunk(0, 256)

    def test_refuses_unaligned_and_tiny_max(self):
        with self.assertRaises(ValueError):
            layout.transfer_chunks(nand(100, 0x1000), 64)
        with self.assertRaises(ValueError):
            layout.transfer_chunks(nand(100, 0), 3)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class PlanCliTests(unittest.TestCase):
    def test_plan_cli_shows_layout(self):
        with Images(("BOOT1.ROM", 512 * 1024, 0), ("LINUX.DAT", 2 * MIB, 0xC0000)) as files:
            code, out, err = run(["plan", "--ini", str(files.ini), "--bad-blocks", "3"])
        self.assertEqual(code, 0, err)
        first = json.loads(out[:out.rindex("]") + 1])[0]
        self.assertEqual(first["mapping"], layout.MAPPING_NOTE)
        self.assertEqual(first["bad_blocks"], [3])
        self.assertEqual(first["physical_offset"], "UNVERIFIED")
        self.assertEqual(first["layout"]["physical_blocks"], [2, 6])
        self.assertEqual(first["layout"]["chunks"], 4)
        self.assertEqual(first["layout"]["mtd_partitions"], ["mtd1 (Linux Bootloader)"])

    def test_plan_cli_refuses_unaligned(self):
        with Images(("BOOT1.ROM", 4096, 0x1000)) as files:
            code, _, err = run(["plan", "--ini", str(files.ini)])
        self.assertEqual(code, 1)
        self.assertIn("(c)", err)


if __name__ == "__main__":
    unittest.main()
