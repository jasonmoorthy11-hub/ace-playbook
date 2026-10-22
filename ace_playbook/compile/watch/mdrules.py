"""Markdown pages for `sanitize`: when is a raw-text opener (`<script>`, `<style>`, ...) prose and not markup, and which lines render nothing?
Stdlib only.

The rule is fail closed. An opener is PROSE only when ALL of these hold; every other opener stays markup, which `strip_hidden` hides to
its closer or to the end of the input:

* it sits inside a fenced code block, or inside an inline code span that opens and closes on ONE line (CommonMark pairs a backtick run
  with the next run of the same length, but a heading, a quote, a list item, a table cell or a thematic break in between cuts the span, so
  a span is trusted only when it does not cross a line). That line must start with no block marker (`#`, `>`, `-`, `*`, `+`, `|`, `1.`,
  after any spaces), carry no backslash directly before a backtick (an escape may drop the first backtick only), have no `<` before the
  opening run (a tag may hide a backtick in a quoted attribute) and no `|` inside the span (a table cell ends there);
* it is not inside an HTML block (a line that starts with `<tag`, `</tag`, `<!` or `<?`, after any list or quote markers, runs to the next
  blank line, a `<script>`/`<pre>`/`<style>`/`<textarea>` block to its closing tag, a comment to `-->`), where Markdown has no code;
* NO matching closer (`</script` for `<script`) follows it anywhere later in the input, however the closer is written. A closer
  means a browser or a renderer would hide what lies between them, so the text there is never shown.

* it is not in a fence that starts away from the left margin (a fence with an indent can sit in a list item, which ends the fence and
  the code with it; the indent alone cannot tell a container from a top-level fence): such a fence is not trusted, its lines keep their
  HTML meaning, and a bare opener in them hides to its closer or the end of the input (fail closed; only a fence at column 0 makes prose).

A link reference definition (`[label]: url "title"`, the `[//]: # (comment)` idiom included) renders nothing, and a line is dropped ONLY
when it is a STRICTLY valid one (`DEFINITION`, CommonMark 0.31): the label on one line, `:`, a destination (`<...>` with no `<`, `>` or
newline inside, or a run of non-space characters with no `<` and balanced parentheses), optionally a title fully quoted with `"..."`,
`'...'` or `(...)` of at most three lines and no blank line, and nothing else on the last line. The destination and the title may
follow on the next line; any line after the first that a paragraph could not continue (a quote, a heading, a fence, a list item, a
thematic break, a `<` at all) makes it no definition. A definition cannot start inside a paragraph, so the line after paragraph text
stays. That paragraph is tracked as a renderer does: a heading, a thematic break (`---`, `***`) or a setext underline that has a paragraph
above it ends it, but a `===`, `=` or `--` line with NO paragraph open is itself paragraph text and a definition after it is not one
(fail closed: its tags keep their HTML meaning). The same holds for such a line INSIDE a quote or a list item (`cont`: set by a `>` or a list
marker line, a BARE marker (`-`, `1.`) too, which opens an empty item, unless a lone `-` is the underline of the paragraph above it; kept
over blank and indented lines, cleared by an unindented line that is no marker after a blank line or that is a heading
or a thematic break): there it is a lazy continuation of the paragraph, never a setext underline (`> foo` then `===`).
Whatever fails the rule is not dropped and keeps its HTML meaning: `[x]: /u <script>` hides to the closer, as a renderer
passes the tag through. Never "a definition and whatever goes on with it": a definition is at most a label, a destination and a
title, each strictly valid.

`markdown_code_as_text(text, prose, definitions)` escapes the `<` of the prose openers (`&lt;`) so the HTML parser reads them as text and
drops the definition lines. `drop_definitions(text)` drops the strictly valid ones from VISIBLE text (no paragraph rule: the text of an
HTML page has no structure left, and a visible `[x]: url` line there is never markup).
"""

from __future__ import annotations

import re
from bisect import bisect_right

