"""Row identity and purity around the per-detector hash: unhashable fires, the nightly pass, state isolation, no mutation."""

import ast
import dataclasses
import functools
import re
from pathlib import Path

import pytest

from ace_playbook.compile import catalogue as cat, demotions as dm, detectors, events, offline, proposal as prop
from . import compile_support as cs
from .test_compile_shadow import RM, Runner

ROWS = list(cat.CATALOGUE)


# ------------------------------------------------------------------ a fire without a name, and the nightly pass
class Callable_:
    def __call__(self, event, state):
        return None


@pytest.mark.parametrize("fire", [functools.partial(detectors.rm_rf), Callable_()])
def test_a_fire_without_a_name_fails_only_its_own_row(monkeypatch, fire):
    monkeypatch.setitem(cat.CATALOGUE, "rm_rf", dataclasses.replace(cat.CATALOGUE["rm_rf"], fire=fire))
    with pytest.raises(ValueError, match=r"fire of rm_rf is not detectors\."):
        cat.CATALOGUE["rm_rf"].source_sha256
    others = {n: r.source_sha256 for n, r in cat.CATALOGUE.items() if n != "rm_rf"}
    assert len(others) == len(ROWS) - 1 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in others.values())
    with pytest.raises(ValueError):
        cat.source_table()                                     # a seal must refuse a registry with an unhashable row


@pytest.mark.parametrize("fire", [functools.partial(detectors.rm_rf_nonscratch), functools.wraps(detectors.rm_rf_nonscratch)(lambda e, s: None)])
def test_the_nightly_pass_treats_an_unhashable_row_as_a_stale_proposal(tmp_path, monkeypatch, fire):
    w = cs.offline_world(tmp_path, "observe")
    row = cat.CATALOGUE["rm_rf_nonscratch"]
    monkeypatch.setitem(cat.CATALOGUE, "rm_rf_nonscratch", dataclasses.replace(row, fire=fire))
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["demotions"] == 1 and out["errors"] == 0
    assert [(d["to_mode"], d["cause"]) for d in dm.read_chain(w.data)[0]] == [("disabled", "source_sha256")]


# ------------------------------------------------------------------ fire identity, state isolation, purity
def test_a_fire_that_is_not_the_detectors_function_fails_closed():
    row = cat.CATALOGUE["rm_rf"]
    for fire in (functools.wraps(detectors.rm_rf)(lambda e, s: None), lambda e, s: None):
        with pytest.raises(ValueError, match=r"fire of rm_rf is not detectors\."):
            dataclasses.replace(row, fire=fire).source_sha256


def test_the_hook_skips_a_proposal_whose_row_has_a_wrapped_fire(tmp_path, monkeypatch, capsys):
    w = cs.real_world(tmp_path)
    run = Runner(w, monkeypatch, capsys)
    row, pid = cat.CATALOGUE["rm_rf_nonscratch"], w.props["rm_rf_nonscratch"]["id"]
    monkeypatch.setitem(cat.CATALOGUE, "rm_rf_nonscratch", dataclasses.replace(row, fire=functools.wraps(row.fire)(lambda e, s: None)))
    assert run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows == [] and f"skipped:{pid}:invalid:schema" in run.log                  # validate fails closed on the ValueError
    rep = prop.validate(w.props["rm_rf_nonscratch"], w.cfg, hot=True)
    assert rep.failed == "schema" and rep.detail("schema") == "ValueError (fail closed)"


def test_evaluate_gives_each_fire_its_own_copy_of_the_state(monkeypatch):
    seen = []

    def first(event, state):
        state["git"]["commit"] = 99
        state["seen_paths"].append("x")

    def second(event, state):
        seen.append((state["git"]["commit"], list(state["seen_paths"])))

    rows = {n: cat.Detector(n, "weak", "live+offline", (), "none", f, "none") for n, f in (("a", first), ("b", second))}
    monkeypatch.setattr(cat, "CATALOGUE", rows)
    state = detectors.blank_state()
    _, new, errors = cat.evaluate(events.event_from_hook(cs.pre_payload("ls")), state)
    assert seen == [(0, [])] and errors == [] and state == detectors.blank_state() and new["seen_paths"] == []


