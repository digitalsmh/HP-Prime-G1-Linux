#!/usr/bin/env python3
"""HP Prime G1 integrated tool. Read-only dumps, offline checks, simulated Boot0 protocol.

Every real write path is blocked in code. See README.md ("blocked" list) and docs/.
"""

import argparse
import configparser
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hpprime import BLOCKED_REASON, dump, layout, synth, vendor  # noqa: E402
from hpprime.log import execution_log  # noqa: E402
from hpprime.transport import detect_hp_prime_usb  # noqa: E402



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="Inspect USB sysfs only; sends no device commands")

    check = commands.add_parser("check", help="Validate a selected block device via sysfs without opening it")
    check.add_argument("--device", required=True, type=Path)
    check.add_argument("--profile", required=True, choices=[*dump.PROFILES, dump.LEGACY_PREPATCH_PROFILE],
                       help="Required: capacity never selects a profile automatically")

    capture = commands.add_parser("dump", help="Read-only dump; preflight only unless --execute")
    capture.add_argument("--source", required=True, choices=dump.SOURCES,
                         help="filedumper: patched 528 MiB NAND/OOB; disk-response: stock recovery disk "
                              "(NOT NAND, firmware-synthesized); image: saved 528 MiB virtual disk")
    capture.add_argument("--device", type=Path)
    capture.add_argument("--image", type=Path)
    capture.add_argument("--confirm-g1", action="store_true", help="Hardware generation verified independently of USB ID")
    capture.add_argument("--confirm-filedumper", action="store_true", help="FileDumper mapping is already installed")
    capture.add_argument("--confirm-recovery-v16", action="store_true", help="Recovery screen v16 verified by the user")
    capture.add_argument("--output-root", type=Path, default=dump.DEFAULT_OUTPUT)
    capture.add_argument("--execute", action="store_true", help="Perform reads and create output; otherwise preflight only")

    verify = commands.add_parser("verify", help="Verify saved files against a completed manifest (accepts legacy profile name)")
    verify.add_argument("directory", type=Path)

    synth_check = commands.add_parser("synth-check", help="Offline: is a recovery-disk image only V16-synthesized sectors?")
    synth_check.add_argument("boot0", type=Path, help="reference Boot0 image, e.g. BXCBOOT0V16.bin")
    synth_check.add_argument("disk", type=Path, help="recovery-disk.bin (268,173,312 bytes)")

    plan = commands.add_parser("plan", help="Strict usbtool.ini validation; no device access; physical offsets UNVERIFIED")
    plan.add_argument("--ini", type=Path, required=True)
    plan.add_argument("--bad-blocks", type=layout.parse_bad_blocks, default=[],
                      help="Assumed bad physical blocks N,N,... for the layout model (default none)")

    update = commands.add_parser("boot0", help="Offline simulation only; the real update path is blocked")
    update.add_argument("--variant", choices=vendor.KNOWN_BOOT0, required=True)
    update.add_argument("--image", type=Path)
    update.add_argument("--device", type=Path, help="Ignored by the simulation; never opened")
    update.add_argument("--execute", action="store_true", help=f"Always refused: {BLOCKED_REASON}")
    update.add_argument("--expected-sha256")
    update.add_argument("--hardware-family", choices=("v1", "v2"))
    update.add_argument("--confirm-g1", action="store_true")
    update.add_argument("--confirm-fresh-recovery", action="store_true")
    update.add_argument("--acknowledge-boot0-risk", action="store_true")
    update.add_argument("--reboot", action="store_true", help="Simulate a reboot request after acknowledged commit")
    return parser


def run_status() -> None:
    devices = detect_hp_prime_usb()
    for found in devices:
        print(json.dumps({key: found[key] for key in ("usb_id", "sysfs_node", "mode", "block_devices")}, indent=2))
    if not devices:
        print("No HP Prime USB device detected")
    print("SYSFS ONLY: no USB commands; generation and compatibility are not auto-detected.")
    print("FileDumper dump requires G1 + confirmed FileDumper mapping + unmounted 528 MiB MSC device.")


def run_check(args) -> None:
    metadata = dump.inspect_device(args.device, profile=dump.canonical_profile(args.profile))
    print(json.dumps(metadata, indent=2))
    print("PREFLIGHT ONLY: device not opened; capacity/USB identity do not prove hardware generation or firmware mapping.")


def run_boot0(args) -> None:
    path = args.image or vendor.REFERENCE / f"BXCBOOT0{args.variant.upper()}.bin"
    image = vendor.load_image(path, boot0=True)
    vendor.validate_boot0(image, args.variant)
    print(json.dumps(image.summary(), indent=2))
    print("WARNING: Boot0 update can brick the device; it changes the pre-backup state")
    if args.hardware_family == "v2" and args.variant == "v13":
        raise ValueError("V13 is not compatible with the reference V2 hardware family")
    if args.execute:
        # Real Boot0 update is held: the USB-to-NAND address mapping is unverified.
        raise ValueError(BLOCKED_REASON)
    transport = vendor.SimulatedTransport()
    result = vendor.execute_packets(transport, vendor.boot0_packets(image, args.variant, args.reboot),
                                    notify=lambda message: None, delay=lambda seconds: None)
    if bytes(transport.uploaded) != image.data:
        raise RuntimeError("Simulation did not preserve the full image")
    print(json.dumps({"mode": "OFFLINE_SIMULATION", "scsi_calls": transport.calls, **result}, indent=2))
    print("NO DEVICE OPENED; no real update or backup performed")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in (None, "status"):
            run_status()
        elif args.command == "check":
            run_check(args)
        elif args.command == "dump":
            return dump.run_dump(args)
        elif args.command == "verify":
            manifest = dump.verify_backup(args.directory)
            legacy = " (legacy profile name accepted)" if manifest["profile"] in dump.PROFILE_ALIASES else ""
            print(f"VERIFIED: {args.directory}; profile={manifest['profile']}{legacy}; source kind={manifest['source']['kind']}")
        elif args.command == "synth-check":
            try:
                result = synth.check(args.boot0, args.disk)
            except ValueError as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 2
            return synth.report(result)
        elif args.command == "plan":
            print(json.dumps(layout.summarize(vendor.read_ini(args.ini), args.bad_blocks), indent=2))
            print("PLAN ONLY: NAND flashing is not implemented; physical NAND offsets are UNVERIFIED")
            print(f"LAYOUT: {layout.MAPPING_NOTE}")
        else:
            run_boot0(args)
        return 0
    except (OSError, ValueError, RuntimeError, configparser.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if args.command == "boot0":
            print("If an update started: do not retry or reset automatically; inspect device state first.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("CANCELLED/INTERRUPTED: outcome may be incomplete; incomplete dumps must not be used", file=sys.stderr)
        return 130


if __name__ == "__main__":
    with execution_log("HPPrime-Tool"):
        raise SystemExit(main())
