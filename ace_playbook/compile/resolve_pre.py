"""The trust gate of resolve.py (docs/design/compiler.md 4.1.2; hashed with the catalogue, like resolve.py).

v2 may call a target scratch only when the resolution is PROVEN; anything else falls back to v1's literal rule, so v2 never
fires less than v1 except on proven scratch. A working directory, a variable binding or a worktree mark is trusted only when:
 (a) a plain segment set it: exactly `cd <literal>`, `pushd <literal>`, `NAME=<literal>`, `export NAME=<literal>` or
     `git [-C <literal>] worktree add [opts] <literal>`; no `!`, wrapper, `time`, `builtin`, redirection or assignment prefix;
 (b) it sits in the current untainted `&&` chain (since the last `;`, newline or `&`; no `||` or `!` before it), not in a
     pipeline, and the command has no compound command or function (this module's `untrusted`);
 (c) the literal has no whitespace, `$`, backtick, glob, brace or backslash, and the command assigns IFS or CDPATH nowhere;
 (d) the command defines no function, `trap` or `alias`, and holds no `eval` whose text is not plain `cd`/`rm`/assignments.
`untrusted(events, quotes)` answers (b)'s compound part, (c)'s IFS part and (d) for a whole command: True means no resolution at
all (v1's literal reading of every token). The chain-local rules (a)-(c) are applied by resolve.py segment by segment.
"""

from __future__ import annotations

import posixpath
import re

from . import shell

COMPOUND = frozenset("if then elif else fi while until do done for select case esac function { }".split())
_BAD = re.compile(r"[\s$`*?\[{\\]")           # whitespace, $, backtick, glob, brace, backslash: not a literal
_REDIR = re.compile(r"[0-9]*&?[<>]")
_SET = re.compile(r"^(\w+)=(.*)$", re.S)
_EVALUABLE = frozenset(("cd", "rm", "pushd", "export"))
_REF = re.compile(r"\$(\w+)|\$\{(\w+)\}")
_EXEC = frozenset(("-exec", "-execdir", "-ok", "-okdir"))


def base(word: str) -> str:
    return posixpath.basename(word.replace("\\", ""))


def literal_text(tok: str, quotes: list) -> str | None:
    """The word with its quotes opened and NOTHING expanded; None unless it is a plain literal (rule c)."""
    text = shell.HOLE.sub(lambda m: quotes[int(m.group(1))][1], tok)
    return None if not text or _BAD.search(text) else text


def plain(raw: list[str], front: list[str]) -> bool:
    """Rule (a): no prefix was stripped from the segment (assignment, wrapper, keyword, `!`, `builtin`) and nothing is redirected."""
    pure = not front and all(_SET.match(t) for t in raw)
    return (pure or (bool(front) and len(raw) == len(front))) and not any(_REDIR.match(t) for t in raw)


def _readable(text: str | None, deep: int) -> bool:
    """An `eval` text the scanner can read in full: no `$`/backtick and only cd, rm, pushd, export or assignment segments."""
    if text is None or "$" in text or "`" in text or deep > 2:
        return False
    events, quotes = shell.command_stream(text)
    if untrusted(events, quotes, deep):
        return False
    for sep, raw in events:
        front = shell.front(raw) if raw else []
        if sep in ("(", ")", "`") or (raw and front and posixpath.basename(front[0]) not in _EVALUABLE):
            return False
    return True


def untrusted(events: list, quotes: list, deep: int = 0) -> bool:
    """True when the command holds a compound command, a function, `trap`, `alias`, an IFS or CDPATH assignment or an unreadable `eval`."""
    for i, (sep, raw) in enumerate(events):
        if sep:
            if sep == "(" and i + 1 < len(events) and events[i + 1][0] == ")":      # f() { ...; }
                return True
            continue
        front = shell.front(raw)
        word = posixpath.basename(front[0].replace("\\", "")) if front else ""
        if raw[0] in COMPOUND or word in ("trap", "alias"):
            return True
        if any(t in ("IFS", "CDPATH") or t.startswith(("IFS=", "IFS+=", "CDPATH=", "CDPATH+=")) for t in raw):
            return True
        if word == "eval" and not _readable(shell.script(front, quotes), deep + 1):
            return True
    return False


def rm_args(toks: list[str]) -> list[str] | None:
    """Arguments of a recursive AND forced `rm` (possibly none: `xargs rm -rf`); None for anything else. After `--` everything is a target."""
    if not toks or base(toks[0]) != "rm":
        return None
    fl = [t for t in toks[1:] if t.startswith("-")]
    rec = any(f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f)) for f in fl)
    frc = any(f == "--force" or (not f.startswith("--") and "f" in f) for f in fl)
    cut = toks.index("--") if "--" in toks[1:] else len(toks)
    return [t for k, t in enumerate(toks) if k > cut or (0 < k < cut and not t.startswith("-"))] if rec and frc else None


def text(tok: str, env: dict, quotes: list) -> str | None:
    """The word with its variables and quoted holes filled; None if a quoted part holds a command substitution."""
    out = []
    expand = lambda s: _REF.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), s)   # noqa: E731
    for i, piece in enumerate(shell.HOLE.split(tok)):
        if i % 2 == 0:
            out.append(expand(piece))
            continue
        kind, inner = quotes[int(piece)]
        if "$(" in inner or "`" in inner or "\\" in inner:     # a substitution, or a backslash whose meaning depends on the quoting
            return None
        out.append(inner if kind == "'" else expand(inner))
    return "".join(out)


def victims(raw: list[str], front: list[str], events: list, i: int) -> list[str]:
    if front and base(front[0]) == "find":
        run = next((front[k + 1:] for k, t in enumerate(front) if t in _EXEC), None)
        return shell.roots(front[1:]) if "-delete" in front or (run is not None and rm_args(run) is not None) else []
    args = rm_args(front)
    if args is None:
        return []
    xargs = any(base(t) == "xargs" for t in raw[:len(raw) - len(front)])
    return shell.fed(events, i) if xargs and not args else args
