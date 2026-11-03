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


def _pattern_leaves(cwd, inp: dict, val: str) -> int:
    """A Glob `pattern` / Grep `glob`: an absolute or `~` one is tested as a path; a `{` with `..` is outside (brace expansion can hide a `..`);
    a relative one with a `..` component is resolved against the tool's `path` input (else the cwd); any other relative one stays inside."""
    if "{" in val and ".." in val:
        return 1
    if val.startswith(("/", "~")):
        return leaves(cwd, val)
    if ".." not in val.split("/"):
        return 0
    base = inp.get("path")
    return leaves(cwd, posixpath.join(base if isinstance(base, str) and base else ".", val))


def inputs(cwd, name, inp: dict) -> int:
    """The outside count of one tool_use input: the path keys, and a Glob `pattern` or a Grep `glob` (a Grep `pattern` is a content regex and
    is left alone)."""
    n = sum(leaves(cwd, inp[k]) for k in PATH_KEYS if isinstance(inp.get(k), str) and inp[k])
    for key in ("glob", "pattern") if name == "Glob" else ("glob",):
        val = inp.get(key)
        n += isinstance(val, str) and bool(val) and _pattern_leaves(cwd, inp, val)
    return n
