"""The style pass of the sanitizer, over the TREE of `treebuild`: the sheet of a page is the text of its `style` elements, in ANY namespace. Stdlib only.

A rule applies to the whole page, elements before the `<style>` included, so the sheets are read before the visible text. The tree builder decides
what a `<style>` is (not one inside a script string, comment, attribute, `<textarea>`, `<title>`, a custom element `<style-x>` or a `<noscript>`'s
raw text) and what its text is: the child text of the element, so an HTML style is its raw text (comments and all: CSS reads them), an SVG style
is its character data (a CDATA section is text, a comment is not, character references are decoded). With a `<select` the page is built a second
time in the other reading of the select and the sheets unioned. Every sheet is parsed ON ITS OWN (`cssstyle.css_rules`), so an open block, string or
comment never carries into the next one.

A SAFETY NET, unioned with the tree's sheets, so a page the builder and a browser tokenize differently can only hide MORE: `scan_sheets` starts a sheet
at EVERY `<style` opener (inside another sheet's text too) and takes the text to the next `</style` (the end of the start tag is looked for at the
first `>` and with quoted values skipped), and `variants` reads each scanned sheet also with the CDATA markers removed, with character references decoded
and with tags and comments dropped. At most `MAX_OVERLAP` openers may lie inside another sheet and the sheets together `MAX_SCAN_CHARS` characters
(`TooDeep`, fail closed)."""

from __future__ import annotations

import re
from html import unescape

from .cssstyle import css_rules
from .htmlrules import TooDeep
from .treebuild import build

TAG_REST = re.compile(r"""(?:[^>"'=]|=[ \t\n\f\r]*(?:"[^"]*"?|'[^']*'?)?|["'])*>?""")       # the rest of a start tag: quoted values only after `=`
STYLE_MARK, SELECT_MARK = re.compile(r"<style", re.I), re.compile(r"<select", re.I)
STYLE_OPEN, STYLE_CLOSE = re.compile(r"<style(?=[\s/>])", re.I), re.compile(r"</style(?=[\s/>])", re.I)
CDATA_MARK, MARKUP = re.compile(r"<!\[CDATA\[|\]\]>"), re.compile(r"<!--.*?(?:-->|\Z)|<[^>]*>?", re.S)
MAX_OVERLAP, MAX_SCAN_CHARS = 1000, 4 * 1024 * 1024


def scan_sheets(src: str) -> list[str]:
    """The text between every `<style` start tag and the next `</style` (an unclosed one runs to the end), one string per opener, overlapping ones
    included. Linear: each opener is scanned once, the closer and the end of the start tag found for one are reused by the next."""
    parts: list[str] = []
    pos = inner = chars = 0
    gt, close, searched, last, stop = -1, None, False, -1, -1
    while (m := STYLE_OPEN.search(src, pos)):
        pos = m.end()
        if gt < pos and (gt := src.find(">", pos)) < 0:
            break
        tag = TAG_REST.match(src, pos, min(len(src), pos + MAX_SCAN_CHARS - chars + 1))
        chars += tag.end() - pos
        for end in sorted({gt, tag.end() - 1 if tag.group().endswith(">") else gt}):
            start = end + 1
            if start == last:
                continue                                            # the same start tag again: the same sheet
            if not searched or (close and close.start() < start):
                close, searched = STYLE_CLOSE.search(src, start), True
            inner += m.start() < stop
            stop, last = (close.start() if close else len(src)), start
            chars += stop - start
            if inner > MAX_OVERLAP or chars > MAX_SCAN_CHARS:
                raise TooDeep("too many overlapping style sheets")
            parts.append(src[start:stop])
    return parts


def variants(sheet: str) -> list[str]:
    """`sheet` and, one after the other, with the CDATA markers removed, character references decoded, tags and comments dropped (when they differ)."""
    out = [sheet]
    for f in (lambda s: CDATA_MARK.sub("", s), unescape, lambda s: MARKUP.sub("", s)):
        if (nxt := f(out[-1])) != out[-1]:
            out.append(nxt)
    return out


def style_sheets(src: str, tree=None) -> list[str]:
    """The text of every `style` element of `src` (`tree`: the relaxed tree already built), in document order; with a `<select` the elements of
    the other reading of the select follow, those the first reading did not have."""
    if not STYLE_MARK.search(src):
        return []
    sheets = ["".join(c for c in n.kids if c.__class__ is str) for n in (tree or build(src)).styles]
    if SELECT_MARK.search(src):
        seen = set(sheets)
        sheets += [s for s in ("".join(c for c in n.kids if c.__class__ is str) for n in build(src, False).styles) if s not in seen and not seen.add(s)]
    return sheets


def style_rules(src: str, tree=None) -> tuple[frozenset, frozenset, frozenset]:
    """(classes, ids, tags) hidden by the simple rules of the style elements or of any scanned `<style` text, each sheet parsed on its own."""
    found: tuple[set, set, set] = (set(), set(), set())
    if STYLE_MARK.search(src):
        seen = set(style_sheets(src, tree))
        for sheet in dict.fromkeys((*seen, *(v for s in scan_sheets(src) for v in variants(s)))):
            for acc, got in zip(found, css_rules(sheet)):
                acc |= got
    return tuple(map(frozenset, found))                             # type: ignore[return-value]


def style_text(src: str) -> str:
    """The text of every `style` element of `src`, one per line, in document order."""
    return "\n".join(style_sheets(src))


def scan_text(src: str) -> str:
    """The text of every `<style` found by the plain scan, one per line."""
    return "\n".join(scan_sheets(src))
