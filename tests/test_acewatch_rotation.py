"""The sandbox logs are opened and rotated by the acewatch user itself (hooks/acewatch/acewatch_run.py): the plists name no launchd log path and no root
process works inside the acewatch-writable logs directory (docs/watch/SANDBOX.md "Log rotation"). Temp directories and the text of the files only: nothing
here installs or runs a template."""

import importlib.util
import os
import plistlib
import re
import signal
import stat
import subprocess
import sys
import time
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


def test_each_plist_starts_its_scan_through_the_wrapper_which_opens_and_rotates_exactly_two_logs_per_plist():
    seen = set()
    for data, args in plist_args():
        assert args[1:3] == ["-I", "__ACE_PLAYBOOK_COPY__/hooks/acewatch/acewatch_run.py"]
        assert args[3] == HOME + "/logs" and args[6] == "--" and args[7:9] == [args[0], "-m"]      # the scan argv follows `--`, unchanged
        out, err = args[4], args[5]
        assert out.endswith(".out.log") and err.endswith(".err.log") and "/" not in out + err
        seen |= {out, err}
    assert len(seen) == 4


def test_no_plist_names_a_launchd_log_path_so_launchd_opens_no_file_in_the_acewatch_directory():
    for path in sorted(FOLDER.glob("*.plist")):
        data = plistlib.loads(path.read_bytes())
        assert "StandardOutPath" not in data and "StandardErrorPath" not in data, path.name
        assert "<key>Standard" not in path.read_text(encoding="utf-8")
        assert "sudo launchctl print system/com.example.acewatch" in path.read_text(encoding="utf-8")       # where a job that cannot start shows
        assert [a for a in data["ProgramArguments"] if "/logs" in a] == [HOME + "/logs"]                    # the only logs path is an argument of the acewatch-run job


def test_the_runbook_log_rotation_section_says_who_opens_and_rotates_and_has_no_root_command_in_the_logs_directory():
    raw = SANDBOX.read_text(encoding="utf-8")
    text = " ".join(raw.split())
    log = text.split("## Log rotation", 1)[1].split("## The operator-side status line", 1)[0]
    assert "NO `StandardOutPath` and NO `StandardErrorPath`" in log and "opens no file anywhere" in log and "hooks/acewatch/acewatch_run.py" in log
    assert "opens the job's two logs itself" in log and "does NOT run the scan" in log and "exits with status 3" in log
    assert "never as root" in log and "not compressed" in log and "5 archives are kept" in log and "1024 KiB" in log
    assert "forwards the signal to the scan" in log and "without rotating" in log and "`ExecStart`" in log
    assert "sudo install" not in log and "sudo newsyslog" not in log and " sed " not in log and "the script is already in it" not in text
    assert not re.search(r"sudo (-u root )?(rm|mv|chown|chmod|install|cp)\b[^`\n]*/var/acewatch/logs", raw)       # root never works inside the logs directory
    assert "sudo -u acewatch rm /var/acewatch/logs/<file>" in text and "/etc/newsyslog.d/acewatch.conf" in text.split("## Uninstalling", 1)[1]


def test_the_runbook_has_an_upgrade_section_before_log_rotation_with_the_steps_in_order_each_with_its_state():
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    assert text.index("## Upgrading an existing install") < text.index("## Log rotation")
    section = text.split("## Upgrading an existing install", 1)[1].split("## Log rotation", 1)[0]
    marks = ["1. If the old newsyslog file was installed: `sudo rm /etc/newsyslog.d/acewatch.conf`", "2. Replace the sudoers line with the `-I` form",
             "3. `sudo launchctl bootout system/com.yourdomain.acewatch`", "4. The log files the OLD plists made", "5. Refresh the root-owned copy",
             "6. Re-run step 7's `sed`"]
    assert [section.index(m) for m in marks] == sorted(section.index(m) for m in marks)       # both security fixes first, the outage window shortest
    assert section.count("State: ") == 6 and "already finds `ace_playbook` under `-I`" in section and "no scan runs, from here until step 6" in section
    assert 'sudo launchctl print system/com.yourdomain.acewatch | grep -E "state|last exit"' in section and "`last exit code = 0`, not 3" in section
    assert "sudo ls -lna /var/acewatch/logs" in section and "the `.` line is the directory itself" in section and "`*.bz2`, the old template's `J` flag" in section
    assert "if the Mac restarts before step 6, the OLD jobs are loaded again at boot" in section and "launchd still opens their logs there (possibly as root) until step 3" in section
    assert "sudo ls -lna /var/acewatch/logs" in section and "sudo -u acewatch rm /var/acewatch/logs/<file>" in section and "REFUSES to run that job (exit status 3)" in section
    assert "root never deletes inside that tree" in section and '-I -c "import ace_playbook"' in section
    assert "Never re-run step 2's `sudo chown -R acewatch:acewatch /var/acewatch` after the first install" in section


