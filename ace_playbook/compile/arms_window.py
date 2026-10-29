"""The window of the arm analysis (see `arms.py`): when a detector's phase starts and ends, and which sessions are eligible. Stdlib and
sibling modules only, never on the hook path.

Arm start: a line `arm_start <detector> <UTC stamp>` under the top-level `## Amendments` heading of PREREGISTRATION.md, read nowhere else
(the first line of a detector wins), and refused when the stamp is earlier than the `sealed_at` of the frozen block. The horizon ends at the
earliest of 12 weeks after the arm start, the 400th eligible session and the first demotion row of the detector's proposals at or after the
arm start, whatever its cause (read from demotions.jsonl; the report prints the cause).

The Holm family is fixed at the arm start (`family`, intention to treat): a proposal of a started detector belongs when its newest approvals
row stamped at or before the arm start is not `revoked`, and every member must be approved at `warn` or `block` with no demotion row in
force at that start. A row stamped later (a revocation, a re-approval, a mode change) changes nothing.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import approvals, chains, demotions, metrics, modes, report, seal

WEEKS, CAP, LAG_DAYS = 12, 400, 7
FMT = "%Y-%m-%dT%H:%M:%SZ"
START = re.compile(r"^arm_start (\S+) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$", re.M)
SEALED = re.compile(r"^sealed_at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$", re.M)
AMENDMENTS = re.compile(r"^## Amendments$", re.M)


def _dt(ts: object) -> datetime | None:
    try:
        return datetime.strptime(ts, FMT).replace(tzinfo=timezone.utc) if isinstance(ts, str) else None
    except ValueError:
        return None


def _fmt(t: datetime) -> str:
    return t.strftime(FMT)


def _text(text: str | None) -> str:
    return text if text is not None else seal.PREREG.read_text(encoding="utf-8") if seal.PREREG.exists() else ""


def arm_starts(text: str | None = None) -> dict[str, str]:
    """{detector: arm start stamp} from the `arm_start` lines under the top-level Amendments heading (the first line of a detector wins)."""
    text = _text(text)
    head = AMENDMENTS.search(text)
    out: dict[str, str] = {}
    for det, ts in START.findall(text[head.end():] if head else ""):
        out.setdefault(det, ts)
    return out


def check_starts(text: str, starts: dict[str, str]) -> None:
    """ValueError unless the text is sealed (a `sealed_at` stamp) and no arm start is earlier than it."""
    sealed = SEALED.search(text)
    if starts and not sealed:
        raise ValueError("an arm_start line exists but the pre-registration has no sealed_at stamp")
    for det, ts in starts.items():
        if _dt(ts) < _dt(sealed.group(1)):
            raise ValueError(f"arm_start of {det} is earlier than sealed_at")


def _rows(cfg, mod, name: str) -> list[dict]:
    """The verified rows of a chain file (`approvals` or `demotions`); a symlink, a non-regular file or a broken chain is a ValueError."""
    try:
        rows, broken = mod.read_chain(cfg.data_dir, chains.key_of(cfg))
    except OSError:
        raise ValueError(f"{name}.jsonl is a symlink or not a regular file") from None
    if broken is not None:
        raise ValueError(f"the {name} chain is broken or unreadable; repair it before the analysis")
    return rows


def first_demotion(cfg, pids: list[str], arm_start: str) -> tuple[str, str] | tuple[None, None]:
    """(stamp, cause) of the first demotion row of any of `pids` at or after the arm start, whatever its cause, else (None, None). A broken
    or unsafe chain is a ValueError."""
    rows = _rows(cfg, demotions, "demotions")
    return min(((r["ts"], r["cause"]) for r in rows if r["proposal_id"] in pids and r["ts"] >= arm_start), default=(None, None))


def family(cfg, starts: dict[str, str]) -> dict[str, list[str]]:
    """{detector: sorted proposal ids} of every started detector, as fixed at its arm start. ValueError for a member whose proposal file
    cannot be read, or that is not approved at `warn` or `block` with no demotion row stamped before the start still in force."""
    arows, drows = _rows(cfg, approvals, "approvals"), _rows(cfg, demotions, "demotions")
    out: dict[str, list[str]] = {}
    for pid in sorted({r["proposal_id"] for r in arows}):
        det = report._meta(cfg, pid)[0]
        for started, t0 in starts.items():
            row = modes.anchor(arows, pid, t0)
            if det not in (started, "-") or row is None or row["mode"] == "revoked":
                continue
            if det == "-":
                raise ValueError(f"approved proposal {pid} has no readable proposal file (the family cannot be built)")
            n = len(modes.in_force([r for r in drows if r["ts"] < t0], pid, arows, t0))
            if row["mode"] not in ("warn", "block") or n:
                raise ValueError(f"arm_start {det}: proposal {pid} is approved at {row['mode']} / has {n} demotion(s) in force at the arm "
                                 "start (warn or block with none in force required)")
            out.setdefault(det, []).append(pid)
    return out


def horizon_end(arm_start: str, starts: list[str], demoted: str | None = None) -> str:
    """The end of the half-open window: the earliest of 12 weeks after the arm start, one second after the start of the 400th eligible
    session and the stamp of the first demotion. `starts` are the first-event stamps of the interactive sessions the detector consumes."""
    t0 = _dt(arm_start)
    end = t0 + timedelta(weeks=WEEKS)
    inside = sorted(t for t in map(_dt, starts) if t is not None and t0 <= t < end)
    end = inside[CAP - 1] + timedelta(seconds=1) if len(inside) >= CAP else end
    return _fmt(min(end, _dt(demoted)) if demoted else end)


def _interactive(row: dict, detector: str) -> bool:
    p = row.get("prompts")
    return detector in row.get("eligible", []) and isinstance(p, int) and not isinstance(p, bool) and p > 0


def eligible_sessions(ev_rows: list[dict], arm_start: str, end: str | None, detector: str,
                      demoted: str | None = None) -> tuple[dict[str, dict], str, int]:
    """(eligible sessions {hash: newest `_session` row}, the horizon end, sessions missing a start). Eligible: an interactive session
    (`prompts` > 0) that the detector consumes and whose first event falls in [arm start, horizon end); `end` None derives the horizon."""
    mine = {s: r for s, r in metrics.latest_sessions(ev_rows).items() if _interactive(r, detector)}
    timed = {s: r for s, r in mine.items() if _dt(r.get("start"))}
    end = end or horizon_end(arm_start, [r["start"] for r in timed.values()], demoted)
    t0, t1 = _dt(arm_start), _dt(end)
    return {s: r for s, r in timed.items() if t0 <= _dt(r["start"]) < t1}, end, len(mine) - len(timed)
