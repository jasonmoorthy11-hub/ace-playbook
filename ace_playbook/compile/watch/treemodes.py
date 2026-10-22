"""The insertion modes of the sanitizer's tree builder (HTML Standard) as functions `mode(t, k, n, a, sc)`: `t` the `Tree`, `k` the kind ("c" characters, "s"
start tag, "e" end tag, "d" doctype), `n` its name (the data of "c"), `a` its attributes, `sc` self-closing. Stdlib only. Simpler than a browser, towards
hiding MORE: no start-tag rules for `a`, `nobr`, `button`, headings, `option`, `form`, `ruby`, an open select (end tags ignored but its own and a table end tag
in table scope, which ends a select in a table; nested selects stay open, a raw text `<` hides the rest, a `</template>` that closes a select holding a barrier
hides it too, applet, marquee, object above it: its ending table tag hides the rest, a table in it is no select in a table, a cell tag after a template-closed
select hides the rest, `</select>` needs scope), `Tree.adopt`. One function for the table modes (by `Tree.reset_mode`); a template keeps its own (`Node.tm`)."""

from __future__ import annotations

from .htmlrules import (BARRIERS, BREAKOUT, FORMAT, HEAD_TAGS, CTX, HEADINGS, IGNORED, OPS, RAW_ANY, SCOPED_END, SECTIONS, STANDARDS, STRUCT, VOID, WS,
                        begin, select_in_table, special)

def run(t, o, n, a):
    """A simple start tag (`htmlrules.OPS`): close a p, reconstruct, not frameset-ok, then the element (void, raw text or plain), the extras."""
    "p" in o and t.close_p(); "r" in o and t.reconstruct()
    if "f" in o: t.ok = False
    (t.void if "v" in o else t.rawtext if "x" in o else t.insert)(n, a)
    if "F" in o: t.fmt_push(t.stack[-1])
    if "q" in o: t.setraw(n)
    if "S" in o: t.mode = "select"

def before(t, k, n, a, sc):
    """initial / before html / before head: the first doctype decides the quirks mode; the implied element is made unless the token is its own start tag."""
    if k == "c" and not (n := n.lstrip(WS)): return
    if k == "d":
        if t.mode == "initial": t.quirks, t.mode = STANDARDS.fullmatch(n) is None, "beforehtml"
        return
    t.mode = "beforehtml" if t.mode == "initial" else t.mode            # anything else in the initial mode: quirks stays
    me = "html" if t.mode == "beforehtml" else "head"
    if (k == "e" and n not in ("head", "body", "html", "br")) or (k == "s" and n == "html" and me == "head"): return
    own = k == "s" and n == me
    el = t.insert(me, a if own else (), syn=not own)
    t.head = el if me == "head" else t.head
    t.mode = "head" if me == "head" else "beforehead"
    own or t.process(k, n, a, sc)

def template_end(t):          # closing a select that holds a barrier: the readings' formatting lists differ from here, so the rest hides (fail closed)
    t.poisoned |= bool(t.relaxed and (m := t.idx.get("template")) and (s := t.idx.get("select")) and s[-1] > m[-1] and not reach(t, m[-1]))
    if t.idx.get("template"): t.implied(thorough=True); t.pop_to("template"); t.reset_mode()      # no scope check: pops to the template whatever lies between
    s = t.relaxed and t.mode == "select" and t.idx.get("select"); s and t.stack[s[-1]].via_template(t)      # the older reading fell back to "in select"?

def head(t, k, n, a, sc):
    """in head and after head (an after-head head tag is inserted into the html element: all of them hide themselves)."""
    after = t.mode == "afterhead"
    if k == "c":
        s = n.lstrip(WS)                                             # the leading blanks are text (hidden by the head), the rest ends the head
        s != n and t.text(n[:len(n) - len(s)])
        if not (n := s): return
    elif k == "s":
        if n in ("html", "head"): return
        if n in ("base", "basefont", "bgsound", "link", "meta"): return t.void(n, a)
        if n in ("title", "noscript", "noframes", "style", "script"): return t.rawtext(n, a)
        if n == "template": t.insert(n, a); t.ok = False; t.mode = "template"; return
        if after and n == "body": t.insert(n, a); t.ok, t.mode = False, "body"; return
        if after and n == "frameset": t.dead = True; return          # the frameset replaces the body: nothing after it renders
    elif k == "e":
        if n == "template": return template_end(t)
        if n == "head" and not after: t.pop(); t.mode = "afterhead"; return
        if n not in ("body", "html", "br"): return
    else: return
    if after: t.insert("body", (), syn=True); t.mode = "body"
    else: t.pop(); t.mode = "afterhead"
    t.process(k, n, a, sc)

def any_other(t, n):
    """The "any other end tag" walk: the nearest open HTML element of that name closes (with what is above it) unless a special element lies between."""
    st = t.stack
    for i in range(len(st) - 1, -1, -1) if t.idx.get(n) else ():
        if st[i].ns == 0 and st[i].name == n: t.implied(n); return t.popto(i)
        if special(st[i]): return

