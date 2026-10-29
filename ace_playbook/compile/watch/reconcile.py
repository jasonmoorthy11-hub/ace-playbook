"""Reconcile one scan's candidates with the store (docs/design/compiler.md 8.x "Watch (as built)"). Stdlib and package modules only.

Dedupe key: a docs proposal is (class, target, evidence sha256), so a page that changes again after a dismissal is a new proposal; a
model_pin is (class, target, model id, successor), so editing the row of the deprecations table (a new date, "(extended)") does not open
another proposal; every other class is (class, target, quote, model id), so an unrelated edit of the same page does not bring a dismissed
proposal back, and two ids in one table cell stay two proposals. A model_pin whose target and model a candidate also names, but with
another successor (or an older duplicate with the same key), is closed as `superseded` when the candidate opens. A state-based proposal
(model_pin, version, an inventory lead) that has expired or auto-closed while its trigger is still true is REOPENED by a `reopened`
decision row whose note is the new expiry (the file is immutable); it waits when five are already open. A state-based proposal is
auto-closed only when its trigger is really gone: a version proposal when `claude` reports a real version (`match._ver` finds one;
garbage output closes nothing) and npm no longer has a newer one; a model proposal when the inventory no longer pins the id, or when the
deprecations page still lists the id and now says it is not retiring. A page that stopped parsing (no rows) is a dropped fetch upstream
and never closes anything.
A candidate WITH a diff for a live twin of the same key whose diff is null (every imported record) always wins: the same id REPLACES the
twin's file (atomic, `fsio.write_private`; the id does not depend on the diff, so a second file is impossible, and the twin's decision rows stay;
`diffed` lists those ids); another id closes the twin as `superseded` and is written. This holds for an expired or auto-closed twin too (it is
reopened), and for a twin of the candidate's id whatever its key; a dismissed or accepted twin is a decision and is not replaced.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import fsio, match, pin, store
from .records import DAYS, title_model

STAMP = "%Y-%m-%dT%H:%M:%SZ"
LIVE = ("open", "queued")


def state_based(p: dict) -> bool:
    return p.get("class") in ("model_pin", "version") or (p.get("class") == "lead" and bool(p.get("inventory_hits")))


def key(p: dict) -> tuple:
    ev, target = p["evidence"][0], p["change"]["target"]
    if p["class"] == "docs":
        return ("docs", target, ev["sha256"])
    mid, succ = title_model(p.get("title"))
    return ("model_pin", target, mid, succ) if p["class"] == "model_pin" and mid else (p["class"], target, ev["quote"], mid)


def vanished(p: dict, current: set, results: list[dict], inv: dict | None) -> bool:
    if p["class"] == "version":
        return (inv is not None and match._ver(inv.get("claude_version")) is not None and key(p) not in current
                and any(r["url"] == p["evidence"][0]["url"] for r in results))
    mid = pin.model_of(p)
    if mid is None or inv is None:
        return False
    if not pin.pinned(inv, p["change"]["target"], mid):
        return True
    row = pin.state(results).get(mid)
    return row is not None and not row["retiring"]


def superseded(rows: list[tuple[dict, str]], cands: list[dict], skip: set) -> list[str]:
    """Ids of live model_pin proposals that a candidate for the same target and model replaces: one with another successor, and every
    duplicate of the candidate's own key but the newest."""
    out: list[str] = []
    for c in (c for c in cands if c["class"] == "model_pin"):
        who = (c["change"]["target"], pin.model_of(c))
        group = [p for p, s in rows if s in LIVE and p["class"] == "model_pin" and p["id"] not in skip
                 and (p["change"]["target"], pin.model_of(p)) == who]
        same = [p["id"] for p in group if key(p) == key(c)]
        out += [p["id"] for p in group if p["id"] not in (same[-1:] or [None]) and p["id"] not in out]
    return out


def reconcile(wd: Path, cands: list[dict], results: list[dict], now: str, dry: bool, inv: dict | None = None) -> dict:
    """Close vanished and superseded state-based proposals, promote queued ones, reopen returned ones, write the new candidates (queued
    beyond the cap). `closed` lists the ids closed for either reason."""
    rows = store.statuses(wd, now)
    current = {key(c) for c in cands}
    gone = [p["id"] for p, s in rows if s in LIVE and state_based(p) and key(p) not in current and vanished(p, current, results, inv)]
    replaced = superseded(rows, cands, set(gone))
    for pid, why in [*((g, "trigger vanished") for g in gone), *((r, "superseded") for r in replaced)]:
        if not dry:
            store.append_decision(wd, pid, "closed", why, now)
    closed = [*gone, *replaced]
    promoted = [] if dry else store.rebalance(wd, now)
    live = rows if dry else store.statuses(wd, now)
    n_open = store.open_count([(p, s) for p, s in live if p["id"] not in closed])
    by_key = {key(p): (p, s) for p, s in sorted(live, key=lambda r: r[1] in LIVE)}           # a live proposal wins over a closed twin
    new, reopened, diffed = [], [], []
    renewed = (datetime.strptime(now, STAMP) + timedelta(days=DAYS)).strftime(STAMP)
    by_id = {p["id"]: (p, s) for p, s in live}
    for c in cands:
        mine, old = by_id.get(c["id"]), by_key.get(key(c))
        if c["change"]["diff"] is not None and mine is not None and mine[0]["change"]["diff"] is None and mine[1] not in ("accepted", "dismissed"):
            twin, tstat = mine                                              # the file of this id has no diff (an import): the own candidate replaces it,
            diffed.append(c["id"])                                          # whatever its key and status (a forged key or a closed twin hides nothing)
            back = tstat in ("expired", "auto-closed") and c["class"] != "lead"
            queued = back and n_open >= store.CAP
            n_open += back and not queued
            if not dry:
                keep = twin.get("status", "open") if tstat in LIVE else "open"
                text = json.dumps({**c, "status": "queued" if queued else keep}, indent=1, sort_keys=True) + "\n"
                fsio.write_private(wd / store.PROPOSALS / (c["id"] + ".json"), text)
                if tstat == "auto-closed" and not queued:                   # closed by a decision row: reopened by one (the file is the candidate now)
                    store.append_decision(wd, c["id"], "reopened", renewed, now)
                    reopened.append(c["id"])
            continue
        if c["change"]["diff"] is not None and old is not None and old[0]["change"]["diff"] is None and old[1] not in ("accepted", "dismissed"):
            twin = old[0]                                                   # another id, the same key: closed as superseded if live, candidate written
            if old[1] in LIVE:
                closed.append(twin["id"])
                n_open -= old[1] == "open" and twin["class"] != "lead"
                if not dry:
                    store.append_decision(wd, twin["id"], "closed", "superseded", now)
            old = None
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
    return {"new": new, "closed": closed, "promoted": promoted, "reopened": reopened, "diffed": diffed}
