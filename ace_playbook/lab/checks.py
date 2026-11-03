"""The report's `checks` (docs/lab.md): three Counters that show what was actually run, counts only, never an outcome.
`lesson_sha16`: `lesson/sha16` over the last record of every trial id. `binary`: `<init claude_code_version>/<init model>` over every
stream (a stream without an init event, or an unreadable one, is `None/None`). `model_usage`: the keys of each result event's `modelUsage`,
one count per result event. `report --exclude-pilot` drops the pilot records from all three."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import score, stats


def _stream(stream) -> tuple[str, list[str]]:
    """(the binary key, the modelUsage keys of every result event) of one saved stream; ("None/None", []) when it cannot be read."""
    try:
        events, _ = score._events(stream)
    except (OSError, TypeError, ValueError):
        return "None/None", []
    init = next((ev for ev in events if ev.get("type") == "system" and ev.get("subtype") == "init"), {})
    usage = [str(k) for ev in events if ev.get("type") == "result" and isinstance(ev.get("modelUsage"), dict) for k in ev["modelUsage"]]
    return f"{init.get('claude_code_version')}/{init.get('model')}", usage


def checks(trials: Path, exclude_pilot: bool = False) -> dict:
    """{"lesson_sha16": {...}, "binary": {...}, "model_usage": {...}}: the three Counters as plain dicts (empty when there is no record)."""
    sha, binary, usage = Counter(), Counter(), Counter()
    for rec in stats._records(trials, exclude_pilot):
        sha[f"{rec.get('lesson')}/{rec.get('lesson_sha16')}"] += 1
        key, keys = _stream(rec.get("stream"))
        binary[key] += 1
        usage.update(keys)
    return {"lesson_sha16": dict(sorted(sha.items())), "binary": dict(sorted(binary.items())), "model_usage": dict(sorted(usage.items()))}


def line(found: dict) -> str:
    """The one text line of the report."""
    return "checks: " + "  ".join(f"{k}={found[k]}" for k in ("lesson_sha16", "binary", "model_usage"))
