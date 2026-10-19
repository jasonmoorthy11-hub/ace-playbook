"""shadow.py log rate limits (docs/design/compiler.md 7): repeating lines are written once, errors every time."""

from types import SimpleNamespace

from ace_playbook.compile import demotions, shadow

from . import compile_support as cs
from .test_compile_shadow import RM, Runner, run, w  # noqa: F401  (fixtures)


def test_thirty_demoted_proposals_do_not_grow_the_log_on_later_calls(w, run, monkeypatch):
    base = w.props["rm_rf_nonscratch"]
    monkeypatch.setattr(shadow.proposal, "validate", lambda body, cfg, **kw: SimpleNamespace(ok=True, failed=None))
    ids = [f"P-{i:08x}" for i in range(30)]
    for pid in ids:                                           # valid approved copies, each one disabled by a demotion row
        cs.approve_file(w.cfg, {**base, "id": pid})
        demotions.append(w.data, {"proposal_id": pid, "from_mode": "block", "to_mode": "disabled", "cause": "error_rate",
                                  "ref": "-", "review_required": False})
    sizes = []
    for _ in range(5):
        run(cs.pre_payload(RM))
        sizes.append((w.data / "shadow.log").stat().st_size)
    assert sizes[0] > 30 * 20 and len(set(sizes)) == 1
    assert all(run.log.count(f"skipped:{pid}:demoted") == 1 for pid in ids)


def test_a_broken_approvals_chain_is_logged_once_across_calls(w, run):
    p = w.props["rm_rf_nonscratch"]
    cs.approve_file(w.cfg, p, "warn", row=True)
    path = w.data / "approvals.jsonl"
    path.write_text(path.read_text().replace("operator-tty", "someone-else", 1))
    for _ in range(5):
        run(cs.pre_payload(RM))
    assert run.log.count("approvals:chain_broken") == 1 and [r["mode"] for r in run.rows] == ["observe"] * 5


def test_error_lines_are_not_throttled(w, run):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)
    (w.data / "approvals.jsonl").unlink()
    (w.data / "approvals.jsonl").symlink_to(w.data / "nowhere")
    for _ in range(5):
        run(cs.pre_payload(RM))
    assert run.log.count("error:approvals:OSError") == 5
