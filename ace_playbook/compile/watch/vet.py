"""What an imported proposal must satisfy beyond `records.check` (used by `watch/carry.py`; stdlib and package modules only).

The copy is evidence plus a title: its stamps must be in the store's format and at most `records.DAYS` apart (`dates`), every evidence URL
http(s) (`url`), its target one that the OPERATOR's own config knows (`target`: an inventory key or a mapped docs URL of `watch_inventory_map`, a
source id of `watch_sources`, or `claude_version`). A diff is never imported: only the operator's own scan writes one, so `change.diff` and
`diff_sha256` become null (the id does not depend on them) and the title gets a visible ` (imported)`; `records.check` must still pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from ...config import Config
from . import records, store

MARK, FMT = " (imported)", "%Y-%m-%dT%H:%M:%SZ"


def known(cfg: Config) -> set[str]:
    """The targets the operator's own config knows; ValueError (one line) when the sources file or the inventory map is not usable."""
    from . import fetch, inventory                                      # lazy: both load the network or subprocess modules
    if not cfg.watch_sources or not cfg.watch_inventory_map:
        raise ValueError("import needs watch_sources and watch_inventory_map in the config (to know the targets)")
    mapping = inventory.load_map(Path(cfg.watch_inventory_map))
    return {"claude_version", *mapping["targets"], *mapping["docs"], *(s["id"] for s in fetch.load_sources(Path(cfg.watch_sources)))}


def _reason(rec: dict, known: set[str]) -> str:
    """"" or why a checked record is not taken: `dates` (store stamp format, at most DAYS apart), `url` (http or https), `target` (unknown)."""
    try:
        if not all(store.STAMP.fullmatch(rec[k]) for k in ("created", "expires")):
            return "dates"
        if datetime.strptime(rec["expires"], FMT) - datetime.strptime(rec["created"], FMT) > timedelta(days=records.DAYS):
            return "dates"
    except (ValueError, TypeError):
        return "dates"
    try:
        if not all(urlsplit(e["url"]).scheme in ("http", "https") and urlsplit(e["url"]).hostname for e in rec["evidence"]):
            return "url"
    except (ValueError, TypeError, AttributeError):
        return "url"
    return "" if isinstance(rec["change"]["target"], str) and rec["change"]["target"] in known else "target"


def vet(rec: dict, known: set[str]) -> tuple[dict | None, str]:
    """(the record to store, "") or (None, reason): checked, then the diff dropped, the title marked, `check` run again on the result."""
    why = _reason(rec, known)
    if why:
        return None, why
    title = rec["title"] if rec["title"].endswith(MARK) else rec["title"] + MARK
    new = {**rec, "title": title, "status": "open", "change": {**rec["change"], "diff": None, "diff_sha256": None}}
    if records.title_model(title) != records.title_model(rec["title"]) or records.check(new):      # too long to mark, or the mark changed the id
        return None, "title"
    return new, ""
