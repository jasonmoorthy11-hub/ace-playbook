"""Track H wave H2: the phase marker (compile/phase.py), the symmetric correction rule, human-only demotion and the `ACE P-` minting
freeze (PREREGISTRATION.md, Arm assignment and Pre-arm-start conditions (3), (4), (7)). The hook-side silence is in test_shadow_arm.py."""

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.canon import record_hash
from ace_playbook.compile import labels, offline, phase
from ace_playbook.compile import demotions as dm
from ace_playbook.compile.events import events_from_records, hash16
from ace_playbook.inject.ledger import read_jsonl
from ace_playbook.reflect import ReflectorClient, reflect_transcript
from ace_playbook.store import DeltaLog

from . import compile_support as cs
from .jsonl import assistant, tool_result, tool_use, user, write
from .test_reflect import INSIGHTS, FakeTransport, api

CMD, DET = cs.RM_PROMPT_CMD, "rm_rf_nonscratch"
RM = {"tool": "Bash", "input": {"command": CMD}}
STARTED = "2026-10-01T00:00:00Z"                     # before cs.NOW (2026-10-19T12:00:00Z)
D1, D2 = "2026-10-10T10:00:00Z", "2026-10-18T10:00:00Z"
ROOT = Path(__file__).resolve().parent.parent


TS = "2026-09-30T00:00:00Z"                           # the signing time of the fixture markers


def mark(data, **detectors):
    """A marker signed the way the arm-start verb signs it (an unkeyed install: the plain record hash)."""
    phase.write(SimpleNamespace(data_dir=data), detectors, TS)


