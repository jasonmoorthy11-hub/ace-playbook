"""Human-only promotion of `provisional` lessons to `active`, and the provisional listing.

`promote` appends one PATCH whose evidence is the operator's own quote (origin `user_message`),
the user-origin evidence the live-status rule asks for. The TTY check proves only that a
terminal is attached to stdin, not that a person typed: a pty wrapper can pass it. It stops
hooks, pipes and plain subprocesses, not a determined local process. With no `--evidence-quote`
the quote and a confirmation are read from `/dev/tty`.
"""

from __future__ import annotations

import argparse
import copy
import sys

from .. import ttyio
from ..scrub import OUTBOUND, QuarantineRequired, scan
from . import DeltaLog, view
from .schema import ID_RE, SchemaError, canonical, now_iso, unsafe_text_reason

QUOTE_MIN, QUOTE_MAX = 12, 400
SESSION_ID = "promote-cli"
LIST_CHARS = 80


def _refuse(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


def _prompt(text: str) -> str:
    """Ask on the controlling terminal (not stdin); raises OSError/EOFError if unavailable."""
    return ttyio.ask(text)


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
    if not ID_RE.match(args.lesson_id):
        return _refuse("lesson id must look like L-xxxxxxxx")
    state = log.state()
    lesson = state.get(args.lesson_id)
    if lesson is None:
        return _refuse(f"unknown lesson {args.lesson_id}")
    if lesson["status"] != "provisional":
        return _refuse(f"{args.lesson_id} is {lesson['status']}, not provisional")
    interactive = args.evidence_quote is None
    try:
        if interactive:
            print(f"{args.lesson_id}  {lesson['text'][:LIST_CHARS]}", file=sys.stderr)
            args.evidence_quote = _prompt(
                f"Evidence quote (your own words, {QUOTE_MIN}-{QUOTE_MAX} chars): ")
        quote = args.evidence_quote.strip()
        bad = _check_quote(quote, log.deny_terms)
        if bad:
            return _refuse(bad)
        if interactive and not args.dry_run:
            if _prompt(f"Promote {args.lesson_id}? [y/N] ").strip().lower() not in ("y", "yes"):
                return _refuse("not confirmed")
    except (OSError, EOFError):
        return _refuse("could not read from the terminal")
    record = draft(args.lesson_id, quote)
    try:
        if args.dry_run:
            log.check_draft(record)
            # simulate the fold so view-level rules (evidence cap, legacy evidence) surface here
            view.apply_delta(copy.deepcopy(state), copy.deepcopy(record), now_iso())
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
