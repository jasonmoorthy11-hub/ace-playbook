"""The HTML tree builder of the sanitizer (HTML Standard, tree construction): ONE tree (`Node` of `treewalk`) from html.parser tokens (`htmlrules.TreeParser`),
read by the style pass (`treestyle`) and the visible-text walk (`treewalk`); the insertion modes are in `treemodes`. Stdlib only. `Tree` keeps the stack of
open elements with indexes (`idx` HTML positions by name, `fcnt` foreign names, `mk` scope-marker positions per scope variant) so a scope check or an end
tag that names nothing open costs O(1); inserts at the appropriate place (FOSTER PARENTING: what a table cannot hold goes before it); keeps the active
formatting list (markers pushed on insert, cleared on pop of td, th, caption, template and at `</applet|marquee|object>`; Noah's Ark; `reconstruct`; simplified
`adopt`). `relaxed`: an open select is read two ways, the older in-select mode ignores start tags (strict), the newer inserts them (relaxed, the visible
text: a raw-text element a select holds that has a `<` hides the REST of the page, `selraw` `rawlt` `poisoned`); the style pass builds both. A stack past
`MAX_DEPTH` is `TooDeep`."""

from __future__ import annotations

from .htmlrules import MAX_DEPTH, TooDeep, TreeParser, special
from .treemodes import MODES, foreign_dispatch, m_foreign
from .treewalk import Node

TABLEISH = frozenset("table tbody tfoot thead tr".split())
IMPLIED = frozenset("dd dt li optgroup option p rb rp rt rtc".split())
IMPLIED_ALL = IMPLIED | frozenset("caption colgroup tbody td tfoot th thead tr".split())
MARKERS = frozenset("applet caption marquee object td th template".split())
RESET = {"select": "select", "td": "cell", "th": "cell", "tr": "row", "tbody": "tbody", "thead": "tbody", "tfoot": "tbody", "caption": "caption",
         "colgroup": "colgroup", "table": "table", "head": "head", "body": "body", "frameset": "body"}


