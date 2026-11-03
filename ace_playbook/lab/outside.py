"""The outside-run-dir test of one tool_use input (score.py's `outside_paths`): `resolve`, `leaves`, and `inputs`, the count of one input dict."""

from __future__ import annotations

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
GLOB_CHARS = frozenset("*?[")


def _split(body: str) -> list[str]:
    """The top-level comma-separated alternatives of a brace body (a body with no comma is one alternative). ValueError as soon as there are more
    than MAX_ALTERNATIVES of them, so the cost is bounded by the first 64 commas, not by the body."""
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
    MAX_GROUPS groups, or more than MAX_ALTERNATIVES alternatives (counted BEFORE the new strings are built): the caller counts such a pattern as
    outside (fail closed)."""
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


def _globbed_dotdot(val: str) -> bool:
    """A `..` component after a component with a glob character (`**` can match no directory, so `x/**/../../*` can mean `../*`)."""
    globbed = False
    for part in val.split("/"):
        if part == ".." and globbed:
            return True
        globbed = globbed or bool(GLOB_CHARS & set(part))
    return False


def _one_leaves(cwd, val: str) -> int:
    """One brace-free alternative: a `..` after a glob component is outside; an absolute or `~` one is tested as a path; a relative one with a `..`
    component is resolved against the cwd; any other relative one stays inside."""
    if _globbed_dotdot(val):
        return 1
    if val.startswith(("/", "~")) or ".." in val.split("/"):
        return leaves(cwd, val)
    return 0


def _pattern_leaves(cwd, val: str) -> int:
    """A Glob `pattern` / Grep `glob`, already joined to the tool's `path`: a backslash is outside (an escape can build a `..`: `.\\./*`), its
    braces are expanded (a brace can build a `..` without a literal one; an unbalanced, too deep or too wide group counts as outside), then every
    alternative is tested by `_one_leaves`."""
    if "\\" in val:
        return 1
    try:
        alts = expand(val)
    except ValueError:
        return 1
    return int(any(_one_leaves(cwd, alt) for alt in alts))


def inputs(cwd, name, inp: dict) -> int:
    """The outside count of one tool_use input: the path keys; a Glob `pattern` or a Grep `glob` (a Grep `pattern` is a content regex and is left
    alone), joined to the tool's `path` when it has one and tested as one pattern; and a `path` with no pattern beside it, which also counts when
    it has a brace, a backslash, or a `..` after a glob component."""
    n = sum(leaves(cwd, inp[k]) for k in PATH_KEYS if isinstance(inp.get(k), str) and inp[k])
    base = inp.get("path") if isinstance(inp.get("path"), str) and inp.get("path") else None
    pats = [v for v in (inp.get(k) for k in (("glob", "pattern") if name == "Glob" else ("glob",))) if isinstance(v, str) and v]
    for val in pats:
        n += _pattern_leaves(cwd, val if base is None or val.startswith(("/", "~")) else posixpath.join(base, val))
    if base is not None and not pats:
        n += int(bool(set("{\\") & set(base)) or _globbed_dotdot(base))
    return n
