"""Provenance gate: a lesson is minted only if a verbatim quote comes from the user.

Quotes are matched (whitespace-collapsed, case-folded) against the turns the
reflector actually saw. A quote found only in tool output, a file the agent wrote, the user's
shell output, or in no turn at all, cannot support a lesson. This is a structural control: the reflector's own claim
about where a quote came from is never trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..clean.transcript import Turn
from ..store.schema import QUOTE_MAX, QUOTE_MIN, USER_ORIGINS

MIN_MATCH_CHARS = 30
MIN_MATCH_WORDS = 6
MIN_OVERLAP = 2
STOPWORDS = frozenset(
    "about above after again also always been before being below between both bring could does doing "
    "done down during each even every from further have having here into just like make many more most "
    "must never only other over please same should some such than that their them then there these they "
    "this those through under until very want well were what when where which while will with would "
    "your yours you can fix the and for not use using are was but has had its our out all any "
    "his her him how who why did get got let may now off one our own say see she too two way yet "
    "don doesn didn isn aren won can".split())
ORIGIN_PRIORITY = ("user_message", "user_file", "agent_file", "shell_output", "tool_output")


def norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def content_stems(s: str) -> set[str]:
    """Distinct 5-char stems of the non-trivial words in `s`."""
    return {w[:5] for w in re.findall(r"[a-z0-9]{2,}", s.casefold()) if w not in STOPWORDS}


def long_enough(qn: str) -> bool:
    return len(qn) >= MIN_MATCH_CHARS or len(qn.split()) >= MIN_MATCH_WORDS


def related(qn: str, lesson_text: str) -> bool:
    """The quote must share content words with the lesson it is offered to support."""
    lesson = content_stems(lesson_text)
    need = MIN_OVERLAP if len(lesson) >= 4 else 1
    return len(content_stems(qn) & lesson) >= need


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


def check_provenance(quotes: list[str], turns: list[Turn], session_id: str = "",
                     lesson_text: str | None = None) -> Verdict:
    """`lesson_text` is the lesson's text plus applies_when; None skips the relatedness test."""
    haystacks: dict[str, list[str]] = {}
    for t in turns:
        if t.origin in ORIGIN_PRIORITY:
            haystacks.setdefault(t.origin, []).append(norm(t.text))
    evidence: list[dict] = []
    weak = False
    for q in quotes[:2]:
        q = q.strip()[:QUOTE_MAX].strip()
        qn = norm(q)
        origin = _origin_of(qn, haystacks)
        if not origin:
            continue
        if (not long_enough(qn) or len(q) < QUOTE_MIN
                or (lesson_text is not None and not related(qn, lesson_text))):
            weak = True
            continue
        item = {"quote": q, "origin": origin}
        if session_id:
            item["session_id"] = session_id
        evidence.append(item)
    if not evidence:
        return Verdict(False, [], "quote_weak" if weak else "quote_not_in_transcript")
    if not any(e["origin"] in USER_ORIGINS for e in evidence):
        return Verdict(False, evidence, "no_user_evidence")
    return Verdict(True, evidence)
