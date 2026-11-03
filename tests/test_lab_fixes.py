"""Lab wave 2, part A: the round-1 review fixes in the runner and the CLI (fake claude only; the real binary is never run)."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import time

import pytest

from ace_playbook.lab import __main__ as cmd
from ace_playbook.lab import run, stats, tasks

from .test_lab_run import FAKE, READ_X, cli, cli_env, lab, trials_of  # noqa: F401  (fixtures and helpers)


def test_the_fake_fails_a_driver_that_writes_every_turn_up_front(lab):
    lab.scenario(turns=[READ_X, READ_X])
    p = subprocess.Popen([str(FAKE), "--model", "m"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}) + "\n"
    p.stdin.write(line + line)
    p.stdin.flush()
    out, err = p.communicate(timeout=30)
    assert p.returncode == 97 and "already waiting" in err and '"type": "result"' not in out


def test_the_real_driver_passes_the_fake_stdin_check_turn_by_turn(lab):
    lab.scenario(turns=[READ_X, READ_X, READ_X])
    rec = lab.go("eu-three-turns", "told")
    assert rec["error"] is None and rec["exit_code"] == 0 and rec["results"] == ["success"] * 3


def test_a_term_ignoring_grandchild_that_holds_stdout_is_killed_and_the_trial_returns_fast(lab):
    lab.scenario(turns=[{"hang": True}], grandchild=True)
    t0 = time.monotonic()
    rec = lab.go("pc-port", "told", timeout_s=1.0, grace_s=0.5)
    assert rec["error"] == "timeout" and time.monotonic() - t0 < 10
    gone = False
    for _ in range(50):
        try:
            os.kill(int((lab.dir / "grandchild_pid").read_text()), 0)
        except ProcessLookupError:
            gone = True
            break
        time.sleep(0.1)
    assert gone


def test_the_pilot_mark_is_part_of_the_trial_id_and_a_main_run_after_a_pilot_runs_everything(capsys, cli_env):
    assert run.trial_id("m", "t", "told", 1, pilot=True) == "pilot.m.t.told.r1" and run.trial_id("m", "t", "told", 1) == "m.t.told.r1"
    base = ("run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--tasks", "pc-port", "--arms", "told", "--claude", str(FAKE))
    cli(capsys, *base, "--pilot")
    cli(capsys, *base)
    cli(capsys, *base)                                                                              # resumes: nothing new
    rows = trials_of(cli_env)
    assert [(r["trial_id"], r["pilot"]) for r in rows] == [("pilot.m1.pc-port.told.r0", True), ("m1.pc-port.told.r0", False)]
    assert len({r["stream"] for r in rows}) == 2


def test_a_build_exception_is_recorded_not_raised(lab, monkeypatch):
    lab.scenario(turns=[READ_X])
    monkeypatch.setattr(tasks, "build", lambda task, run_dir: (_ for _ in ()).throw(OSError("disk")))
    rec = lab.go("pc-port", "told")
    assert rec["error"] == "build failed: OSError" and rec["exit_code"] is None


def test_a_between_exception_is_recorded_not_raised(lab, monkeypatch):
    lab.scenario(turns=[READ_X, READ_X])
    monkeypatch.setattr(tasks, "between", lambda task, work, i: (_ for _ in ()).throw(KeyError("x")))
    rec = lab.go("eu-parser", "told")
    assert rec["error"] == "between failed: KeyError" and rec["exit_code"] is not None


def test_permission_denials_are_counted_per_trial_from_the_result_events(lab):
    lab.scenario(turns=[{"result": {"permission_denials": [{"tool_name": "Bash"}, {"tool_name": "Bash"}]}},
                        {"result": {"permission_denials": "x"}}, {}])
    assert lab.go("eu-three-turns", "told")["permission_denials"] == 2
    lab.scenario(turns=[READ_X])
    assert lab.go("pc-port", "told")["permission_denials"] == 0


def test_a_relative_claude_path_is_resolved_before_the_work_dir_becomes_the_cwd(capsys, cli_env, monkeypatch):
    monkeypatch.chdir(FAKE.parent)
    code, out, _ = cli(capsys, "run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--tasks", "pc-port", "--arms", "told",
                       "--claude", "./fake_claude.py")
    [r] = trials_of(cli_env)
    assert code == 0 and r["error"] is None and r["exit_code"] == 0


def run_help():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cmd.main(["run", "--help"])
    return " ".join(buf.getvalue().split())


def test_an_errored_trial_is_retried_on_resume_and_the_later_record_wins(capsys, cli_env):
    base = ("run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--tasks", "pc-port", "--arms", "told")
    cli(capsys, *base, "--claude", str(cli_env / "nope"))
    assert [r["error"] for r in trials_of(cli_env)] == ["spawn failed: FileNotFoundError"]
    assert cmd.recorded(cli_env / "trials.jsonl") == set()
    cli(capsys, *base, "--claude", str(FAKE))
    rows = trials_of(cli_env)
    assert [r["error"] for r in rows] == ["spawn failed: FileNotFoundError", None]
    assert cmd.recorded(cli_env / "trials.jsonl") == {rows[0]["trial_id"]}
    scored, excluded = stats.load_rows(cli_env / "trials.jsonl")
    assert len(scored) == 1 and excluded == {}
    assert cli(capsys, *base, "--claude", str(FAKE))[1] == "" and len(trials_of(cli_env)) == 2
    assert "retried" in run_help()


# ------------------------------------------------------------------ wave 8: an error result or a nonzero exit is a trial error
ERR = {"is_error": True, "subtype": "error_during_execution"}


def test_an_is_error_result_sets_the_error_and_keeps_the_record(lab):
    lab.scenario(turns=[{"result": {**ERR, "total_cost_usd": 0.5}}])
    rec = lab.go("pc-port", "told")
    assert rec["error"] == "result error: error_during_execution" and rec["is_error"] is True
    assert rec["results"] == ["error_during_execution"] and rec["costs"] == [0.5] and rec["exit_code"] is not None


def test_a_non_success_subtype_without_is_error_sets_the_error(lab):
    lab.scenario(turns=[{"result": {"subtype": "error_max_budget_usd"}}])
    rec = lab.go("pc-port", "told")
    assert rec["error"] == "result error: error_max_budget_usd" and rec["is_error"] is False and rec["results"] == ["error_max_budget_usd"]


def test_an_error_result_in_a_later_turn_is_recorded_after_the_earlier_ones(lab):
    lab.scenario(turns=[{}, {"result": ERR}])
    rec = lab.go("eu-parser", "told")
    assert rec["error"] == "result error: error_during_execution" and rec["results"] == ["success", "error_during_execution"]


def test_a_nonzero_exit_with_clean_results_sets_exit_code_as_the_error(lab):
    lab.scenario(turns=[{}], exit=2)
    rec = lab.go("pc-port", "told")
    assert rec["error"] == "exit 2" and rec["exit_code"] == 2 and rec["results"] == ["success"]


def test_a_nonzero_exit_never_replaces_an_earlier_error(lab):
    lab.scenario(turns=[{"result": ERR}], exit=2)
    assert lab.go("pc-port", "told")["error"] == "result error: error_during_execution"


def test_a_clean_trial_has_no_error(lab):
    lab.scenario(turns=[{}, {}])
    rec = lab.go("eu-parser", "told")
    assert rec["error"] is None and rec["exit_code"] == 0 and rec["is_error"] is False and rec["results"] == ["success", "success"]


@pytest.mark.parametrize("bad", [dict(turns=[{"result": ERR}]), dict(turns=[{"result": {"subtype": "error_max_budget_usd"}}]), dict(turns=[{}], exit=2)])
def test_such_a_trial_is_excluded_by_report_and_retried_by_the_next_run(capsys, cli_env, lab, bad):
    base = ("run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--tasks", "pc-port", "--arms", "told", "--claude", str(FAKE))
    lab.scenario(**bad)
    out = cli(capsys, *base)[1]
    assert " error=" in out and "violations" not in out
    assert cmd.recorded(cli_env / "trials.jsonl") == set()
    assert stats.load_rows(cli_env / "trials.jsonl") == ([], {"m1/preclaim/told": 1})
    lab.scenario(turns=[{}])
    out = cli(capsys, *base)[1]
    rows = trials_of(cli_env)
    assert out.count("\n") == 1 and " error=" not in out and [r["error"] is None for r in rows] == [False, True]
    assert cmd.recorded(cli_env / "trials.jsonl") == {rows[0]["trial_id"]} and len(stats.load_rows(cli_env / "trials.jsonl")[0]) == 1
    assert cli(capsys, *base)[1] == "" and len(trials_of(cli_env)) == 2


# ------------------------------------------------------------------ wave 9, C-B: one stream per attempt; the safety count reads every attempt
ESC = {"events": [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "e1", "name": "Read", "input": {"file_path": "/etc/hosts"}}]}}],
       "result": {"is_error": True, "subtype": "error_max_turns"}}


def attempt(lab, turns, task="pc-port", **kw):
    """One trial against the fake, its record appended to trials.jsonl the way `run` does."""
    lab.scenario(turns=turns)
    shutil.rmtree(lab.runs, ignore_errors=True)                                                         # as `run` clears the run dir
    rec = lab.go(task, "told", **kw)
    with run.open_private(lab.data / "trials.jsonl", append=True) as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def test_an_errored_attempt_with_an_escape_then_a_clean_retry_keeps_both_streams_and_the_safety_count(lab):
    bad = attempt(lab, [ESC])
    assert bad["error"] == "result error: error_max_turns" and stats.outside(lab.data / "trials.jsonl")["total"] == 1
    good = attempt(lab, [{}])
    assert good["error"] is None and bad["stream"] != good["stream"]
    assert bad["stream"].endswith(".a1.jsonl") and good["stream"].endswith(".a2.jsonl")
    assert "/etc/hosts" in open(bad["stream"]).read() and "/etc/hosts" not in open(good["stream"]).read()
    found = stats.outside(lab.data / "trials.jsonl")
    assert found["total"] >= 1 and found["trials"] == [bad["trial_id"]] and found["unreadable"] == 0
    assert len(stats.load_rows(lab.data / "trials.jsonl")[0]) == 1                                      # scoring: the last record per id


def test_a_clean_first_attempt_has_one_stream_and_the_same_numbers(lab):
    rec = attempt(lab, [READ_X])
    assert rec["stream"].endswith(".a1.jsonl")
    assert stats.outside(lab.data / "trials.jsonl") == {"total": 1, "unreadable": 0, "bad_streams": 0, "trials": [rec["trial_id"]]}
    assert [p.name for p in (lab.data / "streams").iterdir()] == [rec["trial_id"] + ".a1.jsonl"]


def test_an_existing_stream_is_never_opened_for_writing_so_a_collision_bumps_the_attempt_number(lab):
    streams = lab.data / "streams"
    streams.mkdir(parents=True)
    taken = streams / "claude-test-1.pc-port.told.r0.a1.jsonl"
    taken.write_text("KEEP\n")
    lab.scenario(turns=[{}])
    rec = lab.go("pc-port", "told")
    assert taken.read_text() == "KEEP\n" and rec["stream"].endswith(".a2.jsonl") and rec["error"] is None


def test_an_old_layout_stream_still_reports(tmp_path):
    old = tmp_path / "m.t.told.r0.jsonl"
    old.write_text(json.dumps({"type": "system", "subtype": "init", "cwd": "/r/work"}) + "\n" + json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "/etc/x"}}]}}) + "\n")
    base = {"trial_id": "m.t.told.r0", "stream": str(old), "task": "pc-port", "lesson": "preclaim", "arm": "told", "model": "m", "pilot": False}
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in [{**base, "error": "timeout"}, {**base, "error": None}]) + "\n")
    assert stats.outside(tmp_path / "trials.jsonl") == {"total": 1, "unreadable": 0, "bad_streams": 0, "trials": ["m.t.told.r0"]}   # one stream, once


# ------------------------------------------------------------------ wave 9, m1: the retry cap
def test_a_trial_with_max_attempts_errored_records_is_not_retried_and_stays_excluded(capsys, cli_env):
    base = ("run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--tasks", "pc-port", "--arms", "told")
    for _ in range(3):
        cli(capsys, *base, "--claude", str(cli_env / "nope"))
    assert len(trials_of(cli_env)) == 3
    out = cli(capsys, *base, "--claude", str(FAKE))[1]
    assert len(trials_of(cli_env)) == 3 and "cap" in out and stats.load_rows(cli_env / "trials.jsonl") == ([], {"m1/preclaim/told": 1})
    out = cli(capsys, *base, "--claude", str(FAKE), "--max-attempts", "4")[1]
    rows = trials_of(cli_env)
    assert len(rows) == 4 and rows[-1]["error"] is None and rows[-1]["stream"].endswith(".a4.jsonl") and "cap" not in out


def test_max_attempts_defaults_to_three_and_must_be_at_least_one(capsys, cli_env):
    assert "--max-attempts" in run_help() and "(default: 3)" in run_help()
    base = ("run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--tasks", "pc-port", "--arms", "told", "--claude", str(FAKE))
    assert cli(capsys, *base, "--max-attempts", "0")[0] == 2 and not (cli_env / "trials.jsonl").exists()
