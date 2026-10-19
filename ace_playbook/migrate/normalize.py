"""Text normalizer: legacy lesson text -> printable ASCII on one line.

NFKC first, then a fixed character map, then every remaining character outside
0x20..0x7E becomes a space, then whitespace collapses to single spaces and the
result is stripped. Emoji and other symbols are dropped, never transliterated.
"""

from __future__ import annotations

import unicodedata

CHAR_MAP = {
    "—": "-", "–": "-", "−": "-",              # em dash, en dash, minus
    "→": " to ", "⇒": " to ", "⟶": " to ",     # arrows: words, never "->" (">" fails the
    "←": " from ",                                  # injector's angle_brackets rule)
    "‘": "'", "’": "'",                             # curly single quotes
    "“": '"', "”": '"',                             # curly double quotes
    "…": "...",
    "×": "x",
    "≥": " at least ", "≤": " at most ", "≠": "!=",  # no "<" or ">" in output
    " ": " ", " ": " ", " ": " ",              # NBSP, thin, narrow NBSP
    "•": "-", "·": "-",                             # bullet, middle dot
    "✓": "ok", "✔": "ok",
    "✗": "x", "✘": "x",
}


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text)
    mapped = "".join(CHAR_MAP.get(c, c) for c in folded)
    ascii_only = "".join(c if 0x20 <= ord(c) <= 0x7E else " " for c in mapped)
    return " ".join(ascii_only.split())
