"""Lab wave 2, part A: the round-1 review fixes in the runner and the CLI (fake claude only; the real binary is never run)."""

import contextlib
import io
import json
import os
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
