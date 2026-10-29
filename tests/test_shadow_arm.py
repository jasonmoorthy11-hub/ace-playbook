"""shadow_arm.py: the hook side of the sealed pre-arm-start conditions (1) and (7) (PREREGISTRATION.md, Pre-arm-start conditions).

The hook may not import `arms` (analysis code, hashed in `report_sha256`), so `shadow_arm.assign` is a copy; these tests pin it to the
sealed function on generated inputs and by source text, pin the two new row keys and the `error:delivered:<Type>` log lines, and check
that the seed is never written anywhere. The pipe tests run the real hook with a closed reader; the phase tests (delivery by arm) come last.
Timing: test_hook_conformance.py only bounds single runs by FAST; the p95 test below reuses its `hook` harness and `FAST` budget (no other
test bounds the hook across many runs).
"""

import ast
import hashlib
import inspect
import json
import os
import re
import subprocess
import textwrap
from types import SimpleNamespace

import pytest

from ace_playbook.compile import arms, arms_window, shadow, shadow_arm
from ace_playbook.compile import state as st

from . import compile_support as cs
from .test_compile_shadow import Runner
from .test_hook_conformance import FAST, ROOT, env_of, hook

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
    """The bound is FAST, a ceiling for slow CI machines (about 40 ms is typical): this catches a hang or a gross regression, not a small one."""
    armed(w)
    secs = []
    for i in range(50):
        p, took = hook(w, json.dumps(cs.pre_payload(RM, sid=f"sess-p95-{i}")).encode())
        assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"")
        secs.append(took)
    assert sorted(secs)[47] < FAST and len(cs.shadow_rows(w.data)) == 50              # index 47 of 50 is about the 95th percentile


# ------------------------------------------------------------------ a real pipe whose reader is gone (stdout is block-buffered on a pipe)
def closed_reader_run(w, which: str, payload: dict):
    """Run hooks/shadow.sh with `which` (stdout or stderr) on a pipe whose read end is closed and the other on a capture pipe."""
    rd, wr = os.pipe()
    os.close(rd)
    kw = {"stdout": wr, "stderr": subprocess.PIPE} if which == "stdout" else {"stdout": subprocess.PIPE, "stderr": wr}
    try:
        return subprocess.run([str(ROOT / "hooks" / "shadow.sh"), "pre"], input=json.dumps(payload).encode(), env=env_of(w), timeout=60, **kw)
    finally:
        os.close(wr)


@pytest.mark.parametrize("which", ["stdout", "stderr"])
def test_a_channel_whose_pipe_reader_is_closed_logs_exactly_one_line_writes_the_row_and_exits_0(w, which):
    message = warn_world(w)
    p = closed_reader_run(w, which, cs.pre_payload(RM))
    (line,) = delivered(w)
    assert p.returncode == 0 and len(cs.shadow_rows(w.data)) == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ error:delivered:BrokenPipeError", line)
    if which == "stdout":
        assert p.stderr == (message + "\n").encode()                        # the echo arrived; nothing from the exit flush ("Exception ignored")
    else:
        assert json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"] == message


def test_a_block_decision_on_a_closed_stdout_pipe_is_logged_and_exits_0(w):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "block", row=True)
    p = closed_reader_run(w, "stdout", cs.pre_payload(RM))
    assert p.returncode == 0 and p.stderr == b"" and len(delivered(w)) == 1 and [r["mode"] for r in cs.shadow_rows(w.data)] == ["block"]


# ------------------------------------------------------------------ the phase: an observe-arm session hears nothing
STARTED = "2026-10-01T00:00:00Z"
DET = "rm_rf_nonscratch"


def sid_in(arm: str, pid: str) -> str:
    """A session id whose hash lands in `arm` for this proposal at rate 0.5 under SEED."""
    return next(f"sess-ph-{i}" for i in range(1000) if arms.assign(st.session_hash(f"sess-ph-{i}"), SEED, 0.5, pid) == arm)


def phase_marker(w, body=None):
    (w.data / "arm_phase.json").write_text(json.dumps({DET: STARTED} if body is None else body))


