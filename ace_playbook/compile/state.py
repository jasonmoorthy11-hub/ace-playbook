"""Rolling per-session state for the live detectors (docs/design/compiler.md 4.2).

One small JSON file per session under `data_dir/sessions/`, named by the hash of the session id,
so a hostile id can never traverse paths. Paths inside are stored only as sha256[:16]. Writes are
temp-file-then-rename at 0600 under a non-blocking lock: a hook that cannot get the lock in three
tries skips its update and fails toward silence rather than hang. Corrupt, oversize or symlinked
state loads as `truncated`, which silences the detectors for that session.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from .detectors import MAX_PATHS, MAX_PENDING, blank_state

MAX_BYTES = 262144          # 256 KiB: a full 5,000-entry state is about 200 KiB
LOCK_TRIES = 3
LOCK_SLEEP = 0.01           # seconds between tries
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


class StateError(ValueError):
    """A session id or directory that state must refuse to touch."""


def session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8", "backslashreplace")).hexdigest()[:16]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_state(session_id: str, now: str | None = None) -> dict:
    return {**blank_state(), "session_hash": session_hash(session_id), "created": now or _now()}


def state_path(data_dir: str | Path, session_id: Any) -> Path:
    """`data_dir/sessions/<hash>.json`; raises StateError for a bad id or a symlinked directory."""
    if not isinstance(session_id, str) or not session_id:
        raise StateError("session id must be a non-empty string")
    base = Path(data_dir) / "sessions"
    if base.is_symlink():
        raise StateError("sessions directory is a symlink")
    path = base / (session_hash(session_id) + ".json")
    if path.parent.resolve() != base.resolve() or os.sep in path.name:   # the file itself may be a symlink
        raise StateError("state path escapes the sessions directory")
    return path


def _silent(session_id: str, now: str | None) -> dict:
    return {**new_state(session_id, now), "truncated": True}


def _clean(obj: Any, session_id: str, now: str | None) -> dict | None:
    """Rebuild a state from untrusted JSON, or None if it does not fit the schema."""
    if not isinstance(obj, dict) or obj.get("v") != 1:
        return None
    out = new_state(session_id, now)
    if obj.get("session_hash", out["session_hash"]) != out["session_hash"]:
        return None
    for key in ("seen_paths", "turn_paths"):
        vals = obj.get(key, [])
        if not isinstance(vals, list) or not all(isinstance(x, str) and _HEX16.match(x) for x in vals):
            return None
        out[key] = vals[:MAX_PATHS]
        out["truncated"] = out["truncated"] or len(vals) > MAX_PATHS
    git = obj.get("git", out["git"])
    if not isinstance(git, dict) or not all(isinstance(git.get(k, 0), int) and git.get(k, 0) >= 0
                                            for k in ("commit", "push")):
        return None
    out["git"] = {"commit": git.get("commit", 0), "push": git.get("push", 0)}
    pending = obj.get("pending", {})
    if not isinstance(pending, dict) or not all(
            isinstance(k, str) and len(k) <= 128 and isinstance(v, list) and len(v) == 3
            and isinstance(v[0], str) and isinstance(v[1], bool) and isinstance(v[2], bool)
            for k, v in pending.items()):
        return None
    out["pending"] = dict(list(pending.items())[-MAX_PENDING:])
    if not isinstance(obj.get("events", 0), int) or not isinstance(obj.get("truncated", False), bool):
        return None
    out["events"] = max(0, obj.get("events", 0))
    out["truncated"] = out["truncated"] or obj.get("truncated", False)
    if isinstance(obj.get("created"), str):
        out["created"] = obj["created"][:32]
    return out


def load(data_dir: str | Path, session_id: str, now: str | None = None) -> dict:
    path = state_path(data_dir, session_id)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return new_state(session_id, now)
    except OSError:                       # a symlink (ELOOP) or an unreadable file
        return _silent(session_id, now)
    try:
        info = os.fstat(fd)
        if not (info.st_mode & 0o170000) == 0o100000 or info.st_size > MAX_BYTES:
            return _silent(session_id, now)
        raw = b""
        while len(raw) <= MAX_BYTES:
            chunk = os.read(fd, MAX_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    except OSError:
        return _silent(session_id, now)
    finally:
        os.close(fd)
    try:
        cleaned = _clean(json.loads(raw), session_id, now)
    except ValueError:
        cleaned = None
    return cleaned if cleaned is not None else _silent(session_id, now)


def _serialize(state: dict) -> bytes:
    return json.dumps(state, sort_keys=True, separators=(",", ":")).encode()


def save(data_dir: str | Path, session_id: str, state: dict) -> bool:
    """Atomically write `state` at 0600; oversize state is cut down and marked truncated.

    Raises OSError if the write fails; the previous file is then left untouched.
    """
    path = state_path(data_dir, session_id)
    s = dict(state)
    for key in ("seen_paths", "turn_paths"):
        vals = list(s.get(key) or [])
        if len(vals) > MAX_PATHS:
            s["truncated"] = True
        s[key] = vals[:MAX_PATHS]
    data = _serialize(s)
    if len(data) > MAX_BYTES:
        s.update(truncated=True, seen_paths=[], turn_paths=[], pending={})
        data = _serialize(s)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def update(data_dir: str | Path, session_id: str, fn: Callable[[dict], dict],
           now: str | None = None) -> tuple[dict | None, str]:
    """Load, apply `fn`, save, all under a non-blocking lock. Returns (state, status).

    Status is "ok", "skipped:locked" (three tries, 10 ms apart, then give up) or "skipped:error".
    Exceptions raised by `fn` propagate after the lock is released.
    """
    try:
        path = state_path(data_dir, session_id)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except (StateError, OSError):
        return None, "skipped:error"
    try:
        for attempt in range(LOCK_TRIES):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if attempt == LOCK_TRIES - 1:
                    return None, "skipped:locked"
                time.sleep(LOCK_SLEEP)
        new = fn(load(data_dir, session_id, now))
        try:
            save(data_dir, session_id, new)
        except OSError:
            return None, "skipped:error"
        return new, "ok"
    finally:
        os.close(fd)     # closing the descriptor releases the lock
