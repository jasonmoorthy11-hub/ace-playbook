"""Delta schema: the only way a lesson ever changes is one of these records."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

SCHEMA_VERSION = 1
GENESIS = "0" * 64

OPS = ("ADD", "PATCH", "MERGE", "NARROW", "RETIRE", "NOOP")
STATUSES = ("active", "dormant", "superseded", "retired", "compiled", "quarantined", "provisional")
KINDS = ("gotcha", "rule", "preference", "workflow", "fact", "hook_candidate")
ORIGINS = ("user_message", "user_file", "tool_output")
USER_ORIGINS = frozenset({"user_message", "user_file"})
COUNTERS = ("shown", "cited", "helpful", "harmful", "violated")
RETIRE_TARGETS = ("retired", "compiled", "dormant", "quarantined")
LIVE_STATUSES = frozenset({"active", "provisional"})
FROZEN_STATUSES = frozenset({"retired", "superseded"})

ID_RE = re.compile(r"^L-[0-9a-f]{8}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,63}$")

TEXT_MIN, TEXT_MAX = 8, 400
WHEN_MIN, WHEN_MAX = 3, 200
QUOTE_MIN, QUOTE_MAX = 8, 400
MAX_EVIDENCE = 5
MAX_COUNTER_INCR = 1000

ADD_FIELDS = {"text", "scope", "kind", "applies_when", "evidence", "status", "pinned",
              "holdout_exempt"}
PATCH_FIELDS = {"text", "kind", "applies_when", "pinned", "holdout_exempt", "status"}


class SchemaError(ValueError):
    """A delta failed validation; the message lists every problem found."""


def make_lesson_id(scope: dict, text: str) -> str:
    key = scope.get("key", "") if scope.get("type") == "project" else ""
    digest = hashlib.sha256(f"{scope.get('type')}|{key}|{text.strip()}".encode()).hexdigest()
    return "L-" + digest[:8]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


FENCE = "==="


def unsafe_text_reason(text: str) -> str | None:
    """Fail-closed rule for injectable lesson text: printable ASCII only.

    No newlines (a lesson must stay on its own line of the advisory block), no
    non-ASCII letters, punctuation, spaces or invisible characters (so no look-alikes,
    fillers, separators or combining marks), and no "===" fence marker.
    """
    for c in text:
        if c in "\n\r":
            return "newline"
        if not 0x20 <= ord(c) <= 0x7E:
            return "non_ascii_or_control"
    if FENCE in text:
        return "fence_marker"
    return None


def _inj_text(f: dict, keys: tuple[str, ...], errs: list[str]) -> None:
    for k in keys:
        if isinstance(f.get(k), str):
            reason = unsafe_text_reason(f[k])
            if reason:
                errs.append(f"{k} not allowed in injectable text: {reason}")


def _str(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, str) and lo <= len(v.strip()) and len(v) <= hi


def _check_scope(scope: Any, errs: list[str]) -> None:
    if not isinstance(scope, dict) or set(scope) - {"type", "key"}:
        errs.append("scope must be {type, key?}")
    elif scope.get("type") == "global":
        if "key" in scope:
            errs.append("global scope takes no key")
    elif scope.get("type") == "project":
        if not (isinstance(scope.get("key"), str) and KEY_RE.match(scope["key"])):
            errs.append("project scope needs a normalized key")
    else:
        errs.append("scope.type must be global|project")


def _check_evidence(ev: Any, errs: list[str]) -> list[str]:
    origins: list[str] = []
    if not isinstance(ev, list) or not 1 <= len(ev) <= MAX_EVIDENCE:
        errs.append(f"evidence must be a list of 1..{MAX_EVIDENCE}")
        return origins
    for i, item in enumerate(ev):
        if not isinstance(item, dict) or set(item) - {"quote", "origin", "session_id"}:
            errs.append(f"evidence[{i}] must be {{quote, origin, session_id?}}")
            continue
        if not _str(item.get("quote"), QUOTE_MIN, QUOTE_MAX):
            errs.append(f"evidence[{i}].quote length out of range")
        if item.get("origin") not in ORIGINS:
            errs.append(f"evidence[{i}].origin must be one of {ORIGINS}")
        else:
            origins.append(item["origin"])
    return origins


def _check_add(f: dict, errs: list[str]) -> None:
    missing = {"text", "scope", "kind", "applies_when", "evidence"} - set(f)
    if missing:
        errs.append(f"ADD missing fields: {sorted(missing)}")
    if set(f) - ADD_FIELDS:
        errs.append(f"ADD unknown fields: {sorted(set(f) - ADD_FIELDS)}")
    if "text" in f and not _str(f["text"], TEXT_MIN, TEXT_MAX):
        errs.append("text length out of range")
    if "applies_when" in f and not _str(f["applies_when"], WHEN_MIN, WHEN_MAX):
        errs.append("applies_when length out of range")
    if "kind" in f and f["kind"] not in KINDS:
        errs.append(f"kind must be one of {KINDS}")
    _inj_text(f, ("text", "applies_when"), errs)
    if "scope" in f:
        _check_scope(f["scope"], errs)
    origins = _check_evidence(f["evidence"], errs) if "evidence" in f else []
    status = f.get("status", "provisional")
    if status not in STATUSES:
        errs.append(f"status must be one of {STATUSES}")
    elif status in LIVE_STATUSES and not USER_ORIGINS & set(origins):
        errs.append("live status requires evidence with a user_message or user_file origin")
    for flag in ("pinned", "holdout_exempt"):
        if flag in f and not isinstance(f[flag], bool):
            errs.append(f"{flag} must be bool")


def _check_patch(f: dict, errs: list[str]) -> None:
    if not f or set(f) - PATCH_FIELDS:
        errs.append(f"PATCH fields must be a non-empty subset of {sorted(PATCH_FIELDS)}")
        return
    if "text" in f and not _str(f["text"], TEXT_MIN, TEXT_MAX):
        errs.append("text length out of range")
    if "applies_when" in f and not _str(f["applies_when"], WHEN_MIN, WHEN_MAX):
        errs.append("applies_when length out of range")
    _inj_text(f, ("text", "applies_when"), errs)
    if "kind" in f and f["kind"] not in KINDS:
        errs.append("bad kind")
    if "status" in f and f["status"] not in STATUSES:
        errs.append("bad status")
    for flag in ("pinned", "holdout_exempt"):
        if flag in f and not isinstance(f[flag], bool):
            errs.append(f"{flag} must be bool")


def _check_other(op: str, lesson_id: Any, f: dict, errs: list[str]) -> None:
    if op == "MERGE":
        ids = f.get("merged_from")
        ok = (isinstance(ids, list) and 1 <= len(ids) <= 5 and len(set(ids)) == len(ids)
              and all(isinstance(i, str) and ID_RE.match(i) for i in ids)
              and lesson_id not in ids)
        if not ok or set(f) - {"merged_from", "text"}:
            errs.append("MERGE needs merged_from (1..5 unique ids, not the survivor) and optional text")
        if "text" in f and not _str(f["text"], TEXT_MIN, TEXT_MAX):
            errs.append("text length out of range")
        _inj_text(f, ("text",), errs)
    elif op == "NARROW":
        if not _str(f.get("applies_when"), WHEN_MIN, WHEN_MAX) or set(f) - {"applies_when", "scope"}:
            errs.append("NARROW needs applies_when and optional scope")
        _inj_text(f, ("applies_when",), errs)
        if "scope" in f:
            _check_scope(f["scope"], errs)
            if f["scope"].get("type") != "project":
                errs.append("NARROW may only narrow scope to a project")
    elif op == "RETIRE":
        if not _str(f.get("reason"), 3, 200) or set(f) - {"reason", "to_status"}:
            errs.append("RETIRE needs reason (3..200) and optional to_status")
        if f.get("to_status", "retired") not in RETIRE_TARGETS:
            errs.append(f"to_status must be one of {RETIRE_TARGETS}")
    elif op == "NOOP":
        if set(f) - {"reason"} or ("reason" in f and not _str(f["reason"], 0, 200)):
            errs.append("NOOP takes only an optional reason")


def validate_draft(d: Any) -> None:
    """Validate a delta as written by a producer (no envelope fields yet)."""
    errs: list[str] = []
    if not isinstance(d, dict):
        raise SchemaError("delta must be an object")
    allowed = {"op", "lesson_id", "fields", "counters", "session_id", "run_id"}
    if set(d) - allowed:
        errs.append(f"unknown keys: {sorted(set(d) - allowed)}")
    op, lid, f = d.get("op"), d.get("lesson_id"), d.get("fields", {})
    if op not in OPS:
        errs.append(f"op must be one of {OPS}")
    elif not isinstance(f, dict):
        errs.append("fields must be an object")
    else:
        if lid is None and op != "NOOP":
            errs.append("lesson_id required")
        if lid is not None and not (isinstance(lid, str) and ID_RE.match(lid)):
            errs.append("lesson_id must look like L-xxxxxxxx")
        if op == "ADD":
            _check_add(f, errs)
        elif op == "PATCH":
            _check_patch(f, errs)
        else:
            _check_other(op, lid, f, errs)
    _check_counters(d.get("counters"), op, lid, errs)
    for k in ("session_id", "run_id"):
        if d.get(k) is not None and not isinstance(d[k], str):
            errs.append(f"{k} must be a string")
    if errs:
        raise SchemaError("; ".join(errs))


def _check_counters(c: Any, op: Any, lid: Any, errs: list[str]) -> None:
    if c is None:
        return
    if op == "ADD":
        errs.append("ADD may not carry counters")
    if lid is None:
        errs.append("counters need a lesson_id")
    if not isinstance(c, dict) or not c or set(c) - set(COUNTERS):
        errs.append(f"counters must be a non-empty subset of {COUNTERS}")
    elif not all(isinstance(v, int) and not isinstance(v, bool)
                 and 1 <= v <= MAX_COUNTER_INCR for v in c.values()):
        errs.append("counter increments must be ints in 1..1000")


def validate_record(r: Any) -> None:
    """Validate a stored record: envelope plus the draft body."""
    if not isinstance(r, dict):
        raise SchemaError("record must be an object")
    env = {"v", "delta_id", "ts", "prev_hash", "hash"}
    if env - set(r):
        raise SchemaError(f"record missing envelope keys: {sorted(env - set(r))}")
    if r["v"] != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema version {r['v']}")
    validate_draft({k: v for k, v in r.items() if k not in env})
    if record_hash(r) != r["hash"]:
        raise SchemaError(f"hash mismatch at {r['delta_id']}")


def string_fields(draft: dict) -> list[tuple[str, str]]:
    """Every free-text value in a draft, for scrubbing."""
    f = draft.get("fields", {})
    out = [(k, f[k]) for k in ("text", "applies_when", "reason") if isinstance(f.get(k), str)]
    out += [(f"evidence[{i}]", e["quote"]) for i, e in enumerate(f.get("evidence", []))]
    return out
