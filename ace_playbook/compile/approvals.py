"""The hash-chained `data_dir/approvals.jsonl` (docs/design/compiler.md 7.3): one row per approval.

A row is `{ts, v: 1|2, proposal_id, proposal_sha256, mode, reviewed_by: "operator-tty", tp_refs, fp_refs, review_sha,
prev_hash, hash}`, chained from `canon.GENESIS`: `v: 1` with `canon.record_hash` like `labels.jsonl` while there is no
chain key, `v: 2` with the keyed hash once there is one (chains.py: the verifier rule, the key, the heads file). Rows hold enums, ids, hashes, integers, timestamps and lists of
row ids only. The shadow hook reads the chain (`read_chain`, no-follow, 1 MiB cap, strict) and honours a warn or block copy
only when `verify_copy` says `ok`. Imports: stdlib, `..canon` and `.chains`.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from pathlib import Path

from ..canon import GENESIS, now_iso
from . import chains

FILE, MAX_BYTES = "approvals.jsonl", 1 << 20
MODES = ("observe", "warn", "block", "revoked")      # `revoked`: unapprove removed the copy
KEYS = {"ts", "v", "proposal_id", "proposal_sha256", "mode", "reviewed_by", "tp_refs", "fp_refs", "review_sha",
        "prev_hash", "hash"}
_STAMP, _PID, _SHA, _REF = (re.compile(x) for x in (r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r"P-[0-9a-f]{8}",
                                                   r"[0-9a-f]{64}", r"[0-9a-f]{8}"))
_REVIEW = re.compile(r"-|[0-9a-f]{7,40}")


def _text(x: object, pattern: re.Pattern) -> bool:
    return isinstance(x, str) and pattern.fullmatch(x) is not None


def valid_row(r: object) -> bool:
    """Strict shape: exactly KEYS, every value of the right type and pattern."""
    return (isinstance(r, dict) and set(r) == KEYS and type(r["v"]) is int and r["v"] in (1, 2)
            and _text(r["ts"], _STAMP) and _text(r["proposal_id"], _PID)
            and (_text(r["proposal_sha256"], _SHA) or (r["mode"] == "revoked" and r["proposal_sha256"] == "-"))
            and r["mode"] in MODES and r["reviewed_by"] == "operator-tty" and _text(r["review_sha"], _REVIEW)
            and all(isinstance(r[k], list) and all(_text(i, _REF) for i in r[k]) for k in ("tp_refs", "fp_refs"))
            and _text(r["prev_hash"], _SHA) and _text(r["hash"], _SHA))


shape, BASE = valid_row, 0                                   # for chains.has_v2 and chains_io: the shape check, what `verify`'s number needs to be 1-based


def verify(raw: bytes, ring: chains.Keyring | None = None) -> tuple[list[dict], int | None]:
    """(the rows that verify, 1-based number of the first line that does not or None); `chains.hash_ok` is the verifier rule."""
    lines = raw.split(b"\n")
    lines = lines[:-1] if lines and lines[-1] == b"" else lines
    prev, good = GENESIS, []
    for n, line in enumerate(lines, 1):
        try:
            row = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            return good, n
        if not valid_row(row) or row["prev_hash"] != prev or not chains.hash_ok(row, ring):
            return good, n
        prev = row["hash"]
        good.append(row)
    return good, None


def read_chain(data_dir, ring: chains.Keyring | None = None) -> tuple[list[dict], int | str | None]:
    """(rows, broken): a missing file is empty; a symlink, non-regular file or one over 1 MiB raises. `broken` is the 1-based row
    number, or `head_mismatch` / `heads_missing` (chains.head_state) when the rows verify but the signed head does not match."""
    try:
        fd = os.open(Path(data_dir) / FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return [], chains.head_state(ring, "approvals", [], None)
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError("not a regular file")
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("approvals.jsonl is over 1 MiB")
    rows, broken = verify(raw, ring)
    return rows, chains.head_state(ring, "approvals", rows, broken)


def newest(rows: list[dict], proposal_id: str) -> dict | None:
    return next((r for r in reversed(rows) if r["proposal_id"] == proposal_id), None)


def verify_copy(rec: dict, rows: list[dict]) -> str:
    """`ok` when the newest row for the copy's proposal has its hash and mode, else `missing` or `mismatch`."""
    row = newest(rows, rec["id"])
    if row is None:
        return "missing"
    return "ok" if row["proposal_sha256"] == rec["proposal_sha256"] and row["mode"] == rec["mode"] else "mismatch"


def append(data_dir, fields: dict, ring: chains.Keyring | None = None) -> dict:
    """Lock the file (O_NOFOLLOW, 0600), verify the chain from that fd and append one row; ValueError on a broken chain
    or bad fields. Skips (returns the existing row) when the newest row of the proposal has the same hash and mode.
    With a keyring the row is `v: 2` (keyed hash) and the signed head is updated inside the lock. Without one:
    ValueError("chain key missing") once the file holds a shape-valid `v: 2` row; on an empty or v1-only chain the row stays
    `v: 1`, so installs from before keygen work unchanged. A chain that does not verify (or whose head does not match) refuses."""
    row = {"ts": now_iso(), "v": 1, "reviewed_by": "operator-tty", "tp_refs": [], "fp_refs": [], "review_sha": "-",
           **fields, "prev_hash": GENESIS, "hash": GENESIS}
    if not valid_row(row):
        raise ValueError("approvals row fields are malformed")
    path = Path(data_dir) / FILE
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), "ab") as fh:
        fd = fh.fileno()
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        size = os.fstat(fd).st_size
        if size > MAX_BYTES:
            raise ValueError("approvals.jsonl is over 1 MiB")
        raw = os.pread(fd, size, 0)
        if ring is None and chains.has_v2(raw, valid_row):
            raise ValueError(chains.MISSING)
        rows, broken = verify(raw, ring)
        if (broken := chains.head_state(ring, "approvals", rows, broken)) is not None:
            raise ValueError(f"approvals.jsonl chain broken {chains.where(broken)}; repair by hand: "
                             "truncate the file to its last complete line")
        last = newest(rows, row["proposal_id"])
        if last and (last["proposal_sha256"], last["mode"]) == (row["proposal_sha256"], row["mode"]):
            return last
        row = chains.seal({**row, "prev_hash": rows[-1]["hash"] if rows else GENESIS}, ring)
        fh.write(json.dumps(row, sort_keys=True).encode() + b"\n")
        fh.flush()
        os.fsync(fd)
        if ring is not None:
            ring.update(approvals=row["hash"])                 # still under the file lock: a crash between the two is `keygen --rehead`
    return row
