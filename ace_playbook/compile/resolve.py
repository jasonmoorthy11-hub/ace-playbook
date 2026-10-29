"""Target resolution for `rm_rf_nonscratch` v2 (docs/design/compiler.md 4.1.2; hashed with the catalogue).

Week 1 read 0 TP / 36 FP: every fire was a subagent cleaning its own scratch (`cd <scratch> && rm -rf ./*`, a shell variable, a
worktree it had just made). v1 judged the literal token; v2 resolves what the command deletes. Pure functions over ONE command
(`shell.command_stream`); nothing is executed, expanded by a shell or read from the environment.
Fails closed: whatever it cannot prove is scratch is not scratch. cwd: `cd`/`pushd` carry forward ONLY across `&&` (the delete then
runs only if the cd worked); after `||`, `;`, a newline or `&` the cwd is unknown and a relative target fires; a bare `cd` is
$HOME (never scratch); `cd -` and `popd` leave it unknown; `( )` and backticks scope the cwd and the variables.
Variables: earlier `NAME=value` and `export NAME=value` fill `$NAME` / `${NAME}` (not inside single quotes); an unassigned name,
`$HOME` and `$PWD` stay unresolved and are NOT scratch; `$TMPDIR` keeps v1's literal-prefix rule; a binding is dropped when its value
stays unresolved (`$`, backticks, `$(`) or when the name is rebound (shell.rebinds), `source` or `.` runs; a command substitution is `Q`.
Scratch is the caller's SCRATCH pattern (it covers /private/tmp/claude-501/, the agent scratch tree) plus a path made by
`git [-C repo] worktree add` in the same `&&` chain (never `~`, `..`, `$`, `/`, `.`, the repo or an ancestor, or fewer than two
components; no memory across tool calls). `bash -c` and `sh -c` (own copy of the state) and `eval` (shared state) are re-scanned
one level deep; `ls -d <paths>` / `find ... | xargs rm -rf`, `find -delete` and `find -exec rm -rf` delete the producer's paths or the
find roots; any other `ls` producer is unknown (fires). Interpreters, scripts, `$IFS`, `$(echo rm)` and other producers stay misses.
"""

from __future__ import annotations

import posixpath
import re

from . import shell

_REF = re.compile(r"\$(\w+)|\$\{(\w+)\}")
_SET = re.compile(r"^(\w+)=(.*)$", re.S)
_KEYWORDS = frozenset(("export", "declare", "typeset", "local", "readonly"))
_BARRIER = frozenset(("|", "&"))                  # a cd beside these runs in a subshell or the background: the cwd stays
_EXEC, _WT_VALUE = frozenset(("-exec", "-execdir", "-ok", "-okdir")), ("-b", "-B", "--reason")
HOME = "$HOME"                                    # a bare `cd`: unknown root, never scratch
MAX_ENV, MAX_VALUE = 64, 4096


class _State:
    __slots__ = ("cwd", "env", "made", "taint")      # taint: a `||` earlier in this chain may have skipped anything before it

    def __init__(self, cwd=None, env=None, made=None, taint=False):
        self.cwd, self.env, self.made, self.taint = cwd, dict(env or {}), set(made or ()), taint

    def copy(self) -> "_State":
        return _State(self.cwd, self.env, self.made, self.taint)


def _base(word: str) -> str:
    return posixpath.basename(word.replace("\\", ""))


def rm_args(toks: list[str]) -> list[str] | None:
    """Arguments of a recursive AND forced `rm` (possibly none: `xargs rm -rf`); None for anything else."""
    if not toks or _base(toks[0]) != "rm":
        return None
    fl = [t for t in toks[1:] if t.startswith("-")]
    rec = any(f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f)) for f in fl)
    frc = any(f == "--force" or (not f.startswith("--") and "f" in f) for f in fl)
    return [t for t in toks[1:] if not t.startswith("-")] if rec and frc else None


def _expand(s: str, env: dict) -> str:
    return _REF.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), s)


def _text(tok: str, st: _State, quotes: list) -> str | None:
    """The word with its variables and quoted holes filled; None if a quoted part holds a command substitution."""
    out = []
    for i, piece in enumerate(shell.HOLE.split(tok)):
        if i % 2 == 0:
            out.append(_expand(piece, st.env))
            continue
        kind, inner = quotes[int(piece)]
        if "$(" in inner or "`" in inner:
            return None
        out.append(inner if kind == "'" else _expand(inner, st.env))
    return "".join(out)


def _path(tok: str, st: _State, quotes: list) -> str:
    s = _text(tok, st, quotes)
    if s is None:
        return "Q"
    return posixpath.normpath(s if st.cwd is None or s.startswith(("/", "$", "~")) else posixpath.join(st.cwd, s))


def _assign(toks: list[str], st: _State, quotes: list) -> None:
    for t in (() if st.taint else toks):
        m = _SET.match(t)
        val = _text(m.group(2), st, quotes) if m else None
        if m and (val is None or "$" in val or "`" in val or len(val) > MAX_VALUE or (m.group(1) not in st.env and len(st.env) >= MAX_ENV)):
            st.env.pop(m.group(1), None)         # unresolvable: the name goes back to "unassigned"
        elif m:
            st.env[m.group(1)] = val