def body_end(t, n):
    if n in SCOPED_END:
        if t.in_scope(n): t.implied(); t.pop_to(n)
    elif n in FORMAT:
        if not t.adopt(n): any_other(t, n)
    elif n in ("p", "li", "dd", "dt"):
        if t.in_scope(n, {"li": 1, "p": 2}.get(n, 0)): t.implied(n); t.pop_to(n)
    elif n in HEADINGS:
        if any(t.in_scope(h) for h in HEADINGS): t.implied(); t.pop_to(*HEADINGS)
    elif n in ("body", "html"):
        if t.in_scope("body"): t.mode = "afterbody"; n == "html" and t.process("e", n)
    elif n == "form":
        if t.in_scope("form"): t.implied(); t.stack[-1].name == "form" and t.pop()
    elif n == "br": run(t, "rvf", n, ())
    elif n in ("applet", "marquee", "object"):
        if t.in_scope(n): t.implied(); t.pop_to(n); t.clear_fmt()
    elif n == "template": template_end(t)
    else: any_other(t, n)

def body_start(t, n, a, sc):
    st, n = t.stack, "img" if n == "image" else n
    if n in HEAD_TAGS: head(t, "s", n, a, sc)
    elif n in ("li", "dd", "dt"):
        stop = ("li",) if n == "li" else ("dd", "dt")
        for i in range(len(st) - 1, -1, -1) if any(t.idx.get(x) for x in stop) else ():
            if st[i].ns == 0 and st[i].name in stop: t.implied(st[i].name); t.popto(i); break
            if special(st[i]) and not (st[i].ns == 0 and st[i].name in ("address", "div", "p")): break
        t.ok = False; t.close_p(); t.insert(n, a)
    elif n == "table":
        if not t.quirks: t.close_p()
        t.insert(n, a); t.ok, t.mode = False, "table"
    elif n == "input": run(t, "rv", n, a); t.ok = t.ok and next((v or "" for k, v in a if k == "type"), "").lower() == "hidden"
    elif n in ("svg", "math"): t.reconstruct(); t.insert(n, a, 1 if n == "svg" else 2); sc and t.pop()
    elif n == "frameset": t.dead = t.dead or (t.ok and len(st) > 1 and st[1].name == "body")      # the body is replaced: nothing renders
    elif n not in IGNORED: run(t, OPS.get(n, "r"), n, a)

def body(t, k, n, a, sc):
    if k == "c": t.reconstruct(); t.text(n); t.ok = t.ok and not n.strip(WS + "\x00")          # a NUL is ignored: frameset-ok stays
    elif k == "s": body_start(t, n, a, sc)
    elif k == "e": body_end(t, n)

def after(t, k, n, a, sc):
    """after body / after after body: blanks and `<html>` are read as body, `</html>` ends; anything else is the body again."""
    if k == "e" and n == "html" and t.mode == "afterbody": t.mode = "afterafter"
    elif k == "d" or (k == "s" and n == "html") or (k == "c" and not n.strip(WS)): body(t, k, n, a, sc)
    else: t.mode = "body"; t.process(k, n, a, sc)

def m_text(t, k, n, a, sc):
    if k == "c": return t.text(n)
    t.pop(); t.mode = t.orig; k == "s" and t.process(k, n, a, sc)       # the end tag of the raw text element (a start tag: the tokenizer was not raw)

# ---- tables
def intable(t, k, n, a, sc):
    """in table, caption, table body, row and cell (the mode is derived from the stack after every structural change)."""
    st, c, cell = t.stack, t.stack[-1], t.mode in ("caption", "cell")      # cell: closing the cell or the caption clears the formatting list ONCE
    if k == "s" and n in STRUCT:
        top = CTX.get(n, ("table",))
        while not (st[-1].ns == 0 and st[-1].name in (*top, "template", "html")): t.pop()
        cell and t.clear_fmt()                                       # close the cell / the caption (before a new td, th or caption pushes its marker)
        if st[-1].name == "template" and not st[-1].keeps(n): return t.reset_mode()      # the template's mode ignores it (a caption in a cell lands here)
        if st[-1].name == "table" and n in ("col", "tr", "td", "th"): t.insert("colgroup" if n == "col" else "tbody", (), syn=True)
        if n in ("td", "th") and st[-1].name in SECTIONS: t.insert("tr", (), syn=True)
        (t.void if n == "col" else t.insert)(n, a)
        t.reset_mode()
    elif n == "table" and (k == "e" or (k == "s" and t.mode not in ("caption", "cell"))):      # a start tag in a cell or caption is read as body (nests)
        if t.in_scope("table", 3): t.pop_to("table"); cell and t.clear_fmt(); t.reset_mode(); k == "s" and t.process(k, n, a, sc)
    elif k == "e" and n in ("caption", "colgroup", "tr", "td", "th", *SECTIONS):
        if t.in_scope(n, 3): t.pop_to(n); cell and t.clear_fmt(); t.reset_mode()
    elif k == "d" or (k == "e" and n in IGNORED): pass
    elif cell: body(t, k, n, a, sc)
    elif k == "c" and c.ns == 0 and c.name in ("table", "tbody", "template", "tfoot", "thead", "tr") and not n.strip(WS): t.text(n)
    elif n in ("style", "script", "template") and k != "c": head(t, k, n, a, sc)
    else:                                                            # anything else: foster parenting, read as body
        f, t.foster = t.foster, True
        body(t, k, n, a, sc)
        t.foster = f

