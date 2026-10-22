"""The node model and the visible-text walk of the sanitizer's HTML tree (`treebuild`). Stdlib only.

`Node`: an element, `ns` 0 html / 1 svg / 2 math, `kids` nodes and text, `ip` its integration kind (0 none, 1 HTML, 2 MathML text, 3 `annotation-xml`
without an HTML encoding: svg foreignObject desc title, math mi mo mn ms mtext, annotation-xml with encoding text/html or application/xhtml+xml),
`own` it hides itself (`own_attrs`: a SKIP name such as `script`, `template`, `head`, `svg`, `select`; a closed `details`; `hidden`, `inert`, `popover`,
`aria-hidden`, an inline style that hides; a declarative shadow host), `syn` the builder made it up (it prints no line break), `mask` the scope variants it
is a marker of (0 default, 1 list item, 2 button, 3 table). A node is also hidden when a simple rule of the style sheets matches its tag, class or id (names
without case), and so is everything below it. The document hides when the builder says it is `dead` (a frameset, a shadow root on the document) or when ANY
`html` or `body` start tag hides (a browser merges the late ones onto the root). A visible block is a line of its own; `br` and `hr` print a blank."""

from __future__ import annotations

from .cssstyle import style_hidden
from .htmlrules import HEAD_TAGS, STRUCT, select_in_table

SKIP = frozenset(("script style template noscript dialog svg math iframe object embed head title meta link canvas audio video datalist "
                  "noembed noframes rp select progress meter").split())
BLOCK = frozenset("address article aside blockquote dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header "
                  "li main nav ol p pre section table tbody td tfoot th thead tr ul".split())
HIDING = frozenset(("hidden", "inert", "popover"))
MARK = {(0, n): (0, 1, 2) for n in "applet caption marquee object td th".split()}                      # scope markers by (namespace, name)
MARK.update({(0, n): (0, 1, 2, 3) for n in ("html", "table", "template")})
MARK.update({(0, "ol"): (1,), (0, "ul"): (1,), (0, "button"): (2,)})
MARK.update({(2, n): (0, 1, 2) for n in "mi mo mn ms mtext annotation-xml".split()})
MARK.update({(1, n): (0, 1, 2) for n in ("foreignobject", "desc", "title")})
FIRST = {**{n: "table" for n in "caption colgroup tbody tfoot thead".split()}, "col": "colgroup", "tr": "tbody", "td": "row", "th": "row"}      # template mode
KEEP = {"table": STRUCT, "colgroup": frozenset(("col",)), "tbody": frozenset(("tr", "td", "th")), "row": frozenset(("td", "th"))}      # tags each keeps


def own_attrs(name: str, attrs) -> bool:
    """Does an element hide itself, whatever the style sheets say (any copy of a repeated attribute counts)?"""
    if name in SKIP or (name == "details" and not any(k == "open" for k, _ in attrs)):
        return True
    return any(k in HIDING or (k == "aria-hidden" and (v or "").strip().lower() == "true") or (k == "style" and style_hidden(v or "")) for k, v in attrs)