RAW_TEXT = ("script|style|title|textarea|xmp|noembed|noframes|iframe|noscript|details|select|svg|math|template|object|dialog|canvas|video|audio"
            "|datalist|head|rp|progress|meter")                      # every tag that raw-text or hides its content: prose in a trusted code span
OPENER = re.compile(rf"<(?P<name>{RAW_TEXT})(?=[\s/>]|$)", re.I)
CLOSER = re.compile(rf"</(?P<name>{RAW_TEXT})(?=[\s/>]|$)", re.I)
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
HTML_START = re.compile(r"[ \t>]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)*<(?:([A-Za-z][A-Za-z0-9-]*)|/[A-Za-z]|(!--)|(\?)|(!\[CDATA\[)|!)")
BLOCK_END = {"!--": re.compile(r"-->"), "?": re.compile(r"\?>"), "![CDATA[": re.compile(r"\]\]>")}
TYPE_ONE = frozenset(("script", "pre", "style", "textarea"))
TICKS, MARKER = re.compile(r"`+"), re.compile(r"[ \t]*(?:[#>|*+-]|\d{1,9}[.)])")


def _quoted(o: str, c: str) -> str:
    """A title opened by `o` and closed by `c`: at most three lines, none blank, backslash escapes allowed."""
    line = rf"(?:[^{re.escape(o + c)}\\\n]|\\[^\n])*"
    return rf"{re.escape(o)}{line}(?:\n(?![ \t\r]*(?:\n|\Z)){line}){{0,2}}{re.escape(c)}"


_ARG = r"(?:[^\s()<\\\x00-\x1f\x7f]|\\\S)"
DESTINATION = rf"(?:<(?:[^<>\n\\]|\\.)*>|(?:{_ARG}|\({_ARG}*\))+)"
TITLE = "|".join(_quoted(o, c) for o, c in (('"', '"'), ("'", "'"), ("(", ")")))
DEFINITION = re.compile(rf" {{0,3}}\[(?![ \t]*\])(?:[^\[\]\\\n]|\\[^\n]){{1,999}}\]:[ \t]*(?:\r?\n[ \t]*)?{DESTINATION}"
                        rf"(?:(?:[ \t]+|[ \t]*\r?\n[ \t]*)(?:{TITLE}))?[ \t]*(?:\r?\n|\Z)")
INTERRUPT = re.compile(r" {0,3}(?:<|>|#{1,6}(?:[ \t\r]|$)|`{3}|~{3}|[-*+_](?:[ \t\r]|$)|\d{1,9}[.)](?:[ \t\r]|$)|[-*_=]{2,}[ \t\r]*$)")
ENDS_PARAGRAPH = re.compile(r" {0,3}(?:#{1,6}(?:[ \t\r]|$)|([-*_])(?:[ \t]*\1){2,}[ \t\r]*$)")        # an ATX heading or a thematic break, whatever is above
SETEXT = re.compile(r" {0,3}[=-]+[ \t\r]*$")                      # an underline ends a paragraph ONLY when one is open; else it is text (`===`, `--`)
CONTAINER = re.compile(r"[ \t]*(?:>|(?:[-*+]|\d{1,9}[.)])(?:[ \t]+[^ \t\r\n]|[ \t\r]*$))")     # a line that opens or continues a quote or list item
# (a BARE marker line opens an empty item; under a paragraph a lone `-` is a setext underline instead, see `scan`)


def _definition(text: str, start: int) -> int:
    """The end offset of the strictly valid link reference definition that starts at `start` (the start of a line), else 0."""
    m = DEFINITION.match(text, start)
    return m.end() if m and not any(INTERRUPT.match(ln) for ln in text[start:m.end()].splitlines()[1:]) else 0


def drop_definitions(text: str) -> str:
    """`text` without its strictly valid link reference definitions (see the module docstring)."""
    out, pos = [], 0
    while pos < len(text):
        end = _definition(text, pos)
        if not end:
            end = text.find("\n", pos) + 1 or len(text)
            out.append(text[pos:end])
        pos = end
    return "".join(out).rstrip("\n")                                  # the text of `strip_hidden` has no blank lines and no edge newlines


