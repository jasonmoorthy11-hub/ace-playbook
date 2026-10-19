"""Fold the delta log into lesson state and (re)build the SQLite+FTS5 view.

The log is the only source of truth; the SQLite file is disposable.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from ..atomic import create_private
from .schema import (COUNTERS, FROZEN_STATUSES, LIVE_STATUSES, MAX_EVIDENCE, USER_ORIGINS,
                     SchemaError, make_lesson_id)

USED_COUNTERS = {"cited", "helpful", "violated"}


def _new_lesson(d: dict, ts: str) -> dict:
    f = d["fields"]
    scope = f["scope"]
    return {
        "id": d["lesson_id"], "text": f["text"].strip(),
        "scope_type": scope["type"], "scope_key": scope.get("key", ""),
        "kind": f["kind"], "applies_when": f["applies_when"].strip(),
        "status": f.get("status", "provisional"),
        "pinned": f.get("pinned", False), "holdout_exempt": f.get("holdout_exempt", False),
        "created": ts, "updated": ts,
        **{c: 0 for c in COUNTERS},
        "evidence": list(f["evidence"]), "merged_from": [], "superseded_by": "",
        "retire_reason": "",
    }


def _has_user_evidence(lesson: dict) -> bool:
    return any(e["origin"] in USER_ORIGINS for e in lesson["evidence"])


def _require(state: dict, lid: str, *, allow_frozen: bool = False) -> dict:
    if lid not in state:
        raise SchemaError(f"unknown lesson {lid}")
    if state[lid]["status"] in FROZEN_STATUSES and not allow_frozen:
        raise SchemaError(f"{lid} is {state[lid]['status']} and cannot be modified")
    return state[lid]


def _apply_patch(lesson: dict, f: dict) -> None:
    if "evidence" in f:  # append-only; added before the status check so it can count
        if len(lesson["evidence"]) + len(f["evidence"]) > MAX_EVIDENCE:
            raise SchemaError(f"{lesson['id']} evidence full (max {MAX_EVIDENCE})")
        lesson["evidence"] = lesson["evidence"] + [dict(e) for e in f["evidence"]]
    for k, v in f.items():
        if k == "evidence":
            continue
        if k == "status" and v in LIVE_STATUSES and not _has_user_evidence(lesson):
            raise SchemaError("live status requires user-origin evidence")
        lesson[k] = v.strip() if isinstance(v, str) and k in ("text", "applies_when") else v


def _apply_merge(state: dict, lesson: dict, f: dict, ts: str) -> None:
    for oid in f["merged_from"]:
        other = _require(state, oid)
        other["status"], other["superseded_by"], other["updated"] = "superseded", lesson["id"], ts
        have = {e["quote"] for e in lesson["evidence"]}
        lesson["evidence"] += [e for e in other["evidence"] if e["quote"] not in have][:5]
        lesson["evidence"] = lesson["evidence"][:5]
    lesson["merged_from"] = sorted(set(lesson["merged_from"]) | set(f["merged_from"]))
    if "text" in f:
        lesson["text"] = f["text"].strip()


def apply_delta(state: dict, d: dict, ts: str) -> None:
    """Apply one draft/record to `state` in place; raises SchemaError on semantic faults."""
    op, lid, f = d["op"], d.get("lesson_id"), d.get("fields", {})
    if op == "ADD":
        if lid != make_lesson_id(f["scope"], f["text"]):
            raise SchemaError("ADD lesson_id must equal make_lesson_id(scope, text)")
        if lid in state:
            raise SchemaError(f"{lid} already exists")
        state[lid] = _new_lesson(d, ts)
        return
    if lid is None:
        return
    counters_only = op == "NOOP"
    lesson = _require(state, lid, allow_frozen=counters_only)
    if op == "PATCH":
        _apply_patch(lesson, f)
    elif op == "MERGE":
        _apply_merge(state, lesson, f, ts)
    elif op == "NARROW":
        if "scope" in f:
            if lesson["scope_type"] != "global":
                raise SchemaError("only global lessons can be narrowed to a project")
            lesson["scope_type"], lesson["scope_key"] = "project", f["scope"]["key"]
        lesson["applies_when"] = f["applies_when"].strip()
    elif op == "RETIRE":
        lesson["status"] = f.get("to_status", "retired")
        lesson["retire_reason"] = f["reason"]
    used = False
    for c, n in (d.get("counters") or {}).items():
        lesson[c] += n
        used = used or c in USED_COUNTERS
    if not counters_only or used:
        lesson["updated"] = ts


def fold(records: Iterable[dict]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for r in records:
        apply_delta(state, r, r["ts"])
    return state


# ---------------------------------------------------------------- SQLite view
DDL = """
CREATE TABLE lessons (
  id TEXT PRIMARY KEY, text TEXT, scope_type TEXT, scope_key TEXT, kind TEXT,
  applies_when TEXT, status TEXT, pinned INTEGER, holdout_exempt INTEGER,
  created TEXT, updated TEXT, shown INTEGER, cited INTEGER, helpful INTEGER,
  harmful INTEGER, violated INTEGER, origins TEXT, merged_from TEXT,
  superseded_by TEXT, retire_reason TEXT);
CREATE VIRTUAL TABLE lessons_fts USING fts5(
  id UNINDEXED, text, applies_when, tokenize='porter unicode61');
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def rebuild(records: list[dict], db_path: Path) -> dict[str, dict]:
    """Rebuild the view from scratch into a temp file, then rename over `db_path`."""
    state = fold(records)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(f"{db_path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        os.close(create_private(tmp))  # sqlite would create it with the umask; accepts an empty file
        con = sqlite3.connect(tmp)
        try:
            con.executescript(DDL)
            for les in state.values():
                con.execute(
                    "INSERT INTO lessons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (les["id"], les["text"], les["scope_type"], les["scope_key"], les["kind"],
                     les["applies_when"], les["status"], int(les["pinned"]),
                     int(les["holdout_exempt"]), les["created"], les["updated"], les["shown"],
                     les["cited"], les["helpful"], les["harmful"], les["violated"],
                     json.dumps(sorted({e["origin"] for e in les["evidence"]})),
                     json.dumps(les["merged_from"]), les["superseded_by"], les["retire_reason"]))
                con.execute("INSERT INTO lessons_fts VALUES (?,?,?)",
                            (les["id"], les["text"], les["applies_when"]))
            head = records[-1]["hash"] if records else ""
            con.execute("INSERT INTO meta VALUES ('log_head', ?)", (head,))
            con.commit()
        finally:
            con.close()
        os.replace(tmp, db_path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # never leave a half-built view behind
        raise
    return state


def open_view(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def view_head(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    con = open_view(db_path)
    try:
        row = con.execute("SELECT value FROM meta WHERE key='log_head'").fetchone()
        return row["value"] if row else None
    finally:
        con.close()


def search(con: sqlite3.Connection, query: str, limit: int = 10) -> list[sqlite3.Row]:
    """FTS5 search over text and applies_when. Query terms are quoted, so user input
    can never inject FTS operators."""
    terms = re.findall(r"[A-Za-z0-9_]+", query)
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms)
    return con.execute(
        "SELECT l.* FROM lessons_fts f JOIN lessons l ON l.id = f.id "
        "WHERE lessons_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit)).fetchall()
