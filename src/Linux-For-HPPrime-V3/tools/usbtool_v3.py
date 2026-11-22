#!/usr/bin/env python3
"""
HP Prime G1 Recovery Mode Flasher & Protocol Tool (usbtool_v3.py).

Linux-native replacement for Windows `usbtool.exe`, reverse-engineered from
`usbtool.exe` (0x40ac90..0x40db00) and `BXCBOOT0V16.bin` (0x0c178..0x0df50).

Key Features:
  - Parses `usbtool.ini` (`[Update Infomation]`, `UpdateItem00..09`) and maps
    USB Mass Storage offsets (`0x00000000`, `0x000C0000`) to 512-byte LBAs,
    Physical NAND offsets (+0x00040000 shift), and Linux MTD partitions.
  - Control path (S6, 2026-11-17, 30_TEST/40_Build/writer/S6_vendor-path.md): BXCBOOT0
    vendor SCSI 0x88 (send 264-byte packet `<II cmd,arg>` + 256 B) / 0x89 (receive the
    264-byte acknowledgement `cmd|0x80000000, result`). Reference V16: 0x88 -> 0xde70 ->
    0xedd4 -> callback 0x30c087dc = file 0xc7dc packet parser, no lock check.
          - 0xBECD (arg 0xBEAD): Handshake, ack (0x8000BECD, 0x8000BEAD); lock unchanged
          - 0xBEDC (arg 0..100): Progress; lock flag [0x30d6f412] = 0 if arg < 100 else 1
                                 (0xc8c4..0xc8ec), ack (0x8000BEDC, 0)
          - 0xBEC1 (arg 0)     : Reboot, no ack (only with --reboot / reboot subcommand)
          - 0xBEC2 (arg 0/1)   : "Scan NAND bad blocks" -- BLOCKED: erases and rewrites
                                 Boot0 block 0 (reference V16 0x7988). scan_bad_blocks() raises.
  - READ_10 is never sent. The LBA 170 / 30-sector unlock READ always ends with CSW
    status 7 (lock flag changes during the read, 0xdb50..0xdc28); on Linux usb-storage
    reset the device (30_TEST/40_Build/writer/exec-boot1.jsonl, 2026-11-17 21:55:20).
    WRITE_10 to LBA 170 (512-byte BXCD packets) is no longer sent either.
  - Safe by default: runs in `--dry-run` simulation mode unless `--execute` is
    explicitly passed.

Writer safety (S5, 2026-11-17, 30_TEST/40_Build/writer/S5_writer.md):
  - Every image is judged by tools/write_plan.py (copy of 20_HPPrime-Tool
    hpprime/layout.py) before any command: no Boot0 blocks 0-1, LBA < 0x7FE00,
    end <= 0x3EC0000, 128 KiB aligned start, no overlap, no Boot0 image.
  - WRITE_10 is sent only as write_plan.transfer_chunks pieces (<= 256 sectors,
    inside one block, page 0 first, 4-sector aligned, 0xFF tail, never LBA 170
    or 30/31 sectors).
  - Command allowlist (WRITER_ALLOWED_COMMANDS); anything else is refused before
    its packet/CDB is built. 0xBEC2, 0xBEC5, 0xBEC6, 0xBEC7, every READ_10 and every
    LBA 170 WRITE_10 are never sent.
  - Real writes need all of --execute --device --confirm-g1 --confirm-recovery-v16
    --acknowledge-nand-write --expected-sha256 (one per image) and a passing
    tools/device_check.py preflight; the device is opened O_RDWR|O_EXCL.
  - No read-back: recovery-mode READ_10 is a synthetic response (W1).
"""

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import device_check, write_plan
from tools.nand_layout import (
    NAND_BLOCK_SIZE,
    USB_SECTOR_SIZE,
    find_partitions_for_phys_range,
    lba_to_phys_offset,
    phys_offset_to_block,
    usb_offset_to_lba,
    usb_offset_to_phys_offset,
)

# Linux SG_IO constants
SG_IO = 0x2285
SG_DXFER_NONE = -1
SG_DXFER_TO_DEV = -2
SG_DXFER_FROM_DEV = -3

# Standard SCSI opcodes & BXCBOOT0 vendor opcodes
SCSI_OP_READ_10 = 0x28
SCSI_OP_WRITE_10 = 0x2A
SCSI_OP_BXC_SEND = 0x88
SCSI_OP_BXC_RECV = 0x89

# BXCBOOT0 special LBA & magic constants (verified in BXCBOOT0V16.bin @ 0x0c4c4 & 0x0c7dc)
SPECIAL_CONTROL_LBA = 0x000000AA        # Sector 170 (byte offset 0x15400)
UNLOCK_SECTOR_COUNT = 30                # 0x1E sectors -> *(uint16_t*)0x30d6f412 = 0 (Unlock)
LOCK_SECTOR_COUNT = 31                  # 0x1F sectors -> *(uint16_t*)0x30d6f412 = 1 (Lock)

BXC_MAGIC_1 = 0xAA5555AA
BXC_MAGIC_2 = 0x44435842                # Little-endian ASCII "BXCD"
BXC_PACKET_SIZE = 512                   # legacy LBA 170 packet (parsed only, never sent)

# Vendor packet path (same framing as 20_HPPrime-Tool hpprime/vendor.py)
VENDOR_SEND_CDB = bytes([SCSI_OP_BXC_SEND]) + bytes(9)
VENDOR_RECEIVE_CDB = bytes([SCSI_OP_BXC_RECV]) + bytes(9)
VENDOR_PACKET_SIZE = 264
VENDOR_ACK_FLAG = 0x80000000
HANDSHAKE_RESULT = 0x8000BEAD

# BXCBOOT0 command IDs (verified in BXCBOOT0V16.bin @ 0x0c810..0x0c960)
CMD_REBOOT = 0xBEC1
CMD_SCAN_BADBLOCKS = 0xBEC2
CMD_SET_SIZE = 0xBEC5
CMD_VERIFY_CRC = 0xBEC6
CMD_CHECK_BUF = 0xBEC7
CMD_HANDSHAKE = 0xBECD
ARG_HANDSHAKE = 0xBEAD
CMD_PROGRESS = 0xBEDC

# Default chunk size for flashing: one WRITE_10 per 128 KiB block (write_plan.transfer_chunks).
# The old 32-sector default is still accepted (any multiple of 4 up to 256).
DEFAULT_CHUNK_SECTORS = write_plan.DEFAULT_CHUNK_SECTORS

# HP Prime USB IDs
HP_PRIME_VID = "03f0"
HP_PRIME_PIDS = ("0441", "1541")

