"""The arm analysis of the pre-registration (PREREGISTRATION.md, Shadow-mode additions): `python -m ace_playbook.compile.arms report [--json]`.

Stdlib and sibling modules only, never on the hook path. It computes, per detector, the one primary: repeat fires per eligible session
(fires beyond the first, `agent_id_present` false rows only), D = mean(warn arm) - mean(observe arm), a permutation test with 10,000
Bernoulli(arm_rate) re-draws under the committed permutation seed (two-sided p = (1 + #{|D*| >= |D|}) / 10,001, intention to treat by
assigned arm), a percentile-pivot interval for D, and Holm over every detector with an `arm_start`. Both seeds are read from the config at
call time; only their sha256 is compared with the sealed block and nothing prints a seed. One approved proposal per detector is assumed;
with more, each proposal is its own hypothesis in the Holm family (conservative). The window (arm start, horizon, eligibility) is `arms_window`.

It REFUSES (ValueError; the CLI exits 1) when a fire row of an eligible session lacks `arm` or `agent_id_present`, or records an `arm` that
`assign` does not give: the hook must have randomized and flagged every row (PREREGISTRATION.md, Pre-arm-start conditions). Before the analysis
day (the horizon end plus 7 days) only counts are shown, never an effect; until every detector with an `arm_start` is final no Holm-adjusted p
and no rejection is shown (`pending: N detectors not final`).
Interactive means at least one human prompt (`prompts` > 0 in the `_session` row); the plan-start clause of step0/measure.py:79 is not
recorded there. The session start is the `_session` row's `start`; a row without one (scanned before it existed) is reported as missing.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from datetime import timedelta
from typing import Callable

from .. import config as cfgmod
from ..canon import now_iso
from . import labels, metrics, report, seal
from .arms_window import (CAP, LAG_DAYS, WEEKS, _dt, _fmt, arm_starts, check_starts, eligible_sessions,  # noqa: F401  (re-exported)
                          first_demotion, horizon_end)
from .arms_stats import ALPHA, RUNS, holm, permutation_test  # noqa: F401  (the public names of the analysis)

WARN, OBSERVE = "warn", "observe"


def assign(session_hash: str, shadow_seed: str, arm_rate: float, proposal_id: str = "") -> str:
    """`warn` (the check runs in its approved mode) with probability `arm_rate`, else `observe`: HMAC-SHA256 of the session hash (and the
    proposal id) under the seed, its first 4 bytes as a fraction of 2**32. Deterministic; the seed is never logged."""
    mac = hmac.new(shadow_seed.encode(), f"{session_hash}|{proposal_id}".encode(), hashlib.sha256).digest()
    return WARN if int.from_bytes(mac[:4], "big") / 2**32 < arm_rate else OBSERVE


def primary(rows: list[dict], arm_of: Callable[[str], str], eligible: dict[str, dict]) -> dict:
    """The primary of one proposal over its eligible sessions. `rows` are its shadow rows, `arm_of(session)` the assigned arm. A session's
    value is max(0, fires - 1) over rows with `agent_id_present` false; subagent rows (true) are counted apart. ValueError (counts only)
    when a fire row of an eligible session lacks `arm` or `agent_id_present`, or records an `arm` that `arm_of` does not give."""
    fires: dict[str, int] = {s: 0 for s in eligible}
    sub, bad = {WARN: 0, OBSERVE: 0}, {"arm": 0, "agent_id_present": 0, "arm differs from assign": 0}
    for r in rows:
        s = r.get("session")
        if s not in eligible or not metrics._fire(r) or r.get("cause") == "truncated":
            continue
        flag, arm = r.get("agent_id_present"), r.get("arm")
        bad["agent_id_present"] += not isinstance(flag, bool)
        bad["arm"] += arm not in (WARN, OBSERVE)
        bad["arm differs from assign"] += arm in (WARN, OBSERVE) and arm != arm_of(s)
        if metrics._scanned(r, eligible):
            if flag is False:
                fires[s] += 1
            elif flag is True:
                sub[arm_of(s)] += 1
    if any(bad.values()):
        raise ValueError("fire rows of eligible sessions cannot be analysed (" + ", ".join(f"{n} with {k}" for k, n in bad.items() if n)
                         + "): the hook must randomize and flag every row before the arm start")
    order = sorted(eligible)
    arms, values = {s: arm_of(s) for s in order}, {s: max(0, fires[s] - 1) for s in order}
    n = {a: sum(arms[s] == a for s in order) for a in (WARN, OBSERVE)}
    mean = {a: (sum(values[s] for s in order if arms[s] == a) / n[a] if n[a] else None) for a in n}
    d = None if None in mean.values() else mean[WARN] - mean[OBSERVE]
    with_fire = {a: sum(fires[s] > 0 for s in order if arms[s] == a) for a in n}
    return {"sessions": order, "values": [values[s] for s in order], "warn": [arms[s] == WARN for s in order], "n": n, "mean": mean, "D": d,
            "rate_ratio": metrics.ratio(mean[WARN], mean[OBSERVE], digits=4) if d is not None else metrics.DASH,
            "sessions_with_fire": {a: {"k": with_fire[a], "n": n[a], "wilson95": metrics.wilson(with_fire[a], n[a])} for a in n},
            "subagent_fires": sub}


def _sealed(cfg, text: str) -> str | None:
    """Why the config's seeds do not match the sealed block, or None. Only sha256 values are compared."""
    for key, line in (("shadow_seed", seal.SEED), ("permutation_seed", seal.PERM)):
        value = getattr(cfg, key)
        if value is None or f"\n{line} {seal._sha(value)}\n" not in "\n" + text:
            return f"{key} is unset or does not match {line} in the pre-registration"
    return None if cfg.arm_rate is not None else "arm_rate is unset"


