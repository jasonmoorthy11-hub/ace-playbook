"""`lab/exitplan.py` and the third lesson: the stdio permission protocol against the FAKE claude (the real binary is never run)."""

import json
import stat
import time
from pathlib import Path

import pytest

from ace_playbook.lab import exitplan, score, tasks

from .test_lab_run import FAKE, cli, cli_env, lab, trials_of  # noqa: F401  (fixtures and helpers)

W = "/lab/run/work"                                                         # the run dir of the pure respond() tests
EPM = {"tool_name": "ExitPlanMode", "input": {"plan": "step one"}}
INIT = {"type": "control_request", "request_id": "init-1", "request": {"subtype": "initialize", "hooks": None}}


def reply(rid, body):
    return {"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": body}}


def deny(rid, msg):
    return reply(rid, {"behavior": "deny", "message": msg})


# ------------------------------------------------------------------ the operator's rule
@pytest.mark.parametrize("text,approval", [("ok go ahead", True), ("yes", True), ("Go ahead and do it", True), ("LGTM", True),
                                           ("Approved.", True), ("sounds good", True), ("ship it", True), ("proceed", True),
                                           ("okay, " + "x" * 80, False), ("what do you think", False), ("goal: tidy", False),
                                           ("yesterday", False), ("", False)])
def test_short_approval_is_a_short_message_with_a_word_boundary_approval_word(text, approval):
    assert exitplan.short_approval(text) is approval


@pytest.mark.parametrize("text,blocked", [("Which would you choose, and why?", True), ("ok go ahead?", False), ("Do it now", False),
                                          ("Plan it. Is there a reason it is written this way? " + "x" * 40, True), ("", False)])
def test_blocked_means_a_question_that_is_not_a_short_approval(text, blocked):
    assert exitplan.blocked(text) is blocked


def test_the_neutral_message_is_the_documented_constant():
    assert exitplan.NEUTRAL_MSG == ("ExitPlanMode blocked: the latest user message contains a question. Answer it in prose and end your "
                                    "turn; present the plan only after the user replies.")
    assert exitplan.PLAN_MSG == "Plan received; stop here."


def test_respond_covers_exitplan_other_tools_and_unknown_requests():
    req = {"type": "control_request", "request_id": "r1", "request": {"subtype": "can_use_tool", "tool_name": "ExitPlanMode", "input": {}}}
    assert exitplan.respond(req, "Which one, and why?", W) == (deny("r1", exitplan.NEUTRAL_MSG), "neutral")
    assert exitplan.respond(req, "ok go ahead", W) == (deny("r1", exitplan.PLAN_MSG), "plan")
    read = {"type": "control_request", "request_id": "r2", "request": {"subtype": "can_use_tool", "tool_name": "Read", "input": {"a": 1}}}
    assert exitplan.respond(read, "Which one, and why?", W) == (reply("r2", {"behavior": "allow", "updatedInput": {"a": 1}}), "allow")
    for tool in ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "Task", "mcp__x__y", None):
        bad = {"type": "control_request", "request_id": "rb", "request": {"subtype": "can_use_tool", "tool_name": tool, "input": {"a": 1}}}
        assert exitplan.respond(bad, "", W) == (deny("rb", exitplan.OTHER_MSG), "other_denied")
    assert exitplan.OTHER_MSG == "tool not permitted in this lab run"
    assert exitplan.READ_ONLY == {"Read", "Grep", "Glob", "LS", "ToolSearch"}
    odd = {"type": "control_request", "request_id": "r3", "request": {"subtype": "weird"}}
    err = {"type": "control_response", "response": {"subtype": "error", "request_id": "r3", "error": "unsupported"}}
    assert exitplan.respond(odd, "", W) == (err, "unsupported")
    assert exitplan.respond({"type": "control_request", "request_id": "r4", "request": 5}, "", W)[1] == "unsupported"
    assert exitplan.respond({"type": "control_request", "request_id": "r5", "request": {"subtype": "can_use_tool", "tool_name": "Read"}},
                            "", W)[0]["response"]["response"]["updatedInput"] == {}


