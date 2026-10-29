"""`compile report` (docs/design/compiler.md 8): per-proposal counts from shadow rows and labels, and (step 5c, metrics.py) rates per corpus.

Two corpora, two tables, never summed: the private `shadow.jsonl` (written by the live hook) and the optional
`shadow.synthetic.jsonl` (every row says `corpus: synthetic`). A row in the wrong file refuses the whole report.
tp/fp come from `labels.jsonl` through `labels.effective` (a human row beats an auto one, the newest row of its kind
wins, `incident` rows ignored, a broken chain ignores every label): fp counts human and auto rows, tp only human
ones, `tp_cand` the auto `tp_candidate` rows, which are never TPs. `approved_mode` is the copy's own mode, `mode` what
`modes.resolve` says the hook runs. A `Demotions` section lists every row of `demotions.jsonl` with `lifted` yes/no (a
strictly later approval lifts it; the lowest row not lifted is the cap; a broken chain is one visible line). No session hash, row id, payload hash or path is printed.
Per corpus a `Rates` table (eligible sessions, fire rate with a Wilson 95% interval, repeat fires, friction, evasion) and a `Hook cost / agreement` block come
from `metrics.compute` over the shadow rows and the offline files `detector_events.jsonl` and `evasion_candidates.jsonl`.
Both shadow files are read here through a no-follow, regular-file-only reader. Imports: stdlib, `..canon`,
`..config`, `.labels`, `.metrics`; `catalogue`, `proposal` and `shadow` load lazily (hook path).
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from ..canon import now_iso
from ..config import Config
from . import chains, demotions, labels, metrics, modes

PRIVATE, SYNTHETIC = "shadow.jsonl", "shadow.synthetic.jsonl"
EVENTS, EVASION = "detector_events.jsonl", "evasion_candidates.jsonl"      # offline.py writes them
KEYS = ("ts", "session", "proposal_id", "payload_sha256")
PID, LID = re.compile(r"P-[0-9a-f]{8}"), re.compile(r"L-[0-9a-f]{8}")
MAX_DAYS = 3660
FIELDS = ("lesson", "detector", "event", "approved_mode", "mode", "rows", "cause_detector", "cause_truncated", "sessions",
          "days", "tp", "fp", "tp_cand", "unlabelled", "first", "last")
HEADS = ("proposal", "lesson", "detector", "event", "approved_mode", "mode", "rows", "detector", "truncated", "sessions",
         "days", "tp", "fp", "tp_cand", "unlabelled", "first", "last")
RATE_HEADS = ("proposal", "detector", "eligible", "fire_sessions", "fire_rate", "wilson95", "fires", "fires/eligible", "repeat_fires",
              "msgs/100calls", "corrections/100prompts", "evasion")
NOT_COMPUTED = "not computed here: arms, randomization inference, spillover flag (they need the randomized phase, design 8)"


HASHED = tuple(sorted((Path(__file__).with_name(n) for n in ("arms.py", "arms_stats.py", "labels.py", "labels_queue.py", "metrics.py", "report.py")),
                      key=lambda p: p.name))      # the rates (metrics, report), the labels they use (labels, labels_queue) and the arm analysis (arms*)


def source_sha256() -> str:
    """sha256 of the bytes of the HASHED files (arms.py, arms_stats.py, labels.py, labels_queue.py, metrics.py, report.py: name order),
    joined by a NUL: the design commits the report code hash before observe starts."""
    return hashlib.sha256(b"\0".join(p.read_bytes() for p in HASHED)).hexdigest()


def _match(value: object, rx: re.Pattern) -> str:
    return value if isinstance(value, str) and rx.fullmatch(value) else "-"


def _pick(value: object, allowed: object) -> str:
    """`value` only when it is one of the known names, else `-` (a hand-edited file prints nothing of its own)."""
    return value if isinstance(value, str) and value in allowed else "-"


def _day(ts: str) -> str | None:
    try:
        return date.fromisoformat(ts[:10]).isoformat() if len(ts) >= 10 else None
    except ValueError:
        return None


def _read(path: Path) -> list[dict]:
    """JSONL objects of a regular file (no symlink, FIFO or directory: exit 2); a missing file is no rows."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise labels.Refusal(f"{path.name} is a symlink or not a regular file", 2) from None
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise labels.Refusal(f"{path.name} is a symlink or not a regular file", 2)
    with os.fdopen(fd, "rb") as fh:
        text = fh.read().decode()                                   # strict: bad bytes are a ValueError, exit 2
    recs = []
    for line in text.splitlines():
        with contextlib.suppress(ValueError):
            recs.append(json.loads(line))
    return [r for r in recs if isinstance(r, dict)]


