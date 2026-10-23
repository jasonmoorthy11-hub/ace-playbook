"""Three-layer scanner: regex rules, entropy, decoded content (depth 2), plus lints."""

from __future__ import annotations

import base64
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable

from . import patterns as P

MAX_DECODE_DEPTH = 2
ENTROPY_MIN = 4.2
ENTROPY_MIN_LEN = 24


@dataclass(frozen=True)
class Profile:
    secrets: bool = True
    pii: bool = False
    long_tokens: bool = False
    outbound: bool = False


SECRETS = Profile()
OUTBOUND = Profile(pii=True, outbound=True)
REPO = Profile(pii=True)
LESSON = Profile(pii=True, long_tokens=True)


@dataclass(frozen=True)
class Hit:
    rule: str
    layer: str  # "regex" | "entropy" | "decoded" | "lint"
    start: int
    end: int
    match: str = field(repr=False, compare=False, default="")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.match.encode()).hexdigest()[:10]

    def public(self) -> dict:
        """Metadata safe to log: never includes the matched text."""
        return {
            "rule": self.rule,
            "layer": self.layer,
            "len": self.end - self.start,
            "fp": self.fingerprint,
        }


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def _is_placeholder(value: str) -> bool:
    v = value.lower()
    return v.startswith(P.PLACEHOLDER_PREFIXES)


def _regex_hits(text: str, offset: int = 0, prefix: str = "") -> list[Hit]:
    hits = []
    for name, pat in P.PROVIDER_RULES:
        for m in pat.finditer(text):
            if name == "secret_assignment" and _is_placeholder(m.group(1)):
                continue
            hits.append(Hit(prefix + name, "decoded" if prefix else "regex",
                            m.start() + offset, m.end() + offset, m.group(0)))
    return hits


def _lines(text: str, pat: re.Pattern):
    """(match, start, end) of `pat` line by line, `start`/`end` being offsets in `text` (S-1 F9 F12). For a pattern that cannot span a line
    break (EMAIL, ENTROPY_CANDIDATE: no white space in their classes) this finds what one pass over `text` finds, in many small scans."""
    off = 0
    for line in text.split("\n"):
        for m in pat.finditer(line):
            yield m, m.start() + off, m.end() + off
        off += len(line) + 1


def _entropy_hits(text: str, offset: int = 0, prefix: str = "") -> list[Hit]:
    hits = []
    for m, start, end in _lines(text, P.ENTROPY_CANDIDATE):
        tok = m.group(0)
        if tok.count("/") >= 2 or len(tok) < ENTROPY_MIN_LEN:
            continue
        classes = sum([any(c.islower() for c in tok), any(c.isupper() for c in tok),
                       any(c.isdigit() for c in tok)])
        if classes >= 3 and shannon_entropy(tok) >= ENTROPY_MIN:
            hits.append(Hit(prefix + "high_entropy", "decoded" if prefix else "entropy", start + offset, end + offset, tok))
    return hits


def try_decode(token: str) -> str | None:
    pad = token + "=" * (-len(token) % 4)
    for fn in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = fn(pad)
        except (ValueError, TypeError):
            continue
        text = raw.decode("utf-8", errors="ignore")
        if len(text) >= 12 and sum(c.isprintable() for c in text) / len(text) >= 0.9:
            return text
    return None


def _decoded_hits(text: str, depth: int, offset: int = 0) -> list[Hit]:
    """Decode base64-looking runs and rescan; hits map to the encoded span."""
    if depth > MAX_DECODE_DEPTH:
        return []
    hits: list[Hit] = []
    for m in P.B64_CANDIDATE.finditer(text):
        decoded = try_decode(m.group(0))
        if not decoded:
            continue
        inner = _regex_hits(decoded) + _entropy_hits(decoded)
        inner += _decoded_hits(decoded, depth + 1)
        for h in inner:
            hits.append(Hit("decoded:" + h.rule.removeprefix("decoded:"), "decoded",
                            m.start() + offset, m.end() + offset, m.group(0)))
    return hits


