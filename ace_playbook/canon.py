"""Stdlib-only leaf: canonical JSON, the UTC clock string, record hashes and the lesson id pattern.

`store/schema.py` re-exports these names; the shadow hook path imports them from here so that it never
pulls in `store/__init__` (sqlite3), the scrub engine or the cleaner (docs/design/compiler.md 7.2).
"""

from __future__ import annotations

import hashlib
import json
import re
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
