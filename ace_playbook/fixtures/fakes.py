"""Fake credentials and PII for the synthetic corpus.

Every planted value contains the marker FAKE. They are assembled at runtime so the
generator's own source holds no token-shaped literal. The pre-commit scrub check
tolerates a hit inside the frozen fixtures only if the matched text contains FAKE
(directly or after base64 decoding).
"""

from __future__ import annotations

import base64
import random
import string

ALNUM = string.ascii_letters + string.digits
HOME_PREFIX = "/" + "Users/"


def _fill(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(ALNUM) for _ in range(n))


def planted(rng: random.Random) -> dict[str, str]:
    """One fake secret per scrubber rule (plus PII), keyed by the rule that should fire."""
    jwt = ".".join(["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiJGQUtFIn0", "FAKEsig" + _fill(rng, 24)])
    return {
        "anthropic_key": "sk-" + "ant-" + "FAKE" + _fill(rng, 36),
        "openai_key": "sk-" + "proj-" + "FAKE" + _fill(rng, 36),
        "github_token": "gh" + "p_" + "FAKE" + _fill(rng, 32),
        "jwt": jwt,
        "railway_token": "RAILWAY_" + "API_TOKEN=" + "FAKE-" + _fill(rng, 20),
        "vercel_token": "VERCEL_" + "TOKEN=" + "FAKE" + _fill(rng, 20),
        "linear_key": "lin_" + "api_" + "FAKE" + _fill(rng, 36),
        "neon_key": "na" + "pi_" + "FAKE" + _fill(rng, 44),
        "bearer_token": "Authorization: Bearer " + "FAKE" + _fill(rng, 30),
        "email": "pat.example@" + "FAKE-CORP.io",
        "user_path": HOME_PREFIX + "FAKEuser/projects/acme",
    }


def encoded(secret: str, depth: int = 2) -> str:
    for _ in range(depth):
        secret = base64.b64encode(secret.encode()).decode()
    return secret