MUTATORS = {"remove", "discard", "extend", "insert", "sort", "reverse", "popitem", "update", "setdefault", "clear", "append", "add", "pop"}


def violations(text):
    """Mutation through a module-level name or an `event`/`state` parameter, and global/nonlocal, in a module's functions."""
    tree = ast.parse(text)
    module = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            module |= {(a.asname or a.name).split(".")[0] for a in n.names}
        else:
            module |= {x.id for x in ast.walk(n) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}

    def root(node):
        while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
            node = node.func if isinstance(node, ast.Call) else node.value
        return node.id if isinstance(node, ast.Name) else None

    bad = []
    for fn in (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        params = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs} & {"event", "state"}
        local = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)} | {x.id for x in ast.walk(fn) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
        shared = (module - local) | params
        for x in ast.walk(fn):
            if isinstance(x, (ast.Global, ast.Nonlocal)):
                bad.append((fn.name, "global/nonlocal"))
            elif isinstance(x, (ast.Attribute, ast.Subscript)) and isinstance(x.ctx, (ast.Store, ast.Del)) and root(x) in shared:
                bad.append((fn.name, "store through " + root(x)))
            elif (isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and root(x.func.value) in shared
                  and (x.func.attr in MUTATORS or x.func.attr.endswith("_update"))):
                bad.append((fn.name, f"{x.func.attr} on {root(x.func.value)}"))
    return bad


def test_detectors_mutate_neither_module_level_state_nor_their_event_and_state_arguments():
    """Structural: each fire sees a deep copy of the state but the same event object, so neither may be mutated."""
    assert violations(Path(detectors.__file__).read_text()) == []


def test_the_mutation_scan_catches_what_it_is_meant_to():
    text = ("REG = []\n\ndef f(event, state):\n    event.tool_uses.clear()\n    state['x'] = 1\n    event.set_update(1)\n"
            "    REG.append(1)\n    del state['y']\n\ndef g(event, state):\n    global REG\n    local = []\n    local.append(1)\n")
    assert sorted(violations(text)) == sorted([("f", "clear on event"), ("f", "store through state"), ("f", "set_update on event"),
                                               ("f", "append on REG"), ("f", "store through state"), ("g", "global/nonlocal")])


# ------------------------------------------------------------------ a fire without a name, and the nightly pass
class Callable_:
    def __call__(self, event, state):
        return None


@pytest.mark.parametrize("fire", [functools.partial(detectors.rm_rf), Callable_()])
def test_a_fire_without_a_name_fails_only_its_own_row(monkeypatch, fire):
    monkeypatch.setitem(cat.CATALOGUE, "rm_rf", dataclasses.replace(cat.CATALOGUE["rm_rf"], fire=fire))
    with pytest.raises(ValueError, match=r"fire of rm_rf is not detectors\."):
        cat.CATALOGUE["rm_rf"].source_sha256
    others = {n: r.source_sha256 for n, r in cat.CATALOGUE.items() if n != "rm_rf"}
    assert len(others) == len(ROWS) - 1 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in others.values())
    with pytest.raises(ValueError):
        cat.source_table()                                     # a seal must refuse a registry with an unhashable row


@pytest.mark.parametrize("fire", [functools.partial(detectors.rm_rf_nonscratch), functools.wraps(detectors.rm_rf_nonscratch)(lambda e, s: None)])
def test_the_nightly_pass_treats_an_unhashable_row_as_a_stale_proposal(tmp_path, monkeypatch, fire):
    w = cs.offline_world(tmp_path, "observe")
    row = cat.CATALOGUE["rm_rf_nonscratch"]
    monkeypatch.setitem(cat.CATALOGUE, "rm_rf_nonscratch", dataclasses.replace(row, fire=fire))
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["demotions"] == 1 and out["errors"] == 0
    assert [(d["to_mode"], d["cause"]) for d in dm.read_chain(w.data)[0]] == [("disabled", "source_sha256")]


