"""Ported from 50_Recorvery/test_usbtool.py (31 tests).

Mapping: usbtool.<protocol name> -> vendor.<name>; usbtool.inspect_device -> device.inspect_device
(capacities = the two former recovery capacities); usbtool.main -> cli.main;
usbtool.LinuxTransport patched as vendor.LinuxTransport.
Changed assertions (reported): two plan tests now expect "physical_offset" == "UNVERIFIED"
instead of computed values (0x100000 and 0), because the +0x40000 rule was withdrawn.
"""

import contextlib
import io
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli  # noqa: E402
from hpprime import device, vendor  # noqa: E402

RECOVERY_CAPACITIES = {256 * 1024 * 1024 - 0x40000, 528 * 1024 * 1024}


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "BOOT1.ROM"
        self.image.write_bytes(b"test" * 128)

    def ini(self, body):
        path = self.root / "usbtool.ini"
        path.write_text("[Update Infomation]\n" + body)
        return path

    def test_windows_relative_path_and_hex_offset(self):
        images = vendor.read_ini(self.ini("UpdateItems=1\nUpdateItem00=.\\BOOT1.ROM 0 000C0000\n"))
        summary = images[0].summary()
        self.assertEqual(summary["usb_offset"], 0xC0000)
        self.assertEqual(summary["physical_offset"], "UNVERIFIED")

    def test_missing_item_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "disagree"):
            vendor.read_ini(self.ini("UpdateItems=2\nUpdateItem00=.\\BOOT1.ROM 0 0\n"))

    def test_boot0_does_not_use_nand_offset(self):
        image = self.root / "BXCBOOT0.BIN"
        image.write_bytes(b"boot0")
        summary = vendor.load_image(image).summary()
        self.assertEqual(summary["kind"], "boot0")
        self.assertEqual(summary["physical_offset"], "UNVERIFIED")
        self.assertIsNone(summary["usb_offset"])

    def test_overlapping_regions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            vendor.read_ini(self.ini("UpdateItems=2\nUpdateItem00=BOOT1.ROM 0 0\nUpdateItem01=BOOT1.ROM 0 0\n"))

    def test_user_data_boundary_is_protected(self):
        with self.assertRaisesRegex(ValueError, "boundary"):
            vendor.load_image(self.image, 0x3EC0000)

    def test_nonzero_drive_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "single-device"):
            vendor.read_ini(self.ini("UpdateItems=1\nUpdateItem00=BOOT1.ROM 1 0\n"))


class DeviceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.target = self.root / "device"
        self.target.touch()
        self.sys_root = self.root / "sys"
        self.proc_root = self.root / "proc"
        self.usb = self.sys_root / "devices/usb3/3-10"
        self.interface = self.usb / "3-10:1.0"
        self.node = self.interface / "host/block/sdb"
        for name, value in {
            "idVendor": "03f0", "idProduct": "1541", "busnum": "3", "devnum": "10"
        }.items():
            self.write(self.usb / name, value)
        self.write(self.interface / "bInterfaceClass", "08")
        self.write(self.node / "size", str(528 * 1024 * 1024 // 512))
        self.write(self.node / "queue/logical_block_size", "512")
        self.write(self.node / "dev", "8:16")
        (self.node / "holders").mkdir()
        self.write(self.proc_root / "self/mountinfo", "")
        self.write(self.proc_root / "swaps", "Filename Type Size Used Priority\n")
        link = self.sys_root / "dev/block/8:16"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.node, target_is_directory=True)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def call(self):
        return device.inspect_device(self.target, self.sys_root / "dev/block", self.proc_root / "self/mountinfo",
                                     self.proc_root / "swaps", capacities=RECOVERY_CAPACITIES)

    def inspect(self):
        original = Path.stat

        def fake_stat(path, *args, **kwargs):
            if path == self.target:
                return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 16))
            return original(path, *args, **kwargs)

        with patch.object(Path, "stat", fake_stat):
            return self.call()

    def test_valid_unmounted_mass_storage_device(self):
        result = self.inspect()
        self.assertEqual(result["usb_id"], "03f0:1541")
        self.assertEqual(result["capacity"], 528 * 1024 * 1024)

    def test_regular_file_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "whole block"):
            self.call()

    def test_partition_target_is_rejected(self):
        self.write(self.node / "partition", "1")
        with self.assertRaisesRegex(ValueError, "partition"):
            self.inspect()

    def test_mounted_target_is_rejected(self):
        self.write(self.proc_root / "self/mountinfo", "36 25 8:16 / /mnt rw - vfat /dev/sdb rw\n")
        with self.assertRaisesRegex(ValueError, "mounted"):
            self.inspect()

    def test_mounted_child_partition_is_rejected(self):
        child = self.node / "sdb1"
        self.write(child / "partition", "1")
        self.write(child / "dev", "8:17")
        (child / "holders").mkdir()
        self.write(self.proc_root / "self/mountinfo", "36 25 8:17 / /mnt rw - vfat /dev/sdb1 rw\n")
        with self.assertRaisesRegex(ValueError, "mounted"):
            self.inspect()

    def test_active_holder_is_rejected(self):
        (self.node / "holders/dm-0").mkdir()
        with self.assertRaisesRegex(ValueError, "holder"):
            self.inspect()

    def test_active_swap_is_rejected(self):
        self.write(self.proc_root / "swaps", f"Filename Type Size Used Priority\n{self.target} partition 1 0 -2\n")
        with self.assertRaisesRegex(ValueError, "swap"):
            self.inspect()

    def test_wrong_usb_vendor_is_rejected(self):
        self.write(self.usb / "idVendor", "1234")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.inspect()

    def test_hid_interface_is_rejected(self):
        self.write(self.interface / "bInterfaceClass", "03")
        with self.assertRaisesRegex(ValueError, "mass-storage"):
            self.inspect()

    def test_wrong_capacity_is_rejected(self):
        self.write(self.node / "size", "1024")
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.inspect()
