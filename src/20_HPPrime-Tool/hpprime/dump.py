"""Read-only HP Prime G1 dumps: FileDumper NAND/OOB, recovery-disk response, offline image extract.

Moved from 30_TEST/20_ALL-Backup/backup.py. Reads only (READ_10 or pread). No write path exists.
The recovery-disk profile is the USB READ_10 *response* of the stock recovery firmware. It is
NOT NAND content: the firmware synthesizes it (see hpprime.synth and docs/).
"""

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import shutil
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import MIB, WORKSPACE, device
from .log import utc_now
from .transport import (
    BLKGETSIZE64,
    LOCK_SECTOR_COUNT,
    SG_DXFER_FROM_DEV,
    SG_IO,
    SPECIAL_CONTROL_LBA,
    UNLOCK_SECTOR_COUNT,
    SgIoHdr,
    build_scsi_rw10_cdb,
    check_sg_result,
)

CHUNK_SIZE = 16 * 1024
DEFAULT_OUTPUT = WORKSPACE / "10_Backup"

PROFILE = "hpprime-g1-filedumper-528mib"
DISK_RESPONSE_PROFILE = "hpprime-g1-recovery-disk-response"
# Former name of DISK_RESPONSE_PROFILE. Existing manifests in 10_Backup use it, so `verify`
# accepts it. New dumps are never written under this name.
LEGACY_PREPATCH_PROFILE = "hpprime-g1-recovery-prepatch"
PROFILE_ALIASES = {LEGACY_PREPATCH_PROFILE: DISK_RESPONSE_PROFILE}

DISK_SIZE = 528 * MIB


@dataclass(frozen=True)
class Region:
    name: str
    offset: int
    size: int


REGIONS = (
    Region("nanddump.bin", 256 * MIB, 256 * MIB),
    Region("oobdump.bin", 512 * MIB, 8 * MIB),
)
# Capacity reported by the stock recovery disk. 0x40000 is that firmware's buffer size, not a
# proven NAND offset; nothing here converts USB offsets into NAND addresses.
DISK_RESPONSE_SIZE = 256 * MIB - 0x40000
DISK_RESPONSE_REGIONS = (Region("recovery-disk.bin", 0, DISK_RESPONSE_SIZE),)


@dataclass(frozen=True)
class BackupProfile:
    disk_size: int
    regions: tuple[Region, ...]
    scope: str


PROFILES = {
    PROFILE: BackupProfile(
        DISK_SIZE, REGIONS,
        "Current NAND including current Boot0, plus OOB; excludes RAM and CPU registers",
    ),
    DISK_RESPONSE_PROFILE: BackupProfile(
        DISK_RESPONSE_SIZE, DISK_RESPONSE_REGIONS,
        "Recovery USB disk READ_10 response only - NAND 아님, 펌웨어 합성 응답 "
        "(not NAND; firmware-synthesized response); excludes Boot0, OOB and raw bad-block layout; "
        "not a NAND backup",
    ),
}


def get_profile(name: str, *, allow_legacy: bool = False) -> BackupProfile:
    if allow_legacy:
        name = PROFILE_ALIASES.get(name, name)
    if name not in PROFILES:
        raise ValueError("Unsupported backup profile")
    return PROFILES[name]


def canonical_profile(name: str) -> str:
    return PROFILE_ALIASES.get(name, name)


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_manifest(directory: Path, manifest: dict) -> None:
    temporary = directory / "manifest.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / "manifest.json")
    sync_directory(directory)


def hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_backup(directory: Path, regions: tuple[Region, ...] | None = None) -> dict:
    """Verify a completed dump. Accepts the legacy profile name through PROFILE_ALIASES."""
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Manifest must not be a symbolic link")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = manifest.get("profile")
    if manifest.get("status") != "complete" or canonical_profile(profile) not in PROFILES:
        raise ValueError("Backup is incomplete or uses an unsupported profile")
    if regions is None:
        regions = get_profile(profile, allow_legacy=True).regions
    records = manifest.get("files")
    if not isinstance(records, list) or len(records) != len(regions):
        raise ValueError("Manifest must describe every selected region exactly once")
    for region, record in zip(regions, records, strict=True):
        expected = (region.name, region.offset, region.size)
        actual = (record.get("name"), record.get("offset"), record.get("size"))
        if actual != expected:
            raise ValueError(f"Region mismatch: {region.name}")
        path = directory / region.name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != region.size:
            raise ValueError(f"Missing, linked or wrong-sized file: {region.name}")
        digest = hash_file(path)
        if digest != record.get("sha256") or digest != record.get("second_read_sha256"):
            raise ValueError(f"SHA-256 mismatch: {region.name}")
    return manifest


