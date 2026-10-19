"""The label pre-fill and evasion rules of the offline pass (docs/design/compiler.md 7.3): pure rules, then through `run`."""

import json

import pytest

from ace_playbook.compile import labels, offline, report
from ace_playbook.compile import state as st
from ace_playbook.compile.events import events_from_records, hash16

from . import compile_support as cs

CMD = cs.RM_PROMPT_CMD
KEY = hash16("/w/src")
RM = {"tool": "Bash", "input": {"command": CMD}}
GUARD = "PreToolUse:Bash hook error: [$HOME/.claude/hooks/guard-destructive.sh]: refusing a recursive delete"
OTHER_HOOK = "PreToolUse:Bash hook error: [$HOME/.claude/hooks/lint-check.sh]: style"


def events(steps):
    return list(events_from_records(cs.records(steps)))


def rule(mode, steps, key=KEY):
    evs = events(steps)
    i = next(n for n, e in enumerate(evs) if e.tool_uses)
    return offline.classify(mode, evs, i, evs[i].tool_uses[0], key)


# ------------------------------------------------------------------ pure rules
@pytest.mark.parametrize("mode", ["warn", "block"])
def test_auto_fp_a_a_correction_within_two_human_prompts_after_a_warn_or_block(mode):
    steps = [{"human": "clean up the build output"}, RM, {"human": "ok"}, {"human": "I told you not to delete that again"}]
    assert rule(mode, steps) == "auto_fp_correction"


def test_auto_fp_a_does_not_look_past_the_second_human_prompt():
    steps = [{"human": "clean up"}, RM, {"human": "ok"}, {"human": "fine"}, {"human": "I told you not to delete that"}]
    assert rule("block", steps) is None


@pytest.mark.parametrize("prompt", ["please remove /w/src now", 'delete "/w/src".', "rm `/w/src`, thanks", "go for ~/w/src",
                                    "remove /w/src/ please"])
def test_auto_fp_b_the_fire_target_is_in_the_latest_human_prompt_before_it(prompt):
    assert rule("observe", [{"human": prompt}, RM]) == "auto_fp_prompt_target"


def test_auto_fp_b_only_the_most_recent_prompt_before_the_fire_counts():
    assert rule("block", [{"human": "remove /w/src"}, {"human": "or maybe something else"}, RM]) is None


def test_auto_fp_b_needs_a_key():
    assert rule("block", [{"human": "remove /w/src"}, RM], key="") is None


def test_auto_fp_c_the_user_runs_the_same_rm_in_a_shell_mode_command_after_a_block():
    steps = [{"human": "tidy"}, RM, {"human": "<bash-input>rm -rf /w/src</bash-input>"}, {"human": "thanks"}]
    assert rule("block", steps) == "auto_fp_shell_rerun"
    assert rule("warn", steps) is None and rule("observe", steps) is None
    other = [{"human": "tidy"}, RM, {"human": "<bash-input>rm -rf /w/other</bash-input>"}]
    assert rule("block", other) is None


def test_auto_tp_a_an_operator_guard_hook_blocked_the_same_call():
    steps = [{"human": "tidy"}, {**RM, "result": GUARD, "error": True}]
    assert rule("observe", steps) == "auto_tp_hook_block"
    assert rule("observe", [{"human": "tidy"}, {**RM, "result": OTHER_HOOK, "error": True}]) is None
    assert rule("observe", [{"human": "tidy"}, {**RM, "result": "removed"}]) is None


def test_auto_tp_b_a_correction_in_observe_mode_is_a_true_positive_candidate_only():
    steps = [{"human": "tidy"}, RM, {"human": "why do you keep doing that"}]
    assert rule("observe", steps) == "auto_tp_correction"
    assert rule("warn", steps) == "auto_fp_correction"             # the same words are an FP once the user saw a message


def test_a_fire_with_no_signal_is_unlabelled():
    assert rule("observe", [{"human": "tidy"}, RM, {"human": "looks good"}]) is None
    assert rule("block", [{"human": "tidy"}, RM]) is None


def test_fp_rules_take_precedence_over_tp_rules():
    steps = [{"human": "remove /w/src"}, {**RM, "result": GUARD, "error": True}, {"human": "I told you not to"}]
    assert rule("warn", steps) == "auto_fp_correction"
    assert rule("observe", steps) == "auto_fp_prompt_target"


