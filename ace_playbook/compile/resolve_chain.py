"""Clause (g) of the trust invariant, the walk state and the outer expansion of a `bash -c` script (docs/design/compiler.md 4.1.2;
hashed with the catalogue, like resolve.py and resolve_pre.py).

(g) between the segment that set a trusted cwd, binding or worktree mark and the delete, EVERY segment is a recognised plain shape
(`cd`, `pushd`, `NAME=`, `export NAME=`, `git [-C x] worktree add`, as in resolve_pre.py (a)) or a command whose command word is a
LITERAL name on ALLOW (commands that change neither the directory nor a variable). Any other word makes cwd, env, made and exp UNKNOWN from
that segment on: a non-literal word (`$X`, backtick, a quote hole), an unknown name (a function or alias of the user's shell snapshot, which
v2 never sees), a zsh precommand (`repeat`, `noglob`, `nocorrect`), `source`, `local`/`declare`/`readonly`/`typeset`, a bare path (zsh AUTO_CD)
or a path-qualified `./cd` (an external program: it never moves the shell). A word is exact: `cd` is the builtin only when written `cd`
(or `\\cd`); `/bin/echo` counts as `echo` (a system directory), any other path is an unknown word.
"""

from __future__ import annotations

import re

from . import resolve_pre as pre, shell

INERT = frozenset((":", "true", "echo", "ls", "mkdir", "touch", "cp", "mv", "cat", "test", "[", "sleep", "pwd"))
READS = frozenset("cd pushd popd export git rm find eval bash sh printf unset read let mapfile readarray getopts".split())   # resolve.py reads these
ALLOW = INERT | {"git", "printf", "rm", "find", "xargs"}                       # literal words that leave cwd and variables alone
KNOWN = ALLOW | READS - {"bash", "sh"}                                       # `bash`/`sh` are known only as `-c <script>`
SYSTEM = ("/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin", "/opt/homebrew/bin")
SLOW, KNOWN_FAST, UNKNOWN = 0, 1, 2
_LITERAL = re.compile(r"[\w.:\[+@%,/-]+")
_DQ = re.compile(r"\\([$`\"\\\n])|\$\{(\w+)\}|\$(\w+)|[$`]")


class _Kept:
    """quotes stand-in for `shell.script`: every hole keeps its \\x00N\\x00 marker, so the script word comes back unopened."""
    def __getitem__(self, n: int) -> tuple[str, str]:
        return "", f"\x00{n}\x00"


def word(tok: str) -> str:
    """The command name a first word spells, or "" (not a plain literal: `$X`, a quote hole, `~`, a path outside the system directories)."""
    w = tok.replace("\\", "")
    if not _LITERAL.fullmatch(w):
        return ""
    head, _, tail = w.rpartition("/")
    return w if "/" not in w else tail if head in SYSTEM and tail in ALLOW else ""


def is_known(name: str) -> bool:
    return name in KNOWN


def lead_kind(tok: str) -> int:
    """How `_run` treats a segment whose FIRST token is `tok`, when nothing precedes the command word (no assignment, wrapper, keyword or
    redirection): SLOW (it needs the full reading), KNOWN_FAST (an allow-listed word nothing reads) or UNKNOWN (clause g: forget)."""
    name = pre.lead(tok)
    if name is None or name in READS:
        return SLOW
    return KNOWN_FAST if is_known(word(tok)) else UNKNOWN


def expand(piece: str, env: dict, dq: bool) -> tuple[str, bool]:
    """One double-quoted (dq) or unquoted piece of a script word as the child receives it, and whether the outer shell fully expanded it."""
    ok = dq or "\\" not in piece

    def one(m: re.Match) -> str:
        nonlocal ok
        if m.group(1) is not None:
            return "" if m.group(1) == "\n" else m.group(1)
        name = m.group(2) or m.group(3)
        if name is not None and name in env:
            return env[name]
        ok = False
        return m.group(0)
    return _DQ.sub(one, piece), ok


