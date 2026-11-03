"""`lab/exitplan.py` and the third lesson: the stdio permission protocol against the FAKE claude (the real binary is never run)."""

import json
import stat
import time
from pathlib import Path

import pytest

from ace_playbook.lab import exitplan, score, tasks

from .test_lab_run import FAKE, cli, cli_env, lab, trials_of  # noqa: F401  (fixtures and helpers)

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
    assert exitplan.respond(req, "Which one, and why?") == (deny("r1", exitplan.NEUTRAL_MSG), "neutral")
    assert exitplan.respond(req, "ok go ahead") == (deny("r1", exitplan.PLAN_MSG), "plan")
    read = {"type": "control_request", "request_id": "r2", "request": {"subtype": "can_use_tool", "tool_name": "Read", "input": {"a": 1}}}
    assert exitplan.respond(read, "Which one, and why?") == (reply("r2", {"behavior": "allow", "updatedInput": {"a": 1}}), "allow")
    odd = {"type": "control_request", "request_id": "r3", "request": {"subtype": "weird"}}
    err = {"type": "control_response", "response": {"subtype": "error", "request_id": "r3", "error": "unsupported"}}
    assert exitplan.respond(odd, "") == (err, "unsupported")
    assert exitplan.respond({"type": "control_request", "request_id": "r4", "request": 5}, "")[1] == "unsupported"
    assert exitplan.respond({"type": "control_request", "request_id": "r5", "request": {"subtype": "can_use_tool", "tool_name": "Read"}},
                            "")[0]["response"]["response"]["updatedInput"] == {}


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


def test_every_other_tool_is_allowed_with_its_input_unchanged(lab):
    read, grep = {"tool_name": "Read", "input": {"file_path": "calc.py"}}, {"tool_name": "Grep", "input": {"pattern": "add"}}
    lab.scenario(turns=[{"control": [read, grep]}])
    rec = lab.go("xp-typehints", "told")
    assert lab.read("control_responses.json") == [reply("req-1", {"behavior": "allow", "updatedInput": read["input"]}),
                                                  reply("req-2", {"behavior": "allow", "updatedInput": grep["input"]})]
    assert rec["exitplan_violations"] == 0 and score.score(rec["stream"], "exitplan")["violations"] == 0


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
