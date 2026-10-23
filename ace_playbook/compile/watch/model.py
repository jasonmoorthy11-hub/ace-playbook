"""The scan's model stage (W2): tier-1 feed items go reader -> validate -> compose, leads only (docs/design/compiler.md 8.x "Reader (W2)").

`stage` is called by `scan.run` for every scan and does nothing unless `cfg.watch_reader` is true; the kill file `<watch_dir>/READER_DISABLED`
stops it too (the rule path of W1 is untouched either way) and a dry run skips it (`reader: skipped (dry run)`). At most MAX_ITEMS new items are
looked at per scan: the queue is dealt round-robin across the sources (item 0 of each, then item 1 of each, a source's own order kept), then cut.
An item read once (a lead, a rejection or "not relevant") is remembered in `seen.json` (`reader.done`, KEEP newest) and not read again. The read
loop itself (the dedupe, the lead cap, the failed tries, the health probe and what an outage loses) is `readerloop.pipeline`, shared with the
canary verb. The Reader runs in the one fixed empty directory `<watch_dir>/reader_cwd`. The counts of the scan go into `seen.json`
(`reader.last`, with `probe`: "ok" when the health probe was asked and healthy) for `watch status`; `settle` replaces its lead count with the
reader leads reconcile stored as new. Costs come from the envelope and stay in the stats.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ...config import Config
from . import readerloop, summary
from .fsio import odd
from .store import clean

KEEP, KILL, CWD = 2000, "READER_DISABLED", "reader_cwd"
FEEDS = ("rss", "atom")


def new_stats() -> dict:
    return {"read": 0, "rejected": 0, "leads": 0, "unavailable": 0, "failed": 0, "outage": 0, "probe": "", "capped": 0, "dup": 0, "signals": 0, "cost": 0.0}


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


def killed(wd: Path) -> bool:
    return os.path.lexists(Path(wd) / KILL)


def reader_cwd(wd: Path) -> Path | None:
    """The one fixed empty directory the Reader runs in, `<watch_dir>/reader_cwd` (0700 when new); None (a temp dir per call) when it cannot be."""
    path = Path(wd) / CWD
    try:
        if odd(path, True):
            return None
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def stage(cfg: Config, results: list[dict], wd: Path, seen: dict, now: str, dry_run: bool = False, dups=frozenset()) -> tuple[list[dict], list[str]]:
    """The reader-made lead records of this scan and the lines to print (`reader: N read, M rejected, K leads`, notes). Mutates `seen`."""
    if cfg.watch_reader is not True:
        return [], []
    if killed(wd):
        return [], ["reader: disabled"]
    if dry_run:
        return [], ["reader: skipped (dry run)"]
    state = seen["reader"] = seen["reader"] if isinstance(seen.get("reader"), dict) else {}
    done = state["done"] = {k: v for k, v in state.get("done", {}).items() if isinstance(v, str)} if isinstance(state.get("done"), dict) else {}
    fail = state["fail"] = ({k: v for k, v in state["fail"].items() if isinstance(k, str) and type(v) is int and 0 < v < readerloop.TRIES}
                            if isinstance(state.get("fail"), dict) else {})
    state.pop("breaker", None)                                                                # the D-164 rotation list is gone (D-165)
    stats = new_stats()
    queued = readerloop.interleave([it for it in items_of(results) if it["key"] not in done])       # m6: no source fills the window alone
    recs = readerloop.pipeline(cfg, queued[:readerloop.MAX_ITEMS], now, stats, done, fail, reader_cwd(wd), dups)
    for key in sorted(done, key=lambda k: done[k])[:max(0, len(done) - KEEP)]:
        del done[key]
    for key in list(fail)[:max(0, len(fail) - KEEP)]:
        del fail[key]
    state["last"] = {"at": now, **{k: stats[k] for k in ("read", "rejected", "leads", "unavailable", "failed", "outage", "probe", "capped", "dup")},
                     "cost": round(stats["cost"], 6)}
    lines = [summary.reader_text(state["last"])]
    if stats["outage"]:
        lines.append(f"reader: outage (the health probe failed after {stats['failed']} failed call(s)); {stats['unavailable']} item(s) not read, "
                     "nothing marked")
    elif stats["unavailable"]:
        lines.append(f"reader: claude is unavailable; {stats['unavailable']} item(s) not read")
    if stats["signals"]:
        lines.append(f"reader: {stats['signals']} item(s) asked for a model_pin (rejected)")
    return list({r["id"]: r for r in recs}.values()), lines


def settle(seen: dict, lines: list[str], now: str, fresh: int) -> None:
    """After reconcile: the scan's `K leads` is the number of NEW reader leads it stored (not those W1 had already, nor ones it skipped)."""
    last = seen.get("reader", {}).get("last") if isinstance(seen.get("reader"), dict) else None
    if isinstance(last, dict) and last.get("at") == now and lines:
        last["leads"] = fresh
        lines[0] = summary.reader_text(last)
