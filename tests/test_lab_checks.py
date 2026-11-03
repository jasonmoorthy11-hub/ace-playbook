"""`lab/checks.py` (the report's `checks`: lesson hash, binary/model, modelUsage counters) and `lab/describe.py` (Newcombe interval, per-task table)."""

import json

import pytest

from ace_playbook.lab import __main__ as cmd
from ace_playbook.lab import checks, describe, stats

from .test_lab_stats import row


def stream(path, version="2.1.309", model="m", usage=(("m", 1),), turns=1, init=True):
    ev = [{"type": "system", "subtype": "init", "cwd": "/r/work", "model": model, **({"claude_code_version": version} if version else {})}] if init else []
    for _ in range(turns):
        ev.append({"type": "result", "subtype": "success", "is_error": False, "modelUsage": {k: {"costUSD": v} for k, v in usage}})
    path.write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    return str(path)


def write(tmp_path, recs):
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return tmp_path / "trials.jsonl"


def rec(tmp_path, i, sha="aaaa", lesson="preclaim", arm="told", pilot=False, **kw):
    return {"trial_id": f"t{i}", "task": "pc-port", "lesson": lesson, "arm": arm, "model": "m", "pilot": pilot, "error": None, "lesson_sha16": sha,
            "stream": stream(tmp_path / f"s{i}.jsonl", **kw)}


def test_the_three_counters_over_every_trial(tmp_path):
    recs = [rec(tmp_path, 0), rec(tmp_path, 1), rec(tmp_path, 2, sha="bbbb", lesson="edit_unread", version="2.1.317", model="m2",
                                                       usage=(("m2", 1), ("haiku", 1)), turns=2)]
    recs[1]["error"] = "timeout"                                              # an errored trial still counts
    out = checks.checks(write(tmp_path, recs))
    assert out == {"lesson_sha16": {"edit_unread/bbbb": 1, "preclaim/aaaa": 2},
                   "binary": {"2.1.309/m": 2, "2.1.317/m2": 1},
                   "model_usage": {"haiku": 2, "m": 2, "m2": 2},                 # counted per result event
                   "attempt_errors": {"told/timeout": 1}, "first_attempt_errors": {"told/timeout": 1}}


def test_the_lesson_counter_takes_the_last_record_of_a_trial_id_and_the_binary_every_stream_of_a_last_record(tmp_path):
    first, last = rec(tmp_path, 0, sha="old"), rec(tmp_path, 0, sha="new", version="9.9.9")
    out = checks.checks(write(tmp_path, [first, last, rec(tmp_path, 1)]))
    assert out["lesson_sha16"] == {"preclaim/aaaa": 1, "preclaim/new": 1} and out["binary"] == {"2.1.309/m": 1, "9.9.9/m": 1}


def test_a_stream_without_init_or_version_or_usage_is_none_and_an_unreadable_one_too(tmp_path):
    recs = [rec(tmp_path, 0, init=False, usage=()), rec(tmp_path, 1, version=None), {**rec(tmp_path, 2), "stream": str(tmp_path / "gone.jsonl")}]
    out = checks.checks(write(tmp_path, recs))
    assert out["binary"] == {"None/None": 2, "None/m": 1} and out["model_usage"] == {"m": 1}


def test_exclude_pilot_drops_the_pilot_records_from_every_counter(tmp_path):
    path = write(tmp_path, [rec(tmp_path, 0), rec(tmp_path, 1, pilot=True, sha="pil", version="1.0.0")])
    assert checks.checks(path)["binary"] == {"1.0.0/m": 1, "2.1.309/m": 1}
    out = checks.checks(path, exclude_pilot=True)
    assert out == {"lesson_sha16": {"preclaim/aaaa": 1}, "binary": {"2.1.309/m": 1}, "model_usage": {"m": 1}, "attempt_errors": {},
                   "first_attempt_errors": {}}


def test_a_missing_or_blank_trials_file_gives_empty_counters(tmp_path):
    empty = {"lesson_sha16": {}, "binary": {}, "model_usage": {}, "attempt_errors": {}, "first_attempt_errors": {}}
    assert checks.checks(tmp_path / "none.jsonl") == empty
    assert checks.checks(write(tmp_path, [])) == empty


def test_a_record_without_a_lesson_hash_or_with_a_bad_value_cannot_crash(tmp_path):
    bad = {"trial_id": "x", "lesson": "preclaim", "stream": 5, "model_usage": 1}
    out = checks.checks(write(tmp_path, [bad, {"trial_id": "y", "stream": "/nonexistent"}]))
    assert out["lesson_sha16"] == {"preclaim/None": 1, "None/None": 1} and out["binary"] == {"None/None": 2}


