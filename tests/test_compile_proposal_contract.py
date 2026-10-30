"""Contract worlds on a proposal (compile/contracts.py, compile/proposal.py, docs/design/compiler.md 6): the optional `contract`
key = {"must_fire": [...], "must_not_fire": [...]}, run by `validate` (propose, validate, approve) exactly as the regression examples are."""

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from ace_playbook.compile import contracts, proposal as prop

from .test_compile_cli import approved_dir, run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_proposal import bash, check, ctx, mutated  # noqa: F401  (fixtures + helpers)

KEEP = object()
RM = "rm_rf_nonscratch"


def rebuild(p, contract=KEEP):
    """The same proposal with another contract (None drops the key); id and message follow the content."""
    q = copy.deepcopy(p)
    if contract is None:
        q.pop("contract", None)
    elif contract is not KEEP:
        q["contract"] = contract
    q["id"] = prop.proposal_id(q)
    q["message"] = f"ACE {q['id']}: {prop.MESSAGES[RM]}"
    return q


def world_item(command, expect):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "expect": expect, "source": "synthetic"}


def with_worlds(p, fire=(), no_fire=()):
    c = copy.deepcopy(p["contract"])
    c["must_fire"] += [world_item(x, "fire") for x in fire]
    c["must_not_fire"] += [world_item(x, "no_fire") for x in no_fire]
    return rebuild(p, c)


# ------------------------------------------------------------------ the table and the key shape
def test_the_table_holds_at_least_eight_worlds_per_side_for_rm_rf_nonscratch():
    w = contracts.CONTRACT_WORLDS[RM]
    assert len(w["must_fire"]) >= 8 and len(w["must_not_fire"]) >= 8
    assert {x["expect"] for x in w["must_fire"]} == {"fire"} and {x["expect"] for x in w["must_not_fire"]} == {"no_fire"}
    assert all(x["tool_name"] == "Bash" and x["source"] == "synthetic" for side in w.values() for x in side)


def test_contract_for_copies_the_table_and_is_empty_for_a_detector_without_worlds():
    got = contracts.contract_for(RM)["contract"]
    got["must_fire"][0]["tool_input"]["command"] = "changed"
    got["must_not_fire"].clear()
    assert contracts.CONTRACT_WORLDS[RM]["must_fire"][0]["tool_input"]["command"] != "changed" and contracts.CONTRACT_WORLDS[RM]["must_not_fire"]
    assert contracts.contract_for("rm_rf") == {} and contracts.contract_for("nope") == {}


def test_the_module_is_data_and_pure_functions_only():
    tree = ast.parse(Path(contracts.__file__).read_text())
    mods = {n.module if isinstance(n, ast.ImportFrom) else a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in getattr(n, "names", [])}
    assert mods == {"__future__"} and len(Path(contracts.__file__).read_text().splitlines()) <= 200


def test_every_world_of_the_table_behaves_against_the_current_detector(ctx):
    for side, verdict in (("must_fire", True), ("must_not_fire", False)):
        for i, x in enumerate(contracts.CONTRACT_WORLDS[RM][side]):
            assert prop.fires(ctx.p["check"], x) is verdict, (side, i, x["tool_input"]["command"])


# ------------------------------------------------------------------ build and validate
def test_a_built_proposal_for_rm_rf_nonscratch_carries_the_contract_and_passes_all_six_steps(ctx):
    assert ctx.p["contract"] == contracts.CONTRACT_WORLDS[RM] and set(ctx.p["contract"]) == {"must_fire", "must_not_fire"}
    rep = check(ctx, ctx.p)
    assert rep.ok, rep.results
    assert [s for s, ok, _ in rep.results] == list(prop.STEPS)


def test_a_record_without_the_key_validates_and_keeps_the_old_id_formula(ctx):
    old = rebuild(ctx.p, None)
    assert "contract" not in old and check(ctx, old).ok
    body = {"lesson_id": old["lesson_id"], "event": "PreToolUse", "matcher": "Bash", "detector": RM, "params": {},
            "cards": old["provenance"]["cards"], "source_sha256": old["provenance"]["source_sha256"]}
    assert old["id"] == "P-" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]


def test_the_proposal_id_changes_when_the_contract_changes(ctx):
    base = ctx.p
    more = with_worlds(base, fire=["rm -rf /w/another"])
    fewer = rebuild(base, {**base["contract"], "must_fire": base["contract"]["must_fire"][:-1]})
    ids = {base["id"], rebuild(base, None)["id"], more["id"], fewer["id"]}
    assert len(ids) == 4
    assert rebuild(base)["id"] == base["id"]                     # same content, same id