def cfg_of(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


# ------------------------------------------------------------------ the marker
def test_a_missing_marker_is_no_phase_and_a_good_one_is_read_back(tmp_path):
    assert phase.started(cfg_of(tmp_path)) == {} and phase.active(cfg_of(tmp_path), "2030-01-01T00:00:00Z") == frozenset()
    mark(tmp_path, a=STARTED, b="2026-12-01T00:00:00Z")
    assert phase.started(cfg_of(tmp_path)) == {"a": STARTED, "b": "2026-12-01T00:00:00Z"}


def test_active_is_the_detectors_whose_stamp_is_at_or_before_now(tmp_path):
    mark(tmp_path, a=STARTED, b="2026-12-01T00:00:00Z")
    c = cfg_of(tmp_path)
    assert phase.active(c, "2026-09-30T23:59:59Z") == frozenset()
    assert phase.active(c, STARTED) == {"a"} and phase.active(c, "2026-11-30T00:00:00Z") == {"a"}
    assert phase.active(c, "2026-12-01T00:00:00Z") == {"a", "b"}


@pytest.mark.parametrize("body", ["[]", '"x"', "null", "{", "", '{"a": 5}', '{"a": "2026-10-01"}', '{"a": "2026-10-01T00:00:00+00:00"}',
                                  '{"": "2026-10-01T00:00:00Z"}', '{"a": "2026-10-01T00:00:00Z", "b": "soon"}', "[" * 100000])
def test_a_malformed_marker_is_no_phase_and_raises_nothing(tmp_path, body):
    (tmp_path / phase.FILE).write_text(body)
    assert phase.started(cfg_of(tmp_path)) == {} and phase.active(cfg_of(tmp_path), "2099-01-01T00:00:00Z") == frozenset()


def test_a_directory_a_symlink_a_fifo_and_an_oversize_file_are_no_phase(tmp_path):
    c = cfg_of(tmp_path)
    (tmp_path / phase.FILE).mkdir()
    assert phase.started(c) == {}
    (tmp_path / phase.FILE).rmdir()
    mark(tmp_path, a=STARTED)
    real = tmp_path / "real.json"
    (tmp_path / phase.FILE).rename(real)
    (tmp_path / phase.FILE).symlink_to(real)
    assert phase.started(c) == {}
    (tmp_path / phase.FILE).unlink()
    os.mkfifo(tmp_path / phase.FILE)
    assert phase.started(c) == {}
    (tmp_path / phase.FILE).unlink()
    mark(tmp_path, a=STARTED)
    assert phase.started(c) == {"a": STARTED}
    with open(tmp_path / phase.FILE, "a") as fh:                      # still the same signed JSON, now over the size cap
        fh.write(" " * phase.MAX_BYTES)
    assert phase.started(c) == {}


K1, K2 = bytes(range(32)), bytes(range(1, 33))


def keyed_cfg(tmp_path, key=K1):
    path = tmp_path / "chain_key"
    path.write_text(key.hex())
    path.chmod(0o600)
    return SimpleNamespace(data_dir=tmp_path, chain_key_path=path)


def put(tmp_path, body):
    (tmp_path / phase.FILE).write_text(body if isinstance(body, str) else json.dumps(body))


def test_a_missing_marker_is_not_unverified_and_a_present_bad_one_is(tmp_path):
    assert phase.load(cfg_of(tmp_path)) == ({}, False)
    put(tmp_path, "{")
    assert phase.load(cfg_of(tmp_path)) == ({}, True)
    (tmp_path / "x").mkdir()
    mark(tmp_path / "x", a=STARTED)
    assert phase.load(cfg_of(tmp_path / "x")) == ({"a": STARTED}, False)


def test_the_verb_marker_is_a_sealed_object_and_merges_what_was_there(tmp_path):
    c = cfg_of(tmp_path)
    phase.write(c, {"a": STARTED}, TS)
    phase.write(c, {"b": "2026-12-01T00:00:00Z"}, "2026-10-30T00:00:00Z")
    raw = json.loads((tmp_path / phase.FILE).read_text())
    assert set(raw) == {"v", "started", "ts", "hash"} and raw["ts"] == "2026-10-30T00:00:00Z" and raw["v"] == 1
    assert raw["started"] == {"a": STARTED, "b": "2026-12-01T00:00:00Z"} and phase.started(c) == raw["started"]
    assert (tmp_path / phase.FILE).stat().st_mode & 0o777 == 0o600 and sorted(p.name for p in tmp_path.iterdir()) == [phase.FILE]


def test_a_keyed_install_reads_only_a_marker_signed_with_its_own_key(tmp_path):
    c = keyed_cfg(tmp_path)
    phase.write(c, {"a": STARTED}, TS)
    assert json.loads((tmp_path / phase.FILE).read_text())["v"] == 2 and phase.load(c) == ({"a": STARTED}, False)
    good = json.loads((tmp_path / phase.FILE).read_text())
    for label, body in (("unsigned", {"a": STARTED}),
                        ("no hash", {k: v for k, v in good.items() if k != "hash"}),
                        ("plain record hash (v1)", {**good, "v": 1, "hash": record_hash({**good, "v": 1})}),
                        ("a forged hash", {**good, "hash": "0" * 64}),
                        ("a stamp changed after signing", {**good, "started": {"a": "2026-10-02T00:00:00Z"}}),
                        ("a detector added after signing", {**good, "started": {**good["started"], "b": STARTED}}),
                        ("an extra key", {**good, "note": "x"}),
                        ("a wrong version", {**good, "v": True})):
        put(tmp_path, body)
        assert phase.load(c) == ({}, True), label
    put(tmp_path, good)
    assert phase.started(c) == {"a": STARTED}


def test_a_marker_signed_with_another_key_is_no_phase(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    phase.write(keyed_cfg(other, K2), {"a": STARTED}, TS)
    (tmp_path / phase.FILE).write_bytes((other / phase.FILE).read_bytes())
    assert phase.load(keyed_cfg(tmp_path, K1)) == ({}, True) and phase.load(keyed_cfg(other, K2)) == ({"a": STARTED}, False)


def test_a_keyed_install_with_an_unusable_key_reads_no_phase_and_cannot_write_one(tmp_path):
    c = keyed_cfg(tmp_path)
    phase.write(c, {"a": STARTED}, TS)
    c.chain_key_path.chmod(0o644)                               # chain_key() refuses a group/other readable key: keyed, no usable key
    assert phase.load(c) == ({}, True)
    with pytest.raises(ValueError, match="chain key"):
        phase.write(c, {"b": STARTED}, TS)


def test_an_unkeyed_install_holds_the_marker_to_the_plain_hash_and_refuses_a_v2_one(tmp_path):
    c = cfg_of(tmp_path)
    mark(tmp_path, a=STARTED)
    good = json.loads((tmp_path / phase.FILE).read_text())
    for body in ({"a": STARTED}, {**good, "started": {"a": "2026-10-02T00:00:00Z"}}, {**good, "v": 2}, {**good, "hash": "1" * 64}):
        put(tmp_path, body)
        assert phase.load(c) == ({}, True)


# ------------------------------------------------------------------ symmetric corrections (offline.classify)
def evs(steps):
    return list(events_from_records(cs.records(steps)))


def rule(mode, steps, phase_on, key=hash16("/w/src")):
    e = evs(steps)
    i = next(n for n, x in enumerate(e) if x.tool_uses)
    return offline.classify(mode, e, i, e[i].tool_uses[0], key, phase_on)


CORRECTED = [{"human": "tidy"}, RM, {"human": "I told you not to delete that again"}]


@pytest.mark.parametrize("mode,expected", [("warn", "auto_fp_correction"), ("block", "auto_fp_correction"), ("observe", "auto_tp_correction")])
def test_classify_labels_a_correction_outside_a_phase_and_nothing_inside_one(mode, expected):
    assert rule(mode, CORRECTED, False) == expected and rule(mode, CORRECTED, True) is None
    assert offline.classify(mode, *(lambda e: (e, 1, e[1].tool_uses[0], hash16("/w/src")))(evs(CORRECTED))) == expected      # phase defaults off


def test_the_other_three_rules_are_unchanged_inside_a_phase():
    assert rule("observe", [{"human": "please remove /w/src"}, RM], True) == "auto_fp_prompt_target"
    tidy = [{"human": "tidy"}, RM, {"human": "<bash-input>rm -rf /w/src</bash-input>"}, {"human": "thanks"}]
    assert rule("block", tidy, True) == "auto_fp_shell_rerun"
    guard = "PreToolUse:Bash hook error: [$HOME/.claude/hooks/guard-destructive.sh]: refusing a recursive delete"
    assert rule("observe", [{"human": "tidy"}, {**RM, "result": guard, "error": True}], True) == "auto_tp_hook_block"


def label_rows(w):
    p = w.cfg.path("labels.jsonl")
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.mark.parametrize("mode,reason", [("warn", "auto_fp_correction"), ("observe", "auto_tp_correction")])
def test_run_labels_the_correction_without_a_marker_and_counts_a_neutral_flag_with_one(tmp_path, mode, reason):
    plain, phased = cs.offline_world(tmp_path / "a", mode), cs.offline_world(tmp_path / "b", mode)
    mark(phased.data, **{DET: STARTED})
    for w in (plain, phased):
        cs.put_session(w, "s1", CORRECTED)
        cs.put_shadow(w, "s1", CMD, mode)
    out_plain, out_phased = offline.run(plain.cfg, now=cs.NOW), offline.run(phased.cfg, now=cs.NOW)
    assert [r["reason_code"] for r in label_rows(plain)] == [reason] and out_plain["neutral_corrections"] == 0
    assert label_rows(phased) == [] and out_phased["labels"][reason] == 0 and out_phased["neutral_corrections"] == 1
    assert out_phased["errors"] == 0


def test_run_still_labels_a_prompt_target_in_a_phase_and_counts_no_neutral_flag_for_it(tmp_path):
    w = cs.offline_world(tmp_path, "warn")
    mark(w.data, **{DET: STARTED})
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM, {"human": "I told you not to delete that again"}])
    cs.put_shadow(w, "s1", CMD, "warn")
    out = offline.run(w.cfg, now=cs.NOW)
    assert [r["reason_code"] for r in label_rows(w)] == ["auto_fp_prompt_target"] and out["neutral_corrections"] == 0


