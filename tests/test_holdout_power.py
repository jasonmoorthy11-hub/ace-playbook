"""`compile/holdout_power.py`: the Monte Carlo power of the per-session lesson holdout. Everything is invented; the data dir is a tmp dir."""

import json
import random
import statistics
import subprocess
import sys
from math import inf
from pathlib import Path

import pytest

from ace_playbook.compile import holdout_power as hp

ROOT = Path(__file__).resolve().parent.parent
DET = "edit_unread_turn"


def sess(h, prompts=1):
    return {"detector": "_session", "tier": "offline", "session": h, "ts": "2026-11-01T00:00:00Z", "prompts": prompts, "start": "2026-11-01T00:00:00Z"}


def fire(h, n, rid, det=DET):
    return {"detector": det, "tier": "offline", "session": h, "count": n, "id": rid}


def rows_for(counts, det=DET, prompts=1):
    ses = [sess(f"s{i}", prompts) for i in range(len(counts))]
    return ses, [fire(f"s{i}", n, f"r{i}", det) for i, n in enumerate(counts) if n]


def test_lessons_are_the_three_pairs_in_order():
    assert hp.LESSONS == (("L-cbfb7a57", "preclaim_audit_block"), ("L-f8a355f9", "edit_unread_turn"), ("L-93544dae", "exitplan_question_block"))


def test_fit_recovers_the_moments_and_the_dispersion():
    ses, fires = rows_for([0, 2, 4])                         # mean 2, sample variance 4, k = mean^2 / (var - mean) = 2
    f = hp.fit(ses, fires, 1)[DET]
    assert (f["mean"], f["var"], f["k"], f["p_any"], f["n"]) == (2.0, 4.0, 2.0, 2 / 3, 3)
    counts = [0, 0, 1, 1, 2, 2, 3, 3, 4, 7]
    ses, fires = rows_for(counts)
    f = hp.fit(ses, fires, 1)[DET]
    m, v = statistics.mean(counts), statistics.variance(counts)
    assert f["mean"] == pytest.approx(m) and f["var"] == pytest.approx(v) and f["k"] == pytest.approx(m * m / (v - m)) and f["p_any"] == 0.8


def test_fit_k_is_infinite_when_the_variance_does_not_exceed_the_mean():
    ses, fires = rows_for([1, 2, 3])                         # mean 2, variance 1
    assert hp.fit(ses, fires, 1)[DET]["k"] == inf


def test_fit_dedupes_by_id_sums_rows_and_applies_min_prompts():
    ses = [sess("a", 3), sess("b", 0), sess("c", 1)]
    fires = [fire("a", 1, "x"), fire("a", 1, "x"), fire("a", 2, "y"), fire("b", 9, "z"), fire("ghost", 5, "w"), fire("c", 0, "v")]
    f = hp.fit(ses, fires, 1)[DET]
    assert f["n"] == 2 and f["mean"] == 1.5 and f["p_any"] == 0.5          # a: 1 + 2 = 3; c: 0; b has no prompts; ghost has no `_session` row
    assert hp.fit(ses, fires, 0)[DET]["n"] == 3
    assert set(hp.fit(ses, fires, 1)) == {d for _, d in hp.LESSONS}


def test_fit_with_no_sessions_is_all_zero():
    f = hp.fit([], [], 1)[DET]
    assert (f["n"], f["mean"], f["p_any"], f["k"]) == (0, 0.0, 0.0, inf)


@pytest.mark.parametrize("mean,k", [(0.6, 1.5), (2.0, inf), (0.05, 0.4)])
def test_the_count_sampler_has_the_fitted_mean(mean, k):
    rng = random.Random(7)
    xs = [hp.draw(rng, mean, k) for _ in range(20000)]
    assert statistics.mean(xs) == pytest.approx(mean, rel=0.08) and all(isinstance(x, int) and x >= 0 for x in xs)
    if k != inf:
        assert statistics.variance(xs) > statistics.mean(xs) * 0.99        # over-dispersed (gamma-Poisson), never below Poisson


def params(p=0.2, mean=0.5, k=1.0):
    return {d: {"mean": mean, "var": mean + mean * mean / k, "k": k, "p_any": p, "n": 100} for _, d in hp.LESSONS}


def power(outcome="binary", rr=1.0, n=200, sims=300, method="normal", perms=99, seed="t"):
    return hp.simulate(params(), rr, n, outcome, sims, perms, seed, method)


def test_power_is_about_alpha_when_there_is_no_effect():
    for outcome in ("binary", "count"):
        assert all(v <= 0.08 for v in power(outcome, 1.0, 300).values()), outcome


def test_power_rises_with_sessions_and_with_effect_size():
    for outcome in ("binary", "count"):
        small, big = power(outcome, 0.5, 60), power(outcome, 0.5, 600)
        weak, strong = power(outcome, 0.85, 300), power(outcome, 0.4, 300)
        for lid, _ in hp.LESSONS:
            assert small[lid] < big[lid] and weak[lid] < strong[lid], (outcome, lid)
        assert min(big.values()) > 0.9


def test_perm_and_normal_modes_agree_on_one_cell():
    a, b = power("binary", 0.3, 120, 150, "normal", seed="agree"), power("binary", 0.3, 120, 150, "perm", perms=99, seed="agree")
    assert 0.2 < min(a.values()) and all(abs(a[k] - b[k]) <= 0.1 for k in a), (a, b)


def test_simulate_is_deterministic_for_a_seed_and_differs_across_seeds():
    assert power(seed="x", sims=100) == power(seed="x", sims=100) and power(rr=0.5, seed="x", sims=100) != power(rr=0.5, seed="y", sims=100)


def test_a_detector_that_never_fires_has_zero_power():
    z = {d: {"mean": 0.0, "var": 0.0, "k": inf, "p_any": 0.0, "n": 50} for _, d in hp.LESSONS}
    assert set(hp.simulate(z, 0.5, 100, "count", 20, 9, "z").values()) == {0.0}


def test_cli_prints_the_fit_and_a_table_and_json(tmp_path):
    ses, fires = rows_for([0, 1, 0, 3, 0, 0, 2, 0, 1, 0])
    ev = tmp_path / "detector_events.jsonl"
    ev.write_text("".join(json.dumps(r) + "\n" for r in [*ses, *fires]))
    base = [sys.executable, "-m", "ace_playbook.compile.holdout_power", "--events", str(ev), "--rates", "2.0", "--weeks", "4,8", "--rr", "0.5",
            "--sims", "20", "--seed", "s"]
    out = subprocess.run(base, capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0 and "L-f8a355f9" in out.stdout and "binary" in out.stdout and "count" in out.stdout, out.stderr
    js = subprocess.run([*base, "--json"], capture_output=True, text=True, cwd=ROOT, timeout=60)
    d = json.loads(js.stdout)
    assert d["fit"][DET]["n"] == 10 and len(d["grid"]) == 3 * 2 * 1 * 2 * 1
    assert js.stdout == subprocess.run([*base, "--json"], capture_output=True, text=True, cwd=ROOT, timeout=60).stdout
