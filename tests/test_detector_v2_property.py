"""Property tests for `rm_rf_nonscratch` v2 (compile/resolve.py): generated commands against an oracle the generator knows.

Every world has ONE effective deletion root: the generator picks the root (a scratch root or one outside scratch), then
spells the delete one of many ways (plain, `./*` after `cd`, `$VAR`, `${VAR}/sub`, quoted, wrapped in sudo/env/nice, `--`,
split flags, `bash -c`/`sh -c`/`eval`, `ls | xargs`, `find -delete`, one or two `cd`s, `&&`/`;`/newline joins). The oracle is
"fires iff the root is outside scratch, or the world relies on something v2 does not trust" (a `cd` not joined by `&&`, a value with
`$`, an assignment before `||` or after `true &&`, an `eval` text with `$`); build() reports these as `forced`. The second half of the
file is the V1-FLOOR property: v2 may stop firing only on a PROVEN construct, so for every mutation of a trusted skeleton (negation,
`||`, compound bodies, functions, pipes, wrappers, non-literal values, IFS, trap, unreadable eval, arithmetic, rebinding builtins ...)
`v2_fires or not v1_fires`, where `v1_fires` is a frozen copy of v1's literal rule kept below. Seeded
(`random.Random(1234)`, override with PYTEST_SEED); stdlib only; the commands are DATA fed to the detector, nothing runs.
"""

import os
import posixpath
import random
import re

import pytest

from ace_playbook.compile import catalogue as cat
from ace_playbook.compile import detectors as d
from ace_playbook.compile import resolve, resolve_pre, shell
from ace_playbook.compile.events import event_from_hook
from ace_playbook.fixtures import fakes

from . import compile_support as cs

SEED = int(os.environ.get("PYTEST_SEED", "1234"))
N = 400
NAME = "rm_rf_nonscratch"
SCRATCH_ROOTS = ["/tmp/w", "/private/tmp/claude-501/proj/sess/scratchpad", "/var/folders/ab/cd/T/x", "$TMPDIR/w", "/tmp/a/b"]
OUTSIDE_ROOTS = [fakes.HOME_PREFIX + "FAKEuser/proj", "/srv/data", "/opt/app/x", "$HOME/proj", "~/proj", "$UNSET/x", "/w/src"]
FLAGS = ["-rf", "-fr", "-Rf", "-r -f", "-f -r", "--recursive --force", "-rf --", "-r -f --"]
WRAP = ["", "", "sudo ", "env ", "nice -n 5 ", "command ", "sudo -u root "]
JOIN = [" && ", "; ", "\n", " || "]
LEAD = ["", "", "echo start; ", "echo start\n", "true && "]
SUB = ["", "", "/x1", "/pkg/y"]


def fires(cmd) -> bool:
    cmd = cmd[0] if isinstance(cmd, tuple) else cmd
    ev = event_from_hook(cs.pre_payload(cmd), probe=lambda _p: False)
    out, _state, errors = cat.evaluate(ev, None, names=[NAME], strict=False)
    assert errors == []
    return any(f.detector == NAME and f.count > 0 for f in out)


