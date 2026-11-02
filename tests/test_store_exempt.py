"""`python -m ace_playbook.store exempt|unexempt <lesson_id>`: the operator verbs for `holdout_exempt`, mirroring `pin` / `unpin`."""

import json

import pytest

from ace_playbook.store import DeltaLog, view
from ace_playbook.store import __main__ as cli

from .helpers import GLOBAL, add_draft


@pytest.fixture
def env(tmp_path):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data")}))
    log = DeltaLog(tmp_path / "data" / "deltas.jsonl")
    ids = []
    for text in ("Use python3, not python, for scripts", "Read a file before you edit it"):
        d = add_draft(text, GLOBAL, status="active")
        log.append(d, ts="2026-01-01T00:00:00Z")
        ids.append(d["lesson_id"])
    return conf, log, ids


def run(conf, *argv):
    return cli.main(["--config", str(conf), *argv])


def flags(log):
    return {k: (v["holdout_exempt"], v["pinned"]) for k, v in log.rebuild_view(log.path.parent / "view.sqlite").items()}


def test_exempt_and_unexempt_write_a_patch_delta_and_rebuild_the_view(env, capsys):
    conf, log, (a, b) = env
    assert run(conf, "exempt", a) == 0
    last = json.loads(log.path.read_text().splitlines()[-1])
    assert (last["op"], last["lesson_id"], last["fields"]) == ("PATCH", a, {"holdout_exempt": True}) and log.verify() == 3
    con = view.open_view(conf.parent / "data" / "playbook.sqlite")
    assert {r["id"]: r["holdout_exempt"] for r in con.execute("SELECT * FROM lessons")} == {a: 1, b: 0}
    assert run(conf, "unexempt", a) == 0
    assert json.loads(log.path.read_text().splitlines()[-1])["fields"] == {"holdout_exempt": False}
    assert flags(log)[a] == (False, False)
    capsys.readouterr()


def test_the_verbs_leave_pinned_alone_and_pin_leaves_exempt_alone(env, capsys):
    conf, log, (a, _b) = env
    assert run(conf, "pin", a) == 0 and run(conf, "exempt", a) == 0 and flags(log)[a] == (True, True)
    assert run(conf, "unpin", a) == 0 and flags(log)[a] == (True, False)
    capsys.readouterr()


def test_list_marks_an_exempt_lesson_and_leaves_every_other_line_as_it_was(env, capsys):
    conf, log, (a, b) = env
    assert run(conf, "list") == 0
    before = capsys.readouterr().out.splitlines()
    assert before == [f"  {k} [active] (global:-) {t}" for k, t in sorted([(a, "Use python3, not python, for scripts"),
                                                                           (b, "Read a file before you edit it")])]
    assert run(conf, "exempt", a) == 0 and run(conf, "list") == 0
    after = capsys.readouterr().out.splitlines()
    assert [x for x in after if a not in x] == [x for x in before if a not in x] and len(after) == 2
    assert "exempt" in next(x for x in after if a in x) and "exempt" not in next(x for x in after if b in x)
    assert run(conf, "search", "python3") == 0 and "exempt" in capsys.readouterr().out
    assert run(conf, "unexempt", a) == 0 and run(conf, "list") == 0
    assert capsys.readouterr().out.splitlines() == before


def test_an_unknown_lesson_is_refused_like_pin(env, capsys):
    conf, log, _ = env
    before = log.path.read_text()
    for verb in ("pin", "exempt"):
        assert run(conf, verb, "L-00000000") == 2
        assert capsys.readouterr().err.startswith("error:")
    assert log.path.read_text() == before


def test_the_verbs_take_exactly_one_lesson_id(env):
    conf, _log, _ids = env
    for verb in ("exempt", "unexempt"):
        with pytest.raises(SystemExit) as e:
            run(conf, verb)
        assert e.value.code == 2
