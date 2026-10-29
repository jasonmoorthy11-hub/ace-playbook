"""Per-detector source hash by exclusion (compile/srchash.py, docs/design/compiler.md 4 and 6): what moves a row's hash."""

import ast
import dataclasses
import inspect
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from ace_playbook.compile import catalogue as cat, detectors, evidence as evm, proposal as prop, srchash

from . import compile_support as cs
from .test_compile_ladder import DET, check as ladder_check, proposal as ladder_proposal

ROOT = Path(__file__).resolve().parent.parent
ROWS = list(cat.CATALOGUE)
STATEFUL = [n for n, r in cat.CATALOGUE.items() if r.state != "none"]
STATELESS = [n for n, r in cat.CATALOGUE.items() if r.state == "none"]


def hashes():
    return {n: r.source_sha256 for n, r in cat.CATALOGUE.items()}


def patch_text(monkeypatch, **edits):
    """Simulate edits to the on-disk text of detectors.py / catalogue.py: {'detectors.py': text -> text}."""
    real = srchash.module_text
    monkeypatch.setattr(srchash, "module_text", lambda p: edits[p.name](real(p)) if p.name in edits else real(p))


def put(text, name, line="    _edit = 1\n"):
    """Insert the statement `line` as the first line of the body of the top-level `def name(`."""
    start = text.index(f"def {name}(")
    end = text.index(":\n", start) + 2
    return text[:end] + line + text[end:]


def swap(old, new):
    def go(text):
        assert text.count(old) == 1, old
        return text.replace(old, new)
    return go


def changed(before, after):
    return {n for n in before if before[n] != after[n]}


# ------------------------------------------------------------------ stability and shape
def test_the_hash_is_stable_across_calls_and_distinct_per_row():
    first = hashes()
    assert first == hashes()
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in first.values()) and len(set(first.values())) == len(first)


def test_source_table_lists_every_catalogue_name_with_its_hash():
    table = cat.source_table()
    assert list(table) == ROWS and table == hashes()


def test_the_catalogue_has_both_stateful_and_stateless_rows():
    assert STATEFUL and STATELESS


def test_every_row_fire_is_a_top_level_function_of_detectors():
    assert all(getattr(detectors, r.fire.__name__, None) is r.fire for r in cat.CATALOGUE.values())


@pytest.mark.parametrize("name", STATELESS)
def test_a_row_with_state_none_never_reads_its_state_argument(name):
    """Hygiene only: advance is hashed for every row now, so nothing depends on this; it keeps the metadata honest."""
    node = ast.parse(textwrap.dedent(inspect.getsource(cat.CATALOGUE[name].fire))).body[0]
    arg = node.args.args[1].arg
    assert not any(isinstance(n, ast.Name) and n.id == arg for stmt in node.body for n in ast.walk(stmt)), name


def test_srchash_uses_no_runtime_introspection():
    tree = ast.parse(Path(srchash.__file__).read_text())
    assert not any(isinstance(n, ast.Name) and n.id in ("inspect", "getattr", "sys") for n in ast.walk(tree))
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) and "inspect" in ast.dump(n) for n in ast.walk(tree))


def test_the_excised_text_for_rm_rf_nonscratch_drops_only_the_other_fires_bodies():
    text = srchash.module_text(Path(detectors.__file__))
    others = [r.fire.__name__ for r in cat.CATALOGUE.values()]
    out = srchash.excised_text(text, srchash.detector_drop(text, "rm_rf_nonscratch", others))
    assert "def rm_rf(event: Event, state: dict) -> Fire | None:\n    ...excised..." in out
    assert "def user_correction(event: Event, state: dict) -> Fire | None:\n    ...excised..." in out
    assert "_rm_hits(event, nonscratch=False)" not in out and "_rm_hits(event, nonscratch=True)" in out
    assert "def _rm_hits(" in out and "import posixpath" in out and "SCRATCH = " in out
    assert "def advance(event: Event, state: dict | None) -> dict:\n    \"\"\"Return the state" in out and "def blank_state()" in out


# ------------------------------------------------------------------ what moves a hash
@pytest.mark.parametrize("name", ROWS)
def test_editing_one_rows_fire_body_moves_only_that_row(monkeypatch, name):
    before = hashes()
    fire = cat.CATALOGUE[name].fire.__name__
    patch_text(monkeypatch, **{"detectors.py": lambda t: put(t, fire)})
    assert changed(before, hashes()) == {n for n, r in cat.CATALOGUE.items() if r.fire.__name__ == fire} == {name}


