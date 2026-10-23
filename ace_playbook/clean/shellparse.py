"""Split a Bash command line into token lists for the origin tracker (D-169, commit review F3), stdlib only.

Quote-aware: a comment (`#` at a word start), a here-document body and a quoted `<<` are told apart before anything is tokenised, and a
backslash-newline is joined. A command that cannot be parsed unambiguously (an open quote at the end, a `<<` with no terminator, a heredoc that
is never closed) is reported as not ok; the caller then fails closed.
"""

from __future__ import annotations

import shlex

WORD_START = " \t\n;&|()"
WORD_END = " \t\n;&|()<>"


def scan(chunk: str) -> tuple[str, bool, list[str], bool]:
    """(text without comments, quote still open, heredoc terminators, a `<<` with no terminator)."""
    out, quote, terms, bad, i = [], "", [], False, 0
    while i < len(chunk):
        c = chunk[i]
        if c == "\\" and quote != "'" and i + 1 < len(chunk):
            out.append(chunk[i:i + 2])
            i += 2
            continue
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (not out or out[-1][-1] in WORD_START):
            break
        elif chunk.startswith("<<", i) and chunk[i - 1:i] != "<" and chunk[i + 2:i + 3] != "<":
            word, i = _terminator(chunk, i + 2)
            terms += [word] if word else []
            bad = bad or not word
            out.append("<< HEREDOC")
            continue
        out.append(c)
        i += 1
    return "".join(out), bool(quote), terms, bad


def _terminator(chunk: str, i: int) -> tuple[str, int]:
    """The delimiter word after `<<` (or `<<-`), quotes and a leading backslash removed; the index after it."""
    i += chunk[i:i + 1] == "-"
    while chunk[i:i + 1] in (" ", "\t"):
        i += 1
    start, word = i, ""
    while i < len(chunk) and chunk[i] not in WORD_END:
        if chunk[i] in "'\"":
            end = chunk.find(chunk[i], i + 1)
            end = len(chunk) if end < 0 else end
            word += chunk[i + 1:end]
            i = end + 1
        else:
            word += chunk[i] if chunk[i] != "\\" else ""
            i += 1
    return word, max(i, start)


def tokens(text: str) -> list[str] | None:
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split, lex.commenters = True, ""
    try:
        return list(lex)
    except ValueError:
        return None


def analyse(cmd: str) -> tuple[list[list[str]], bool]:
    """Token lists of every logical line of `cmd` (heredoc bodies and comments removed), and whether all of it parsed."""
    lines, out, ok, i = cmd.replace("\\\n", "").split("\n"), [], True, 0
    while i < len(lines):
        chunk, i = lines[i], i + 1
        text, open_q, terms, bad = scan(chunk)
        while open_q and i < len(lines):
            chunk, i = chunk + "\n" + lines[i], i + 1
            text, open_q, terms, bad = scan(chunk)
        toks = None if open_q else tokens(text)
        ok = ok and toks is not None and not bad
        out += [toks] if toks else []
        for term in terms:
            while i < len(lines) and lines[i].strip() != term:
                i += 1
            ok, i = ok and i < len(lines), i + 1
    return out, ok


def every_word(cmd: str) -> set[str]:
    """Fail-closed fallback: each non-flag word of the command, quotes and shell punctuation trimmed."""
    words = (w.strip("'\";&|()<>") for w in cmd.split())
    return {w for w in words if w and not w.startswith("-")}
