"""`compile status` (docs/design/compiler.md 8.x): the operator's one screen, and `status.json` behind it.

`build` is `report.collect` plus the chains and the clock: every count and rate comes from `report` and `metrics` (precision is
`metrics.wilson`; this module does no other rate arithmetic), so the text, `status.json` and `digest` can never disagree with
`compile report --json`. A proposal approved but never fired has no row in the report; it is added with `metrics.compute` over the
same inputs, so a silent detector is visible. Nothing here reaches a model or the hook path (the shadow hook never imports it).
No session hash is printed: the queue shows eight characters of it. Imports: stdlib, `..atomic`, `..config`, `.measure`, `.ladder`, `.report` and neighbours,
and `.watch.summary` (the watcher's counts: a pure read, no network); `..health` and `.catalogue` load lazily (`__main__` must stay free of the scrub engine).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from ..atomic import write_private
from ..canon import now_iso
from ..config import Config
from . import approvals, chains, labels, ladder, measure, metrics, report
from .watch import summary as watch_summary

SCHEMA, WARN_DAYS, WEEK, QUEUE_MAX, COST_MS, MIN_LABELS, MIN_TP = 1, 14, 7, 50, 200, 5, 5
STAMP, WORD = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"), re.compile(r"[A-Za-z0-9_:.-]{1,40}")
FMT, OFFLINE_STATE = "%Y-%m-%dT%H:%M:%SZ", "offline_state.json"       # offline.SEEN, named here so this module does not import the pass
LIVE, RETIRED, INSUFFICIENT, COVERING = ("observe", "warn", "block"), ("disabled", "revoked"), "INSUFFICIENT", ("tp", "fp")
HEADS = ("proposal", "detector", "mode", "fires/eligible", "tp/fp/unlab", "precision", "verdict")


def parse_ts(ts: object) -> datetime | None:
    """A `YYYY-MM-DDTHH:MM:SSZ` stamp as a naive UTC datetime; None for anything else."""
    try:
        return datetime.strptime(ts, FMT) if isinstance(ts, str) and STAMP.fullmatch(ts) else None
    except ValueError:
        return None


def seconds_since(ts: object, now: str) -> int | None:
    """Whole seconds from a stamp to `now` (0 for a stamp in the future); None for a bad stamp."""
    then, here = parse_ts(ts), parse_ts(now)
    return None if then is None or here is None else max(0, int((here - then).total_seconds()))


def age(ts: object, now: str) -> str:
    """`5m`, `3h` (under 48 hours) or `2d` between a stamp and `now`; `never` for None or a bad stamp."""
    secs = seconds_since(ts, now)
    if secs is None:
        return "never"
    return f"{secs // 60}m" if secs < 3600 else f"{secs // 3600}h" if secs < 48 * 3600 else f"{secs // 86400}d"


def _tok(value: object) -> str:
    return value if isinstance(value, str) and WORD.fullmatch(value) else "-"


def _stamp(value: object) -> str:
    return value if isinstance(value, str) and STAMP.fullmatch(value) else "-"


def _chain(broken: object) -> str:
    return "ok" if broken is None else f"broken:{broken}"


def fired(row: dict) -> bool:
    """A shadow row that is a fire (`metrics._fire`): everything but an explicit `would_fire: false`. The label queue uses the same test."""
    return row.get("would_fire") is not False


def retired(info: dict) -> bool:
    """A proposal that is out of play: its newest approvals row revokes it (`revoked`), or its mode or approved mode is disabled or revoked."""
    return bool(info.get("revoked")) or info.get("mode") in RETIRED or info.get("approved_mode") in RETIRED


def is_revoked(arows: list[dict], pid: str) -> bool:
    """True when the newest approvals row of `pid` is a revocation."""
    last = approvals.newest(arows, pid)
    return bool(last and last["mode"] == "revoked")


def _rung(mode: object) -> str:
    """The rung a live proposal stands on: its mode, or `observe` for a mode that is not live (no backed copy)."""
    return mode if mode in LIVE else "observe"


def verdict(p: dict, now: str) -> str:
    """`_verdict`, plus `; k auto FPs (label --tp <oldest id> overturns)` when fires in the window carry only an automatic fp (not a
    retired proposal): the ladder counts them as FPs, the queue does not list them, one human `tp` on the row overturns each."""
    text, k = _verdict(p, now), p.get("auto_fp", 0)
    return f"{text}; {k} auto FPs (label --tp {p['auto_fp_oldest']} overturns)" if k and text != "retired" else text


def _verdict(p: dict, now: str) -> str:
    """The first rule that holds, never looser than the ladder (`ladder.check`, design 7.3). On the rung `m` (observe, warn or block) the
    next rung `n` is warn from observe and block from warn: retired, label N fires (`to_label`), silent, `not backed` (no copy, a broken
    approvals chain, a proposal file that differs from the copy, a newest row that does not back the copy, or one dated in the future),
    `demoted:<cap>` (a demotion caps the copy below `n`), `block (top rung)`, `n earliest DATE` (14 days from `window_start`, the resolved
    counting window), needs 5 human TPs, noisy, not eligible (any FP), then `m only: weak detector`, `m only: detector allows X` (catalogue
    ceiling below `n`), `warn only: block is for PreToolUse`, else `n eligible`. `tp_window`/`fp_window`: the ladder's counts in mode `m`."""
    if retired(p):
        return "retired"
    if p["to_label"] > 0:
        return f"label {p['to_label']} fires"
    if not p["eligible"]:
        return "silent: eligible 0"
    m, cap, n = p["mode"], p.get("cap"), measure.NEXT.get(p["mode"])
    if m not in LIVE:
        return "not backed: no approved copy"
    if p["backing"] == "chain":                                                 # ladder.run refuses a broken chain: name it, not a row
        return measure.not_backed("chain", "")
    if (p["approved_at"] or "") > now:                                          # ladder.check: newest approvals row is dated in the future
        return "not backed: newest approvals row is dated in the future"
    if n and cap and ladder.RANK[cap] < ladder.RANK[n]:                         # ladder.check: demoted:<cap>
        return f"demoted:{cap} ({p['cap_note']})"
    if p["backing"] != "ok":                                                    # ladder.check: copy not backed by the approvals chain
        return measure.not_backed(p["backing"], p["approvals_mode"])
    if n is None:
        return "block (top rung)"
    start, here, tp, fp = parse_ts(p["window_start"]), parse_ts(now), p["tp_window"], p["fp_window"]
    if start and here and here - start < timedelta(days=WARN_DAYS):
        return f"{n} earliest " + (start + timedelta(days=WARN_DAYS)).strftime("%Y-%m-%d")
    if tp < MIN_TP:
        return f"needs {MIN_TP} human TPs (has {tp})"
    if tp + fp >= MIN_LABELS and 2 * fp > tp + fp:
        return "noisy: add an exception or retire"
    if fp > 0:
        return f"not eligible: {fp} FPs ({n} needs 0)"
    if p["strength"] != "strong":
        return f"{m} only: weak detector"
    if ladder.RANK.get(p["compilable"], 0) < ladder.RANK[n]:
        return f"{m} only: detector allows {p['compilable']}"
    return "warn only: block is for PreToolUse" if n == "block" and p["event"] != "PreToolUse" else f"{n} eligible"


