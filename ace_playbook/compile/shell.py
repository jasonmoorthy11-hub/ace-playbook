"""Linear-time Bash command tokenizer (a port of Step 0's scanlib, hardened).

The command is agent-controlled text, so every step here is linear in its length: one left-to-right
pass handles quotes and heredocs, at most MAX_HEREDOCS heredoc openers are looked at, and the rest
is split by regex. Nothing is executed, expanded or passed to a shell; strings are only split.

Differences from Step 0 (each is a parity deviation only on hostile input, see step0_parity.json):
a heredoc removes only its BODY (the rest of the opening line is still tokenized); `<<<` is a here-string,
not a heredoc; a single `&`, braces, `!` and the keywords if/then/do/else/while start a new segment or
are stripped from its front; env/nice/xargs/doas/busybox join the wrappers that are stripped.
"""

from __future__ import annotations

import re

MAX_HEREDOCS = 32   # openers examined per command; later `<<` are plain text (more tokens, never fewer)

_OPENER = re.compile(r"<<-?[ \t]*(['\"]?)(\w{1,64})(?!\w)\1")
_SPECIAL = re.compile(r"[\"'<\n]")
_DQ_RUN = re.compile(r"(?:[^\"\\]+|\\[^\n])*")
_SIMPLE = re.compile(r"^[\w./~$@%+=:,-]*$")
_SPLIT = re.compile(r"&&|\|\||;|\||\n|\(|\)|`|(?<![>&])&(?![>&])")
_ASSIGN = re.compile(r"^\w+=\S*$")
_WRAPPERS = frozenset("sudo time nohup command exec env nice xargs doas busybox".split())
_LEAD = frozenset("{ } ! if then elif else while until do".split())


def _mask(inner: str) -> str:
    return inner if _SIMPLE.match(inner) else "Q"


def _dq_end(s: str, i: int) -> tuple[int, int]:
    """(index of the closing quote, _) or (-1, index where the scan failed). Linear."""
    p = _DQ_RUN.match(s, i + 1).end()
    return (p, p) if p < len(s) and s[p] == '"' else (-1, p)


def _terminator(word: str) -> re.Pattern:
    sp = "[ \\t\\r\\f\\v]*"
    return re.compile(rf"(?m)^{sp}{re.escape(word)}{sp}$")


def _scan(s: str) -> str:
    """Mask quoted strings (single tokens stay, prose becomes Q) and drop heredoc bodies."""
    out: list[str] = []
    n, i, dead, attempts = len(s), 0, 0, 0
    skip: dict[int, int] = {}          # newline index ending an opener line -> end of its body
    while i < n:
        m = _SPECIAL.search(s, i)
        if not m:
            out.append(s[i:])
            break
        p = m.start()
        out.append(s[i:p])
        c = s[p]
        i = p + 1
        if c == "\n":
            out.append("\n")
            i = skip.get(p, i)
        elif c == '"':
            end = -1
            if p >= dead:                  # after a failed scan, no `"` before `dead` can match
                end, fail = _dq_end(s, p)
                if end < 0:
                    dead = fail
            if end >= 0:
                out.append(_mask(s[p + 1:end]))
                i = end + 1
            else:
                out.append('"')
        elif c == "'":
            end = s.find("'", p + 1)
            if end >= 0:
                out.append(_mask(s[p + 1:end]))
                i = end + 1
            else:
                out.append("'")
        elif s.startswith("<<<", p):     # here-string, not a heredoc
            out.append("<<<")
            i = p + 3
        elif s.startswith("<<", p):
            o = _OPENER.match(s, p) if attempts < MAX_HEREDOCS else None
            attempts += bool(o)
            e = s.find("\n", o.end()) if o else -1
            t = _terminator(o.group(2)).search(s, skip.get(e, e + 1)) if e >= 0 else None
            if t:
                skip[e] = t.end()          # the body goes; the rest of the opening line stays
                i = o.end()
            else:
                out.append("<<")
                i = p + 2
        else:
            out.append(c)
    return "".join(out)


def command_segments(cmd: str | None) -> list[list[str]]:
    """Split a Bash command into token lists, ignoring heredoc bodies and quoted prose."""
    segs = []
    for part in _SPLIT.split(_scan(cmd or "")):
        toks = part.split()
        while toks and (_ASSIGN.match(toks[0]) or toks[0] in _WRAPPERS or toks[0] in _LEAD):
            wrapper = toks.pop(0) in _WRAPPERS
            while wrapper and toks and toks[0].startswith("-"):
                toks.pop(0)                # a wrapper's own options: env -i, nice -n, xargs -0
        if toks:
            segs.append(toks)
    return segs


def git_subcommand(toks: list[str]) -> tuple[str | None, list[str]]:
    """('push', [args]) for ['git','-C','x','push','--force'] else (None, [])."""
    if not toks or toks[0] != "git":
        return None, []
    i = 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i >= len(toks):
        return None, []
    return toks[i], toks[i + 1:]
