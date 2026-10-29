"""B-3: the nightly's optional `backup` / `brain drift` note lines (ace_playbook/nightly_notes.py, health.record, `compile status`).
Every check here is a throwaway script written under tmp_path: no test runs the operator's own scripts."""

import json
import os
import stat
import sys
import time

import pytest

from ace_playbook import config as cfgmod
from ace_playbook import health, nightly_notes
from ace_playbook.compile import offline, status

from .test_compile_status import build, w  # noqa: F401 - w is a fixture
from .test_hooks import HOOKS  # noqa: F401


def script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def cfg_of(tmp_path, backup=None, brain=None):
    return cfgmod.Config(data_dir=tmp_path / "data", backup_check=backup, brain_check=brain)


def test_a_backup_line_and_a_brain_line_become_notes_in_order(tmp_path):
    backup = script(tmp_path, "b.sh", '[ "$1" = "--check" ] && echo "backup 7h"\n')
    brain = script(tmp_path, "n.sh", 'echo "brain drift: 2 files (1 skipped, 16 entropy)"\n')
    assert nightly_notes.collect(cfg_of(tmp_path, backup, brain)) == ["backup 7h", "brain drift: 2 files (1 skipped, 16 entropy)"]
    assert nightly_notes.collect(cfg_of(tmp_path, backup=backup)) == ["backup 7h"]
    assert nightly_notes.collect(cfg_of(tmp_path, brain=brain)) == ["brain drift: 2 files (1 skipped, 16 entropy)"]


def test_the_script_gets_only_check_a_fixed_path_and_the_home_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_FOR_THE_CHILD", "leak")
    seen = tmp_path / "seen"
    brain = script(tmp_path, "n.sh", f'echo "$# $1 $PATH $HOME ${{SECRET_FOR_THE_CHILD:-none}}" > {seen}\necho "brain drift: 0 files"\n')
    assert nightly_notes.collect(cfg_of(tmp_path, brain=brain)) == ["brain drift: 0 files"]
    assert seen.read_text().split() == ["1", "--check", nightly_notes.PATH, os.path.expanduser("~"), "none"]


@pytest.mark.parametrize("key, text", [("backup", "backup check failed"), ("brain", "brain check failed")])
def test_a_non_zero_exit_is_a_failed_line_that_leaks_neither_stdout_nor_stderr(tmp_path, key, text):
    bad = script(tmp_path, "x.sh", 'echo "backup 1h"\necho "SECRET-DETAIL $0" >&2\nexit 1\n')
    out = nightly_notes.collect(cfg_of(tmp_path, **{key: bad}))
    assert out == [text] and "SECRET" not in out[0] and str(tmp_path) not in out[0]


def test_a_script_that_outlasts_the_timeout_is_a_failed_line_and_is_not_waited_for(tmp_path, monkeypatch):
    monkeypatch.setattr(nightly_notes, "TIMEOUT", 1)
    slow = script(tmp_path, "slow.sh", "exec sleep 30\n")
    began = time.monotonic()
    assert nightly_notes.collect(cfg_of(tmp_path, backup=slow)) == ["backup check failed"]
    assert time.monotonic() - began < 10


def test_a_line_that_shows_a_path_is_a_failed_line(tmp_path):
    for body in ('echo "backup 7h /home/someone/ace_backup.sh"\n', 'echo "$0"\n', 'echo "see /var/log/x"\n'):
        assert nightly_notes.collect(cfg_of(tmp_path, backup=script(tmp_path, "p.sh", body))) == ["backup check failed"]


def test_an_empty_blank_or_unprintable_first_line_and_unreadable_output_are_failed_lines(tmp_path):
    for body in ("exit 0\n", 'echo\necho "backup 7h"\n', 'echo "   "\n', 'printf "backup \\033[31m7h\\n"\n', 'printf "\\377\\376 backup\\n"\n'):
        assert nightly_notes.collect(cfg_of(tmp_path, brain=script(tmp_path, "e.sh", body))) == ["brain check failed"], body


def test_only_the_first_line_is_kept_stripped_and_cut_to_80_characters(tmp_path):
    long = "backup " + "7h " * 60
    backup = script(tmp_path, "b.sh", f'echo "   {long}   "\necho "second line"\n')
    [line] = nightly_notes.collect(cfg_of(tmp_path, backup=backup))
    assert len(line) <= 80 and line.startswith("backup 7h 7h") and "second" not in line and line == line.strip()