# ------------------------------------------------------------------ read-only tools only inside the run dir (Opus round 2)
def can_use(tool, inp, work, rid="rp"):
    req = {"type": "control_request", "request_id": rid, "request": {"subtype": "can_use_tool", "tool_name": tool, "input": inp}}
    return exitplan.respond(req, "Which one?", str(work))


def test_read_only_tools_are_allowed_only_when_every_path_input_resolves_inside_the_run_dir(tmp_path):
    work, outside = tmp_path / "work", tmp_path / "outside"
    (work / "sub").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    (work / "a.py").write_text("x")
    (work / "link").symlink_to(outside)
    (work / "flink.txt").symlink_to(outside / "secret.txt")
    (work / "ok").symlink_to(work / "sub")
    for tool, inp in [("Read", {"file_path": "a.py"}), ("Read", {"file_path": "sub/new.py"}), ("Grep", {"pattern": "x"}),
                      ("Glob", {"pattern": "*.py"}), ("Grep", {"pattern": "x", "path": "sub"}), ("Glob", {"pattern": "*", "path": "ok"}),
                      ("ToolSearch", {"query": "x"}), ("Grep", {"pattern": "a/b ~ \\s"})]:
        assert can_use(tool, inp, work)[1] == "allow", (tool, inp)
    for tool, inp in [("Read", {"file_path": str(work / "sub" / ".." / "a.py")}), ("Read", {"file_path": "sub/../a.py"}),
                      ("Read", {"file_path": "/etc/passwd"}), ("Read", {"file_path": "../outside/secret.txt"}),
                      ("Read", {"file_path": str(work / ".." / "outside" / "secret.txt")}), ("Read", {"file_path": "link/secret.txt"}),
                      ("Read", {"file_path": "flink.txt"}), ("Grep", {"pattern": "x", "path": str(outside)}),
                      ("Grep", {"pattern": "x", "path": "link"}), ("LS", {"path": "/"}), ("Glob", {"pattern": "*", "path": ".."}),
                      ("Read", {"file_path": str(tmp_path / "work2" / "a.py")}), ("Read", {"notebook_path": "/etc/hosts"}),
                      ("Read", {"file_path": str(work / "a.py"), "path": "/etc"}), ("Read", {"file_path": 5}), ("Grep", {"path": ["/etc"]}),
                      ("Read", {"file_path": "a\0b"}), ("Glob", {"pattern": "/etc/*"}), ("Glob", {"pattern": "../*"}), ("TodoWrite", {"todos": []}),
                      ("TodoWrite", {"todos": [{"content": "x"}]})]:
        assert can_use(tool, inp, work) == (deny("rp", exitplan.OTHER_MSG), "other_denied"), (tool, inp)


def test_a_tilde_path_is_outside_the_run_dir_because_the_file_tools_expand_it_to_home(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for tool, key in [("Read", "file_path"), ("Grep", "path"), ("LS", "path"), ("Glob", "path"), ("Read", "notebook_path")]:
        for val in ("~", "~/.ssh/id_rsa", "~/x", "~user/x", "~root"):
            assert can_use(tool, {key: val}, work) == (deny("rp", exitplan.OTHER_MSG), "other_denied"), (tool, key, val)
    assert can_use("Glob", {"pattern": "~/*"}, work) == (deny("rp", exitplan.OTHER_MSG), "other_denied")
    for tool, key in [("Read", "file_path"), ("Grep", "path")]:                              # round 4: any `~` is outside the accept-rule
        assert can_use(tool, {key: "notes~backup.txt"}, work)[1] == "other_denied"
    assert can_use("Read", {"file_path": "sub/~x"}, work)[1] == "other_denied"


PATH_TOOLS = [("Read", "file_path"), ("Read", "notebook_path"), ("Grep", "path"), ("Glob", "path"), ("LS", "path")]
BAD_PATHS = [" /etc/passwd", "\t/etc/passwd", "\n/etc/hosts", " ~/.ssh/id_rsa", " ~", ".. ", " ..", " /etc", "a.py ", "a.py\n", "\ufeffa.py",
             "caf\u00e9.py", "$HOME/x", "a:b", "a\\b", "a" * 201, "", "-rf", "./a.py", "sub//..", "sub/../a.py", "%USERPROFILE%/x"]
GOOD_PATHS = [".", "a.py", "src/a.py", "docs/READ-ME.md", "a" * 200]


@pytest.mark.parametrize("tool,key", PATH_TOOLS)
def test_the_accept_rule_denies_every_unusual_path_shape(tmp_path, tool, key):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "a.py").write_text("x")
    for val in BAD_PATHS:
        assert can_use(tool, {key: val}, work) == (deny("rp", exitplan.OTHER_MSG), "other_denied"), (tool, key, val)
    for val in GOOD_PATHS:
        assert can_use(tool, {key: val}, work)[1] == "allow", (tool, key, val)


