"""Minimal Messages API client for the Haiku 5.5 reflector (stdlib urllib only).

Notes from the API-code checklist for Haiku 5.5: thinking is on by default, so the
text is selected by block type (content[0] may be an empty thinking block) and
thinking counts toward max_tokens; no temperature/top_p/top_k and no budget_tokens
are sent; stop_reason "refusal" has no server-side fallback and is handled here.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config

Transport = Callable[[str, dict, bytes, float], tuple[int, bytes]]
RETRY_STATUS = {429, 500, 502, 503, 529}
BACKOFF_S = (2.0, 6.0)
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens")


class TransportError(Exception):
    """Network-level failure (DNS, timeout, reset)."""


@dataclass
class Result:
    status: str  # ok | refusal | truncated | error
    text: str = ""
    stop_reason: str = ""
    category: str = ""
    usage: dict = field(default_factory=dict)
    http_status: int = 0
    error: str = ""


def urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransportError(type(e).__name__) from None


def log_line(cfg: Config, message: str) -> None:
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with open(cfg.log, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n")
    except OSError:
        pass


class ReflectorClient:
    def __init__(self, cfg: Config, transport: Transport = urllib_transport,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg, self.transport, self.sleep = cfg, transport, sleep

    def _key(self) -> str:
        path = self.cfg.api_key_path
        if not path or not path.exists():
            raise FileNotFoundError("api key file not configured or missing")
        key = path.read_text().strip()
        if not key:
            raise ValueError("api key file is empty")
        return key

    def _body(self, system: str, user: str, with_effort: bool) -> bytes:
        body: dict = {
            "model": self.cfg.model, "max_tokens": self.cfg.max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if with_effort and self.cfg.effort:
            body["output_config"] = {"effort": self.cfg.effort}
        return json.dumps(body).encode()

    def _post(self, headers: dict, system: str, user: str) -> tuple[int, bytes]:
        with_effort = True
        for attempt in range(len(BACKOFF_S) + 1):
            status, raw = self.transport(self.cfg.api_url, headers,
                                         self._body(system, user, with_effort),
                                         self.cfg.timeout_s)
            if status == 400 and with_effort and (b"output_config" in raw or b"effort" in raw):
                with_effort = False  # effort not supported here: retry once without it
                continue
            if status in RETRY_STATUS and attempt < len(BACKOFF_S):
                self.sleep(BACKOFF_S[attempt])
                continue
            return status, raw
        return status, raw

    def complete(self, system: str, user: str) -> Result:
        try:
            key = self._key()
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
            status, raw = self._post(headers, system, user)
        except (FileNotFoundError, ValueError, TransportError) as e:
            return self._done(Result("error", error=f"{type(e).__name__}: {e}"))
        return self._done(parse_response(status, raw))

    def _done(self, res: Result) -> Result:
        log_line(self.cfg, f"api status={res.status} http={res.http_status} "
                           f"stop={res.stop_reason or '-'} cat={res.category or '-'} "
                           f"usage={json.dumps(res.usage, sort_keys=True)} err={res.error[:120]}")
        return res


def parse_response(status: int, raw: bytes) -> Result:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Result("error", http_status=status, error="non-JSON response")
    if status != 200 or data.get("type") == "error":
        err = data.get("error", {}) if isinstance(data, dict) else {}
        return Result("error", http_status=status,
                      error=f"{err.get('type', 'http_error')}: {str(err.get('message', ''))[:200]}")
    usage = {k: data["usage"][k] for k in USAGE_KEYS
             if isinstance(data.get("usage"), dict) and isinstance(data["usage"].get(k), int)}
    stop = str(data.get("stop_reason") or "")
    if stop == "refusal":
        details = data.get("stop_details") or {}
        return Result("refusal", stop_reason=stop, usage=usage, http_status=status,
                      category=str(details.get("category") or ""))
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if isinstance(b, dict) and b.get("type") == "text")
    if stop == "max_tokens":
        return Result("truncated", text, stop, usage=usage, http_status=status,
                      error="output hit max_tokens (thinking counts toward it)")
    return Result("ok", text, stop, usage=usage, http_status=status)
