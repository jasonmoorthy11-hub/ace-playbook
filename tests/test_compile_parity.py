"""Parity of the port with Step 0, from the frozen JSON (CI) and, locally, from a real Step 0 checkout."""

import hashlib
import json
import os
import re

import pytest

from ace_playbook.compile import catalogue as cat

from . import compile_support as cs

FROZEN = json.loads(cs.PARITY.read_text())


def test_frozen_parity_records_the_step0_commit_and_source_hashes():
    assert FROZEN["step0_commit"] == "17f3891b402aab2b39bfe6c6737460cb3de99216"
    assert re.fullmatch(r"[0-9a-f]{64}", FROZEN["source_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", FROZEN["scanlib_sha256"])
    assert FROZEN["detectors"] == list(cs.PARITY_DETECTORS)
    assert set(cs.PARITY_DETECTORS) == set(cat.CATALOGUE) - {"unparseable"}


def test_frozen_parity_covers_every_fixture_scenario_and_nothing_else():
    assert set(FROZEN["scenarios"]) == set(cs.scenarios())
    for counts in FROZEN["scenarios"].values():
        assert set(counts) == set(cs.PARITY_DETECTORS)


def test_every_parity_deviation_is_explained_and_matches_the_port():
    assert FROZEN["deviations"], "the hardening must be visible in the parity file"
    for name, dev in FROZEN["deviations"].items():
        assert dev["reason"] and dev["step0"].keys() == dev["port"].keys() and name in FROZEN["scenarios"]
        for k, v in dev["port"].items():
            assert FROZEN["scenarios"][name][k] == v != dev["step0"][k], (name, k)


@pytest.mark.parametrize("name", sorted(FROZEN["scenarios"]))
def test_port_matches_frozen_step0_counts(name):
    got = cs.run_offline(cs.scenarios()[name])
    want = FROZEN["scenarios"][name]
    assert {k: got.get(k, 0) for k in cs.PARITY_DETECTORS} == want


def test_frozen_parity_is_not_stale_against_local_step0():
    """Local only: needs a rebuild-lab checkout, e.g. ACE_STEP0_DIR=<rebuild-lab>/ace/step0."""
    step0 = os.environ.get("ACE_STEP0_DIR")
    if not step0 or not os.path.isfile(os.path.join(step0, "detectors.py")):
        pytest.skip("ACE_STEP0_DIR not set; the private Step 0 checkout is not available (CI)")
    fresh = cs.regen_parity(step0)
    assert fresh["source_sha256"] == hashlib.sha256(open(os.path.join(step0, "detectors.py"), "rb").read()).hexdigest()
    assert fresh == FROZEN, "step0_parity.json is stale: rerun python -m tests.compile_support <step0 dir>"


def _generator_with(monkeypatch, tmp_path, step0, reason):
    steps = [{"human": "x"}, {"tool": "Bash", "input": {"command": "ls"}}]
    entry = {"id": "s/one", "steps": steps, **({"deviation": reason} if reason else {})}
    monkeypatch.setattr(cs, "scenarios", lambda: {"s/one": steps})
    monkeypatch.setattr(cs, "load_fixture", lambda: {"payloads": [entry], "extras": []})
    monkeypatch.setattr(cs, "step0_counts", lambda _dir, _steps: {k: step0 for k in cs.PARITY_DETECTORS})
    (tmp_path / "detectors.py").write_text("x")
    (tmp_path / "scanlib.py").write_text("y")
    return cs.regen_parity(str(tmp_path))


def test_generator_refuses_an_unexplained_difference(monkeypatch, tmp_path):
    with pytest.raises(AssertionError, match="unexplained"):
        _generator_with(monkeypatch, tmp_path, step0=1, reason=None)


def test_generator_refuses_a_declared_deviation_that_matches_step0(monkeypatch, tmp_path):
    with pytest.raises(AssertionError, match="matches Step 0"):
        _generator_with(monkeypatch, tmp_path, step0=0, reason="claimed but equal")


def test_generator_records_an_explained_difference(monkeypatch, tmp_path):
    out = _generator_with(monkeypatch, tmp_path, step0=1, reason="why")
    dev = out["deviations"]["s/one"]
    assert dev["reason"] == "why" and dev["step0"]["rm_rf"] == 1 and dev["port"]["rm_rf"] == 0
    assert out["scenarios"]["s/one"]["rm_rf"] == 0
