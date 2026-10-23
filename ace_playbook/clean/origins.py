"""Which paths did the agent itself write? Deliberately over-approximate, stdlib only (S-1 F6/F14/F16, D-169 clauses 6-9).

The record-level layer: the four write tools (`file_path` or `notebook_path`) and the Bash analysis of `bashwrite.py`. A false "written" mark
only makes a file untrusted, a missed one is a provenance bypass, so every rule here widens what counts as agent-written; none parses.
"""

from __future__ import annotations

import os
import re

from .bashwrite import DEST, ROOT, bash_analyse

__all__ = ["DEST", "ROOT", "WRITE_TOOLS", "bash_analyse", "is_written", "normalise", "written_paths"]

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
HOME_RE = re.compile(r"^\$\{?HOME\}?(?=/|$)")


def normalise(path: str, cwd: str) -> str:
    """Absolute, `..`-free form of `path`; `~` and a leading `$HOME` are expanded, and a relative path is joined to `cwd`."""
    path = os.path.expanduser(HOME_RE.sub(lambda _: os.path.expanduser("~"), path)) if path.startswith(("~", "$")) else path
    return os.path.normpath(path if os.path.isabs(path) or not cwd else os.path.join(cwd, path))


def is_written(path: str, written: set[str]) -> bool:
    """The path, or a directory above it, is in `written` (a clone or a copied tree covers the files inside)."""
    while path not in written:
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent
    return True


def written_paths(name: str, inp: dict, cwd: str, root: str = "") -> tuple[set[str], bool]:
    """Normalised paths a tool_use writes, and whether a Bash command needed the split fallback. Never `/`, the root or the cwd, nor a parent,
    except a destination directory (extraction, `-J` download, a glob's directory), where `/` or a parent of the root records the root instead.
    The directory-of-path rule (a Read beside a written file may be agent output too) applies to Bash only, never to the four write tools."""
    ok, raw = True, set()
    if name == "Bash":
        raw, ok = bash_analyse(str(inp.get("command", "")))
    elif name in WRITE_TOOLS and (inp.get("file_path") or inp.get("notebook_path")):
        raw = {str(inp.get("file_path") or inp.get("notebook_path"))}
    bases = [normalise(b, "") for b in (cwd, root) if b]
    dests = {normalise(r[1:] or ".", cwd or root) for r in raw if r.startswith(DEST) and r != ROOT and (cwd or root)}
    dests = {root or cwd if d == "/" or root.startswith(d + "/") else d for d in dests} | ({root or cwd} if ROOT in raw else set())
    plain = {normalise(r, cwd) for r in raw if not r.startswith(DEST)}
    return dests | {p for p in plain if p != "/" and not any(b == p or b.startswith(p + "/") for b in bases)}, not ok