# Commands the writer may send. Everything else is refused before a packet is built.
WRITER_ALLOWED_COMMANDS = (
    "vendor 0x88 packet 0xBECD arg 0xBEAD (handshake / probe)",
    "vendor 0x88 packet 0xBEDC arg 0 (unlock)",
    "vendor 0x88 packet 0xBEDC arg 1..99 (progress)",
    "vendor 0x88 packet 0xBEDC arg 100 (lock)",
    "vendor 0x88 packet 0xBEC1 arg 0 (reboot, only with --reboot / reboot subcommand)",
    "vendor 0x89 264-byte acknowledgement",
    "WRITE_10 data chunk from write_plan.transfer_chunks",
)
FORBIDDEN_COMMANDS = {
    CMD_SCAN_BADBLOCKS: "0xBEC2 erases and rewrites Boot0 block 0 (reference V16 0x7988)",
    CMD_SET_SIZE: "0xBEC5 selects the Boot0 RAM upload path, not part of the NAND writer",
    CMD_VERIFY_CRC: "0xBEC6 RAM upload is not part of the NAND writer",
    CMD_CHECK_BUF: "0xBEC7 commits the RAM buffer to Boot0",
}
READ10_BLOCKED = ("blocked: READ_10 is never sent (the LBA 170 / 30-sector unlock READ ends with CSW 7 "
                  "and a usb-storage reset, 2026-11-17 21:55:20; other recovery READ_10 is synthetic)")
NO_READBACK_NOTE = ("검증 불가: 복구 모드 READ_10은 합성 응답이라 다시 읽어 비교할 수 없다(W1). "
                    "5단계 Linux nanddump로 확인하라.")
DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "log"
REQUIRED_EXECUTE_FLAGS = ("--execute", "--device", "--confirm-g1", "--confirm-recovery-v16",
                          "--acknowledge-nand-write", "--expected-sha256")


class CommandNotAllowed(PermissionError):
    """A command outside WRITER_ALLOWED_COMMANDS was requested."""


class SgIoHdr(ctypes.Structure):
    """Linux `sg_io_hdr_t` structure for `ioctl(fd, SG_IO, ...)`."""

    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


@dataclass
class UpdateItem:
    index: int
    raw_path: str
    resolved_path: Path
    drive_index: int
    usb_offset: int

    @property
    def start_lba(self) -> int:
        return usb_offset_to_lba(self.usb_offset)

    @property
    def phys_offset(self) -> int:
        return usb_offset_to_phys_offset(self.usb_offset)

    @property
    def start_block(self) -> int:
        return phys_offset_to_block(self.phys_offset)


@dataclass
class UsbToolConfig:
    ini_path: Path
    ut_mode: int = 1
    Multi_device: int = 0
    reset_after: bool = False
    nand_擦除: int = 0
    items: List[UpdateItem] = field(default_factory=list)


@dataclass
class ScsiCommandRecord:
    opcode: int
    lba: int
    sectors: int
    direction: str
    description: str
    cdb: bytes
    packet: bytes = b""
    status: str = "ok"


def build_scsi_rw10_cdb(opcode: int, lba: int, sectors: int) -> bytes:
    """Build a 10-byte SCSI READ_10 (0x28) or WRITE_10 (0x2A) CDB (Big-Endian LBA & length)."""
    if opcode not in (SCSI_OP_READ_10, SCSI_OP_WRITE_10):
        raise ValueError(f"Unsupported SCSI RW10 opcode: 0x{opcode:02X}")
    if not (0 <= lba <= 0xFFFFFFFF):
        raise ValueError(f"LBA out of 32-bit range: {lba}")
    if not (0 < sectors <= 0xFFFF):
        raise ValueError(f"Sector count out of 16-bit range: {sectors}")
    return struct.pack(">BBIBHB", opcode, 0, lba, 0, sectors, 0)


def build_bxc_control_packet(cmd_id: int, arg: int = 0) -> bytes:
    """
    Build the 512-byte BXCBOOT0 control packet written to LBA 170 (0xAA).
    Layout: `<IIIII` (0xAA5555AA, 0x44435842 "BXCD", 512, cmd_id, arg) + 492 zero bytes.
    """
    header = struct.pack(
        "<IIIII",
        BXC_MAGIC_1,
        BXC_MAGIC_2,
        BXC_PACKET_SIZE,
        cmd_id & 0xFFFFFFFF,
        arg & 0xFFFFFFFF,
    )
    return header + b"\x00" * (BXC_PACKET_SIZE - len(header))


def parse_bxc_control_packet(packet: bytes) -> Tuple[int, int]:
    """
    Validate and unpack `(cmd_id, arg)` from a 512-byte BXCBOOT0 control packet.
    Raises ValueError if magic or size fields do not match.
    """
    if len(packet) != BXC_PACKET_SIZE:
        raise ValueError(f"Control packet must be {BXC_PACKET_SIZE} bytes, got {len(packet)}")
    m1, m2, size, cmd_id, arg = struct.unpack_from("<IIIII", packet, 0)
    if m1 != BXC_MAGIC_1 or m2 != BXC_MAGIC_2 or size != BXC_PACKET_SIZE:
        raise ValueError(
            f"Invalid BXCBOOT0 packet header: magic1=0x{m1:08X}, magic2=0x{m2:08X}, size={size}"
        )
    return cmd_id, arg


def parse_usbtool_ini(ini_path: Union[Path, str], base_dir: Optional[Path] = None) -> UsbToolConfig:
    r"""
    Parse a Windows `usbtool.ini` file (`[Update Infomation]` section) as `usbtool.exe` does.
    Normalizes Windows relative paths (`.\BOOT1.ROM` -> `BOOT1.ROM`).
    """
    path = Path(ini_path)
    root_dir = base_dir if base_dir is not None else path.parent
    cfg = UsbToolConfig(ini_path=path)

    raw_kv: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";") or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            raw_kv[k.strip()] = v.strip()

    if "UT Mode" in raw_kv:
        cfg.ut_mode = int(raw_kv["UT Mode"], 0)
    if "Reset" in raw_kv:
        cfg.reset_after = bool(int(raw_kv["Reset"], 0))

    num_items = int(raw_kv.get("UpdateItems", "0"), 0)
    for idx in range(num_items):
        key = f"UpdateItem{idx:02d}"
        if key not in raw_kv:
            raise ValueError(f"Missing {key} in {path} (UpdateItems={num_items})")
        parts = raw_kv[key].split()
        if len(parts) != 3:
            raise ValueError(
                f"Invalid {key} format in {path}: expected '<file> <drive> <hex_offset>', "
                f"got {raw_kv[key]!r}"
            )
        raw_file, drive_str, offset_str = parts
        normalized_rel = raw_file.replace("\\", "/")
        if normalized_rel.startswith("./"):
            normalized_rel = normalized_rel[2:]
        resolved = (root_dir / normalized_rel).resolve()
        drive_idx = int(drive_str, 0)
        usb_offset = int(offset_str, 16) if not offset_str.lower().startswith("0x") else int(offset_str, 0)
        cfg.items.append(
            UpdateItem(
                index=idx,
                raw_path=raw_file,
                resolved_path=resolved,
                drive_index=drive_idx,
                usb_offset=usb_offset,
            )
        )

    return cfg


