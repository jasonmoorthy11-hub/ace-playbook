"""demotions.py (docs/design/compiler.md 7.3): the hash-chained, lowering-only demotions file and its readers."""

import io
import json
import os
import sys

import pytest

from ace_playbook.atomic import write_private
from ace_playbook.canon import GENESIS, record_hash
from ace_playbook.compile import catalogue, labels, offline, shadow
from ace_playbook.compile import demotions as dm
from ace_playbook.store import DeltaLog

from . import compile_support as cs
from .helpers import GLOBAL, add_draft

PID, OTHER = "P-0a1b2c3d", "P-1b2c3d4e"


def fields(**kw):
    base = {"proposal_id": PID, "from_mode": "block", "to_mode": "warn", "cause": "label_fp", "ref": "0123abcd",
            "review_required": False}
    return {**base, **kw}


def forge(data, *specs):
    """A hand-written chain (the writer would refuse an invalid row, the reader must too)."""
    prev, lines = GENESIS, []
    for spec in specs:
        row = {"ts": "2026-10-19T00:00:00Z", "v": 1, **fields(**spec), "prev_hash": prev}
        row["hash"] = record_hash(row)
        prev = row["hash"]
        lines.append(json.dumps(row, sort_keys=True))
    (data / dm.FILE).write_text("\n".join(lines) + "\n")


def test_a_row_whose_ts_is_not_a_stamp_breaks_the_chain(tmp_path):
    for ts in ("yesterday", "2026-10-19", "２０２６-10-19T00:00:00Z", 5):
        row = {"ts": ts, "v": 1, **fields(), "prev_hash": GENESIS}
        (tmp_path / dm.FILE).write_text(json.dumps({**row, "hash": record_hash(row)}, sort_keys=True) + "\n")
        assert dm.read_chain(tmp_path) == ([], 0)


def test_append_chains_rows_from_genesis_and_writes_0600(tmp_path):
    a = dm.append(tmp_path, fields())
    b = dm.append(tmp_path, fields(from_mode="warn", to_mode="observe", ref="ffff0000"))
    rows, broken = dm.read_chain(tmp_path)
    assert rows == [a, b] and broken is None
    assert (a["prev_hash"], b["prev_hash"]) == (GENESIS, a["hash"]) and a["v"] == 1
    assert a["hash"] == record_hash(a) and oct((tmp_path / dm.FILE).stat().st_mode & 0o777) == "0o600"


def test_a_missing_file_is_an_empty_intact_chain(tmp_path):
    assert dm.read_chain(tmp_path) == ([], None)


def test_tampering_with_a_row_breaks_the_chain_at_that_row(tmp_path):
    dm.append(tmp_path, fields())
    dm.append(tmp_path, fields(from_mode="warn", to_mode="observe", ref="ffff0000"))
    dm.append(tmp_path, fields(proposal_id=OTHER))
    path = tmp_path / dm.FILE
    lines = path.read_text().splitlines()
    lines[1] = lines[1].replace('"observe"', '"disabled"')
    path.write_text("\n".join(lines) + "\n")
    rows, broken = dm.read_chain(tmp_path)
    assert broken == 1 and len(rows) == 1
    with pytest.raises(ValueError):
        dm.append(tmp_path, fields(proposal_id=OTHER, ref="9999"))          # the writer refuses a broken chain


def test_a_row_that_raises_the_mode_is_invalid_and_breaks_the_chain(tmp_path):
    forge(tmp_path, {}, {"from_mode": "observe", "to_mode": "warn", "ref": "bbbbbbbb"}, {"ref": "cccccccc"})
    rows, broken = dm.read_chain(tmp_path)
    assert len(rows) == 1 and broken == 1
    with pytest.raises(ValueError):
        dm.append(tmp_path / "other", fields(from_mode="observe", to_mode="warn"))


@pytest.mark.parametrize("bad", [{"cause": "because"}, {"to_mode": "panic"}, {"proposal_id": "nope"},
                                 {"review_required": "yes"}, {"ref": 7}])
def test_invalid_fields_are_refused_by_the_writer_and_the_reader(tmp_path, bad):
    with pytest.raises(ValueError):
        dm.append(tmp_path, fields(**bad))
    forge(tmp_path, bad)
    assert dm.read_chain(tmp_path) == ([], 0)


def test_a_repeat_of_the_newest_row_for_a_proposal_is_not_appended(tmp_path):
    a = dm.append(tmp_path, fields())
    assert dm.append(tmp_path, fields()) == a
    dm.append(tmp_path, fields(proposal_id=OTHER))                          # another proposal does not hide the repeat
    assert dm.append(tmp_path, fields()) == a and len(dm.read_chain(tmp_path)[0]) == 2
    dm.append(tmp_path, fields(from_mode="warn", ref="eeeeeeee"))            # a different ref is a new row
    assert len(dm.read_chain(tmp_path)[0]) == 3


