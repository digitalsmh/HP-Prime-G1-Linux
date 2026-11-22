"""New safety tests for W6 blocks (a-e) plus synth-check and CLI wiring. Offline only."""

import contextlib
import io
import json
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import cli  # noqa: E402
from hpprime import BLOCKED_REASON, dump, synth, vendor  # noqa: E402


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class Boot0BlockTests(unittest.TestCase):
    """a. Real boot0 path is refused with the fixed reason; simulation still works."""

    FULL = ["boot0", "--variant", "v16", "--execute", "--device", "/dev/nonexistent",
            "--hardware-family", "v2", "--expected-sha256", vendor.KNOWN_BOOT0["v16"],
            "--confirm-g1", "--confirm-fresh-recovery", "--acknowledge-boot0-risk"]

    def test_reason_string_is_exact(self):
        self.assertEqual(BLOCKED_REASON, "blocked: NAND address mapping unverified; see docs")

    def test_fully_confirmed_execute_is_blocked_before_any_open(self):
        with patch("os.open", side_effect=AssertionError("open")), \
                patch.object(vendor.fcntl, "ioctl", side_effect=AssertionError("ioctl")):
            code, _, err = run(self.FULL)
        self.assertEqual(code, 1)
        self.assertIn(BLOCKED_REASON, err)

    def test_linux_transport_cannot_be_constructed(self):
        with self.assertRaisesRegex(ValueError, re.escape(BLOCKED_REASON)):
            vendor.LinuxTransport(Path("/dev/nonexistent"))

    def test_simulation_still_runs(self):
        code, out, _ = run(["boot0", "--variant", "v16", "--reboot"])
        self.assertEqual(code, 0)
        self.assertIn("OFFLINE_SIMULATION", out)
        self.assertIn('"commit_acknowledged": true', out)
        self.assertIn('"scsi_calls": 2257', out)


class PhysicalOffsetTests(unittest.TestCase):
    """b. Plan output never reports a computed NAND physical offset."""

    def test_plan_cli_prints_unverified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "BOOT1.ROM").write_bytes(b"x" * 1024)
            ini = root / "usbtool.ini"
            ini.write_text("[Update Infomation]\nUpdateItems=1\nUpdateItem00=.\\BOOT1.ROM 0 000C0000\n")
            code, out, _ = run(["plan", "--ini", str(ini)])
        self.assertEqual(code, 0)
        summary = json.loads(out[:out.rindex("]") + 1])[0]
        self.assertEqual(summary["physical_offset"], "UNVERIFIED")
        self.assertEqual(summary["usb_offset"], 0xC0000)
        self.assertNotIn(str(0xC0000 + 0x40000), out)

    def test_boot0_summary_is_unverified(self):
        code, out, _ = run(["boot0", "--variant", "v16"])
        self.assertEqual(code, 0)
        self.assertIn('"physical_offset": "UNVERIFIED"', out)

    def test_no_code_adds_0x40000_to_an_offset(self):
        pattern = re.compile(r"\+\s*0x40000|0x40000\s*\+|usb_offset_to_phys|phys_offset", re.IGNORECASE)
        for path in [TOOL / "cli.py", *sorted((TOOL / "hpprime").glob("*.py"))]:
            with self.subTest(path=path.name):
                self.assertIsNone(pattern.search(path.read_text()), path)


class ControlRequestTests(unittest.TestCase):
    """c. LBA 170 starts and 30/31-sector requests stay refused for every profile."""

    def test_every_profile_refuses_control_shapes(self):
        for name, profile in dump.PROFILES.items():
            for offset, size in [(170 * 512, 512), (170 * 512, 16384), (0, 30 * 512), (0, 31 * 512),
                                 (256 * dump.MIB, 30 * 512), (256 * dump.MIB, 31 * 512)]:
                with self.subTest(profile=name, offset=offset, size=size), self.assertRaises(ValueError):
                    dump.check_read_request(offset, size, profile.regions)

    def test_neighbouring_requests_are_still_allowed(self):
        dump.check_read_request(169 * 512, 512, dump.DISK_RESPONSE_REGIONS)
        dump.check_read_request(171 * 512, 29 * 512, dump.DISK_RESPONSE_REGIONS)
        dump.check_read_request(171 * 512, 32 * 512, dump.DISK_RESPONSE_REGIONS)


