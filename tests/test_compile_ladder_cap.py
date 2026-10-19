"""`compile approve --mode warn|block` honours the demotions.jsonl cap (docs/design/compiler.md 7.3)."""

import json

import pytest

from ace_playbook.compile import approvals, demotions

from . import compile_support as cs
from .test_compile_cli import approved_dir, legacy, run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_ladder_cli import copy_of, observed, ready  # noqa: F401  (fixtures)


def demote(w, pid, to_mode="observe"):
    demotions.append(w.cfg.data_dir, {"proposal_id": pid, "from_mode": "block", "to_mode": to_mode, "cause": "label_fp",
                                      "ref": "0123abcd", "review_required": False})


def refused(w, tty, capsys):
    code, out, _ = run(w, "approve", w.pid, "--mode", "warn", capsys=capsys)
    assert code == 1 and "demoted:observe" in out and tty.asked == [] and copy_of(w, w.pid)["mode"] == "observe"


def test_warn_is_refused_for_a_proposal_demoted_to_observe(ready, tty, capsys):
    demote(ready, ready.pid)
    refused(ready, tty, capsys)


def test_warn_is_refused_for_a_disabled_proposal(ready, tty, capsys):
    demote(ready, ready.pid, "disabled")
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


def test_an_observe_back_fill_succeeds_under_a_cap(world, written, tty, capsys):
    cs.approve_file(world.cfg, json.loads(written.read_text()), "observe")           # a copy and no approvals row
    demote(world, written.stem)
    code, out, _ = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 0 and "recorded" in out and [r["mode"] for r in approvals.read_chain(world.cfg.data_dir)[0]] == ["observe"]
