"""The CSS scanner behind `cssstyle`: comments, strings, escapes, nested blocks and selectors, read the way a browser tokenizes. Stdlib only.

One regex (`PUNCT`) finds the punctuation `{`, `}` and `;` and steps OVER everything that hides it: a string (`"{"`, `'}'`; it ends at its quote,
at an unescaped newline or at the end of the text), a backslash escape (`\\{`) and an unquoted `url(...)`, whose ident is recognised AFTER escape
decoding, as a browser does (`\\75rl(`, `u\\72l(`, `\\55RL(`, `URL(`; a quote after the `(` makes it a function with a string). All of it is linear.

* `strip_comments(css)`: `/* ... */` removed (an unclosed one runs to the end) except inside a string, an escape or an unquoted url, so
  `content:"/*";display:none` keeps its declaration. The comment is removed, not replaced by a space (`di/**/splay`: fail closed).
* `unescape(text)`: CSS escapes decoded, `\\68 ` (one to six hex digits and one optional white space) and `\\c` (any other character).
* `rules(css)`: `(selector, body)` of each plain rule in document order. A rule with nested blocks (CSS nesting) is read with the nested blocks CUT
  OUT of its body, and each nested rule is read on its own too (over-hiding: a browser would match it only inside its parent). The rules
  inside a CLOSED at-rule block (a media query) are dropped, `@import x;` ends at its semicolon, blocks nested past `MAX_NESTING` are
  skipped, and a rule still open at the end of the text is closed there, as a browser does (an unclosed at-rule block keeps its rules).
  A `(` or `[` opens a SIMPLE BLOCK (CSS Syntax 3) that runs to its own `)` or `]`: inside it `{`, `}` and `;` are plain values, and a `{`, `(`
  or `[` opens a nested block that must close first (`foo(})` keeps its `}`; a closer that matches nothing is ignored). A block still open at
  the end swallows the rest of the text, as in a browser. Comments are already gone (`strip_comments`), so `foo(/*)` opens a comment.
* `simple_selectors(prelude)`: `(prefix, name)` for each member of a selector list that is one `.class`, `#id`, tag or `*`; the list is split
  on UNESCAPED commas only and the name is decoded and lower-cased (`.\\68` is `.h`; `.a\\,b` is the one class `a,b`).
"""

from __future__ import annotations

import re

MAX_NESTING = 512
STRING = r"\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"?|'[^'\\\n]*(?:\\.[^'\\\n]*)*'?"


def _letter(c: str) -> str:
    """The ident spellings of the letter `c` (no hex digit: u, r, l), as a regex: the letter in either case, `\\c`, or a hex escape of it
    (up to six digits, leading zeros allowed, one optional white space after it). The negative look-ahead keeps `\\750` from being a `u`."""
    return rf"(?:{c}|\\{c}|\\0{{0,4}}(?:{ord(c):x}|{ord(c.upper()):x})(?![0-9a-f])(?:\r\n|[ \t\r\n\f])?)"


URL = _letter("u") + _letter("r") + _letter("l") + r"\((?![ \t\r\n\f]*[\"'])[^)\\]*(?:\\.[^)\\]*)*\)?"            # `url(` as the DECODED ident spells it
SKIP = rf"{STRING}|{URL}|\\."                                       # URL before the escape: `\75rl(` starts with a backslash
COMMENT, PUNCT = re.compile(rf"({SKIP})|/\*.*?(?:\*/|$)", re.S | re.I), re.compile(rf"(?:{SKIP})|([{{}};()\[\]])", re.S | re.I)
CLOSER = {"(": ")", "[": "]", "{": "}"}
ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|\\(.)", re.S)
HEX = r"(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{1,5}(?![0-9a-fA-F]))"                                  # one way to read a run of hex digits: no backtracking
IDENT = rf"(?:[\w-]|\\(?:{HEX}(?:\r\n|[ \t\r\n\f])?|[^\n\r\f0-9a-fA-F]))"
SIMPLE, SPLIT, AT_START = re.compile(rf"[.#]?{IDENT}+|\*"), re.compile(r"([^,\\]*(?:\\.?[^,\\]*)*)(?:,|$)", re.S), re.compile(r"\s*@")


def strip_comments(css: str) -> str:
    """`css` without its comments (see the module docstring)."""
    return COMMENT.sub(lambda m: m.group(1) or "", css)


def _char(m: re.Match) -> str:
    if m.group(1):
        n = int(m.group(1), 16)
        return chr(n) if 0 < n <= 0x10FFFF and not 0xD800 <= n <= 0xDFFF else ""
    return m.group(2)


def unescape(text: str) -> str:
    return ESCAPE.sub(_char, text)


def _close(stack: list, found: list, css: str, end: int, eof: bool = False) -> None:
    """Pop the open block: record a plain rule (the pieces of its body between nested blocks, plus the rest up to `end`) or drop the rules
    found inside a closed at-rule block (an at-rule block still open at the end of the text keeps them)."""
    sel, pieces, cur, mark = stack.pop()
    if "@" not in sel:
        found.append((sel, "".join(pieces) + css[cur:end]))
    elif not eof:
        del found[mark:]
    if stack:
        stack[-1][2] = end + 1                                      # the parent's body goes on after this block


def rules(css: str) -> list[tuple[str, str]]:
    """(selectors, body) of each plain rule of `css` (comments already stripped), see the module docstring."""
    found: list[tuple[str, str]] = []
    stack: list[list] = []                    # [selector, body pieces so far, start of the piece being read (-1: a nested block is open), len(found)]
    start, head, extra = 0, None, 0           # head: does the text since `start` begin with "@" (None: not looked at yet)
    blocks: list[str] = []                    # closers of the open simple blocks `(`, `[` and a `{` inside one: no structure inside them
    for m in PUNCT.finditer(css):
        ch, i = m.group(1), m.start()
        if ch is None:
            continue
        if blocks or ch in "([)]":            # CSS Syntax 3 "consume a simple block": it runs to its own closer, nested blocks close in order
            if blocks and ch == blocks[-1]:
                blocks.pop()
            elif ch in CLOSER and (blocks or ch != "{"):
                blocks.append(CLOSER[ch])
            continue
        if ch == ";":
            if not stack:
                head = AT_START.match(css, start) is not None if head is None else head
                start, head = (i + 1, None) if head else (start, head)
        elif ch == "{":
            sel = css[start:i]
            sel = sel.rpartition(";")[2] if stack else sel          # in a block a `;` ends a declaration; at the top level the rule is invalid
            if len(stack) < MAX_NESTING:
                if stack:
                    stack[-1][1].append(css[stack[-1][2]:i - len(sel)])
                    stack[-1][2] = -1
                stack.append([sel, [], i + 1, len(found)])
            else:
                extra += 1                        # a block nested past the cap is never read as a rule
            start, head = i + 1, None
        else:
            if extra:
                extra -= 1
            elif stack:
                _close(stack, found, css, i)
            start, head = i + 1, None
    while stack:
        _close(stack, found, css, len(css), eof=True)
    return found


def simple_selectors(prelude: str) -> list[tuple[str, str]]:
    """(prefix, name) of each `.class` ("."), `#id` ("#"), tag ("") or `*` in the selector list `prelude`, escapes decoded, lower-cased."""
    out = []
    for sel in (s.strip() for s in SPLIT.findall(prelude)):
        if SIMPLE.fullmatch(sel):
            prefix = sel[0] if sel[0] in ".#" else ""
            name = unescape(sel[len(prefix):]).lower()
            if name:
                out.append((prefix, name))
    return out
