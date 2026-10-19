"""shadow.py (docs/design/compiler.md 7): payload handling, approved-proposal loading, rows, modes, state, budget."""

import dataclasses
import fcntl
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

from ace_playbook.compile import catalogue, evidence, shadow
from ace_playbook.compile import state as st
from ace_playbook.compile.detectors import Fire

from . import compile_support as cs

RM = "rm -rf /w/src"
ROW_KEYS = {"ts", "proposal_id", "lesson_id", "mode", "event", "tool_name", "session", "payload_sha256",
            "would_fire", "cause", "tier"}


class Runner:
    """Calls shadow.main in-process with a payload on a fake stdin; returns (exit code, stdout, stderr)."""

    def __init__(self, w, monkeypatch, capsys):
        self.w, self.mp, self.capsys = w, monkeypatch, capsys
        monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", w.conf)

    def __call__(self, payload=None, *, raw=None, arg="pre"):
        data = raw if raw is not None else json.dumps(payload).encode()
        self.mp.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
        code = shadow.main([arg] if arg is not None else [])
        out = self.capsys.readouterr()
        return code, out.out, out.err

    @property
    def rows(self):
        return cs.shadow_rows(self.w.data)

    @property
    def log(self):
        p = self.w.data / "shadow.log"
        return p.read_text() if p.exists() else ""


@pytest.fixture
def w(tmp_path):
    return cs.real_world(tmp_path)


@pytest.fixture
def run(w, monkeypatch, capsys):
    return Runner(w, monkeypatch, capsys)


# ------------------------------------------------------------------ rows
def test_a_matching_rm_in_observe_mode_writes_one_row_and_prints_nothing(w, run):
    code, out, err = run(cs.pre_payload(RM))
    (row,) = run.rows
    assert (code, out, err) == (0, "", "")
    assert set(row) == ROW_KEYS
    pid = w.props["rm_rf_nonscratch"]["id"]
    assert (row["proposal_id"], row["lesson_id"], row["mode"], row["event"]) == (pid, cs.RM_LESSON, "observe", "PreToolUse")
    assert (row["tool_name"], row["would_fire"], row["cause"], row["tier"]) == ("Bash", True, "detector", "live")
    assert row["session"] == st.session_hash("sess-live-1") and len(row["payload_sha256"]) == 64
    assert row["payload_sha256"] == evidence.payload_hash({"tool_name": "Bash", "tool_input": {"command": RM}})
    assert oct((w.data / "shadow.jsonl").stat().st_mode & 0o777) == "0o600"


def test_a_scratch_rm_writes_no_row(w, run):
    assert run(cs.pre_payload("rm -rf /tmp/scratch/x"))[0] == 0
    assert run.rows == [] and not (w.data / "shadow.jsonl").exists()


def test_rows_never_hold_the_command_text_or_a_session_id(w, run):
    run(cs.pre_payload("rm -rf /w/src/unique-marker-1234"))
    text = (w.data / "shadow.jsonl").read_text()
    assert "unique-marker" not in text and "rm -rf" not in text and "/w/src" not in text and "sess-live-1" not in text


def test_the_same_payload_twice_appends_two_rows(w, run):
    run(cs.pre_payload(RM))
    first = (w.data / "shadow.jsonl").read_text()
    run(cs.pre_payload(RM))
    assert (w.data / "shadow.jsonl").read_text().startswith(first) and len(run.rows) == 2
    assert run.rows[0]["payload_sha256"] == run.rows[1]["payload_sha256"]


def test_a_truncated_command_fires_when_the_proposal_says_on_truncated_fire(w, run):
    assert w.props["rm_rf_nonscratch"]["check"]["on_truncated"] == "fire"
    run(cs.pre_payload("echo " + "a" * 70000))
    assert len(run.rows) == 1 and run.rows[0]["cause"] == "truncated"
    run(cs.pre_payload(RM + " # " + "a" * 70000))                  # cut AND the detector fires on the kept prefix
    assert [r["cause"] for r in run.rows] == ["truncated", "detector"]