EVERYONE = {
    "a helper": ("detectors.py", lambda t: put(t, "_rm_hits")),
    "resolve.py (a leaf module, hashed whole)": ("resolve.py", lambda t: put(t, "canonical_targets")),
    "resolve_pre.py (a leaf module, hashed whole)": ("resolve_pre.py", lambda t: put(t, "untrusted")),
    "resolve_chain.py (a leaf module, hashed whole)": ("resolve_chain.py", lambda t: put(t, "lead_kind")),
    "shell.py (a leaf module, hashed whole)": ("shell.py", lambda t: put(t, "command_stream", "    _edit = 1\n")),
    "a helper's decorator": ("detectors.py", swap("def _rm_hits(", "@staticmethod\ndef _rm_hits(")),
    "another fire's decorator": ("detectors.py", swap("def user_correction(", "@_b\ndef user_correction(")),
    "another fire's default argument": ("detectors.py", swap("def user_correction(event: Event, state: dict) -> Fire | None:",
                                                             "def user_correction(event: Event, state: dict, _x=AUTOLOADED.add(1)) -> Fire | None:")),
    "advance": ("detectors.py", lambda t: put(t, "advance")),
    "blank_state": ("detectors.py", lambda t: put(t, "blank_state")),
    "a statement beside the registry": ("catalogue.py", lambda t: t + "\nCATALOGUE_X = 1; shell.git_subcommand = None\n"),
    "an import line": ("detectors.py", swap("import posixpath", "import ntpath as posixpath")),
    "a from-import": ("detectors.py", swap("from . import resolve, shell", "from . import events as shell, resolve")),
    "a top-level statement": ("detectors.py", lambda t: t + "\nREG = []\nREG.append(1)\n"),
    "a constant's first binding": ("detectors.py", swap("MAX_PATHS = 5000", "MAX_PATHS = 1\nOLD = MAX_PATHS\nMAX_PATHS = 5000")),
    "a rebinding after the def": ("detectors.py", lambda t: t + "\n_rm_hits = _rm_hits\n"),
    "evaluate": ("catalogue.py", lambda t: put(t, "evaluate")),
    "a catalogue import": ("catalogue.py", swap("from .detectors import (Fire, advance,", "from .detectors import (Fire, blank_state as advance,")),
    "the Detector class": ("catalogue.py", lambda t: put(t, "source_sha256", "        _edit = 1\n")),
}


@pytest.mark.parametrize("what", EVERYONE)
def test_editing_shared_text_moves_every_hash(monkeypatch, what):
    before = hashes()
    name, edit = EVERYONE[what]
    patch_text(monkeypatch, **{name: edit})
    assert changed(before, hashes()) == set(before)


def test_a_fire_that_calls_another_fire_keeps_the_callee(monkeypatch):
    patch_text(monkeypatch, **{"detectors.py": lambda t: put(t, "rm_rf_nonscratch", "    _ = rm_rf\n")})
    base = hashes()
    patch_text(monkeypatch, **{"detectors.py": lambda t: put(put(t, "rm_rf_nonscratch", "    _ = rm_rf\n"), "rm_rf")})
    assert changed(base, hashes()) == {"rm_rf", "rm_rf_nonscratch"}


def test_editing_the_registry_literals_text_moves_every_hash(monkeypatch):
    before = hashes()
    patch_text(monkeypatch, **{"catalogue.py": swap('rm_rf, "observe", _RM),', 'rm_rf, "warn", _RM),')})
    assert changed(before, hashes()) == set(before)


def test_a_side_effecting_expression_inside_the_registry_literal_moves_every_hash(monkeypatch):
    before = hashes()
    edit = swap('             rm_rf_nonscratch, "block", _RM),',
                '             (shell.__dict__.update({}) or rm_rf_nonscratch), "block", _RM),')
    patch_text(monkeypatch, **{"catalogue.py": edit})
    assert changed(before, hashes()) == set(before)


def test_the_meta_label_moves_only_the_row_whose_fields_change_on_an_unchanged_file(monkeypatch):
    before = hashes()
    monkeypatch.setitem(cat.CATALOGUE, "rm_rf", dataclasses.replace(cat.CATALOGUE["rm_rf"], compilable="warn"))
    assert changed(before, hashes()) == {"rm_rf"}


