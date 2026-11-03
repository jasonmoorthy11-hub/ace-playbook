"""`lab/stats.py` and the `report` verb: Fisher exact, Wilson, Holm across lessons within a model, exclusions."""

import json
import math
import random
from pathlib import Path

import pytest

from ace_playbook.compile.metrics import wilson
from ace_playbook.lab import __main__ as cmd
from ace_playbook.lab import stats


def reference(k1, n1, k0, n0):
    """An independent float computation of the two-sided Fisher p (the mass of tables no more probable than the observed one)."""
    k = k1 + k0
    pmf = lambda x: math.comb(n1, x) * math.comb(n0, k - x) / math.comb(n1 + n0, k)           # noqa: E731
    return sum(pmf(x) for x in range(max(0, k - n0), min(n1, k) + 1) if pmf(x) <= pmf(k1) * (1 + 1e-9))


def test_fisher_matches_a_published_value_and_the_independent_reference():
    assert stats.fisher_two_sided(8, 10, 1, 6) == pytest.approx(0.0349650, abs=1e-6)      # scipy.stats.fisher_exact([[8, 2], [1, 5]])
    assert stats.fisher_two_sided(3, 3, 0, 3) == pytest.approx(0.1)
    rng = random.Random(7)
    for _ in range(200):
        n1, n0 = rng.randint(1, 25), rng.randint(1, 25)
        k1, k0 = rng.randint(0, n1), rng.randint(0, n0)
        assert stats.fisher_two_sided(k1, n1, k0, n0) == pytest.approx(reference(k1, n1, k0, n0), abs=1e-9)


def test_fisher_edges():
    assert stats.fisher_two_sided(0, 5, 0, 5) == 1.0 and stats.fisher_two_sided(5, 5, 5, 5) == 1.0
    assert stats.fisher_two_sided(2, 4, 2, 4) == 1.0
    assert stats.fisher_two_sided(0, 0, 1, 5) is None and stats.fisher_two_sided(1, 5, 0, 0) is None
    assert stats.fisher_two_sided(4, 4, 0, 4) == stats.fisher_two_sided(0, 4, 4, 4) == pytest.approx(2 / 70)


def row(model, lesson, arm, v):
    return {"model": model, "lesson": lesson, "arm": arm, "violations": v, "any": v > 0, "turns": 1, "parse_errors": 0}


def rows(model, lesson, told, not_told):
    """told / not_told: lists of violation counts."""
    return [row(model, lesson, "told", v) for v in told] + [row(model, lesson, "not_told", v) for v in not_told]


def test_summarize_counts_rates_wilson_diff_and_means():
    [g] = stats.summarize(rows("m", "preclaim", [0, 0, 1, 0], [1, 2, 0, 1, 1, 0]))
    assert (g["told"]["n"], g["told"]["k"], g["told"]["rate"], g["told"]["mean_violations"]) == (4, 1, 0.25, 0.25)
    assert (g["not_told"]["n"], g["not_told"]["k"], g["not_told"]["mean_violations"]) == (6, 4, 0.8333)
    assert g["told"]["wilson95"] == wilson(1, 4) and g["not_told"]["wilson95"] == wilson(4, 6)
    assert g["diff"] == round(0.25 - 4 / 6, 4) and g["fisher_p"] == pytest.approx(reference(1, 4, 4, 6), abs=1e-6)


def test_holm_runs_across_the_lessons_within_each_model_only():
    data = (rows("a", "preclaim", [1] * 9 + [0], [0] * 10) + rows("a", "edit_unread", [1] * 3 + [0] * 7, [1] * 2 + [0] * 8)
            + rows("b", "preclaim", [1] * 9 + [0], [0] * 10))
    g = {(x["model"], x["lesson"]): x for x in stats.summarize(data)}
    p_pre, p_eu = g["a", "preclaim"]["fisher_p"], g["a", "edit_unread"]["fisher_p"]
    assert g["a", "preclaim"]["p_holm"] == pytest.approx(min(1, 2 * p_pre), abs=1e-6) and g["a", "preclaim"]["reject"] is True
    assert g["a", "edit_unread"]["p_holm"] == pytest.approx(max(min(1, 2 * p_pre), p_eu), abs=1e-6) and g["a", "edit_unread"]["reject"] is False
    assert g["b", "preclaim"]["p_holm"] == pytest.approx(p_pre, abs=1e-6)                            # one lesson: unadjusted


