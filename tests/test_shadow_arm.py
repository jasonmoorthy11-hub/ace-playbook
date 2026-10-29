"""shadow_arm.py: the hook side of the sealed pre-arm-start conditions (1) and (7) (PREREGISTRATION.md, Pre-arm-start conditions).

The hook may not import `arms` (analysis code, hashed in `report_sha256`), so `shadow_arm.assign` is a copy; these tests pin it to the
sealed function on generated inputs and by source text, pin the two new row keys and the `error:delivered:<Type>` log lines, and check
that the seed is never written anywhere. Timing: test_hook_conformance.py only bounds single runs by FAST; the p95 test below reuses
its `hook` harness and `FAST` budget (no other test bounds the hook across many runs).
"""

import ast
import hashlib
import inspect
import json
import re
import textwrap
from types import SimpleNamespace

import pytest

from ace_playbook.compile import arms, arms_window, shadow, shadow_arm
from ace_playbook.compile import state as st

from . import compile_support as cs
from .test_compile_shadow import Runner
from .test_hook_conformance import FAST, hook

SEED = "ab" * 32
RM = "rm -rf /w/src"
LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ error:delivered:OSError$")


@pytest.fixture
def w(tmp_path):
    return cs.real_world(tmp_path)


@pytest.fixture
def run(w, monkeypatch, capsys):
    return Runner(w, monkeypatch, capsys)


def configure(w, **extra):
    """Rewrite the world's config file with `extra` keys (the seed and rate) and return the file's path."""
    conf = json.loads(open(w.conf).read())
    with open(w.conf, "w") as fh:
        json.dump({**conf, **extra}, fh)
    return w.conf


def armed(w):
    return configure(w, shadow_seed=SEED, arm_rate=0.5)


def log_lines(w):
    p = w.data / "shadow.log"
    return p.read_text().splitlines() if p.exists() else []


def delivered(w):
    return [ln for ln in log_lines(w) if ln[21:].startswith("error:delivered:")]


# ------------------------------------------------------------------ identity with the sealed arms.assign
def test_assign_equals_the_sealed_assign_on_generated_inputs():
    n = 0
    for i in range(30):
        seed = hashlib.sha256(f"seed-{i}".encode()).hexdigest()
        for rate in (0.01, 0.25, 0.5, 0.5, 0.9, 0.999):
            for j in range(4):
                sess = hashlib.sha256(f"s-{i}-{j}".encode()).hexdigest()
                for pid in ("", "P-00000001", f"P-{i:08d}"):
                    assert shadow_arm.assign(sess, seed, rate, pid) == arms.assign(sess, seed, rate, pid)
                    n += 1
    assert n >= 500
    assert shadow_arm.assign("s", SEED, 0.5) == arms.assign("s", SEED, 0.5)         # the default proposal id


def _body(fn) -> str:
    src = textwrap.dedent(inspect.getsource(fn))
    node = ast.parse(src).body[0]
    stmts = node.body[1:] if isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) \
        and isinstance(node.body[0].value.value, str) else node.body
    return "\n".join(src.splitlines()[stmts[0].lineno - 1:])


def test_the_two_assign_bodies_are_the_same_text_and_the_signatures_agree():
    assert _body(shadow_arm.assign) == _body(arms.assign) and "hmac.new" in _body(arms.assign)
    assert str(inspect.signature(shadow_arm.assign)) == str(inspect.signature(arms.assign))
    assert (shadow_arm.WARN, shadow_arm.OBSERVE) == (arms.WARN, arms.OBSERVE) == ("warn", "observe")


# ------------------------------------------------------------------ fields
def test_fields_assign_when_both_keys_are_set_and_mark_unassigned_otherwise():
    both = SimpleNamespace(shadow_seed=SEED, arm_rate=0.5)
    got = shadow_arm.fields(both, "sh", "P-00000001", {"session_id": "x"})
    assert got == {"arm": arms.assign("sh", SEED, 0.5, "P-00000001"), "agent_id_present": False}
    for cfg in (SimpleNamespace(shadow_seed=None, arm_rate=0.5), SimpleNamespace(shadow_seed=SEED, arm_rate=None),
                SimpleNamespace(shadow_seed=None, arm_rate=None)):
        assert shadow_arm.fields(cfg, "sh", "P-00000001", {}) == {"arm": "unassigned", "agent_id_present": False}


def test_agent_id_present_is_the_key_not_its_value_and_false_without_a_parsed_payload():
    cfg = SimpleNamespace(shadow_seed=None, arm_rate=None)
    assert shadow_arm.fields(cfg, "sh", "p", {"agent_id": ""})["agent_id_present"] is True
    assert shadow_arm.fields(cfg, "sh", "p", {"agent_id": None})["agent_id_present"] is True
    assert shadow_arm.fields(cfg, "sh", "p", {"session_id": "x"})["agent_id_present"] is False
    assert shadow_arm.fields(cfg, "sh", "p", None)["agent_id_present"] is False


# ------------------------------------------------------------------ the hook run
def test_a_hook_run_writes_the_assigned_arm_and_agent_id_present_false(w):
    armed(w)
    p, _ = hook(w, json.dumps(cs.pre_payload(RM)).encode())
    (row,) = cs.shadow_rows(w.data)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"")
    assert row["arm"] in ("warn", "observe") and row["agent_id_present"] is False
    assert row["arm"] == arms.assign(row["session"], SEED, 0.5, row["proposal_id"])


