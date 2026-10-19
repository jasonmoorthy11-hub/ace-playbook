"""ladder.check (docs/design/compiler.md 7.3): the promotion rules as pure functions over rows, no filesystem."""

import dataclasses

import pytest

from ace_playbook.compile import catalogue, labels, ladder

PID, LESSON = "P-00000001", "L-00000001"
SINCE, NOW = "2026-10-01T00:00:00Z", "2026-10-16T00:00:00Z"          # 15 days apart
SHA, SHA2 = "a" * 64, "b" * 64
DET = catalogue.CATALOGUE["rm_rf_nonscratch"]


def fire(i, ts="2026-10-05T00:00:00Z", pid=PID, tier="live", mode="observe"):
    return {"ts": ts, "proposal_id": pid, "session": f"{i:016x}", "payload_sha256": f"{i:064x}", "tier": tier,
            "mode": mode, "would_fire": True}


def appr(mode, ts, pid=PID, sha=SHA):
    return {"proposal_id": pid, "mode": mode, "ts": ts, "proposal_sha256": sha}


def cp(mode="observe", at=SINCE, sha=SHA):
    return {"id": PID, "mode": mode, "approved_at": at, "proposal_sha256": sha}


def lab(row, label="tp", at="2026-10-06T00:00:00Z", reason="human"):
    return {"ref": labels.row_id(row), "label": label, "reason_code": reason, "at": at}


def proposal(det=DET):
    return {"id": PID, "lesson_id": LESSON, "event": "PreToolUse", "check": {"detector": det.name},
            "provenance": {"source_sha256": det.source_sha256}}


def world(n=5, **over):
    """5 live fires with a human tp each, an observe copy and row at SINCE, lesson active."""
    fires = [fire(i) for i in range(n)]
    kw = dict(p=proposal(), copy=cp(), rows_shadow=fires,
              rows_labels=[lab(f) for f in fires],
              rows_approvals=[appr("observe", SINCE)],
              catalogue_row=DET, lesson_status="active", now=NOW)
    kw.update(over)
    return kw


def check(raw_checked=False, cap=None, mode="warn", **over):
    return ladder.check(**world(**over), raw_checked=raw_checked, cap=cap, mode=mode)


def test_the_clean_case_passes_and_counts_five_tps():
    res = check()
    assert res.ok and res.reasons == [] and len(res.tps) == 5 and res.fps == []
    assert res.since == SINCE and res.since_source == "approvals row"


def test_fewer_than_five_human_tps_fails():
    res = check(rows_labels=[lab(fire(i)) for i in range(4)])
    assert not res.ok and any("4 human TPs" in r for r in res.reasons)


def test_a_candidate_or_auto_tp_never_counts():
    rows = [lab(fire(i)) for i in range(4)] + [lab(fire(4), "tp_candidate", reason="auto_hook_block"),
                                               lab(fire(4), "tp", reason="auto")]
    assert not check(rows_labels=rows).ok


def test_less_than_14_days_in_observe_fails_and_14_days_passes():
    assert any("13 days" in r for r in check(now="2026-10-14T23:59:59Z").reasons)
    assert check(now="2026-10-15T00:00:00Z").ok


def test_without_an_observe_row_the_copys_approved_at_is_the_start_and_says_so():
    res = check(rows_approvals=[])
    assert res.ok and res.since == SINCE and res.since_source == "copy approved_at"


def test_the_newest_observe_row_is_the_start_not_the_first():
    rows = [appr("observe", "2026-09-01T00:00:00Z", sha=SHA2), appr("observe", "2026-10-10T00:00:00Z")]
    res = check(rows_approvals=rows)
    assert not res.ok and res.since == "2026-10-10T00:00:00Z" and res.tps == []     # fires before it do not count


def test_a_copy_that_is_missing_or_not_in_observe_fails():
    assert any("not in observe" in r for r in check(copy=None).reasons)
    assert any("not in observe" in r for r in check(copy=cp("warn")).reasons)


