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

# Extensions that are not delegated TLDs: a bare "main.json" is a file name.
SAFE_EXTS = frozenset(
    "json jsonl toml yml yaml txt js ts tsx jsx cfg ini lock log csv html css rb java cpp hpp php "
    "sql xml pdf png jpg jpeg gif svg gz tar env conf pem key bak tmp pyc ipynb mjs cjs vue swift "
    "lua mdx rst cff plist db sqlite sock pid out lockb".split())
# Extensions that are also real TLDs (.py Paraguay, .md Moldova, .rs Serbia, .zip, .pl, ...).
# Exempt only as the last segment of a path ("src/baz.py"), never bare and never followed by "/".
AMBIGUOUS_EXTS = frozenset("py md rs zip pl sh kt cs go mov cc ws to so".split())

_DOT_LIKES = str.maketrans({c: "." for c in "\u3002\uff61\uff0e\ufe52\u2024\ufe12\u06d4\u0701\u0702"})
_CONFUSABLES = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u0501\u051b\u051d\u04bb\u043a\u043c\u0442"
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0406\u0408\u0405"
    "\u03bf\u03b1\u03bd\u03b9\u03ba\u03c1\u03c4\u03c5\u03c7\u03b5"
    "\u039f\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u03a1\u03a4\u03a5\u03a7"
    "\u0131\u0261\u0269",
    "aeopcyxijsdqwhkmt"
    "ABEKMHOPCTXIJS"
    "oavikptuxe"
    "OABEZHIKMNPTYX"
    "igi")
_DOMAIN_RE = re.compile(r"(?i)\b[\w-]+(?:\.[\w-]+)*\.([a-z]{2,})\b")


def skeleton(text: str) -> str:
    """NFKC, dot-like punctuation to '.', Cyrillic/Greek look-alikes to Latin, spaces collapsed."""
    t = unicodedata.normalize("NFKC", text).translate(_DOT_LIKES).translate(_CONFUSABLES)
    return " ".join(t.split())


def _domain_like(text: str) -> bool:
    for m in _DOMAIN_RE.finditer(text):
        ext = m.group(1).lower()
        if ext in SAFE_EXTS:
            continue
        before = text[m.start() - 1] if m.start() else ""
        after = text[m.end()] if m.end() < len(text) else ""
        if ext in AMBIGUOUS_EXTS and before == "/" and after != "/":
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
    ("url", re.compile(r"(?i)(?:[a-z][a-z0-9+.\-]*://|\bwww\.|\b[a-z0-9\-]+\.(?:com|net|org|io|dev|sh)/)")),
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
    text = skeleton(" ".join(parts))
    for name, pat in _CHECKS:
        if pat.search(text):
            return name
        if name == "url" and _domain_like(text):
            return "domain"
    return None
