"""Provenance gate: a lesson is minted only if a verbatim quote comes from the user.

Quotes are matched (whitespace-collapsed, case-folded) against the turns the
reflector actually saw. A quote found only in tool output, or in no turn at all,
cannot support a lesson. This is a structural control: the reflector's own claim
about where a quote came from is never trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..clean.transcript import Turn
from ..store.schema import QUOTE_MAX, QUOTE_MIN, USER_ORIGINS

MIN_MATCH_CHARS = 12
ORIGIN_PRIORITY = ("user_message", "user_file", "tool_output")


def norm(s: str) -> str:
    return " ".join(s.split()).casefold()


@dataclass
class Verdict:
    ok: bool
    evidence: list[dict]
    reason: str = ""


def _origin_of(quote_n: str, haystacks: dict[str, list[str]]) -> str | None:
    for origin in ORIGIN_PRIORITY:
        if any(quote_n in h for h in haystacks.get(origin, [])):
            return origin
    return None


def check_provenance(quotes: list[str], turns: list[Turn], session_id: str = "") -> Verdict:
    haystacks: dict[str, list[str]] = {}
    for t in turns:
        if t.origin in ORIGIN_PRIORITY:
            haystacks.setdefault(t.origin, []).append(norm(t.text))
    evidence: list[dict] = []
    for q in quotes[:2]:
        q = q.strip()[:QUOTE_MAX].strip()
        qn = norm(q)
        if len(qn) < MIN_MATCH_CHARS or len(q) < QUOTE_MIN:
            continue
        origin = _origin_of(qn, haystacks)
        if origin:
            item = {"quote": q, "origin": origin}
            if session_id:
                item["session_id"] = session_id
            evidence.append(item)
    if not evidence:
        return Verdict(False, [], "quote_not_in_transcript")
    if not any(e["origin"] in USER_ORIGINS for e in evidence):
        return Verdict(False, evidence, "no_user_evidence")
    return Verdict(True, evidence)
