"""modes.resolve (docs/design/compiler.md 7.3, "Mode resolution"): one answer for the hook, the ladder, the offline pass and the report.

An operator approval lifts every demotion row stamped strictly before it; the cap is the lowest row stamped at or after it."""

import dataclasses
import json
import time

import pytest

from ace_playbook.atomic import write_private
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import catalogue
from ace_playbook.compile import approvals, demotions as dm, detectors, labels, modes, offline, report, shadow
from ace_playbook.compile.events import META_KEYS, event_from_hook, event_from_record, events_from_records, hash16

from . import compile_support as cs
from .test_compile_cli import approved_dir, run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_demotions import CMD, D2, chain, fields, fp, hook
from .test_compile_ladder_cli import copy_of, observed, ready, shift  # noqa: F401  (fixtures)

PID, SHA, SHA2, NOW = "P-0a1b2c3d", "a" * 64, "b" * 64, "2026-10-20T00:00:00Z"
T1, T2, T3, T4 = ("2026-10-0%dT00:00:00Z" % n for n in (1, 3, 5, 7))


def cp(mode="observe", sha=SHA, at=T1):
    return {"id": PID, "mode": mode, "approved_at": at, "proposal_sha256": sha}


def ap(mode="observe", ts=T1, sha=SHA, pid=PID):
    return {"proposal_id": pid, "mode": mode, "ts": ts, "proposal_sha256": sha}


def dr(to, ts, cause="label_fp", pid=PID):
    return {"proposal_id": pid, "from_mode": "block", "to_mode": to, "cause": cause, "ts": ts}


def res(copy, arows=(), abroken=None, drows=(), dbroken=None, now=NOW):
    return modes.resolve(PID, copy, list(arows), abroken, list(drows), dbroken, now)


# (copy, approvals rows, approvals broken, demotion rows, demotions broken, mode, approved_mode, cap, problems)
CASES = {
    "observe copy needs no row": (cp(), [], None, [], None, "observe", "observe", None, ()),
    "observe copy with its row": (cp(), [ap()], None, [], None, "observe", "observe", None, ()),
    "warn copy with its row": (cp("warn"), [ap("warn")], None, [], None, "warn", "warn", None, ()),
    "block copy with its row": (cp("block"), [ap("block")], None, [], None, "block", "block", None, ()),
    "warn copy without a row": (cp("warn"), [], None, [], None, "observe", "observe", None, ("approvals:missing",)),
    "warn copy with another hash": (cp("warn"), [ap("warn", sha=SHA2)], None, [], None, "observe", "observe", None,
                                    ("approvals:mismatch",)),
    "block copy with a warn row": (cp("block"), [ap("warn")], None, [], None, "observe", "observe", None, ("approvals:mismatch",)),
    "approvals broken, warn copy": (cp("warn"), [ap("warn")], 3, [], None, "observe", "observe", None, ("approvals:chain_broken",)),
    "approvals broken, observe copy": (cp(), [ap()], 1, [], None, "observe", "observe", None, ("approvals:chain_broken",)),
    "newest row revoked": (cp("warn"), [ap("warn"), ap("revoked", T2, "-")], None, [], None, "disabled", "disabled", None,
                           ("revoked",)),
    "re-approved after a revocation": (cp("warn"), [ap("revoked", T1, "-"), ap("warn", T2)], None, [], None, "warn", "warn", None, ()),
    "no copy": (None, [ap()], None, [], None, "disabled", "disabled", None, ("no_copy",)),
    "demotions broken": (cp("warn"), [ap("warn")], None, [], 0, "observe", "warn", "observe", ("demotions:chain_broken",)),
    "demotions unreadable": (cp("warn"), [ap("warn")], None, [], "unreadable", "observe", "warn", "observe",
                             ("demotions:unreadable",)),
    "demotions broken after a disabled row": (cp("warn"), [ap("warn")], None, [dr("disabled", T2)], 1, "disabled", "warn",
                                              "disabled", ("demotions:chain_broken",)),
    "a demotion after the approval caps": (cp("warn"), [ap("warn")], None, [dr("observe", T2)], None, "observe", "warn", "observe", ()),
    "a disabled row after the approval": (cp("warn"), [ap("warn")], None, [dr("disabled", T2)], None, "disabled", "warn",
                                          "disabled", ()),
    "a demotion before the approval is lifted": (cp("warn", at=T3), [ap("warn", T3)], None, [dr("disabled", T2)], None, "warn", "warn",
                                                 None, ()),
    "a demotion at the approval second is NOT lifted": (cp("warn", at=T2), [ap("warn", T2)], None, [dr("observe", T2)], None,
                                                        "observe", "warn", "observe", ()),
    "the lowest row after the approval": (cp("block"), [ap("block")], None, [dr("warn", T2), dr("observe", T3), dr("warn", T4)],
                                          None, "observe", "block", "observe", ()),
    "only the rows after the approval count": (cp("block", at=T3), [ap("block", T3)], None, [dr("disabled", T2), dr("warn", T4)],
                                               None, "warn", "block", "warn", ()),
    "no approvals row, every row counts": (cp(), [], None, [dr("disabled", "2026-09-01T00:00:00Z")], None, "disabled", "observe",
                                           "disabled", ()),
    "another proposal's rows are ignored": (cp("warn"), [ap("warn"), ap("block", T3, pid="P-11111111")], None,
                                            [dr("disabled", T2, pid="P-11111111")], None, "warn", "warn", None, ()),
}