class WriteLimitTests(unittest.TestCase):
    """d. Planned NAND writes may not cross 0x3EC0000."""

    def test_limit_value_and_boundary(self):
        self.assertEqual(vendor.WRITE_LIMIT, 0x3EC0000)
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "BOOT1.ROM"
            image.write_bytes(b"x" * 513)
            vendor.load_image(image, 0x3EC0000 - 1024)
            with self.assertRaisesRegex(ValueError, "boundary"):
                vendor.load_image(image, 0x3EC0000 - 512)


class LegacyProfileTests(unittest.TestCase):
    """e. `verify` accepts manifests written under the old profile name; new dumps use the new one."""

    def make_backup(self, root: Path) -> Path:
        region = dump.Region("recovery-disk.bin", 0, 1024)
        directory = root / "old-prepatch"
        data = bytes(range(256)) * 4
        dump.acquire(lambda offset, size: data[offset:offset + size], directory, {"kind": "synthetic"},
                     (region,), lambda message: None, profile=dump.DISK_RESPONSE_PROFILE)
        return directory

    def test_verify_accepts_legacy_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = self.make_backup(Path(temporary))
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["profile"] = dump.LEGACY_PREPATCH_PROFILE
            manifest_path.write_text(json.dumps(manifest))
            region = (dump.Region("recovery-disk.bin", 0, 1024),)
            self.assertEqual(dump.verify_backup(directory, region)["profile"], "hpprime-g1-recovery-prepatch")
            with patch.object(dump, "DISK_RESPONSE_REGIONS", region), \
                    patch.dict(dump.PROFILES, {dump.DISK_RESPONSE_PROFILE: dump.BackupProfile(1024, region, "t")}):
                code, out, _ = run(["verify", str(directory)])
            self.assertEqual(code, 0)
            self.assertIn("legacy profile name accepted", out)

    def test_unknown_profile_still_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = self.make_backup(Path(temporary))
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["profile"] = "hpprime-g1-something-else"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "unsupported profile"):
                dump.verify_backup(directory)

    def test_legacy_name_cannot_start_new_dump(self):
        self.assertEqual(dump.LEGACY_PREPATCH_PROFILE, "hpprime-g1-recovery-prepatch")
        self.assertEqual(dump.DISK_RESPONSE_PROFILE, "hpprime-g1-recovery-disk-response")
        with self.assertRaises(ValueError):
            dump.get_profile(dump.LEGACY_PREPATCH_PROFILE)
        self.assertIs(dump.get_profile(dump.LEGACY_PREPATCH_PROFILE, allow_legacy=True),
                      dump.PROFILES[dump.DISK_RESPONSE_PROFILE])

    def test_disk_response_scope_says_not_nand(self):
        scope = dump.PROFILES[dump.DISK_RESPONSE_PROFILE].scope
        self.assertIn("NAND 아님, 펌웨어 합성 응답", scope)
        self.assertNotIn(dump.LEGACY_PREPATCH_PROFILE, dump.PROFILES)


