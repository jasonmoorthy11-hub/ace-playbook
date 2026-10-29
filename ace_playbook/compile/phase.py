"""The arm-phase marker (PREREGISTRATION.md, Arm assignment and Pre-arm-start conditions (3), (4), (7)): the runtime's view of the sealed
`arm_start` amendment lines. On the hook path: stdlib only, no import of `arms` (analysis code, hashed in `report_sha256`).

`<data_dir>/arm_phase.json` is `{"v": 1|2, "started": {"<detector>": "<arm start, YYYY-MM-DDTHH:MM:SSZ>", ...}, "ts": "<signing time>",
"hash": "<hex>"}`, sealed like an approvals row (`chains.seal`: the keyed hash with a `v: 2` on a keyed install, the plain record hash with
a `v: 1` on an unkeyed one) and written by `compile.arm_start` only (`write`: merge, re-sign, atomic, 0600). An agent with Bash can write any
plain file, so an unsigned marker would be a control file that silences a block; `load` therefore verifies it with `chains.hash_ok` and a
marker that is present but unsigned, malformed, signed with another key or changed after signing is no phase. (The kill file `DISABLED`
stays the documented unauthenticated off switch; the marker is not one.) The `arm_start` line in PREREGISTRATION.md stays the analysis's
authority; the marker only makes the hook, the offline pass and the minting freeze behave as the phase requires. `load` returns
`({detector: stamp}, unverified)`: `({}, False)` for no file, `({}, True)` for a file that is not a usable signed marker (a directory, a
symlink, a fifo, oversize, bad JSON, a deep nesting, a bad entry), and never raises or logs; the hook logs `arm:phase:unverified` for the
second. `active(cfg, now)` is the detectors whose stamp is at or before `now` (a fixed-width UTC stamp, so the string order is the time order).
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from . import chains

FILE, MAX_BYTES = "arm_phase.json", 1 << 16
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def _valid(raw: object) -> bool:
    """The shape of a marker: exactly the four keys, a stamp for every detector and for `ts`, a string hash."""
    return (isinstance(raw, dict) and set(raw) == {"v", "started", "ts", "hash"} and isinstance(raw["started"], dict)
            and all(isinstance(k, str) and k and isinstance(v, str) and STAMP.fullmatch(v) for k, v in raw["started"].items())
            and isinstance(raw["ts"], str) and STAMP.fullmatch(raw["ts"]) is not None and isinstance(raw["hash"], str))


def load(cfg) -> tuple[dict[str, str], bool]:
    """({detector: arm start stamp}, unverified): the verified marker, or ({}, False) when there is no file and ({}, True) when there is one
    that is not a marker signed under this install's rule (see the module docstring)."""
    try:
        with os.fdopen(os.open(Path(cfg.data_dir) / FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as fh:
            info = os.fstat(fh.fileno())
            raw = json.loads(fh.read(MAX_BYTES + 1)) if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES else None
        return (dict(raw["started"]), False) if _valid(raw) and chains.hash_ok(raw, chains.key_of(cfg)) else ({}, True)
    except FileNotFoundError:
        return {}, False
    except Exception:                                         # a directory, a fifo, a symlink, bad JSON, a deep nesting: present, unusable
        return {}, True


def started(cfg) -> dict[str, str]:
    """{detector: arm start stamp} of the verified marker, {} when there is no usable one."""
    return load(cfg)[0]


def active(cfg, now: str) -> frozenset[str]:
    """The detectors whose arm start is at or before `now` (a `YYYY-MM-DDTHH:MM:SSZ` stamp)."""
    return frozenset(d for d, stamp in started(cfg).items() if stamp <= now)


def write(cfg, add: dict[str, str], ts: str) -> dict:
    """Merge `add` into the verified marker, seal it with the chain keyring and replace the file atomically (0600); the marker written.
    ValueError on a keyed install without a usable key. A marker that was present but unverifiable is replaced (it was no phase)."""
    import tempfile                                           # lazy: the hook only reads
    ring = chains.key_of(cfg)
    if ring is not None and ring.key is None:
        raise ValueError(chains.MISSING)
    marker = chains.seal({"started": dict(sorted({**started(cfg), **add}.items())), "ts": ts}, ring)
    path = Path(cfg.data_dir) / FILE
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{FILE}.")                      # 0600, O_EXCL
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(marker, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return marker
