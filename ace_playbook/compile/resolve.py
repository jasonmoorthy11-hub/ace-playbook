"""Target resolution for `rm_rf_nonscratch` v2 (docs/design/compiler.md 4.1.2; hashed with the catalogue).

Week 1 read 0 TP / 36 FP: every fire was a subagent cleaning its own scratch (`cd <scratch> && rm -rf ./*`, a shell variable, a
worktree it had just made). v1 judged the literal token; v2 resolves what the command deletes, but ONLY from proven state: the rules
of resolve_pre.py (a plain `cd`/`NAME=`/`export`/`git worktree add` segment with a literal value, in the current untainted `&&` chain,
not piped, in a command without compound commands, functions, `trap`, `alias`, IFS or an unreadable `eval`). Everything else falls
back to v1's literal rule for that token, so v2 fires wherever v1 fired except on proven scratch. Pure functions over ONE command
(`shell.command_stream`); nothing is executed, expanded by a shell or read from the environment.
cwd lives in one `&&` chain only (`;`, newline, `&` end it; `||` and `!` taint the rest of the chain); a bare `cd` is $HOME (never
scratch); `cd -`, `popd`, `pushd +N`, `((..))`, `source`, `read`, `printf -v`, `let`, `unset`, `mapfile`, `getopts`, `declare -n` make it
and the bindings unknown. A binding set after an `&&` (not first in its chain) dies with the chain. A resolved target that still holds
`$`, `{a,b}` or a glob next to `..` is judged as v1 judged the token (every target, not only those with a `$`). A worktree added in the
same `&&` chain is scratch (never `~`, `..`, `/`, `.`, the repo or an ancestor, fewer than two components). `bash -c`, `sh -c` (a copy:
the cwd and worktree marks, but only the EXPORTED names and literal `NAME=v` / `env NAME=v` prefix assignments; any other prefix passes
none) and `eval` (shared state; a wrapped one runs on a copy and leaves the outer state unknown) are re-scanned one level deep;
`ls -d <paths>` / `find` `| xargs rm -rf`, `find -delete` and `find -exec rm -rf` delete the producer's paths or the roots; any other
`ls` producer is unknown (fires).
"""

from __future__ import annotations

import posixpath
import re

from . import resolve_pre as pre, shell

_SET = re.compile(r"^(\w+)=(.*)$", re.S)
_TMPDIR = re.compile(r"^\$\{?TMPDIR\}?")
_KEYWORDS = frozenset(("export", "declare", "typeset", "local", "readonly"))
_WT_VALUE = ("-b", "-B", "--reason")
HOME = "$HOME"                                    # a bare `cd`: unknown root, never scratch
MAX_ENV, MAX_VALUE = 64, 4096
rm_args = pre.rm_args


class _State:
    # taint: a `||` or `!` earlier in this chain; trust: the command passed resolve_pre.untrusted; first: no segment yet in this chain;
    # cond: names bound after an `&&` (they may not have run, so they die with the chain); exp: the bound names that are exported
    __slots__ = ("cwd", "env", "made", "taint", "trust", "first", "cond", "exp")

    def __init__(self, cwd=None, env=None, made=None, taint=False, trust=True, first=True, cond=(), exp=()):
        self.cwd, self.env, self.made, self.taint, self.trust, self.first = cwd, dict(env or {}), set(made or ()), taint, trust, first
        self.cond, self.exp = set(cond), set(exp)

    def copy(self) -> "_State":
        return _State(self.cwd, self.env, self.made, self.taint, self.trust, self.first, self.cond, self.exp)

    def drop(self, *names: str) -> None:          # a name goes back to "unassigned" (and so stops being exported)
        for n in names:
            self.env.pop(n, None)
            self.exp.discard(n)

    def wipe(self) -> None:                       # the directory and every binding are unknown
        self.cwd, self.env, self.exp = None, {}, set()

    def forget(self, taint: bool = False) -> None:
        self.wipe()
        self.made, self.cond, self.taint = set(), set(), taint

    def end_chain(self) -> None:                 # `;` newline `&`: the cd or the worktree add may not have worked; conditional names die
        self.cwd, self.made, self.taint, self.first = None, set(), False, True
        self.drop(*self.cond)
        self.cond.clear()