@pytest.mark.parametrize("case", CASES)
def test_the_resolver_table(case):
    copy, arows, abroken, drows, dbroken, mode, approved, cap, problems = CASES[case]
    got = res(copy, arows, abroken, drows, dbroken)
    assert (got.mode, got.approved_mode, got.cap, got.problems) == (mode, approved, cap, problems)


def test_a_resolution_is_frozen_and_its_problems_are_a_tuple():
    got = res(cp("warn"))
    assert isinstance(got.problems, tuple) and got.problems == ("approvals:missing",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.mode = "block"


def test_a_broken_approvals_chain_lifts_nothing():
    got = res(cp("warn", at=T3), [ap("warn", T3)], 2, [dr("observe", T2)])
    assert got.cap == "observe" and got.mode == "observe"


# ------------------------------------------------------------------ the counting window and its source
def test_the_window_starts_at_the_newest_approval_row():
    got = res(cp("warn", at=T3), [ap("observe", T1, sha=SHA2), ap("warn", T3)])
    assert (got.window_start, got.source) == (T3, "approval")


def test_without_an_approvals_row_the_window_falls_back_to_the_copy_and_says_so():
    assert (res(cp(at=T2)).window_start, res(cp(at=T2)).source) == (T2, "copy")


def test_a_counter_resetting_demotion_after_the_approval_moves_the_window_but_an_evasion_pair_does_not():
    got = res(cp("warn", at=T1), [ap("warn")], None, [dr("observe", T3)])
    assert (got.window_start, got.source) == (T3, "demotion")
    got = res(cp("warn", at=T1), [ap("warn")], None, [dr("warn", T3, cause="evasion_pair")])
    assert (got.window_start, got.source, got.cap) == (T1, "approval", "warn")
    got = res(cp("warn", at=T3), [ap("warn", T3)], None, [dr("observe", T2)])                  # older than the approval
    assert (got.window_start, got.source) == (T3, "approval")


def test_stamps_after_now_are_ignored_for_the_window_not_clamped_and_a_future_demotion_still_caps():
    got = res(cp("warn", at=T1), [ap("warn")], None, [dr("observe", "2099-01-01T00:00:00Z")])
    assert (got.window_start, got.source, got.cap) == (T1, "approval", "observe")
    got = res(cp("warn", at=T3), [ap("warn", T3)], None, [], None, now=T2)                    # the only approval row is in the future
    assert got.window_start == "" and got.source == "none"
    got = res(cp("warn", at=T1), [ap("observe", T1, sha=SHA2), ap("warn", T3)], None, [], None, now=T2)
    assert (got.window_start, got.source) == (T1, "approval")                                   # the older row, not a clamp to now


def test_a_future_approved_at_never_starts_a_window():
    assert (res(cp(at="2099-01-01T00:00:00Z")).window_start, res(cp(at="2099-01-01T00:00:00Z")).source) == ("", "none")


def test_unreadable_chains_read_as_broken_not_as_exceptions(tmp_path):
    (tmp_path / dm.FILE).mkdir()
    (tmp_path / approvals.FILE).symlink_to(tmp_path / "nowhere")
    assert modes.read_demotions(tmp_path) == ([], "unreadable") and modes.read_approvals(tmp_path) == ([], "unreadable")
    assert modes.read_demotions(tmp_path / "none") == ([], None)


# ------------------------------------------------------------------ chain validity no longer holds the cap
def test_a_demotion_from_a_lifted_mode_is_a_valid_row_and_append_refuses_one_above_the_current_mode(tmp_path):
    dm.append(tmp_path, fields(to_mode="disabled", cause="error_rate", ref="-"))
    late = fields(to_mode="block", cause="evasion_pair", ref="cafe0001", review_required=True)
    with pytest.raises(ValueError):
        dm.append(tmp_path, late, current="disabled")                          # the resolver says disabled
    assert dm.append(tmp_path, late, current="block")["from_mode"] == "block"  # after a lift it is block again
    assert dm.read_chain(tmp_path)[1] is None and len(dm.read_chain(tmp_path)[0]) == 2
    assert dm.effective_cap(dm.read_chain(tmp_path)[0], PID) == "disabled"      # the caller passes the rows in force


# ------------------------------------------------------------------ the hook
def reapprove(w, mode, at):
    """A NEW approval: a copy with a fresh `approved_at` and its approvals row (what `compile approve` writes)."""
    rec = shadow.approved_record(w.props["rm_rf_nonscratch"], mode, at)
    write_private(approved_dir(w) / (w.pid + ".json"), json.dumps(rec, indent=1, sort_keys=True) + "\n")
    approvals.append(w.data, {"proposal_id": w.pid, "proposal_sha256": rec["proposal_sha256"], "mode": mode, "ts": at})


def test_hook_warn_then_a_demotion_to_observe_then_a_newer_approval_lifts_it(tmp_path, monkeypatch, capsys):
    w = cs.offline_world(tmp_path, "warn")
    out, _, _ = hook(w, monkeypatch, capsys)
    assert '"additionalContext"' in out and cs.shadow_rows(w.data)[-1]["mode"] == "warn"
    dm.append(w.data, fields(proposal_id=w.pid, from_mode="warn", to_mode="observe", ref="aaaa1111", ts=T2))
    out, err, _ = hook(w, monkeypatch, capsys)
    assert (out, err) == ("", "") and cs.shadow_rows(w.data)[-1]["mode"] == "observe"
    reapprove(w, "warn", T3)                                                    # a NEW warn approval row
    out, _, log = hook(w, monkeypatch, capsys)
    assert '"additionalContext"' in out and cs.shadow_rows(w.data)[-1]["mode"] == "warn" and "approvals:" not in log
    dm.append(w.data, fields(proposal_id=w.pid, from_mode="warn", to_mode="observe", ref="bbbb2222", ts=T4), current="warn")
    assert dm.read_chain(w.data)[1] is None and hook(w, monkeypatch, capsys)[0] == ""
    assert cs.shadow_rows(w.data)[-1]["mode"] == "observe"


def test_hook_a_disabled_row_skips_until_a_newer_approval_and_a_revoked_proposal_is_skipped_by_name(tmp_path, monkeypatch, capsys):
    w = cs.offline_world(tmp_path, "observe")
    dm.append(w.data, fields(proposal_id=w.pid, from_mode="observe", to_mode="disabled", cause="error_rate", ref="-", ts=T2))
    _, _, log = hook(w, monkeypatch, capsys)
    assert f"skipped:{w.pid}:demoted" in log and cs.shadow_rows(w.data) == []
    reapprove(w, "observe", T3)
    hook(w, monkeypatch, capsys)
    assert len(cs.shadow_rows(w.data)) == 1
    approvals.append(w.data, {"proposal_id": w.pid, "proposal_sha256": "-", "mode": "revoked", "ts": T4})
    _, _, log = hook(w, monkeypatch, capsys)
    assert f"skipped:{w.pid}:revoked" in log and len(cs.shadow_rows(w.data)) == 1


def test_hook_logs_each_problem_once_and_reads_both_chains_once(tmp_path, monkeypatch, capsys):
    w = cs.offline_world(tmp_path, "warn")
    (w.data / approvals.FILE).write_text("not json\n")
    seen = []
    for mod in (approvals, dm):
        real = mod.read_chain
        monkeypatch.setattr(mod, "read_chain", lambda d, key=None, real=real, name=mod.__name__: seen.append(name) or real(d, key))
    hook(w, monkeypatch, capsys)
    _, _, log = hook(w, monkeypatch, capsys)
    assert log.count("approvals:chain_broken") == 1 and cs.shadow_rows(w.data)[-1]["mode"] == "observe"
    assert "error:approvals" not in log and "error:demotions" not in log          # the chain was read and judged broken, not a failed read
    assert sorted(seen) == sorted([approvals.__name__, dm.__name__] * 2)


# ------------------------------------------------------------------ the ladder
def test_the_ladder_refuses_demoted_observe_until_a_newer_approval_lifts_the_row(ready, tty, capsys, monkeypatch):
    w = ready
    dm.append(w.cfg.data_dir, fields(proposal_id=w.pid, from_mode="observe", to_mode="observe", cause="label_fp_pair",
                                     review_required=True, ts=shift(w.entry, 1)))
    code, out, _ = run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "demoted:observe" in out and tty.asked == []          # the demotion is newer than the approval
    monkeypatch.setattr(shadow, "now_iso", lambda: shift(w.entry, 2))
    monkeypatch.setattr(cli, "now_iso", lambda: shift(w.entry, 2))                 # the fixture's days are in the future
    code, out, _ = run(w, "approve", w.pid, "--mode", "observe", capsys=capsys)  # not "already approved": it lifts the row
    assert code == 0 and "lifting 1 demotion row(s)" in tty.asked[-1] and "lifts demotion: observe  label_fp_pair" in out
    assert [(r["mode"], r["ts"]) for r in approvals.read_chain(w.cfg.data_dir)[0]] == [
        ("observe", w.entry), ("observe", shift(w.entry, 2))]
    code, out, _ = run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "demoted" not in out and "13 days in observe, need 14" in out   # the window restarts at the new row


def test_approve_observe_stays_a_no_op_without_a_demotion_in_force(ready, tty, capsys):
    assert "already approved" in run(ready, "approve", ready.pid, "--mode", "observe", capsys=capsys)[1]
    dm.append(ready.cfg.data_dir, fields(proposal_id=ready.pid, from_mode="observe", to_mode="observe", ts=shift(ready.entry, -1)))
    assert "already approved" in run(ready, "approve", ready.pid, "--mode", "observe", capsys=capsys)[1]    # older: lifted


# ------------------------------------------------------------------ the offline pass
def test_the_offline_pass_counts_fps_from_the_resolver_window_and_appends_from_the_resolved_mode(tmp_path):
    w = cs.offline_world(tmp_path, "block")
    dm.append(w.data, fields(proposal_id=w.pid, from_mode="block", to_mode="warn", ref="aaaa1111", ts="2026-10-11T00:00:00Z"))
    reapprove(w, "block", "2026-10-15T00:00:00Z")                               # lifts the older block-to-warn row
    fp(w, cs.put_shadow(w, "s0", CMD, "block", "2026-10-12T10:00:00Z"))         # before the new approval: not counted
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 0
    late = cs.put_shadow(w, "s1", CMD, "block", D2)
    fp(w, late)
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 1
    new = chain(w)[-1]
    assert (new["from_mode"], new["to_mode"], new["ref"]) == ("block", "warn", labels.row_id(late))
    assert [r.mode for r in [offline.current_mode(c, *modes.read_approvals(w.data), chain(w), offline.iso(cs.NOW))
                              for c in shadow.approved_records(w.cfg)]] == ["warn"]


def test_the_offline_pass_skips_a_revoked_proposal_and_ignores_a_future_stamp(tmp_path):
    w = cs.offline_world(tmp_path, "block")
    fp(w, cs.put_shadow(w, "s1", CMD, "block", D2))
    approvals.append(w.data, {"proposal_id": w.pid, "proposal_sha256": "-", "mode": "revoked", "ts": T4})
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["demotions"] == 0 and out["errors"] == 0
    (copy,) = shadow.approved_records(w.cfg)
    dm.append(w.data, fields(proposal_id=w.pid, from_mode="block", to_mode="warn", ref="aaaa1111", ts="2099-01-01T00:00:00Z"))
    arows = [ap("block", T1, sha=copy[2]["proposal_sha256"], pid=w.pid)]
    got = offline.current_mode(copy, arows, None, chain(w), "2026-10-19T12:00:00Z")
    assert got.window_start == T1 and got.mode == "warn"                          # the forged future stamp does not blind counting


def test_a_cause_that_comes_back_after_a_lift_is_written_again_and_the_hook_skips_again(tmp_path, monkeypatch, capsys):
    w = cs.offline_world(tmp_path, "observe")
    monkeypatch.setattr(catalogue, "_source_texts", lambda: ("an edited detector",))         # a standing disable cause
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 1 and offline.run(w.cfg, now=cs.NOW + 60)["demotions"] == 0
    reapprove(w, "observe", "2026-10-19T12:30:00Z")                                           # the operator lifts it
    assert offline.run(w.cfg, now=cs.NOW + 7200)["demotions"] == 1                            # same to_mode, cause and ref
    assert [(d["cause"], d["from_mode"], d["to_mode"]) for d in chain(w)] == [("source_sha256", "observe", "disabled")] * 2
    monkeypatch.undo()
    _, _, log = hook(w, monkeypatch, capsys)
    assert f"skipped:{w.pid}:demoted" in log and cs.shadow_rows(w.data) == []


def test_a_real_pass_stamps_each_row_when_it_is_appended_not_when_the_pass_started(tmp_path, monkeypatch):
    w = cs.offline_world(tmp_path, "observe")
    monkeypatch.setattr(catalogue, "_source_texts", lambda: ("an edited detector",))
    assert offline.run(w.cfg)["demotions"] == 1                                               # no `now`: the real clock
    assert abs(dm.epoch(chain(w)[0]["ts"]) - time.time()) < 10


# ------------------------------------------------------------------ the lift prompt
def test_approve_observe_on_a_demoted_warn_copy_lists_each_row_it_lifts(ready, tty, capsys, monkeypatch):
    w = ready
    assert run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)[0] == 0
    dm.append(w.cfg.data_dir, fields(proposal_id=w.pid, from_mode="warn", to_mode="observe", ts=shift(w.entry, 16)))
    for mod in (shadow, cli):
        monkeypatch.setattr(mod, "now_iso", lambda: shift(w.entry, 17))
    tty.asked.clear()
    code, out, _ = run(w, "approve", w.pid, "--mode", "observe", capsys=capsys)             # the copy is warn: a fresh approval
    assert code == 0 and f"lifts demotion: observe  label_fp  {shift(w.entry, 16)}" in out
    assert "lifting 1 demotion row(s)" in tty.asked[-1] and copy_of(w, w.pid)["mode"] == "observe"
    rows = approvals.read_chain(w.cfg.data_dir)[0]
    assert modes.resolve(w.pid, copy_of(w, w.pid), rows, None, dm.read_chain(w.cfg.data_dir)[0], None, shift(w.entry, 18)).cap is None


