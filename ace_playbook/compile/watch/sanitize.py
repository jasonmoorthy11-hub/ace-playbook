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
SKIP = frozenset(("script", "style", "template", "noscript"))
BLOCK = frozenset("address article aside blockquote dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header "
                  "li main nav ol p pre section table tbody td tfoot th thead tr ul".split())
STYLE = re.compile(r"display:none|visibility:hidden|font-size:0(?:px|pt|em|rem|%)?(?![\d.])|opacity:0(?:\.0*)?(?![\d.])")
SPACES = re.compile(r"[ \t\r\f\v\xa0]+")


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
        a = {k: v or "" for k, v in attrs}
        style = re.sub(r"\s+|!important", "", a.get("style", "").lower())
        hid = self._hidden() or tag in SKIP or "hidden" in a or a.get("aria-hidden", "").strip().lower() == "true" or bool(STYLE.search(style))
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