def check_control_command(cmd_id: int, arg: int, allow_reboot: bool = True) -> str:
    """Return the allowlist kind of a vendor packet command or raise CommandNotAllowed (before any packet)."""
    if cmd_id in FORBIDDEN_COMMANDS:
        raise CommandNotAllowed(f"blocked: {FORBIDDEN_COMMANDS[cmd_id]}")
    if cmd_id == CMD_HANDSHAKE and arg == ARG_HANDSHAKE:
        return "handshake"
    if cmd_id == CMD_PROGRESS and arg == 0:
        return "unlock"
    if cmd_id == CMD_PROGRESS and 1 <= arg <= 99:
        return "progress"
    if cmd_id == CMD_PROGRESS and arg == 100:
        return "lock"
    if cmd_id == CMD_REBOOT and arg == 0:
        if not allow_reboot:
            raise CommandNotAllowed("blocked: 0xBEC1 reboot needs the explicit --reboot option")
        return "reboot"
    raise CommandNotAllowed(f"blocked: vendor command 0x{cmd_id:04X} arg 0x{arg:X} is not in the writer allowlist")


def build_vendor_packet(cmd_id: int, arg: int = 0) -> bytes:
    """264-byte vendor packet `<II cmd,arg>` + 256 zero bytes (T hpprime/vendor.py vendor_packet)."""
    check_control_command(cmd_id, arg)
    return struct.pack("<II", cmd_id & 0xFFFFFFFF, arg & 0xFFFFFFFF) + bytes(VENDOR_PACKET_SIZE - 8)


def parse_vendor_packet(packet: bytes) -> Tuple[int, int]:
    if not isinstance(packet, (bytes, bytearray)) or len(packet) != VENDOR_PACKET_SIZE:
        raise CommandNotAllowed(f"blocked: vendor packet must be {VENDOR_PACKET_SIZE} bytes")
    if any(packet[8:]):
        raise CommandNotAllowed("blocked: vendor control packets carry no payload")
    return struct.unpack_from("<II", packet, 0)


def expected_ack(cmd_id: int, arg: int) -> Optional[Tuple[int, int]]:
    """Acknowledgement (cmd|0x80000000, result) for a command; None for 0xBEC1 (no response)."""
    if cmd_id == CMD_REBOOT:
        return None
    result = HANDSHAKE_RESULT if cmd_id == CMD_HANDSHAKE else 0 if cmd_id == CMD_PROGRESS else arg
    return (cmd_id | VENDOR_ACK_FLAG) & 0xFFFFFFFF, result


def check_outgoing(opcode: int, lba: int = 0, sectors: int = 0, data: Optional[bytes] = None) -> str:
    """Transport-level allowlist (second line of defence). Returns the command kind."""
    if opcode == SCSI_OP_READ_10:
        raise CommandNotAllowed(f"{READ10_BLOCKED}; requested LBA {lba} / {sectors} sectors")
    if opcode == SCSI_OP_BXC_SEND:
        cmd_id, arg = parse_vendor_packet(data)
        return check_control_command(cmd_id, arg)
    if opcode == SCSI_OP_BXC_RECV:
        if data is not None:
            raise CommandNotAllowed("blocked: vendor 0x89 receives only")
        return "ack"
    if opcode != SCSI_OP_WRITE_10:
        raise CommandNotAllowed(f"blocked: SCSI opcode 0x{opcode:02X}")
    if lba == SPECIAL_CONTROL_LBA:
        raise CommandNotAllowed("blocked: WRITE_10 LBA 170 control packets are replaced by vendor 0x88")
    try:
        write_plan.check_chunk(lba, sectors)
    except ValueError as error:
        raise CommandNotAllowed(f"blocked: {error}") from error
    return "data"


