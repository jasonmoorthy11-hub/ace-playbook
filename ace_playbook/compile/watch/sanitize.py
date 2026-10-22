"""Visible text only (design 4.1: about 70 % of real injections hide in non-rendered HTML, so nothing but visible text goes on).

`strip_hidden(html)` drops the content of `<script>`, `<style>`, `<template>`, `<noscript>`, `<head>`, `<title>`, `<dialog>`, `<svg>`,
`<math>`, `<iframe>`, `<object>`, `<embed>`, `<canvas>`, `<audio>`, `<video>`, `<datalist>`, `<noembed>`, `<noframes>`, `<rp>`, a `<details>`
without `open`, comments, any element (and its whole subtree) that carries `hidden`, `inert`, `popover`, `aria-hidden="true"` or an inline
style that hides (`cssstyle.style_hidden`), any element matched by a SIMPLE hiding rule of a `<style>` block (a single `.class`, `#id` or
`tag`, names compared without case; media queries, descendant selectors and external stylesheets are not handled), and every invisible
character (`chars.invisible`: all Unicode format characters, tag characters, variation selectors and the blank fillers). An element that
is never closed stays hidden to the end of the input (fail closed); a formatting element closed implicitly by an outer end tag is re-opened
hidden, as a browser's adoption agency would. A slash on a non-void tag (`<div hidden/>`, `<script/>`) is ignored, as a browser ignores it.
An end tag that a browser ignores (a scope barrier or a special element in between, see `htmlrules`) pops nothing when that would unhide
text; a script that is double-escaped (`<!--<script>`) closes at its next-but-one `</script`. More than `htmlrules.MAX_DEPTH` open elements
raise `htmlrules.TooDeep` (a ValueError: the page is a parse failure). Tag names are compared as written (`</x:div>` does not close
`<div>`: a browser ignores it); the inline-XML children of a feed arrive with local names already. `markdown=True` is for Markdown pages:
a raw-text opener is prose only in code, outside HTML blocks and with no closer after it (`mdrules`); anywhere else the rules above apply
as in HTML (a bare `<script>` hides to its closer, or to the end of the input). The result is plain text, one non-empty line per block.
Stdlib only.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .chars import control, invisible
from .cssstyle import css_rules, style_hidden  # noqa: F401 - style_hidden is part of this module's surface
from .htmlrules import BLOCK, FORMATTING, MAX_DEPTH, SKIP, VOID, ScriptState, TooDeep, end_ignored
from .mdrules import markdown_code_as_text

SPACES = re.compile(r"[ \t\r\f\v\xa0]+")
STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.I | re.S)


def plain(text: str) -> str:
    """`text` without invisible characters and without control characters other than newline and tab (U+2028/2029 become newlines)."""
    return "".join("\n" if c in "\u2028\u2029" else c for c in text if not invisible(c) and not control(c))


class _Text(HTMLParser):
    def __init__(self, rules: tuple) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[list] = []                                # [tag, hidden: this element or an ancestor is, hidden: this element is]
        self.out: list[str] = []
        self.open: dict[str, int] = {}                             # tag -> how many are on the stack: an end tag with none is skipped in O(1)
        self.classes, self.ids, self.tags = rules
        self.script: ScriptState | None = None                    # the script-data state of the open <script>
        self.again = False                                        # a `</script` inside a double-escaped script: stay in script mode

    def _hidden(self) -> bool:
        return bool(self.stack) and self.stack[-1][1]

    def _own(self, tag: str, attrs: list) -> bool:
        names = {k for k, _ in attrs}
        values = {k: (v or "") for k, v in reversed(attrs)}
        aria = any((v or "").strip().lower() == "true" for k, v in attrs if k == "aria-hidden")       # a repeated attribute: any copy counts
        return bool(tag in SKIP or (tag == "details" and "open" not in names) or names & {"hidden", "inert", "popover"} or aria
                    or any(style_hidden(v or "") for k, v in attrs if k == "style")
                    or tag in self.tags or values.get("id", "").lower() in self.ids or self.classes & set(values.get("class", "").lower().split()))

    def handle_starttag(self, tag, attrs) -> None:
        own = self._own(tag, attrs)
        hid = self._hidden() or own
        if tag in VOID:
            self.out.append(" " if not hid and tag in ("br", "hr") else "")
            return
        self.stack.append([tag, hid, own])
        self.open[tag] = self.open.get(tag, 0) + 1
        if len(self.stack) > MAX_DEPTH:
            raise TooDeep("nesting too deep")
        if tag == "script":
            self.script = ScriptState()
        self.out.append("\n" if tag in BLOCK and not hid else "")

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)                            # a browser ignores the slash on a non-void element
        if tag in ("script", "style"):
            self.set_cdata_mode(tag)

    def clear_cdata_mode(self) -> None:
        if self.again:                                              # the end tag was a `</script` of a double-escaped script
            self.again = False
            return
        super().clear_cdata_mode()

    def handle_endtag(self, tag) -> None:
        if tag == "script" and self.script is not None and not self.script.closes():
            self.again = True
            return
        if not self.open.get(tag):
            return
        i = next(i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag)
        if end_ignored(self.stack, i):
            return
        popped = self.stack[i + 1:]
        for t, _, _ in self.stack[i:]:
            self.open[t] -= 1
        del self.stack[i:]
        if tag == "script":
            self.script = None
        for t, hid, own in popped:                                  # `<b><i hidden></b>X</i>`: the browser re-opens the hidden <i> around X
            if t in FORMATTING and hid:
                self.stack.append([t, self._hidden() or own, own])
                self.open[t] += 1
        self.out.append("\n" if tag in BLOCK and not self._hidden() else "")

    def handle_data(self, data) -> None:
        if self.script is not None:
            self.script.feed(data)
        if not self._hidden():
            self.out.append(data)


def strip_hidden(html: str, markdown: bool = False) -> str:
    """The visible text of `html` (see the module docstring); "" when the parser gives up; TooDeep (ValueError) past MAX_DEPTH."""
    src = markdown_code_as_text(html) if markdown else html
    rules = css_rules("\n".join(STYLE_BLOCK.findall(src)))
    parser = _Text(rules)
    try:
        parser.feed(src)
        parser.close()
    except TooDeep:
        raise
    except Exception:                                               # a parser fault must never let raw markup through
        return ""
    lines = (SPACES.sub(" ", plain("".join(parser.out))).split("\n"))
    return "\n".join(s for s in (ln.strip() for ln in lines) if s)


def one_line(text: str, limit: int) -> str:
    """Visible text of `text` on one line, at most `limit` characters (for titles, links and summaries from feeds)."""
    return " ".join(strip_hidden(text).split())[:limit]
