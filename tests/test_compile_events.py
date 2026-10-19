"""Event model, the two adapters, and the ported command tokenizer (compile/events.py)."""

import ast
import re
from pathlib import Path

import pytest

from ace_playbook.compile import detectors
from ace_playbook.compile import events as ev
from ace_playbook.compile import state as state_mod

COMPILE_DIR = Path(ev.__file__).parent


def pre(tool="Bash", tool_input=None, **extra):
    p = {"hook_event_name": "PreToolUse", "session_id": "s-1", "tool_name": tool,
         "tool_input": {"command": "ls"} if tool_input is None else tool_input, "tool_use_id": "tu-1"}
    p.update(extra)
    return p


# ------------------------------------------------------------------ hook adapter
def test_hook_pretooluse_maps_to_one_tool_use():
    e = ev.event_from_hook(pre("Edit", {"file_path": "/w/a.py"}), probe=lambda p: True)
    assert (e.tier, e.hook, e.session_id) == ("live", "PreToolUse", "s-1")
    (tu,) = e.tool_uses
    assert (tu.id, tu.name, tu.input, tu.exists) == ("tu-1", "Edit", {"file_path": "/w/a.py"}, True)


def test_hook_unknown_fields_are_ignored():
    p = pre(future_field={"a": 1}, cwd="/w", permission_mode="default", another=[1, 2])
    p["tool_input"]["surprise"] = {"nested": True}
    e = ev.event_from_hook(p)
    assert e.tool_uses[0].input["command"] == "ls"
    assert not hasattr(e, "future_field")


def test_hook_missing_fields_default():
    e = ev.event_from_hook({})
    assert (e.hook, e.session_id, e.tool_uses, e.tool_results, e.user_text, e.human) == ("", "", [], [], "", False)
    e = ev.event_from_hook({"hook_event_name": "PreToolUse"})
    assert e.tool_uses[0].name == "" and e.tool_uses[0].input == {} and e.tool_uses[0].id == ""


@pytest.mark.parametrize("payload", [None, [], "text", 7, b"bytes"])
def test_hook_non_dict_payload_is_an_empty_event(payload):
    e = ev.event_from_hook(payload)
    assert e.tier == "live" and e.tool_uses == [] and e.user_text == ""


@pytest.mark.parametrize("bad", [None, "ls", ["ls"], 5])
def test_hook_tool_input_of_wrong_type_becomes_empty(bad):
    p = pre()
    p["tool_input"] = bad
    assert ev.event_from_hook(p).tool_uses[0].input == {}


def test_hook_wrong_typed_scalars_are_coerced_or_dropped():
    e = ev.event_from_hook({"hook_event_name": "PreToolUse", "session_id": 5, "tool_name": ["x"], "tool_use_id": None})
    assert e.session_id == "" and e.tool_uses[0].name == "" and e.tool_uses[0].id == ""


def test_hook_shell_metacharacters_are_data(tmp_path):
    marker = tmp_path / "pwned"
    cmd = f"echo $(touch {marker}1) `touch {marker}2`; touch {marker}3 | sh -c 'touch {marker}4' && rm -rf /w/x"
    e = ev.event_from_hook(pre(tool_input={"command": cmd}))
    assert e.tool_uses[0].input["command"] == cmd
    segs = ev.command_segments(cmd)
    assert segs and all(isinstance(t, str) for s in segs for t in s)
    fires, _, _ = detectors.evaluate(e, state_mod.new_state("s-1"), strict=True)
    assert sorted(f.detector for f in fires) == ["rm_rf", "rm_rf_nonscratch"]
    assert not list(tmp_path.iterdir()), "nothing in the payload may have been executed"


def test_hook_edit_probe_sets_exists_and_read_stays_unknown():
    seen = []

    def probe(path):
        seen.append(path)
        return False

    e = ev.event_from_hook(pre("Write", {"file_path": "/w/new.py"}), probe=probe)
    assert e.tool_uses[0].exists is False and seen == ["/w/new.py"]
    e = ev.event_from_hook(pre("Read", {"file_path": "/w/a.py"}), probe=probe)
    assert e.tool_uses[0].exists is None and seen == ["/w/new.py"]


def test_hook_probe_errors_leave_exists_unknown():
    def boom(path):
        raise OSError("denied")

    e = ev.event_from_hook(pre("Edit", {"file_path": "/w/a.py"}), probe=boom)
    assert e.tool_uses[0].exists is None
    e = ev.event_from_hook(pre("Edit", {"file_path": "/w/a\x00.py"}))  # os.path.lexists swallows ValueError
    assert e.tool_uses[0].exists is False


