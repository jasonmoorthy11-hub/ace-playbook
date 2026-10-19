"""`compile approve` end to end (docs/design/compiler.md 7.3): the observe row, the warn and block ladder, the hook."""

import calendar
import io
import json
import os
import stat
import sys
import time
from types import SimpleNamespace

import pytest

from ace_playbook.compile import approvals, catalogue, labels, ladder, shadow
from ace_playbook.compile import __main__ as cli

from . import compile_support as cs
from .test_compile_cli import BANNER, FakeTty, approved_dir, legacy, run, tty, world, written  # noqa: F401  (fixtures)


def shift(ts, days=0, secs=0):
    t = calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) + days * 86400 + secs
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def copy_of(world, pid):
    return json.loads((approved_dir(world) / (pid + ".json")).read_text())


# ------------------------------------------------------------------ approve --mode observe and the chain
def test_approve_observe_appends_one_chained_row_with_the_copys_hash(world, written, tty, capsys):
    run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    (row,) = approvals.read_chain(world.cfg.data_dir)[0]
    rec = copy_of(world, written.stem)
    assert (row["proposal_id"], row["mode"], row["proposal_sha256"], row["ts"]) == \
           (written.stem, "observe", rec["proposal_sha256"], rec["approved_at"])
    assert (row["tp_refs"], row["fp_refs"], row["review_sha"]) == ([], [], "-")


def test_approve_observe_back_fills_a_row_for_a_copy_that_predates_the_chain(world, written, tty, capsys):
    dest = cs.approve_file(world.cfg, json.loads(written.read_text()), "observe")     # a copy and no row
    before = dest.read_bytes()
    code, out, _ = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    (row,) = approvals.read_chain(world.cfg.data_dir)[0]
    rec = json.loads(before)
    assert code == 0 and "recorded" in out and dest.read_bytes() == before and len(tty.asked) == 1
    assert (row["proposal_sha256"], row["ts"], row["mode"]) == (rec["proposal_sha256"], rec["approved_at"], "observe")
    assert approvals.verify_copy(rec, [row]) == "ok"
    assert "already approved" in run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)[1]
    assert len(tty.asked) == 1                                                       # no second question, no second row


def test_approve_observe_back_fill_asks_and_a_no_writes_nothing(world, written, tty, capsys):
    cs.approve_file(world.cfg, json.loads(written.read_text()), "observe")
    tty.answer = "n"
    code, _, err = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "not approved" in err and not (world.cfg.data_dir / "approvals.jsonl").exists()


def test_approve_observe_refuses_on_a_broken_approvals_chain(world, written, tty, capsys):
    run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    path = world.cfg.data_dir / "approvals.jsonl"
    path.write_text(path.read_text().replace("observe", "warn"))
    tty.asked.clear()
    code, _, err = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "chain broken at row 1" in err and tty.asked == []


def test_a_copy_edited_down_to_observe_is_approved_afresh_not_back_filled(observed, tty, capsys):
    w = observed
    approvals.append(w.cfg.data_dir, {"proposal_id": w.pid, "proposal_sha256": "c" * 64, "mode": "warn"})   # the chain says warn
    code, out, _ = run(w, "approve", w.pid, "--mode", "observe", capsys=capsys)
    rows = approvals.read_chain(w.cfg.data_dir)[0]
    rec = copy_of(w, w.pid)
    assert code == 0 and "recorded" not in out and "evidence cards:" in out
    assert tty.asked == [f"Approve {w.pid} in observe mode? [y/N] "]
    assert [r["mode"] for r in rows] == ["observe", "warn", "observe"]
    assert (rows[2]["ts"], rows[2]["proposal_sha256"]) == (rec["approved_at"], rec["proposal_sha256"])


def test_unapprove_appends_a_revocation_so_restoring_the_old_warn_copy_runs_observe(ready, tty, capsys, monkeypatch):
    w = ready
    assert run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)[0] == 0
    dest = approved_dir(w) / (w.pid + ".json")
    old = dest.read_bytes()
    tty.asked.clear()
    assert run(w, "unapprove", w.pid, capsys=capsys)[0] == 0 and not dest.exists()
    assert tty.asked == [f"Remove the approval of {w.pid}? [y/N] "]
    last = approvals.read_chain(w.cfg.data_dir)[0][-1]
    assert (last["mode"], last["proposal_id"], last["proposal_sha256"]) == ("revoked", w.pid, json.loads(old)["proposal_sha256"])
    assert (last["tp_refs"], last["fp_refs"], last["review_sha"]) == ([], [], "-")
    dest.write_bytes(old)                                                           # the agent puts the old bytes back
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", w.conf)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(cs.RM_CMD)).encode())))
    assert shadow.main(["pre"]) == 0
    assert capsys.readouterr().out == "" and cs.shadow_rows(w.cfg.data_dir)[-1]["mode"] == "observe"
    assert f"approvals:{w.pid}:mismatch" in (w.cfg.data_dir / "shadow.log").read_text()


