"""`lab/run.py`: one trial against the FAKE claude (tests/fixtures/lab/fake_claude.py); the real binary is never run."""

import hashlib
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from ace_playbook.lab import run, tasks
from ace_playbook.store import view

FAKE = Path(__file__).resolve().parent / "fixtures" / "lab" / "fake_claude.py"
LESSON = "SECRET-LESSON-TEXT-9f3a only for the told arm"
READ_X = {"events": [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                                                     "input": {"file_path": "/lab/run/x.py"}}]}}],
          "result": {"total_cost_usd": 0.0123, "num_turns": 2}}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    monkeypatch.setenv("ACE_FAKE_DIR", str(fake_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("CLAUDECODE", "1")

    class Lab:
        dir, data, runs = fake_dir, tmp_path / "data", tmp_path / "runs"

        def scenario(self, **sc):
            path = tmp_path / "scenario.json"
            path.write_text(json.dumps(sc))
            monkeypatch.setenv("ACE_FAKE_SCENARIO", str(path))

        def go(self, task_id, arm, **kw):
            return run.trial(tasks.get(task_id), arm, "claude-test-1", LESSON, self.runs / f"{task_id}-{arm}", self.data,
                             claude=str(FAKE), **kw)

        def read(self, name):
            return json.loads((self.dir / name).read_text())

    return Lab()


def test_the_exact_flags_in_the_told_arm(lab):
    lab.scenario(turns=[READ_X])
    rec = lab.go("pc-port", "told")
    argv = lab.read("argv.json")
    assert rec["error"] is None and rec["exit_code"] == 0
    assert argv[:5] == ["-p", "--model", "claude-test-1", "--no-session-persistence", "--setting-sources"] and argv[5] == ""
    assert argv[argv.index("--settings") + 1].endswith("settings.json")
    for flag, val in [("--permission-mode", "acceptEdits"), ("--input-format", "stream-json"), ("--output-format", "stream-json"),
                      ("--max-turns", "20"), ("--max-budget-usd", "0.50")]:
        assert argv[argv.index(flag) + 1] == val
    assert "--verbose" in argv and "--bare" not in argv
    assert argv[argv.index("--append-system-prompt") + 1] == LESSON


DENY = "--disallowedTools=Bash,WebFetch,WebSearch,NotebookEdit,Task,Agent"


@pytest.mark.parametrize("task_id,arm", [("pc-port", "told"), ("pc-port", "not_told"), ("eu-parser", "told"), ("eu-parser", "not_told"),
                                         ("xp-greet", "told"), ("xp-greet", "not_told")])
def test_every_trial_of_every_lesson_passes_one_single_argument_deny_list(lab, task_id, arm):
    lab.scenario(turns=[{}, {}])
    lab.go(task_id, arm)
    argv = lab.read("argv.json")
    assert argv.count(DENY) == 1 and not any(a in ("--disallowedTools", "--disallowed-tools", "--allowedTools") for a in argv)
    i = argv.index(DENY)
    assert argv[i + 1].startswith("--")                                      # the variadic flag swallows nothing: a flag follows it


TOOLS_EDIT = "--tools=Read,Edit,Write,Glob,Grep,ToolSearch"
TOOLS_PLAN = "--tools=Read,Glob,Grep,ToolSearch,ExitPlanMode"
KIND_TOOLS = {"pc-port": TOOLS_EDIT, "eu-parser": TOOLS_EDIT, "xp-greet": TOOLS_PLAN}                # preclaim, edit_unread, exitplan


def test_the_tool_constants_are_the_probed_literals():
    assert (run.TOOLS_EDIT, run.TOOLS_PLAN) == (TOOLS_EDIT, TOOLS_PLAN)


@pytest.mark.parametrize("task_id,arm", [("pc-port", "told"), ("pc-port", "not_told"), ("eu-parser", "told"), ("eu-parser", "not_told"),
                                         ("xp-greet", "told"), ("xp-greet", "not_told")])
def test_every_trial_allowlists_the_built_in_tools_per_kind_and_drops_every_mcp_server(lab, task_id, arm):
    lab.scenario(turns=[{}, {}])
    lab.go(task_id, arm)
    argv = lab.read("argv.json")
    assert [a for a in argv if a.startswith("--tools")] == [KIND_TOOLS[task_id]]            # exactly one, a single `=` element
    assert argv.count("--strict-mcp-config") == 1 and "--mcp-config" not in argv and not any(a.startswith("--mcp-config") for a in argv)
    assert "--tools" not in argv and argv.index("--strict-mcp-config") != len(argv) - 1
    i = argv.index(KIND_TOOLS[task_id])
    assert argv[i + 1].startswith("--")                                      # the variadic flag swallows nothing: a flag follows it
    assert DENY in argv                                                      # the denied list stays as the second layer


def test_the_exitplan_tool_set_is_the_drivers_read_only_set_plus_exit_plan_mode_minus_ls():
    from ace_playbook.lab import exitplan
    names = set(TOOLS_PLAN.split("=", 1)[1].split(","))
    assert names == (exitplan.READ_ONLY | {"ExitPlanMode"}) - {"LS"}         # LS is not among the 25 built-ins of 2.1.317 (probed 2026-11-04)
    assert set(TOOLS_EDIT.split("=", 1)[1].split(",")) == {"Read", "Edit", "Write", "Glob", "Grep", "ToolSearch"}


def test_not_told_has_no_append_system_prompt_and_no_lesson_anywhere_in_argv(lab):
    lab.scenario(turns=[READ_X])
    lab.go("pc-port", "not_told")
    argv = lab.read("argv.json")
    assert "--append-system-prompt" not in argv and LESSON not in " ".join(argv)


def test_the_prompt_goes_through_stdin_never_argv(lab):
    lab.scenario(turns=[READ_X, READ_X])
    task = tasks.get("eu-parser")
    lab.go("eu-parser", "told")
    argv = " ".join(lab.read("argv.json"))
    lines = [json.loads(x) for x in lab.read("stdin_lines.json")]
    assert [x["message"]["content"] for x in lines] == list(task.turns) and all(x["type"] == "user" for x in lines)
    assert all(x["message"]["role"] == "user" for x in lines) and not any(t in argv for t in task.turns)


def test_environment_drops_the_key_and_claudecode_but_keeps_the_basics(lab):
    lab.scenario(turns=[READ_X])
    lab.go("pc-port", "told")
    env = lab.read("env.json")
    assert (env["has_api_key"], env["has_claudecode"]) == (False, False)
    assert all(env[k] == os.environ.get(k) for k in ("HOME", "USER", "LOGNAME", "PATH"))


def test_settings_file_carries_the_stop_hook_for_preclaim_and_plans_dir_only_otherwise(lab):
    lab.scenario(turns=[READ_X, READ_X])
    lab.go("pc-port", "told")
    s = lab.read("settings.json")
    assert set(s) == {"plansDirectory", "hooks"} and s["plansDirectory"] == str(lab.runs / "pc-port-told" / "work" / ".lab-plans")
    cmd = s["hooks"]["Stop"][0]["hooks"][0]
    assert cmd["type"] == "command" and cmd["command"] == str(run.HOOK) and os.access(cmd["command"], os.X_OK)
    assert run.HOOK.name == "stop-mid-task-audit.sh" and (lab.runs / "pc-port-told" / "work" / ".lab-plans").is_dir()
    lab.go("eu-parser", "told")
    assert lab.read("settings.json") == {"plansDirectory": str(lab.runs / "eu-parser-told" / "work" / ".lab-plans")}


@pytest.mark.parametrize("task_id", ["pc-port", "eu-parser", "xp-greet"])
def test_plans_directory_is_inside_the_project_root_exists_and_leaves_git_clean(lab, task_id):
    lab.scenario(turns=[{}, {}])
    lab.go(task_id, "told")
    work = (lab.runs / f"{task_id}-told" / "work").resolve()
    plans = lab.read("settings.json")["plansDirectory"]
    assert plans == str(work / ".lab-plans") and plans.startswith(str(work) + "/") and Path(plans).is_dir()
    assert Path((lab.dir / "cwd.txt").read_text()).resolve() == work


def test_the_agent_runs_in_the_built_work_dir_and_the_between_hook_rewrites_files(lab):
    lab.scenario(turns=[READ_X, READ_X])
    rec = lab.go("eu-hand-notes", "not_told")
    work = lab.runs / "eu-hand-notes-not_told" / "work"
    assert Path((lab.dir / "cwd.txt").read_text()).resolve() == work.resolve() and rec["results"] == ["success", "success"]
    assert "freeze is on Friday" in (work / "notes.txt").read_text() and (work / ".git").is_dir()


def test_the_stream_is_saved_private_and_complete(lab):
    lab.scenario(turns=[READ_X, READ_X])
    rec = lab.go("eu-parser", "told", rep=3)
    path = Path(rec["stream"])
    assert path == lab.data / "streams" / "claude-test-1.eu-parser.told.r3.a1.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(lab.data.stat().st_mode) == 0o700
    types = [json.loads(x)["type"] for x in path.read_text().splitlines()]
    assert types == ["system", "assistant", "result", "assistant", "result"]


def test_the_record_has_the_counts_and_only_a_hash_of_the_lesson(lab):
    lab.scenario(turns=[READ_X, READ_X])
    rec = lab.go("eu-parser", "told", rep=1, pilot=True)
    assert rec["results"] == ["success", "success"] and rec["costs"] == [0.0123, 0.0123] and rec["num_turns"] == [2, 2]
    assert (rec["task"], rec["lesson"], rec["arm"], rec["model"], rec["rep"], rec["pilot"]) == ("eu-parser", "edit_unread", "told",
                                                                                               "claude-test-1", 1, True)
    assert len(rec["lesson_sha16"]) == 16
    assert rec["is_error"] is False and rec["trial_id"] == "pilot.claude-test-1.eu-parser.told.r1" and rec["error"] is None
    assert "SECRET-LESSON" not in json.dumps(rec) and LESSON not in json.dumps(rec)
    json.dumps(rec)


def test_the_lesson_hash_is_sha256_16_and_cost_is_optional(lab):
    lab.scenario(turns=[{"events": []}])
    rec = lab.go("pc-port", "not_told")
    assert rec["lesson_sha16"] == hashlib.sha256(LESSON.encode()).hexdigest()[:16] and rec["costs"] == []


def test_a_process_that_dies_before_its_result_is_an_error_not_an_exception(lab):
    lab.scenario(turns=[{"die": True}])
    rec = lab.go("pc-port", "told")
    assert rec["error"] == "exited before the result" and rec["exit_code"] == 3 and rec["results"] == []


def test_a_hung_process_is_terminated_at_the_timeout_and_killed_if_it_ignores_it(lab):
    lab.scenario(turns=[{"hang": True}], ignore_term=True)
    t0 = time.monotonic()
    rec = lab.go("pc-port", "told", timeout_s=1.0, grace_s=0.5)
    pid = int((lab.dir / "pid").read_text())
    assert rec["error"] == "timeout" and time.monotonic() - t0 < 15 and rec["exit_code"] == -9
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_missing_binary_is_recorded(lab, tmp_path):
    rec = run.trial(tasks.get("pc-port"), "told", "m", LESSON, tmp_path / "r", tmp_path / "d", claude=str(tmp_path / "nope"))
    assert rec["error"] == "spawn failed: FileNotFoundError" and rec["exit_code"] is None


def test_unknown_arm_is_refused(lab):
    with pytest.raises(ValueError):
        lab.go("pc-port", "maybe")


def test_trial_id_is_filename_safe():
    assert run.trial_id("a/b c", "t", "told", 2) == "a_b_c.t.told.r2"


def test_lesson_text_is_read_from_the_store_view_by_id(tmp_path, monkeypatch):
    data = tmp_path / "ace"
    data.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"data_dir": str(data)}))
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(cfg))
    with pytest.raises(LookupError):
        run.lesson_text("preclaim")                                       # no view yet
    con = sqlite3.connect(data / "playbook.sqlite")
    con.executescript(view.DDL)
    row = [tasks.LESSON_IDS["preclaim"], "synthetic lesson text", "global", "", "gotcha", "", "active", 1, 0, "", "",
           0, 0, 0, 0, 0, "[]", "[]", None, None]
    con.execute("INSERT INTO lessons VALUES (" + ",".join("?" * 20) + ")", row)
    con.commit()
    con.close()
    assert run.lesson_text("preclaim") == "synthetic lesson text"
    with pytest.raises(LookupError) as exc:
        run.lesson_text("edit_unread")
    assert "L-f8a355f9" not in str(exc.value)


