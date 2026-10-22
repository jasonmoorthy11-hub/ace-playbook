"""Characters that are invisible, reorder text or are control codes (shared by `sanitize` and `store`). Stdlib only.

`invisible(c)`: every Unicode format character (category Cf: zero-width, bidi marks and overrides, the invisible operators U+2061 to U+2064,
the soft hyphen, the tag characters U+E0000 to U+E007F, the Mongolian vowel separator and more), the variation selectors, and the blank
fillers that render as nothing (U+3164, U+115F, U+1160, U+FFA0, U+2800, U+034F, U+17B4, U+17B5).
"""

from __future__ import annotations

import unicodedata

BLANKS = frozenset("­ㅤ⠀ᅟᅠﾠ͏឴឵᠎؜")


def invisible(c: str) -> bool:
    o = ord(c)
    return (unicodedata.category(c) == "Cf" or c in BLANKS or 0xFE00 <= o <= 0xFE0F or 0xE0000 <= o <= 0xE007F or 0xE0100 <= o <= 0xE01EF)


def control(c: str) -> bool:
    """A C0 or C1 control character other than newline and tab (and DEL)."""
    return (c < " " and c not in "\n\t") or "\x7f" <= c <= "\x9f"