def build(seed: int, root: str) -> tuple[str, bool]:
    """(command, forced): a generated command whose effective deletion root is `root` (same seed -> same shape, only the root
    differs). `forced` = the world is unprovable by design (a `cd` joined by something other than `&&`) and must fire."""
    r = random.Random(seed)
    rm = lambda target: f"{r.choice(WRAP)}rm {r.choice(FLAGS)} {target}"   # noqa: E731
    join, sub = r.choice(JOIN), r.choice(SUB)
    rel = r.choice(["./*", "*", ".", "./"]) if not sub else "."
    kind = r.choice(["plain", "quoted", "cd", "cd2", "cd_sub", "var", "brace", "export", "pipe", "find", "findexec", "cdls"])
    other = r.choice(SCRATCH_ROOTS + OUTSIDE_ROOTS)
    var_kind = kind in ("var", "brace", "export")
    forced = (kind in ("cd", "cd2", "cd_sub", "cdls") and (join.strip() != "&&" or "$" in root)) or (var_kind and join.strip() == "||")
    if kind in ("var", "brace", "export") and "$" in root:      # a binding with an unresolved value is dropped: same class, plain spelling
        root = "/tmp/vars" if d.SCRATCH.search(root) else "/srv/vars"
    if kind == "plain":
        cmd = rm(root + sub)
    elif kind == "quoted":
        q = r.choice(['"', "'"])
        cmd = rm(f"{q}{root}{sub}{q}")
    elif kind == "cd":
        cmd = f"cd {root}{sub}{join}{rm(rel)}"
    elif kind == "cd2":                       # an absolute cd overrides whatever came first
        cmd = f"cd {other}{join}cd {root}{sub}{join}{rm(rel)}"
    elif kind == "cd_sub":                    # a relative cd goes below the root
        cmd = f"cd {root}{join}cd sub{join}{rm(rel)}"
    elif kind == "var":
        cmd = f"D={root}{join}" + rm(r.choice(['$D', '"$D"', "$D/x", '"$D/x"']))
    elif kind == "brace":
        cmd = f"D={root}{join}" + rm(r.choice(['${D}', '"${D}"', '"${D}/x"', '${D}/x']))
    elif kind == "export":
        cmd = f"export D={root}{join}" + rm("$D")
    elif kind == "pipe":
        cmd = f"ls -d {root}/* | xargs rm -rf"
    elif kind == "find":
        cmd = f"find {root} -type d -delete"
    elif kind == "findexec":
        cmd = f"find {root} -exec rm -rf {{}} +"
    else:
        cmd = f"cd {root}{join}ls -d * | xargs rm -rf"
    wrap = None
    if kind in ("plain", "quoted", "cd", "cd2", "cd_sub", "var", "brace", "export") and r.random() < 0.3:
        # `bash -c '...'`, `sh -c '...'` and `eval '...'` hold the command in single quotes (the quoting the generator avoids inside)
        inner = cmd.replace("'", "")
        wrap = r.choice(["bash -c '%s'", "sh -c '%s'", "eval '%s'", "bash -lc '%s'"])
        cmd, forced = wrap % inner, forced or (wrap.startswith("eval") and var_kind)     # an eval text holding `$` is not read
    lead = r.choice(LEAD)
    # a binding made after `true &&` may not have run: it dies with its chain, so a delete after `;` or a newline sees none
    return lead + cmd, forced or (var_kind and lead == "true && " and join.strip() != "&&" and wrap is None)


def worlds(n: int, salt: int):
    rng = random.Random(SEED * 7 + salt)
    for _ in range(n):
        yield rng.randrange(1 << 30), rng.choice(SCRATCH_ROOTS), rng.choice(OUTSIDE_ROOTS)


def test_generated_commands_fire_exactly_when_the_root_is_outside_scratch():
    seen = {True: 0, False: 0}
    for seed, scratch, outside in worlds(N, 1):
        for root, scratchy in ((outside, False), (scratch, True)):
            cmd, forced = build(seed, root)
            oracle = forced or not scratchy
            assert fires(cmd) is oracle, (seed, root, cmd)
            seen[oracle] += 1
    assert seen[True] >= N and seen[False] >= N // 2


def test_swapping_the_root_of_a_fire_world_to_scratch_gives_a_twin_that_does_not_fire():
    for seed, scratch, outside in worlds(N, 2):
        (fire, forced), (twin, forced_twin) = build(seed, outside), build(seed, scratch)
        assert fires(fire) and (forced or forced_twin or not fires(twin)), (seed, fire, twin)


def test_the_world_grammar_covers_every_shape_and_wrapper():
    shapes = set()
    for seed, scratch, _outside in worlds(N, 3):
        cmd = build(seed, scratch)[0]
        shapes |= {s for s in ("bash -c", "sh -c", "eval", "xargs", "find", "export", "cd ", "${D}", " -- ", "sudo", "nice", "\n")
                   if s in cmd}
    assert shapes == {"bash -c", "sh -c", "eval", "xargs", "find", "export", "cd ", "${D}", " -- ", "sudo", "nice", "\n"}


