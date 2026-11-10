"""hooks/acewatch/acewatch_run.py as a program: it opens the job's two logs itself (the plists name no launchd log path), refuses an unsafe log WITHOUT running the
job (exit 3), hands the job those descriptors as stdout and stderr, keeps the job's status, forwards SIGTERM and rotates after the run. Temp directories only."""

import importlib.util
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "hooks" / "acewatch" / "acewatch_run.py"
KIB = 1024
_spec = importlib.util.spec_from_file_location("acewatch_run_prog", SCRIPT)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


def argv(logs: Path, code: str, out: str = "o.log", err: str = "e.log") -> list[str]:
    return [sys.executable, "-I", str(SCRIPT), str(logs), out, err, "--", sys.executable, "-c", code]


def call(logs: Path, code: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv(logs, code, **kw), capture_output=True, text=True, timeout=30)


def logs_dir(tmp_path: Path) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir()
    return logs


def marker_job(marker: Path) -> str:
    return f"open({str(marker)!r}, 'w').write('ran')"


# ---------------------------------------------------------------- the happy path
def test_missing_logs_are_created_0600_and_the_jobs_stdout_stderr_land_in_the_right_files(tmp_path):
    logs = logs_dir(tmp_path)
    done = call(logs, "import sys; print('to-out'); print('to-err', file=sys.stderr)")
    assert done.returncode == 0 and (done.stdout, done.stderr) == ("", "")
    assert (logs / "o.log").read_text() == "to-out\n" and (logs / "e.log").read_text() == "to-err\n"
    assert all(stat.S_IMODE((logs / n).stat().st_mode) == 0o600 for n in ("o.log", "e.log"))
    assert sorted(p.name for p in logs.iterdir()) == ["e.log", "o.log"]


def test_the_jobs_descriptors_are_blocking_and_append_mode(tmp_path):
    logs = logs_dir(tmp_path)
    call(logs, "import fcntl, os; f = fcntl.fcntl(1, fcntl.F_GETFL); print(bool(f & os.O_NONBLOCK), bool(f & os.O_APPEND))")
    assert (logs / "o.log").read_text() == "False True\n"


def test_an_existing_log_is_appended_to_and_its_mode_is_set_to_0600(tmp_path):
    logs = logs_dir(tmp_path)
    (logs / "o.log").write_text("old\n")
    os.chmod(logs / "o.log", 0o644)
    call(logs, "print('new')")
    assert (logs / "o.log").read_text() == "old\nnew\n" and stat.S_IMODE((logs / "o.log").stat().st_mode) == 0o600


def test_the_jobs_exit_status_is_the_wrappers_and_a_job_that_cannot_start_is_127_with_the_reason_in_the_err_log(tmp_path):
    logs = logs_dir(tmp_path)
    assert call(logs, "import sys; sys.exit(7)").returncode == 7
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), str(logs), "o.log", "e.log", "--", str(tmp_path / "no-such-program")], capture_output=True, text=True, timeout=30)
    assert done.returncode == 127 and "cannot start the job" in (logs / "e.log").read_text() and done.stderr == ""
    assert subprocess.run([sys.executable, "-I", str(SCRIPT), str(logs), "o.log", "e.log"], capture_output=True, text=True).returncode == 2


def test_a_job_killed_by_sigterm_gives_143_and_is_still_rotated_when_over_the_limit(tmp_path):
    logs = logs_dir(tmp_path)
    done = call(logs, "import os, signal, sys; sys.stdout.write('x' * (2 * 1024 * 1024)); sys.stdout.flush(); os.kill(os.getpid(), signal.SIGTERM)")
    assert done.returncode == 143 and (logs / "o.log.0").stat().st_size == 2 * 1024 * 1024 and not (logs / "o.log").exists()


def test_the_output_is_in_the_live_log_when_the_run_left_it_within_the_limit_and_in_dot_0_when_it_left_it_over(tmp_path):
    logs = logs_dir(tmp_path)
    call(logs, "print('small')")
    assert (logs / "o.log").read_text() == "small\n" and not (logs / "o.log.0").exists()
    call(logs, "import sys; sys.stdout.write('BIG' + 'x' * (1100 * 1024))")
    assert not (logs / "o.log").exists() and (logs / "o.log.0").read_text().startswith("small\nBIG") and (logs / "e.log").exists()
    call(logs, "print('next')")
    assert (logs / "o.log").read_text() == "next\n"                         # the next run opens a fresh live log