def test_unapprove_asks_first_and_a_broken_chain_does_not_stop_the_removal(observed, tty, capsys):
    w, dest = observed, approved_dir(observed) / (observed.pid + ".json")
    tty.answer = "n"
    assert run(w, "unapprove", w.pid, capsys=capsys)[0] == 1 and dest.exists()
    tty.answer = "y"
    path = w.cfg.data_dir / "approvals.jsonl"
    path.write_text(path.read_text().replace("observe", "warn"))
    code, _, err = run(w, "unapprove", w.pid, capsys=capsys)
    assert code == 0 and not dest.exists() and "revocation row not written" in err


def test_a_back_fill_shows_the_provenance_and_a_legacy_copy_needs_both_answers(legacy, tty, capsys):
    cs.approve_file(legacy.cfg, json.loads((legacy.cfg.data_dir / "proposals" / (legacy.pid + ".json")).read_text()), "observe")
    code, out, _ = run(legacy, "approve", legacy.pid, "--mode", "observe", capsys=capsys)
    assert code == 0 and BANNER in out and "evidence cards:" in out and "message:" in out and "source_sha256:" in out
    (row,) = approvals.read_chain(legacy.cfg.data_dir)[0]
    assert len(tty.asked) == 2 and tty.asked[0] == f"Record the approval of {legacy.pid} in observe mode (approved {row['ts']})? [y/N] "
    assert tty.asked[1].startswith("Legacy evidence")


def test_a_back_fill_of_a_copy_dated_in_the_future_is_refused(world, written, tty, capsys):
    from ace_playbook.atomic import write_private
    p = json.loads(written.read_text())
    dest = approved_dir(world) / (written.stem + ".json")
    dest.parent.mkdir(mode=0o700, parents=True)
    write_private(dest, json.dumps(shadow.approved_record(p, "observe", now="2099-01-01T00:00:00Z")))
    code, _, err = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "in the future" in err and tty.asked == [] and not (world.cfg.data_dir / "approvals.jsonl").exists()


def test_warn_refuses_a_copy_with_no_approvals_row_and_names_the_command(world, written, tty, capsys):
    cs.approve_file(world.cfg, json.loads(written.read_text()), "observe")
    code, out, _ = run(world, "approve", written.stem, "--mode", "warn", capsys=capsys)
    assert code == 1 and "run approve --mode observe to record it" in out and tty.asked == []