def test_a_script_that_cannot_be_started_is_a_failed_line_without_the_error_text(tmp_path):
    plain = tmp_path / "plain.sh"
    plain.write_text("#!/bin/bash\necho hi\n")
    plain.chmod(stat.S_IMODE(plain.stat().st_mode) & 0o600)                         # a regular file that is not executable: PermissionError
    out = nightly_notes.collect(cfg_of(tmp_path, backup=plain))
    assert out == ["backup check failed"] and "ermission" not in out[0] and "plain" not in out[0]


def test_no_configured_check_means_no_notes_and_the_health_record_is_as_before(tmp_path):
    cfg = cfg_of(tmp_path)
    assert nightly_notes.collect(cfg) == []
    health.record(cfg, True, now=0)
    assert health.read(cfg) == {"last_run": "1970-01-01T00:00:00Z", "ok": True, "consecutive_failures": 0, "last_error": ""}
    health.record(cfg, True, now=0, notes=[])
    assert "notes" not in health.read(cfg)


def test_health_record_stores_the_notes_as_a_list_of_strings_of_at_most_80_characters(tmp_path):
    cfg = cfg_of(tmp_path)
    health.record(cfg, True, now=0, notes=["backup 7h", "n" * 200, 5, None])
    rec = health.read(cfg)
    assert rec["notes"] == ["backup 7h", "n" * 80] and rec["ok"] is True
    health.record(cfg, False, "x", now=0, notes=["backup check failed"])
    assert health.read(cfg)["notes"] == ["backup check failed"] and health.read(cfg)["consecutive_failures"] == 1
    mode = stat.S_IMODE(os.stat(health.health_path(cfg)).st_mode)
    assert mode == 0o600


def test_status_renders_the_notes_of_the_health_record_and_validates_them(w):
    path = w.cfg.path("health.json")
    base = {"last_run": "2026-10-20T03:00:00Z", "ok": True, "consecutive_failures": 0, "last_error": ""}
    path.write_text(json.dumps({**base, "notes": ["backup 7h", "brain drift: 0 files (1 skipped, 16 entropy)"]}))
    s = build(w)
    assert s["heartbeat"]["notes"][-2:] == ["backup 7h", "brain drift: 0 files (1 skipped, 16 entropy)"]
    assert "notes: " in status.render(s) and "backup 7h, brain drift: 0 files (1 skipped, 16 entropy)" in status.render(s)
    path.write_text(json.dumps({**base, "notes": ["a", 7, None, ["x"], "b" * 200, "c", "d", "e", "f"]}))
    assert build(w)["heartbeat"]["notes"][-4:] == ["a", "b" * 80, "c", "d"]            # strings only, each cut to 80, at most 4
    path.write_text(json.dumps(base))
    plain = build(w)["heartbeat"]["notes"]                                                  # the notes the world has without any check line
    assert "backup" not in status.render(build(w))
    for bad in ("backup 7h", 5, {"a": "b"}, None, True):
        path.write_text(json.dumps({**base, "notes": bad}))
        assert build(w)["heartbeat"]["notes"] == plain and build(w)["heartbeat"]["last_nightly_ok"] == base["last_run"]


def test_status_drops_a_note_with_a_control_character_so_it_never_reaches_the_screen_raw(w):
    base = {"last_run": "2026-10-20T03:00:00Z", "ok": True, "consecutive_failures": 0, "last_error": ""}
    w.cfg.path("health.json").write_text(json.dumps({**base, "notes": ["esc \u001b[31mRED", "nl\nx", "tab\tx", "backup 7h", "x\u0085y"]}))
    s = build(w)
    assert s["heartbeat"]["notes"][-1] == "backup 7h" and not [n for n in s["heartbeat"]["notes"] if not n.isprintable()]
    assert "\x1b" not in status.render(s) and "RED" not in status.render(s) and "nl\n" not in status.render(s)