def test_report_json_carries_checks_and_the_text_form_prints_one_checks_line(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    write(tmp_path, [rec(tmp_path, 0), rec(tmp_path, 1, arm="not_told")])
    assert cmd.main(["report", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)["checks"]
    assert got == {"lesson_sha16": {"preclaim/aaaa": 2}, "binary": {"2.1.309/m": 2}, "model_usage": {"m": 2}, "attempt_errors": {},
                   "first_attempt_errors": {}}
    assert cmd.main(["report"]) == 0
    lines = [x for x in capsys.readouterr().out.splitlines() if x.startswith("checks:")]
    assert len(lines) == 1 and all(s in lines[0] for s in ("preclaim/aaaa", "2.1.309/m", "model_usage"))
    assert "outcome" not in lines[0] and "violations" not in lines[0]


def test_report_json_checks_is_empty_without_data_and_the_text_says_no_scored_trials_then_prints_the_empty_checks_line(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path / "none"))
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["checks"] == {k: {} for k in ("lesson_sha16", "binary", "model_usage", "attempt_errors",
                                                                             "first_attempt_errors")}
    assert cmd.main(["report"]) == 0
    first, second = capsys.readouterr().out.strip().splitlines()
    assert first == "no scored trials" and second.startswith("checks: lesson_sha16={}")


# ------------------------------------------------------------------ Newcombe hybrid score interval (method 10, Newcombe 1998)
@pytest.mark.parametrize("args,want", [((56, 70, 48, 80), [0.0524, 0.3339]),                  # Newcombe's own example
                                       ((0, 10, 0, 10), [-0.2775, 0.2775]), ((10, 10, 10, 10), [-0.2775, 0.2775]),
                                       ((10, 10, 0, 10), [0.6075, 1.0]), ((0, 10, 10, 10), [-1.0, -0.6075]), ((3, 5, 1, 5), [-0.1626, 0.7264])])
def test_newcombe_matches_the_hand_computed_cases(args, want):
    assert describe.newcombe(*args) == want


def test_newcombe_is_none_when_an_arm_is_empty_and_stays_inside_minus_one_to_one():
    assert describe.newcombe(0, 0, 1, 5) is None and describe.newcombe(1, 5, 0, 0) is None
    for k1 in range(0, 6):
        for k0 in range(0, 6):
            lo, hi = describe.newcombe(k1, 5, k0, 5)
            assert -1 <= lo <= k1 / 5 - k0 / 5 <= hi <= 1


def rows_for(tasks_arms):
    """tasks_arms: [(task, arm, violations)]"""
    return [{**row("m", "preclaim", arm, v), "task": task} for task, arm, v in tasks_arms]


def test_summarize_adds_the_newcombe_interval_and_the_per_task_table_to_the_primary_groups():
    data = rows_for([("a", "told", 1)] * 3 + [("a", "told", 0)] * 2 + [("a", "not_told", 1)] + [("a", "not_told", 0)] * 4
                    + [("b", "told", 0)] * 2 + [("b", "not_told", 1)] * 2)
    [g] = stats.summarize(data)
    assert g["diff_newcombe95"] == describe.newcombe(3, 7, 3, 7) and g["diff_newcombe95"][0] < g["diff"] < g["diff_newcombe95"][1]
    assert g["per_task"] == {"a": {"told": {"any": 3, "n": 5}, "not_told": {"any": 1, "n": 5}},
                             "b": {"told": {"any": 0, "n": 2}, "not_told": {"any": 2, "n": 2}}}


def test_an_empty_arm_has_a_null_interval_and_the_secondary_rows_get_the_interval_but_no_per_task_table():
    [g] = stats.summarize(rows_for([("a", "told", 1)]))
    assert g["diff_newcombe95"] is None and g["per_task"] == {"a": {"told": {"any": 1, "n": 1}, "not_told": {"any": 0, "n": 0}}}
    data = [{**r, "cue": True} for r in rows_for([("a", "told", 1), ("a", "not_told", 0)])]
    [c] = stats.summarize(data, cued=True)
    assert c["diff_newcombe95"] == describe.newcombe(1, 1, 0, 1) and "per_task" not in c


def test_the_text_appends_the_interval_to_the_diff_line_and_prints_the_table_after_each_group():
    data = rows_for([("a", "told", 1)] * 3 + [("a", "told", 0)] * 2 + [("a", "not_told", 1)] + [("a", "not_told", 0)] * 4)
    text = stats.render(stats.summarize(data), {})
    lines = text.splitlines()
    i = next(n for n, x in enumerate(lines) if x.startswith("  diff(told - not_told)"))
    assert lines[i].endswith(f"newcombe95={describe.newcombe(3, 5, 1, 5)}") and "no rejection at" in lines[i]
    assert lines[i + 1].split() == ["task", "a", "told", "3/5", "not_told", "1/5"]


def test_the_report_json_carries_the_interval_and_the_table_per_group(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    hook = "Stop hook feedback:\n[/lab/hooks/stop-mid-task-audit.sh]: audit"
    recs = []
    for i, (task, arm, n) in enumerate([("pc-port", "told", 1), ("pc-port", "not_told", 0), ("pc-ini", "told", 0), ("pc-ini", "not_told", 0)]):
        ev = [{"type": "user", "message": {"content": [{"type": "text", "text": hook}]}}] * n + [{"type": "result", "subtype": "success"}]
        (tmp_path / f"p{i}.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
        recs.append({"trial_id": f"t{i}", "task": task, "lesson": "preclaim", "arm": arm, "model": "m", "pilot": False, "error": None,
                     "multi_claim": True, "stream": str(tmp_path / f"p{i}.jsonl")})
    write(tmp_path, recs)
    assert cmd.main(["report", "--json"]) == 0
    [g] = json.loads(capsys.readouterr().out)["groups"]
    assert g["diff_newcombe95"] == describe.newcombe(1, 2, 0, 2)
    assert g["per_task"]["pc-port"] == {"told": {"any": 1, "n": 1}, "not_told": {"any": 0, "n": 1}}
    assert g["per_task"]["pc-ini"] == {"told": {"any": 0, "n": 1}, "not_told": {"any": 0, "n": 1}}


# ------------------------------------------------------------------ wave 9: every attempt counts (C-B), attempt errors (m1), the line always prints (m2)
def attempts(tmp_path):
    """t0: three attempts (two errored, then clean); t1: an errored first attempt (not_told), then clean; every attempt has its own stream."""
    def one(tid, n, arm, err, **kw):
        return {**rec(tmp_path, f"{tid}a{n}", **kw), "trial_id": tid, "arm": arm, "error": err}
    return [one("t0", 1, "told", "result error: error_max_turns", version="1.0.0"), one("t0", 2, "told", "exit 2", version="1.0.1", usage=(("h", 1),)),
            one("t0", 3, "told", None), one("t1", 1, "not_told", "timeout"), one("t1", 2, "not_told", None)]


def test_binary_and_model_usage_read_the_stream_of_every_attempt_and_the_lesson_counter_the_last_record(tmp_path):
    out = checks.checks(write(tmp_path, attempts(tmp_path)))
    assert out["binary"] == {"1.0.0/m": 1, "1.0.1/m": 1, "2.1.309/m": 3} and out["model_usage"] == {"h": 1, "m": 4}
    assert out["lesson_sha16"] == {"preclaim/aaaa": 2}


def test_attempt_errors_count_every_errored_record_by_arm_and_first_attempt_errors_only_attempt_one(tmp_path):
    out = checks.checks(write(tmp_path, attempts(tmp_path)))
    assert out["attempt_errors"] == {"not_told/timeout": 1, "told/exit 2": 1, "told/result error: error_max_turns": 1}
    assert out["first_attempt_errors"] == {"not_told/timeout": 1, "told/result error: error_max_turns": 1}


def test_an_error_is_kept_whole_and_a_clean_trial_adds_nothing(tmp_path):
    recs = [{**rec(tmp_path, 0), "error": "spawn failed: FileNotFoundError"}, {**rec(tmp_path, 1), "error": "build failed: OSError", "arm": "not_told"},
            rec(tmp_path, 2)]
    out = checks.checks(write(tmp_path, recs))
    assert out["attempt_errors"] == (
        {"not_told/build failed: OSError": 1, "told/spawn failed: FileNotFoundError": 1})
    assert out["first_attempt_errors"] == out["attempt_errors"]


def test_exclude_pilot_keeps_the_attempt_counters_main_only(tmp_path):
    path = write(tmp_path, attempts(tmp_path))
    out = checks.checks(path, exclude_pilot=True)
    assert out["attempt_errors"] == {"not_told/timeout": 1, "told/exit 2": 1, "told/result error: error_max_turns": 1}
    pilot = [{**r, "pilot": True, "trial_id": "p" + r["trial_id"]} for r in attempts(tmp_path)]
    out = checks.checks(write(tmp_path, attempts(tmp_path) + pilot), exclude_pilot=True)
    assert out["attempt_errors"] == {"not_told/timeout": 1, "told/exit 2": 1, "told/result error: error_max_turns": 1}
    assert checks.checks(write(tmp_path, pilot), exclude_pilot=True) == {k: {} for k in out}


def test_the_text_checks_line_always_prints_with_empty_counters_and_exclude_pilot_keeps_it_main_only(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path / "none"))
    assert cmd.main(["report"]) == 0
    text = capsys.readouterr().out
    assert [x for x in text.splitlines() if x.startswith("checks:")] == [
        "checks: lesson_sha16={}  binary={}  model_usage={}  attempt_errors={}  first_attempt_errors={}"]
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    write(tmp_path, [rec(tmp_path, 0, pilot=True, sha="pil"), rec(tmp_path, 1, sha="main")])
    assert cmd.main(["report", "--exclude-pilot"]) == 0
    [line] = [x for x in capsys.readouterr().out.splitlines() if x.startswith("checks:")]
    assert "preclaim/main" in line and "pil" not in line
    write(tmp_path, [rec(tmp_path, 0, pilot=True, sha="pil")])
    assert cmd.main(["report", "--exclude-pilot"]) == 0
    assert "checks: lesson_sha16={}  binary={}" in capsys.readouterr().out
