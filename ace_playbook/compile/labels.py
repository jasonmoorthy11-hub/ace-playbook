"""Human labels (docs/design/compiler.md 7.3): `compile label` appends to the hash-chained `labels.jsonl`.

A row is `{ref, label: incident|tp|fp, reason_code: human, at, [lesson_id, session], [note], prev_hash, hash}`, read
by `evidence._labels`. The nightly offline pass also writes auto rows (`reason_code` one of AUTO, label `fp` or
`tp_candidate`); `effective` lets a human row beat them. Append-only (O_APPEND, 0600), the whole chain is verified
before every append, notes are scrubbed. Imports: stdlib, `..canon`, `..config`; the store, scrub engine and
`evidence` load lazily (hook path).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Callable

from ..canon import GENESIS, ID_RE, now_iso, record_hash
from ..config import Config

NOTE_MAX = 200
KINDS = ("incident", "tp", "fp", "tp_candidate")
AUTO = {"auto_fp_correction": "fp", "auto_fp_prompt_target": "fp", "auto_fp_shell_rerun": "fp",
        "auto_tp_hook_block": "tp_candidate", "auto_tp_correction": "tp_candidate"}     # reason_code -> its label
NOT_A_FILE = "labels.jsonl is a symlink or not a regular file"


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


def _parse(text: str) -> list[dict]:
    """Tolerant JSONL: bad lines are skipped, as in `evidence._jsonl`."""
    recs = []
    for line in text.splitlines():
        with contextlib.suppress(ValueError):
            recs.append(json.loads(line))
    return [r for r in recs if isinstance(r, dict)]


def _read(path: Path) -> list[dict]:
    try:
        return _parse(path.read_text())
    except FileNotFoundError:
        return []


def _clean(value: object) -> str:
    """Printable characters only: a hand-edited file must not carry terminal escapes to the operator."""
    return "".join(ch for ch in str(value) if ch.isprintable())


def shadow_rows(cfg: Config) -> list[dict]:
    keys = ("ts", "session", "proposal_id", "payload_sha256")
    return [r for r in _read(cfg.path("shadow.jsonl")) if all(isinstance(r.get(k), str) for k in keys)]


def _verify(recs: list[dict]) -> tuple[list[dict], int | None]:
    """(rows that verify, 1-based number of the first row that does not or None): the rule of `evidence._labels`."""
    prev, good = GENESIS, []
    for n, row in enumerate(recs, 1):
        if row.get("prev_hash") != prev or row.get("hash") != record_hash(row):
            return good, n
        prev = row["hash"]
        good.append(row)
    return good, None


def read_chain(cfg: Config) -> tuple[list[dict], int | None]:
    path = cfg.path("labels.jsonl")
    _need(not (path.is_symlink() or (path.exists() and not path.is_file())), NOT_A_FILE, 2)
    return _verify(_read(path))


def effective(rows: list[dict]) -> dict[str, str]:
    """ref -> tp|fp|tp_candidate: the newest human row of a ref if it has one (any reason_code outside AUTO, label tp or
    fp), else its newest auto row (label as AUTO says). `incident` rows and unknown labels count for nothing."""
    human, auto = {}, {}
    for r in rows:
        ref, label = r.get("ref"), r.get("label")
        if not isinstance(ref, str):
            continue
        if r.get("reason_code") in AUTO:
            if AUTO[r["reason_code"]] == label:
                auto[ref] = label
        elif label in ("tp", "fp"):
            human[ref] = label
    return {**auto, **human}


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


def append_row(cfg: Config, label: str, ref: str, note_checked: str | None = None, *, reason_code: str = "human",
               **extra: str) -> tuple[dict, int]:
    """Open (O_NOFOLLOW, 0600) and lock labels.jsonl, verify the chain from that fd, append a row: (row, number).

    `note_checked` must be the output of `check_note` (it is not scrubbed again); `reason_code` is `human` except for
    the offline pass. When the newest row of `ref` already carries `label`, nothing is appended and that row and its
    number are returned."""
    from .evidence import private_dir
    path = cfg.path("labels.jsonl")
    private_dir(path.parent)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600), "ab") as fh:
        fd = fh.fileno()
        _need(stat.S_ISREG(os.fstat(fd).st_mode), NOT_A_FILE, 2)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)                                     # the size after the lock is the size to trust
        rows, broken = _verify(_parse(os.pread(fd, info.st_size, 0).decode()))
        _need(not broken, f"labels.jsonl chain broken at row {broken}")
        same = [(n, r) for n, r in enumerate(rows, 1) if r.get("ref") == ref]
        if same and same[-1][1].get("label") == label:
            return same[-1][1], same[-1][0]
        row = {"ref": ref, "label": label, "reason_code": reason_code, "at": now_iso(), **extra,
               **({"note": note_checked} if note_checked else {}), "prev_hash": rows[-1]["hash"] if rows else GENESIS}
        row["hash"] = record_hash(row)
        lead = b"\n" if info.st_size and os.pread(fd, 1, info.st_size - 1) != b"\n" else b""   # a cut last line
        fh.write(lead + json.dumps(row, sort_keys=True).encode() + b"\n")
        fh.flush()
        os.fsync(fd)
        if not info.st_size:                                    # a new file: make its directory entry durable too
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    return row, len(rows) + 1


def resolve_incident(cfg: Config, lesson_id: str, session: str) -> dict:
    """The first mapped fire of `lesson_id` in an eligible `session`: {detector, ts, payload, ref}, else Refusal."""
    _need(ID_RE.fullmatch(lesson_id), "lesson id must look like L-xxxxxxxx", 2)
    from ..store import DeltaLog
    from ..store.schema import LIVE_STATUSES, USER_ORIGINS
    from . import catalogue, evidence
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    log.verify()
    les = log.state().get(lesson_id)
    _need(les is not None and les["status"] in LIVE_STATUSES, f"lesson {lesson_id} is unknown or not live")
    _need(not any(e["origin"] in USER_ORIGINS and isinstance(e.get("session_id"), str)
                  and not e["session_id"].startswith("legacy:") for e in les["evidence"]),
          "lesson has standard user evidence; incident labels apply to legacy-evidence lessons only")
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
    return _clean(" ".join(mask(val, scan(val, LESSON, cfg.deny_terms)).split()))[:120]


def _list(cfg: Config, kind: str | None) -> int:
    rows, broken = read_chain(cfg)
    for n, r in enumerate(rows, 1):
        if kind in (None, r.get("label")):
            print(n, *(_clean(r.get(k) or "-") for k in ("at", "label", "ref", "lesson_id", "session", "note")))
    print(f"chain BROKEN at row {broken}" if broken else f"chain ok ({len(rows)} rows)")
    return 1 if broken else 0


def _rows(cfg: Config, limit: int) -> int:
    _need(limit >= 1, "--limit must be at least 1", 2)
    for r in shadow_rows(cfg)[-limit:]:
        print(*map(_clean, (r["ts"], row_id(r), r["proposal_id"], r.get("lesson_id", "-"), r.get("cause", "-"),
                            str(r.get("would_fire")).lower())))
    return 0


def _target(cfg: Config, args) -> tuple[str, str, dict, str]:
    """(label, ref, extra row keys, confirmation question) for --incident, --tp or --fp; prints what is judged."""
    if args.incident:
        _need(args.lesson and args.session, "--incident needs --lesson and --session", 2)
        inc = resolve_incident(cfg, args.lesson, args.session)
        for name, val in (("detector", inc["detector"]), ("incident", inc["ts"]),
                          ("tool_name", inc["payload"]["tool_name"]), ("input", preview(cfg, inc["payload"])),
                          ("ref", inc["ref"])):
            print(f"{name + ':':<11}{_clean(val)}")
        return ("incident", inc["ref"], {"lesson_id": args.lesson, "session": inc["session"]},
                f"Label this as the incident for {args.lesson}? [y/N] ")
    label, rid = ("tp", args.tp) if args.tp else ("fp", args.fp)
    row = next((r for r in shadow_rows(cfg) if row_id(r) == rid), None)
    _need(row, f"no shadow row with id {rid}")
    shown = (row["ts"], row["proposal_id"], row.get("lesson_id", "-"), row.get("cause", "-"),
             "would_fire=" + str(row.get("would_fire")).lower())
    print(f"row {rid}:", *map(_clean, shown))
    return label, rid, {}, f"Label row {rid} as {label}? [y/N] "


def run(cfg: Config, args, ask: Callable[[str], str], need_tty: Callable[[str], bool]) -> int:
    """Entry of `compile label`; exit 0 ok, 1 refused (chain, scrub, evidence, answer), 2 usage or unsafe file."""
    try:
        bad = [f for f, v, ok in (("--lesson", args.lesson, args.incident), ("--session", args.session, args.incident),
                                  ("--note", args.note, not (args.list or args.rows)), ("--kind", args.kind, args.list),
                                  ("--limit", args.limit, args.rows)) if v is not None and not ok]
        _need(not bad, f"{', '.join(bad)} do not apply to this action", 2)
        if args.list or args.rows:
            return _list(cfg, args.kind) if args.list else _rows(cfg, 50 if args.limit is None else args.limit)
        if not need_tty("label"):
            return 2
        note = check_note(cfg, args.note)
        prior = check_chain(cfg)
        label, ref, extra, question = _target(cfg, args)
        same = [r for r in prior if r.get("ref") == ref]
        if same and same[-1].get("label") == label:
            print("already labelled")
            return 0
        if same:
            print(f"warning: {ref} was labelled {_clean(same[-1].get('label'))} before; the newest row wins")
        _need(ask(question).lower() in ("y", "yes"), "not labelled")
        _, n = append_row(cfg, label, ref, note, **extra)
        print(f"ok: labelled {ref} (row {n})")
        return 0
    except Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
