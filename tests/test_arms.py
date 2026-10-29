"""The arm analysis of the pre-registration (`compile/arms.py`, `arms_stats.py`) and the blind label queue (`labels_queue.py`).

Everything is invented: session hashes, seeds, stamps. The permutation test is checked against the exact value, enumerated over every
labelling of a small world; the seeds must never appear in any output."""

import json
import subprocess
import sys
from itertools import product
from pathlib import Path

import pytest

from ace_playbook.compile import arms, arms_stats, labels, labels_queue, metrics, queue, seal
from ace_playbook.compile import state as st
from ace_playbook.compile.events import Event

from . import compile_support as cs
from .test_compile_queue import NOW, Script, fire, run, w  # noqa: F401  (fixtures and helpers)

ROOT = Path(__file__).resolve().parent.parent
SHADOW, PERMUT = "ab" * 32, "cd" * 32                              # invented private seeds; only their sha256 may appear anywhere
T0, DET = "2026-11-01T00:00:00Z", "rm_rf_nonscratch"
DUE = "2027-01-31T00:00:00Z"                                      # arm start + 84 days = the horizon end 2027-01-24, + 7 days
SCANNED = "2027-02-15T00:00:00Z"                                  # a `_session` row stamp later than every fire


def srow(h, start, prompts=2, eligible=(DET,), ts=SCANNED):
    return {"detector": "_session", "tier": "offline", "session": h, "ts": ts, "count": 5, "prompts": prompts, "eligible": list(eligible),
            "start": start}


def prereg_text(arm_line=True):
    block = f"shadow_seed_sha256 {seal._sha(SHADOW)}\npermutation_seed_sha256 {seal._sha(PERMUT)}\narm_rate 0.5\n"
    return "# test copy\n\n```text\n" + block + "```\n" + (f"\narm_start {DET} {T0}\n" if arm_line else "")


@pytest.fixture
def prereg(tmp_path, monkeypatch):
    path = tmp_path / "PREREGISTRATION.md"
    path.write_text(prereg_text())
    monkeypatch.setattr(seal, "PREREG", path)
    return path


# ------------------------------------------------------------------ assign
def test_assign_is_deterministic_and_follows_the_rate():
    arms_of = [arms.assign(f"s{n}", SHADOW, 0.5) for n in range(2000)]
    assert arms_of == [arms.assign(f"s{n}", SHADOW, 0.5) for n in range(2000)] and set(arms_of) == {arms.WARN, arms.OBSERVE}
    assert 0.45 < arms_of.count(arms.WARN) / 2000 < 0.55
    quarter = [arms.assign(f"s{n}", SHADOW, 0.25) for n in range(4000)]
    assert 0.21 < quarter.count(arms.WARN) / 4000 < 0.29


def test_assign_depends_on_the_seed_the_session_and_the_proposal():
    base = [arms.assign(f"s{n}", SHADOW, 0.5) for n in range(200)]
    assert base != [arms.assign(f"s{n}", PERMUT, 0.5) for n in range(200)]
    assert base != [arms.assign(f"s{n}", SHADOW, 0.5, "P-00000001") for n in range(200)]


def test_assign_nests_in_the_rate():
    # one coin per pair: a session in the warn arm at a low rate stays there at a higher one
    for n in range(300):
        if arms.assign(f"s{n}", SHADOW, 0.3) == arms.WARN:
            assert arms.assign(f"s{n}", SHADOW, 0.6) == arms.WARN


# ------------------------------------------------------------------ the horizon
def test_the_horizon_is_twelve_weeks_unless_the_400th_session_comes_first():
    few = [f"2026-11-0{d}T00:00:00Z" for d in range(1, 9)]
    assert arms.horizon_end(T0, few) == "2027-01-24T00:00:00Z"
    first = arms._dt(T0)
    many = [arms._fmt(first + arms.timedelta(minutes=n)) for n in range(450)]
    assert arms.horizon_end(T0, many) == "2026-11-01T06:39:01Z"            # the 400th starts at minute 399, so the window ends 1 s later
    late = [arms._fmt(first + arms.timedelta(days=90, minutes=n)) for n in range(450)]     # after the 12 weeks: they do not count
    assert arms.horizon_end(T0, late) == "2027-01-24T00:00:00Z"
    assert arms.horizon_end(T0, many[:399]) == "2027-01-24T00:00:00Z"


