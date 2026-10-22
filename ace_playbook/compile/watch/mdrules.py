"""Markdown pages for `sanitize`: when is a raw-text opener (`<script>`, `<style>`, ...) prose and not markup? Stdlib only.

The rule is fail closed. An opener is PROSE only when ALL of these hold; every other opener stays markup, which `strip_hidden` hides to
its closer or to the end of the input:

* it sits inside an inline code span or a fenced code block (CommonMark: a backtick run closes at the next run of the same length in the
  same paragraph; an opener after a backslash is not one; a backtick inside a tag, as in `<a title="`">`, opens nothing);
* it is not inside an HTML block (a line that starts with `<tag`, `</tag`, `<!` or `<?`, after any list or quote markers, runs to the next
  blank line, a `<script>`/`<pre>`/`<style>`/`<textarea>` block to its closing tag, a comment to `-->`), where Markdown has no code;
* NO matching closer (`</script` for `<script`) follows it anywhere later in the input, however the closer is written. A closer
  means a browser or a renderer would hide what lies between them, so the text there is never shown.

`markdown_code_as_text(text)` escapes the `<` of the prose openers (`&lt;`) so the HTML parser reads them as text.
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
TICKS, TAG = re.compile(r"`+"), re.compile(r"</?[A-Za-z][^<>]*>")


def _code_spans(text: str, base: int) -> list[tuple[int, int]]:
    """The inside of every inline code span of one paragraph `text` (which starts at offset `base` of the page), as page offsets."""
    tags = [m.span() for m in TAG.finditer(text)]
    starts = [a for a, _ in tags]

    def in_tag(i: int) -> bool:
        k = bisect_right(starts, i) - 1
        return k >= 0 and i < tags[k][1]

    runs = [(m.start(), m.end()) for m in TICKS.finditer(text) if not in_tag(m.start())]
    by_len: dict[int, list[int]] = {}
    for n, (a, b) in enumerate(runs):
        by_len.setdefault(b - a, []).append(n)
    out, n = [], 0
    while n < len(runs):
        (a, b), same = runs[n], by_len[runs[n][1] - runs[n][0]]
        k = bisect_right(same, n)
        if k < len(same) and not (a and text[a - 1] == "\\"):
            out.append((base + b, base + runs[same[k]][0]))
            n = same[k] + 1
        else:
            n += 1
    return out


def prose_ranges(text: str) -> list[tuple[int, int]]:
    """Offsets `(start, end)` of the code spans and fenced blocks that lie outside every HTML block, in order. One pass over the lines."""
    ranges: list[tuple[int, int]] = []
    para: list[int] = []                                              # offsets (start, end) of the open paragraph, or []
    fence, block, pos = None, None, 0                                 # fence: (char, length); block: None, "blank" or a closing pattern

    def flush() -> None:
        if para:
            ranges.extend(_code_spans(text[para[0]:para[1]], para[0]))
            para.clear()

    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
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
            flush()
            fence = (m.group(1)[0], len(m.group(1)))                  # the info string is not rendered text: it stays markup
        elif (h := HTML_START.match(line)):
            flush()
            name = (h.group(1) or "").lower()
            block = re.compile(rf"</{name}\b", re.I) if name in TYPE_ONE else BLOCK_END.get(h.group(2) or h.group(3) or h.group(4) or "", "blank")
            if block != "blank" and block.search(line, h.end()):
                block = None
        elif not line.strip():
            flush()
        else:
            para[:] = [para[0] if para else start, pos]
    flush()
    return ranges


def markdown_code_as_text(text: str) -> str:
    """`text` with the `<` of the prose raw-text openers (see the module docstring) escaped; everything else is returned as it is."""
    if not OPENER.search(text):
        return text
    last: dict[str, int] = {}
    for m in CLOSER.finditer(text):
        last[m.group("name").lower()] = m.start()
    out, at = [], 0
    for a, b in prose_ranges(text):
        for m in OPENER.finditer(text, a, b):
            if m.start() > last.get(m.group("name").lower(), -1):
                out += [text[at:m.start()], "&lt;"]
                at = m.start() + 1
    return "".join(out) + text[at:]
