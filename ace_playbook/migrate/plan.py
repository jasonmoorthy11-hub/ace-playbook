"""Action resolution: turn classified records into ADD drafts, handoff rows and review rows.

Evidence provenance (judgment recorded here as the brief requires): the legacy files are
the user's own memory files on the user's machine, and the lesson text itself is the only
evidence that survives. Each migrated lesson therefore carries one evidence item whose
quote is the lesson text, with origin `user_file` and session id `legacy:<legacy id>`.
That origin lets a lesson pass the schema's live-status rule, but it is self-quoted,
model-written provenance, not user evidence. So a classified `keep_active` lands as
`provisional` (reason code `legacy_unverified`); only an override row with action
`keep_active`, the user's own decision, yields `active`. Any future evidence-strength
logic must not count `legacy:` evidence as user evidence.

No network access happens here. Paraphrase verdicts and actions come from the input files.
Every text that reaches the review packet or the handoff file is scrubbed and masked first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..inject.sanitize import inert_reason
from ..keys.resolver import normalize as normalize_key
from ..scrub import LESSON, QuarantineRequired, redact, scan
from ..store import DeltaLog, SchemaError, make_lesson_id
from .inputs import Legacy, valid_date
from .normalize import normalize

# keep_active > keep_dormant > mark_compiled when a paraphrase group merges.
RANK = {"keep_active": 0, "keep_dormant": 1, "mark_compiled": 2}
STORE_STATUS = {"keep_active": "provisional", "keep_dormant": "dormant", "mark_compiled": "dormant"}
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
    duplicate_ids: list[str] = field(default_factory=list)
    unmatched_overrides: list[str] = field(default_factory=list)
    overrides_applied: int = 0
    joined: int = 0
    rescued: int = 0
    forced_quarantine: int = 0
    undated: int = 0
    malformed_dates: int = 0
    handoff_masked: int = 0

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
    explicit: bool  # the action came from an override row
    members: list["_Item"] = field(default_factory=list)
    merged_into: str | None = None
    out: Outcome | None = None


def safe(text: str, deny_terms: tuple[str, ...]) -> tuple[str, list[str]]:
    """Scrub-mask any text bound for a private output; return it and the rule names that fired."""
    hits = scan(text, LESSON, deny_terms)
    out = redact(text, hits).replace("[REDACTED:deny_term]", "[deny-term]")
    for term in sorted(deny_terms, key=len, reverse=True):
        out = re.sub(re.escape(term), "[deny-term]", out, flags=re.IGNORECASE)
    return out, sorted({h.rule for h in hits})


def _row(log: DeltaLog, item_id: str, key: str, reason: str, text: str) -> dict:
    """A packet row with every string masked."""
    return {"id": item_id, "key": safe(key, log.deny_terms)[0], "reason": safe(reason, log.deny_terms)[0],
            "text": safe(text, log.deny_terms)[0]}


def _items(records: list[dict], legacy: Legacy, overrides: dict[str, dict], plan: Plan) -> list[_Item]:
    items: list[_Item] = []
    seen: set[str] = set()
    for rec in records:
        rid = rec["id"]
        if rid in seen:
            plan.duplicate_ids.append(rid)
            continue
        seen.add(rid)
        key = str(rec.get("key") or "key_unknown")
        ov = overrides.get(rid)
        plan.overrides_applied += 1 if ov else 0
        action = str(ov["action"] if ov else rec.get("proposed_action"))
        if not ov and rec.get("quarantine") is True and action != "quarantine":
            action = "quarantine"  # an override is the only release path
            plan.forced_quarantine += 1
        text = ov["text"] if ov and "text" in ov else legacy.texts.get(rid)
        plan.joined += 1 if rid in legacy.texts else 0
        raw = rec.get("created")
        created = valid_date(raw) or legacy.created.get(rid)
        plan.undated += 1 if created is None else 0
        bad = (raw not in (None, "") and not valid_date(raw)) or rid in legacy.bad_dates
        plan.malformed_dates += 1 if bad else 0
        item = _Item(rec, rid, key, action, text, created, bool(ov))
        if action == "skip":
            item.out = Outcome(rid, key, action, "skip", reason_code="skipped")
        elif action not in RANK and action not in OTHER_ACTIONS:
            item.out = Outcome(rid, key, action, "error", reason_code="unknown_action")
        elif text is None:
            item.out = Outcome(rid, key, action, "error", reason_code="missing_join")
            plan.missing_join.append(rid)
        elif rid not in legacy.texts:
            plan.rescued += 1
        items.append(item)
    plan.unmatched_overrides = sorted(set(overrides) - seen)
    return items


def _link_paraphrases(items: list[_Item]) -> None:
    """Tentatively link members to their lead (both must carry a storable action)."""
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


def _build(item: _Item, text: str, log: DeltaLog, known: set[str], plan: Plan) -> Outcome:
    group = [item, *item.members]
    action = min((i.action for i in group), key=RANK.__getitem__)
    promoted = action == "keep_active" and any(i.explicit and i.action == "keep_active" for i in group)
    out = Outcome(item.id, item.key, item.action, action, created=item.created)
    if len(text) < TEXT_MIN:
        out.reason_code = "too_short"
        return out
    if len(text) > TEXT_MAX:
        out.reason_code = "too_long"
        shown, rules = safe(text, log.deny_terms)
        note = f"{len(text)} chars" + (f"; scrub hits: {', '.join(rules)}" if rules else "")
        plan.packet["too_long"].append(_row(log, item.id, item.key, note, shown))
        return out
    scope, label = _scope(item.key), item.rec.get("label")
    when = _applies_when(item.rec.get("reason"), item.key)
    status = "active" if promoted else STORE_STATUS[action]
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
        shown, rules = safe(text, log.deny_terms)
        out.reason_code = "scrub_hit"
        found = ", ".join(sorted(set(rules) | {h.rule for h in e.hits}))
        plan.packet["scrub_hit"].append(
            _row(log, item.id, item.key, f"{e.where or 'text'}: {found}", shown))
        return out
    except SchemaError as e:
        out.reason_code = "schema_error"
        plan.packet["schema_error"].append(_row(log, item.id, item.key, str(e), text))
        return out
    out.lesson_id = lid
    if lid in known:
        out.reason_code = "already_present"
        return out
    known.add(lid)
    out.status, out.kind, out.draft = status, kind, draft
    why = []
    if action == "keep_active" and not promoted:
        out.reason_code = "legacy_unverified"
        why.append("legacy_unverified")
    if out.inert_reason and status == "provisional":
        why.append(f"inert: {out.inert_reason}")
    if status == "provisional":
        plan.packet["provisional"].append(_row(log, item.id, item.key, "; ".join(why), text))
    return out


def _resolve(item: _Item, log: DeltaLog, known: set[str], plan: Plan) -> Outcome:
    if item.out is not None:
        return item.out
    text = normalize(item.text or "")
    if item.action in RANK:
        return _build(item, text, log, known, plan)
    out = Outcome(item.id, item.key, item.action, item.action, created=item.created)
    if item.action == "route_to_handoff":
        out.reason_code = "handoff"
        shown, rules = safe(text, log.deny_terms)
        plan.handoff_masked += 1 if rules else 0
        plan.handoff.append({"id": item.id, "key": safe(item.key, log.deny_terms)[0],
                             "created": item.created, "text": shown})
    else:
        out.reason_code = item.action
        if item.action == "quarantine":
            pats = item.rec.get("quarantine_patterns")
            names = ", ".join(map(str, pats)) if isinstance(pats, list) and pats else "none"
            plan.packet["quarantine"].append(
                _row(log, item.id, item.key, "quarantined", f"<quarantined; patterns: {names}>"))
    return out


def build(records: list[dict], legacy: Legacy, overrides: dict[str, dict], log: DeltaLog) -> Plan:
    """Run every gate on every record. Reads the store, never writes it.

    Leads are resolved first. A member merges only into a lead that is stored or already
    present; if the lead fails a gate, its members are released and handled by their own
    actions, so nothing is lost with a lead that was never stored.
    """
    plan = Plan()
    items = _items(records, legacy, overrides, plan)
    _link_paraphrases(items)
    known = set(log.state())
    done: dict[str, Outcome] = {}
    for item in items:
        if item.merged_into is None:
            done[item.id] = _resolve(item, log, known, plan)
    for item in items:
        if item.merged_into is None:
            continue
        lead = done[item.merged_into]
        if lead.draft is not None or lead.reason_code == "already_present":
            lead.merged_from.append(item.id)
            done[item.id] = Outcome(item.id, item.key, item.action, "merged", reason_code="merged_member",
                                    merged_into=item.merged_into, created=item.created)
        else:
            item.merged_into = None
            done[item.id] = _resolve(item, log, known, plan)
    plan.outcomes = [done[i.id] for i in items]
    return plan
