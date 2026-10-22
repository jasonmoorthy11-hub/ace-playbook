"""Implied end tags for `sanitize` (the HTML tree-construction rules for `p`, `li`, `dd` and `dt`). Stdlib only.

A browser closes an open `<p>` when a block start tag arrives, a `<li>` at the next `<li>`, a `<dd>` or `<dt>` at the next `<dd>` or `<dt>`.
A `<table>` does so only in standards mode (`standards=True`: the caller saw a leading `<!DOCTYPE html>`); in quirks mode the table nests
in the paragraph, so `<p hidden><table>X` keeps X hidden. Without that doctype the mode is taken to be quirks (fail closed).
`<p>a<div hidden></p>X</div>` is therefore a p, then a hidden div that is its SIBLING; the later `</p>` finds no p and makes an empty one
INSIDE the div, so X stays hidden. A model that kept the p open would pop the hidden div with that `</p>` and show X.

FOREIGN content (`<svg>`, `<math>`) is different: a start tag inside it is not processed by the HTML rules, so `<p hidden><svg><section>X`
keeps X inside the p. Only a BREAKOUT tag (`BREAKOUT`, and `<font>` with a `color`, `face` or `size`) pops the foreign elements first, and
only up to the nearest HTML element or integration point (`INTEGRATION`: `foreignObject`, `desc`, `title`, `mi`, `mo`, `mn`, `ms`,
`mtext`, `annotation-xml`: their content is HTML again, but they are scope markers, so no implied end tag reaches past them). The
integration names are taken in either namespace and `annotation-xml` always counts as one: both only hide more.

Every stack entry of `sanitize` carries `marks`: the stack index of the nearest open `p`, scope barrier (button scope), `li`, `dd`/`dt`
and other special element at or below it, and the first entry of the run of foreign elements that ends the stack (-1: the top is HTML
content). `implied(marks, tag, attrs)` answers in O(1) which index a start tag pops from (that element and everything above it); the
caller repeats until it returns None. Elements with optional end tags that this does not model (`td`, `tr`, `option`, `h1`..`h6` among
them) nest deeper than in a browser, which only hides more (LIMITATIONS.md).
"""

from __future__ import annotations

from .htmlrules import BARRIERS, INTEGRATION, SPECIAL

NONE = (-1, -1, -1, -1, -1, -1)
FOREIGN = frozenset(("svg", "math"))
BUTTON_SCOPE = BARRIERS | FOREIGN | {"button"}
BREAKOUT = frozenset(("b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 h6 head hr i img li listing menu meta nobr ol "
                      "p pre ruby s small span strong strike sub sup table tt u ul var").split())
FONT_ATTRS = frozenset(("color", "face", "size"))
CLOSES_P = frozenset(("address article aside blockquote center details dialog dir div dl fieldset figcaption figure footer form h1 h2 h3 h4 "
                      "h5 h6 header hgroup hr li dd dt main menu nav ol p pre listing plaintext search section summary table ul xmp").split())
STOPS = (SPECIAL | INTEGRATION) - {"address", "div", "p", "li", "dd", "dt"}             # a special element that ends the search for an open li/dd/dt


def marks(prev: tuple | None, tag: str, index: int) -> tuple:
    """The marks of a new stack entry `tag` at stack index `index`, from the marks of the entry below it (None for the first)."""
    p, bar, li, dd, stop, run = prev or NONE
    run = run if run >= 0 and tag not in INTEGRATION else index if tag in FOREIGN else -1        # a foreign element extends the run it is in
    return (index if tag == "p" else p, index if tag in BUTTON_SCOPE else bar, index if tag == "li" else li,
            index if tag in ("dd", "dt") else dd, index if tag in STOPS else stop, run)


def implied(top: tuple | None, tag: str, attrs: list | tuple = (), standards: bool = False) -> int | None:
    """The stack index that the start tag `tag` pops from, given the marks of the top entry (None for an empty stack); None: nothing to pop.
    Inside foreign content only a breakout tag pops anything: the run of foreign elements, after which the HTML rules apply. A `table`
    closes the open `p` only when `standards` is true (quirks mode otherwise)."""
    if top is None:
        return None
    p, bar, li, dd, stop, run = top
    if run >= 0:
        return run if tag in BREAKOUT or (tag == "font" and any(k in FONT_ATTRS for k, _ in attrs)) else None
    if tag == "li" and li > max(stop, dd):
        return li
    if tag in ("dd", "dt") and dd > max(stop, li):
        return dd
    return p if tag in CLOSES_P and p > bar and (standards or tag != "table") else None
