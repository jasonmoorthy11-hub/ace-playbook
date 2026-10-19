"""The scrub step of proposal validation and the quarantine path of `proposal.write` (offline only).

Kept apart from proposal.py so that the shadow hook, which imports proposal.py, never loads the scrub engine;
proposal.py imports this module lazily. Fields step 1 proved to be a bare digest or a timestamp are exempt:
the LESSON lint flags every hex run over 12 characters and every ISO time.
"""

from __future__ import annotations

import json

from ..canon import canonical
from ..config import Config
from ..scrub import LESSON, scan
from ..scrub.quarantine import quarantine
from .proposal import _leaves


def hits(p: dict, cfg: Config) -> list:
    q = json.loads(canonical(p))
    q["created"] = ""
    q["provenance"].update(source_sha256="", lesson_evidence=[])
    q["replay"].update({"payload_sha256": ""} if "payload_sha256" in q["replay"] else {})
    return [h for t in _leaves(q) for h in scan(t, LESSON, cfg.deny_terms)]


def error(p: dict, cfg: Config) -> str | None:
    found = hits(p, cfg)
    return "scrub hit (" + ", ".join(sorted({h.rule for h in found})) + ")" if found else None


def to_quarantine(p: dict, cfg: Config) -> None:
    quarantine(cfg.quarantine, "proposal", {"id": p["id"]}, canonical(p), hits(p, cfg), "proposal scrub hit",
               deny_terms=tuple(cfg.deny_terms))