def test_hook_default_probe_uses_lexists(tmp_path):
    present = tmp_path / "there.py"
    present.write_text("x")
    assert ev.event_from_hook(pre("Edit", {"file_path": str(present)})).tool_uses[0].exists is True
    assert ev.event_from_hook(pre("Edit", {"file_path": str(tmp_path / "gone.py")})).tool_uses[0].exists is False


def test_hook_oversize_command_is_capped():
    e = ev.event_from_hook(pre(tool_input={"command": "a" * (ev.MAX_CMD + 500)}))
    assert len(e.tool_uses[0].input["command"]) == ev.MAX_CMD


def test_hook_posttooluse_maps_to_a_result():
    p = {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash", "tool_use_id": "t9",
         "tool_input": {"command": "false"}, "tool_response": {"stdout": "", "stderr": "boom", "is_error": True}}
    (tr,) = ev.event_from_hook(p).tool_results
    assert (tr.id, tr.name, tr.input, tr.is_error) == ("t9", "Bash", {"command": "false"}, True)
    assert "boom" in tr.text
    p["tool_response"] = "plain text result"
    assert ev.event_from_hook(p).tool_results[0].text == "plain text result"
    p["tool_response"] = None
    assert ev.event_from_hook(p).tool_results[0].text == ""


def test_hook_userpromptsubmit_and_stop():
    e = ev.event_from_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "do the thing"})
    assert (e.user_text, e.human) == ("do the thing", True)
    e = ev.event_from_hook({"hook_event_name": "UserPromptSubmit", "prompt": "<task-notification>x"})
    assert e.human is False
    e = ev.event_from_hook({"hook_event_name": "Stop", "session_id": "s", "stop_hook_active": True,
                            "last_assistant_message": "done"})
    assert (e.hook, e.tool_uses, e.human) == ("Stop", [], False)


def test_end_event_marks_session_end():
    assert ev.end_event("live").rtype == "session_end" and ev.end_event().tier == "offline"


# ------------------------------------------------------------------ transcript adapter
def assistant(tid, name, inp, **kw):
    r = {"type": "assistant", "timestamp": "2026-09-15T10:00:00Z",
         "message": {"id": "m", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}
    r.update(kw)
    return r


def result(tid, content, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}]}}


def test_record_tool_use_and_result_link_through_the_index():
    idx = {}
    a = ev.event_from_record(assistant("t1", "Bash", {"command": "ls"}), idx)
    assert a.tier == "offline" and a.tool_uses[0].name == "Bash" and "t1" in idx
    r = ev.event_from_record(result("t1", "out", True), idx)
    (tr,) = r.tool_results
    assert (tr.name, tr.input, tr.text, tr.is_error) == ("Bash", {"command": "ls"}, "out", True)
    assert r.user_text == "" and r.human is False


def test_record_result_content_blocks_are_joined():
    r = ev.event_from_record(result("t1", [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}, "junk"]), {})
    assert r.tool_results[0].text == "a b"


def test_record_unknown_fields_ignored_and_missing_default():
    e = ev.event_from_record({"type": "assistant", "mystery": {"x": 1}, "message": {"content": "not a list"}})
    assert e.tool_uses == [] and e.ts == "" and e.sidechain is False
    e = ev.event_from_record({})
    assert e.rtype == "" and e.user_text == ""
    e = ev.event_from_record({"type": "user", "message": None})
    assert e.human is False


def test_record_human_vs_synthetic_text():
    human = ev.event_from_record({"type": "user", "message": {"content": "please fix the bug"}})
    assert human.human and human.user_text == "please fix the bug"
    for text in ("<local-command-stdout>x</local-command-stdout>", "[Request interrupted]",
                 "Stop hook feedback:\nx", "This session is being continued from"):
        assert ev.event_from_record({"type": "user", "message": {"content": text}}).human is False, text
    assert ev.event_from_record({"type": "user", "isMeta": True, "message": {"content": "hello"}}).human is False
    assert ev.event_from_record({"type": "user", "isCompactSummary": True, "message": {"content": "hi"}}).human is False


def test_record_attachments():
    f = ev.event_from_record({"type": "attachment", "attachment": {"type": "file", "filename": "/w/a.py"}})
    assert f.read_files == ["/w/a.py"]
    q = ev.event_from_record({"type": "attachment", "attachment": {
        "type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": "hello there"}})
    assert (q.queued, q.human, q.user_text) == (True, True, "hello there")
    junk = ev.event_from_record({"type": "attachment", "attachment": "not a dict"})
    assert junk.attachment == {} and junk.read_files == []


