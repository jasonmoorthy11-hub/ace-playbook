"""approvals.jsonl (docs/design/compiler.md 7.3): the hash chain, verify_copy, and what the hook does with it."""

import json
import os
import stat

import pytest

from ace_playbook.canon import GENESIS, record_hash
from ace_playbook.compile import approvals, shadow
from ace_playbook.compile.detectors import Fire

from . import compile_support as cs
from .test_compile_shadow import RM, Runner

SHA, OTHER = "a" * 64, "b" * 64


def fields(pid="P-00000001", sha=SHA, mode="observe", **kw):
    return {"proposal_id": pid, "proposal_sha256": sha, "mode": mode, **kw}


# ------------------------------------------------------------------ the chain
def test_append_writes_a_chained_0600_row_with_every_key(tmp_path):
    r1 = approvals.append(tmp_path, fields(ts="2026-10-01T00:00:00Z"))
    r2 = approvals.append(tmp_path, fields(sha=OTHER, mode="warn", tp_refs=["0123abcd"], fp_refs=[], review_sha="abc1234"))
    assert set(r1) == approvals.KEYS and r1["prev_hash"] == GENESIS and r1["hash"] == record_hash(r1)
    assert r2["prev_hash"] == r1["hash"] and r2["tp_refs"] == ["0123abcd"] and r2["review_sha"] == "abc1234"
    assert (r1["v"], r1["reviewed_by"], r1["tp_refs"], r1["fp_refs"], r1["review_sha"]) == (1, "operator-tty", [], [], "-")
    assert stat.S_IMODE(os.stat(tmp_path / "approvals.jsonl").st_mode) == 0o600
    assert approvals.read_chain(tmp_path) == ([r1, r2], None)


def test_a_missing_file_is_an_empty_chain(tmp_path):
    assert approvals.read_chain(tmp_path) == ([], None)