def test_the_config_takes_the_two_keys_expands_the_home_and_only_type_checks_them(tmp_path, monkeypatch):
    """D-150: a note source never breaks a loader. A path is not looked at until collection time; a missing script LOADS."""
    monkeypatch.setenv("HOME", str(tmp_path))
    good = script(tmp_path, "check.sh", "echo ok\n")
    cfg = cfgmod.from_dict({"backup_check": "~/check.sh", "brain_check": str(good)})
    assert cfg.backup_check == good and cfg.brain_check == good
    cfg = cfgmod.from_dict({})
    assert cfg.backup_check is None and cfg.brain_check is None
    cfg = cfgmod.from_dict({"backup_check": str(tmp_path / "missing.sh"), "brain_check": str(tmp_path)})      # gone, and a directory: both load
    assert cfg.backup_check == tmp_path / "missing.sh" and cfg.brain_check == tmp_path
    for key in ("backup_check", "brain_check"):
        for bad in ("", "   ", 5, ["x"], {"a": 1}, True):
            with pytest.raises(ValueError, match=key):
                cfgmod.from_dict({key: bad})
    with pytest.raises(ValueError, match="brain_check"):
        cfgmod.Config(brain_check="")                              # a blank path is not "absent"


def test_the_config_loader_loads_a_config_whose_check_script_is_gone_and_raises_for_a_blank_or_non_string_value(tmp_path):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "backup_check": str(tmp_path / "gone.sh")}))
    assert cfgmod.load(conf).backup_check == tmp_path / "gone.sh"
    for bad in ("", 7):
        conf.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "backup_check": bad}))
        with pytest.raises(ValueError, match="backup_check"):
            cfgmod.load(conf)


def test_a_check_script_that_is_gone_or_a_directory_is_a_failed_note_not_an_error(tmp_path):
    gone, here = script(tmp_path, "g.sh", "echo ok\n"), tmp_path / "adir"
    here.mkdir()
    cfg = cfg_of(tmp_path, backup=gone, brain=here)
    gone.unlink()
    assert nightly_notes.collect(cfg) == ["backup check failed", "brain check failed"]


def test_a_line_whose_first_word_carries_a_colon_cannot_pose_as_a_digest_category(tmp_path):
    for body in ('echo "silent:hook 9d"\n', 'echo "approvals:broken:3"\n', 'echo "x:y"\n', 'echo "brain: ok"\n'):
        assert nightly_notes.collect(cfg_of(tmp_path, brain=script(tmp_path, "c.sh", body))) == ["brain check failed"], body
    ok = script(tmp_path, "ok.sh", 'echo "brain drift: 2 files (1 skipped)"\n')
    assert nightly_notes.collect(cfg_of(tmp_path, brain=ok)) == ["brain drift: 2 files (1 skipped)"]


def load_nightly(tmp_path, monkeypatch, conf_extra, *args):
    import importlib.util
    spec = importlib.util.spec_from_file_location("nightly_sweep_under_test", HOOKS / "nightly_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "chain_key_path": None, **conf_extra}))
    monkeypatch.setattr(sys, "argv", ["nightly_sweep.py", "--config", str(conf), *args])
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {"transcripts": 0})
    monkeypatch.setattr(mod, "run_sweep", lambda cfg, **kw: [])
    return mod, cfgmod.load(conf)


def run_nightly(tmp_path, monkeypatch, capsys, conf_extra, *args):
    mod, cfg = load_nightly(tmp_path, monkeypatch, conf_extra, *args)
    rc = mod.main()
    capsys.readouterr()
    return rc, cfg, mod


def boom(*a, **kw):
    raise KeyError("detail that must not matter")


def noted(tmp_path, ok=None):
    rec = health.read(cfgmod.Config(data_dir=tmp_path / "data"))
    assert rec is not None and rec["notes"] == ["backup 7h"] and (ok is None or rec["ok"] is ok)
    return rec


def test_a_failed_offline_pass_records_the_failure_with_the_notes(tmp_path, monkeypatch, capsys):
    backup = script(tmp_path, "b.sh", 'echo "backup 7h"\n')
    mod, _ = load_nightly(tmp_path, monkeypatch, {"backup_check": str(backup)})
    monkeypatch.setattr(offline, "run", boom)
    assert mod.main() == 0
    assert noted(tmp_path, ok=False)["consecutive_failures"] == 1


def test_a_dry_run_sweep_that_raises_records_the_failure_with_the_notes(tmp_path, monkeypatch):
    backup = script(tmp_path, "b.sh", 'echo "backup 7h"\n')
    mod, _ = load_nightly(tmp_path, monkeypatch, {"backup_check": str(backup)}, "--dry-run")
    monkeypatch.setattr(mod, "run_sweep", boom)
    with pytest.raises(KeyError):
        mod.main()
    noted(tmp_path, ok=False)


