"""The hook side of the sealed pre-arm-start conditions (1) and (7) (PREREGISTRATION.md, Pre-arm-start conditions). On the hook path:
stdlib only, it must never import `arms` (analysis code, hashed in `report_sha256`).

`assign` is a copy of `arms.assign`; tests/test_shadow_arm.py pins the two to the same results on generated inputs and to the same body text.
`fields` gives the two keys every shadow row carries: `arm` (`unassigned` until both `shadow_seed` and `arm_rate` are set, which
`arms.primary` refuses, so no phase can run on such a row) and `agent_id_present` (whether the hook payload had an `agent_id` key, never its
value; False when the payload was not parsed). `deliver` writes the warn echo (stderr) and the decision JSON (stdout), each in its own try, and
logs a failure as `error:delivered:<Type>` through the caller's `log` (condition (7)). The seed is never logged, printed or written.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from typing import Callable

WARN, OBSERVE, UNASSIGNED = "warn", "observe", "unassigned"


def assign(session_hash: str, shadow_seed: str, arm_rate: float, proposal_id: str = "") -> str:
    """`warn` (the check runs in its approved mode) with probability `arm_rate`, else `observe`: HMAC-SHA256 of the session hash (and the
    proposal id) under the seed, its first 4 bytes as a fraction of 2**32. Deterministic; the seed is never logged."""
    mac = hmac.new(shadow_seed.encode(), f"{session_hash}|{proposal_id}".encode(), hashlib.sha256).digest()
    return WARN if int.from_bytes(mac[:4], "big") / 2**32 < arm_rate else OBSERVE


def fields(cfg, session_hash: str, proposal_id: str, payload: object) -> dict:
    """{"arm", "agent_id_present"} of one shadow row; `payload` is the parsed hook input (None when it was never parsed)."""
    seed, rate = cfg.shadow_seed, cfg.arm_rate
    try:
        arm = assign(session_hash, seed, rate, proposal_id) if seed is not None and rate is not None else UNASSIGNED
    except Exception:                                         # a row must never be lost to a bad config value; arms.primary refuses it
        arm = UNASSIGNED
    return {"arm": arm, "agent_id_present": isinstance(payload, dict) and "agent_id" in payload}


def deliver(cfg, warns: list, out: dict | None, log: Callable) -> None:
    """The delivered path: the user-visible echo on stderr (the model never sees it), then the model-visible JSON on stdout. A failure of
    one write is logged (`error:delivered:<Type>`, type only) and never stops the other."""
    for stream, text in ((sys.stderr, "".join(m + "\n" for m in warns) if warns else None),
                         (sys.stdout, json.dumps(out, sort_keys=True) + "\n" if out is not None else None)):
        if text is None:
            continue
        try:
            stream.write(text)
        except Exception as exc:
            log(cfg, f"error:delivered:{type(exc).__name__}")