def test_step_2_always_creates_the_logs_directory_as_acewatch_and_roots_chown_is_first_install_only():
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    step = text.split("2. `/var/acewatch` is owned by `acewatch`", 1)[1].split("3. Give it a checkout copy", 1)[0]
    assert "sudo -u acewatch mkdir -p -m 0700 /var/acewatch/logs" in step and "on the FIRST install only" in step and "/var/acewatch/logs`, then" not in step


def test_the_runbook_says_where_a_refusal_reason_goes_and_names_the_launchctl_command_for_the_last_case():
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    log = text.split("## Log rotation", 1)[1].split("## The operator-side status line", 1)[0]
    assert "the err log is opened first; if it opens, the reason (an out-log refusal included) is written there" in log
    assert "the reason is written to the out log if that opens" in log and "neither opens, or the directory is refused" in log
    assert "the only trace is `last exit code = 3`" in log and 'sudo launchctl print system/com.yourdomain.acewatch | grep -E "state|last exit"' in log
    assert "was NOT checked on a real install" in log and "tested in temp directories only" in log
    assert "a missing one is created 0700, only its last path component" in log and "stdin is `/dev/null`" in log and "a hard link" in log
    tail = text.split("## Uninstalling", 1)[1]
    assert "launchctl bootout system/com.yourdomain.acewatch" in tail and "launchctl bootout system/com.yourdomain.acewatch.weekly" in tail


def test_step_3_requires_a_root_owned_interpreter_venv_and_copy_with_a_check_that_prints_nothing():
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    step = text.split("3. Give it a checkout copy", 1)[1].split("4. `acewatch`'s own config", 1)[0]
    assert "NOT a Homebrew or other python installed by a user account" in step and "must ALL be owned by root and not writable by group or other" in step
    assert '! -type l \\( ! -user root -o -perm -020 -o -perm -002 \\) -print' in step and "readlink -f" in step and "must print nothing" in step
    assert "provided step 3's ownership holds" in text
    assert 'P=$(dirname "$(dirname "$(sudo readlink -f <copy>/.venv/bin/python)")")' in step and 'case "$P" in ""|.|/) echo "refusing: no interpreter tree found"' in step
    assert "Darwin 25.6" in step and "not been run on Linux" in step


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
    with pytest.raises(run.Refused, match="a.log is not a regular file with one link owned"):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert sorted(p.name for p in logs.iterdir()) == ["a.log"]


def test_a_logs_directory_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    logs = make(tmp_path, size=2 * KIB)
    mine = os.stat(logs).st_uid
    _euid_sequence(monkeypatch, [mine + 1, mine])
    with pytest.raises(run.Refused, match="directory is not owned"):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert sorted(p.name for p in logs.iterdir()) == ["a.log"]


def test_a_hard_linked_log_or_archive_is_refused_and_the_other_name_is_left_alone(tmp_path):
    other = tmp_path / "other"
    other.write_bytes(b"o" * 10)
    logs = make(tmp_path, size=2 * KIB)
    os.link(logs / "a.log", other.with_name("twin"))
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "a.log", size_kib=1)
    (tmp_path / "twin").unlink()
    os.link(other, logs / "a.log.1")
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "a.log", size_kib=1)
    assert other.read_bytes() == b"o" * 10 and sorted(p.name for p in logs.iterdir()) == ["a.log", "a.log.1"]


def test_a_name_with_a_slash_is_refused(tmp_path):
    logs = make(tmp_path, size=2 * KIB)
    with pytest.raises(run.Refused):
        run.rotate(str(logs), "../logs/a.log", size_kib=1)


