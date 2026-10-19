"""Human labels (docs/design/compiler.md 7.3): `compile label` appends to the hash-chained `labels.jsonl`.

A row is `{ref, label: incident|tp|fp, reason_code: human, at, [lesson_id, session], [note], prev_hash, hash}`, read
by `evidence._labels`. Append-only (O_APPEND, 0600), the whole chain is verified before every append, notes are
scrubbed. Imports: stdlib, `..canon`, `..config`; the store, scrub engine and `evidence` load lazily (hook path).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable

from ..canon import GENESIS, ID_RE, now_iso, record_hash
from ..config import Config

NOTE_MAX = 200
KINDS = ("incident", "tp", "fp")


class Refusal(Exception):
    """A refused action: the message is safe to print, `code` is the exit status (1 refused, 2 usage)."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _need(ok: object, message: str, code: int = 1) -> None:
    if not ok:
        raise Refusal(message, code)


def row_id(row: dict) -> str:
    """Design 7.3: sha256(ts, session, proposal_id, payload_sha256)[:8] of a shadow.jsonl row."""
    parts = [row["ts"], row["session"], row["proposal_id"], row["payload_sha256"]]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:8]


def _read(path: Path) -> list[dict]:
    """Tolerant JSONL: bad lines are skipped (as in `evidence._jsonl`), a missing file is no rows."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    recs = []
    for line in lines:
        with contextlib.suppress(ValueError):
            recs.append(json.loads(line))
    return [r for r in recs if isinstance(r, dict)]


def shadow_rows(cfg: Config) -> list[dict]:
    keys = ("ts", "session", "proposal_id", "payload_sha256")
    return [r for r in _read(cfg.path("shadow.jsonl")) if all(isinstance(r.get(k), str) for k in keys)]


def read_chain(cfg: Config) -> tuple[list[dict], int | None]:
    """(rows that verify, 1-based number of the first row that does not or None): the rule of `evidence._labels`."""
    path = cfg.path("labels.jsonl")
    _need(not (path.is_symlink() or (path.exists() and not path.is_file())), "labels.jsonl is a symlink or not a file", 2)
    prev, good = GENESIS, []
    for n, row in enumerate(_read(path), 1):
        if row.get("prev_hash") != prev or row.get("hash") != record_hash(row):
            return good, n
        prev = row["hash"]
        good.append(row)
    return good, None


def check_chain(cfg: Config) -> list[dict]:
    rows, broken = read_chain(cfg)
    _need(not broken, f"labels.jsonl chain broken at row {broken}")
    return rows


def check_note(cfg: Config, note: str | None) -> str | None:
    """The trimmed note, None when empty. Over 200 characters is a usage error; a scrub hit names the rule only."""
    note = (note or "").strip()
    _need(len(note) <= NOTE_MAX, f"note is longer than {NOTE_MAX} characters", 2)
    if note:
        from ..scrub import LESSON, scan
        rules = sorted({h.rule for h in scan(note, LESSON, cfg.deny_terms)})
        _need(not rules, "note refused by the scrubber (rule: " + ", ".join(rules) + "); nothing was written")
    return note or None


def append_row(cfg: Config, label: str, ref: str, note: str | None = None, **extra: str) -> tuple[dict, int]:
    """Verify the chain, append one row (file created 0600 in a 0700 dir); returns (row, 1-based row number)."""
    from .evidence import private_dir
    note, rows, path = check_note(cfg, note), check_chain(cfg), cfg.path("labels.jsonl")
    row = {"ref": ref, "label": label, "reason_code": "human", "at": now_iso(), **extra,
           **({"note": note} if note else {}), "prev_hash": rows[-1]["hash"] if rows else GENESIS}
    row["hash"] = record_hash(row)
    private_dir(path.parent)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600), "ab") as fh:
        fd = fh.fileno()
        os.fchmod(fd, 0o600)
        size = os.fstat(fd).st_size            # never merge into a last line that lost its newline
        lead = b"\n" if size and os.pread(fd, 1, size - 1) != b"\n" else b""
        fh.write(lead + json.dumps(row, sort_keys=True).encode() + b"\n")
        fh.flush()
        os.fsync(fd)
    return row, len(rows) + 1


def resolve_incident(cfg: Config, lesson_id: str, session: str) -> dict:
    """The first mapped fire of `lesson_id` in an eligible `session`: {detector, ts, payload, ref}, else Refusal."""
    _need(ID_RE.fullmatch(lesson_id), "lesson id must look like L-xxxxxxxx", 2)
    from ..store import DeltaLog
    from ..store.schema import LIVE_STATUSES
    from . import catalogue, evidence
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    log.verify()
    les = log.state().get(lesson_id)
    _need(les is not None and les["status"] in LIVE_STATUSES, f"lesson {lesson_id} is unknown or not live")
    names = [n for n, d in catalogue.CATALOGUE.items() if lesson_id in d.lessons]
    _need(names, "no_mapped_detector")
    _need(session in evidence._exposure(cfg).get(lesson_id, {}), "lesson was never eligible in that session")
    path = evidence._transcript(cfg, session)
    _need(path, "transcript not found")
    inc = evidence._first_fire(path, names)
    _need(inc, "no mapped detector fires in that session")
    ref = evidence.card_id(lesson_id, evidence.sid(session), evidence.payload_hash(inc["payload"]))
    return {**inc, "ref": ref, "session": evidence.sid(session)}


def preview(cfg: Config, payload: dict) -> str:
    """The first string value of tool_input, scrub hits masked, whitespace collapsed, cut to 120 characters."""
    from ..scrub import LESSON, scan
    from ..scrub.quarantine import mask
    val = next((v for v in payload["tool_input"].values() if isinstance(v, str)), "")
    return " ".join(mask(val, scan(val, LESSON, cfg.deny_terms)).split())[:120]


def _list(cfg: Config, kind: str | None) -> int:
    rows, broken = read_chain(cfg)
    for n, r in enumerate(rows, 1):
        if kind in (None, r.get("label")):
            print(n, r.get("at"), r.get("label"), r.get("ref"), r.get("lesson_id") or "-", r.get("session") or "-",
                  r.get("note") or "-")
    print(f"chain BROKEN at row {broken}" if broken else f"chain ok ({len(rows)} rows)")
    return 1 if broken else 0


def _rows(cfg: Config, limit: int) -> int:
    _need(limit >= 1, "--limit must be at least 1", 2)
    for r in shadow_rows(cfg)[-limit:]:
        print(r["ts"], row_id(r), r["proposal_id"], r.get("lesson_id", "-"), r.get("cause", "-"),
              str(r.get("would_fire")).lower())
    return 0


def _target(cfg: Config, args) -> tuple[str, str, dict, str]:
    """(label, ref, extra row keys, confirmation question) for --incident, --tp or --fp; prints what is judged."""
    if args.incident:
        _need(args.lesson and args.session, "--incident needs --lesson and --session", 2)
        inc = resolve_incident(cfg, args.lesson, args.session)
        for name, val in (("detector", inc["detector"]), ("incident", inc["ts"]),
                          ("tool_name", inc["payload"]["tool_name"]), ("input", preview(cfg, inc["payload"])),
                          ("ref", inc["ref"])):
            print(f"{name + ':':<11}{val}")
        return ("incident", inc["ref"], {"lesson_id": args.lesson, "session": inc["session"]},
                f"Label this as the incident for {args.lesson}? [y/N] ")
    label, rid = ("tp", args.tp) if args.tp else ("fp", args.fp)
    row = next((r for r in shadow_rows(cfg) if row_id(r) == rid), None)
    _need(row, f"no shadow row with id {rid}")
    print(f"row {rid}: {row['ts']} {row['proposal_id']} {row.get('lesson_id', '-')} {row.get('cause', '-')} "
          f"would_fire={str(row.get('would_fire')).lower()}")
    return label, rid, {}, f"Label row {rid} as {label}? [y/N] "


def run(cfg: Config, args, ask: Callable[[str], str], need_tty: Callable[[str], bool]) -> int:
    """Entry of `compile label`; exit 0 ok, 1 refused (chain, scrub, evidence, answer), 2 usage or unsafe file."""
    try:
        if args.list or args.rows:
            return _list(cfg, args.kind) if args.list else _rows(cfg, args.limit)
        if not need_tty("label"):
            return 2
        note = check_note(cfg, args.note)
        prior = check_chain(cfg)
        label, ref, extra, question = _target(cfg, args)
        same = [r for r in prior if r.get("ref") == ref]
        if any(r.get("label") == label for r in same):
            print("already labelled")
            return 0
        if same:
            print(f"warning: {ref} was labelled {same[-1].get('label')} before; the newest row wins for readers")
        _need(ask(question).lower() in ("y", "yes"), "not labelled")
        _, n = append_row(cfg, label, ref, note, **extra)
        print(f"ok: labelled {ref} (row {n})")
        return 0
    except Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
