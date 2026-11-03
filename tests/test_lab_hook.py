"""`lab/hooks/stop-mid-task-audit.sh`: the simplified analogue of the operator's audit hook, driven with JSON on stdin."""

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "ace_playbook" / "lab" / "hooks" / "stop-mid-task-audit.sh"
AUDIT = "Pre-claim audit:\n- committed: a\n- actually did: b\n- gaps: none"


def run(payload, raw=None):
    data = raw if raw is not None else json.dumps(payload)
    return subprocess.run([str(HOOK)], input=data, capture_output=True, text=True, timeout=30)


def test_the_script_is_executable_and_short():
    assert os.access(HOOK, os.X_OK) and len(HOOK.read_text().splitlines()) <= 40


def test_stop_hook_active_exits_0_even_with_claims():
    p = run({"stop_hook_active": True, "last_assistant_message": "Done. It shipped and looks good."})
    assert (p.returncode, p.stderr) == (0, "")


def test_two_claim_words_without_an_audit_block_with_exit_2_and_a_neutral_stderr():
    p = run({"stop_hook_active": False, "last_assistant_message": "All done, the fix is shipped."})
    assert p.returncode == 2 and p.stdout == "" and "committed" in p.stderr and "gaps" in p.stderr
    assert "actually did" in p.stderr and len(p.stderr) < 400


def test_an_audit_block_lets_it_through_case_insensitively():
    for audit in (AUDIT, AUDIT.lower(), AUDIT.upper()):
        p = run({"last_assistant_message": f"Done, shipped.\n{audit}"})
        assert (p.returncode, p.stderr) == (0, "")


def test_one_claim_word_exits_0():
    assert run({"last_assistant_message": "That is done."}).returncode == 0


@pytest.mark.parametrize("text,code", [("Looks GOOD and COMPLETED", 2), ("order 12 is complete; order 13 complete", 0),
                                       ("order 12 is complete and done", 2), ("ready to commit, cycle close", 2),
                                       ("nothing relevant here", 0), ("", 0)])
def test_the_claim_pattern(text, code):
    assert run({"last_assistant_message": text}).returncode == code


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"last_assistant_message": 5}', "{}", "\xff"])
def test_unusable_input_fails_open(raw):
    p = run(None, raw=raw)
    assert (p.returncode, p.stdout) == (0, "")
