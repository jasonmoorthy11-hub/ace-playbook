"""The scan's model stage (W2): tier-1 feed items go reader -> validate -> compose, leads only (docs/design/compiler.md 8.x "Reader (W2)").

`stage` is called by `scan.run` for every scan and does nothing unless `cfg.watch_reader` is true; the kill file `<watch_dir>/READER_DISABLED`
stops it too (the rule path of W1 is untouched either way) and a dry run skips it (`reader: skipped (dry run)`). At most MAX_ITEMS new items are
read per scan, oldest feed position first, and at most MAX_LEADS composed leads are kept (the rest count `capped`); an item read once (a lead, a
rejection or "not relevant") is remembered in `seen.json` (`reader.done`, KEEP newest) and not read again. Only a claude that is missing or cannot
be spawned is `unavailable`: it stops the loop and the item is not marked. A call that was spawned and failed counts `failed`, the loop goes on,
the try count is kept in `seen.json` (`reader.fail`) and the item is marked done after TRIES failed tries. The Reader runs in the one fixed empty
directory `<watch_dir>/reader_cwd`. The counts of the scan go into `seen.json` (`reader.last`) for `watch status`; `settle` replaces its lead
count with the reader leads reconcile stored as new. Costs come from the envelope and stay in the stats. `pipeline` is shared with the canary verb.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ...config import Config
from . import compose, reader, summary, validate
from .fsio import odd
from .store import clean

MAX_ITEMS, MAX_LEADS, KEEP, TRIES, KILL, CWD = 20, 5, 2000, 2, "READER_DISABLED", "reader_cwd"
FEEDS = ("rss", "atom")


def new_stats() -> dict:
    return {"read": 0, "rejected": 0, "leads": 0, "unavailable": 0, "failed": 0, "capped": 0, "signals": 0, "cost": 0.0}


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


def _failed_try(key: str, now: str, done: dict | None, fail: dict | None) -> None:
    """Count a failed try of `key`; the TRIES-th one marks the item done (the loop never reads it again) and drops its count."""
    if done is None or fail is None:
        return
    fail[key] = fail.get(key, 0) + 1
    if fail[key] >= TRIES:
        del fail[key]
        done[key] = now


def pipeline(cfg: Config, items: list[dict], now: str, stats: dict, done: dict | None = None, fail: dict | None = None,
             cwd: Path | None = None) -> list[dict]:
    """Read, validate and compose each item; the records are leads only. Counts go into `stats`, a processed item's key into `done`, the
    failed tries into `fail`. Only `unavailable` stops the loop."""
    out: list[dict] = []
    for n, item in enumerate(items):
        before = (stats["unavailable"], stats["failed"])
        answer = reader.read_item(item, cfg, stats, cwd)
        if stats["unavailable"] > before[0]:                                    # no claude or no spawn: stop, do not mark done
            stats["unavailable"] += len(items) - n - 1
            break
        if stats["failed"] > before[1]:                                         # the call ran and failed: try again later, go on to the next item
            _failed_try(item["key"], now, done, fail)
            continue
        if done is not None:
            done[item["key"]] = now
        if fail is not None:
            fail.pop(item["key"], None)
        ok = validate.validate(answer, item, stats) if answer is not None else None        # validate alone decides relevance and the pin signal
        if ok is None:
            continue
        try:
            rec = compose.compose(ok, item, now)
        except ValueError:
            stats["rejected"] += 1
            continue
        if len(out) >= MAX_LEADS:
            stats["capped"] += 1
        else:
            out.append(rec)
            stats["leads"] += 1
    return out


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


def stage(cfg: Config, results: list[dict], wd: Path, seen: dict, now: str, dry_run: bool = False) -> tuple[list[dict], list[str]]:
    """The reader-made lead records of this scan and the lines to print (`reader: N read, M rejected, K leads`, notes). Mutates `seen`."""
    if cfg.watch_reader is not True:
        return [], []
    if killed(wd):
        return [], ["reader: disabled"]
    if dry_run:
        return [], ["reader: skipped (dry run)"]
    state = seen["reader"] = seen["reader"] if isinstance(seen.get("reader"), dict) else {}
    done = state["done"] = {k: v for k, v in state.get("done", {}).items() if isinstance(v, str)} if isinstance(state.get("done"), dict) else {}
    fail = state["fail"] = ({k: v for k, v in state["fail"].items() if isinstance(k, str) and type(v) is int and 0 < v < TRIES}
                            if isinstance(state.get("fail"), dict) else {})
    stats = new_stats()
    todo = [it for it in items_of(results) if it["key"] not in done][:MAX_ITEMS]
    recs = pipeline(cfg, todo, now, stats, done, fail, reader_cwd(wd))
    for key in sorted(done, key=lambda k: done[k])[:max(0, len(done) - KEEP)]:
        del done[key]
    for key in list(fail)[:max(0, len(fail) - KEEP)]:
        del fail[key]
    state["last"] = {"at": now, **{k: stats[k] for k in ("read", "rejected", "leads", "unavailable", "failed", "capped")},
                     "cost": round(stats["cost"], 6)}
    lines = [summary.reader_text(state["last"])]
    if stats["unavailable"]:
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
