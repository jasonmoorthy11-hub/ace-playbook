"""The eight catalogue detectors: payload fixtures in both tiers, catalogue shape."""

import copy
import dataclasses
import json
import re

import pytest

from ace_playbook.compile import catalogue as cat
from ace_playbook.compile import detectors as d
from ace_playbook.compile import events as ev
from ace_playbook.compile import state as state_mod

from . import compile_support as cs

FX = cs.load_fixture()
PAYLOADS = FX["payloads"]
LIVE_CAPABLE = {"rm_rf", "rm_rf_nonscratch", "edit_unread_session", "edit_unread_turn", "commit_without_push"}
OFFLINE_ONLY = {"exitplan_question_block", "preclaim_audit_block", "user_correction"}
STRONG = ("edit_unread_session", "rm_rf_nonscratch", "exitplan_question_block", "preclaim_audit_block")
WEAK = ("rm_rf", "edit_unread_turn", "commit_without_push", "user_correction")


def ids(payloads):
    return [p["id"] for p in payloads]


def payloads_for(name):
    return [p for p in PAYLOADS if p["detector"] == name]


@pytest.mark.parametrize("p", PAYLOADS, ids=ids(PAYLOADS))
def test_payload_offline(p):
    n = cs.run_offline(p["steps"]).get(p["detector"], 0)
    assert (n > 0) == (p["expect"] == "fire"), (p["id"], n)


@pytest.mark.parametrize("name", STRONG + WEAK)
def test_every_detector_has_the_required_payload_mix(name):
    mine = payloads_for(name)
    assert sum(p["expect"] == "fire" for p in mine) == 1
    assert sum(p["expect"] == "clean" for p in mine) >= 3
    assert len(mine) >= (5 if name in STRONG else 4)


LIVE_PAYLOADS = [p for p in PAYLOADS if p["detector"] in LIVE_CAPABLE]


@pytest.mark.parametrize("p", LIVE_PAYLOADS, ids=ids(LIVE_PAYLOADS))
def test_payload_live(p):
    n = cs.run_live(p["steps"]).get(p["detector"], 0)
    assert (n > 0) == (p["expect"] == "fire"), (p["id"], n)


@pytest.mark.parametrize("p", [p for p in PAYLOADS if p["detector"] in OFFLINE_ONLY],
                         ids=ids([p for p in PAYLOADS if p["detector"] in OFFLINE_ONLY]))
def test_offline_only_detectors_stay_silent_live(p):
    assert cs.run_live(p["steps"]).get(p["detector"], 0) == 0


def test_catalogue_rows_match_the_design_table():
    expect = {
        "rm_rf_nonscratch": ("strong", "live+offline", "block"),
        "rm_rf": ("weak", "live+offline", "observe"),
        "edit_unread_session": ("strong", "live+offline", "warn"),
        "edit_unread_turn": ("weak", "live+offline", "observe"),
        "exitplan_question_block": ("strong", "offline", "none"),
        "preclaim_audit_block": ("strong", "offline", "none"),
        "commit_without_push": ("weak", "live+offline", "observe"),
        "user_correction": ("weak", "offline", "none"),
        "unparseable": ("weak", "live+offline", "observe"),
    }
    assert list(cat.CATALOGUE) == list(expect)
    assert cat.CATALOGUE["rm_rf_nonscratch"].lessons == ("L-b3d1fba9",)
    assert {n for n, r in cat.CATALOGUE.items() if r.strength == "strong"} == set(STRONG)
    for name, (strength, tier, compilable) in expect.items():
        row = cat.CATALOGUE[name]
        assert (row.name, row.strength, row.tier, row.compilable) == (name, strength, tier, compilable)
        assert callable(row.fire) and isinstance(row.lessons, tuple)
        assert row.consumes and isinstance(row.state, str)


def test_catalogue_consumes_and_state_columns():
    c = cat.CATALOGUE
    assert c["rm_rf_nonscratch"].consumes == ("PreToolUse:Bash",) and c["rm_rf_nonscratch"].state == "none"
    assert "PreToolUse:NotebookEdit" in c["edit_unread_session"].consumes
    assert "attachment:file" in c["edit_unread_session"].consumes
    assert "Stop" in " ".join(c["edit_unread_turn"].consumes)
    assert c["commit_without_push"].state == "git_counts"


