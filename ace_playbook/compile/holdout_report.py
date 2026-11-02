"""The pre-registered analysis of the per-session lesson holdout: `python -m ace_playbook.compile.holdout_report [--min-prompts 1] [--json]` is
the single confirmatory read of the window registered in the config (`holdout_start`, `holdout_end`), and `--interim-descriptive [--start ISO
--end ISO]` describes any window (section "Holdout analysis" of docs/design/compiler.md). Stdlib and sibling modules only; offline, never on the
hook path. Prints counts and lesson ids, never text, and never the seed.

Assignment (`holdout_io.assignments`): the injector's `injections.jsonl` rows of one RAW session id, joined to the detector rows by
`state.session_hash`. A session counts when every row has `holdout_rate` 0.5 (a session with a rate-0 row and a live row is `mixed_rate`,
whatever its arms; live rows with different `seed_fp` are `seed_fp_mismatch`), none was `refused`, and every row (startup, clear, resume,
compact) says the same for the three lessons of `LESSONS`: in `holdout_ids` (hidden) or `shown_ids` (shown). A lesson in `dropped_cap` is
shown (the coin said so; `dropped_cap` counts them); a lesson in none of the lists is not assigned: that (session, lesson) pair is left out
and counted. Eligibility: the newest `_session` row has `prompts` >= `--min-prompts` and `start` in [start, end); stamps may carry
milliseconds. Outcome per (session, lesson): `count` = the sum of the `count` of the lesson's offline-tier detector rows (deduplicated by
`id`), `any` = count > 0. Per lesson: the PRIMARY difference in the `any` rate (shown minus hidden, Wilson interval per arm), the SECONDARY
difference in mean counts, each with `arms_stats.permutation_test` (arm rate 0.5, 10,000 draws, seed `holdout_permutation_seed`) and
`arms_stats.holm` over the three lessons, the two families apart. Inference runs only on the registered window and only after `holdout_end`;
`--start/--end` are for `--interim-descriptive` alone (per-arm n and means, no p-value and no interval).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config as cfgmod
from ..canon import now_iso
from . import labels, metrics, report
from .arms_stats import ALPHA, RUNS, holm, permutation_test
from .holdout_io import ARM_RATE, LESSONS, assignments, fire_counts, parse_ts

HEADING = "descriptive, not the pre-registered test"
COUNTERS = ("injection_sessions", "null_session_id", "session_rows", "below_min_prompts", "outside_window", "no_start", "unmatched_injection",
            "unmatched_session", "refused", "holdout_off", "mixed_rate", "rate_not_half", "seed_fp_mismatch", "assignment_conflict",
            "analysed_sessions", "lesson_not_assigned", "dropped_cap")


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _test(shown: list[float], hidden: list[float], seed: str) -> dict:
    """D = mean(shown) - mean(hidden) and its permutation test over the sessions' values (D None when an arm is empty: p None). `ci95` is
    the interval of D minus the quantiles of the shifted null re-draws, not a confidence interval of the effect: callers rename it."""
    d = None if not shown or not hidden else _mean(shown) - _mean(hidden)
    t = permutation_test({"values": shown + hidden, "D": d}, ARM_RATE, seed, RUNS)
    return {"D": d, "p": t["p"], "ci95": t["ci95"], "rate_ratio": metrics.ratio(_mean(shown), _mean(hidden), digits=4) if d is not None else metrics.DASH}


def _classify(a: dict) -> str | None:
    """The first reason an assigned session is left out, else None (the order is the order of the counters)."""
    return next((k for k, bad in (("refused", a["refused"]), ("holdout_off", a["off"]), ("mixed_rate", a["mixed"]), ("rate_not_half", a["odd_rate"]),
                                  ("seed_fp_mismatch", a["seed_fp_mismatch"]), ("assignment_conflict", a["conflict"])) if bad), None)


def analyse(cfg, start: str, end: str, min_prompts: int, inj_rows: list[dict], ev_rows: list[dict], descriptive: bool) -> dict:
    t0, t1 = parse_ts(start), parse_ts(end)
    (inj, null_ids), sess = assignments(inj_rows), metrics.latest_sessions(ev_rows)
    c = dict.fromkeys(COUNTERS, 0)
    c["injection_sessions"], c["null_session_id"], c["session_rows"] = len(inj), null_ids, len(sess)
    window = lambda t: t is not None and t0 <= t < t1                                        # noqa: E731
    fires = fire_counts(ev_rows)
    used: list[str] = []
    for h, r in sorted(sess.items()):
        ok, t = isinstance(r.get("prompts"), int) and not isinstance(r.get("prompts"), bool) and r["prompts"] >= min_prompts, parse_ts(r.get("start"))
        if h not in inj:
            c["unmatched_session"] += ok and window(t)
        elif t is None:
            c["no_start"] += 1
        elif not ok:
            c["below_min_prompts"] += 1
        elif not window(t):
            c["outside_window"] += 1
        else:
            reason = _classify(inj[h])
            c[reason or "analysed_sessions"] += 1
            used += [] if reason else [h]
    c["unmatched_injection"] = sum(h not in sess and window(a["first"]) for h, a in inj.items())
    lessons = []
    for lid, det in LESSONS:
        arms = {h: inj[h]["arms"][lid] for h in used}
        shown, hidden = ([fires.get((h, det), 0) for h in used if arms[h] == k] for k in ("shown", "hidden"))
        gone = sum(v == "none" for v in arms.values())
        c["lesson_not_assigned"] += gone
        c["dropped_cap"] += sum(lid in inj[h]["dropped"] and arms[h] == "shown" for h in used)
        row = {"lesson": lid, "detector": det, "n": {"shown": len(shown), "hidden": len(hidden)}, "not_assigned": gone}
        anys = ([int(x > 0) for x in shown], [int(x > 0) for x in hidden])
        row |= {"any_rate": dict(zip(("shown", "hidden"), map(_mean, anys))), "mean": dict(zip(("shown", "hidden"), map(_mean, (shown, hidden))))}
        if not descriptive:
            row["primary"] = {"any_rate": row.pop("any_rate"), "wilson95": {k: metrics.wilson(sum(a), len(a)) for k, a in zip(("shown", "hidden"), anys)},
                              **{k: v for k, v in _test(*anys, cfg.holdout_permutation_seed).items() if k != "ci95"}}
            sec = _test([float(x) for x in shown], [float(x) for x in hidden], cfg.holdout_permutation_seed)
            row["secondary"] = {"mean": row.pop("mean"), **{k: v for k, v in sec.items() if k != "ci95"}, "shift_ci95_null_redraws": sec["ci95"]}
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


def _window(args, cfg, ap) -> tuple[str, str]:
    """The (start, end) to analyse, or a one-line refusal: the registered window for the test, any window (both ends, from the command line
    or else the config) only for the descriptive mode."""
    if (args.start is None) != (args.end is None):
        raise labels.Refusal("--start and --end go together", 2)
    if args.start is not None:
        if not args.interim_descriptive:
            raise labels.Refusal("--start/--end only describe: use them with --interim-descriptive; the test reads holdout_start/holdout_end", 2)
        if not (parse_ts(args.start) and parse_ts(args.end) and parse_ts(args.start) < parse_ts(args.end)):
            ap.error("--start and --end must be UTC stamps like 2026-11-01T00:00:00Z, start before end")
        return args.start, args.end
    if cfg.holdout_start is None or cfg.holdout_end is None:
        raise labels.Refusal("holdout_start and holdout_end are unset in the config (UTC stamps): the registered window is required", 2)
    return cfg.holdout_start, cfg.holdout_end


def main(argv: list[str] | None = None, now: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ace_playbook.compile.holdout_report")
    ap.add_argument("--config")
    ap.add_argument("--start", help="with --interim-descriptive only: describe this window instead of the registered one")
    ap.add_argument("--end", help="with --interim-descriptive only")
    ap.add_argument("--min-prompts", type=int, default=1)
    ap.add_argument("--interim-descriptive", action="store_true")
    ap.add_argument("--injections", help="injections.jsonl (default: the configured data dir's)")
    ap.add_argument("--events", help="detector_events.jsonl (default: the configured data dir's)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = cfgmod.load(args.config)
        if cfg.holdout_permutation_seed is None:
            raise labels.Refusal("holdout_permutation_seed is unset in the config (64 lowercase hex): the analysis refuses without it", 2)
        start, end = _window(args, cfg, ap)
        if not args.interim_descriptive and parse_ts(now or now_iso()) < parse_ts(end):
            raise labels.Refusal(f"the analysis day (holdout_end {end}) has not passed: only --interim-descriptive runs before it", 2)
        inj_rows = report._read(Path(args.injections) if args.injections else cfg.injections)
        ev_rows = report._read(Path(args.events) if args.events else cfg.path(report.EVENTS))
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print(f"error: {'config not found' if isinstance(exc, OSError) else exc}", file=sys.stderr)
        return 2
    d = analyse(cfg, start, end, args.min_prompts, inj_rows, ev_rows, args.interim_descriptive)
    print(json.dumps(d, indent=1, sort_keys=True) if args.json else render(d), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