def phased(w, mode="warn"):
    armed(w)
    cs.approve_file(w.cfg, w.props[DET], mode, row=True)
    return w.props[DET]["message"], w.props[DET]["id"]


def test_in_a_phase_the_observe_arm_session_is_silent_and_the_warn_arm_session_hears_the_warn(w, run):
    message, pid = phased(w)
    phase_marker(w)
    code, out, err = run(cs.pre_payload(RM, sid=sid_in("observe", pid)))
    assert (code, out, err) == (0, "", "")
    (row,) = run.rows
    assert (row["mode"], row["arm"], row["would_fire"]) == ("warn", "observe", True)
    code, out, err = run(cs.pre_payload(RM, sid=sid_in("warn", pid)))
    assert code == 0 and err == message + "\n" and json.loads(out)["hookSpecificOutput"]["additionalContext"] == message
    assert [r["arm"] for r in run.rows] == ["observe", "warn"] and delivered(w) == []


def test_in_a_phase_a_block_mode_proposal_denies_the_warn_arm_and_nothing_the_observe_arm(w, run):
    _, pid = phased(w, "block")
    phase_marker(w)
    assert run(cs.pre_payload(RM, sid=sid_in("observe", pid))) == (0, "", "")
    code, out, _ = run(cs.pre_payload(RM, sid=sid_in("warn", pid)))
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert [(r["mode"], r["arm"]) for r in run.rows] == [("block", "observe"), ("block", "warn")]


@pytest.mark.parametrize("marker", [None, {DET: "2099-01-01T00:00:00Z"}, {"user_correction": STARTED}, "[]", "{"])
def test_without_a_usable_started_marker_for_the_detector_both_arms_deliver(w, run, marker):
    message, pid = phased(w)
    if marker is not None:
        (w.data / "arm_phase.json").write_text(marker if isinstance(marker, str) else json.dumps(marker))
    for arm in ("observe", "warn"):
        code, out, err = run(cs.pre_payload(RM, sid=sid_in(arm, pid)))
        assert code == 0 and err == message + "\n" and out


def test_an_unreadable_marker_a_directory_delivers_in_both_arms_and_the_hook_stays_within_budget(w):
    message, pid = phased(w)
    (w.data / "arm_phase.json").mkdir()
    for arm in ("observe", "warn"):
        p, took = hook(w, json.dumps(cs.pre_payload(RM, sid=sid_in(arm, pid))).encode())
        assert p.returncode == 0 and p.stderr == (message + "\n").encode() and p.stdout and took < FAST


def test_the_real_hook_is_silent_for_the_observe_arm_in_a_phase(w):
    _, pid = phased(w)
    phase_marker(w)
    p, _ = hook(w, json.dumps(cs.pre_payload(RM, sid=sid_in("observe", pid))).encode())
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"") and [r["arm"] for r in cs.shadow_rows(w.data)] == ["observe"]


def test_an_unassigned_row_in_a_phase_delivers_and_logs_arm_unassigned_once_without_an_error_prefix(w, run):
    message = warn_world(w)                                      # no seed or rate: every row is unassigned
    phase_marker(w)
    for i in range(3):
        code, out, err = run(cs.pre_payload(RM, sid=f"sess-un-{i}"))
        assert code == 0 and err == message + "\n" and out
    lines = [ln[21:] for ln in log_lines(w)]
    assert lines.count("arm:unassigned") == 1 and not any(ln.startswith("error:") for ln in lines)
    assert [r["arm"] for r in run.rows] == ["unassigned"] * 3


def test_no_arm_unassigned_line_outside_a_phase(w, run):
    warn_world(w)
    run(cs.pre_payload(RM))
    assert "arm:unassigned" not in "\n".join(log_lines(w))


def test_audible_delivers_everything_when_anything_goes_wrong(w):
    cfg = SimpleNamespace(data_dir=w.data, shadow_seed=SEED, arm_rate=0.5)
    hits = [({"id": "P-00000001"}, "warn", "detector")]                   # no "check" key: the filter raises, the hits stand
    phase_marker(w)
    assert shadow_arm.audible(cfg, hits, "sh", lambda msg: None, "2026-10-19T00:00:00Z") == hits
    assert shadow_arm.audible(cfg, [], "sh", lambda msg: None) == []