def test_an_fp_since_observe_fails_and_is_listed():
    f = fire(9)
    res = check(rows_shadow=world()["rows_shadow"] + [f], rows_labels=world()["rows_labels"] + [lab(f, "fp")])
    assert not res.ok and len(res.fps) == 1 and res.fps[0].row_id == labels.row_id(f)
    assert any("1 FPs" in r for r in res.reasons)


def test_an_auto_fp_blocks_unless_a_human_label_exists_for_the_ref():
    f = fire(9)
    shadow = world()["rows_shadow"] + [f]
    auto = lab(f, "fp", reason="auto_user_objected")
    res = check(rows_shadow=shadow, rows_labels=world()["rows_labels"] + [auto])
    assert not res.ok and len(res.fps) == 1
    human_tp = [lab(f, "tp", at="2026-10-07T00:00:00Z"), lab(f, "fp", at="2026-10-08T00:00:00Z", reason="auto_x")]
    res = check(rows_shadow=shadow, rows_labels=world()["rows_labels"] + human_tp)            # the human row wins
    assert res.ok and len(res.tps) == 6 and res.fps == []
    res = check(rows_shadow=shadow, rows_labels=world()["rows_labels"] + [lab(f, "fp"), lab(f, "tp", reason="auto_y")])
    assert not res.ok and len(res.fps) == 1                                                    # a human fp exists: it decides


def test_a_later_human_tp_replaces_an_earlier_human_fp_and_the_reverse():
    f = fire(9)
    shadow = world()["rows_shadow"] + [f]
    base = world()["rows_labels"]
    assert check(rows_shadow=shadow, rows_labels=base + [lab(f, "fp"), lab(f, "tp")]).ok
    assert not check(rows_shadow=shadow, rows_labels=base + [lab(f, "tp"), lab(f, "fp")]).ok


def test_fires_before_the_start_other_proposals_and_non_live_tiers_do_not_count():
    old, other, synth = fire(10, ts="2026-09-30T23:59:59Z"), fire(11, pid="P-00000002"), fire(12, tier="synthetic")
    res = check(rows_shadow=world()["rows_shadow"] + [old, other, synth],
                rows_labels=world()["rows_labels"] + [lab(old, "fp"), lab(other, "fp"), lab(synth, "fp")])
    assert res.ok and len(res.tps) == 5 and res.fps == []


def test_a_label_without_a_fire_row_is_a_forgery_and_fails():
    ghost = {"ref": "deadbeef", "label": "tp", "reason_code": "human", "at": "2026-10-06T00:00:00Z"}
    res = check(rows_labels=world()["rows_labels"] + [ghost])
    assert not res.ok and any("forged label" in r and "deadbeef" in r for r in res.reasons)
    assert not check(rows_labels=world()["rows_labels"] + [{**ghost, "label": "fp", "reason_code": "auto"}]).ok
    assert check(rows_labels=world()["rows_labels"] + [{**ghost, "label": "tp_candidate", "reason_code": "auto"},
                                                      {**ghost, "ref": "feedface", "label": "incident"}]).ok


def test_a_label_for_a_fire_of_another_proposal_is_not_a_forgery():
    other = fire(11, pid="P-00000002")
    res = check(rows_shadow=world()["rows_shadow"] + [other], rows_labels=world()["rows_labels"] + [lab(other)])
    assert res.ok and len(res.tps) == 5


def test_the_lesson_must_be_active():
    for status in ("quarantined", "retired", None):
        assert any("lesson" in r for r in check(lesson_status=status).reasons)


def test_a_weak_detector_never_promotes_and_neither_does_a_low_compilable_rank():
    weak = catalogue.CATALOGUE["rm_rf"]
    res = check(p=proposal(weak), catalogue_row=weak)
    assert not res.ok and any("weak" in r for r in res.reasons)
    low = dataclasses.replace(DET, compilable="observe")
    res = check(p=proposal(low), catalogue_row=low)
    assert not res.ok and any("observe at most" in r for r in res.reasons)