def test_a_real_sweep_that_raises_records_the_failure_with_the_notes(tmp_path, monkeypatch):
    from ace_playbook import sweep
    cfg = cfgmod.Config(data_dir=tmp_path / "data", transcripts_root=tmp_path / "projects")
    monkeypatch.setattr(sweep, "_sweep", boom)
    with pytest.raises(KeyError):
        sweep.run_sweep(cfg, notes=["backup 7h"])
    noted(tmp_path, ok=False)


def test_the_nightly_with_a_deleted_check_script_still_runs_and_records_health_with_a_failed_note(tmp_path, monkeypatch, capsys):
    backup = script(tmp_path, "b.sh", 'echo "backup 7h"\n')
    brain = script(tmp_path, "n.sh", 'echo "brain drift: 0 files"\n')
    mod, cfg = load_nightly(tmp_path, monkeypatch, {"backup_check": str(backup), "brain_check": str(brain)}, "--dry-run")
    backup.unlink()
    assert mod.main() == 0
    capsys.readouterr()
    rec = health.read(cfg)
    assert rec["ok"] is True and rec["notes"] == ["backup check failed", "brain drift: 0 files"]


def test_the_nightly_records_the_notes_in_health_and_a_failing_collection_never_fails_the_night(tmp_path, monkeypatch, capsys):
    backup = script(tmp_path, "b.sh", 'echo "backup 7h"\n')
    rc, cfg, mod = run_nightly(tmp_path, monkeypatch, capsys, {"backup_check": str(backup)}, "--dry-run")
    assert rc == 0 and health.read(cfg)["notes"] == ["backup 7h"]               # --dry-run with a clean offline stage still records the night
    monkeypatch.setattr(mod.nightly_notes, "collect", lambda cfg: 1 / 0)
    monkeypatch.setattr("sys.argv", ["nightly_sweep.py", "--config", str(cfg.source), "--dry-run"])
    assert mod.main() == 0 and "notes" not in health.read(cfg)


def test_a_real_sweep_gets_the_notes_so_its_own_health_write_carries_them(tmp_path, monkeypatch, capsys):
    got = {}
    backup = script(tmp_path, "b.sh", 'echo "backup 7h"\n')
    rc, cfg, mod = run_nightly(tmp_path, monkeypatch, capsys, {"backup_check": str(backup)})
    monkeypatch.setattr(mod, "run_sweep", lambda cfg, **kw: got.update(kw) or [])
    monkeypatch.setattr("sys.argv", ["nightly_sweep.py", "--config", str(cfg.source)])
    assert mod.main() == 0 and got["notes"] == ["backup 7h"] and got["dry_run"] is False


def test_run_sweep_writes_the_notes_into_its_own_health_record(tmp_path):
    from ace_playbook.sweep import run_sweep
    cfg = cfgmod.Config(data_dir=tmp_path / "data", transcripts_root=tmp_path / "projects")
    assert run_sweep(cfg, notes=["backup 7h"]) == [] and health.read(cfg)["notes"] == ["backup 7h"]
    assert run_sweep(cfg) == [] and "notes" not in health.read(cfg)


# ------------------------------------------------------------------ the third key: acewatch_check (an operator-side acewatch status line)
SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "acewatch", "acewatch_check.sh")
AW_LINE = "acewatch open 3  leads 1  oldest 2d  last scan 4h"


def test_the_config_takes_acewatch_check_expands_the_home_and_only_type_checks_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    good = script(tmp_path, "aw.sh", "echo ok\n")
    assert cfgmod.from_dict({"acewatch_check": "~/aw.sh"}).acewatch_check == good
    assert cfgmod.from_dict({}).acewatch_check is None
    assert cfgmod.from_dict({"acewatch_check": str(tmp_path / "gone")}).acewatch_check == tmp_path / "gone"
    for bad in ("", "  ", 3, ["x"]):
        with pytest.raises(ValueError, match="acewatch_check"):
            cfgmod.from_dict({"acewatch_check": bad})


