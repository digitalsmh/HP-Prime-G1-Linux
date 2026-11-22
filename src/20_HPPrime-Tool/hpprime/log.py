"""Console + log-file tee and the execution log context (log folder: 30_TEST/log)."""

import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import LOG_DIRECTORY


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Tee:
    def __init__(self, console, logfile):
        self.console = console
        self.logfile = logfile

    def write(self, text):
        self.logfile.write(text)
        self.logfile.flush()
        return self.console.write(text)

    def flush(self):
        self.logfile.flush()
        self.console.flush()


@contextlib.contextmanager
def execution_log(prefix: str = "HPPrime-Tool", directory: Path | None = None, argv: list[str] | None = None):
    """Mirror stdout/stderr into `<directory>/<UTC>_<prefix>_XXXX.log` and record the exit code."""
    directory = LOG_DIRECTORY if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S_{prefix}_")
    descriptor, filename = tempfile.mkstemp(prefix=stamp, suffix=".log", dir=directory)
    with os.fdopen(descriptor, "w", encoding="utf-8") as logfile:
        with contextlib.redirect_stdout(Tee(sys.stdout, logfile)), contextlib.redirect_stderr(Tee(sys.stderr, logfile)):
            print(f"LOG_FILE: {filename}\nSTART_UTC: {utc_now()}")
            print("COMMAND:", json.dumps(sys.argv if argv is None else argv, ensure_ascii=True))
            code = 0
            try:
                yield filename
            except BaseException as error:
                code = error.code if isinstance(error, SystemExit) else 130 if isinstance(error, KeyboardInterrupt) else 1
                raise
            finally:
                print(f"END_UTC: {utc_now()}\nEXIT_CODE: {code}")
                logfile.flush()
                os.fsync(logfile.fileno())
