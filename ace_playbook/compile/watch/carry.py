"""`compile watch import <dir>`: move a reviewed copy of acewatch's `proposals/W-*.json` into the operator's own store.

Only regular files named `W-<hex>.json` (not links, at most 64 KiB) whose record passes `records.check` (no body) and whose id is the file
stem are taken. Then: both stamps in the store's format and at most DAYS apart (`dates`), every evidence URL http(s) (`url`), the target one the
OPERATOR's own config knows (`target`: an inventory key, a mapped docs URL, a source id or `claude_version`). An imported record is evidence plus
title: its `change.diff` is ALWAYS dropped (null, with a null `diff_sha256`; the id does not depend on it), because only the operator's own scan
may write a diff; the title carries a visible ` (imported)`. `status` is forced to open and the CAP of five open still holds (the rest are
queued, leads excepted). A record whose `reconcile.key` is in the store, or whose id exists, is a duplicate. The copy is never changed. Output
is counts, reason words and the (already cleaned) titles: no file content. Loaded lazily like `scan` and `canary`.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from ...canon import now_iso
from ...config import Config
from . import fsio, reconcile, records, store, vet

MAX_BYTES = 64 * 1024


def _read(path: Path, known: set[str]) -> tuple[dict | None, str, bool]:
    """(the record, "", whether its diff was dropped) or (None, the reason it is not taken); the reason is one fixed word or a `records.check` reason."""
    if not store.ID.fullmatch(path.stem):
        return None, "name", False
    if path.is_symlink():
        return None, "symlink", False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
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
    if rec.get("id") != path.stem:
        return None, "id", False
    reason = records.check(rec)
    if reason:
        return None, reason, False
    new, why = vet.vet(rec, known)
    return new, why, rec["change"]["diff"] is not None


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
        files, known = sorted(folder.glob("W-*.json")), vet.known(cfg)
    except (OSError, ValueError) as exc:
        print(f"error: {exc if isinstance(exc, ValueError) else 'the directory cannot be listed'}", file=sys.stderr)
        return 2
    lock = None
    try:
        if not dry:
            store.ensure(wd)
            lock = fsio.lock(wd, blocking=True)
        now = now_iso()
        rows = store.statuses(wd, now)
        keys, n_open = {reconcile.key(p) for p, _ in rows}, store.open_count(rows)
        taken, dup, bad, plain = [], 0, [], set()
        for path in files:
            rec, why, had_diff = _read(path, known)
            if rec is None:
                bad.append(f"rejected {store.clean(path.stem, 40)}: {why}")
            elif reconcile.key(rec) in keys:
                dup += 1
            else:
                keys.add(reconcile.key(rec))
                taken.append(rec)
                if had_diff:
                    plain.add(rec["id"])
        imported = []
        for rec in sorted(taken, key=store.rank_key):
            if rec["class"] != "lead":
                rec["status"] = "queued" if n_open >= store.CAP else "open"
                n_open += rec["status"] == "open"
            if dry or store.write_proposal(wd, rec):
                imported.append(rec)
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
    cut = sum(1 for r in imported if r["id"] in plain)
    print("\n".join([f"import: {len(imported)} imported, {dup} duplicate, {len(bad)} rejected" + (f"; {cut} imported without diff" if cut else ""),
                     *bad, *(f"- {r['title']}" for r in imported)]))
    return 0
