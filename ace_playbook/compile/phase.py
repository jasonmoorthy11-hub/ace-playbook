"""The arm-phase marker (PREREGISTRATION.md, Arm assignment and Pre-arm-start conditions (3), (4), (7)): the runtime's view of the sealed
`arm_start` amendment lines. On the hook path: stdlib only, no import of `arms` (analysis code, hashed in `report_sha256`).

`<data_dir>/arm_phase.json` is `{"<detector>": "<arm start, YYYY-MM-DDTHH:MM:SSZ>", ...}`, written by the arm-start verb only (a later wave). The
`arm_start` line in PREREGISTRATION.md stays the analysis's authority; the marker only makes the hook, the offline pass and the minting
freeze behave as the phase requires. `started` is {} for a missing, unreadable, oversize, non-regular or malformed marker (one bad entry
invalidates the file: every consumer then behaves as outside a phase) and never raises or logs. `active(cfg, now)` is the detectors whose
stamp is at or before `now` (a fixed-width UTC stamp, so the string order is the time order).
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

FILE, MAX_BYTES = "arm_phase.json", 1 << 16
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def started(cfg) -> dict[str, str]:
    """{detector: arm start stamp} of the marker, {} when there is no usable one."""
    try:
        with os.fdopen(os.open(Path(cfg.data_dir) / FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as fh:
            info = os.fstat(fh.fileno())
            raw = json.loads(fh.read(MAX_BYTES + 1)) if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES else None
    except Exception:                                         # a directory, a fifo, a symlink, bad JSON, a deep nesting: no phase
        return {}
    ok = isinstance(raw, dict) and all(isinstance(k, str) and k and isinstance(v, str) and STAMP.fullmatch(v) for k, v in raw.items())
    return dict(raw) if ok else {}


def active(cfg, now: str) -> frozenset[str]:
    """The detectors whose arm start is at or before `now` (a `YYYY-MM-DDTHH:MM:SSZ` stamp)."""
    return frozenset(d for d, stamp in started(cfg).items() if stamp <= now)