def read_corpora(cfg: Config) -> tuple[list[dict], list[dict]]:
    """(private rows, synthetic rows). The corpus guard runs on every raw object, before the key filter."""
    private, synthetic = _read(cfg.path(PRIVATE)), _read(cfg.path(SYNTHETIC))
    if any(r.get("corpus", "private") != "private" or r.get("tier") == "synthetic" for r in private):
        raise labels.Refusal("row with a non-private corpus in shadow.jsonl", 2)
    if any(r.get("corpus") != "synthetic" for r in synthetic):
        raise labels.Refusal("non-synthetic row found in the synthetic corpus", 2)
    full = lambda rows: [r for r in rows if all(isinstance(r.get(k), str) for k in KEYS)]  # noqa: E731
    return full(private), full(synthetic)


def label_map(cfg: Config) -> tuple[dict[str, str], int | None]:
    """({row_id: tp|fp|tp_candidate}, broken_at): `labels.effective`; a broken chain gives no labels at all."""
    rows, broken = labels.read_chain(cfg)
    return ({}, broken) if broken is not None else (labels.effective(rows), None)


def hook_errors(cfg: Config, rows: list[dict], since: str) -> tuple[int, int, bool]:
    """(`error:` lines of shadow.log stamped at or after `since`, shadow rows stamped at or after it, log full). A log at the
    hook's size cap has stopped recording errors, so its error rate cannot be trusted."""
    from . import shadow
    raw = demotions.read_regular(cfg.path("shadow.log"), 2 * shadow.LOG_CAP) or b""
    errors = sum(ln[:20] >= since and ln[21:].startswith("error:") for ln in raw.decode("utf-8", "replace").splitlines())
    return errors, sum(r["ts"] >= since for r in rows), len(raw) >= shadow.LOG_CAP


def lesson_statuses(cfg: Config) -> tuple[dict | None, str]:
    """({lesson id: status}, "ok"); (None, "skipped (no store)") without deltas.jsonl; (None, "chain invalid") when the log
    fails verification (no lesson check is then made)."""
    if not cfg.deltas.exists():
        return None, "skipped (no store)"
    from ..store import DeltaLog
    log = DeltaLog(cfg.deltas)
    try:
        log.verify()
        return {k: v.get("status") for k, v in log.state().items()}, "ok"
    except ValueError:                                            # includes store.schema.SchemaError
        return None, "chain invalid"


def demotion_summary(cfg: Config, now: str | None = None) -> dict:
    """{"chain_ok", "broken_at", "proposals": {pid: {cap, cause, ts, review_required, rows, lifted_rows}}, "rows": [...]}: a row
    is `lifted` when the proposal's newest approvals row (at or before `now`) is stamped strictly after it; `cap` is the lowest row still in
    force (`-` when all are lifted) with its cause and time, `review_required` is true when any row of the proposal asked."""
    try:
        rows, broken = demotions.read_chain(cfg.data_dir, chains.key_of(cfg))
    except OSError:
        raise labels.Refusal("demotions.jsonl is a symlink or not a regular file", 2) from None
    (arows, abroken), now = modes.read_approvals(cfg.data_dir, chains.key_of(cfg)), now or now_iso()
    props: dict[str, dict] = {}
    listed = []
    for r in rows:
        done = modes.lifted(r, modes.anchor(arows, r["proposal_id"], now, abroken))
        listed.append({**{k: r[k] for k in ("proposal_id", "to_mode", "cause", "ts", "review_required")}, "lifted": done})
        old = props.setdefault(r["proposal_id"], {"cap": "-", "cause": r["cause"], "ts": r["ts"], "review_required": False,
                                                  "rows": 0, "lifted_rows": 0})
        old.update(rows=old["rows"] + 1, lifted_rows=old["lifted_rows"] + done, review_required=old["review_required"] or r["review_required"])
        if not done and (old["cap"] == "-" or demotions.LADDER.index(r["to_mode"]) <= demotions.LADDER.index(old["cap"])):
            old.update(cap=r["to_mode"], cause=r["cause"], ts=r["ts"])          # the newest of the lowest rows in force
        elif old["cap"] == "-":
            old.update(cause=r["cause"], ts=r["ts"])                            # all lifted so far: the newest row
    return {"chain_ok": broken is None, "broken_at": broken, "proposals": props, "rows": listed}


def _chains(cfg: Config) -> tuple:
    """(approvals rows, break, demotion rows, break) for `modes.resolve`; a read error is `unreadable`, never raised."""
    return (*modes.read_approvals(cfg.data_dir, ring := chains.key_of(cfg)), *modes.read_demotions(cfg.data_dir, ring))


