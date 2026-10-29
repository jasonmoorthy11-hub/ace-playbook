"""The nightly's optional health notes: one line from each of three operator scripts (`backup_check`, `brain_check`, `acewatch_check` in the config).

`collect(cfg)` runs `<script> --check` for each configured script and keeps the FIRST stdout line, stripped and cut to 80 characters
(for example `backup 7h`, `brain drift: 0 files (1 skipped, 16 entropy)` or `acewatch open 3  leads 1  oldest 2d  last scan 4h`).
The line is text from a local program the operator named in their own config file, not from the network.
Nothing else leaves this module: a script that is not an existing regular file, a non-zero exit, a timeout, an OS error, undecodable output, an empty or
unprintable first line, a line with a `/` (it could be a path) or a first word
with a `:` (it could pose as a digest category such as `silent:`) is reported as `backup check failed` / `brain check failed` / `acewatch check failed`,
never with stderr, the script path or an exception text. The child gets a fixed PATH and the home directory only. Stdlib only; not on the
hook path (tests/test_hook_conformance.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

TIMEOUT = 20                                     # seconds per script
LINE_MAX = 80
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
CHECKS = (("backup_check", "backup"), ("brain_check", "brain"), ("acewatch_check", "acewatch"))      # (config key, label)


def _line(script, label: str) -> str:
    failed = f"{label} check failed"
    if not Path(script).is_file():
        return failed
    try:
        done = subprocess.run([str(script), "--check"], capture_output=True, text=True, timeout=TIMEOUT,
                              env={"PATH": PATH, "HOME": str(Path.home())})
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):    # ValueError: bad output or path; RuntimeError: no home
        return failed
    first = done.stdout.split("\n", 1)[0].strip()
    if done.returncode != 0 or not first or "/" in first or not first.isprintable() or ":" in first.split(" ", 1)[0]:
        return failed
    return first[:LINE_MAX].rstrip()


def collect(cfg) -> list[str]:
    """The note lines of the configured checks, in the order backup, brain, acewatch; [] when none is configured."""
    return [_line(getattr(cfg, key), label) for key, label in CHECKS if getattr(cfg, key, None) is not None]
