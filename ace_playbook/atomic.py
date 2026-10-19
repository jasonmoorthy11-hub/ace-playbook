"""Stdlib-only private atomic file helpers: data files are 0600 whatever the umask."""

from __future__ import annotations

import os
from pathlib import Path


def create_private(tmp: Path) -> int:
    """Open `tmp` for writing as a fresh 0600 file; never follows a symlink at `tmp`."""
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)  # a pre-existing tmp keeps its old mode otherwise
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_private(path: Path, text: str) -> None:
    """Atomically replace `path` with `text`; the result is 0600 and the tmp never lingers.

    A leftover symlink at the tmp path fails closed (ELOOP) until it is removed.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = create_private(tmp)
    try:
        try:
            fh = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)  # fdopen failed, so it did not take ownership of the fd
            raise
        with fh:
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
