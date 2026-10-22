"""Reconcile one scan's candidates with the store (docs/design/compiler.md 8.x "Watch (as built)"). Stdlib and package modules only.

Dedupe key: a docs proposal is (class, target, evidence sha256), so a page that changes again after a dismissal is a new proposal; every other
class is (class, target, quote), so an unrelated edit of the same page does not bring a dismissed proposal back. A state-based proposal
(model_pin, version, an inventory lead) that has expired or auto-closed while its trigger is still true is REOPENED by a `reopened` decision row
whose note is the new expiry (the file is immutable); it waits when five are already open. A state-based proposal is auto-closed only when
its trigger is really gone: a version proposal when `claude` reports a version and npm no longer has a newer one; a model proposal when the
inventory no longer pins the id, or when the deprecations page still lists the id and now says it is not retiring. A page that stopped parsing
(no rows) is a dropped fetch upstream and never closes anything.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from . import pin, store
from .records import DAYS

STAMP = "%Y-%m-%dT%H:%M:%SZ"


def state_based(p: dict) -> bool:
    return p.get("class") in ("model_pin", "version") or (p.get("class") == "lead" and bool(p.get("inventory_hits")))


def key(p: dict) -> tuple:
    ev = p["evidence"][0]
    return ("docs", p["change"]["target"], ev["sha256"]) if p["class"] == "docs" else (p["class"], p["change"]["target"], ev["quote"])


def vanished(p: dict, current: set, results: list[dict], inv: dict | None) -> bool:
    if p["class"] == "version":
        return (inv is not None and inv.get("claude_version") is not None and key(p) not in current
                and any(r["url"] == p["evidence"][0]["url"] for r in results))
    mid = pin.model_of(p)
    if mid is None or inv is None:
        return False
    if not pin.pinned(inv, p["change"]["target"], mid):
        return True
    row = pin.state(results).get(mid)
    return row is not None and not row["retiring"]


def reconcile(wd: Path, cands: list[dict], results: list[dict], now: str, dry: bool, inv: dict | None = None) -> dict:
    """Close vanished state-based proposals, promote queued ones, reopen returned ones, write the new candidates (queued beyond the cap)."""
    rows = store.statuses(wd, now)
    current = {key(c) for c in cands}
    gone = [p["id"] for p, s in rows if s in ("open", "queued") and state_based(p) and key(p) not in current and vanished(p, current, results, inv)]
    for pid in gone:
        if not dry:
            store.append_decision(wd, pid, "closed", "trigger vanished", now)
    promoted = [] if dry else store.rebalance(wd, now)
    live = rows if dry else store.statuses(wd, now)
    n_open = store.open_count([(p, s) for p, s in live if p["id"] not in gone])
    by_key = {key(p): (p, s) for p, s in live}
    new, reopened = [], []
    renewed = (datetime.strptime(now, STAMP) + timedelta(days=DAYS)).strftime(STAMP)
    for c in cands:
        old = by_key.get(key(c))
        if old is not None:
            p, status = old
            if state_based(p) and status in ("expired", "auto-closed") and (p["class"] == "lead" or n_open < store.CAP):
                reopened.append(p["id"])
                n_open += p["class"] != "lead"
                if not dry:
                    store.append_decision(wd, p["id"], "reopened", renewed, now)
            continue
        if os.path.lexists(wd / store.PROPOSALS / (c["id"] + ".json")):
            continue
        if c["class"] != "lead":
            c = {**c, "status": "queued" if n_open >= store.CAP else "open"}
            n_open += c["status"] == "open"
        new.append(c)
        if not dry:
            store.write_proposal(wd, c)
    return {"new": new, "closed": gone, "promoted": promoted, "reopened": reopened}
