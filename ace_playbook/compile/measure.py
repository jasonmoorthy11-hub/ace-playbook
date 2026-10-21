"""The measurement start, one rule for every reader (docs/design/compiler.md 7.3 and 8.x): `status` (`measurement.start`) and `seal`
(`measurement_start`) both call `start_of`, so the two cannot drift.

Pure, over the rows of `approvals.jsonl` in chain order. Not on the hook path (the shadow hook never imports it): stdlib and `.approvals`.
"""

from __future__ import annotations

from . import approvals


def start_of(arows: list[dict]) -> str | None:
    """The greatest, over proposal ids whose newest approvals row (chain order) is not mode `revoked`, of that proposal's newest `observe`
    row `ts`; None when no proposal qualifies. This is the ladder's restart point (7.3): a warn or block promotion appends a newer row but
    leaves the observe row where it was, a demotion to disabled lives in demotions.jsonl and removes nothing, and only a revocation
    takes a proposal out. A proposal with no `observe` row sets nothing."""
    observe = {r["proposal_id"]: r["ts"] for r in arows if r["mode"] == "observe"}
    return max((ts for pid, ts in observe.items() if approvals.newest(arows, pid)["mode"] != "revoked"), default=None)
