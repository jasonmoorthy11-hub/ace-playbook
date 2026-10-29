"""`compile watch import <dir>`: move a reviewed copy of acewatch's `proposals/W-*.json` into the operator's own store.

`<dir>` is opened with `O_NOFOLLOW | O_DIRECTORY` after its path is normalised (so `<link>/sub/..` is the link and is refused) and its entries are read
through that descriptor. Only regular files named `W-<hex>.json` (never links, FIFOs or devices; at most 64 KiB) whose record passes `records.check`
(no body), the id of which is the file stem, are taken, and `vet.py` holds the rest of the rules: shape, stamps, http(s) URLs, targets the OPERATOR's own
config knows, the diff recomputed from the operator's own inventory (else dropped), the visible ` (imported)`. `status` is forced to open and the CAP of
five open still holds (the rest are queued, leads excepted). A record whose `reconcile.key` is in the store, or whose id exists, is a duplicate. The copy
is never changed. Output is counts, reason words and the (already cleaned) titles: no file content. Loaded lazily like `scan` and `canary`.
"""

from __future__ import annotations

import os
import sys

from ...canon import now_iso
from ...config import Config
from . import fsio, reconcile, store, vet


def run(cfg: Config, src: str, dry: bool) -> int:
    """Exit 0 (with the summary line) or 2 for a source that is not a real directory, an unusable config, an unsafe or disabled watch directory."""
    wd = store.watch_dir(cfg)
    try:
        dirfd = os.open(os.path.abspath(src), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        print("error: import needs a real directory (not a symlink)", file=sys.stderr)
        return 2
    lock = None
    try:
        if store.disabled(wd):
            print("error: watch is disabled (the DISABLED file is in the watch directory)", file=sys.stderr)
            return 2
        try:
            names, ctx = sorted(n for n in os.listdir(dirfd) if n.startswith("W-") and n.endswith(".json")), vet.context(cfg)
        except (OSError, ValueError) as exc:
            print(f"error: {exc if isinstance(exc, ValueError) else 'the directory or the config files could not be read'}", file=sys.stderr)
            return 2
        if not dry:
            store.ensure(wd)
            lock = fsio.lock(wd, blocking=True)
        now = now_iso()
        rows = store.statuses(wd, now)
        keys, n_open = {reconcile.key(p) for p, _ in rows}, store.open_count(rows)
        taken, dup, bad, plain = [], 0, [], set()
        for name in names:
            rec, why, had_diff = vet.read(dirfd, name, ctx)
            if rec is None:
                bad.append(f"rejected {store.clean(name[:-5], 40)}: {why}")
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
        os.close(dirfd)
        if lock is not None:
            os.close(lock)
    cut = sum(1 for r in imported if r["id"] in plain)
    print("\n".join([f"import: {len(imported)} imported, {dup} duplicate, {len(bad)} rejected" + (f"; {cut} imported without diff" if cut else ""),
                     *bad, *(f"- {r['title']}" for r in imported)]))
    return 0