def test_effective_cap_is_the_lowest_valid_row_of_that_proposal(tmp_path):
    dm.append(tmp_path, fields())
    dm.append(tmp_path, fields(from_mode="warn", to_mode="observe", ref="ffff0000"))
    dm.append(tmp_path, fields(proposal_id=OTHER, to_mode="disabled", cause="error_rate", ref="-"))
    rows, _ = dm.read_chain(tmp_path)
    assert (dm.effective_cap(rows, PID), dm.effective_cap(rows, OTHER), dm.effective_cap(rows, "P-22222222")) == (
        "observe", "disabled", None)


def test_lower_returns_the_lower_rung_and_disabled_is_lowest():
    assert dm.lower("block", "warn") == "warn" and dm.lower("observe", "warn") == "observe"
    assert dm.lower("warn", "disabled") == "disabled" and dm.lower("block", None) == "block"
    assert dm.lower("disabled", "block") == "disabled"


def test_a_symlink_and_a_non_json_line_are_handled(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_text("")
    (tmp_path / dm.FILE).symlink_to(real)
    with pytest.raises(OSError):
        dm.read_chain(tmp_path)
    with pytest.raises(OSError):
        dm.append(tmp_path, fields())
    (tmp_path / dm.FILE).unlink()
    (tmp_path / dm.FILE).write_bytes(b"\xff\xfe not json\n")
    assert dm.read_chain(tmp_path) == ([], 0)
    (tmp_path / dm.FILE).write_text('{"a": NaN}\n')
    assert dm.read_chain(tmp_path) == ([], 0)


def test_the_reader_caps_the_file_at_1_mib_and_calls_the_cut_a_break(tmp_path):
    forge(tmp_path, {})
    with open(tmp_path / dm.FILE, "ab") as fh:
        fh.write(b" " * dm.MAX_BYTES)
    rows, broken = dm.read_chain(tmp_path)
    assert len(rows) == 1 and broken == 1
    with pytest.raises(ValueError):
        dm.append(tmp_path, fields(proposal_id=OTHER))


def test_the_file_is_created_in_a_private_directory(tmp_path):
    dm.append(tmp_path / "fresh", fields())
    assert oct((tmp_path / "fresh").stat().st_mode & 0o777) == "0o700"
    assert not os.path.islink(tmp_path / "fresh" / dm.FILE)


# ------------------------------------------------------------------ the demotion rules, through the nightly pass
CMD = cs.RM_PROMPT_CMD
D1, D2 = "2026-10-10T10:00:00Z", "2026-10-18T10:00:00Z"


def fp(w, row):
    labels.append_row(w.cfg, "fp", labels.row_id(row))


def chain(w):
    return dm.read_chain(w.data)[0]


def test_block_plus_one_fp_demotes_to_warn_and_names_the_label_that_caused_it(tmp_path):
    w = cs.offline_world(tmp_path, "block")
    row = cs.put_shadow(w, "s1", CMD, "block", D2)
    fp(w, row)
    out = offline.run(w.cfg, now=cs.NOW)
    (d,) = chain(w)
    assert out["demotions"] == 1 and d["ts"] == "2026-10-19T12:00:00Z" and d["proposal_id"] == w.pid
    assert (d["from_mode"], d["to_mode"], d["cause"], d["ref"], d["review_required"]) == (
        "block", "warn", "label_fp", labels.row_id(row), False)


def test_warn_plus_one_fp_demotes_to_observe_and_observe_stays(tmp_path):
    w = cs.offline_world(tmp_path, "warn")
    fp(w, cs.put_shadow(w, "s1", CMD, "warn", D2))
    offline.run(w.cfg, now=cs.NOW)
    assert [(d["from_mode"], d["to_mode"], d["cause"]) for d in chain(w)] == [("warn", "observe", "label_fp")]
    other = cs.offline_world(tmp_path / "o", "observe")
    fp(other, cs.put_shadow(other, "s1", CMD, "observe", D2))
    assert offline.run(other.cfg, now=cs.NOW)["demotions"] == 0 and chain(other) == []


@pytest.mark.parametrize("mode", ["block", "warn", "observe"])
def test_two_fps_within_30_days_go_to_observe_with_review_required_in_any_mode(tmp_path, mode):
    w = cs.offline_world(tmp_path, mode)
    fp(w, cs.put_shadow(w, "s1", CMD, mode, D1))
    newer = cs.put_shadow(w, "s2", CMD, mode, D2)
    fp(w, newer)
    offline.run(w.cfg, now=cs.NOW)
    (d,) = chain(w)
    assert (d["from_mode"], d["to_mode"], d["cause"], d["review_required"], d["ref"]) == (
        mode, "observe", "label_fp_pair", True, labels.row_id(newer))
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 0                  # idempotent


def test_two_fps_more_than_30_days_apart_lower_one_rung_each(tmp_path):
    w = cs.offline_world(tmp_path, "block", approved_at="2026-08-01T00:00:00Z")
    fp(w, cs.put_shadow(w, "s1", CMD, "block", "2026-08-10T10:00:00Z"))
    fp(w, cs.put_shadow(w, "s2", CMD, "warn", D2))
    offline.run(w.cfg, now=cs.NOW)
    assert [(d["from_mode"], d["to_mode"], d["cause"], d["review_required"]) for d in chain(w)] == [
        ("block", "warn", "label_fp", False), ("warn", "observe", "label_fp", False)]


def test_counters_reset_after_a_demotion(tmp_path):
    w = cs.offline_world(tmp_path, "block")
    fp(w, cs.put_shadow(w, "s1", CMD, "block", D1))
    offline.run(w.cfg, now=cs.NOW)                                           # block -> warn at NOW
    fp(w, cs.put_shadow(w, "s2", CMD, "warn", D2))                           # an FP from before the demotion: not counted
    assert offline.run(w.cfg, now=cs.NOW + 60)["demotions"] == 0
    later = cs.put_shadow(w, "s3", CMD, "warn", "2026-10-19T13:00:00Z")
    fp(w, later)
    assert offline.run(w.cfg, now=cs.NOW + 7200)["demotions"] == 1
    assert [(d["from_mode"], d["to_mode"]) for d in chain(w)] == [("block", "warn"), ("warn", "observe")]
    assert chain(w)[-1]["ref"] == labels.row_id(later)


def test_fps_before_the_approval_do_not_count(tmp_path):
    w = cs.offline_world(tmp_path, "block", approved_at="2026-10-15T00:00:00Z")
    fp(w, cs.put_shadow(w, "s1", CMD, "block", D1))
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 0


def test_an_auto_fp_demotes_but_a_human_tp_over_it_and_a_tp_candidate_do_not(tmp_path):
    steps = [{"human": "please remove /w/src"}, {"tool": "Bash", "input": {"command": CMD}}]
    w = cs.offline_world(tmp_path, "block")
    cs.put_session(w, "s1", steps)
    cs.put_shadow(w, "s1", CMD, "block", D2)
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["labels"]["auto_fp_prompt_target"] == 1 and out["demotions"] == 1 and chain(w)[0]["to_mode"] == "warn"
    h = cs.offline_world(tmp_path / "h", "block")
    cs.put_session(h, "s1", steps)
    row = cs.put_shadow(h, "s1", CMD, "block", D2)
    labels.append_row(h.cfg, "tp", labels.row_id(row))
    assert offline.run(h.cfg, now=cs.NOW)["demotions"] == 0
    c = cs.offline_world(tmp_path / "c", "observe")
    cs.put_session(c, "s1", [{"human": "tidy"}, steps[1], {"human": "you keep doing that"}])
    cs.put_shadow(c, "s1", CMD, "observe", D2)
    out = offline.run(c.cfg, now=cs.NOW)
    assert out["labels"]["auto_tp_correction"] == 1 and out["demotions"] == 0


# ------------------------------------------------------------------ disable triggers
def approve_for(w, lesson_id, mode="observe"):
    p = {**w.props["rm_rf_nonscratch"], "lesson_id": lesson_id}
    write_private(w.data / "proposals" / "approved" / (w.pid + ".json"),
                  json.dumps(shadow.approved_record(p, mode, "2026-10-01T00:00:00Z"), indent=1, sort_keys=True) + "\n")


def test_a_lesson_that_is_not_active_in_the_store_disables_the_proposal(tmp_path):
    w = cs.offline_world(tmp_path, "warn")
    log = DeltaLog(w.cfg.deltas)
    live = add_draft("Pinned global lesson about careful deletes", GLOBAL, status="active", pinned=True)
    gone = add_draft("A retired global lesson about deletes", GLOBAL, status="retired")
    log.append(live, ts="2026-10-01T00:00:00Z")
    log.append(gone, ts="2026-10-01T00:00:00Z")
    approve_for(w, live["lesson_id"], "warn")
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 0 and chain(w) == []
    approve_for(w, gone["lesson_id"], "warn")
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 1
    (d,) = chain(w)
    assert (d["from_mode"], d["to_mode"], d["cause"], d["ref"]) == ("warn", "disabled", "lesson_inactive", "-")
    assert dm.effective_cap(chain(w), w.pid) == "disabled"


def test_a_changed_detector_source_disables_the_proposal(tmp_path, monkeypatch):
    w = cs.offline_world(tmp_path, "observe")
    out = offline.run(w.cfg, now=cs.NOW)                                     # no store and an unchanged source: nothing
    assert out["demotions"] == 0 and out["lesson_check"] == "skipped (no store)"
    monkeypatch.setattr(catalogue, "_source_texts", lambda: ("an edited detector",))
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 1
    assert [(d["to_mode"], d["cause"]) for d in chain(w)] == [("disabled", "source_sha256")]


def error_log(w, errors, rows, ts="2026-10-18T10:00:00Z"):
    w.cfg.path("shadow.log").write_text("".join(f"{ts} error:rm_rf:RuntimeError\n" for _ in range(errors)) + f"{ts} skipped:x:y\n")
    for n in range(rows):
        cs.put_shadow(w, f"s{n}", f"echo {n}", ts=ts)


def test_a_hook_error_rate_above_one_percent_over_seven_days_disables_every_approved_proposal(tmp_path):
    w = cs.offline_world(tmp_path, "observe")
    error_log(w, 2, 98)                                                     # 2 of 100 = 2 %
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["demotions"] == 1 and [(d["to_mode"], d["cause"], d["ref"]) for d in chain(w)] == [("disabled", "error_rate", "-")]


def test_a_low_or_old_error_rate_disables_nothing(tmp_path):
    low = cs.offline_world(tmp_path / "low", "observe")
    error_log(low, 1, 199)                                                  # exactly 0.5 %
    assert offline.run(low.cfg, now=cs.NOW)["demotions"] == 0
    old = cs.offline_world(tmp_path / "old", "observe")
    error_log(old, 5, 5, ts="2026-10-01T10:00:00Z")                          # outside the seven days
    assert offline.run(old.cfg, now=cs.NOW)["demotions"] == 0


def test_a_disabled_proposal_gets_no_further_rows_and_a_broken_chain_is_one_error(tmp_path, monkeypatch):
    w = cs.offline_world(tmp_path, "block")
    monkeypatch.setattr(catalogue, "_source_texts", lambda: ("an edited detector",))
    offline.run(w.cfg, now=cs.NOW)
    fp(w, cs.put_shadow(w, "s1", CMD, "block", "2026-10-19T13:00:00Z"))
    assert offline.run(w.cfg, now=cs.NOW + 7200)["demotions"] == 0 and len(chain(w)) == 1
    w.cfg.path("demotions.jsonl").write_text(w.cfg.path("demotions.jsonl").read_text().replace("disabled", "warn", 1))
    assert offline.run(w.cfg, now=cs.NOW + 7200)["errors"] == 1


# ------------------------------------------------------------------ the hook honours demotions
def hook(w, monkeypatch, capsys, command=CMD):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(command)).encode())))
    assert shadow.main(["pre"], w.cfg) == 0
    out = capsys.readouterr()
    log = w.data / "shadow.log"
    return out.out, out.err, log.read_text() if log.exists() else ""


