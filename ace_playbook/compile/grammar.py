"""Whole-command grammar for `rm_rf_nonscratch` v2 (D-209; docs/design/compiler.md 4.1.2). Stdlib `re` + posixpath only.

`proven_scratch(command)` is True ONLY when the RAW command text equals, in full (`\\A ... \\Z`, never `$`), one of three exact
shapes whose every delete target is scratch. No shell is modelled: no tokenizer, no quotes, no state. Spaces (one or more) separate
tokens; a tab, newline, quote, backslash, `$(`, `;` (outside S2's own), `|`, `&` (outside `&&`), parenthesis, brace, bracket,
redirection, `#` or `!` is in no shape, so it never matches. Everything else is v1's literal rule, unchanged.
  S1  cd <SCR> && rm <FLAGS> <REL>( <REL>)*
  S2  NAME=<SCR>(;| &&) rm <FLAGS> <VREF>( <VREF>)*    VREF: $NAME ${NAME} "$NAME" "${NAME}" + optional /<REL>, the same NAME
  S3  git( -C <LIT>)? worktree add( -b <LIT>)? <WT>( <LIT>)? && rm <FLAGS> <WT>    (the rm path is the path the add just created)
"""

from __future__ import annotations

import posixpath
import re

_COMP = r"[A-Za-z0-9_-][A-Za-z0-9._-]*"                 # one component of a literal absolute path: no leading dot, no glob
_SCR = rf"(?:/{_COMP})+"
_RC = r"[A-Za-z0-9_-][A-Za-z0-9._*?-]*"                 # a relative component: a glob character only after its first character
_REL = rf"(?:\.|\./|(?:\./)?(?:{_RC}/)*(?:{_RC}|\*))"   # . ./ * ./* a/b a*/b/* ... (a whole trailing * is the only leading glob)
_FLAGS = r"(?:-rf|-fr|-Rf|-fR|-r +-f|-f +-r)(?: +--)?"
_LIT = r"[A-Za-z0-9_./][A-Za-z0-9_./-]*"                # a git path or ref: never an option (no leading dash)
_REF = r"(?:\$(?P=n)|\$\{(?P=n)\})"                      # $NAME or ${NAME}
_VAR = rf'(?:{_REF}(?:/{_REL})?|"{_REF}(?:/{_REL})?"|"{_REF}"/{_REL})'
_S1 = re.compile(rf"\Acd +(?P<scr>{_SCR}) +&& +rm +{_FLAGS} +(?P<rel>{_REL}(?: +{_REL})*)\Z")
_S2 = re.compile(rf"\A(?P<n>[A-Za-z_][A-Za-z0-9_]*)=(?P<scr>{_SCR})(?:;| +&&) +rm +{_FLAGS} +(?P<refs>{_VAR}(?: +{_VAR})*)\Z")
_S3 = re.compile(rf"\Agit(?: +-C +(?P<c>{_LIT}))? +worktree +add(?: +-b +{_LIT})? +(?P<wt>{_LIT})(?: +{_LIT})? +&& +rm +{_FLAGS} +(?P<rm>{_LIT})\Z")
# names the shell or the C library reads (search paths, word splitting, startup files, specials that change on their own)
_BAD = frozenset(("_ IFS CDPATH PATH HOME TMPDIR PWD OLDPWD ENV SHELL SHELLOPTS GLOBIGNORE FPATH MANPATH MAILPATH MODULE_PATH ZDOTDIR UID "
                  "EUID PPID TERM USER LOGNAME LANG NULLCMD READNULLCMD POSIXLY_CORRECT RANDOM SRANDOM SECONDS LINENO OPTIND OPTARG REPLY "
                  "SHLVL HISTCMD HISTSIZE HISTFILE COLUMNS LINES FUNCNAME EPOCHSECONDS EPOCHREALTIME STATUS ARGC ARGV PIPESTATUS GID EGID "
                  "TTY USERNAME GROUPS DIRSTACK HOSTNAME HOSTTYPE MACHTYPE OSTYPE").split())
_BAD_PREFIX = ("BASH", "LD_", "DYLD_", "LC_", "ZSH")


def _dir(path: str, scratch) -> bool:
    return bool(scratch.search(posixpath.normpath(path)))


def _s1(m, scratch) -> bool:
    return _dir(m["scr"], scratch) and all(not t.startswith("-") and _dir(posixpath.join(m["scr"], t), scratch) for t in m["rel"].split())


def _s2(m, scratch) -> bool:
    name = m["n"].upper()
    if name in _BAD or name.startswith(_BAD_PREFIX) or not _dir(m["scr"], scratch):
        return False
    for tok in m["refs"].split():
        rest = tok.lstrip('"')[1:]                                              # drop the quote and the `$`
        rest = rest[rest.index("}") + 1:] if rest.startswith("{") else rest[len(m["n"]):]
        rest = rest.replace('"', "")                                            # what follows the variable: "" or "/<REL>"
        if rest and not _dir(posixpath.join(m["scr"], rest[1:]), scratch):
            return False
    return True


def _s3(m, scratch) -> bool:
    c, wt, rm = m["c"], m["wt"], m["rm"]
    wn = posixpath.normpath(wt)
    if any(".." in p.split("/") for p in (wt, rm, c or "")) or wn != posixpath.normpath(rm) or wn in (".", "/"):
        return False
    if c and not wt.startswith("/"):                                            # `git -C <dir>` puts a relative path under <dir>
        return False
    if c and (posixpath.normpath(c) + "/").startswith(wn.rstrip("/") + "/"):    # never the repository itself or one of its parents
        return False
    return not wt.startswith("/") or wn.count("/") >= 2                         # an absolute worktree is at least two components deep


def proven_scratch(command: str, scratch=None) -> bool:
    """True only when `command` is one of the three shapes above and every target it deletes is scratch. Never raises.
    `scratch` is detectors.SCRATCH (v1's own rule; detectors.py passes it, an omitted one is imported lazily)."""
    try:
        if not isinstance(command, str):
            return False
        if scratch is None:
            from .detectors import SCRATCH as scratch
        for shape, test in ((_S1, _s1), (_S2, _s2), (_S3, _s3)):
            m = shape.match(command)
            if m:
                return test(m, scratch)
        return False
    except Exception:   # noqa: BLE001  fail closed: an input the grammar cannot read is not a proof
        return False