def test_approve_observe_without_a_demotion_in_force_shows_no_lift_notice(ready, tty, capsys):
    w = ready
    assert run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)[0] == 0
    tty.asked.clear()
    code, out, _ = run(w, "approve", w.pid, "--mode", "observe", capsys=capsys)
    assert code == 0 and "lifts demotion" not in out and "lifting" not in tty.asked[-1]


# ------------------------------------------------------------------ chain refusals
def test_a_garbage_first_line_of_approvals_is_a_clean_refusal_on_the_observe_and_the_warn_paths(ready, tty, capsys):
    (ready.cfg.data_dir / approvals.FILE).write_text("not json\n")
    for mode in ("observe", "warn"):
        code, out, err = run(ready, "approve", ready.pid, "--mode", mode, capsys=capsys)
        assert code == 1 and "approvals.jsonl chain broken at row 1" in out + err and "Traceback" not in out + err
    assert tty.asked == []


def test_approve_observe_says_so_when_the_only_demotion_row_cannot_be_lifted_yet(observed, tty, capsys, monkeypatch):
    monkeypatch.setattr(cli, "now_iso", lambda: observed.entry)                          # the row ties the clock second
    dm.append(observed.cfg.data_dir, fields(proposal_id=observed.pid, from_mode="observe", to_mode="observe", ts=observed.entry))
    code, out, _ = run(observed, "approve", observed.pid, "--mode", "observe", capsys=capsys)
    assert code == 0 and "capped at observe by 1 row(s) not liftable now (re-run in a second)" in out and "already approved" not in out
    assert tty.asked == [] and len(approvals.read_chain(observed.cfg.data_dir)[0]) == 1


