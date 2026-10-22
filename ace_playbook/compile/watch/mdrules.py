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

A link reference definition (`[label]: url "title"`, the `[//]: # (comment)` idiom included) renders nothing: outside fences and HTML
blocks its line is dropped, with the lines that go on with it (a destination or a title on the next line, a title that is not closed on
its own line, up to the next blank line). A definition inside a paragraph is dropped too (over-hiding is safe).

`markdown_code_as_text(text, prose, definitions)` escapes the `<` of the prose openers (`&lt;`) so the HTML parser reads them as text and
drops the definition lines.
"""

from __future__ import annotations

import re
from bisect import bisect_right

RAW_TEXT = "script|style|title|textarea|xmp|noembed|noframes|iframe"
OPENER = re.compile(rf"<(?P<name>{RAW_TEXT})(?=[\s/>]|$)", re.I)
CLOSER = re.compile(rf"</(?P<name>{RAW_TEXT})(?=[\s/>]|$)", re.I)
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
HTML_START = re.compile(r"[ \t>]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)*<(?:([A-Za-z][A-Za-z0-9-]*)|/[A-Za-z]|(!--)|(\?)|(!\[CDATA\[)|!)")
BLOCK_END = {"!--": re.compile(r"-->"), "?": re.compile(r"\?>"), "![CDATA[": re.compile(r"\]\]>")}
TYPE_ONE = frozenset(("script", "pre", "style", "textarea"))
TICKS, MARKER = re.compile(r"`+"), re.compile(r"[ \t]*(?:[#>|*+-]|\d{1,9}[.)])")
DEFINITION, TITLE_LINE, TITLE_OPEN = re.compile(r" {0,3}\[[^\]\n]+\]:(?:\s|$)"), re.compile(r"[ \t]{0,3}[\"'(]"), re.compile(r"\s([\"'(])")


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


def _incomplete(rest: str) -> bool:
    """True when what follows `[label]:` is empty (the destination is on the next line) or opens a title that this line does not close."""
    if not rest.strip():
        return True
    m = TITLE_OPEN.search(rest)
    return bool(m) and (")" if m.group(1) == "(" else m.group(1)) not in rest[m.end():]


def scan(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(offsets of the code spans and fenced blocks outside every HTML block, offsets of the link definition lines), each in order."""
    ranges: list[tuple[int, int]] = []
    drops: list[tuple[int, int]] = []
    fence, block, more, pos = None, None, None, 0                     # fence: (char, length); block: None, "blank" or a closing pattern

    for line in text.splitlines(keepends=True):
        start, pos, more, after = pos, pos + len(line), None, more
        m = FENCE.match(line)
        if fence:
            ranges.append((start, pos))
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
        elif block is not None:
            if block == "blank":
                block = None if not line.strip() else block
            elif block.search(line):
                block = None
        elif m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = (m.group(1)[0], len(m.group(1)))                  # the info string is not rendered text: it stays markup
        elif after and line.strip() and (after == "block" or TITLE_LINE.match(line)):
            drops.append((start, pos))                                # a destination or a title that goes on from a definition
            more = "block" if after == "block" or _incomplete(" " + line.strip()) else None
        elif DEFINITION.match(line):
            drops.append((start, pos))
            more = "block" if _incomplete(line[line.index("]:") + 2:]) else "title"
        elif (h := HTML_START.match(line)):
            name = (h.group(1) or "").lower()
            block = re.compile(rf"</{name}\b", re.I) if name in TYPE_ONE else BLOCK_END.get(h.group(2) or h.group(3) or h.group(4) or "", "blank")
            if block != "blank" and block.search(line, h.end()):
                block = None
        else:
            ranges.extend(_spans(line, start))
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
