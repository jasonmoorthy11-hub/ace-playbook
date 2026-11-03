"""Orphan streams (docs/lab.md): a `<data>/streams/*.jsonl` that no record of trials.jsonl names, left by an attempt that never wrote its record
(Ctrl-C, an exception, SIGKILL, power loss; the next attempt of that trial takes the next free n). The outside count is a safety STOP, so such a
stream is read like any other and counted apart in `orphan_streams`; matching is by file name, so a data dir that was moved still matches."""

from __future__ import annotations

import re
from pathlib import Path

from . import score


def _trial_id(name: str) -> str:
    """The trial id of a stream file name: `<tid>.a<n>.jsonl` (or the old layout `<tid>.jsonl`)."""
    return re.sub(r"(\.a\d+)?\.jsonl$", "", name)


def scan(trials: Path, recs: list[dict]) -> dict:
    """{total, unreadable, bad_streams, trials, orphan_streams} over the streams next to `trials` that no record in `recs` names (any attempt)."""
    named = {Path(r["stream"]).name for r in recs if isinstance(r.get("stream"), str) and r["stream"]}
    found = {"total": 0, "unreadable": 0, "bad_streams": 0, "trials": [], "orphan_streams": 0}
    try:
        files = sorted(p for p in (Path(trials).parent / "streams").glob("*.jsonl") if p.name not in named)
    except OSError:
        found["unreadable"] += 1                                                       # a streams dir that cannot be listed: fail closed
        return found
    for path in files:
        found["orphan_streams"] += 1
        try:
            n, bad_lines, has_init, _ = score.outside_scan(path)
        except (OSError, ValueError):
            found["unreadable"] += 1
            continue
        found["unreadable"] += not has_init
        found["bad_streams"] += bad_lines > 0
        found["total"] += n
        found["trials"] += [_trial_id(path.name)] if n else []
    return found


def merge(trials: Path, recs: list[dict], found: dict, any_line: bool) -> dict | None:
    """`found` (the named streams' counts) plus the orphans'; None when trials.jsonl has no line and there is no orphan stream either."""
    more = scan(trials, recs)
    if not any_line and not (more["orphan_streams"] or more["unreadable"]):
        return None
    return {"total": found["total"] + more["total"], "unreadable": found["unreadable"] + more["unreadable"],
            "bad_streams": found["bad_streams"] + more["bad_streams"], "trials": sorted(set(found["trials"]) | set(more["trials"])),
            "orphan_streams": more["orphan_streams"]}