def acquire(
    read_at: Callable[[int, int], bytes],
    directory: Path,
    source: dict,
    regions: tuple[Region, ...] | None = None,
    report: Callable[[str], None] = print,
    *,
    profile: str = PROFILE,
) -> dict:
    configuration = get_profile(profile)
    if regions is None:
        regions = configuration.regions
    if shutil.disk_usage(directory.parent).free < sum(region.size for region in regions) + MIB:
        raise OSError("Insufficient free space for backup")
    directory.mkdir(mode=0o700)
    manifest = {
        "format_version": 1,
        "profile": profile,
        "status": "in_progress",
        "started_utc": utc_now(),
        "source": source,
        "scope": configuration.scope,
        "original_pre_patch_boot0_recoverable": False,
        "files": [],
    }
    save_manifest(directory, manifest)
    try:
        for region in regions:
            report(f"READ {region.name}: offset={region.offset} bytes={region.size}")
            partial = directory / (region.name + ".partial")
            first_hash = hashlib.sha256()
            with partial.open("xb") as stream:
                for position in range(0, region.size, CHUNK_SIZE):
                    size = min(CHUNK_SIZE, region.size - position)
                    payload = read_at(region.offset + position, size)
                    if len(payload) != size:
                        raise OSError(f"Short read: {region.name} at {position}")
                    stream.write(payload)
                    first_hash.update(payload)
                    if (position + size) % (16 * MIB) == 0 or position + size == region.size:
                        report(f"READ {region.name}: {position + size}/{region.size}")
                stream.flush()
                os.fsync(stream.fileno())
            report(f"VERIFY_SOURCE {region.name}")
            second_hash = hashlib.sha256()
            for position in range(0, region.size, CHUNK_SIZE):
                size = min(CHUNK_SIZE, region.size - position)
                payload = read_at(region.offset + position, size)
                if len(payload) != size:
                    raise OSError(f"Short verification read: {region.name} at {position}")
                second_hash.update(payload)
                if (position + size) % (16 * MIB) == 0 or position + size == region.size:
                    report(f"VERIFY_SOURCE {region.name}: {position + size}/{region.size}")
            digest = first_hash.hexdigest()
            if digest != second_hash.hexdigest() or digest != hash_file(partial):
                raise OSError(f"Source changed or saved file is corrupt: {region.name}")
            partial.rename(directory / region.name)
            manifest["files"].append({
                "name": region.name,
                "offset": region.offset,
                "size": region.size,
                "sha256": digest,
                "second_read_sha256": second_hash.hexdigest(),
            })
            save_manifest(directory, manifest)
        manifest["status"] = "complete"
        manifest["ended_utc"] = utc_now()
        save_manifest(directory, manifest)
        verify_backup(directory, regions)
        return manifest
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["ended_utc"] = utc_now()
        manifest["error"] = f"{type(error).__name__}: {error}"
        save_manifest(directory, manifest)
        raise


def inspect_device(
    path: Path,
    sys_devices: Path = Path("/sys/dev/block"),
    mountinfo: Path = Path("/proc/self/mountinfo"),
    swaps: Path = Path("/proc/swaps"),
    *,
    profile: str = PROFILE,
) -> dict:
    """Profile-specific preflight: the capacity must match exactly the selected profile."""
    configuration = get_profile(profile)
    return device.inspect_device(path, sys_devices, mountinfo, swaps, capacities={configuration.disk_size})


require_kernel_read_only = device.require_kernel_read_only


def check_read_request(offset: int, size: int, regions: tuple[Region, ...]) -> None:
    """Refuse anything but an aligned in-region READ that the firmware cannot treat as control."""
    if offset % 512 or size % 512 or not 0 < size <= CHUNK_SIZE:
        raise ValueError("Read must be sector-aligned and at most 16 KiB")
    if offset // 512 == SPECIAL_CONTROL_LBA or size // 512 in (UNLOCK_SECTOR_COUNT, LOCK_SECTOR_COUNT):
        raise ValueError("Vendor control LBA or transfer length is forbidden")
    if not any(region.offset <= offset and offset + size <= region.offset + region.size for region in regions):
        raise ValueError("Read outside selected backup regions is forbidden")


