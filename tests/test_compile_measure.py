"""`compile/measure.py`: the one measurement-start rule shared by `status` and `seal` (docs/design/compiler.md 7.3 and 8.x)."""

from pathlib import Path

import pytest

from ace_playbook.compile import approvals, demotions, measure, seal, status

from . import compile_support as cs
from .test_compile_status import approve, build, pid_of, revoke

ROOT = Path(__file__).resolve().parent.parent
DETS = ("rm_rf_nonscratch", "rm_rf")                                # promoted to warn / demotion-disabled
GONE = "P-0badf00d"                                                 # a third proposal, revoked: only its approvals rows exist


def r(pid, mode, ts):
    return {"proposal_id": pid, "proposal_sha256": "a" * 64, "mode": mode, "ts": ts}


def test_nothing_approved_has_no_start():
    assert measure.start_of([]) is None


def test_the_start_is_the_greatest_newest_observe_row_over_the_proposals_in_play():
    rows = [r("P-1", "observe", "2026-10-01T00:00:00Z"), r("P-2", "observe", "2026-10-03T00:00:00Z"), r("P-1", "observe", "2026-10-02T00:00:00Z")]
    assert measure.start_of(rows) == "2026-10-03T00:00:00Z"
    assert measure.start_of(rows[:1] + rows[2:]) == "2026-10-02T00:00:00Z"          # a proposal's NEWEST observe row, in chain order


def test_a_warn_or_block_row_after_the_observe_row_does_not_move_the_start():
    rows = [r("P-1", "observe", "2026-10-01T00:00:00Z"), r("P-1", "warn", "2026-10-20T00:00:00Z"), r("P-1", "block", "2026-10-25T00:00:00Z")]
    assert measure.start_of(rows) == "2026-10-01T00:00:00Z"


def test_only_a_revoked_newest_row_removes_a_proposal_and_a_reapproval_restores_it():
    rows = [r("P-1", "observe", "2026-10-01T00:00:00Z"), r("P-2", "observe", "2026-10-05T00:00:00Z"), r("P-2", "revoked", "2026-10-06T00:00:00Z")]
    assert measure.start_of(rows) == "2026-10-01T00:00:00Z"
    assert measure.start_of(rows + [r("P-2", "observe", "2026-10-07T00:00:00Z")]) == "2026-10-07T00:00:00Z"
    assert measure.start_of(rows + [r("P-1", "revoked", "2026-10-08T00:00:00Z")]) is None


def test_a_proposal_whose_chain_holds_no_observe_row_does_not_set_the_start():
    assert measure.start_of([r("P-1", "warn", "2026-10-20T00:00:00Z")]) is None


def test_measure_stays_within_its_line_budget():
    assert len((ROOT / "ace_playbook" / "compile" / "measure.py").read_text().splitlines()) <= 120


# ------------------------------------------------------------------ status, seal and the helper agree on a real world
@pytest.fixture
def w3(tmp_path):
    return cs.real_world(tmp_path, detectors=DETS, approve=False)


def three(w):
    """(status start, seal start, helper start) read from the world's own files."""
    rows, _ = approvals.read_chain(w.data, None)
    return build(w)["measurement"]["start"], seal._proposals(w.cfg)[1], measure.start_of(rows)


def test_status_seal_and_the_helper_agree_when_nothing_is_approved(w3):
    assert three(w3) == (None, "-", None)
    assert "measurement_start -\n" in seal.frozen_block(w3.cfg)


def test_status_seal_and_the_helper_agree_with_a_revoked_a_demoted_and_a_warn_promoted_proposal(w3):
    approve(w3, "rm_rf_nonscratch", "2026-10-12T00:00:00Z")                               # (c) kept: observe, then promoted
    approve(w3, "rm_rf", "2026-10-10T00:00:00Z")                                          # (b) demoted to disabled later
    approvals.append(w3.data, r(GONE, "observe", "2026-10-19T00:00:00Z"))                 # (a) revoked: its later observe row must not count
    approvals.append(w3.data, r(GONE, "revoked", "2026-10-19T12:00:00Z"))
    demotions.append(w3.data, {"proposal_id": pid_of(w3, "rm_rf"), "from_mode": "observe", "to_mode": "disabled",
                               "cause": "label_fp", "ref": "0123abcd", "review_required": False, "ts": "2026-10-11T00:00:00Z"})
    assert three(w3) == ("2026-10-12T00:00:00Z",) * 3
    approve(w3, "rm_rf_nonscratch", "2026-10-20T00:00:00Z", "warn")                       # (c) a warn promotion after the observe row
    assert three(w3) == ("2026-10-12T00:00:00Z",) * 3                                     # the start does not move
    assert "measurement_start 2026-10-12T00:00:00Z\n" in seal.frozen_block(w3.cfg)
    assert f"  {pid_of(w3)} warn " in seal.frozen_block(w3.cfg)                           # the listed lines are unchanged: newest row, any mode
    assert GONE not in seal.frozen_block(w3.cfg)
    revoke(w3, "rm_rf_nonscratch", "2026-10-21T00:00:00Z")
    assert three(w3) == ("2026-10-10T00:00:00Z",) * 3                                     # only the demotion-disabled proposal is left
    assert build(w3)["proposals"][pid_of(w3, "rm_rf")]["mode"] == "disabled"


def test_the_helper_is_what_status_and_seal_call():
    assert status.measure is measure and seal.measure is measure
