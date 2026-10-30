"""Whole-command grammar for `rm_rf_nonscratch` v2 (D-209; docs/design/compiler.md 4.1.2). Stdlib `re` + posixpath only.

`proven_scratch(command)` is True ONLY when the RAW command text equals, in full (`\\A ... \\Z`, never `$`), one of three exact
shapes whose every delete target is scratch. No shell is modelled: no tokenizer, no quotes, no state. Spaces (one or more) separate
tokens; a tab, newline, quote, backslash, `$(`, `;` (outside S2's own), `|`, `&` (outside `&&`), parenthesis, brace, bracket,
redirection, `#` or `!` is in no shape, so it never matches. Everything else is v1's literal rule, unchanged.
  S1  cd <SCR> && rm <FLAGS> <REL>( <REL>)*
  S2  NAME=<SCR>(;| &&) rm <FLAGS> <VREF>( <VREF>)*    VREF: $NAME ${NAME} "$NAME" "${NAME}" + optional /<REL>, the same NAME; NAME 1-32 chars, no shell special
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
_S2 = re.compile(rf"\A(?P<n>[A-Za-z_][A-Za-z0-9_]{{0,31}})=(?P<scr>{_SCR})(?:;| +&&) +rm +{_FLAGS} +(?P<refs>{_VAR}(?: +{_VAR})*)\Z")
_S3 = re.compile(rf"\Agit(?: +-C +(?P<c>{_LIT}))? +worktree +add(?: +-b +{_LIT})? +(?P<wt>{_LIT})(?: +{_LIT})? +&& +rm +{_FLAGS} +(?P<rm>{_LIT})\Z")
# Names a shell treats specially, compared upper-cased: zsh 5.9 `print -l ${(k)parameters}` (plain, after compinit, and with the 36 modules of
# /usr/lib/zsh/5.9 loaded, zsh/net/tcp first; zsh/deltochar does not load there) U bash 3.2 `compgen -v`, plus names those cannot list (POSTEDIT,
# RPROMPT, RPROMPT2: unset by default; zle and env names), generated ONCE and FROZEN here and in the test (never computed at hook time).
# zsh cuts the value of KEYBOARD_HACK / HISTCHARS, so `NAME=<scr>; rm -rf $NAME/x` would delete outside scratch. A shell special added later
# is a documented residual (LIMITATIONS.md); the prefixes cover the families (BASH*, LC_*, ZSH_*, HIST*, PS0-9 ...).
_SPECIAL = tuple((
    "ALIASES ARGC ARGV BASH BASHOPTS BASHPID BASH_ARGC BASH_ARGV BASH_COMMAND BASH_ENV BASH_EXECUTION_STRING BASH_LINENO "
    "BASH_SOURCE BASH_SUBSHELL BASH_VERSINFO BASH_VERSION BASH_XTRACEFD BUFFER BUILTINS CDPATH CHILD_MAX COLUMNS COMMANDS "
    "COMPPOSTFUNCS COMPPREFUNCS COMP_WORDBREAKS COPROC COPROC_PID CPUTYPE CURSOR CUTBUFFER DIRSTACK DIS_ALIASES "
    "DIS_BUILTINS DIS_FUNCTIONS DIS_FUNCTIONS_SOURCE DIS_GALIASES DIS_PATCHARS DIS_RESWORDS DIS_SALIASES EGID ENV "
    "EPOCHREALTIME EPOCHSECONDS EPOCHTIME ERRNO ERRNOS EUID EXARR EXINT EXSTR FCEDIT FIGNORE FPATH FUNCFILETRACE FUNCNAME "
    "FUNCNEST FUNCSOURCETRACE FUNCSTACK FUNCTIONS FUNCTIONS_SOURCE FUNCTRACE GALIASES GID GLOBIGNORE GROUPS HISTCHARS "
    "HISTCMD HISTCONTROL HISTFILE HISTFILESIZE HISTIGNORE HISTNO HISTORY HISTORYWORDS HISTSIZE HISTTIMEFORMAT HOME HOST "
    "HOSTFILE HOSTNAME HOSTTYPE IFS IGNOREEOF INPUTRC IPREFIX ISUFFIX JOBDIRS JOBSTATES JOBTEXTS KEYBOARD_HACK KEYMAP "
    "KEYMAPS KEYS KEYS_QUEUED_COUNT KEYTIMEOUT LANG LANGINFO LASTABORTEDSEARCH LASTSEARCH LASTWIDGET LBUFFER LESS LINENO "
    "LINES LISTMAX LOGCHECK LOGNAME MACHTYPE MAIL MAILCHECK MAILPATH MANPATH MAPFILE MARK MATCH MBEGIN MEND MODULES "
    "MODULE_PATH NAMEDDIRS NULLCMD NUMERIC OLDPWD OPTARG OPTERR OPTIND OPTIONS OSTYPE PARAMETERS PATCHARS PATH PENDING "
    "PIPESTATUS POSIXLY_CORRECT POSTDISPLAY POSTEDIT PPID PREBUFFER PREDISPLAY PREFIX PROMPT PROMPT2 PROMPT3 PROMPT4 "
    "PROMPT_COMMAND PS0 PS1 PS2 PS3 PS4 PSVAR PWD QIPREFIX QISUFFIX RANDOM RBUFFER READLINE_LINE READLINE_MARK "
    "READLINE_POINT READNULLCMD REGION_ACTIVE REPLY RESWORDS RPROMPT RPROMPT2 SALIASES SAVEHIST SECONDS SHELL SHELLOPTS "
    "SHLVL SIGNALS SPROMPT SRANDOM STATUS SUFFIX SYSPARAMS TERM TERMCAP TERMINFO TIMEFMT TIMEFORMAT TMOUT TMPDIR TMPPREFIX "
    "TRY_BLOCK_ERROR TRY_BLOCK_INTERRUPT TTY TTYIDLE TZ UID USER USERDIRS USERGROUPS USERNAME VENDOR WATCH WATCHFMT WIDGETS "
    "WORDCHARS ZCURSES_ATTRS ZCURSES_COLORS ZCURSES_COLOR_PAIRS ZCURSES_KEYCODES ZCURSES_WINDOWS ZDOTDIR ZFTP_PREFS "
    "ZFTP_SESSION ZFTP_TMOUT ZFTP_VERBOSE ZLE_BRACKETED_PASTE ZLE_LINE_ABORTED ZLE_REMOVE_SUFFIX_CHARS ZLE_RPROMPT_INDENT "
    "ZLE_SPACE_SUFFIX_CHARS ZLE_STATE ZSH_ARGZERO ZSH_EVAL_CONTEXT ZSH_EXECUTION_STRING ZSH_NAME ZSH_PATCHLEVEL "
    "ZSH_SCHEDULED_EVENTS ZSH_SUBSHELL ZSH_VERSION _ _COMPAUTOS _COMPS _COMP_DUMPFILE _COMP_OPTIONS _COMP_SETUP _LASTCOMP "
    "_PATCOMPS _POSTPATCOMPS _SERVICES"
).split())
_SPECIAL_PREFIX = ("BASH", "COMP", "DIS_", "DYLD_", "EPOCH", "FUNC", "HIST", "KEYBOARD", "LANG", "LC_", "LD_", "LESS", "MAIL", "MATCH", "OPT", "PROMPT",
                   "PS0", "PS1", "PS2", "PS3", "PS4", "PS5", "PS6", "PS7", "PS8", "PS9", "REPLY", "RPS", "TERM", "UNDO_", "WATCH", "WIDGET",
                   "WORD", "YANK", "ZFTP_", "ZLE_", "ZSH", "_COMP")


def _dir(path: str, scratch) -> bool:
    return bool(scratch.search(posixpath.normpath(path)))


def _s1(m, scratch) -> bool:
    return _dir(m["scr"], scratch) and all(not t.startswith("-") and _dir(posixpath.join(m["scr"], t), scratch) for t in m["rel"].split())


def _s2(m, scratch) -> bool:
    name = m["n"].upper()
    if name in _SPECIAL or name.startswith(_SPECIAL_PREFIX) or not _dir(m["scr"], scratch):
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
