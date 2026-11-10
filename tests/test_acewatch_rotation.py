"""The sandbox logs are rotated by the acewatch user itself (hooks/acewatch/acewatch_run.py), never by a root newsyslog job inside the acewatch-writable
logs directory (docs/watch/SANDBOX.md "Log rotation"). Temp directories and the text of the files only: nothing here installs or runs a template."""

import importlib.util
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "hooks" / "acewatch"
SCRIPT = FOLDER / "acewatch_run.py"
SANDBOX = ROOT / "docs" / "watch" / "SANDBOX.md"
HOME = "__ACEWATCH_HOME__"
KIB = 1024

_spec = importlib.util.spec_from_file_location("acewatch_run", SCRIPT)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


def make(tmp_path: Path, size: int = 0, name: str = "a.log") -> Path:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_bytes(b"x" * size)
    return logs


def plist_args() -> list[tuple[dict, list[str]]]:
    out = []
    for path in sorted(FOLDER.glob("*.plist")):
        data = plistlib.loads(path.read_bytes())
        out.append((data, data["ProgramArguments"]))
    return out


# ---------------------------------------------------------------- the templates: a wrapper per plist, no root rotation file
def test_the_newsyslog_template_is_gone_and_no_template_asks_root_to_rotate():
    assert not (FOLDER / "newsyslog.acewatch.conf").exists()
    for path in (p for p in FOLDER.iterdir() if p.is_file()):
        assert "newsyslog.d" not in path.read_text(encoding="utf-8"), path


def test_each_plist_starts_its_scan_through_the_wrapper_which_rotates_exactly_the_two_logs_that_plist_writes():
    seen = set()
    for data, args in plist_args():
        assert args[1:3] == ["-I", "__ACE_PLAYBOOK_COPY__/hooks/acewatch/acewatch_run.py"]
        assert args[3] == HOME + "/logs" and args[5] == "--" and args[6:8] == [args[0], "-m"]      # the scan argv follows `--`, unchanged
        names = args[4].split(",")
        assert {HOME + "/logs/" + n for n in names} == {data["StandardOutPath"], data["StandardErrorPath"]} and len(names) == 2
        seen |= set(names)
    assert len(seen) == 4


def test_the_runbook_says_acewatch_rotates_and_tells_an_old_install_to_remove_the_newsyslog_file():
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    log = text.split("## Log rotation", 1)[1].split("## The operator-side status line", 1)[0]
    assert "rotated by `acewatch` itself, never by root" in log and "hooks/acewatch/acewatch_run.py" in log
    assert "sudo rm /etc/newsyslog.d/acewatch.conf" in log and "root newsyslog file" in log
    assert "sudo install" not in log and "sudo newsyslog" not in log and " sed " not in log
    assert "refuses" in log and "symlink" in log and "5 archives" in log and "1024 KiB" in log
    assert "/etc/newsyslog.d/acewatch.conf" in text.split("## Uninstalling", 1)[1]


# ---------------------------------------------------------------- the rotation, in temp directories
def test_a_missing_log_or_directory_is_a_no_op(tmp_path):
    (tmp_path / "logs").mkdir()
    assert run.rotate(str(tmp_path / "logs"), "a.log") is False and list((tmp_path / "logs").iterdir()) == []
    assert run.rotate(str(tmp_path / "nope"), "a.log") is False


def test_a_log_is_rotated_only_past_the_size(tmp_path):
    logs = make(tmp_path, size=1024 * KIB)
    assert run.rotate(str(logs), "a.log") is False and (logs / "a.log").stat().st_size == 1024 * KIB and sorted(p.name for p in logs.iterdir()) == ["a.log"]
    (logs / "a.log").write_bytes(b"x" * (1024 * KIB + 1))
    assert run.rotate(str(logs), "a.log") is True
    assert sorted(p.name for p in logs.iterdir()) == ["a.log.0"] and (logs / "a.log.0").stat().st_size == 1024 * KIB + 1


def test_at_most_five_archives_are_kept_newest_first_with_mode_0600(tmp_path):
    logs = make(tmp_path)
    for n in range(8):
        (logs / "a.log").write_bytes(f"gen{n}".encode() + b"x" * 2 * KIB)
        os.chmod(logs / "a.log", 0o644)
        assert run.rotate(str(logs), "a.log", size_kib=1) is True
    assert sorted(p.name for p in logs.iterdir()) == [f"a.log.{i}" for i in range(5)]
    assert [(logs / f"a.log.{i}").read_bytes()[:4] for i in range(5)] == [f"gen{7 - i}".encode() for i in range(5)]
    assert all(stat.S_IMODE((logs / f"a.log.{i}").stat().st_mode) == 0o600 for i in range(5))


def test_a_log_planted_as_a_symlink_is_refused_and_its_target_is_left_alone(tmp_path):
    target = tmp_path / "victim"
    target.write_bytes(b"v" * (2 * KIB))
    os.chmod(target, 0o644)
    before = (target.stat().st_ino, target.stat().st_mode, target.stat().st_mtime_ns, target.read_bytes())
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").symlink_to(target)
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert (logs / "a.log").is_symlink() and sorted(p.name for p in logs.iterdir()) == ["a.log"]
    assert (target.stat().st_ino, target.stat().st_mode, target.stat().st_mtime_ns, target.read_bytes()) == before


