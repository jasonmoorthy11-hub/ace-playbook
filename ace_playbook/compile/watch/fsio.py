"""File helpers of the watcher store: a private atomic write with a unique temporary name, and the scan lock. Stdlib only.

`write_private` creates `.<name>.<pid>.<random>.tmp` with O_EXCL (never follows a symlink, never shares a name with another process),
fsyncs, and renames over the target (0600). `lock` takes a non-blocking (or blocking) exclusive flock on `<dir>/<name>`; closing the
returned descriptor releases it.
"""

from __future__ import annotations

import fcntl
import os
import secrets
from pathlib import Path


def odd(path: Path, directory: bool = False) -> bool:
    """True when `path` is there (a dangling link included) but is a link or the wrong kind: a file where a directory belongs, or the reverse."""
    path = Path(path)
    return path.is_symlink() or (os.path.lexists(path) and not (path.is_dir() if directory else path.is_file()))


def write_private(path: Path, text: str) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lock(directory: Path, name: str = "scan.lock", blocking: bool = False) -> int | None:
    """An exclusive flock on `directory/name` (created 0600); None when it is held elsewhere and `blocking` is false."""
    fd = os.open(Path(directory) / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd
