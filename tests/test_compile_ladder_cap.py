"""`compile approve --mode warn|block` honours the demotions.jsonl cap (docs/design/compiler.md 7.3)."""

import json

import pytest

from ace_playbook.compile import approvals, demotions, modes

from . import compile_support as cs
from .test_compile_cli import approved_dir, legacy, run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_ladder_cli import copy_of, observed, ready, shift  # noqa: F401  (fixtures)


def demote(w, pid, to_mode="observe", ts=None):
    """A demotion row; `ts` after the approval (a row at or before the newest approval row is lifted by it)."""
    demotions.append(w.cfg.data_dir, {"proposal_id": pid, "from_mode": "block", "to_mode": to_mode, "cause": "label_fp",
                                      "ref": "0123abcd", "review_required": False, **({"ts": ts} if ts else {})})


def refused(w, tty, capsys):
    code, out, _ = run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "demoted:observe" in out and tty.asked == [] and copy_of(w, w.pid)["mode"] == "observe"


def test_warn_is_refused_for_a_proposal_demoted_to_observe(ready, tty, capsys):
    demote(ready, ready.pid, ts=shift(ready.entry, 1))
    refused(ready, tty, capsys)


def test_warn_is_refused_for_a_disabled_proposal(ready, tty, capsys):
    demote(ready, ready.pid, "disabled", ts=shift(ready.entry, 1))
    code, out, _ = run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "demoted:disabled" in out and tty.asked == []


def test_a_demotion_of_another_proposal_does_not_refuse(ready, tty, capsys):
    demote(ready, "P-22222222")
    assert run(ready, "approve", ready.pid, "--mode", "warn", capsys=capsys)[0] == 0


def test_a_broken_demotions_chain_refuses_warn(ready, tty, capsys):
    (ready.cfg.data_dir / demotions.FILE).write_text("not json\n")
    refused(ready, tty, capsys)


def test_an_unreadable_demotions_file_refuses_warn(ready, tty, capsys):
    (ready.cfg.data_dir / demotions.FILE).symlink_to(ready.cfg.data_dir / "nowhere")
    refused(ready, tty, capsys)


@pytest.mark.parametrize("to_mode", ["observe", "disabled"])
def test_observe_approvals_are_never_refused_by_the_cap(world, written, tty, capsys, to_mode):
    demote(world, written.stem, to_mode)
    assert run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)[0] == 0


def test_an_observe_back_fill_succeeds_under_a_cap_only_without_demotion_rows(world, written, tty, capsys):
    cs.approve_file(world.cfg, json.loads(written.read_text()), "observe")           # a copy and no approvals row
    demote(world, "P-22222222")                                                      # another proposal's row does not matter
    code, out, _ = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 0 and "recorded" in out and [r["mode"] for r in approvals.read_chain(world.cfg.data_dir)[0]] == ["observe"]


def test_a_back_fill_is_not_offered_under_a_demotion_row_a_fresh_approval_that_lifts_it_is(world, written, tty, capsys):
    rec = json.loads(cs.approve_file(world.cfg, json.loads(written.read_text()), "observe").read_text())
    demote(world, written.stem, "disabled", ts="2026-10-01T00:00:00Z")               # older than the fresh approval
    code, out, _ = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    (row,) = approvals.read_chain(world.cfg.data_dir)[0]
    assert code == 0 and "recorded" not in out and "lifts demotion: disabled  label_fp  2026-10-01T00:00:00Z" in out
    assert "lifting 1 demotion row(s)" in tty.asked[-1] and row["mode"] == "observe" and row["ts"] >= rec["approved_at"]
    assert modes.resolve(written.stem, copy_of(world, written.stem), [row], None, demotions.read_chain(world.cfg.data_dir)[0],
                         None, "2099-01-01T00:00:00Z").cap is None                    # the row is lifted


@pytest.mark.parametrize("kind", ["broken", "unreadable"])
def test_no_back_fill_under_a_broken_or_unreadable_demotions_file(world, written, tty, capsys, kind):
    cs.approve_file(world.cfg, json.loads(written.read_text()), "observe")           # a copy and no approvals row
    path = world.cfg.data_dir / demotions.FILE
    path.write_text("not json\n") if kind == "broken" else path.symlink_to(world.cfg.data_dir / "nowhere")
    code, out, _ = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 0 and "recorded" not in out and "demotions.jsonl is " + ("broken at row 0" if kind == "broken" else "unreadable") in out
    assert tty.asked[-1].startswith(f"Approve {written.stem} in observe mode?")
