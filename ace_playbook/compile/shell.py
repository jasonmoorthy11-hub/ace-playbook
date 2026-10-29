"""Linear-time Bash command tokenizer (a port of Step 0's scanlib, hardened).

The command is agent-controlled text, so every step here is linear in its length: one left-to-right
pass handles quotes, comments and heredocs, at most MAX_HEREDOCS heredoc openers are looked at, and
the rest is split by regex. Nothing is executed, expanded or passed to a shell; strings are only split.

Differences from Step 0 (parity deviations only on unusual input, see step0_parity.json): a heredoc
removes only its BODY (cut at the real end of its command line) and is not opened for `<<<`, an escaped `<<`,
`<<` inside `((`/`$((` (tracked by paren level), or after a comment `#`
(a word-initial, unescaped `#` drops the rest of the line; backslash-newline continues a line); `$'..'` and `$".."` are quoted words; a single `&`
separates segments; braces, `!` and if/then/do/else/while are stripped from a segment's front, and so
are leading redirections and the wrappers sudo, env, nice, xargs, doas, timeout, ... with their options.
"""

from __future__ import annotations

import posixpath
import re

MAX_HEREDOCS = 32   # openers examined per command; later `<<` are plain text (more tokens, never fewer)

_OPENER = re.compile(r"<<-?[ \t]*(['\"]?)(\w{1,64})(?!\w)\1")
_SPECIAL = re.compile(r"[\"'<\n#]")
_DQ_RUN = re.compile(r"(?:[^\"\\]+|\\[\s\S])*")      # inside "..", backslash-newline is a continuation
_PAREN = re.compile(r"\(\(|[()]")
_ANSI_RUN = re.compile(r"(?:[^'\\]+|\\[\s\S])*")
_SIMPLE = re.compile(r"^[\w./~$@%+=:,-]*$")
_SPLIT = re.compile(r"&&|\|\||;|\||\n|\(|\)|`|(?<![>&])&(?![>&])")
_ASSIGN = re.compile(r"^\w+=\S*$")
_REDIR = re.compile(r"^[0-9]*&?[<>]{1,2}&?(\S*)$")
_LEAD = frozenset("{ } ! if then elif else while until do".split())
_WRAPPERS = frozenset("sudo time nohup command exec env nice xargs doas busybox timeout stdbuf ionice chroot".split())
_WRAP_ARGS = {   # options that take the NEXT token as their argument
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D"}, "nice": {"-n"}, "env": {"-u", "-C"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a"}, "doas": {"-u", "-C"}, "timeout": {"-s", "-k"},
    "stdbuf": {"-i", "-o", "-e"}, "ionice": {"-c", "-n", "-p", "-t"},
}
_WRAP_POSITIONAL = {"timeout": 1, "chroot": 1}   # leading positional arguments (duration, new root)


def _mask(inner: str) -> str:
    return inner if _SIMPLE.match(inner) else "Q"


def _ansi(inner: str) -> str:
    """Decode the escapes of a `$'..'` word; anything undecodable counts as prose."""
    try:
        return inner.encode("latin-1", "backslashreplace").decode("unicode_escape")
    except ValueError:
        return " "


def _dq_end(s: str, i: int) -> tuple[int, int]:
    """(index of the closing quote, _) or (-1, index where the scan failed). Linear."""
    p = _DQ_RUN.match(s, i + 1).end()
    return (p, p) if p < len(s) and s[p] == '"' else (-1, p)


def _terminator(word: str) -> re.Pattern:
    sp = "[ \\t\\r\\f\\v]*"
    return re.compile(rf"(?m)^{sp}{re.escape(word)}{sp}$")


def _escaped(s: str, p: int) -> bool:
    """True if the character at p is escaped: an odd run of backslashes sits right before it."""
    j = p
    while j > 0 and s[j - 1] == "\\":
        j -= 1
    return (p - j) % 2 == 1


