"""The measurement clock, one rule for every reader (docs/design/compiler.md 7.3 and 8.x): `start_of` is the measurement start that `status`
(`measurement.start`) and `seal` (`measurement_start`) both print, so the two cannot drift; `window_start` and `window_counts` are the
counting window of one proposal and the ladder's TP/FP counts inside it (and the automatic FPs among them), which `status` shows beside the verdict.

Not on the hook path (the shadow hook never imports it): stdlib, `..config` and sibling modules.
"""

from __future__ import annotations

from ..config import Config
from . import approvals, labels, ladder, modes, proposal, shadow

NEXT = {"observe": "warn", "warn": "block"}       # the next rung of the promotion ladder (7.3); block is the top


def start_of(arows: list[dict]) -> str | None:
    """The greatest, over proposal ids whose newest approvals row (chain order) is not mode `revoked`, of that proposal's newest `observe`
    row `ts`; None when no proposal qualifies. This is the ladder's restart point (7.3): a warn or block promotion appends a newer row but
    leaves the observe row where it was, a demotion to disabled lives in demotions.jsonl and removes nothing, and only a revocation
    takes a proposal out. A proposal with no `observe` row sets nothing."""
    observe = {r["proposal_id"]: r["ts"] for r in arows if r["mode"] == "observe"}
    return max((ts for pid, ts in observe.items() if approvals.newest(arows, pid)["mode"] != "revoked"), default=None)


def window_start(cfg: Config, pid: str, ring: tuple, now: str) -> str | None:
    """The proposal's counting window start as `modes.resolve` says (the hook's and the ladder's clock); None without a backed copy."""
    try:
        rec = proposal.load(shadow.approved_path(cfg, pid))
        body, _ = shadow.pending_body(rec)
    except (ValueError, OSError):
        return None
    return (modes.resolve(pid, rec, *ring, now).window_start or None) if body is not None else None


def _counted_fires(pid: str, start: str | None, now: str, fires: dict, eff: dict, mode: str):
    """(ref, kind, row, human) for each label the ladder counts for `pid` since `start`: live fires in `mode`, `ladder.effective` labels."""
    for ref, (_, lab, human) in eff.items() if start else ():
        kind, f = ladder._counted(lab, human), fires.get(ref)
        if kind and f and f["proposal_id"] == pid and f.get("tier") == "live" and f.get("mode") == mode and start <= f["ts"] <= now:
            yield ref, kind, lab, human, f


def window_counts(pid: str, start: str | None, now: str, fires: dict, eff: dict, mode: str) -> tuple[int, int]:
    """(human tps, fps) the ladder counts for `pid` since `start`: live fires in the proposal's current `mode` (the rung it stands on,
    as `ladder.check` counts them for the next one), `ladder.effective` labels."""
    kinds = [kind for _, kind, *_ in _counted_fires(pid, start, now, fires, eff, mode)]
    return kinds.count("tp"), kinds.count("fp")


def auto_fps(pid: str, start: str | None, now: str, fires: dict, eff: dict, mode: str) -> list[str]:
    """Row ids, oldest fire first, of the fires in the window (as `window_counts`) whose only label is an automatic `fp` (an offline-pass
    `reason_code` of `labels.AUTO`, no human row): the ladder counts them as FPs, the label queue does not list them, and a human
    `tp` on the row overturns them."""
    found = [(f["ts"], ref) for ref, kind, lab, human, f in _counted_fires(pid, start, now, fires, eff, mode)
             if kind == "fp" and not human and lab.get("reason_code") in labels.AUTO]
    return [ref for _, ref in sorted(found)]
