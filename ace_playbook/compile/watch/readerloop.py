"""The Reader's read loop (W2; docs/design/compiler.md 8.x "Reader (W2, as built)"): `pipeline`, shared by `model.stage` and the canary verb.

An item the W1 rules already made a lead for (`dups`: the (target, quote) pairs of the rule-path lead candidates) is skipped BEFORE the read: no
call, marked done, counted `dup` (D-162). The loop STOPS once MAX_LEADS leads are composed; the items not reached stay unmarked, are counted
`capped` and are read on later scans (D-163). Only a claude that is missing or cannot be spawned is `unavailable`: it stops the loop and the item
is not marked. A call that was spawned and failed counts `failed`, the loop goes on, the try count is kept in `seen.json` (`reader.fail`) and the
item is marked done after TRIES failed tries. THE HEALTH PROBE (D-165): no feed item decides "outage". A failed call is HELD (nothing counted,
nothing marked); after PROBE_AFTER failed calls with no success, or when the run ends with failed calls and no success, ONE call is made on the
built-in `reader.PROBE`. A parsed envelope means the Reader is healthy: the held tries count, the run goes on (later failures count at once), the
line `reader: health probe ok after N failed call(s)` goes to stderr and the probe's cost is added to the run's. A failed probe is an OUTAGE (an
expired login, no network): the held tries are dropped, the rest is `unavailable`, nothing is marked, and it is reported as `outage`. A first
success also commits the held tries; a claude that cannot be started mid-run drops them. So an outage that starts before a run's first success
or healthy probe loses no item; one that starts after it costs each later item one try (two tries retire it), and an item that makes the model fail twice is
retired on its own and cannot stall the stage. The unreached counts leave out the `dup` items (they would never have been called).
"""

from __future__ import annotations

import sys
from itertools import zip_longest
from pathlib import Path

from ...config import Config
from . import compose, reader, validate

MAX_ITEMS, MAX_LEADS, TRIES, PROBE_AFTER = 20, 5, 2, 3


def interleave(items: list[dict]) -> list[dict]:
    """Round-robin across sources, stable: item 0 of each source (in order of first appearance), then item 1 of each, and so on. One source: unchanged."""
    by_source: dict[str, list[dict]] = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)
    return [it for rank in zip_longest(*by_source.values()) for it in rank if it is not None]


def _dup(item: dict, dups) -> bool:
    """True when the W1 rules already made a lead for this entry: same source and the entry's first line (its title) is the lead's quote."""
    first = reader.lines_of(item["text"])[:1]
    return bool(first) and (item["source"], first[0]) in dups


def _behind(items: list[dict], n: int, dups) -> int:
    """The items after position `n` that a stopped loop never reached and would have called (the `dup` ones would not)."""
    return sum(1 for it in items[n + 1:] if not _dup(it, dups))


def _failed_try(key: str, now: str, done: dict | None, fail: dict | None) -> None:
    """Count a failed try of `key`; the TRIES-th one marks the item done (the loop never reads it again) and drops its count."""
    if done is None or fail is None:
        return
    fail[key] = fail.get(key, 0) + 1
    if fail[key] >= TRIES:
        del fail[key]
        done[key] = now


def _probe(cfg: Config, cwd: Path | None, stats: dict, held: list, now: str, done: dict | None, fail: dict | None, unreached: int) -> bool:
    """D-165: ask the health probe once and add its cost to the run's. Healthy: the held tries count (True). An outage: they are dropped and the
    held items and the `unreached` ones are `unavailable`, nothing is marked (False)."""
    healthy, cost = reader.probe(cfg, cwd)
    stats["cost"] += cost
    if healthy:
        stats["probe"] = "ok"
        print(f"reader: health probe ok after {len(held)} failed call(s)", file=sys.stderr)
        for key in held:
            _failed_try(key, now, done, fail)
        return True
    stats["unavailable"] += unreached + len(held)
    stats["outage"] = 1
    return False


def pipeline(cfg: Config, items: list[dict], now: str, stats: dict, done: dict | None = None, fail: dict | None = None,
             cwd: Path | None = None, dups=frozenset()) -> list[dict]:
    """Read, validate and compose each item; the records are leads only. Counts go into `stats`, a processed item's key into `done`, the failed
    tries into `fail`. `unavailable` and an outage stop the loop; so does the lead cap. `held` (D-165) has the keys of the failed calls before the
    first success or healthy probe (None after): their tries are not counted yet."""
    out: list[dict] = []
    held: list[str] | None = []
    for n, item in enumerate(items):
        if len(out) >= MAX_LEADS:                                               # D-163: no call after the cap; the rest are read on a later scan
            stats["capped"] += sum(1 for it in items[n:] if not _dup(it, dups))
            break
        if _dup(item, dups):                                                    # D-162: the rule path already has a lead for it: no call
            stats["dup"] += 1
            if done is not None:
                done[item["key"]] = now
            continue
        before = (stats["unavailable"], stats["failed"], stats["read"])
        answer = reader.read_item(item, cfg, stats, cwd)
        if stats["unavailable"] > before[0]:                                    # no claude or no spawn: stop, mark nothing, count none of the held tries
            stats["unavailable"] += _behind(items, n, dups)
            return out
        if stats["failed"] > before[1]:                                         # the call ran and failed: go on to the next item
            if held is None:
                _failed_try(item["key"], now, done, fail)
            else:
                held.append(item["key"])
                if len(held) >= PROBE_AFTER:                                    # D-165: ask the probe, not the feed, whether this is an outage
                    if not _probe(cfg, cwd, stats, held, now, done, fail, _behind(items, n, dups)):
                        return out
                    held = None
            continue
        if stats["read"] > before[2]:                                           # a success: the failures before it were the items' own
            for key in held or []:
                _failed_try(key, now, done, fail)
            held = None
        if done is not None:
            done[item["key"]] = now
        if fail is not None:
            fail.pop(item["key"], None)
        ok = validate.validate(answer, item, stats) if answer is not None else None        # validate alone decides relevance and the pin signal
        if ok is None:
            continue
        try:
            out.append(compose.compose(ok, item, now))
            stats["leads"] += 1
        except ValueError:
            stats["rejected"] += 1
    if held:                                                                    # the run ended with failed calls and no success: probe once
        _probe(cfg, cwd, stats, held, now, done, fail, 0)
    return out