def _proposal(p: dict, rate: dict, last: dict | None, start: str | None, win: tuple[int, int], now: str, event: str = "-", to_label: int = 0,
              auto: tuple[str, ...] = (), cap: tuple[str | None, str] = (None, ""), backing: str = "ok") -> dict:
    """One `proposals` entry: the report's counts (`p`) and rates, the newest approvals row, the resolved `window_start`, the ladder's
    (tp, fp) since it in the current mode, `to_label` (see `_uncovered`), the demotion `cap` in force and its `cap_note` remedy, the newest approvals row's mode
    (`approvals_mode`), the `next_rung` and its earliest date, and the proposal's `event` and the catalogue's `compilable` ceiling (the
    next rung's checks)."""
    from . import catalogue
    n, at, cat = p["tp"] + p["fp"], last["ts"] if last else None, catalogue.CATALOGUE.get(p["detector"])
    nxt = measure.NEXT.get(_rung(p["mode"]))
    row = {"detector": p["detector"], "strength": getattr(cat, "strength", None), "compilable": getattr(cat, "compilable", "none"), "event": event,
           "mode": p["mode"], "approved_mode": p["approved_mode"], "cap": cap[0], "cap_note": cap[1],
           "backing": backing, "approvals_mode": last["mode"] if last else "none",
           "revoked": bool(last and last["mode"] == "revoked"), "approved_at": at,
           "window_start": start, "rows": p["rows"], "fires": rate.get("fires"), "tp": p["tp"], "fp": p["fp"], "tp_window": win[0],
           "fp_window": win[1], "unlabelled": p["unlabelled"], "to_label": to_label, "auto_fp": len(auto),
           "auto_fp_oldest": auto[0] if auto else None, "eligible": rate.get("eligible"), "fires_per_eligible": rate.get("fires_per_eligible"),
           "precision": metrics.wilson(p["tp"], n) if n >= MIN_LABELS else INSUFFICIENT,
           "fp_upper_95": metrics.upper95_zero(n) if p["fp"] == 0 and n >= 1 else None,
           "next_rung": nxt, "next_earliest": (parse_ts(start) + timedelta(days=WARN_DAYS)).strftime(FMT) if nxt and parse_ts(start) else None}
    row["verdict"] = verdict(row, now)
    return row