def test_the_weak_rm_rf_detector_is_unchanged_by_v2_for_plain_deletes():
    """v2 only changes WHICH deletes are non-scratch; `rm_rf` still counts every recursive force delete as Step 0 did."""
    for root in SCRATCH_ROOTS + OUTSIDE_ROOTS:
        ev = event_from_hook(cs.pre_payload(f"rm -rf {root}/a"), probe=lambda _p: False)
        out, _s, errors = cat.evaluate(ev, None, names=["rm_rf"], strict=False)
        assert errors == [] and [f.count for f in out] == [1], root


def canon(cmd: str):
    return resolve.canonical_targets(shell.command_stream(cmd), d.SCRATCH)


def test_the_canonicalizer_is_idempotent_on_generated_commands():
    """A resolved target, written back as a plain `rm -rf <target>`, resolves to itself with the same verdict."""
    checked = 0
    for seed, scratch, outside in worlds(N, 4):
        for root in (scratch, outside):
            for group in canon(build(seed, root)[0]):
                for path, verdict in group:
                    again = canon(f"rm -rf {path}")
                    assert again == [[(path, verdict)]], (seed, root, path, again)
                    checked += 1
    assert checked >= N


def test_the_canonicalizer_is_pure_it_gives_the_same_answer_twice():
    for seed, scratch, outside in worlds(N, 5):
        cmd = build(seed, outside)[0]
        assert canon(cmd) == canon(cmd) != []


ALPHABET = "abcdefgxyz /.*~-_=:,{}()[]<>|&;$\\\"'`!#%\n\t0123456789"
FRAGMENTS = ["rm -rf ", "rm -r -f ", "cd ", "pushd ", "popd", "D=", "export D=", "$D", "${D}", "\"$D\"", "bash -c '", "sh -c \"", "eval ",
             "xargs ", "find ", " -delete", " -exec rm -rf {} +", "git worktree add ", "git -C ", " && ", " || ", "; ", " | ", "\n", "(", ")",
             "`", "$(", "<<EOF\n", "\nEOF\n", "/tmp/", "/private/tmp/claude-501/", "$TMPDIR/", "~", "..", "./*", "'", "\"", "\\", "\x00"]


