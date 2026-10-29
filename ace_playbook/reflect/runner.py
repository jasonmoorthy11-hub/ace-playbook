"""Reflect one transcript: clean, scrub outbound, call Haiku, gate, mint deltas."""

from __future__ import annotations

import time
from pathlib import Path

from ..clean import clean_file
from ..compile import phase
from ..config import Config
from ..inject.ledger import append_jsonl, read_jsonl, shown_ids_for_session
from ..inject.sanitize import inert_reason
from ..keys import resolve_key
from ..scrub import LESSON, OUTBOUND, QuarantineRequired, quarantine, redact, scan
from ..store import DeltaLog, SchemaError, make_lesson_id
from ..store.schema import KINDS
from . import client as client_mod
from .prompt import SYSTEM, ReflectParseError, build_user, parse_reflection
from .provenance import check_provenance

TAG_COUNTERS = {"helpful": "helpful", "harmful": "harmful", "violated": "violated"}
MIN_USER_TURNS = 2
MAX_ATTEMPTS = 3
FROZEN = "frozen_arm_phase"
# the sealed marker (PREREGISTRATION.md condition (7)): the `ACE P-<id>: ` prefix of a family proposal's message
TAG = b"ACE P-"
MAX_SCAN_BYTES = 64 << 20


def retry_count(cfg: Config, session_id: str) -> int:
    return sum(1 for r in read_jsonl(cfg.reflections)
               if r.get("session_id") == session_id and r.get("status") == "retryable")


def is_final_http_error(res) -> bool:
    """4xx other than 429 will not succeed on a retry."""
    return 400 <= res.http_status < 500 and res.http_status != 429


def _fail(cfg: Config, sid: str, error: str, final: bool = False, **extra) -> dict:
    """Ledger a retryable failure; the third failure (or a final one) closes the session."""
    attempts = retry_count(cfg, sid) + 1
    if final:
        return _ledger(cfg, sid, "failed_final", error=error, attempts=attempts, **extra)
    if attempts >= MAX_ATTEMPTS:
        return _ledger(cfg, sid, "retry_exhausted", error=error, attempts=attempts, **extra)
    return _ledger(cfg, sid, "retryable", error=error, attempts=attempts, **extra)


def already_reflected(cfg: Config, session_id: str) -> bool:
    return any(r.get("session_id") == session_id and r.get("status") != "retryable"
               for r in read_jsonl(cfg.reflections))


def arm_frozen(cfg: Config, path: Path) -> bool:
    """During an arm phase (compile/phase.py) a transcript whose raw bytes carry `ACE P-` mints nothing (PREREGISTRATION.md, Pre-arm-start
    conditions (7)). Unreadable or over 64 MiB counts as carrying it. Minting is the only session-driven writer of lesson status: nothing
    here writes RETIRE/supersede/deactivate (store/promote.py is operator-driven; tests/test_arm_phase.py guards that)."""
    if not phase.active(cfg, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())):
        return False
    try:
        return path.stat().st_size > MAX_SCAN_BYTES or TAG in path.read_bytes()
    except OSError:
        return True


def _ledger(cfg: Config, session_id: str, status: str, **extra) -> dict:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "session_id": session_id, "status": status, **extra}
    append_jsonl(cfg.reflections, rec)
    client_mod.log_line(cfg, f"reflect session={session_id} status={status}")
    return rec


def _evidence(insight: dict) -> list:
    """The insight's `evidence` when the model gave a list, else []: null, a number or an object must not abort the sweep (S-1 F10)."""
    ev = insight.get("evidence")
    return ev if isinstance(ev, list) else []


def _quarantine(cfg: Config, session_id: str, insight: dict, reason: str, hits=()) -> None:
    """Record a rejected insight. Only text and applies_when are stored, and only masked;
    evidence quotes are scanned (so their secrets are masked everywhere) but never stored."""
    deny = tuple(cfg.deny_terms)
    text = str(insight.get("text", ""))
    when = str(insight.get("applies_when", ""))
    hits = list(hits)
    for e in _evidence(insight):
        if isinstance(e, dict):
            q = str(e.get("quote", ""))
            hits += scan(q, LESSON, deny) + scan(q, OUTBOUND, deny)
    quarantine(cfg.quarantine, "lesson", insight, f"{text}\n[applies_when] {when}", hits, reason,
               session_id, deny)


def _to_draft(cfg: Config, insight: dict, cwd: str, evidence: list[dict], session_id: str) -> dict:
    scope = ({"type": "global"} if insight.get("scope") == "global"
             else {"type": "project", "key": resolve_key(cwd or ".", cfg.engagements_path, cfg.fold_key_separators)})
    text = insight["text"].strip()
    return {"op": "ADD", "lesson_id": make_lesson_id(scope, text), "session_id": session_id,
            "fields": {"text": text, "scope": scope, "kind": insight["kind"],
                       "applies_when": str(insight["applies_when"]).strip(),
                       "evidence": evidence, "status": "provisional"}}


def gate_insight(cfg: Config, insight: dict, kept_turns, session_id: str) -> tuple[dict | None, str]:
    """Return (evidence-bearing verdict dict, reason). reason == '' means mint."""
    text, when = insight.get("text"), insight.get("applies_when")
    if not (isinstance(text, str) and isinstance(when, str)) or insight.get("kind") not in KINDS:
        return None, "malformed"
    deny = tuple(cfg.deny_terms)
    hits = scan(f"{text}\n{when}", LESSON, deny) + scan(f"{text}\n{when}", OUTBOUND, deny)
    if hits:
        return {"hits": hits}, "scrub_hit"
    quotes = [e.get("quote", "") for e in _evidence(insight) if isinstance(e, dict)]
    verdict = check_provenance([q for q in quotes if isinstance(q, str)], kept_turns, session_id,
                               f"{text} {when}")
    if not verdict.ok:
        return None, verdict.reason
    for e in verdict.evidence:
        hits += scan(e["quote"], LESSON, deny)
    if hits:
        return {"hits": hits}, "scrub_hit"
    if inert_reason(text, when):
        return None, "not_inert"
    return {"evidence": verdict.evidence}, ""


