"""The tokenizer side of the sanitizer's HTML tree builder (`treebuild`): element sets, the double-escaped `<script>` state and the html.parser subclasses the
tree is fed from. Stdlib only. `SPECIAL`: the spec's special category (HTML names; `special(node)` asks by namespace: svg `desc` is special, an HTML `desc`
is not); `OPS`: what a start tag of the simple kind does in the body (see `treemodes.run`); `ScriptState`: html.parser has no "script data double escaped"
state, so the tokenizer's is run over the script text (`<script><!--<script></script>X</script>` hides X); `ScriptedParser`: a slash on a start tag is
reported in `selfclosing`, a comment ends where a browser ends it (`<!-->`, `<!--->`, `-->`, `--!>`), `<![CDATA[` (any `<![`) is a bogus comment to the
first `>` unless the current node is not an HTML element (`foreign()`), where it is a real section to `]]>` whose text is character data; `TreeParser`:
feeds a `Tree` and hands the tokenizer the builder's decisions (`Tree.raw`: only an HTML element enters raw text or RCDATA, `foreign()`); `MAX_DEPTH`: more
open elements are `TooDeep`."""

from __future__ import annotations

import re
from html.parser import HTMLParser

MAX_DEPTH = 512
WS = " \t\n\f\r"
STANDARDS = re.compile(r"doctype[\t\n\f\r ]+html(?:[\t\n\f\r ]+system[\t\n\f\r ]+(?:\"about:legacy-compat\"|'about:legacy-compat'))?[\t\n\f\r ]*", re.I)
HEADINGS, SECTIONS = ("h1", "h2", "h3", "h4", "h5", "h6"), ("tbody", "tfoot", "thead")
CTX = {"col": ("colgroup", "table"), "tr": (*SECTIONS, "table"), "td": ("tr", *SECTIONS, "table"), "th": ("tr", *SECTIONS, "table")}  # clear back to
VOID = frozenset("area base basefont bgsound br col embed frame hr img input keygen link meta param source track wbr".split())
HTML_SPECIAL = frozenset(("address applet area article aside base basefont bgsound blockquote body br button caption center col colgroup dd "
                     "details dialog dir div dl dt embed fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head header "
                     "hgroup hr html iframe img input keygen li link listing main marquee menu meta nav noembed noframes noscript object ol "
                     "p param plaintext pre script search section select source style summary table tbody td template textarea tfoot th "
                     "thead title tr track ul wbr xmp").split())
FOREIGN_SPECIAL = frozenset((1, n) for n in ("foreignobject", "desc", "title")) | frozenset((2, n) for n in "mi mo mn ms mtext annotation-xml".split())
SPECIAL = HTML_SPECIAL | {n for _, n in FOREIGN_SPECIAL}                                    # the special names of every namespace
HEAD_TAGS = frozenset("base basefont bgsound link meta noframes script style template title".split())
RAW_ANY = frozenset("title noscript noframes style script xmp iframe noembed textarea plaintext".split())
FORMAT = frozenset("a b big code em font i nobr s small strike strong tt u".split())
IGNORED = frozenset("html body caption col colgroup frame head tbody td tfoot th thead tr".split())          # start tags a browser ignores in the body
STRUCT = frozenset("caption colgroup col tbody tfoot thead tr td th".split())
BARRIERS = frozenset("applet caption html marquee object table td template th foreignobject desc title mi mo mn ms mtext annotation-xml".split())
BREAKOUT = frozenset(("b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 h6 head hr i img li listing menu meta nobr ol "
                      "p pre ruby s small span strong strike sub sup table tt u ul var").split())
SCOPED_END = frozenset(("address article aside blockquote button center details dialog dir div dl fieldset figcaption figure footer header hgroup listing "
                        "main menu nav ol pre search section summary ul").split())
OPS = {}                  # simple start tags: p close a p, r reconstruct, f not frameset-ok, v void, x raw text, F formatting, q plaintext, S select
for _names, _ops in (("address article aside blockquote center details dialog dir div dl fieldset figcaption figure footer form header hgroup main menu "
                      "nav ol p search section summary ul h1 h2 h3 h4 h5 h6", "p"), ("pre listing", "pf"), ("hr", "pvf"), ("xmp", "prxf"),
                     ("param source track", "v"),
                     ("area br embed img keygen wbr", "rvf"), ("applet marquee object button", "rf"), ("plaintext", "pq"), ("textarea iframe", "xf"),
                     ("noembed noscript", "x"), ("select", "rSf"), (" ".join(FORMAT), "rF")):
    OPS.update(dict.fromkeys(_names.split(), _ops))
RCDATA = getattr(HTMLParser, "RCDATA_CONTENT_ELEMENTS", ())                                 # textarea, title (and plaintext): a newer html.parser
COMMENT_ABRUPT, COMMENT_END = re.compile(r"-?>"), re.compile(r"--!?>")
SCRIPT_MARKS = re.compile(r"<!--->|<!-->|<!--|-->|<script(?=[\t\n\f\r />])|</script(?=[\t\n\f\r />])", re.I | re.A)


class TooDeep(ValueError):
    """More than MAX_DEPTH open elements, or too many overlapping `<style` openers: `strip_hidden` gives up and `fetch` drops the page as `parse`."""


def special(node) -> bool:
    """Is `node` (a name and a namespace: 0 html, 1 svg, 2 math) in the spec's special category?"""
    return node.name in HTML_SPECIAL if node.ns == 0 else (node.ns, node.name) in FOREIGN_SPECIAL


def select_in_table(t) -> bool:
    """Was the open select opened in a table? Only if the last table lies BELOW it, no template between (a table above it, opened inside it: no)."""
    s, tb, tp = ((t.idx.get(k) or [-1])[-1] for k in ("select", "table", "template")); return tp < tb < s


