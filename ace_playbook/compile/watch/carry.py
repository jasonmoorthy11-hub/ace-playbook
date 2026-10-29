"""`compile watch import <dir>`: move a reviewed copy of acewatch's `proposals/W-*.json` into the operator's own store.

Only regular files named `W-<hex>.json` (not links, at most 64 KiB) whose record passes `records.check` (no body) and whose id is the file
stem are taken; `status` is forced to open and the CAP of five open still holds (the rest are queued, leads excepted). A record whose
`reconcile.key` is already in the store, or whose id exists, is a duplicate. The copy is never changed. Output is counts, the `check` reason
words and the (already cleaned) titles: no file content. Stdlib and package modules only; loaded lazily like `scan` and `canary`.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from ...canon import now_iso
from ...config import Config
from . import fsio, reconcile, records, store

MAX_BYTES = 64 * 1024


def _read(path: Path) -> tuple[dict | None, str]:
    """(the record, "") or (None, the reason it is not taken); the reason is one fixed word or a `records.check` reason."""
    if not store.ID.fullmatch(path.stem):
        return None, "name"
    if path.is_symlink():
        return None, "symlink"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                return None, "not a file"
            if st.st_size > MAX_BYTES:
                return None, "size"
            data = fh.read(MAX_BYTES + 1)
    except OSError:
        return None, "unreadable"
    try:
        rec = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None, "json"
    if not isinstance(rec, dict):
        return None, "shape"
    if rec.get("id") != path.stem:
        return None, "id"
    reason = records.check(rec)
    return (None, reason) if reason else ({**rec, "status": "open"}, "")


def run(cfg: Config, src: str, dry: bool) -> int:
    """Exit 0 (with the summary line) or 2 for a source that is not a real directory, an unsafe or disabled watch directory."""
    folder = Path(src)
    if folder.is_symlink() or not folder.is_dir():
        print("error: import needs a real directory (not a symlink)", file=sys.stderr)
        return 2
    wd = store.watch_dir(cfg)
    if store.disabled(wd):
        print("error: watch is disabled (the DISABLED file is in the watch directory)", file=sys.stderr)
        return 2
    try:
        files = sorted(folder.glob("W-*.json"))
    except OSError:
        print("error: the directory cannot be listed", file=sys.stderr)
        return 2
    lock = None
    try:
        if not dry:
            store.ensure(wd)
            lock = fsio.lock(wd, blocking=True)
        now = now_iso()
        rows = store.statuses(wd, now)
        keys, n_open = {reconcile.key(p) for p, _ in rows}, store.open_count(rows)
        taken, dup, bad = [], 0, []
        for path in files:
            rec, why = _read(path)
            if rec is None:
                bad.append(f"rejected {store.clean(path.stem, 40)}: {why}")
            elif reconcile.key(rec) in keys:
                dup += 1
            else:
                keys.add(reconcile.key(rec))
                taken.append(rec)
        imported = []
        for rec in sorted(taken, key=store.rank_key):
            if rec["class"] != "lead":
                rec["status"] = "queued" if n_open >= store.CAP else "open"
                n_open += rec["status"] == "open"
            if dry or store.write_proposal(wd, rec):
                imported.append(rec["title"])
            else:
                dup += 1
        if not dry:
            store.rebalance(wd, now)
    except store.UnsafeDir as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            os.close(lock)
    print("\n".join([f"import: {len(imported)} imported, {dup} duplicate, {len(bad)} rejected", *bad, *(f"- {t}" for t in imported)]))
    return 0
