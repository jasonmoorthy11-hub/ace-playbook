"""Stop-hook helper: record [L-id] citations found in the last assistant message."""

from __future__ import annotations

import re
import time

from ..config import Config
from .ledger import append_jsonl, shown_ids_for_session

CITE_RE = re.compile(r"\[(L-[0-9a-f]{8})\]")


def record_citations(cfg: Config, session_id: str, message: str) -> dict | None:
    ids = list(dict.fromkeys(CITE_RE.findall(message or "")))
    if not ids:
        return None
    shown = set(shown_ids_for_session(cfg.injections, session_id))
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "cited_ids": [i for i in ids if i in shown],
        "unknown_ids": [i for i in ids if i not in shown],
    }
    append_jsonl(cfg.citations, rec)
    return rec