# ------------------------------------------------------------------ eligibility
def test_the_window_edges_are_half_open():
    end = "2026-11-08T00:00:00Z"
    ev = [srow("before", "2026-10-31T23:59:59Z"), srow("at_start", T0), srow("last_second", "2026-11-07T23:59:59Z"), srow("at_end", end)]
    got, horizon, missing = arms.eligible_sessions(ev, T0, end, DET)
    assert sorted(got) == ["at_start", "last_second"] and horizon == end and missing == 0


def test_only_interactive_sessions_that_the_detector_consumes_are_eligible():
    ev = [srow("ok", T0), srow("automated", T0, prompts=0), srow("other_detector", T0, eligible=("rm_rf",)), srow("flag", T0, prompts=True)]
    assert sorted(arms.eligible_sessions(ev, T0, "2026-12-01T00:00:00Z", DET)[0]) == ["ok"]


def test_a_session_without_a_start_is_missing_and_never_dropped_silently():
    ev = [srow("ok", T0), {**srow("old", T0), "start": ""}, {k: v for k, v in srow("older", T0).items() if k != "start"}, srow("junk", "not a time")]
    got, _, missing = arms.eligible_sessions(ev, T0, "2026-12-01T00:00:00Z", DET)
    assert sorted(got) == ["ok"] and missing == 3


def test_the_newest_summary_row_of_a_session_wins():
    ev = [srow("s", "2026-10-01T00:00:00Z"), srow("s", T0)]
    assert sorted(arms.eligible_sessions(ev, T0, "2026-12-01T00:00:00Z", DET)[0]) == ["s"]


def test_the_session_row_records_the_first_event_stamp():
    events = [Event(ts=""), Event(ts="2026-10-20T10:00:00Z"), Event(ts="2026-10-20T11:00:00Z")]
    row = metrics.session_row(events, "s1", "2026-10-20T11:00:00Z", {})
    assert row["start"] == "2026-10-20T10:00:00Z" and row["ts"] == "2026-10-20T11:00:00Z"
    assert metrics.session_row([], "s1", "-", {})["start"] == ""


# ------------------------------------------------------------------ the primary
def frow(s, n, flag=False, **kw):
    return {"ts": f"2026-12-01T00:00:{n:02d}Z", "session": s, "proposal_id": "P-00000001", "payload_sha256": f"{n:064x}", "would_fire": True,
            "cause": "detector", "agent_id_present": flag, **kw}


def test_the_primary_counts_repeat_fires_of_non_subagent_rows_per_eligible_session():
    elig = {s: srow(s, T0) for s in ("a", "b", "c", "d")}
    arm_of = {"a": arms.WARN, "b": arms.WARN, "c": arms.OBSERVE, "d": arms.OBSERVE}.get
    rows = [frow("a", 1), frow("a", 2), frow("a", 3),                      # a: 3 fires -> 2 repeats
            frow("b", 4),                                                  # b: 1 fire -> 0
            frow("c", 5), frow("c", 6), frow("c", 7), frow("c", 8), frow("c", 9),  # c: 5 -> 4
            frow("d", 10, True), frow("d", 11, True),                      # d: subagent fires only -> secondary
            frow("c", 12, True), frow("a", 13, None), {k: v for k, v in frow("a", 14).items() if k != "agent_id_present"},
            frow("a", 15, would_fire=False), frow("a", 16, cause="truncated"), frow("zz", 17)]   # none of these count
    got = arms.primary(rows, arm_of, elig)
    assert dict(zip(got["sessions"], got["values"])) == {"a": 2, "b": 0, "c": 4, "d": 0}
    assert got["n"] == {arms.WARN: 2, arms.OBSERVE: 2} and got["mean"] == {arms.WARN: 1.0, arms.OBSERVE: 2.0} and got["D"] == -1.0
    assert got["rate_ratio"] == 0.5 and got["subagent_fires"] == {arms.WARN: 0, arms.OBSERVE: 3} and got["fires_without_agent_flag"] == 2
    assert got["sessions_with_fire"][arms.WARN] == {"k": 2, "n": 2, "wilson95": metrics.wilson(2, 2)}
    assert got["sessions_with_fire"][arms.OBSERVE]["k"] == 1


