"""Implied end tags for `sanitize` (the HTML tree-construction rules for `p`, `li`, `dd` and `dt`). Stdlib only.

A browser closes an open `<p>` when a block start tag arrives, a `<li>` at the next `<li>`, a `<dd>` or `<dt>` at the next `<dd>` or `<dt>`.
`<p>a<div hidden></p>X</div>` is therefore a p, then a hidden div that is its SIBLING; the later `</p>` finds no p and makes an empty one
INSIDE the div, so X stays hidden. A model that kept the p open would pop the hidden div with that `</p>` and show X.

Every stack entry of `sanitize` carries `marks`: the stack index of the nearest open `p`, scope barrier (button scope), `li`, `dd`/`dt`
and other special element at or below it. `implied(marks, tag)` answers in O(1) which index a start tag pops from (that element and
everything above it); the caller repeats until it returns None. Elements with optional end tags that this does not model (`td`, `tr`,
`option`, `h1`..`h6` among them) nest deeper than in a browser, which only hides more (LIMITATIONS.md).
"""

from __future__ import annotations

from .htmlrules import BARRIERS, SPECIAL

NONE = (-1, -1, -1, -1, -1)
BUTTON_SCOPE = BARRIERS | {"button"}
CLOSES_P = frozenset(("address article aside blockquote center details dialog dir div dl fieldset figcaption figure footer form h1 h2 h3 h4 "
                      "h5 h6 header hgroup hr li dd dt main menu nav ol p pre listing plaintext search section summary table ul xmp").split())
STOPS = SPECIAL - {"address", "div", "p", "li", "dd", "dt"}                          # a special element that ends the search for an open li/dd/dt


def marks(prev: tuple | None, tag: str, index: int) -> tuple:
    """The marks of a new stack entry `tag` at stack index `index`, from the marks of the entry below it (None for the first)."""
    p, bar, li, dd, stop = prev or NONE
    return (index if tag == "p" else p, index if tag in BUTTON_SCOPE else bar, index if tag == "li" else li,
            index if tag in ("dd", "dt") else dd, index if tag in STOPS else stop)


def implied(top: tuple | None, tag: str) -> int | None:
    """The stack index that the start tag `tag` pops from, given the marks of the top entry (None for an empty stack); None: nothing to pop."""
    if top is None:
        return None
    p, bar, li, dd, stop = top
    if tag == "li" and li > max(stop, dd):
        return li
    if tag in ("dd", "dt") and dd > max(stop, li):
        return dd
    return p if tag in CLOSES_P and p > bar else None
