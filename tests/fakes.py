"""Fake credentials assembled at runtime so no token-shaped literal sits in the source."""

import base64

FILL = "aB3dE5fG7hJ9" "kL2mN4pQ6rS8" "tU0vW1xY"


def anthropic() -> str:
    return "sk-" + "ant-" + "FAKE" + FILL


def openai() -> str:
    return "sk-" + "proj-" + "FAKE" + FILL


def github() -> str:
    return "gh" + "p_" + "FAKE" + FILL[:32]


def jwt() -> str:
    return ".".join(["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiJGQUtFIn0", "FAKEsignatureAbC123xyz"])


def railway() -> str:
    return "RAILWAY_" + "API_TOKEN=" + "FAKE-" + "0f3a9c1d-77b2-4e8a"


def railway_uuid() -> str:
    return "railway token: " + "-".join(["5b0c7e1a", "9d42", "4f6b", "8a31", "c2d4e6f80a1b"])


def vercel() -> str:
    return "VERCEL_" + "TOKEN=" + "FAKE" + FILL[:20]


def linear() -> str:
    return "lin_" + "api_" + "FAKE" + FILL


def neon() -> str:
    return "na" + "pi_" + "fake" + FILL.lower() + FILL.lower()[:10]


def bearer() -> str:
    return "Authorization: Bearer " + "FAKE" + FILL


def b64(s: str, times: int = 1) -> str:
    for _ in range(times):
        s = base64.b64encode(s.encode()).decode()
    return s


ALL = {
    "anthropic_key": anthropic, "openai_key": openai, "github_token": github, "jwt": jwt,
    "railway_token": railway, "railway_uuid_context": railway_uuid, "vercel_token": vercel,
    "linear_key": linear, "neon_key": neon, "bearer_token": bearer,
}