def test_a_far_future_row_says_until_when_and_a_mixed_case_says_what_stays_capped(observed, tty, capsys, monkeypatch):
    w = observed
    far = fields(proposal_id=w.pid, from_mode="observe", to_mode="disabled", cause="error_rate", ref="-", ts="2099-01-01T00:00:00Z")
    dm.append(w.cfg.data_dir, far)
    out = run(w, "approve", w.pid, "--mode", "observe", capsys=capsys)[1]
    assert "capped at disabled until 2099-01-01T00:00:00Z (1 row(s) not liftable now)" in out and tty.asked == []
    dm.append(w.cfg.data_dir, fields(proposal_id=w.pid, from_mode="observe", to_mode="observe", ts=shift(w.entry, 1)))
    for mod in (shadow, cli):
        monkeypatch.setattr(mod, "now_iso", lambda: shift(w.entry, 2))
    code, out, _ = run(w, "approve", w.pid, "--mode", "observe", capsys=capsys)          # one row lifts, the 2099 row stays
    assert code == 0 and "lifts demotion: observe  label_fp" in out and "still capped at disabled until 2099-01-01" in out


def test_an_ok_copy_with_a_broken_demotions_file_and_nothing_in_force_still_gets_the_chain_notice(observed, tty, capsys):
    (observed.cfg.data_dir / dm.FILE).write_text("not json\n")
    code, out, _ = run(observed, "approve", observed.pid, "--mode", "observe", capsys=capsys)
    assert code == 0 and "demotions.jsonl is broken at row 1" in out and "already approved" in out and tty.asked == []


