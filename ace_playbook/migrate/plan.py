"""Action resolution: turn classified records into ADD drafts, handoff rows and review rows.

Evidence provenance (judgment recorded here as the brief requires): the legacy files are
the user's own memory files on the user's machine, and the lesson text itself is the only
evidence that survives. Each migrated lesson therefore carries one evidence item whose
quote is the lesson text, with origin `user_file` and session id `legacy:<legacy id>`.
That origin is what lets active lessons pass the schema's live-status rule. The id map
keeps the per-lesson provenance. This choice is to be reviewed by the orchestrator.

No network access happens here. Paraphrase verdicts and actions come from the input files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..inject.sanitize import inert_reason
from ..keys.resolver import normalize as normalize_key
from ..scrub import LESSON, QuarantineRequired, scan
from ..store import DeltaLog, SchemaError, make_lesson_id
from .inputs import Legacy, valid_date
from .normalize import normalize

# keep_active > keep_dormant > mark_compiled when a paraphrase group merges.
RANK = {"keep_active": 0, "keep_dormant": 1, "mark_compiled": 2}
STORE_STATUS = {"keep_active": "active", "keep_dormant": "dormant", "mark_compiled": "dormant"}
OTHER_ACTIONS = ("route_to_handoff", "retire", "drop_junk", "quarantine")
KIND_BY_LABEL = {"reusable_gotcha": "gotcha", "engagement_bound": "gotcha",
                 "dup_of_claude_md": "rule", "stale_version_fact": "fact"}
BUCKETS = ("provisional", "too_long", "scrub_hit", "schema_error", "quarantine")
TEXT_MIN, TEXT_MAX, WHEN_MIN, WHEN_MAX = 8, 400, 3, 200


@dataclass
class Outcome:
    id: str
    key: str
    action_in: str
    action_out: str
    lesson_id: str | None = None
    status: str | None = None
    kind: str | None = None
    inert_reason: str | None = None
    reason_code: str | None = None
    merged_into: str | None = None
    merged_from: list[str] = field(default_factory=list)
    # not part of the id map
    draft: dict | None = None
    created: str | None = None

    def row(self) -> dict:
        return {k: getattr(self, k) for k in (
            "id", "key", "action_in", "action_out", "lesson_id", "status", "kind", "inert_reason",
            "reason_code", "merged_into", "merged_from")}


@dataclass
class Plan:
    outcomes: list[Outcome] = field(default_factory=list)
    handoff: list[dict] = field(default_factory=list)
    packet: dict[str, list[dict]] = field(default_factory=lambda: {b: [] for b in BUCKETS})
    missing_join: list[str] = field(default_factory=list)
    overrides_applied: int = 0

    def stored(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.draft is not None]


@dataclass
class _Item:
    rec: dict
    id: str
    key: str
    action: str
    text: str | None
    created: str | None
    members: list["_Item"] = field(default_factory=list)
    merged_into: str | None = None
    out: Outcome | None = None


def _mask(text: str, deny_terms: tuple[str, ...]) -> tuple[str, list[str]]:
    """Text with every scrub hit and deny term replaced, and the rule names that fired."""
    hits = scan(text, LESSON, deny_terms)
    for h in sorted(hits, key=lambda h: -len(h.match)):
        if h.match:
            text = text.replace(h.match, "[deny-term]" if h.rule == "deny_term" else f"[hit:{h.rule}]")
    for term in sorted(deny_terms, key=len, reverse=True):
        text = re.sub(re.escape(term), "[deny-term]", text, flags=re.IGNORECASE)
    return text, sorted({h.rule for h in hits})


def _items(records: list[dict], legacy: Legacy, overrides: dict[str, dict], plan: Plan) -> list[_Item]:
    items = []
    for rec in records:
        rid = rec["id"]
        key = str(rec.get("key") or "key_unknown")
        ov = overrides.get(rid)
        plan.overrides_applied += 1 if ov else 0
        action = str(ov["action"] if ov else rec.get("proposed_action"))
        text = ov["text"] if ov and "text" in ov else legacy.texts.get(rid)
        if rid not in legacy.texts:
            plan.missing_join.append(rid)
        created = valid_date(rec.get("created")) or legacy.created.get(rid)
        item = _Item(rec, rid, key, action, text, created)
        known = action in RANK or action in OTHER_ACTIONS or action == "skip"
        if action == "skip":
            item.out = Outcome(rid, key, action, "skip", reason_code="skipped")
        elif not known:
            item.out = Outcome(rid, key, action, "error", reason_code="unknown_action")
        elif text is None:
            item.out = Outcome(rid, key, action, "error", reason_code="missing_join")
        items.append(item)
    return items


def _link_paraphrases(items: list[_Item]) -> None:
    """Members merge into their lead; the lead takes the strongest action in the group.

    A member only merges when it and its lead both carry a storable action. Otherwise it
    is handled on its own, so nothing is merged into a lead that will not be stored.
    """
    live = {i.id: i for i in items if i.out is None}
    for item in items:
        lead = live.get(item.rec.get("paraphrase_of") or "")
        if (item.out is not None or lead is None or lead is item or lead.rec.get("paraphrase_of")
                or item.action not in RANK or lead.action not in RANK):
            continue
        groups = {item.rec.get("paraphrase_group"), lead.rec.get("paraphrase_group")} - {None}
        if len(groups) > 1:
            continue
        item.merged_into = lead.id
        lead.members.append(item)


def _scope(key: str) -> dict:
    return {"type": "global"} if key == "global" else {"type": "project", "key": normalize_key(key)}


def _applies_when(reason: object, key: str) -> str:
    when = normalize(reason) if isinstance(reason, str) else ""
    if WHEN_MIN <= len(when) <= WHEN_MAX and inert_reason(when) is None:
        return when
    return f"migrated legacy lesson ({key})"


def _build(item: _Item, action: str, text: str, log: DeltaLog, known: set[str], plan: Plan) -> Outcome:
    out = Outcome(item.id, item.key, item.action, action, created=item.created,
                  merged_from=[m.id for m in item.members])
    if len(text) < TEXT_MIN:
        out.reason_code = "too_short"
        return out
    if len(text) > TEXT_MAX:
        out.reason_code = "too_long"
        plan.packet["too_long"].append(
            {"id": item.id, "key": item.key, "reason": f"{len(text)} chars", "text": text})
        return out
    scope, label = _scope(item.key), item.rec.get("label")
    when = _applies_when(item.rec.get("reason"), item.key)
    status = STORE_STATUS[action]
    out.inert_reason = inert_reason(text, when)
    if out.inert_reason and status == "active":
        status = "provisional"
    kind = "hook_candidate" if action == "mark_compiled" else KIND_BY_LABEL.get(label, "gotcha")
    lid = make_lesson_id(scope, text)
    draft = {"op": "ADD", "lesson_id": lid, "fields": {
        "text": text, "scope": scope, "kind": kind, "applies_when": when, "status": status,
        "evidence": [{"quote": text, "origin": "user_file", "session_id": f"legacy:{item.id}"}]}}
    try:
        log.check_draft(draft)
    except QuarantineRequired as e:
        masked, rules = _mask(text, log.deny_terms)
        out.reason_code = "scrub_hit"
        found = ", ".join(sorted(set(rules) | {h.rule for h in e.hits}))
        plan.packet["scrub_hit"].append(
            {"id": item.id, "key": item.key, "reason": f"{e.where or 'text'}: {found}", "text": masked})
        return out
    except SchemaError as e:
        out.reason_code = "schema_error"
        plan.packet["schema_error"].append(
            {"id": item.id, "key": item.key, "reason": str(e), "text": text})
        return out
    out.lesson_id = lid
    if lid in known:
        out.reason_code = "already_present"
        return out
    known.add(lid)
    out.status, out.kind, out.draft = status, kind, draft
    if status == "provisional":
        plan.packet["provisional"].append(
            {"id": item.id, "key": item.key, "reason": out.inert_reason, "text": text})
    return out


def _resolve(item: _Item, log: DeltaLog, known: set[str], plan: Plan) -> Outcome:
    if item.out is not None:
        return item.out
    text = normalize(item.text or "")
    if item.merged_into:
        return Outcome(item.id, item.key, item.action, "merged", reason_code="merged_member",
                       merged_into=item.merged_into, created=item.created)
    if item.action in RANK:
        action = min([item.action] + [m.action for m in item.members], key=RANK.__getitem__)
        return _build(item, action, text, log, known, plan)
    out = Outcome(item.id, item.key, item.action, item.action, created=item.created)
    if item.action == "route_to_handoff":
        out.reason_code = "handoff"
        plan.handoff.append({"id": item.id, "key": item.key, "created": item.created, "text": text})
    else:
        out.reason_code = item.action
        if item.action == "quarantine":
            pats = item.rec.get("quarantine_patterns")
            names = ", ".join(map(str, pats)) if isinstance(pats, list) and pats else "none"
            plan.packet["quarantine"].append({"id": item.id, "key": item.key, "reason": "quarantined",
                                              "text": f"<quarantined; patterns: {names}>"})
    return out


def build(records: list[dict], legacy: Legacy, overrides: dict[str, dict], log: DeltaLog) -> Plan:
    """Run every gate on every record. Reads the store, never writes it."""
    plan = Plan()
    items = _items(records, legacy, overrides, plan)
    _link_paraphrases(items)
    known = set(log.state())
    plan.outcomes = [_resolve(i, log, known, plan) for i in items]
    return plan