def _spans(line: str, base: int) -> list[tuple[int, int]]:
    """The inside of every code span of one line that may be trusted as prose (see the module docstring), as page offsets."""
    if "\\`" in line or MARKER.match(line):
        return []
    runs = [m.span() for m in TICKS.finditer(line)]
    by_len: dict[int, list[int]] = {}
    for n, (a, b) in enumerate(runs):
        by_len.setdefault(b - a, []).append(n)
    out, n, seen, scanned = [], 0, False, 0                             # seen: a `<` lies outside the trusted spans before the opener
    while n < len(runs):
        (a, b), same = runs[n], by_len[runs[n][1] - runs[n][0]]
        k = bisect_right(same, n)
        seen, scanned = seen or line.find("<", scanned, a) >= 0, max(scanned, a)
        if k == len(same):
            n += 1
            continue
        end = runs[same[k]][0]
        if not seen and "|" not in line[b:end]:
            out.append((base + b, base + end))
            scanned = runs[same[k]][1]
        n = same[k] + 1
    return out


def scan(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(offsets of the code spans and fenced blocks outside every HTML block, offsets of the link definitions: all their lines), in order."""
    ranges: list[tuple[int, int]] = []
    drops: list[tuple[int, int]] = []
    fence, block, para, skip, pos = None, None, False, 0, 0            # fence: (char, length); block: None, "blank" or a closing pattern
    #                                                                    para: the line above is paragraph text; skip: end of the open definition
    cont, gap = False, False                                           # cont: inside a quote or list item (a `===` line is lazy text); gap: blank line above
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        if start < skip:
            continue                                                  # a later line of a definition that began above
        m = FENCE.match(line)
        if fence:
            ranges.append((start, pos))
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
            para = False
        elif block is not None:
            if block == "blank":
                block = None if not line.strip() else block
            elif block.search(line):
                block = None
            para = False
        elif m and m.start(1) == 0 and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence, para = (m.group(1)[0], len(m.group(1))), False     # only a fence at column 0: the info string is not rendered text
        elif not para and (start == 0 or text[start - 1] == "\n") and (end := _definition(text, start)):
            drops.append((start, end))
            skip, para = end, False
        elif (h := HTML_START.match(line)):
            name = (h.group(1) or "").lower()
            block = re.compile(rf"</{name}\b", re.I) if name in TYPE_ONE else BLOCK_END.get(h.group(2) or h.group(3) or h.group(4) or "", "blank")
            if block != "blank" and block.search(line, h.end()):
                block = None
            para = False
        else:
            ranges.extend(_spans(line, start))
            ends = ENDS_PARAGRAPH.match(line)
            opens = CONTAINER.match(line) and not (para and SETEXT.match(line))        # a lone `-` under a paragraph is its underline
            cont = bool(opens) or (cont and not (line[0] not in " \t" and (ends or (gap and line.strip()))))
            gap = not line.strip()
            para = bool(line.strip()) and not ends and not (para and not cont and SETEXT.match(line))
    return ranges, drops


def markdown_code_as_text(text: str, prose: bool = True, definitions: bool = True) -> str:
    """`text` with the `<` of the prose raw-text openers (see the module docstring) escaped and the link definition lines dropped;
    everything else is returned as it is. `prose` and `definitions` switch the two edits off."""
    if not (prose and OPENER.search(text)) and not (definitions and "]:" in text):
        return text
    last: dict[str, int] = {}
    for m in CLOSER.finditer(text):
        last[m.group("name").lower()] = m.start()
    ranges, drops = scan(text)
    edits: list[tuple[int, int, str]] = [(a, b, "\n" if text[b - 1] == "\n" else "") for a, b in drops] if definitions else []
    for a, b in ranges if prose else ():
        for m in OPENER.finditer(text, a, b):
            if m.start() > last.get(m.group("name").lower(), -1):
                edits.append((m.start(), m.start() + 1, "&lt;"))
    out, at = [], 0
    for a, b, new in sorted(edits):
        out += [text[at:a], new]
        at = b
    return "".join(out) + text[at:]
