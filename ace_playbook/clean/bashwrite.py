"""Which paths may a Bash command line have written? Deliberately over-approximate, stdlib only (D-169 clauses 6-9).

Any write-capable command (a write verb, `>`/`>>`/`<<`, or an output flag) marks every path-looking word it mentions, every URL basename
and the directory of each as agent-written; a false mark only makes a file untrusted, a missed one is a provenance bypass. Destination
verbs mark every word and every `dir/basename` pair; globs and braces record their directory; extraction verbs, any `git` and
server-named downloads record the cwd plus every directory-flag value; a command that changes directory or re-evaluates text
(`cd`, `eval`, `find`, `CDPATH` ...) taints the root; a command shlex cannot read marks every word. No parser.
A raw item starting with `DEST` is a destination directory (empty: the cwd), recorded as written even when it is the cwd itself.
"""

from __future__ import annotations

import os
import re
import shlex
from urllib.parse import urlparse

DEST = "\0"
ROOT = DEST + DEST  # a raw item for "taint the whole session root" (written_paths maps it to the first cwd, never a record cwd)
EXTRACT = {"tar", "unzip", "ditto", "rsync", "git", "patch", "gh", "bsdtar", "7z", "7za", "gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz",
           "zstd", "unzstd", "cpio", "pax"}  # write into the cwd and any directory flag's value
VERBS = {"tee", "cp", "mv", "install", "curl", "wget", "dd", "sed", "python", "python3", "perl", "ruby", "node", "sh", "bash", "zsh",
         "touch", "mkdir", "ln"} | EXTRACT
DEST_VERBS = {"tee", "cp", "mv", "install", "ln", "mkdir", "touch", "rsync", "ditto", "unzip", "tar", "git", "python", "python3", "perl",
              "ruby", "node", "sh", "bash", "zsh", "sed", "patch", "dd"}  # every non-flag word is a target (interpreters: `python3 gen.py out`)
DIR_FLAG_RE = re.compile(r"(?:--directory-prefix|--directory|--output-dir|-[CdPo])(?:=(.*)|(.+))?")  # separated, `=value` and attached forms
SERVER_RE = re.compile(r"-[A-Za-z]*J[A-Za-z]*|--(?:remote-h|content-d|trust-s).*")
OUT_RE = re.compile(r"-[A-Za-z]*[oOP]$|--(?:output|directory-prefix)[\w-]*(?:=.*)?$")  # flags whose value (next word or `=value`) is a path
ATTACHED_RE = re.compile(r"-(?!-)[A-Za-z]*?[oOPtCd](.+)")  # `-oout.md`, `-sLoout.md`, `-Pdl`, `-tdir`: the rest after the first such letter
PATH_RE = re.compile(r"[^/]*/|[.~]|.*\.[A-Za-z0-9]{1,8}$")  # a `/` anywhere, a leading `.`/`~`, or a file suffix
GLOB_RE = re.compile(r"[*?\[]|\{[^}]*\}")
LOOSE_RE = re.compile(r"[^\s;&|()<>'\"`$,{}]+")  # words with quotes and shell punctuation cut away (also splits `bash -c "..."`)
REDIRECT_RE = re.compile(r">>?[|&!]?\s*((?:\"[^\"]*\"|'[^']*'|\\.|[^\s;&|()<>\"'\\])+)")  # a quoted, escaped or bare redirect target
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
EXPANSION_RE = re.compile(r"\$[\w{(\[@*#?!$'\"-]|[`*?\[{]|(?:^|[\s;&|(])=|(?:^|[\s;&|(<>=:])~(?=[^/\s;&|)<>])")
# variable, substitution, quote, backtick, glob, brace, zsh `=cmd`, and a tilde form other than `~/` or a bare `~` (`~+`, `~-`, `~N`, `~user`)
ROOT_WORDS = {"cd", "pushd", "popd", "eval", "source", ".", "exec", "env", "nohup", "time", "xargs", "find"}  # change directory or re-evaluate text

def _server_named(w: str) -> bool:
    """A flag that makes the server choose the file name (`curl -J`, `wget --content-d`, a wgetrc spelling in any case, `_` or `-`)."""
    n = w.lower().replace("_", "").replace("-", "")
    return bool(SERVER_RE.fullmatch(w)) or "contentdisposition" in n or "trustservernames" in n


def _redirect_targets(cmd: str) -> set[str]:
    """Every redirect target, raw and unquoted by shlex (`> "no"tes` writes `notes`, `> my\\ notes` writes `my notes`)."""
    out = set()
    for target in REDIRECT_RE.findall(cmd):
        out |= {target, target.strip("'\"")}
        try:
            out |= set(shlex.split(target)[:1])
        except ValueError:
            pass  # the raw form stays
    return out