class SynthTests(unittest.TestCase):
    """synth-check: rebuilt sectors match a synthetic disk; extra data is reported."""

    def make(self, root: Path, extra: bool = False):
        fw = bytearray(0x40000)
        fw[0xbe84:0xbe8c] = b"MSWIN4.1"
        fw[0xbe90:0xbea8] = b"\x01\x02\x03\x04NO NAME    \x00FAT16   "
        fw[0x3FFF4:0x40000] = synth.BOOT0_TAG
        boot0 = root / "BXCBOOT0V16.bin"
        boot0.write_bytes(bytes(fw))
        disk = root / "recovery-disk.bin"
        with disk.open("wb") as stream:
            stream.truncate((synth.PART_LBA + 64) * 512)
            stream.write(synth.synth_mbr())
            stream.seek(synth.PART_LBA * 512)
            stream.write(synth.synth_boot(bytes(fw)))
            for lba in synth.FAT_HEAD_LBAS:
                stream.seek(lba * 512)
                stream.write(b"\xf8\xff\xff\xff")
            if extra:
                stream.seek(5 * 512)
                stream.write(b"DATA")
        return boot0, disk

    def test_only_synthesized_sectors(self):
        with tempfile.TemporaryDirectory() as temporary:
            boot0, disk = self.make(Path(temporary))
            code, out, _ = run(["synth-check", str(boot0), str(disk)])
        self.assertEqual(code, 0)
        self.assertIn("NONZERO_SECTORS=4 ONLY_SYNTHESIZED=True", out)

    def test_extra_sector_and_wrong_boot0(self):
        with tempfile.TemporaryDirectory() as temporary:
            boot0, disk = self.make(Path(temporary), extra=True)
            self.assertEqual(run(["synth-check", str(boot0), str(disk)])[0], 3)
            boot0.write_bytes(bytes(0x40000))
            self.assertEqual(run(["synth-check", str(boot0), str(disk)])[0], 2)

    def test_mbr_layout(self):
        mbr = synth.synth_mbr()
        self.assertEqual(struct.unpack_from("<II", mbr, 454), (0x20000, 0x4000))
        self.assertEqual(mbr[510:], b"\x55\xaa")


class CliTests(unittest.TestCase):
    def test_subcommands(self):
        parser = cli.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertEqual(set(choices), {"status", "check", "dump", "verify", "synth-check", "plan", "boot0"})

    def test_dump_disk_response_uses_new_profile_in_preflight(self):
        with patch.object(dump, "inspect_device", side_effect=ValueError("stop")) as inspect:
            code, _, err = run(["dump", "--source", "disk-response", "--device", "/dev/x",
                                "--confirm-g1", "--confirm-recovery-v16"])
        self.assertEqual(code, 1)
        inspect.assert_called_once_with(Path("/dev/x"), profile=dump.DISK_RESPONSE_PROFILE)

    def test_image_source_rejects_device_argument(self):
        code, _, err = run(["dump", "--source", "image", "--image", "/nonexistent", "--device", "/dev/x"])
        self.assertEqual(code, 1)
        self.assertIn("--image only", err)



class CheckProfileRequiredTests(unittest.TestCase):
    """Capacity must never select a profile automatically (orchestrator fix, 2026-11-17)."""

    def test_check_without_profile_is_rejected(self):
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
            cli.build_parser().parse_args(["check", "--device", "/nonexistent"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_check_accepts_legacy_and_new_profile_names(self):
        parser = cli.build_parser()
        for name in (dump.LEGACY_PREPATCH_PROFILE, *dump.PROFILES):
            args = parser.parse_args(["check", "--device", "/nonexistent", "--profile", name])
            self.assertEqual(args.profile, name)

class ScanCommandBlockedTests(unittest.TestCase):
    """0xBEC2 erases and rewrites Boot0 block 0 in reference V16 (0x7988); never build it."""

    def test_scan_packet_is_refused(self):
        for arg in (0, 1):
            with self.assertRaises(PermissionError) as ctx:
                vendor.vendor_packet(vendor.SCAN_ERASES_BOOT0, arg)
            self.assertIn("Boot0 block 0", str(ctx.exception))

    def test_scan_is_not_in_any_generated_sequence(self):
        image = vendor.load_image(vendor.REFERENCE / "BXCBOOT0V16.bin", boot0=True)
        commands = [struct.unpack_from("<I", p)[0] for p in vendor.boot0_packets(image, "v16", reboot=True)]
        self.assertNotIn(vendor.SCAN_ERASES_BOOT0, commands)


if __name__ == "__main__":
    unittest.main()