def test_events_from_records_marks_duplicate_queued_prompt():
    recs = [{"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "prompt",
                                                  "origin": {"kind": "human"}, "prompt": "same words"}},
            {"type": "user", "message": {"content": "same words"}},
            {"type": "user", "message": {"content": "same words"}}]
    out = list(ev.events_from_records(recs))
    assert [(e.queued, e.dup) for e in out] == [(True, False), (False, True), (False, False)]


def test_events_from_records_skips_sidechain_and_non_dicts():
    recs = [{"type": "user", "isSidechain": True, "message": {"content": "side"}}, "junk", 5,
            {"type": "user", "message": {"content": "main"}}]
    assert [e.user_text for e in ev.events_from_records(recs)] == ["main"]
    assert len(list(ev.events_from_records(recs, include_sidechain=True))) == 2


# ------------------------------------------------------------------ tokenizer (ported)
def test_command_segments_mask_quotes_and_heredocs():
    assert ev.command_segments('echo "rm -rf x" && ls') == [["echo", "Q"], ["ls"]]
    assert ev.command_segments("cat <<'EOF'\nrm -rf x\nEOF\nls") == [["cat"], ["ls"]]


def test_command_segments_keep_simple_quoted_paths_and_strip_prefixes():
    assert ev.command_segments("rm -rf '/w/dir'") == [["rm", "-rf", "/w/dir"]]
    assert ev.command_segments("FOO=1 sudo nohup python x.py") == [["python", "x.py"]]
    assert ev.command_segments("(cd /w; ls) | wc") == [["cd", "/w"], ["ls"], ["wc"]]
    assert ev.command_segments("") == [] and ev.command_segments(None) == []


def test_command_segments_is_bounded_on_pathological_heredocs():
    import time
    t0 = time.monotonic()
    ev.command_segments("<<a\n" * 16000)
    ev.command_segments(("cat <<EOF\nx\n" * 5000)[:ev.MAX_CMD])
    assert time.monotonic() - t0 < 1.0
    many = "ls a\n" + "<<a\n" * 40 + "ls tail"
    assert ev.command_segments(many)[0] == ["ls", "a"]


def test_git_subcommand():
    assert ev.git_subcommand(["git", "-C", "x", "push", "--force"]) == ("push", ["--force"])
    assert ev.git_subcommand(["git", "commit", "-m", "Q"]) == ("commit", ["-m", "Q"])
    assert ev.git_subcommand(["git"]) == (None, [])
    assert ev.git_subcommand(["ls", "push"]) == (None, [])
    assert ev.git_subcommand([]) == (None, [])


def test_hash16_is_stable_and_short():
    assert ev.hash16("/w/a.py") == ev.hash16("/w/a.py") != ev.hash16("/w/b.py")
    assert re.fullmatch(r"[0-9a-f]{16}", ev.hash16("x"))


# ------------------------------------------------------------------ tokenizer hardening (fix wave A-D)
def fires_for(cmd, tier="live"):
    e = ev.event_from_hook(pre(tool_input={"command": cmd}))
    fires, _, _ = detectors.evaluate(e, state_mod.new_state("s-1"), strict=True)
    return {f.detector: f.count for f in fires}


SLOW_INPUTS = {
    "unterminated-dq-with-escapes": '"' + '\\"' * 32767,
    "heredoc-32k-delimiter": "<<" + "a" * 32768,
    "heredoc-256-delimiter": ("<<" + "a" * 256 + "\ncat\n") * 3,
    "many-openers-no-terminator": "<<a\n" * 16000,
    "heredoc-repeated": ("cat <<EOF\nx\n" * 5000),
    "dq-backslash-newline": '"\\\n' * 20000,
    "single-quotes": "'" * 60000,
    "64kb-padding": ("x " * 40000),
}


@pytest.mark.parametrize("name", sorted(SLOW_INPUTS))
def test_evaluate_is_fast_on_hostile_commands(name):
    import time
    e = ev.event_from_hook(pre(tool_input={"command": SLOW_INPUTS[name]}))
    t0 = time.monotonic()
    detectors.evaluate(e, state_mod.new_state("s-1"), strict=True)
    assert time.monotonic() - t0 <= 0.1, name


