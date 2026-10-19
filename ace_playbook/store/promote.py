"""Human-only promotion of `provisional` lessons to `active`, and the provisional listing.

`promote` appends one PATCH whose evidence is a quote the operator typed at a terminal.
That typed quote (origin `user_message`) is the user-origin evidence the live-status rule
asks for, so a promotion cannot be produced by a hook, a pipe or a model.
"""

from __future__ import annotations

import argparse
import sys

from ..scrub import OUTBOUND, QuarantineRequired, scan
from . import DeltaLog
from .schema import SchemaError, canonical, unsafe_text_reason

QUOTE_MIN, QUOTE_MAX = 12, 400
SESSION_ID = "promote-cli"
LIST_CHARS = 80


def _refuse(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


def _check_quote(quote: str, deny_terms: tuple[str, ...]) -> str | None:
    """Return a one-line refusal reason, or None if the quote may be recorded."""
    if not QUOTE_MIN <= len(quote) <= QUOTE_MAX:
        return f"evidence quote must be {QUOTE_MIN}-{QUOTE_MAX} characters"
    reason = unsafe_text_reason(quote)
    if reason:
        return f"evidence quote not allowed: {reason}"
    hits = scan(quote, OUTBOUND, deny_terms)
    if hits:  # rule names only: never echo the matched secret or client name
        return "evidence quote failed scrub: " + ", ".join(sorted({h.rule for h in hits}))
    return None


def draft(lesson_id: str, quote: str) -> dict:
    return {"op": "PATCH", "lesson_id": lesson_id, "session_id": SESSION_ID,
            "fields": {"status": "active", "evidence": [
                {"quote": quote, "origin": "user_message", "session_id": SESSION_ID}]}}


def promote(log: DeltaLog, args: argparse.Namespace) -> int:
    """Validate and (unless --dry-run) append the promotion; 0 on success, 2 on refusal."""
    if not sys.stdin.isatty():
        return _refuse("promote needs an interactive terminal (stdin is not a TTY)")
    state = log.state()
    lesson = state.get(args.lesson_id)
    if lesson is None:
        return _refuse(f"unknown lesson {args.lesson_id}")
    if lesson["status"] != "provisional":
        return _refuse(f"{args.lesson_id} is {lesson['status']}, not provisional")
    quote = (args.evidence_quote or "").strip()
    bad = _check_quote(quote, log.deny_terms)
    if bad:
        return _refuse(bad)
    record = draft(args.lesson_id, quote)
    try:
        if args.dry_run:
            log.check_draft(record)
            print(canonical(record))
            return 0
        log.append(record)
    except (QuarantineRequired, SchemaError) as e:
        return _refuse(str(e))
    print(f"promoted {args.lesson_id} to active")
    return 0


def provisional(log: DeltaLog, limit: int | None) -> int:
    rows = [r for r in log.state().values() if r["status"] == "provisional"]
    rows.sort(key=lambda r: (r["created"], r["id"]), reverse=True)
    for r in rows[:limit]:
        print(f"{r['id']}  {r['text'][:LIST_CHARS]}")
    return 0