def test_a_changed_detector_source_fails():
    p = proposal()
    p["provenance"] = {"source_sha256": "0" * 64}
    assert any("source_sha256" in r for r in check(p=p).reasons)


def test_edit_unread_session_needs_the_raw_checked_attestation():
    det = catalogue.CATALOGUE["edit_unread_session"]
    assert det.strength == "strong" and det.compilable == "warn"
    assert any("raw-checked" in r for r in check(p=proposal(det), catalogue_row=det).reasons)
    assert check(p=proposal(det), catalogue_row=det, raw_checked=True).ok
    assert check(raw_checked=False).ok                                                           # other detectors do not need it


def test_a_cap_below_the_requested_mode_refuses_with_demoted():
    assert any(r == "demoted:observe" for r in check(cap="observe").reasons)
    assert check(cap="warn").ok and check(cap="block").ok and check(cap=None).ok


def test_labels_after_the_last_approval_are_flagged_in_the_listing():
    fires = world()["rows_shadow"]
    rows = [lab(f, at="2026-10-05T00:00:00Z") for f in fires[:3]] + [lab(f, at="2026-10-09T00:00:00Z") for f in fires[3:]]
    appr_rows = [appr("observe", SINCE, sha=SHA2), appr("observe", "2026-10-05T00:00:00Z"),
                 appr("warn", "2026-10-20T00:00:00Z", pid="P-00000002")]
    res = check(rows_labels=rows, rows_approvals=appr_rows)
    assert sorted(l.after for l in res.tps) == [False, False, False, True, True] and len(res.flagged) == 2


def test_the_listing_line_has_every_field_and_the_chain_position():
    fires = world()["rows_shadow"]
    labs = [lab(f, at="2026-10-05T00:00:00Z") for f in fires]
    res = check(rows_labels=[{"ref": "00000000", "label": "incident", "reason_code": "human", "at": SINCE}, *labs],
                rows_approvals=[appr("observe", "2026-10-05T00:00:00Z")])
    first = next(x for x in res.tps if x.row_id == labels.row_id(fires[0]))
    assert first.n == 2 and not first.after                                                       # 1-based position in labels.jsonl
    assert ladder.listing_line(first) == (f"fire {fires[0]['ts']}  session {fires[0]['session']}  "
                                          f"row {labels.row_id(fires[0])}  label {labs[0]['at']}  chain #2")
    flagged = dataclasses.replace(first, after=True)
    assert ladder.listing_line(flagged).endswith("chain #2  [after last approval]")
    assert ladder.listing_line(flagged, tag=False).endswith("chain #2")


def test_a_listing_line_strips_control_characters_from_file_values():
    f = fire(1)
    l = ladder.Listing("tp", "2026-10-05T00:00:00Z\x1b[2J", f["session"], "ab12cd34", "at\x07", 1, False)
    assert "\x1b" not in ladder.listing_line(l) and "\x07" not in ladder.listing_line(l)


# ------------------------------------------------------------------ block
def block_world(**over):
    over.setdefault("copy", cp("warn"))
    over.setdefault("rows_shadow", [fire(i, mode="warn") for i in range(5)])      # fires of the warn period
    over.setdefault("rows_approvals", [appr("observe", "2026-09-01T00:00:00Z", sha=SHA2), appr("warn", SINCE)])
    return check(mode="block", **over)


def test_block_counts_from_the_warn_row_and_passes_with_the_same_numbers():
    res = block_world()
    assert res.ok and res.since == SINCE and len(res.tps) == 5


def test_block_without_a_warn_row_or_a_warn_copy_is_not_in_warn():
    assert any("not in warn" in r for r in block_world(rows_approvals=[]).reasons)
    assert any("not in warn" in r for r in block_world(copy=cp("observe")).reasons)