def test_no_compilable_detector_reads_stop():
    for row in cat.CATALOGUE.values():
        if row.compilable in ("warn", "block"):
            assert not any(c.startswith("Stop") for c in row.consumes), row.name


def test_source_sha256_changes_with_the_leaf_modules(monkeypatch):
    before = {n: r.source_sha256 for n, r in cat.CATALOGUE.items()}
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in before.values()) and len(set(before.values())) == len(before)
    texts = cat._source_texts()
    assert len(texts) == 3 and all(texts)
    for i in range(len(texts)):          # events, shell, textrules: any edit moves every hash (per-detector rules: test_compile_srchash)
        changed = tuple(t + "# edit" if j == i else t for j, t in enumerate(texts))
        monkeypatch.setattr(cat, "_source_texts", lambda changed=changed: changed)
        assert all(r.source_sha256 != before[n] for n, r in cat.CATALOGUE.items()), i


@pytest.mark.parametrize("target,scratch", [
    ("/tmp/x", True), ("/private/tmp/x", True), ("/var/folders/ab/c", True), ("$TMPDIR/x", True),
    ("tmp/x", True), ("./tmp/x", True), ("node_modules", True), ("pkg/.venv/", True), ("a/__pycache__", True),
    ("/w/src", False), ("src", False), ("Q", False), ("/", False), ("~/proj", False),
    ("/tmp", True), ("/tmp/./x", True), ("${TMPDIR}/x", True), ("$TMPDIRX/x", False),
    ("/tmpfoo/x", False), ("/private/tmpx", False),
    ("${TMPDIR:-/w}/x", False), ("${TMPDIR:=/w}/x", False), ("${TMPDIR#a}/x", False),
])
def test_scratch_regex(target, scratch):
    assert bool(d.SCRATCH.search(target)) is scratch


@pytest.mark.parametrize("target", ["/tmp/../w/src", "/tmpfoo/x", "/tmp/x/../../w", "/var/folders/../w"])
def test_dotdot_and_unbounded_prefixes_are_not_scratch_after_normpath(target):
    steps = [{"human": "x"}, {"tool": "Bash", "input": {"command": "rm -rf " + target}}]
    assert cs.run_offline(steps)["rm_rf_nonscratch"] == 1


def bash(cmd, tier="live", **kw):
    p = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash", "tool_input": {"command": cmd}}
    e = ev.event_from_hook(p) if tier == "live" else ev.event_from_record(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "Bash",
                                                        "input": {"command": cmd}}]}}, {})
    return e


def test_fire_carries_detector_count_tier_and_target_hash():
    f = d.rm_rf_nonscratch(bash("rm -rf /w/src"), state_mod.new_state("s"))
    assert (f.detector, f.count, f.tier) == ("rm_rf_nonscratch", 1, "live")
    assert re.fullmatch(r"[0-9a-f]{16}", f.key) and "/w/src" not in repr(f)
    assert d.rm_rf_nonscratch(bash("rm -rf /w/src", "offline"), state_mod.new_state("s")).tier == "offline"


def test_rm_counts_each_matching_segment():
    e = bash("rm -rf /w/a && rm -fr /w/b ; rm -rf /tmp/c")
    st = state_mod.new_state("s")
    assert d.rm_rf(e, st).count == 3 and d.rm_rf_nonscratch(e, st).count == 2


def test_rm_ignores_non_bash_tools_and_non_string_commands():
    st = state_mod.new_state("s")
    e = ev.event_from_hook({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                            "tool_input": {"command": "rm -rf /w/src"}})
    assert d.rm_rf(e, st) is None
    e = ev.event_from_hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": 5}})
    assert d.rm_rf(e, st) is None and d.rm_rf_nonscratch(e, st) is None


def test_rm_quoted_target_with_spaces_is_masked_and_counts_as_nonscratch():
    assert d.rm_rf_nonscratch(bash('rm -rf "my dir"'), state_mod.new_state("s")).count == 1
    # bash -c bodies stay invisible, as in Step 0
    assert d.rm_rf(bash("bash -c 'rm -rf /w/src'"), state_mod.new_state("s")) is None


def hook(tool, path, exists=True, event="PreToolUse"):
    return ev.event_from_hook({"hook_event_name": event, "session_id": "s", "tool_name": tool,
                               "tool_input": {"file_path": path}, "tool_use_id": "t"}, probe=lambda p: exists)


