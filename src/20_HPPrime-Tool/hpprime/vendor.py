"""HP Prime G1 recovery vendor protocol (0xBECx packets over SCSI 0x88/0x89) and offline simulation.

Moved from 50_Recorvery/usbtool.py. Behaviour changes in this tool:
- The real (device) Boot0 path is blocked in code: BLOCKED_REASON. Only simulation runs.
- Plan summaries no longer compute a NAND physical offset ("physical_offset": "UNVERIFIED").
  The former "USB offset plus 0x40000" rule used a buffer size, not a proven address mapping.
- The 0x3EC0000 upper bound for any planned NAND write is kept.
"""

import configparser
import ctypes
import fcntl
import hashlib
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from . import BLOCKED_REASON, SECTOR_SIZE, WORKSPACE
from .transport import SG_DXFER_FROM_DEV, SG_DXFER_TO_DEV, SG_IO, SgIoHdr, check_sg_result

REFERENCE = WORKSPACE / "20_Repo/G1/HPPrimeG1FileDumper"
KNOWN_BOOT0 = {
    "v13": "a882bb7de810708283921f73f52c25e99aa472a05bd05546e0e7cec4bbeb87e1",
    "v16": "87d04e01c59d7bf912eef38a644314a643a0c5921cb69c2c1f24b61cd0dc6248",
}
WRITE_LIMIT = 0x3EC0000
UNVERIFIED = "UNVERIFIED"

VENDOR_SEND = bytes.fromhex("88 00 00 00 00 00 00 00 00 00")
VENDOR_RECEIVE = bytes.fromhex("89 00 00 00 00 00 00 00 00 00")
PACKET_SIZE = 264
HANDSHAKE = 0xBECD
SELECT_DEVICE = 0xBEC5
UPLOAD = 0xBEC6
COMMIT = 0xBEC7
PROGRESS = 0xBEDC
REBOOT = 0xBEC1
# 0xBEC2 ("scan bad blocks") is never sent. Reference V16: 0xc91c -> 0x7ed4 -> 0x8004 -> 0x73e4(sl=1)
# -> 0x7988 erases physical NAND address 0 (Boot0 block 0) and rewrites it. Power loss there loses Boot0.
SCAN_ERASES_BOOT0 = 0xBEC2
SCAN_BLOCKED_REASON = "blocked: 0xBEC2 erases and rewrites Boot0 block 0 (reference V16 0x7988)"

@dataclass(frozen=True)
class UpdateImage:
    path: Path
    data: bytes
    usb_offset: int
    kind: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def summary(self) -> dict:
        # No physical NAND address is derived: the USB-to-NAND mapping is unverified.
        return {"path": str(self.path), "size": len(self.data), "sha256": self.sha256,
                "kind": self.kind, "usb_offset": None if self.kind == "boot0" else self.usb_offset,
                "physical_offset": UNVERIFIED}