def test_a_copy_write_failure_after_the_row_says_so(ready, tty, capsys, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("x")

    monkeypatch.setattr(ladder, "write_private", boom)
    code, _, err = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "approvals row written but the copy failed (PermissionError)" in err
    assert "re-run approve --mode warn" in err and [r["mode"] for r in approvals.read_chain(ready.cfg.data_dir)[0]] == ["observe", "warn"]
    assert copy_of(ready, ready.pid)["mode"] == "observe"


# ------------------------------------------------------------------ the warn ladder
@pytest.fixture
def observed(world, written, tty, capsys, monkeypatch):
    """The proposal approved in observe through the CLI; `world.entry` is the approval time."""
    run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    world.pid, world.entry = written.stem, copy_of(world, written.stem)["approved_at"]
    world.later = lambda days: monkeypatch.setattr(ladder, "now_iso", lambda: shift(world.entry, days))
    tty.asked.clear()
    return world


def add_fires(world, n=5, day=1, mode="observe", tier="live", offset=0):
    rows = [{"ts": shift(world.entry, day, i), "proposal_id": world.pid, "lesson_id": world.lesson_id, "mode": mode,
             "event": "PreToolUse", "tool_name": "Bash", "session": f"{i + offset:016x}",
             "payload_sha256": f"{i + offset + 1:064x}", "would_fire": True, "cause": "detector", "tier": tier}
            for i in range(n)]
    for r in rows:
        cs.append_jsonl(world.cfg.path("shadow.jsonl"), r)
    return rows


def set_labels(world, rows):
    world.cfg.path("labels.jsonl").unlink(missing_ok=True)
    cs.write_labels(world.cfg, [{"reason_code": "human", "label": "tp", **r} for r in rows])


def tp_labels(fires, at):
    return [{"ref": labels.row_id(f), "at": at} for f in fires]


@pytest.fixture
def ready(observed):
    """Five live fires a day after approval, each with a human tp label dated two days after approval (after its fire)."""
    fires = add_fires(observed)
    observed.fires = fires
    set_labels(observed, tp_labels(fires, shift(observed.entry, 2)))
    observed.later(15)
    return observed


def test_warn_prints_every_counted_tp_then_asks_and_promotes(ready, tty, capsys):
    w = ready
    code, out, _ = run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)
    assert code == 0 and tty.asked == [f"Promote {w.pid} to warn mode? 5 TPs, 0 FPs listed above. [y/N] "]
    expected = sorted(f"fire {f['ts']}  session {f['session']}  row {labels.row_id(f)}  label {shift(w.entry, 2)}  chain #{n}"
                      for n, f in enumerate(w.fires, 1))
    shown = [line.strip() for line in out.splitlines() if line.strip().startswith("fire ")]
    assert sorted(shown) == expected and f"observe since {w.entry} (approvals row)" in out
    assert f"all 5 counted labels postdate the last approval row ({w.entry})" in out and "[after last approval]" not in out
    rec, rows = copy_of(w, w.pid), approvals.read_chain(w.cfg.data_dir)[0]
    assert rec["mode"] == "warn" and rec["approved_at"] == shift(w.entry, 15) and [r["mode"] for r in rows] == ["observe", "warn"]
    assert rows[1]["proposal_sha256"] == rec["proposal_sha256"] and rows[1]["tp_refs"] == sorted(labels.row_id(f) for f in w.fires)
    assert rows[1]["fp_refs"] == [] and rows[1]["review_sha"] == "-" and approvals.verify_copy(rec, rows) == "ok"
    assert [p["id"] for p, m in shadow.load_approved(w.cfg, lambda m: None) if m == "warn"] == [w.pid]


def test_a_no_at_the_prompt_leaves_the_copy_and_the_chain_alone(ready, tty, capsys):
    tty.answer = "n"
    code, _, err = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "not approved" in err and copy_of(ready, ready.pid)["mode"] == "observe"
    assert len(approvals.read_chain(ready.cfg.data_dir)[0]) == 1


def test_less_than_14_days_prints_the_failing_rule_and_asks_nothing(ready, tty, capsys, monkeypatch):
    ready.later(3)
    code, out, err = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "FAIL: 3 days in observe, need 14" in out and "not promoted" in err and tty.asked == []


def test_an_fp_label_is_listed_and_blocks_promotion(ready, tty, capsys):
    bad = add_fires(ready, 1, day=3, offset=50)[0]
    set_labels(ready, tp_labels(ready.fires, shift(ready.entry, 2)) + [{**tp_labels([bad], shift(ready.entry, 4))[0], "label": "fp"}])
    code, out, _ = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "counted FPs (1):" in out and f"row {labels.row_id(bad)}" in out and "1 FPs" in out and tty.asked == []


def test_a_forged_label_without_a_fire_row_is_refused(ready, tty, capsys):
    set_labels(ready, tp_labels(ready.fires, shift(ready.entry, 2)) + [{"ref": "deadbeef", "at": shift(ready.entry, 2)}])
    code, out, _ = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "forged label: deadbeef (no fire row)" in out and tty.asked == []
    assert copy_of(ready, ready.pid)["mode"] == "observe"


def test_a_broken_labels_or_approvals_chain_is_refused(ready, tty, capsys):
    path = ready.cfg.path("labels.jsonl")
    good = path.read_text()
    path.write_text(good.replace('"tp"', '"fp"', 1))
    code, _, err = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "labels.jsonl chain broken at row 1" in err
    path.write_text(good)
    appr = ready.cfg.data_dir / "approvals.jsonl"
    appr.write_text(appr.read_text().replace("observe", "block"))
    code, _, err = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "approvals.jsonl chain broken at row 1" in err and tty.asked == []


