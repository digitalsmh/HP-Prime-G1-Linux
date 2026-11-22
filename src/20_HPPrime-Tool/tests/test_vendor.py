"""Ported from 50_Recorvery/test_usbtool.py, class VendorTests (15 tests).

`--execute` now always fails with BLOCKED_REASON; the original assertions (exit code 1 and
LinuxTransport never constructed) still hold unchanged.
"""

import contextlib
import io
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli  # noqa: E402
from hpprime import vendor  # noqa: E402


class VendorTests(unittest.TestCase):
    def image(self, variant="v16"):
        return vendor.load_image(vendor.REFERENCE / f"BXCBOOT0{variant.upper()}.bin", boot0=True)

    def test_exact_vendor_cdb_and_handshake(self):
        self.assertEqual(vendor.VENDOR_SEND, b"\x88" + bytes(9))
        self.assertEqual(vendor.VENDOR_RECEIVE, b"\x89" + bytes(9))
        self.assertEqual(vendor.vendor_packet(vendor.HANDSHAKE, 0xBEAD),
                         bytes.fromhex("cd be 00 00 ad be 00 00") + bytes(256))

    def test_both_reference_images_are_transferred_exactly(self):
        for variant in ("v13", "v16"):
            with self.subTest(variant=variant):
                image = self.image(variant)
                transport = vendor.SimulatedTransport()
                result = vendor.execute_packets(transport, vendor.boot0_packets(image, variant),
                                                notify=lambda message: None, delay=lambda seconds: None)
                self.assertEqual(bytes(transport.uploaded), image.data)
                self.assertEqual(result["uploaded_bytes"], 262144)
                self.assertTrue(result["commit_acknowledged"])
                self.assertEqual(result["commands"], 1128)

    def test_bank_boundary_packets_and_commit(self):
        packets = list(vendor.boot0_packets(self.image(), "v16"))
        uploads = [packet for packet in packets if struct.unpack_from("<I", packet)[0] == vendor.UPLOAD]
        self.assertEqual(uploads[255][:8], bytes.fromhex("c6 be 00 00 00 01 00 ff"))
        self.assertEqual(uploads[256][:8], bytes.fromhex("c6 be 00 00 00 01 00 00"))
        self.assertEqual(packets[-2][:8], bytes.fromhex("c7 be 00 00 00 00 04 00"))
        self.assertEqual(packets[-1][:8], bytes.fromhex("dc be 00 00 64 00 00 00"))

    def test_wrong_variant_is_rejected_before_io(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            list(vendor.boot0_packets(self.image(), "v13"))

    def test_bad_ack_stops_without_retry_or_commit(self):
        transport = vendor.SimulatedTransport()
        original = transport.transfer

        def corrupt(cdb, data, timeout):
            response = original(cdb, data, timeout)
            return bytes(264) if cdb == vendor.VENDOR_RECEIVE else response

        transport.transfer = corrupt
        with self.assertRaisesRegex(OSError, "acknowledgement"):
            vendor.execute_packets(transport, vendor.boot0_packets(self.image(), "v16"),
                                   notify=lambda message: None, delay=lambda seconds: None)
        self.assertEqual(transport.calls, 2)
        self.assertFalse(transport.committed)

    def test_commit_failure_does_not_send_progress_or_reboot(self):
        transport = vendor.SimulatedTransport()
        original = transport.transfer
        sent = []

        def fail_commit(cdb, data, timeout):
            if cdb == vendor.VENDOR_SEND:
                command = struct.unpack_from("<I", data)[0]
                sent.append(command)
                if command == vendor.COMMIT:
                    raise OSError("Commit outcome unknown")
            return original(cdb, data, timeout)

        transport.transfer = fail_commit
        with self.assertRaisesRegex(OSError, "unknown"):
            vendor.execute_packets(transport, vendor.boot0_packets(self.image(), "v16", reboot=True),
                                   notify=lambda message: None, delay=lambda seconds: None)
        self.assertEqual(sent[-1], vendor.COMMIT)
        self.assertEqual(sent.count(vendor.COMMIT), 1)
        self.assertNotIn(vendor.REBOOT, sent)

    def test_default_cli_never_opens_device(self):
        with patch.object(vendor, "LinuxTransport", side_effect=AssertionError("Live access")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["boot0", "--variant", "v16", "--device", "/dev/nonexistent"]), 0)

    def test_execute_without_confirmations_never_opens_device(self):
        with patch.object(vendor, "LinuxTransport", side_effect=AssertionError("Live access")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["boot0", "--variant", "v16", "--execute"]), 1)

    def test_sg_io_short_transfer_is_failure(self):
        transport = object.__new__(vendor.LinuxTransport)
        transport.fd = -1

        def short_transfer(descriptor, request, header):
            header.resid = 1
            return 0

        with patch.object(vendor.fcntl, "ioctl", side_effect=short_transfer):
            with self.assertRaisesRegex(OSError, "resid=1"):
                transport.transfer(vendor.VENDOR_RECEIVE, 264, 2000)

    def test_sg_io_rejects_standard_write10(self):
        transport = object.__new__(vendor.LinuxTransport)
        transport.fd = -1
        with patch.object(vendor.fcntl, "ioctl", side_effect=AssertionError("Unexpected ioctl")):
            with self.assertRaisesRegex(ValueError, "vendor"):
                transport.transfer(b"\x2a" + bytes(9), bytes(512), 1000)

    def test_sg_io_all_completion_errors_are_fatal(self):
        transport = object.__new__(vendor.LinuxTransport)
        transport.fd = -1
        for field in ("status", "host_status", "driver_status", "info"):
            with self.subTest(field=field):
                def fail(descriptor, request, header):
                    setattr(header, field, 1)
                    return 0

                with patch.object(vendor.fcntl, "ioctl", side_effect=fail):
                    with self.assertRaisesRegex(OSError, "SG_IO failed"):
                        transport.transfer(vendor.VENDOR_RECEIVE, 264, 2000)

    def test_each_execute_confirmation_is_required(self):
        arguments = ["boot0", "--variant", "v16", "--execute", "--device", "/dev/nonexistent",
                     "--hardware-family", "v2", "--expected-sha256", vendor.KNOWN_BOOT0["v16"],
                     "--confirm-g1", "--confirm-fresh-recovery", "--acknowledge-boot0-risk"]
        for option in ("--confirm-g1", "--confirm-fresh-recovery", "--acknowledge-boot0-risk"):
            with self.subTest(option=option), patch.object(vendor, "LinuxTransport", side_effect=AssertionError("Live access")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([argument for argument in arguments if argument != option]), 1)

    def test_wrong_expected_hash_blocks_device_open(self):
        with patch.object(vendor, "LinuxTransport", side_effect=AssertionError("Live access")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["boot0", "--variant", "v16", "--execute", "--device", "/dev/nonexistent",
                                       "--hardware-family", "v2", "--confirm-g1", "--confirm-fresh-recovery",
                                       "--acknowledge-boot0-risk", "--expected-sha256", "wrong"]), 1)

    def test_v13_v2_combination_is_rejected(self):
        with patch.object(vendor, "LinuxTransport", side_effect=AssertionError("Live access")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["boot0", "--variant", "v13", "--hardware-family", "v2"]), 1)

    def test_reboot_only_after_acknowledged_commit(self):
        transport = vendor.SimulatedTransport()
        result = vendor.execute_packets(transport, vendor.boot0_packets(self.image(), "v16", reboot=True),
                                        notify=lambda message: None, delay=lambda seconds: None)
        self.assertTrue(result["commit_acknowledged"])
        self.assertEqual(transport.calls, 2257)


if __name__ == "__main__":
    unittest.main()
