"""Health line: the nightly sweep records its outcome in `health.json` (0600), and the
SessionStart injector warns once the sweep has failed several nights running.

Nothing here reads the API key or the delta log, and nothing here raises to its caller: a
broken health file must never change what the sweep or a session does.
"""

from __future__ import annotations

import json
import os
import time

from .config import Config
from .scrub import LESSON, OUTBOUND, redact, scan

WARN_AFTER = 3
MAX_ERROR_CHARS = 200
READ_LIMIT = 4096
NO_RECORD = "no health record"


def health_path(cfg: Config):
    return cfg.path("health.json")


def scrub_error(text: str, deny_terms: tuple[str, ...] = ()) -> str:
    """One line, secrets and personal data masked first, then cut to MAX_ERROR_CHARS."""
    text = " ".join(str(text).split())
    hits = scan(text, OUTBOUND, deny_terms) + scan(text, LESSON, deny_terms)
    return (redact(text, hits) if hits else text)[:MAX_ERROR_CHARS]


def _write_private(path, payload: dict) -> None:
    """Atomic replace; the file is created 0600 and never readable by others."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(json.dumps(payload, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            if fd >= 0:
                os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read(cfg: Config) -> dict | None:
    """The parsed health record, or None if it is missing, unreadable or not a JSON object."""
    try:
        with open(health_path(cfg), "rb") as fh:
            rec = json.loads(fh.read(READ_LIMIT))  # a real record is well under 1 KiB
    except (OSError, ValueError, RecursionError):  # ValueError covers bad JSON and bad UTF-8
        return None
    return rec if isinstance(rec, dict) else None


def failures(cfg: Config) -> int:
    """Consecutive failures on record; 0 when the record is absent or malformed."""
    rec = read(cfg)
    n = rec.get("consecutive_failures") if rec else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def _describe(exc: BaseException) -> str:
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:  # noqa: BLE001 - a hostile __str__ must not lose the record
        return type(exc).__name__


def record(cfg: Config, ok: bool, error: str = "", now: float | None = None,
           exc: BaseException | None = None) -> None:
    """Write the outcome of one sweep run. Swallows every error, including a failing str(exc)."""
    try:
        if exc is not None:
            error = _describe(exc)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        _write_private(health_path(cfg), {
            "last_run": stamp, "ok": bool(ok),
            "consecutive_failures": 0 if ok else failures(cfg) + 1,
            "last_error": "" if ok else scrub_error(error, tuple(cfg.deny_terms))})
    except Exception:  # noqa: BLE001 - health writing must not change the sweep's outcome
        pass


def warning_line(cfg: Config) -> str | None:
    """The one-line injector warning once failures reach WARN_AFTER, else None."""
    try:
        n = failures(cfg)
        if n < WARN_AFTER:
            return None
        return (f"- ace-playbook: nightly sweep failed {min(n, 99999)} times in a row; "
                "see the reflect log in the data dir")
    except Exception:  # noqa: BLE001 - a bad health file adds nothing
        return None