def test_a_fire_after_the_sessions_scan_waits_and_a_zero_arm_mean_has_no_ratio():
    elig = {"a": srow("a", T0, ts="2026-12-01T00:00:00Z"), "b": srow("b", T0)}
    got = arms.primary([frow("a", 5, ts="2026-12-02T00:00:00Z"), frow("a", 6, ts="2026-12-02T00:00:00Z")],
                       {"a": arms.WARN, "b": arms.OBSERVE}.get, elig)
    assert got["values"] == [0, 0] and got["mean"] == {arms.WARN: 0.0, arms.OBSERVE: 0.0} and got["D"] == 0.0
    assert got["rate_ratio"] == metrics.DASH                                # 0 / 0 is not a ratio


def test_an_empty_arm_gives_no_effect():
    got = arms.primary([], lambda s: arms.WARN, {"a": srow("a", T0)})
    assert got["D"] is None and got["mean"][arms.OBSERVE] is None and got["rate_ratio"] == metrics.DASH
    assert arms.permutation_test(got, 0.5, PERMUT)["p"] is None


# ------------------------------------------------------------------ the permutation test
def exact_p(values, warn, rate):
    """P(|D*| >= |D|) over every Bernoulli(rate) labelling of the sessions; a labelling with an empty arm counts as extreme."""
    total, n = sum(values), len(values)
    d = sum(v for v, f in zip(values, warn) if f) / sum(warn) - sum(v for v, f in zip(values, warn) if not f) / (n - sum(warn))
    out = 0.0
    for flags in product((True, False), repeat=n):
        k = sum(flags)
        weight = rate ** k * (1 - rate) ** (n - k)
        if k in (0, n):
            out += weight
            continue
        s = sum(v for v, f in zip(values, flags) if f)
        out += weight * (abs(s / k - (total - s) / (n - k)) >= abs(d) - 1e-12)
    return d, out


WORLD = {"values": [0, 0, 1, 0, 0, 0, 2, 3, 1, 0, 4, 2], "warn": [True] * 6 + [False] * 6}      # warn mean 1/6, observe mean 2: D = -11/6


@pytest.mark.parametrize("rate", [0.5, 0.3])
def test_the_p_value_matches_the_exact_value_over_every_labelling(rate):
    d, exact = exact_p(WORLD["values"], WORLD["warn"], rate)
    assert d == pytest.approx(-11 / 6)
    got = arms.permutation_test({**WORLD, "D": d}, rate, PERMUT)
    assert 0.0 < exact < 0.2 and abs(got["p"] - exact) <= 0.02 and got["draws"] == 10_000
    assert got["p"] == (1 + round(got["p"] * 10_001 - 1)) / 10_001            # the p formula: (1 + hits) / 10,001
    assert got["ci95"][0] <= d <= got["ci95"][1]


def test_the_test_is_deterministic_under_the_committed_seed_and_changes_with_it():
    stats = {**WORLD, "D": -11 / 6}
    first = arms.permutation_test(stats, 0.5, PERMUT)
    assert first == arms.permutation_test(stats, 0.5, PERMUT) and first != arms.permutation_test(stats, 0.5, SHADOW)
    assert arms.permutation_test(stats, 0.5, PERMUT, n=50)["draws"] == 50


def test_no_difference_gives_p_one_and_a_clean_separation_gives_the_smallest_p():
    flat = {"values": [0.0] * 10, "warn": [True] * 5 + [False] * 5, "D": 0.0}
    assert arms.permutation_test(flat, 0.5, PERMUT)["p"] == 1.0
    split = {"values": [0] * 20 + [5] * 20, "warn": [True] * 20 + [False] * 20, "D": -5.0}
    assert arms.permutation_test(split, 0.5, PERMUT)["p"] == 1 / 10_001


def test_arms_stats_exports_are_the_ones_arms_uses():
    assert arms.permutation_test is arms_stats.permutation_test and arms.holm is arms_stats.holm and arms.RUNS == 10_000


# ------------------------------------------------------------------ Holm
def test_holm_on_a_known_vector():
    # sorted 0.005, 0.01, 0.03, 0.04 -> 4*0.005 = 0.02, 3*0.01 = 0.03, 2*0.03 = 0.06, max(0.06, 1*0.04) = 0.06
    got = arms.holm([0.01, 0.04, 0.03, 0.005])
    assert [round(a, 10) for a, _ in got] == [0.03, 0.06, 0.06, 0.02] and [r for _, r in got] == [True, False, False, True]


