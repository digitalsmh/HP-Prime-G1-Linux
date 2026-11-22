"""Ported from 30_TEST/20_ALL-Backup/test_backup.py, class FullSizeOfflineTests (4 tests).

Split from tests/test_dump.py only to keep each file under the 300-line edit limit.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli  # noqa: E402
from hpprime import dump  # noqa: E402


class FullSizeOfflineTests(unittest.TestCase):
    def test_prepatch_full_size_read_plan_and_hash_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "prepatch.img"
            with image.open("wb") as stream:
                stream.truncate(dump.DISK_RESPONSE_SIZE)
                stream.write(b"BOOT1_NOT_BOOT0")
                stream.seek(dump.DISK_RESPONSE_SIZE - 512)
                stream.write(b"Z" * 512)
            output = root / "capture"
            calls = []
            with dump.ReadOnlySource(image, image=True, profile=dump.DISK_RESPONSE_PROFILE) as source:
                def reader(offset, size):
                    calls.append((offset, size))
                    return source.read_at(offset, size)
                dump.acquire(reader, output, source.metadata, profile=dump.DISK_RESPONSE_PROFILE, report=lambda message: None)
            manifest = dump.verify_backup(output)
            self.assertEqual(manifest["profile"], dump.DISK_RESPONSE_PROFILE)
            self.assertFalse(manifest["original_pre_patch_boot0_recoverable"])
            self.assertEqual((output / "recovery-disk.bin").stat().st_size, dump.DISK_RESPONSE_SIZE)
            self.assertEqual(dump.hash_file(image), manifest["files"][0]["sha256"])
            self.assertEqual(len(calls), 2 * dump.DISK_RESPONSE_SIZE // dump.CHUNK_SIZE)
            self.assertTrue(all(offset // 512 != 170 and size == dump.CHUNK_SIZE for offset, size in calls))
            with (output / "recovery-disk.bin").open("r+b") as stream:
                stream.write(b"!")
            with self.assertRaises(ValueError):
                dump.verify_backup(output)

    def test_prepatch_transport_blocks_control_requests(self):
        source = object.__new__(dump.ReadOnlySource)
        source.fd = 123
        source.image = False
        source.regions = dump.DISK_RESPONSE_REGIONS
        with patch.object(dump.fcntl, "ioctl") as transfer:
            for offset, size in [(170 * 512, 512), (170 * 512, 30 * 512), (170 * 512, 31 * 512), (0, 30 * 512), (0, 31 * 512), (dump.DISK_RESPONSE_SIZE, 512)]:
                with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                    source.read_at(offset, size)
            transfer.assert_not_called()

    def test_prepatch_missing_confirmation_never_opens_device(self):
        with patch.object(dump, "ReadOnlySource") as source, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["dump", "--source", "disk-response", "--device", "/dev/unused", "--confirm-g1", "--execute"]), 1)
            source.assert_not_called()

    def test_real_layout_cli_extract_verify_and_tamper(self):
        with tempfile.TemporaryDirectory(prefix="hpprime-offline-test-") as temporary:
            root = Path(temporary)
            image = root / "synthetic.img"
            destination = root / "results"
            with image.open("wb") as stream:
                stream.truncate(dump.DISK_SIZE)
                stream.write(b"NOT_NAND_START")
                for index, region in enumerate(dump.REGIONS):
                    stream.seek(region.offset)
                    stream.write(bytes([0x31 + index]) * 512)
                    stream.seek(region.offset + region.size - 512)
                    stream.write(bytes([0x71 + index]) * 512)
            extract = ["dump", "--source", "image", "--image", str(image), "--output-root", str(destination)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(extract), 0)
                self.assertFalse(destination.exists())
                self.assertEqual(cli.main([*extract, "--execute"]), 0)
            outputs = list(destination.iterdir())
            self.assertEqual(len(outputs), 1)
            directory = outputs[0]
            self.assertTrue(directory.name.startswith("image-extract_"))
            self.assertEqual(dump.verify_backup(directory)["source"]["kind"], "image-file")
            with image.open("rb") as original:
                for region in dump.REGIONS:
                    saved = directory / region.name
                    self.assertEqual(saved.stat().st_size, region.size)
                    original.seek(region.offset)
                    with saved.open("rb") as stream:
                        for position in range(0, region.size, dump.MIB):
                            size = min(dump.MIB, region.size - position)
                            self.assertEqual(stream.read(size), original.read(size))
                        self.assertEqual(stream.read(1), b"")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["verify", str(directory)]), 0)
            with (directory / "oobdump.bin").open("r+b") as stream:
                stream.write(b"!")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["verify", str(directory)]), 1)


if __name__ == "__main__":
    unittest.main()
