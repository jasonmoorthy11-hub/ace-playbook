"""Visible text only (design 4.1: about 70 % of real injections hide in non-rendered HTML, so nothing but visible text goes on).

`strip_hidden(html)` drops the content of `<script>`, `<style>`, `<template>`, `<noscript>`, `<head>`, `<title>`, `<dialog>`, `<svg>`,
`<math>`, `<iframe>`, `<object>`, `<embed>`, `<canvas>`, `<audio>`, `<video>`, `<datalist>`, `<noembed>`, `<noframes>`, `<rp>`, a `<details>`
without `open`, comments, any element (and its whole subtree) that carries `hidden`, `inert`, `popover`, `aria-hidden="true"` or an inline
style that hides (`cssstyle.style_hidden`), any element matched by a SIMPLE hiding rule of a `<style>` block (a single `.class`, `#id` or
`tag`; media queries, descendant selectors and external stylesheets are not handled), and every invisible character (`chars.invisible`: all
Unicode format characters, tag characters, variation selectors and the blank fillers). An element that is never closed stays hidden to the end
of the input (fail closed); a formatting element closed implicitly by an outer end tag is re-opened hidden, as a browser's adoption agency would.
`markdown=True` is for Markdown pages: a raw-text opener such as `<script>` that is never closed is prose, not markup. The result is plain
text, one non-empty line per block. Stdlib only.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .chars import control, invisible
from .cssstyle import css_rules, style_hidden  # noqa: F401 - style_hidden is part of this module's surface

VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
SKIP = frozenset(("script style template noscript dialog svg math iframe object embed head title meta link canvas audio video datalist "
                  "noembed noframes rp").split())
FORMATTING = frozenset("a b big code em font i nobr s small strike strong tt u".split())
BLOCK = frozenset("address article aside blockquote dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header "
                  "li main nav ol p pre section table tbody td tfoot th thead tr ul".split())
SPACES = re.compile(r"[ \t\r\f\v\xa0]+")
STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.I | re.S)


def plain(text: str) -> str:
    """`text` without invisible characters and without control characters other than newline and tab (U+2028/2029 become newlines)."""
    return "".join("\n" if c in "  " else c for c in text if not invisible(c) and not control(c))


class _Text(HTMLParser):
    def __init__(self, rules: tuple) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[list] = []                                # [tag, hidden: this element or an ancestor is, hidden: this element is]
        self.out: list[str] = []
        self.classes, self.ids, self.tags = rules

    def _hidden(self) -> bool:
        return bool(self.stack) and self.stack[-1][1]

    def _own(self, tag: str, attrs: list) -> bool:
        names = {k for k, _ in attrs}
        values = {k: (v or "") for k, v in reversed(attrs)}
        aria = any((v or "").strip().lower() == "true" for k, v in attrs if k == "aria-hidden")       # a repeated attribute: any copy counts
        return bool(tag in SKIP or (tag == "details" and "open" not in names) or names & {"hidden", "inert", "popover"} or aria
                    or any(style_hidden(v or "") for k, v in attrs if k == "style")
                    or tag in self.tags or values.get("id") in self.ids or self.classes & set(values.get("class", "").split()))

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.rsplit(":", 1)[-1]
        own = self._own(tag, attrs)
        hid = self._hidden() or own
        if tag in VOID:
            self.out.append(" " if not hid and tag in ("br", "hr") else "")
            return
        self.stack.append([tag, hid, own])
        self.out.append("\n" if tag in BLOCK and not hid else "")

    def handle_startendtag(self, tag, attrs) -> None:
        if tag.rsplit(":", 1)[-1] in VOID:
            self.handle_starttag(tag, attrs)                        # `<div/>` has no content to hide or show

    def handle_endtag(self, tag) -> None:
        tag = tag.rsplit(":", 1)[-1]
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                popped = self.stack[i + 1:]
                del self.stack[i:]
                for t, hid, own in popped:                          # `<b><i hidden></b>X</i>`: the browser re-opens the hidden <i> around X
                    if t in FORMATTING and hid:
                        self.stack.append([t, self._hidden() or own, own])
                self.out.append("\n" if tag in BLOCK and not self._hidden() else "")
                return

    def handle_data(self, data) -> None:
        if not self._hidden():
            self.out.append(data)


def _unclosed_as_text(text: str) -> str:
    """Markdown mode: an opener of a raw-text element with no closing tag anywhere is prose (`the <script> tag`), so it is escaped."""
    for name in SKIP:
        if not re.search(rf"</{name}\s*>", text, re.I):
            text = re.sub(rf"<(?={name}\b)", "&lt;", text, flags=re.I)
    return text


def strip_hidden(html: str, markdown: bool = False) -> str:
    """The visible text of `html` (see the module docstring); "" when the parser gives up."""
    rules = css_rules("\n".join(STYLE_BLOCK.findall(html)))
    parser = _Text(rules)
    try:
        parser.feed(_unclosed_as_text(html) if markdown else html)
        parser.close()
    except Exception:                                               # a parser fault must never let raw markup through
        return ""
    lines = (SPACES.sub(" ", plain("".join(parser.out))).split("\n"))
    return "\n".join(s for s in (ln.strip() for ln in lines) if s)


def one_line(text: str, limit: int) -> str:
    """Visible text of `text` on one line, at most `limit` characters (for titles, links and summaries from feeds)."""
    return " ".join(strip_hidden(text).split())[:limit]
