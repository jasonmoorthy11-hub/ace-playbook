"""Store writes (--apply) and the four private outputs written in both modes.

Outputs go to --out-dir only and hold lesson text (the review packet and the handoff
file), so they are private: never commit them. Only the report and the id map are
text-free. The two Markdown files carry the mode in their first line; the JSONL files
stay one record per line so they can be read back as data.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..scrub import QuarantineRequired
from ..store import KINDS, DeltaLog, SchemaError
from ..store.schema import now_iso
from .inputs import Legacy
from .plan import BUCKETS, OTHER_ACTIONS, RANK, Plan

STATUSES = ("active", "dormant", "provisional")
NOT_STORED = ("too_long", "too_short", "scrub_hit", "schema_error", "already_present", "skipped", "retire",
              "drop_junk", "quarantine", "merged_member", "handoff", "missing_join", "unknown_action",
              "apply_failed")
ACTION_ORDER = (*RANK, *OTHER_ACTIONS, "skip", "merged", "error")


@dataclass
class StoreDelta:
    """Store state around an --apply run."""
    head_before: str
    head_after: str
    count_before: int
    count_after: int


def commit(plan: Plan, log: DeltaLog) -> None:
    """Append the stored drafts, one batch per legacy date (ascending), undated records last.

    A batch the log refuses is left unwritten and its records are marked apply_failed.
    """
    batches: dict[str | None, list] = {}
    for o in plan.stored():
        batches.setdefault(o.created, []).append(o)
    order = sorted(d for d in batches if d) + ([None] if None in batches else [])
    for day in order:
        outs = batches[day]
        try:
            log.append_many([o.draft for o in outs], ts=f"{day}T00:00:00Z" if day else now_iso())
        except (SchemaError, QuarantineRequired):
            for o in outs:
                o.reason_code, o.draft, o.status, o.kind, o.lesson_id = "apply_failed", None, None, None, None


def _lines(title: str, counts: dict[str, int], order: tuple[str, ...] = ()) -> list[str]:
    keys = [k for k in order if k in counts] + sorted(k for k in counts if k not in order)
    return [f"## {title}", *(f"- {k}: {counts[k]}" for k in keys), ""]


def _report(plan: Plan, legacy: Legacy, n_records: int, notes: list[str], mode: str,
            store: StoreDelta | None) -> str:
    stored = plan.stored()
    dry = store is None
    lines = [f"# Migration report ({mode})", ""]
    lines += ["## Inputs", f"- legacy files: {len(legacy.files)}",
              f"- legacy files unreadable: {len(legacy.unreadable)}",
              f"- legacy lessons indexed: {len(legacy.texts)}", f"- classified records: {n_records}",
              f"- joined: {n_records - len(plan.missing_join)}", f"- missing join: {len(plan.missing_join)}",
              f"- overrides applied: {plan.overrides_applied}"]
    lines += [f"  - no legacy text for {i}" for i in plan.missing_join]
    lines += [f"- input note: {n}" for n in notes] + [""]
    n_in = Counter(o.action_in for o in plan.outcomes)
    n_out = Counter(o.action_out for o in plan.outcomes)
    lines += ["## Actions, in vs out"]
    lines += [f"- {a}: in {n_in.get(a, 0)}, out {n_out.get(a, 0)}"
              for a in [*ACTION_ORDER, *sorted((set(n_in) | set(n_out)) - set(ACTION_ORDER))]
              if n_in.get(a) or n_out.get(a)] + [""]
    verb = "would be stored" if dry else "stored"
    by_status, by_kind = Counter(o.status for o in stored), Counter(o.kind for o in stored)
    lines += [f"## Lessons {verb}, by status", *(f"- {s}: {by_status.get(s, 0)}" for s in STATUSES), ""]
    lines += [f"## Lessons {verb}, by kind", *(f"- {k}: {by_kind.get(k, 0)}" for k in KINDS), ""]
    reasons = Counter(o.reason_code for o in plan.outcomes if o.reason_code)
    lines += ["## Not stored, by reason", *(f"- {r}: {reasons.get(r, 0)}" for r in NOT_STORED), ""]
    inert = Counter(o.inert_reason for o in plan.outcomes if o.inert_reason)
    lines += _lines("Inert reasons (provisional if active, else stays dormant)", dict(inert))
    members = sum(len(o.merged_from) for o in plan.outcomes)
    groups = sum(1 for o in plan.outcomes if o.merged_from)
    lines += ["## Paraphrase groups", f"- groups: {groups}", f"- members merged: {members}", ""]
    lines += ["## Handoff", f"- handoff records: {len(plan.handoff)}", ""]
    lines += ["## Store"]
    if store is None:
        lines += ["- store untouched (dry run)"]
    else:
        lines += [f"- head hash before: {store.head_before or '(empty)'}",
                  f"- head hash after: {store.head_after or '(empty)'}",
                  f"- record count delta: {store.count_after - store.count_before}"]
    return "\n".join(lines) + "\n"


def _packet(plan: Plan, mode: str) -> str:
    lines = [f"# Review packet ({mode})", "", "Private: holds lesson text. Never commit this file.", ""]
    for bucket in BUCKETS:
        rows = plan.packet[bucket]
        lines += [f"## {bucket} ({len(rows)})", ""]
        for r in rows:
            lines.append(f"- {r['id']} | {r['key']} | {r['reason']} | {r['text']}")
        lines.append("")
    return "\n".join(lines)


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)


def write_outputs(plan: Plan, legacy: Legacy, n_records: int, notes: list[str], out_dir: Path,
                  store: StoreDelta | None) -> None:
    mode = "DRY RUN" if store is None else "APPLY"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "MIGRATION_REPORT.md", _report(plan, legacy, n_records, notes, mode, store))
    _write(out_dir / "review_packet.md", _packet(plan, mode))
    _write(out_dir / "id_map.jsonl", "".join(json.dumps(o.row()) + "\n" for o in plan.outcomes))
    _write(out_dir / "handoff.jsonl", "".join(json.dumps(r) + "\n" for r in plan.handoff))
