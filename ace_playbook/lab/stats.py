"""Lab statistics, descriptive-first: per (model, lesson) the any-violation rate in each arm (Wilson 95%), their difference, a two-sided
Fisher exact p (stdlib hypergeometric, exact integer arithmetic), Holm across the lessons within a model, and mean violations per trial
as a secondary, descriptive number. A trial with a recorded error or a missing stream is excluded and counted, per arm, never scored.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ..compile.arms_stats import ALPHA, holm
from ..compile.metrics import wilson
from . import score, tasks

ARMS = ("told", "not_told")


def fisher_two_sided(k1: int, n1: int, k0: int, n0: int) -> float | None:
    """Two-sided Fisher exact p for k1 of n1 against k0 of n0: the mass of every table no more probable than the observed one."""
    if n1 == 0 or n0 == 0:
        return None
    total, successes = n1 + n0, k1 + k0
    weight = {x: math.comb(n1, x) * math.comb(n0, successes - x) for x in range(max(0, successes - n0), min(n1, successes) + 1)}
    return min(1.0, sum(w for w in weight.values() if w <= weight[k1]) / math.comb(total, successes))


def _show(p: float) -> float:
    """Six significant digits for display (a tiny p must not round to 0.0); never fed back into Holm."""
    return float(f"{p:.6g}")


def _arm(rows: list[dict]) -> dict:
    n, k = len(rows), sum(bool(r["any"]) for r in rows)
    return {"n": n, "k": k, "rate": round(k / n, 4) if n else None, "wilson95": wilson(k, n),
            "mean_violations": round(sum(r["violations"] for r in rows) / n, 4) if n else None}


def summarize(rows: list[dict], cued: bool = False) -> list[dict]:
    """rows: {model, lesson, arm, violations, any, cue?}. One group per (model, lesson), Holm-adjusted across the lessons of a model.
    The primary summary takes the uncued rows only; `cued=True` summarises the cued rows apart (descriptive: no Holm, never a rejection)."""
    rows = [r for r in rows if bool(r.get("cue")) == cued]
    keys = sorted({(r["model"], r["lesson"]) for r in rows})
    groups, raw = [], {}
    for model, lesson in keys:
        mine = [r for r in rows if (r["model"], r["lesson"]) == (model, lesson)]
        told, not_told = _arm([r for r in mine if r["arm"] == "told"]), _arm([r for r in mine if r["arm"] == "not_told"])
        diff = None if told["rate"] is None or not_told["rate"] is None else round(told["rate"] - not_told["rate"], 4)
        p = fisher_two_sided(told["k"], told["n"], not_told["k"], not_told["n"])
        raw[model, lesson] = p                                                  # Holm runs on the raw p; only the display is rounded
        groups.append({"model": model, "lesson": lesson, "told": told, "not_told": not_told, "diff": diff,
                       "fisher_p": None if p is None else _show(p), "p_holm": None, "reject": False})
    for model in sorted({g["model"] for g in groups} if not cued else ()):
        same = [g for g in groups if g["model"] == model]
        for g, (adj, rej) in zip(same, holm([raw[model, g["lesson"]] for g in same])):
            g["p_holm"], g["reject"] = _show(adj), rej and raw[model, g["lesson"]] is not None
    return groups


def load_rows(trials: Path, exclude_pilot: bool = False) -> tuple[list[dict], dict]:
    """(scored rows, excluded counts per 'model/lesson/arm'); the last record of a trial id wins."""
    last: dict[str, dict] = {}
    for line in (Path(trials).read_text(encoding="utf-8", errors="replace").splitlines() if Path(trials).exists() else []):
        try:
            rec = json.loads(line)
            last[rec["trial_id"]] = rec
        except (ValueError, KeyError, TypeError):
            continue
    rows, excluded = [], {}
    for rec in last.values():
        if exclude_pilot and rec.get("pilot"):
            continue
        try:
            if rec.get("error"):
                raise OSError("error")
            sc = score.score(rec["stream"], rec["lesson"])
        except (OSError, KeyError, ValueError):
            key = f"{rec.get('model')}/{rec.get('lesson')}/{rec.get('arm')}"
            excluded[key] = excluded.get(key, 0) + 1
            continue
        rows.append({"model": rec["model"], "lesson": rec["lesson"], "arm": rec["arm"], "cue": _cued(rec), **sc})
    return rows, excluded


def _cued(rec: dict) -> bool:
    """The record's own `cue`, else the task spec's; an unknown task is uncued."""
    if "cue" in rec:
        return bool(rec["cue"])
    try:
        return tasks.get(rec["task"]).cue
    except KeyError:
        return False


def render(groups: list[dict], excluded: dict, cued: list[dict] = ()) -> str:
    out = []
    for title, part in (("", groups), ("cued tasks (secondary, descriptive)", cued)):
        out += _render(part, title)
    out += [f"excluded (error or missing stream) {k}: {v}" for k, v in sorted(excluded.items())]
    return "\n".join(out) if out else "no scored trials"


def _render(groups: list[dict], title: str) -> list[str]:
    out = [title] if title and groups else []
    for g in groups:
        out.append(f"model {g['model']}  lesson {g['lesson']}")
        for arm in ARMS:
            a = g[arm]
            out.append(f"  {arm:<9} n={a['n']:<4} any={a['k']:<4} rate={a['rate']} wilson95={a['wilson95']} mean_violations={a['mean_violations']}")
        out.append(f"  diff(told - not_told)={g['diff']}  fisher p={g['fisher_p']}  holm p={g['p_holm']}  "
                   f"{'REJECT' if g['reject'] else 'no rejection'} at {ALPHA}")
    return out