def test_an_acewatch_line_follows_backup_and_brain_and_a_failure_is_acewatch_check_failed(tmp_path):
    aw = script(tmp_path, "aw.sh", f'[ "$1" = "--check" ] && echo "{AW_LINE}"\n')
    backup = script(tmp_path, "b.sh", 'echo "backup 7h"\n')
    assert nightly_notes.collect(cfgmod.Config(data_dir=tmp_path / "data", backup_check=backup, acewatch_check=aw)) == ["backup 7h", AW_LINE]
    assert nightly_notes.collect(cfgmod.Config(data_dir=tmp_path / "data", acewatch_check=aw)) == [AW_LINE]
    bad = script(tmp_path, "bad.sh", 'echo "acewatch open 3"\nexit 1\n')
    empty = script(tmp_path, "empty.sh", "exit 0\n")
    slashed = script(tmp_path, "sl.sh", 'echo "acewatch /var/acewatch"\n')
    for path in (bad, empty, slashed, tmp_path / "missing.sh"):
        assert nightly_notes.collect(cfgmod.Config(data_dir=tmp_path / "data", acewatch_check=path)) == ["acewatch check failed"], path


def test_status_renders_the_acewatch_line_the_nightly_recorded(tmp_path, monkeypatch, capsys, w):
    aw = script(tmp_path, "aw.sh", f'echo "{AW_LINE}"\n')
    rc, cfg, mod = run_nightly(tmp_path, monkeypatch, capsys, {"acewatch_check": str(aw)}, "--dry-run")
    assert rc == 0 and health.read(cfg)["notes"] == [AW_LINE]
    w.cfg.path("health.json").write_text(json.dumps(health.read(cfg)))
    assert AW_LINE in status.render(build(w))


def test_the_arm_notes_stay_first_when_the_acewatch_note_is_configured(tmp_path, monkeypatch, capsys):
    from ace_playbook.compile import arm_check
    arm = ["arm phase missing: x", "holdout on during an arm phase"]
    monkeypatch.setattr(arm_check, "check", lambda cfg, *a, **k: arm)
    aw = script(tmp_path, "aw.sh", f'echo "{AW_LINE}"\n')
    backup = script(tmp_path, "b.sh", 'echo "backup 7h"\n')
    rc, cfg, mod = run_nightly(tmp_path, monkeypatch, capsys, {"acewatch_check": str(aw), "backup_check": str(backup)}, "--dry-run")
    assert health.read(cfg)["notes"] == [*arm, "backup 7h", AW_LINE]


def sample(tmp_path, sudo_body, *args):
    """Run the sample script with a fake `sudo` first on PATH."""
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "sudo"
    fake.write_text("#!/bin/bash\n" + sudo_body)
    fake.chmod(0o755)
    return subprocess.run(["/bin/bash", SAMPLE, *(args or ("--check",))], capture_output=True, text=True, timeout=30,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin"})


def test_the_sample_script_passes_bash_n_and_is_short():
    import subprocess
    assert subprocess.run(["/bin/bash", "-n", SAMPLE], capture_output=True).returncode == 0
    assert len(open(SAMPLE).read().splitlines()) <= 40


def test_the_sample_script_prints_acewatch_and_the_first_status_line(tmp_path):
    argv = tmp_path / "argv"
    where = tmp_path / "cwd"
    done = sample(tmp_path, f'pwd > {where}\nprintf "%s " "$@" > {argv}\nprintf "proposals\\nopen 3  leads 1  oldest 2d  last scan 4h\\n- a title\\n"\n')
    assert (done.returncode, done.stdout) == (0, AW_LINE + "\n")
    assert where.read_text().strip() == "/"                                  # not the caller's working directory
    words = argv.read_text().split()
    assert words[:3] == ["-n", "-u", "acewatch"] and words[-2:] == ["watch", "status"]
    for first in ("none", "disabled"):
        done = sample(tmp_path, f'printf "proposals\\n{first}\\n"\n')
        assert (done.returncode, done.stdout) == (0, f"acewatch {first}\n")


def test_the_sample_script_exits_1_on_any_failure_and_prints_nothing(tmp_path):
    for body in ("exit 1\n", 'printf "proposals\\n"\n', "exit 0\n", 'printf "garbage\\n"\n'):
        done = sample(tmp_path, body)
        assert done.returncode == 1 and done.stdout == "", body
    assert sample(tmp_path, "exit 0\n", "--other").returncode == 1