def test_holm_boundaries_and_untestable_detectors():
    assert [r for _, r in arms.holm([0.04, 0.04])] == [False, False]
    assert [r for _, r in arms.holm([0.001, 0.04])] == [True, True]            # 0.002 and max(0.002, 0.04)
    assert arms.holm([0.5, None])[1] == (1.0, False) and arms.holm([0.9])[0] == (0.9, False) and arms.holm([]) == []
    assert arms.holm([0.03], alpha=0.02) == [(0.03, False)]


# ------------------------------------------------------------------ the whole report, on a synthetic world with known arms
def world(w, n=20):
    """`n` interactive sessions started in the window. A session in the warn arm fires once (0 repeats), one in the observe arm four times
    (3 repeats): D is exactly -3 whatever the split."""
    w.cfg.shadow_seed, w.cfg.permutation_seed, w.cfg.arm_rate = SHADOW, PERMUT, 0.5
    path, k = w.cfg.path("detector_events.jsonl"), 0
    for i in range(n):
        h = st.session_hash(f"sess-{i}")
        cs.append_jsonl(path, srow(h, f"2026-11-{1 + i % 20:02d}T10:00:00Z"))
        for _ in range(1 if arms.assign(h, SHADOW, 0.5, w.pid) == arms.WARN else 4):
            k += 1
            cs.append_jsonl(w.cfg.path("shadow.jsonl"), {"ts": f"2026-11-{1 + i % 20:02d}T11:00:00Z", "proposal_id": w.pid, "lesson_id": cs.RM_LESSON,
                                                         "mode": "observe", "event": "PreToolUse", "tool_name": "Bash", "session": h,
                                                         "would_fire": True, "cause": "detector", "tier": "live", "payload_sha256": f"{k:064x}",
                                                         "agent_id_present": False})
    return w


def test_the_report_before_the_analysis_day_shows_counts_and_no_effect(w, prereg):
    world(w)
    d = arms.analyse(w.cfg, now="2027-01-30T00:00:00Z")
    (v,) = d["detectors"].values()
    assert v["final"] is False and v["eligible"] == 20 and v["analysis_due"] == DUE and "test" not in v and d["holm"] == {}
    assert "no effect is shown" in arms.render(d)


def test_the_report_on_the_analysis_day_gives_d_p_the_interval_and_holm(w, prereg):
    world(w)
    d = arms.analyse(w.cfg, now="2027-02-01T00:00:00Z")
    (v,) = d["detectors"].values()
    n = v["n"]
    assert v["final"] and v["eligible"] == 20 and n[arms.WARN] + n[arms.OBSERVE] == 20 and min(n.values()) >= 3
    assert v["mean"] == {arms.WARN: 0.0, arms.OBSERVE: 3.0} and v["test"]["D"] == -3.0 and v["rate_ratio"] == 0.0
    assert v["test"]["p"] < 0.01 and v["test"]["ci95"][0] <= -3.0 <= v["test"]["ci95"][1]
    (h,) = d["holm"].values()
    assert h["adjusted_p"] == v["test"]["p"] and h["reject"] is True and v["missing_start"] == 0 and v["fires_awaiting_scan"] == 0
    assert "holm-adjusted p" in arms.render(d)


def test_a_session_outside_the_window_or_without_a_start_is_not_in_the_analysis(w, prereg):
    world(w, n=6)
    path = w.cfg.path("detector_events.jsonl")
    cs.append_jsonl(path, srow(st.session_hash("early"), "2026-10-31T23:59:59Z"))
    cs.append_jsonl(path, srow(st.session_hash("auto"), T0, prompts=0))
    cs.append_jsonl(path, {k: v for k, v in srow(st.session_hash("old"), T0).items() if k != "start"})
    v = next(iter(arms.analyse(w.cfg, now="2027-02-01T00:00:00Z")["detectors"].values()))
    assert v["eligible"] == 6 and v["missing_start"] == 1


