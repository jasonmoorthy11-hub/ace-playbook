"""Visible text only (design 4.1: about 70 % of real injections hide in non-rendered HTML, so nothing but visible text goes on).

`strip_hidden(html)` drops the content of `<script>`, `<style>`, `<template>`, `<noscript>`, `<head>`, `<title>`, `<dialog>`, `<svg>`,
`<math>`, `<iframe>`, `<object>`, `<embed>`, `<canvas>`, `<audio>`, `<video>`, `<datalist>`, `<noembed>`, `<noframes>`, `<rp>`, a `<details>`
without `open`, comments, any element (and its whole subtree) that carries `hidden`, `inert`, `popover`, `aria-hidden="true"` or an inline
style that hides (`cssstyle.style_hidden`), any element matched by a SIMPLE hiding rule of a `<style>` block (a single `.class`, `#id` or
`tag`; media queries, descendant selectors and external stylesheets are not handled), and every invisible character (`chars.invisible`: all
Unicode format characters, tag characters, variation selectors and the blank fillers). An element that is never closed stays hidden to the end
of the input (fail closed); a formatting element closed implicitly by an outer end tag is re-opened hidden, as a browser's adoption agency would.
Tag names are compared as written (`</x:div>` does not close `<div>`: a browser ignores it); the inline-XML children of a feed arrive with
local names already. `markdown=True` is for Markdown pages: a raw-text opener (`<script>`, `<style>`, `<title>`, `<textarea>`, `<xmp>`,
`<noembed>`, `<noframes>`, `<iframe>`) is prose ONLY inside an inline code span or a fenced code block; anywhere else the rules above apply
as in HTML (a bare `<script>` hides to its closer, or to the end of the input). The result is plain text, one non-empty line per block.
Stdlib only.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
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
RAW_TEXT = "script|style|title|textarea|xmp|noembed|noframes|iframe"
RAW_OPENER = re.compile(rf"<(?=(?:{RAW_TEXT})(?:[\s/>]|$))", re.I)
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
TICKS, BLANK_LINE = re.compile(r"`+"), re.compile(r"\n[ \t]*\n")


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
        own = self._own(tag, attrs)
        hid = self._hidden() or own
        if tag in VOID:
            self.out.append(" " if not hid and tag in ("br", "hr") else "")
            return
        self.stack.append([tag, hid, own])
        self.out.append("\n" if tag in BLOCK and not hid else "")

    def handle_startendtag(self, tag, attrs) -> None:
        if tag in VOID:
            self.handle_starttag(tag, attrs)                        # `<div/>` has no content to hide or show

    def handle_endtag(self, tag) -> None:
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


def _prose(code: str) -> str:
    return RAW_OPENER.sub("&lt;", code)


def _spans(text: str) -> str:
    """`text` with the raw-text openers inside its inline code spans escaped (CommonMark: a backtick run closes at the next run of the same
    length in the same paragraph; an opener after a backslash is not one). One pass over the runs, so it is not quadratic."""
    runs = [(m.start(), m.end()) for m in TICKS.finditer(text)]
    by_len: dict[int, list[int]] = {}
    for n, (a, b) in enumerate(runs):
        by_len.setdefault(b - a, []).append(n)
    blanks, out, last, n = [m.start() for m in BLANK_LINE.finditer(text)], [], 0, 0
    while n < len(runs):
        (a, b), same = runs[n], by_len[runs[n][1] - runs[n][0]]
        k, nxt = bisect_right(same, n), None
        if k < len(same) and not (a and text[a - 1] == "\\"):
            nxt = same[k]
            at = bisect_left(blanks, b)
            if at < len(blanks) and blanks[at] < runs[nxt][0]:
                nxt = None                                            # a blank line ends the paragraph first: the opener is literal
        if nxt is None:
            n += 1
            continue
        out += [text[last:b], _prose(text[b:runs[nxt][0]])]
        last, n = runs[nxt][0], nxt + 1
    return "".join(out) + text[last:]


def markdown_code_as_text(text: str) -> str:
    """Markdown mode: the raw-text openers (`script`, `style`, ...) inside a code span or a fenced code block are prose, so they are escaped.
    Everywhere else an opener stays markup, which the parser hides to its closer or to the end of the input (fail closed)."""
    out, chunk, fence = [], [], None                                  # fence: (character, length) of the open fenced block
    for line in text.splitlines(keepends=True):
        m = FENCE.match(line)
        if fence:
            out.append(_prose(line))
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
        elif m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            out.append(_spans("".join(chunk)))
            chunk, fence = [], (m.group(1)[0], len(m.group(1)))
            out.append(line)                                          # the info string is not rendered text: it stays markup
        else:
            chunk.append(line)
    return "".join(out) + _spans("".join(chunk))


def strip_hidden(html: str, markdown: bool = False) -> str:
    """The visible text of `html` (see the module docstring); "" when the parser gives up."""
    src = markdown_code_as_text(html) if markdown else html
    rules = css_rules("\n".join(STYLE_BLOCK.findall(src)))
    parser = _Text(rules)
    try:
        parser.feed(src)
        parser.close()
    except Exception:                                               # a parser fault must never let raw markup through
        return ""
    lines = (SPACES.sub(" ", plain("".join(parser.out))).split("\n"))
    return "\n".join(s for s in (ln.strip() for ln in lines) if s)


def one_line(text: str, limit: int) -> str:
    """Visible text of `text` on one line, at most `limit` characters (for titles, links and summaries from feeds)."""
    return " ".join(strip_hidden(text).split())[:limit]
