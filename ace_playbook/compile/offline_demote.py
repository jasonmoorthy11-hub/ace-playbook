"""The label and demotion stages of the nightly offline pass (docs/design/compiler.md 7.3), split out of offline.py for its line budget;
`offline` re-exports every name here. `write_labels` appends the auto labels (a human row always wins) and also returns the newest HUMAN
label per ref; `demote` appends design 7.3's lowering rows. During an arm phase of a detector (phase.py) the FP count of its proposals reads
the human labels only (PREREGISTRATION.md, Pre-arm-start condition (4)); `error_rate`, `lesson_inactive`, `source_sha256` and the
`evasion_pair` flag are not labels and stay as they were. Rows hold enums, ids, hashes and counts, never transcript text.
"""

from __future__ import annotations

import time
from collections import Counter

from . import catalogue, chains, chains_io, demotions, labels, modes, phase, report, shadow

DAY, STAMP, MIN_SAMPLE = 86400, "%Y-%m-%dT%H:%M:%SZ", 100
WITHDRAWN = {"dormant", "superseded", "retired", "quarantined"}      # store statuses that mean withdrawn (not compiled)


def iso(t: float) -> str:
    return time.strftime(STAMP, time.gmtime(t))


def write_labels(cfg, picks: list[tuple[dict, str]], now: float, dry: bool) -> tuple[Counter, dict[str, str], dict[str, str]]:
    """(auto rows written per rule, effective labels, the newest human tp|fp label per ref). Never over a human row, never twice
    (newest row is an auto row)."""
    rows, broken = labels.read_chain(cfg)
    if broken is not None:
        raise labels.Refusal("labels.jsonl chain broken")
    human, done, added = {r.get("ref") for r in rows if r.get("reason_code") not in labels.AUTO}, Counter(), []
    mine = {r["ref"]: r["label"] for r in rows if r.get("reason_code") not in labels.AUTO and r.get("label") in ("tp", "fp")
            and isinstance(r.get("ref"), str)}                     # the same test as labels.effective's human half
    newest = {r.get("ref"): r.get("reason_code") for r in rows}
    for row, rule in picks:
        ref = labels.row_id(row)
        if ref not in human and newest.get(ref) not in labels.AUTO:
            newest[ref] = rule
            done[rule] += 1
            added.append({"ref": ref, "label": labels.AUTO[rule], "reason_code": rule})
            if not dry:
                labels.append_row(cfg, labels.AUTO[rule], ref, None, reason_code=rule, at=iso(now))
    return done, labels.effective(rows + added), mine


def current_mode(copy: tuple[dict, str, dict], arows: list[dict], abroken, drows: list[dict], until: str = "~") -> modes.Resolution:
    """`modes.resolve` for an approved copy (proposal, mode, record) as of `until` (a stamp after it cannot blind FP counting)."""
    return modes.resolve(copy[0]["id"], copy[2], arows, abroken, drows, None, until)


def demote(cfg, rows: list[dict], labs: dict[str, str], cands: list[dict], now: float, dry: bool, out: dict, fixed=True,
           human: dict[str, str] | None = None) -> None:
    """Append design 7.3's demotion rows (counts to `out`); `fixed` stamps `iso(now)`, else `append` stamps at append time. `human` is the
    newest human label per ref (`write_labels`): in a started phase of a proposal's detector only those count as FPs."""
    drows, broken = demotions.read_chain(cfg.data_dir, ring := chains.key_of(cfg) if dry else chains_io.need_key(cfg, demotions))   # KeyMissing: one error
    if broken is not None:
        raise ValueError("demotions.jsonl chain broken")
    lessons, out["lesson_check"] = report.lesson_statuses(cfg)
    out["errors"] += out["lesson_check"] == "chain invalid"
    errors, fires, full = report.hook_errors(cfg, rows, iso(now - 7 * DAY))
    n = errors + fires
    sure = n >= MIN_SAMPLE and not full
    out["error_rate"] = ("not computable (log full)" if full else f"computed (n={n})" if sure
                         else f"not computable (n={n}, need {MIN_SAMPLE})")
    arows, abroken = modes.read_approvals(cfg.data_dir, ring)         # unreadable counts as broken: the resolver caps at observe
    live = phase.active(cfg, iso(now))
    for copy in shadow.approved_records(cfg):
        try:
            pid, det = (p := copy[0])["id"], p["check"]["detector"]
            res = current_mode(copy, arows, abroken, drows, iso(now))
            after = (a := modes.anchor(arows, pid, iso(now), abroken)) and a["ts"]     # rows before it are lifted: they hide no cause
            if (cur := res.mode) == "disabled":                # revoked or disabled: nothing left to lower
                continue
            seen = (human or {}) if det in live else labs         # a phase: an auto FP, however many, lowers nothing
            fps = [(r["ts"], labels.row_id(r)) for r in rows if r["proposal_id"] == pid and r["ts"] >= res.window_start
                   and seen.get(labels.row_id(r)) == "fp"]
            ref = demotions.pair_ref([c for c in cands if c.get("proposal_id") == pid], 14)
            flagged = any(r["proposal_id"] == pid and r["cause"] == "evasion_pair" and r["ref"] == ref for r in drows)
            stale = catalogue.stale(det, p["provenance"]["source_sha256"])
            disable = ("lesson_inactive" if (lessons or {}).get(p["lesson_id"]) in WITHDRAWN else
                       "source_sha256" if stale else "error_rate" if sure and errors > 0.01 * n else None)
            for f in demotions.decide(pid, cur, disable=disable, fps=sorted(set(fps)), evasion=None if flagged else ref):
                f = {**f, "ts": iso(now)} if fixed else f
                if demotions.duplicate(drows, f, after) is None:
                    drows.append({"ts": iso(time.time()), **f} if dry else demotions.append(cfg.data_dir, f, current=cur, after=after, ring=ring))
                    out["demotions"] += 1
        except (KeyError, TypeError, ValueError, OSError):
            out["errors"] += 1