def test_each_proposal_gets_its_own_row_and_on_truncated_ignore_is_honoured(tmp_path, monkeypatch, capsys):
    w2 = cs.real_world(tmp_path, ("rm_rf_nonscratch", "rm_rf"))
    r = Runner(w2, monkeypatch, capsys)
    r(cs.pre_payload("rm -rf /tmp/scratch/x"))                     # only the weak detector fires
    (row,) = r.rows
    assert row["proposal_id"] == w2.props["rm_rf"]["id"]
    r(cs.pre_payload("echo " + "a" * 70000))                       # truncated: fire-proposal only
    assert [x["proposal_id"] for x in r.rows[1:]] == [w2.props["rm_rf_nonscratch"]["id"]]
    r(cs.pre_payload(RM))
    assert len(r.rows) == 4                                       # both detectors fire on a real rm -rf


def test_the_hook_loads_proposals_without_the_scrub_engine(w, run, monkeypatch):
    import ace_playbook.compile.propscrub as ps
    monkeypatch.setattr(ps, "error", lambda *a: pytest.fail("the hot path must not rerun the scrub step"))
    run(cs.pre_payload(RM))
    assert len(run.rows) == 1
    p = w.props["rm_rf_nonscratch"]
    assert [d for _, ok, d in proposal_validate(w, p, hot=True).results][-2] == "hash-bound, not rerun"


def proposal_validate(w, p, **kw):
    from ace_playbook.compile import proposal
    return proposal.validate(p, w.cfg, **kw)


# ------------------------------------------------------------------ payload handling
@pytest.mark.parametrize("raw", [b"", b"not json", b"\xff\xfe", b"[1,2]", b'"x"', b"5", b"null", b"[" * 100000,
                                 b'{"hook_event_name":"PreToolUse"}',
                                 json.dumps({"session_id": "", "tool_name": "Bash", "tool_input": {"command": RM}}).encode(),
                                 json.dumps({"session_id": 7, "tool_name": "Bash", "tool_input": {"command": RM}}).encode(),
                                 json.dumps({"session_id": "s", "tool_input": {"command": RM}}).encode(),
                                 json.dumps({"session_id": "s", "tool_name": "Bash", "tool_input": "rm -rf /w/x"}).encode()])
def test_unparseable_or_incomplete_payloads_exit_silently(w, run, raw):
    assert run(raw=raw) == (0, "", "")
    assert run.rows == []


def test_an_oversize_payload_is_skipped_and_logged(w, run):
    big = json.dumps(cs.pre_payload(RM + " # " + "a" * 270000)).encode()
    assert len(big) > shadow.MAX_STDIN
    assert run(raw=big) == (0, "", "")
    assert run.rows == [] and "skipped:oversize" in run.log


@pytest.mark.parametrize("arg", [None, "", "bogus", "PRE"])
def test_a_missing_or_unknown_argument_exits_0_silently(w, run, arg):
    assert run(cs.pre_payload(RM), arg=arg) == (0, "", "")
    assert run.rows == []


def test_the_argument_decides_the_event_and_a_stop_payload_is_not_a_bash_call(w, run):
    assert run({"hook_event_name": "Stop", "session_id": "s"}, arg="stop") == (0, "", "")
    assert run.rows == []


def test_the_kill_switch_file_silences_the_hook(w, run):
    (w.data / "proposals" / "DISABLED").write_text("")
    assert run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows == []


def test_shell_metacharacters_in_the_command_are_data(w, run):
    marker = w.data.parent / "pwned"
    code, out, err = run(cs.pre_payload(f"rm -rf /w/$(touch {marker}) `touch {marker}`"))
    assert (code, out, err) == (0, "", "") and not marker.exists() and len(run.rows) == 1


# ------------------------------------------------------------------ approved proposals
def test_no_approved_directory_or_an_empty_one_is_a_quiet_no_op(tmp_path, run, w):
    approved = w.data / "proposals" / "approved"
    for f in approved.iterdir():
        f.unlink()
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == []
    approved.rmdir()
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == []


def test_an_invalid_file_is_skipped_and_logged_by_id_only(w, run):
    bad = w.data / "proposals" / "approved" / "P-deadbeef.json"
    bad.write_text('{"message": "SECRET-TEXT-IN-A-BAD-FILE"}')
    (w.data / "proposals" / "approved" / "notes.json").write_text("{}")
    run(cs.pre_payload(RM))
    assert len(run.rows) == 1                                     # the good proposal still runs
    assert "P-deadbeef" in run.log and "SECRET-TEXT" not in run.log