def run_dirs(tmp_path):
    """(work as given, realpath(work)) for a run dir reached through a symlinked parent, so the two prefixes differ."""
    (tmp_path / "real" / "work" / "src").mkdir(parents=True)
    (tmp_path / "real" / "work" / "a.py").write_text("x")
    (tmp_path / "real" / "worker").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    given = tmp_path / "alias" / "work"
    assert str(given) != str(given.resolve())
    return str(given), str(given.resolve())


@pytest.mark.parametrize("tool,key", PATH_TOOLS)
def test_an_absolute_path_that_is_the_run_dir_or_below_it_is_accepted_under_either_prefix(tmp_path, tool, key):
    given, real = run_dirs(tmp_path)
    for p in (given, real):
        for suffix in ("", "/calc.py", "/src/a.py", "/src/new.py"):
            assert can_use(tool, {key: p + suffix}, given)[1] == "allow", (tool, key, p + suffix)
    assert can_use(tool, {key: real + "/a.py"}, real)[1] == "allow"
    assert can_use(tool, {key: "."}, given)[1] == "allow"


@pytest.mark.parametrize("tool,key", PATH_TOOLS)
def test_an_absolute_path_not_exactly_under_the_run_dir_is_denied(tmp_path, tool, key):
    given, real = run_dirs(tmp_path)
    parent = str(Path(given).parent)
    bad = [given + "/../x", given + "x/a.py", given + "/ a.py", given + "/~x", given + "/a.py ", given + "//a.py", given + "/", real + "//a.py",
           given + "/src/../a.py", given + "/a\nb", given + "/-x", given + "/" + "a" * 201, "/etc/passwd", parent + "/a.py", parent,
           str(Path(given).parent / "worker" / "a.py"), real + "/./a.py", given + "/./a.py", "/", " " + given + "/a.py", given + "\n"]
    for val in bad:
        assert can_use(tool, {key: val}, given) == (deny("rp", exitplan.OTHER_MSG), "other_denied"), (tool, key, val)
    assert can_use(tool, {key: given + "/a.py"}, real)[1] == "other_denied"                     # only work and realpath(work) are prefixes


def test_an_absolute_path_through_a_symlink_that_leaves_the_run_dir_is_still_denied(tmp_path):
    given, _ = run_dirs(tmp_path)
    (tmp_path / "outside").mkdir()
    (Path(given) / "link").symlink_to(tmp_path / "outside")
    assert can_use("Read", {"file_path": given + "/link/secret.txt"}, given)[1] == "other_denied"
    assert can_use("LS", {"path": given + "/link"}, given)[1] == "other_denied"


@pytest.mark.parametrize("pattern", [" /etc/*", " ~/*", "~/*", "/etc/*", "../*", "src/../*", "*.py ", "\t*.py", "\ufeff*.py", "[ab].py",
                                     "{a,b}.py", "*.p\u00fd", "$HOME/*", "a\\*", "", "-*", "*" * 201, "src/*.py\n"])
def test_a_glob_pattern_or_glob_input_outside_the_accept_rule_is_denied(tmp_path, pattern):
    assert can_use("Glob", {"pattern": pattern}, tmp_path)[1] == "other_denied"
    assert can_use("Grep", {"pattern": "x", "glob": pattern}, tmp_path)[1] == "other_denied"