# ------------------------------------------------------------------ the CLI
def cli(capsys, *argv):
    from ace_playbook.lab import __main__ as cmd
    code = cmd.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def cli_env(lab, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path / "labdata"))
    monkeypatch.setattr(run, "lesson_text", lambda lesson: LESSON)
    lab.scenario(turns=[READ_X])
    return tmp_path / "labdata"


def trials_of(data):
    return [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]


def test_cli_tasks_lists_ids_and_lessons(capsys):
    code, out, _ = cli(capsys, "tasks")
    assert code == 0 and all(t.id in out and t.lesson in out for t in tasks.TASKS)


def test_cli_dry_run_prints_the_plan_and_touches_nothing(capsys, cli_env, monkeypatch):
    monkeypatch.setattr(run, "lesson_text", lambda lesson: pytest.fail("the dry run must not read the store"))
    code, out, _ = cli(capsys, "run", "--model", "m1", "--lesson", "preclaim", "--reps", "1", "--seed", "1", "--dry-run")
    n = len([t for t in tasks.TASKS if t.lesson == "preclaim"]) * 2
    assert code == 0 and out.count("\n") == n + 1 and f"[1/{n}] m1.pc-" in out and "nothing was run" in out
    assert not cli_env.exists()


def test_cli_the_shuffle_is_seeded(capsys, cli_env):
    args = ("run", "--model", "m1", "--lesson", "edit_unread", "--reps", "2", "--dry-run")
    a, b, c = (cli(capsys, *args, "--seed", s)[1] for s in ("5", "5", "6"))
    assert a == b and a != c


