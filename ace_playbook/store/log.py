"""Append-only `deltas.jsonl` writer: schema validation, scrubbing, hash chain,
atomic whole-file writes."""

from __future__ import annotations

import copy
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..scrub import LESSON, QuarantineRequired, scan
from . import view
from .schema import (GENESIS, SCHEMA_VERSION, SchemaError, canonical, now_iso, record_hash,
                     string_fields, validate_draft, validate_record)


class DeltaLog:
    def __init__(self, path: Path, deny_terms: tuple[str, ...] = ()) -> None:
        self.path = Path(path)
        self.deny_terms = tuple(deny_terms)

    # -------------------------------------------------------------- reading
    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SchemaError(f"{self.path.name}:{n}: invalid JSON ({e.msg})") from None
        return out

    def verify(self) -> int:
        """Check schema and hash chain of every record; return the record count."""
        prev = GENESIS
        records = self.read()
        for r in records:
            validate_record(r)
            if r["prev_hash"] != prev:
                raise SchemaError(f"chain broken at {r['delta_id']}")
            prev = r["hash"]
        return len(records)

    def head_hash(self) -> str:
        recs = self.read()
        return recs[-1]["hash"] if recs else ""

    def state(self) -> dict[str, dict]:
        return view.fold(self.read())

    # -------------------------------------------------------------- writing
    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _scrub(self, draft: dict) -> None:
        for where, text in string_fields(draft):
            hits = scan(text, LESSON, self.deny_terms)
            if hits:
                raise QuarantineRequired(hits, where)

    def _atomic_write(self, records: list[dict]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(canonical(r) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        dfd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def append_many(self, drafts: list[dict], *, ts: str | None = None,
                    max_touched: int | None = None) -> list[dict]:
        """Validate every draft, then write all of them in one atomic replace.

        All-or-nothing: if any draft fails schema, semantic or scrub checks,
        nothing is written. `max_touched` caps distinct lessons touched by
        non-counter-only deltas (the merger's per-run budget).
        """
        with self._lock():
            existing = self.read()
            state = view.fold(existing)
            prev = existing[-1]["hash"] if existing else GENESIS
            touched = {d.get("lesson_id") for d in drafts
                       if d.get("lesson_id") and d.get("op") != "NOOP"}
            if max_touched is not None and len(touched) > max_touched:
                raise SchemaError(f"run touches {len(touched)} lessons (max {max_touched})")
            new: list[dict] = []
            for draft in drafts:
                validate_draft(draft)
                self._scrub(draft)
                stamp = ts or now_iso()
                view.apply_delta(state, copy.deepcopy(draft), stamp)
                rec = {"v": SCHEMA_VERSION, "ts": stamp, "prev_hash": prev,
                       **{k: v for k, v in draft.items() if v is not None}}
                rec["delta_id"] = "D-" + record_hash({**rec, "delta_id": ""})[:12]
                rec["hash"] = record_hash(rec)
                new.append(rec)
                prev = rec["hash"]
            if new:
                self._atomic_write(existing + new)
            return new

    def append(self, draft: dict, *, ts: str | None = None) -> dict:
        return self.append_many([draft], ts=ts)[0]

    def rebuild_view(self, db_path: Path) -> dict[str, dict]:
        return view.rebuild(self.read(), db_path)