def test_a_meta_human_record_does_not_reset_the_turn():
    state = detectors.advance(event_from_record({"type": "attachment", "attachment": {"type": "file", "filename": "/a"}}), None)
    assert state["turn_paths"]
    meta = event_from_record({"type": "user", "isVisibleInTranscriptOnly": True, "message": {"role": "user", "content": "go on"}})
    assert meta.human and detectors.advance(meta, state)["turn_paths"] == state["turn_paths"]
    plain = event_from_record({"type": "user", "message": {"role": "user", "content": "go on"}})
    assert detectors.advance(plain, state)["turn_paths"] == []


# ------------------------------------------------------------------ the report
def test_the_report_marks_lifted_rows_and_shows_the_resolved_and_the_approved_mode(tmp_path):
    w = cs.offline_world(tmp_path, "warn")
    dm.append(w.data, fields(proposal_id=w.pid, from_mode="warn", to_mode="observe", ref="aaaa1111", ts=T2))
    chains = report._chains(w.cfg)
    assert report._meta(w.cfg, w.pid, chains, NOW)[2:] == ("warn", "observe")     # (approved_mode, mode)
    assert [r["lifted"] for r in report.demotion_summary(w.cfg, NOW)["rows"]] == [False]
    reapprove(w, "warn", T3)
    got = report.demotion_summary(w.cfg, NOW)
    assert [r["lifted"] for r in got["rows"]] == [True] and got["proposals"][w.pid]["cap"] == "-"
    assert report._meta(w.cfg, w.pid, report._chains(w.cfg), NOW)[2:] == ("warn", "warn")
    assert "lifted" in report.render("0" * 64, 14, {"proposals": {}, "per_day": []}, {"proposals": {}, "per_day": []}, None, got)


