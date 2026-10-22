"""`compile watch scan` (docs/design/compiler.md 8.x "Watch (as built)"): fetch the sources that are due, rebuild the inventory, match,
reconcile with the store. Network-using (`fetch`, `inventory`); loaded on demand by `cli`, never on the hook path.

Sources are due by their cadence (daily or weekly) against the stamps in `seen.json`; `force` ignores that. The parsed result of every
source is cached in `seen.json`, so an unchanged page (a 304, or the same sha256) still yields its proposals on the next scan. What a scan does
with the candidates (dedupe, auto-close, reopen, the cap) is `reconcile.py`. A scan holds an exclusive non-blocking lock on `watch_dir/scan.lock`;
a second one prints `watch: scan running` and exits 0. Writes go to `watch_dir` only; `dry_run` writes nothing (the network is still read).
"""

from __future__ import annotations

import copy
import os
from datetime import datetime
from pathlib import Path

from ...canon import now_iso
from ...config import Config
from . import fetch as fetchmod
from . import fsio, inventory, match, store
from .reconcile import reconcile  # noqa: F401 - tests call scan.reconcile

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
    lock = None
    if not dry_run:
        store.ensure(wd)
        lock = fsio.lock(wd)                                      # one scan at a time: a second one stops here
        if lock is None:
            return ["watch: scan running"]
    try:
        seen = store.read_seen(wd)
        before = copy.deepcopy(seen.get("lastmod", {}))
        results, notes, counts = collect(sources, seen, now, force, opener)
        inv = inventory.build(mapping, now)
        out = reconcile(wd, match.candidates(inv, results, mapping, before, now), results, now, dry_run, inv)
        mapped = {match.norm(u) for u in mapping["docs"]}
        seen.setdefault("lastmod", {}).update({loc: lm for r in results if r["kind"] == "sitemap" for loc, lm in r["data"].items()
                                               if match.norm(loc) in mapped and lm})
        seen["last_scan"] = now
        if not dry_run:
            inventory.write(wd, inv)
            store.save_seen(wd, seen)
        live = store.statuses(wd, now) if wd.is_dir() else []
    finally:
        if lock is not None:
            os.close(lock)
    lines = [*notes, *(f"new {c['id']} {c['class']} {c['status']}: {c['title']}" for c in out["new"])]
    lines.append(f"watch{' (dry run, nothing written)' if dry_run else ''}: fetched {counts['fetched']}, unchanged {counts['unchanged']}, "
                 f"dropped {counts['dropped']}; new {len(out['new'])}, closed {len(out['closed'])}, promoted {len(out['promoted'])}; "
                 f"open {store.open_count(live)}, leads {sum(1 for p, s in live if s == 'open' and p['class'] == 'lead')}"
                 + (f"; reopened {len(out['reopened'])}" if out["reopened"] else ""))
    return lines
