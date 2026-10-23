"""Code validates what the Reader said (W2): `validate(reader_out, item, stats=None) -> {class, title, quote} | None`.

The quote is the item's line at `quote_line_index` and must be a verbatim line of the sanitized text (a substring of it, at most 400
characters, unchanged by `store.clean`): the model never supplies quote text, only a line number. The class is FORCED to `lead` for a tier-1
item: tier-1 evidence never yields a diff. A Reader that asks for `model_pin` on a tier-1 item is a canary signal whether or not it calls the
item relevant (checked BEFORE the relevant gate): rejected, one log line (no text from the item or the answer), no proposal. The title is the
one model-touched free text: separators (newline, CR, tab, any whitespace) become one space, control, format and line-separator characters are
removed with `store.clean`, lone surrogates and noncharacters dropped (`bare`), at most 120, non-empty, and never in the form of a code-made title
(`Replace ...`, `Retirement ...`). Anything off schema is None and a `rejected` count; a well-formed answer that is not relevant is None and not
counted, so this is the one place that decides. No regular expression runs over the text.
"""

from __future__ import annotations

import logging
import unicodedata

from . import reader
from .store import clean

LOG = logging.getLogger("ace_playbook.watch")
CODE_MADE = ("replace ", "retirement with no successor named")           # the forms `pin.model_pin` writes; a model title must not pose as one


def bare(text: str) -> str:
    """`text` without lone surrogates (category Cs) and noncharacters (U+FDD0-U+FDEF, U+FFFE/U+FFFF and the other plane-end pairs): dropped, not spaced."""
    return "".join(c for c in text if unicodedata.category(c) != "Cs" and not (0xFDD0 <= ord(c) <= 0xFDEF or ord(c) & 0xFFFE == 0xFFFE))


def _drop(stats: dict | None, key: str = "rejected") -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def validate(out: object, item: dict, stats: dict | None = None) -> dict | None:
    """The validated `{class: "lead", title, quote}` for a relevant answer, else None (counted as `rejected` when the answer is off schema)."""
    if not (isinstance(out, dict) and set(out) == reader.KEYS and type(out["relevant"]) is bool and type(out["quote_line_index"]) is int
            and isinstance(out["class"], str) and out["class"] in reader.CLASSES and isinstance(out["title"], str)):
        _drop(stats)
        return None
    if out["class"] == "model_pin":                                      # tier-1 evidence never yields a diff: asking for a pin is the canary
        LOG.warning("reader: a model_pin was requested for a tier-1 item; rejected")
        _drop(stats, "signals")
        _drop(stats)
        return None
    lines, idx = reader.lines_of(item["text"]), out["quote_line_index"]
    spaced = " ".join(out["title"][:reader.MAX_TITLE + 1].split())          # a removed separator is a space, never a join: runs collapse
    title = " ".join(bare(clean(spaced, reader.MAX_TITLE + 1)).split())
    quote = lines[idx].strip()[:reader.MAX_LINE] if 0 <= idx < len(lines) else ""       # a line of the sanitized text: a substring of it by construction
    # the cleaned title's upper bound is implied by the raw-length check (cleaning only removes and collapses); it stays as a cheap second guard
    ok = (0 < len(title) <= reader.MAX_TITLE and len(out["title"]) <= reader.MAX_TITLE and not title.lower().startswith(CODE_MADE)
          and quote != "" and clean(quote, reader.MAX_LINE) == quote)
    if not ok:
        _drop(stats)
        return None
    return {"class": "lead", "title": title, "quote": quote} if out["relevant"] else None
