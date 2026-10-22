"""The first parser pass of `sanitize`: the text of the REAL `<style>` elements. Stdlib only.

The rules of a stylesheet apply to the whole page, to elements that come before the `<style>` too, so they must be known before the visible
text is read. A scan for the string `<style` is fooled by every `<style` that is not a start tag (inside a script string, a comment, an
attribute value, a `<textarea>` or `<title>`, a custom element `<style-x>`): a fake one hides the real rules. Here the tokenizer decides:
html.parser reads the page once with the same script rules as the second pass (`htmlrules.ScriptedParser`) and collects the character data
of each `<style>` element, an unclosed one at the end of the page included (a browser applies it). Linear in the size of the page.
"""

from __future__ import annotations

import re

from .htmlrules import ScriptedParser, ScriptState

STYLE_MARK = re.compile(r"<style", re.I)


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


def style_text(src: str) -> str:
    """The text of every `<style>` element of `src`, one per line, in document order."""
    if not STYLE_MARK.search(src):
        return ""                                                   # no start tag can be there without these six characters: no first pass
    parser = _Styles()
    parser.feed(src)
    parser.close()
    return "\n".join("".join(parts) for parts in parser.sheets)