def test_an_inactive_lesson_a_failed_validation_and_a_stale_detector_source_each_refuse(ready, tty, capsys, monkeypatch):
    assert ladder.lesson_status(ready.cfg, ready.lesson_id) == "active" and ladder.lesson_status(ready.cfg, "L-00000000") is None
    with monkeypatch.context() as m:
        m.setattr(ladder, "lesson_status", lambda cfg, lid: "retired")
        code, out, _ = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "lesson is not active (retired)" in out
    src = ready.cfg.data_dir / "proposals" / (ready.pid + ".json")
    p = json.loads(src.read_text())
    src.write_text(json.dumps({**p, "message": "ACE nope"}))
    code, out, _ = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "validation failed at step 'message'" in out
    src.write_text(json.dumps(p))
    monkeypatch.setattr(catalogue, "_source_texts", lambda: ("changed",))
    code, _, err = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "step 'schema'" in err and tty.asked == []


def test_a_demotion_cap_below_the_requested_mode_refuses(ready, tty):
    args = SimpleNamespace(id=ready.pid, mode="warn", opus_reviewed=None, raw_checked=False)
    assert ladder.run(ready.cfg, args, lambda q: "y", lambda what: True, cap="observe") == 1
    assert copy_of(ready, ready.pid)["mode"] == "observe"
    assert ladder.run(ready.cfg, args, lambda q: "y", lambda what: True, cap="warn") == 0


def test_a_missing_tty_is_exit_2_and_nothing_is_read(ready, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", type("NoTty", (), {"isatty": lambda s: False})())
    code, out, err = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 2 and "terminal" in err and out == ""


# ------------------------------------------------------------------ the block ladder
def test_block_needs_a_review_sha_and_not_on_warn(ready, tty, capsys):
    for extra in ([], ["--opus-reviewed", "XYZ"], ["--opus-reviewed", "abc12"]):
        assert run(ready, "approve", ready.pid, "--mode", "block", *extra, capsys=capsys)[0] == 2
    assert run(ready, "approve", ready.pid, "--mode", "warn", "--opus-reviewed", "abc1234", capsys=capsys)[0] == 2
    code, out, _ = run(ready, "approve", ready.pid, "--mode", "block", "--opus-reviewed", "abc1234", capsys=capsys)
    assert code == 1 and "FAIL: not in warn" in out and tty.asked == []


def test_warn_then_block_records_the_review_sha_and_the_hook_denies(ready, tty, capsys, monkeypatch):
    w = ready
    assert run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)[0] == 0
    warned = copy_of(w, w.pid)["approved_at"]
    fires = add_fires(w, 5, day=16, mode="warn", offset=100)
    set_labels(w, tp_labels(w.fires, shift(w.entry, 2)) + tp_labels(fires, shift(w.entry, 17)))
    w.later(31)
    tty.asked.clear()
    code, out, _ = run(w, "approve", w.pid, "--mode", "block", "--opus-reviewed", "abc1234", capsys=capsys)
    assert code == 0 and tty.asked == [f"Promote {w.pid} to block mode? 5 TPs, 0 FPs listed above. Opus review abc1234. [y/N] "]
    assert f"warn since {warned} (approvals row)" in out and out.count("fire ") == 5     # the old observe-period fires are not counted
    rows = approvals.read_chain(w.cfg.data_dir)[0]
    assert [r["mode"] for r in rows] == ["observe", "warn", "block"] and rows[2]["review_sha"] == "abc1234"
    assert rows[2]["tp_refs"] == sorted(labels.row_id(f) for f in fires)
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", w.conf)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(cs.RM_CMD)).encode())))
    assert shadow.main(["pre"]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ------------------------------------------------------------------ the whole thing through the real hook
def test_end_to_end_observe_then_five_labelled_fires_then_warn_then_the_hook_speaks(observed, tty, capsys, monkeypatch):
    w = observed
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", w.conf)

    def hook(payload):
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
        assert shadow.main(["pre"]) == 0
        monkeypatch.setattr(sys, "stdin", FakeTty())
        return capsys.readouterr()

    for i in range(5):
        out = hook(cs.pre_payload(cs.RM_CMD, sid=f"sess-e2e-{i}"))
        assert (out.out, out.err) == ("", "")                                       # observe: silent
    ids = [labels.row_id(r) for r in cs.shadow_rows(w.cfg.data_dir)]
    assert len(set(ids)) == 5
    for rid in ids:
        assert run(w, "label", "--tp", rid, capsys=capsys)[0] == 0
    assert run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)[0] == 1        # today: 0 days in observe
    w.later(15)
    assert run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)[0] == 0
    msg = json.loads((w.cfg.data_dir / "proposals" / (w.pid + ".json")).read_text())["message"]
    out = hook(cs.pre_payload(cs.RM_CMD, sid="sess-e2e-fresh"))
    assert json.loads(out.out) == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}
    assert out.err == msg + "\n" and cs.shadow_rows(w.cfg.data_dir)[-1]["mode"] == "warn"


