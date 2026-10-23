"""Layer 1 regex rules and the lint patterns used by the scrubber."""

from __future__ import annotations

import re

# (rule name, compiled pattern). Order matters only for overlap tie-breaks:
# earlier rules win when two rules cover the same span.
PROVIDER_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{50,}")),
    (
        "jwt",
        re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}+\.eyJ[A-Za-z0-9_\-]{8,}+\.[A-Za-z0-9_\-]{8,}"),
    ),
    (
        "railway_token",
        re.compile(r"(?<![A-Z_])RAILWAY_[A-Z_]*(?:TOKEN|KEY)\s*[=:]\s*['\"]?[A-Za-z0-9\-_]{16,}"),
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
        re.compile(r"(?<![A-Z_])VERCEL_[A-Z_]*(?:TOKEN|KEY)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    ),
    ("linear_key", re.compile(r"lin_(?:api|oauth)_[A-Za-z0-9]{30,}")),
    ("neon_key", re.compile(r"napi_[A-Za-z0-9]{40,}")),
    ("aws_access_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("stripe_key", re.compile(r"[sr]k_live_[A-Za-z0-9]{16,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=/+~]{20,}")),
    (
        "url_userinfo",
        re.compile(r"(?<![\w+.\-])[a-z][a-z0-9+.\-]*://[^\s/:@]+:[^\s/@]{3,}@"),
    ),
    # S-1 F12. The two assignment rules were `[A-Z0-9_]*KEYWORD[A-Z0-9_]*\s*[=:]`, which re-walked a whole identifier run for each keyword in it.
    # A run starts once (the lookbehind / `\b`), a lookahead finds a keyword in it, and the possessive repeat takes the run whole: linear.
    (
        "secret_assignment",
        re.compile(
            r"(?<![A-Z0-9_])(?=[A-Z0-9_]*?(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY))"
            r"[A-Z0-9_]++\s*[=:]\s*['\"]?([^\s'\"\\]{12,})"
        ),
    ),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?=[a-z0-9_]*?(?:secret|token|password|passwd|api_?key))"
            r"[a-z0-9_]++\s*[=:]\s*['\"]([^\s'\"]{12,})['\"]"
        ),
    ),
]

PLACEHOLDER_PREFIXES = (
    "$", "<", "{", "your", "xxx", "...", "changeme", "example", "placeholder",
    "none", "null", "true", "false", "os.environ", "env[", "getenv",
)

_EMAIL_BODY = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+\-])" + _EMAIL_BODY)         # S-1 F9: one start per run, so a long run with no `@` is scanned once
EMAIL_TAIL = re.compile(_EMAIL_BODY)                                # the same without the guard: for a match that starts where the last one ended


def emails(line: str):
    """The email matches of one line, exactly those a plain `EMAIL_TAIL.finditer(line)` finds (S-1 Opus N-1). A match ends inside a run of local-part
    characters (its TLD is letters only), so the next one may start right there, where the guard forbids a start: after every match, safe ones
    included, an unguarded attempt is made at its end, and repeated from the end of each success (three addresses glued with `_`). One extra attempt
    per match, each scanning the rest of one run at most once: linear."""
    pos = 0
    while m := EMAIL.search(line, pos):
        yield m
        pos = m.end()
        while m := EMAIL_TAIL.match(line, pos):
            yield m
            pos = m.end()


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

ENTROPY_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_\-+/=])[A-Za-z0-9_\-+/=]{24,}")
B64_CANDIDATE = re.compile(r"[A-Za-z0-9+/_\-]{24,}={0,2}")

# Outbound-only (text sent to the reflector): any 20+ char token mixing letters and digits
# (bare UUIDs, hex digests, short passwords) and lowercase secret-ish key=value pairs.
MIXED_RUN = re.compile(r"(?<![A-Za-z0-9_\-+=])[A-Za-z0-9_\-+=]{20,}(?![A-Za-z0-9_\-+=])")
LOWER_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?=[a-z0-9_]*?(?:secret|token|password|passwd|pwd|api_?key|credential|private_?key))"
    r"[a-z0-9_]++\s*[=:]\s*['\"]?([^\s'\"]{4,})"
)
