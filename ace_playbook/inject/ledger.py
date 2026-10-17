"""Append-only operational ledgers (injections, citations). Not the lesson store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            yield rec


def shown_ids_for_session(injections: Path, session_id: str) -> list[str]:
    """Ids actually shown (not held out) in a session, first-seen order."""
    seen: dict[str, None] = {}
    for rec in read_jsonl(injections):
        if rec.get("session_id") == session_id:
            seen.update(dict.fromkeys(rec.get("shown_ids", [])))
    return list(seen)
