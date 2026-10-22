"""The first parser pass of `sanitize`: the text of the REAL `<style>` elements. Stdlib only.

The rules of a stylesheet apply to the whole page, to elements that come before the `<style>` too, so they must be known before the visible
text is read. A scan for the string `<style` is fooled by every `<style` that is not a start tag (inside a script string, a comment, an
attribute value, a `<textarea>` or `<title>`, a custom element `<style-x>`): a fake one hides the real rules. Here the tokenizer decides:
html.parser reads the page once with the same script rules as the second pass (`htmlrules.ScriptedParser`) and collects the character data
of each `<style>` element, an unclosed one at the end of the page included (a browser applies it). Linear in the size of the page.

The tokenizer is not the browser, though (a `<![CDATA[` section, a bogus comment and the like end at different places), so
`style_rules` also applies the rules of a plain scan for `<style ... </style` (`scan_text`): a rule found by EITHER applies. The scan
can only add rules (over-hiding), never remove one.
"""

from __future__ import annotations

import re

from .cssstyle import css_rules
from .htmlrules import ScriptedParser, ScriptState

STYLE_MARK = re.compile(r"<style", re.I)
STYLE_OPEN, STYLE_CLOSE = re.compile(r"<style(?=[\s/>])", re.I), re.compile(r"</style(?=[\s/>])", re.I)


class _Styles(ScriptedParser):
    def __init__(self) -> None:
        super().__init__()
        self.sheets: list[list[str]] = []
        self.inside = False

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "script":
            self.script = ScriptState()
        elif tag == "style":
            self.inside = True
            self.sheets.append([])

    def handle_endtag(self, tag) -> None:
        if self.script_stays_open(tag):
            return
        if tag == "script":
            self.script = None
        elif tag == "style":
            self.inside = False

    def handle_data(self, data) -> None:
        super().handle_data(data)
        if self.inside:
            self.sheets[-1].append(data)


def scan_text(src: str) -> str:
    """The text between every `<style` start tag and the next `</style`, found by a plain scan (an unclosed one runs to the end of the
    page), one per line. It reads fake `<style`s too, so it can only add rules: `style_rules` unions it with the tokenizer's, which
    covers every page whose style html.parser and a browser tokenize differently (a CDATA section, a bogus comment, ...). Linear: the
    scan resumes after each closer, and ends at the first opener with no `>` or no closer."""
    parts: list[str] = []
    pos = 0
    while (m := STYLE_OPEN.search(src, pos)) and (gt := src.find(">", m.end())) >= 0:
        close = STYLE_CLOSE.search(src, gt + 1)
        parts.append(src[gt + 1:close.start() if close else len(src)])
        if not close:
            break
        pos = close.end()
    return "\n".join(parts)


def style_rules(src: str) -> tuple[frozenset, frozenset, frozenset]:
    """(classes, ids, tags) hidden by the simple rules of the real style elements (`style_text`) or of any `<style` text (`scan_text`)."""
    if not STYLE_MARK.search(src):
        return css_rules("")
    t, s = css_rules(style_text(src)), css_rules(scan_text(src))
    return t[0] | s[0], t[1] | s[1], t[2] | s[2]


def style_text(src: str) -> str:
    """The text of every `<style>` element of `src`, one per line, in document order."""
    if not STYLE_MARK.search(src):
        return ""                                                   # no start tag can be there without these six characters: no first pass
    parser = _Styles()
    parser.feed(src)
    parser.close()
    return "\n".join("".join(parts) for parts in parser.sheets)
