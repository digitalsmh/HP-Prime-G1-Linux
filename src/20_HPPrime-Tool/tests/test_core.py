"""New tests for hpprime.transport, hpprime.log and hpprime.device (offline only)."""

import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpprime import device, log, transport  # noqa: E402

MIB = 1024 * 1024


class TransportCopyTests(unittest.TestCase):
    def test_read10_cdb_layout(self):
        self.assertEqual(transport.build_scsi_rw10_cdb(0x28, 0x12345678, 32),
                         struct.pack(">BBIBHB", 0x28, 0, 0x12345678, 0, 32, 0))

    def test_cdb_rejects_other_opcodes_and_ranges(self):
        for arguments in ((0x88, 0, 1), (0x28, -1, 1), (0x28, 0, 0), (0x28, 0, 0x10000)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                transport.build_scsi_rw10_cdb(*arguments)

    def test_sg_io_constants_and_struct_size(self):
        self.assertEqual(transport.SG_IO, 0x2285)
        self.assertEqual(transport.SG_DXFER_FROM_DEV, -3)
        self.assertEqual(transport.SG_DXFER_TO_DEV, -2)
        self.assertEqual(transport.SgIoHdr().interface_id, 0)
        if struct.calcsize("P") == 8:
            self.assertEqual(ctypes_sizeof(transport.SgIoHdr), 88)

    def test_detect_reads_fake_sysfs_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device_dir = root / "3-10"
            interface = device_dir / "3-10:1.0"
            (interface / "host/block/sdq").mkdir(parents=True)
            (device_dir / "idVendor").write_text("03f0\n")
            (device_dir / "idProduct").write_text("1541\n")
            (interface / "bInterfaceClass").write_text("08\n")
            other = root / "1-1"
            other.mkdir()
            (other / "idVendor").write_text("1234\n")
            (other / "idProduct").write_text("0001\n")
            found = transport.detect_hp_prime_usb(root)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["usb_id"], "03f0:1541")
            self.assertEqual(found[0]["block_devices"], ["/dev/sdq"])
            self.assertTrue(found[0]["mode"].startswith("RECOVERY_MSC"))
            self.assertEqual(transport.detect_hp_prime_usb(root / "missing"), [])


def ctypes_sizeof(structure):
    import ctypes
    return ctypes.sizeof(structure)


class LogTests(unittest.TestCase):
    def test_log_file_records_command_output_and_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "log"
            with contextlib.redirect_stdout(io.StringIO()) as console:
                with self.assertRaises(SystemExit):
                    with log.execution_log("UnitTest", directory, ["cli.py", "status"]) as filename:
                        print("HELLO")
                        raise SystemExit(3)
            path = Path(filename)
            self.assertEqual(path.parent, directory)
            self.assertIn("_UnitTest_", path.name)
            text = path.read_text()
            self.assertIn("HELLO", text)
            self.assertIn('COMMAND: ["cli.py", "status"]', text)
            self.assertIn("EXIT_CODE: 3", text)
            self.assertIn("HELLO", console.getvalue())

    def test_default_log_directory_is_30_test_log(self):
        self.assertEqual(log.LOG_DIRECTORY.parts[-2:], ("30_TEST", "log"))


class UnifiedDeviceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.usb = self.root / "usb"
        self.interface = self.usb / "1-1:1.0"
        self.node = self.interface / "block/sdz"
        (self.node / "queue").mkdir(parents=True)
        (self.node / "holders").mkdir()
        self.sys_devices = self.root / "sysdev"
        self.sys_devices.mkdir()
        (self.sys_devices / "8:0").symlink_to(self.node)
        (self.usb / "idVendor").write_text("03F0\n")
        (self.usb / "idProduct").write_text("0441\n")
        (self.interface / "bInterfaceClass").write_text("08\n")
        (self.node / "queue/logical_block_size").write_text("512\n")
        (self.node / "dev").write_text("8:0\n")
        (self.root / "mountinfo").write_text("")
        (self.root / "swaps").write_text("Filename Type Size Used Priority\n")
        mocked = patch.object(device, "block_number", return_value=os.makedev(8, 0))
        mocked.start()
        self.addCleanup(mocked.stop)

    def inspect(self, size, capacities):
        (self.node / "size").write_text(str(size // 512))
        return device.inspect_device(Path("/dev/fake"), self.sys_devices, self.root / "mountinfo",
                                     self.root / "swaps", capacities=capacities)

    def test_uppercase_vid_is_normalised_and_missing_busnum_is_none(self):
        metadata = self.inspect(528 * MIB, {528 * MIB})
        self.assertEqual(metadata["usb_id"], "03f0:0441")
        self.assertIsNone(metadata["usb_busnum"])

    def test_capacity_set_is_enforced(self):
        both = {528 * MIB, 256 * MIB - 0x40000}
        self.assertEqual(self.inspect(256 * MIB - 0x40000, both)["capacity"], 256 * MIB - 0x40000)
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.inspect(256 * MIB, both)

    def test_kernel_read_only_flag_required(self):
        metadata = self.inspect(528 * MIB, {528 * MIB})
        with self.assertRaises(ValueError):
            device.require_kernel_read_only(metadata)
        (self.node / "ro").write_text("1\n")
        device.require_kernel_read_only(metadata)


if __name__ == "__main__":
    unittest.main()