def test_an_empty_arm_is_not_testable_and_never_rejects():
    [g] = stats.summarize(rows("m", "preclaim", [1, 1], []))
    assert g["not_told"]["n"] == 0 and g["not_told"]["rate"] is None and g["diff"] is None and g["fisher_p"] is None
    assert g["p_holm"] == 1.0 and g["reject"] is False and g["not_told"]["wilson95"] == "-"


# ------------------------------------------------------------------ the report verb over a data dir
def stream(path, lesson, n):
    hook = "Stop hook feedback:\n[/lab/hooks/stop-mid-task-audit.sh]: audit"
    ev = [{"type": "user", "message": {"content": [{"type": "text", "text": hook}]}} for _ in range(n if lesson == "preclaim" else 0)]
    path.write_text("\n".join(json.dumps(e) for e in ev + [{"type": "result", "subtype": "success"}]) + "\n")
    return str(path)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    recs = []

    def add(i, arm, n, **extra):
        rec = {"trial_id": f"t{i}", "task": "pc-port", "lesson": "preclaim", "arm": arm, "model": "m1", "pilot": False, "error": None,
               "stream": stream(tmp_path / f"s{i}.jsonl", "preclaim", n), **extra}
        recs.append(rec)
    for i, (arm, n) in enumerate([("told", 0), ("told", 0), ("told", 1), ("not_told", 1), ("not_told", 2), ("not_told", 0)]):
        add(i, arm, n)
    add(10, "told", 3, pilot=True)
    add(11, "told", 1, error="timeout")
    add(12, "not_told", 1, stream=str(tmp_path / "gone.jsonl"))
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
    return tmp_path