def _path(tok: str, st: _State, quotes: list) -> str:
    """The target: v1's reading of the token unless the state proves more (see the module docstring)."""
    v1 = posixpath.normpath(shell.HOLE.sub("Q", tok))
    s = pre.text(tok, st.env, quotes) if st.trust else v1
    if s is None:
        return "Q"
    if st.trust and pre.leftover(_TMPDIR.sub("", s)):   # a `$`, a brace expansion or a glob next to `..` is left: no better than v1
        return v1
    return posixpath.normpath(s if st.cwd is None or s.startswith(("/", "$", "~")) else posixpath.join(st.cwd, s))


def _abs(tok: str, st: _State, quotes: list) -> str | None:
    """A literal, determinable path (absolute, `~`, or relative to a TRUSTED cwd); None otherwise."""
    lit = pre.literal_text(tok, quotes)
    if lit is None or lit.startswith(("-", "+")) or not (lit.startswith(("/", "~")) or st.cwd is not None):
        return None
    return posixpath.normpath(lit if lit.startswith(("/", "~")) else posixpath.join(st.cwd, lit))


def _assign(toks: list[str], st: _State, quotes: list, ok: bool, first: bool, export: bool = False) -> None:
    for t in toks:
        m = _SET.match(t)
        if not m:
            if export and t in st.env:           # `export NAME` marks a bound name; `export -n NAME` un-marks it
                (st.exp.add if ok and "-n" not in toks else st.exp.discard)(t)
            continue
        val = pre.literal_text(m.group(2), quotes) if ok else None
        if val is None or len(val) > MAX_VALUE or (m.group(1) not in st.env and len(st.env) >= MAX_ENV):
            st.drop(m.group(1))                  # not a proven literal: the name goes back to "unassigned"
            st.cond.discard(m.group(1))
        else:
            st.env[m.group(1)] = val
            (st.cond.discard if first else st.cond.add)(m.group(1))
            if export and "-n" not in toks:
                st.exp.add(m.group(1))


def _cd(args: list[str], st: _State, quotes: list, ok: bool) -> None:
    args = [a for a in args if a not in ("-L", "-P", "-e")]
    st.cwd = HOME if ok and not args else _abs(args[0], st, quotes) if ok and len(args) == 1 else None


def _worktree(front: list[str], st: _State, quotes: list, ok: bool) -> None:
    """`git [-C repo] worktree add [opts] <path> ...`: <path> is scratch for the rest of this `&&` chain."""
    sub, args = shell.git_subcommand(front)
    if sub != "worktree" or args[:1] != ["add"] or not ok:
        return
    k, repo = len(front) - len(args) - 1, st.copy()           # front[k] is "worktree"
    if "-C" in front[:k] and front.index("-C") + 1 < k:
        repo.cwd = _abs(front[front.index("-C") + 1], st, quotes)
    rest, j = args[1:], 0
    while j < len(rest) and rest[j].startswith("-"):
        j += 2 if rest[j] in _WT_VALUE else 1
    p = _abs(rest[j], repo, quotes) if j < len(rest) else None
    wide = p is not None and repo.cwd is not None and (p == repo.cwd or repo.cwd.startswith(p.rstrip("/") + "/"))   # the repo or an ancestor
    if p is not None and not (wide or p.startswith("~") or ".." in rest[j].split("/") or p in ("/", ".") or len(p.strip("/").split("/")) < 2):
        st.made.add(p)