def _parens(run: str, paren: int, arith: list[int]) -> int:
    """Track unquoted parentheses. `arith` holds the paren level at which each open `((` / `$((` began;
    it is non-empty exactly while a `<<` would be an arithmetic shift. Returns the new level."""
    for m in _PAREN.finditer(run):
        if _escaped(run, m.start()):
            continue
        if m.group() == "((":
            arith.append(paren)
            paren += 2
        elif m.group() == "(":
            paren += 1
        elif paren > 0:
            paren -= 1
            if arith and paren == arith[-1]:
                arith.pop()
    return paren


def _scan(s: str) -> str:
    """Mask quoted strings (single tokens stay, prose becomes Q), drop comments and heredoc bodies.

    A heredoc opener is queued and its body is cut when the scan reaches the real end of the command
    line, the first newline that is not backslash-continued, quoted or inside a comment.
    """
    out: list[str] = []
    n, i, dead, ansi_dead, attempts, paren = len(s), 0, 0, 0, 0, 0
    arith: list[int] = []              # paren levels of the open arithmetic expressions
    pending: list[str] = []            # heredoc delimiters waiting for the end of their command line
    while i < n:
        m = _SPECIAL.search(s, i)
        if not m:
            out.append(s[i:])
            break
        p = m.start()
        run = s[i:p]
        out.append(run)
        c = s[p]
        i = p + 1
        paren = _parens(run, paren, arith)
        if c == "\n":
            if run.endswith("\\") and _escaped(s, p):   # backslash-newline: both vanish, the line goes on
                out[-1] = run[:-1]
            else:
                out.append("\n")
                while pending:             # the bodies start on the next line, in opener order
                    t = _terminator(pending.pop(0)).search(s, i)
                    if not t:              # unterminated: nothing is hidden
                        pending.clear()
                        break
                    i = t.end()
        elif c == "#":
            if p == 0 or (s[p - 1] in " \t\n;|&(" and not _escaped(s, p - 1)):   # word-initial: a comment
                e = s.find("\n", p)
                i = n if e < 0 else e
            else:
                out.append("#")
        elif c == '"':
            end = -1
            if p >= dead:                  # after a failed scan, no `"` before `dead` can match
                end, fail = _dq_end(s, p)
                if end < 0:
                    dead = fail
            if end >= 0:
                if run.endswith("$") and not _escaped(s, p - 1):      # $"..": the $ is part of the quoting
                    out[-1] = run[:-1]
                out.append(_mask(s[p + 1:end]))
                i = end + 1
            else:
                out.append('"')
        elif c == "'":
            end = -1
            if run.endswith("$") and not _escaped(s, p - 1) and p >= ansi_dead:    # $'..' with escapes
                q = _ANSI_RUN.match(s, p + 1).end()
                if q < n and s[q] == "'":
                    out[-1] = run[:-1]
                    out.append(_mask(_ansi(s[p + 1:q])))
                    i = q + 1
                    continue
                ansi_dead = q
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
            if o and not _escaped(s, p) and not arith:     # not `\<<`, not a shift inside `((`/`$((`
                pending.append(o.group(2))                  # the opener goes; the rest of the line stays
                i = o.end()
            else:
                out.append("<<")
                i = p + 2
        else:
            out.append(c)
    return "".join(out)


def _front(toks: list[str]) -> list[str]:
    """Drop what precedes the command word: assignments, keywords, redirections, wrappers + options."""
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        redir = _REDIR.match(t)
        if _ASSIGN.match(t) or t in _LEAD:
            i += 1
        elif redir:
            i += 1 if redir.group(1) else 2          # a bare `>` takes the next token as its target
        elif posixpath.basename(t.replace("\\", "")) in _WRAPPERS:
            name = posixpath.basename(t.replace("\\", ""))
            i += 1
            while i < n and toks[i].startswith("-"):
                i += 2 if toks[i] in _WRAP_ARGS.get(name, ()) else 1
            i += _WRAP_POSITIONAL.get(name, 0)
        else:
            break
    return toks[i:]


def command_segments(cmd: str | None) -> list[list[str]]:
    """Split a Bash command into token lists, ignoring comments, heredoc bodies and quoted prose."""
    return [toks for part in _SPLIT.split(_scan(cmd or "")) if (toks := _front(part.split()))]


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