def test_agent_self_tags_and_citations_are_never_inputs():
    recs = cs.records([{"human": "tidy"}, RM])
    recs.insert(2, {"type": "assistant", "timestamp": "2026-09-15T10:00:59Z", "message": {"content": [
        {"type": "text", "text": "Applied [L-b3d1fba9] helpful; harmful if skipped; violated?"}]}})
    recs.append({"type": "user", "timestamp": "2026-09-15T10:01:00Z",
                 "message": {"role": "user", "content": "that was harmful and violated L-b3d1fba9, helpful otherwise"}})
    evs = list(events_from_records(recs))
    i = next(n for n, e in enumerate(evs) if e.tool_uses)
    assert offline.classify("observe", evs, i, evs[i].tool_uses[0], KEY) is None


def test_the_tmp_dotdot_shape_fires_rm_rf_nonscratch_because_normpath_resolves_it():
    assert cs.run_offline([{"human": "x"}, {"tool": "Bash", "input": {"command": "rm -rf /tmp/../x"}}])["rm_rf_nonscratch"] == 1
    assert cs.run_offline([{"human": "x"}, {"tool": "Bash", "input": {"command": "rm -rf /tmp/x"}}])["rm_rf_nonscratch"] == 0


# ------------------------------------------------------------------ effective label
def row(ref, label, reason="human"):
    return {"ref": ref, "label": label, "reason_code": reason}


def test_a_human_row_beats_an_auto_row_whatever_the_order():
    assert labels.effective([row("a", "tp"), row("a", "fp", "auto_fp_correction")]) == {"a": "tp"}
    assert labels.effective([row("a", "fp", "auto_fp_correction"), row("a", "tp")]) == {"a": "tp"}
    assert labels.effective([row("a", "tp"), row("a", "fp")]) == {"a": "fp"}          # human rows: the newest wins


def test_without_a_human_row_the_newest_auto_row_counts_and_incident_rows_never_do():
    assert labels.effective([row("a", "tp_candidate", "auto_tp_correction")]) == {"a": "tp_candidate"}
    assert labels.effective([row("a", "tp_candidate", "auto_tp_correction"), row("a", "fp", "auto_fp_correction")]) == {"a": "fp"}
    assert labels.effective([row("a", "incident")]) == {}
    assert labels.effective([row("a", "tp_candidate")]) == {}                 # a human row can never be a tp_candidate
    assert labels.effective([row("a", "tp", "auto_fp_correction")]) == {}     # an auto row must carry its rule's label


# ------------------------------------------------------------------ through run()
@pytest.fixture
def w(tmp_path):
    return cs.offline_world(tmp_path)


def label_rows(w):
    p = w.cfg.path("labels.jsonl")
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_run_pre_fills_an_fp_label_with_the_rule_as_its_reason_code(w):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    src = cs.put_shadow(w, "s1", CMD)
    out = offline.run(w.cfg, now=cs.NOW)
    (lab,) = label_rows(w)
    assert (lab["label"], lab["reason_code"], lab["ref"]) == ("fp", "auto_fp_prompt_target", labels.row_id(src))
    assert out["labels"]["auto_fp_prompt_target"] == 1 and sum(out["labels"].values()) == 1 and out["errors"] == 0
    assert labels.read_chain(w.cfg)[1] is None and oct(w.cfg.path("labels.jsonl").stat().st_mode & 0o777) == "0o600"


def test_run_pre_fills_a_tp_candidate_in_observe_mode(w):
    cs.put_session(w, "s1", [{"human": "tidy"}, RM, {"human": "you keep doing that"}])
    cs.put_shadow(w, "s1", CMD)
    offline.run(w.cfg, now=cs.NOW)
    (lab,) = label_rows(w)
    assert (lab["label"], lab["reason_code"]) == ("tp_candidate", "auto_tp_correction")


