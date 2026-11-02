"""The pre-registered analysis of the per-session lesson holdout: `python -m ace_playbook.compile.holdout_report --start ISO --end ISO
[--min-prompts 1] [--interim-descriptive] [--json]` (section "Holdout analysis" of docs/design/compiler.md). Stdlib and sibling modules only;
offline, never on the hook path. Prints counts and lesson ids, never text, and never the seed.

Assignment: the injector's `injections.jsonl` rows of one RAW session id, joined to the detector rows by `state.session_hash`. A session
counts when at least one of its rows has `holdout_rate` > 0 (all must be 0.5, the null of the permutation test), none was `refused`, and every
row (startup, clear, resume, compact) says the same for the three lessons of `LESSONS`: in `holdout_ids` (hidden) or `shown_ids` (shown). A
lesson in neither is not assigned: that (session, lesson) pair is left out and counted. Eligibility: the newest `_session` row has `prompts`
>= `--min-prompts` and `start` in [start, end). Outcome per (session, lesson): `count` = the sum of the `count` of the lesson's detector rows
(deduplicated by `id`), `any` = count > 0. Per lesson: the PRIMARY difference in the `any` rate (shown minus hidden, Wilson interval per arm),
the SECONDARY difference in mean counts, each with `arms_stats.permutation_test` (arm rate 0.5, 10,000 draws, seed `holdout_permutation_seed`)
and `arms_stats.holm` over the three lessons, the two families apart. Before `--end` (exit 2) only `--interim-descriptive` runs: per-arm n and
means, no p-value and no interval.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config as cfgmod
from ..canon import now_iso
from . import labels, metrics, report
from . import state as state_mod
from .arms_stats import ALPHA, RUNS, holm, permutation_test
from .arms_window import _dt
from .holdout_power import LESSONS

HEADING = "descriptive, not the pre-registered test"
ARM_RATE = 0.5
COUNTERS = ("injection_sessions", "session_rows", "below_min_prompts", "outside_window", "no_start", "unmatched_injection", "unmatched_session",
            "refused", "holdout_off", "rate_not_half", "assignment_conflict", "analysed_sessions", "lesson_not_assigned")


def _ids(row: dict, key: str) -> set:
    return set(row[key]) if isinstance(row.get(key), list) else set()


def _rate(row: dict) -> float:
    r = row.get("holdout_rate")
    return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else 0.0


def _arm(row: dict, lid: str) -> str:
    hidden, shown = lid in _ids(row, "holdout_ids"), lid in _ids(row, "shown_ids")
    return "both" if hidden and shown else "hidden" if hidden else "shown" if shown else "none"


def assignments(inj_rows: list[dict]) -> dict[str, dict]:
    """{session hash: {"first": earliest row stamp, "rows", "off", "refused", "odd_rate", "conflict", "arms": {lesson: shown|hidden|none}}}."""
    by: dict[str, list[dict]] = {}
    for r in inj_rows:
        if isinstance(r.get("session_id"), str) and r["session_id"]:
            by.setdefault(state_mod.session_hash(r["session_id"]), []).append(r)
    out = {}
    for h, rows in by.items():
        sigs = {tuple(_arm(r, lid) for lid, _ in LESSONS) for r in rows}
        live = [_rate(r) for r in rows if _rate(r) > 0]
        out[h] = {"first": min(str(r.get("ts", "")) for r in rows), "off": not live, "refused": any(r.get("refused") for r in rows),
                  "odd_rate": any(x != ARM_RATE for x in live), "conflict": len(sigs) > 1 or "both" in next(iter(sigs)),
                  "arms": dict(zip((lid for lid, _ in LESSONS), sorted(sigs)[0]))}
    return out


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _test(shown: list[float], hidden: list[float], seed: str) -> dict:
    """D = mean(shown) - mean(hidden) and its permutation test over the sessions' values (D None when an arm is empty: p None)."""
    d = None if not shown or not hidden else _mean(shown) - _mean(hidden)
    t = permutation_test({"values": shown + hidden, "D": d}, ARM_RATE, seed, RUNS)
    return {"D": d, "p": t["p"], "ci95": t["ci95"], "rate_ratio": metrics.ratio(_mean(shown), _mean(hidden), digits=4) if d is not None else metrics.DASH}


