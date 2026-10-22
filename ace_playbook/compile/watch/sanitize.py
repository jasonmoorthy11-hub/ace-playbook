"""Visible text only (design 4.1: about 70 % of real injections hide in non-rendered HTML, so nothing but visible text goes on).

`strip_hidden(html)` builds ONE HTML5-style tree of the page (`treebuild`, the insertion modes of `treemodes`: implied elements, scope, foster
parenting of table content, foreign content by namespace, template, frameset, select) and reads it twice: the style sheets (`treestyle`) and the
visible text (`treewalk`). It drops the content of `<script>`, `<style>`, `<template>`, `<noscript>`, `<head>`, `<title>`, `<dialog>`, `<svg>`,
`<math>`, `<iframe>`, `<object>`, `<embed>`, `<canvas>`, `<audio>`, `<video>`, `<datalist>`, `<noembed>`, `<noframes>`, `<rp>`, `<select>` (all its
option text), `<progress>`, `<meter>`, a `<details>` without `open`, comments, any element (and its whole subtree) that carries `hidden`, `inert`,
`popover`, `aria-hidden="true"` or an inline style that hides (`cssstyle.style_hidden`), any element matched by a SIMPLE hiding rule of a `<style>`
block (a single `.class`, `#id` or `tag`, names compared without case; media queries, descendant selectors and external stylesheets are not
handled), and every invisible character (`chars.invisible`: all Unicode format characters, tag characters, variation selectors and the blank
fillers). An element that is never closed stays hidden to the end of the input (fail closed). Because the tree is a browser's, an end tag that a
browser ignores (a scope marker or a special element in between) pops nothing, `</p>` finds no `p` after `<p>a<div hidden></p>`, a formatting element
closed by an outer end tag is re-opened (hidden, if it was), and a table's text and elements land before the table (`<p hidden><table>X` keeps X in the
hidden `p` unless the page is in standards mode, which is a FIRST doctype `<!DOCTYPE html>`, optionally with `SYSTEM "about:legacy-compat"`; any other
doctype or none is quirks mode, fail closed). The byte order mark is dropped in ONE place (`fetch` decodes with `utf-8-sig`): a remaining U+FEFF is a
character token and makes the page quirks, as in a browser. Document-level hiding: an `<html>` or `<body>` start tag, the first or a late one, that
hides, or a `<style>` rule that hides `html`, `body` or `*`, makes the whole result ""; a `<frameset>` that replaces the body does too (nothing
renders); a `<template shadowrootmode=...>` hides every child of its host (those before it too), the whole document when its host is the document.
`<![CDATA[` is a bogus comment to the first `>` in HTML content and a real section whenever the current node is not an HTML element (svg, math, an
integration point); a slash on a non-void HTML tag is ignored; `<noscript>` is raw text in HTML content (a browser with scripting on); a double-escaped
script closes at its next-but-one `</script`. Over-hides kept on purpose (`treemodes`): an open `select` ignores end tags but a table end tag in table scope
(it ends a select in a table), a nested `<select>` stays open, raw text a select holds that has a `<` hides the REST of the page, a `</template>` that closes
a select holding a barrier hides the REST too, the adoption agency leaves an element with a furthest block open, an `applet`, `marquee` or `object`
above a select makes a table tag that ends it poison the rest (after a template-closed select the next cell tag hides the rest), a table in a
select is no select in a table, `</select>` needs scope. More than
`htmlrules.MAX_DEPTH` open elements raise `htmlrules.TooDeep` (a ValueError: a parse failure). Tag names are compared as written
(`</x:div>` does not close `<div>`). `markdown=True` is for Markdown pages: a raw-text or content-hiding opener (`mdrules.RAW_TEXT`) is prose only in a
code span that opens and closes on one plain line, or in a fenced block, outside HTML blocks and with no closer after it (`mdrules`); anywhere else the
rules above apply as in HTML. Link reference definitions (`[x]: url "title"`) are not rendered: with `markdown` the strictly valid ones are dropped from
the source; `definitions=True` on an HTML page drops them from the VISIBLE text only. The result is plain text, one non-empty line per block.
Stdlib only.
"""

from __future__ import annotations

import re

from .chars import control, invisible
from .cssstyle import style_hidden  # noqa: F401 - style_hidden is part of this module's surface
from .htmlrules import TooDeep
from .mdrules import drop_definitions, markdown_code_as_text
from .treebuild import build
from .treestyle import style_rules, style_text  # noqa: F401 - style_text is part of this module's surface
from .treewalk import visible

SPACES = re.compile(r"[ \t\r\f\v\xa0]+")
ROOT_HIDING = frozenset(("html", "body", "*"))


def plain(text: str) -> str:
    """`text` without invisible characters and without control characters other than newline and tab (U+2028/2029 become newlines)."""
    return "".join("\n" if c in "  " else c for c in text if not invisible(c) and not control(c))


def strip_hidden(html: str, markdown: bool = False, definitions: bool | None = None) -> str:
    """The visible text of `html` (see the module docstring); "" when the parser gives up; TooDeep (ValueError) past MAX_DEPTH."""
    defs = markdown if definitions is None else definitions
    src = markdown_code_as_text(html, markdown, markdown and defs)
    try:
        tree = build(src)
        rules = style_rules(src, tree)
        text = "" if ROOT_HIDING & rules[2] else visible(tree, rules)
    except TooDeep:
        raise
    except Exception:                                               # a parser fault must never let raw markup through
        return ""
    lines = (SPACES.sub(" ", plain(text)).split("\n"))
    text = "\n".join(s for s in (ln.strip() for ln in lines) if s)
    return text if markdown or not defs else drop_definitions(text)


def one_line(text: str, limit: int) -> str:
    """Visible text of `text` on one line, at most `limit` characters (for titles, links and summaries from feeds)."""
    return " ".join(strip_hidden(text).split())[:limit]
