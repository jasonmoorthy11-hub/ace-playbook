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
    (up to three, ranked), `watch_disabled` (the kill file) and `watch_last_scan` (an ISO stamp or None)."""
    now, wd = now or now_iso(), store.watch_dir(cfg)
    out = {"watch_open": 0, "watch_leads": 0, "watch_oldest": None, "watch_top": None, "watch_titles": [],
           "watch_disabled": store.disabled(wd), "watch_last_scan": None}
    try:
        live = [p for p, s in store.statuses(wd, now) if s == "open"]
        scan = store.read_seen(wd).get("last_scan")
    except OSError:
        return out
    real = sorted((p for p in live if p.get("class") != "lead"), key=store.rank_key)
    out["watch_open"], out["watch_leads"] = len(real), len(live) - len(real)
    out["watch_oldest"] = min((str(p.get("created")) for p in real), default=None)
    out["watch_titles"] = [store.clean(p.get("title")) for p in real[:TITLES]]
    out["watch_top"] = out["watch_titles"][0] if out["watch_titles"] else None
    out["watch_last_scan"] = scan if isinstance(scan, str) and STAMP.fullmatch(scan) else None
    return out


def block(f: dict, now: str, age) -> list[str]:
    """The `proposals` lines of the digest and of `compile watch status`; `age(ts, now)` is `status.age`. Tolerates a status dict
    without watch fields (an older status.json): that reads as `none`."""
    if f.get("watch_disabled") is True:
        return ["disabled"]
    num = lambda k: f[k] if isinstance(f.get(k), int) and not isinstance(f.get(k), bool) else 0  # noqa: E731
    titles = [store.clean(t) for t in f.get("watch_titles", []) if isinstance(t, str)][:TITLES] if isinstance(f.get("watch_titles"), list) else []
    if not num("watch_open") and not num("watch_leads"):
        return ["none"]
    return [f"open {num('watch_open')}  leads {num('watch_leads')}  oldest {age(f.get('watch_oldest'), now)}  "
            f"last scan {age(f.get('watch_last_scan'), now)}", *(f"- {t}" for t in titles)]