def test_the_seeds_never_appear_in_any_output(w, prereg, tmp_path, capsys):
    world(w)
    d = arms.analyse(w.cfg, now="2027-02-01T00:00:00Z")
    text = arms.render(d) + json.dumps(d)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(w.data), "shadow_seed": SHADOW, "permutation_seed": PERMUT, "arm_rate": 0.5}))
    for argv in (["--config", str(conf), "report"], ["--config", str(conf), "report", "--json"]):
        capsys.readouterr()
        assert arms.main(argv) == 0
        text += "".join(capsys.readouterr())
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"data_dir": str(w.data), "shadow_seed": PERMUT, "permutation_seed": SHADOW, "arm_rate": 0.5}))   # swapped: no match
    assert arms.main(["--config", str(bad), "report"]) == 1
    text += "".join(capsys.readouterr())
    assert "does not match" in text and SHADOW not in text and PERMUT not in text and seal._sha(SHADOW) not in text


def test_unset_seeds_are_refused_once_the_arm_phase_has_started(w, prereg, capsys):
    world(w)
    for key in ("shadow_seed", "permutation_seed", "arm_rate"):
        setattr(w.cfg, key, None)
        with pytest.raises(labels.Refusal):
            arms.analyse(w.cfg)
        w.cfg.shadow_seed, w.cfg.permutation_seed, w.cfg.arm_rate = SHADOW, PERMUT, 0.5


# ------------------------------------------------------------------ the CLI
def test_the_report_says_not_started_until_an_arm_start_line_exists(w, tmp_path, capsys, monkeypatch):
    path = tmp_path / "PREREGISTRATION.md"
    path.write_text(prereg_text(arm_line=False))
    monkeypatch.setattr(seal, "PREREG", path)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(w.data)}))
    assert arms.main(["--config", str(conf), "report"]) == 0 and capsys.readouterr().out == "not started\n"
    assert arms.main(["--config", str(conf), "report", "--json"]) == 0 and json.loads(capsys.readouterr().out) == {"status": "not started"}
    path.write_text(prereg_text() + f"arm_start {DET} 2027-01-01T00:00:00Z\n")                      # a second line for a detector does not win
    assert arms.arm_starts(path.read_text()) == {DET: T0}


def test_the_module_runs_as_a_command_and_says_not_started_on_the_repository_text(tmp_path):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data")}))
    out = subprocess.run([sys.executable, "-m", "ace_playbook.compile.arms", "--config", str(conf), "report"], cwd=ROOT, capture_output=True, text=True)
    assert (out.returncode, out.stdout, out.stderr) == (0, "not started\n", "")


# ------------------------------------------------------------------ the blind label queue
def test_the_queue_and_the_rows_listing_hide_the_mode_during_an_arm_phase(w, prereg, capsys):
    prereg.write_text(prereg_text().replace(T0, "2026-10-01T00:00:00Z"))                 # the phase is running at NOW (2026-10-20)
    fire(w, 0)
    assert labels_queue.blind(NOW) and queue.pending(w.cfg, 50, NOW)[0]["mode"] == "hidden"
    assert run(w, Script("q")) == 0
    out = capsys.readouterr().out
    assert "rm_rf_nonscratch@" in out and "mode" not in out and "observe" not in out and "warn" not in out and "arm" not in out
    assert labels._rows(w.cfg, 5) == 0
    listing = capsys.readouterr().out.split()
    assert listing[-1] == "hidden" and "observe" not in listing


def test_the_mode_shows_again_before_the_phase_and_after_its_window(w, prereg, capsys):
    fire(w, 0)
    for start in ("2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z"):                        # not yet started; long over (the queue run uses the real clock)
        prereg.write_text(prereg_text().replace(T0, start))
        assert not labels_queue.blind(NOW) and queue.pending(w.cfg, 50, NOW)[0]["mode"] == "observe"
        assert run(w, Script("q")) == 0 and "mode observe" in capsys.readouterr().out
    prereg.write_text(prereg_text(arm_line=False))
    assert labels_queue.mode_shown("warn", NOW) == "warn"


def test_labels_py_stays_within_its_cap_and_the_new_modules_are_small():
    root = ROOT / "ace_playbook" / "compile"
    sizes = {n: len((root / n).read_text().splitlines()) for n in ("labels.py", "labels_queue.py", "arms.py", "arms_stats.py")}
    assert sizes["labels.py"] <= 260 and all(sizes[n] <= 200 for n in ("labels_queue.py", "arms.py", "arms_stats.py")), sizes
