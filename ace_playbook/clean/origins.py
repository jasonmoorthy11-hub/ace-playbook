"""Which paths did the agent itself write? Deliberately over-approximate, stdlib only (S-1 F6/F14/F16, D-169 clauses 6-9).

The record-level layer: the four write tools (`file_path` or `notebook_path`) and the Bash analysis of `bashwrite.py`. A false "written" mark
only makes a file untrusted, a missed one is a provenance bypass, so every rule here widens what counts as agent-written; none parses.
"""

from __future__ import annotations

import os
import re
import unicodedata
from urllib.parse import unquote

from .bashwrite import DEST, ROOT, bash_analyse

__all__ = ["DEST", "ROOT", "WRITE_TOOLS", "bash_analyse", "is_written", "key", "normalise", "written_paths"]

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_ONLY = {"Read", "Grep", "Glob", "LS", "TodoRead", "TaskList", "TaskGet", "WebSearch", "WebFetch", "ListMcpResourcesTool",
             "ReadMcpResourceTool"}  # every other tool but Bash and the four write tools is presumed to write what it names
READ_ONLY_RE = re.compile(r"(?:read|list|search|get|find|query|describe)_")  # the last `__` segment of an MCP tool name ...
WRITE_VERB_RE = re.compile(r"write|edit|create|replace|move|copy|save|append|rename|delete|put|upload|patch|set|update|insert|remove|mkdir|touch"
                           r"|extract|clone|download", re.IGNORECASE)  # ... unless the whole name also holds a write verb (`get_and_write`)
COMMAND_KEYS = {"command", "cmd", "script", "commands", "args"}  # a non-read-only tool's shell-command strings go through bash_analyse too
PATH_KEYS = {"path", "file_path", "destination", "dest", "target", "notebook_path", "filename", "source", "src", "from", "to", "new_path",
             "old_path", "output", "output_path", "out", "file", "files", "paths", "uri"}  # everything under these is recorded, whatever its shape
PATH_LIKE_RE = re.compile(r"[^/]*/|[.~]|.*\.[A-Za-z0-9]{1,8}$")
GLOB_META_RE = re.compile(r"[*?\[{]")
MAX_TOOL_PATHS = 200
HOME_RE = re.compile(r"^\$\{?HOME\}?(?=/|$)")
FILE_URL_RE = re.compile(r"^file://[^/]*", re.IGNORECASE)  # `file:///a/b`, `file://localhost/a/b`: the path after the authority, percent-decoded


def normalise(path: str, cwd: str) -> str:
    """Absolute, `..`-free form of `path`; `~` and a leading `$HOME` are expanded, and a relative path is joined to `cwd`."""
    path = os.path.expanduser(HOME_RE.sub(lambda _: os.path.expanduser("~"), path)) if path.startswith(("~", "$")) else path
    out = os.path.normpath(path if os.path.isabs(path) or not cwd else os.path.join(cwd, path))
    return "/" + out.lstrip("/") if out.startswith("//") else out  # posix normpath keeps exactly two leading slashes


def key(path: str) -> str:
    """The comparison form of a path: NFKC-normalised and casefolded (macOS stores NFD, default APFS and NTFS ignore case)."""
    return unicodedata.normalize("NFKC", path).casefold()


def _path_strings(value: object) -> set[str]:
    """Every string anywhere inside `value` (dict keys and values, list items, nested) that sits under a known path key or looks like a
    path; over the cap, the ROOT marker."""
    found, stack = set(), [(value, False)]
    while stack:
        v, known = stack.pop()
        if isinstance(v, dict):
            for k, item in v.items():
                stack += [(k, known), (item, known or str(k).lower() in PATH_KEYS)]
        elif isinstance(v, (list, tuple)):
            stack += [(i, known) for i in v]
        elif isinstance(v, str) and v and (known or PATH_LIKE_RE.match(v)):
            found.add(v)  # the raw string is always recorded (`a%20b.md` is a literal name); only a file URL also adds its path and decoded path
            if FILE_URL_RE.match(v):
                p = FILE_URL_RE.sub("", v)
                found |= {p, unquote(p)} - {""}  # the glob rule sees both
    return {ROOT} if len(found) > MAX_TOOL_PATHS else found


def _command_strings(value: object, under: bool = False) -> set[str]:
    """Every string under a command key (nested), each alone and the strings of a list joined, for `bash_analyse`."""
    out = set()
    if isinstance(value, dict):
        for k, item in value.items():
            out |= _command_strings(item, under or str(k).lower() in COMMAND_KEYS)
    elif isinstance(value, (list, tuple)):
        parts = [i for i in value if isinstance(i, str)]
        out |= ({" ".join(parts)} if under and parts else set()) | set().union(*(_command_strings(i, under) for i in value))
    elif isinstance(value, str) and under:
        out.add(value)
    return out


def _read_only(name: str) -> bool:
    return name in READ_ONLY or bool(READ_ONLY_RE.match(name.split("__")[-1]) and not WRITE_VERB_RE.search(name))


def is_written(path: str, written: set[str]) -> bool:
    """The path, or a directory above it, is in `written` (a clone or a copied tree covers the files inside). `written` holds casefolded
    paths and the lookup is casefolded too: default APFS and NTFS are case-insensitive, and a false match only makes a file untrusted."""
    path = key(path)
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
    elif name in WRITE_TOOLS:
        raw = {str(inp.get("file_path") or inp.get("notebook_path"))} if inp.get("file_path") or inp.get("notebook_path") else set()
    elif not _read_only(name):
        raw = _path_strings(inp)  # an MCP or other tool that may write: every path-looking string it was given, however nested
        raw |= {DEST + os.path.dirname(p[:m.start()]) for p in raw if (m := GLOB_META_RE.search(p))}  # `docs/*.md`: its directory is written too
        for command in _command_strings(inp):  # ... and its shell-command strings are Bash commands
            more, fine = bash_analyse(command)
            raw, ok = raw | more, ok and fine
    bases = [normalise(b, "") for b in (cwd, root) if b]
    dests = {normalise(r[1:] or ".", cwd or root) for r in raw if r.startswith(DEST) and r != ROOT and (cwd or root)}
    dests = {root or cwd if d == "/" or root.startswith(d + "/") else d for d in dests} | ({root or cwd} if ROOT in raw and (root or cwd) else set())
    plain = {normalise(r, cwd) for r in raw if not r.startswith(DEST)}
    return dests | {p for p in plain if p != "/" and not any(b == p or b.startswith(p + "/") for b in bases)}, not ok
