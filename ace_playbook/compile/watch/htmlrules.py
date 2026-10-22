"""The HTML tree rules behind `sanitize`: element sets, which end tags a browser ignores, the double-escaped `<script>`. Stdlib only.

A browser ignores an end tag whose element is not "in scope", and `sanitize` must never show text that a browser would still hide, so when
a hidden element is involved the same rules apply and anything doubtful keeps the text hidden (fail closed):

* `end_ignored(stack, i)`: the end tag that targets `stack[i]` pops nothing when it would unhide something and an element in between stops
  it. A block-level end tag (`</div>`, ...) is stopped by a scope barrier (`table td th caption object marquee applet template`); `</p>` also
  by `button`, `</li>` also by `ol` and `ul`; a table end tag by a nested `table` or `template`; every other end tag (`</span>`, `</b>`, ...)
  by any "special" element in between. An open `select` stops every end tag but its own (the in-select insertion mode drops them).
  `INTEGRATION` (`foreignObject`, `desc`, `title`, `mi`, `mo`, `mn`, `ms`, `mtext`, `annotation-xml`) are scope barriers too.
* `SKIP` holds the elements whose content a browser does not render as text, `select` (all its option text), `progress` and `meter`
  (fallback text) among them.
* `ScriptState`: html.parser has no "script data double escaped" state, so `<script><!--<script></script>X</script>` would show X.
  The state machine of the HTML tokenizer is run over the script text instead: a `</script` seen in the double-escaped state does not
  close the element.
* `ScriptedParser`: the html.parser subclass that both passes of `sanitize` (the style text of `stylepass`, the visible text) share, so
  they tokenize alike: a slash on `<script/>`, `<style/>`, `<noscript/>`, `<xmp/>`, `<iframe/>`, `<noembed/>`, `<noframes/>`, `<textarea/>`,
  `<title/>` or `<plaintext/>` is ignored, `noscript` is raw text (a browser with scripting on) in HTML content only: in foreign content
  (`foreign()`) or inside an open `<select>` (`in_select()`) it is an ordinary element, a double-escaped script keeps its next `</script` as
  text, a comment ends at `<!-->`, `<!--->`, `-->` or `--!>`, and `<![CDATA[` is a bogus comment that ends at the first `>` (any `<![` is),
  except in foreign content, where it runs to `]]>`.
* `MAX_DEPTH`: the open-element stack is capped; a document nested deeper is a parse failure (`TooDeep`), not a quadratic scan.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

MAX_DEPTH = 512
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
SKIP = frozenset(("script style template noscript dialog svg math iframe object embed head title meta link canvas audio video datalist "
                  "noembed noframes rp select progress meter").split())
FORMATTING = frozenset("a b big code em font i nobr s small strike strong tt u".split())
BLOCK = frozenset("address article aside blockquote dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header "
                  "li main nav ol p pre section table tbody td tfoot th thead tr ul".split())
INTEGRATION = frozenset("foreignobject desc title mi mo mn ms mtext annotation-xml".split())                  # foreign scope markers (see `treerules`)
BARRIERS = frozenset("applet caption html marquee object table td template th".split()) | INTEGRATION
TABLES = frozenset("table tbody tfoot thead tr td th caption colgroup".split())
SPECIAL = frozenset(("address applet area article aside base basefont bgsound blockquote body br button caption center col colgroup dd "
                     "details dialog dir div dl dt embed fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head header "
                     "hgroup hr html iframe img input keygen li link listing main marquee menu meta nav noembed noframes noscript object ol "
                     "p param plaintext pre script search section select source style summary table tbody td template textarea tfoot th "
                     "thead title tr track ul wbr xmp mi mo mn ms mtext foreignobject desc").split())
EXTRA = {"p": frozenset(("button",)), "li": frozenset(("ol", "ul"))}                      # per-tag scope barriers beyond BARRIERS
RCDATA = getattr(HTMLParser, "RCDATA_CONTENT_ELEMENTS", ())                                 # textarea, title (and plaintext): a newer html.parser
COMMENT_ABRUPT, COMMENT_END = re.compile(r"-?>"), re.compile(r"--!?>")
SCRIPT_MARKS = re.compile(r"<!--->|<!-->|<!--|-->|<script(?=[\t\n\f\r />])|</script(?=[\t\n\f\r />])", re.I | re.A)


class TooDeep(ValueError):
    """More than MAX_DEPTH open elements: `strip_hidden` gives up and `fetch` drops the page as `parse`."""


def end_ignored(stack: list, i: int) -> bool:
    """True when the end tag for `stack[i]` (entries are `[tag, hidden, own]`) pops nothing, because a browser would ignore it and
    popping would unhide the text of an element that is still open."""
    above = stack[i + 1:]
    if not above or not (stack[i][1] or any(e[1] for e in above)):
        return False
    names = {e[0] for e in above}
    tag = stack[i][0]
    if "select" in names:
        return True
    if tag in TABLES:
        return bool(names & {"table", "template"})
    return bool(names & (BARRIERS | EXTRA.get(tag, frozenset()) if tag in SPECIAL else SPECIAL))


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
    """An HTMLParser that reads `<script>` the way a browser does (see the module docstring). A subclass sets `self.script =
    ScriptState()` when it sees the start tag, asks `script_stays_open(tag)` first in `handle_endtag`, calls `super().handle_data` and
    overrides `foreign()` when it tracks the open elements."""

    CDATA_CONTENT_ELEMENTS = (*HTMLParser.CDATA_CONTENT_ELEMENTS, "noscript")                   # raw text; noscript: a browser with scripting on

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._support_cdata = False                               # the EOF path of `goahead` must agree with `parse_html_declaration` below
        self.script: ScriptState | None = None                    # the script-data state of the open <script>
        self.again = False                                        # a `</script` inside a double-escaped script: stay in script mode

    def parse_comment(self, i, report=1):
        """A comment ends where a browser ends it (`<!-->`, `<!--->`, `-->`, `--!>`) whatever the patch release of html.parser: older ones
        read `<!-->` and `--!>` as comment text, so a `<style>` behind them was never found. Done here, not by rewriting the page, because
        `--!>` is no terminator in script data, an attribute or RCDATA."""
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
        """True when the current node is a foreign (svg or math) element: a CDATA section is a real one there. A subclass that tracks the
        open elements overrides this; the default (False) reads every `<![CDATA[` as a bogus comment, which only matters for what follows it."""
        return False

    def parse_html_declaration(self, i):
        """`<![CDATA[`, `<![if x]>` and every other `<![` is a bogus comment that ends at the first `>` (a browser: markup declaration open
        state), whatever the patch release of html.parser; only in foreign content is `<![CDATA[` a section that ends at `]]>`."""
        raw = self.rawdata
        if not raw.startswith("<![", i):
            return super().parse_html_declaration(i)
        real = raw.startswith("<![CDATA[", i) and self.foreign()
        end = raw.find("]]>" if real else ">", i + 3)
        if end < 0:
            return -1
        self.handle_comment(raw[i + 2:end])
        return end + (3 if real else 1)

    def in_select(self) -> bool:
        """True when a `<select>` is open: a browser ignores a `<noscript>` start tag there, so its content is not raw text. A subclass that
        tracks the open elements overrides this; the default (False) only matters for a `<noscript>` inside a select."""
        return False

    def set_cdata_mode(self, elem, **kw) -> None:
        """`<noscript>` is raw text only in HTML content (a browser with scripting on); in foreign content or a select it is an element."""
        if elem == "noscript" and (self.foreign() or self.in_select()):
            return
        super().set_cdata_mode(elem, **kw)

    def handle_startendtag(self, tag, attrs) -> None:
        """A browser ignores the slash on a non-void element: every raw-text element enters its mode, as `parse_starttag` does for `<x>`."""
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)
        elif tag in RCDATA:
            self.set_cdata_mode(tag, escapable=True)
        elif tag == "plaintext" and RCDATA:
            self.set_cdata_mode(tag)

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