def _mint(cfg: Config, insights: list[dict], cleaned, state: dict, session_id: str, log=None):
    drafts, reasons, seen = [], [], set()
    log = log or DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    for i, ins in enumerate(insights):
        if i >= cfg.max_new_per_run:
            _quarantine(cfg, session_id, ins, "rate_limited")
            reasons.append("rate_limited")
            continue
        info, reason = gate_insight(cfg, ins, cleaned.kept_turns, session_id)
        if reason:
            _quarantine(cfg, session_id, ins, reason, (info or {}).get("hits", ()))
            reasons.append(reason)
            continue
        draft = _to_draft(cfg, ins, cleaned.cwd, info["evidence"], session_id)
        if draft["lesson_id"] in state or draft["lesson_id"] in seen:
            reasons.append("duplicate")
            continue
        try:
            log.check_draft(draft)
        except (SchemaError, QuarantineRequired) as e:
            _quarantine(cfg, session_id, ins, "schema_invalid" if isinstance(e, SchemaError) else "scrub_hit",
                        getattr(e, "hits", ()))
            reasons.append("schema_invalid" if isinstance(e, SchemaError) else "scrub_hit")
            continue
        seen.add(draft["lesson_id"])
        drafts.append(draft)
    return drafts, reasons


def _tag_drafts(tags: list[dict], shown: list[str], session_id: str) -> tuple[list[dict], int]:
    """(the NOOP drafts, the number of malformed tags): a tag whose `id` or `tag` is not a string is skipped and counted (S-1 F11)."""
    per: dict[str, dict[str, int]] = {sid: {"shown": 1} for sid in shown}
    bad = 0
    for t in tags:
        lid, tag = t.get("id"), t.get("tag")
        if not (isinstance(lid, str) and isinstance(tag, str)):
            bad += 1
            continue
        counter = TAG_COUNTERS.get(tag)
        if lid in per and counter:
            per[lid][counter] = per[lid].get(counter, 0) + 1
    return [{"op": "NOOP", "lesson_id": lid, "counters": c, "session_id": session_id}
            for lid, c in per.items()], bad


def reflect_transcript(cfg: Config, path: Path, client: client_mod.ReflectorClient,
                       force: bool = False) -> dict:
    """Reflect one transcript and append the outcome to the reflections ledger."""
    cleaned = clean_file(path, cfg.tool_result_max_chars, tuple(cfg.untrusted_path_markers))
    sid = cleaned.session_id
    if not force and already_reflected(cfg, sid):
        return {"session_id": sid, "status": "already_reflected"}
    if cleaned.user_turn_count < MIN_USER_TURNS:
        return _ledger(cfg, sid, "skipped_short")
    if arm_frozen(cfg, path):                  # no outbound call, no delta of any kind, and NO ledger row: the freeze defers, the session stays open
        client_mod.log_line(cfg, f"reflect session={sid} status={FROZEN}")
        return {"session_id": sid, "status": FROZEN}
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    try:
        if cfg.deltas.exists():
            log.verify()
    except SchemaError as e:
        client_mod.log_line(cfg, f"reflect session={sid} REFUSED delta log failed verification: {e}")
        return {"session_id": sid, "status": "refused_chain_invalid", "error": str(e)[:200]}
    state = log.state()
    shown = [i for i in shown_ids_for_session(cfg.injections, sid) if i in state]
    user = build_user(cleaned, [state[i] for i in shown])
    hits = scan(user, OUTBOUND, tuple(cfg.deny_terms))
    res = client.complete(SYSTEM, redact(user, hits) if hits else user)
    base = {"usage": res.usage, "model": cfg.model, "outbound_redactions": len(hits),
            "signals": {"corrections": cleaned.signals.user_corrections,
                        "hook_blocks": cleaned.signals.hook_blocks}}
    if res.status != "ok":
        if res.status == "error":
            return _fail(cfg, sid, res.error, final=is_final_http_error(res), category=res.category, **base)
        return _ledger(cfg, sid, res.status, error=res.error, category=res.category, **base)
    return _apply(cfg, log, state, cleaned, shown, res.text, base)


def _apply(cfg, log, state, cleaned, shown, text, base) -> dict:
    sid = cleaned.session_id
    try:
        parsed = parse_reflection(text)
    except ReflectParseError as e:
        return _ledger(cfg, sid, "parse_error", error=str(e), **base)
    drafts, reasons = _mint(cfg, parsed["insights"], cleaned, state, sid, log)
    tag_drafts, bad_tags = _tag_drafts(parsed["tags"], shown, sid)
    drafts += tag_drafts
    reasons += ["malformed_tag"] * bad_tags
    try:
        written = log.append_many(drafts, max_touched=cfg.max_touched_per_run) if drafts else []
        if written:
            log.rebuild_view(cfg.sqlite)
    except (SchemaError, QuarantineRequired) as e:
        return _fail(cfg, sid, f"write_error: {type(e).__name__}", **base)
    minted = [d["lesson_id"] for d in drafts if d["op"] == "ADD"]
    return _ledger(cfg, sid, "ok", minted=minted, rejected=reasons,
                   tags_applied=sum(1 for d in drafts if d["op"] == "NOOP"), **base)
