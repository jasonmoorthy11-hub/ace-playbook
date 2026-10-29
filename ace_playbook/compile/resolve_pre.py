"""The trust gate of resolve.py (docs/design/compiler.md 4.1.2; hashed with the catalogue, like resolve.py).

v2 may call a target scratch only when the resolution is PROVEN; anything else falls back to v1's literal rule, so v2 never
fires less than v1 except on proven scratch. A working directory, a variable binding or a worktree mark is trusted only when:
 (a) a plain segment set it: exactly `cd <literal>`, `pushd <literal>`, `NAME=<literal>`, `export NAME=<literal>` or
     `git [-C <literal>] worktree add [opts] <literal>`; no `!`, wrapper, `time`, `builtin`, assignment prefix or redirection (a `<`
     or `>` anywhere in a word: `cd /x>y`, `D=/x<f`); `local`, `readonly`, `declare` and `typeset` are not binding forms;
 (b) it sits in the current untainted `&&` chain (since the last `;`, newline or `&`; no `||` or `!` before it), not in a
     pipeline, and the command has no compound command or function (this module's `untrusted`);
 (c) the literal has no whitespace, `$`, backtick, glob, brace, backslash, `<>&|;#()`, and the command assigns IFS or CDPATH nowhere;
 (d) the command defines no function, `trap` or `alias`, runs no `shopt`/`setopt`/`unsetopt`, and holds no `eval` whose text is not
     plain `cd`/`rm`/assignments;
 (g) (resolve_chain.py) every segment between the set-point and the delete is a recognised shape or a literal allow-listed word.
`untrusted(events, quotes)` answers (b)'s compound part, (c)'s IFS part and (d) for a whole command: True means no resolution at
all (v1's literal reading of every token). The chain-local rules (a)-(c) and (g) are applied by resolve.py segment by segment; `leftover`
judges every expanded target and `child_env` the names a `bash -c` / `sh -c` child inherits (only the exported ones); `victims` lists
what a delete removes: an xargs-fed one always has its stdin as an unknown target unless the producer is `ls -d` / `find`, and a
redirection (`2>/dev/null`, `> f`) is never a target.
"""

from __future__ import annotations

import posixpath
import re
from functools import lru_cache

from . import shell

COMPOUND = frozenset("if then elif else fi while until do done for select case esac function { }".split())
_BAD = re.compile(r"[\s$`*?\[{\\<>&|;#()]")   # whitespace, $, backtick, glob, brace, backslash, redirection, separator, comment, paren
_GLOB = re.compile(r"[*?\[]")
_BRACE = re.compile(r"\{[^{}]*,[^{}]*\}")
_SET = re.compile(r"^(\w+)=(.*)$", re.S)
_EVALUABLE = frozenset(("cd", "rm", "pushd", "export"))
_REF = re.compile(r"\$(\w+)|\$\{(\w+)\}")
_EXEC = frozenset(("-exec", "-execdir", "-ok", "-okdir"))
_POISON = frozenset(("trap", "alias", "shopt", "setopt", "unsetopt"))       # a command word that makes the whole command unresolvable
_IFS = ("IFS", "CDPATH")
_IFS_SET = ("IFS=", "IFS+=", "CDPATH=", "CDPATH+=")
_GLUED = re.compile(r"^([^<>]*)([<>].*)$", re.S)                            # `2>/dev/null`, `a>b`: the part before the operator, the operator
_BARE = re.compile(r"<<<|<<-?|<>|>>|>\||&>>|&>|>&|<&|<|>")                  # a redirection operator whose operand is the NEXT word


@lru_cache(maxsize=8192)
def base(word: str) -> str:
    return posixpath.basename(word.replace("\\", ""))


@lru_cache(maxsize=8192)
def lead(tok: str) -> str | None:
    """The base name of `tok` when it starts a segment with nothing before the command word (no assignment, wrapper, keyword or
    redirection: shell.front leaves it in place); None otherwise."""
    probe = [tok, "x"]
    return base(tok) if shell.front(probe) == probe else None


