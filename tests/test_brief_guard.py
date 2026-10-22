"""`hooks/brief_guard.py`: a deny-by-default PreToolUse hook, tested but NOT run by the brief (disableAllHooks; docs/design/compiler.md 8.x)."""

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
                                  "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "Task",
                                  "Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite", "SomeFutureTool", "toolsearch", "ToolSearch2"])
def test_deny_by_default_every_tool_not_on_the_allowlist_is_denied(name):
    code, out, _ = guard(call(name))
    assert code == 0 and json.loads(out) == DENY


def test_read_and_glob_are_denied_and_toolsearch_is_allowed():
    assert json.loads(guard(call("Read"))[1]) == DENY and json.loads(guard(call("Glob"))[1]) == DENY
    assert guard(call("ToolSearch")) == (0, "", "")
    assert not {"Read", "Glob", "Grep", "LS", "NotebookRead"} & set(names())


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


def test_the_settings_file_disables_all_hooks_has_no_hooks_block_and_denies_writes():
    s = json.loads((ROOT / "hooks" / "brief-settings.json").read_text())
    assert s["disableAllHooks"] is True and "hooks" not in s and set(s) == {"disableAllHooks", "permissions"}   # the guard does not run here
    builtin = ["Bash", "Edit", "Write", "WebFetch", "WebSearch", "Agent", "Read", "Glob", "Grep", "LS", "NotebookRead", "MultiEdit",
               "NotebookEdit", "Task"]
    denied = [ln.strip() for ln in (ROOT / "hooks" / "brief_denied_tools.txt").read_text().splitlines() if ln.strip() and ln[0] != "#"]
    assert s["permissions"]["deny"] == builtin + denied              # the settings list is the built-ins plus the data file, in order


def test_the_connector_deny_list_names_every_write_tool_of_the_three_servers_and_whole_other_servers():
    denied = [ln.strip() for ln in (ROOT / "hooks" / "brief_denied_tools.txt").read_text().splitlines() if ln.strip() and ln[0] != "#"]
    for name in ("Gmail__send_message", "Gmail__reply", "Gmail__forward", "Gmail__trash_thread", "Gmail__create_draft", "Gmail__update_draft",
                 "Google_Calendar__create_event", "Google_Calendar__update_event", "Google_Calendar__delete_event",
                 "Google_Calendar__respond_to_event", "Linear__save_issue", "Linear__save_comment", "Linear__delete_comment",
                 "Linear__merge_diff", "Linear__share_issue", "Linear__mark_notification", "Linear__prepare_attachment_upload"):
        assert f"mcp__claude_ai_{name}" in denied, name
    for server in ("Google_Drive", "Slack", "Canva", "Claude_Docs", "Microsoft_365", "rey_robin"):
        assert f"mcp__claude_ai_{server}" in denied
    allowed = set(names())
    assert not allowed & set(denied) and len(denied) == len(set(denied))                      # nothing allowed is also denied
    verbs = ("send", "create", "update", "delete", "save", "reply", "forward", "trash", "label", "mark", "merge", "share")
    assert not [n for n in allowed if n.startswith("mcp__") and any(v in n.rsplit("__", 1)[1] for v in verbs)]
    load = "import json,sys; json.load(open(sys.argv[1]))"
    assert subprocess.run(["python3", "-I", "-S", "-c", load, str(ROOT / "hooks/brief-settings.json")]).returncode == 0


# ------------------------------------------------------------------ D-117: the snapshot of the connector tools, every one allowed or denied
SNAPSHOT = ROOT / "hooks" / "brief_connector_tools.txt"
DENIED = ROOT / "hooks" / "brief_denied_tools.txt"


def rule_lines(path):
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and ln.lstrip()[0] != "#"]


def denied_by(name, rules):
    return name in rules or "__".join(name.split("__")[:2]) in rules


def test_the_connector_snapshot_is_dated_and_holds_the_real_names():
    head = SNAPSHOT.read_text().splitlines()[0]
    snap = rule_lines(SNAPSHOT)
    assert head.startswith("#") and "2026-10-22" in head and len(snap) == len(set(snap)) == 129
    assert all(n.startswith("mcp__") and n.count("__") == 2 for n in snap)
    assert {"mcp__claude_ai_Linear__extract_images", "mcp__claude_ai_Gmail__get_message", "mcp__claude_ai_Google_Calendar__suggest_time"} <= set(snap)
    assert set(names()) - {"ToolSearch"} <= set(snap)                                    # every allowed connector tool really exists


