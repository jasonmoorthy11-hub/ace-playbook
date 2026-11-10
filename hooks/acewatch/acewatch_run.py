#!/usr/bin/env python3
"""acewatch_run.py -- run one acewatch job with logs the wrapper opens ITSELF, then rotate them, all as the sandbox user (docs/watch/SANDBOX.md, "Log rotation").

    <venv python> -I <copy>/hooks/acewatch/acewatch_run.py <logs dir> <out name> <err name> -- <the job's argv>

The two LaunchDaemon templates name NO StandardOutPath / StandardErrorPath, so launchd opens no file anywhere. This wrapper opens the two logs as the running
user (`acewatch`) before it starts the job: the directory with O_NOFOLLOW and owned by us (a MISSING directory is created, 0700, only its last component and only
under an existing real directory we own), each log relative to that directory descriptor with O_NOFOLLOW, mode 0600 (created 0600, fchmod 0600), and a
descriptor check (a regular file with one link, ours; a FIFO, directory, symlink, hard link or somebody else's file is refused). The job's stdout and stderr ARE
those descriptors (its stdin is /dev/null and no other descriptor is passed on), and the wrapper's own messages go to the err log.
If a log cannot be opened safely the job is NOT run and the exit status is 3. Nothing runs unlogged. Where the reason goes: the ERR log is opened first; if it opens,
every message (an out-log refusal included) goes there; if the err log is refused, the reason goes to the out log if that opens; only if neither opens (or the
directory is refused) does it go to the inherited stderr, which launchd discards: then the only trace is the last exit status 3 in `launchctl print`.

After the job, a log larger than 1024 KiB becomes `<name>.0`, `<name>.0` becomes `<name>.1` and so on; 5 archives are kept (mode 0600, not compressed). The rule:
the job's output is in the LIVE log when that log was within the limit after the run, and in `.0` when the run left it over the limit. The next run opens a fresh
live log. Rotation works only on regular files the running user owns (every archive name is checked before anything is renamed); a refusal is one line in the
err log and changes nothing. The job's exit status is the wrapper's: a job killed by signal N gives 128+N. On SIGTERM, SIGINT or SIGHUP the wrapper forwards
the signal to the job, waits for it and exits 128+N WITHOUT rotating (the next run rotates). Standard library only; nothing here runs as root.
"""

import fcntl
import os
import signal
import stat
import subprocess
import sys

SIZE_KIB = 1024
KEEP = 5
REFUSED_STATUS = 3
FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class Refused(Exception):
    """The log, an archive name or the directory is not a plain file of the running user: nothing was changed."""


def _lstat(name: str, dir_fd: int):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _plain_and_ours(st, name: str) -> None:
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1:
        raise Refused(f"{name} is not a regular file with one link owned by this user")


def _make_dir(logdir: str) -> bool:
    """Create the MISSING last component of `logdir` (0700): only under an existing real directory (not a symlink) owned by the running user. True if created."""
    parent, leaf = os.path.split(logdir.rstrip("/"))
    if leaf in ("", ".", "..") or not parent:
        raise Refused(f"{logdir!r} is not a directory path that can be created")
    try:
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Refused(f"the log directory is missing and its parent cannot be opened without following a link ({exc.strerror})") from exc
    try:
        if os.fstat(parent_fd).st_uid != os.geteuid():
            raise Refused("the log directory is missing and its parent is not owned by this user")
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            return False
        except OSError as exc:
            raise Refused(f"the log directory cannot be created ({exc.strerror})") from exc
        return True
    finally:
        os.close(parent_fd)