# ---------------------------------------------------------------- an unsafe log: the job is NOT run
def plant(logs: Path, name: str, kind: str, tmp_path: Path) -> Path:
    target = tmp_path / f"victim-{name}"
    if kind == "symlink":
        target.write_text("victim")
        os.chmod(target, 0o644)
        (logs / name).symlink_to(target)
    elif kind == "dangling":
        (logs / name).symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(logs / name)
    elif kind == "hardlink":
        target.write_text("victim")
        os.link(target, logs / name)
    else:
        (logs / name).mkdir()
    return target


@pytest.mark.parametrize("which", ["out", "err", "both"])
@pytest.mark.parametrize("kind", ["symlink", "dangling", "fifo", "directory", "hardlink"])
def test_an_unsafe_live_log_refuses_the_job_exit_3_touches_nothing_and_the_reason_goes_where_it_can(tmp_path, which, kind):
    logs = logs_dir(tmp_path)
    marker = tmp_path / "marker"
    planted = {name: plant(logs, name, kind, tmp_path) for name in {"out": ["o.log"], "err": ["e.log"], "both": ["o.log", "e.log"]}[which]}
    done = call(logs, marker_job(marker))                                   # a FIFO must not hang: call() has a timeout
    assert done.returncode == 3 and not marker.exists()
    for target in planted.values():
        if kind in ("symlink", "hardlink"):
            assert target.read_text() == "victim" and stat.S_IMODE(target.stat().st_mode) == 0o644
        if kind == "dangling":
            assert not target.exists()
    reason = "the job was NOT run"
    if which == "out":                                                      # the err log opened: the out-log refusal is written THERE
        assert reason in (logs / "e.log").read_text() and "o.log" in (logs / "e.log").read_text() and done.stderr == ""
    elif which == "err":                                                    # the err log is refused: the reason goes to the out log
        assert reason in (logs / "o.log").read_text() and "e.log" in (logs / "o.log").read_text() and done.stderr == ""
    else:                                                                   # neither opens: the inherited stderr, the only trace
        assert reason in done.stderr and "o.log" in done.stderr and "e.log" in done.stderr


