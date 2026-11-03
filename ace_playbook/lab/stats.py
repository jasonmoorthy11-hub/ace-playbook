"""Lab statistics, descriptive-first: per (model, lesson) the any-violation rate in each arm (Wilson 95%), their difference, a two-sided
Fisher exact p (stdlib hypergeometric, exact integer arithmetic), Holm across the lessons within a model, and mean violations per trial
as a secondary, descriptive number. A trial with a recorded error or a missing stream is excluded and counted, per arm, never scored.
Two kinds of row are kept apart as descriptive secondaries (no Holm, never a rejection): cued edit_unread tasks, and single-claim preclaim
tasks (the lab hook needs two claim phrases, so the PRIMARY preclaim row is the multi_claim tasks only). The report also counts the exitplan
trials whose driver count and rule recomputation differ (`mismatches`), and counts the tool inputs outside the run dir (`outside`: every saved
stream, pilot and errored trials too; an unreadable or empty stream, a stream with a bad line, or a trials.jsonl line is counted apart).
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


def summarize(rows: list[dict], cued: bool = False, single: bool = False) -> list[dict]:
    """rows: {model, lesson, arm, violations, any, cue?, single?}. One group per (model, lesson), Holm-adjusted across the lessons of a model.
    The primary summary takes the uncued, multi-claim rows only; `cued=True` or `single=True` summarises those rows apart (descriptive)."""
    want = "cued" if cued else "single" if single else "primary"
    rows = [r for r in rows if ("cued" if r.get("cue") else "single" if r.get("single") else "primary") == want]
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
    for model in sorted({g["model"] for g in groups} if want == "primary" else ()):
        same = [g for g in groups if g["model"] == model]
        for g, (adj, rej) in zip(same, holm([raw[model, g["lesson"]] for g in same])):
            g["p_holm"], g["reject"] = _show(adj), rej and raw[model, g["lesson"]] is not None
    return groups


def _scan(trials: Path, exclude_pilot: bool = False) -> tuple[list[dict], int, bool]:
    """(the last record of each trial id in file order, pilot records dropped when `exclude_pilot`; the count of unparseable lines; whether the
    file has any non-blank line)."""
    last: dict[str, dict] = {}
    bad, lines = 0, 0
    for line in (Path(trials).read_text(encoding="utf-8", errors="replace").splitlines() if Path(trials).exists() else []):
        if not line.strip():
            continue
        lines += 1
        try:
            rec = json.loads(line)
            last[rec["trial_id"]] = rec
        except (ValueError, KeyError, TypeError):
            bad += 1
    return [rec for rec in last.values() if not (exclude_pilot and rec.get("pilot"))], bad, bool(lines)


def _records(trials: Path, exclude_pilot: bool = False) -> list[dict]:
    return _scan(trials, exclude_pilot)[0]


def load_rows(trials: Path, exclude_pilot: bool = False) -> tuple[list[dict], dict]:
    """(scored rows, excluded counts per 'model/lesson/arm'); the last record of a trial id wins."""
    rows, excluded = [], {}
    for rec in _records(trials, exclude_pilot):
        stream = rec.get("stream")
        try:
            if rec.get("error") or not isinstance(stream, str) or not stream or not Path(stream).is_file():
                raise OSError("no stream to score")
            sc = score.score(stream, rec["lesson"])
        except (OSError, KeyError, ValueError):
            key = f"{rec.get('model')}/{rec.get('lesson')}/{rec.get('arm')}"
            excluded[key] = excluded.get(key, 0) + 1
            continue
        rows.append({"model": rec["model"], "lesson": rec["lesson"], "arm": rec["arm"], "cue": _cued(rec), "single": _single(rec), **sc})
    return rows, excluded


def _cued(rec: dict) -> bool:
    """The record's own `cue`, else the task spec's; an unknown task is uncued."""
    if "cue" in rec:
        return bool(rec["cue"])
    try:
        return tasks.get(rec["task"]).cue
    except KeyError:
        return False


def _single(rec: dict) -> bool:
    """A preclaim record from a single-claim task: the record's own `multi_claim`, else the task spec's; an unknown task is not single."""
    if rec.get("lesson") != "preclaim":
        return False
    if "multi_claim" in rec:
        return not rec["multi_claim"]
    try:
        return not tasks.get(rec["task"]).multi_claim
    except KeyError:
        return False


def mismatches(rows: list[dict]) -> int | None:
    """The exitplan trials whose rule recomputation differs from the driver's count; None when no exitplan trial was scored."""
    mine = [r for r in rows if r["lesson"] == "exitplan"]
    return sum(r.get("rule_violations") != r["violations"] for r in mine) if mine else None


def outside(trials: Path) -> dict | None:
    """{total, unreadable, bad_streams, trials}: the tool inputs (file_path, path, notebook_path, a Glob pattern or Grep glob) outside the run dir,
    summed over EVERY saved stream, pilot and errored trials included; `trials` lists the sorted ids with a count above 0. FAIL CLOSED:
    `unreadable` counts each record whose stream is missing, unreadable, wholly unparseable or not a usable path (a `build failed` record ran no
    agent, so it has no stream to read), each stream that is empty or has no init event (unless its record's error starts `spawn failed`: run.py
    opens the stream before the spawn), and each unparseable line of trials.jsonl; `bad_streams` counts the streams with an unparseable line.
    None when trials.jsonl has no line."""
    recs, bad, any_line = _scan(trials)
    if not any_line:
        return None
    total, unreadable, bad_streams, hit = 0, bad, 0, []
    for rec in recs:
        stream, err = rec.get("stream"), str(rec.get("error") or "")
        try:
            if not isinstance(stream, str) or not stream:
                raise TypeError("no stream")
            n, bad_lines, has_init = score.outside_of(stream)
        except (OSError, TypeError, ValueError):
            unreadable += not err.startswith("build failed")
            continue
        unreadable += not (has_init or err.startswith("spawn failed"))
        bad_streams += bad_lines > 0
        total += n
        hit += [str(rec["trial_id"])] if n else []
    return {"total": total, "unreadable": unreadable, "bad_streams": bad_streams, "trials": sorted(hit)}


def render(groups: list[dict], excluded: dict, cued: list[dict] = (), single: list[dict] = (), mismatched: int | None = None,
           outside_n: int | None = None, unreadable: int = 0, bad_streams: int = 0) -> str:
    out = []
    for title, part in (("", groups), ("cued tasks (secondary, descriptive)", cued),
                        ("single-claim preclaim tasks (secondary, descriptive)", single)):
        out += _render(part, title)
    out += [] if mismatched is None else [f"exitplan rule/driver mismatches: {mismatched}"]
    bad = f", streams with bad lines: {bad_streams}" if bad_streams else ""
    out += [] if outside_n is None else [f"outside-run-dir tool inputs: {outside_n} (unreadable streams: {unreadable}{bad})"]
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
