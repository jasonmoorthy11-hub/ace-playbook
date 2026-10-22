"""The first parser pass of `sanitize`: the text of the REAL `<style>` elements. Stdlib only.

The rules of a stylesheet apply to the whole page, to elements that come before the `<style>` too, so they must be known before the visible
text is read. A scan for the string `<style` is fooled by every `<style` that is not a start tag (inside a script string, a comment, an
attribute value, a `<textarea>` or `<title>`, a custom element `<style-x>`): a fake one hides the real rules. Here the tokenizer decides:
html.parser reads the page once with the same script rules as the second pass (`htmlrules.ScriptedParser`) and collects the character data
of each `<style>` element, an unclosed one at the end of the page included (a browser applies it). Linear in the size of the page. It counts
the open `svg`, `math` and integration-point elements only, to know whether a raw-text name is raw text (HTML content) or an element
(foreign content) and whether a `<![CDATA[` is a section (foreign content): the answers of the second pass, approximated.

Every sheet is parsed ON ITS OWN (`style_sheets`, `scan_sheets`; `style_rules` unions the rules of the sheets): an open block, string or
comment at the end of one `<style>` never carries into the next, as in a browser. `style_text` and `scan_text` join the sheets with newlines.

The tokenizer is not the browser in every corner (a CDATA section in foreign content, a bogus comment and the like may still end at
different places), so `style_rules` also applies the rules of a plain scan for `<style ... </style` (`scan_text`): a rule found by EITHER
applies. The scan can only add rules (over-hiding), never remove one.
"""

from __future__ import annotations

import re

from .cssstyle import css_rules
from .htmlrules import INTEGRATION, ScriptedParser, ScriptState

WATCHED = frozenset(("svg", "math")) | INTEGRATION
STYLE_MARK = re.compile(r"<style", re.I)
STYLE_OPEN, STYLE_CLOSE = re.compile(r"<style(?=[\s/>])", re.I), re.compile(r"</style(?=[\s/>])", re.I)


class _Styles(ScriptedParser):
    def __init__(self) -> None:
        super().__init__()
        self.sheets: list[list[str]] = []
        self.inside = False
        self.depth: dict[str, int] = {}                             # how many svg / math / integration-point elements are open
        self.born_foreign = False                                   # the last start tag was made in foreign content

    def foreign(self) -> bool:
        """Inside svg or math and in no integration point (an approximation of the tree of the second pass: the plain scan covers the rest)."""
        return bool(self.depth.get("svg") or self.depth.get("math")) and not any(self.depth.get(t) for t in INTEGRATION)

    def in_foreign(self) -> bool:
        return self.born_foreign

    def handle_starttag(self, tag, attrs) -> None:
        self.born_foreign = self.foreign()                          # asked before the tag is counted: an integration point is foreign itself
        if tag in WATCHED:
            self.depth[tag] = self.depth.get(tag, 0) + 1
        if tag == "script":
            self.script = ScriptState()
        elif tag == "style":
            self.inside = True
            self.sheets.append([])

    def handle_endtag(self, tag) -> None:
        if self.script_stays_open(tag):
            return
        if self.depth.get(tag):
            self.depth[tag] -= 1
        if tag == "script":
            self.script = None
        elif tag == "style":
            self.inside = False

    def handle_data(self, data) -> None:
        super().handle_data(data)
        if self.inside:
            self.sheets[-1].append(data)


def scan_sheets(src: str) -> list[str]:
    """The text between every `<style` start tag and the next `</style`, found by a plain scan (an unclosed one runs to the end of the
    page), one string per sheet. It reads fake `<style`s too, so it can only add rules: `style_rules` unions it with the tokenizer's, which
    covers every page whose style html.parser and a browser tokenize differently (a CDATA section in svg, a bogus comment, ...). Linear: the
    scan resumes after each closer, and ends at the first opener with no `>` or no closer."""
    parts: list[str] = []
    pos = 0
    while (m := STYLE_OPEN.search(src, pos)) and (gt := src.find(">", m.end())) >= 0:
        close = STYLE_CLOSE.search(src, gt + 1)
        parts.append(src[gt + 1:close.start() if close else len(src)])
        if not close:
            break
        pos = close.end()
    return parts


def style_sheets(src: str) -> list[str]:
    """The text of every `<style>` element of `src`, one string per element, in document order."""
    if not STYLE_MARK.search(src):
        return []                                                   # no start tag can be there without these six characters: no first pass
    parser = _Styles()
    parser.feed(src)
    parser.close()
    return ["".join(parts) for parts in parser.sheets]


def style_rules(src: str) -> tuple[frozenset, frozenset, frozenset]:
    """(classes, ids, tags) hidden by the simple rules of the real style elements or of any `<style` text, each sheet parsed on its own."""
    classes: set[str] = set()
    ids: set[str] = set()
    tags: set[str] = set()
    if STYLE_MARK.search(src):
        for sheet in (*style_sheets(src), *scan_sheets(src)):
            c, i, t = css_rules(sheet)
            classes |= c
            ids |= i
            tags |= t
    return frozenset(classes), frozenset(ids), frozenset(tags)


def style_text(src: str) -> str:
    """The text of every `<style>` element of `src`, one per line, in document order."""
    return "\n".join(style_sheets(src))


def scan_text(src: str) -> str:
    """The text of every `<style` found by the plain scan, one per line."""
    return "\n".join(scan_sheets(src))