def test_a_rotation_refusal_after_the_job_is_written_to_the_err_log_and_leaves_the_exit_status_alone(tmp_path):
    logs = logs_dir(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("victim")
    (logs / "o.log.2").symlink_to(victim)
    done = call(logs, "import sys; sys.stdout.write('x' * (1100 * 1024)); sys.exit(5)")
    assert done.returncode == 5 and "log not rotated" in (logs / "e.log").read_text() and "o.log.2" in (logs / "e.log").read_text()
    assert "log not rotated" not in (logs / "o.log").read_text() and victim.read_text() == "victim" and done.stderr == ""


def test_a_symlinked_logs_directory_refuses_the_job(tmp_path):
    real = logs_dir(tmp_path)
    (tmp_path / "link").symlink_to(real)
    marker = tmp_path / "marker"
    done = call(tmp_path / "link", marker_job(marker))
    assert done.returncode == 3 and not marker.exists() and list(real.iterdir()) == [] and "the job was NOT run" in done.stderr


def test_the_same_name_for_out_and_err_refuses_the_job(tmp_path):
    logs = logs_dir(tmp_path)
    marker = tmp_path / "marker"
    assert call(logs, marker_job(marker), out="o.log", err="o.log").returncode == 3 and not marker.exists()


def test_a_fifo_with_a_reader_attached_is_still_refused_by_the_descriptor_check(tmp_path):
    logs = logs_dir(tmp_path)
    os.mkfifo(logs / "o.log")
    reader = os.open(logs / "o.log", os.O_RDONLY | os.O_NONBLOCK)
    dir_fd = os.open(logs, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(run.Refused, match="not a regular file with one link"):
            run.open_log(dir_fd, "o.log")
    finally:
        os.close(reader)
        os.close(dir_fd)


def _euid_as(monkeypatch, other_in: str) -> None:
    """os.geteuid answers a foreign uid only to the function named `other_in` (the directory check or the file check) and our own uid to everyone else."""
    mine = os.getuid()
    monkeypatch.setattr(os, "geteuid", lambda: mine + 1 if sys._getframe(1).f_code.co_name == other_in else mine)


@pytest.mark.parametrize("other_in", ["_open_dir", "_plain_and_ours"])               # the directory is not ours / the log is not ours
def test_a_log_or_directory_that_is_not_ours_refuses_the_job(tmp_path, monkeypatch, other_in):
    logs = logs_dir(tmp_path)
    marker = tmp_path / "marker"
    _euid_as(monkeypatch, other_in)
    assert run.main(["x", str(logs), "o.log", "e.log", "--", sys.executable, "-c", marker_job(marker)]) == 3 and not marker.exists()


# ---------------------------------------------------------------- signals
def test_sigterm_to_the_wrapper_reaches_the_job_which_is_waited_for_and_the_wrapper_exits_143_without_rotating(tmp_path):
    logs = logs_dir(tmp_path)
    ready, marker = tmp_path / "ready", tmp_path / "term"
    code = (f"import os, signal, sys, time\n"
            f"def h(*a):\n    open({str(marker)!r}, 'w').write('term'); sys.exit(0)\n"
            f"signal.signal(signal.SIGTERM, h)\n"
            f"sys.stdout.write('x' * (2 * 1024 * 1024)); sys.stdout.flush()\n"
            f"open({str(ready)!r}, 'w').write(str(os.getpid()))\n"
            f"time.sleep(30)\n")
    wrapper = subprocess.Popen(argv(logs, code), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 20
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        wrapper.send_signal(signal.SIGTERM)
        assert wrapper.wait(timeout=20) == 143
        assert marker.read_text() == "term"                                 # the job got the signal, and the wrapper waited for it
        assert (logs / "o.log").stat().st_size == 2 * 1024 * 1024 and not (logs / "o.log.0").exists()     # signalled: no rotation
    finally:
        wrapper.kill()
        wrapper.communicate()
        if ready.exists():
            try:
                os.kill(int(ready.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


# ---------------------------------------------------------------- a missing logs directory (first install)
def test_a_missing_logs_directory_is_created_0700_and_the_job_runs(tmp_path):
    parent = tmp_path / "home"
    parent.mkdir()
    done = call(parent / "logs", "print('ran')")
    assert done.returncode == 0 and stat.S_IMODE((parent / "logs").stat().st_mode) == 0o700 and (parent / "logs" / "o.log").read_text() == "ran\n"


def test_a_missing_logs_directory_is_not_created_under_a_symlinked_or_missing_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    marker = tmp_path / "marker"
    for parent in (tmp_path / "link", tmp_path / "no-such-parent"):
        done = call(parent / "logs", marker_job(marker))
        assert done.returncode == 3 and not marker.exists() and "the job was NOT run" in done.stderr
    assert list(real.iterdir()) == [] and not (tmp_path / "no-such-parent").exists()


def test_a_missing_logs_directory_is_not_created_under_a_parent_that_is_not_ours(tmp_path, monkeypatch):
    parent = tmp_path / "home"
    parent.mkdir()
    marker = tmp_path / "marker"
    _euid_as(monkeypatch, "_make_dir")
    assert run.main(["x", str(parent / "logs"), "o.log", "e.log", "--", sys.executable, "-c", marker_job(marker)]) == 3
    assert not marker.exists() and not (parent / "logs").exists()


def test_only_the_last_path_component_is_ever_created(tmp_path):
    marker = tmp_path / "marker"
    done = call(tmp_path / "a" / "b" / "logs", marker_job(marker))
    assert done.returncode == 3 and not marker.exists() and not (tmp_path / "a").exists()


# ---------------------------------------------------------------- what the job inherits
def test_the_job_sees_only_fds_0_1_2_and_a_dev_null_stdin(tmp_path):
    logs = logs_dir(tmp_path)
    leaked = os.open(tmp_path, os.O_RDONLY)
    os.set_inheritable(leaked, True)                                        # an inheritable descriptor of the wrapper's own: close_fds must drop it
    try:
        code = (f"import os\nfor fd in ({leaked}, 3, 4, 5):\n    try:\n        os.fstat(fd)\n        print('open', fd)\n    except OSError:\n        pass\n"
                f"print('stdin', os.read(0, 10))\n")
        assert run.main(["x", str(logs), "o.log", "e.log", "--", sys.executable, "-c", code]) == 0
    finally:
        os.close(leaked)
    assert (logs / "o.log").read_text() == "stdin b''\n"


def test_the_jobs_stdin_is_not_the_wrappers_open_pipe(tmp_path):
    logs = logs_dir(tmp_path)
    wrapper = subprocess.Popen(argv(logs, "import sys; print('eof' if sys.stdin.read() == '' else 'data')"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wrapper.wait(timeout=20) == 0                                # an inherited, never-closed pipe would block the job's read
    finally:
        wrapper.kill()
        wrapper.communicate()
    assert (logs / "o.log").read_text() == "eof\n"


# ---------------------------------------------------------------- messages
def test_a_message_with_an_undecodable_name_does_not_raise(tmp_path):
    fd = os.open(tmp_path / "e.log", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        run._say("log refused: bad\udcffname", fd, None)
    finally:
        os.close(fd)
    assert (tmp_path / "e.log").read_text() == "acewatch_run: log refused: bad\\udcffname\n"


# ---------------------------------------------------------------- a signal that arrives before the job exists
def test_a_sigterm_before_the_job_exists_is_delivered_to_it_once_it_does(tmp_path, monkeypatch):
    real, started = subprocess.Popen, []

    def popen_after_signal(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)                                # the wrapper's handler runs before the job exists
        proc = real(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(run.subprocess, "Popen", popen_after_signal)
    fd = os.open(os.devnull, os.O_WRONLY)
    begin = time.time()
    try:
        status = run.run_job([sys.executable, "-c", "import time; time.sleep(30)"], fd, fd)
        assert status == (143, signal.SIGTERM) and time.time() - begin < 15 and started[0].poll() is not None
    finally:
        os.close(fd)
        for proc in started:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


# ---------------------------------------------------------------- review-3 hardenings
def test_a_mkdir_that_loses_a_race_to_another_creator_is_not_an_error(tmp_path, monkeypatch):
    parent = tmp_path / "home"
    parent.mkdir()
    marker = tmp_path / "marker"
    real = os.mkdir

    def mkdir_then_exists(path, mode=0o777, *, dir_fd=None):
        real(path, mode, dir_fd=dir_fd)
        raise FileExistsError(17, "File exists")

    monkeypatch.setattr(run.os, "mkdir", mkdir_then_exists)
    assert run.main(["x", str(parent / "logs"), "o.log", "e.log", "--", sys.executable, "-c", marker_job(marker)]) == 0
    assert marker.read_text() == "ran" and (parent / "logs" / "o.log").exists()


@pytest.mark.parametrize("suffix", ["/", "/."])
def test_a_logs_path_ending_in_a_slash_or_a_dot_is_refused_so_a_symlinked_last_component_is_never_followed(tmp_path, suffix):
    real = logs_dir(tmp_path)
    (tmp_path / "link").symlink_to(real)
    marker = tmp_path / "marker"
    assert run.main(["x", str(tmp_path / "link") + suffix, "o.log", "e.log", "--", sys.executable, "-c", marker_job(marker)]) == 3
    assert not marker.exists() and list(real.iterdir()) == []


def test_a_closed_stderr_still_ends_in_exit_3_without_a_traceback(tmp_path):
    logs = logs_dir(tmp_path)
    done = subprocess.run(argv(logs, "pass", out="o.log", err="o.log"), preexec_fn=lambda: os.close(2), capture_output=False, stdout=subprocess.PIPE, timeout=30)
    assert done.returncode == 3 and done.stdout == b""


def test_a_created_logs_directory_is_0700_whatever_the_umask(tmp_path):
    parent = tmp_path / "home"
    parent.mkdir()
    old = os.umask(0o277)
    try:
        assert run.main(["x", str(parent / "logs"), "o.log", "e.log", "--", sys.executable, "-c", "print('ran')"]) == 0
    finally:
        os.umask(old)
    assert stat.S_IMODE((parent / "logs").stat().st_mode) == 0o700 and (parent / "logs" / "o.log").read_text() == "ran\n"