def analyse(cfg, now: str | None = None) -> dict:
    """{"status": "not started"} or {"detectors": {proposal: {...}}, "holm": {...}, "pending": N}: the whole report, in plain data.
    ValueError for an arm_start before sealed_at, a broken demotions chain or rows that `primary` refuses; Refusal for the seeds."""
    text = seal.PREREG.read_text(encoding="utf-8") if seal.PREREG.exists() else ""
    starts = arm_starts(text)
    if not starts:
        return {"status": "not started"}
    check_starts(text, starts)
    if why := _sealed(cfg, text):
        raise labels.Refusal(why)
    now = now or now_iso()
    rows, _ = report.read_corpora(cfg)
    ev_rows, out, ok = report._read(cfg.path(report.EVENTS)), {}, {}
    scanned = metrics.latest_sessions(ev_rows)
    for det, t0 in sorted(starts.items()):
        pids = sorted({r["proposal_id"] for r in rows if report._meta(cfg, r["proposal_id"])[0] == det})
        demoted, ok[det] = first_demotion(cfg, pids, t0), bool(pids)
        for pid in pids:
            sess, end, missing = eligible_sessions(ev_rows, t0, None, det, demoted)
            arm_of = lambda s, pid=pid: assign(s, cfg.shadow_seed, cfg.arm_rate, pid)       # noqa: E731
            stat = primary([r for r in rows if r["proposal_id"] == pid], arm_of, sess)
            due = _fmt(_dt(end) + timedelta(days=LAG_DAYS))
            final = ok[det] = ok[det] and now >= due
            waiting = sum(r["proposal_id"] == pid and metrics._fire(r) and not metrics._scanned(r, scanned) for r in rows)
            row = {"detector": det, "arm_start": t0, "horizon_end": end, "analysis_due": due, "final": now >= due, "eligible": len(sess),
                   "missing_start": missing, "fires_awaiting_scan": waiting, "demoted_at": demoted}
            if row["final"]:
                row |= {k: stat[k] for k in ("n", "mean", "rate_ratio", "sessions_with_fire", "subagent_fires")}
                row["test"] = permutation_test(stat, cfg.arm_rate, cfg.permutation_seed)
            out[pid] = row
    pending = sum(not v for v in ok.values())
    if pending:                                                     # the family is every detector with an arm_start: no rejection until all are final
        return {"detectors": out, "holm": {}, "pending": pending}
    adj = holm([v["test"]["p"] for v in out.values()])
    return {"detectors": out, "holm": {k: {"adjusted_p": a, "reject": r} for k, (a, r) in zip(out, adj)}, "pending": 0}


def render(d: dict) -> str:
    if d.get("status"):
        return d["status"] + "\n"
    lines = ["ace-playbook arm analysis (preregistered; intention to treat; Holm across detectors)"]
    for pid, v in d["detectors"].items():
        head = f"{pid} {v['detector']}: eligible {v['eligible']}, missing start {v['missing_start']}, awaiting scan {v['fires_awaiting_scan']}"
        lines.append(f"{head}; arm start {v['arm_start']}, horizon end {v['horizon_end']}, analysis due {v['analysis_due']}")
        if v["final"]:
            t = v["test"]
            lines.append(f"  n {v['n']}  mean {v['mean']}  D {t['D']}  p {t['p']}  ci95 {t['ci95']}")
            if d["holm"]:
                lines.append(f"  holm-adjusted p {d['holm'][pid]['adjusted_p']}  reject {d['holm'][pid]['reject']}  (family alpha {ALPHA}, two-sided)")
        else:
            lines.append("  no effect is shown before the analysis day (no interim looks)")
    if d["pending"]:
        lines.append(f"pending: {d['pending']} detectors not final (no Holm-adjusted p and no rejection until every detector is final)")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ace_playbook.compile.arms")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report").add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        d = analyse(cfgmod.load(args.config))
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(d, indent=1, sort_keys=True) if args.json else render(d), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