def test_every_connector_tool_in_the_snapshot_is_allowed_or_denied_by_name_or_by_whole_server():
    rules, allowed = set(rule_lines(DENIED)), set(names())
    assert [n for n in rule_lines(SNAPSHOT) if n not in allowed and not denied_by(n, rules)] == []
    assert not [n for n in rule_lines(SNAPSHOT) if n in allowed and denied_by(n, rules)]       # nothing allowed is also denied


def test_the_three_servers_the_brief_reads_deny_every_unneeded_tool_by_full_name_reads_included():
    rules, allowed = set(rule_lines(DENIED)), set(names())
    for server in ("Gmail", "Google_Calendar", "Linear"):
        every = [n for n in rule_lines(SNAPSHOT) if n.startswith(f"mcp__claude_ai_{server}__")]
        assert every and set(every) - allowed <= rules, server
        assert f"mcp__claude_ai_{server}" not in rules                      # a whole-server rule would hide the allowed reads
    for read in ("get_message", "list_drafts", "get_draft", "list_labels", "get_event", "list_calendars", "suggest_time", "extract_images"):
        assert any(r.endswith("__" + read) for r in rules), read


def test_the_deny_file_and_the_settings_deny_list_stay_equal_with_no_allow_or_default_mode():
    s = json.loads((ROOT / "hooks" / "brief-settings.json").read_text())
    builtin = [n for n in s["permissions"]["deny"] if not n.startswith("mcp__")]
    assert s["permissions"]["deny"] == builtin + rule_lines(DENIED) and "Bash" in builtin
    assert len(rule_lines(DENIED)) == len(set(rule_lines(DENIED))) and set(s["permissions"]) == {"deny"}


# ------------------------------------------------------------------ a list line is blank, a full-line comment, or exactly one name
INLINE = ["mcp__x__read # a note", "mcp__x__read#note", "mcp__x__read\t# note", "mcp__x__read mcp__x__other", "mcp__x__re#ad"]


@pytest.mark.parametrize("line", INLINE)
def test_the_guard_rejects_the_whole_allowlist_when_a_name_line_carries_a_comment_or_a_second_token(tmp_path, line):
    f = tmp_path / "a.txt"
    f.write_text(f"# header\nmcp__y__fine\n{line}\n")
    for name in ("mcp__y__fine", "mcp__x__read", "mcp__x__other"):
        code, out, _ = guard(call(name), allowlist=f)
        assert code == 0 and json.loads(out) == DENY, name                  # fail closed: a dead rule here would be a live rule there
    assert guard(call("mcp__y__fine"), "--hard", allowlist=f) == (2, "", "brief is read-only\n")


def test_the_guard_accepts_blank_lines_and_indented_full_line_comments(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("  # indented comment\n\n   \nmcp__y__fine  \n\t# tab comment\n")
    assert guard(call("mcp__y__fine"), allowlist=f) == (0, "", "")


BAD_BYTES = [b"\xef\xbb\xbfmcp__x__read\n", b"\xef\xbb\xbf# comment\n", "mcp__x__re ad\n".encode(), " \n".encode(), "mcp__x__é\n".encode(),
             b"mcp__x__re\rad\n", b"mcp__x__read\x00\n", b"mcp__x__a:b\n", b"\xff\xfe\n"]


@pytest.mark.parametrize("raw", BAD_BYTES)
def test_a_bom_or_any_byte_outside_the_name_alphabet_makes_the_guard_deny_everything(tmp_path, raw):
    f = tmp_path / "a.txt"
    f.write_bytes(b"mcp__y__fine\n" + raw)
    for name in ("mcp__y__fine", "mcp__x__read"):
        code, out, _ = guard(call(name), allowlist=f)
        assert code == 0 and json.loads(out) == DENY, name


def test_a_crlf_list_and_names_with_dots_and_dashes_are_accepted(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"# c\r\nmcp__y__fine-1.x\r\n\r\nToolSearch\r\n")
    assert guard(call("mcp__y__fine-1.x"), allowlist=f) == (0, "", "") and guard(call("ToolSearch"), allowlist=f) == (0, "", "")