def analyse(cfg, start: str, end: str, min_prompts: int, inj_rows: list[dict], ev_rows: list[dict], descriptive: bool) -> dict:
    t0, t1 = _dt(start), _dt(end)
    inj, sess = assignments(inj_rows), metrics.latest_sessions(ev_rows)
    c = dict.fromkeys(COUNTERS, 0)
    c["injection_sessions"], c["session_rows"] = len(inj), len(sess)
    window = lambda ts: _dt(ts) is not None and t0 <= _dt(ts) < t1                               # noqa: E731
    fires: dict[tuple[str, str], int] = {}
    seen: set = set()
    for r in ev_rows:
        key = (r.get("session"), r.get("detector"))
        if r.get("detector") not in dict(LESSONS).values() or (r.get("id") in seen and r.get("id") is not None):
            continue
        seen.add(r.get("id"))
        fires[key] = fires.get(key, 0) + (r["count"] if isinstance(r.get("count"), int) and not isinstance(r.get("count"), bool) else 0)
    used: list[str] = []
    for h, r in sorted(sess.items()):
        p = r.get("prompts")
        ok = isinstance(p, int) and not isinstance(p, bool) and p >= min_prompts
        if h not in inj:
            c["unmatched_session"] += ok and window(r.get("start"))
        elif not r.get("start") or _dt(r.get("start")) is None:
            c["no_start"] += 1
        elif not ok:
            c["below_min_prompts"] += 1
        elif not window(r["start"]):
            c["outside_window"] += 1
        else:
            a = inj[h]
            reason = next((k for k, bad in (("refused", a["refused"]), ("holdout_off", a["off"]), ("rate_not_half", a["odd_rate"]),
                                            ("assignment_conflict", a["conflict"])) if bad), None)
            c[reason or "analysed_sessions"] += 1
            used += [] if reason else [h]
    c["unmatched_injection"] = sum(h not in sess and window(a["first"]) for h, a in inj.items())
    lessons, c["lesson_not_assigned"] = [], 0
    for lid, det in LESSONS:
        arms = {h: inj[h]["arms"][lid] for h in used}
        shown, hidden = ([fires.get((h, det), 0) for h in used if arms[h] == k] for k in ("shown", "hidden"))
        gone = sum(v == "none" for v in arms.values())
        c["lesson_not_assigned"] += gone
        row = {"lesson": lid, "detector": det, "n": {"shown": len(shown), "hidden": len(hidden)}, "not_assigned": gone}
        anys = ([int(x > 0) for x in shown], [int(x > 0) for x in hidden])
        row |= {"any_rate": dict(zip(("shown", "hidden"), map(_mean, anys))), "mean": dict(zip(("shown", "hidden"), map(_mean, (shown, hidden))))}
        if not descriptive:
            row["primary"] = {"any_rate": row.pop("any_rate"), "wilson95": {k: metrics.wilson(sum(a), len(a)) for k, a in zip(("shown", "hidden"), anys)},
                              **_test(*anys, cfg.holdout_permutation_seed)}
            row["secondary"] = {"mean": row.pop("mean"), **_test([float(x) for x in shown], [float(x) for x in hidden], cfg.holdout_permutation_seed)}
        lessons.append(row)
    for part in () if descriptive else ("primary", "secondary"):
        for row, (adj, rej) in zip(lessons, holm([x[part]["p"] for x in lessons], ALPHA)):
            row[part] |= {"holm_p": adj, "reject": rej}
    return {"config": {"start": start, "end": end, "min_prompts": min_prompts, "permutation_draws": RUNS, "alpha": ALPHA},
            **({"heading": HEADING} if descriptive else {}), "counts": c, "lessons": lessons}


def _cells(d: dict) -> list[str]:
    out = []
    for x in d["lessons"]:
        n = f"{x['lesson']} {x['detector']}: n shown {x['n']['shown']}, hidden {x['n']['hidden']}, not assigned {x['not_assigned']}"
        if "primary" not in x:
            out += [n, f"  any rate {x['any_rate']}  mean count {x['mean']}"]
            continue
        p, s = x["primary"], x["secondary"]
        out += [n, f"  primary (any): rate {p['any_rate']}  wilson95 {p['wilson95']}  D {p['D']}  RR {p['rate_ratio']}  p {p['p']}  holm {p['holm_p']}  "
                   f"reject {p['reject']}", f"  secondary (count): mean {s['mean']}  D {s['D']}  RR {s['rate_ratio']}  p {s['p']}  holm {s['holm_p']}  "
                   f"reject {s['reject']}"]
    return out


def render(d: dict) -> str:
    head = d.get("heading") or f"ace-playbook holdout analysis (preregistered; permutation test, Holm across the three lessons, alpha {ALPHA})"
    cfg = d["config"]
    return "\n".join([head, f"window [{cfg['start']}, {cfg['end']}), min prompts {cfg['min_prompts']}",
                      "counts: " + ", ".join(f"{k} {v}" for k, v in d["counts"].items()), *_cells(d)]) + "\n"


def main(argv: list[str] | None = None, now: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ace_playbook.compile.holdout_report")
    ap.add_argument("--config")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--min-prompts", type=int, default=1)
    ap.add_argument("--interim-descriptive", action="store_true")
    ap.add_argument("--injections", help="injections.jsonl (default: the configured data dir's)")
    ap.add_argument("--events", help="detector_events.jsonl (default: the configured data dir's)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not (_dt(args.start) and _dt(args.end) and _dt(args.start) < _dt(args.end)):
        ap.error("--start and --end must be UTC stamps like 2026-11-01T00:00:00Z, start before end")
    try:
        cfg = cfgmod.load(args.config)
        if cfg.holdout_permutation_seed is None:
            raise labels.Refusal("holdout_permutation_seed is unset in the config (64 lowercase hex): the analysis refuses without it", 2)
        if not args.interim_descriptive and (_dt(now or now_iso()) < _dt(args.end)):
            raise labels.Refusal(f"the analysis day (--end {args.end}) has not passed: only --interim-descriptive runs before it", 2)
        inj_rows = report._read(Path(args.injections) if args.injections else cfg.injections)
        ev_rows = report._read(Path(args.events) if args.events else cfg.path(report.EVENTS))
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print(f"error: {'config not found' if isinstance(exc, OSError) else exc}", file=sys.stderr)
        return 2
    d = analyse(cfg, args.start, args.end, args.min_prompts, inj_rows, ev_rows, args.interim_descriptive)
    print(json.dumps(d, indent=1, sort_keys=True) if args.json else render(d), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
