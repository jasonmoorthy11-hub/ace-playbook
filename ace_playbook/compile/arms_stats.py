"""The statistics of the arm analysis (see `arms.py`): a permutation test with a committed seed and Holm. Stdlib only, never on the hook path."""

from __future__ import annotations

import random

RUNS, ALPHA, EPS = 10_000, 0.05, 1e-12


def permutation_test(stats: dict, arm_rate: float, permutation_seed: str, n: int = RUNS) -> dict:
    """p = (1 + #{|D*| >= |D|}) / (n + 1) over `n` Bernoulli(arm_rate) re-draws of the arm labels on `stats["values"]` (in order), drawn from
    `random.Random(permutation_seed)`; a draw that leaves an arm empty counts as extreme. The interval is [D - q97.5(D*), D - q2.5(D*)]."""
    vals, d = stats["values"], stats["D"]
    if d is None:
        return {"D": None, "p": None, "ci95": None, "draws": 0, "degenerate": 0}
    rng, total, m = random.Random(permutation_seed), sum(stats["values"]), len(vals)
    hits, degenerate, ds = 0, 0, []
    for _ in range(n):
        flags = [rng.random() < arm_rate for _ in vals]
        k = sum(flags)
        if k in (0, m):
            hits, degenerate = hits + 1, degenerate + 1
            continue
        s = sum(v for v, f in zip(vals, flags) if f)
        ds.append(s / k - (total - s) / (m - k))
        hits += abs(ds[-1]) >= abs(d) - EPS
    ds.sort()
    q = lambda f: ds[min(len(ds) - 1, max(0, int(f * len(ds))))]                      # noqa: E731
    return {"D": d, "p": (1 + hits) / (n + 1), "ci95": [d - q(0.975), d - q(0.025)] if ds else None, "draws": n, "degenerate": degenerate}


def holm(pvalues: list[float | None], alpha: float = ALPHA) -> list[tuple[float, bool]]:
    """[(Holm-adjusted p, rejected at `alpha`)] in input order; a None p (not testable) counts as 1."""
    ps = [1.0 if p is None else p for p in pvalues]
    m, adj, top = len(ps), [0.0] * len(ps), 0.0
    for rank, i in enumerate(sorted(range(m), key=lambda j: ps[j])):
        top = max(top, min(1.0, (m - rank) * ps[i]))
        adj[i] = top
    return [(a, a <= alpha) for a in adj]