def _uncovered(rows: list[dict], labs: dict) -> Iterator[dict]:
    """Every fired row that no human tp/fp covers (a `tp_candidate` covers nothing), oldest first, one per row id: the label queue before
    retired proposals are set aside."""
    seen = set()
    for r in sorted(rows, key=lambda r: r["ts"]):
        rid = labels.row_id(r)
        if fired(r) and labs.get(rid) not in COVERING and rid not in seen:
            seen.add(rid)
            yield r


def _queue(rows: list[dict], labs: dict, props: dict) -> tuple[list[dict], int]:
    """(entries, hidden): the `_uncovered` rows, oldest first; the rows of retired proposals are counted in `hidden`, not listed."""
    out, hidden = [], 0
    for r in _uncovered(rows, labs):
        info = props.get(r["proposal_id"])
        if info and retired(info):
            hidden += 1
            continue
        out.append({"id": labels.row_id(r), "ts": _stamp(r["ts"]), "proposal_id": _tok(r["proposal_id"]), "detector": info["detector"] if info else "-",
                    "session8": _tok(r["session"][:8]), "event": _tok(r.get("event")), "cause": _tok(r.get("cause"))})
    return out, hidden


def _mtime(path: Path) -> str | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return time.strftime(FMT, time.gmtime(info.st_mtime)) if stat.S_ISREG(info.st_mode) else None


def _heartbeat(cfg: Config, rows: list[dict], notes: list[str]) -> dict:
    from .. import health                                          # lazy: health pulls the scrub engine, which `__main__` must not load
    stamps = lambda rs: max((r["ts"] for r in rs if STAMP.fullmatch(r["ts"])), default=None)  # noqa: E731
    rec = health.read(cfg) or {}
    ok = rec.get("ok") is True and _stamp(rec.get("last_run")) != "-"
    return {"last_hook_row": stamps(rows), "last_live_fire": stamps([r for r in rows if fired(r)]),
            "last_offline_scan": _mtime(cfg.path(OFFLINE_STATE)), "last_nightly_ok": rec["last_run"] if ok else None,
            "notes": [*notes, *(["nightly:failed"] if rec.get("ok") is False else [])]}


def build(cfg: Config, days: int = 14, now: str | None = None) -> dict:
    """status.json as a dict; raises labels.Refusal like `report.collect`."""
    now = now or now_iso()
    d, ring = report.collect(cfg, days, now), chains.key_of(cfg)
    chain4 = report._chains(cfg)
    (arows, abroken), dem_broken, lab_broken = chain4[:2], d["demotions"]["broken_at"], d["labels"]["broken_at"]
    rows, labs = report.read_corpora(cfg)[0], report.label_map(cfg)[0]
    lab_rows = labels.read_chain(cfg)[0] if lab_broken is None else []
    counts, rates = d["private"]["proposals"], d["rates"]["private"]
    extra_counts, extra_rates = measure.unfired(cfg, d, rows, now)
    counts, per = {**counts, **extra_counts}, {**rates["proposals"], **extra_rates}
    fires, eff, props, owed = {}, ladder.effective(lab_rows), {}, Counter(r["proposal_id"] for r in _uncovered(rows, labs))
    for r in rows:
        fires.setdefault(labels.row_id(r), r)
    for pid, p in sorted(counts.items()):
        res, backing = measure.resolve(cfg, pid, chain4, now)
        start = (res.window_start or None) if res else None
        where = (pid, start, now, fires, eff, _rung(p["mode"]))
        props[pid] = _proposal(p, per.get(pid, {}), approvals.newest(arows, pid), start, measure.window_counts(*where), now,
                               report._meta(cfg, pid)[1], owed[pid], measure.auto_fps(*where), measure.cap_of(res), backing)
    start, here = measure.start_of(arows), parse_ts(now)
    t0, corpus = parse_ts(start), rates["corpus"]
    notes = [*chains.notes(ring, (abroken, dem_broken)),
             *(f"{name}:broken:{b}" for name, b in (("approvals", abroken), ("demotions", dem_broken), ("labels", lab_broken)) if b is not None),
             *(f"silent:{p['detector']}" for p in props.values() if p["mode"] in LIVE and p["eligible"] and p["fires"] == 0 and p["rows"] == 0)]
    queue, hidden = _queue(rows, labs, props)
    live = corpus["agreement"] if isinstance(corpus["agreement"], dict) else {"live_only": None, "offline_only": None}
    return {"schema": SCHEMA, "generated_at": now, "report_sha256": d["report_sha256"], "days": days,
            "measurement": {"start": start, "day": (here.date() - t0.date()).days + 1 if t0 and here else None,
                            "week1_due": (t0 + timedelta(days=WEEK)).strftime(FMT) if t0 else None, "keyed": ring is not None,
                            "chains": {"approvals": _chain(abroken), "demotions": _chain(dem_broken), "labels": _chain(lab_broken)}},
            "heartbeat": _heartbeat(cfg, rows, notes), "proposals": props, "queue": queue[:QUEUE_MAX], "queue_total": len(queue),
            "queue_retired": hidden, "per_day": d["private"]["per_day"],
            "cost": {"p50_ms": corpus["cost_p50_ms"], "p95_ms": corpus["cost_p95_ms"], "rows": corpus["cost_rows"], "threshold_ms": COST_MS},
            "agreement": {**live, "awaiting_scan": corpus["awaiting_offline_scan"]}, "demotions": d["demotions"]["rows"],
            **watch_summary.fields(cfg, now)}