def test_label_list_kind_tp_candidate_lists_the_auto_rows_and_the_human_cli_never_writes_one(w, capsys):
    from ace_playbook.compile import __main__ as cli
    cs.put_session(w, "s1", [{"human": "tidy"}, RM, {"human": "you keep doing that"}])
    src = cs.put_shadow(w, "s1", CMD)
    offline.run(w.cfg, now=cs.NOW)
    assert "tp_candidate" in labels.KINDS and cli.main(["--config", w.conf, "label", "--list", "--kind", "tp_candidate"]) == 0
    listed = capsys.readouterr().out
    assert labels.row_id(src) in listed and "chain ok (1 rows)" in listed
    cli.main(["--config", w.conf, "label", "--list", "--kind", "fp"])
    assert labels.row_id(src) not in capsys.readouterr().out
    assert cli.main(["--config", w.conf, "label", "--tp", "x", "--kind", "tp_candidate"]) == 2   # --kind is for --list only
    assert "do not apply" in capsys.readouterr().err
    row, _ = labels.append_row(w.cfg, "tp", "deadbeef")
    assert row["reason_code"] == "human"                                                  # the default is unchanged


def test_run_leaves_a_fire_without_a_signal_unlabelled(w):
    cs.put_session(w, "s1", [{"human": "tidy"}, RM, {"human": "thanks"}])
    cs.put_shadow(w, "s1", CMD)
    assert sum(offline.run(w.cfg, now=cs.NOW)["labels"].values()) == 0 and label_rows(w) == []


def test_run_writes_no_auto_row_for_a_ref_that_already_has_a_human_row(w):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    src = cs.put_shadow(w, "s1", CMD)
    cs.write_labels(w.cfg, [{"ref": labels.row_id(src), "label": "tp", "reason_code": "human"}])
    assert sum(offline.run(w.cfg, now=cs.NOW)["labels"].values()) == 0
    assert [r["reason_code"] for r in label_rows(w)] == ["human"]
    assert labels.effective(label_rows(w)) == {labels.row_id(src): "tp"}


def test_run_never_writes_the_same_auto_label_twice(w):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    cs.put_shadow(w, "s1", CMD)
    offline.run(w.cfg, now=cs.NOW)
    w.cfg.path("offline_state.json").unlink()                       # force the transcript to be scanned again
    assert sum(offline.run(w.cfg, now=cs.NOW)["labels"].values()) == 0 and len(label_rows(w)) == 1


def test_self_tags_and_citations_in_a_transcript_write_no_label(w):
    path = cs.put_session(w, "s1", [{"human": "that was harmful, helpful and violated [L-b3d1fba9]"}, RM])
    with open(path, "a") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "[L-b3d1fba9] helpful"}]}}) + "\n")
    cs.put_shadow(w, "s1", CMD)
    assert sum(offline.run(w.cfg, now=cs.NOW)["labels"].values()) == 0 and label_rows(w) == []


def test_a_broken_labels_chain_is_one_error_and_the_other_stages_still_run(w):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    cs.put_shadow(w, "s1", CMD)
    cs.write_labels(w.cfg, [{"ref": "aaaaaaaa", "label": "tp"}])
    w.cfg.path("labels.jsonl").write_text(w.cfg.path("labels.jsonl").read_text().replace('"tp"', '"fp"'))
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["errors"] == 1 and out["transcripts"] == 1 and out["detector_events"] >= 1


# ------------------------------------------------------------------ report with auto rows
def test_report_counts_auto_fp_as_fp_tp_candidates_apart_and_unlabelled_on_neither_side(w, capsys):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    cs.put_session(w, "s2", [{"human": "tidy"}, RM, {"human": "you keep doing that"}])
    cs.put_session(w, "s3", [{"human": "tidy"}, RM, {"human": "thanks"}])
    for sid in ("s1", "s2", "s3"):
        cs.put_shadow(w, sid, CMD)
    offline.run(w.cfg, now=cs.NOW)
    assert report.run(w.cfg, 30, as_json=True, now="2026-10-19T12:00:00Z") == 0
    row = json.loads(capsys.readouterr().out)["private"]["proposals"][w.pid]
    assert (row["rows"], row["tp"], row["fp"], row["tp_cand"], row["unlabelled"]) == (3, 0, 1, 1, 1)


def test_report_prefers_the_human_row_over_an_auto_one(w, capsys):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    src = cs.put_shadow(w, "s1", CMD)
    offline.run(w.cfg, now=cs.NOW)
    labels.append_row(w.cfg, "tp", labels.row_id(src))               # the human decision comes after the auto fp row
    report.run(w.cfg, 30, as_json=True, now="2026-10-19T12:00:00Z")
    row = json.loads(capsys.readouterr().out)["private"]["proposals"][w.pid]
    assert (row["tp"], row["fp"], row["tp_cand"], row["unlabelled"]) == (1, 0, 0, 0)


