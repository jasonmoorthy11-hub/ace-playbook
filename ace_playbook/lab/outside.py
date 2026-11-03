"""The outside-run-dir test of one tool_use input (score.py's `outside_paths`): `resolve`, `leaves`, and `inputs`, the count of one input dict."""

from __future__ import annotations

import fnmatch
import posixpath

PATH_KEYS = ("file_path", "path", "notebook_path")


def resolve(cwd: str, path: str) -> str:
    return posixpath.normpath(posixpath.join(cwd, path))


def leaves(cwd, path: str) -> int:
    """1 when the path input is outside `cwd` (None: unknown, so every absolute path is), else 0; a leading `~` is outside (expanded to a home dir).
    A relative path is resolved against `cwd` first (`../work/a.py` inside a cwd `/r/work` is inside); with no cwd a leading `..` is outside."""
    if path.startswith("~"):
        return 1
    if not posixpath.isabs(path):
        if cwd is None:
            return int(posixpath.normpath(path).split("/")[0] == "..")
        path = resolve(cwd, path)
    norm = posixpath.normpath(path)
    return int(cwd is None or not (norm == cwd or norm.startswith(cwd.rstrip("/") + "/")))


MAX_ALTERNATIVES = 64
MAX_DEPTH = 32
MAX_GROUPS = 64
MAX_CHARS = 4096
GLOB_CHARS = frozenset("*?[")


