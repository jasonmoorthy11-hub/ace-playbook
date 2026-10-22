"""Characters that are invisible, reorder text or are control codes (shared by `sanitize` and `store`). Stdlib only.

`invisible(c)`: every Unicode format character (category Cf: zero-width, bidi marks and overrides, the invisible operators U+2061 to U+2064,
the soft hyphen, the tag characters U+E0000 to U+E007F, the Mongolian vowel separator and more), the whole Default_Ignorable_Code_Point set
(also its unassigned and reserved members: U+180B-180F, U+2065, U+FFF0-FFF8, U+E0080-E0FFF), the variation selectors, and the blank fillers
that render as nothing (U+3164, U+115F, U+1160, U+FFA0, U+2800, U+034F, U+17B4, U+17B5).
"""

from __future__ import annotations

import unicodedata

BLANKS = frozenset("\u00ad\u3164\u2800\u115f\u1160\uffa0\u034f\u17b4\u17b5\u180e\u061c")
IGNORABLE = ((0xAD, 0xAD), (0x34F, 0x34F), (0x61C, 0x61C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F),
             (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
             (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))


def invisible(c: str) -> bool:
    o = ord(c)
    return unicodedata.category(c) == "Cf" or c in BLANKS or any(a <= o <= b for a, b in IGNORABLE)


def control(c: str) -> bool:
    """A C0 or C1 control character other than newline and tab (and DEL)."""
    return (c < " " and c not in "\n\t") or "\x7f" <= c <= "\x9f"
