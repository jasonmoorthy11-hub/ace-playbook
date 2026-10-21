"""Keyed chains (docs/design/compiler.md 7.3, "Keyed chains (as built)"): what `approvals.jsonl` and `demotions.jsonl` share.

An install is KEYED (`key_of`) when the key or heads file exists (any file type) or `ACE_PLAYBOOK_KEYED=1` is set. Verifier rule (`hash_ok`): unkeyed, ONLY `v: 1` rows verify (`canon.record_hash`; installs from before `compile keygen`) and any `v: 2` row
breaks the chain at that row. Keyed with a readable key, ONLY `v: 2` rows verify (HMAC-SHA256, `canon.keyed_hash`), any `v: 1` row breaks
the chain at that row, and the last verified row must equal the signed head in `chain_heads.json` (`head_state`). Keyed with no usable key,
both chains are broken, even empty ones. `compile keygen` re-seals chains as `v: 2` (chains_io.py). Imports: stdlib and `..canon`.
"""

from __future__ import annotations

import contextlib
import fcntl
import hmac
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ..canon import GENESIS, chain_key, keyed_hash, record_hash

MISSING = "chain key missing"
OPERATOR = (f"error: {MISSING} (run: uv run python -m ace_playbook.compile keygen; "
            "a broken chain itself: see hooks/INSTALL.md section 7, Repair)")
HEADS, NAMES, ENV = "chain_heads.json", ("approvals", "demotions"), "ACE_PLAYBOOK_KEYED"


class KeyMissing(ValueError):
    """A writer needs the chain key (the chain has a shape-valid `v: 2` row) and there is none; the operator line was printed."""


def read_regular(path: Path, cap: int) -> bytes | None:
    """Up to `cap` + 1 bytes of a regular file opened O_NOFOLLOW; None when missing, OSError for a symlink or non-file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError("not a regular file")
        return fh.read(cap + 1)


@dataclass(frozen=True)
class Keyring:
    """The chain key (None: a keyed install whose key is unusable) and the heads file beside it (`heads_path` None: heads are neither
    checked nor written; unit tests only)."""
    key: bytes | None
    heads_path: Path | None = None

    def heads(self) -> dict | None:
        """{"approvals": hash, "demotions": hash} when the heads file is a regular file of the right shape with a valid MAC, else None."""
        try:
            d = json.loads(read_regular(self.heads_path, 4096) or b"") if self.key else None
        except (OSError, ValueError, RecursionError):
            return None
        if not (isinstance(d, dict) and set(d) == {*NAMES, "mac"} and all(isinstance(v, str) and v.isascii() for v in d.values())):
            return None
        return d if hmac.compare_digest(d["mac"], keyed_hash(self.key, {n: d[n] for n in NAMES})) else None

    def update(self, **heads: str) -> None:
        """Replace the given heads (temp file, fsync, `os.replace`, 0600) under a lock on the directory (both chains share the file)."""
        if self.heads_path is None:
            return
        dfd = os.open(self.heads_path.parent, os.O_RDONLY)
        try:
            fcntl.flock(dfd, fcntl.LOCK_EX)
            body = {**(self.heads() or dict.fromkeys(NAMES, GENESIS)), **heads}
            body = {n: body[n] for n in NAMES}
            import tempfile                                                                    # lazy: the hook path only reads
            fd, tmp = tempfile.mkstemp(dir=self.heads_path.parent, prefix=f".{HEADS}.")        # 0600, O_EXCL, never a predictable name
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(json.dumps({**body, "mac": keyed_hash(self.key, body)}, sort_keys=True) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.heads_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        finally:
            os.close(dfd)


def hash_ok(row: dict, ring: Keyring | None) -> bool:
    """The verifier rule for one shape-checked row: with a key only `v: 2` and its keyed hash, without one only `v: 1` and `record_hash`."""
    h = row.get("hash")
    if not (isinstance(h, str) and h.isascii() and type(row.get("v")) is int):
        return False
    if ring is None:
        return row["v"] == 1 and h == record_hash(row)
    return ring.key is not None and row["v"] == 2 and hmac.compare_digest(h, keyed_hash(ring.key, row))


def seal(row: dict, ring: Keyring | None) -> dict:
    """`row` as version 2 under the key, or as version 1 (plain hash) without one; `prev_hash` must already be set."""
    row = {**row, "v": 1 if ring is None else 2}
    return {**row, "hash": record_hash(row) if ring is None else keyed_hash(ring.key, row)}


def head_state(ring: Keyring | None, name: str, rows: list[dict], broken):
    """`broken`, else on a keyed install "key_missing" (no usable key), then with a heads file in play "heads_missing" (missing or
    invalid) or "head_mismatch" (the last verified row, GENESIS for none, is not the head), else None."""
    if broken is not None or ring is None:
        return broken
    if ring.key is None:
        return "key_missing"
    if ring.heads_path is None:
        return None
    heads = ring.heads()
    if heads is None:
        return "heads_missing"
    return None if (rows[-1]["hash"] if rows else GENESIS) == heads[name] else "head_mismatch"


_WHY = {"key_missing": "because the install is keyed and the chain key is missing or unusable (run: compile keygen, or see Repair)",
        "head_mismatch": "by head mismatch (rows were cut or rolled back; after a deliberate repair run: compile keygen --rehead)",
        "heads_missing": "by a missing or invalid chain_heads.json (run: compile keygen --rehead)"}


def where(broken, base: int = 0) -> str:
    """Operator wording of a `read_chain` break: a row number (`base` 1 makes a 0-based index 1-based) or a head failure."""
    return _WHY.get(broken) or (f"at row {broken + base}" if isinstance(broken, int) else str(broken))


def has_v2(raw: bytes, shape) -> bool:
    """True when some line is a row that passes `shape` (the module's shape check, hash link aside) and has `v` 2."""
    for line in raw.split(b"\n"):
        with contextlib.suppress(ValueError, RecursionError):
            if isinstance(row := json.loads(line), dict) and type(row.get("v")) is int and row["v"] == 2 and shape(row):
                return True
    return False


def key_of(cfg) -> Keyring | None:
    """The keyring of a KEYED install (key or heads file, any file type, or `ACE_PLAYBOOK_KEYED=1`); `Keyring(None, ...)` if the key is unusable."""
    path, src = getattr(cfg, "chain_key_path", None), getattr(cfg, "source", None)
    near = Path(path).parent if path else src.parent if src else None     # the key's directory; a config naming no key: its own
    heads = near / HEADS if near else None
    if (key := chain_key(path) if path else None) is not None:
        return Keyring(key, heads)
    marked = os.environ.get(ENV) == "1" or any(p is not None and os.path.lexists(p) for p in (path, heads))
    return Keyring(None, heads) if marked else None


def notes(ring: Keyring | None, brokens: tuple) -> list[str]:
    """The hook's throttled log lines: `chain_key:missing` (keyed install, no usable key, a chain is broken) and `chain_heads:missing`."""
    return ["chain_key:missing"] * (ring is not None and ring.key is None and any(b is not None for b in brokens)) + \
           ["chain_heads:missing"] * ("heads_missing" in brokens)
