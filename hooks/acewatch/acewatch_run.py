#!/usr/bin/env python3
"""acewatch_run.py -- run one acewatch job, then rotate that job's own log files AS THE SANDBOX USER (docs/watch/SANDBOX.md, "Log rotation").

    <venv python> -I <copy>/hooks/acewatch/acewatch_run.py <logs dir> <name>[,<name>...] -- <the job's argv>

The two LaunchDaemon templates run the scan through this file. It runs the job (same argv, same working directory, same environment, same output
files), takes the job's exit status as its own, and afterwards rotates the named logs: when a log is larger than 1024 KiB it becomes `<name>.0`,
`<name>.0` becomes `<name>.1` and so on, and at most 5 archives are kept (mode 0600). launchd opens the log files again for every run, so renaming
one after the run is safe. Nothing here runs as root: root never operates inside a directory `acewatch` can write (that was the newsyslog design).

The rotation works only on regular files that the running user owns. The directory is opened with O_NOFOLLOW and must be owned by the running user,
the live log is opened with O_NOFOLLOW and checked on the open descriptor, and all five archive names are checked before anything is renamed. A symlink, a directory or a file owned by someone else makes
the rotation of that log REFUSE (one line on stderr, nothing renamed, nothing chmodded, the target of a link untouched). A missing log is not an
error. Standard library only.
"""

import os
import stat
import subprocess
import sys

SIZE_KIB = 1024
KEEP = 5


class Refused(Exception):
    """The log, an archive name or the directory is not a plain file of the running user: nothing was changed."""


def _lstat(name: str, dir_fd: int):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _plain_and_ours(st, name: str) -> None:
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
        raise Refused(f"{name} is not a regular file owned by this user")


def rotate(logdir: str, name: str, size_kib: int = SIZE_KIB, keep: int = KEEP) -> bool:
    """Rotate `logdir/name` when it is larger than `size_kib` KiB; True if it was rotated. Raises Refused (nothing changed) for anything but plain own files."""
    if "/" in name or name in ("", ".", ".."):
        raise Refused(f"{name!r} is not a plain file name")
    try:
        dir_fd = os.open(logdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise Refused(f"cannot open the log directory without following a link ({exc.strerror})") from exc
    try:
        if os.fstat(dir_fd).st_uid != os.geteuid():
            raise Refused("the log directory is not owned by this user")
        if _lstat(name, dir_fd) is None:                              # no log yet: nothing to do (a dangling link is not "missing": the open refuses it)
            return False
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except OSError as exc:
            raise Refused(f"{name} cannot be opened without following a link ({exc.strerror})") from exc
        try:
            opened = os.fstat(fd)
            _plain_and_ours(opened, name)
            if opened.st_size <= size_kib * 1024:
                return False
            for i in range(keep):                                     # every archive name is checked before anything moves
                old = _lstat(f"{name}.{i}", dir_fd)
                if old is not None:
                    _plain_and_ours(old, f"{name}.{i}")
            os.fchmod(fd, 0o600)                                      # on the descriptor we checked, never by path
        finally:
            os.close(fd)
        for i in range(keep - 1, -1, -1):                             # .4 is dropped, .3 -> .4, ... .0 -> .1, then the log -> .0
            src = name if i == 0 else f"{name}.{i - 1}"
            if i == keep - 1:
                try:
                    os.unlink(f"{name}.{i}", dir_fd=dir_fd)
                except FileNotFoundError:
                    pass
            try:
                os.rename(src, f"{name}.{i}", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        return True
    finally:
        os.close(dir_fd)


def main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[3] != "--":
        print("usage: acewatch_run.py <logs dir> <name>[,<name>...] -- <command> [args...]", file=sys.stderr)
        return 2
    logdir, names, command = argv[1], [n for n in argv[2].split(",") if n], argv[4:]
    try:
        status = subprocess.run(command).returncode
    except OSError as exc:
        print(f"acewatch_run: cannot start the job ({exc.strerror})", file=sys.stderr)
        status = 127
    for name in names:
        try:
            rotate(logdir, name)
        except (Refused, OSError) as exc:
            print(f"acewatch_run: log not rotated: {exc}", file=sys.stderr)
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