class ScriptState:
    """The script-data states of the HTML tokenizer: `data`, `escaped` (after `<!--`) and `double` (after `<!--` and `<script`)."""

    def __init__(self) -> None:
        self.state = "data"

    def feed(self, text: str) -> None:
        for m in SCRIPT_MARKS.finditer(text):
            tok = m.group().lower()
            if tok == "<!--":
                self.state = "escaped" if self.state == "data" else self.state
            elif tok in ("<!--->", "<!-->", "-->"):
                self.state = "data"
            elif tok == "<script" and self.state == "escaped":
                self.state = "double"
            elif tok == "</script" and self.state == "double":
                self.state = "escaped"

    def closes(self) -> bool:
        """A `</script` has just been read: True when it ends the element, False (and the state drops to `escaped`) in the double state."""
        if self.state == "double":
            self.state = "escaped"
            return False
        return True


class ScriptedParser(HTMLParser):
    """An HTMLParser that reads `<script>`, comments and CDATA the way a browser does (see the module docstring); a subclass sets `self.script`
    when a script enters raw text, asks `script_stays_open(tag)` first in `handle_endtag` and overrides `foreign()` and `set_cdata_mode`."""

    CDATA_CONTENT_ELEMENTS = (*HTMLParser.CDATA_CONTENT_ELEMENTS, "noscript")                   # raw text; noscript: a browser with scripting on

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._support_cdata = False                               # the EOF path of `goahead` must agree with `parse_html_declaration` below
        self.script: ScriptState | None = None                    # the script-data state of the open <script>
        self.again = False                                        # a `</script` inside a double-escaped script: stay in script mode
        self.selfclosing = False                                  # the start tag being handled had a slash

    def parse_comment(self, i, report=1):
        """A comment ends where a browser ends it (`<!-->`, `<!--->`, `-->`, `--!>`) whatever the patch release of html.parser: older ones read
        `<!-->` and `--!>` as comment text, so a `<style>` behind them was never found (`--!>` is no terminator in script data or RCDATA)."""
        raw = self.rawdata
        if raw[i:i + 4] != "<!--":
            raise AssertionError("unexpected call to parse_comment()")
        m = COMMENT_ABRUPT.match(raw, i + 4)
        end = m or COMMENT_END.search(raw, i + 4)
        if not end:
            return -1
        if report:
            self.handle_comment("" if m else raw[i + 4:end.start()])
        return end.end()

    def foreign(self) -> bool:
        return False                                                # True: the current node is not an HTML element, `<![CDATA[` is a real section

    def parse_html_declaration(self, i):
        """`<![CDATA[`, `<![if x]>` and every other `<![` is a bogus comment that ends at the first `>` (a browser: markup declaration open state);
        only where `foreign()` is `<![CDATA[` a section that ends at `]]>`, whose text is character data."""
        raw = self.rawdata
        if not raw.startswith("<![", i):
            return super().parse_html_declaration(i)
        real = raw.startswith("<![CDATA[", i) and self.foreign()
        end = raw.find("]]>" if real else ">", i + 3)
        if end < 0:
            return -1
        self.handle_data(raw[i + 9:end]) if real else self.handle_comment(raw[i + 2:end])
        return end + (3 if real else 1)

    def handle_startendtag(self, tag, attrs) -> None:
        """A browser ignores the slash on a non-void HTML element: every raw-text name is offered to `set_cdata_mode`, as `parse_starttag` does."""
        self.selfclosing = True
        self.handle_starttag(tag, attrs)
        self.selfclosing = False
        if tag in self.CDATA_CONTENT_ELEMENTS or tag == "plaintext":
            self.set_cdata_mode(tag)
        elif tag in RCDATA:
            self.set_cdata_mode(tag, escapable=True)

    def clear_cdata_mode(self) -> None:
        if self.again:                                              # the end tag was a `</script` of a double-escaped script
            self.again = False
            return
        super().clear_cdata_mode()

    def script_stays_open(self, tag: str) -> bool:
        """True for a `</script` that a double-escaped script reads as text: the end tag does nothing."""
        if tag == "script" and self.script is not None and not self.script.closes():
            self.again = True
            return True
        return False

    def handle_data(self, data) -> None:
        if self.script is not None:
            self.script.feed(data)


class TreeParser(ScriptedParser):
    def __init__(self, tree) -> None:
        super().__init__()
        self.t = tree

    def foreign(self) -> bool:
        return bool(self.t.stack) and self.t.stack[-1].ns != 0

    def set_cdata_mode(self, elem, **kw) -> None:
        """Raw text and RCDATA only where the tree builder asked: an HTML element (in foreign content every such name is an ordinary element)."""
        if self.t.raw == elem:
            self.t.raw = None
            self.script = ScriptState() if elem == "script" else self.script
            super().set_cdata_mode(elem, **kw)

    def handle_starttag(self, tag, attrs) -> None:
        self.t.hb.append((tag, attrs)) if tag in ("html", "body") else None
        self.t.raw = None
        self.t.process("s", tag, attrs, self.selfclosing)

    def handle_endtag(self, tag) -> None:
        t = self.t
        if self.script_stays_open(tag):
            return
        if t.selraw:                                                # the closer of the raw text held by a select
            t.selraw, t.poisoned = False, t.poisoned or t.rawlt
        t.process("e", tag)
        self.script = None if tag == "script" else self.script

    def handle_data(self, data) -> None:
        super().handle_data(data)
        self.t.rawlt = self.t.rawlt or (self.t.selraw and "<" in data)
        self.t.process("c", data)

    def handle_decl(self, decl) -> None:
        self.t.process("d", decl)