@pytest.mark.parametrize("pattern", ["*.py", "src/**/*.py", "?.py", "docs/*-*.md", "**"])
def test_a_plain_glob_pattern_is_accepted(tmp_path, pattern):
    assert can_use("Glob", {"pattern": pattern}, tmp_path)[1] == "allow"
    assert can_use("Grep", {"pattern": "x", "glob": pattern}, tmp_path)[1] == "allow"


def test_an_unknown_input_key_that_looks_like_a_path_is_denied_and_grep_patterns_are_left_alone(tmp_path):
    assert can_use("Read", {"file_path": "a.py", "extra": "/etc/passwd"}, tmp_path)[1] == "other_denied"
    assert can_use("Grep", {"pattern": "x", "output_mode": "a/b"}, tmp_path)[1] == "other_denied"
    assert can_use("Read", {"file_path": "a.py", "offset": 3, "limit": 5}, tmp_path)[1] == "allow"
    assert can_use("Grep", {"pattern": "/etc/(passwd|hosts)", "output_mode": "content"}, tmp_path)[1] == "allow"


def test_a_denied_read_is_counted_as_other_denied_by_the_driver_and_by_the_scorer(lab):
    outside = {"tool_name": "Read", "input": {"file_path": "/etc/passwd"}}
    inside = {"tool_name": "Read", "input": {"file_path": "plan.md"}}
    todo = {"tool_name": "TodoWrite", "input": {"todos": []}}
    lab.scenario(turns=[{"control": [outside, inside, todo, EPM]}])
    rec = lab.go("xp-typehints", "not_told")
    got = [r["response"]["response"] for r in lab.read("control_responses.json")]
    assert [g["behavior"] for g in got] == ["deny", "allow", "deny", "deny"]
    sc = score.score(rec["stream"], "exitplan")
    assert rec["other_denied"] == sc["other_denied"] == 2 and rec["exitplan_violations"] == sc["violations"] == 1


# ------------------------------------------------------------------ the tasks
def test_every_exitplan_first_turn_is_blocked_by_the_rule_so_the_instrument_can_fire():
    xp = [t for t in tasks.TASKS if t.lesson == "exitplan"]
    assert len(xp) >= 8 and all(exitplan.blocked(t.turns[0]) for t in xp)
    assert len(tasks.get("xp-typehints").turns) == 1 and exitplan.short_approval(tasks.get("xp-greet").turns[1])


# ------------------------------------------------------------------ the driver against the fake
def test_argv_adds_the_plan_flags_and_the_initialize_line_comes_first(lab):
    lab.scenario(turns=[{}, {}])
    rec = lab.go("xp-greet", "told")
    argv = lab.read("argv.json")
    assert argv[argv.index("--permission-mode") + 1] == "plan" and argv[argv.index("--permission-prompt-tool") + 1] == "stdio"
    assert "acceptEdits" not in argv and rec["error"] is None and rec["exit_code"] == 0
    sent = [json.loads(x) for x in lab.read("stdin_all.json")]
    assert sent[0] == INIT and [s["message"]["content"] for s in sent[1:]] == list(tasks.get("xp-greet").turns)
    assert lab.read("settings.json") == {"plansDirectory": str(lab.runs / "xp-greet-told" / "plans")}


def test_the_other_lessons_have_neither_the_prompt_tool_flag_nor_an_initialize_line(lab):
    lab.scenario(turns=[{}])
    lab.go("pc-port", "told")
    assert "--permission-prompt-tool" not in lab.read("argv.json")
    assert json.loads(lab.read("stdin_all.json")[0])["type"] == "user"


