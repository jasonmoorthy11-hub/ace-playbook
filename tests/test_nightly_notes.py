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
    for bad in ("backup 7h", 5, {"a": "b"}, None):
        path.write_text(json.dumps({**base, "notes": bad}))
        assert "backup 7h" not in build(w)["heartbeat"]["notes"] and build(w)["heartbeat"]["last_nightly_ok"] == base["last_run"]
    path.write_text(json.dumps(base))
    assert "backup" not in status.render(build(w))


def test_the_config_takes_the_two_keys_expands_the_home_and_wants_an_existing_regular_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    good = script(tmp_path, "check.sh", "echo ok\n")
    cfg = cfgmod.from_dict({"backup_check": "~/check.sh", "brain_check": str(good)})
    assert cfg.backup_check == good and cfg.brain_check == good
    cfg = cfgmod.from_dict({})
    assert cfg.backup_check is None and cfg.brain_check is None
    for key in ("backup_check", "brain_check"):
        for bad in (str(tmp_path / "missing.sh"), str(tmp_path), ""):
            with pytest.raises(ValueError, match=key):
                cfgmod.from_dict({key: bad if bad else "~"})      # "" reads as no value to from_dict; "~" is a directory
    with pytest.raises(ValueError, match="brain_check"):
        cfgmod.Config(brain_check="")                              # a blank path is not "absent"


def test_the_config_loader_raises_for_a_missing_check_script(tmp_path):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "backup_check": str(tmp_path / "gone.sh")}))
    with pytest.raises(ValueError, match="backup_check"):
        cfgmod.load(conf)


def run_nightly(tmp_path, monkeypatch, capsys, conf_extra, *args):
    import importlib.util
    spec = importlib.util.spec_from_file_location("nightly_sweep_under_test", HOOKS / "nightly_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "chain_key_path": None, **conf_extra}))
    monkeypatch.setattr(sys, "argv", ["nightly_sweep.py", "--config", str(conf), *args])
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {"transcripts": 0})
    monkeypatch.setattr(mod, "run_sweep", lambda cfg, **kw: [])
    rc = mod.main()
    capsys.readouterr()
    return rc, cfgmod.load(conf), mod


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
