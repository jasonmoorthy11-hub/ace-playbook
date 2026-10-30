"""Property tests for `rm_rf_nonscratch` v2 (compile/grammar.py, D-209): generated commands against an oracle the generator knows.

v2 is v1's literal rule plus ONE exception: a command that is, in full, one of three exact scratch shapes (S1 `cd <scr> && rm -rf <rel>...`,
S2 `NAME=<scr>; rm -rf "$NAME"...`, S3 `git worktree add <p> && rm -rf <p>`) is proven scratch and does not fire. The generator builds those
shapes with a scratch or an outside root; the oracle is "proven iff every joined target is scratch", and the detector must fire exactly when
v1 fires and the shape is not proven. The V1 FLOOR: `v2_fires or not v1_fires or proven`, where `v1_fires` is a frozen copy of v1's rule kept
below and `proven` = the generator built a grammar shape with scratch targets. Every mutation of a shape (a forbidden character, a trailing
newline, a tab, a `..` or leading-dot component, another NAME, a `;` in S1) is NOT proven.
Nineteen mutants of grammar.py and one of detectors.py (the `truncated` guard) must each fail a test.
Seeded (`random.Random(1234)`, override with PYTEST_SEED); stdlib only; the commands are DATA fed to the detector, nothing runs.
"""

import os
import posixpath
import random
import re
import sys
import time
import types
from pathlib import Path

import pytest

from ace_playbook.compile import catalogue as cat
from ace_playbook.compile import detectors as d
from ace_playbook.compile import grammar, shell
from ace_playbook.compile.events import event_from_hook
from ace_playbook.fixtures import fakes

from . import compile_support as cs

SEED = int(os.environ.get("PYTEST_SEED", "1234"))
N = 400
NAME = "rm_rf_nonscratch"
SCR_ROOTS = ["/tmp/w", "/tmp", "/private/tmp/claude-501/proj/sess/scratchpad", "/var/folders/ab/cd/T/x", "/tmp/a/b-c_d.e", "/srv/app/node_modules",
             "/w/x/build", "/private/tmp/z"]
OUT_ROOTS = [fakes.HOME_PREFIX + "FAKEuser/proj", "/srv/data", "/opt/app/x", "/w/src", "/tmpfoo/x", "/var/foldersx/a", "/srv/app/dist2", "/home/u"]
FLAGS = ["-rf", "-fr", "-Rf", "-fR", "-r -f", "-f -r"]
RELS = [".", "./", "*", "./*", "x", "sub/y", "./sub/*", "a*b", "d?e", "a.b/c-d_e", "sub/*", "x/y/z*", "./-x"]
NAMES = ["D", "DIR", "_x", "Tmp1", "target", "a"]


def fires(cmd) -> bool:
    ev = event_from_hook(cs.pre_payload(cmd), probe=lambda _p: False)
    out, _state, errors = cat.evaluate(ev, None, names=[NAME], strict=False)
    assert errors == []
    return any(f.detector == NAME and f.count > 0 for f in out)


def v1_fires(cmd: str) -> bool:
    """A frozen copy of v1's rule (detectors.py before D-204): tokens from `shell.command_segments`, literal SCRATCH test of each target."""
    for toks in shell.command_segments(cmd):
        if not toks or posixpath.basename(toks[0].replace("\\", "")) != "rm":
            continue
        fl = [t for t in toks[1:] if t.startswith("-")]
        rec = any(f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f)) for f in fl)
        frc = any(f == "--force" or (not f.startswith("--") and "f" in f) for f in fl)
        targets = [t for t in toks[1:] if not t.startswith("-")]
        if rec and frc and any(not d.SCRATCH.search(posixpath.normpath(t)) for t in targets):
            return True
    return False


def scratch(path: str) -> bool:
    return bool(d.SCRATCH.search(posixpath.normpath(path)))


def proven(cmd: str) -> bool:
    return grammar.proven_scratch(cmd, d.SCRATCH)


# ------------------------------------------------------------------ the generator: (command, oracle) with the oracle from the roots
def flags(r: random.Random) -> str:
    return r.choice(FLAGS) + r.choice(["", "", " --"])


def s1(r: random.Random, root: str, rels=None, sep=" && ") -> tuple[str, bool]:
    rels = rels or [r.choice(RELS) for _ in range(r.randint(1, 3))]
    return f"cd {root}{sep}rm {flags(r)} {' '.join(rels)}", scratch(root) and all(scratch(posixpath.join(root, x)) for x in rels)


def vref(r: random.Random, name: str, rel) -> str:
    var = r.choice([f"${name}", f"${{{name}}}"])
    if rel is None:
        return r.choice([var, f'"{var}"'])
    return r.choice([f"{var}/{rel}", f'"{var}/{rel}"', f'"{var}"/{rel}'])


def s2(r: random.Random, root: str) -> tuple[str, bool]:
    name = r.choice(NAMES)
    rels = [r.choice([None] + RELS) for _ in range(r.randint(1, 3))]
    refs = " ".join(vref(r, name, x) for x in rels)
    ok = scratch(root) and all(scratch(root if x is None else posixpath.join(root, x)) for x in rels)
    return f"{name}={root}{r.choice([';', ' &&'])} rm {flags(r)} {refs}", ok


def s3(r: random.Random, wt: str, rm=None) -> tuple[str, bool]:
    c = f"-C {r.choice(['/w/repo', '/srv/r'])} " if r.random() < 0.3 else ""
    opts = r.choice(["", "-b nb "]) + wt + r.choice(["", " HEAD", " main"])
    return f"git {c}worktree add {opts} && rm {flags(r)} {rm or wt}", rm is None or posixpath.normpath(rm) == posixpath.normpath(wt)


def shapes(r: random.Random, root: str) -> list[tuple[str, bool]]:
    return [s1(r, root), s2(r, root), s3(r, root + "/wt")]


def worlds(n: int, salt: int):
    rng = random.Random(SEED * 7 + salt)
    for _ in range(n):
        yield rng.randrange(1 << 30), rng.choice(SCR_ROOTS), rng.choice(OUT_ROOTS)


