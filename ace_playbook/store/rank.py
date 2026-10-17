"""Read-time ranking. Nothing here is ever written back to the log."""

from __future__ import annotations

import calendar
import math
import time

HALF_LIFE_DAYS = 90.0


def parse_ts(ts: str) -> float:
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def decay(updated: str, now: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """Exponential recency multiplier in (0, 1]; affects rank only."""
    age_days = max(0.0, (now - parse_ts(updated)) / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def wilson_lower(pos: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = pos / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def score(lesson: dict, now: float) -> float:
    """Rank score. Agent-tagged helpful/harmful is a weak feature (weight 0.3):
    published audits show it is not a safe retirement or ranking authority."""
    n = lesson["helpful"] + lesson["harmful"]
    weak = wilson_lower(lesson["helpful"], n) if n else 0.5
    cited = min(lesson["cited"], 5) / 5
    base = 0.6 + 0.3 * weak + 0.1 * cited
    if lesson["pinned"]:
        base += 1.0
    return decay(lesson["updated"], now) * base