class Tree:
    def __init__(self, relaxed: bool = True) -> None:
        self.relaxed, self.root, self.stack, self.idx, self.fcnt, self.mk, self.fmt = relaxed, Node("#document"), [], {}, {}, ([], [], [], []), []
        self.mode = self.orig = "initial"
        self.hb, self.styles, self.head, self.raw = [], [], None, None            # hb: (name, attrs) of every html / body start tag
        self.quirks, self.ok, self.foster, self.dead = True, True, False, False                    # ok: frameset-ok; foster: foster parenting is on
        self.selraw = self.rawlt = self.poisoned = False

    def _add(self, n: Node) -> None:
        n.open, n.pos = True, len(self.stack) - 1
        if n.ns:
            self.fcnt[n.name] = self.fcnt.get(n.name, 0) + 1
        else:
            self.idx.setdefault(n.name, []).append(n.pos)
        for v in n.mask:
            self.mk[v].append(n.pos)

    def push(self, n: Node) -> None:
        self.stack.append(n)
        self._add(n)
        if len(self.stack) > MAX_DEPTH:
            raise TooDeep("nesting too deep")

    def pop(self) -> Node:
        n = self.stack.pop()
        n.open = False
        if n.ns:
            self.fcnt[n.name] -= 1
        else:
            self.idx[n.name].pop()
            if n.name in ("td", "th", "caption", "template"): self.clear_fmt()    # applet, marquee, object: only at their own end tag
        for v in n.mask:
            self.mk[v].pop()
        return n

    def clear_fmt(self) -> None:
        while self.fmt and self.fmt.pop() is not None: pass         # clear the list of active formatting elements up to the last marker

    def popto(self, i: int) -> None:
        while len(self.stack) > i:                                  # pop down to position i, the element there included
            self.pop()

    def pop_to(self, *names: str) -> None:
        while not ((e := self.pop()).ns == 0 and e.name in names):  # pop until an HTML element of one of the names has gone (the caller checked)
            pass

    def in_scope(self, name: str, v: int = 0) -> bool:
        """Is an HTML `name` in scope (v: 0 default, 1 list item, 2 button, 3 table)? No marker of that variant lies above its last open instance."""
        ids = self.idx.get(name)
        return bool(ids) and (not self.mk[v] or ids[-1] >= self.mk[v][-1])

    def implied(self, skip: str | None = None, thorough: bool = False) -> None:
        st = self.stack                                             # generate implied end tags (except for `skip`)
        while st and st[-1].ns == 0 and st[-1].name in (IMPLIED_ALL if thorough else IMPLIED) and st[-1].name != skip:
            self.pop()

    def close_p(self) -> None:
        if self.in_scope("p", 2):
            self.implied("p")
            self.pop_to("p")

    def where(self) -> tuple:
        """The appropriate place for inserting: (parent, child to insert before or None). Foster parenting sends what a table cannot hold to the node
        before the last table (into the last template, when one is above it)."""
        st = self.stack
        if not st:
            return self.root, None
        if self.foster and st[-1].ns == 0 and st[-1].name in TABLEISH:
            tpl, tbl = self.idx.get("template"), self.idx.get("table")
            if tpl and (not tbl or tpl[-1] > tbl[-1]):
                return st[tpl[-1]], None
            if not tbl:
                return st[0], None
            t = st[tbl[-1]]
            return (t.parent, t) if t.parent else (st[tbl[-1] - 1], None)
        return st[-1], None

    def place(self, item) -> Node:
        parent, ref = self.where()
        if item.__class__ is not str:
            item.parent = parent
        k = parent.kids
        k.append(item) if ref is None else k.insert(len(k) - 1 if k[-1] is ref else k.index(ref), item)
        return parent

    def text(self, data: str) -> None:
        if not self.poisoned:
            self.place(data)

    def insert(self, name: str, attrs=(), ns: int = 0, syn: bool = False) -> Node:
        n = Node(name, ns, attrs, syn)
        host = self.place(n)
        if name == "style":
            self.styles.append(n)
        elif ns == 0 and name in MARKERS:
            self.fmt.append(None)
            if name == "template" and any(k in ("shadowrootmode", "shadowroot") for k, _ in attrs):
                if host.ns == 0 and host.name in ("html", "head", "#document"):
                    self.dead = True                                # a declarative shadow root on the document: all of it is unslotted
                else:
                    host.own = True                                 # the host hides every child, those before the template too
        self.push(n)
        return n

    def void(self, name: str, attrs=()) -> None:
        self.insert(name, attrs)
        self.pop()

    def setraw(self, name: str) -> None:
        """The tokenizer enters raw text for the element just inserted. A raw-text element opened while a select is open (not `script`) is tracked:
        if its text holds a `<` the REST of the document stays hidden once it closes (a browser may have read that markup as elements)."""
        self.raw, self.rawlt, self.selraw = name, False, name != "script" and bool(self.idx.get("select"))

    def rawtext(self, name: str, attrs) -> None:
        """The generic raw text / RCDATA parsing algorithm: insert, raw tokenizer, remember the mode, `text` mode."""
        self.insert(name, attrs)
        self.setraw(name)
        self.orig, self.mode = self.mode, "text"

    def fmt_push(self, n: Node) -> None:
        same = 0
        for e in reversed(self.fmt):
            if e is None:
                break
            if e.name == n.name and e.attrs == n.attrs:
                same += 1
                if same == 3:                                       # the Noah's Ark clause: three equal copies after the last marker at most
                    self.fmt.remove(e)
                    break
        self.fmt.append(n)

    def reconstruct(self) -> None:
        """Re-open the formatting elements that an end tag popped: a hidden `<b>` is hidden again around the text that follows."""
        f = self.fmt
        if not f or f[-1] is None or f[-1].open:
            return
        i = len(f) - 1
        while i and f[i - 1] is not None and not f[i - 1].open:
            i -= 1
        for j in range(i, len(f)):
            f[j] = self.insert(f[j].name, f[j].attrs)

    def adopt(self, name: str) -> bool:
        """The end tag of a formatting element; False: none in the list ("any other end tag"). The adoption agency is SIMPLIFIED where it is ambiguous
        for a tokenizer-fed builder: without a furthest block the element and all above it are popped, as in the spec; with one (a special element
        above it) nothing is popped and the element stays open, hiding at least what a browser that re-parents the block hides (D-128)."""
        f = self.fmt
        fe = next((e for e in reversed(f) if e is None or e.name == name), None)
        if fe is not None and not fe.open:
            f.remove(fe)
        elif fe is not None and not (self.mk[0] and fe.pos < self.mk[0][-1]) and not any(special(e) for e in self.stack[fe.pos + 1:]):
            self.popto(fe.pos)
            f.remove(fe)
        return fe is not None

    def reset_mode(self) -> None:
        """Reset the insertion mode appropriately: by the nearest open element that decides it."""
        best = max(((ids[-1], nm) for nm in (*RESET, "template") if (ids := self.idx.get(nm))), default=(0, ""))[1]
        self.mode = ("afterhead" if self.head else "beforehead") if not best else "template" if best == "template" else RESET[best]

    def process(self, k: str, name: str, attrs=(), sc: bool = False) -> None:
        """Dispatch a token (k: "c" characters, "s" start tag, "e" end tag, "d" doctype) to the foreign content rules or the insertion mode."""
        if self.dead:
            return
        st = self.stack
        if st and st[-1].ns and foreign_dispatch(st[-1], k, name):
            m_foreign(self, k, name, attrs, sc)
        else:
            MODES[self.mode](self, k, name, attrs, sc)


def build(src: str, relaxed: bool = True) -> Tree:
    """The tree of `src` (TooDeep past MAX_DEPTH open elements)."""
    parser = TreeParser(Tree(relaxed))
    parser.feed(src)
    parser.close()
    return parser.t