def _pii_hits(text: str, deny_terms: Iterable[str]) -> list[Hit]:
    hits = []
    for m, start, end in _lines(text, P.EMAIL):
        local, _, domain = m.group(0).partition("@")
        if domain.lower() in P.SAFE_EMAIL_DOMAINS or local.lower() in P.SAFE_EMAIL_LOCALS:
            continue
        hits.append(Hit("email", "lint", start, end, m.group(0)))
    for m in P.PHONE.finditer(text):
        hits.append(Hit("phone", "lint", m.start(), m.end(), m.group(0)))
    for m in P.USER_PATH.finditer(text):
        name = m.group(1) or m.group(2)
        if name not in P.SAFE_PATH_NAMES:
            hits.append(Hit("user_path", "lint", m.start(), m.end(), m.group(0)))
    for term in deny_terms:
        for m in re.finditer(re.escape(term), text, re.IGNORECASE):
            hits.append(Hit("deny_term", "lint", m.start(), m.end(), m.group(0)))
    return hits


def _long_token_hits(text: str) -> list[Hit]:
    hits = [Hit("long_hex", "lint", m.start(), m.end(), m.group(0))
            for m in P.LONG_HEX.finditer(text)]
    for m in P.LONG_B64.finditer(text):
        tok = m.group(0)
        if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok):
            hits.append(Hit("long_token", "lint", m.start(), m.end(), tok))
    return hits


def _outbound_hits(text: str) -> list[Hit]:
    """Broader masking for text leaving the machine: precision matters less than recall."""
    hits = []
    for m in P.MIXED_RUN.finditer(text):
        tok = m.group(0)
        if any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok):
            hits.append(Hit("mixed_token", "lint", m.start(), m.end(), tok))
    for m in P.LOWER_SECRET_ASSIGNMENT.finditer(text):
        if not _is_placeholder(m.group(1)):
            hits.append(Hit("secret_assignment", "regex", m.start(), m.end(), m.group(0)))
    return hits


GENERIC_RULES = {"secret_assignment", "bearer_token", "url_userinfo"}


def _rank(h: Hit) -> int:
    if h.layer == "regex":
        return 1 if h.rule in GENERIC_RULES else 0
    return {"decoded": 2, "lint": 3, "entropy": 4}[h.layer]


def _dedupe(hits: list[Hit]) -> list[Hit]:
    """Keep the most specific hits; drop one wholly inside a better-or-equal hit.

    A broad rule (an assignment whose value runs on) never hides a specific
    provider hit inside it. An entropy hit is dropped only when it lies wholly
    inside a kept hit; one that extends past it is kept so the tail of a secret
    is not left behind (redaction merges overlapping spans).
    """
    kept: list[Hit] = []
    for h in sorted(hits, key=lambda h: (_rank(h), h.start, -(h.end - h.start))):
        if h.layer == "entropy":
            if any(k.start <= h.start and h.end <= k.end for k in kept):
                continue
        elif any(k.start <= h.start and h.end <= k.end and _rank(k) <= _rank(h) for k in kept):
            continue
        kept.append(h)
    return sorted(kept, key=lambda h: h.start)


def scan(text: str, profile: Profile = LESSON, deny_terms: Iterable[str] = ()) -> list[Hit]:
    """Return hits for `text`. The matched text stays inside Hit.match only."""
    hits: list[Hit] = []
    if profile.secrets:
        hits += _regex_hits(text) + _entropy_hits(text) + _decoded_hits(text, 1)
    if profile.pii:
        hits += _pii_hits(text, deny_terms)
    if profile.long_tokens:
        hits += _long_token_hits(text)
    if profile.outbound:
        hits += _outbound_hits(text)
    return _dedupe(hits)


def _merge_spans(hits: list[Hit]) -> list[tuple[int, int, str]]:
    """Union overlapping hit spans so no tail of an overlapping secret survives."""
    spans: list[tuple[int, int, str]] = []
    for h in sorted(hits, key=lambda h: (h.start, h.end)):
        if spans and h.start < spans[-1][1]:
            start, end, rule = spans[-1]
            spans[-1] = (start, max(end, h.end), rule)
        else:
            spans.append((h.start, h.end, h.rule))
    return spans


def redact(text: str, hits: list[Hit]) -> str:
    """Outbound-only masking. Never used to keep a lesson (see quarantine)."""
    out, last = [], 0
    for start, end, rule in _merge_spans(hits):
        out.append(text[last:start])
        out.append(f"[REDACTED:{rule}]")
        last = end
    out.append(text[last:])
    return "".join(out)


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
