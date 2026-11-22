"""Ported from 30_TEST/20_ALL-Backup/test_backup.py (35 tests).

Mapping: backup.<name> -> dump.<name>; backup.block_number -> device.block_number;
PREPATCH_* -> DISK_RESPONSE_*; backup.main(["backup"|"prepatch"|"extract", ...]) ->
cli.main(["dump", "--source", "filedumper"|"disk-response"|"image", ...]).
"""

import ctypes
import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli  # noqa: E402
from hpprime import device, dump  # noqa: E402


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "capture"
        self.regions = (dump.Region("nanddump.bin", 512, 1024), dump.Region("oobdump.bin", 1536, 512))
        self.data = bytes(range(256)) * 8
        self.reads = []

    def read_at(self, offset, size):
        self.reads.append((offset, size))
        return self.data[offset:offset + size]

    def capture(self, reader=None):
        return dump.acquire(reader or self.read_at, self.output, {"kind": "synthetic"}, self.regions, lambda message: None)

    def test_complete_reads_twice_and_verifies_every_byte(self):
        self.capture()
        self.assertEqual(self.reads, [(512, 1024), (512, 1024), (1536, 512), (1536, 512)])
        self.assertEqual((self.output / "nanddump.bin").read_bytes(), self.data[512:1536])
        self.assertEqual((self.output / "oobdump.bin").read_bytes(), self.data[1536:2048])
        self.assertEqual(dump.verify_backup(self.output, self.regions)["status"], "complete")
        self.assertEqual(self.output.stat().st_mode & 0o077, 0)

    def test_short_read_is_failed_and_preserved(self):
        with self.assertRaises(OSError):
            self.capture(lambda offset, size: b"")
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertTrue((self.output / "nanddump.bin.partial").exists())
        with self.assertRaises(ValueError):
            dump.verify_backup(self.output, self.regions)

    def test_second_read_mismatch_is_failed(self):
        def changing_reader(offset, size):
            payload = self.read_at(offset, size)
            return payload if len(self.reads) == 1 else b"\xff" * size
        with self.assertRaises(OSError):
            self.capture(changing_reader)
        self.assertFalse((self.output / "nanddump.bin").exists())

    def test_existing_directory_is_never_overwritten(self):
        self.capture()
        before = (self.output / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual((self.output / "manifest.json").read_bytes(), before)

    def test_same_size_corruption_is_detected(self):
        self.capture()
        (self.output / "oobdump.bin").write_bytes(b"\xff" * 512)
        with self.assertRaises(ValueError):
            dump.verify_backup(self.output, self.regions)

    def test_missing_manifest_region_is_rejected(self):
        self.capture()
        path = self.output / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"].pop()
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            dump.verify_backup(self.output, self.regions)

    def test_second_read_short_is_failed(self):
        def reader(offset, size):
            payload = self.read_at(offset, size)
            return payload if len(self.reads) == 1 else payload[:-1]
        with self.assertRaises(OSError):
            self.capture(reader)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text())["status"], "failed")

    def test_interrupt_does_not_complete_backup(self):
        def reader(offset, size):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.capture(reader)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text())["status"], "failed")

    def test_insufficient_space_prevents_reads(self):
        usage = dump.shutil.disk_usage(self.output.parent)
        with patch.object(dump.shutil, "disk_usage", return_value=usage._replace(free=0)), self.assertRaises(OSError):
            self.capture()
        self.assertEqual(self.reads, [])
        self.assertFalse(self.output.exists())

    def test_linked_dump_is_rejected(self):
        self.capture()
        saved = self.output / "oobdump.bin"
        moved = self.output / "other.bin"
        saved.rename(moved)
        saved.symlink_to(moved)
        with self.assertRaises(ValueError):
            dump.verify_backup(self.output, self.regions)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.source = object.__new__(dump.ReadOnlySource)
        self.source.fd = 123
        self.source.image = False

    def test_only_read10_in_data_window(self):
        def transfer(descriptor, request, header):
            self.assertEqual(request, dump.SG_IO)
            self.assertEqual(header.dxfer_direction, dump.SG_DXFER_FROM_DEV)
            self.assertEqual(header.timeout, 10000)
            cdb = ctypes.string_at(header.cmdp, header.cmd_len)
            self.assertEqual(struct.unpack(">BBIBHB", cdb), (0x28, 0, 256 * 2048, 0, 32, 0))
            ctypes.memset(header.dxferp, 0x5a, header.dxfer_len)
            return 0
        with patch.object(dump.fcntl, "ioctl", side_effect=transfer):
            self.assertEqual(self.source.read_at(256 * dump.MIB, 16384), b"Z" * 16384)

    def test_prepatch_read10_start_control_overlap_and_end(self):
        self.source.regions = dump.DISK_RESPONSE_REGIONS
        for offset in (0, 160 * 512, dump.DISK_RESPONSE_SIZE - dump.CHUNK_SIZE):
            def transfer(descriptor, request, header):
                self.assertEqual(request, dump.SG_IO)
                self.assertEqual(header.dxfer_direction, dump.SG_DXFER_FROM_DEV)
                cdb = ctypes.string_at(header.cmdp, header.cmd_len)
                self.assertEqual(struct.unpack(">BBIBHB", cdb), (0x28, 0, offset // 512, 0, 32, 0))
                ctypes.memset(header.dxferp, 0x5a, header.dxfer_len)
                return 0
            with self.subTest(offset=offset), patch.object(dump.fcntl, "ioctl", side_effect=transfer):
                self.assertEqual(self.source.read_at(offset, dump.CHUNK_SIZE), b"Z" * dump.CHUNK_SIZE)

    def test_control_lba_alignment_and_range_rejected_before_ioctl(self):
        with patch.object(dump.fcntl, "ioctl") as transfer:
            for offset, size in [(170 * 512, 30 * 512), (0, 512), (256 * dump.MIB + 1, 512), (520 * dump.MIB, 512), (256 * dump.MIB, 32768), (256 * dump.MIB, 0)]:
                with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                    self.source.read_at(offset, size)
            transfer.assert_not_called()

    def test_scsi_residual_and_errors_rejected(self):
        for field in ("resid", "status", "host_status", "driver_status", "info"):
            def transfer(descriptor, request, header):
                setattr(header, field, 1)
                return 0
            with self.subTest(field=field), patch.object(dump.fcntl, "ioctl", side_effect=transfer), self.assertRaises(OSError):
                self.source.read_at(256 * dump.MIB, 512)

    def test_image_open_is_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "disk.img"
            with path.open("wb") as stream:
                stream.truncate(dump.DISK_SIZE)
            original_open = os.open
            with patch.object(dump.os, "open", wraps=original_open) as opened:
                with dump.ReadOnlySource(path, image=True) as source:
                    self.assertEqual(source.read_at(256 * dump.MIB, 512), bytes(512))
                flags = opened.call_args.args[1]
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)

    def test_missing_confirmations_never_opens_device(self):
        with patch.object(dump, "ReadOnlySource") as source, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["dump", "--source", "filedumper", "--device", "/dev/nonexistent", "--execute"]), 1)
            source.assert_not_called()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.usb = self.root / "usb"
        self.interface = self.usb / "1-1:1.0"
        self.node = self.interface / "block/sdz"
        (self.node / "queue").mkdir(parents=True)
        (self.node / "holders").mkdir()
        self.sys_devices = self.root / "sysdev"
        self.sys_devices.mkdir()
        (self.sys_devices / "8:0").symlink_to(self.node)
        (self.usb / "idVendor").write_text("03f0\n")
        (self.usb / "idProduct").write_text("1541\n")
        (self.interface / "bInterfaceClass").write_text("08\n")
        (self.node / "size").write_text(str(dump.DISK_SIZE // 512))
        (self.node / "queue/logical_block_size").write_text("512\n")
        (self.node / "dev").write_text("8:0\n")
        self.mountinfo = self.root / "mountinfo"
        self.mountinfo.write_text("")
        self.swaps = self.root / "swaps"
        self.swaps.write_text("Filename Type Size Used Priority\n")
        mocked_number = patch.object(device, "block_number", return_value=os.makedev(8, 0))
        mocked_number.start()
        self.addCleanup(mocked_number.stop)

    def inspect(self, profile=dump.PROFILE):
        return dump.inspect_device(Path("/dev/fake-hpprime"), self.sys_devices, self.mountinfo, self.swaps, profile=profile)

    def test_matching_usb_and_capacity(self):
        self.assertEqual(self.inspect()["capacity"], dump.DISK_SIZE)

    def test_prepatch_capacity_requires_matching_profile(self):
        (self.node / "size").write_text(str(dump.DISK_RESPONSE_SIZE // 512))
        self.assertEqual(self.inspect(dump.DISK_RESPONSE_PROFILE)["capacity"], dump.DISK_RESPONSE_SIZE)
        with self.assertRaises(ValueError):
            self.inspect()
        (self.node / "size").write_text(str(dump.DISK_SIZE // 512))
        with self.assertRaises(ValueError):
            self.inspect(dump.DISK_RESPONSE_PROFILE)

    def test_prepatch_mount_guard_is_preserved(self):
        (self.node / "size").write_text(str(dump.DISK_RESPONSE_SIZE // 512))
        self.mountinfo.write_text("42 1 8:0 / /media rw - vfat /dev/sdz rw\n")
        with self.assertRaises(ValueError):
            self.inspect(dump.DISK_RESPONSE_PROFILE)

    def test_prepatch_preflight_never_opens_source_or_creates_output(self):
        (self.node / "size").write_text(str(dump.DISK_RESPONSE_SIZE // 512))
        (self.node / "ro").write_text("1\n")
        metadata = self.inspect(dump.DISK_RESPONSE_PROFILE)
        output = self.root / "uncreated"
        with patch.object(dump, "inspect_device", return_value=metadata) as inspect, patch.object(dump, "ReadOnlySource") as source, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["dump", "--source", "disk-response", "--device", "/dev/fake-hpprime", "--confirm-g1", "--confirm-recovery-v16", "--output-root", str(output)]), 0)
            inspect.assert_called_with(Path("/dev/fake-hpprime"), profile=dump.DISK_RESPONSE_PROFILE)
            source.assert_not_called()
        self.assertFalse(output.exists())

    def test_prepatch_requires_kernel_read_only_disk_and_partitions(self):
        (self.node / "size").write_text(str(dump.DISK_RESPONSE_SIZE // 512))
        partition = self.node / "sdz1"
        partition.mkdir()
        (partition / "partition").write_text("1\n")
        (partition / "dev").write_text("8:1\n")
        (partition / "holders").mkdir()
        metadata = self.inspect(dump.DISK_RESPONSE_PROFILE)
        for disk_ro, part_ro in (("0", "1"), ("1", "0"), (None, "1")):
            with self.subTest(disk=disk_ro, partition=part_ro):
                if disk_ro is None:
                    (self.node / "ro").unlink(missing_ok=True)
                else:
                    (self.node / "ro").write_text(disk_ro + "\n")
                (partition / "ro").write_text(part_ro + "\n")
                with self.assertRaises(ValueError):
                    dump.require_kernel_read_only(metadata)
        (self.node / "ro").write_text("1\n")
        (partition / "ro").write_text("1\n")
        dump.require_kernel_read_only(metadata)

    def test_prepatch_writable_disk_never_opens_device(self):
        (self.node / "size").write_text(str(dump.DISK_RESPONSE_SIZE // 512))
        (self.node / "ro").write_text("0\n")
        metadata = self.inspect(dump.DISK_RESPONSE_PROFILE)
        with patch.object(dump, "inspect_device", return_value=metadata), patch.object(dump, "ReadOnlySource") as source, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["dump", "--source", "disk-response", "--device", "/dev/fake-hpprime", "--confirm-g1", "--confirm-recovery-v16", "--output-root", str(self.root / "out"), "--execute"]), 1)
            source.assert_not_called()

    def test_wrong_usb_is_rejected(self):
        (self.usb / "idVendor").write_text("1234")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_unpatched_capacity_is_rejected(self):
        (self.node / "size").write_text(str(255 * dump.MIB // 512))
        with self.assertRaises(ValueError):
            self.inspect()

    def test_hid_interface_is_rejected(self):
        (self.interface / "bInterfaceClass").write_text("03")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_partition_is_rejected(self):
        (self.node / "partition").write_text("1")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_mounted_disk_is_rejected(self):
        self.mountinfo.write_text("42 1 8:0 / /media rw - vfat /dev/sdz rw\n")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_mounted_child_partition_is_rejected(self):
        child = self.node / "sdz1"
        child.mkdir()
        (child / "partition").write_text("1")
        (child / "dev").write_text("8:1")
        self.mountinfo.write_text("42 1 8:1 / /media rw - vfat /dev/sdz1 rw\n")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_holder_is_rejected(self):
        (self.node / "holders/dm-0").touch()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_missing_metadata_is_rejected(self):
        (self.node / "size").unlink()
        with self.assertRaises(OSError):
            self.inspect()

    def test_non_512_sector_size_is_rejected(self):
        (self.node / "queue/logical_block_size").write_text("4096")
        with self.assertRaises(ValueError):
            self.inspect()