def test_generated_shapes_are_proven_exactly_when_every_joined_target_is_scratch():
    seen = {True: 0, False: 0}
    for seed, scr, out in worlds(N, 1):
        for root in (scr, out):
            r = random.Random(seed)
            for cmd, oracle in shapes(r, root)[:2]:
                assert proven(cmd) is oracle, (seed, cmd)
                seen[oracle] += 1
            cmd, oracle = shapes(r, root)[2]
            assert proven(cmd) is True is oracle, cmd        # S3: the rm path is the path the add created, wherever it is
    assert seen[True] >= N and seen[False] >= N // 2


def test_the_detector_fires_exactly_when_v1_fires_and_the_shape_is_not_proven():
    """The v1 floor on the shapes themselves: `v2_fires or not v1_fires or proven`, and no new fire either."""
    for seed, scr, out in worlds(N, 2):
        for root in (scr, out):
            for cmd, oracle in shapes(random.Random(seed), root):
                v1, v2 = v1_fires(cmd), fires(cmd)
                assert v2 or not v1 or oracle, (seed, cmd)
                assert v2 is (v1 and not oracle), (seed, cmd)


def test_extra_spaces_between_tokens_change_nothing():
    """A shape separates its tokens by one or more spaces (never a tab or a newline): the verdict and the detector are the same."""
    for seed, scr, out in worlds(N // 4, 8):
        for root in (scr, out):
            for cmd, oracle in shapes(random.Random(seed), root):
                wide = cmd.replace(" ", "   ")
                assert proven(wide) is proven(cmd) and proven(cmd) is oracle, (seed, wide)
                assert fires(wide) is fires(cmd), (seed, wide)


def test_the_generator_covers_every_variant_of_every_shape():
    text = "\n".join(c for seed, scr, _o in worlds(N, 3) for c, _ in shapes(random.Random(seed), scr))
    for token in ("-rf", "-fr", "-Rf", "-fR", "-r -f", "-f -r", " --", "${", '"$', "/*", "./", "&&", ";", "-b nb", "-C ", "HEAD", "worktree add"):
        assert token in text, token


# ------------------------------------------------------------------ mutations of a proven shape are never proven
FORBIDDEN = list("\\`;|&()[]{}<>#!'\"\n\t~=%,:@+^") + ["$(", "$((", "&&", "||", ">&2", "2>"]


def mutate_chars(r: random.Random, cmd: str):
    for ch in FORBIDDEN:
        for _ in range(3):
            k = r.randrange(len(cmd) + 1)
            yield f"insert {ch!r} at {k}", cmd[:k] + ch + cmd[k:]
    yield "trailing newline", cmd + "\n"
    yield "trailing space", cmd + " "
    yield "leading space", " " + cmd
    yield "trailing tab", cmd + "\t"
    for k in [i for i, c in enumerate(cmd) if c == " "][:6]:
        yield f"tab for the space at {k}", cmd[:k] + "\t" + cmd[k + 1:]
        yield f"newline for the space at {k}", cmd[:k] + "\n" + cmd[k + 1:]


def test_every_character_level_mutation_of_a_proven_shape_is_not_proven():
    rng, count = random.Random(SEED + 5), 0
    for seed, scr, _out in worlds(60, 4):
        for cmd, oracle in shapes(random.Random(seed), scr):
            if not oracle:
                continue
            assert proven(cmd), cmd
            for what, mutant in mutate_chars(rng, cmd):
                assert not proven(mutant), (what, mutant)
                count += 1
    assert count >= 3000


BAD_RELS = ["..", "../x", "sub/../x", "x/..", ".hidden", "./.hidden", ".*", "./.*", "./.[.]", "?x", "[a]x", "*/../x", "a/./b", "a//b", "/abs",
            "~/x", "$X", "{a,b}", "a\tb", "x/", "./x/", "-rf", "--", "-"]


def structured_mutants(r: random.Random, root: str):
    """(why, command): the shape built right, then broken in exactly one way the grammar names."""
    for rel in BAD_RELS:
        yield f"s1 rel {rel!r}", f"cd {root} && rm -rf {rel}"
        yield f"s1 later rel {rel!r}", f"cd {root} && rm -rf x {rel}"
        if rel not in ("-rf", "--", "-"):
            yield f"s2 rel {rel!r}", f'D={root}; rm -rf "$D/{rel}"'
    yield "s1 dotdot in the root", f"cd {root}/.. && rm -rf x"
    yield "s1 dotdot inside the root", f"cd {root}/x/../y && rm -rf x"
    yield "s1 leading-dot component in the root", f"cd {root}/.d && rm -rf x"
    yield "s1 dot component in the root", f"cd {root}/./d && rm -rf x"
    yield "s1 semicolon", f"cd {root}; rm -rf ./x"
    yield "s1 or", f"cd {root} || rm -rf ./x"
    yield "s1 relative root", "cd tmp/w && rm -rf x"
    yield "s1 root with a trailing slash", f"cd {root}/ && rm -rf x"
    yield "s1 second command", f"cd {root} && rm -rf x && ls"
    yield "s1 second rm", f"cd {root} && rm -rf x; rm -rf y"
    yield "s1 flags -v", f"cd {root} && rm -rf -v x"
    yield "s1 flags -rfv", f"cd {root} && rm -rfv x"
    yield "s1 flags -r only", f"cd {root} && rm -r x"
    yield "s1 flags -f only", f"cd {root} && rm -f x"
    yield "s1 long flags", f"cd {root} && rm --recursive --force x"
    yield "s1 flags after the target", f"cd {root} && rm x -rf"
    yield "s1 no target", f"cd {root} && rm -rf"
    yield "s1 extra cd", f"cd {root} && cd sub && rm -rf x"
    yield "s1 env wrapper", f"cd {root} && env rm -rf x"
    yield "s1 sudo", f"cd {root} && sudo rm -rf x"
    yield "s1 absolute rm", f"cd {root} && /bin/rm -rf x"
    yield "s1 quoted root", f'cd "{root}" && rm -rf x'
    yield "s1 variable root", "cd $D && rm -rf x"
    yield "s1 tilde root", "cd ~ && rm -rf x"
    for name in ("D", "E"):
        yield f"s2 other name {name}", f'D={root}; rm -rf "${name}x"'
    yield "s2 different NAME in the rm", f'D={root}; rm -rf "$E"'
    yield "s2 different NAME braced", f'D={root}; rm -rf "${{E}}/x"'
    yield "s2 different NAME second ref", f'D={root}; rm -rf "$D" "$E"'
    yield "s2 prefix NAME", f'D={root}; rm -rf "$D1"'
    yield "s2 name suffix glued", f'D={root}; rm -rf "$D"x'
    yield "s2 single-quoted ref", f"D={root}; rm -rf '$D'"
    yield "s2 alias chain", f'D={root}; E=$D; rm -rf "$E"'
    yield "s2 dotdot after the ref", f'D={root}; rm -rf "$D"/../x'
    yield "s2 or", f'D={root} || rm -rf "$D"'
    yield "s2 pipe", f'D={root} | rm -rf "$D"'
    yield "s2 export", f'export D={root}; rm -rf "$D"'
    yield "s2 quoted value", f'D="{root}"; rm -rf "$D"'
    yield "s2 no space after the semicolon", f'D={root};rm -rf "$D"'
    for bad in ("IFS", "PATH", "path", "Path", "HOME", "CDPATH", "TMPDIR", "PWD", "OLDPWD", "ENV", "BASH_ENV", "BASHPID", "LD_PRELOAD", "LC_ALL", "_",
                "SHELLOPTS", "GLOBIGNORE", "RANDOM", "ZDOTDIR", "status", "ARGC", "GID", "pipestatus", "KEYBOARD_HACK", "HISTCHARS", "histchars",
                "TMOUT", "psvar", "OPTIND"):
        yield f"s2 special name {bad}", f'{bad}={root}; rm -rf "${bad}"'
    wt = "/w/repo/wt"
    yield "s3 other rm path", f"git worktree add {wt} && rm -rf {wt}2"
    yield "s3 parent", f"git worktree add {wt} && rm -rf /w/repo"
    yield "s3 child", f"git worktree add {wt} && rm -rf {wt}/src"
    yield "s3 relative rm for an absolute add", f"git worktree add {wt} && rm -rf wt"
    yield "s3 relative add for an absolute rm", f"git worktree add wt && rm -rf {wt}"
    yield "s3 semicolon", f"git worktree add {wt}; rm -rf {wt}"
    yield "s3 or", f"git worktree add {wt} || rm -rf {wt}"
    yield "s3 dotdot add and rm", f"git worktree add /w/x/../wt && rm -rf /w/x/../wt"
    yield "s3 dotdot in -C", f"git -C /w/x/.. worktree add {wt} && rm -rf {wt}"
    yield "s3 relative under -C", "git -C /w/repo worktree add wt && rm -rf wt"
    yield "s3 the repository itself", "git -C /w/repo worktree add /w/repo && rm -rf /w/repo"
    yield "s3 a parent of the repository", "git -C /w/repo/a/b worktree add /w/repo && rm -rf /w/repo"
    yield "s3 root", "git worktree add / && rm -rf /"
    yield "s3 one component", "git worktree add /wt && rm -rf /wt"
    yield "s3 dot", "git worktree add . && rm -rf ."
    yield "s3 option as the path", "git worktree add -f && rm -rf -f"
    yield "s3 force option", f"git worktree add -f {wt} && rm -rf {wt}"
    yield "s3 option after the path", f"git worktree add {wt} -b nb && rm -rf {wt}"
    yield "s3 remove", f"git worktree remove {wt} && rm -rf {wt}"
    yield "s3 rm first", f"rm -rf {wt} && git worktree add {wt}"
    yield "s3 git alias", f"git wt add {wt} && rm -rf {wt}"
    yield "s3 tilde", "git worktree add ~/wt/x && rm -rf ~/wt/x"
    yield "s3 variable", "git worktree add $W/x && rm -rf $W/x"


def test_every_structured_mutation_is_not_proven_and_the_floor_holds():
    count = 0
    for seed, scr, _out in worlds(30, 6):
        for why, cmd in structured_mutants(random.Random(seed), scr):
            assert not proven(cmd), (why, cmd)
            assert fires(cmd) or not v1_fires(cmd), (why, cmd)       # the floor: v2 fires wherever v1 fires
            count += 1
    assert count >= 3000


# The names a shell treats specially: zsh 5.9 `print -l ${(k)parameters}` (plain, after compinit, and with the 36 modules under /usr/lib/zsh/5.9 loaded,
# zsh/net/tcp first; zsh/deltochar does not load on this Mac) U bash 3.2 `compgen -v`, upper-cased, plus the specials those commands cannot list
# (POSTEDIT, RPROMPT, RPROMPT2: unset by default; zle widget variables; env-borne names). FROZEN: a future shell adding a name is a
# change to this tuple AND to grammar._SPECIAL, never a silent miss. zsh truncates the VALUE of KEYBOARD_HACK / HISTCHARS / histchars, so S2 proved
# a delete outside scratch (Opus grammar round 1, blocking). The list is a deny list of NAMES inside an allow-list of SPELLINGS ([A-Za-z_]\w{0,31}).
FROZEN_SPECIALS = tuple((
    "ALIASES ARGC ARGV BASH BASHOPTS BASHPID BASH_ARGC BASH_ARGV BASH_COMMAND BASH_ENV BASH_EXECUTION_STRING BASH_LINENO "
    "BASH_SOURCE BASH_SUBSHELL BASH_VERSINFO BASH_VERSION BASH_XTRACEFD BUFFER BUILTINS CDPATH CHILD_MAX COLUMNS COMMANDS "
    "COMPPOSTFUNCS COMPPREFUNCS COMP_WORDBREAKS COPROC COPROC_PID CPUTYPE CURSOR CUTBUFFER DIRSTACK DIS_ALIASES DIS_BUILTINS "
    "DIS_FUNCTIONS DIS_FUNCTIONS_SOURCE DIS_GALIASES DIS_PATCHARS DIS_RESWORDS DIS_SALIASES EGID ENV EPOCHREALTIME EPOCHSECONDS "
    "EPOCHTIME ERRNO ERRNOS EUID EXARR EXINT EXSTR FCEDIT FIGNORE FPATH FUNCFILETRACE FUNCNAME FUNCNEST FUNCSOURCETRACE FUNCSTACK "
    "FUNCTIONS FUNCTIONS_SOURCE FUNCTRACE GALIASES GID GLOBIGNORE GROUPS HISTCHARS HISTCMD HISTCONTROL HISTFILE HISTFILESIZE "
    "HISTIGNORE HISTNO HISTORY HISTORYWORDS HISTSIZE HISTTIMEFORMAT HOME HOST HOSTFILE HOSTNAME HOSTTYPE IFS IGNOREEOF INPUTRC "
    "IPREFIX ISUFFIX JOBDIRS JOBSTATES JOBTEXTS KEYBOARD_HACK KEYMAP KEYMAPS KEYS KEYS_QUEUED_COUNT KEYTIMEOUT LANG LANGINFO "
    "LASTABORTEDSEARCH LASTSEARCH LASTWIDGET LBUFFER LESS LINENO LINES LISTMAX LOGCHECK LOGNAME MACHTYPE MAIL MAILCHECK MAILPATH "
    "MANPATH MAPFILE MARK MATCH MBEGIN MEND MODULES MODULE_PATH NAMEDDIRS NULLCMD NUMERIC OLDPWD OPTARG OPTERR OPTIND OPTIONS "
    "OSTYPE PARAMETERS PATCHARS PATH PENDING PIPESTATUS POSIXLY_CORRECT POSTDISPLAY POSTEDIT PPID PREBUFFER PREDISPLAY PREFIX "
    "PROMPT PROMPT2 PROMPT3 PROMPT4 PROMPT_COMMAND PS0 PS1 PS2 PS3 PS4 PSVAR PWD QIPREFIX QISUFFIX RANDOM RBUFFER READLINE_LINE "
    "READLINE_MARK READLINE_POINT READNULLCMD REGION_ACTIVE REPLY RESWORDS RPROMPT RPROMPT2 SALIASES SAVEHIST SECONDS SHELL "
    "SHELLOPTS SHLVL SIGNALS SPROMPT SRANDOM STATUS SUFFIX SYSPARAMS TERM TERMCAP TERMINFO TIMEFMT TIMEFORMAT TMOUT TMPDIR "
    "TMPPREFIX TRY_BLOCK_ERROR TRY_BLOCK_INTERRUPT TTY TTYIDLE TZ UID USER USERDIRS USERGROUPS USERNAME VENDOR WATCH WATCHFMT "
    "WIDGETS WORDCHARS ZCURSES_ATTRS ZCURSES_COLORS ZCURSES_COLOR_PAIRS ZCURSES_KEYCODES ZCURSES_WINDOWS ZDOTDIR ZFTP_PREFS "
    "ZFTP_SESSION ZFTP_TMOUT ZFTP_VERBOSE ZLE_BRACKETED_PASTE ZLE_LINE_ABORTED ZLE_REMOVE_SUFFIX_CHARS ZLE_RPROMPT_INDENT "
    "ZLE_SPACE_SUFFIX_CHARS ZLE_STATE ZSH_ARGZERO ZSH_EVAL_CONTEXT ZSH_EXECUTION_STRING ZSH_NAME ZSH_PATCHLEVEL "
    "ZSH_SCHEDULED_EVENTS ZSH_SUBSHELL ZSH_VERSION _ _COMPAUTOS _COMPS _COMP_DUMPFILE _COMP_OPTIONS _COMP_SETUP _LASTCOMP "
    "_PATCOMPS _POSTPATCOMPS _SERVICES"
).split())


def test_the_frozen_special_names_are_the_grammars_and_sorted():
    assert grammar._SPECIAL == FROZEN_SPECIALS and list(FROZEN_SPECIALS) == sorted(set(FROZEN_SPECIALS)) and len(FROZEN_SPECIALS) >= 200
    assert all(n == n.upper() for n in FROZEN_SPECIALS)
    assert {"KEYBOARD_HACK", "HISTCHARS", "IFS", "PATH", "_"} <= set(FROZEN_SPECIALS)
    assert {"POSTEDIT", "RPROMPT", "RPROMPT2", "ZFTP_PREFS", "ZFTP_SESSION", "ZFTP_TMOUT", "ZFTP_VERBOSE"} <= set(FROZEN_SPECIALS)


def test_every_special_name_is_denied():
    """Each frozen name, as spelled, lower-cased and braced, with every VREF spelling: S2 never proves it."""
    for name in FROZEN_SPECIALS:
        for spelled in {name, name.lower()}:
            for cmd in (f'{spelled}=/tmp/w; rm -rf "${spelled}"', f"{spelled}=/tmp/w && rm -rf ${{{spelled}}}/x", f'{spelled}=/tmp/w; rm -rf "${spelled}/x"'):
                assert not proven(cmd), cmd
                assert fires(cmd) or not v1_fires(cmd), cmd       # the floor (v1 itself reads a $TMPDIR prefix as scratch)


def test_every_special_prefix_is_denied_and_the_prefixes_are_pinned():
    assert grammar._SPECIAL_PREFIX == ("BASH", "COMP", "DIS_", "DYLD_", "EPOCH", "FUNC", "HIST", "KEYBOARD", "LANG", "LC_", "LD_", "LESS",
                                       "MAIL", "MATCH", "OPT",
                                       "PROMPT", "PS0", "PS1", "PS2", "PS3", "PS4", "PS5", "PS6", "PS7", "PS8", "PS9", "REPLY", "RPS", "TERM",
                                       "UNDO_", "WATCH", "WIDGET", "WORD", "YANK", "ZFTP_", "ZLE_", "ZSH", "_COMP")
    for pre in grammar._SPECIAL_PREFIX:
        for name in (pre + "X", pre.lower() + "x", pre + "_1"):
            assert not proven(f'{name}=/tmp/w; rm -rf "${name}"'), name


# The deny set of the grammar before wave 7 (`git show 79e5ae4:ace_playbook/compile/grammar.py`, `_BAD` and `_BAD_PREFIX`), frozen: the frozen
# list replaced it, and a name it dropped would be a control regression (security review 7: FUNCNAME and SRANDOM were dropped once).
OLD_BAD = tuple(("_ IFS CDPATH PATH HOME TMPDIR PWD OLDPWD ENV SHELL SHELLOPTS GLOBIGNORE FPATH MANPATH MAILPATH MODULE_PATH ZDOTDIR UID "
                 "EUID PPID TERM USER LOGNAME LANG NULLCMD READNULLCMD POSIXLY_CORRECT RANDOM SRANDOM SECONDS LINENO OPTIND OPTARG REPLY "
                 "SHLVL HISTCMD HISTSIZE HISTFILE COLUMNS LINES FUNCNAME EPOCHSECONDS EPOCHREALTIME STATUS ARGC ARGV PIPESTATUS GID EGID "
                 "TTY USERNAME GROUPS DIRSTACK HOSTNAME HOSTTYPE MACHTYPE OSTYPE").split())
OLD_BAD_PREFIX = ("BASH", "LD_", "DYLD_", "LC_", "ZSH")
BASH_READONLY = ("BASH_ARGC", "BASH_ARGV", "BASH_LINENO", "BASH_SOURCE", "BASH_SUBSHELL", "BASH_VERSINFO", "BASH_COMMAND", "BASH_REMATCH", "BASH_ALIASES",
                 "BASH_CMDS", "BASH_ARGV0", "FUNCNAME", "GROUPS", "SRANDOM", "RANDOM", "EPOCHSECONDS", "EPOCHREALTIME", "SECONDS", "LINENO", "PPID", "UID",
                 "EUID", "SHELLOPTS", "BASHOPTS", "BASHPID", "DIRSTACK", "HISTCMD", "COMP_WORDS", "COMP_CWORD", "COMP_LINE", "PIPESTATUS", "_")


def test_every_name_the_old_deny_set_held_is_still_denied():
    for name in OLD_BAD + BASH_READONLY:
        assert name in FROZEN_SPECIALS or name.startswith(grammar._SPECIAL_PREFIX), name
        for spelled in {name, name.lower()}:
            assert not proven(f'{spelled}=/tmp/w; rm -rf "${spelled}/x"'), spelled
    for pre in OLD_BAD_PREFIX:
        assert pre in grammar._SPECIAL_PREFIX or any(pre.startswith(p) for p in grammar._SPECIAL_PREFIX), pre


def test_the_zsh_value_truncating_names_and_the_name_spelling_rule_of_s2():
    for name in (*NAMES, "WORK", "Dir", "x1", "N" * 32, "PSEUDO", "T", "tmp_dir"):
        assert proven(f'{name}=/tmp/w; rm -rf "${name}"'), name
        assert proven(f"{name}=/tmp/w && rm -rf ${{{name}}}/x"), name
    for name in ("KEYBOARD_HACK", "HISTCHARS", "histchars", "N" * 33, "IFS", "path", "cdpath", "HOME", "BASH_XYZ", "LD_LIBRARY_PATH", "LC_ALL", "ZSH_X",
                 "RANDOM", "status", "ARGV", "UID", "Path", "psvar", "OPTIND", "LANG", "MAIL", "TMOUT", "SAVEHIST", "PS1", "_"):
        assert not proven(f'{name}=/tmp/w; rm -rf "${name}/x"'), name


# ------------------------------------------------------------------ the oracle as one function: used by the real grammar and by the mutants
def check(fn) -> list:
    """Every killer world for the named mutants, run through `fn(command, SCRATCH)`; the real grammar returns []."""
    bad = []

    def want(cmd, expected, why, scr=d.SCRATCH):
        try:
            got = fn(cmd, scr)
        except Exception:                                                              # noqa: BLE001  a mutant that lets it escape
            got = "raised"
        if got is not expected:
            bad.append(why)

    for seed, scr, out in worlds(60, 7):
        for root in (scr, out):
            r = random.Random(seed)
            for cmd, oracle in shapes(r, root)[:2]:
                want(cmd, oracle, "oracle")
    for cmd in ("cd /tmp/w && rm -rf ./x\n", "D=/tmp/w; rm -rf \"$D\"\n", "git worktree add /w/a/wt && rm -rf /w/a/wt\n"):
        want(cmd, False, "trailing newline")                                              # the `\Z` mutant
    want("git worktree add /w/x/../wt && rm -rf /w/x/../wt", False, "s3 dotdot")           # the `..` check
    want("git -C /w/x/.. worktree add /w/y/wt && rm -rf /w/y/wt", False, "s3 dotdot -C")
    want("cd /tmp/w/.. && rm -rf x", False, "s1 dotdot")                                  # the component class of the absolute root
    want("cd /srv/app/node_modules && rm -rf x", False, "s1 joined target")               # the SCRATCH check on the JOINED target
    want('D=/w/x/build; rm -rf "$D/x"', False, "s2 joined target")
    want('D=/w/x/build; rm -rf "$D" "$D/x"', False, "s2 second target")
    want("git worktree add /w/wt && rm -rf /w/other", False, "s3 equal paths")             # the rm target equals the add path
    want("git worktree add /w/wt && rm -rf /w", False, "s3 parent")
    want("git -C /w/repo/a worktree add /w/repo && rm -rf /w/repo", False, "s3 repository parent")     # never the repo or a parent of it
    want("git -C /w/repo worktree add /w/repo/wt && rm -rf /w/repo/wt", True, "s3 repository child")
    for cmd in ("git worktree add // && rm -rf //", "git worktree add //x && rm -rf //x", "git -C //w/repo worktree add /w/a/wt && rm -rf /w/a/wt"):
        want(cmd, False, "s3 double slash")                                                # normpath keeps a leading `//` (Opus grammar round 3)
    want("git worktree add /tmp/x/wt && rm -rf /tmp/x/wt", True, "s3 scratch path")
    want('D=/tmp/w; rm -rf "$E"', False, "s2 NAME")                                       # the NAME equality
    want('D=/tmp/w; rm -rf "${E}/x"', False, "s2 NAME braced")
    want("cd /var && rm -rf folders/x", False, "s1 cwd")                                  # S1's OWN check of the cd path (the join alone looks scratch)
    want("cd /usr/local && rm -rf ./x", False, "s1 cwd two")
    want('D=/tmp/w&& rm -rf "$D"', False, "s2 spelling")                                   # one or more spaces before `&&`, never none
    for nm in ("KEYBOARD_HACK", "HISTCHARS", "histchars"):                                # zsh cuts the value: the delete lands outside scratch
        want(f'{nm}=/tmp/w; rm -rf "${nm}/x"', False, "s2 truncating " + nm)
    for nm in ("LC_FOO", "ZSH_FOO", "HISTFOO", "PS7", "BASHFOO", "MAILFOO", "COMPFOO", "KEYBOARDX", "WORDX", "ZFTP_FOO"):
        want(f'{nm}=/tmp/w; rm -rf "${nm}"', False, "s2 prefix " + nm)
    for nm in ("IFS", "PATH", "SHELL", "UID", "status", "path", "SRANDOM",
               "POSTEDIT", "RPROMPT", "RPROMPT2", "ZFTP_PREFS", "ZFTP_TMOUT"):                  # names only the frozen tuple holds (no prefix covers them)
        want(f'{nm}=/tmp/w; rm -rf "${nm}"', False, "s2 special " + nm)
    want(f'{"N" * 33}=/tmp/w; rm -rf "${"N" * 33}"', False, "s2 name length")
    want(f'{"N" * 32}=/tmp/w; rm -rf "${"N" * 32}"', True, "s2 name length ok")
    class Boom:
        def search(self, _p):
            raise RuntimeError("the scratch rule cannot read it")

    for scr in (object(), Boom()):                                                         # the fail-closed `except` (Opus grammar round 2, important)
        for cmd in ("cd /tmp/w && rm -rf ./x", 'D=/tmp/w; rm -rf "$D"'):
            want(cmd, False, "raising scratch rule is not a proof", scr)
    for odd in (None, 5, b"cd /tmp/w && rm -rf ./x", [], object()):                       # a non-str command is not a proof
        want(odd, False, "non-str command")
    want("cd /tmp/w && rm -rf ./x", True, "s1 plain")
    want('D=/tmp/w; rm -rf "$D"', True, "s2 plain")
    want("git worktree add /w/wt && rm -rf /w/wt", True, "s3 plain")
    return bad


def mutant(*pairs: str):
    """`mutant(old, new[, old, new ...])`: the grammar source with every (old -> new) pair applied, loaded as a module."""
    src = Path(grammar.__file__).read_text()
    for old, new in zip(pairs[::2], pairs[1::2]):
        assert src.count(old) >= 1, old
        src = src.replace(old, new)
    mod = types.ModuleType("ace_playbook.compile.grammar_mutant")
    mod.__package__ = "ace_playbook.compile"
    exec(compile(src, "grammar_mutant.py", "exec"), mod.__dict__)
    return mod.proven_scratch


MUTANTS = {
    "anchor-Z-becomes-dollar": ('\\Z")', '$")'),
    "s3-dotdot-check-removed": ('any(".." in p.split("/") for p in (wt, rm, c or "")) or ', ""),
    "s1-joined-target-check-removed": ('and _dir(posixpath.join(m["scr"], t), scratch) for t', "and True for t"),
    "s2-joined-target-check-removed": ('if rest and not _dir(posixpath.join(m["scr"], rest[1:]), scratch):', "if False:"),
    "s3-rm-equals-add-check-removed": (" or wn != posixpath.normpath(rm)", ""),
    "s3-repo-parent-check-removed": ('if c and (posixpath.normpath(c) + "/").startswith(wn.rstrip("/") + "/"):', "if False:"),
    "s2-name-equality-removed": ("(?P=n)", "[A-Za-z_][A-Za-z0-9_]*"),
    "dot-free-component-class-removed": ('_COMP = r"[A-Za-z0-9_-][A-Za-z0-9._-]*"', '_COMP = r"[A-Za-z0-9._-]+"'),
    "s1-own-scratch-check-of-the-cd-path-removed": ('return _dir(m["scr"], scratch) and all(', "return all("),
    "s2-and-spelling-space-optional": ("(?:;| +&&)", "(?:;| *&&)"),
    "s2-special-names-removed": ("name in _SPECIAL or ", ""),
    "s2-special-prefixes-removed": ("name.startswith(_SPECIAL_PREFIX) or ", ""),
    "s2-name-length-unbounded": ("[A-Za-z0-9_]{{0,31}})=", "[A-Za-z0-9_]*)="),
    "s2-name-compared-case-sensitively": ('name = m["n"].upper()', 'name = m["n"]'),
    "except-fails-open": ("is not a proof\n        return False", "is not a proof\n        return True"),         # grammar.py: the fail-closed branch
    "except-narrowed-so-errors-escape": ("except Exception:", "except ZeroDivisionError:"),
    "isinstance-check-and-except-removed": ("        if not isinstance(command, str):\n            return False\n", "",
                                            "except Exception:", "except ZeroDivisionError:"),
    "s2-special-prefix-zftp-removed": ('"YANK", "ZFTP_", "ZLE_"', '"YANK", "ZLE_"'),
    "s3-double-slash-check-removed": ('if any(p.startswith("//") for p in (wt, rm, c or "")):', "if False:"),   # Opus grammar round 3, important
}
DETECTOR_MUTANTS = {"truncated-guard-removed": ("not tu.truncated and ", "")}      # detectors.py:83 (Opus grammar round 1, important)


def test_the_real_grammar_passes_every_killer_world():
    assert check(grammar.proven_scratch) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_each_mutant_of_the_grammar_fails_a_test(name):
    assert check(mutant(*MUTANTS[name])), name


def test_the_mutant_loader_changes_nothing_when_the_edit_is_the_identity():
    assert check(mutant("(?P=n)", "(?P=n)")) == []


def test_the_isinstance_check_alone_is_an_equivalent_mutant_of_the_except():
    """With the `except` in place a non-str command raises inside the `try` (`re.match` on bytes, None, a list) and is caught: the check is a
    guard in depth, not a behaviour, so no test can tell it away. Removing it AND the `except` is killed above."""
    assert check(mutant("        if not isinstance(command, str):\n            return False\n", "")) == []


def detector_mutant(old: str, new: str):
    src = Path(d.__file__).read_text()
    assert src.count(old) == 1, old
    name = "ace_playbook.compile.detectors_mutant"
    mod = types.ModuleType(name)
    mod.__package__ = "ace_playbook.compile"
    sys.modules[name] = mod                                            # @dataclass looks its module up here
    try:
        exec(compile(src.replace(old, new), "detectors_mutant.py", "exec"), mod.__dict__)
    finally:
        sys.modules.pop(name, None)
    return mod


def cut_s1_fires(mod) -> bool:
    """A valid S1 shape longer than MAX_CMD: the tail was cut, so the command is never proven and v1's literal rule fires."""
    e = event_from_hook(cs.pre_payload("cd /tmp/work && rm -rf " + " ".join(["a"] * 40000)), probe=lambda _p: False)
    assert e.tool_uses[0].truncated
    return mod._rm_hits(e, True)[0] > 0


@pytest.mark.parametrize("name", sorted(DETECTOR_MUTANTS))
def test_each_mutant_of_the_detector_fails_a_test(name):
    assert cut_s1_fires(d) is True and cut_s1_fires(detector_mutant("not tu.truncated and ", "not tu.truncated and ")) is True
    assert cut_s1_fires(detector_mutant(*DETECTOR_MUTANTS[name])) is False, name


# ------------------------------------------------------------------ the V1 floor on constructs the old resolver trusted
WRAPS = {   # name -> wrapper(setter, join, delete): constructs that used to be trusted; none is a grammar shape, so v2 must fire wherever v1 does
    "negated": lambda s_, j, dl: f"! {s_}{j}{dl}", "or": lambda s_, j, dl: f"{s_} || {dl}", "or-first": lambda s_, j, dl: f"false || {s_}{j}{dl}",
    "semicolon": lambda s_, j, dl: f"{s_}; {dl}", "newline": lambda s_, j, dl: f"{s_}\n{dl}", "if": lambda s_, j, dl: f"if true; then {s_}{j}{dl}; fi",
    "while": lambda s_, j, dl: f"while true; do {s_}{j}{dl}; done", "group": lambda s_, j, dl: f"{{ {s_}{j}{dl}; }}",
    "function": lambda s_, j, dl: f"f() {{ {s_}{j}{dl}; }}; f", "pipe": lambda s_, j, dl: f"{s_} | cat{j}{dl}",
    "background": lambda s_, j, dl: f"{s_} &\n{dl}", "subshell": lambda s_, j, dl: f"({s_}){j}{dl}", "redirect": lambda s_, j, dl: f"{s_} >/dev/null{j}{dl}",
    "env": lambda s_, j, dl: f"env {s_}{j}{dl}", "sudo": lambda s_, j, dl: f"sudo {s_}{j}{dl}", "time": lambda s_, j, dl: f"time {s_}{j}{dl}",
    "ifs": lambda s_, j, dl: f"IFS=:; {s_}{j}{dl}", "trap": lambda s_, j, dl: f"trap 'x' DEBUG; {s_}{j}{dl}",
    "alias": lambda s_, j, dl: f"alias a=b; {s_}{j}{dl}",
    "eval-unreadable": lambda s_, j, dl: f'{s_} && eval "$CMD"{j}{dl}', "arith": lambda s_, j, dl: f"{s_}{j}(( D=1 )){j}{dl}",
    "unknown-word": lambda s_, j, dl: f"{s_}{j}$CD x{j}{dl}", "unknown-fn": lambda s_, j, dl: f"{s_}{j}f{j}{dl}",
    "true-between": lambda s_, j, dl: f"{s_}{j}true{j}{dl}", "source": lambda s_, j, dl: f"{s_}{j}source x{j}{dl}",
    "unset": lambda s_, j, dl: f"{s_}{j}unset D{j}{dl}", "env-C": lambda s_, j, dl: f"{s_}{j}env -C /srv/x {dl}",
    "newline-in-quotes": lambda s_, j, dl: f': "\n" {s_}{j}{dl}', "glob-qualifier": lambda s_, j, dl: f"{s_}{j}{dl}(P:x:)",
}
SKELETONS = {"cd": ("cd {r}", " && ", "rm -rf ./*"), "var": ("D={r}", "; ", 'rm -rf "$D"'), "varand": ("D={r}", " && ", 'rm -rf "${{D}}/x"'),
             "export": ("export D={r}", " && ", "rm -rf $D"), "wt": ("git worktree add {r}/wt b", " && ", "rm -rf {r}/wt")}


def test_the_floor_holds_for_every_wrapped_skeleton():
    count = 0
    for k, (setter, join, delete) in SKELETONS.items():
        for root in ("/tmp/w", "/srv/data"):
            s_, dl = setter.format(r=root), delete.format(r=root)
            for name, wrap in WRAPS.items():
                for lead in ("", "echo start; ", "echo start\n", "true && "):
                    cmd = lead + wrap(s_, join, dl)
                    shape = (k, name, lead) in (("var", "semicolon", ""), ("varand", "semicolon", "")) and root == "/tmp/w"   # S2 rebuilt by itself
                    assert proven(cmd) is shape, (k, name, cmd)
                    assert fires(cmd) or not v1_fires(cmd) or shape, (k, name, cmd)       # the floor: proven = the generator built a shape
                    count += 1
    assert count >= 400


def test_the_plain_skeletons_are_where_v2_fires_less_than_v1():
    """The three shapes (and nothing else the old resolver trusted besides them) are the only commands v1 fires on and v2 does not."""
    for cmd, expect_proven in (("cd /tmp/w && rm -rf ./*", True), ('D=/tmp/w; rm -rf "$D"', True), ("D=/tmp/w && rm -rf $D/x", True),
                               ("git worktree add /w/wt && rm -rf /w/wt", True), ("export D=/tmp/w && rm -rf $D", False)):
        assert v1_fires(cmd) and fires(cmd) is not expect_proven and proven(cmd) is expect_proven, cmd


FLOOR_FRAGMENTS = ["rm -rf ", "rm -r -f ", "rm -fr -- ", " && ", " || ", "; ", "\n", " | ", " & ", "/srv/", "/w/", "$X", "${X}", '"$X"', "~/", "..",
                   "./*", "*", "'", '"', "\\", "bash -c '", "eval ", "xargs ", "find ", " -delete", "(", ")", "`", "$(", "!", "if ", "then ",
                   "fi", "{ ", "} ", "cat ", "echo ", "ls -d ", "-- ", "a", "b", "x y", "cd ", "D=", "export D=", "time ", "env "]


def test_the_floor_holds_on_random_command_soup_without_scratch_text():
    rng = random.Random(SEED + 11)
    fired = 0
    for _ in range(N * 2):
        text = "".join(rng.choice(FLOOR_FRAGMENTS) for _ in range(rng.randrange(1, 14)))
        assert not proven(text), text
        if v1_fires(text):
            fired += 1
            assert fires(text), text
    assert fired >= N // 8, "the soup must reach rm -rf often enough to mean something"


def test_the_weak_rm_rf_detector_is_unchanged_by_v2_for_plain_deletes():
    """v2 only changes WHICH deletes are non-scratch; `rm_rf` still counts every recursive force delete as Step 0 did."""
    for root in SCR_ROOTS + OUT_ROOTS:
        for cmd in (f"rm -rf {root}/a", f"cd {root} && rm -rf ./*"):
            ev = event_from_hook(cs.pre_payload(cmd), probe=lambda _p: False)
            out, _s, errors = cat.evaluate(ev, None, names=["rm_rf"], strict=False)
            assert errors == [] and [f.count for f in out] == [1], cmd


# ------------------------------------------------------------------ fuzz: never raises
ALPHABET = "abcdefgxyz /.*~-_=:,{}()[]<>|&;$\\\"'`!#%\n\t0123456789"
FRAGMENTS = ["rm -rf ", "rm -r -f ", "cd ", "D=", "$D", "${D}", "\"$D\"", "bash -c '", "xargs ", "git worktree add ", "git -C ", " && ", " || ", "; ",
             " | ", "\n", "(", ")", "`", "$(", "<<EOF\n", "/tmp/", "/private/tmp/claude-501/", "$TMPDIR/", "~", "..", "./*", "'", "\"", "\\", "\x00"]


def garbage(rng: random.Random) -> str:
    parts = []
    while sum(map(len, parts)) < rng.randrange(0, 200):
        parts.append(rng.choice(FRAGMENTS) if rng.random() < 0.5 else "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(1, 8))))
    return "".join(parts)[:200]


def test_the_grammar_never_raises_on_1000_garbage_strings_and_the_detector_stays_total():
    rng = random.Random(SEED + 99)
    for _ in range(1000):
        text = garbage(rng)
        assert proven(text) in (True, False) and proven(text) is proven(text)
        ev = event_from_hook(cs.pre_payload(text), probe=lambda _p: False)
        _out, _state, errors = cat.evaluate(ev, None, names=[NAME, "rm_rf"], strict=True)
        assert errors == []
    for odd in (None, 5, b"cd /tmp/w && rm -rf x", [], {}, "", "\x00", "\u202e", "cd /tmp/w && rm -rf x\u0301"):
        assert grammar.proven_scratch(odd, d.SCRATCH) is False
    # the fail-closed `except`: a scratch rule that cannot be read (no `.search`, or one that raises) is not a proof (Opus grammar round 2, important)
    assert grammar.proven_scratch("cd /tmp/w && rm -rf ./x", object()) is False
    assert grammar.proven_scratch('D=/tmp/w; rm -rf "$D"', object()) is False
    boom = types.SimpleNamespace(search=lambda _p: (_ for _ in ()).throw(RuntimeError("boom")))
    assert grammar.proven_scratch("cd /tmp/w && rm -rf ./x", boom) is False and grammar.proven_scratch('D=/tmp/w; rm -rf "$D"', boom) is False


def test_the_grammar_default_scratch_is_the_detectors_own_rule():
    assert grammar.proven_scratch("cd /tmp/w && rm -rf ./x") is True and grammar.proven_scratch("cd /srv/w && rm -rf ./x") is False
    assert not hasattr(grammar, "SCRATCH") and "SCRATCH =" not in Path(grammar.__file__).read_text()


# ------------------------------------------------------------------ the 64 KB gate: <= 50 ms locally, asserted at 3x for a slow CI runner
CI_FACTOR_MS = 150
_ENV = "".join(f"D{i}=/tmp/a{i}; " for i in range(64))
TIMING_SHAPES = {
    "dollar-paren-pairs": _ENV + "$(a) " * 13000, "paren-pairs": _ENV + "(a)" * 21000, "backtick-pairs": _ENV + "`a` " * 16000,
    "sixty-four-k-parens": _ENV + "(" * 25000 + "rm -rf /x" + ")" * 25000, "open-parens": _ENV + "(" * 32000,
    "dollar-paren-pairs-then-a-delete": _ENV + "$(a) " * 12000 + "rm -rf ./x", "unknown-words": _ENV + "f g h; " * 9000,
    "allow-listed-words": _ENV + "echo a; " * 8000 + "rm -rf /tmp/w/x",
    "worktree-adds-then-many-targets": "git worktree add /a/b/c1 && " * 1000 + "rm -rf " + " ".join(f"/a/b/c{i}/x" for i in range(5000)),
    "negations": "! x; " * 13000,
    "s1-many-rels": "cd /tmp/w && rm -rf " + "a " * 32000, "s1-one-huge-rel": "cd /tmp/w && rm -rf " + "a" * 64000,
    "s1-huge-rel-bad-tail": "cd /tmp/w && rm -rf " + "a/" * 30000 + "!", "s1-star-runs": "cd /tmp/w && rm -rf " + "a*" * 30000 + "\n",
    "s1-many-components": "cd " + "/a" * 30000 + " && rm -rf x", "s2-many-refs": 'D=/tmp/w; rm -rf ' + '"$D/x" ' * 10000,
    "s2-many-refs-bad-name": 'D=/tmp/w; rm -rf ' + '"$D/x" ' * 10000 + '"$E"', "s3-long-lit": "git worktree add " + "a" * 30000 + " && rm -rf " + "a" * 30000,
    "dots-and-dashes": "cd /tmp/w && rm -rf " + "-." * 30000, "spaces": "cd /tmp/w && rm -rf " + " " * 60000 + "x",
    "quotes": 'D=/tmp/w; rm -rf "$D' + '"' * 60000,
}


@pytest.mark.parametrize("name", sorted(TIMING_SHAPES))
def test_the_64kb_budget_holds(name):
    cmd = TIMING_SHAPES[name][:65000]
    best, best_g = 1e9, 1e9
    for _ in range(3):
        ev = event_from_hook(cs.pre_payload(cmd), probe=lambda _p: False)       # fresh every run: a reused event serves cached segments
        t0 = time.perf_counter()
        _out, _state, errors = cat.evaluate(ev, None, names=[NAME, "rm_rf"], strict=True)
        best = min(best, (time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        proven(cmd)
        best_g = min(best_g, (time.perf_counter() - t0) * 1000)
    assert errors == []
    print(f"timing {name}: evaluate best {best:.1f} ms, grammar alone {best_g:.2f} ms")
    assert best <= CI_FACTOR_MS and best_g <= 15, (name, best, best_g)
