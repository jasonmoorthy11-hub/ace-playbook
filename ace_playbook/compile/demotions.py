"""Demotions (docs/design/compiler.md 7.3): `data_dir/demotions.jsonl`, hash-chained, append-only, lowering only.

A row is `{ts, v: 1, proposal_id, from_mode, to_mode, cause, ref, review_required, prev_hash, hash}`, chained from
`canon.GENESIS` like labels.jsonl. On the ladder block > warn > observe > disabled `to_mode` is at most `from_mode`; a
row that raises is invalid and breaks the chain there. The reader is O_NOFOLLOW, regular files only, 1 MiB, strict JSON.
`decide` holds the pure FP and evasion rules of the nightly pass. The hook reads this file: stdlib and `..canon` only.
"""

from __future__ import annotations

import calendar
import fcntl
import json
import os
import re
import stat
import time
from pathlib import Path

from ..canon import GENESIS, now_iso, record_hash

FILE, MAX_BYTES = "demotions.jsonl", 1 << 20
LADDER = ("disabled", "observe", "warn", "block")                   # lowest first
CAUSES = ("label_fp", "label_fp_pair", "lesson_inactive", "source_sha256", "error_rate", "evasion_pair")
KEYS = {"ts", "v", "proposal_id", "from_mode", "to_mode", "cause", "ref", "review_required", "prev_hash", "hash"}
PID = re.compile(r"P-[0-9a-f]{8}")


def lower(mode: str, cap: str | None) -> str:
    """The lower of `mode` and `cap` (no cap: `mode`); `disabled` is the lowest rung."""
    return mode if cap is None else min(mode, cap, key=LADDER.index)


def read_regular(path: Path, cap: int) -> bytes | None:
    """Up to `cap` + 1 bytes of a regular file opened O_NOFOLLOW; None when missing, OSError for a symlink or non-file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError("not a regular file")
        return fh.read(cap + 1)


def _ok(row: dict, prev: str) -> bool:
    """Shape, enums, ladder direction and hash link of one row."""
    try:
        return (set(row) == KEYS and row["v"] == 1 and PID.fullmatch(row["proposal_id"]) is not None
                and row["cause"] in CAUSES and isinstance(row["ref"], str) and isinstance(row["ts"], str)
                and isinstance(row["review_required"], bool)
                and LADDER.index(row["to_mode"]) <= LADDER.index(row["from_mode"])
                and row["prev_hash"] == prev and row["hash"] == record_hash(row))
    except (TypeError, ValueError):
        return False


def _parse(data: bytes) -> tuple[list[dict], int | None]:
    rows, prev = [], GENESIS
    for line in data.splitlines():
        try:
            row = json.loads(line.decode("utf-8"), parse_constant=int)      # int("NaN") raises: no NaN or Infinity
        except (ValueError, RecursionError):                         # includes UnicodeDecodeError
            return rows, len(rows)
        if not (isinstance(row, dict) and _ok(row, prev)):
            return rows, len(rows)
        rows.append(row)
        prev = row["hash"]
    return rows, None


def read_chain(data_dir: str | Path) -> tuple[list[dict], int | None]:
    """(rows before the first bad one, its 0-based index or None). Missing file: ([], None); symlink: OSError; over 1 MiB: a break."""
    data = read_regular(Path(data_dir) / FILE, MAX_BYTES) or b""
    rows, broken = _parse(data[:MAX_BYTES])
    return rows, len(rows) if broken is None and len(data) > MAX_BYTES else broken


def effective_cap(rows: list[dict], proposal_id: str) -> str | None:
    """The newest valid row's `to_mode` for the proposal, None when it has no row."""
    return next((r["to_mode"] for r in reversed(rows) if r["proposal_id"] == proposal_id), None)


def duplicate(rows: list[dict], row: dict) -> dict | None:
    """The newest row of the proposal when it already says the same (`to_mode`, `cause`, `ref`)."""
    last = next((r for r in reversed(rows) if r["proposal_id"] == row["proposal_id"]), None)
    return last if last and all(last[k] == row[k] for k in ("to_mode", "cause", "ref")) else None


def append(data_dir: str | Path, fields: dict) -> dict:
    """Lock (O_NOFOLLOW, 0600), verify the chain from that fd, append and return the row; the newest row of the proposal
    saying the same is returned instead. ValueError: invalid row, broken or oversize chain."""
    path = Path(data_dir) / FILE
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), "ab") as fh:
        fd = fh.fileno()
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        size = os.fstat(fd).st_size
        rows, broken = _parse(os.pread(fd, size, 0)) if size <= MAX_BYTES else ([], 0)
        if broken is not None:
            raise ValueError(f"demotions.jsonl chain broken at row {broken} (or over 1 MiB)")
        prev = rows[-1]["hash"] if rows else GENESIS
        row = {"ts": now_iso(), "v": 1, **fields, "prev_hash": prev}
        row["hash"] = record_hash(row)
        if not _ok(row, prev):
            raise ValueError("invalid demotion row")
        if (same := duplicate(rows, row)) is not None:
            return same
        fh.write(json.dumps(row, sort_keys=True).encode() + b"\n")
        fh.flush()
        os.fsync(fd)
    return row


def epoch(ts: object) -> float:
    """Seconds since the epoch of a `YYYY-MM-DDTHH:MM:SSZ` stamp; 0.0 for anything else."""
    try:
        return float(calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def pair_ref(rows: list[dict], days: int) -> str | None:
    """`id` of the newer of the last two rows (by ts) at most `days` apart, else None."""
    rows = sorted(rows, key=lambda r: str(r.get("ts")))
    near = [b.get("id") for a, b in zip(rows, rows[1:]) if epoch(b.get("ts")) - epoch(a.get("ts")) <= days * 86400]
    return near[-1] if near else None


def decide(pid: str, cur: str, *, disable: str | None, fps: list[tuple[str, str]], evasion: str | None) -> list[dict]:
    """Fields of the rows to append, in order: `cur` is the current mode, `disable` a disable trigger's cause, `fps` the
    (ts, ref) of its effective FPs since its last demotion, `evasion` the ref of a new evasion pair."""
    def row(to: str, cause: str, ref: str, review: bool = False) -> dict:
        return {"proposal_id": pid, "from_mode": cur, "to_mode": to, "cause": cause, "ref": ref, "review_required": review}
    if cur == "disabled":
        return []
    if disable:
        return [row("disabled", disable, "-")]
    out, near = [], pair_ref([{"ts": t, "id": r} for t, r in fps], 30)
    if near:                                                  # two FPs within 30 days: observe and a re-review
        out, cur = [row("observe", "label_fp_pair", near, True)], "observe"
    for _ts, ref in ([] if near else sorted(fps)):            # otherwise one rung per FP
        if cur in ("block", "warn"):
            out.append(row("warn" if cur == "block" else "observe", "label_fp", ref))
            cur = out[-1]["to_mode"]
    return out + ([row(cur, "evasion_pair", evasion, True)] if evasion else [])
