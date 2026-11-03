"""Power of the per-session lesson holdout: `python -m ace_playbook.compile.holdout_power [--events PATH] [--json]` (section "Holdout analysis" of
docs/design/compiler.md). Stdlib and sibling modules only; offline, never on the hook path.

In each real session each of the three lessons is independently shown or hidden (the injector's `coin`, rate 0.5), and the outcome is the lesson's
detector (`LESSONS`) in `detector_events.jsonl`: binary (did the mistake occur at least once) or the count of fires. `fit` reads the per-session
fire counts of the eligible sessions (mean, sample variance, the negative-binomial dispersion k = mean^2 / (var - mean) by moments, infinite when
var <= mean, and the share of sessions with a fire); `simulate` draws sessions (the hidden arm from the fitted law, the shown arm with its mean or
probability times the rate ratio `rr`) and tests each lesson with `arms_stats.permutation_test` (or, in the fast `normal` method, a Welch z), then
`arms_stats.holm` over the three lessons: the power of a lesson is the share of simulations in which it is rejected at 0.05 after Holm.
The data of a simulation come from their own random stream, so the two methods see the same sessions. The Welch z is only trusted from
`SMALL_N` sessions per arm: a smaller `normal` cell is run with the permutation method and labelled `small-n`. `fit(.., since)` leaves out the
sessions that started before the detectors existed (default `default_since`: the earliest start with a fire row of a lesson's detector).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime
from math import inf
from pathlib import Path

from .. import config as cfgmod
from . import labels, metrics, report
from .arms_stats import ALPHA, holm, permutation_test
from .holdout_io import ARM_RATE, LESSONS, fire_counts, nat, parse_ts

OUTCOMES = ("binary", "count")
SMALL_N = 60                       # sessions per arm below which the normal approximation is not trusted: such cells use `perm` ("small-n")


def default_since(session_rows: list[dict], fire_rows: list[dict]) -> str | None:
    """The earliest `_session` start (as written) among the sessions with ANY offline fire row of a lesson's detector, else None: before it
    the detectors did not exist, so those sessions cannot fire."""
    dets = {d for _, d in LESSONS}
    fired = {r.get("session") for r in fire_rows if r.get("tier") == "offline" and r.get("detector") in dets}
    starts = [(parse_ts(r.get("start")), r["start"]) for s, r in metrics.latest_sessions(session_rows).items() if s in fired and parse_ts(r.get("start"))]
    return min(starts)[1] if starts else None


def fit(session_rows: list[dict], fire_rows: list[dict], min_prompts: int, since: str | None = None) -> dict[str, dict]:
    """{detector: {"mean", "var", "k", "p_any", "n"}} over the eligible sessions (the newest `_session` row of a session with `prompts` >=
    `min_prompts` and, with `since`, a `start` at or after it), counting `count` of the offline-tier rows of the detector deduplicated by
    `id`; a session with no fire row has count 0."""
    t0 = parse_ts(since) if since else None
    elig = {s for s, r in metrics.latest_sessions(session_rows).items()
            if nat(r.get("prompts")) >= min_prompts and (t0 is None or (parse_ts(r.get("start")) or datetime.min) >= t0)}
    counts = fire_counts(fire_rows)
    per: dict[str, dict[str, int]] = {d: {s: counts.get((s, d), 0) for s in sorted(elig)} for _, d in LESSONS}
    out = {}
    for d, by in per.items():
        xs, n = list(by.values()), len(by)
        mean = sum(xs) / n if n else 0.0
        var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
        out[d] = {"mean": mean, "var": var, "k": mean * mean / (var - mean) if var > mean else inf,
                  "p_any": sum(x > 0 for x in xs) / n if n else 0.0, "n": n}
    return out


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's product method, in pieces of at most 20 (a sum of Poissons is a Poisson)."""
    total = 0
    while lam > 0:
        part, lam = min(lam, 20.0), lam - min(lam, 20.0)
        limit, prod = math.exp(-part), rng.random()
        while prod > limit:
            total, prod = total + 1, prod * rng.random()
    return total


def draw(rng: random.Random, mean: float, k: float) -> int:
    """One count with mean `mean`: gamma-Poisson (negative binomial, dispersion k), or Poisson when k is infinite."""
    if mean <= 0:
        return 0
    return _poisson(rng, mean if k == inf else rng.gammavariate(k, mean / k))


def _values(rng: random.Random, par: dict, rr: float, n: int, outcome: str) -> tuple[list[float], list[bool]]:
    vals, shown = [], []
    for _ in range(n):
        on = rng.random() < ARM_RATE
        if outcome == "binary":
            vals.append(float(rng.random() < par["p_any"] * (rr if on else 1.0)))
        else:
            vals.append(float(draw(rng, par["mean"] * (rr if on else 1.0), par["k"])))
        shown.append(on)
    return vals, shown


def _welch_p(vals: list[float], shown: list[bool]) -> float:
    a, b = [v for v, s in zip(vals, shown) if s], [v for v, s in zip(vals, shown) if not s]
    if len(a) < 2 or len(b) < 2:
        return 1.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    se = math.sqrt(sum((x - ma) ** 2 for x in a) / (len(a) - 1) / len(a) + sum((x - mb) ** 2 for x in b) / (len(b) - 1) / len(b))
    return (1.0 if ma == mb else 0.0) if se == 0 else math.erfc(abs(ma - mb) / se / math.sqrt(2))


def _perm_p(vals: list[float], shown: list[bool], perms: int, seed: str) -> float | None:
    k = sum(shown)
    if k in (0, len(vals)):
        return None
    d = sum(v for v, s in zip(vals, shown) if s) / k - sum(v for v, s in zip(vals, shown) if not s) / (len(vals) - k)
    return permutation_test({"values": vals, "D": d}, ARM_RATE, seed, perms)["p"]


