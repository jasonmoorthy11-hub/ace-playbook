"""Inertness check for text that will be injected into a session.

Fail closed: a lesson that fails any check is left out of the block (and the
reason is logged), never rewritten. The injected block is advisory data; no
lesson may carry a URL, code, shell syntax or an instruction aimed at a tool.
"""

from __future__ import annotations

import re
import unicodedata

_CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("url", re.compile(r"(?i)(?:[a-z][a-z0-9+.\-]*://|\bwww\.|\b[a-z0-9\-]+\.(?:com|net|org|io|dev|sh)/)")),
    ("code_fence_or_backtick", re.compile(r"`")),
    ("shell_syntax", re.compile(r"\$\(|\$\{|&&|\|\||\s\|\s|>>|\s>\s|;\s*(?:rm|curl|wget|sh|bash)\b")),
    ("angle_brackets", re.compile(r"[<>]")),
    ("tool_imperative",
     re.compile(r"(?i)\b(?:run|execute|invoke|call|use)\s+(?:the\s+)?"
                r"(?:bash|shell|terminal|sudo|curl|wget|eval|\w+\s+tool|\w+\s+command)\b")),
    ("leading_command",
     re.compile(r"(?i)^\s*(?:sudo|curl|wget|rm|chmod|eval|exec|bash|sh)\b")),
    ("override_attempt",
     re.compile(r"(?i)ignore (?:all |any )?(?:previous|prior|above)|disregard (?:the )?(?:above|instructions)"
                r"|you must now|new instructions|system prompt")),
]


def invisible_chars(text: str) -> bool:
    return any(unicodedata.category(c) in ("Cf", "Cc") and c not in "\n\t" for c in text)


def inert_reason(*parts: str) -> str | None:
    """Return the first reason the text is not inert, or None if it is safe to inject."""
    text = " ".join(parts)
    if invisible_chars(text):
        return "invisible_chars"
    for name, pat in _CHECKS:
        if pat.search(text):
            return name
    return None
