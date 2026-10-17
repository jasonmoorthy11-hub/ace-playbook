"""Inertness check for text that will be injected into a session.

Fail closed, in two layers. Layer one is structural and lives in the schema: lesson
text is printable ASCII only, one line, no "===" fence. Layer two is the regex list
below (URLs, domains, scheme-like tokens, shell syntax, tool imperatives, override
wording, agent-configuration wording). A lesson that fails any check is left out of
the block (and the reason is logged), never rewritten.
"""

from __future__ import annotations

import re

from ..store.schema import unsafe_text_reason

# Extensions that are not delegated TLDs, plus .py/.md which are too common in lessons to ban.
BARE_OK_EXTS = frozenset(
    "json jsonl toml yml yaml txt js ts tsx jsx cfg ini lock log csv html css rb java cpp hpp php "
    "sql xml pdf png jpg jpeg gif svg gz tar env conf pem key bak tmp pyc ipynb mjs cjs vue swift "
    "lua mdx rst cff plist db sqlite sock pid out lockb py md".split())
# Real TLDs (.rs Serbia, .zip, .pl Poland, .sh, ...): a file name only inside a real path ("src/lib.rs").
PATH_ONLY_EXTS = frozenset("rs zip pl sh kt cs go mov cc ws to so".split())

_DOTTED = re.compile(r"(?<![\w.-])([\w-]+)((?:\.[\w-]+)+)(?![\w-])")
_OPENERS = frozenset(" \t(\"'[,;")


def _host_like(text: str) -> bool:
    """True if any dotted token could be a host name, other than a plain file name."""
    for m in _DOTTED.finditer(text):
        labels = m.group(2).lstrip(".").split(".")
        last = labels[-1]
        if not (len(last) >= 2 and last.isalpha()):  # TLD must be 2+ letters (any script)
            continue
        if len(labels) == 1 and _plain_filename(text, m, last.lower()):
            continue
        return True
    return False


def _plain_filename(text: str, m: re.Match, ext: str) -> bool:
    prev = text[m.start() - 1] if m.start() else ""
    after = text[m.end()] if m.end() < len(text) else ""
    if after and after in ":?#@/":
        return False
    if prev == "/":  # a real path segment: "src/main.py", not "//evil.md" or ":/evil.rs"
        before = text[m.start() - 2] if m.start() >= 2 else ""
        if not (before.isalnum() or before in "._~-"):
            return False
        return ext in BARE_OK_EXTS or ext in PATH_ONLY_EXTS
    if prev == "" or prev in _OPENERS:
        return ext in BARE_OK_EXTS
    return False


_CONFIG_STRONG = (r"auto[\s_-]?approve|bypass[\s_-]?permissions|dangerously|allowed[\s_-]?tools|"
                  r"skip[\s_-]?permissions|\.claude\b|claude\.md|settings(?:\.local)?\.json|"
                  r"settings\.local|\.mcp\.json")
_CONFIG_WORD = r"(?:settings|permissions?|hooks?|mcp\s+servers?)"
_MODIFY = (r"(?:set|change|edit|modify|disable|enable|add|update|write|grant|allow|turn|remove|"
           r"delete|overwrite|append|configure|toggle|replace|insert|patch)")

_CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("url", re.compile(r"(?i)(?:\b[a-z][a-z0-9+.\-]*:/|\bwww\.|\b[a-z0-9\-]+\.(?:com|net|org|io|dev|sh)/)")),
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