def test_editing_the_contract_without_the_id_is_refused_at_the_schema_step(ctx):
    p = copy.deepcopy(ctx.p)
    p["contract"]["must_fire"].append(world_item("rm -rf /w/another", "fire"))
    rep = check(ctx, p)
    assert rep.failed == "schema" and "id does not match" in rep.detail("schema")


def test_a_must_fire_world_that_does_not_fire_is_refused(ctx):
    p = with_worlds(ctx.p, fire=["echo hello"])
    n = len(p["contract"]["must_fire"]) - 1
    rep = check(ctx, p)
    assert rep.failed == "regressions" and rep.detail("regressions") == f"contract must_fire example(s) [{n}] do not fire"
    assert [ok for _, ok, _ in rep.results] == [True, True, False, None, None, None]


def test_a_must_not_fire_world_that_fires_is_refused(ctx):
    p = with_worlds(ctx.p, no_fire=["rm -rf /w/src"])
    n = len(p["contract"]["must_not_fire"]) - 1
    rep = check(ctx, p)
    assert rep.failed == "regressions" and rep.detail("regressions") == f"contract must_not_fire example(s) [{n}] fire"


def test_every_failing_index_is_listed(ctx):
    p = with_worlds(ctx.p, fire=["echo a", "echo b"])
    n = len(p["contract"]["must_fire"])
    assert check(ctx, p).detail("regressions") == f"contract must_fire example(s) [{n - 2}, {n - 1}] do not fire"


def test_the_regression_examples_are_still_checked_first(ctx):
    p = rebuild(mutated(ctx, **{"regression_examples": [{**ctx.p["regression_examples"][0], "tool_input": {"command": "rm -rf /w/src"}}] * 3}))
    assert check(ctx, p).detail("regressions").startswith("regression example(s)")


def test_the_hot_path_runs_the_contract_too(ctx):
    p = with_worlds(ctx.p, fire=["echo hello"])
    assert prop.validate(p, ctx.cfg, ctx.known, hot=True).failed == "regressions"


def test_a_stale_detector_is_what_the_contract_catches(ctx, monkeypatch):
    """The point of the key: a detector edit that breaks a world refuses the proposal, whoever else still passes."""
    real = prop.fires
    monkeypatch.setattr(prop, "fires", lambda chk, x: False if "env -C" in x["tool_input"]["command"] else real(chk, x))
    rep = check(ctx, ctx.p)
    assert rep.failed == "regressions" and rep.detail("regressions").startswith("contract must_fire example(s) [")


# ------------------------------------------------------------------ schema strictness
@pytest.mark.parametrize("contract,why", [
    ("not a dict", "contract keys differ"),
    ({"must_fire": []}, "contract keys differ"),
    ({"must_fire": [], "must_not_fire": [], "extra": []}, "contract keys differ"),
    ({"must_fire": [], "must_not_fire": "x"}, "needs at least"),
])
def test_a_malformed_contract_is_a_schema_error(ctx, contract, why):
    rep = check(ctx, rebuild(ctx.p, contract))
    assert rep.failed == "schema" and why in rep.detail("schema")


def test_the_minimum_is_one_must_fire_and_three_must_not_fire(ctx):
    one, three = world_item("rm -rf /w/src", "fire"), [world_item(f"ls /w/{i}", "no_fire") for i in range(3)]
    assert check(ctx, rebuild(ctx.p, {"must_fire": [one], "must_not_fire": three})).ok
    assert "must_fire needs at least 1" in check(ctx, rebuild(ctx.p, {"must_fire": [], "must_not_fire": three})).detail("schema")
    assert "must_not_fire needs at least 3" in check(ctx, rebuild(ctx.p, {"must_fire": [one], "must_not_fire": three[:2]})).detail("schema")


@pytest.mark.parametrize("edit", [
    lambda x: {**x, "expect": "no_fire"}, lambda x: {**x, "source": "private"}, lambda x: {**x, "extra": 1},
    lambda x: {"tool_name": x["tool_name"], "tool_input": x["tool_input"]}, lambda x: {**x, "tool_input": {"command": 5}},
    lambda x: {**x, "tool_input": {"command": "x" * 70000}}, lambda x: "text",
])
def test_a_malformed_world_is_a_schema_error(ctx, edit):
    c = copy.deepcopy(ctx.p["contract"])
    c["must_fire"][0] = edit(c["must_fire"][0])
    rep = check(ctx, rebuild(ctx.p, c))
    assert rep.failed == "schema" and "example" in rep.detail("schema")