def test_a_marker_that_starts_after_the_scan_or_for_another_detector_changes_nothing(tmp_path):
    for i, marker in enumerate(({DET: "2026-12-01T00:00:00Z"}, {"user_correction": STARTED})):
        w = cs.offline_world(tmp_path / str(i), "warn")
        mark(w.data, **marker)
        cs.put_session(w, "s1", CORRECTED)
        cs.put_shadow(w, "s1", CMD, "warn")
        out = offline.run(w.cfg, now=cs.NOW)
        assert [r["reason_code"] for r in label_rows(w)] == ["auto_fp_correction"] and out["neutral_corrections"] == 0


# ------------------------------------------------------------------ human-only demotion in a phase
def auto_fp(w, row):
    labels.append_row(w.cfg, "fp", labels.row_id(row), None, reason_code="auto_fp_correction")


def human_fp(w, row):
    labels.append_row(w.cfg, "fp", labels.row_id(row))


def chain(w):
    return dm.read_chain(w.data)[0]


def two_rows(w):
    return cs.put_shadow(w, "s1", CMD, "warn", D1), cs.put_shadow(w, "s2", CMD, "warn", D2)


def test_two_auto_fps_demote_without_a_marker_and_not_with_one(tmp_path):
    plain, phased = cs.offline_world(tmp_path / "a", "warn"), cs.offline_world(tmp_path / "b", "warn")
    mark(phased.data, **{DET: STARTED})
    for w in (plain, phased):
        for row in two_rows(w):
            auto_fp(w, row)
    assert offline.run(plain.cfg, now=cs.NOW)["demotions"] == 1 and [d["cause"] for d in chain(plain)] == ["label_fp_pair"]
    assert offline.run(phased.cfg, now=cs.NOW)["demotions"] == 0 and chain(phased) == []