def test_cli_runs_every_trial_once_records_private_json_and_prints_counts_only(capsys, cli_env):
    code, out, err = cli(capsys, "run", "--model", "m1", "--lesson", "preclaim", "--reps", "2", "--seed", "3", "--tasks", "pc-port,pc-ini",
                         "--claude", str(FAKE))
    rows = trials_of(cli_env)
    assert code == 0 and len(rows) == 8 and len(out.splitlines()) == 8 and err == ""
    assert {(r["task"], r["arm"], r["rep"]) for r in rows} == {(t, a, r) for t in ("pc-port", "pc-ini") for a in run.ARMS for r in (0, 1)}
    assert stat.S_IMODE((cli_env / "trials.jsonl").stat().st_mode) == 0o600 and all(not r["pilot"] and r["error"] is None for r in rows)
    assert "violations" not in out and "exit=0" in out and "results=success" in out and LESSON not in out + json.dumps(rows)
    assert all(x.startswith("[") and "] m1." in x for x in out.splitlines())


def test_cli_resume_skips_recorded_trials_and_the_stop_file_halts_cleanly(capsys, cli_env):
    base = ("run", "--model", "m1", "--lesson", "preclaim", "--reps", "1", "--seed", "3", "--tasks", "pc-port", "--claude", str(FAKE))
    cli(capsys, *base, "--arms", "told")
    assert len(trials_of(cli_env)) == 1
    (cli_env / "STOP").write_text("")
    code, out, _ = cli(capsys, *base)
    assert code == 0 and "stopped" in out and len(trials_of(cli_env)) == 1
    (cli_env / "STOP").unlink()
    code, out, _ = cli(capsys, *base)
    assert len(trials_of(cli_env)) == 2 and out.count("\n") == 1 and "not_told" in out          # told was skipped
    code, out, _ = cli(capsys, *base)
    assert out == "" and len(trials_of(cli_env)) == 2