def _cd(args: list[str], st: _State, quotes: list) -> None:
    args = [a for a in args if a == "-" or not a.startswith("-")]
    st.cwd = None if st.taint or (args and args[0] == "-") else HOME if not args else _path(args[0], st, quotes)


def _worktree(front: list[str], st: _State, quotes: list) -> None:
    """`git [-C repo] worktree add [opts] <path> ...`: <path> is scratch for the rest of this command."""
    sub, args = shell.git_subcommand(front)
    if sub != "worktree" or args[:1] != ["add"] or st.taint:
        return
    k, repo = len(front) - len(args) - 1, st.copy()           # front[k] is "worktree"
    if "-C" in front[:k] and front.index("-C") + 1 < k:
        repo.cwd = _path(front[front.index("-C") + 1], st, quotes)
    rest, j = args[1:], 0
    while j < len(rest) and rest[j].startswith("-"):
        j += 2 if rest[j] in _WT_VALUE else 1
    if j >= len(rest):
        return
    text, p = _text(rest[j], repo, quotes) or "$", _path(rest[j], repo, quotes)
    wide = repo.cwd is not None and (p == repo.cwd or repo.cwd.startswith(p.rstrip("/") + "/"))   # the repo itself or an ancestor
    if not (text.startswith(("~", "$")) or ".." in text.split("/") or wide or p in ("/", ".", "Q") or len(p.strip("/").split("/")) < 2):
        st.made.add(p)


def _victims(raw: list[str], front: list[str], events: list, i: int) -> list[str]:
    if front and _base(front[0]) == "find":
        run = next((front[k + 1:] for k, t in enumerate(front) if t in _EXEC), None)
        return shell.roots(front[1:]) if "-delete" in front or (run is not None and rm_args(run) is not None) else []
    args = rm_args(front)
    if args is None:
        return []
    xargs = any(_base(t) == "xargs" for t in raw[:len(raw) - len(front)])
    return shell.fed(events, i) if xargs and not args else args


def _run(events: list, quotes: list, st: _State, depth: int, scratch, out: list) -> None:
    stack = []                                   # (opener, cwd, env) of the open `(` / backtick scopes
    for i, (sep, raw) in enumerate(events):
        if sep:
            if sep in ("(", "`") and not (stack and stack[-1][0] == sep == "`"):
                prev = events[i - 1] if i else ("x", [])
                if not prev[0] and prev[1] and not shell.front(prev[1]) and (m := _SET.match(prev[1][-1])):
                    st.env.pop(m.group(1), None)           # `NAME=$(...)` / NAME=`...`: the value is unknowable
                stack.append((sep, st.cwd, dict(st.env)))
            elif stack and stack[-1][0] == {")": "(", "`": "`"}.get(sep):
                _, st.cwd, st.env = stack.pop()
            elif sep == "||":                    # what ran before may have been skipped, and so may what follows: trust nothing here
                st.cwd, st.made, st.env, st.taint = None, set(), {}, True
            elif sep not in ("&&", "|"):         # `;` newline `&`: the cd (or the worktree add) may not have worked; a new chain
                st.cwd, st.made, st.taint = None, set(), False
            continue
        front, wrapped = shell.words(raw)
        if not front:
            _assign(raw, st, quotes)             # a bare `NAME=value` segment (the tokenizer's front drops it)
            continue
        name = _base(front[0])
        if wrapped and name in shell.BUILTINS:   # `env cd X`, `sudo export D=x`: an external program, the shell state is untouched
            st.cwd, st.env = None, {}
            continue
        piped = any(events[j][0] in _BARRIER for j in (i - 1, i + 1) if 0 <= j < len(events))
        if name in ("cd", "pushd", "popd"):
            if not piped:
                _cd(front[1:] if name != "popd" else ["-"], st, quotes)
        elif name in _KEYWORDS:
            _assign(front[1:], st, quotes)
        elif name == "git":
            _worktree(front, st, quotes)
        elif name in ("source", "."):
            st.env.clear()                       # a sourced file may rebind anything and change the directory
            st.cwd = None
        elif (script := shell.script(front, quotes)) is not None:
            if depth == 0 and script.strip():
                sub, subq = shell.command_stream(script)
                inner, was = (st, st.taint) if name == "eval" else (st.copy(), False)
                inner.taint = False                  # the script is a chain of its own; an `eval` hands its state back
                _run(sub, subq, inner, 1, scratch, out)
                if name == "eval":
                    st.taint = was
                    if was:                              # the eval itself may have been skipped: nothing it set counts
                        st.cwd, st.env, st.made = None, {}, set()
        elif targets := _victims(raw, front, events, i):
            paths = [_path(t, st, quotes) for t in targets]
            out.append([(p, bool(scratch.search(p)) or any(p == w or p.startswith(w + "/") for w in st.made)) for p in paths])
        gone = shell.rebinds(raw, front)
        if gone is None:                         # a binding builtin in a shape rebinds cannot read: forget everything
            st.cwd, st.env = None, {}
        for n in gone or ():
            st.env.pop(n, None)


def canonical_targets(parsed, scratch) -> list[list[tuple[str, bool]]]:
    """One list per recursive-force delete in the command: [(resolved target, is_scratch), ...].

    `parsed` is `shell.command_stream(cmd)`; `scratch` is the compiled SCRATCH pattern of detectors.py."""
    out: list = []
    _run(parsed[0], parsed[1], _State(), 0, scratch, out)
    return out