def test_a_symlinked_approved_directory_is_refused(w, run, tmp_path):
    real = w.data / "proposals" / "approved"
    moved = tmp_path / "elsewhere"
    real.rename(moved)
    real.symlink_to(moved)
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == []
    assert "approved_dir_symlink" in run.log


def test_a_symlinked_approved_file_is_ignored(w, run, tmp_path):
    f = next((w.data / "proposals" / "approved").iterdir())
    target = tmp_path / "copy.json"
    target.write_text(f.read_text())
    f.unlink()
    f.symlink_to(target)
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == []


def edit_approved(w, fn, rehash=False):
    f = next((w.data / "proposals" / "approved").iterdir())
    rec = json.loads(f.read_text())
    fn(rec)
    if rehash:
        rec["proposal_sha256"] = shadow.record_sha256(rec)
    f.write_text(json.dumps(rec))
    return f


def test_a_proposal_edited_after_approval_is_rejected(w, run):
    edit_approved(w, lambda r: r.update(message=r["message"] + " Also be careful."))
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == []
    assert w.props["rm_rf_nonscratch"]["id"] in run.log and "invalid" in run.log


def test_validate_runs_at_load_even_when_the_edit_carries_a_fresh_hash(w, run):
    edit_approved(w, lambda r: r.update(message="ACE %s: see https://example.com now" % r["id"]), rehash=True)
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == []
    assert "invalid:message" in run.log


def test_a_mode_edited_to_block_in_the_file_is_rejected(w, run):
    edit_approved(w, lambda r: r.update(mode="block"))
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == []


def test_a_mode_outside_the_ladder_is_skipped_as_disabled(w, run, monkeypatch):
    monkeypatch.setattr(shadow, "ALLOWED_MODES", ("observe",))
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "block", row=True)
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows == [] and "mode_disabled" in run.log


# ------------------------------------------------------------------ warn and block (hook rules: test_compile_approvals.py)
def test_warn_prints_the_stored_message_on_stderr_and_as_additional_context(w, run):
    p = w.props["rm_rf_nonscratch"]
    cs.approve_file(w.cfg, p, "warn", row=True)
    code, out, err = run(cs.pre_payload(RM + "/unique-marker-1234"))
    assert code == 0 and err == p["message"] + "\n"
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == p["message"]
    assert "unique-marker" not in err + out and run.rows[0]["mode"] == "warn"


def test_block_emits_the_deny_json_on_stdout_and_nothing_on_stderr(w, run):
    p = w.props["rm_rf_nonscratch"]
    cs.approve_file(w.cfg, p, "block", row=True)
    code, out, err = run(cs.pre_payload(RM))
    assert code == 0 and err == ""
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                      "permissionDecisionReason": p["message"]}}
    assert run.rows[0]["mode"] == "block"
    assert run(cs.pre_payload("rm -rf /tmp/scratch/x"))[1] == ""          # no fire, no decision


def fake_stop_block(monkeypatch, mode="block"):
    p = {"id": "P-00000001", "lesson_id": "L-00000001", "event": "Stop", "matcher": "*", "message": "ACE P-00000001: stop here",
         "check": {"detector": "rm_rf_nonscratch", "on_truncated": "ignore"}}
    monkeypatch.setattr(shadow, "load_approved", lambda cfg, log: [(p, mode)])
    monkeypatch.setattr(shadow.catalogue, "evaluate",
                        lambda ev, s, names=None, strict=False: ([Fire("rm_rf_nonscratch")], s or {}, []))
    return p


def test_a_stop_block_is_json_and_is_never_emitted_while_stop_hook_active(w, run, monkeypatch):
    p = fake_stop_block(monkeypatch)
    code, out, _ = run({"hook_event_name": "Stop", "session_id": "s"}, arg="stop")
    assert code == 0 and json.loads(out) == {"decision": "block", "reason": p["message"]}
    code, out, err = run({"hook_event_name": "Stop", "session_id": "s", "stop_hook_active": True}, arg="stop")
    assert (code, out, err) == (0, "", "") and len(run.rows) == 2        # the fire is still recorded