def test_a_tampered_row_stops_the_chain_at_that_row_and_refuses_an_append(tmp_path):
    for sha in (SHA, OTHER, "c" * 64):
        approvals.append(tmp_path, fields(sha=sha, mode="warn"))
    path = tmp_path / "approvals.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[1])
    row["mode"] = "block"                                          # hash no longer matches
    lines[1] = json.dumps(row, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    rows, broken = approvals.read_chain(tmp_path)
    assert broken == 2 and len(rows) == 1
    with pytest.raises(ValueError, match="chain broken at row 2"):
        approvals.append(tmp_path, fields(sha="d" * 64))
    assert path.read_text().count("\n") == 3                       # nothing was appended


@pytest.mark.parametrize("junk", [b"not json\n", b"[1]\n", b"\xff\xfe\n", b'{"a":1}\n', b"\n"])
def test_a_malformed_line_breaks_the_chain_at_that_line(tmp_path, junk):
    approvals.append(tmp_path, fields())
    with open(tmp_path / "approvals.jsonl", "ab") as fh:
        fh.write(junk)
    rows, broken = approvals.read_chain(tmp_path)
    assert len(rows) == 1 and broken == 2


def test_a_row_with_a_wrong_type_even_with_a_valid_hash_is_broken(tmp_path):
    row = approvals.append(tmp_path, fields())
    bad = {**row, "tp_refs": "not-a-list", "prev_hash": row["hash"]}
    bad["hash"] = record_hash(bad)
    with open(tmp_path / "approvals.jsonl", "a") as fh:
        fh.write(json.dumps(bad, sort_keys=True) + "\n")
    assert approvals.read_chain(tmp_path)[1] == 2


def test_the_same_proposal_hash_and_mode_twice_appends_once(tmp_path):
    first = approvals.append(tmp_path, fields())
    assert approvals.append(tmp_path, fields()) == first
    assert approvals.append(tmp_path, fields(pid="P-00000002"))["prev_hash"] == first["hash"]
    approvals.append(tmp_path, fields(mode="warn"))
    assert len(approvals.read_chain(tmp_path)[0]) == 3
    assert approvals.append(tmp_path, fields(mode="observe"))["mode"] == "observe"     # not the newest: a new row
    assert len(approvals.read_chain(tmp_path)[0]) == 4


def test_bad_fields_are_refused_before_anything_is_written(tmp_path):
    for bad in (fields(pid="P-xyz"), fields(sha="zz"), fields(mode="disabled"), fields(tp_refs=["../x"]),
                fields(review_sha="XYZ"), fields(ts="yesterday")):
        with pytest.raises(ValueError):
            approvals.append(tmp_path, bad)
    assert not (tmp_path / "approvals.jsonl").exists()


def test_a_symlink_is_refused_and_the_target_untouched(tmp_path):
    (tmp_path / "elsewhere").write_text("")
    (tmp_path / "approvals.jsonl").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        approvals.append(tmp_path, fields())
    with pytest.raises(OSError):
        approvals.read_chain(tmp_path)
    assert (tmp_path / "elsewhere").read_text() == ""


def test_an_oversize_file_is_a_read_error(tmp_path):
    (tmp_path / "approvals.jsonl").write_text("x" * (approvals.MAX_BYTES + 1))
    with pytest.raises(ValueError):
        approvals.read_chain(tmp_path)


def test_a_revocation_row_is_valid_and_never_matches_a_copy(tmp_path):
    a = approvals.append(tmp_path, fields(mode="warn"))
    r = approvals.append(tmp_path, fields(sha="-", mode="revoked"))
    assert r["mode"] == "revoked" and approvals.read_chain(tmp_path) == ([a, r], None)
    assert approvals.verify_copy({"id": "P-00000001", "proposal_sha256": SHA, "mode": "warn"}, [a, r]) == "mismatch"
    with pytest.raises(ValueError):
        approvals.append(tmp_path, fields(sha="-", mode="warn"))                    # "-" is for revocations only


def test_a_cut_last_line_breaks_the_chain_and_append_names_the_repair(tmp_path):
    approvals.append(tmp_path, fields())
    approvals.append(tmp_path, fields(pid="P-00000002"))
    path = tmp_path / "approvals.jsonl"
    path.write_bytes(path.read_bytes()[:-20])                                        # the second row is cut mid-line
    rows, broken = approvals.read_chain(tmp_path)
    assert len(rows) == 1 and broken == 2
    with pytest.raises(ValueError, match=r"chain broken at row 2.*truncate the file to its last complete line"):
        approvals.append(tmp_path, fields(pid="P-00000003"))
    assert path.read_bytes().count(b"\n") == 1                                       # no lead-newline recovery: nothing appended


def test_a_fifo_is_refused_by_the_reader_and_the_writer(tmp_path):
    os.mkfifo(tmp_path / "approvals.jsonl")
    with pytest.raises(OSError):
        approvals.read_chain(tmp_path)
    with pytest.raises(OSError):
        approvals.append(tmp_path, fields())


def test_newest_and_verify_copy(tmp_path):
    a = approvals.append(tmp_path, fields())
    b = approvals.append(tmp_path, fields(sha=OTHER, mode="warn"))
    rows = [a, b]
    assert approvals.newest(rows, "P-00000001") == b and approvals.newest(rows, "P-00000009") is None
    rec = {"id": "P-00000001", "proposal_sha256": OTHER, "mode": "warn"}
    assert approvals.verify_copy(rec, rows) == "ok"
    assert approvals.verify_copy({**rec, "id": "P-00000009"}, rows) == "missing"
    assert approvals.verify_copy({**rec, "proposal_sha256": SHA}, rows) == "mismatch"      # hash edited
    assert approvals.verify_copy({**rec, "mode": "block"}, rows) == "mismatch"              # mode edited
    assert approvals.verify_copy(rec, []) == "missing"


# ------------------------------------------------------------------ the hook honours a copy only with a matching row
@pytest.fixture
def w(tmp_path):
    return cs.real_world(tmp_path)


@pytest.fixture
def run(w, monkeypatch, capsys):
    return Runner(w, monkeypatch, capsys)


def promote(w, mode, row=True):
    p = w.props["rm_rf_nonscratch"]
    return p, cs.approve_file(w.cfg, p, mode, row=row)


def test_the_allowed_modes_cover_the_whole_ladder():
    assert shadow.ALLOWED_MODES == ("observe", "warn", "block")


@pytest.mark.parametrize("mode", ["warn", "block"])
def test_a_copy_above_observe_without_a_row_runs_in_observe_and_logs_missing(w, run, mode):
    p, _ = promote(w, mode, row=False)
    code, out, err = run(cs.pre_payload(RM))
    assert (code, out, err) == (0, "", "") and run.rows[0]["mode"] == "observe"
    assert f"approvals:{p['id']}:missing" in run.log


def test_a_copy_whose_hash_or_mode_differs_from_the_newest_row_runs_in_observe(w, run):
    p, path = promote(w, "warn")
    rec = json.loads(path.read_text())
    rec["mode"] = "block"
    rec["proposal_sha256"] = shadow.record_sha256(rec)              # a deliberate edit that recomputes the hash
    path.write_text(json.dumps(rec))
    assert run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows[0]["mode"] == "observe" and f"approvals:{p['id']}:mismatch" in run.log


def test_an_observe_copy_needs_no_row(w, run):
    promote(w, "observe", row=False)
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows[0]["mode"] == "observe"
    assert "approvals:" not in run.log


def test_a_broken_approvals_chain_demotes_every_copy_above_observe_and_logs_once(tmp_path, monkeypatch, capsys):
    w2 = cs.real_world(tmp_path, ("rm_rf_nonscratch", "rm_rf"))
    r = Runner(w2, monkeypatch, capsys)
    for p in w2.props.values():
        cs.approve_file(w2.cfg, p, "warn", row=True)
    path = w2.data / "approvals.jsonl"
    path.write_text(path.read_text().replace("operator-tty", "someone-else", 1))
    assert r(cs.pre_payload(RM)) == (0, "", "")
    assert {x["mode"] for x in r.rows} == {"observe"} and r.log.count("approvals:chain_broken") == 1


def test_an_unreadable_approvals_file_falls_back_to_observe(w, run):
    promote(w, "warn")
    (w.data / "approvals.jsonl").unlink()
    (w.data / "approvals.jsonl").symlink_to(w.data / "nowhere")
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows[0]["mode"] == "observe"
    assert "OSError" in run.log and str(w.data) not in run.log


def test_a_cut_last_line_or_a_fifo_makes_the_hook_run_observe(w, run):
    promote(w, "warn")
    path = w.data / "approvals.jsonl"
    path.write_bytes(path.read_bytes()[:-20])
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows[-1]["mode"] == "observe"
    assert "approvals:chain_broken" in run.log
    path.unlink()
    os.mkfifo(path)
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows[-1]["mode"] == "observe" and "OSError" in run.log


# ------------------------------------------------------------------ warn delivery
def test_a_matching_warn_row_prints_one_additional_context_json_on_stdout(w, run):
    p, _ = promote(w, "warn")
    code, out, err = run(cs.pre_payload(RM + "/unique-marker-1234"))
    assert code == 0 and out.count("\n") == 1 and err == p["message"] + "\n"
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": p["message"]}}
    assert "unique-marker" not in out + err and run.rows[0]["mode"] == "warn"
    assert run(cs.pre_payload("ls"))[1:] == ("", "")


def fake_hits(monkeypatch, modes, event="Stop"):
    ps = [{"id": f"P-0000000{i}", "lesson_id": "L-00000001", "event": event, "matcher": "*",
           "message": f"ACE P-0000000{i}: message {i}", "check": {"detector": "rm_rf_nonscratch", "on_truncated": "ignore"}}
          for i, _ in enumerate(modes, 1)]
    monkeypatch.setattr(shadow, "load_approved", lambda cfg, log: list(zip(ps, modes)))
    monkeypatch.setattr(shadow.catalogue, "evaluate",
                        lambda ev, s, names=None, strict=False: ([Fire("rm_rf_nonscratch")], s or {}, []))
    return ps


STOP = {"hook_event_name": "Stop", "session_id": "s"}


def test_stop_warns_join_into_one_additional_context(w, run, monkeypatch):
    ps = fake_hits(monkeypatch, ["warn", "warn"])
    code, out, err = run(STOP, arg="stop")
    both = ps[0]["message"] + "\n" + ps[1]["message"]
    assert code == 0 and out.count("\n") == 1 and err == both + "\n"
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": both}}


def test_a_block_hit_wins_over_warn_which_then_goes_to_stderr_only(w, run, monkeypatch):
    ps = fake_hits(monkeypatch, ["warn", "block"])
    code, out, err = run(STOP, arg="stop")
    assert json.loads(out) == {"decision": "block", "reason": ps[1]["message"]} and ps[0]["message"] in err
    ps = fake_hits(monkeypatch, ["warn", "block"], event="PreToolUse")
    code, out, err = run(cs.pre_payload("ls"))
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                      "permissionDecisionReason": ps[1]["message"]}}
    assert out.count("\n") == 1 and ps[0]["message"] in err


def test_stop_hook_active_silences_stdout_for_block_and_warn(w, run, monkeypatch):
    fake_hits(monkeypatch, ["warn"])
    assert run({**STOP, "stop_hook_active": True}, arg="stop")[1] == ""
    fake_hits(monkeypatch, ["warn", "block"])
    assert run({**STOP, "stop_hook_active": True}, arg="stop")[1] == ""