def test_editing_evaluate_replaces_the_old_whole_catalogue_edit_check(monkeypatch):
    before = hashes()
    patch_text(monkeypatch, **{"catalogue.py": lambda t: put(t, "evaluate")})
    assert changed(before, hashes()) == set(before)


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5])
def test_editing_events_shell_textrules_resolve_resolve_pre_or_resolve_chain_moves_every_hash(monkeypatch, index):
    before = hashes()
    texts = cat._source_texts()
    assert len(texts) == 6 and all(texts)
    edited = tuple(t + "# edit" if i == index else t for i, t in enumerate(texts))
    monkeypatch.setattr(cat, "_source_texts", lambda: edited)
    assert changed(before, hashes()) == set(before)


def test_the_leaf_texts_are_the_six_modules_whole():
    mods = (cat.events, cat.shell, cat.textrules, cat.resolve, cat.resolve_pre, cat.resolve_chain)
    assert cat._source_texts() == tuple(Path(m.__file__).read_text() for m in mods)


@pytest.mark.parametrize("field,value", [
    ("consumes", ("PreToolUse:Bash", "Stop")), ("compilable", "none"), ("strength", "weak"), ("tier", "offline"),
    ("state", "git_counts"), ("lessons", ("L-00000000",)), ("name", "renamed"), ("fire", detectors.user_correction),
])
def test_a_changed_row_field_changes_the_hash(field, value):
    row = cat.CATALOGUE["rm_rf_nonscratch"]
    assert getattr(row, field) != value
    assert dataclasses.replace(row, **{field: value}).source_sha256 != row.source_sha256


def test_a_row_rebuilt_elsewhere_with_the_same_fields_has_the_same_hash():
    row = cat.CATALOGUE["rm_rf"]
    assert dataclasses.replace(row).source_sha256 == row.source_sha256


# ------------------------------------------------------------------ the consumers still refuse
@pytest.fixture
def ctx(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch)
    cards, _ = evm.build_cards(w.cfg)
    p = prop.build(w.cfg, w.lesson_id, "rm_rf_nonscratch", cards, now="2026-10-20T03:12:00Z")
    return SimpleNamespace(cfg=w.cfg, known={c["id"]: c for c in cards}, p=p)


def test_the_hot_check_refuses_a_stale_proposal_with_the_same_message(ctx, monkeypatch):
    assert prop.validate(ctx.p, ctx.cfg, ctx.known).ok
    patch_text(monkeypatch, **{"detectors.py": lambda t: put(t, "rm_rf_nonscratch")})
    rep = prop.validate(ctx.p, ctx.cfg, ctx.known)
    assert rep.failed == "schema" and rep.detail("schema") == "catalogue source_sha256 is stale"


def test_a_proposal_for_an_untouched_detector_survives_another_detectors_edit(ctx, monkeypatch):
    patch_text(monkeypatch, **{"detectors.py": lambda t: put(t, "user_correction")})
    assert prop.validate(ctx.p, ctx.cfg, ctx.known).ok


def test_the_ladder_refusal_text_is_unchanged(monkeypatch):
    p = ladder_proposal()
    assert ladder_check(p=p).ok
    patch_text(monkeypatch, **{"detectors.py": lambda t: put(t, "rm_rf_nonscratch")})
    assert "detector source_sha256 changed since the proposal was written" in ladder_check(p=p).reasons
    assert DET.source_sha256 != p["provenance"]["source_sha256"]


def test_the_ladder_ignores_an_edit_to_another_detector(monkeypatch):
    p = ladder_proposal()
    patch_text(monkeypatch, **{"detectors.py": lambda t: put(t, "user_correction")})
    assert ladder_check(p=p).ok


def test_the_proposal_id_differs_for_different_hashes(ctx):
    other = {**ctx.p, "provenance": {**ctx.p["provenance"], "source_sha256": "1" * 64}}
    assert prop.proposal_id(other) != prop.proposal_id(ctx.p)
    assert prop.proposal_id(ctx.p) == ctx.p["id"]


# ------------------------------------------------------------------ determinism across processes
def test_two_processes_compute_the_same_table():
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); from ace_playbook.compile import catalogue; "
            f"print(sorted(catalogue.source_table().items()))")
    outs = [subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
            for _ in range(2)]
    assert all(o.returncode == 0 for o in outs), [o.stderr for o in outs]
    assert outs[0].stdout == outs[1].stdout and outs[0].stdout.strip()
    assert outs[0].stdout.strip() == str(sorted(cat.source_table().items()))
