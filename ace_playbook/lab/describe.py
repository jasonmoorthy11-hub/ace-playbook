"""Descriptive additions to a report group (docs/lab.md), no test of significance: the Newcombe hybrid-score interval for TOLD - NOT_TOLD
(Newcombe 1998, method 10: the two Wilson score bounds combined) and the per-task table (task x arm: trials with any violation / trials)."""

from __future__ import annotations

import math

from ..compile.metrics import Z


def _wilson(k: int, n: int) -> tuple[float, float]:
    """The unrounded Wilson score bounds for k of n (n >= 1)."""
    p, d = k / n, 1 + Z * Z / n
    c, h = (p + Z * Z / (2 * n)) / d, Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def newcombe(k1: int, n1: int, k0: int, n0: int) -> list[float] | None:
    """[lo, hi] (four decimals) of the 95% Newcombe hybrid-score interval for k1/n1 - k0/n0, None when an arm has no trials."""
    if n1 == 0 or n0 == 0:
        return None
    p1, p0, (l1, u1), (l0, u0) = k1 / n1, k0 / n0, _wilson(k1, n1), _wilson(k0, n0)
    diff = p1 - p0
    return [round(max(-1.0, diff - math.sqrt((p1 - l1) ** 2 + (u0 - p0) ** 2)), 4), round(min(1.0, diff + math.sqrt((u1 - p1) ** 2 + (p0 - l0) ** 2)), 4)]


def per_task(rows: list[dict]) -> dict:
    """{task: {arm: {"any": trials with a violation, "n": trials}}} over scored rows, both arms always present (a row without a task is `None`)."""
    out: dict = {}
    for r in rows:
        if r.get("arm") not in ("told", "not_told"):
            continue
        cell = out.setdefault(str(r.get("task")), {"told": {"any": 0, "n": 0}, "not_told": {"any": 0, "n": 0}})[r["arm"]]
        cell["n"] += 1
        cell["any"] += bool(r["any"])
    return dict(sorted(out.items()))


def table_lines(table: dict) -> list[str]:
    """The text form of `per_task`, one line per task."""
    return [f"  task {t:<16} told {c['told']['any']}/{c['told']['n']}  not_told {c['not_told']['any']}/{c['not_told']['n']}" for t, c in table.items()]