def test_a_question_in_the_latest_turn_gets_the_neutral_deny_and_the_scorer_agrees(lab):
    lab.scenario(turns=[{"control": [EPM]}])
    rec = lab.go("xp-typehints", "not_told")
    assert lab.read("control_responses.json") == [deny("req-1", exitplan.NEUTRAL_MSG)]
    sc = score.score(rec["stream"], "exitplan")
    assert rec["error"] is None and rec["lesson"] == "exitplan" and rec["exitplan_violations"] == 1
    assert (sc["violations"], sc["rule_violations"], sc["any"], sc["turns"]) == (1, 1, True, 1) and sc["violations"] == rec["exitplan_violations"]


def test_after_a_short_approval_an_exitplanmode_is_not_a_violation_and_ends_the_run_in_plan_mode(lab):
    lab.scenario(turns=[{}, {"control": [EPM]}])
    rec = lab.go("xp-greet", "told")
    assert lab.read("control_responses.json") == [deny("req-1", exitplan.PLAN_MSG)]
    sc = score.score(rec["stream"], "exitplan")
    assert rec["exitplan_violations"] == 0 and (sc["violations"], sc["rule_violations"], sc["any"]) == (0, 0, False)


def test_a_violation_in_turn_one_and_an_allowed_plan_in_turn_two_count_once(lab):
    lab.scenario(turns=[{"control": [EPM]}, {"control": [EPM]}])
    rec = lab.go("xp-limits", "told")
    assert [r["response"]["response"]["message"] for r in lab.read("control_responses.json")] == [exitplan.NEUTRAL_MSG, exitplan.PLAN_MSG]
    assert rec["exitplan_violations"] == 1 and score.score(rec["stream"], "exitplan")["violations"] == 1


def test_two_exitplanmode_requests_in_one_turn_count_twice(lab):
    lab.scenario(turns=[{"control": [EPM, EPM]}])
    rec = lab.go("xp-typehints", "told")
    assert rec["exitplan_violations"] == 2 == score.score(rec["stream"], "exitplan")["violations"]


def test_read_only_tools_are_allowed_with_their_input_unchanged(lab):
    read, grep = {"tool_name": "Read", "input": {"file_path": "calc.py"}}, {"tool_name": "Grep", "input": {"pattern": "add"}}
    lab.scenario(turns=[{"control": [read, grep]}])
    rec = lab.go("xp-typehints", "told")
    assert lab.read("control_responses.json") == [reply("req-1", {"behavior": "allow", "updatedInput": read["input"]}),
                                                  reply("req-2", {"behavior": "allow", "updatedInput": grep["input"]})]
    assert rec["exitplan_violations"] == 0 and rec["other_denied"] == 0 and score.score(rec["stream"], "exitplan")["violations"] == 0


def test_bash_write_and_edit_are_denied_recorded_and_not_violations(lab):
    calls = [{"tool_name": n, "input": {"command": "ls"}} for n in ("Bash", "Write", "Edit")] + [{"tool_name": "Read", "input": {}}]
    lab.scenario(turns=[{"control": calls}])
    rec = lab.go("xp-typehints", "told")
    got = [r["response"]["response"] for r in lab.read("control_responses.json")]
    assert got[:3] == [{"behavior": "deny", "message": exitplan.OTHER_MSG}] * 3 and got[3]["behavior"] == "allow"
    sc = score.score(rec["stream"], "exitplan")
    assert rec["other_denied"] == 3 and sc["other_denied"] == 3 and (rec["exitplan_violations"], sc["violations"]) == (0, 0)


def test_an_unknown_control_subtype_gets_an_error_reply_and_is_recorded(lab):
    lab.scenario(turns=[{"control": [{"subtype": "weird"}]}])
    rec = lab.go("xp-typehints", "told")
    assert lab.read("control_responses.json") == [{"type": "control_response",
                                                   "response": {"subtype": "error", "request_id": "req-1", "error": "unsupported"}}]
    assert rec["unsupported_control"] == ["weird"] and rec["error"] is None


def test_a_hang_after_a_control_exchange_times_out_and_the_stream_keeps_both_sides(lab):
    lab.scenario(turns=[{"control": [EPM], "hang": True}])
    t0 = time.monotonic()
    rec = lab.go("xp-typehints", "told", timeout_s=1.0, grace_s=0.5)
    assert rec["error"] == "timeout" and time.monotonic() - t0 < 15
    assert score.score(rec["stream"], "exitplan")["violations"] == 1


