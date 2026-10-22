"""The watcher's read-side summary (docs/design/compiler.md 8.x "Watch (as built)"): the fields `status.json` carries and the digest block.

`fields` is a pure read of the store: a missing `watch_dir` gives zeros and creates nothing. No network import here (the conformance
HEAVY list): `status.py` imports this module, and `status.py` is imported by `__main__`. Titles are the only free text (model-touched
in later phases); they are cleaned of control characters and cut to 120 characters before they reach `status.json`.
"""

from __future__ import annotations

import re

from ...canon import now_iso
from ...config import Config
from . import store

STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
TITLES = 3


def fields(cfg: Config, now: str | None = None) -> dict:
    """`watch_open` (open, leads excluded), `watch_leads`, `watch_oldest` (the oldest open non-lead), `watch_top` and `watch_titles`
    (up to three, ranked), `watch_disabled` (the kill file), `watch_last_scan` (an ISO stamp or None) and `watch_corrupt` (proposal
    files skipped as unreadable or malformed). A store that raises anything gives the zeros: the status line must never fail on it."""
    now, wd = now or now_iso(), store.watch_dir(cfg)
    out = {"watch_open": 0, "watch_leads": 0, "watch_oldest": None, "watch_top": None, "watch_titles": [],
           "watch_disabled": store.disabled(wd), "watch_last_scan": None, "watch_corrupt": 0}
    try:
        props, bad = store.scan_proposals(wd)
        decisions = store.read_decisions(wd)
        live = [p for p in props if store.status_of(p, decisions, now) == "open"]
        scan = store.read_seen(wd).get("last_scan")
        real = sorted((p for p in live if p.get("class") != "lead"), key=store.rank_key)
        titles = [store.clean(p.get("title")) for p in real[:TITLES]]
        return {**out, "watch_open": len(real), "watch_leads": len(live) - len(real), "watch_titles": titles, "watch_corrupt": bad,
                "watch_oldest": min((str(p.get("created")) for p in real), default=None), "watch_top": titles[0] if titles else None,
                "watch_last_scan": scan if isinstance(scan, str) and STAMP.fullmatch(scan) else None}
    except Exception:                                                  # noqa: BLE001 - a corrupt store must not break status.json or the digest
        return out


def block(f: dict, now: str, age) -> list[str]:
    """The `proposals` lines of the digest and of `compile watch status`; `age(ts, now)` is `status.age`. Tolerates a status dict
    without watch fields (an older status.json): that reads as `none`."""
    if f.get("watch_disabled") is True:
        return ["disabled"]
    num = lambda k: f[k] if isinstance(f.get(k), int) and not isinstance(f.get(k), bool) else 0  # noqa: E731
    titles = [store.clean(t) for t in f.get("watch_titles", []) if isinstance(t, str)][:TITLES] if isinstance(f.get("watch_titles"), list) else []
    bad = f"  corrupt {num('watch_corrupt')}" if num("watch_corrupt") else ""
    if not num("watch_open") and not num("watch_leads"):
        return ["none" + bad]
    return [f"open {num('watch_open')}  leads {num('watch_leads')}  oldest {age(f.get('watch_oldest'), now)}  "
            f"last scan {age(f.get('watch_last_scan'), now)}{bad}", *(f"- {t}" for t in titles)]
