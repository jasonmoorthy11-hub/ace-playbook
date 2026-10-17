"""Layer 1 regex rules and the lint patterns used by the scrubber."""

from __future__ import annotations

import re

# (rule name, compiled pattern). Order matters only for overlap tie-breaks:
# earlier rules win when two rules cover the same span.
SECRET_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    ),
    (
        "railway_token",
        re.compile(r"\bRAILWAY_[A-Z_]*(?:TOKEN|KEY)\s*[=:]\s*['\"]?[A-Za-z0-9\-_]{16,}"),
    ),
    (
        "railway_uuid_context",
        re.compile(
            r"(?i)railway[^\n]{0,40}?[=:\s\"']\s*"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        ),
    ),
    (
        "vercel_token",
        re.compile(r"\bVERCEL_[A-Z_]*(?:TOKEN|KEY)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    ),
    ("linear_key", re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{30,}")),
    ("neon_key", re.compile(r"\bnapi_[A-Za-z0-9]{40,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("stripe_key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}")),
    (
        "url_userinfo",
        re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:[^\s/@]{3,}@"),
    ),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY)"
            r"[A-Z0-9_]*\s*[=:]\s*['\"]?([^\s'\"]{12,})"
        ),
    ),
]

PLACEHOLDER_PREFIXES = (
    "$", "<", "{", "your", "xxx", "...", "changeme", "example", "placeholder",
    "none", "null", "true", "false", "os.environ", "env[", "getenv",
)

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "users.noreply.github.com"}
SAFE_EMAIL_LOCALS = {"noreply", "no-reply"}

PHONE = re.compile(r"(?<![\d.])(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?![\d.])")

# Home-directory style paths. The two prefixes are split so this module does
# not itself contain the literal string the repo denylist greps for.
USER_PATH = re.compile(
    r"(?:/" + r"Users|/" + r"home)/([A-Za-z0-9._\-]+)/|C:\\" + r"Users\\([A-Za-z0-9._\-]+)\\"
)
SAFE_PATH_NAMES = {
    "runner", "you", "user", "username", "me", "name", "example", "someone",
    "shared", "Shared", "$HOME", "<you>", "<user>",
}

# Lesson profile only: hex or base64-looking runs longer than 12 characters.
LONG_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{13,}(?![0-9A-Za-z])")
LONG_B64 = re.compile(r"(?<![0-9A-Za-z+/])[A-Za-z0-9+/_\-]{13,}={0,2}(?![0-9A-Za-z+/])")

ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")
B64_CANDIDATE = re.compile(r"[A-Za-z0-9+/_\-]{24,}={0,2}")
