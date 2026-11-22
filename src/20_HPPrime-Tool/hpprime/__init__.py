"""HP Prime G1 integrated tool (read-only dump, offline checks, simulated vendor protocol)."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
TOOL_ROOT = PACKAGE_ROOT.parent
WORKSPACE = TOOL_ROOT.parents[1]
LOG_DIRECTORY = WORKSPACE / "30_TEST/log"
MIB = 1024 * 1024
SECTOR_SIZE = 512

# Reason string for every real write path that is blocked in this tool.
BLOCKED_REASON = "blocked: NAND address mapping unverified; see docs"