def _marked(seq: list[str], dest: bool) -> set[str]:
    """Path-looking words of one tokenisation (all plain words for a destination verb), flag values, and the last plain word."""
    found, plain, bare = set(), [], False
    for i, w in enumerate(seq):
        if w == "--" and not bare:
            bare = True
        elif w.startswith("-") and not bare:
            value = w.split("=", 1)[1] if "=" in w else seq[i + 1] if i + 1 < len(seq) else ""
            found |= {value} if OUT_RE.match(w) and not URL_RE.match(value) else set()
            found |= {w.split("=", 1)[1]} if w.startswith("--") and "=" in w else set()
            found |= {m.group(1)} if (m := ATTACHED_RE.fullmatch(w)) else set()
        elif not URL_RE.match(w):
            plain.append(w)
            found |= {w, w.split("=", 1)[-1]} if dest or PATH_RE.match(w) else set()
    return found | set(plain[-1:])


def _flag_values(seqs: list[list[str]]) -> set[str]:
    """Values of every directory flag anywhere in the command (next word, `=value` or attached), leaving out another flag or a URL."""
    out = set()
    for s in seqs:
        for i, w in enumerate(s):
            m = DIR_FLAG_RE.fullmatch(w)
            val = (m.group(1) or m.group(2) or (s[i + 1] if i + 1 < len(s) else "")) if m else ""
            out |= {val} if val and not val.startswith("-") and not URL_RE.match(val) else set()
    return out


def _destinations(seqs: list[list[str]], names: set[str], found: set[str], urls: set[str]) -> set[str]:
    """Directories to record as written: the cwd and flag values (extraction, git, server names), glob/brace directories,
    and for every URL its host and first path segment (a recursive wget) under the cwd and under each marked directory."""
    dests = set()
    if names & EXTRACT or any(_server_named(w) for s in seqs for w in s):
        dests |= _flag_values(seqs) | {""}  # the cwd is always recorded; a flag value never replaces it
    for w in found:
        if m := GLOB_RE.search(w):
            dests.add(os.path.dirname(w[:m.start()]))
    for u in urls:
        host, seg = urlparse(u).netloc, urlparse(u).path.strip("/").split("/")[0]
        under = {host, seg, os.path.join(host, seg)} - {""}
        dests |= under | {os.path.join(m, d) for m in found for d in under}
    return dests


def bash_analyse(cmd: str) -> tuple[set[str], bool]:
    """Raw paths a command may write (empty unless it is write-capable) and whether shlex read it (else `cmd.split()` was used)."""
    cmd = cmd.replace("\\\n", "")
    try:
        toks, ok = shlex.split(cmd, comments=False, posix=True), True
    except ValueError:
        toks, ok = cmd.split(), False
    seqs, redirects = [toks, LOOSE_RE.findall(cmd)], _redirect_targets(cmd)
    names = {os.path.basename(w).casefold() for s in seqs for w in s}  # macOS volumes are case-insensitive: `CP` runs `cp`
    forced = not ok  # a command shlex cannot read is write-capable by definition: every non-flag word of the fallback is marked
    expanding = bool(EXPANSION_RE.search(cmd))  # an unmodelled expansion can spell a verb or a path: write-capable, and it taints the root
    if not (forced or expanding or names & VERBS or redirects or "<<" in cmd or any(OUT_RE.match(w) and w[:2] != "-P" for s in seqs for w in s)):
        return set(), ok
    found = redirects.union(*(_marked(s, forced or bool(names & DEST_VERBS)) for s in seqs)) - {""}
    urls = {w for s in seqs for w in s if URL_RE.match(w)}
    dests, root = _destinations(seqs, names, found, urls), False
    if names & DEST_VERBS:  # `cp a.md docs/` writes docs/a.md: every marked word joined with every basename
        qs = sorted({os.path.basename(w) for w in found} - {""})
        if len(found) > 200 or len(qs) > 2000:
            root = True  # the cap bounds the work; a command over it taints the root instead of dropping pairs
        else:
            found |= {os.path.join(p, q) for p in found for q in qs}
    bases = {n for u in urls for b in [os.path.basename(urlparse(u).path) or "index.html"] for n in (b, b.removesuffix(".git"))}
    found |= bases | {os.path.join(p, b) for p in found for b in bases}
    if expanding or names & ROOT_WORDS or any("CDPATH" in w for s in seqs for w in s):
        root = True  # the tracker does not model the shell's working directory (a cd, eval, find, xargs...) nor any expansion
    return ((found | {os.path.dirname(p) for p in found}) - {""}) | {DEST + d for d in dests} | ({ROOT} if root else set()), ok
