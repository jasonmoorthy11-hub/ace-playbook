"""`compile status` (docs/design/compiler.md 8.x): the operator's one screen, and `status.json` behind it.

`build` is `report.collect` plus the chains and the clock: every count and rate comes from `report` and `metrics` (precision is
`metrics.wilson`; this module does no other rate arithmetic), so the text, `status.json` and `digest` can never disagree with
`compile report --json`. A proposal approved but never fired has no row in the report; it is added with `metrics.compute` over the
same inputs, so a silent detector is visible. Nothing here reaches a model or the hook path (the shadow hook never imports it).
No session hash is printed: the queue shows eight characters of it. Imports: stdlib, `..atomic`, `..config`, `.report` and its neighbours;
`..health` and `.catalogue` load lazily (`__main__` imports this module and must stay free of the scrub engine).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..atomic import write_private
from ..canon import now_iso
from ..config import Config
from . import approvals, chains, labels, metrics, modes, report

SCHEMA, WARN_DAYS, WEEK, QUEUE_MAX, COST_MS, MIN_LABELS, MIN_TP = 1, 14, 7, 50, 200, 5, 5
STAMP, WORD = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"), re.compile(r"[A-Za-z0-9_:.-]{1,40}")
FMT, OFFLINE_STATE = "%Y-%m-%dT%H:%M:%SZ", "offline_state.json"       # offline.SEEN, named here so this module does not import the pass
LIVE, RETIRED, INSUFFICIENT = ("observe", "warn", "block"), ("disabled", "revoked"), "INSUFFICIENT"
HEADS = ("proposal", "detector", "mode", "fires/eligible", "tp/fp/unlab", "precision", "verdict")


def parse_ts(ts: object) -> datetime | None:
    """A `YYYY-MM-DDTHH:MM:SSZ` stamp as a naive UTC datetime; None for anything else."""
    try:
        return datetime.strptime(ts, FMT) if isinstance(ts, str) and STAMP.fullmatch(ts) else None
    except ValueError:
        return None


def age(ts: object, now: str) -> str:
    """`5m`, `3h` (under 48 hours) or `2d` between a stamp and `now`; `never` for None or a bad stamp."""
    then, here = parse_ts(ts), parse_ts(now)
    if then is None or here is None:
        return "never"
    secs = max(0, int((here - then).total_seconds()))
    return f"{secs // 60}m" if secs < 3600 else f"{secs // 3600}h" if secs < 48 * 3600 else f"{secs // 86400}d"


def _tok(value: object) -> str:
    return value if isinstance(value, str) and WORD.fullmatch(value) else "-"


def _stamp(value: object) -> str:
    return value if isinstance(value, str) and STAMP.fullmatch(value) else "-"


def _chain(broken: object) -> str:
    return "ok" if broken is None else f"broken:{broken}"


def verdict(p: dict, now: str, revoked: bool = False) -> str:
    """The first rule that holds: retired, label N fires, silent, warn earliest DATE, needs 5 human TPs, noisy, else warn eligible.
    `p` needs mode, approved_mode, unlabelled, eligible, approved_at, tp, fp; `revoked` is the newest approvals row being a revocation."""
    if revoked or p["mode"] in RETIRED or p["approved_mode"] in RETIRED:
        return "retired"
    if p["unlabelled"] > 0:
        return f"label {p['unlabelled']} fires"
    if not p["eligible"]:
        return "silent: eligible 0"
    at, here = parse_ts(p["approved_at"]), parse_ts(now)
    if at and here and here - at < timedelta(days=WARN_DAYS):
        return "warn earliest " + (at + timedelta(days=WARN_DAYS)).strftime("%Y-%m-%d")
    if p["tp"] < MIN_TP:
        return f"needs {MIN_TP} human TPs (has {p['tp']})"
    if p["tp"] + p["fp"] >= MIN_LABELS and 2 * p["fp"] > p["tp"] + p["fp"]:
        return "noisy: add an exception or retire"
    return "warn eligible"


def _proposal(p: dict, rate: dict, last: dict | None, now: str) -> dict:
    """One `proposals` entry from the report's counts (`p`), its rates and the proposal's newest approvals row."""
    from . import catalogue
    n, at = p["tp"] + p["fp"], last["ts"] if last else None
    row = {"detector": p["detector"], "strength": getattr(catalogue.CATALOGUE.get(p["detector"]), "strength", None),
           "mode": p["mode"], "approved_mode": p["approved_mode"], "approved_at": at, "rows": p["rows"],
           "fires": rate.get("fires"), "tp": p["tp"], "fp": p["fp"], "unlabelled": p["unlabelled"], "eligible": rate.get("eligible"),
           "fires_per_eligible": rate.get("fires_per_eligible"),
           "precision": metrics.wilson(p["tp"], n) if n >= MIN_LABELS else INSUFFICIENT,
           "fp_upper_95": round(1 - 0.05 ** (1 / n), 3) if p["fp"] == 0 and n >= 1 else None,
           "warn_earliest": (parse_ts(at) + timedelta(days=WARN_DAYS)).strftime(FMT) if parse_ts(at) else None}
    row["verdict"] = verdict(row, now, bool(last and last["mode"] == "revoked"))
    return row


def _unfired(cfg: Config, d: dict, rows: list[dict], now: str) -> tuple[dict, dict]:
    """(counts, rates) of approved copies with no shadow row, which `report.collect` leaves out; `metrics.compute` as in `collect`."""
    from . import shadow
    have, ring = set(d["private"]["proposals"]), report._chains(cfg)
    meta = {rec["id"]: report._meta(cfg, rec["id"], ring, now) for _, _, rec in shadow.approved_records(cfg) if rec["id"] not in have}
    known = {pid: m[0] for pid, m in meta.items() if m[0] != "-"}
    counts = {pid: {"detector": m[0], "approved_mode": m[2], "mode": m[3], "rows": 0, "tp": 0, "fp": 0, "unlabelled": 0}
              for pid, m in meta.items()}
    rates = metrics.compute("private", rows, known, report._read(cfg.path(report.EVENTS)), report._read(cfg.path(report.EVASION)),
                            {pid for pid, m in meta.items() if m[1] == "Stop"})["proposals"] if known else {}
    return counts, rates


def _queue(rows: list[dict], labs: dict, detectors: dict) -> list[dict]:
    """Every fired row whose id has no effective label, oldest first, one entry per row id."""
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["ts"]):
        rid = labels.row_id(r)
        if r.get("would_fire") is False or rid in labs or rid in seen:
            continue
        seen.add(rid)
        out.append({"id": rid, "ts": _stamp(r["ts"]), "proposal_id": _tok(r["proposal_id"]), "detector": detectors.get(r["proposal_id"], "-"),
                    "session8": _tok(r["session"][:8]), "event": _tok(r.get("event")), "cause": _tok(r.get("cause"))})
    return out


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
    return {"last_hook_row": stamps(rows), "last_live_fire": stamps([r for r in rows if r.get("would_fire") is not False]),
            "last_offline_scan": _mtime(cfg.path(OFFLINE_STATE)), "last_nightly_ok": rec["last_run"] if ok else None,
            "notes": [*notes, *(["nightly:failed"] if rec.get("ok") is False else [])]}


def build(cfg: Config, days: int = 14, now: str | None = None) -> dict:
    """status.json as a dict; raises labels.Refusal like `report.collect`."""
    now = now or now_iso()
    d, ring = report.collect(cfg, days, now), chains.key_of(cfg)
    arows, abroken = modes.read_approvals(cfg.data_dir, ring)
    rows, labs = report.read_corpora(cfg)[0], report.label_map(cfg)[0]
    dem_broken, lab_broken = d["demotions"]["broken_at"], d["labels"]["broken_at"]
    counts, rates = d["private"]["proposals"], d["rates"]["private"]
    extra_counts, extra_rates = _unfired(cfg, d, rows, now)
    counts, per = {**counts, **extra_counts}, {**rates["proposals"], **extra_rates}
    props = {pid: _proposal(p, per.get(pid, {}), approvals.newest(arows, pid), now) for pid, p in sorted(counts.items())}
    starts = [r["ts"] for r in arows if r["mode"] in LIVE]
    start, here = (starts[-1] if starts else None), parse_ts(now)
    t0 = parse_ts(start)
    notes = [*chains.notes(ring, (abroken, dem_broken)),
             *(f"{name}:broken:{b}" for name, b in (("approvals", abroken), ("demotions", dem_broken), ("labels", lab_broken)) if b is not None),
             *(f"silent:{p['detector']}" for p in props.values() if p["mode"] in LIVE and p["eligible"] and p["fires"] == 0)]
    queue, corpus = _queue(rows, labs, {pid: p["detector"] for pid, p in props.items()}), rates["corpus"]
    return {"schema": SCHEMA, "generated_at": now, "report_sha256": d["report_sha256"], "days": days,
            "measurement": {"start": start, "day": (here.date() - t0.date()).days + 1 if t0 and here else None,
                            "week1_due": (t0 + timedelta(days=WEEK)).strftime(FMT) if t0 else None, "keyed": ring is not None,
                            "chains": {"approvals": _chain(abroken), "demotions": _chain(dem_broken), "labels": _chain(lab_broken)}},
            "heartbeat": _heartbeat(cfg, rows, notes), "proposals": props, "queue": queue[:QUEUE_MAX], "queue_total": len(queue),
            "cost": {"p50_ms": corpus["cost_p50_ms"], "p95_ms": corpus["cost_p95_ms"], "rows": corpus["cost_rows"], "threshold_ms": COST_MS},
            "agreement": corpus["agreement"], "demotions": d["demotions"]["rows"]}


def _precision(v: object) -> str:
    return v if isinstance(v, str) else "[{},{}]".format(*v)


def render(s: dict) -> str:
    """The text screen: every figure in it is in `s`."""
    m, h, now, c = s["measurement"], s["heartbeat"], s["generated_at"], s["cost"]
    head = (f"ace status  day {'-' if m['day'] is None else m['day']} of {WEEK}  keyed {'yes' if m['keyed'] else 'no'}  "
            f"chains {'ok' if all(v == 'ok' for v in m['chains'].values()) else 'BROKEN'}  report {s['report_sha256'][:8]}")
    body = [[pid, p["detector"], p["mode"], f"{'-' if p['fires'] is None else p['fires']}/{'-' if p['eligible'] is None else p['eligible']}",
             f"{p['tp']}/{p['fp']}/{p['unlabelled']}", _precision(p["precision"]), p["verdict"]] for pid, p in s["proposals"].items()]
    oldest = f" (oldest {age(s['queue'][0]['ts'], now)})" if s["queue"] else ""
    nightly = age(h["last_nightly_ok"], now)
    lines = [head, *report._grid(HEADS, body), "", f"queue: {s['queue_total']} unlabelled{oldest}",
             f"heartbeat  hook {age(h['last_hook_row'], now)}  live fire {age(h['last_live_fire'], now)}  "
             f"offline scan {age(h['last_offline_scan'], now)}  nightly ok {nightly}",
             f"cost p95 {c['p95_ms']} ms vs {c['threshold_ms']}", "notes: " + (", ".join(h["notes"]) or "none")]
    return "\n".join(lines) + "\n"


def add_parser(sub) -> None:
    """Register `status` on the CLI's subparsers."""
    p = sub.add_parser("status", help="the operator's one screen and status.json (read-only; built from report --json)")
    p.add_argument("--days", type=int, default=14, help="days in the report window (default 14, 1 to 3660)")
    p.add_argument("--json", action="store_true", help="print status.json instead of the text screen")
    p.add_argument("--out", metavar="PATH", help="where to write status.json (default: status.json in the data dir; 0600)")


def run(cfg: Config, days: int, as_json: bool = False, out: str | None = None) -> int:
    """Entry of `compile status`: build, write status.json (0600, atomic; `out` or `<data_dir>/status.json`), print the screen or the JSON."""
    try:
        s = build(cfg, days)
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    text, path = json.dumps(s, indent=1, sort_keys=True), Path(out) if out else cfg.path("status.json")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(path, text + "\n")
    except OSError as exc:                                          # type only: an errno message would carry a path
        print(f"error: status.json not written ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(text) if as_json else print(render(s), end="")
    return 0