# ------------------------------------------------------------------ evasion candidates
@pytest.fixture
def blocked(tmp_path):
    return cs.offline_world(tmp_path, mode="block")


def candidates(w):
    p = w.cfg.path("evasion_candidates.jsonl")
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_a_different_command_on_the_same_target_after_a_block_is_an_evasion_candidate_and_not_a_demotion(blocked):
    w = blocked
    cs.put_session(w, "s1", [{"human": "tidy"}, RM, {"tool": "Bash", "input": {"command": "echo hi"}},
                             {"tool": "Bash", "input": {"command": "rm -rf /w/src/"}}])
    cs.put_shadow(w, "s1", CMD, mode="block")
    out = offline.run(w.cfg, now=cs.NOW)
    (cand,) = candidates(w)
    assert out["evasion_candidates"] == 1 and out["demotions"] == 0 and not w.cfg.path("demotions.jsonl").exists()
    assert set(cand) == {"ts", "v", "id", "session", "proposal_id", "key"}
    assert (cand["session"], cand["proposal_id"], cand["key"]) == (st.session_hash("s1"), w.pid, KEY)


@pytest.mark.parametrize("steps", [
    [{"human": "tidy"}, RM, {"tool": "Bash", "input": {"command": CMD}}],                         # the same payload again
    [{"human": "tidy"}, RM, {"tool": "Bash", "input": {"command": "rm -rf /w/other"}}],           # another target
    [{"human": "tidy"}, RM, *[{"tool": "Bash", "input": {"command": f"echo {n}"}} for n in range(5)],
     {"tool": "Bash", "input": {"command": "rm -rf /w/src/"}}],                                   # the sixth tool use after
])
def test_no_evasion_candidate_without_a_different_command_on_the_same_target_within_five_tool_uses(blocked, steps):
    cs.put_session(blocked, "s1", steps)
    cs.put_shadow(blocked, "s1", CMD, mode="block")
    assert offline.run(blocked.cfg, now=cs.NOW)["evasion_candidates"] == 0 and candidates(blocked) == []


def test_only_block_rows_can_have_evasion_candidates(w):
    cs.put_session(w, "s1", [{"human": "tidy"}, RM, {"tool": "Bash", "input": {"command": "rm -rf /w/src/"}}])
    cs.put_shadow(w, "s1", CMD, mode="observe")
    assert offline.run(w.cfg, now=cs.NOW)["evasion_candidates"] == 0


def test_two_candidates_in_14_days_flag_the_proposal_for_review_without_lowering_it(blocked):
    w = blocked
    evade = [{"human": "tidy"}, RM, {"tool": "Bash", "input": {"command": "rm -rf /w/src/"}}]
    cs.put_session(w, "s1", evade)
    cs.put_session(w, "s2", evade)
    cs.put_shadow(w, "s1", CMD, mode="block", ts="2026-10-10T10:00:00Z")
    cs.put_shadow(w, "s2", CMD, mode="block", ts="2026-10-18T10:00:00Z")
    out = offline.run(w.cfg, now=cs.NOW)
    from ace_playbook.compile import demotions
    (flag,) = demotions.read_chain(w.cfg.data_dir)[0]
    assert out["evasion_candidates"] == 2 and out["demotions"] == 1
    assert (flag["cause"], flag["review_required"], flag["from_mode"], flag["to_mode"]) == ("evasion_pair", True, "block", "block")
    assert flag["ref"] == max(candidates(w), key=lambda c: c["ts"])["id"] and flag["proposal_id"] == w.pid
    w.cfg.path("offline_state.json").unlink()
    again = offline.run(w.cfg, now=cs.NOW)
    assert again["demotions"] == 0 and again["evasion_candidates"] == 0
    assert demotions.effective_cap(demotions.read_chain(w.cfg.data_dir)[0], w.pid) == "block"


def test_two_candidates_more_than_14_days_apart_do_not_flag(blocked):
    w = blocked
    evade = [{"human": "tidy"}, RM, {"tool": "Bash", "input": {"command": "rm -rf /w/src/"}}]
    cs.put_session(w, "s1", evade)
    cs.put_session(w, "s2", evade)
    cs.put_shadow(w, "s1", CMD, mode="block", ts="2026-09-20T10:00:00Z")
    cs.put_shadow(w, "s2", CMD, mode="block", ts="2026-10-18T10:00:00Z")
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["evasion_candidates"] == 2 and out["demotions"] == 0
