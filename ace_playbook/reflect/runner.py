"""Reflect one transcript: clean, scrub outbound, call Haiku, gate, mint deltas."""

from __future__ import annotations

import time
from pathlib import Path

from ..clean import clean_file
from ..config import Config
from ..inject.ledger import append_jsonl, read_jsonl, shown_ids_for_session
from ..inject.sanitize import inert_reason
from ..keys import resolve_key
from ..scrub import LESSON, SECRETS, QuarantineRequired, quarantine, redact, scan
from ..store import DeltaLog, SchemaError, make_lesson_id
from ..store.schema import KINDS
from . import client as client_mod
from .prompt import SYSTEM, ReflectParseError, build_user, parse_reflection
from .provenance import check_provenance

TAG_COUNTERS = {"helpful": "helpful", "harmful": "harmful", "violated": "violated"}
MIN_USER_TURNS = 2


def already_reflected(cfg: Config, session_id: str) -> bool:
    return any(r.get("session_id") == session_id and r.get("status") != "retryable"
               for r in read_jsonl(cfg.reflections))


def _ledger(cfg: Config, session_id: str, status: str, **extra) -> dict:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "session_id": session_id, "status": status, **extra}
    append_jsonl(cfg.reflections, rec)
    client_mod.log_line(cfg, f"reflect session={session_id} status={status}")
    return rec


def _quarantine(cfg: Config, session_id: str, insight: dict, reason: str, hits=()) -> None:
    text = str(insight.get("text", ""))
    quarantine(cfg.quarantine, "lesson", insight, text, list(hits), reason, session_id)


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
    quotes = [e.get("quote", "") for e in insight.get("evidence", []) if isinstance(e, dict)]
    verdict = check_provenance([q for q in quotes if isinstance(q, str)], kept_turns, session_id)
    if not verdict.ok:
        return None, verdict.reason
    hits = scan(f"{text}\n{when}", LESSON, tuple(cfg.deny_terms))
    for e in verdict.evidence:
        hits += scan(e["quote"], LESSON, tuple(cfg.deny_terms))
    if hits:
        return {"hits": hits}, "scrub_hit"
    if inert_reason(text, when):
        return None, "not_inert"
    return {"evidence": verdict.evidence}, ""


def _mint(cfg: Config, insights: list[dict], cleaned, state: dict, session_id: str):
    drafts, reasons = [], []
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
        if draft["lesson_id"] in state:
            reasons.append("duplicate")
            continue
        drafts.append(draft)
    return drafts, reasons


def _tag_drafts(tags: list[dict], shown: list[str], session_id: str) -> list[dict]:
    per: dict[str, dict[str, int]] = {sid: {"shown": 1} for sid in shown}
    for t in tags:
        lid, counter = t.get("id"), TAG_COUNTERS.get(t.get("tag"))
        if lid in per and counter:
            per[lid][counter] = per[lid].get(counter, 0) + 1
    return [{"op": "NOOP", "lesson_id": lid, "counters": c, "session_id": session_id}
            for lid, c in per.items()]


def reflect_transcript(cfg: Config, path: Path, client: client_mod.ReflectorClient,
                       force: bool = False) -> dict:
    """Reflect one transcript and append the outcome to the reflections ledger."""
    cleaned = clean_file(path, cfg.tool_result_max_chars, tuple(cfg.untrusted_path_markers))
    sid = cleaned.session_id
    if not force and already_reflected(cfg, sid):
        return {"session_id": sid, "status": "already_reflected"}
    if cleaned.user_turn_count < MIN_USER_TURNS:
        return _ledger(cfg, sid, "skipped_short")
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    state = log.state()
    shown = [i for i in shown_ids_for_session(cfg.injections, sid) if i in state]
    user = build_user(cleaned, [state[i] for i in shown])
    hits = scan(user, SECRETS)
    res = client.complete(SYSTEM, redact(user, hits) if hits else user)
    base = {"usage": res.usage, "model": cfg.model, "outbound_redactions": len(hits),
            "signals": {"corrections": cleaned.signals.user_corrections,
                        "hook_blocks": cleaned.signals.hook_blocks}}
    if res.status != "ok":
        status = "retryable" if res.status == "error" else res.status
        return _ledger(cfg, sid, status, error=res.error, category=res.category, **base)
    return _apply(cfg, log, state, cleaned, shown, res.text, base)


def _apply(cfg, log, state, cleaned, shown, text, base) -> dict:
    sid = cleaned.session_id
    try:
        parsed = parse_reflection(text)
    except ReflectParseError as e:
        return _ledger(cfg, sid, "parse_error", error=str(e), **base)
    drafts, reasons = _mint(cfg, parsed["insights"], cleaned, state, sid)
    drafts += _tag_drafts(parsed["tags"], shown, sid)
    try:
        written = log.append_many(drafts, max_touched=cfg.max_touched_per_run) if drafts else []
        if written:
            log.rebuild_view(cfg.sqlite)
    except (SchemaError, QuarantineRequired) as e:
        return _ledger(cfg, sid, "write_error", error=type(e).__name__, **base)
    minted = [d["lesson_id"] for d in drafts if d["op"] == "ADD"]
    return _ledger(cfg, sid, "ok", minted=minted, rejected=reasons,
                   tags_applied=sum(1 for d in drafts if d["op"] == "NOOP"), **base)