def demote_row(w, to_mode="disabled", from_mode="observe", cause="error_rate"):
    return dm.append(w.data, fields(proposal_id=w.pid, from_mode=from_mode, to_mode=to_mode, cause=cause, ref="-"))


def test_a_disabled_demotion_makes_the_hook_skip_the_proposal(tmp_path, monkeypatch, capsys):
    w = cs.offline_world(tmp_path, "observe")
    hook(w, monkeypatch, capsys)
    assert len(cs.shadow_rows(w.data)) == 1
    demote_row(w)
    _, _, log = hook(w, monkeypatch, capsys)
    assert len(cs.shadow_rows(w.data)) == 1 and f"skipped:{w.pid}:demoted" in log


def test_a_broken_demotions_chain_caps_every_proposal_at_observe_and_the_hook_still_measures(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shadow, "ALLOWED_MODES", ("observe", "warn", "block"))
    w = cs.offline_world(tmp_path, "block")
    demote_row(w, "warn", "block", "label_fp")
    path = w.data / dm.FILE
    path.write_text(path.read_text().replace("warn", "block"))              # tampered
    out, err, log = hook(w, monkeypatch, capsys)
    (row,) = cs.shadow_rows(w.data)
    assert row["mode"] == "observe" and (out, err) == ("", "") and log.count("demotions:chain_broken") == 1