def step(event, st):
    _, st, _ = cat.evaluate(event, st, strict=True)
    return st


def test_live_read_then_edit_is_clean_and_edit_alone_fires():
    st = state_mod.new_state("s")
    assert d.edit_unread_session(hook("Edit", "/w/a.py"), st).detector == "edit_unread_session"
    st = step(hook("Read", "/w/a.py"), st)
    assert d.edit_unread_session(hook("Edit", "/w/a.py"), st) is None
    # an Edit registers its own path, so a second Edit of the same file is clean
    st = step(hook("Edit", "/w/b.py"), st)
    assert d.edit_unread_session(hook("Edit", "/w/b.py"), st) is None


def test_live_absent_path_is_a_new_file_not_a_fire():
    st = state_mod.new_state("s")
    assert d.edit_unread_session(hook("Write", "/w/new.py", exists=False), st) is None
    assert d.edit_unread_turn(hook("Write", "/w/new.py", exists=False), st) is None


def test_live_unknown_existence_still_fires():
    e = hook("Edit", "/w/a.py")
    e.tool_uses[0].exists = None
    assert d.edit_unread_session(e, state_mod.new_state("s")) is not None


def test_autoloaded_basenames_and_plan_files_are_exempt():
    st = state_mod.new_state("s")
    for path in ("/w/CLAUDE.md", "/w/MEMORY.md", "/w/AGENTS.md", "/h/.claude/plans/p.md"):
        assert d.edit_unread_session(hook("Edit", path), st) is None, path
    assert d.edit_unread_session(hook("Edit", "/w/notes/CLAUDE.md.bak"), st) is not None


def test_multiedit_fires_and_notebookedit_registers_a_path():
    st = state_mod.new_state("s")
    assert d.edit_unread_session(hook("MultiEdit", "/w/a.py"), st) is not None
    e = ev.event_from_hook({"hook_event_name": "PreToolUse", "tool_name": "NotebookEdit",
                            "tool_input": {"notebook_path": "/w/n.ipynb"}}, probe=lambda p: True)
    assert d.edit_unread_session(e, st) is None
    st = step(e, st)
    assert d.edit_unread_session(hook("Edit", "/w/n.ipynb"), st) is None


def test_user_prompt_submit_does_not_reset_the_turn_set():
    st = step(hook("Read", "/w/a.py"), state_mod.new_state("s"))
    st = step(ev.event_from_hook({"hook_event_name": "UserPromptSubmit", "prompt": "next task"}), st)
    assert d.edit_unread_turn(hook("Edit", "/w/a.py"), st) is None


def test_notebookedit_registers_live_only():
    nb = {"hook_event_name": "PreToolUse", "tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/w/n.ipynb"}}
    live = step(ev.event_from_hook(nb, probe=lambda p: True), state_mod.new_state("s"))
    assert live["seen_paths"] == [ev.hash16("/w/n.ipynb")]
    rec = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "NotebookEdit", "input": {"notebook_path": "/w/n.ipynb"}}]}}
    assert step(ev.event_from_record(rec), state_mod.new_state("s"))["seen_paths"] == []


def test_hook_name_bracket_is_capped_before_the_regex():
    st = state_mod.new_state("s")
    long = "Stop hook feedback:\n[" + "a" * 600 + " stop-mid-task-audit.sh]: x"
    assert d.preclaim_audit_block(stop_event(long), st) is None


def test_stop_resets_the_turn_set_but_not_the_session_set():
    st = step(hook("Read", "/w/a.py"), state_mod.new_state("s"))
    assert d.edit_unread_turn(hook("Edit", "/w/a.py"), st) is None
    st = step(ev.event_from_hook({"hook_event_name": "Stop", "session_id": "s"}), st)
    assert d.edit_unread_turn(hook("Edit", "/w/a.py"), st) is not None
    assert d.edit_unread_session(hook("Edit", "/w/a.py"), st) is None


def test_offline_attachment_reads_count_as_reads():
    st = state_mod.new_state("s")
    att = ev.event_from_record({"type": "attachment", "attachment": {"type": "edited_text_file", "filename": "/w/a.py"}})
    st = step(att, st)
    steps = [{"human": "x"}, {"tool": "Edit", "input": {"file_path": "/w/a.py"}, "result": "ok"}]
    evs = list(ev.events_from_records(cs.records(steps)))
    total = 0
    for e in evs:
        fires, st, _ = cat.evaluate(e, st, strict=True)
        total += sum(f.count for f in fires if f.detector == "edit_unread_session")
    assert total == 0


