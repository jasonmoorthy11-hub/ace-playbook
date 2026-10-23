"""Which paths did the agent itself write? A conservative parser, stdlib only (S-1 F6/F14/F16, D-169).

A Read of a file the agent wrote (write tool, Bash redirection, copy, download, clone, subagent) is not the user's; other commands
(`sed -i`, `patch`, `python -c`) are not tracked (LIMITATIONS.md). A command the parser cannot read fails closed: every word counts as written.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from .shellparse import analyse, every_word

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
INPUTS = {"<", "<<", "<<-", "<<<"}
PREFIXES = {"sudo", "command", "env", "time", "nohup", "exec"}
CLONE_VALUE_FLAGS = {"--depth", "-b", "--branch", "-o", "--origin", "-c", "--config", "--reference", "--filter", "-j", "--jobs",
                     "--template", "--separate-git-dir"}
FD_ONLY_RE = re.compile(r"[&\d-]*")
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


def normalise(path: str, cwd: str) -> str:
    """Absolute, `..`-free form of `path`; a relative path is joined to `cwd`."""
    return os.path.normpath(path if os.path.isabs(path) or not cwd else os.path.join(cwd, path))


def is_written(path: str, written: set[str]) -> bool:
    """The path, or a directory above it, is in `written` (a clone or a copied tree covers the files inside)."""
    while path not in written:
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent
    return True


def _is_break(tok: str) -> bool:
    return bool(tok) and not set(tok) - set(";&|()")

def _clone_target(args: list[str]) -> set[str]:
    rest, value = [], False
    for a in args:
        if not value and not a.startswith("-"):
            rest.append(a)
        value = a in CLONE_VALUE_FLAGS
    if len(rest) >= 2:
        return {rest[-1]}
    return {os.path.basename(rest[0].rstrip("/")).removesuffix(".git")} if rest else set()


def _download(name: str, args: list[str]) -> tuple[set[str], bool]:
    """curl: `-o`/`--output` take a value, `-O`/`--remote-name[-all]` write basename(URL). wget: `-O` takes a value, else basename(URL)."""
    found, bad, remote, prefix = set(), False, name == "wget", ""
    out_short = re.compile(r"-[A-Za-z]*o" if name == "curl" else r"-[A-Za-z]*[oO]")
    for i, a in enumerate(args):
        val = args[i + 1] if i + 1 < len(args) else None
        if a.split("=", 1)[0] in ("--output", "--output-document") or out_short.fullmatch(a):
            target = a.split("=", 1)[1] if "=" in a else val
            bad, remote = bad or target is None, remote and not (a.endswith("O") or "document" in a)
            found |= {target} - {None, "-"}
        elif a in ("--remote-name", "--remote-name-all") or (name == "curl" and re.fullmatch(r"-[A-Za-z]*O", a)):
            remote = True
        elif a in ("-P", "--directory-prefix"):
            prefix = val or ""
    if remote:
        urls = [a for a in args if URL_RE.match(a)]
        bad = bad or (name == "curl" and not urls)
        found |= {os.path.join(prefix, os.path.basename(urlparse(u).path) or "index.html") for u in urls}
    return found, bad


def _segment_targets(seg: list[str]) -> tuple[set[str], bool]:
    words = list(seg)
    while words and (re.match(r"\w+=", words[0]) or words[0] in PREFIXES):
        words.pop(0)
    name, args = (os.path.basename(words[0]), words[1:]) if words else ("", [])
    pos = [a for a in args if not a.startswith("-")]
    if name == "tee":
        return set(pos), False
    if name in ("cp", "mv", "install") and len(pos) >= 2:
        return {pos[-1]} | {os.path.join(pos[-1], os.path.basename(s)) for s in pos[:-1]}, False
    if name in ("curl", "wget"):
        return _download(name, args)
    if name == "git" and args[:1] == ["clone"]:
        return _clone_target(args[1:]), False
    return set(), False


def bash_analyse(cmd: str) -> tuple[set[str], bool]:
    """Raw destinations a command writes (redirections, tee, cp/mv/install, curl/wget, git clone) and whether it all parsed; if not, every word."""
    found: set[str] = set()
    lines, ok = analyse(cmd)
    for toks in lines:
        seg: list[str] = []
        for i, tok in enumerate(toks + [";"]):
            after = toks[i + 1] if i + 1 < len(toks) else ""
            if tok in REDIRECTS and not _is_break(after) and not FD_ONLY_RE.fullmatch(after):
                found.add(after)
            if _is_break(tok):
                got, bad = _segment_targets(seg)
                found, ok, seg = found | got, ok and not bad, []
            elif tok not in REDIRECTS | INPUTS and not (i and toks[i - 1] in REDIRECTS | INPUTS):
                seg.append(tok)
    return ({t for t in found if t} if ok else every_word(cmd) | found), ok


def bash_write_targets(cmd: str) -> set[str]:
    return bash_analyse(cmd)[0]


def written_paths(name: str, inp: dict, cwd: str, root: str = "") -> tuple[set[str], bool]:
    """Normalised paths a tool_use writes, and whether a Bash command failed to parse. Never `/`, the root or the cwd, nor a parent of them."""
    ok, raw = True, set()
    if name == "Bash":
        raw, ok = bash_analyse(str(inp.get("command", "")))
    elif name in WRITE_TOOLS and inp.get("file_path"):
        raw = {str(inp["file_path"])}
    bases = [normalise(b, "") for b in (cwd, root) if b]
    return {p for p in {normalise(r, cwd) for r in raw} if p != "/" and not any(b == p or b.startswith(p + "/") for b in bases)}, not ok