def _precision(v: object) -> str:
    return v if isinstance(v, str) else "[{},{}]".format(*v)


def table(s: dict) -> list[str]:
    """The per-proposal grid (header first) of the screen and of the digest."""
    body = [[pid, p["detector"], p["mode"], f"{'-' if p['fires'] is None else p['fires']}/{'-' if p['eligible'] is None else p['eligible']}",
             f"{p['tp']}/{p['fp']}/{p['unlabelled']}", _precision(p["precision"]), p["verdict"]] for pid, p in s["proposals"].items()]
    return report._grid(HEADS, body)


def render(s: dict) -> str:
    """The text screen: every figure in it is in `s`."""
    m, h, now, c = s["measurement"], s["heartbeat"], s["generated_at"], s["cost"]
    head = (f"ace status  day {'-' if m['day'] is None else m['day']} of {WEEK}  keyed {'yes' if m['keyed'] else 'no'}  "
            f"chains {'ok' if all(v == 'ok' for v in m['chains'].values()) else 'BROKEN'}  report {s['report_sha256'][:8]}")
    oldest = f" (oldest {age(s['queue'][0]['ts'], now)})" if s["queue"] else ""
    oldest += f", {s['queue_retired']} more on retired proposals" if s.get("queue_retired") else ""
    nightly = age(h["last_nightly_ok"], now)
    lines = [head, *table(s), "", f"queue: {s['queue_total']} unlabelled{oldest}",
             f"heartbeat  hook {age(h['last_hook_row'], now)}  live fire {age(h['last_live_fire'], now)}  "
             f"offline scan {age(h['last_offline_scan'], now)}  nightly ok {nightly}",
             f"cost p95 {c['p95_ms']} ms vs {c['threshold_ms']}",
             "agreement  " + "  ".join(f"{k.replace('_', ' ')} {'-' if v is None else v}" for k, v in s["agreement"].items()),
             "notes: " + (", ".join(h["notes"]) or "none")]
    return "\n".join(lines) + "\n"


def write_json(cfg: Config, s: dict, out: str | None = None) -> None:
    """Write status dict `s` as `status.json` (0600, atomic) to `out` or the data dir; OSError when it cannot."""
    path = Path(out) if out else cfg.path("status.json")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(path, json.dumps(s, indent=1, sort_keys=True) + "\n")


def add_parser(sub) -> None:
    """Register `status` on the CLI's subparsers."""
    p = sub.add_parser("status", help="the operator's one screen and status.json (read-only; built from report --json)")
    p.add_argument("--days", type=int, default=14, help="days in the report window (default 14, 1 to 3660)")
    p.add_argument("--json", action="store_true", help="print status.json instead of the text screen")
    p.add_argument("--out", metavar="PATH", help="where to write status.json (default: status.json in the data dir; 0600)")
    p.set_defaults(run=lambda c, a: run(c, a.days, a.json, a.out))


def run(cfg: Config, days: int, as_json: bool = False, out: str | None = None) -> int:
    """Entry of `compile status`: build, write status.json (0600, atomic; `out` or `<data_dir>/status.json`), print the screen or the JSON."""
    try:
        s = build(cfg, days)
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    try:
        write_json(cfg, s, out)
    except OSError as exc:                                          # type only: an errno message would carry a path
        print(f"error: status.json not written ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(json.dumps(s, indent=1, sort_keys=True)) if as_json else print(render(s), end="")
    return 0