def _run(events: list, quotes: list, st: _State, depth: int, scratch, out: list) -> None:
    stack = []                                   # [opener, cwd, env, cond, first, arithmetic, made, exp] of the open `(` / backtick scopes
    outer_trust = st.trust
    st.trust = outer_trust and not pre.untrusted(events, quotes)
    for i, (sep, raw) in enumerate(events):
        if sep:
            if sep in ("(", "`") and not (stack and stack[-1][0] == sep == "`"):
                if stack and sep == "(" and i and events[i - 1][0] == "(":     # `((..))` / `$((..))`: arithmetic may assign anything
                    stack[-1][5] = True
                prev = events[i - 1] if i else ("x", [])
                if not prev[0] and prev[1] and not shell.front(prev[1]) and (m := _SET.match(prev[1][-1])):
                    st.drop(m.group(1))                    # `NAME=$(...)` / NAME=`...`: the value is unknowable
                stack.append([sep, st.cwd, dict(st.env), set(st.cond), st.first, False, set(st.made), set(st.exp)])
                st.first = True
            elif stack and stack[-1][0] == {")": "(", "`": "`"}.get(sep):
                _, st.cwd, st.env, st.cond, st.first, arith, st.made, st.exp = stack.pop()
                if arith:
                    st.forget()
            elif sep == "||":                    # what ran before may have been skipped, and so may what follows: trust nothing here
                st.forget(taint=True)
            elif sep not in ("&&", "|"):
                st.end_chain()
            continue
        front, wrapped = shell.words(raw)
        first, st.first = st.first, False
        if raw[0] == "!":                        # negated: the rest of the chain runs only if this failed
            st.forget(taint=True)
        piped = (i > 0 and events[i - 1][0] == "|") or (i + 1 < len(events) and events[i + 1][0] in ("|", "&"))   # a pipeline member or backgrounded
        ok = st.trust and not st.taint and not piped and pre.plain(raw, front)
        if not front:
            _assign(raw, st, quotes, ok, first)  # a bare `NAME=value` segment (the tokenizer's front drops it)
            continue
        name = pre.base(front[0])
        blind = wrapped and name in shell.BUILTINS   # `env cd X`, `sudo export D=x`, `time cd X`: the effect is not read, the state is unknown
        if blind:                                # (a wrapped builtin is still scanned below: `sudo eval "..."` and its targets count)
            st.wipe()
        if name in ("cd", "pushd", "popd") and not blind:
            _cd(front[1:] if name != "popd" else ["-"], st, quotes, ok)
        elif name in _KEYWORDS and not blind:
            _assign(front[1:], st, quotes, ok, first, name == "export")
        elif name == "git":
            _worktree(front, st, quotes, ok)
        elif name in ("source", "."):
            st.wipe()                            # a sourced file may rebind anything and change the directory
        elif (script := shell.script(front, quotes)) is not None:
            if depth == 0 and script.strip():
                sub, subq = shell.command_stream(script)
                shared = name == "eval" and not blind    # a bare `eval` runs in this shell; `bash -c` and a wrapped eval get a copy
                inner, was = (st, st.taint) if shared else (st.copy(), False)
                if name in ("bash", "sh"):               # a child shell inherits only exported names and literal prefix assignments
                    inner.env = pre.child_env(raw[:len(raw) - len(front)], st.env, st.exp, quotes)
                inner.taint, inner.first = False, True   # the script is a chain of its own; a shared state is handed back
                before = dict(st.env)
                _run(sub, subq, inner, 1, scratch, out)
                if shared:
                    st.taint = was
                    st.cond |= {n for n, v in st.env.items() if before.get(n) != v} if not first else set()   # a skipped eval binds nothing
                if was or blind:                 # the eval itself may have been skipped, or ran elsewhere: nothing it set counts
                    st.forget(st.taint)
        elif targets := pre.victims(raw, front, events, i):
            paths = [_path(t, st, quotes) for t in targets]
            out.append([(p, bool(scratch.search(p)) or any(p == w or p.startswith(w + "/") for w in st.made)) for p in paths])
        gone = shell.rebinds(raw, front)
        if gone is None:                         # a binding builtin in a shape rebinds cannot read: forget everything
            st.wipe()
        st.drop(*(gone or ()))
    st.trust = outer_trust


def canonical_targets(parsed, scratch) -> list[list[tuple[str, bool]]]:
    """One list per recursive-force delete in the command: [(resolved target, is_scratch), ...].

    `parsed` is `shell.command_stream(cmd)`; `scratch` is the compiled SCRATCH pattern of detectors.py."""
    out: list = []
    _run(parsed[0], parsed[1], _State(), 0, scratch, out)
    return out
