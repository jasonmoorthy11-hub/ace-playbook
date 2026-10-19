"""`compile report` (docs/design/compiler.md 8, minimal first cut): per-proposal counts from shadow rows and labels.

Two corpora, two tables, never summed: the private `shadow.jsonl` (written by the live hook) and the optional
`shadow.synthetic.jsonl` (every row says `corpus: synthetic`). A row in the wrong file refuses the whole report.
tp/fp come from `labels.jsonl` through `labels.effective` (a human row beats an auto one, the newest row of its kind
wins, `incident` rows ignored, a broken chain ignores every label): fp counts human and auto rows, tp only human
ones, `tp_cand` the auto `tp_candidate` rows, which are never TPs. No session hash, row id, payload hash or path is printed. Rates and hook cost need the offline pass.
Both shadow files are read here through a no-follow, regular-file-only reader. Imports: stdlib, `..canon`,
`..config`, `.labels`; `catalogue`, `proposal` and `shadow` load lazily (hook path).
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
from . import labels

PRIVATE, SYNTHETIC = "shadow.jsonl", "shadow.synthetic.jsonl"
KEYS = ("ts", "session", "proposal_id", "payload_sha256")
PID, LID = re.compile(r"P-[0-9a-f]{8}"), re.compile(r"L-[0-9a-f]{8}")
MAX_DAYS = 3660
FIELDS = ("lesson", "detector", "event", "approved_mode", "rows", "cause_detector", "cause_truncated", "sessions",
          "days", "tp", "fp", "tp_cand", "unlabelled", "first", "last")
HEADS = ("proposal", "lesson", "detector", "event", "approved_mode", "rows", "detector", "truncated", "sessions",
         "days", "tp", "fp", "tp_cand", "unlabelled", "first", "last")
NOT_COMPUTED = ("not computed here: eligible-session rates, Wilson intervals, friction, hook cost "
                "(need the offline pass, design 8)")


def source_sha256() -> str:
    """sha256 of this file's bytes: the design commits the report code hash before observe starts."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


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
    if any(r.get("corpus", "private") != "private" for r in private):
        raise labels.Refusal("row with a non-private corpus in shadow.jsonl", 2)
    if any(r.get("corpus") != "synthetic" for r in synthetic):
        raise labels.Refusal("non-synthetic row found in the synthetic corpus", 2)
    full = lambda rows: [r for r in rows if all(isinstance(r.get(k), str) for k in KEYS)]  # noqa: E731
    return full(private), full(synthetic)


def label_map(cfg: Config) -> tuple[dict[str, str], int | None]:
    """({row_id: tp|fp|tp_candidate}, broken_at): `labels.effective`; a broken chain gives no labels at all."""
    rows, broken = labels.read_chain(cfg)
    return ({}, broken) if broken is not None else (labels.effective(rows), None)


def _meta(cfg: Config, pid: str) -> tuple[str, str, str]:
    """(detector, event, approved mode) from the proposal files; `-` for anything missing, broken, unsafe or unknown."""
    from . import catalogue, proposal, shadow
    det = ev = mode = "-"
    if not PID.fullmatch(pid):
        return det, ev, mode
    try:
        p = proposal.load(proposal.proposal_path(cfg, pid))
        det, ev = _pick(p["check"]["detector"], catalogue.CATALOGUE), _pick(p["event"], shadow.EVENTS.values())
    except (ValueError, OSError, KeyError, TypeError):
        pass
    try:
        body, approved = shadow.pending_body(proposal.load(shadow.approved_path(cfg, pid)))
        mode = _pick(approved, shadow.MODES) if body is not None else "-"
    except (ValueError, OSError):
        pass
    return det, ev, mode


def summarize(cfg: Config, rows: list[dict], labs: dict[str, str], now: str, days: int) -> dict:
    """{"proposals": {pid: {FIELDS}}, "per_day": [{date, proposal_id, rows}]} for one corpus."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["proposal_id"] if PID.fullmatch(r["proposal_id"]) else "invalid", []).append(r)
    today = date.fromisoformat(now[:10])
    cutoff = today - timedelta(days=days - 1)
    props, per_day = {}, Counter()
    for pid in sorted(by):
        rs = by[pid]
        dates = [d for d in (_day(r["ts"]) for r in rs) if d]
        ids = {labels.row_id(r) for r in rs}                         # rows sharing an id collapse for labelling
        marks, causes = Counter(labs.get(i) for i in ids), Counter(r.get("cause") for r in rs)
        det, ev, mode = _meta(cfg, pid)
        props[pid] = {"lesson": next((v for v in (_match(r.get("lesson_id"), LID) for r in rs) if v != "-"), "-"),
                      "detector": det, "event": ev, "approved_mode": mode, "rows": len(rs),
                      "cause_detector": causes["detector"], "cause_truncated": causes["truncated"],
                      "sessions": len({r["session"] for r in rs}), "days": len(set(dates)),
                      "tp": marks["tp"], "fp": marks["fp"], "tp_cand": marks["tp_candidate"], "unlabelled": sum(labels.row_id(r) not in labs for r in rs),
                      "first": min(dates, default="-"), "last": max(dates, default="-")}
        per_day.update(Counter((d, pid) for d in dates if cutoff <= date.fromisoformat(d) <= today))
    return {"proposals": props,
            "per_day": [{"date": d, "proposal_id": p, "rows": n} for (d, p), n in sorted(per_day.items())]}


def _grid(head: tuple[str, ...], body: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in [list(head), *body]) for i in range(len(head))]
    return ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in [list(head), *body]]


def _section(title: str, source: str, data: dict, days: int) -> list[str]:
    out = [title, f"source: {source}"]
    if not data["proposals"]:
        return [*out, "no rows", ""]
    out += _grid(HEADS, [[pid, *(str(p[f]) for f in FIELDS)] for pid, p in data["proposals"].items()])
    out += ["", f"Rows per day (last {days} days)"]
    body = [[d["date"], d["proposal_id"], str(d["rows"])] for d in data["per_day"]]
    out += _grid(("date", "proposal_id", "rows"), body) if body else ["no rows in window"]
    return [*out, ""]


def render(sha: str, days: int, private: dict, synthetic: dict, broken: int | None) -> str:
    lines = ["ace-playbook shadow report", f"report_sha256: {sha}", ""]
    lines += _section("Private corpus (aggregate, counts only)", PRIVATE, private, days)
    lines += _section("Synthetic corpus (reproducible)", SYNTHETIC, synthetic, days)
    lines.append(NOT_COMPUTED)
    if broken is not None:
        lines.append(f"labels: chain BROKEN at row {broken}, labels ignored")
    return "\n".join(lines) + "\n"


def run(cfg: Config, days: int, as_json: bool = False, now: str | None = None) -> int:
    """Entry of `compile report`: exit 0 (also with no rows), 2 for usage, a corpus mix-up or an unsafe file."""
    try:
        if not 1 <= days <= MAX_DAYS:
            raise labels.Refusal(f"--days must be 1..{MAX_DAYS}", 2)
        now = now or now_iso()
        priv_rows, syn_rows = read_corpora(cfg)
        labs, broken = label_map(cfg)
        private = summarize(cfg, priv_rows, labs, now, days)
        synthetic = summarize(cfg, syn_rows, {}, now, days)          # human labels are on private rows only
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    sha = source_sha256()
    if as_json:
        print(json.dumps({"report_sha256": sha, "days": days, "private": private, "synthetic": synthetic,
                          "labels": {"chain_ok": broken is None, "broken_at": broken}}, indent=1, sort_keys=True))
    else:
        print(render(sha, days, private, synthetic, broken), end="")
    return 0