class ReadOnlySource:
    regions = REGIONS

    def __init__(self, path: Path, image: bool = False, *, profile: str = PROFILE):
        configuration = get_profile(profile)
        self.regions = configuration.regions
        if image:
            information = path.stat()
            if not stat.S_ISREG(information.st_mode) or information.st_size != configuration.disk_size:
                raise ValueError("Offline source must be a regular image matching the selected profile size")
            self.metadata = {"kind": "image-file", "path": str(path.resolve()), "capacity": information.st_size}
        else:
            self.metadata = inspect_device(path, profile=profile)
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        if not image:
            flags |= os.O_EXCL
        self.fd = os.open(path.resolve(), flags)
        self.image = image
        try:
            opened = os.fstat(self.fd)
            if image:
                if not stat.S_ISREG(opened.st_mode) or opened.st_size != configuration.disk_size:
                    raise ValueError("Offline source changed while opening")
            elif not stat.S_ISBLK(opened.st_mode) or opened.st_rdev != self.metadata["device_number"]:
                raise ValueError("Device changed while opening")
            elif inspect_device(path, profile=profile) != self.metadata:
                raise ValueError("Device state changed while opening")
            if not image:
                capacity = ctypes.c_uint64()
                fcntl.ioctl(self.fd, BLKGETSIZE64, capacity)
                if capacity.value != configuration.disk_size:
                    raise ValueError("Opened device capacity does not match selected profile")
        except BaseException:
            os.close(self.fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        os.close(self.fd)

    def read_at(self, offset: int, size: int) -> bytes:
        check_read_request(offset, size, self.regions)
        if self.image:
            return os.pread(self.fd, size, offset)
        cdb = build_scsi_rw10_cdb(0x28, offset // 512, size // 512)
        cdb_buffer = ctypes.create_string_buffer(cdb, len(cdb))
        payload = ctypes.create_string_buffer(size)
        sense = ctypes.create_string_buffer(32)
        header = SgIoHdr()
        header.interface_id = ord("S")
        header.dxfer_direction = SG_DXFER_FROM_DEV
        header.cmd_len = len(cdb)
        header.mx_sb_len = len(sense)
        header.dxfer_len = size
        header.dxferp = ctypes.cast(payload, ctypes.c_void_p)
        header.cmdp = ctypes.cast(cdb_buffer, ctypes.c_void_p)
        header.sbp = ctypes.cast(sense, ctypes.c_void_p)
        header.timeout = 10000
        result = fcntl.ioctl(self.fd, SG_IO, header)
        if not check_sg_result(result, header):
            raise OSError(f"SCSI read failed at {offset}: status={header.status}, host={header.host_status}, driver={header.driver_status}, resid={header.resid}")
        return payload.raw


# `dump --source` choices -> (profile, output prefix)
SOURCES = {
    "filedumper": (PROFILE, "g1-nand-oob"),
    "disk-response": (DISK_RESPONSE_PROFILE, "g1-disk-response"),
    "image": (PROFILE, "image-extract"),
}


def run_dump(args: argparse.Namespace) -> int:
    """Body of `cli.py dump`. Preflight only unless --execute."""
    profile, prefix = SOURCES[args.source]
    configuration = get_profile(profile)
    image = args.source == "image"
    path = args.image if image else args.device
    if path is None:
        raise ValueError("--image is required for --source image; --device otherwise")
    if image:
        if args.device is not None:
            raise ValueError("--source image takes --image only")
        if not path.is_file() or path.stat().st_size != DISK_SIZE:
            raise ValueError("Offline source must be a regular 528 MiB virtual-disk image")
    else:
        if args.image is not None:
            raise ValueError("--image is only valid with --source image")
        disk_response = args.source == "disk-response"
        confirmed = args.confirm_recovery_v16 if disk_response else args.confirm_filedumper
        if not args.confirm_g1 or not confirmed:
            raise ValueError("Require --confirm-g1 and the matching recovery/mapping confirmation; no patching is provided")
        metadata = inspect_device(path, profile=profile)
        if disk_response:
            require_kernel_read_only(metadata)
            print("KERNEL_READ_ONLY: disk and partitions report ro=1")
    print(f"SCOPE: {configuration.scope}")
    for region in configuration.regions:
        print(f"PLAN {region.name}: USB/image offset={region.offset}, size={region.size}")
    if not args.execute:
        print("PREFLIGHT ONLY: no source opened, no backup created. Add --execute to acquire.")
        return 0
    with ReadOnlySource(path, image=image, profile=profile) as source:
        if args.source == "disk-response":
            require_kernel_read_only(source.metadata)
        args.output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        directory = args.output_root / f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"
        metadata = dict(source.metadata)
        if not image:
            metadata.update(g1_confirmed=True, filedumper_mapping_confirmed=args.source == "filedumper")
            if args.source == "disk-response":
                metadata["recovery_screen_version_user_reported"] = "v16"
        print(f"OUTPUT: {directory}")
        acquire(source.read_at, directory, metadata, profile=profile)
        print(f"COMPLETE {profile}: {directory}")
    return 0