def test_block_needs_a_block_capable_pretooluse_detector():
    det = catalogue.CATALOGUE["edit_unread_session"]
    assert any("warn at most" in r for r in block_world(p=proposal(det), catalogue_row=det).reasons)
    p = proposal()
    p["event"] = "Stop"
    assert any("PreToolUse" in r for r in block_world(p=p).reasons)


def test_block_honours_the_cap_and_the_fourteen_days():
    assert any(r == "demoted:warn" for r in block_world(cap="warn").reasons)
    assert any("days" in r for r in block_world(now="2026-10-10T00:00:00Z").reasons)


def test_check_does_not_mutate_its_inputs():
    kw = world()
    import copy as cp
    before = cp.deepcopy(kw)
    ladder.check(**kw, raw_checked=False)
    assert kw == before


# ------------------------------------------------------------------ the copy must be what the chain says, and the window is bounded
def test_a_demotion_after_warn_means_the_hook_runs_observe_so_block_is_refused():
    rows = [appr("observe", "2026-09-01T00:00:00Z", sha=SHA2), appr("warn", "2026-09-16T00:00:00Z", sha=SHA2),
            appr("observe", SINCE, sha="c" * 64)]
    res = block_world(rows_approvals=rows)                                                       # the copy says warn, no row backs it
    assert not res.ok and res.since is None
    assert "copy not backed by the approvals chain" in res.reasons
    assert "not in warn (newest approvals row is observe)" in res.reasons


def test_a_row_without_its_copy_is_refused_the_crash_path():
    res = block_world(copy=None)
    assert not res.ok and "not in warn" in res.reasons and "copy not backed by the approvals chain" in res.reasons
    res = check(copy=None)
    assert "copy not backed by the approvals chain" in res.reasons


def test_a_copy_whose_hash_differs_from_the_newest_row_is_not_backed():
    assert "copy not backed by the approvals chain" in check(copy=cp(sha=SHA2)).reasons


def test_a_newer_row_of_another_mode_or_a_revocation_means_not_in_the_previous_mode():
    res = check(rows_approvals=[appr("observe", "2026-09-01T00:00:00Z", sha=SHA2), appr("warn", SINCE)], copy=cp("warn"))
    assert "not in observe (newest approvals row is warn)" in res.reasons
    res = check(rows_approvals=[appr("observe", "2026-09-01T00:00:00Z"), appr("revoked", SINCE, sha="-")])
    assert "not in observe (newest approvals row is revoked)" in res.reasons and not res.ok


def test_only_fires_run_in_the_previous_mode_count():
    assert check(rows_shadow=[fire(i, mode="warn") for i in range(5)]).tps == []
    assert block_world(rows_shadow=[fire(i, mode="observe") for i in range(5)]).tps == []


def test_a_fire_after_now_does_not_count_and_a_label_dated_in_the_future_refuses():
    late = fire(9, ts="2026-10-17T00:00:00Z")
    res = check(rows_shadow=world()["rows_shadow"] + [late], rows_labels=world()["rows_labels"] + [lab(late, at="2026-10-18T00:00:00Z")])
    assert len(res.tps) == 5 and any("label dated in the future" in r for r in res.reasons) and not res.ok
    assert any("label dated in the future" in r for r in check(rows_labels=[lab(f, at="2026-10-17T00:00:00Z") for f in world()["rows_shadow"]]).reasons)


def test_a_label_dated_before_its_fire_refuses():
    fires = world()["rows_shadow"]
    res = check(rows_labels=[lab(f, at="2026-10-04T23:59:59Z") for f in fires])
    assert not res.ok and any("label precedes its fire" in r for r in res.reasons)
    assert check(rows_labels=[lab(f, at=f["ts"]) for f in fires]).ok                              # the same second is fine