def load_image(path: Path, usb_offset: int = 0, boot0: bool = False) -> UpdateImage:
    if usb_offset < 0 or usb_offset % SECTOR_SIZE:
        raise ValueError("USB offset must be non-negative and 512-byte aligned")
    if not path.is_file():
        raise ValueError(f"Image is not a regular file: {path}")
    size = path.stat().st_size
    if not 0 < size <= 256 * 1024 * 1024:
        raise ValueError("Image is empty or exceeds NAND capacity")
    data = path.read_bytes()
    if len(data) != size:
        raise ValueError("Image changed while loading")
    named_boot0 = re.fullmatch(r"BXCBOOT0(?:V\d+)?\.BIN", path.name, re.IGNORECASE) is not None
    known_boot0 = hashlib.sha256(data).hexdigest() in KNOWN_BOOT0.values()
    kind = "boot0" if boot0 or named_boot0 or known_boot0 else "nand"
    if kind == "boot0":
        if usb_offset or size > 0x40000:
            raise ValueError("Boot0 must have offset zero and fit its 256 KiB region")
    elif usb_offset + ((size + 511) // 512) * 512 > WRITE_LIMIT:
        raise ValueError("NAND update crosses the protected user-data boundary")
    return UpdateImage(path.resolve(), data, usb_offset, kind)


def read_ini(path: Path) -> list[UpdateImage]:
    config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    with path.open(encoding="utf-8-sig") as stream:
        config.read_file(stream)
    if "Update Infomation" not in config:
        raise ValueError("Missing [Update Infomation] section")
    section = config["Update Infomation"]
    count = section.getint("UpdateItems", fallback=0)
    if not 1 <= count <= 10:
        raise ValueError("UpdateItems must be between 1 and 10")
    expected = {f"updateitem{index:02d}" for index in range(count)}
    actual = {key for key in section if re.fullmatch(r"updateitem\d+", key)}
    if actual != expected:
        raise ValueError("UpdateItems count and UpdateItemNN entries disagree")
    images = []
    for index in range(count):
        value = section[f"UpdateItem{index:02d}"]
        match = re.fullmatch(r'\s*(?:"([^"]+)"|(\S+))\s+(\d+)\s+(?:0[xX])?([0-9a-fA-F]+)\s*', value)
        if match is None:
            raise ValueError(f"Invalid UpdateItem{index:02d}: expected path drive hex-offset")
        filename, unquoted, drive, offset = match.groups()
        if int(drive) != 0:
            raise ValueError("Only explicit single-device drive index 0 is supported")
        image_path = Path((filename or unquoted).replace("\\", "/"))
        if re.match(r"^[a-zA-Z]:", str(image_path)):
            raise ValueError("Windows drive paths require an explicit Linux path")
        images.append(load_image(image_path if image_path.is_absolute() else path.parent / image_path, int(offset, 16)))
    if any(image.kind == "boot0" for image in images) and len(images) != 1:
        raise ValueError("Boot0 update must be isolated from other image updates")
    ordered = sorted(images, key=lambda image: image.usb_offset)
    for previous, following in zip(ordered, ordered[1:]):
        if previous.usb_offset + ((len(previous.data) + 511) // 512) * 512 > following.usb_offset:
            raise ValueError("Update regions overlap after sector padding")
    return images


def vendor_packet(command: int, argument: int, payload: bytes = b"") -> bytes:
    if command == SCAN_ERASES_BOOT0:
        raise PermissionError(SCAN_BLOCKED_REASON)
    if command not in {HANDSHAKE, SELECT_DEVICE, UPLOAD, COMMIT, PROGRESS, REBOOT}:
        raise ValueError("Unsupported vendor command")
    if not 0 <= argument <= 0xFFFFFFFF or len(payload) > 256:
        raise ValueError("Invalid vendor argument or payload length")
    if command == UPLOAD:
        if len(payload) != 256 or argument & 0xFFFF != 256 or (argument >> 16) % 256:
            raise ValueError("Upload requires a 256-byte payload and aligned offset")
    elif payload:
        raise ValueError("Only upload packets may contain image data")
    return struct.pack("<II", command, argument) + payload.ljust(256, b"\0")


def validate_boot0(image: UpdateImage, variant: str) -> None:
    if image.kind != "boot0" or image.usb_offset != 0 or len(image.data) != 0x40000:
        raise ValueError("Boot0 vendor update requires a complete 256 KiB image")
    if image.sha256 != KNOWN_BOOT0.get(variant):
        raise ValueError("Boot0 SHA-256 does not match the selected reference variant")


def boot0_packets(image: UpdateImage, variant: str, reboot: bool = False):
    validate_boot0(image, variant)
    yield vendor_packet(HANDSHAKE, 0xBEAD)
    yield vendor_packet(SELECT_DEVICE, 0)
    yield vendor_packet(PROGRESS, 0)
    last_progress = 0
    for offset in range(0, len(image.data), 256):
        yield vendor_packet(UPLOAD, ((offset & 0xFFFF) << 16) | 256, image.data[offset:offset + 256])
        progress = min(99, (offset + 256) * 100 // len(image.data))
        if progress != last_progress:
            yield vendor_packet(PROGRESS, progress)
            last_progress = progress
    yield vendor_packet(COMMIT, len(image.data))
    yield vendor_packet(PROGRESS, 100)
    if reboot:
        yield vendor_packet(REBOOT, 0)


def execute_packets(transport, packets, notify=print, delay=time.sleep) -> dict:
    uploaded = 0
    committed = False
    commands = 0
    for packet in packets:
        command, argument = struct.unpack_from("<II", packet)
        if command == COMMIT:
            notify("COMMIT_REQUEST: Boot0 erase/program may start now; no automatic retry")
        transport.transfer(VENDOR_SEND, packet, 120000 if command == COMMIT else 4000)
        delay(0.020)
        commands += 1
        if command == REBOOT:
            notify("REBOOT_REQUEST_SENT: device reconnection not verified")
            continue
        response = transport.transfer(VENDOR_RECEIVE, PACKET_SIZE, 2000)
        if len(response) != PACKET_SIZE:
            raise OSError("Short vendor response; stop and do not retry this session")
        response_command, response_argument = struct.unpack_from("<II", response)
        expected_argument = 0x8000BEAD if command == HANDSHAKE else 0 if command == PROGRESS else argument
        if (response_command, response_argument) != (command | 0x80000000, expected_argument):
            raise OSError(f"Unexpected acknowledgement for command 0x{command:04x}; no retry")
        if command == UPLOAD:
            uploaded += argument & 0xFFFF
        elif command == PROGRESS:
            notify(f"PROGRESS: {argument}%")
        elif command == COMMIT:
            committed = True
            notify("COMMIT_ACKNOWLEDGED: independent NAND read-back has NOT been performed")
    return {"commands": commands, "uploaded_bytes": uploaded, "commit_acknowledged": committed}


class SimulatedTransport:
    def __init__(self):
        self.calls = 0
        self.uploaded = bytearray()
        self.pending = None
        self.committed = False

    def transfer(self, cdb, data, timeout):
        self.calls += 1
        if cdb == VENDOR_SEND:
            if self.pending is not None or not isinstance(data, bytes) or len(data) != PACKET_SIZE:
                raise OSError("Invalid simulated send sequence")
            command, argument = struct.unpack_from("<II", data)
            if command == UPLOAD:
                if argument >> 16 != len(self.uploaded) % 65536 or argument & 0xFFFF != 256:
                    raise OSError("Invalid simulated upload offset")
                self.uploaded.extend(data[8:])
            if command == COMMIT:
                if argument != len(self.uploaded):
                    raise OSError("Incomplete simulated image")
                self.committed = True
            if command != REBOOT:
                result = 0x8000BEAD if command == HANDSHAKE else 0 if command == PROGRESS else argument
                self.pending = struct.pack("<II", command | 0x80000000, result) + bytes(256)
            return b""
        if cdb != VENDOR_RECEIVE or data != PACKET_SIZE or self.pending is None:
            raise OSError("Invalid simulated receive sequence")
        response, self.pending = self.pending, None
        return response


class LinuxTransport:
    """Audited 264-byte vendor framing over SG_IO.

    Opening a device is blocked: `__init__` always raises BLOCKED_REASON and never opens a file.
    `transfer` is kept only so its framing and error checks stay under test (on a fake fd).
    """

    def __init__(self, path: Path):
        raise ValueError(BLOCKED_REASON)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def transfer(self, cdb, data, timeout):
        sending = cdb == VENDOR_SEND and isinstance(data, bytes) and len(data) == PACKET_SIZE
        receiving = cdb == VENDOR_RECEIVE and data == PACKET_SIZE
        if not sending and not receiving:
            raise ValueError("Only the audited 264-byte vendor protocol is permitted")
        cdb_buffer = ctypes.create_string_buffer(cdb, len(cdb))
        buffer = ctypes.create_string_buffer(data, PACKET_SIZE) if sending else ctypes.create_string_buffer(PACKET_SIZE)
        sense = ctypes.create_string_buffer(32)
        header = SgIoHdr()
        header.interface_id = ord("S")
        header.dxfer_direction = SG_DXFER_TO_DEV if sending else SG_DXFER_FROM_DEV
        header.cmd_len = len(cdb)
        header.mx_sb_len = len(sense)
        header.dxfer_len = PACKET_SIZE
        header.dxferp = ctypes.cast(buffer, ctypes.c_void_p)
        header.cmdp = ctypes.cast(cdb_buffer, ctypes.c_void_p)
        header.sbp = ctypes.cast(sense, ctypes.c_void_p)
        header.timeout = timeout
        result = fcntl.ioctl(self.fd, SG_IO, header)
        if not check_sg_result(result, header):
            raise OSError(f"SG_IO failed: status={header.status}, host={header.host_status}, "
                          f"driver={header.driver_status}, resid={header.resid}; outcome may be unknown, no retry")
        return b"" if sending else buffer.raw