def simulate(params: dict, rr: float, n_sessions: int, outcome: str, sims: int, perms: int, seed: str, method: str = "perm") -> dict[str, float]:
    """{lesson id: power} of `sims` simulated experiments of `n_sessions` sessions. `outcome` is `binary` or `count`, `method` `perm`
    (`perms` permutations per test) or `normal`. Deterministic for a seed."""
    if outcome not in OUTCOMES or method not in ("perm", "normal"):
        raise ValueError("outcome is binary|count and method perm|normal")
    rng, hits = random.Random(f"{seed}|{rr}|{n_sessions}|{outcome}|data"), dict.fromkeys((lid for lid, _ in LESSONS), 0)
    for sim in range(sims):
        ps = []
        for lid, det in LESSONS:
            vals, shown = _values(rng, params[det], rr, n_sessions, outcome)
            ps.append(_welch_p(vals, shown) if method == "normal" else _perm_p(vals, shown, perms, f"{seed}|{sim}|{lid}"))
        for (lid, _), (_, rejected) in zip(LESSONS, holm(ps, ALPHA)):
            hits[lid] += rejected
    return {lid: n / sims for lid, n in hits.items()}


def grid(params: dict, rates: list[float], weeks: list[int], rrs: list[float], method: str, sims: int | None, perms: int, seed: str) -> list[dict]:
    """One row per (lesson, outcome, sessions per day, weeks) with `power` = {rr: power} and the `method` used: a `normal` cell with fewer
    than `SMALL_N` sessions per arm is run with `perm` and labelled `small-n`. `sims` None: 400 for `normal`, 60 for `perm` cells."""
    out = []
    for outcome in OUTCOMES:
        for rate in rates:
            for w in weeks:
                n = round(rate * 7 * w)
                label = "small-n" if method == "normal" and n < 2 * SMALL_N else method
                use = "perm" if label == "small-n" else method
                by_rr = {rr: simulate(params, rr, n, outcome, sims or (400 if use == "normal" else 60), perms, seed, use) for rr in rrs}
                out += [{"lesson": lid, "outcome": outcome, "rate": rate, "weeks": w, "sessions": n, "method": label,
                         "power": {str(rr): by_rr[rr][lid] for rr in rrs}} for lid, _ in LESSONS]
    return sorted(out, key=lambda r: ([lid for lid, _ in LESSONS].index(r["lesson"]), OUTCOMES.index(r["outcome"]), r["rate"], r["weeks"]))


def _floats(text: str) -> list[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ace_playbook.compile.holdout_power")
    ap.add_argument("--config")
    ap.add_argument("--events", help="detector_events.jsonl (default: the configured data dir's)")
    ap.add_argument("--min-prompts", type=int, default=1)
    ap.add_argument("--since", help="leave out sessions that started before this UTC stamp (default: the earliest start with a detector fire row)")
    ap.add_argument("--rates", default="1.1,2.0,4.0", help="eligible sessions per day: the INTERACTIVE rate (D-239)")
    ap.add_argument("--weeks", default="4,8,12,16")
    ap.add_argument("--rr", default="0.5,0.67,0.8", help="rate ratios, shown versus hidden")
    ap.add_argument("--method", choices=("normal", "perm"), default="normal")
    ap.add_argument("--sims", type=int)
    ap.add_argument("--perms", type=int, default=199)
    ap.add_argument("--seed", default="holdout-power")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.since is not None and parse_ts(args.since) is None:
        ap.error("--since must be a UTC stamp like 2026-11-01T00:00:00Z")
    try:
        path = args.events or cfgmod.load(args.config).path(report.EVENTS)
        rows = report._read(Path(path))
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    since = args.since or default_since(rows, rows)
    par, rrs = fit(rows, rows, args.min_prompts, since), _floats(args.rr)
    cells = grid(par, _floats(args.rates), [int(w) for w in _floats(args.weeks)], rrs, args.method, args.sims, args.perms, args.seed)
    shown = {d: {k: ("inf" if v == inf else v) for k, v in f.items()} for d, f in par.items()}
    if args.json:
        print(json.dumps({"since": since, "fit": shown, "grid": cells}, indent=1, sort_keys=True))
        return 0
    print(f"fit over eligible sessions (prompts >= {args.min_prompts}, since {since} ({'--since' if args.since else 'default: first fire row'})): "
          "n, mean, var, k (inf: Poisson), share with a fire")
    for lid, d in LESSONS:
        f = shown[d]
        print(f"  {lid} {d}: n {f['n']}  mean {f['mean']:.3f}  var {f['var']:.3f}  k {f['k'] if f['k'] == 'inf' else round(f['k'], 3)}  p_any {f['p_any']:.3f}")
    print(f"power ({args.method}, alpha {ALPHA} after Holm over 3 lessons, shown arm rate x rr; columns are rr)")
    print("note: --rates must be the INTERACTIVE rate (the report analyses entrypoint cli sessions only); the fit reads no injection rows")
    print("lesson      outcome  /day weeks  n  " + "  ".join(f"rr{rr:g}".rjust(6) for rr in rrs) + "  method (small-n: under 60 per arm, run as perm)")
    for c in cells:
        print(f"{c['lesson']}  {c['outcome']:6}  {c['rate']:4g} {c['weeks']:4}  {c['sessions']:4}  "
              + "  ".join(f"{c['power'][str(rr)]:6.2f}" for rr in rrs) + f"  {c['method']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
