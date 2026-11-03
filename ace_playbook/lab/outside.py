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
GLOB_CHARS = frozenset("*?[")


def _split(body: str) -> list[str]:
    """The top-level comma-separated alternatives of a brace body (a body with no comma is one alternative)."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        depth += (ch == "{") - (ch == "}")
        if ch == "," and depth == 0:
            parts, start = parts + [body[start:i]], i + 1
    return parts + [body[start:]]


def expand(val: str) -> list[str]:
    """Every alternative of the `{a,b,...}` groups of a glob (nested allowed). ValueError on an unbalanced brace or more than MAX_ALTERNATIVES
    alternatives: the caller counts such a pattern as outside (fail closed)."""
    depth = 0
    for ch in val:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            raise ValueError("unbalanced brace")
    if depth:
        raise ValueError("unbalanced brace")
    todo, done = [val], []
    while todo:
        cur = todo.pop()
        start = cur.find("{")
        if start < 0:
            done.append(cur)
        else:
            d = 0
            for end in range(start, len(cur)):
                d += (cur[end] == "{") - (cur[end] == "}")
                if d == 0:
                    break
            todo += [cur[:start] + alt + cur[end + 1:] for alt in _split(cur[start + 1:end])]
        if len(todo) + len(done) > MAX_ALTERNATIVES:
            raise ValueError("too many alternatives")
    return done


def _one_leaves(cwd, inp: dict, val: str) -> int:
    """One brace-free alternative: an absolute or `~` one is tested as a path; a `..` component after a component with a glob character is outside
    (`**` can match no directory, so `x/**/../../*` can mean `../*`); a relative one with a `..` component is resolved against the tool's `path`
    input (else the cwd); any other relative one stays inside."""
    parts = val.split("/")
    if any(p == ".." and any(GLOB_CHARS & set(q) for q in parts[:i]) for i, p in enumerate(parts)):
        return 1
    if val.startswith(("/", "~")):
        return leaves(cwd, val)
    if ".." not in parts:
        return 0
    base = inp.get("path")
    return leaves(cwd, posixpath.join(base if isinstance(base, str) and base else ".", val))


def _pattern_leaves(cwd, inp: dict, val: str) -> int:
    """A Glob `pattern` / Grep `glob`: its braces are expanded first (a brace can build a `..` without a literal one; an unbalanced brace or more
    than MAX_ALTERNATIVES alternatives counts as outside), then every alternative is tested by `_one_leaves`."""
    try:
        alts = expand(val)
    except ValueError:
        return 1
    return int(any(_one_leaves(cwd, inp, alt) for alt in alts))


def inputs(cwd, name, inp: dict) -> int:
    """The outside count of one tool_use input: the path keys, and a Glob `pattern` or a Grep `glob` (a Grep `pattern` is a content regex and
    is left alone)."""
    n = sum(leaves(cwd, inp[k]) for k in PATH_KEYS if isinstance(inp.get(k), str) and inp[k])
    for key in ("glob", "pattern") if name == "Glob" else ("glob",):
        val = inp.get(key)
        n += isinstance(val, str) and bool(val) and _pattern_leaves(cwd, inp, val)
    return n
