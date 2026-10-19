"""shadow.py log rate limits (docs/design/compiler.md 7): repeating lines are written once, errors every time."""

import json
from types import SimpleNamespace

import pytest

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


def _stale(w):                                               # the body changes and the hash is recomputed: it fails validate
    path = next((w.data / "proposals" / "approved").glob("*.json"))
    rec = json.loads(path.read_text())
    rec["lesson_id"] = "L-00000000"
    rec["proposal_sha256"] = shadow.record_sha256(rec)
    path.write_text(json.dumps(rec))


def _oversize(w):
    next((w.data / "proposals" / "approved").glob("*.json")).write_bytes(b" " * (shadow.MAX_FILE_BYTES + 1))


def _bad_name(w):
    (w.data / "proposals" / "approved" / "bad.json").write_text("{}")


def _too_many(w):
    src = next((w.data / "proposals" / "approved").glob("*.json")).read_bytes()
    for i in range(shadow.MAX_FILES + 1):
        (w.data / "proposals" / "approved" / f"P-{i:08x}.json").write_bytes(src)


def _symlink(w):
    d = w.data / "proposals" / "approved"
    d.rename(w.data / "elsewhere")
    d.symlink_to(w.data / "elsewhere")


@pytest.mark.parametrize("setup, line", [(_stale, ":invalid:"), (_oversize, ":oversize"), (_bad_name, "skipped:file_name"),
                                         (_too_many, "skipped:too_many_files"), (_symlink, "approved_dir_symlink")])
def test_every_skip_line_is_written_once_not_on_every_call(w, run, setup, line):
    setup(w)
    sizes = []
    for _ in range(5):
        run(cs.pre_payload(RM))
        sizes.append((w.data / "shadow.log").stat().st_size)
    assert sizes[0] > 0 and len(set(sizes)) == 1 and line in run.log and "error:" not in run.log


def test_once_does_not_match_the_concatenation_of_two_earlier_short_messages(tmp_path):
    from ace_playbook.config import Config
    cfg, out = Config(data_dir=tmp_path), []
    once = shadow._Once(cfg, out.append)
    for msg in ("ab", "cd", "abcd", "ab"):
        once(msg)
    assert out == ["ab", "cd", "abcd"]
