"""The scan's model stage (W2): tier-1 feed items go reader -> validate -> compose, leads only (docs/design/compiler.md 8.x "Reader (W2)").

`stage` is called by `scan.run` for every scan and does nothing unless `cfg.watch_reader` is true; the kill file `<watch_dir>/READER_DISABLED`
stops it too (the rule path of W1 is untouched either way). At most MAX_ITEMS new items are read per scan, oldest feed position first; an item
read once (a lead, a rejection or "not relevant") is remembered in `seen.json` (`reader.done`, KEEP newest) and not read again, an item the
Reader could not be run for is not. The first `unavailable` stops the loop (the rest count as unavailable too). The counts of the scan go into
`seen.json` (`reader.last`) for `watch status`; costs come from the envelope and stay in the stats. `pipeline` is shared with the canary verb.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ...config import Config
from . import compose, reader, summary, validate
from .store import clean

MAX_ITEMS, KEEP, KILL = 20, 2000, "READER_DISABLED"
FEEDS = ("rss", "atom")


def new_stats() -> dict:
    return {"read": 0, "rejected": 0, "leads": 0, "unavailable": 0, "signals": 0, "cost": 0.0}


def items_from_entries(entries: list[dict], source: str, url: str, sha256: str) -> list[dict]:
    """One Reader item per parsed feed entry: its title and summary (already visible text, one line each) and the entry's own link."""
    out = []
    for e in entries:
        if not (isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("title", "summary", "link"))):
            continue
        text = "\n".join(x for x in (clean(e["title"], 400).strip(), clean(e["summary"], 1000).strip()) if x)
        if text:
            key = hashlib.sha256(f"{source}|{e.get('id')}|{text}".encode()).hexdigest()[:32]
            out.append({"source": source, "url": e["link"] or url, "sha256": sha256, "text": text, "key": key})
    return out


def items_of(results: list[dict]) -> list[dict]:
    return [it for r in results if r.get("tier") == 1 and r.get("kind") in FEEDS and isinstance(r.get("data"), list)
            for it in items_from_entries(r["data"], r["id"], r["url"], r["sha256"])]


def pipeline(cfg: Config, items: list[dict], now: str, stats: dict, done: dict | None = None) -> list[dict]:
    """Read, validate and compose each item; the records are leads only. Counts go into `stats`, a processed item's key into `done`."""
    out: list[dict] = []
    for n, item in enumerate(items):
        before = stats["unavailable"]
        answer = reader.read_item(item, cfg, stats)
        if stats["unavailable"] > before:                                       # no claude, a failed call or a timeout: stop, do not mark done
            stats["unavailable"] += len(items) - n - 1
            break
        if done is not None:
            done[item["key"]] = now
        ok = validate.validate(answer, item, stats) if answer is not None and answer["relevant"] else None
        if ok is None:
            continue
        try:
            out.append(compose.compose(ok, item, now))
            stats["leads"] += 1
        except ValueError:
            stats["rejected"] += 1
    return out


def killed(wd: Path) -> bool:
    return os.path.lexists(Path(wd) / KILL)


def stage(cfg: Config, results: list[dict], wd: Path, seen: dict, now: str) -> tuple[list[dict], list[str]]:
    """The reader-made lead records of this scan and the lines to print (`reader: N read, M rejected, K leads`, notes). Mutates `seen`."""
    if cfg.watch_reader is not True:
        return [], []
    if killed(wd):
        return [], ["reader: disabled"]
    state = seen["reader"] = seen["reader"] if isinstance(seen.get("reader"), dict) else {}
    done = state["done"] = {k: v for k, v in state.get("done", {}).items() if isinstance(v, str)} if isinstance(state.get("done"), dict) else {}
    stats = new_stats()
    todo = [it for it in items_of(results) if it["key"] not in done][:MAX_ITEMS]
    recs = pipeline(cfg, todo, now, stats, done)
    for key in sorted(done, key=lambda k: done[k])[:max(0, len(done) - KEEP)]:
        del done[key]
    state["last"] = {"at": now, **{k: stats[k] for k in ("read", "rejected", "leads", "unavailable")}, "cost": round(stats["cost"], 6)}
    lines = [summary.reader_text(state["last"])]
    if stats["unavailable"]:
        lines.append(f"reader: claude is unavailable; {stats['unavailable']} item(s) not read")
    if stats["signals"]:
        lines.append(f"reader: {stats['signals']} item(s) asked for a model_pin (rejected)")
    return list({r["id"]: r for r in recs}.values()), lines
