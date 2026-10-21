"""The measurement clock, one rule for every reader (docs/design/compiler.md 7.3 and 8.x): `start_of` is the measurement start that `status`
(`measurement.start`) and `seal` (`measurement_start`) both print, so the two cannot drift; `resolve` is what the mode resolver says of one
proposal (its counting window and cap), `window_counts` and `auto_fps` the ladder's TP/FP counts inside that window (and the automatic FPs
among them), which `status` shows beside the verdict; `unfired` adds the approved copies the report leaves out.

Not on the hook path (the shadow hook never imports it): stdlib, `..config` and sibling modules.
"""

from __future__ import annotations

from ..config import Config
from . import approvals, labels, ladder, metrics, modes, proposal, report, shadow

NEXT = {"observe": "warn", "warn": "block"}       # the next rung of the promotion ladder (7.3); block is the top


def start_of(arows: list[dict]) -> str | None:
    """The greatest, over proposal ids whose newest approvals row (chain order) is not mode `revoked`, of that proposal's newest `observe`
    row `ts`; None when no proposal qualifies. This is the ladder's restart point (7.3): a warn or block promotion appends a newer row but
    leaves the observe row where it was, a demotion to disabled lives in demotions.jsonl and removes nothing, and only a revocation
    takes a proposal out. A proposal with no `observe` row sets nothing."""
    observe = {r["proposal_id"]: r["ts"] for r in arows if r["mode"] == "observe"}
    return max((ts for pid, ts in observe.items() if approvals.newest(arows, pid)["mode"] != "revoked"), default=None)


def resolve(cfg: Config, pid: str, ring: tuple, now: str) -> tuple[modes.Resolution | None, str]:
    """(what `modes.resolve` says about the proposal (the hook's and the ladder's view: mode, cap, counting window), `backing`). The
    resolution is None without an approved copy (`backing` is then `none`). `backing` is `ok` only when `ladder.check` would accept the copy
    as the one the operator approved: the approvals chain is intact (`chain` if not; `ladder.run` refuses a broken one), the copy's body is the
    proposal file's (`proposal` if not: `ladder._load`'s `shadow.pending_body(rec)[0] == p`, a load error counting as a difference), and the
    newest approvals row has the copy's hash and mode (`row` if not: `approvals.verify_copy`, which also fails on no row at all, observe
    copies included, as `check`'s "copy not backed by the approvals chain" does)."""
    try:
        rec = proposal.load(shadow.approved_path(cfg, pid))
        body, _ = shadow.pending_body(rec)
    except (ValueError, OSError):
        return None, "none"
    if body is None:
        return None, "none"
    try:
        same = body == proposal.load(proposal.proposal_path(cfg, pid))
    except (ValueError, OSError):
        same = False
    backing = "chain" if ring[1] is not None else "proposal" if not same else "ok" if approvals.verify_copy(rec, ring[0]) == "ok" else "row"
    return modes.resolve(pid, rec, *ring, now), backing


def cap_of(res: modes.Resolution | None) -> tuple[str | None, str]:
    """(the demotion cap in force or None, the remedy to show beside it). A broken demotions chain caps at observe and no re-approval lifts
    that cap (`modes.resolve` fails toward observe until the chain is repaired); any other cap is lifted by `approve --mode observe`."""
    if res is None:
        return None, ""
    broken = any(pr.startswith("demotions:") for pr in res.problems)
    return res.cap, "demotions chain broken" if broken else "approve --mode observe restarts"


def not_backed(backing: str, approvals_mode: str) -> str:
    """The verdict for a copy whose `backing` (see `resolve`) is not `ok`; `approvals_mode` is the newest approvals row's mode or `none`."""
    if backing == "chain":
        return "not backed: approvals chain broken"
    if backing == "proposal":
        return "not backed: proposal file differs from the approved copy"
    return ("not backed: no approvals row (approve --mode observe records it)" if approvals_mode == "none"
            else f"not backed: newest approvals row is {approvals_mode}")


def unfired(cfg: Config, d: dict, rows: list[dict], now: str) -> tuple[dict, dict]:
    """(counts, rates) of approved copies with no shadow row, which `report.collect` leaves out; `metrics.compute` as in `collect`."""
    have, ring = set(d["private"]["proposals"]), report._chains(cfg)
    meta = {rec["id"]: report._meta(cfg, rec["id"], ring, now) for _, _, rec in shadow.approved_records(cfg) if rec["id"] not in have}
    known = {pid: m[0] for pid, m in meta.items() if m[0] != "-"}
    counts = {pid: {"detector": m[0], "approved_mode": m[2], "mode": m[3], "rows": 0, "tp": 0, "fp": 0, "unlabelled": 0}
              for pid, m in meta.items()}
    rates = metrics.compute("private", rows, known, report._read(cfg.path(report.EVENTS)), report._read(cfg.path(report.EVASION)),
                            {pid for pid, m in meta.items() if m[1] == "Stop"})["proposals"] if known else {}
    return counts, rates


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