def _open_dir(logdir: str, create: bool = False) -> int:
    try:
        dir_fd = os.open(logdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        if not create:
            raise Refused(f"the log directory cannot be opened ({exc.strerror})") from exc
        created = _make_dir(logdir)
        dir_fd = _open_dir(logdir)
        if created:
            os.fchmod(dir_fd, 0o700)                                           # the mode must not depend on the umask
        return dir_fd
    except OSError as exc:
        raise Refused(f"cannot open the log directory without following a link ({exc.strerror})") from exc
    if os.fstat(dir_fd).st_uid != os.geteuid():
        os.close(dir_fd)
        raise Refused("the log directory is not owned by this user")
    return dir_fd


def _plain_name(name: str) -> None:
    if "/" in name or name in ("", ".", ".."):
        raise Refused(f"{name!r} is not a plain file name")


def open_log(dir_fd: int, name: str) -> int:
    """The live log `name` in the directory `dir_fd`, opened for appending (created 0600); Refused for a symlink, FIFO, directory or a file that is not ours."""
    _plain_name(name)
    try:                                         # O_NONBLOCK: opening a FIFO for writing must fail (ENXIO) instead of waiting for a reader
        fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise Refused(f"{name} cannot be opened for appending without following a link ({exc.strerror})") from exc
    try:
        _plain_and_ours(os.fstat(fd), name)
        os.fchmod(fd, 0o600)
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
    except BaseException:
        os.close(fd)
        raise
    return fd


def rotate(logdir: str, name: str, size_kib: int = SIZE_KIB, keep: int = KEEP) -> bool:
    """Rotate `logdir/name` when it is larger than `size_kib` KiB; True if it was rotated. Raises Refused (nothing changed) for anything but plain own files."""
    _plain_name(name)
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


def _say(message: str, err_fd: int | None, out_fd: int | None) -> None:
    line = f"acewatch_run: {message}\n"
    for fd in (err_fd, out_fd):
        if fd is not None:
            try:
                os.write(fd, line.encode(errors="backslashreplace"))
                return
            except OSError:
                pass
    if sys.stderr is not None:                                                # a closed fd 2 leaves no stderr: still exit 3, no traceback
        try:
            sys.stderr.write(line)
        except OSError:
            pass


def run_job(command: list[str], out_fd: int, err_fd: int) -> tuple[int, int]:
    """Run the job on the two descriptors. (exit status, signal the wrapper itself received or 0); a received signal is forwarded to the job first."""
    state = {"proc": None, "got": 0}

    def forward(signum, _frame):
        state["got"] = state["got"] or signum
        if state["proc"] is not None and state["proc"].poll() is None:
            state["proc"].send_signal(signum)

    old = {s: signal.signal(s, forward) for s in FORWARDED}
    try:
        try:
            state["proc"] = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out_fd, stderr=err_fd, close_fds=True)
        except OSError as exc:
            _say(f"cannot start the job ({exc.strerror})", err_fd, out_fd)
            return 127, state["got"]
        if state["got"]:                                              # a signal that came in before the job existed
            state["proc"].send_signal(state["got"])
        code = state["proc"].wait()
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)
    return (code if code >= 0 else 128 - code), state["got"]


def main(argv: list[str]) -> int:
    if len(argv) < 6 or argv[4] != "--":
        sys.stderr.write("usage: acewatch_run.py <logs dir> <out name> <err name> -- <command> [args...]\n")
        return 2
    logdir, out_name, err_name, command = argv[1], argv[2], argv[3], argv[5:]
    out_fd = err_fd = None
    refusals = []
    try:
        if out_name == err_name:
            raise Refused("the out and err logs must be two different files")
        if os.path.basename(logdir) in ("", ".", ".."):                        # a trailing slash or dot would make the open follow a symlink
            raise Refused(f"{logdir!r} must end in a plain directory name")
        dir_fd = _open_dir(logdir, create=True)
    except Refused as exc:
        _say(f"log refused, the job was NOT run: {exc}", None, None)           # no log to write to: the inherited stderr
        return REFUSED_STATUS
    try:
        for name in (err_name, out_name):                                      # the ERR log first: every later message goes there when it opens
            try:
                fd = open_log(dir_fd, name)
            except Refused as exc:
                refusals.append(str(exc))
                continue
            if name == err_name:
                err_fd = fd
            else:
                out_fd = fd
    finally:
        os.close(dir_fd)
    if refusals:
        _say(f"log refused, the job was NOT run: {'; '.join(refusals)}", err_fd, out_fd)       # err log, else out log, else stderr
        return REFUSED_STATUS
    status, got = run_job(command, out_fd, err_fd)
    if got:
        return 128 + got                                              # signalled: no rotation, the next run rotates
    for name in (out_name, err_name):
        try:
            rotate(logdir, name)
        except (Refused, OSError) as exc:
            _say(f"log not rotated: {exc}", err_fd, out_fd)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
