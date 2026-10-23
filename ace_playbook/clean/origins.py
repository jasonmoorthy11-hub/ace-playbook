"""Which paths did the agent itself write? Deliberately over-approximate, stdlib only (S-1 F6/F14/F16, D-169 clauses 6-7).

Any write-capable Bash command (a write verb, `>`/`>>`/`<<`, or an output flag) marks every path-looking word it mentions, every URL
basename and the directory of each as agent-written; a false mark only makes a file untrusted, a missed one is a provenance bypass.
Extraction verbs (`tar unzip ditto rsync`, `git` with no URL) and server-named downloads (`-J`) record their destination directory, the cwd
by default; a command shlex cannot read marks every word of it.
"""

from __future__ import annotations

import os
import re
import shlex
from urllib.parse import urlparse

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
VERBS = {"tee", "cp", "mv", "install", "curl", "wget", "git", "rsync", "ditto", "dd", "sed", "patch", "python", "python3", "perl",
         "ruby", "node", "sh", "bash", "zsh", "touch", "mkdir", "ln", "unzip", "tar"}
DEST_VERBS = {"tee", "cp", "mv", "install", "ln", "mkdir", "touch", "rsync", "ditto", "unzip", "tar", "git", "python", "python3", "perl",
              "ruby", "node", "sh", "bash", "zsh", "sed", "patch", "dd"}  # every non-flag word is a target (interpreters: `python3 gen.py out`)
EXTRACT = {"tar", "unzip", "ditto", "rsync"}  # also write into the cwd or the `-C`/`-d`/`--directory` value, as does `git` with no URL
EXTRACT_DIR_RE = re.compile(r"-C|-d|--directory|--directory-prefix")
DOWNLOAD_DIR_RE = re.compile(r"-P|-o|--directory-prefix|--output-dir")
SERVER_NAME_RE = re.compile(r"-[A-Za-z]*J[A-Za-z]*|--remote-header-name|--content-disposition|--trust-server-names")  # the name is not in the URL
DEST = "\0"  # a raw item starting with this is a destination directory (empty: the cwd), recorded as written even when it is the cwd itself
OUT_RE = re.compile(r"-[A-Za-z]*[oOP]$|--(?:output|directory-prefix)[\w-]*(?:=.*)?$")  # flags whose value (next word or `=value`) is a path
PATH_RE = re.compile(r"[^/]*/|[.~]|.*\.[A-Za-z0-9]{1,8}$")  # a `/` anywhere, a leading `.`/`~`, or a file suffix
LOOSE_RE = re.compile(r"[^\s;&|()<>'\"`$,{}]+")  # words with quotes and shell punctuation cut away (also splits `bash -c "..."`)
REDIRECT_RE = re.compile(r">>?[|&]?\s*(\"[^\"]*\"|'[^']*'|[^\s;&|()<>]+)")
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


def _marked(seq: list[str], dest: bool) -> set[str]:
    """Path-looking words of one tokenisation (all non-flag words for a destination verb), flag values, and the last non-flag word."""
    found, plain = set(), []
    for i, w in enumerate(seq):
        if w.startswith("-"):
            value = w.split("=", 1)[1] if "=" in w else seq[i + 1] if i + 1 < len(seq) else ""
            found |= {value} if OUT_RE.match(w) and not URL_RE.match(value) else set()
        elif not URL_RE.match(w):
            plain.append(w)
            found |= {w, w.split("=", 1)[-1]} if dest or PATH_RE.match(w) else set()
    return found | set(plain[-1:])


def _flag_values(seqs: list[list[str]], flags: re.Pattern) -> set[str]:
    """Values of the flags matching `flags` (next word or `=value`), leaving out another flag or a URL."""
    out = set()
    for s in seqs:
        for i, w in enumerate(s):
            flag, eq, val = w.partition("=")
            val = val if eq else s[i + 1] if i + 1 < len(s) else ""
            out |= {val} if flags.fullmatch(flag) and val and not val.startswith("-") and not URL_RE.match(val) else set()
    return out


def bash_analyse(cmd: str) -> tuple[set[str], bool]:
    """Raw paths a command may write (empty unless it is write-capable) and whether shlex read it (else `cmd.split()` was used)."""
    cmd = cmd.replace("\\\n", "")
    try:
        toks, ok = shlex.split(cmd, comments=False, posix=True), True
    except ValueError:
        toks, ok = cmd.split(), False
    seqs, redirects = [toks, LOOSE_RE.findall(cmd)], {m.strip("'\"") for m in REDIRECT_RE.findall(cmd)}
    names = {os.path.basename(w) for s in seqs for w in s}
    forced = not ok  # a command shlex cannot read is write-capable by definition: every non-flag word of the fallback is marked
    if not (forced or names & VERBS or redirects or "<<" in cmd or any(OUT_RE.match(w) and w[:2] != "-P" for s in seqs for w in s)):
        return set(), ok
    found = redirects.union(*(_marked(s, forced or bool(names & DEST_VERBS)) for s in seqs)) - {""}
    urls = {w for s in seqs for w in s if URL_RE.match(w)}
    dests = set()
    if names & EXTRACT or ("git" in names and not urls):
        dirs = _flag_values(seqs, EXTRACT_DIR_RE)
        dests |= dirs if dirs and not names & {"ditto", "rsync"} else dirs | {""}
    if any(SERVER_NAME_RE.fullmatch(w) for s in seqs for w in s):
        dests |= _flag_values(seqs, DOWNLOAD_DIR_RE) or {""}
    bases = {n for u in urls for b in [os.path.basename(urlparse(u).path) or "index.html"] for n in (b, b.removesuffix(".git"))}
    found |= bases | {os.path.join(p, b) for p in found for b in bases}
    return ((found | {os.path.dirname(p) for p in found}) - {""}) | {DEST + d for d in dests}, ok


def written_paths(name: str, inp: dict, cwd: str, root: str = "") -> tuple[set[str], bool]:
    """Normalised paths a tool_use writes, and whether a Bash command needed the split fallback. Never `/`, the root or the cwd, nor a parent,
    except a destination directory (extraction, `-J` download), where `/` or a parent of the root records the root instead.
    The directory-of-path rule (a Read beside a written file may be agent output too) applies to Bash only, never to the four write tools."""
    ok, raw = True, set()
    if name == "Bash":
        raw, ok = bash_analyse(str(inp.get("command", "")))
    elif name in WRITE_TOOLS and inp.get("file_path"):
        raw = {str(inp["file_path"])}
    bases = [normalise(b, "") for b in (cwd, root) if b]
    dests = {normalise(r[1:] or ".", cwd or root) for r in raw if r.startswith(DEST) and (cwd or root)}
    dests = {root or cwd if d == "/" or root.startswith(d + "/") else d for d in dests}
    plain = {normalise(r, cwd) for r in raw if not r.startswith(DEST)}
    return dests | {p for p in plain if p != "/" and not any(b == p or b.startswith(p + "/") for b in bases)}, not ok