# ------------------------------------------------------------------ unapprove: the revocation row has to land
def test_unapprove_records_a_revocation_when_the_copy_is_gone_but_the_row_is_not_revoked(observed, tty, capsys):
    w = observed
    (approved_dir(w) / (w.pid + ".json")).unlink()
    code, out, _ = run(w, "unapprove", w.pid, capsys=capsys)
    last = approvals.read_chain(w.cfg.data_dir)[0][-1]
    assert code == 0 and f"ok: revocation recorded for {w.pid} (no copy present)" in out
    assert (last["mode"], last["proposal_sha256"]) == ("revoked", "-")
    code, _, err = run(w, "unapprove", w.pid, capsys=capsys)                      # now there is nothing left to revoke
    assert code == 1 and "is not approved" in err and len(approvals.read_chain(w.cfg.data_dir)[0]) == 2


def test_unapprove_with_neither_a_copy_nor_a_row_is_not_approved(world, written, tty, capsys):
    code, _, err = run(world, "unapprove", written.stem, capsys=capsys)
    assert code == 1 and "is not approved" in err and tty.asked == []


def test_a_broken_chain_at_unapprove_is_repaired_then_unapprove_is_rerun(ready, tty, capsys, monkeypatch):
    w = ready
    assert run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)[0] == 0
    dest = approved_dir(w) / (w.pid + ".json")
    old = dest.read_bytes()
    path = w.cfg.data_dir / "approvals.jsonl"
    whole = path.read_bytes()
    path.write_bytes(whole[:-20])                                                  # the warn row is cut mid-line
    code, _, err = run(w, "unapprove", w.pid, capsys=capsys)
    assert code == 0 and not dest.exists()
    assert f"revocation row not written" in err and f"re-run unapprove {w.pid}" in err
    code, _, err = run(w, "unapprove", w.pid, capsys=capsys)                      # no copy, chain still broken: nothing to do yet
    assert code == 1 and "re-run unapprove" in err
    path.write_bytes(whole[:whole.rindex(b"\n", 0, len(whole) - 1) + 1])          # repair: truncate to the last complete line
    code, out, _ = run(w, "unapprove", w.pid, capsys=capsys)
    assert code == 0 and "revocation recorded" in out and approvals.read_chain(w.cfg.data_dir)[0][-1]["mode"] == "revoked"
    dest.write_bytes(old)                                                          # the old warn copy comes back
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", w.conf)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(cs.RM_CMD)).encode())))
    assert shadow.main(["pre"]) == 0
    assert capsys.readouterr().out == "" and cs.shadow_rows(w.cfg.data_dir)[-1]["mode"] == "observe"
    assert f"approvals:{w.pid}:mismatch" in (w.cfg.data_dir / "shadow.log").read_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_an_unwritable_approvals_file_warns_by_type_and_still_removes_the_copy(observed, tty, capsys):
    w, dest = observed, approved_dir(observed) / (observed.pid + ".json")
    path = w.cfg.data_dir / "approvals.jsonl"
    path.chmod(0o400)
    try:
        code, _, err = run(w, "unapprove", w.pid, capsys=capsys)
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert code == 0 and not dest.exists() and "revocation row not written (PermissionError)" in err
    assert str(w.cfg.data_dir) not in err


def test_an_unreadable_copy_is_revoked_with_a_dash_hash(observed, tty, capsys):
    w, dest = observed, approved_dir(observed) / (observed.pid + ".json")
    dest.write_text("{not json")
    assert run(w, "unapprove", w.pid, capsys=capsys)[0] == 0 and not dest.exists()
    last = approvals.read_chain(w.cfg.data_dir)[0][-1]
    assert (last["mode"], last["proposal_sha256"]) == ("revoked", "-")