def test_report_json_scores_streams_and_excludes_errors_and_missing(capsys, data):
    assert cmd.main(["report", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    [g] = out["groups"]
    assert (g["told"]["n"], g["told"]["k"], g["not_told"]["n"], g["not_told"]["k"]) == (4, 2, 3, 2)           # the pilot trial is in
    assert out["excluded"] == {"m1/preclaim/not_told": 1, "m1/preclaim/told": 1}


def test_report_exclude_pilot_and_the_text_form(capsys, data):
    assert cmd.main(["report", "--exclude-pilot", "--json"]) == 0
    [g] = json.loads(capsys.readouterr().out)["groups"]
    assert (g["told"]["n"], g["told"]["k"]) == (3, 1)
    assert cmd.main(["report", "--exclude-pilot"]) == 0
    text = capsys.readouterr().out
    assert "model m1  lesson preclaim" in text and "told" in text and "fisher p=" in text and "excluded (error or missing stream)" in text


def test_report_with_no_data(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path / "none"))
    assert cmd.main(["report"]) == 0
    first, second = capsys.readouterr().out.strip().splitlines()                                        # m2: the checks line always prints
    assert first == "no scored trials" and second.startswith("checks: lesson_sha16={}")


def test_holm_takes_the_raw_fisher_p_and_only_the_display_is_rounded():
    data = rows("m", "preclaim", [1] * 14, [0] * 14) + rows("m", "edit_unread", [1, 0, 1, 0], [1, 0, 0, 1])
    g = {x["lesson"]: x for x in stats.summarize(data)}
    raw = 2 / math.comb(28, 14)                                              # about 5e-8: below the old 6 dp rounding
    assert raw < 5e-7 and g["preclaim"]["fisher_p"] == pytest.approx(raw, rel=1e-4) and g["preclaim"]["fisher_p"] > 0
    assert g["preclaim"]["p_holm"] == pytest.approx(2 * raw, rel=1e-4) and g["preclaim"]["p_holm"] > 0 and g["preclaim"]["reject"] is True
    assert "e-08" in stats.render(list(g.values()), {})


def test_the_primary_summary_is_uncued_only_and_the_cued_rows_are_reported_apart_without_holm():
    data = rows("m", "edit_unread", [1, 1, 0, 1], [0, 0, 0, 0]) + [dict(r, cue=True) for r in rows("m", "edit_unread", [1, 1], [1, 1, 1])]
    [g] = stats.summarize(data)
    assert (g["told"]["n"], g["told"]["k"], g["not_told"]["n"], g["not_told"]["k"]) == (4, 3, 4, 0)
    [c] = stats.summarize(data, cued=True)
    assert (c["told"]["n"], c["not_told"]["n"], c["p_holm"], c["reject"]) == (2, 3, None, False)
    assert stats.summarize(rows("m", "preclaim", [1], [0]), cued=True) == []
    text = stats.render([g], {}, [c])
    assert "cued tasks (secondary, descriptive)" in text and text.index("model m  lesson edit_unread") < text.index("cued tasks")


def test_holm_runs_across_all_three_lessons_of_a_model():
    data = (rows("a", "preclaim", [1] * 9 + [0], [0] * 10) + rows("a", "edit_unread", [1] * 5 + [0] * 5, [1] * 2 + [0] * 8)
            + rows("a", "exitplan", [1] * 8 + [0] * 2, [0] * 8 + [1] * 2) + rows("b", "exitplan", [1] * 8 + [0] * 2, [0] * 10))
    g = {(x["model"], x["lesson"]): x for x in stats.summarize(data)}
    ps = sorted((g["a", lesson]["fisher_p"], lesson) for lesson in ("preclaim", "edit_unread", "exitplan"))
    adj, prev = {}, 0.0
    for i, (p, lesson) in enumerate(ps):
        prev = adj[lesson] = max(prev, min(1.0, (3 - i) * p))
    for lesson in adj:
        assert g["a", lesson]["p_holm"] == pytest.approx(adj[lesson], rel=1e-4)
    assert g["b", "exitplan"]["p_holm"] == g["b", "exitplan"]["fisher_p"]                                  # one lesson: unadjusted


def test_report_splits_cued_from_uncued_by_the_record_or_the_task_spec(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    recs = []
    for i, (task, arm, cue) in enumerate([("eu-parser", "told", None), ("eu-parser", "not_told", None), ("eu-hand-notes", "told", None),
                                          ("eu-hand-notes", "not_told", None), ("eu-readme", "told", True), ("eu-readme", "not_told", False)]):
        path = tmp_path / f"s{i}.jsonl"
        path.write_text(json.dumps({"type": "result", "subtype": "success"}) + "\n")
        rec = {"trial_id": f"t{i}", "task": task, "lesson": "edit_unread", "arm": arm, "model": "m", "error": None, "stream": str(path)}
        recs.append(rec if cue is None else {**rec, "cue": cue})
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert cmd.main(["report", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    [g], [c] = out["groups"], out["cued"]
    assert (g["told"]["n"], g["not_told"]["n"]) == (1, 2) and (c["told"]["n"], c["not_told"]["n"]) == (2, 1)      # hand-notes: cued by spec


def test_the_primary_preclaim_row_is_multi_claim_only_and_single_claim_tasks_are_a_secondary_row():
    base = rows("m", "preclaim", [1, 1, 0], [0, 0, 0]) + rows("m", "edit_unread", [1, 0], [0, 0])
    data = base + [dict(r, single=True) for r in rows("m", "preclaim", [1, 0], [1, 0, 0])]
    groups = {g["lesson"]: g for g in stats.summarize(data)}
    assert (groups["preclaim"]["told"]["n"], groups["preclaim"]["not_told"]["n"]) == (3, 3) and "edit_unread" in groups
    [s1] = stats.summarize(data, single=True)
    assert (s1["lesson"], s1["told"]["n"], s1["not_told"]["n"], s1["p_holm"], s1["reject"]) == ("preclaim", 2, 3, None, False)
    assert stats.summarize(data, cued=True) == []
    text = stats.render(list(groups.values()), {}, [], [s1])
    assert "single-claim preclaim tasks (secondary, descriptive)" in text and text.index("model m  lesson preclaim") < text.index("single-claim")


def test_report_splits_single_claim_preclaim_by_the_record_or_the_task_spec(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    recs = []
    for i, (task, arm, multi) in enumerate([("pc-rename", "told", None), ("pc-rename", "not_told", None), ("pc-summarise", "told", None),
                                            ("pc-summarise", "not_told", None), ("pc-ini", "told", True), ("pc-ini", "not_told", False)]):
        path = tmp_path / f"s{i}.jsonl"
        path.write_text(json.dumps({"type": "result", "subtype": "success"}) + "\n")
        rec = {"trial_id": f"t{i}", "task": task, "lesson": "preclaim", "arm": arm, "model": "m", "error": None, "stream": str(path)}
        recs.append(rec if multi is None else {**rec, "multi_claim": multi})
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert cmd.main(["report", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    [g], [s1] = out["groups"], out["single"]
    assert (g["told"]["n"], g["not_told"]["n"]) == (2, 1) and (s1["told"]["n"], s1["not_told"]["n"]) == (1, 2)      # pc-rename: single by spec


def exitplan_stream(path, rule, driver):
    """A stream whose rule recomputation says `rule` and whose driver neutral denies say `driver` (0 or 1 ExitPlanMode request)."""
    ev = [{"type": "lab_turn", "text": "Which one?" if rule else "ok go ahead"},
          {"type": "control_request", "request_id": "r1", "request": {"subtype": "can_use_tool", "tool_name": "ExitPlanMode", "input": {}}}]
    if driver:
        from ace_playbook.lab import exitplan
        ev.append({"type": "control_response", "response": {"subtype": "success", "request_id": "r1",
                                                            "response": {"behavior": "deny", "message": exitplan.NEUTRAL_MSG}}})
    path.write_text("\n".join(json.dumps(e) for e in ev + [{"type": "result", "subtype": "success"}]) + "\n")
    return str(path)


def test_the_report_counts_exitplan_trials_where_the_rule_and_the_driver_differ(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    spec = [("a", "told", 1, 1), ("b", "told", 1, 0), ("c", "not_told", 0, 0), ("d", "not_told", 0, 1)]         # (id, arm, rule, driver)
    recs = [{"trial_id": i, "task": "xp-typehints", "lesson": "exitplan", "arm": arm, "model": "m", "error": None,
             "stream": exitplan_stream(tmp_path / f"{i}.jsonl", rule, drv)} for i, arm, rule, drv in spec]
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["exitplan_mismatches"] == 2
    assert cmd.main(["report"]) == 0
    assert "exitplan rule/driver mismatches: 2" in capsys.readouterr().out


def test_the_mismatch_line_is_absent_without_exitplan_trials_and_the_json_carries_null(capsys, data):
    assert cmd.main(["report"]) == 0
    assert "mismatches" not in capsys.readouterr().out
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["exitplan_mismatches"] is None


def test_the_json_carries_zero_when_exitplan_trials_were_scored_and_none_differ(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    recs = [{"trial_id": "a", "task": "xp-typehints", "lesson": "exitplan", "arm": "told", "model": "m", "error": None,
             "stream": exitplan_stream(tmp_path / "a.jsonl", 1, 1)}]
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["exitplan_mismatches"] == 0


def outside_stream(path, *paths):
    ev = [{"type": "system", "subtype": "init", "cwd": "/r/work"}]
    ev += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": "Write", "input": {"file_path": p}}]}}
           for i, p in enumerate(paths)]
    path.write_text("\n".join(json.dumps(e) for e in ev + [{"type": "result", "subtype": "success"}]) + "\n")
    return str(path)


def test_the_report_prints_and_the_json_carries_the_outside_run_dir_count_of_every_stream_whatever_the_flag(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    spec = [("a", False, ["/r/work/a.py", "/h/.claude/plans/p.md"]), ("b", False, ["/r/work/b.py"]), ("c", True, ["/h/x", "/r/x"])]
    recs = [{"trial_id": i, "task": "pc-port", "lesson": "preclaim", "arm": "told", "model": "m", "error": None, "pilot": pilot,
             "stream": outside_stream(tmp_path / f"{i}.jsonl", *paths)} for i, pilot, paths in spec]
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert cmd.main(["report"]) == 0
    assert "outside-run-dir tool inputs: 3" in capsys.readouterr().out
    assert cmd.main(["report", "--exclude-pilot"]) == 0
    assert "outside-run-dir tool inputs: 3" in capsys.readouterr().out                                  # m2: the safety count ignores the flag
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_run_dir_tool_inputs"] == 3


def test_the_outside_count_is_absent_without_scored_trials_and_the_json_carries_null(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    assert cmd.main(["report"]) == 0
    assert "outside-run-dir" not in capsys.readouterr().out
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_run_dir_tool_inputs"] is None
    assert stats.outside(tmp_path / "none.jsonl") is None


def test_the_outside_count_covers_errored_trials_while_every_other_statistic_keeps_its_rows(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    spec = [("a", None, ["/h/.claude/plans/p.md"]), ("b", "timeout", ["/h/x", "/r/work/b.py"]), ("c", "timeout", [])]
    recs = [{"trial_id": i, "task": "pc-port", "lesson": "preclaim", "arm": "told", "model": "m", "error": err, "pilot": False,
             "stream": outside_stream(tmp_path / f"{i}.jsonl", *paths)} for i, err, paths in spec]
    recs.append({"trial_id": "d", "task": "pc-port", "lesson": "preclaim", "arm": "told", "model": "m", "error": "x", "stream": str(tmp_path / "gone")})
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert cmd.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "outside-run-dir tool inputs: 2" in out and "excluded (error or missing stream) m/preclaim/told: 3" in out
    assert cmd.main(["report", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outside_run_dir_tool_inputs"] == 2 and data["groups"][0]["told"]["n"] == 1
    assert data["excluded"] == {"m/preclaim/told": 3}


def test_the_outside_count_ignores_exclude_pilot_and_counts_the_stream_of_every_attempt_not_only_the_last(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    base = {"task": "pc-port", "lesson": "preclaim", "arm": "told", "model": "m"}
    recs = [{**base, "trial_id": "a", "error": "boom", "pilot": True, "stream": outside_stream(tmp_path / "a.jsonl", "/h/1", "/h/2")},
            {**base, "trial_id": "b", "error": "boom", "stream": outside_stream(tmp_path / "b1.jsonl", "/h/1")},
            {**base, "trial_id": "b", "error": None, "stream": outside_stream(tmp_path / "b2.jsonl", "/r/work/ok")}]
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_run_dir_tool_inputs"] == 3                      # a: 2, b's errored attempt: 1, b's retry: 0
    assert cmd.main(["report", "--json", "--exclude-pilot"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_run_dir_tool_inputs"] == 3                      # m2: pilot streams stay in the safety count


def rec_of(tid, stream, error=None):
    return {"trial_id": tid, "task": "pc-port", "lesson": "preclaim", "arm": "told", "model": "m", "error": error, "pilot": False,
            "stream": stream}


def put(tmp_path, recs, extra_lines=()):
    lines = [json.dumps(r) for r in recs] + list(extra_lines)
    (tmp_path / "trials.jsonl").write_text("\n".join(lines) + "\n")


def test_a_missing_stream_of_an_errored_non_build_trial_counts_as_unreadable(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    put(tmp_path, [rec_of("a", outside_stream(tmp_path / "a.jsonl", "/h/x")), rec_of("b", str(tmp_path / "gone"), "timeout")])
    assert cmd.main(["report"]) == 0
    assert "outside-run-dir tool inputs: 1 (unreadable streams: 1)" in capsys.readouterr().out
    assert cmd.main(["report", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outside_run_dir_tool_inputs"] == 1 and data["outside_unreadable"] == 1


def test_a_build_failed_record_without_a_stream_is_not_unreadable(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    put(tmp_path, [rec_of("a", str(tmp_path / "never"), "build failed: OSError"), rec_of("b", None, "build failed: ValueError")])
    assert cmd.main(["report"]) == 0
    assert "outside-run-dir tool inputs: 0 (unreadable streams: 0)" in capsys.readouterr().out
    put(tmp_path, [rec_of("a", str(tmp_path / "never"), "spawn failed: OSError")])
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_unreadable"] == 1


def test_the_line_still_prints_when_every_stream_is_unreadable(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    (tmp_path / "bad.jsonl").write_text("{nope\nalso nope\n")
    put(tmp_path, [rec_of("a", str(tmp_path / "gone")), rec_of("b", str(tmp_path / "bad.jsonl")), rec_of("c", None), rec_of("d", 7), rec_of("e", "")])
    assert cmd.main(["report"]) == 0
    assert "outside-run-dir tool inputs: 0 (unreadable streams: 5)" in capsys.readouterr().out


def test_an_unparseable_trials_line_is_counted_as_unreadable(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    put(tmp_path, [], ["{not json", "[1, 2]", '{"no_trial_id": 1}', ""])
    assert cmd.main(["report"]) == 0
    assert "outside-run-dir tool inputs: 0 (unreadable streams: 3)" in capsys.readouterr().out


def test_the_json_lists_the_trials_with_outside_paths_sorted(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    put(tmp_path, [rec_of("z", outside_stream(tmp_path / "z.jsonl", "/h/x")), rec_of("b", outside_stream(tmp_path / "b.jsonl", "/r/work/a")),
                   rec_of("a", outside_stream(tmp_path / "a.jsonl", "/h/1", "/h/2"))])
    assert cmd.main(["report", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outside_trials"] == ["a", "z"] and data["outside_run_dir_tool_inputs"] == 3 and data["outside_unreadable"] == 0
    (tmp_path / "trials.jsonl").unlink()
    assert cmd.main(["report", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outside_trials"] == [] and data["outside_unreadable"] is None


def test_exclude_pilot_never_hides_a_pilot_stream_from_the_safety_count(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    put(tmp_path, [{**rec_of("a", outside_stream(tmp_path / "a.jsonl", "/h/x")), "pilot": True},
                   {**rec_of("b", str(tmp_path / "gone"), "timeout"), "pilot": True}])
    for flag in ([], ["--exclude-pilot"]):
        assert cmd.main(["report", *flag]) == 0
        assert "outside-run-dir tool inputs: 1 (unreadable streams: 1)" in capsys.readouterr().out
        assert cmd.main(["report", "--json", *flag]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["outside_run_dir_tool_inputs"] == 1 and data["outside_unreadable"] == 1 and data["outside_trials"] == ["a"]


def text_stream(path, text=""):
    path.write_text(text)
    return str(path)


def test_an_empty_blank_or_init_less_stream_is_unreadable_unless_the_spawn_failed(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    noinit = json.dumps({"type": "result", "subtype": "success"}) + "\n"
    for err in (None, "timeout", "exit 1"):
        put(tmp_path, [rec_of("a", text_stream(tmp_path / "e"), err), rec_of("b", text_stream(tmp_path / "b", "\n  \n"), err),
                       rec_of("c", text_stream(tmp_path / "c", noinit), err)])
        assert cmd.main(["report"]) == 0
        assert "outside-run-dir tool inputs: 0 (unreadable streams: 3)" in capsys.readouterr().out
    put(tmp_path, [rec_of("a", text_stream(tmp_path / "e"), "spawn failed: FileNotFoundError"),
                   rec_of("b", text_stream(tmp_path / "b", "\n \n"), "spawn failed: PermissionError")])
    assert cmd.main(["report", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outside_unreadable"] == 0 and data["outside_bad_lines"] == 0


def test_m3_branches_a_timeout_with_init_is_readable_and_a_build_failed_record_with_an_empty_stream_is_not(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    put(tmp_path, [rec_of("a", outside_stream(tmp_path / "a.jsonl"), "timeout")])                  # init, no tool use: readable
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_unreadable"] == 0
    put(tmp_path, [rec_of("b", text_stream(tmp_path / "b"), "build failed: OSError")])             # exempt only without a stream: this one exists
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_unreadable"] == 1


def test_the_spawn_failed_exemption_is_only_for_a_stream_with_no_events_at_all(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    noinit = json.dumps({"type": "result", "subtype": "success"}) + "\n"
    put(tmp_path, [rec_of("a", text_stream(tmp_path / "a", noinit), "spawn failed: OSError")])
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_unreadable"] == 1
    put(tmp_path, [rec_of("a", text_stream(tmp_path / "a"), "spawn failed: OSError"), rec_of("b", text_stream(tmp_path / "b", " \n\n"), "spawn failed: X"),
                   rec_of("c", outside_stream(tmp_path / "c.jsonl"), "spawn failed: X")])
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_unreadable"] == 0


def test_a_stream_with_some_unparseable_lines_is_counted_separately_in_the_same_line_and_the_json(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    good = outside_stream(tmp_path / "g.jsonl", "/h/x")
    torn = text_stream(tmp_path / "t.jsonl", (tmp_path / "g.jsonl").read_text() + '{"type": "assistant", "message": {"content": [{"type": "tool_u')
    put(tmp_path, [rec_of("g", good), rec_of("t", torn), rec_of("u", str(tmp_path / "gone"), "timeout")])
    assert cmd.main(["report"]) == 0
    assert "outside-run-dir tool inputs: 2 (unreadable streams: 1, streams with bad lines: 1)" in capsys.readouterr().out
    assert cmd.main(["report", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outside_bad_lines"] == 1 and data["outside_unreadable"] == 1 and data["outside_run_dir_tool_inputs"] == 2


def test_a_non_str_stream_is_excluded_and_a_type_error_inside_score_propagates(tmp_path, monkeypatch):
    put(tmp_path, [rec_of("a", 7), rec_of("b", None), rec_of("c", str(tmp_path / "gone")), rec_of("d", outside_stream(tmp_path / "d.jsonl"))])
    rows, excluded = stats.load_rows(tmp_path / "trials.jsonl")
    assert excluded == {"m/preclaim/told": 3} and len(rows) == 1

    def boom(*a, **k):
        raise TypeError("a real bug")
    monkeypatch.setattr(stats.score, "score", boom)
    with pytest.raises(TypeError, match="a real bug"):
        stats.load_rows(tmp_path / "trials.jsonl")


def test_a_stream_that_vanishes_after_the_one_read_cannot_crash_the_report(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_LAB_DATA", str(tmp_path))
    orig = stats.score._events

    def read_then_remove(path):
        out = orig(path)
        Path(path).unlink()
        return out
    monkeypatch.setattr(stats.score, "_events", read_then_remove)
    put(tmp_path, [rec_of("a", text_stream(tmp_path / "a", "\n  \n"), "spawn failed: OSError")])
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_unreadable"] == 0
    put(tmp_path, [rec_of("b", text_stream(tmp_path / "b", json.dumps({"type": "result", "subtype": "success"}) + "\n"), "spawn failed: OSError")])
    assert cmd.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outside_unreadable"] == 1
