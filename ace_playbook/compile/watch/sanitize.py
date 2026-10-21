"""Visible text only (design 4.1: about 70 % of real injections hide in non-rendered HTML, so nothing but visible text goes on).

`strip_hidden(html)` drops `<script>`, `<style>`, `<template>`, `<noscript>`, comments, any element (and its whole subtree) that carries
`hidden`, `aria-hidden="true"` or an inline `display:none`, `visibility:hidden`, `font-size:0` or `opacity:0` style, and the zero-width
(U+200B to U+200D, U+2060, U+FEFF) and bidirectional-override characters. An element that is never closed stays hidden to the end of the
input (fail closed). The result is plain text, one non-empty line per block. Stdlib only.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿‪‫‬‭‮⁦⁧⁨⁩"))
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
SKIP = frozenset(("script", "style", "template", "noscript", "dialog", "svg", "math", "iframe", "object", "embed", "head", "title", "meta", "link"))
BLOCK = frozenset("address article aside blockquote dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header "
                  "li main nav ol p pre section table tbody td tfoot th thead tr ul".split())
SPACES = re.compile(r"[ \t\r\f\v\xa0]+")
CSS_COMMENT, CSS_ESCAPE = re.compile(r"/\*.*?(?:\*/|$)", re.S), re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\r\n\f]?|\\(.)", re.S)
NUMBER = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)([a-z%]*)")
SCALE0 = re.compile(r"scale(?:3d|x|y)?\((?:0+(?:\.0*)?|\.0+)[,)]")
CLEAR = re.compile(r"(?:rgba|hsla)\(.*,0*(?:\.0*)?\)|#[0-9a-f]{3}0|#[0-9a-f]{6}00")
FAR = {"px": 1000, "pt": 1000, "": 1000, "em": 100, "rem": 100, "ch": 100, "vw": 100, "vh": 100, "%": 100}


def _unescape(m: re.Match) -> str:
    if m.group(1):
        n = int(m.group(1), 16)
        return chr(n) if 0 < n <= 0x10FFFF and not 0xD800 <= n <= 0xDFFF else ""
    return m.group(2)


def _num(value: str) -> tuple[float, str] | None:
    m = NUMBER.fullmatch(value)
    return (float(m.group(1)), m.group(2)) if m else None


def _zero(value: str) -> bool:
    n = _num(value)
    return n is not None and n[0] == 0


def style_hidden(style: str) -> bool:
    """True when an inline `style` hides its element. The text is normalised first (CSS comments removed, CSS escapes such as `\\3A`
    decoded, lower-cased, whitespace and `!important` dropped), then each declaration is read: display:none, visibility:hidden or
    collapse, opacity or font-size zero in any spelling or unit, a zero width or height with overflow hidden, position absolute or
    fixed far off-screen, text-indent far negative, clip-path:inset(100%), transform:scale(0), a transparent color."""
    s = CSS_ESCAPE.sub(_unescape, CSS_COMMENT.sub("", style)).lower()
    decls = [tuple(d.partition(":")[::2]) for d in re.sub(r"\s+|!important", "", s).split(";")]
    decls = [(k, v) for k, v in decls if k and v]
    for k, v in decls:
        if ((k == "display" and v == "none") or (k == "visibility" and v in ("hidden", "collapse")) or (k == "opacity" and _zero(v.rstrip("%")))
                or (k == "font-size" and _zero(v)) or (k == "clip-path" and v.startswith(("inset(100%", "circle(0")))
                or (k == "transform" and SCALE0.search(v)) or (k == "color" and (v == "transparent" or CLEAR.fullmatch(v)))
                or (k == "text-indent" and (_num(v) or (0, ""))[0] <= -1000)):
            return True
    clipped = any(k.startswith("overflow") and v in ("hidden", "clip") for k, v in decls)
    away = any(k == "position" and v in ("absolute", "fixed") for k, v in decls)
    return (clipped and any(k in ("width", "height", "max-width", "max-height") and _zero(v) for k, v in decls)) or (
        away and any(k in ("left", "top", "right", "bottom") and (n := _num(v)) is not None and n[0] <= -FAR.get(n[1], 1000) for k, v in decls))


def plain(text: str) -> str:
    """`text` without invisible characters and without control characters other than newline and tab."""
    return "".join(c for c in text.translate(INVISIBLE) if c in "\n\t" or (c >= " " and c != "\x7f" and not "\x80" <= c <= "\x9f"))


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []                    # (tag, hidden: this element or an ancestor is)
        self.out: list[str] = []

    def _hidden(self) -> bool:
        return bool(self.stack) and self.stack[-1][1]

    def handle_starttag(self, tag, attrs) -> None:
        a = {k for k, _ in attrs}
        aria = [(v or "").strip().lower() == "true" for k, v in attrs if k == "aria-hidden"]       # a repeated attribute: any copy counts
        hid = (self._hidden() or tag in SKIP or (tag == "details" and "open" not in a) or "hidden" in a or "inert" in a or any(aria)
               or any(style_hidden(v or "") for k, v in attrs if k == "style"))
        if tag in VOID:
            self.out.append(" " if not hid and tag in ("br", "hr") else "")
            return
        self.stack.append((tag, hid))
        self.out.append("\n" if tag in BLOCK and not hid else "")

    def handle_startendtag(self, tag, attrs) -> None:
        if tag in VOID:
            self.handle_starttag(tag, attrs)                        # `<div/>` has no content to hide or show

    def handle_endtag(self, tag) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                self.out.append("\n" if tag in BLOCK and not self._hidden() else "")
                return

    def handle_data(self, data) -> None:
        if not self._hidden():
            self.out.append(data)


def strip_hidden(html: str) -> str:
    """The visible text of `html` (see the module docstring); "" when the parser gives up."""
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except Exception:                                               # a parser fault must never let raw markup through
        return ""
    lines = (SPACES.sub(" ", plain("".join(parser.out))).split("\n"))
    return "\n".join(s for s in (ln.strip() for ln in lines) if s)


def one_line(text: str, limit: int) -> str:
    """Visible text of `text` on one line, at most `limit` characters (for titles, links and summaries from feeds)."""
    return " ".join(strip_hidden(text).split())[:limit]
