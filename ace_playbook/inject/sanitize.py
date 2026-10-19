"""Inertness check for text that will be injected into a session.

Fail closed, in two layers. Layer one is structural and lives in the schema: lesson
text is printable ASCII only, one line, no "===" fence. Layer two is the regex list
below (URLs, domains, scheme-like tokens, shell syntax, tool imperatives, override
wording, agent-configuration wording). A lesson that fails any check is left out of
the block (and the reason is logged), never rewritten.
"""

from __future__ import annotations

import re
import string

from ..store.schema import unsafe_text_reason

# Well-known file names that may appear bare. Everything else dotted is host-like.
KNOWN_FILES = frozenset(
    "README.md CHANGELOG.md LICENSE.md setup.py conftest.py pyproject.toml package.json "
    "SKILL.md CLAUDE.md".split())

_DOTTED = re.compile(r"(?<![\w.-])([\w-]+(?:\.[\w-]+)+)")
_IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w])")
_SCHEME = re.compile(r"(?i)(?<![a-z0-9+.\-])[a-z][a-z0-9+.\-]*:/|[\w+.\-]:/|://")
_PUNYCODE = re.compile(r"(?i)(?<![\w-])xn--")
_PATH_CHARS = frozenset("/.~-_" + string.ascii_letters + string.digits)


def _path_prefix_ok(text: str, start: int) -> bool:
    """True if the char before `start` is '/' closing a genuine path segment [\\w.-]+/ that is
    not part of '//' or a scheme-like ':/' or user@host/ prefix."""
    i = start - 1
    if i < 0 or text[i] != "/":
        return False
    j = i
    while j > 0 and text[j - 1] in _PATH_CHARS:
        j -= 1
    run = text[j:i + 1]
    if "//" in run or (j > 0 and text[j - 1] in ":@"):
        return False
    seg = run[:-1].rsplit("/", 1)[-1]
    return bool(seg) and all(c.isalnum() or c in "._-~" for c in seg)


def _exempt(text: str, start: int, end: int, token: str) -> bool:
    if end < len(text) and text[end] in ":?#@/":
        return False
    prev = text[start - 1] if start else ""
    if prev == "/":
        return _path_prefix_ok(text, start)
    if prev in ":@":
        return False
    return token in KNOWN_FILES


def _host_like(text: str) -> bool:
    """True if any dotted token ends in a label with a letter (a possible TLD), unless it is
    a known file name or the last segment of a genuine path. Single-letter final labels
    (e.g., i.e.) cannot be TLDs and are ignored."""
    for m in _DOTTED.finditer(text):
        token = m.group(1)
        last = token.rsplit(".", 1)[-1]
        if len(last) < 2 or not any(c.isalpha() for c in last):
            continue
        if _exempt(text, m.start(1), m.end(1), token):
            continue
        return True
    return False


_CONFIG_STRONG = (r"auto[\s_-]?approve|bypass[\s_-]?permissions|dangerously|allowed[\s_-]?tools|"
                  r"skip[\s_-]?permissions|\.claude\b|claude\.md|settings(?:\.local)?\.json|"
                  r"settings\.local|\.mcp\.json")
_CONFIG_WORD = r"(?:settings|permissions?|hooks?|mcp\s+servers?)"
_MODIFY = (r"(?:set|change|edit|modify|disable|enable|add|update|write|grant|allow|turn|remove|"
           r"delete|overwrite|append|configure|toggle|replace|insert|patch)")

_CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("url", _SCHEME),
    ("ip_literal", _IPV4),
    ("punycode", _PUNYCODE),
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
            return "invisible_chars" if reason == "non_ascii_or_control" else reason
    text = " ".join(" ".join(parts).split())
    for name, pat in _CHECKS:
        if pat.search(text):
            return name
        if name == "url" and _host_like(text):
            return "domain"
    return None
