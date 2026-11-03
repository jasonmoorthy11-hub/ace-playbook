"""What `holdout_power` and `holdout_report` share (section "Holdout analysis" of docs/design/compiler.md): the three lessons with their
detectors, the UTC stamp parser, the fire counts of the offline detector rows and the per-session assignment read from `injections.jsonl`.
Stdlib and sibling modules only; offline, never on the hook path.

Stamps: whole-second (`2026-11-01T00:00:00Z`) or fractional-second (`2026-11-01T00:00:00.123Z`, as the transcripts' `_session.start` carries)
UTC `Z` stamps; anything else parses to None. `arms_window._dt` is whole-second only and is not used here.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import labels
from . import state as state_mod

LESSONS = (("L-cbfb7a57", "preclaim_audit_block"), ("L-f8a355f9", "edit_unread_turn"), ("L-93544dae", "exitplan_question_block"))
ARM_RATE = 0.5
_STAMP = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z")


def parse_ts(value: object) -> datetime | None:
    """A naive UTC datetime from a whole-second or fractional-second `Z` stamp (fraction kept to the microsecond); else None."""
    m = _STAMP.fullmatch(value) if isinstance(value, str) else None
    try:
        return datetime.strptime(m[1], "%Y-%m-%dT%H:%M:%S").replace(microsecond=int(((m[2] or "") + "000000")[:6])) if m else None
    except ValueError:
        return None


def nat(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def exempt_lessons(cfg, end: str) -> list[str] | None:
    """The sorted ids of `LESSONS` that some record of the delta LOG sets `holdout_exempt` true with a `ts` before `end` (an unparseable `ts`
    counts as before), even if a later record unexempts it: the injector always shows an exempt lesson, so its row would look coin-shown. A
    record from `end` on does not count; the SQLite view is not read (it keeps no history). No log: []. A log that cannot be read, or a record
    that is not an object or has a `lesson_id` that is not a string: None."""
    from ..store import DeltaLog, SchemaError                 # lazy: the store pulls in sqlite3, which compile/ never imports itself
    ids, hit, t1 = {lid for lid, _ in LESSONS}, set(), parse_ts(end)
    try:
        records = DeltaLog(cfg.deltas, tuple(cfg.deny_terms)).read()
    except (SchemaError, OSError):
        return None
    for r in records:
        if not isinstance(r, dict) or ("lesson_id" in r and not isinstance(r["lesson_id"], str)):
            return None
        f = r.get("fields") if isinstance(r.get("fields"), dict) else {}
        t = parse_ts(r.get("ts"))
        if r.get("lesson_id") in ids and f.get("holdout_exempt") is True and (t is None or t < t1):
            hit.add(r["lesson_id"])
    return sorted(hit)


def check_exempt(cfg, end: str, descriptive: bool) -> list[str] | str:
    """The registered lessons that were `holdout_exempt` before `end`. The test refuses on any, and on a log it cannot read; the descriptive
    mode returns them (or "unreadable")."""
    ids = exempt_lessons(cfg, end)
    if descriptive:
        return "unreadable" if ids is None else ids
    if ids is None:
        raise labels.Refusal("the delta log cannot be read: the test needs to know no registered lesson was holdout_exempt", 2)
    if ids:
        raise labels.Refusal(f"lesson {', '.join(ids)} was holdout_exempt (always shown) before holdout_end: the registered lessons must not be "
                             "exempt during the window", 2)
    return ids


def fire_counts(ev_rows: list[dict]) -> dict[tuple[str, str], int]:
    """{(session, detector): the sum of `count`} over the offline-tier fire rows of the lessons' detectors, deduplicated by `id`."""
    dets, seen, out = {d for _, d in LESSONS}, set(), {}
    for r in ev_rows:
        if r.get("tier") != "offline" or r.get("detector") not in dets or (r.get("id") is not None and r["id"] in seen):
            continue
        seen.add(r.get("id"))
        out[(r.get("session"), r["detector"])] = out.get((r.get("session"), r["detector"]), 0) + nat(r.get("count"))
    return out


def _ids(row: dict, key: str) -> set:
    return set(row[key]) if isinstance(row.get(key), list) else set()


def _rate(row: dict) -> float:
    r = row.get("holdout_rate")
    return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else 0.0


def _arm(row: dict, lid: str) -> str:
    hidden, shown = lid in _ids(row, "holdout_ids"), lid in _ids(row, "shown_ids")
    return "both" if hidden and shown else "hidden" if hidden else "shown" if shown else "shown" if lid in _ids(row, "dropped_cap") else "none"


def assignments(inj_rows: list[dict]) -> tuple[dict[str, dict], int]:
    """({session hash: the facts of one session's injection rows}, the number of rows with no usable session id). A row with a rate above 0
    is live; `interactive` is a session whose every row has `entrypoint` "cli" (any other value, null or no field: not); `mixed` is a
    session with live and rate-0 rows, `seed_fp_mismatch` live rows with different `seed_fp`; a lesson in neither
    list but in `dropped_cap` is SHOWN (the coin said shown, the cap dropped the line) and goes to `dropped`; `arms` is {lesson: shown |
    hidden | none} (read only when `conflict` is false)."""
    by: dict[str, list[dict]] = {}
    null_ids = 0
    for r in inj_rows:
        if isinstance(r.get("session_id"), str) and r["session_id"]:
            by.setdefault(state_mod.session_hash(r["session_id"]), []).append(r)
        else:
            null_ids += 1
    out = {}
    for h, rows in by.items():
        sigs = {tuple(_arm(r, lid) for lid, _ in LESSONS) for r in rows}
        live = [r for r in rows if _rate(r) > 0]
        firsts = [t for t in (parse_ts(r.get("ts")) for r in rows) if t]
        out[h] = {"first": min(firsts) if firsts else None, "off": not live, "mixed": bool(live) and len(live) < len(rows),
                  "interactive": all(r.get("entrypoint") == "cli" for r in rows),
                  "refused": any(r.get("refused") for r in rows), "odd_rate": any(_rate(r) != ARM_RATE for r in live),
                  "seed_fp_mismatch": len({str(r.get("seed_fp")) for r in live}) > 1, "conflict": len(sigs) > 1 or "both" in next(iter(sigs)),
                  "dropped": {lid for lid, _ in LESSONS for r in rows if lid in _ids(r, "dropped_cap") and _arm(r, lid) == "shown"
                              and lid not in _ids(r, "shown_ids")},
                  "arms": dict(zip((lid for lid, _ in LESSONS), sorted(sigs)[0]))}
    return out, null_ids