def test_a_demotion_to_warn_lowers_a_block_proposal_and_a_symlinked_file_caps_at_observe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shadow, "ALLOWED_MODES", ("observe", "warn", "block"))
    w = cs.offline_world(tmp_path, "block")
    out, _, _ = hook(w, monkeypatch, capsys)
    assert cs.shadow_rows(w.data)[-1]["mode"] == "block" and '"deny"' in out
    demote_row(w, "warn", "block", "label_fp")
    out, err, _ = hook(w, monkeypatch, capsys)
    assert cs.shadow_rows(w.data)[-1]["mode"] == "warn" and err.strip() == w.props["rm_rf_nonscratch"]["message"]
    assert '"additionalContext"' in out and '"deny"' not in out     # step 5b: warn is model-visible JSON on stdout
    (w.data / dm.FILE).unlink()
    (w.data / dm.FILE).symlink_to(tmp_path / "elsewhere.jsonl")
    out, _, log = hook(w, monkeypatch, capsys)
    assert cs.shadow_rows(w.data)[-1]["mode"] == "observe" and "demotions:OSError" in log      # fail safe, not fail open


def test_the_hook_reads_the_demotions_file_once_per_call(tmp_path, monkeypatch, capsys):
    w = cs.offline_world(tmp_path, "observe")
    seen = []
    real = dm.read_chain
    monkeypatch.setattr(dm, "read_chain", lambda d: seen.append(d) or real(d))
    hook(w, monkeypatch, capsys)
    assert len(seen) == 1