def test_cli_pilot_flag_is_recorded(capsys, cli_env):
    cli(capsys, "run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--tasks", "pc-port", "--arms", "told", "--pilot",
        "--claude", str(FAKE))
    assert [r["pilot"] for r in trials_of(cli_env)] == [True]


def test_cli_usage_errors_exit_2_without_running(capsys, cli_env):
    for extra in (["--tasks", "eu-parser"], ["--arms", "told,maybe"], ["--reps", "0"], ["--tasks", "nope"]):
        code, out, err = cli(capsys, "run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--claude", str(FAKE), *extra)
        assert (code, out) == (2, "") and err.startswith("error:")
    assert not cli_env.exists()


def test_cli_an_unreadable_store_exits_2_and_names_no_text(capsys, cli_env, monkeypatch):
    def boom(lesson):
        raise LookupError("the lesson is not in the store")
    monkeypatch.setattr(run, "lesson_text", boom)
    code, out, err = cli(capsys, "run", "--model", "m1", "--lesson", "preclaim", "--seed", "1", "--claude", str(FAKE))
    assert code == 2 and "LookupError" in err and not (cli_env / "trials.jsonl").exists()


def test_the_run_dir_is_resolved_once_and_the_resolved_path_is_the_cwd_the_drive_and_the_control_work(lab, monkeypatch):
    """Opus round 6: the cwd, `drive` and `exitplan.Control` all get the same symlink-free absolute string."""
    (lab.runs / "real").mkdir(parents=True)
    (lab.runs / "alias").symlink_to(lab.runs / "real")
    seen = {}
    real_popen, real_drive, real_control = run.subprocess.Popen, run.drive.drive, run.exitplan.Control

    def popen(argv, **kw):
        seen["cwd"] = kw.get("cwd")
        return real_popen(argv, **kw)

    def drive(proc, task, work, *a, **kw):
        seen["drive"] = work
        return real_drive(proc, task, work, *a, **kw)

    class Control(real_control):
        def __init__(self, proc, sink, work):
            seen["control"] = work
            super().__init__(proc, sink, work)

    monkeypatch.setattr(run.subprocess, "Popen", popen)
    monkeypatch.setattr(run.drive, "drive", drive)
    monkeypatch.setattr(run.exitplan, "Control", Control)
    lab.scenario(turns=[{}, {}])
    rec = run.trial(tasks.get("xp-greet"), "not_told", "claude-test-1", LESSON, lab.runs / "alias" / "r1", lab.data, claude=str(FAKE))
    want = str((lab.runs / "real" / "r1" / "work").resolve())
    assert rec["error"] is None and want != str(lab.runs / "alias" / "r1" / "work")
    assert str(seen["cwd"]) == str(seen["drive"]) == str(seen["control"]) == want