def test_a_payload_with_an_agent_id_key_sets_agent_id_present_true_whatever_its_value(w):
    armed(w)
    for i, val in enumerate(("x", "")):
        hook(w, json.dumps({**cs.pre_payload(RM, sid=f"sess-sub-{i}"), "agent_id": val}).encode())
    rows = cs.shadow_rows(w.data)
    assert [r["agent_id_present"] for r in rows] == [True, True]
    assert all(r["arm"] == arms.assign(r["session"], SEED, 0.5, r["proposal_id"]) for r in rows)


def test_without_the_seed_and_rate_the_row_says_unassigned(w):
    hook(w, json.dumps(cs.pre_payload(RM)).encode())
    configure(w, shadow_seed=SEED)                                          # a seed alone is not enough either
    hook(w, json.dumps(cs.pre_payload(RM, sid="sess-live-2")).encode())
    assert [(r["arm"], r["agent_id_present"]) for r in cs.shadow_rows(w.data)] == [("unassigned", False)] * 2


def test_an_oversize_row_and_a_truncated_row_carry_both_keys_too(w):
    armed(w)
    pad = json.dumps({**cs.pre_payload(RM), "pad": "a" * (shadow.MAX_STDIN + 10), "agent_id": "x"}).encode()
    hook(w, pad)
    (row,) = cs.shadow_rows(w.data)
    assert row["cause"] == "truncated" and row["agent_id_present"] is False             # never parsed: the flag cannot be known
    assert row["arm"] == arms.assign(row["session"], SEED, 0.5, row["proposal_id"])


def test_the_warn_share_of_2000_session_hashes_at_rate_half_is_near_half():
    cfg = SimpleNamespace(shadow_seed=SEED, arm_rate=0.5)
    arm = [shadow_arm.fields(cfg, st.session_hash(f"deterministic-{i}"), "P-00000001", {})["arm"] for i in range(2000)]
    assert set(arm) == {"warn", "observe"} and 0.45 <= arm.count("warn") / 2000 <= 0.55


# ------------------------------------------------------------------ the delivered path
def warn_world(w):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)
    return w.props["rm_rf_nonscratch"]["message"]


class Broken:
    def write(self, text):
        raise OSError("closed")


def test_a_failing_stderr_logs_one_line_and_the_stdout_decision_is_still_written(w, run, monkeypatch):
    message = warn_world(w)
    monkeypatch.setattr(shadow_arm.sys, "stderr", Broken())
    code, out, _ = run(cs.pre_payload(RM))
    assert code == 0 and len(run.rows) == 1
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == message
    (line,) = delivered(w)
    assert LOG_LINE.fullmatch(line) and line[20] == " " and len(line.split()[0]) == 20


def test_a_failing_stdout_logs_one_line_and_the_stderr_echo_is_still_written(w, run, monkeypatch):
    message = warn_world(w)
    monkeypatch.setattr(shadow_arm.sys, "stdout", Broken())
    code, _, err = run(cs.pre_payload(RM))
    assert code == 0 and len(run.rows) == 1 and err == message + "\n"
    (line,) = delivered(w)
    assert LOG_LINE.fullmatch(line)


def test_both_channels_failing_logs_two_lines_and_the_hook_still_exits_0(w, run, monkeypatch):
    warn_world(w)
    monkeypatch.setattr(shadow_arm.sys, "stderr", Broken())
    monkeypatch.setattr(shadow_arm.sys, "stdout", Broken())
    assert run(cs.pre_payload(RM))[0] == 0 and len(delivered(w)) == 2 and len(run.rows) == 1


def test_a_failing_block_decision_write_is_logged_and_the_exit_code_is_unchanged(w, run, monkeypatch):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "block", row=True)
    monkeypatch.setattr(shadow_arm.sys, "stdout", Broken())
    code, out, err = run(cs.pre_payload(RM))
    assert (code, out, err) == (0, "", "") and [r["mode"] for r in run.rows] == ["block"] and len(delivered(w)) == 1


def test_a_clean_delivery_logs_nothing(w, run):
    warn_world(w)
    code, out, err = run(cs.pre_payload(RM))
    assert code == 0 and out and err and delivered(w) == []


def test_arms_window_counts_the_delivered_line_inside_its_window_only(w, run, monkeypatch):
    warn_world(w)
    monkeypatch.setattr(shadow_arm.sys, "stderr", Broken())
    run(cs.pre_payload(RM))
    stamp = delivered(w)[0][:20]
    assert arms_window.delivered_errors(w.cfg, stamp, stamp) == 1                       # the end of the window is included
    assert arms_window.delivered_errors(w.cfg, "2000-01-01T00:00:00Z", "2000-01-02T00:00:00Z") == 0


# ------------------------------------------------------------------ secrecy and cost
def test_the_seed_never_reaches_a_row_the_log_stdout_or_stderr(w):
    armed(w)
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)
    p, _ = hook(w, json.dumps(cs.pre_payload(RM)).encode())
    assert p.stdout and p.stderr and cs.shadow_rows(w.data)
    seen = p.stdout + p.stderr + b"".join(f.read_bytes() for f in w.data.rglob("*") if f.is_file())
    assert SEED.encode() not in seen and bytes.fromhex(SEED) not in seen


def test_the_p95_of_50_hook_runs_stays_under_the_conformance_budget(w):
    armed(w)
    secs = []
    for i in range(50):
        p, took = hook(w, json.dumps(cs.pre_payload(RM, sid=f"sess-p95-{i}")).encode())
        assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"")
        secs.append(took)
    assert sorted(secs)[int(0.95 * 50) - 1] < FAST and len(cs.shadow_rows(w.data)) == 50