def test_segments_are_computed_once_per_tool_use(monkeypatch):
    from ace_playbook.compile import shell
    calls = []
    real = shell.command_segments
    monkeypatch.setattr(ev, "command_segments", lambda c: (calls.append(c), real(c))[1])
    e = ev.event_from_hook(pre(tool_input={"command": "git commit -m m && rm -rf /w/src"}))
    detectors.evaluate(e, state_mod.new_state("s-1"), strict=True)
    assert len(calls) == 1


def test_dropped_tail_after_many_herestrings_still_fires():
    assert "rm_rf_nonscratch" in fires_for("cat <<<x; " * 33 + "rm -rf /w/src")


def test_cap_marks_truncated_and_emits_unparseable():
    cmd = "x " * (ev.MAX_CMD // 2) + "; rm -rf /w/src"
    e = ev.event_from_hook(pre(tool_input={"command": cmd}))
    assert e.tool_uses[0].truncated is True and len(e.tool_uses[0].input["command"]) == ev.MAX_CMD
    got = fires_for(cmd)
    assert got.get("unparseable") == 1 and "rm_rf_nonscratch" not in got
    assert "unparseable" not in fires_for("ls -la")


def test_unparseable_is_a_weak_observe_catalogue_row():
    row = detectors.CATALOGUE["unparseable"]
    assert (row.strength, row.compilable) == ("weak", "observe")


def test_heredoc_removal_strips_only_the_body():
    assert "rm_rf_nonscratch" in fires_for("cat <<EOF && rm -rf /w/src\nbody\nEOF")
    assert ev.command_segments("cat <<'EOF'\nrm -rf x\nEOF\nls") == [["cat"], ["ls"]]
    assert ev.command_segments("cat <<EOF\nEOF\nls") == [["cat"], ["ls"]]


def test_openers_inside_quotes_or_herestrings_are_not_heredocs():
    assert "rm_rf_nonscratch" in fires_for('echo "<<EOF"\nrm -rf /w/src\nEOF')
    assert "rm_rf_nonscratch" in fires_for("cat <<<EOF\nrm -rf /w/src\nEOF")


@pytest.mark.parametrize("cmd", ["true & rm -rf /w/src", "{ rm -rf /w/src; }", "if true; then rm -rf /w/src; fi",
                                 "! rm -rf /w/src", "while true; do rm -rf /w/src; done", "a || { rm -rf /w/src; }"])
def test_shell_syntax_prefixes_and_background_ampersand_are_split(cmd):
    assert fires_for(cmd).get("rm_rf_nonscratch") == 1, cmd


def test_redirect_ampersands_are_not_separators():
    assert ev.command_segments("ls 2>&1 | wc") == [["ls", "2>&1"], ["wc"]]
    assert ev.command_segments("ls &> out") == [["ls", "&>", "out"]]
    assert ev.command_segments("a && b") == [["a"], ["b"]]


@pytest.mark.parametrize("cmd", ["/bin/rm -rf /w/src", "\\rm -rf /w/src", "env rm -rf /w/src", "nice rm -rf /w/src",
                                 "xargs rm -rf /w/src", "doas rm -rf /w/src", "busybox rm -rf /w/src",
                                 "command rm -rf /w/src", "exec rm -rf /w/src", "sudo rm -rf /w/src",
                                 "env FOO=1 /usr/bin/rm -rf /w/src", "xargs -0 rm -rf /w/src"])
def test_rm_wrappers_and_paths_are_seen(cmd):
    assert fires_for(cmd).get("rm_rf_nonscratch") == 1, cmd


# ------------------------------------------------------------------ source hygiene
def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_compile_sources_never_spawn_or_eval_or_touch_the_network():
    banned_mods = {"subprocess", "socket", "urllib", "http", "ssl", "sqlite3", "requests", "ctypes", "pty"}
    banned_calls = {"eval", "exec", "compile", "__import__", "os.system", "os.popen", "os.spawnl", "os.execv",
                    "os.execvp", "os.execl", "os.fork"}
    files = sorted(COMPILE_DIR.glob("*.py"))
    assert {p.name for p in files} >= {"__init__.py", "events.py", "detectors.py", "state.py"}
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                mods = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods = {(node.module or "").split(".")[0]} if node.level == 0 else set()
            else:
                mods = set()
            assert not (mods & banned_mods), (path.name, mods)
            if isinstance(node, ast.Call):
                assert _dotted(node.func) not in banned_calls, (path.name, _dotted(node.func))


def test_compile_files_stay_within_the_line_budget():
    for path in sorted(COMPILE_DIR.glob("*.py")):
        assert len(path.read_text().splitlines()) <= 300, path.name
