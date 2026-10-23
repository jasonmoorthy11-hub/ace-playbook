"""Which paths did the agent itself write? A conservative parser, stdlib only (S-1 F6/F14/F16, D-169).

A Read of a file the agent wrote (a write tool, a Bash redirection or copy, a download, a clone, a subagent) is not the user's file.
Only the forms below are recognised; a write through any other command (`sed -i`, `patch`, `python -c`) is not tracked (LIMITATIONS.md).
"""

from __future__ import annotations

import os
import re
import shlex

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
INPUTS = {"<", "<<", "<<-", "<<<"}
BREAKS = {";", "&&", "||", "|", "|&", "&", "(", ")"}
PREFIXES = {"sudo", "command", "env", "time", "nohup", "exec"}
CLONE_VALUE_FLAGS = {"--depth", "-b", "--branch", "-o", "--origin", "-c", "--config", "--reference", "--filter", "-j", "--jobs",
                     "--template", "--separate-git-dir"}
HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*\\?(['\"]?)([^\s'\"<>|&;()]+)\1")
FD_ONLY_RE = re.compile(r"[&\d-]*")


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


def _lines(cmd: str) -> list[str]:
    """Command lines with here-document bodies removed, so text inside a heredoc is never read as a command."""
    out, end = [], ""
    for line in cmd.splitlines():
        if end:
            end = "" if line.strip() == end else end
            continue
        out.append(line)
        m = HEREDOC_RE.search(line)
        end = m.group(2) if m else ""
    return out


def _tokens(line: str) -> list[str]:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:                      # unbalanced quote: fall back to bare whitespace words
        return [w.strip("'\"") for w in line.split()]


def _clone_target(args: list[str]) -> set[str]:
    rest, value = [], False
    for a in args:
        if not value and not a.startswith("-"):
            rest.append(a)
        value = a in CLONE_VALUE_FLAGS
    if len(rest) >= 2:
        return {rest[-1]}
    return {os.path.basename(rest[0].rstrip("/")).removesuffix(".git")} if rest else set()


def _segment_targets(seg: list[str]) -> set[str]:
    words = list(seg)
    while words and (re.match(r"\w+=", words[0]) or words[0] in PREFIXES):
        words.pop(0)
    name, args = (os.path.basename(words[0]), words[1:]) if words else ("", [])
    pos = [a for a in args if not a.startswith("-")]
    found: set[str] = set()
    if name == "tee":
        found.update(pos)
    elif name in ("cp", "mv", "install") and len(pos) >= 2:
        found.add(pos[-1])                  # also `dest/<name>`: the destination may be a directory
        found.update(os.path.join(pos[-1], os.path.basename(s)) for s in pos[:-1])
    elif name in ("curl", "wget"):
        for i, a in enumerate(args):
            if a.startswith(("--output=", "--output-document=")):
                found.add(a.split("=", 1)[1])
            elif a in ("--output", "--output-document") or re.fullmatch(r"-[A-Za-z]*[oO]", a):
                found.update(args[i + 1:i + 2])
    elif name == "git" and args[:1] == ["clone"]:
        found |= _clone_target(args[1:])
    return found


def bash_write_targets(cmd: str) -> set[str]:
    """Raw (un-normalised) destinations a shell command line writes: redirections, tee, cp/mv/install, curl/wget output, git clone."""
    found: set[str] = set()
    for line in _lines(cmd):
        toks, seg = _tokens(line), []
        for i, tok in enumerate(toks + [";"]):
            after = toks[i + 1] if i + 1 < len(toks) else ""
            if tok in REDIRECTS and after not in BREAKS and not FD_ONLY_RE.fullmatch(after):
                found.add(after)
            if tok in BREAKS:
                found |= _segment_targets(seg)
                seg = []
            elif tok not in REDIRECTS | INPUTS and not (i and toks[i - 1] in REDIRECTS | INPUTS):
                seg.append(tok)
    return {t for t in found if t}


def written_paths(name: str, inp: dict, cwd: str) -> set[str]:
    """Normalised paths a tool_use block writes: the four write tools and Bash command lines (never `/` or the cwd and its parents)."""
    raw = bash_write_targets(str(inp.get("command", ""))) if name == "Bash" else (
        {str(inp["file_path"])} if name in WRITE_TOOLS and inp.get("file_path") else set())
    base = normalise(cwd, "") if cwd else ""
    return {p for p in {normalise(r, cwd) for r in raw} if p != "/" and not (base and (p == base or base.startswith(p + "/")))}
