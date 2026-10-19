import subprocess
import sys
from pathlib import Path

import pytest

from .migrate_support import Env

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "ace_playbook" / "migrate"


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def base_args(env):
    return ["--legacy-glob", str(env.legacy_dir / "lessons-*.json"), "--classified", str(env.classified),
            "--config", str(env.config), "--out-dir", str(env.out)]


def test_both_modes_is_usage_error(env, capsys):
    from ace_playbook.migrate.__main__ import main
    with pytest.raises(SystemExit) as e:
        main(base_args(env) + ["--dry-run", "--apply"])
    assert e.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_neither_mode_is_usage_error(env, capsys):
    from ace_playbook.migrate.__main__ import main
    with pytest.raises(SystemExit) as e:
        main(base_args(env))
    assert e.value.code == 2
    assert "one of the arguments --dry-run --apply is required" in capsys.readouterr().err


def test_dry_run_exits_zero_as_subprocess(env):
    proc = subprocess.run([sys.executable, "-m", "ace_playbook.migrate", *base_args(env), "--dry-run"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN" in proc.stdout
    assert not env.data_dir.exists()


def test_missing_classified_file_is_exit_2(env, capsys):
    from ace_playbook.migrate.__main__ import main
    env.classified.unlink()
    assert main(base_args(env) + ["--dry-run"]) == 2
    assert "error:" in capsys.readouterr().err


def test_missing_config_file_is_exit_2(env, capsys):
    from ace_playbook.migrate.__main__ import main
    env.config.unlink()
    assert main(base_args(env) + ["--dry-run"]) == 2


def test_no_network_and_no_reflect_client():
    src = "\n".join(p.read_text() for p in sorted(PKG.glob("*.py")))
    for banned in ("reflect.client", "reflect import client", "urllib", "http.client", "socket", "requests",
                   "subprocess"):
        assert banned not in src, banned


def test_no_personal_paths_in_package():
    src = "\n".join(p.read_text() for p in sorted(PKG.glob("*.py")))
    for banned in ("/Us" + "ers/", "jason" + "moorthy"):  # split so the CI grep does not match this file
        assert banned not in src


def test_files_stay_under_400_lines():
    files = sorted(PKG.glob("*.py"))
    assert len(files) <= 6
    assert all(len(p.read_text().splitlines()) <= 400 for p in files)
