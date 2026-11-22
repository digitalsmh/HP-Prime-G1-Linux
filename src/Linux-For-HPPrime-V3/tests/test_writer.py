#!/usr/bin/env python3
"""S5 writer safety tests (2026-11-17). Mock transport and temp files only; no device access."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import device_check, usbtool_v3, write_plan
import ctypes
import struct

from tools.usbtool_v3 import (
    ARG_HANDSHAKE, CMD_CHECK_BUF, CMD_HANDSHAKE, CMD_PROGRESS, CMD_REBOOT, CMD_SCAN_BADBLOCKS,
    CMD_SET_SIZE, CMD_VERIFY_CRC,
    SCSI_OP_BXC_RECV, SCSI_OP_BXC_SEND, SCSI_OP_READ_10, SCSI_OP_WRITE_10, SPECIAL_CONTROL_LBA,
    BxcBoot0Controller, CommandLog, CommandNotAllowed, DryRunTransport,
    build_bxc_control_packet, build_vendor_packet, parse_usbtool_ini, parse_vendor_packet,
)


def raw_packet(cmd, arg):
    """264-byte vendor packet built without the allowlist (to test the transport's own check)."""
    return struct.pack("<II", cmd & 0xFFFFFFFF, arg & 0xFFFFFFFF) + bytes(256)


def history_kinds(history):
    """Map DryRunTransport history to kinds: handshake/unlock/progress/lock/reboot/ack/data."""
    out = []
    for rec in history:
        if rec.opcode == SCSI_OP_BXC_RECV:
            out.append("ack")
        elif rec.opcode == SCSI_OP_WRITE_10:
            out.append("data")
        elif rec.opcode == SCSI_OP_BXC_SEND:
            cmd, arg = parse_vendor_packet(rec.packet)
            if cmd == CMD_HANDSHAKE:
                out.append("handshake")
            elif cmd == CMD_REBOOT:
                out.append("reboot")
            else:
                out.append("unlock" if arg == 0 else "lock" if arg == 100 else "progress")
        else:
            out.append(f"other-0x{rec.opcode:02X}")
    return out

MIB = 1024 * 1024
BOOT1_SIZE = 0x80000
LINUX_SIZE = 62 * MIB


def ff_file(path: Path, size: int) -> Path:
    with path.open("wb") as stream:
        block = b"\xFF" * MIB
        left = size
        while left > 0:
            stream.write(block[:min(left, MIB)])
            left -= MIB
    return path


class V3IniFixture(unittest.TestCase):
    """BOOT1.ROM (512 KiB) + LINUX.DAT (62 MiB) of 0xFF next to a copy of V3 usbtool.ini."""

    linux_size = LINUX_SIZE

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        ff_file(self.tmp / "BOOT1.ROM", BOOT1_SIZE)
        ff_file(self.tmp / "LINUX.DAT", self.linux_size)
        self.ini = self.tmp / "usbtool.ini"
        self.ini.write_bytes((PROJECT_ROOT / "usbtool.ini").read_bytes())
        self.cfg = parse_usbtool_ini(self.ini)
        self.images = usbtool_v3.load_config_images(self.cfg)

    def tearDown(self):
        self._tmp.cleanup()

    def sha_args(self):
        return [arg for image in self.images for arg in ("--expected-sha256", image.sha256)]


class TestPlan(V3IniFixture):
    def test_v3_ini_plan_passes(self):
        plans = write_plan.plan_writes(self.images)
        self.assertEqual([(p["lba_first"], p["lba_last"]) for p in plans], [(0, 1023), (1536, 128511)])
        self.assertEqual(plans[0]["physical_blocks"], [2, 5])
        self.assertEqual(plans[1]["physical_blocks"], [8, 503])
        self.assertEqual((plans[1]["lba_last"] + 1) * 512, write_plan.WRITE_LIMIT)

    def test_63_mib_refused(self):
        ff_file(self.tmp / "LINUX.DAT", 63 * MIB)
        images = usbtool_v3.load_config_images(parse_usbtool_ini(self.ini))
        with self.assertRaisesRegex(ValueError, r"refused \(b\).*WRITE_LIMIT"):
            write_plan.plan_writes(images)

    def test_unaligned_offset_refused(self):
        for offset in (0xC0200, 0x1000, 0xC0000 + 0x800):
            image = write_plan.load_plan_image(self.tmp / "LINUX.DAT", offset)
            with self.assertRaisesRegex(ValueError, r"refused \(c\)"):
                write_plan.plan_writes([image])

    def test_boot0_image_refused(self):
        boot0 = ff_file(self.tmp / "BXCBOOT0.BIN", 0x40000)
        image = write_plan.load_plan_image(boot0, 0)
        self.assertEqual(image.kind, "boot0")
        with self.assertRaisesRegex(ValueError, r"refused \(e\)"):
            write_plan.plan_writes([image])
        transport = DryRunTransport()
        with self.assertRaises(ValueError):
            BxcBoot0Controller(transport).flash_file_at_usb_offset(boot0, 0)
        self.assertEqual(transport.history, [])

    def test_overlap_and_boot0_blocks_and_capacity_refused(self):
        a = write_plan.PlanImage(Path("A"), b"\xFF" * 0x40000, 0, "nand")
        b = write_plan.PlanImage(Path("B"), b"\xFF" * 0x20000, 0x20000, "nand")
        with self.assertRaisesRegex(ValueError, r"refused \(d\)"):
            write_plan.plan_writes([a, b])
        # With physical blocks 2.. all bad the table entry for logical 0 stays 0 (Boot0).
        with self.assertRaisesRegex(ValueError, r"refused \(a\)"):
            write_plan.plan_writes([a], bad_blocks=range(2, 2048))
        far = write_plan.PlanImage(Path("C"), b"\xFF" * 0x800, 0x7FE00 * 512, "nand")
        with self.assertRaisesRegex(ValueError, r"refused \(b\)"):
            write_plan.plan_writes([far])


class TestChunks(V3IniFixture):
    linux_size = 3 * write_plan.BLOCK_SIZE + 1000      # small: chunk rules do not depend on size

    def test_chunks_stay_in_block_and_avoid_control_shapes(self):
        sizes = list(range(4, 257, 4)) + [1, 2, 3, 30, 31, 32, 33]
        for image in self.images:
            for max_sectors in sizes:
                if max_sectors < 4:
                    with self.assertRaises(ValueError):
                        write_plan.transfer_chunks(image, max_sectors)
                    continue
                chunks = write_plan.transfer_chunks(image, max_sectors)
                seen_blocks = set()
                expected = image.usb_offset // 512
                for lba, count in chunks:
                    self.assertEqual(lba, expected)
                    expected += count
                    block = lba // 256
                    self.assertEqual(block, (lba + count - 1) // 256, (lba, count))
                    if block not in seen_blocks:
                        self.assertEqual(lba % 256, 0, "first chunk of a block must start at page 0")
                        seen_blocks.add(block)
                    self.assertEqual(lba % 4, 0)
                    self.assertEqual(count % 4, 0)
                    self.assertLessEqual(count, 256)
                    self.assertNotEqual(lba, SPECIAL_CONTROL_LBA)
                    self.assertNotIn(count, (30, 31))
                self.assertEqual(expected - image.usb_offset // 512, write_plan.padded_sectors(len(image.data)))

    def test_default_is_four_chunks_per_block_and_over_256_refused(self):
        # 2026-11-17 real device: host cap max_hw_sectors_kb=120 -> 256-sector chunks get EIO.
        self.assertEqual(usbtool_v3.DEFAULT_CHUNK_SECTORS, 64)
        self.assertLessEqual(usbtool_v3.DEFAULT_CHUNK_SECTORS * 512 // 1024, write_plan.HOST_MAX_SECTORS_KB)
        self.assertEqual(write_plan.BLOCK_SECTORS % usbtool_v3.DEFAULT_CHUNK_SECTORS, 0)
        self.assertEqual(len(write_plan.transfer_chunks(self.images[0])), 16)
        with self.assertRaises(ValueError):
            write_plan.transfer_chunks(self.images[0], 512)

    def test_tail_is_ff_filled(self):
        image = write_plan.PlanImage(Path("T"), b"\x00" * 1000, 0x20000, "nand")
        (lba, count), = write_plan.transfer_chunks(image)
        self.assertEqual(count, 4)
        payload = write_plan.chunk_payload(image, lba, count)
        self.assertEqual(payload, b"\x00" * 1000 + b"\xFF" * (2048 - 1000))

    def test_transport_refuses_bad_chunk_shapes(self):
        transport = DryRunTransport()
        BxcBoot0Controller(transport).unlock_writes()
        for lba, sectors in ((SPECIAL_CONTROL_LBA, 4), (SPECIAL_CONTROL_LBA, 1), (256, 30), (256, 31),
                             (2, 4), (252, 8), (0, 512)):
            with self.assertRaises(CommandNotAllowed, msg=(lba, sectors)):
                transport.write_sectors(lba, b"\xFF" * 512 * sectors)
        self.assertEqual(history_kinds(transport.history), ["unlock", "ack"])


class TestAllowlist(unittest.TestCase):
    def test_forbidden_commands_refused_before_packet(self):
        transport = DryRunTransport()
        ctrl = BxcBoot0Controller(transport, allow_reboot=True)
        ctrl.unlock_writes()
        with mock.patch.object(usbtool_v3, "build_vendor_packet", wraps=build_vendor_packet) as builder:
            for cmd in (CMD_SCAN_BADBLOCKS, CMD_SET_SIZE, CMD_VERIFY_CRC, CMD_CHECK_BUF, 0x1234):
                for arg in (0, 1):
                    with self.assertRaises(CommandNotAllowed, msg=hex(cmd)):
                        ctrl.vendor_command(cmd, arg)
            for cmd, arg in ((CMD_HANDSHAKE, 0), (CMD_PROGRESS, 101), (CMD_REBOOT, 1)):
                with self.assertRaises(CommandNotAllowed):
                    ctrl.vendor_command(cmd, arg)
            for arg in (101, -1):
                with self.assertRaises(CommandNotAllowed):
                    ctrl.set_progress(arg)
            builder.assert_not_called()
        self.assertEqual(history_kinds(transport.history), ["unlock", "ack"])

    def test_transport_refuses_forbidden_packets_reads_and_lba170(self):
        transport = DryRunTransport()
        for cmd in (CMD_SCAN_BADBLOCKS, CMD_SET_SIZE, CMD_VERIFY_CRC, CMD_CHECK_BUF):
            with self.assertRaises(CommandNotAllowed):
                transport.vendor_send(raw_packet(cmd, 0))
        with self.assertRaises(CommandNotAllowed):
            transport.vendor_send(raw_packet(CMD_PROGRESS, 0)[:263])          # wrong length
        with self.assertRaises(CommandNotAllowed):
            transport.vendor_send(raw_packet(CMD_PROGRESS, 0)[:-1] + b"\x01")  # payload not allowed
        for lba, sectors in ((SPECIAL_CONTROL_LBA, 30), (SPECIAL_CONTROL_LBA, 31),
                             (SPECIAL_CONTROL_LBA, 1), (0, 4)):
            with self.assertRaises(CommandNotAllowed):
                transport.read_sectors(lba, sectors)
            with self.assertRaises(CommandNotAllowed):
                usbtool_v3.check_outgoing(SCSI_OP_READ_10, lba, sectors)
        for cmd, arg in ((CMD_HANDSHAKE, ARG_HANDSHAKE), (CMD_PROGRESS, 0), (CMD_PROGRESS, 100)):
            with self.assertRaises(CommandNotAllowed):          # old LBA 170 BXCD packet path
                transport.write_sectors(SPECIAL_CONTROL_LBA, build_bxc_control_packet(cmd, arg))
        self.assertEqual(transport.history, [])

    def test_linux_transport_refuses_before_ioctl(self):
        with mock.patch.object(usbtool_v3.os, "open", return_value=99), \
                mock.patch.object(usbtool_v3.os, "close"), \
                mock.patch.object(usbtool_v3.fcntl, "ioctl") as ioctl:
            transport = usbtool_v3.LinuxSgIoTransport("/nonexistent/x")
            with self.assertRaises(CommandNotAllowed):
                transport.read_sectors(SPECIAL_CONTROL_LBA, 30)
            with self.assertRaises(CommandNotAllowed):
                transport.write_sectors(SPECIAL_CONTROL_LBA, build_bxc_control_packet(CMD_PROGRESS, 0))
            with self.assertRaises(CommandNotAllowed):
                transport.vendor_send(raw_packet(CMD_SCAN_BADBLOCKS, 0))
            transport.close()
        ioctl.assert_not_called()

    def test_linux_transport_vendor_framing(self):
        seen = []

        def fake_ioctl(fd, request, hdr):
            cdb = ctypes.string_at(hdr.cmdp, hdr.cmd_len)
            seen.append((cdb, hdr.dxfer_direction, hdr.dxfer_len, ctypes.string_at(hdr.dxferp, hdr.dxfer_len)))
            if hdr.dxfer_direction == usbtool_v3.SG_DXFER_FROM_DEV:
                ack = struct.pack("<II", 0x8000BECD, 0x8000BEAD)
                ctypes.memmove(hdr.dxferp, ack, len(ack))
            return 0

        with mock.patch.object(usbtool_v3.os, "open", return_value=99), \
                mock.patch.object(usbtool_v3.os, "close"), \
                mock.patch.object(usbtool_v3.fcntl, "ioctl", side_effect=fake_ioctl):
            transport = usbtool_v3.LinuxSgIoTransport("/nonexistent/x")
            ack = BxcBoot0Controller(transport).handshake()
            transport.close()
        self.assertEqual(ack, (0x8000BECD, 0x8000BEAD))
        (cdb1, dir1, len1, data1), (cdb2, dir2, len2, _) = seen
        self.assertEqual(cdb1, bytes.fromhex("88 00 00 00 00 00 00 00 00 00"))
        self.assertEqual((dir1, len1), (usbtool_v3.SG_DXFER_TO_DEV, 264))
        self.assertEqual(data1, struct.pack("<II", 0xBECD, 0xBEAD) + bytes(256))
        self.assertEqual(cdb2, bytes.fromhex("89 00 00 00 00 00 00 00 00 00"))
        self.assertEqual((dir2, len2), (usbtool_v3.SG_DXFER_FROM_DEV, 264))

    def test_reboot_needs_explicit_option(self):
        transport = DryRunTransport()
        ctrl = BxcBoot0Controller(transport)
        with self.assertRaises(CommandNotAllowed):
            ctrl.reboot()
        with self.assertRaises(CommandNotAllowed):
            ctrl.flash_images([], reboot=True)
        self.assertEqual(transport.history, [])

    def test_scan_bad_blocks_still_blocked(self):
        with self.assertRaises(PermissionError):
            BxcBoot0Controller(DryRunTransport()).scan_bad_blocks()


class TestDryRunOrder(V3IniFixture):
    def run_flash(self, reboot=False, transport=None):
        self.transport = transport or DryRunTransport(mirror_file=self.tmp / "mirror.bin")
        log = CommandLog(self.tmp / "log.jsonl")
        try:
            ctrl = BxcBoot0Controller(self.transport, log=log, allow_reboot=reboot)
            results = ctrl.flash_from_ini(self.cfg, reboot=reboot)
        finally:
            self.transport.close()
            log.close()
        return results

    def packets(self):
        return [parse_vendor_packet(rec.packet) for rec in self.transport.history
                if rec.opcode == SCSI_OP_BXC_SEND]

    def test_full_order_handshake_unlock_progress_chunks_lock(self):
        results = self.run_flash()
        kinds = history_kinds(self.transport.history)
        self.assertEqual(kinds[:5], ["handshake", "ack", "unlock", "ack", "progress"])
        self.assertEqual(kinds[-2:], ["lock", "ack"])
        for name in ("handshake", "unlock", "lock"):
            self.assertEqual(kinds.count(name), 1, name)
        # 64-sector default (host cap 120 KiB): 4 chunks per 128 KiB block -> BOOT1 16, LINUX.DAT 1984.
        self.assertEqual(kinds.count("data"), (4 + 496) * 4)
        self.assertNotIn("reboot", kinds)
        # Every 0x88 is followed by exactly one 0x89.
        sends = [i for i, k in enumerate(kinds) if k not in ("ack", "data")]
        self.assertTrue(all(kinds[i + 1] == "ack" for i in sends))
        self.assertEqual(kinds.count("ack"), len(sends))
        body = [k for k in kinds[4:-2] if k != "ack"]
        self.assertEqual(set(body), {"progress", "data"})
        self.assertEqual(body[-1], "data")
        progress = [arg for cmd, arg in self.packets() if cmd == CMD_PROGRESS][1:-1]
        self.assertEqual(progress, sorted(set(progress)))
        self.assertTrue(all(1 <= p <= 99 for p in progress))
        # No READ_10, no LBA 170 WRITE_10.
        self.assertFalse(any(rec.opcode == SCSI_OP_READ_10 for rec in self.transport.history))
        self.assertFalse(any(rec.opcode == SCSI_OP_WRITE_10 and rec.lba == SPECIAL_CONTROL_LBA
                             for rec in self.transport.history))
        self.assertTrue(all(rec.status == "ok" for rec in self.transport.history))
        self.assertEqual([written for _, written in results], [BOOT1_SIZE, LINUX_SIZE])
        self.assertFalse(self.transport.unlocked)
        lines = [json.loads(line) for line in (self.tmp / "log.jsonl").read_text().splitlines()]
        self.assertEqual(len(lines), len(self.transport.history))
        self.assertEqual([entry["kind"] for entry in lines], kinds)
        self.assertEqual(lines[0]["cdb"], "88 00 00 00 00 00 00 00 00 00")
        self.assertEqual(lines[1]["response"], ["0x8000BECD", "0x8000BEAD"])
        self.__class__.observed_total = len(kinds)
        self.__class__.observed_progress = kinds.count("progress")

    def test_boot1_only_command_count(self):
        self.cfg.items = self.cfg.items[:1]
        self.run_flash()
        kinds = history_kinds(self.transport.history)
        self.assertEqual(kinds.count("data"), 4 * 4)  # 4 blocks x 4 chunks of 64 sectors
        self.assertNotIn("other-0x28", kinds)
        self.__class__.boot1_kinds = kinds

    def test_reboot_option_appends_only_reboot(self):
        self.run_flash(reboot=True)
        kinds = history_kinds(self.transport.history)
        self.assertEqual(kinds[-3:], ["lock", "ack", "reboot"])
        self.assertEqual(kinds.count("unlock"), 1)
        self.assertTrue(self.transport.rebooted)

    def test_failure_mid_write_relocks(self):
        transport = DryRunTransport()
        calls = {"n": 0}
        original = transport.write_sectors

        def flaky(lba, data, description=""):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("simulated USB error")
            return original(lba, data, description)

        transport.write_sectors = flaky
        with self.assertRaises(OSError):
            BxcBoot0Controller(transport).flash_from_ini(self.cfg)
        self.assertEqual(transport.last_progress, 100)
        self.assertFalse(transport.unlocked)
        self.assertEqual(history_kinds(transport.history)[-2:], ["lock", "ack"])

    def ack_mismatch(self, bad_cmd, bad_arg):
        transport = DryRunTransport()
        original = transport.vendor_receive
        state = {"cmd": None}
        original_send = transport.vendor_send

        def send(packet, description=""):
            state["cmd"] = parse_vendor_packet(packet)
            return original_send(packet, description)

        def receive(description=""):
            response = original(description)
            if state["cmd"] == (bad_cmd, bad_arg):
                return struct.pack("<II", 0x8000BECD, 0x12345678) + bytes(256)
            return response

        transport.vendor_send, transport.vendor_receive = send, receive
        with self.assertRaisesRegex(OSError, "Unexpected acknowledgement"):
            BxcBoot0Controller(transport).flash_from_ini(self.cfg)
        return transport, history_kinds(transport.history)

    def test_ack_mismatch_stops_and_relocks(self):
        for bad in ((CMD_HANDSHAKE, ARG_HANDSHAKE), (CMD_PROGRESS, 0), (CMD_PROGRESS, 1)):
            transport, kinds = self.ack_mismatch(*bad)
            self.assertEqual(kinds[-2:], ["lock", "ack"], bad)
            self.assertFalse(transport.unlocked, bad)
            sent = [parse_vendor_packet(r.packet) for r in transport.history if r.opcode == SCSI_OP_BXC_SEND]
            self.assertEqual(sent.count(bad), 1, "no retry")
            if bad[1] in (ARG_HANDSHAKE, 0):
                self.assertNotIn("data", kinds, bad)

    def test_short_response_stops(self):
        transport = DryRunTransport()
        transport.vendor_receive = lambda description="": b"\x00" * 8
        with self.assertRaisesRegex(OSError, "Short vendor response"):
            BxcBoot0Controller(transport).handshake()

    def test_locked_data_write_is_write_protected(self):
        transport = DryRunTransport(mirror_file=self.tmp / "m.bin")
        transport.write_sectors(256, b"\x00" * 2048)
        ctrl = BxcBoot0Controller(transport)
        ctrl.handshake()                         # BECD does not unlock
        transport.write_sectors(512, b"\x00" * 2048)
        ctrl.unlock_writes()
        transport.write_sectors(768, b"\x11" * 2048)
        ctrl.lock_writes()
        transport.write_sectors(1024, b"\x22" * 2048)
        transport.close()
        statuses = [r.status for r in transport.history if r.opcode == SCSI_OP_WRITE_10]
        self.assertEqual(statuses, ["write-protected", "write-protected", "ok", "write-protected"])
        mirror = (self.tmp / "m.bin").read_bytes()
        self.assertEqual(mirror[768 * 512:768 * 512 + 2048], b"\x11" * 2048)
        self.assertEqual(len(mirror), 768 * 512 + 2048)

    def test_cli_dry_run_prints_plan_and_writes_log(self):
        log = self.tmp / "cli.jsonl"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = usbtool_v3.main(["flash", "--ini", str(self.ini), "--log-jsonl", str(log)])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("PLAN UpdateItem01 LINUX.DAT: LBA 1536..128511, physical blocks 8..503", text)
        self.assertIn("[DRY-RUN COMPLETE]", text)
        self.assertIn("88 00 00 00 00 00 00 00 00 00", text)
        self.assertGreater(len(log.read_text().splitlines()), 500)


class TestExecuteGuards(V3IniFixture):
    linux_size = 4096

    FLAGS = ["--execute", "--device", "--confirm-g1", "--confirm-recovery-v16",
             "--acknowledge-nand-write", "--expected-sha256"]

    def argv(self, skip=None):
        argv = ["flash", "--ini", str(self.ini), "--log-jsonl", str(self.tmp / "x.jsonl")]
        for flag in self.FLAGS:
            if flag == skip:
                continue
            if flag == "--device":
                argv += ["--device", "/nonexistent/hpprime-test-device"]
            elif flag == "--expected-sha256":
                argv += self.sha_args()
            else:
                argv.append(flag)
        return argv

    def run_main(self, argv):
        with mock.patch.object(usbtool_v3.os, "open") as fake_open, \
                mock.patch.object(device_check, "inspect_device") as fake_inspect, \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                rc = usbtool_v3.main(argv)
            except SystemExit as stop:
                rc = stop
        return rc, fake_open.call_count, fake_inspect.call_count

    def test_missing_any_flag_never_opens_device(self):
        for flag in self.FLAGS:
            rc, opens, inspects = self.run_main(self.argv(skip=flag))
            self.assertEqual(opens, 0, flag)
            self.assertEqual(inspects, 0, flag)
            if flag != "--execute":
                self.assertIsInstance(rc, SystemExit, flag)
                self.assertIn(flag, str(rc.code))

    def test_wrong_or_partial_sha256_never_opens_device(self):
        argv = self.argv(skip="--expected-sha256") + ["--expected-sha256", self.images[0].sha256]
        rc, opens, _ = self.run_main(argv)
        self.assertIsInstance(rc, SystemExit)
        self.assertEqual(opens, 0)
        argv = self.argv(skip="--expected-sha256") + ["--expected-sha256", "0" * 64] * 2
        rc, opens, _ = self.run_main(argv)
        self.assertEqual(opens, 0)

    def test_plan_refusal_happens_before_flags_or_device(self):
        ff_file(self.tmp / "LINUX.DAT", 63 * MIB)
        with mock.patch.object(usbtool_v3.os, "open") as fake_open, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                usbtool_v3.main(self.argv())
        self.assertEqual(fake_open.call_count, 0)

    def test_preflight_failure_never_opens(self):
        def refuse(path):
            raise ValueError("Device USB identity 03f0:1541 is not the HP Prime recovery ID")
        with mock.patch.object(usbtool_v3.os, "open") as fake_open:
            with self.assertRaises(SystemExit):
                usbtool_v3.open_checked_device("/nonexistent/x", inspect=refuse)
        self.assertEqual(fake_open.call_count, 0)

    def test_open_uses_rdwr_excl_after_preflight(self):
        order = []
        with mock.patch.object(usbtool_v3.os, "open", side_effect=lambda *a: order.append(("open", a)) or 99) \
                as fake_open, mock.patch.object(usbtool_v3.os, "close"):
            transport, meta = usbtool_v3.open_checked_device(
                "/nonexistent/x", inspect=lambda p: order.append(("inspect", str(p))) or {"ok": 1})
            transport.close()
        self.assertEqual([step for step, _ in order], ["inspect", "open"])
        fake_open.assert_called_once_with("/nonexistent/x", os.O_RDWR | os.O_EXCL)
        self.assertEqual(meta, {"ok": 1})

    def test_reboot_subcommand_needs_flags(self):
        with mock.patch.object(usbtool_v3.os, "open") as fake_open, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                usbtool_v3.main(["reboot", "--execute", "--device", "/nonexistent/x"])
        self.assertEqual(fake_open.call_count, 0)


class TestProbe(unittest.TestCase):
    PROBE_FLAGS = ["--execute", "--device", "--confirm-g1", "--confirm-recovery-v16"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def argv(self, skip=None):
        argv = ["probe", "--log-jsonl", str(self.tmp / "probe.jsonl")]
        for flag in self.PROBE_FLAGS:
            if flag != skip:
                argv += ["--device", "/nonexistent/hpprime-test-device"] if flag == "--device" else [flag]
        return argv

    def test_probe_missing_flag_never_opens(self):
        for flag in self.PROBE_FLAGS[1:]:
            with mock.patch.object(usbtool_v3.os, "open") as fake_open, \
                    mock.patch.object(device_check, "inspect_device") as fake_inspect, \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as stop:
                    usbtool_v3.main(self.argv(skip=flag))
            self.assertIn(flag, str(stop.exception.code))
            self.assertEqual(fake_open.call_count, 0, flag)
            self.assertEqual(fake_inspect.call_count, 0, flag)

    def test_probe_does_not_need_acknowledge_nand_write(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            usbtool_v3.main(self.argv() + ["--acknowledge-nand-write"])     # unknown option for probe

    def run_execute(self, transport):
        out = io.StringIO()
        with mock.patch.object(usbtool_v3, "open_checked_device",
                               return_value=(transport, {"usb_id": "03f0:0441"})) as opener, \
                contextlib.redirect_stdout(out):
            rc = usbtool_v3.main(self.argv())
        opener.assert_called_once_with("/nonexistent/hpprime-test-device")
        return rc, out.getvalue()

    def test_probe_execute_sends_one_0x88_and_one_0x89(self):
        transport = DryRunTransport()
        rc, text = self.run_execute(transport)
        self.assertEqual(rc, 0)
        self.assertIn("PROBE_ACK: 0x8000BECD 0x8000BEAD", text)
        self.assertEqual([r.opcode for r in transport.history], [SCSI_OP_BXC_SEND, SCSI_OP_BXC_RECV])
        self.assertEqual(parse_vendor_packet(transport.history[0].packet), (CMD_HANDSHAKE, ARG_HANDSHAKE))
        self.assertFalse(any(r.opcode in (SCSI_OP_WRITE_10, SCSI_OP_READ_10) for r in transport.history))
        self.assertFalse(transport.unlocked)          # 0xBECD does not change the lock flag
        lines = [json.loads(line) for line in (self.tmp / "probe.jsonl").read_text().splitlines()]
        self.assertEqual([e["kind"] for e in lines], ["preflight", "handshake", "ack"])
        self.assertEqual(lines[2]["response"], ["0x8000BECD", "0x8000BEAD"])

    def test_probe_execute_bad_ack_reports_failure(self):
        transport = DryRunTransport()
        original = transport.vendor_receive
        transport.vendor_receive = lambda description="": original(description)[:4] + b"\0" * 260
        rc, text = self.run_execute(transport)
        self.assertEqual(rc, 2)
        self.assertIn("PROBE_FAILED", text)
        self.assertEqual(len(transport.history), 2)
        lines = [json.loads(line) for line in (self.tmp / "probe.jsonl").read_text().splitlines()]
        self.assertEqual(lines[-1]["result"], "error")

    def test_probe_dry_run(self):
        out = io.StringIO()
        with mock.patch.object(usbtool_v3.os, "open") as fake_open, contextlib.redirect_stdout(out):
            rc = usbtool_v3.main(["probe"])
        self.assertEqual(rc, 0)
        self.assertEqual(fake_open.call_count, 0)
        self.assertIn("[DRY-RUN] PROBE_ACK: 0x8000BECD 0x8000BEAD", out.getvalue())


class TestDeviceCheck(unittest.TestCase):
    """Fake sysfs tree; block_number is mocked so no device node is touched."""

    def build(self, root: Path, pid="0441", size=268173312 // 512, lbs=512, partition=False):
        usb = root / "devices/usb1/1-1"
        iface = usb / "1-1:1.0"
        disk = iface / "host6/target6:0:0/6:0:0:0/block/sdz"
        (disk / "queue").mkdir(parents=True)
        (disk / "holders").mkdir()
        (usb / "idVendor").write_text("03f0\n")
        (usb / "idProduct").write_text(pid + "\n")
        (iface / "bInterfaceClass").write_text("08\n")
        (disk / "size").write_text(f"{size}\n")
        (disk / "queue/logical_block_size").write_text(f"{lbs}\n")
        (disk / "dev").write_text("8:240\n")
        if partition:
            (disk / "partition").write_text("1\n")
        (root / "dev/block").mkdir(parents=True)
        (root / "dev/block/8:240").symlink_to(disk)
        (root / "mountinfo").write_text("1 0 0:1 / / rw - ext4 /dev/root rw\n")
        (root / "swaps").write_text("Filename Type Size Used Priority\n")
        return root

    def check(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build(Path(tmp), **{k: v for k, v in kwargs.items() if k != "mounted"})
            if kwargs.get("mounted"):
                (root / "mountinfo").write_text("1 0 8:240 / /mnt rw - vfat /dev/sdz rw\n")
            with mock.patch.object(device_check, "block_number", return_value=os.makedev(8, 240)):
                return device_check.inspect_device(Path("/nonexistent/sdz"), root / "dev/block",
                                                   root / "mountinfo", root / "swaps")

    def test_recovery_device_passes(self):
        meta = self.check()
        self.assertEqual(meta["usb_id"], "03f0:0441")
        self.assertEqual(meta["capacity"], 268173312)

    def test_refusals(self):
        for kwargs, pattern in (({"pid": "1541"}, "recovery ID"), ({"size": 1000}, "capacity"),
                                ({"lbs": 4096}, "capacity"), ({"partition": True}, "partition"),
                                ({"mounted": True}, "mounted")):
            with self.assertRaisesRegex(ValueError, pattern, msg=kwargs):
                self.check(**kwargs)

    def test_regular_file_refused(self):
        with tempfile.NamedTemporaryFile() as handle:
            with self.assertRaisesRegex(ValueError, "block device"):
                device_check.inspect_device(Path(handle.name))


if __name__ == "__main__":
    unittest.main()
