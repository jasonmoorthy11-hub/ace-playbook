"""Inertness check for text that will be injected into a session.

Fail closed: a lesson that fails any check is left out of the block (and the
reason is logged), never rewritten. The injected block is advisory data; no
lesson may carry a URL, domain, code, shell syntax, an instruction aimed at a
tool, wording that targets agent configuration, or any character that could
break out of its one-line slot in the block.

All regexes run on the NFKC-normalized, whitespace-collapsed text so fullwidth
and other compatibility look-alikes cannot dodge them.
"""

from __future__ import annotations

import re
import unicodedata

from ..store.schema import unsafe_text_reason

# File extensions that look like a TLD; "main.py" is a file name, not a domain.
FILE_EXTS = (
    "py|md|json|jsonl|toml|yml|yaml|txt|js|ts|tsx|jsx|cfg|ini|lock|log|csv|html|css|rs|go|rb|java|"
    "c|h|cpp|hpp|cs|php|sql|xml|pdf|png|jpg|jpeg|gif|svg|zip|gz|tar|env|conf|pem|key|bak|tmp|pyc|"
    "ipynb|mjs|cjs|vue|swift|kt|lua|pl|r|mdx|rst|cff|plist|db|sqlite|sock|pid|out|lockb")

_CONFIG_STRONG = (r"auto[\s_-]?approve|bypass[\s_-]?permissions|dangerously|allowed[\s_-]?tools|"
                  r"skip[\s_-]?permissions|\.claude\b|claude\.md|settings(?:\.local)?\.json|"
                  r"settings\.local|\.mcp\.json")
_CONFIG_WORD = r"(?:settings|permissions?|hooks?|mcp\s+servers?)"
_MODIFY = (r"(?:set|change|edit|modify|disable|enable|add|update|write|grant|allow|turn|remove|"
           r"delete|overwrite|append|configure|toggle|replace|insert|patch)")

_CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("url", re.compile(r"(?i)(?:[a-z][a-z0-9+.\-]*://|\bwww\.|\b[a-z0-9\-]+\.(?:com|net|org|io|dev|sh)/)")),
    ("domain", re.compile(rf"(?i)\b[\w-]+(?:\.[\w-]+)*\.(?!(?:{FILE_EXTS})\b)[a-z]{{2,}}\b")),
    ("code_fence_or_backtick", re.compile(r"`")),
    ("shell_syntax", re.compile(r"\$\(|\$\{|&&|\|\||\s\|\s|>>|\s>\s|;\s*(?:rm|curl|wget|sh|bash)\b")),
    ("pipe_to_shell",
     re.compile(r"(?i)\b(?:pip(?:e|es|ed|ing)|feed|redirect|stream)\b.{0,60}\b(?:sh|bash|zsh|shell)\b")),
    ("angle_brackets", re.compile(r"[<>]")),
    ("tool_imperative",
     re.compile(r"(?i)\b(?:run|execute|invoke|call|use)\s+(?:the\s+)?"
                r"(?:bash|shell|terminal|sudo|curl|wget|eval|\w+\s+tool|\w+\s+command)\b")),
    ("leading_command",
     re.compile(r"(?i)^\s*(?:sudo|curl|wget|rm|chmod|eval|exec|bash|sh)\b")),
    ("override_attempt",
     re.compile(r"(?i)\b(?:ignore|disregard|forget|override)\s+(?:(?:all|any|the|your|my|these|those)\s+)*"
                r"(?:previous|prior|above|earlier|preceding|former|existing|other)\b"
                r"|disregard (?:the )?(?:above|instructions)"
                r"|you must now|new instructions|system prompt")),
    ("config_target",
     re.compile(rf"(?i)(?<!\w)(?:{_CONFIG_STRONG})"
                rf"|\b{_MODIFY}\b.{{0,40}}\b{_CONFIG_WORD}\b"
                rf"|\b{_CONFIG_WORD}\b.{{0,40}}\b{_MODIFY}\b")),
]


def invisible_chars(text: str) -> bool:
    return unsafe_text_reason(text) is not None


def inert_reason(*parts: str) -> str | None:
    """Return the first reason the text is not inert, or None if it is safe to inject."""
    for part in parts:
        reason = unsafe_text_reason(part)
        if reason:
            return "invisible_chars" if reason == "invisible_or_symbol_char" else reason
    text = " ".join(unicodedata.normalize("NFKC", " ".join(parts)).split())
    for name, pat in _CHECKS:
        if pat.search(text):
            return name
    return None
