"""Quarantine semantics: a scrub hit sends the whole lesson to an untracked file.

The lesson is never redacted-and-kept. The quarantine record stores only
metadata about the hits (rule, layer, length, fingerprint), a fingerprint of the
candidate, and the candidate text with hit spans masked.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .engine import Hit


class QuarantineRequired(Exception):
    """Raised by writers that refuse to persist text containing scrub hits."""

    def __init__(self, hits: list[Hit], where: str = "") -> None:
        self.hits = hits
        self.where = where
        rules = sorted({h.rule for h in hits})
        super().__init__(f"scrub hit{' in ' + where if where else ''}: {', '.join(rules)}")


def mask(text: str, hits: list[Hit]) -> str:
    """Replace every hit's matched string wherever it occurs in `text`.

    Matching by string rather than offset keeps this correct when the hits came
    from scanning a different string (for example an evidence quote).
    """
    for h in sorted(hits, key=lambda h: -len(h.match)):
        if h.match:
            text = text.replace(h.match, f"[HIT:{h.rule}]")
    return text


def quarantine(
    path: Path,
    kind: str,
    candidate: dict[str, Any],
    text: str,
    hits: list[Hit],
    reason: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Append a quarantine record and return it."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "reason": reason,
        "session_id": session_id,
        "candidate_fp": hashlib.sha256(
            json.dumps(candidate, sort_keys=True).encode()
        ).hexdigest()[:16],
        "hits": [h.public() for h in hits],
        "masked_text": mask(text, hits),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)
    return record