def test_offline_fire_waits_for_the_result_and_skips_errors_and_new_files():
    st = state_mod.new_state("s")
    a, r = list(ev.events_from_records(cs.records(
        [{"tool": "Edit", "input": {"file_path": "/w/a.py"}, "result": "updated"}])))
    fires, st, _ = cat.evaluate(a, st, strict=True)
    assert fires == [] and "t0" in st["pending"]
    fires, st, _ = cat.evaluate(r, st, strict=True)
    assert {f.detector for f in fires} == {"edit_unread_session", "edit_unread_turn"} and st["pending"] == {}


def test_offline_write_file_created_and_error_results_do_not_fire():
    for tool, result, err in (("Write", "File created successfully at /w/n.py", False),
                              ("Edit", "String not found", True)):
        counts = cs.run_offline([{"human": "x"}, {"tool": tool, "input": {"file_path": "/w/n.py"},
                                                  "result": result, "error": err}])
        assert counts.get("edit_unread_session", 0) == 0, (tool, result)


def test_human_prompt_resets_the_turn_set_but_a_queued_one_does_not():
    base = [{"human": "one"}, {"tool": "Read", "input": {"file_path": "/w/a.py"}}]
    edit = {"tool": "Edit", "input": {"file_path": "/w/a.py"}, "result": "updated"}
    assert cs.run_offline(base + [{"queued": "more words"}, edit]).get("edit_unread_turn", 0) == 0
    assert cs.run_offline(base + [{"human": "two"}, edit]).get("edit_unread_turn", 0) == 1


def test_path_sets_cap_at_5000_and_a_truncated_state_silences_the_edit_detectors():
    st = state_mod.new_state("s")
    st["seen_paths"] = [format(i, "016x") for i in range(d.MAX_PATHS)]
    st = step(hook("Read", "/w/one-more.py"), st)
    assert st["truncated"] is True and len(st["seen_paths"]) == d.MAX_PATHS
    assert d.edit_unread_session(hook("Edit", "/w/a.py"), st) is None
    assert d.edit_unread_turn(hook("Edit", "/w/a.py"), st) is None


def test_only_hashes_of_paths_are_stored():
    st = step(hook("Read", "/w/secret-name.py"), state_mod.new_state("s"))
    assert "secret-name" not in json.dumps(st) and st["seen_paths"] == [ev.hash16("/w/secret-name.py")]


def test_advance_and_detectors_do_not_mutate_their_input_state():
    st = state_mod.new_state("s")
    frozen = copy.deepcopy(st)
    e = hook("Edit", "/w/a.py")
    d.edit_unread_session(e, st)
    d.advance(e, st)
    cat.evaluate(e, st, strict=True)
    assert st == frozen


def result_event(text, tool="Bash", error=True):
    recs = cs.records([{"tool": tool, "input": {"command": "x"}, "result": text, "error": error}])
    return list(ev.events_from_records(recs))[1]


def test_exitplan_requires_the_hook_error_prefix_and_script_name():
    st = state_mod.new_state("s")
    ok = "PreToolUse:ExitPlanMode hook error: [bash ~/.claude/hooks/pretooluse-exitplan-question-check.sh]: no"
    assert d.exitplan_question_block(result_event(ok, "ExitPlanMode"), st).count == 1
    assert d.exitplan_question_block(result_event("PostToolUse:Edit hook error: [pretooluse-exitplan-question-check.sh]: x"), st)
    assert d.exitplan_question_block(result_event("note: pretooluse-exitplan-question-check.sh blocked"), st) is None
    assert d.exitplan_question_block(result_event(ok.replace("question-check", "other-check")), st) is None


def stop_event(text):
    return ev.event_from_record({"type": "user", "message": {"content": text}})