def child_text(front: list[str], quotes: list, env: dict) -> tuple[str, bool]:
    """(script, proven) for `bash -c` / `sh -c`: the text the child runs once the OUTER shell expanded the script word. A single-quoted or
    `$'..'` piece reaches the child as written; in a double-quoted or unquoted piece `\\$`, `\\``, `\\"` and `\\\\` lose their backslash and
    `$NAME` / `${NAME}` take the value of a trusted outer binding. Any other `$` or backtick, an unbound name or an unquoted backslash is
    not provable: the script comes back as written (backtick pairs turned into `$( )`) with proven False; the caller rescans it with
    trust off, v1's literal rule, so the delete it holds fires unless its target is a literal scratch path."""
    parts, proven = [], True
    for i, piece in enumerate(shell.HOLE.split(shell.script(front, _Kept()))):
        if i % 2:
            kind, inner = quotes[int(piece)]
            text, ok = (inner, True) if kind != '"' else expand(inner, env, True)
        else:
            text, ok = expand(piece, env, False)
        parts.append(text)
        proven = proven and ok
    if proven:
        return "".join(parts), True
    ticks = iter(range(1 << 30))                 # unproven: a backtick pair becomes `$( )`, which the rescan reads as an unknown target
    return re.sub("`", lambda _m: "$(" if next(ticks) % 2 == 0 else ")", shell.script(front, quotes)), False


class State:
    # taint: a `||` or `!` earlier in this chain; trust: the command passed resolve_pre.untrusted; first: no segment yet in this chain;
    # cond: names bound after an `&&` (they may not have run, so they die with the chain); exp: the bound names that are exported;
    # shared: env/made/cond/exp are also held by a copy or a `(` scope, so the first write copies them (a `(` costs no copy)
    __slots__ = ("cwd", "env", "made", "taint", "trust", "first", "cond", "exp", "shared")

    def __init__(self, cwd=None, env=None, made=None, taint=False, trust=True, first=True, cond=(), exp=()):
        self.cwd, self.env, self.made, self.taint, self.trust, self.first = cwd, dict(env or {}), set(made or ()), taint, trust, first
        self.cond, self.exp, self.shared = set(cond), set(exp), False

    def copy(self) -> "State":
        c = State.__new__(State)
        self.shared = True
        for a in State.__slots__:
            setattr(c, a, getattr(self, a))
        return c

    def _own(self) -> None:
        if self.shared:
            self.env, self.made, self.cond, self.exp, self.shared = dict(self.env), set(self.made), set(self.cond), set(self.exp), False

    def known(self) -> bool:                      # anything left to forget?
        return bool(self.cwd is not None or self.env or self.made or self.cond or self.exp)

    def drop(self, *names: str) -> None:          # a name goes back to "unassigned" (and so stops being exported or conditional)
        if names:
            self._own()
        for n in names:
            self.env.pop(n, None)
            self.exp.discard(n)
            self.cond.discard(n)

    def bind(self, name: str, val: str, cond: bool) -> None:
        self._own()
        self.env[name] = val
        (self.cond.add if cond else self.cond.discard)(name)

    def export(self, name: str, on: bool) -> None:
        self._own()
        (self.exp.add if on else self.exp.discard)(name)

    def add_made(self, path: str) -> None:
        self._own()
        self.made.add(path)

    def add_cond(self, names) -> None:
        self._own()
        self.cond |= set(names)

    def wipe(self) -> None:                       # the directory and every binding are unknown
        self.cwd, self.env, self.exp, self.shared = None, {}, set(), False
        self.made, self.cond = set(), set()

    def forget(self, taint: bool = False) -> None:
        self.wipe()
        self.taint = taint

    def end_chain(self) -> None:                 # `;` newline `&`: the cd or the worktree add may not have worked; conditional names die
        self.cwd, self.taint, self.first = None, False, True
        if self.made:
            self.made = set()
        if self.cond:
            self.drop(*self.cond)                # (the star builds a tuple first: dropping edits the set it came from)
            self.cond = set()

    def push(self, opener: str) -> list:         # a `(` or backtick scope: [opener, cwd, env, cond, first, arithmetic, made, exp]
        self.shared = True
        return [opener, self.cwd, self.env, self.cond, self.first, False, self.made, self.exp]

    def pop(self, entry: list) -> bool:
        _, self.cwd, self.env, self.cond, self.first, arith, self.made, self.exp = entry
        self.shared = True
        return arith
