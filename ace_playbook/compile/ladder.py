"""The promotion ladder (docs/design/compiler.md 7.3): `compile approve --mode warn|block`.

`check` is a pure function over rows: it re-derives every counted TP and FP from `shadow.jsonl` and returns a `Result`
(reasons that failed, the listings, the flagged labels). `run` is the CLI around it: it reads the files, refuses a broken
chain, prints the listing, asks on the TTY and, on `y`, appends the `approvals.jsonl` row and writes the approved copy.
Imported lazily by `cmd_approve`. Stdlib, this package and `..atomic`/`..canon`; the store loads inside `lesson_status`.
"""

from __future__ import annotations

import calendar
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

from ..atomic import write_private
from ..canon import now_iso
from . import approvals, evidence, labels, proposal, shadow

RANK = {"none": 0, "observe": 1, "warn": 2, "block": 3}
PREV = {"warn": "observe", "block": "warn"}
MIN_DAYS, MIN_TPS = 14, 5
REVIEW_SHA = re.compile(r"[0-9a-f]{7,40}")


@dataclass(frozen=True)
class Listing:
    kind: str          # tp | fp
    ts: str            # the fire
    session: str       # bare session hash
    row_id: str
    at: str            # the label
    n: int             # 1-based row number in labels.jsonl
    after: bool        # the label is newer than the proposal's newest approvals row


@dataclass
class Result:
    reasons: list[str] = field(default_factory=list)
    tps: list[Listing] = field(default_factory=list)
    fps: list[Listing] = field(default_factory=list)
    flagged: list[Listing] = field(default_factory=list)
    since: str | None = None
    since_source: str = ""

    @property
    def ok(self) -> bool:
        return not self.reasons


def listing_line(x: Listing, tag: bool = True) -> str:
    c = labels._clean
    return (f"fire {c(x.ts)}  session {c(x.session)}  row {c(x.row_id)}  label {c(x.at)}  chain #{x.n}"
            + ("  [after last approval]" if x.after and tag else ""))


def _epoch(ts: str) -> int:
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def effective(rows_labels: list[dict]) -> dict[str, tuple[int, dict, bool]]:
    """{ref: (1-based chain number, row, human)}: the newest HUMAN row of a ref if there is one, else the newest row."""
    by_ref: dict[str, list] = {}
    for n, row in enumerate(rows_labels, 1):
        if isinstance(row.get("ref"), str):
            by_ref.setdefault(row["ref"], []).append((n, row))
    out = {}
    for ref, items in by_ref.items():
        human = [x for x in items if x[1].get("reason_code") == "human"]
        n, row = (human or items)[-1]
        out[ref] = (n, row, bool(human))
    return out


def _counted(row: dict, human: bool) -> str | None:
    """`tp` only for a human tp (a candidate or auto row never counts); `fp` from any reason code."""
    return "tp" if human and row.get("label") == "tp" else "fp" if row.get("label") == "fp" else None


def check(p: dict, copy: dict | None, rows_shadow: list[dict], rows_labels: list[dict], rows_approvals: list[dict],
          catalogue_row, lesson_status: str | None, now: str, *, raw_checked: bool = False, cap: str | None = None,
          mode: str = "warn") -> Result:
    """The rule for moving `p` up to `mode` (warn or block); `copy` is the approved record or None. Pure."""
    res, prev = Result(), PREV[mode]
    why = res.reasons.append
    mine = [r for r in rows_approvals if r["proposal_id"] == p["id"]]
    newest = mine[-1] if mine else None
    if copy is None or copy.get("mode") != prev:
        why(f"not in {prev}")
    if mine and (copy is None or approvals.verify_copy(copy, rows_approvals) != "ok"):
        why("copy not backed by the approvals chain")          # what the hook runs must be what the chain says
    if newest and newest["mode"] == prev:
        res.since, res.since_source = newest["ts"], "approvals row"
    elif newest:
        why(f"not in {prev} (newest approvals row is {newest['mode']})")
    elif prev == "observe" and copy is not None:
        res.since, res.since_source = copy["approved_at"], "copy approved_at"
    elif f"not in {prev}" not in res.reasons:
        why(f"not in {prev}")                                  # block: a warn copy with no warn row in the chain
    if res.since is not None:
        days = (_epoch(now) - _epoch(res.since)) // 86400
        if days < MIN_DAYS:
            why(f"{days} days in {prev}, need {MIN_DAYS}")
    fires = {}
    for r in rows_shadow:
        fires.setdefault(labels.row_id(r), r)
    last = mine[-1]["ts"] if mine else None
    for ref, (n, row, human) in sorted(effective(rows_labels).items()):
        kind, f = _counted(row, human), fires.get(ref)
        if kind and f is None:
            why(f"forged label: {labels._clean(ref)[:32]} has no fire row")
        if not (kind and f and f["proposal_id"] == p["id"]):
            continue
        at = row.get("at") if isinstance(row.get("at"), str) else "-"
        if at < f["ts"]:
            why(f"label precedes its fire: {labels._clean(ref)[:32]}")
        elif at > now:
            why(f"label dated in the future: {labels._clean(ref)[:32]}")
        if not (res.since is not None and f.get("tier") == "live" and f.get("mode") == prev
                and res.since <= f["ts"] <= now):
            continue
        item = Listing(kind, f["ts"], f["session"], ref, at, n, bool(last and at > last))
        (res.tps if kind == "tp" else res.fps).append(item)
        if item.after:
            res.flagged.append(item)
    for lst in (res.tps, res.fps, res.flagged):
        lst.sort(key=lambda x: (x.ts, x.row_id))
    if res.since is not None and len(res.tps) < MIN_TPS:
        why(f"{len(res.tps)} human TPs since entering {prev}, need {MIN_TPS}")
    if res.fps:
        why(f"{len(res.fps)} FPs since entering {prev}, need 0")
    if lesson_status != "active":
        why(f"lesson is not active ({lesson_status})")
    if catalogue_row.strength != "strong":
        why("detector is weak: only strong detectors are promoted")
    if RANK.get(catalogue_row.compilable, 0) < RANK[mode]:
        why(f"detector allows {catalogue_row.compilable} at most")
    if p["provenance"]["source_sha256"] != catalogue_row.source_sha256:
        why("detector source_sha256 changed since the proposal was written")
    if p["check"]["detector"] == "edit_unread_session" and not raw_checked:
        why("edit_unread_session needs --raw-checked (each TP checked against the raw transcript)")
    if cap is not None and RANK[cap] < RANK[mode]:
        why(f"demoted:{cap}")
    if mode == "block" and p["event"] != "PreToolUse":
        why("block is for PreToolUse proposals only")
    return res