# ---- select and template
def reach(t, m=None) -> bool:
    """May `input`, `keygen`, `textarea` end the open select? Nothing above it (over template `m`: the whole region) may be a marker, foreign or hide itself."""
    return bool(s := t.idx.get("select")) and all(e.ns == 0 and (e.name == "select" or e.name not in BARRIERS and not e.own) for e in t.stack[begin(s, m) + 1:])

def inselect(t, k, n, a, sc):
    st = t.stack
    if k == "c": t.text(n)
    elif k == "s":
        if n in ("option", "optgroup", "hr"):
            if st[-1].name == "option": t.pop()
            if n != "option" and st[-1].name == "optgroup": t.pop()
            (t.void if n == "hr" else t.insert)(n, a)
        elif n in ("script", "template"): head(t, k, n, a, sc)
        elif ((n in ("input", "keygen", "textarea") or (n == "select" and not t.relaxed)) and reach(t)) or (n in STRUCT and select_in_table(t)):
            t.stack[t.idx["select"][-1]].ends(t, k, n, a, sc)                               # a table tag ends a select in a table (`tq`)
        elif t.relaxed and n not in STRUCT:                          # any other start tag is inserted as it is (strict: ignored; STRUCT: as in body)
            if n in RAW_ANY: t.rawtext(n, a)
            elif n in ("svg", "math"): t.insert(n, a, 1 if n == "svg" else 2); sc and t.pop()
            else: run(t, "rF", n, a) if n in FORMAT else t.void(n, a) if n in VOID else t.insert(n, a)       # a formatting tag as in body
    elif k == "e":
        if n == "select" and (t.in_scope(n) if t.relaxed else reach(t)): t.popto(t.idx["select"][-1]); t.reset_mode()
        elif n in ("caption", "table", "tbody", "tfoot", "thead", "tr", "td", "th"): st[t.idx["select"][-1]].ends(t, k, n, a, sc)
        elif n in ("option", "optgroup"): st[-1].name == "option" and t.pop(); n == "optgroup" and st[-1].name == "optgroup" and t.pop()
        elif n == "template": head(t, k, n, a, sc)

def intemplate(t, k, n, a, sc):
    (intable if r else body)(t, k, n, a, sc) if (r := t.stack[t.idx["template"][-1]].builds(k, n)) is not None else None      # None: ignored by its mode

# ---- foreign content
def foreign_dispatch(cur, k, n) -> bool:
    # not for a start tag or characters at an HTML integration point or a MathML text one (`mglyph`, `malignmark` stay math), nor `<svg>` at `annotation-xml`
    ip = cur.ip
    return k == "e" if ip == 1 else not (k == "c" or (k == "s" and n not in ("mglyph", "malignmark"))) if ip == 2 else not (ip == 3 and k == "s" and n == "svg")

def m_foreign(t, k, n, a, sc):      # a breakout start tag, `</p>`, `</br>` pop the foreign run and are read again
    st = t.stack
    if k == "c": t.text(n); t.ok = t.ok and not n.strip(WS + "\x00")                        # foreign NUL: U+FFFD, frameset-ok untouched
    elif k == "s" and not (n in BREAKOUT or (n == "font" and any(x in ("color", "face", "size") for x, _ in a))):
        t.insert(n, a, st[-1].ns); sc and t.pop()                    # a self-closed foreign element is popped at once
    elif k == "s" or (k == "e" and n in ("br", "p")):
        while st[-1].ns and st[-1].ip not in (1, 2): t.pop()
        MODES[t.mode](t, k, n, a, sc)                                # read again by the insertion mode (an HTML element or integration point is current)
    elif k == "e":
        for i in range(len(st) - 1, -1, -1) if t.fcnt.get(n) else ():
            if st[i].ns == 0: break
            if st[i].name == n: return t.popto(i)
        MODES[t.mode](t, k, n, a, sc)                                # an HTML element is reached first: the rules of the insertion mode

MODES = {"initial": before, "beforehtml": before, "beforehead": before, "head": head, "afterhead": head, "body": body, "text": m_text, "table": intable,
         "caption": intable, "tbody": intable, "row": intable, "cell": intable, "colgroup": intable, "select": inselect, "template": intemplate,
         "afterbody": after, "afterafter": after}
