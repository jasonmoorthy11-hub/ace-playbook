"""What an imported proposal must satisfy beyond `records.check` (used by `watch/carry.py`; stdlib and package modules only).

`read` opens one file of the copy (an `lstat` for a regular file BEFORE the open, `O_NOFOLLOW | O_NONBLOCK`, an `fstat` that it is the same regular
inode: a FIFO named `W-<hex>.json` is refused, never waited for). The record must be shaped as the store's own readers need it (`shape`: `store._shaped`,
`inventory_hits` a list of at most 50 clean strings of at most 200 characters, a `tier` that is an int and not a bool), its stamps in the store's format,
at most `records.DAYS` apart and not later than a day from now (`dates`), every evidence URL http(s) (`url`), its target one that the OPERATOR's own config
knows (`target`: an inventory key or a mapped docs URL of `watch_inventory_map`, a source id of `watch_sources`, or `claude_version`). A diff is never taken
from the copy, and none is computed from its fields: `change.diff` and `diff_sha256` are null (the id does not depend on them), so an imported record is
evidence and a title, and a diff comes only from the operator's own `watch scan`, which supersedes the import (`reconcile`). The title gets a visible
` (imported)`; `records.check` must still pass on what is stored.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ...config import Config
from . import records, store

MARK, FMT, MAX_BYTES = " (imported)", "%Y-%m-%dT%H:%M:%SZ", 64 * 1024


def context(cfg: Config) -> dict:
    """The operator's own targets and map (ValueError, one line, when the sources file or the inventory map is not usable)."""
    from . import fetch, inventory                                      # lazy: both load the network or subprocess modules
    if not cfg.watch_sources or not cfg.watch_inventory_map:
        raise ValueError("import needs watch_sources and watch_inventory_map in the config (to know the targets)")
    mapping = inventory.load_map(Path(cfg.watch_inventory_map))
    ids = {s["id"] for s in fetch.load_sources(Path(cfg.watch_sources))}
    return {"targets": {"claude_version", *mapping["targets"], *mapping["docs"], *ids}, "now": datetime.now(timezone.utc).replace(tzinfo=None)}


def _hits(rec: dict) -> bool:
    h = rec["inventory_hits"]
    return isinstance(h, list) and len(h) <= 50 and all(isinstance(x, str) and len(x) <= 200 and store.clean(x, 200) == x for x in h)


def _reason(rec: dict, ctx: dict) -> str:
    """"" or why a checked record is not taken: `shape`, `dates`, `url` (http or https) or `target` (unknown)."""
    if not store._shaped(rec) or not _hits(rec) or any(type(e["tier"]) is not int for e in rec["evidence"]):
        return "shape"
    try:
        if not all(store.STAMP.fullmatch(rec[k]) for k in ("created", "expires")):
            return "dates"
        made, gone = (datetime.strptime(rec[k], FMT) for k in ("created", "expires"))
        if gone - made > timedelta(days=records.DAYS) or made > ctx["now"] + timedelta(days=1):
            return "dates"
    except (ValueError, TypeError):
        return "dates"
    try:
        if not all(urlsplit(e["url"]).scheme in ("http", "https") and urlsplit(e["url"]).hostname for e in rec["evidence"]):
            return "url"
    except (ValueError, TypeError, AttributeError):
        return "url"
    return "" if rec["change"]["target"] in ctx["targets"] else "target"


def vet(rec: dict, ctx: dict) -> tuple[dict | None, str]:
    """(the record to store, "") or (None, reason): checked, the diff dropped (never taken from the copy), the title marked, `check` run again."""
    why = _reason(rec, ctx)
    if why:
        return None, why
    title = rec["title"] if rec["title"].endswith(MARK) else rec["title"] + MARK
    new = {**rec, "title": title, "status": "open", "change": {**rec["change"], "diff": None, "diff_sha256": None}}
    if records.title_model(title) != records.title_model(rec["title"]) or records.check(new):      # too long to mark, or the mark changed the id
        return None, "title"
    return new, ""


def read(dirfd: int, name: str, ctx: dict) -> tuple[dict | None, str, bool]:
    """(the record to store, "", whether the copy's diff was dropped) or (None, reason, False) for the file `name` in the open directory `dirfd`."""
    if not store.ID.fullmatch(name[:-5]):
        return None, "name", False
    try:
        before = os.lstat(name, dir_fd=dirfd)
        if not stat.S_ISREG(before.st_mode):
            return None, "symlink" if stat.S_ISLNK(before.st_mode) else "not a file", False
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
                return None, "not a file", False
            if st.st_size > MAX_BYTES:
                return None, "size", False
            data = fh.read(MAX_BYTES + 1)
    except OSError:
        return None, "unreadable", False
    try:
        rec = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None, "json", False
    if not isinstance(rec, dict):
        return None, "shape", False
    if rec.get("id") != name[:-5]:
        return None, "id", False
    reason = records.check(rec)
    if reason:
        return None, reason, False
    new, why = vet(rec, ctx)
    return new, why, new is not None and rec["change"]["diff"] is not None