def test_two_human_fps_still_demote_in_a_phase(tmp_path):
    w = cs.offline_world(tmp_path, "warn")
    mark(w.data, **{DET: STARTED})
    first, newer = two_rows(w)
    human_fp(w, first)
    human_fp(w, newer)
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 1
    (d,) = chain(w)
    assert (d["cause"], d["to_mode"], d["review_required"], d["ref"]) == ("label_fp_pair", "observe", True, labels.row_id(newer))


def test_one_auto_fp_lowers_nothing_in_a_phase_and_one_human_fp_does(tmp_path):
    auto, human = cs.offline_world(tmp_path / "a", "warn"), cs.offline_world(tmp_path / "b", "warn")
    for w in (auto, human):
        mark(w.data, **{DET: STARTED})
    auto_fp(auto, cs.put_shadow(auto, "s1", CMD, "warn", D2))
    human_fp(human, cs.put_shadow(human, "s1", CMD, "warn", D2))
    assert offline.run(auto.cfg, now=cs.NOW)["demotions"] == 0 and chain(auto) == []
    assert offline.run(human.cfg, now=cs.NOW)["demotions"] == 1 and [d["cause"] for d in chain(human)] == ["label_fp"]


def test_a_newer_human_tp_beats_an_older_auto_fp_and_an_older_human_fp_is_beaten_by_a_newer_human_tp(tmp_path):
    w = cs.offline_world(tmp_path, "warn")
    mark(w.data, **{DET: STARTED})
    first, newer = two_rows(w)
    human_fp(w, first)
    labels.append_row(w.cfg, "tp", labels.row_id(first))             # the newest human row of the ref decides
    auto_fp(w, newer)
    assert offline.run(w.cfg, now=cs.NOW)["demotions"] == 0