def lesson_status(cfg, lesson_id: str) -> str | None:
    from ..store import DeltaLog                              # lazy: the store pulls in sqlite3
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    log.verify()
    les = log.state().get(lesson_id)
    return les["status"] if les else None


def _need(ok: object, message: str, code: int = 1) -> None:
    if not ok:
        raise labels.Refusal(message, code)


def _load(cfg, pid: str):
    """(proposal, validation report, the approved record when it is this proposal's copy else None)."""
    src = proposal.proposal_path(cfg, pid)
    p = proposal.load(src)
    rep = proposal.validate(p, cfg, path=src)
    copy, dest = None, shadow.approved_path(cfg, pid)
    if dest.exists():
        try:
            rec = proposal.load(dest)
            copy = rec if shadow.pending_body(rec)[0] == p else None
        except proposal.ProposalError:
            copy = None
    return p, rep, copy, dest


def run(cfg, args, ask: Callable[[str], str], need_tty: Callable[[str], bool], now: str | None = None,
        cap: str | None = None) -> int:
    """`approve --mode warn|block`: exit 0 promoted, 1 refused or not confirmed, 2 usage, unsafe file or no terminal."""
    mode, review = args.mode, getattr(args, "opus_reviewed", None)
    try:
        _need(mode == "block" or review is None, "--opus-reviewed applies to --mode block only", 2)
        _need(mode != "block" or (review and REVIEW_SHA.fullmatch(review)),
              "--mode block needs --opus-reviewed <sha> (7 to 40 lowercase hex characters)", 2)
        if not need_tty("approve"):
            return 2
        now = now or now_iso()
        p, rep, copy, dest = _load(cfg, args.id)
        _need(rep.failed != "schema", "validation failed at step 'schema'")
        arows, abroken = approvals.read_chain(cfg.data_dir)
        lrows, lbroken = labels.read_chain(cfg)
        _need(not abroken, f"approvals.jsonl chain broken at row {abroken}")
        _need(not lbroken, f"labels.jsonl chain broken at row {lbroken}")
        from . import catalogue
        res = check(p, copy, labels.shadow_rows(cfg), lrows, arows, catalogue.CATALOGUE[p["check"]["detector"]],
                    lesson_status(cfg, p["lesson_id"]), now, raw_checked=bool(getattr(args, "raw_checked", False)),
                    cap=cap, mode=mode)
        if not rep.ok:
            res.reasons.insert(0, f"validation failed at step '{rep.failed}'")
        print(f"{p['id']}  lesson {p['lesson_id']}  detector {p['check']['detector']}  to {mode}")
        if res.since is not None:
            print(f"  {PREV[mode]} since {res.since} ({res.since_source}); now {now}")
        counted = len(res.tps) + len(res.fps)
        every = bool(counted) and len(res.flagged) == counted     # all labels postdate the row: one line, not N tags
        for name, rows in (("TPs", res.tps), ("FPs", res.fps)):
            print(f"  counted {name} ({len(rows)}):")
            for x in rows:
                print("    " + listing_line(x, tag=not every))
        if every:
            last = approvals.newest(arows, p["id"])["ts"]
            print(f"  all {counted} counted labels postdate the last approval row ({last})")
        for r in res.reasons:
            print(f"  FAIL: {r}")
        _need(res.ok, f"{p['id']} is not promoted to {mode}")
        tail = f" Opus review {review}." if review else ""
        _need(ask(f"Promote {p['id']} to {mode} mode? {len(res.tps)} TPs, {len(res.fps)} FPs listed above.{tail} [y/N] ")
              .lower() in ("y", "yes"), "not approved")
        rec = shadow.approved_record(p, mode=mode, now=now)
        approvals.append(cfg.data_dir, {"ts": now, "proposal_id": p["id"], "proposal_sha256": rec["proposal_sha256"],
                                        "mode": mode, "tp_refs": sorted(x.row_id for x in res.tps),
                                        "fp_refs": sorted(x.row_id for x in res.fps), "review_sha": review or "-"})
        evidence.private_dir(dest.parent)
        write_private(dest, json.dumps(rec, indent=1, sort_keys=True) + "\n")
        print(f"ok: {p['id']} is now in {mode} mode\n{dest}")
        return 0
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
