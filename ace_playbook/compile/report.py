"""`compile report` (docs/design/compiler.md 8, minimal first cut): per-proposal counts from shadow rows and labels.

Two corpora, two tables, never summed: the private `shadow.jsonl` (written by the live hook) and the optional
`shadow.synthetic.jsonl` (every row says `corpus: synthetic`). A row in the wrong file refuses the whole report.
tp/fp come from `labels.jsonl` (newest row per ref wins, `incident` rows ignored, a broken chain ignores every
label). No session hash, row id, payload hash or path is printed. Rates and hook cost need the offline pass.
Imports: stdlib, `..canon`, `..config`, `.labels`; `proposal` and `shadow` load lazily (hook path).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from ..canon import now_iso
from ..config import Config
from . import labels

PRIVATE, SYNTHETIC = "shadow.jsonl", "shadow.synthetic.jsonl"
KEYS = ("ts", "session", "proposal_id", "payload_sha256")
PID, LID, WORD = re.compile(r"P-[0-9a-f]{8}"), re.compile(r"L-[0-9a-f]{8}"), re.compile(r"[A-Za-z0-9_|*]{1,40}")
FIELDS = ("lesson", "detector", "event", "approved_mode", "rows", "cause_detector", "cause_truncated", "sessions",
          "days", "tp", "fp", "unlabelled", "first", "last")
HEADS = ("proposal", "lesson", "detector", "event", "approved_mode", "rows", "detector", "truncated", "sessions",
         "days", "tp", "fp", "unlabelled", "first", "last")
NOT_COMPUTED = ("not computed here: eligible-session rates, Wilson intervals, friction, hook cost "
                "(need the offline pass, design 8)")


def source_sha256() -> str:
    """sha256 of this file's bytes: the design commits the report code hash before observe starts."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _tok(value: object, rx: re.Pattern) -> str:
    return value if isinstance(value, str) and rx.fullmatch(value) else "-"


def _day(ts: str) -> str | None:
    try:
        return date.fromisoformat(ts[:10]).isoformat() if len(ts) >= 10 else None
    except ValueError:
        return None


def _check(rows: list[dict], want: str, message: str) -> list[dict]:
    """The corpus guard: private rows may omit `corpus` (or say private); synthetic rows must say synthetic."""
    ok = (lambda r: r.get("corpus", "private") == "private") if want == "private" else (
        lambda r: r.get("corpus") == "synthetic")
    if not all(ok(r) for r in rows):
        raise labels.Refusal(message, 2)
    return rows


def read_corpora(cfg: Config) -> tuple[list[dict], list[dict]]:
    private = _check(labels.shadow_rows(cfg), "private", "synthetic row found in the private corpus")
    synthetic = [r for r in labels._read(cfg.path(SYNTHETIC)) if all(isinstance(r.get(k), str) for k in KEYS)]
    return private, _check(synthetic, "synthetic", "non-synthetic row found in the synthetic corpus")


def label_map(cfg: Config) -> tuple[dict[str, str], int | None]:
    """({row_id: tp|fp}, broken_at): the newest row per ref wins; a broken chain gives no labels at all."""
    rows, broken = labels.read_chain(cfg)
    if broken is not None:
        return {}, broken
    return {r["ref"]: r["label"] for r in rows if r.get("label") in ("tp", "fp") and isinstance(r.get("ref"), str)}, None


def _meta(cfg: Config, pid: str) -> tuple[str, str, str]:
    """(detector, event, approved mode) from the proposal files; `-` for anything missing, broken or unsafe."""
    from . import proposal, shadow
    det = ev = mode = "-"
    if not PID.fullmatch(pid):
        return det, ev, mode
    try:
        p = proposal.load(proposal.proposal_path(cfg, pid))
        det, ev = _tok(p["check"]["detector"], WORD), _tok(p["event"], WORD)
    except (ValueError, OSError, KeyError, TypeError):
        pass
    try:
        body, approved = shadow.pending_body(proposal.load(shadow.approved_path(cfg, pid)))
        mode = _tok(approved, WORD) if body is not None else "-"
    except (ValueError, OSError):
        pass
    return det, ev, mode


def summarize(cfg: Config, rows: list[dict], labs: dict[str, str], now: str, days: int) -> dict:
    """{"proposals": {pid: {FIELDS}}, "per_day": [{date, proposal_id, rows}]} for one corpus."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["proposal_id"] if PID.fullmatch(r["proposal_id"]) else "invalid", []).append(r)
    cutoff = date.fromisoformat(now[:10]) - timedelta(days=days - 1)
    props, per_day = {}, Counter()
    for pid in sorted(by):
        rs = by[pid]
        dates = [d for d in (_day(r["ts"]) for r in rs) if d]
        marks, causes = Counter(labs.get(labels.row_id(r)) for r in rs), Counter(r.get("cause") for r in rs)
        det, ev, mode = _meta(cfg, pid)
        props[pid] = {"lesson": next((v for v in (_tok(r.get("lesson_id"), LID) for r in rs) if v != "-"), "-"),
                      "detector": det, "event": ev, "approved_mode": mode, "rows": len(rs),
                      "cause_detector": causes["detector"], "cause_truncated": causes["truncated"],
                      "sessions": len({r["session"] for r in rs}), "days": len(set(dates)),
                      "tp": marks["tp"], "fp": marks["fp"], "unlabelled": len(rs) - marks["tp"] - marks["fp"],
                      "first": min(dates, default="-"), "last": max(dates, default="-")}
        per_day.update(Counter((d, pid) for d in dates if date.fromisoformat(d) >= cutoff))
    return {"proposals": props, "per_day": [{"date": d, "proposal_id": p, "rows": n} for (d, p), n in sorted(per_day.items())]}


def _grid(head: tuple[str, ...], body: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in [list(head), *body]) for i in range(len(head))]
    return ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in [list(head), *body]]


def _section(title: str, source: str, data: dict, days: int) -> list[str]:
    out = [title, f"source: {source}"]
    if not data["proposals"]:
        return [*out, "no rows", ""]
    out += _grid(HEADS, [[pid, *(str(p[f]) for f in FIELDS)] for pid, p in data["proposals"].items()])
    out += ["", f"Rows per day (last {days} days)"]
    out += _grid(("date", "proposal_id", "rows"), [[d["date"], d["proposal_id"], str(d["rows"])] for d in data["per_day"]]
                 ) if data["per_day"] else ["no rows in window"]
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
        if days < 1:
            raise labels.Refusal("--days must be at least 1", 2)
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