class Node:
    __slots__ = ("name", "ns", "attrs", "parent", "kids", "ip", "own", "syn", "mask", "open", "pos", "tm", "tq")

    def __init__(self, name: str, ns: int = 0, attrs=(), syn: bool = False) -> None:
        self.name, self.ns, self.attrs, self.syn, self.parent, self.kids, self.open, self.pos = name, ns, attrs, syn, None, [], False, -1
        self.mask, self.own, self.ip, self.tm = MARK.get((ns, name), ()), own_attrs(name, attrs), 0, None
        self.tq = False
        if ns == 1:
            self.ip = 1 if name in ("foreignobject", "desc", "title") else 0
        elif ns == 2:
            enc = next((v or "" for k, v in attrs if k == "encoding"), "").lower()
            tx = name in ("mi", "mo", "mn", "ms", "mtext")
            self.ip = (1 if enc in ("text/html", "application/xhtml+xml") else 3) if name == "annotation-xml" else 2 if tx else 0

    def builds(self, k: str, n: str):
        """For a template, its insertion mode `tm` is set once, by its first start tag outside HEAD_TAGS (`FIRST`; "body" otherwise), and never changes.
        True: the token goes to the table rules (a structure tag its mode keeps: `KEEP`); False: to the body, whose IGNORED set drops the other structure
        tags; None: ignored (any other start tag in "colgroup" mode, `<table>` in a table mode). Characters and end tags always go to the body."""
        if k != "s" or n in HEAD_TAGS: return False
        self.tm = self.tm or FIRST.get(n, "body")
        if n in STRUCT: return self.keeps(n)
        return None if self.tm == "colgroup" or (n == "table" and self.tm != "body") else False

    def keeps(self, n: str) -> bool:
        """Does the template's mode build this table-structure tag? Else a browser ignores it: no tbody, tr or table lies in scope inside the template."""
        return n in KEEP.get(self.tm, ())

    def via_template(self, t) -> None:
        """Called on the open select after a `</template>` reset the mode to "select". If its "in a table" status (`select_in_table`) comes only from a
        table-mode template (a table below that template, none between), the older reading has fallen back to "in select" (its reset walk meets the template
        before any table) and ignores a cell tag, while the newer one (Chrome) ends the select: the readings split, so `tq` makes that tag poison the rest."""
        tb, tp = ((t.idx.get(k) or [-1])[-1] for k in ("table", "template"))
        self.tq = self.tq or (tb < tp and t.stack[tp].tm in ("table", "tbody", "row"))

    def ends(self, t, k: str, n: str, a, sc: bool) -> None:
        """This open select is ended by `input` `keygen` `textarea` `select` (`reach` passed), a table start tag (`STRUCT`) or a table end tag (caption
        table tbody tfoot thead tr td th) that is in table scope while the select is in a table, past anything it holds; else the end tag is ignored. Pop
        it, reset the mode, read the token again (not `<select>`). A table tag of a select that `via_template` flagged (`tq`), or past an applet, marquee
        or object (only the newer reading inserts them, each with a marker: the cell clears differ), poisons the rest."""
        if k == "e" and not (select_in_table(t) and t.in_scope(n, 3)): return
        up = any(e.ns == 0 and e.name in ("applet", "marquee", "object") for e in t.stack[t.idx["select"][-1] + 1:])
        t.poisoned |= (k == "e" or n in STRUCT) and (self.tq or up)
        t.popto(t.idx["select"][-1]); t.reset_mode()
        n != "select" and t.process(k, n, a, sc)


def hidden(name: str, own: bool, attrs, rules: tuple) -> bool:
    classes, ids, tags = rules
    if own or name in tags:
        return True
    if classes or ids:
        d = {k: v or "" for k, v in reversed(attrs)}                 # the first copy of a repeated attribute wins
        return d.get("id", "").lower() in ids or bool(classes & set(d.get("class", "").lower().split()))
    return False


def visible(tree, rules: tuple) -> str:
    """The visible text of `tree` (lines separated by newlines, not yet cleaned) under `rules` (classes, ids, tags)."""
    if tree.dead or any(hidden(name, own_attrs(name, attrs), attrs, rules) for name, attrs in tree.hb):
        return ""
    out: list[str] = []
    todo = [(tree.root.kids, 0, "")]                                 # (children, next index, closer) of the open elements
    while todo:
        kids, i, close = todo.pop()
        while i < len(kids):
            c, i = kids[i], i + 1
            if c.__class__ is str:
                out.append(c)
                continue
            blk, hid = "\n" if c.name in BLOCK and not c.syn else "", hidden(c.name, c.own, c.attrs, rules)
            out.append(" " if c.name in ("br", "hr") and not hid else blk)
            if hid or not c.kids:
                out.append("" if c.name in ("br", "hr") else blk)
                continue
            todo.extend(((kids, i, close), (c.kids, 0, blk)))
            break
        else:
            out.append(close)
    return "".join(out)