# ------------------------------------------------------------------ errors fail open
def test_an_exception_in_evaluation_logs_the_type_only(w, run, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret detail /private/path")

    monkeypatch.setattr(shadow, "_evaluate", boom)
    assert run(cs.pre_payload(RM)) == (0, "", "")
    assert "RuntimeError" in run.log and "secret" not in run.log and "/private" not in run.log
    assert len(run.log.splitlines()) == 1


def test_a_detector_error_is_logged_and_the_hook_stays_silent(w, run, monkeypatch):
    row = catalogue.CATALOGUE["rm_rf_nonscratch"]

    def bad(event, state):
        if event.session_id == "replay":                          # validation replays must still pass
            return row.fire(event, state)
        raise ValueError("boom")

    monkeypatch.setitem(catalogue.CATALOGUE, "rm_rf_nonscratch", dataclasses.replace(row, fire=bad))
    assert run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows == [] and "error:rm_rf_nonscratch:ValueError" in run.log


def test_a_broken_config_exits_0_without_writing(w, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(RM)).encode())))
    assert shadow.main(["pre"]) == 0 and capsys.readouterr() == ("", "")
    assert cs.shadow_rows(w.data) == []


def test_the_log_is_0600_and_capped(w, run):
    run(raw=b"x" * (shadow.MAX_STDIN + 1))
    log = w.data / "shadow.log"
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    log.write_text("x" * shadow.LOG_CAP)
    run(raw=b"x" * (shadow.MAX_STDIN + 1))
    assert log.stat().st_size == shadow.LOG_CAP


# ------------------------------------------------------------------ state shared with step 1
def test_shadow_updates_the_same_state_file_as_step_one(w, run):
    run(cs.pre_payload(RM, sid="sess-state"))
    run(cs.pre_payload("ls", sid="sess-state"))
    assert st.state_path(w.data, "sess-state").exists()
    s = st.load(w.data, "sess-state")
    assert s["events"] == 2 and s["session_hash"] == st.session_hash("sess-state") and s["truncated"] is False
    assert [p.name for p in (w.data / "shadow_state").glob("*.json")] == [st.session_hash("sess-state") + ".json"]


def test_a_locked_state_file_is_skipped_but_a_stateless_detector_still_records(w, run):
    p = st.state_path(w.data, "sess-lock")
    p.parent.mkdir(mode=0o700, parents=True)
    fd = os.open(str(p) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = time.perf_counter()
        assert run(cs.pre_payload(RM, sid="sess-lock")) == (0, "", "")
        assert time.perf_counter() - t0 < 0.5
    finally:
        os.close(fd)
    assert len(run.rows) == 1 and "skipped:locked" in run.log
    assert not p.exists()


def test_a_symlinked_state_directory_does_not_stop_the_row(w, run, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (w.data / "shadow_state").symlink_to(tmp_path / "elsewhere")
    assert run(cs.pre_payload(RM)) == (0, "", "")
    assert len(run.rows) == 1 and "skipped:error" in run.log and list((tmp_path / "elsewhere").iterdir()) == []


# ------------------------------------------------------------------ budget
def test_200_bash_events_through_one_observe_proposal_take_under_two_seconds(w, run):
    payloads = [cs.pre_payload(RM if i % 4 == 0 else f"ls -la /w/dir{i}", sid=f"sess-b{i % 10}") for i in range(200)]
    t0 = time.perf_counter()
    for p in payloads:
        run(p)
    elapsed = time.perf_counter() - t0
    assert len(run.rows) == 50
    assert elapsed < 2.0, f"200 calls took {elapsed:.2f}s ({elapsed / 200 * 1000:.1f} ms per call)"
    print(f"shadow budget: 200 calls in {elapsed:.3f}s, mean {elapsed / 200 * 1000:.2f} ms per call")


# ------------------------------------------------------------------ module entry
def test_python_dash_m_ace_playbook_compile_shadow_works_like_the_wrapper(w):
    import subprocess
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "ACE_PLAYBOOK_CONFIG": w.conf}
    for payload in (cs.pre_payload(RM), cs.pre_payload("ls")):
        p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.shadow", "pre"], input=json.dumps(payload),
                           capture_output=True, text=True, cwd=root, env=env, timeout=30)
        assert (p.returncode, p.stdout) == (0, "")
    assert len(cs.shadow_rows(w.data)) == 1


def test_a_broken_demotions_chain_is_logged_once_not_on_every_call(w, run):
    (w.data / "demotions.jsonl").write_text("not json\n")
    for _ in range(5):
        run(cs.pre_payload(RM))
    assert run.log.count("demotions:chain_broken") == 1 and len(run.rows) == 5