class CommandLog:
    """JSON Lines record of every SCSI command the writer sends (one object per line)."""

    def __init__(self, path: Union[Path, str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("a", encoding="utf-8")
        self.seq = 0

    @staticmethod
    def default_path(prefix: str = "usbtool_v3_flash") -> Path:
        return DEFAULT_LOG_DIR / f"{time.strftime('%Y%m%dT%H%M%S')}_{prefix}.jsonl"

    def record(self, **entry) -> None:
        self.seq += 1
        entry = {"seq": self.seq, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}
        self._fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._fp.flush()

    def close(self) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None


class ScsiTransport:
    """Abstract base transport for BXCBOOT0 SCSI commands."""

    def read_sectors(self, lba: int, sectors: int, description: str = "") -> bytes:
        raise NotImplementedError

    def write_sectors(self, lba: int, data: bytes, description: str = "") -> None:
        raise NotImplementedError

    def vendor_send(self, packet: bytes, description: str = "") -> None:
        raise NotImplementedError

    def vendor_receive(self, description: str = "") -> bytes:
        raise NotImplementedError


class DryRunTransport(ScsiTransport):
    """
    Safe simulation transport that logs every SCSI CDB and control packet
    without touching physical hardware. Optionally mirrors writes into a backing file.

    Lock model (S6, reference V16): starts locked (flag [0x30d6f412] = 1 at 0xe668).
    Vendor 0x88 packets are parsed without a lock check (0xc7dc). 0xBEDC <100 sets the
    flag to 0 (unlocked), >= 100 sets it to 1 (0xc8c4..0xc8ec); 0xBECD leaves it alone;
    0xBEC1 resets to locked. A data WRITE_10 while locked is recorded as
    "write-protected" and not mirrored (0xc578). Every 0x88 except 0xBEC1 queues one
    264-byte 0x89 response (cmd|0x80000000, result).
    """

    def __init__(self, mirror_file: Optional[Path] = None):
        self.history: List[ScsiCommandRecord] = []
        self.unlocked: bool = False
        self.last_progress: int = 0
        self.rebooted: bool = False
        self._pending: Optional[bytes] = None
        self.mirror_file = mirror_file
        self._mirror_fp = mirror_file.open("r+b") if (mirror_file and mirror_file.exists()) else None
        if mirror_file and not mirror_file.exists():
            mirror_file.parent.mkdir(parents=True, exist_ok=True)
            self._mirror_fp = mirror_file.open("w+b")

    def close(self) -> None:
        if self._mirror_fp is not None:
            self._mirror_fp.close()
            self._mirror_fp = None

    def read_sectors(self, lba: int, sectors: int, description: str = "") -> bytes:
        check_outgoing(SCSI_OP_READ_10, lba, sectors)       # always raises: READ_10 is never sent
        raise CommandNotAllowed(READ10_BLOCKED)

    def write_sectors(self, lba: int, data: bytes, description: str = "") -> None:
        if len(data) % USB_SECTOR_SIZE != 0:
            raise ValueError(f"Data length ({len(data)}) must be a multiple of {USB_SECTOR_SIZE}")
        sectors = len(data) // USB_SECTOR_SIZE
        check_outgoing(SCSI_OP_WRITE_10, lba, sectors, data)
        cdb = build_scsi_rw10_cdb(SCSI_OP_WRITE_10, lba, sectors)

        if lba == SPECIAL_CONTROL_LBA:
            raise CommandNotAllowed("blocked: WRITE_10 LBA 170")      # check_outgoing already refuses
        desc = description or f"WRITE_10 LBA {lba} ({sectors} sectors)"
        status = "ok"
        if not self.unlocked:
            # V16 WRITE_10 mode 2 (0xc578): lock flag 1 -> sense 0x27, nothing is programmed.
            status = "write-protected"
            desc += " [write-protected: not written]"
        elif self._mirror_fp is not None:
            self._mirror_fp.seek(lba * USB_SECTOR_SIZE)
            self._mirror_fp.write(data)
        self.history.append(ScsiCommandRecord(SCSI_OP_WRITE_10, lba, sectors, "TO_DEV", desc, cdb,
                                              status=status))

    def vendor_send(self, packet: bytes, description: str = "") -> None:
        check_outgoing(SCSI_OP_BXC_SEND, data=packet)
        if self._pending is not None:
            raise OSError("Invalid simulated vendor sequence: previous 0x89 response not read")
        cmd_id, arg = parse_vendor_packet(packet)
        if cmd_id == CMD_PROGRESS:
            # 0xc8c4..0xc8ec: lock flag = 0 if arg < 100 else 1
            self.last_progress = arg
            self.unlocked = arg < 100
        elif cmd_id == CMD_REBOOT:
            self.rebooted = True
            self.unlocked = False
        ack = expected_ack(cmd_id, arg)
        if ack is not None:
            self._pending = struct.pack("<II", *ack) + bytes(VENDOR_PACKET_SIZE - 8)
        desc = description or f"vendor 0x88 packet 0x{cmd_id:04X} arg {arg}"
        self.history.append(ScsiCommandRecord(SCSI_OP_BXC_SEND, 0, 0, "TO_DEV", desc, VENDOR_SEND_CDB,
                                              packet=bytes(packet)))

    def vendor_receive(self, description: str = "") -> bytes:
        check_outgoing(SCSI_OP_BXC_RECV)
        if self._pending is None:
            raise OSError("Invalid simulated vendor sequence: no pending 0x88 command")
        response, self._pending = self._pending, None
        self.history.append(ScsiCommandRecord(SCSI_OP_BXC_RECV, 0, 0, "FROM_DEV",
                                              description or "vendor 0x89 response", VENDOR_RECEIVE_CDB,
                                              packet=response))
        return response


class LinuxSgIoTransport(ScsiTransport):
    """Real Linux SG_IO transport (`ioctl 0x2285`) for Recovery Mode `/dev/sdX` device.

    Construct only through open_checked_device() (preflight + confirmation flags).
    Opened with O_NONBLOCK (and O_EXCL when supported) so removable virtual SCSI disks open cleanly.
    """

    OPEN_FLAGS = os.O_RDWR | os.O_NONBLOCK

    def __init__(self, dev_path: Union[Path, str], timeout_ms: int = 10000):
        self.dev_path = str(dev_path)
        self.timeout_ms = timeout_ms
        self.fd = -1
        self.last_status: Optional[dict] = None
        try:
            self.fd = os.open(self.dev_path, os.O_RDWR | os.O_NONBLOCK | os.O_EXCL)
        except OSError:
            self.fd = os.open(self.dev_path, self.OPEN_FLAGS)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def _exec_sg_io(self, cdb: bytes, buf: ctypes.Array, direction: int, tolerate_status: bool = False) -> None:
        sense = ctypes.create_string_buffer(32)
        cdb_buf = ctypes.create_string_buffer(cdb, len(cdb))
        hdr = SgIoHdr()
        hdr.interface_id = ord("S")
        hdr.dxfer_direction = direction
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = len(sense)
        hdr.iovec_count = 0
        hdr.dxfer_len = len(buf)
        hdr.dxferp = ctypes.cast(buf, ctypes.c_void_p)
        hdr.cmdp = ctypes.cast(cdb_buf, ctypes.c_void_p)
        hdr.sbp = ctypes.cast(sense, ctypes.c_void_p)
        hdr.timeout = self.timeout_ms
        hdr.flags = 0

        rc = fcntl.ioctl(self.fd, SG_IO, hdr)
        self.last_status = {"rc": rc, "status": hdr.status, "host_status": hdr.host_status,
                            "driver_status": hdr.driver_status}
        failed = rc < 0 or hdr.status != 0 or hdr.host_status != 0 or hdr.driver_status != 0
        if failed and not (tolerate_status and rc >= 0):
            raise OSError(
                f"SG_IO failed on {self.dev_path}: rc={rc}, status=0x{hdr.status:02X}, "
                f"host_status=0x{hdr.host_status:04X}, driver_status=0x{hdr.driver_status:04X}"
            )

    def read_sectors(self, lba: int, sectors: int, description: str = "") -> bytes:
        check_outgoing(SCSI_OP_READ_10, lba, sectors)       # always raises: READ_10 is never sent
        raise CommandNotAllowed(READ10_BLOCKED)

    def write_sectors(self, lba: int, data: bytes, description: str = "") -> None:
        if len(data) % USB_SECTOR_SIZE != 0:
            raise ValueError(f"Data length ({len(data)}) must be a multiple of {USB_SECTOR_SIZE}")
        sectors = len(data) // USB_SECTOR_SIZE
        check_outgoing(SCSI_OP_WRITE_10, lba, sectors, data)
        cdb = build_scsi_rw10_cdb(SCSI_OP_WRITE_10, lba, sectors)
        buf = ctypes.create_string_buffer(data, len(data))
        self._exec_sg_io(cdb, buf, SG_DXFER_TO_DEV)

    def vendor_send(self, packet: bytes, description: str = "") -> None:
        """SCSI 0x88, 264 bytes TO_DEV (T hpprime/vendor.py LinuxTransport.transfer framing)."""
        check_outgoing(SCSI_OP_BXC_SEND, data=packet)
        buf = ctypes.create_string_buffer(bytes(packet), VENDOR_PACKET_SIZE)
        self._exec_sg_io(VENDOR_SEND_CDB, buf, SG_DXFER_TO_DEV)

    def vendor_receive(self, description: str = "") -> bytes:
        """SCSI 0x89, 264 bytes FROM_DEV."""
        check_outgoing(SCSI_OP_BXC_RECV)
        buf = ctypes.create_string_buffer(VENDOR_PACKET_SIZE)
        self._exec_sg_io(VENDOR_RECEIVE_CDB, buf, SG_DXFER_FROM_DEV)
        return bytes(buf.raw[:VENDOR_PACKET_SIZE])


class BxcBoot0Controller:
    """High-level BXCBOOT0 Recovery Mode protocol controller (writer allowlist enforced).

    Writer order (one session for all images, S6 vendor path):
      0x88 0xBECD/0xBEAD -> 0x89 ack -> 0x88 0xBEDC 0 (unlock) -> 0x89 ack
      -> {0x88 0xBEDC 1..99 + 0x89 ack, WRITE_10 chunks} -> 0x88 0xBEDC 100 (lock) -> 0x89 ack
      -> optional 0x88 0xBEC1 0 (allow_reboot only, no ack).
    Every acknowledgement is checked; a mismatch raises OSError (no retry).
    """

    def __init__(self, transport: ScsiTransport, log: Optional["CommandLog"] = None,
                 allow_reboot: bool = False):
        self.transport = transport
        self.log = log
        self.allow_reboot = allow_reboot
        self.unlocked = False

    def _log(self, entry: dict, error: Optional[BaseException] = None) -> None:
        if self.log is None:
            return
        entry["result"] = "error" if error else "ok"
        status = getattr(self.transport, "last_status", None)
        if status is not None:
            entry["sg_status"] = status
        if error is not None:
            entry["error"] = f"{type(error).__name__}: {error}"
        self.log.record(**entry)

    def _record(self, kind: str, opcode: int, lba: int, sectors: int, description: str,
                error: Optional[BaseException] = None, **extra) -> None:
        self._log({"kind": kind, "opcode": f"0x{opcode:02X}", "lba": lba, "sectors": sectors,
                   "cdb": build_scsi_rw10_cdb(opcode, lba, sectors).hex(" "),
                   "description": description, **extra}, error)

    def _write(self, kind: str, lba: int, data: bytes, description: str, **extra) -> None:
        sectors = len(data) // USB_SECTOR_SIZE
        try:
            self.transport.write_sectors(lba, data, description=description)
        except BaseException as error:
            self._record(kind, SCSI_OP_WRITE_10, lba, sectors, description, error, **extra)
            raise
        self._record(kind, SCSI_OP_WRITE_10, lba, sectors, description, **extra)

    def vendor_command(self, cmd_id: int, arg: int = 0, description: str = "") -> Optional[Tuple[int, int]]:
        """0x88 packet, then (except 0xBEC1) one 0x89 response checked against expected_ack.

        Returns the (cmd, result) acknowledgement, None for 0xBEC1. Raises OSError on mismatch.
        """
        kind = check_control_command(cmd_id, arg, allow_reboot=self.allow_reboot)   # before the packet
        packet = build_vendor_packet(cmd_id, arg)
        description = description or f"vendor 0x{cmd_id:04X} {arg}"
        base = {"opcode": f"0x{SCSI_OP_BXC_SEND:02X}", "cdb": VENDOR_SEND_CDB.hex(" "),
                "vendor_cmd": f"0x{cmd_id:04X}", "vendor_arg": arg, "description": description}
        try:
            self.transport.vendor_send(packet, description=description)
        except BaseException as error:
            self._log({"kind": kind, **base}, error)
            raise
        self._log({"kind": kind, **base})
        if cmd_id == CMD_PROGRESS:
            self.unlocked = arg < 100      # the firmware may have changed the flag even if the ack fails
        elif cmd_id == CMD_REBOOT:
            self.unlocked = False
            return None
        want = expected_ack(cmd_id, arg)
        entry = {"kind": "ack", "opcode": f"0x{SCSI_OP_BXC_RECV:02X}", "cdb": VENDOR_RECEIVE_CDB.hex(" "),
                 "vendor_cmd": f"0x{cmd_id:04X}", "expected": [f"0x{v:08X}" for v in want],
                 "description": f"vendor 0x89 response to 0x{cmd_id:04X} {arg}"}
        try:
            response = self.transport.vendor_receive(description=entry["description"])
            if len(response) != VENDOR_PACKET_SIZE:
                raise OSError(f"Short vendor response ({len(response)} bytes) to 0x{cmd_id:04X}; no retry")
            got = struct.unpack_from("<II", response, 0)
            entry["response"] = [f"0x{v:08X}" for v in got]
            if got != want:
                raise OSError(f"Unexpected acknowledgement for 0x{cmd_id:04X} {arg}: "
                              f"got 0x{got[0]:08X}, 0x{got[1]:08X}; want 0x{want[0]:08X}, 0x{want[1]:08X}; no retry")
        except BaseException as error:
            self._log(entry, error)
            raise
        self._log(entry)
        return got

    # Old name kept for callers; same vendor path.
    send_command = vendor_command

    def handshake(self) -> Tuple[int, int]:
        """0xBECD/0xBEAD; ack must be (0x8000BECD, 0x8000BEAD). Does not touch the lock flag (0xc7dc)."""
        return self.vendor_command(CMD_HANDSHAKE, ARG_HANDSHAKE, description="BXCBOOT0 Handshake (0xBECD/0xBEAD)")

    def unlock_writes(self) -> None:
        """0xBEDC 0: lock flag [0x30d6f412] = 0 (0xc8c4..0xc8ec). Replaces the LBA 170 / 30-sector READ."""
        self.vendor_command(CMD_PROGRESS, 0, description="BXCBOOT0 Unlock (0xBEDC 0)")

    def lock_writes(self) -> None:
        """0xBEDC 100: lock flag = 1."""
        self.vendor_command(CMD_PROGRESS, 100, description="BXCBOOT0 Lock (0xBEDC 100)")

    def set_progress(self, percent: int) -> None:
        pct = int(percent)
        if not 0 <= pct <= 100:
            raise CommandNotAllowed(f"blocked: progress {pct} outside 0..100")
        if pct == 0:
            return self.unlock_writes()
        if pct == 100:
            return self.lock_writes()
        self.vendor_command(CMD_PROGRESS, pct, description=f"BXCBOOT0 Progress {pct}% (0xBEDC)")

    def reboot(self) -> None:
        """0xBEC1 0, no response (only with allow_reboot)."""
        self.vendor_command(CMD_REBOOT, 0, description="BXCBOOT0 Reboot (0xBEC1)")

    def scan_bad_blocks(self, arg: int = 0) -> None:
        # Blocked 2026-11-17: in reference V16 Boot0, 0xBEC2 runs 0x7ed4 -> 0x73e4(sl=1), which erases
        # physical NAND address 0 (Boot0 block 0) at 0x7988 and rewrites it. Power loss there bricks Boot0.
        # See 30_TEST/30_Boot0-Path-Survey/S1_write10-mapping.md. Do not re-enable without user approval.
        raise PermissionError(
            "blocked: 0xBEC2 erases and rewrites Boot0 block 0 (reference V16 0x7988); "
            "see 30_TEST/30_Boot0-Path-Survey/S1_write10-mapping.md"
        )

    def flash_file_at_usb_offset(
        self,
        file_path: Path,
        usb_offset: int,
        chunk_sectors: int = DEFAULT_CHUNK_SECTORS,
        progress_range: Tuple[int, int] = (0, 99),
    ) -> int:
        """
        Flash one file at `usb_offset` as a complete session
        (handshake -> 0xBEDC 0 -> progress/chunks -> 0xBEDC 100).
        The image is judged by write_plan.plan_writes and sent only as
        write_plan.transfer_chunks pieces; the tail page is 0xFF filled.
        Returns bytes sent (whole pages).
        """
        image = write_plan.load_plan_image(Path(file_path), usb_offset)
        results = self.flash_images([image], chunk_sectors=chunk_sectors, progress_range=progress_range)
        return results[0]

    def plan_images(self, images, chunk_sectors: int = DEFAULT_CHUNK_SECTORS, bad_blocks=()):
        """Judge every image and compute every chunk before any command is sent."""
        plans = write_plan.plan_writes(images, bad_blocks)
        chunks = [write_plan.transfer_chunks(image, chunk_sectors) for image in images]
        return plans, chunks

    def flash_images(self, images, chunk_sectors: int = DEFAULT_CHUNK_SECTORS,
                     progress_range: Tuple[int, int] = (0, 99), reboot: bool = False,
                     bad_blocks=()) -> List[int]:
        """One handshake/unlock/lock session for all images. Returns bytes sent per image.

        Order: 0xBECD -> 0xBEDC 0 -> {0xBEDC 1..99, WRITE_10 chunks} -> 0xBEDC 100 [-> 0xBEC1].
        The handshake does not change the lock flag, but an earlier session (for example
        the 2026-11-17 21:55 unlock READ) may have left it at 0, so any failure from the
        handshake on (ack mismatch, SG_IO error) stops and tries 0xBEDC 100 once; the
        original error is re-raised.
        """
        if reboot and not self.allow_reboot:
            raise CommandNotAllowed("blocked: 0xBEC1 reboot needs the explicit --reboot option")
        _, all_chunks = self.plan_images(images, chunk_sectors, bad_blocks)
        total = sum(count for chunks in all_chunks for _, count in chunks)
        p_start, p_end = progress_range
        p_start = max(1, p_start)          # 0 is the unlock itself
        p_end = max(p_start, min(99, p_end))
        sent = 0
        last_pct = 0
        results: List[int] = []

        try:
            self.handshake()
            self.unlock_writes()
            for image, chunks in zip(images, all_chunks):
                written = 0
                for lba, count in chunks:
                    pct = min(p_end, p_start + ((p_end - p_start) * sent) // max(1, total))
                    if pct != last_pct:
                        self.set_progress(pct)
                        last_pct = pct
                    payload = write_plan.chunk_payload(image, lba, count)
                    self._write("data", lba, payload,
                                f"Write {Path(image.path).name} LBA {lba} ({count} sectors)",
                                image=Path(image.path).name)
                    written += len(payload)
                    sent += count
                results.append(written)
        except BaseException:
            try:
                self.lock_writes()              # best-effort relock; original error is re-raised
            except BaseException:
                pass
            raise
        self.lock_writes()
        if reboot:
            # Vendor 0x88 packets are parsed without a lock check (0xc7dc), so 0xBEC1 goes right
            # after the lock; no re-unlock is needed.
            self.reboot()
        return results

    def flash_from_ini(
        self,
        cfg: UsbToolConfig,
        chunk_sectors: int = DEFAULT_CHUNK_SECTORS,
        reboot: bool = False,
        bad_blocks=(),
    ) -> List[Tuple[UpdateItem, int]]:
        """Execute multi-item flashing defined in `usbtool.ini`.

        ini `Reset = 1` alone does not reboot; 0xBEC1 is sent only when `reboot`
        is True (CLI --reboot) and the controller was built with allow_reboot.
        """
        if not cfg.items:
            return []
        images = load_config_images(cfg)
        written = self.flash_images(images, chunk_sectors=chunk_sectors, reboot=reboot,
                                    bad_blocks=bad_blocks)
        return list(zip(cfg.items, written))


def load_config_images(cfg: UsbToolConfig) -> list:
    """Load every UpdateItem as a write_plan.PlanImage (drive index 0 only)."""
    images = []
    for item in cfg.items:
        if item.drive_index != 0:
            raise ValueError(f"UpdateItem{item.index:02d}: only drive index 0 is supported")
        if not item.resolved_path.exists():
            raise FileNotFoundError(f"UpdateItem{item.index:02d} file not found: {item.resolved_path}")
        images.append(write_plan.load_plan_image(item.resolved_path, item.usb_offset))
    return images


def detect_hp_prime_usb() -> List[dict]:
    """
    Scan `/sys/bus/usb/devices` (read-only) to detect connected HP Prime calculators
    and distinguish Normal Calculator HID Mode (Class 03) vs Recovery MSC Mode (Class 08).
    """
    sys_usb = Path("/sys/bus/usb/devices")
    found: List[dict] = []
    if not sys_usb.exists():
        return found

    for dev_dir in sorted(sys_usb.iterdir()):
        vid_file = dev_dir / "idVendor"
        pid_file = dev_dir / "idProduct"
        if not (vid_file.exists() and pid_file.exists()):
            continue
        vid = vid_file.read_text(errors="ignore").strip().lower()
        pid = pid_file.read_text(errors="ignore").strip().lower()
        if vid != HP_PRIME_VID or pid not in HP_PRIME_PIDS:
            continue

        product = (dev_dir / "product").read_text(errors="ignore").strip() if (dev_dir / "product").exists() else ""
        manufacturer = (
            (dev_dir / "manufacturer").read_text(errors="ignore").strip()
            if (dev_dir / "manufacturer").exists()
            else ""
        )

        interfaces = []
        block_devs = []
        for sub in sorted(dev_dir.glob(f"{dev_dir.name}:*")):
            cls_file = sub / "bInterfaceClass"
            if cls_file.exists():
                icls = cls_file.read_text(errors="ignore").strip().lower()
                interfaces.append(icls)
            for blk in sub.rglob("block/*"):
                if blk.is_dir():
                    block_devs.append(f"/dev/{blk.name}")

        if "08" in interfaces:
            mode = "RECOVERY_MSC (BXCBOOT0 USB Mass Storage - Ready for SG_IO)"
        elif "03" in interfaces:
            mode = "NORMAL_HID (Calculator OS Running - Not in Recovery Mode)"
        else:
            mode = f"UNKNOWN (Interfaces: {interfaces})"

        found.append(
            {
                "sysfs_node": str(dev_dir),
                "usb_id": f"{vid}:{pid}",
                "manufacturer": manufacturer,
                "product": product,
                "interfaces": interfaces,
                "mode": mode,
                "block_devices": block_devs,
            }
        )
    return found


def print_ini_summary(cfg: UsbToolConfig) -> None:
    print("================================================================================")
    print(f" usbtool.ini Analysis : {cfg.ini_path}")
    print(f" UT Mode              : {cfg.ut_mode} | Reset After: {cfg.reset_after}")
    print("================================================================================")
    for item in cfg.items:
        exists_str = "FOUND" if item.resolved_path.exists() else "MISSING"
        fsize = item.resolved_path.stat().st_size if item.resolved_path.exists() else 0
        phys_start = item.phys_offset
        phys_end = phys_start + fsize
        start_blk = item.start_block
        end_blk = phys_offset_to_block(max(phys_start, phys_end - 1)) if fsize > 0 else start_blk
        parts = find_partitions_for_phys_range(phys_start, max(1, fsize))
        part_names = ", ".join(f"mtd{p.index}({p.name})" for p in parts)

        print(f" [UpdateItem{item.index:02d}] {item.raw_path} -> {item.resolved_path.name} ({exists_str})")
        print(f"   File Size          : {fsize:,} bytes ({fsize / 1024:.1f} KiB)")
        print(f"   USB MSC Offset     : 0x{item.usb_offset:08X} (Start LBA: {item.start_lba})")
        print(
            f"   Physical NAND Addr : 0x{phys_start:08X} .. 0x{max(phys_start, phys_end - 1):08X} "
            f"(+0x00040000 Recovery shift)"
        )
        print(f"   Physical NAND Blks : Block {start_blk} .. {end_blk} (128 KiB/block)")
        print(f"   Target Linux MTD   : {part_names}")
        print("--------------------------------------------------------------------------------")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="HP Prime G1 Recovery Mode Flasher & Protocol Tool (Linux SG_IO replacement for usbtool.exe)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Detect connected HP Prime USB mode (Normal HID vs Recovery MSC)")

    p_ini = sub.add_parser("parse-ini", help="Parse usbtool.ini and show LBA / Physical NAND / MTD mapping")
    p_ini.add_argument("--ini", default="usbtool.ini", help="Path to usbtool.ini")

    p_flash = sub.add_parser("flash", help="Flash images defined in usbtool.ini (Dry-run by default)")
    p_flash.add_argument("--ini", default="usbtool.ini", help="Path to usbtool.ini")
    p_flash.add_argument("--device", default=None, help="Target Recovery Mode block device (e.g. /dev/sdX)")
    p_flash.add_argument(
        "--chunk-sectors",
        type=int,
        default=DEFAULT_CHUNK_SECTORS,
        help="Sectors per WRITE_10 command, multiple of 4, at most 256 (default: 64 = 32 KiB; "
             "the host usb-storage cap for this device is max_hw_sectors_kb=120, so 256 fails with EIO)",
    )
    p_flash.add_argument(
        "--execute",
        action="store_true",
        help="Execute real SG_IO writes to --device (default is safe --dry-run simulation)",
    )
    add_safety_arguments(p_flash)
    p_flash.add_argument("--acknowledge-nand-write", action="store_true",
                         help="Required with --execute: NAND blocks will be erased and written")
    p_flash.add_argument("--expected-sha256", action="append", default=[], metavar="HEX",
                         help="Required with --execute: sha256 of each ini image (repeat once per image)")
    p_flash.add_argument("--reboot", action="store_true",
                         help="Send 0xBEC1 after the final lock (ini 'Reset' alone does not reboot)")
    p_flash.add_argument("--bad-blocks", default="",
                         help="Known bad physical blocks N,N,... for the plan model (default none)")

    p_reboot = sub.add_parser("reboot", help="Send BXCBOOT0 Reboot command (0xBEC1) to Recovery device")
    p_reboot.add_argument("--device", default=None, help="Target Recovery Mode block device")
    p_reboot.add_argument("--execute", action="store_true", help="Execute real SG_IO command")
    add_safety_arguments(p_reboot)

    p_probe = sub.add_parser("probe", help="Send one vendor 0x88 0xBECD handshake and read the 0x89 "
                                           "response (no NAND write, no lock change)")
    p_probe.add_argument("--device", default=None, help="Target Recovery Mode block device")
    p_probe.add_argument("--execute", action="store_true", help="Execute real SG_IO command")
    add_safety_arguments(p_probe)

    args = parser.parse_args(argv)

    if args.cmd == "status":
        devices = detect_hp_prime_usb()
        if not devices:
            print("No HP Prime calculator (03f0:0441 or 03f0:1541) detected on USB bus.")
            return 0
        for d in devices:
            print("============================================================")
            print(f" USB ID        : {d['usb_id']} ({d['sysfs_node']})")
            print(f" Product       : {d['manufacturer']} - {d['product']}")
            print(f" Current Mode  : {d['mode']}")
            print(f" Block Devices : {d['block_devices'] or 'None (HID mode)'}")
            print("============================================================")
        return 0

    if args.cmd == "parse-ini":
        cfg = parse_usbtool_ini(args.ini)
        print_ini_summary(cfg)
        return 0

    if args.cmd == "flash":
        cfg = parse_usbtool_ini(args.ini)
        print_ini_summary(cfg)
        bad_blocks = write_plan.parse_bad_blocks(args.bad_blocks)
        images = load_config_images(cfg)
        # Plan and chunk rules are checked before any device or log is opened.
        plans, chunks = BxcBoot0Controller(DryRunTransport()).plan_images(images, args.chunk_sectors, bad_blocks)
        for item, image, plan, pieces in zip(cfg.items, images, plans, chunks):
            print(f"  PLAN UpdateItem{item.index:02d} {Path(image.path).name}: LBA {plan['lba_first']}..{plan['lba_last']}, "
                  f"physical blocks {plan['physical_blocks'][0]}..{plan['physical_blocks'][1]} "
                  f"({write_plan.MAPPING_NOTE}), {len(pieces)} WRITE_10, sha256 {image.sha256}")

        if not args.execute:
            print("[DRY-RUN MODE] Simulating BXCBOOT0 SCSI command stream (pass --execute for real device)...")
            log = CommandLog(args.log_jsonl) if args.log_jsonl else None
            transport = DryRunTransport()
            try:
                ctrl = BxcBoot0Controller(transport, log=log, allow_reboot=args.reboot)
                results = ctrl.flash_from_ini(cfg, chunk_sectors=args.chunk_sectors, reboot=args.reboot,
                                              bad_blocks=bad_blocks)
            finally:
                transport.close()
                if log is not None:
                    log.close()
            print(f"[DRY-RUN COMPLETE] Total SCSI commands generated: {len(transport.history):,}")
            for item, written in results:
                print(f"  - UpdateItem{item.index:02d} ({item.resolved_path.name}): "
                      f"{written:,} bytes -> LBA {item.start_lba}")
            print("  First 5 SCSI Commands:")
            for rec in transport.history[:5]:
                print(f"    CDB [{rec.cdb.hex(' ')}] -> {rec.description}")
            print("  Last 3 SCSI Commands:")
            for rec in transport.history[-3:]:
                print(f"    CDB [{rec.cdb.hex(' ')}] -> {rec.description}")
            if log is not None:
                print(f"  JSON Lines log: {log.path}")
            return 0

        require_execute_flags(args, flash=True)
        check_expected_sha256(images, args.expected_sha256)
        log = CommandLog(args.log_jsonl or CommandLog.default_path())
        sg_transport, metadata = open_checked_device(args.device)
        log.record(kind="preflight", device=metadata)
        try:
            ctrl = BxcBoot0Controller(sg_transport, log=log, allow_reboot=args.reboot)
            results = ctrl.flash_from_ini(cfg, chunk_sectors=args.chunk_sectors, reboot=args.reboot,
                                          bad_blocks=bad_blocks)
            for item, written in results:
                print(f"Flashed {item.resolved_path.name}: {written:,} bytes at LBA {item.start_lba}")
        finally:
            sg_transport.close()
            log.close()
        print(NO_READBACK_NOTE)
        print(f"JSON Lines log: {log.path}")
        return 0

    if args.cmd == "reboot":
        # Vendor 0x88 has no lock check (0xc7dc), so 0xBEC1 needs no unlock first.
        if not args.execute:
            transport = DryRunTransport()
            ctrl = BxcBoot0Controller(transport, allow_reboot=True)
            ctrl.reboot()
            for rec in transport.history:
                print(f"[DRY-RUN] CDB: [{rec.cdb.hex(' ')}] -> {rec.description}")
            return 0
        require_execute_flags(args, flash=False)
        log = CommandLog(args.log_jsonl or CommandLog.default_path("usbtool_v3_reboot"))
        sg_transport, metadata = open_checked_device(args.device)
        log.record(kind="preflight", device=metadata)
        try:
            ctrl = BxcBoot0Controller(sg_transport, log=log, allow_reboot=True)
            ctrl.reboot()
            print("Reboot command (0xBEC1) sent.")
        finally:
            sg_transport.close()
            log.close()
        return 0

    if args.cmd == "probe":
        return run_probe(args)

    return 1


def run_probe(args) -> int:
    """One 0x88 0xBECD/0xBEAD packet and one 0x89 response. No WRITE_10, no READ_10, no lock change.

    0xBECD in the 0xc7dc packet parser only builds the reply; the lock flag [0x30d6f412] is written
    only at 0xc4d4..0xc4f4 (LBA 170 READ), 0xc8c4..0xc8ec (0xBEDC) and 0xe668 (init).
    """
    if not args.execute:
        transport = DryRunTransport()
        log = CommandLog(args.log_jsonl) if args.log_jsonl else None
        try:
            ack = BxcBoot0Controller(transport, log=log).handshake()
        finally:
            if log is not None:
                log.close()
        for rec in transport.history:
            print(f"[DRY-RUN] CDB: [{rec.cdb.hex(' ')}] -> {rec.description}")
        print(f"[DRY-RUN] PROBE_ACK: 0x{ack[0]:08X} 0x{ack[1]:08X} (expected 0x8000BECD 0x8000BEAD)")
        return 0
    require_execute_flags(args, flash=False)          # no --acknowledge-nand-write: nothing is written
    log = CommandLog(args.log_jsonl or CommandLog.default_path("usbtool_v3_probe"))
    sg_transport, metadata = open_checked_device(args.device)
    log.record(kind="preflight", device=metadata)
    try:
        ack = BxcBoot0Controller(sg_transport, log=log).handshake()
        print(f"PROBE_ACK: 0x{ack[0]:08X} 0x{ack[1]:08X} (expected 0x8000BECD 0x8000BEAD) OK")
    except OSError as error:
        print(f"PROBE_FAILED: {error}")
        print(f"JSON Lines log: {log.path}")
        return 2
    finally:
        sg_transport.close()
        log.close()
    print(f"JSON Lines log: {log.path}")
    return 0


def add_safety_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-g1", action="store_true",
                        help="Required with --execute: the target is an HP Prime G1 (G8X92AA)")
    parser.add_argument("--confirm-recovery-v16", action="store_true",
                        help="Required with --execute: the calculator runs recovery Boot0 V16 (03f0:0441)")
    parser.add_argument("--log-jsonl", nargs="?", const=str(CommandLog.default_path()), default=None,
                        metavar="PATH", help=f"Write every SCSI command as JSON Lines "
                                             f"(default dir {DEFAULT_LOG_DIR}; always on with --execute)")


def require_execute_flags(args, flash: bool) -> None:
    """Refuse (SystemExit) unless every confirmation flag is present. Runs before any device access."""
    missing = []
    if not args.execute:
        missing.append("--execute")
    if not args.device:
        missing.append("--device")
    if not args.confirm_g1:
        missing.append("--confirm-g1")
    if not args.confirm_recovery_v16:
        missing.append("--confirm-recovery-v16")
    if flash and not args.acknowledge_nand_write:
        missing.append("--acknowledge-nand-write")
    if flash and not args.expected_sha256:
        missing.append("--expected-sha256")
    if missing:
        raise SystemExit("ERROR: refusing to open the device; missing " + " ".join(missing))


def check_expected_sha256(images, expected) -> None:
    """Every ini image must have exactly one matching --expected-sha256 (order free)."""
    wanted = sorted(value.strip().lower() for value in expected)
    actual = sorted(image.sha256 for image in images)
    if wanted != actual:
        raise SystemExit("ERROR: --expected-sha256 values do not match the ini images exactly:\n  "
                         + "\n  ".join(f"{Path(image.path).name}: {image.sha256}" for image in images))


def open_checked_device(device, inspect=None):
    """device_check preflight, then O_RDWR|O_EXCL open. Returns (transport, metadata)."""
    inspect = inspect or device_check.inspect_device
    try:
        metadata = inspect(Path(device))
    except (OSError, ValueError) as error:
        raise SystemExit(f"ERROR: device preflight refused {device}: {error}")
    return LinuxSgIoTransport(device), metadata


if __name__ == "__main__":
    sys.exit(main())
