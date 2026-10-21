"""`compile watch scan` (docs/design/compiler.md 8.x "Watch (as built)"): fetch the sources that are due, rebuild the inventory, match,
reconcile with the store. Network-using (`fetch`, `inventory`); loaded on demand by `cli`, never on the hook path.

Sources are due by their cadence (daily or weekly) against the stamps in `seen.json`; `force` ignores that. The parsed result of every
source is cached in `seen.json`, so an unchanged page (a 304, or the same sha256) still yields its proposals on the next scan. A stored
proposal whose trigger no longer holds is closed by a `closed` decision row, but only when its state-based trigger (a model_pin,
a version, or an inventory lead) is gone while its source was read this scan; docs and feed leads are events and run to their expiry.
Writes go to `watch_dir` only; `dry_run` writes nothing (the network is still read).
"""

from __future__ import annotations

import copy
import os
from datetime import datetime
from pathlib import Path

from ...canon import now_iso
from ...config import Config
from . import fetch as fetchmod
from . import inventory, match, store

PERIOD = {"daily": 86400, "weekly": 7 * 86400}
FMT = "%Y-%m-%dT%H:%M:%SZ"


class ConfigError(ValueError):
    """A one-line configuration problem (exit 2)."""


def check_config(cfg: Config) -> tuple[Path, Path]:
    for name in ("watch_sources", "watch_inventory_map"):
        path = getattr(cfg, name)
        if not path or not Path(path).is_file():
            raise ConfigError(f"{name} is not set or the file is missing (hooks/INSTALL.md, Watch)")
    return Path(cfg.watch_sources), Path(cfg.watch_inventory_map)


def _due(src: dict, seen: dict, now: str, force: bool) -> bool:
    last = seen.get("sources", {}).get(src["id"])
    try:
        return force or (datetime.strptime(now, FMT) - datetime.strptime(last, FMT)).total_seconds() >= PERIOD[src["cadence"]]
    except (TypeError, ValueError):
        return True


def _result(src: dict, item: dict) -> dict:
    data = item["data"]
    return {"id": src["id"], "url": src["url"], "kind": src["kind"], "tier": src["tier"], "sha256": item["sha256"],
            "prev_sha256": item["prev_sha256"], "fetched_at": item["fetched_at"], "data": data, "quote": item["body"][:400],
            "heading": data.get("heading", "") if src["kind"] == "md" else ""}


def collect(sources: list[dict], seen: dict, now: str, force: bool, opener=None) -> tuple[list[dict], list[str], dict]:
    """Fetch what is due; returns (results, notes, counts). Unchanged, not-due and dropped sources fall back to their cached result."""
    parsed, stamps = seen.setdefault("parsed", {}), seen.setdefault("sources", {})
    results, notes, counts = [], [], {"fetched": 0, "unchanged": 0, "dropped": 0}
    for src in sources:
        cached = parsed.get(src["id"])
        cached = cached if isinstance(cached, dict) and cached.get("url") == src["url"] else None
        if cached is None:
            seen.setdefault("fetched", {}).pop(src["url"], None)              # no cache to fall back on: do not accept "unchanged"
        fresh = None
        if _due(src, seen, now, force) or cached is None:
            item = fetchmod.fetch(src["url"], expect=fetchmod.EXPECT[src["kind"]], store=seen, kind=src["kind"], opener=opener, now=now)
            if item is not None and "dropped" in item:
                counts["dropped"] += 1
                notes.append(f"dropped {src['id']}: {item['dropped']}")
            else:
                counts["unchanged" if item is None else "fetched"] += 1
                stamps[src["id"]] = now
                if item is not None:
                    fresh = parsed[src["id"]] = _result(src, item)
        if fresh or cached is not None:
            results.append(fresh or {**cached, "prev_sha256": cached["sha256"]})        # a cached result is never a change
    return results, notes, counts


def _state_based(p: dict) -> bool:
    return p.get("class") in ("model_pin", "version") or (p.get("class") == "lead" and bool(p.get("inventory_hits")))


def _triple(p: dict) -> tuple:
    return p["class"], p["change"]["target"], p["evidence"][0]["quote"]


def reconcile(wd: Path, cands: list[dict], results: list[dict], now: str, dry: bool) -> dict:
    """Close vanished state-based proposals, promote queued ones, write the new candidates (queued beyond the cap)."""
    rows, urls = store.statuses(wd, now), {r["url"] for r in results}
    known, current = {_triple(p) for p, _ in rows}, {_triple(c) for c in cands}
    gone = [p["id"] for p, s in rows if s in ("open", "queued") and _state_based(p) and _triple(p) not in current and p["evidence"][0]["url"] in urls]
    for pid in gone:
        if not dry:
            store.append_decision(wd, pid, "closed", "trigger vanished", now)
    promoted = [] if dry else store.rebalance(wd, now)
    n_open = store.open_count([(p, s) for p, s in (rows if dry else store.statuses(wd, now)) if p["id"] not in gone])
    new = []
    for c in cands:
        if _triple(c) in known or os.path.lexists(wd / store.PROPOSALS / (c["id"] + ".json")):
            continue
        if c["class"] != "lead":
            c = {**c, "status": "queued" if n_open >= store.CAP else "open"}
            n_open += c["status"] == "open"
        new.append(c)
        if not dry:
            store.write_proposal(wd, c)
    return {"new": new, "closed": gone, "promoted": promoted}


def run(cfg: Config, tier: str = "all", dry_run: bool = False, force: bool = False, now: str | None = None, opener=None) -> list[str]:
    """The output lines of one scan; ConfigError for a missing or malformed sources file or inventory map."""
    now, wd = now or now_iso(), store.watch_dir(cfg)
    if store.disabled(wd):
        return ["watch: disabled"]
    sources_path, map_path = check_config(cfg)
    try:
        sources = [s for s in fetchmod.load_sources(sources_path) if tier == "all" or str(s["tier"]) == tier]
        mapping = inventory.load_map(map_path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    if not dry_run:
        store.ensure(wd)
    seen = store.read_seen(wd)
    before = copy.deepcopy(seen.get("lastmod", {}))
    results, notes, counts = collect(sources, seen, now, force, opener)
    inv = inventory.build(mapping, now)
    out = reconcile(wd, match.candidates(inv, results, mapping, before, now), results, now, dry_run)
    mapped = {match.norm(u) for u in mapping["docs"]}
    seen.setdefault("lastmod", {}).update({loc: lm for r in results if r["kind"] == "sitemap" for loc, lm in r["data"].items()
                                           if match.norm(loc) in mapped and lm})
    seen["last_scan"] = now
    if not dry_run:
        inventory.write(wd, inv)
        store.save_seen(wd, seen)
    live = store.statuses(wd, now) if wd.is_dir() else []
    lines = [*notes, *(f"new {c['id']} {c['class']} {c['status']}: {c['title']}" for c in out["new"])]
    lines.append(f"watch{' (dry run, nothing written)' if dry_run else ''}: fetched {counts['fetched']}, unchanged {counts['unchanged']}, "
                 f"dropped {counts['dropped']}; new {len(out['new'])}, closed {len(out['closed'])}, promoted {len(out['promoted'])}; "
                 f"open {store.open_count(live)}, leads {sum(1 for p, s in live if s == 'open' and p['class'] == 'lead')}")
    return lines
