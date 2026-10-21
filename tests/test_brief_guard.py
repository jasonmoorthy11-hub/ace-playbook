"""`hooks/brief_guard.py`: the brief's PreToolUse deny hook (second of three read-only controls; docs/design/compiler.md 8.x)."""

import json
import os
import subprocess
import sys

import pytest

from .test_statusline import ROOT

GUARD = ROOT / "hooks" / "brief_guard.py"
ALLOW = ROOT / "hooks" / "brief_allowed_tools.txt"
DENY = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "brief is read-only"}}


def guard(stdin, *args, allowlist=None):
    env = {"PATH": os.environ["PATH"]}
    if allowlist is not None:
        env["ACE_BRIEF_ALLOWLIST"] = str(allowlist)
    p = subprocess.run([sys.executable, "-I", "-S", str(GUARD), *args], input=stdin if isinstance(stdin, str) else json.dumps(stdin),
                       capture_output=True, text=True, env=env, timeout=10)
    return p.returncode, p.stdout, p.stderr


def call(name):
    return {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": {}}


def names():
    return [ln.strip() for ln in ALLOW.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def test_the_allowlist_file_holds_exactly_the_read_only_tools():
    assert names() == ["mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Google_Calendar__search_events",
                       "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
                       "mcp__claude_ai_Linear__list_issues", "mcp__claude_ai_Linear__get_notifications", "ToolSearch"]


@pytest.mark.parametrize("name", names())
def test_an_allowed_tool_prints_nothing_and_exits_0(name):
    assert guard(call(name)) == (0, "", "")


@pytest.mark.parametrize("name", ["mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Gmail__trash_thread", "mcp__claude_ai_Gmail__reply",
                                  "mcp__claude_ai_Google_Calendar__create_event", "mcp__claude_ai_Linear__save_issue", "mcp__unknown__x",
                                  "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "Task"])
def test_every_other_connector_and_every_write_tool_is_denied(name):
    code, out, _ = guard(call(name))
    assert code == 0 and json.loads(out) == DENY


def test_a_plain_non_mcp_read_tool_is_not_the_guards_business():
    assert guard(call("Read")) == (0, "", "")


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", '{"tool_name": 7}', "{}", '{"tool_name": null}', "\x00"])
def test_malformed_input_is_denied(stdin):
    code, out, _ = guard(stdin)
    assert code == 0 and json.loads(out) == DENY


def test_a_missing_or_empty_allowlist_denies_even_the_listed_names(tmp_path):
    for target in (tmp_path / "nowhere.txt", tmp_path):
        code, out, _ = guard(call("mcp__claude_ai_Gmail__search_threads"), allowlist=target)
        assert code == 0 and json.loads(out) == DENY
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    assert json.loads(guard(call("mcp__claude_ai_Gmail__search_threads"), allowlist=empty)[1]) == DENY


def test_a_custom_allowlist_is_honoured_exactly(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("# c\nmcp__x__read\n\n")
    assert guard(call("mcp__x__read"), allowlist=f) == (0, "", "")
    assert json.loads(guard(call("mcp__x__read2"), allowlist=f)[1]) == DENY


def test_hard_mode_exits_2_with_the_reason_on_stderr_and_nothing_on_stdout():
    assert guard(call("mcp__claude_ai_Gmail__send_message"), "--hard") == (2, "", "brief is read-only\n")
    assert guard("garbage", "--hard") == (2, "", "brief is read-only\n")
    assert guard(call("mcp__claude_ai_Gmail__get_thread"), "--hard") == (0, "", "")


def test_the_settings_file_parses_wires_the_guard_to_every_tool_and_denies_writes():
    s = json.loads((ROOT / "hooks" / "brief-settings.json").read_text())
    (entry,) = s["hooks"]["PreToolUse"]
    assert entry["matcher"] == "" and entry["hooks"][0]["type"] == "command" and entry["hooks"][0]["timeout"] == 5
    cmd = entry["hooks"][0]["command"]
    assert cmd.startswith('python3 -I -S "$ACE_PLAYBOOK_REPO/hooks/brief_guard.py"') and cmd.endswith("|| exit 2")   # a crash blocks too
    assert s["permissions"]["deny"] == ["Bash", "Edit", "Write", "WebFetch", "WebSearch", "Agent"]
    load = "import json,sys; json.load(open(sys.argv[1]))"
    assert subprocess.run(["python3", "-I", "-S", "-c", load, str(ROOT / "hooks/brief-settings.json")]).returncode == 0