def _split(body: str) -> list[str]:
    """The top-level comma-separated alternatives of a brace body (a body with no comma is one alternative). ValueError as soon as there are more
    than MAX_ALTERNATIVES of them; one pass over the body, so the cost is linear in its length (callers refuse more than MAX_CHARS first)."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        depth += (ch == "{") - (ch == "}")
        if ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
            if len(parts) >= MAX_ALTERNATIVES:
                raise ValueError("too many alternatives")
    parts.append(body[start:])
    return parts


def expand(val: str) -> list[str]:
    """Every alternative of the `{a,b,...}` groups of a glob (nested allowed). ValueError on an unbalanced brace, more than MAX_DEPTH levels or
    MAX_GROUPS groups, or more than MAX_ALTERNATIVES alternatives (counted BEFORE the new strings are built); the cost is linear in the input
    (callers refuse more than MAX_CHARS first): the caller counts such a pattern as outside (fail closed)."""
    depth = deepest = 0
    for ch in val:
        depth += (ch == "{") - (ch == "}")
        deepest = max(deepest, depth)
        if depth < 0:
            raise ValueError("unbalanced brace")
    if depth:
        raise ValueError("unbalanced brace")
    if deepest > MAX_DEPTH or val.count("{") > MAX_GROUPS:
        raise ValueError("too deep or too many groups")
    todo, done = [val], []
    while todo:
        cur = todo.pop()
        start = cur.find("{")
        if start < 0:
            done.append(cur)
            continue
        d = 0
        for end in range(start, len(cur)):
            d += (cur[end] == "{") - (cur[end] == "}")
            if d == 0:
                break
        alts = _split(cur[start + 1:end])
        if len(todo) + len(done) + len(alts) > MAX_ALTERNATIVES:
            raise ValueError("too many alternatives")
        todo += [cur[:start] + alt + cur[end + 1:] for alt in alts]
    return done


def _class_end(val: str, i: int) -> int:
    """The index of the `]` closing the class that opens at `val[i]` (as `fnmatch` reads it), or -1: no class, the `[` is a literal."""
    j = i + 1 + (val[i + 1:i + 2] == "!")
    return val.find("]", j + (val[j:j + 1] == "]"))


def _components(val: str) -> list[tuple[str, bool]]:
    """`val` split on every `/` that is not inside a `[...]` class (as `fnmatch` reads one: an optional `!`, then a `]` first is literal), each
    with whether a class of it may hold a `/` (a literal one or a range over it; a negated class is not counted)."""
    parts, cur, slash, i = [], [], False, 0
    while i < len(val):
        ch = val[i]
        end = _class_end(val, i) if ch == "[" else -1
        if end > 0:
            slash = slash or (val[i + 1] != "!" and _holds_slash(val[i + 1:end]))
            cur.append(val[i:end + 1])
            i = end + 1
            continue
        if ch == "/":
            parts.append(("".join(cur), slash))
            cur, slash = [], False
        else:
            cur.append(ch)
        i += 1
    parts.append(("".join(cur), slash))
    return parts


def _holds_slash(body: str) -> bool:
    """Whether a class body (no leading `!`) lists a `/` or a range over it."""
    i = 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            if ord(body[i]) <= 47 <= ord(body[i + 2]):
                return True
            i += 3
        else:
            if body[i] == "/":
                return True
            i += 1
    return False


def _worst_case(val: str) -> str:
    """`val` with every component that may match `..` written as `..`: a component other than bare `*` / `**` for which Python's `fnmatch` says
    `..` matches (`[.][.]`, `[!a][!a]`, `??`, `.?`, `.*`, `[--/][.]`), or whose class may hold a `/` (it can build one). Other engines
    (minimatch, ripgrep, bash) read globs differently, so this is the conservative reading of every `*`, `?`, `[seq]`, `[!seq]` and range."""
    return "/".join(".." if sl or p not in ("*", "**") and fnmatch.fnmatchcase("..", p) else p for p, sl in _components(val))


def _globbed_dotdot(val: str) -> bool:
    """A `..` component after a component with a glob character (`**` can match no directory, so `x/**/../../*` can mean `../*`)."""
    globbed = False
    for part in val.split("/"):
        if part == ".." and globbed:
            return True
        globbed = globbed or bool(GLOB_CHARS & set(part))
    return False


def _one_leaves(cwd, val: str) -> int:
    """One brace-free alternative, read worst case (`_worst_case`): a `..` after a glob component is outside; an absolute or `~` one is tested as
    a path; a relative one with a `..` component is resolved against the cwd; any other relative one stays inside."""
    val = _worst_case(val)
    if _globbed_dotdot(val):
        return 1
    if val.startswith(("/", "~")) or ".." in val.split("/"):
        return leaves(cwd, val)
    return 0


def _pattern_leaves(cwd, val: str) -> int:
    """A Glob `pattern` / Grep `glob`, already joined to the tool's `path`: longer than MAX_CHARS or holding a backslash is outside (an escape can
    build a `..`: `.\\./*`), its braces are expanded (a brace can build a `..` without a literal one; an unbalanced, too deep or too wide group
    counts as outside), then every alternative is tested by `_one_leaves`."""
    if len(val) > MAX_CHARS or "\\" in val:
        return 1
    try:
        alts = expand(val)
    except ValueError:
        return 1
    return int(any(_one_leaves(cwd, alt) for alt in alts))


def _base_leaves(cwd, base: str) -> int:
    """A `path` with no pattern beside it: outside when it is over MAX_CHARS, has a brace or a backslash, has a `..` after a glob component, or
    has a component that may match `..` and then resolves outside (`leaves` already tests a literal `..`)."""
    if len(base) > MAX_CHARS or set("{\\") & set(base) or _globbed_dotdot(base):
        return 1
    worst = _worst_case(base)
    return int(worst != base and bool(leaves(cwd, worst)))


def inputs(cwd, name, inp: dict) -> int:
    """The outside count of one tool_use input: a non-str value (not None) under `pattern`, `glob`, `path`, `file_path` or `notebook_path` (1
    each); the path keys; a Glob `pattern` or a Grep `glob` (a Grep `pattern` is a content regex and is left alone), joined to the tool's `path`
    when it has one (not when the pattern is absolute or starts with `~`) and tested as one pattern; and a `path` with no pattern beside it
    (`_base_leaves`)."""
    n = sum(1 for k in ("pattern", "glob") + PATH_KEYS if inp.get(k) is not None and not isinstance(inp[k], str))
    n += sum(leaves(cwd, inp[k]) for k in PATH_KEYS if isinstance(inp.get(k), str) and inp[k])
    base = inp.get("path") if isinstance(inp.get("path"), str) and inp.get("path") else None
    pats = [v for v in (inp.get(k) for k in (("glob", "pattern") if name == "Glob" else ("glob",))) if isinstance(v, str) and v]
    for val in pats:
        n += _pattern_leaves(cwd, val if base is None or val.startswith(("/", "~")) else posixpath.join(base, val))
    if base is not None and not pats:
        n += _base_leaves(cwd, base)
    return n