def targets(toks: list[str]) -> list[str]:
    """`toks` without redirections: a glued operator and operand (`2>/dev/null`) vanish, a bare operator (`>`, `&>`) takes the next word
    with it, and the part of a word before a glued operator (`a>b`) stays; an fd number or `&` in front of an operator is no target."""
    out, skip = [], False
    for t in toks:
        if skip:
            skip = False
            continue
        m = _GLUED.match(t)
        if not m:
            out.append(t)
            continue
        if m.group(1) and not m.group(1).isdigit() and m.group(1) != "&":
            out.append(m.group(1))
        skip = bool(_BARE.fullmatch(m.group(2)))
    return out


def literal_text(tok: str, quotes: list) -> str | None:
    """The word with its quotes opened and NOTHING expanded; None unless it is a plain literal (rule c)."""
    text = shell.HOLE.sub(lambda m: quotes[int(m.group(1))][1], tok)
    return None if not text or _BAD.search(text) else text


def plain(raw: list[str], front: list[str]) -> bool:
    """Rule (a): no prefix was stripped from the segment (assignment, wrapper, keyword, `!`, `builtin`) and nothing is redirected
    (a `<` or `>` anywhere in a token counts: `cd /x>y`, `D=/x<f` and `cd /x2>/dev/null` are one word each)."""
    pure = not front and all(_SET.match(t) for t in raw)
    return (pure or (bool(front) and len(raw) == len(front))) and not any("<" in t or ">" in t for t in raw)


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
    """True when the command holds a compound command, a function, `trap`, `alias`, `shopt`/`setopt`, an IFS or CDPATH assignment or an
    unreadable `eval`."""
    seen = set()                                 # tokens already checked for IFS / CDPATH
    for i, (sep, raw) in enumerate(events):
        if sep:
            if sep == "(" and i + 1 < len(events) and events[i + 1][0] == ")":      # f() { ...; }
                return True
            continue
        if raw[0] in COMPOUND:
            return True
        for t in raw:
            if t not in seen:
                seen.add(t)
                if t in _IFS or t.startswith(_IFS_SET):
                    return True
        word = lead(raw[0])
        if word is None:
            front = shell.front(raw)
            word = base(front[0]) if front else ""
        if word in _POISON:
            return True
        if word == "eval" and not _readable(shell.script(shell.front(raw), quotes), deep + 1):
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


def leftover(s: str) -> bool:
    """True when a resolved target still holds `$`, a brace expansion or a glob that may reach `.`/`..`: it proves nothing.

    A glob component is left when it is not the final one, when the final one starts with `.`, `[` or `?` (it may match `.` or
    `..`), or when it follows a `..`; a final `*`-led glob (`./*`, `./sub/*`) is the one proven shape. No fnmatch: it lets `*` match `.`."""
    if "$" in s or _BRACE.search(s):
        return True
    parts = s.split("/")
    last = len(parts) - 1
    return any(_GLOB.search(p) and (k < last or p[0] in ".[?" or (k > 0 and parts[k - 1] == "..")) for k, p in enumerate(parts))


def child_env(prefix: list[str], env: dict, exp: set, quotes: list) -> dict:
    """What a `bash -c` / `sh -c` child starts with: the exported names plus literal `NAME=v` / `env NAME=v` in front of it.

    Any other prefix (sudo, nice, command, time, `env -i`, ...) passes NO names; a prefix value that is not a literal drops its name."""
    if any(not (_SET.match(t) or base(t) == "env") for t in prefix):
        return {}
    out = {n: env[n] for n in exp if n in env}
    for m in filter(None, map(_SET.match, prefix)):
        if (val := literal_text(m.group(2), quotes)) is None:
            out.pop(m.group(1), None)
        else:
            out[m.group(1)] = val
    return out


def victims(raw: list[str], front: list[str], events: list, i: int) -> list[str]:
    """What a recursive force delete removes (redirections are never targets). Behind `xargs` the stdin items are targets too: the paths of
    a recognised `ls -d` / `find` producer, otherwise ONE unknown target (positional arguments or not); `[]` for anything that deletes nothing."""
    if front and base(front[0]) == "find":
        run = next((front[k + 1:] for k, t in enumerate(front) if t in _EXEC), None)
        return (targets(shell.roots(front[1:])) or ["."]) if "-delete" in front or (run is not None and rm_args(run) is not None) else []
    args = rm_args(front)
    if args is None:
        return []
    args = targets(args)
    if any(base(t) == "xargs" for t in raw[:len(raw) - len(front)]):
        args += targets(shell.fed(events, i)) or ["$UNKNOWN"]
    return args
