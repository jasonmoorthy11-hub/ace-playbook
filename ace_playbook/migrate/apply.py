"""Store writes (--apply) and the four private outputs written in both modes.

Outputs go to --out-dir only and hold lesson text (the review packet and the handoff
file), so they are private: never commit them. Only the report and the id map are
text-free. The two Markdown files carry the mode in their first line; every JSONL row
carries a `mode` field.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..scrub import QuarantineRequired
from ..store import KINDS, DeltaLog, SchemaError
from ..store.schema import now_iso
from .inputs import Legacy
from .plan import BUCKETS, OTHER_ACTIONS, RANK, Outcome, Plan

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


def _fail(outs: list[Outcome]) -> None:
    for o in outs:
        o.reason_code, o.draft, o.status, o.kind, o.lesson_id = "apply_failed", None, None, None, None


def commit(plan: Plan, log: DeltaLog) -> None:
    """Append the stored drafts, one batch per legacy date (ascending), undated records last.

    A batch the log refuses (or cannot write) is left unwritten and its records are marked
    apply_failed. An unexpected error marks every batch not yet written, then propagates, so
    the caller's report never claims lessons that did not land.
    """
    batches: dict[str | None, list[Outcome]] = {}
    for o in plan.stored():
        batches.setdefault(o.created, []).append(o)
    order = sorted(d for d in batches if d) + ([None] if None in batches else [])
    for n, day in enumerate(order):
        outs = batches[day]
        try:
            log.append_many([o.draft for o in outs], ts=f"{day}T00:00:00Z" if day else now_iso())
        except (SchemaError, QuarantineRequired, OSError):
            _fail(outs)
        except BaseException:
            for rest in order[n:]:
                _fail(batches[rest])
            raise


def _lines(title: str, counts: dict[str, int]) -> list[str]:
    return [f"## {title}", *(f"- {k}: {counts[k]}" for k in sorted(counts)), ""]


def _inputs(plan: Plan, legacy: Legacy, n_records: int, notes: list[str]) -> list[str]:
    lines = ["## Inputs", f"- legacy files: {len(legacy.files)}",
             f"- legacy files unreadable: {len(legacy.unreadable)}",
             f"- legacy lessons indexed: {len(legacy.texts)}", f"- classified records: {n_records}",
             f"- joined: {plan.joined}", f"- missing join: {len(plan.missing_join)}"]
    lines += [f"  - no legacy text for {i}" for i in plan.missing_join]
    lines += [f"- rescued by override text: {plan.rescued}", f"- overrides applied: {plan.overrides_applied}"]
    lines += [f"- input note: {n}" for n in notes] + [""]
    lines += ["## Input checks", f"- duplicate classified ids ignored: {len(plan.duplicate_ids)}"]
    lines += [f"  - {i}" for i in plan.duplicate_ids]
    lines += [f"- overrides matching no record: {len(plan.unmatched_overrides)}"]
    lines += [f"  - {i}" for i in plan.unmatched_overrides]
    lines += [f"- legacy ids with differing text: {len(legacy.conflicts)} (first occurrence used)",
              f"- records with no usable date: {plan.undated}",
              f"- records with a malformed date: {plan.malformed_dates}",
              f"- forced to quarantine by flag: {plan.forced_quarantine}", ""]
    return lines


def _report(plan: Plan, legacy: Legacy, n_records: int, notes: list[str], mode: str,
            store: StoreDelta | None) -> str:
    stored = plan.stored()
    lines = [f"# Migration report ({mode})", ""] + _inputs(plan, legacy, n_records, notes)
    n_in = Counter(o.action_in for o in plan.outcomes)
    n_out = Counter(o.action_out for o in plan.outcomes)
    lines += ["## Actions, in vs out"]
    lines += [f"- {a}: in {n_in.get(a, 0)}, out {n_out.get(a, 0)}"
              for a in [*ACTION_ORDER, *sorted((set(n_in) | set(n_out)) - set(ACTION_ORDER))]
              if n_in.get(a) or n_out.get(a)] + [""]
    verb = "stored" if store else "would be stored"
    by_status, by_kind = Counter(o.status for o in stored), Counter(o.kind for o in stored)
    unverified = sum(1 for o in stored if o.reason_code == "legacy_unverified")
    lines += [f"## Lessons {verb}, by status"]
    for s in STATUSES:
        lines.append(f"- {s}: {by_status.get(s, 0)}")
        if s == "provisional":
            lines.append(f"  - legacy_unverified: {unverified}")
    lines += ["", f"## Lessons {verb}, by kind", *(f"- {k}: {by_kind.get(k, 0)}" for k in KINDS), ""]
    reasons = Counter(o.reason_code for o in plan.outcomes if o.draft is None and o.reason_code)
    lines += ["## Not stored, by reason", *(f"- {r}: {reasons.get(r, 0)}" for r in NOT_STORED), ""]
    lines += _lines("Inert reasons (provisional if it would be active, else stays dormant)",
                    dict(Counter(o.inert_reason for o in plan.outcomes if o.inert_reason)))
    lines += ["## Paraphrase groups", f"- groups: {sum(1 for o in plan.outcomes if o.merged_from)}",
              f"- members merged: {sum(len(o.merged_from) for o in plan.outcomes)}", ""]
    lines += ["## Handoff", f"- handoff records: {len(plan.handoff)}",
              f"- handoff rows with masked hits: {plan.handoff_masked}", "", "## Store"]
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
        lines += [f"- {r['id']} | {r['key']} | {r['reason']} | {r['text']}" for r in rows]
        lines.append("")
    return "\n".join(lines)


def _write(path: Path, body: str) -> None:
    """Create the file private from the start (0600), never widen then narrow."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(body)


def write_outputs(plan: Plan, legacy: Legacy, n_records: int, notes: list[str], out_dir: Path,
                  store: StoreDelta | None) -> None:
    mode = "DRY RUN" if store is None else "APPLY"
    tag = "dry-run" if store is None else "apply"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(out_dir / "MIGRATION_REPORT.md", _report(plan, legacy, n_records, notes, mode, store))
    _write(out_dir / "review_packet.md", _packet(plan, mode))
    _write(out_dir / "id_map.jsonl",
           "".join(json.dumps({"mode": tag, **o.row()}) + "\n" for o in plan.outcomes))
    _write(out_dir / "handoff.jsonl", "".join(json.dumps({"mode": tag, **r}) + "\n" for r in plan.handoff))
