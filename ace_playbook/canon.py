"""Stdlib-only leaf: canonical JSON, the UTC clock string, record hashes, the chain key and the lesson id pattern.

`store/schema.py` re-exports these names; the shadow hook path imports them from here so that it never
pulls in `store/__init__` (sqlite3), the scrub engine or the cleaner (docs/design/compiler.md 7.2).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import time
from typing import Any

GENESIS = "0" * 64
ID_RE = re.compile(r"^L-[0-9a-f]{8}$")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def keyed_hash(key: bytes, row: dict) -> str:
    """HMAC-SHA256 over the canonical row without its `hash` (the v2 chain rows, docs/design/compiler.md 7.3)."""
    return hmac.new(key, canonical({k: v for k, v in row.items() if k != "hash"}).encode(), hashlib.sha256).hexdigest()


def chain_key(path: object) -> bytes | None:
    """The 32-byte key in `path` (64 hex characters, one optional newline): a regular file of this user, opened O_NOFOLLOW, owner-only
    (no group or other bits) and owner-readable. Anything else (missing, symlink, directory, other owner or mode, bad length or
    characters) is None; never raises."""
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as fh:
            info = os.fstat(fh.fileno())
            mine = stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o477 == 0o400
            raw = fh.read(66).removesuffix(b"\n") if mine else b""
        key = bytes.fromhex(raw.decode("ascii")) if len(raw) == 64 else b""
        return key if len(key) == 32 else None
    except (OSError, ValueError, TypeError):
        return None
