"""The outside-run-dir test of one tool_use input (score.py's `outside_paths`): `resolve`, `leaves`, and `inputs`, the count of one input dict."""

from __future__ import annotations

import fnmatch
import posixpath
import re

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
GLOB_CHARS = frozenset("*?")
ALLOWED = re.compile(r"[A-Za-z0-9_./*?{},-]*")                                # ASCII only: the whitelist of a pattern, a glob and a path


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


def _brace_dotdot(val: str) -> bool:
    """A `..` inside a brace body: a sequence expression (`{Z..a}`, `{1..3}`) builds characters this module does not expand."""
    depth = 0
    for i, ch in enumerate(val):
        depth += (ch == "{") - (ch == "}")
        if depth > 0 and val.startswith("..", i):
            return True
    return False


def _worst_case(val: str) -> str:
    """`val` with every component that may match `..` written as `..`: one that starts with `.` and for which Python's `fnmatch` says `..`
    matches (`.*`, `.?`, `.*.`). A component that starts with `*` or `?` never matches `..` (bash 3.2 with or without dotglob, node glob and
    minimatch), so `*.*`, `??`, `?.` and `*.` stay as they are."""
    return "/".join(".." if p[:1] == "." and fnmatch.fnmatchcase("..", p) else p for p in val.split("/"))


def _globbed_dotdot(val: str) -> bool:
    """A `..` component after a component with a glob character (`**` can match no directory, so `x/**/../../*` can mean `../*`)."""
    globbed = False
    for part in val.split("/"):
        if part == ".." and globbed:
            return True
        globbed = globbed or bool(GLOB_CHARS & set(part))
    return False


def _one_leaves(cwd, val: str) -> int:
    """One brace-free alternative, read worst case (`_worst_case`): a `..` after a glob component is outside; an absolute one is tested as a
    path; a relative one with a `..` component is resolved against the cwd; any other relative one stays inside."""
    val = _worst_case(val)
    if _globbed_dotdot(val):
        return 1
    if val.startswith("/") or ".." in val.split("/"):
        return leaves(cwd, val)
    return 0


def _pattern_leaves(cwd, val: str) -> int:
    """A Glob `pattern` / Grep `glob`, already joined to the tool's `path` (a Grep glob's leading `!` is stripped first). A WHITELIST decides
    first: longer than MAX_CHARS, any character other than ASCII `A-Za-z0-9_./*?-{},` (so no `[`, `\\`, `^`, `:`, `=`, `~`, `!`, space, NUL or
    non-ASCII), or a `..` inside a brace body (`{Z..a}`) is outside. Then the braces are expanded (an unbalanced, too deep or too wide group is
    outside) and every alternative is tested by `_one_leaves`."""
    if len(val) > MAX_CHARS or not ALLOWED.fullmatch(val) or _brace_dotdot(val):
        return 1
    try:
        alts = expand(val)
    except ValueError:
        return 1
    return int(any(_one_leaves(cwd, alt) for alt in alts))


def _base_leaves(cwd, base: str) -> int:
    """A `path` with no pattern beside it: outside when it is over MAX_CHARS, has a character outside the whitelist or a brace, has a `..` after
    a glob component, or has a component that may match `..` and then resolves outside (`leaves` already tests a literal `..` and a leading `~`)."""
    if base.startswith("~"):
        return 0
    if len(base) > MAX_CHARS or not ALLOWED.fullmatch(base) or set("{}") & set(base) or _globbed_dotdot(base):
        return 1
    worst = _worst_case(base)
    return int(worst != base and bool(leaves(cwd, worst)))


def inputs(cwd, name, inp: dict) -> int:
    """The outside count of one tool_use input: a non-str value (not None) under `pattern`, `glob`, `path`, `file_path` or `notebook_path` (1
    each); the path keys; a Glob `pattern` or a Grep `glob` (a Grep `pattern` is a content regex and is left alone; a Grep `glob` may start with
    one `!`, ripgrep's negation, which only filters), joined to the tool's `path` when it has one (not when the pattern is absolute or starts
    with `~`) and tested as one pattern; and a `path` with no pattern beside it (`_base_leaves`)."""
    n = sum(1 for k in ("pattern", "glob") + PATH_KEYS if inp.get(k) is not None and not isinstance(inp[k], str))
    n += sum(leaves(cwd, inp[k]) for k in PATH_KEYS if isinstance(inp.get(k), str) and inp[k])
    base = inp.get("path") if isinstance(inp.get("path"), str) and inp.get("path") else None
    pats = []
    for key in ("glob", "pattern") if name == "Glob" else ("glob",):
        val = inp.get(key)
        if isinstance(val, str):
            pats.append(val[1:] if name == "Grep" and key == "glob" and val.startswith("!") else val)
    pats = [v for v in pats if v]
    for val in pats:
        n += _pattern_leaves(cwd, val if base is None or val.startswith(("/", "~")) else posixpath.join(base, val))
    if base is not None and not pats:
        n += _base_leaves(cwd, base)
    return n