@pytest.mark.parametrize("status,disabled", [("active", False), ("provisional", False), ("compiled", False), ("dormant", True),
                                             ("retired", True), ("superseded", True), ("quarantined", True)])
def test_only_a_withdrawn_lesson_status_disables_the_proposal(tmp_path, status, disabled):
    w = cs.offline_world(tmp_path, "warn")
    draft = add_draft(f"A {status} global lesson about careful deletes", GLOBAL, status=status)
    DeltaLog(w.cfg.deltas).append(draft, ts="2026-10-01T00:00:00Z")
    approve_for(w, draft["lesson_id"], "warn")
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["lesson_check"] == "ok" and out["demotions"] == (1 if disabled else 0)
    assert [d["cause"] for d in chain(w)] == (["lesson_inactive"] if disabled else [])


def test_an_evasion_pair_row_does_not_reset_the_fp_counters(tmp_path):
    w = cs.offline_world(tmp_path, "block")
    fp(w, cs.put_shadow(w, "s1", CMD, "block", D2))
    dm.append(w.data, fields(proposal_id=w.pid, from_mode="block", to_mode="block", cause="evasion_pair", ref="cafe0001",
                             review_required=True))
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 1
    assert [(d["cause"], d["to_mode"]) for d in chain(w)] == [("evasion_pair", "block"), ("label_fp", "warn")]


def test_the_cap_is_the_lowest_valid_row_and_a_row_from_a_mode_the_proposal_lost_is_refused(tmp_path):
    first = dm.append(tmp_path, fields(from_mode="block", to_mode="disabled", cause="error_rate", ref="-"))
    with pytest.raises(ValueError):
        dm.append(tmp_path, fields(from_mode="block", to_mode="block", cause="evasion_pair", ref="cafe0001", review_required=True))
    rows, _ = dm.read_chain(tmp_path)
    assert rows == [first] and dm.effective_cap(rows, PID) == "disabled"
    assert dm.effective_cap([{"proposal_id": PID, "to_mode": "disabled"}, {"proposal_id": PID, "to_mode": "block"}], PID) == "disabled"
    (tmp_path / "f").mkdir()
    forge(tmp_path / "f", {"from_mode": "block", "to_mode": "disabled", "cause": "error_rate", "ref": "-"},
          {"from_mode": "block", "to_mode": "block", "cause": "evasion_pair", "ref": "x"})
    rows, broken = dm.read_chain(tmp_path / "f")
    assert (len(rows), broken, dm.effective_cap(rows, PID)) == (1, 1, "disabled")


def test_an_unreadable_demotions_file_caps_a_block_approval_at_observe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shadow, "ALLOWED_MODES", ("observe", "warn", "block"))
    w = cs.offline_world(tmp_path, "block")
    (w.data / dm.FILE).mkdir()                                              # not a regular file
    out, err, log = hook(w, monkeypatch, capsys)
    assert cs.shadow_rows(w.data)[-1]["mode"] == "observe" and (out, err) == ("", "") and "demotions:IsADirectoryError" in log