def _meta(cfg: Config, pid: str, chains: tuple | None = None, now: str = "~") -> tuple[str, str, str, str]:
    """(detector, event, approved mode, resolved mode) from the proposal files and `chains` (see `_chains`); `-` for anything
    missing, broken, unsafe or unknown (the resolved mode is `-` without `chains`)."""
    from . import catalogue, proposal, shadow
    det = ev = mode = res = "-"
    if not PID.fullmatch(pid):
        return det, ev, mode, res
    try:
        p = proposal.load(proposal.proposal_path(cfg, pid))
        det = _pick(p["check"]["detector"], catalogue.CATALOGUE)
        ev = _pick(p["event"], shadow.EVENTS.values())
    except (ValueError, OSError, KeyError, TypeError):
        pass
    try:
        rec = proposal.load(shadow.approved_path(cfg, pid))
        body, approved = shadow.pending_body(rec)
        mode = _pick(approved, shadow.MODES) if body is not None else "-"
        res = modes.resolve(pid, rec, *chains, now).mode if chains and body is not None else "-"
    except (ValueError, OSError):
        pass
    return det, ev, mode, res


def summarize(cfg: Config, rows: list[dict], labs: dict[str, str], now: str, days: int, synthetic: bool = False) -> dict:
    """{"proposals": {pid: {FIELDS}}, "per_day": [{date, proposal_id, rows}]} for one corpus. A synthetic id is a stand-in: it is never
    looked up in the private proposals directory, its detector and event come from the rows."""
    from . import catalogue
    shadow_events = ("PreToolUse", "Stop")
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["proposal_id"] if PID.fullmatch(r["proposal_id"]) else "invalid", []).append(r)
    today = date.fromisoformat(now[:10])
    cutoff = today - timedelta(days=days - 1)
    props, per_day, chains = {}, Counter(), _chains(cfg)
    for pid in sorted(by):
        rs = by[pid]
        dates = [d for d in (_day(r["ts"]) for r in rs) if d]
        ids = {labels.row_id(r) for r in rs}                         # rows sharing an id collapse for labelling
        marks, causes = Counter(labs.get(i) for i in ids), Counter(r.get("cause") for r in rs)
        det, ev, mode, res = ("-",) * 4 if synthetic else _meta(cfg, pid, chains, now)
        det, ev = (_pick(rs[0].get("detector"), catalogue.CATALOGUE), _pick(rs[0].get("event"), shadow_events)) if synthetic else (det, ev)
        props[pid] = {"lesson": next((v for v in (_match(r.get("lesson_id"), LID) for r in rs) if v != "-"), "-"),
                      "detector": det, "event": ev, "approved_mode": mode, "mode": res, "rows": len(rs),
                      "cause_detector": causes["detector"], "cause_truncated": causes["truncated"],
                      "sessions": len({r["session"] for r in rs}), "days": len(set(dates)),
                      "tp": marks["tp"], "fp": marks["fp"], "tp_cand": marks["tp_candidate"],
                      "unlabelled": sum(labels.row_id(r) not in labs for r in rs),
                      "first": min(dates, default="-"), "last": max(dates, default="-")}
        per_day.update(Counter((d, pid) for d in dates if cutoff <= date.fromisoformat(d) <= today))
    return {"proposals": props,
            "per_day": [{"date": d, "proposal_id": p, "rows": n} for (d, p), n in sorted(per_day.items())]}


def _grid(head: tuple[str, ...], body: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in [list(head), *body]) for i in range(len(head))]
    return ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in [list(head), *body]]


def _rates(rates: dict, synthetic: bool) -> list[str]:
    body = [[pid, r["detector"], *map(str, (r["eligible"], r["sessions_with_fire"], r["fire_rate"])),
             r["wilson95"] if isinstance(r["wilson95"], str) else "[{},{}]".format(*r["wilson95"]),
             *map(str, (r["fires"], r["fires_per_eligible"], r["repeat_fires"], *r["friction"].values(), r["evasion"]))]
            for pid, r in rates["proposals"].items()]
    c, ag = rates["corpus"], rates["corpus"]["agreement"]
    return ["Rates (per eligible session; eligible = a session with an event the detector consumes)", *_grid(RATE_HEADS, body), "",
            "Hook cost / agreement (hook cost: " + ("the evaluator only, not a hook call" if synthetic else
                                                  "firing calls only, in-process: no interpreter start, imports or config load") + ")",
            f"dur_ms p50 {c['cost_p50_ms']}  p95 {c['cost_p95_ms']}  (rows with dur_ms: {c['cost_rows']})",
            f"live fires awaiting offline scan: {c['awaiting_offline_scan']} (in neither the numerator nor the denominator of any rate)",
            "live versus offline: " + (ag if isinstance(ag, str) else f"{ag['live_only']} live-only fires, {ag['offline_only']} offline-only events"), ""]