# ------------------------------------------------------------------ the minting freeze
@pytest.fixture
def rcfg(tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("sk-" + "ant-" + "FAKEFAKEFAKEFAKEFAKEFAKE1234\n")
    (tmp_path / "data").mkdir()
    return cfgmod.Config(data_dir=tmp_path / "data", api_key_path=key, holdout_enabled=False)


def transcript(tmp_path, tagged: bool):
    recs = [user("Please always run uv sync before pytest in this repo"), assistant("On it.", tool_use("w1", "Bash", command="ls")),
            tool_result("w1", "ok"), user("No, don't use pip here. I said use uv for everything"), assistant("Done.")]
    if tagged:
        recs.insert(1, assistant("ACE P-3f9a0c12: do not rm -rf outside scratch"))
    return write(tmp_path / "sess-abc.jsonl", recs)


def reflect(rcfg, tmp_path, tagged):
    t = FakeTransport(api(json.dumps({"insights": INSIGHTS["insights"][:1], "tags": []})))
    res = reflect_transcript(rcfg, transcript(tmp_path, tagged), ReflectorClient(rcfg, transport=t, sleep=lambda s: None))
    return res, t


def minted(rcfg):
    return [s["text"] for s in DeltaLog(rcfg.deltas).state().values()] if rcfg.deltas.exists() else []


def test_an_ace_p_transcript_mints_nothing_while_a_phase_runs(rcfg, tmp_path):
    mark(rcfg.data_dir, **{DET: STARTED})
    res, t = reflect(rcfg, tmp_path, True)
    assert res["status"] == "frozen_arm_phase" and t.calls == [] and minted(rcfg) == [] and not rcfg.deltas.exists()
    assert not rcfg.reflections.exists() and "status=frozen_arm_phase" in rcfg.log.read_text()      # no ledger row: the freeze defers, it does not close


def test_a_frozen_transcript_is_minted_by_the_next_run_once_the_marker_is_gone(rcfg, tmp_path):
    from ace_playbook import sweep
    mark(rcfg.data_dir, **{DET: STARTED})
    res, t = reflect(rcfg, tmp_path, True)
    assert res["status"] == "frozen_arm_phase" and sweep.reflected_ids(rcfg) == set()              # the nightly sweep still lists it
    again, _ = reflect(rcfg, tmp_path, True)
    assert again["status"] == "frozen_arm_phase"                                                    # still in the phase: still frozen, still open
    (rcfg.data_dir / phase.FILE).unlink()                                                           # the phase ended
    res, t = reflect(rcfg, tmp_path, True)
    assert res["status"] == "ok" and len(t.calls) == 1 and minted(rcfg) == ["Run uv sync before pytest in this repo"]
    assert [r["status"] for r in read_jsonl(rcfg.reflections)] == ["ok"]


def test_the_same_transcript_mints_without_a_marker(rcfg, tmp_path):
    res, t = reflect(rcfg, tmp_path, True)
    assert res["status"] == "ok" and len(t.calls) == 1 and minted(rcfg) == ["Run uv sync before pytest in this repo"]


def sweep_world(rcfg, tmp_path):
    """Three idle transcripts under the root, the two frozen ones OLDEST (the sweep takes the oldest first); two carry `ACE P-`."""
    import time
    rcfg.transcripts_root = tmp_path / "projects"
    rcfg.transcripts_root.mkdir()
    now = time.time()
    for n, (name, tagged) in enumerate((("fa", True), ("fb", True), ("clean", False))):
        recs = [user(f"message {i} for {name} " + "pad " * 40, sessionId=name) for i in range(8)]
        recs[1] = assistant(("ACE P-3f9a0c12: do not rm -rf outside scratch " if tagged else "ok ") * 30)
        p = write(rcfg.transcripts_root / f"{name}.jsonl", recs)
        os.utime(p, (now - 9000 + n, now - 9000 + n))
    t = FakeTransport(*[api('{"insights":[],"tags":[]}')] * 6)
    return ReflectorClient(rcfg, transport=t, sleep=lambda s: None), t


def test_frozen_transcripts_do_not_consume_sweep_slots_and_are_counted(rcfg, tmp_path):
    from ace_playbook.sweep import run_sweep
    client, t = sweep_world(rcfg, tmp_path)
    mark(rcfg.data_dir, **{DET: STARTED})
    dry = run_sweep(rcfg, client, max_n=2, dry_run=True)
    assert [d["path"] for d in dry if "path" in d] == ["clean.jsonl"] and dry[-1] == {"status": "frozen_arm_phase", "count": 2}
    out = run_sweep(rcfg, client, max_n=2)                                      # the two oldest are frozen: the clean one still gets a slot
    assert [o for o in out if o.get("path")] == [{"path": "clean.jsonl", "source": "scan", "status": "ok"}]
    assert out[-1] == {"status": "frozen_arm_phase", "count": 2} and len(t.calls) == 1             # no API call for the frozen two
    assert {r["session_id"] for r in read_jsonl(rcfg.reflections)} == {"clean"}


def test_without_the_marker_all_three_are_sweep_candidates(rcfg, tmp_path):
    from ace_playbook.sweep import run_sweep
    client, t = sweep_world(rcfg, tmp_path)
    mark(rcfg.data_dir, **{DET: STARTED})
    run_sweep(rcfg, client, max_n=2)
    (rcfg.data_dir / phase.FILE).unlink()                                        # the phase ended
    out = run_sweep(rcfg, client, max_n=5)
    assert sorted(o["path"] for o in out) == ["fa.jsonl", "fb.jsonl"] and all(o["status"] == "ok" for o in out)
    assert not any(o.get("status") == "frozen_arm_phase" for o in out) and len(t.calls) == 3


def test_a_sweep_of_only_frozen_transcripts_makes_no_call_and_needs_no_key(rcfg, tmp_path):
    from ace_playbook.sweep import run_sweep
    client, t = sweep_world(rcfg, tmp_path)
    (rcfg.transcripts_root / "clean.jsonl").unlink()
    rcfg.api_key_path = None
    mark(rcfg.data_dir, **{DET: STARTED})
    assert run_sweep(rcfg, client) == [{"status": "frozen_arm_phase", "count": 2}] and t.calls == []


def test_a_marker_that_has_not_started_makes_the_sweep_output_unchanged(rcfg, tmp_path):
    from ace_playbook.sweep import run_sweep
    client, _ = sweep_world(rcfg, tmp_path)
    mark(rcfg.data_dir, **{DET: "2099-01-01T00:00:00Z"})
    out = run_sweep(rcfg, client, max_n=5)
    assert len(out) == 3 and all(o["status"] == "ok" for o in out)


def test_a_marker_that_has_not_started_freezes_nothing(rcfg, tmp_path):
    mark(rcfg.data_dir, **{DET: "2099-01-01T00:00:00Z"})
    assert reflect(rcfg, tmp_path, True)[0]["status"] == "ok"


def test_a_phase_without_ace_p_in_the_transcript_mints_as_today(rcfg, tmp_path):
    mark(rcfg.data_dir, **{DET: STARTED})
    res, t = reflect(rcfg, tmp_path, False)
    assert res["status"] == "ok" and len(t.calls) == 1 and minted(rcfg) == ["Run uv sync before pytest in this repo"]


def test_an_unreadable_or_oversize_transcript_in_a_phase_counts_as_carrying_ace_p(rcfg, tmp_path, monkeypatch):
    from ace_playbook.reflect import runner
    mark(rcfg.data_dir, **{DET: STARTED})
    monkeypatch.setattr(runner, "MAX_SCAN_BYTES", 10)
    assert runner.arm_frozen(rcfg, transcript(tmp_path, False)) is True
    monkeypatch.undo()
    assert runner.arm_frozen(rcfg, tmp_path / "missing.jsonl") is True and runner.arm_frozen(rcfg, transcript(tmp_path, False)) is False


# ------------------------------------------------------------------ no session-driven status writer exists
ALLOWED = {"store/promote.py", "store/__main__.py"}
WRITER = re.compile(r"""["']op["']\s*:\s*["'](RETIRE|PATCH|MERGE|NARROW)["']|\bop\s*=\s*["'](RETIRE|PATCH|MERGE|NARROW)["']""")


def test_no_module_outside_the_operator_tools_writes_a_retire_patch_merge_or_narrow_delta():
    """Condition (7) freezes status changes of family lessons from `ACE P-` sessions. None is written by any session-driven code today
    (store/promote.py and store/__main__.py are operator tools), so minting is the only writer to freeze. A future writer trips this."""
    hits = []
    for path in sorted((ROOT / "ace_playbook").rglob("*.py")):
        rel = path.relative_to(ROOT / "ace_playbook").as_posix()
        if rel in ALLOWED or rel.startswith("fixtures/"):
            continue
        hits += [f"{rel}:{n}" for n, line in enumerate(path.read_text().splitlines(), 1) if WRITER.search(line)]
    assert hits == []