def garbage(rng: random.Random) -> str:
    parts = []
    while sum(map(len, parts)) < rng.randrange(0, 200):
        parts.append(rng.choice(FRAGMENTS) if rng.random() < 0.5 else "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(1, 8))))
    return "".join(parts)[:200]


def test_the_canonicalizer_never_raises_on_printable_garbage():
    rng = random.Random(SEED + 99)
    nonempty = 0
    for _ in range(1000):
        text = garbage(rng)
        events, quotes = shell.command_stream(text)
        assert all(isinstance(sep, str) and isinstance(toks, list) for sep, toks in events) and isinstance(quotes, list)
        groups = canon(text)
        nonempty += bool(groups)
        assert all(isinstance(p, str) and isinstance(v, bool) for g in groups for p, v in g)
        assert canon(text) == groups
        ev = event_from_hook(cs.pre_payload(text), probe=lambda _p: False)
        _out, _state, errors = cat.evaluate(ev, None, names=[NAME, "rm_rf"], strict=True)
        assert errors == []
    assert nonempty >= 10, "the garbage grammar must reach the delete path now and then"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_other_seeds_agree_with_the_oracle(seed):
    rng = random.Random(seed)
    for _ in range(100):
        s, scratch, outside = rng.randrange(1 << 30), rng.choice(SCRATCH_ROOTS), rng.choice(OUTSIDE_ROOTS)
        (fire, forced), (twin, forced_twin) = build(s, outside), build(s, scratch)
        assert fires(fire) and (forced or forced_twin or not fires(twin)), (seed, s)


# ------------------------------------------------------------------ the v1 floor
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


SCR, OUT = "/tmp/w", "/srv/data"


def skeletons(r: str) -> dict:
    """name -> (setter, join, delete): the constructs v2 trusts, with `r` as the root they set."""
    return {"cd": (f"cd {r}", " && ", "rm -rf ./*"), "cd2": (f"cd {r} && cd sub", " && ", "rm -rf ./*"),
            "var": (f"D={r}", "; ", 'rm -rf "$D"'), "varand": (f"D={r}", " && ", 'rm -rf "${D}/x"'),
            "export": (f"export D={r}", " && ", "rm -rf $D"), "wt": (f"git worktree add {r}/wt b", " && ", f"rm -rf {r}/wt")}


MUTANTS = {   # name -> (setter, join, delete, skeleton name) -> command; every one breaks a rule of resolve_pre.py
    "negated": lambda s_, j, dl, k: f"! {s_}{j}{dl}", "or": lambda s_, j, dl, k: f"{s_} || {dl}",
    "or-first": lambda s_, j, dl, k: f"false || {s_}{j}{dl}", "neg-first": lambda s_, j, dl, k: f"! false && {s_}{j}{dl}",
    "semicolon": lambda s_, j, dl, k: f"{s_}; {dl}" if k in ("cd", "cd2", "wt") else f"false && {s_}; {dl}",
    "newline": lambda s_, j, dl, k: f"{s_}\n{dl}" if k in ("cd", "cd2", "wt") else f"false && {s_}\n{dl}",
    "if": lambda s_, j, dl, k: f"if true; then {s_}{j}{dl}; fi", "while": lambda s_, j, dl, k: f"while true; do {s_}{j}{dl}; done",
    "for": lambda s_, j, dl, k: f"for x in a; do {s_}{j}{dl}; done", "case": lambda s_, j, dl, k: f"case x in x) {s_}{j}{dl};; esac",
    "group": lambda s_, j, dl, k: f"{{ {s_}{j}{dl}; }}", "function": lambda s_, j, dl, k: f"f() {{ {s_}{j}{dl}; }}; f",
    "pipe": lambda s_, j, dl, k: f"{s_} | cat{j}{dl}", "background": lambda s_, j, dl, k: f"{s_} &\n{dl}",
    "subshell": lambda s_, j, dl, k: f"({s_}){j}{dl}", "redirect": lambda s_, j, dl, k: f"{s_} >/dev/null{j}{dl}",
    "env": lambda s_, j, dl, k: f"env {s_}{j}{dl}", "sudo": lambda s_, j, dl, k: f"sudo {s_}{j}{dl}",
    "time": lambda s_, j, dl, k: f"time {s_}{j}{dl}", "command": lambda s_, j, dl, k: f"command {s_}{j}{dl}",
    "builtin": lambda s_, j, dl, k: f"builtin {s_}{j}{dl}", "ifs": lambda s_, j, dl, k: f"IFS=:; {s_}{j}{dl}",
    "trap": lambda s_, j, dl, k: f"trap 'x' DEBUG; {s_}{j}{dl}", "alias": lambda s_, j, dl, k: f"alias a=b; {s_}{j}{dl}",
    "unreadable-eval": lambda s_, j, dl, k: f'{s_} && eval "$CMD"{j}{dl}', "eval-name": lambda s_, j, dl, k: f'{s_} && eval "f"{j}{dl}',
    "arithmetic": lambda s_, j, dl, k: f"{s_}{j}(( D=1 )){j}{dl}", "arith-subst": lambda s_, j, dl, k: f"{s_}{j}: $(( D=1 )){j}{dl}",
}
BINDINGS, DIRS = ("var", "varand", "export"), ("cd", "cd2")
APPLIES = {"subshell": BINDINGS + DIRS}                  # the other mutants apply to every skeleton; a worktree mark is not scoped by `( )`
for _c, _kinds in (("let D=1", BINDINGS), ("unset D", BINDINGS), ("read D", BINDINGS), ("printf -vD x", BINDINGS), ("declare -n D=E", BINDINGS),
                   ("mapfile D", BINDINGS), ("getopts ab D", BINDINGS), ("source x", BINDINGS + DIRS), (". x", BINDINGS + DIRS),
                   ("popd", DIRS), ("cd -", DIRS), ("pushd +1", DIRS)):
    MUTANTS["then-" + _c] = (lambda c: lambda s_, j, dl, k: f"{s_}{j}{c}{j}{dl}")(_c)
    APPLIES["then-" + _c] = _kinds
VALUES = {"dollar": "{r}/$E", "glob": "{r}/*", "brace": "{r}/{{a,b}}", "space": '"{r} x"', "backtick": '"{r}/`id`"', "subst": '"{r}/$(id)"',
          "glued-out": "{r}>x", "glued-in": "{r}<f", "glued-fd": "{r}2>/dev/null"}   # fix wave 3: a redirection glued to the literal


def _outside(k: str) -> str:
    return OUT if k == "wt" else SCR            # the worktree skeleton deletes outside scratch (v1 fires), the others inside it


def floor_worlds():
    for k in skeletons(SCR):
        for name, mutate in MUTANTS.items():
            if k not in APPLIES.get(name, tuple(skeletons(SCR))):
                continue
            setter, join, delete = skeletons(_outside(k))[k]
            for lead in ("", "echo start; ", "echo start\n", "true && "):
                yield f"{k}/{name}", lead + mutate(setter, join, delete, k), k
        for name, value in VALUES.items():
            setter, join, delete = skeletons(value.format(r=_outside(k)))[k]
            for lead in ("", "echo start; ", "echo start\n", "true && "):
                yield f"{k}/value-{name}", lead + setter + join + delete, k


def floor_violations() -> list:
    """Worlds where v1 fires, v2 does not, and nothing was proven: must be empty."""
    return [(name, cmd) for name, cmd, _k in floor_worlds() if v1_fires(cmd) and not fires(cmd)]


def test_the_proven_skeletons_are_the_only_place_v2_fires_less_than_v1():
    count = 0
    for k, (setter, join, delete) in ((k, skeletons(_outside(k))[k]) for k in skeletons(SCR)):
        for lead in ("", "echo start; ", "echo start\n", "true && "):
            if lead == "true && " and k == "var":
                continue                                     # a binding after `true &&` dies with its chain: not proven
            cmd = lead + setter + join + delete
            assert v1_fires(cmd) and not fires(cmd), (k, cmd)    # proven scratch: v1 fired, v2 does not
            count += 1
    assert count >= 20


def test_v2_never_fires_less_than_v1_on_a_mutation_of_a_trusted_construct():
    worlds_ = list(floor_worlds())
    assert len(worlds_) >= 400
    assert floor_violations() == []


def test_the_floor_property_fails_when_the_compound_and_function_check_is_removed(monkeypatch):
    monkeypatch.setattr(resolve_pre, "untrusted", lambda *a, **k: False)
    names = {n.split("/", 1)[1] for n, _c in floor_violations()}
    assert names and {"if", "function", "group", "trap", "ifs", "unreadable-eval"} & names


def test_the_floor_property_fails_when_the_taint_is_removed(monkeypatch):
    real = resolve._State.forget
    monkeypatch.setattr(resolve._State, "forget", lambda self, taint=False: real(self, False))
    names = {n.split("/", 1)[1] for n, _c in floor_violations()}
    assert names and {"or-first", "neg-first"} & names


def test_the_floor_property_fails_when_a_binding_survives_its_chain(monkeypatch):
    monkeypatch.setattr(resolve._State, "end_chain", lambda self: setattr(self, "first", True) or setattr(self, "taint", False))
    assert any(n.endswith("semicolon") or n.endswith("newline") for n, _c in floor_violations())


FLOOR_FRAGMENTS = ["rm -rf ", "rm -r -f ", "rm -fr -- ", " && ", " || ", "; ", "\n", " | ", " & ", "/srv/", "/w/", "$X", "${X}", '"$X"', "~/", "..",
                   "./*", "*", "'", '"', "\\", "bash -c '", "eval ", "xargs ", "find ", " -delete", "(", ")", "`", "$(", "!", "if ", "then ",
                   "fi", "{ ", "} ", "cat ", "echo ", "ls -d ", "-- ", "a", "b", "x y", "cd ", "D=", "export D=", "time ", "env "]


def test_the_floor_holds_on_random_command_soup_without_scratch_text():
    rng = random.Random(SEED + 11)
    fired = 0
    for _ in range(N * 2):
        text = "".join(rng.choice(FLOOR_FRAGMENTS) for _ in range(rng.randrange(1, 14)))
        if v1_fires(text):
            fired += 1
            assert fires(text), text
    assert fired >= N // 8, "the soup must reach rm -rf often enough to mean something"