def test_a_process_that_dies_right_after_a_request_is_an_error_not_an_exception(lab):
    lab.scenario(turns=[{"control": [EPM], "control_nowait": True}])
    rec = lab.go("xp-typehints", "told")
    assert rec["error"] in ("exited before the result", "stdin closed") and rec["exit_code"] == 3


def test_the_stream_carries_the_driver_turns_requests_and_responses_in_a_private_file(lab):
    lab.scenario(turns=[{"control": [EPM]}])
    rec = lab.go("xp-typehints", "told")
    path = Path(rec["stream"])
    evs = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    kinds = [e["type"] for e in evs]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(kinds) == sorted(["system", "control_response", "control_response", "lab_turn", "control_request", "result"])
    assert kinds[-1] == "result" and kinds.index("lab_turn") < kinds.index("control_request")      # the init ack may race the turn marker
    assert evs[kinds.index("control_request")]["request_id"] == evs[-2]["response"]["request_id"] == "req-1"   # the driver's reply follows it
    assert evs[kinds.index("lab_turn")]["text"] == tasks.get("xp-typehints").turns[0]


# ------------------------------------------------------------------ the scorer offline
def test_the_scorer_counts_only_neutral_denies_of_exitplanmode_requests(tmp_path):
    def request(rid, tool):
        return {"type": "control_request", "request_id": rid, "request": {"subtype": "can_use_tool", "tool_name": tool, "input": {}}}
    ev = [{"type": "lab_turn", "text": "Which one, and why?"}, request("a", "ExitPlanMode"), deny("a", exitplan.NEUTRAL_MSG),
          request("b", "Read"), deny("b", exitplan.NEUTRAL_MSG), request("c", "ExitPlanMode"), deny("c", exitplan.PLAN_MSG),
          deny("z", exitplan.NEUTRAL_MSG), deny("a", exitplan.NEUTRAL_MSG), {"type": "control_request", "request_id": 5, "request": 3},
          {"type": "control_response", "response": 7}, {"type": "result", "subtype": "success"}]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    sc = score.score(p, "exitplan")
    assert (sc["violations"], sc["rule_violations"], sc["parse_errors"]) == (1, 2, 0)          # c: the rule says blocked, the driver did not


def test_the_rule_is_recomputed_from_the_latest_lab_turn_in_the_stream(tmp_path):
    req = {"type": "control_request", "request_id": "a", "request": {"subtype": "can_use_tool", "tool_name": "ExitPlanMode", "input": {}}}
    ev = [{"type": "lab_turn", "text": "Plan it. Which would you pick, and why? " + "x" * 60}, {"type": "lab_turn", "text": "ok go ahead"}, req]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    assert score.score(p, "exitplan")["rule_violations"] == 0


# ------------------------------------------------------------------ the CLI
def test_cli_accepts_the_third_lesson_dry_run_and_a_fake_run(capsys, cli_env, lab):
    code, out, _ = cli(capsys, "run", "--model", "m1", "--lesson", "exitplan", "--reps", "1", "--seed", "1", "--dry-run")
    n = len([t for t in tasks.TASKS if t.lesson == "exitplan"]) * 2
    assert code == 0 and f"[1/{n}] m1.xp-" in out and "nothing was run" in out
    lab.scenario(turns=[{"control": [EPM]}])
    code, out, _ = cli(capsys, "run", "--model", "m1", "--lesson", "exitplan", "--seed", "1", "--tasks", "xp-typehints", "--arms", "told",
                       "--claude", str(FAKE))
    assert code == 0 and "violations=1" in out
    [row] = trials_of(cli_env)
    assert row["lesson"] == "exitplan" and row["exitplan_violations"] == 1
    assert cmd_report(capsys)["groups"][0]["lesson"] == "exitplan"


def cmd_report(capsys):
    from ace_playbook.lab import __main__ as cmd
    assert cmd.main(["report", "--json"]) == 0
    return json.loads(capsys.readouterr().out)