# ------------------------------------------------------------------ meta records
@pytest.mark.parametrize("key", META_KEYS)
def test_meta_user_records_are_flagged_and_never_a_correction(key):
    e = event_from_record({"type": "user", "message": {"role": "user", "content": "I told you not to do that"}, key: True})
    assert e.meta and detectors.user_correction(e, {}) is None             # `human` is not touched: only the consumers check meta
    plain = event_from_record({"type": "user", "message": {"role": "user", "content": "I told you not to do that"}})
    assert not plain.meta and plain.human and detectors.user_correction(plain, {}).count == 1
    assert not event_from_hook({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}).meta


def test_a_meta_queued_prompt_is_flagged():
    att = {"type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": "no, stop", "isMeta": True}
    e = event_from_record({"type": "attachment", "attachment": att})
    assert e.meta and not e.human


RM = {"tool": "Bash", "input": {"command": CMD}}


def with_meta(steps, key, text):
    return list(events_from_records(cs.records(steps) + [{"type": "user", "isSidechain": False, key: True,
                                                          "message": {"role": "user", "content": text}}]))


@pytest.mark.parametrize("key", META_KEYS)
@pytest.mark.parametrize("mode", ["observe", "warn", "block"])
def test_a_meta_correction_after_a_fire_never_auto_labels(mode, key):
    evs = with_meta([{"human": "tidy"}, RM], key, "I told you not to delete that again")
    i = next(n for n, e in enumerate(evs) if e.tool_uses)
    assert offline.classify(mode, evs, i, evs[i].tool_uses[0], hash16("/w/src")) is None