def test_an_unknown_top_level_key_is_still_refused(ctx):
    p = copy.deepcopy(ctx.p)
    p["contrakt"] = {}
    assert check(ctx, p).failed == "schema" and "keys differ" in check(ctx, p).detail("schema")
    q = copy.deepcopy(ctx.p)
    q["contract2"] = ctx.p["contract"]
    assert check(ctx, q).failed == "schema"


def test_a_proposal_over_the_size_cap_cannot_be_loaded(ctx, tmp_path):
    big = copy.deepcopy(ctx.p)
    big["contract"]["must_fire"][0]["tool_input"]["command"] = "x" * prop.MAX_BYTES
    f = tmp_path / "big.json"
    f.write_text(json.dumps(big))
    with pytest.raises(prop.ProposalError):
        prop.load(f)


def test_run_and_shape_work_on_plain_data():
    items = lambda n, e: [world_item(f"c{i}", e) for i in range(n)]                  # noqa: E731
    p = {"contract": {"must_fire": items(2, "fire"), "must_not_fire": items(3, "no_fire")}}
    assert contracts.run(p, lambda x: x["expect"] == "fire") is None
    assert contracts.run(p, lambda x: False) == "contract must_fire example(s) [0, 1] do not fire"
    assert contracts.run(p, lambda x: True) == "contract must_not_fire example(s) [0, 1, 2] fire"
    assert contracts.run({}, lambda x: 1 / 0) is None and contracts.shape({}, None) is None and contracts.id_part({}) == {}


# ------------------------------------------------------------------ the CLI path
def test_propose_writes_the_contract_and_validate_prints_six_ok_steps(world, written, capsys):
    p = json.loads(written.read_text())
    assert p["contract"] == contracts.CONTRACT_WORLDS[RM] and written.stem == p["id"]
    code, out, _ = run(world, "validate", str(written), capsys=capsys)
    assert code == 0 and out.count("[ok]") == 6


def test_propose_dry_run_prints_the_contract_and_writes_nothing(world, capsys):
    code, out, _ = run(world, "propose", "--lesson", world.lesson_id, "--detector", RM, "--dry-run", capsys=capsys)
    assert code == 0 and '"contract"' in out and '"must_not_fire"' in out and not (world.cfg.data_dir / "proposals").exists()


def stale_copy(world, written):
    """The written proposal with a must_fire world that cannot fire, re-identified, saved beside it."""
    p = with_worlds(json.loads(written.read_text()), fire=["echo hello"])
    dest = written.with_name(p["id"] + ".json")
    dest.write_text(json.dumps(p, indent=1, sort_keys=True) + "\n")
    return dest


def test_validate_exits_1_at_the_regressions_step_for_a_stale_contract(world, written, capsys):
    code, out, err = run(world, "validate", str(stale_copy(world, written)), capsys=capsys)
    assert code == 1 and "step 'regressions'" in err and "[FAIL] regressions: contract must_fire example(s)" in out


def test_approve_refuses_a_stale_contract_like_a_stale_regression_and_writes_nothing(world, written, tty, capsys):
    stale = stale_copy(world, written)
    code, out, err = run(world, "approve", stale.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "step 'regressions'" in err and "[FAIL] regressions: contract must_fire" in out
    assert tty.asked == [] and not (approved_dir(world) / stale.name).exists()


def test_approve_accepts_the_untouched_proposal_with_its_contract_and_the_hook_loads_it(world, written, tty, capsys):
    from ace_playbook.compile import shadow
    code, _, _ = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 0
    ((p, m),) = shadow.load_approved(world.cfg, lambda msg: None)
    assert (p["id"], m, p["contract"]) == (written.stem, "observe", contracts.CONTRACT_WORLDS[RM])
    rec = json.loads((approved_dir(world) / written.name).read_text())
    assert set(rec) == set(prop.KEYS) | {"contract", "approved_at", "proposal_sha256"}


def test_an_approved_record_whose_contract_was_edited_is_not_loaded(world, written, tty, capsys):
    from ace_playbook.compile import shadow
    run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    path = approved_dir(world) / written.name
    rec = json.loads(path.read_text())
    rec["contract"]["must_not_fire"].pop()
    path.write_text(json.dumps(rec))
    notes = []
    assert shadow.load_approved(world.cfg, notes.append) == [] and any("hash" in n or "invalid" in n for n in notes)
