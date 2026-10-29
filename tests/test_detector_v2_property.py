"""Property tests for `rm_rf_nonscratch` v2 (compile/resolve.py): generated commands against an oracle the generator knows.

Every world has ONE effective deletion root: the generator picks the root (a scratch root or one outside scratch), then
spells the delete one of many ways (plain, `./*` after `cd`, `$VAR`, `${VAR}/sub`, quoted, wrapped in sudo/env/nice, `--`,
split flags, `bash -c`/`sh -c`/`eval`, `ls | xargs`, `find -delete`, one or two `cd`s, `&&`/`;`/newline joins). The oracle is
"fires iff the root is outside scratch". Seeded (`random.Random(1234)`, override with PYTEST_SEED); stdlib only; the
commands are DATA fed to the detector, nothing runs.
"""

import os
import random

import pytest

from ace_playbook.compile import catalogue as cat
from ace_playbook.compile import detectors as d
from ace_playbook.compile import resolve, shell
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


def fires(cmd: str) -> bool:
    ev = event_from_hook(cs.pre_payload(cmd), probe=lambda _p: False)
    out, _state, errors = cat.evaluate(ev, None, names=[NAME], strict=False)
    assert errors == []
    return any(f.detector == NAME and f.count > 0 for f in out)


def build(seed: int, root: str) -> str:
    """One generated command whose effective deletion root is `root` (same seed -> same shape, only the root differs)."""
    r = random.Random(seed)
    rm = lambda target: f"{r.choice(WRAP)}rm {r.choice(FLAGS)} {target}"   # noqa: E731
    join, sub = r.choice(JOIN), r.choice(SUB)
    rel = r.choice(["./*", "*", ".", "./"]) if not sub else "."
    kind = r.choice(["plain", "quoted", "cd", "cd2", "cd_sub", "var", "brace", "export", "pipe", "find", "findexec", "cdls"])
    other = r.choice(SCRATCH_ROOTS + OUTSIDE_ROOTS)
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
        cmd = f"cd {root}{join}ls | xargs rm -rf"
    if kind in ("plain", "quoted", "cd", "cd2", "cd_sub", "var", "brace", "export") and r.random() < 0.3:
        # `bash -c '...'`, `sh -c '...'` and `eval '...'` hold the command in single quotes (the quoting the generator avoids inside)
        inner = cmd.replace("'", "")
        cmd = r.choice(["bash -c '%s'", "sh -c '%s'", "eval '%s'", "bash -lc '%s'"]) % inner
    return r.choice(LEAD) + cmd


def worlds(n: int, salt: int):
    rng = random.Random(SEED * 7 + salt)
    for _ in range(n):
        yield rng.randrange(1 << 30), rng.choice(SCRATCH_ROOTS), rng.choice(OUTSIDE_ROOTS)


def test_generated_commands_fire_exactly_when_the_root_is_outside_scratch():
    seen = {True: 0, False: 0}
    for seed, scratch, outside in worlds(N, 1):
        for root, oracle in ((outside, True), (scratch, False)):
            cmd = build(seed, root)
            assert fires(cmd) is oracle, (seed, root, cmd)
            seen[oracle] += 1
    assert seen[True] >= N and seen[False] >= N


def test_swapping_the_root_of_a_fire_world_to_scratch_gives_a_twin_that_does_not_fire():
    for seed, scratch, outside in worlds(N, 2):
        fire, twin = build(seed, outside), build(seed, scratch)
        assert fires(fire) and not fires(twin), (seed, fire, twin)


def test_the_world_grammar_covers_every_shape_and_wrapper():
    shapes = set()
    for seed, scratch, _outside in worlds(N, 3):
        cmd = build(seed, scratch)
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
            for group in canon(build(seed, root)):
                for path, verdict in group:
                    again = canon(f"rm -rf {path}")
                    assert again == [[(path, verdict)]], (seed, root, path, again)
                    checked += 1
    assert checked >= N


def test_the_canonicalizer_is_pure_it_gives_the_same_answer_twice():
    for seed, scratch, outside in worlds(N, 5):
        cmd = build(seed, outside)
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
        assert fires(build(s, outside)) and not fires(build(s, scratch)), (seed, s)