def _section(title: str, source: str, data: dict, days: int, rates: dict | None) -> list[str]:
    out = [title, f"source: {source}"]
    if not data["proposals"]:
        return [*out, "no rows", ""]
    out += _grid(HEADS, [[pid, *(str(p[f]) for f in FIELDS)] for pid, p in data["proposals"].items()])
    out += ["", *(_rates(rates, source == SYNTHETIC) if rates else []), f"Rows per day (last {days} days)"]
    body = [[d["date"], d["proposal_id"], str(d["rows"])] for d in data["per_day"]]
    out += _grid(("date", "proposal_id", "rows"), body) if body else ["no rows in window"]
    return [*out, ""]


def _demotions(dem: dict) -> list[str]:
    out = ["Demotions (demotions.jsonl, every row; lifted = a strictly later approval of the proposal lifted it)"]
    if not dem["chain_ok"]:
        out.append(f"demotions: chain BROKEN {chains.where(dem['broken_at'], 1)}, rows after it ignored (the hook caps every proposal at observe)")
    rows = [[r["proposal_id"], r["to_mode"], r["cause"], r["ts"], str(r["review_required"]).lower(), "yes" if r["lifted"] else "no"]
            for r in dem["rows"]]
    return [*out, *(_grid(("proposal", "to_mode", "cause", "ts", "review_required", "lifted"), rows) if rows else ["no demotions"]), ""]


def render(sha: str, days: int, private: dict, synthetic: dict, broken: int | None, dem: dict, rates: dict | None = None) -> str:
    lines = ["ace-playbook shadow report", f"report_sha256: {sha}", ""]
    lines += _section("Private corpus (aggregate, counts only)", PRIVATE, private, days, rates and rates["private"])
    lines += _section("Synthetic corpus (reproducible)", SYNTHETIC, synthetic, days, rates and rates["synthetic"])
    lines += _demotions(dem)
    lines.append(NOT_COMPUTED)
    if broken is not None:
        lines.append(f"labels: chain BROKEN at row {broken}, labels ignored")
    return "\n".join(lines) + "\n"


def collect(cfg: Config, days: int, now: str | None = None) -> dict:
    """Everything `compile report` prints, as the `--json` dict (also what `compile dashboard` renders); raises labels.Refusal."""
    if not 1 <= days <= MAX_DAYS:
        raise labels.Refusal(f"--days must be 1..{MAX_DAYS}", 2)
    now = now or now_iso()
    priv_rows, syn_rows = read_corpora(cfg)
    labs, broken = label_map(cfg)
    private = summarize(cfg, priv_rows, labs, now, days)
    synthetic = summarize(cfg, [r for r in syn_rows if r.get("would_fire") is not False], {}, now, days, True)   # labels: private only
    dem, (events, evasion) = demotion_summary(cfg, now), (_read(cfg.path(EVENTS)), _read(cfg.path(EVASION)))
    from . import catalogue
    syn_det = {r["proposal_id"]: r["detector"] for r in syn_rows if PID.fullmatch(r["proposal_id"]) and r.get("detector") in catalogue.CATALOGUE}
    stop = {k for k, p in private["proposals"].items() if p["event"] == "Stop"}
    known = {k: p["detector"] for k, p in private["proposals"].items() if p["detector"] != "-"}       # no proposal file: no rate
    rates = {"private": metrics.compute("private", priv_rows, known, events, evasion, stop),
             "synthetic": metrics.compute("synthetic", syn_rows, syn_det, [], [])}
    return {"report_sha256": source_sha256(), "days": days, "private": private, "synthetic": synthetic, "rates": rates,
            "demotions": dem, "labels": {"chain_ok": broken is None, "broken_at": broken}}


def run(cfg: Config, days: int, as_json: bool = False, now: str | None = None) -> int:
    """Entry of `compile report`: exit 0 (also with no rows), 2 for usage, a corpus mix-up or an unsafe file."""
    try:
        d = collect(cfg, days, now)
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    if as_json:
        print(json.dumps(d, indent=1, sort_keys=True))
    else:
        print(render(d["report_sha256"], days, d["private"], d["synthetic"], d["labels"]["broken_at"], d["demotions"], d["rates"]), end="")
    return 0
