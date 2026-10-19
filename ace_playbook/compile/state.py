"""Rolling per-session state for the live detectors (docs/design/compiler.md 4.2).

One small JSON file per session under `data_dir/shadow_state/`, named by the hash of the session id,
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
import stat
import time
from pathlib import Path
from typing import Any, Callable

from .detectors import MAX_PATHS, MAX_PENDING, blank_state

MAX_BYTES = 262144          # 256 KiB: a full 5,000-entry state is about 200 KiB
STATE_DIR = "shadow_state"
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
    """`data_dir/shadow_state/<hash>.json`; raises StateError for a bad id or a symlinked directory."""
    if not isinstance(session_id, str) or not session_id:
        raise StateError("session id must be a non-empty string")
    base = Path(data_dir) / STATE_DIR
    if base.is_symlink():
        raise StateError("state directory is a symlink")
    path = base / (session_hash(session_id) + ".json")
    if path.parent.resolve() != base.resolve() or os.sep in path.name:   # the file itself may be a symlink
        raise StateError("state path escapes the state directory")
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
    """The saved state, a fresh one if there is none, or a `truncated` one if the file is unusable."""
    try:
        path = state_path(data_dir, session_id)
    except (OSError, RuntimeError):       # e.g. a symlink loop in data_dir
        return _silent(session_id, now)
    try:
        # O_NONBLOCK: opening a FIFO planted at the state path must not stall the hook
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return new_state(session_id, now)
    except OSError:                       # a symlink (ELOOP) or an unreadable file
        return _silent(session_id, now)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or info.st_mode & 0o077:
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
    if len(raw) > MAX_BYTES:              # grew while we read it
        return _silent(session_id, now)
    try:
        cleaned = _clean(json.loads(raw), session_id, now)
    except (ValueError, RecursionError):  # deeply nested JSON raises RecursionError
        cleaned = None
    return cleaned if cleaned is not None else _silent(session_id, now)


def _ensure_dir(d: Path) -> None:
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(d)
    if not stat.S_ISDIR(info.st_mode):
        raise StateError("state directory is not a directory")
    if info.st_mode & 0o077:
        os.chmod(d, 0o700)                # an existing directory may be wider than we want


def _target_ok(path: Path) -> bool:
    """False if something other than a regular file (a FIFO, a symlink, a socket) sits at `path`."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except FileNotFoundError:
        return True
    except OSError:
        return False


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
    _ensure_dir(path.parent)
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

    Status is "ok", "skipped:locked" (three tries, 10 ms apart, then give up) or "skipped:error"
    (bad id, symlink or FIFO in the way, wide permissions, I/O failure). Nothing but `fn`'s own
    exceptions escapes; those propagate after the lock is released.
    """
    try:
        path = state_path(data_dir, session_id)
        _ensure_dir(path.parent)
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except (StateError, OSError, RuntimeError):
        return None, "skipped:error"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            return None, "skipped:error"
        for attempt in range(LOCK_TRIES):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if attempt == LOCK_TRIES - 1:
                    return None, "skipped:locked"
                time.sleep(LOCK_SLEEP)
        try:
            if not _target_ok(path):
                return None, "skipped:error"
            current = load(data_dir, session_id, now)
        except (StateError, OSError, RuntimeError):
            return None, "skipped:error"
        new = fn(current)
        try:
            save(data_dir, session_id, new)
        except (StateError, OSError, RuntimeError):
            return None, "skipped:error"
        return new, "ok"
    finally:
        os.close(fd)     # closing the descriptor releases the lock


def prune(data_dir: str | Path, before: float, dry_run: bool = False) -> int:
    """Delete (dry_run: only count) the regular `<hash>.json` and `<hash>.json.lock` files under shadow_state/ last
    modified before `before` (epoch seconds). A symlink, any other name and a symlinked directory are left alone."""
    base, removed = Path(data_dir) / STATE_DIR, 0
    if base.is_symlink() or not base.is_dir():
        return 0
    for path in sorted(base.iterdir()):
        try:
            info = os.lstat(path)
            if re.fullmatch(r"[0-9a-f]{16}\.json(\.lock)?", path.name) and stat.S_ISREG(info.st_mode) and info.st_mtime < before:
                if not dry_run:
                    os.unlink(path)
                removed += 1
        except OSError:
            pass
    return removed