@pytest.mark.parametrize("key", META_KEYS)
def test_a_meta_prompt_naming_the_target_is_not_a_prompt_target_and_does_not_cut_the_span(key):
    meta = {"type": "user", "isSidechain": False, key: True, "message": {"role": "user", "content": "clean /w/src please"}}
    evs = list(events_from_records(cs.records([{"human": "tidy"}]) + [meta] + cs.records([RM])))
    i = next(n for n, e in enumerate(evs) if e.tool_uses)
    assert offline.classify("observe", evs, i, evs[i].tool_uses[0], hash16("/w/src")) is None
    plain = list(events_from_records(cs.records([{"human": "tidy"}, {"human": "clean /w/src please"}, RM])))
    assert offline.classify("observe", plain, 2, plain[2].tool_uses[0], hash16("/w/src")) == "auto_fp_prompt_target"
    after = list(events_from_records([*cs.records([RM]), meta, *cs.records([{"human": "a"}, {"human": "b"}, {"human": "c"}])]))
    assert offline.span(after, 0)[-1].user_text == "b"                                       # the second REAL prompt ends it


@pytest.mark.parametrize("key", META_KEYS)
def test_a_meta_bash_input_record_is_not_a_shell_entry(key):
    text = "<bash-input>rm -rf /w/src</bash-input>"
    assert offline.shell_keys(with_meta([{"human": "tidy"}], key, text)) == set()
    assert offline.shell_keys(list(events_from_records(cs.records([{"human": "tidy"}, {"human": text}])))) == {hash16("/w/src")}
