"""The report's `checks` (docs/lab.md): five Counters that show what was actually run, counts only, never an outcome.
`lesson_sha16`: `lesson/sha16` over the last record of every trial id. `binary`: `<init claude_code_version>/<init model>` over the stream of
EVERY attempt (a stream without an init event, or an unreadable one, is `None/None`). `model_usage`: the keys of each result event's
`modelUsage`, one count per result event, over every attempt's stream. `attempt_errors`: `<arm>/<error up to the first ':'>` over every record
(every attempt); `first_attempt_errors`: the same over the first record of each trial id. `report --exclude-pilot` drops the pilot records
from all five."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import score, stats

KEYS = ("lesson_sha16", "binary", "model_usage", "attempt_errors", "first_attempt_errors")


def _stream(stream) -> tuple[str, list[str]]:
    """(the binary key, the modelUsage keys of every result event) of one saved stream; ("None/None", []) when it cannot be read."""
    try:
        events, _ = score._events(stream)
    except (OSError, TypeError, ValueError):
        return "None/None", []
    init = next((ev for ev in events if ev.get("type") == "system" and ev.get("subtype") == "init"), {})
    usage = [str(k) for ev in events if ev.get("type") == "result" and isinstance(ev.get("modelUsage"), dict) for k in ev["modelUsage"]]
    return f"{init.get('claude_code_version')}/{init.get('model')}", usage


def _error_keys(rec: dict) -> list[str]:
    """[`<arm>/<error up to the first ':'>`] of an errored record, else []."""
    return [f"{rec.get('arm')}/{str(rec['error']).split(':')[0]}"] if rec.get("error") else []


def checks(trials: Path, exclude_pilot: bool = False) -> dict:
    """{key: {...}} for the five KEYS: the Counters as plain dicts (empty when there is no record)."""
    sha, binary, usage, errors, first = (Counter() for _ in KEYS)
    for rec in stats._records(trials, exclude_pilot):
        sha[f"{rec.get('lesson')}/{rec.get('lesson_sha16')}"] += 1
    every = stats._records(trials, exclude_pilot, every=True)
    for rec in stats._distinct(every):
        key, keys = _stream(rec.get("stream"))
        binary[key] += 1
        usage.update(keys)
    seen = set()
    for rec in every:
        errors.update(_error_keys(rec))
        if rec["trial_id"] not in seen:
            first.update(_error_keys(rec))
            seen.add(rec["trial_id"])
    return {k: dict(sorted(c.items())) for k, c in zip(KEYS, (sha, binary, usage, errors, first))}


def line(found: dict) -> str:
    """The one text line of the report."""
    return "checks: " + "  ".join(f"{k}={found[k]}" for k in KEYS)
