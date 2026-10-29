"""The hook side of the sealed pre-arm-start conditions (1) and (7) and of the delivery rule (PREREGISTRATION.md, Arm assignment and
Pre-arm-start conditions). On the hook path: stdlib only, it must never import `arms` (analysis code, hashed in `report_sha256`).

`assign` is a copy of `arms.assign`; tests/test_shadow_arm.py pins the two to the same results on generated inputs and to the same body text.
`fields` gives the two keys every shadow row carries: `arm` (`unassigned` until both `shadow_seed` and `arm_rate` are set, which
`arms.primary` refuses, so no phase can run on such a row) and `agent_id_present` (whether the hook payload had an `agent_id` key, never its
value; False when the payload was not parsed). `audible` is the delivery rule: for a proposal whose detector has a started phase (phase.py) a
hit of an observe-arm session is not delivered (the row is written as ever, with the approved mode); a warn-arm or unassigned hit is, and an
unassigned one logs `arm:unassigned` (not an `error:` line) through the caller's throttle. `deliver` writes the warn echo (stderr) and the
decision JSON (stdout), each in its own try and flushed inside it, and logs a failure as `error:delivered:<Type>` through the caller's `log`
(condition (7)); after a failed write the fd is pointed at /dev/null so the interpreter's exit flush cannot fail unlogged. The seed is never logged.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import sys
from collections.abc import Callable

from ..canon import now_iso
from . import phase

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


def audible(cfg, hits: list, session_hash: str, once: Callable[[str], object], now: str | None = None) -> list:
    """The hits to deliver: those of a detector with no started phase, of a warn-arm session and of an unassigned one (logged `arm:unassigned`
    through `once`, an operator error during a phase). Anything that goes wrong delivers everything (the pre-phase behaviour)."""
    try:
        live = phase.active(cfg, now or now_iso()) if hits else frozenset()
        out = []
        for hit in hits:
            arm = fields(cfg, session_hash, hit[0]["id"], None)["arm"] if hit[0]["check"]["detector"] in live else WARN
            if arm == UNASSIGNED:
                once("arm:unassigned")
            if arm != OBSERVE:
                out.append(hit)
        return out
    except Exception:
        return hits


def _write(cfg, stream, text: str, log: Callable) -> None:
    try:
        stream.write(text)
        stream.flush()                                        # a pipe is block-buffered: the failure must happen here, inside the try
    except Exception as exc:
        log(cfg, f"error:delivered:{type(exc).__name__}")
        with contextlib.suppress(Exception):                  # the unflushed bytes stay buffered: the exit flush must go nowhere, silently
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, stream.fileno())
            finally:
                os.close(null)


def deliver(cfg, warns: list, out: dict | None, log: Callable) -> None:
    """The delivered path: the user-visible echo on stderr (the model never sees it), then the model-visible JSON on stdout. A failure of
    one write is logged (`error:delivered:<Type>`, type only) and never stops the other."""
    for stream, text in ((sys.stderr, "".join(m + "\n" for m in warns) if warns else None),
                         (sys.stdout, json.dumps(out, sort_keys=True) + "\n" if out is not None else None)):
        if text is not None:
            _write(cfg, stream, text, log)