def test_a_symlink_swapped_in_after_the_check_is_still_refused_by_the_open(tmp_path, monkeypatch):
    target = tmp_path / "victim"
    target.write_bytes(b"v" * (2 * KIB))
    os.chmod(target, 0o644)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").symlink_to(target)
    monkeypatch.setattr(run, "_lstat", lambda name, dir_fd: target.stat())          # the check saw a plain file; the open must not follow the link
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644 and sorted(p.name for p in logs.iterdir()) == ["a.log"]


def test_an_archive_name_planted_as_a_symlink_stops_the_whole_rotation_before_anything_moves(tmp_path):
    target = tmp_path / "victim"
    target.write_bytes(b"v" * 10)
    os.chmod(target, 0o644)
    logs = make(tmp_path, size=2 * KIB)
    (logs / "a.log.0").write_bytes(b"zero")
    (logs / "a.log.2").symlink_to(target)
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert sorted(p.name for p in logs.iterdir()) == ["a.log", "a.log.0", "a.log.2"] and (logs / "a.log").stat().st_size == 2 * KIB
    assert (logs / "a.log.0").read_bytes() == b"zero" and (logs / "a.log.2").is_symlink()
    assert target.read_bytes() == b"v" * 10 and stat.S_IMODE(target.stat().st_mode) == 0o644


def test_a_symlink_at_the_oldest_archive_name_is_refused_too(tmp_path):
    target = tmp_path / "victim"
    target.write_bytes(b"keep")
    logs = make(tmp_path, size=2 * KIB)
    (logs / "a.log.4").symlink_to(target)
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert target.read_bytes() == b"keep" and (logs / "a.log.4").is_symlink() and (logs / "a.log").exists()


def test_a_directory_at_the_log_name_and_a_symlinked_logs_directory_are_refused(tmp_path):
    logs = tmp_path / "logs"
    (logs / "a.log").mkdir(parents=True)
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "a.log", size_kib=1)
    real = make(tmp_path / "real", size=2 * KIB)
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(run.Refused):
        run.rotate(str(tmp_path / "link"), "a.log", size_kib=1)
    assert (real / "a.log").stat().st_size == 2 * KIB and sorted(p.name for p in real.iterdir()) == ["a.log"]


def _euid_sequence(monkeypatch, answers: list[int]) -> None:
    """os.geteuid answers the given uids in call order, then the last one: the directory check asks first, the file checks after it."""
    calls = iter(answers)
    monkeypatch.setattr(os, "geteuid", lambda: next(calls, answers[-1]))


def test_a_log_owned_by_someone_else_is_refused_although_the_directory_is_ours(tmp_path, monkeypatch):
    logs = make(tmp_path, size=2 * KIB)
    mine = os.stat(logs).st_uid
    _euid_sequence(monkeypatch, [mine, mine + 1])
    with pytest.raises(run.Refused, match="a.log is not a regular file owned"):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert sorted(p.name for p in logs.iterdir()) == ["a.log"]


def test_a_logs_directory_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    logs = make(tmp_path, size=2 * KIB)
    mine = os.stat(logs).st_uid
    _euid_sequence(monkeypatch, [mine + 1, mine])
    with pytest.raises(run.Refused, match="directory is not owned"):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert sorted(p.name for p in logs.iterdir()) == ["a.log"]


def test_a_name_with_a_slash_is_refused(tmp_path):
    logs = make(tmp_path, size=2 * KIB)
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "../logs/a.log", size_kib=1)


# ---------------------------------------------------------------- the wrapper: the job runs unchanged, its status is kept, then the logs rotate
def wrapper(logs: Path, names: str, code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), str(logs), names, "--", sys.executable, "-c", code], capture_output=True, text=True)


def test_the_wrapper_keeps_the_jobs_exit_status_and_rotates_afterwards(tmp_path):
    logs = make(tmp_path, size=1024 * KIB + 1)
    done = wrapper(logs, "a.log,b.log", "import sys; sys.exit(3)")
    assert done.returncode == 3 and done.stderr == "" and sorted(p.name for p in logs.iterdir()) == ["a.log.0"]


def test_the_wrapper_reports_a_refusal_on_stderr_but_never_changes_the_jobs_status(tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"v" * (1024 * KIB + 1))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").symlink_to(victim)
    done = wrapper(logs, "a.log", "print('ran')")
    assert done.returncode == 0 and done.stdout == "ran\n" and "not rotated" in done.stderr and (logs / "a.log").is_symlink()
    assert victim.stat().st_size == 1024 * KIB + 1


def test_the_wrapper_exits_127_for_a_job_that_cannot_start_and_2_for_a_bad_call(tmp_path):
    logs = make(tmp_path, size=1)
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), str(logs), "a.log", "--", str(tmp_path / "no-such-program")], capture_output=True, text=True)
    assert done.returncode == 127 and "cannot start the job" in done.stderr
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), str(logs), "a.log"], capture_output=True, text=True)
    assert done.returncode == 2 and "usage" in done.stderr
