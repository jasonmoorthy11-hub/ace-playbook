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

A raw-text start tag inside an open `<select>` is ignored by one browser reading and is raw text in the other, so the tokenizer pass runs both
ways when the page has a `<select` (`_Styles(ignored=True)` is the ignoring reading) and unions the sheets; the scan starts a sheet at EVERY
`<style` opener, the ones inside another sheet's text included (overlapping sheets only add rules), so a fake opener never merges with a real
sheet. At most `MAX_OVERLAP` openers may lie inside another sheet and the sheets together at most `MAX_SCAN_CHARS` characters (the work is the
sum of the sheet lengths): past that the page is a parse failure (`TooDeep`, fail closed).

The tokenizer is not the browser in every corner (a CDATA section in foreign content, a bogus comment and the like may still end at
different places), so `style_rules` also applies the rules of a plain scan for `<style ... </style` (`scan_text`): a rule found by EITHER
applies. The scan can only add rules (over-hiding), never remove one.
"""

from __future__ import annotations

import re

from .cssstyle import css_rules
from .htmlrules import INTEGRATION, ScriptedParser, ScriptState, TooDeep

WATCHED = frozenset(("svg", "math")) | INTEGRATION
STYLE_MARK, SELECT_MARK = re.compile(r"<style", re.I), re.compile(r"<select", re.I)
MAX_OVERLAP, MAX_SCAN_CHARS = 1000, 4 * 1024 * 1024
SELECT_KEPT = frozenset(("option", "optgroup", "hr", "script", "template"))                      # the other start tags are ignored in a select
SELECT_ENDS = frozenset(("input", "keygen", "textarea", "select"))                               # these close an open select first
STYLE_OPEN, STYLE_CLOSE = re.compile(r"<style(?=[\s/>])", re.I), re.compile(r"</style(?=[\s/>])", re.I)


class _Styles(ScriptedParser):
    def __init__(self, ignored: bool = False) -> None:
        super().__init__()
        self.ignored = ignored                                      # the reading where an open select ignores most start tags and end tags
        self.in_select, self.skipped = False, False
        self.sheets: list[list[str]] = []
        self.inside = False
        self.depth: dict[str, int] = {}                             # how many svg / math / integration-point elements are open
        self.born_foreign = False                                   # the last start tag was made in foreign content

    def foreign(self) -> bool:
        """Inside svg or math and in no integration point (an approximation of the tree of the second pass: the plain scan covers the rest)."""
        return bool(self.depth.get("svg") or self.depth.get("math")) and not any(self.depth.get(t) for t in INTEGRATION)

    def in_foreign(self) -> bool:
        return self.born_foreign

    def set_cdata_mode(self, elem, **kw) -> None:
        if self.skipped:                                            # the start tag was ignored by the select: no raw text
            self.skipped = False
            return
        super().set_cdata_mode(elem, **kw)

    def handle_starttag(self, tag, attrs) -> None:
        self.skipped = False
        if self.ignored:
            if tag in SELECT_ENDS:
                self.in_select = tag == "select" and not self.in_select
            elif self.in_select and tag not in SELECT_KEPT:
                self.skipped = True
                return
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
        if self.ignored and self.in_select and tag not in SELECT_KEPT:
            self.in_select = tag != "select"
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
    page), one string per opener, overlapping ones included (see the module docstring). It reads fake `<style`s too, so it can only add rules:
    `style_rules` unions it with the tokenizer's, which covers every page whose style html.parser and a browser tokenize differently (a CDATA
    section in svg, a bogus comment, an ignored start tag in a select, ...). Linear: each opener is scanned once and the closer and the
    end of the start tag found for one are reused by the next. `TooDeep` past `MAX_OVERLAP` openers inside another sheet or
    `MAX_SCAN_CHARS` characters of sheets (checked before a sheet is cut)."""
    parts: list[str] = []
    pos = inner = chars = 0
    gt, close, searched, last, stop = -1, None, False, -1, -1
    while (m := STYLE_OPEN.search(src, pos)):
        pos = m.end()
        if gt < pos and (gt := src.find(">", pos)) < 0:
            break
        start = gt + 1
        if start == last:
            continue                                                # the same start tag again: the same sheet
        if not searched or (close and close.start() < start):
            close, searched = STYLE_CLOSE.search(src, start), True
        inner += m.start() < stop
        stop, last = (close.start() if close else len(src)), start
        chars += stop - start
        if inner > MAX_OVERLAP or chars > MAX_SCAN_CHARS:
            raise TooDeep("too many overlapping style sheets")
        parts.append(src[start:stop])
    return parts


def _read(src: str, ignored: bool) -> list[str]:
    parser = _Styles(ignored)
    parser.feed(src)
    parser.close()
    return ["".join(parts) for parts in parser.sheets]


def style_sheets(src: str) -> list[str]:
    """The text of every `<style>` element of `src`, one string per element, in document order; with a `<select` also the elements of the
    reading where the select ignores a raw-text start tag (the sheets the first reading did not have, after it)."""
    if not STYLE_MARK.search(src):
        return []                                                   # no start tag can be there without these six characters: no first pass
    sheets = _read(src, False)
    if SELECT_MARK.search(src):
        seen = set(sheets)
        sheets += [t for t in _read(src, True) if t not in seen and not seen.add(t)]
    return sheets


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