# ------------------------------------------------------------------ fire identity, state isolation, purity
def test_a_fire_that_is_not_the_detectors_function_fails_closed():
    row = cat.CATALOGUE["rm_rf"]
    for fire in (functools.wraps(detectors.rm_rf)(lambda e, s: None), lambda e, s: None):
        with pytest.raises(ValueError, match=r"fire of rm_rf is not detectors\."):
            dataclasses.replace(row, fire=fire).source_sha256


def test_the_hook_skips_a_proposal_whose_row_has_a_wrapped_fire(tmp_path, monkeypatch, capsys):
    w = cs.real_world(tmp_path)
    run = Runner(w, monkeypatch, capsys)
    row, pid = cat.CATALOGUE["rm_rf_nonscratch"], w.props["rm_rf_nonscratch"]["id"]
    monkeypatch.setitem(cat.CATALOGUE, "rm_rf_nonscratch", dataclasses.replace(row, fire=functools.wraps(row.fire)(lambda e, s: None)))
    assert run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows == [] and f"skipped:{pid}:invalid:schema" in run.log                  # validate fails closed on the ValueError
    rep = prop.validate(w.props["rm_rf_nonscratch"], w.cfg, hot=True)
    assert rep.failed == "schema" and rep.detail("schema") == "ValueError (fail closed)"


def test_evaluate_gives_each_fire_its_own_copy_of_the_state(monkeypatch):
    seen = []

    def first(event, state):
        state["git"]["commit"] = 99
        state["seen_paths"].append("x")

    def second(event, state):
        seen.append((state["git"]["commit"], list(state["seen_paths"])))

    rows = {n: cat.Detector(n, "weak", "live+offline", (), "none", f, "none") for n, f in (("a", first), ("b", second))}
    monkeypatch.setattr(cat, "CATALOGUE", rows)
    state = detectors.blank_state()
    _, new, errors = cat.evaluate(events.event_from_hook(cs.pre_payload("ls")), state)
    assert seen == [(0, [])] and errors == [] and state == detectors.blank_state() and new["seen_paths"] == []


MUTATORS = {"remove", "discard", "extend", "insert", "sort", "reverse", "popitem", "update", "setdefault", "clear", "append", "add", "pop"}


def test_detectors_mutate_neither_module_level_state_nor_their_event_and_state_arguments():
    """Structural: no global/nonlocal; no store, delete or mutating call (`*_update` included) through a module-level
    name or through a parameter named `event` or `state`. Each fire sees a deep copy of the state, not of the event."""
    tree = ast.parse(Path(detectors.__file__).read_text())
    module = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            module |= {(a.asname or a.name).split(".")[0] for a in n.names}
        else:
            module |= {x.id for x in ast.walk(n) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}

    def root(node):
        while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
            node = node.func if isinstance(node, ast.Call) else node.value
        return node.id if isinstance(node, ast.Name) else None

    def mutating(attr):
        return attr in MUTATORS or attr.endswith("_update")

    bad = []
    for fn in (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        params = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs} & {"event", "state"}
        local = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)} | {x.id for x in ast.walk(fn) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
        shared = (module - local) | params
        for x in ast.walk(fn):
            if isinstance(x, (ast.Global, ast.Nonlocal)):
                bad.append((fn.name, "global/nonlocal"))
            elif isinstance(x, (ast.Attribute, ast.Subscript)) and isinstance(x.ctx, (ast.Store, ast.Del)) and root(x) in shared:
                bad.append((fn.name, "store through " + root(x)))
            elif isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and mutating(x.func.attr) and root(x.func.value) in shared:
                bad.append((fn.name, f"{x.func.attr} on {root(x.func.value)}"))
    assert bad == []


def test_the_mutation_scan_would_catch_a_fire_that_clears_its_event():
    """Guards the scan itself: the same walk flags `event.tool_uses.clear()` and `state['x'] = 1` in a synthetic fire."""
    fn = ast.parse("def f(event, state):\n    event.tool_uses.clear()\n    state['x'] = 1\n    event.add_update(1)\n").body[0]
    calls = [x for x in ast.walk(fn) if isinstance(x, ast.Call)]
    assert [c.func.attr for c in calls] == ["clear", "add_update"]
    assert all(c.func.attr in MUTATORS or c.func.attr.endswith("_update") for c in calls)