def test_preclaim_matches_user_and_plugin_variants_only_when_prefixed():
    st = state_mod.new_state("s")
    for bracket in ("bash ~/.claude/hooks/stop-mid-task-audit.sh", 'bash "/p/plugins/core/scripts/stop-mid-task-audit.sh"'):
        assert d.preclaim_audit_block(stop_event(f"Stop hook feedback:\n[{bracket}]: stop"), st).count == 1
    assert d.preclaim_audit_block(stop_event("Note: Stop hook feedback:\n[stop-mid-task-audit.sh]: x"), st) is None
    assert d.preclaim_audit_block(stop_event("Stop hook feedback:\nno bracket at all"), st) is None


def test_exitplan_and_preclaim_do_not_fire_in_the_live_tier():
    st = state_mod.new_state("s")
    e = ev.event_from_hook({"hook_event_name": "PostToolUse", "tool_name": "ExitPlanMode", "tool_response":
                            "PreToolUse:ExitPlanMode hook error: [pretooluse-exitplan-question-check.sh]: x"})
    assert d.exitplan_question_block(e, st) is None


@pytest.mark.parametrize("text,fires", [
    ("you keep doing that", True), ("I already told you to stop", True), ("how many times do I say it", True),
    ("you forgot the import again", True), ("like I said, use tabs", True),
    ("please add a login button", False), ("this is great", False), ("what do you recommend", False),
])
def test_user_correction_phrases(text, fires):
    e = ev.event_from_record({"type": "user", "message": {"content": text}})
    assert (d.user_correction(e, state_mod.new_state("s")) is not None) is fires


def test_user_correction_skips_long_prompts_duplicates_and_live_events():
    st = state_mod.new_state("s")
    long = ev.event_from_record({"type": "user", "message": {"content": "stop adding " + "x" * 900}})
    assert d.user_correction(long, st) is None
    dup = ev.event_from_record({"type": "user", "message": {"content": "you keep doing that"}})
    dup.dup = True
    assert d.user_correction(dup, st) is None
    live = ev.event_from_hook({"hook_event_name": "UserPromptSubmit", "prompt": "you keep doing that"})
    assert d.user_correction(live, st) is None


def test_commit_counts_accumulate_in_state_and_fire_only_at_session_end():
    st = state_mod.new_state("s")
    st = step(bash("git commit -m m"), st)
    assert st["git"] == {"commit": 1, "push": 0}
    assert d.commit_without_push(bash("ls"), st) is None
    assert d.commit_without_push(ev.end_event("live"), st).detector == "commit_without_push"
    st = step(bash("git -C /w push origin main"), st)
    assert d.commit_without_push(ev.end_event("live"), st) is None
    assert d.commit_without_push(ev.end_event("offline"), state_mod.new_state("s")) is None


def test_evaluate_runs_the_catalogue_in_order_and_filters_by_name():
    e = bash("cd /w && rm -rf /w/src")
    fires, st, _ = cat.evaluate(e, state_mod.new_state("s"), strict=True)
    assert [f.detector for f in fires] == ["rm_rf_nonscratch", "rm_rf"] and st["events"] == 1
    fires, _, _ = cat.evaluate(e, state_mod.new_state("s"), names=["rm_rf"], strict=True)
    assert [f.detector for f in fires] == ["rm_rf"]
    with pytest.raises(KeyError):
        cat.evaluate(e, state_mod.new_state("s"), names=["nope"])


def test_a_failing_state_transition_is_reported_and_keeps_the_old_state(monkeypatch):
    def boom(event, state):
        raise ValueError("bug")

    monkeypatch.setattr(cat, "advance", boom)
    st = state_mod.new_state("s")
    fires, new, errors = cat.evaluate(bash("ls"), st)
    assert new is st and errors == [("advance", "ValueError")] and fires == []
    with pytest.raises(ValueError):
        cat.evaluate(bash("ls"), st, strict=True)


def test_a_raising_detector_is_reported_and_strict_mode_reraises(monkeypatch):
    def boom(event, state):
        raise RuntimeError("bug")

    monkeypatch.setitem(cat.CATALOGUE, "rm_rf", dataclasses.replace(cat.CATALOGUE["rm_rf"], fire=boom))
    fires, _, errors = cat.evaluate(bash("rm -rf /w/src"), state_mod.new_state("s"))
    assert [f.detector for f in fires] == ["rm_rf_nonscratch"] and errors == [("rm_rf", "RuntimeError")]
    with pytest.raises(RuntimeError):
        cat.evaluate(bash("rm -rf /w/src"), state_mod.new_state("s"), strict=True)
