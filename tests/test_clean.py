from ace_playbook.clean import clean_file, sample_turns, strip_noise
from ace_playbook.clean.transcript import Turn

from .jsonl import assistant, tool_result, tool_use, user, write


def test_strip_noise_removes_reminders_and_ace_block():
    text = ("hello <system-reminder>secret harness text</system-reminder> world "
            "=== ACE PLAYBOOK (advisory) ===\n- [L-aaaaaaaa] x\n=== END ACE PLAYBOOK ===")
    assert strip_noise(text) == "hello  world"


def test_drops_noise_sidechain_thinking_and_big_results(tmp_path):
    big = "x" * 5000
    recs = [
        user("<system-reminder>boot context</system-reminder>"),
        {"type": "summary", "summary": "old"},
        {"type": "attachment", "attachment": {"type": "hook_stderr", "content": "noise"}},
        user("fix the build please"),
        user("meta line", isMeta=True),
        assistant({"type": "thinking", "thinking": "private"}, "Looking at it",
                  tool_use("t1", "Bash", command="npm test"), isSidechain=False),
        tool_result("t1", big),
        assistant("subagent chatter", isSidechain=True),
        tool_result("t1", "short ok"),
        "not json at all",
    ]
    c = clean_file(write(tmp_path / "s.jsonl", recs), result_max=2000)
    text = c.body
    assert "boot context" not in text and "private" not in text and "subagent chatter" not in text
    assert "fix the build please" in text and "Looking at it" in text
    assert "T: Bash: npm test" in text and "[result omitted: 5000 chars]" in text
    assert "short ok" in text and "xxxxx" not in text
    assert "meta line" not in text


def test_outcome_signals(tmp_path):
    recs = [
        user("run the tests"),
        assistant(tool_use("a", "Bash", command="pytest -x")),
        tool_result("a", "Exit code 1\nFAILED", is_error=True),
        assistant(tool_use("b", "Bash", command="pytest   -x")),
        tool_result("b", "Exit code 1\nFAILED", is_error=True),
        user("No, that's wrong. I said use uv"),
        user("don't touch the lockfile"),
        user("thanks, looks fine"),
        assistant(tool_use("c", "Bash", command="rm -rf build")),
        tool_result("c", "PreToolUse:Bash hook blocking error: blocked destructive command", True),
        {"type": "attachment", "attachment": {"type": "hook_blocking_error", "content": "x"}},
    ]
    c = clean_file(write(tmp_path / "s.jsonl", recs))
    s = c.signals
    assert s.user_corrections == 2
    assert s.hook_blocks == 2
    assert s.repeated_failing_commands == [("pytest -x", 2)]
    assert "user_corrections: 2" in c.header and "2x 'pytest -x'" in c.header


def test_session_id_and_cwd_from_records_and_fallback(tmp_path):
    c = clean_file(write(tmp_path / "abc.jsonl", [user("hi there")]))
    assert c.session_id == "sess-1" and c.cwd == "/work/demo"
    rec = {"type": "user", "message": {"role": "user", "content": "x y z"}}
    assert clean_file(write(tmp_path / "xyz.jsonl", [rec])).session_id == "xyz"


def test_provenance_origins(tmp_path):
    recs = [
        user("always use tabs in Makefiles"),
        assistant(tool_use("r1", "Read", file_path="/work/demo/notes.md")),
        tool_result("r1", "project note: deploy only on Fridays"),
        assistant(tool_use("w1", "Write", file_path="/work/demo/gen.md")),
        assistant(tool_use("r2", "Read", file_path="/work/demo/gen.md")),
        tool_result("r2", "agent wrote this itself"),
        assistant(tool_use("r3", "Read", file_path="/work/demo/upstream/lib/README.md")),
        tool_result("r3", "third party readme text"),
        assistant(tool_use("r4", "Read", file_path="/etc/hosts")),
        tool_result("r4", "outside project file"),
        assistant(tool_use("f1", "WebFetch", url="https://example.com")),
        tool_result("f1", "fetched page body"),
    ]
    c = clean_file(write(tmp_path / "s.jsonl", recs))
    origin = {t.text: t.origin for t in c.all_turns}
    assert origin["always use tabs in Makefiles"] == "user_message"
    assert origin["project note: deploy only on Fridays"] == "user_file"
    assert origin["agent wrote this itself"] == "tool_output"
    assert origin["third party readme text"] == "tool_output"
    assert origin["outside project file"] == "tool_output"
    assert origin["fetched page body"] == "tool_output"


def test_head_tail_sampling():
    turns = [Turn("user", f"line {i} " + "y" * 90, "user_message") for i in range(2000)]
    text, kept = sample_turns(turns, head=2500, tail=7500)
    assert text.startswith("U: line 0 ") and text.rstrip().endswith("y" * 90)
    assert "line 1999" in text and "omitted" in text
    assert len(text) < 2500 + 7500 + 200
    assert kept[0] is turns[0] and kept[-1] is turns[-1] and len(kept) < len(turns)
    short, kept2 = sample_turns(turns[:5])
    assert "omitted" not in short and len(kept2) == 5


def test_default_sampling_budget_is_25k_plus_75k(tmp_path):
    recs = [user(f"turn {i} " + "z" * 900) for i in range(400)]
    c = clean_file(write(tmp_path / "big.jsonl", recs))
    assert 90_000 < len(c.body) <= 100_300
    assert "turn 0 " in c.body and "turn 399 " in c.body
