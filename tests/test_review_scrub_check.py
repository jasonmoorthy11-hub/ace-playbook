"""Regression tests for the 2026-10-17 stage-1 review: staged scanning and pre-commit isolation."""
import os
import subprocess
import sys
from pathlib import Path

from ace_playbook.scrub.check import scan_files

from . import fakes

ROOT = Path(__file__).resolve().parent.parent


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_staged_mode_scans_the_index_blob_not_the_working_tree(tmp_path):
    _git(tmp_path, "init", "-q")
    leak = tmp_path / "leak.py"
    leak.write_text(f'KEY = "{fakes.github()}"\n')
    _git(tmp_path, "add", "leak.py")
    leak.write_text("KEY = None\n")  # working tree is clean, the index is not
    assert scan_files(tmp_path, ["leak.py"], "pre-commit", (), staged=True)
    assert not scan_files(tmp_path, ["leak.py"], "pre-commit", (), staged=False)
    _git(tmp_path, "add", "leak.py")
    leak.write_text(f'KEY = "{fakes.github()}"\n')  # now the reverse
    assert not scan_files(tmp_path, ["leak.py"], "pre-commit", (), staged=True)


def test_pre_commit_hook_ignores_modules_planted_in_the_scanned_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "ace_playbook" / "scrub").mkdir(parents=True)
    marker = tmp_path / "PWNED"
    evil = f"open({str(marker)!r}, 'w').write('x')\n"
    (repo / "ace_playbook" / "__init__.py").write_text(evil)
    (repo / "ace_playbook" / "scrub" / "__init__.py").write_text(evil)
    (repo / "ace_playbook" / "scrub" / "__main__.py").write_text(evil)
    for shadow in ("json.py", "subprocess.py", "pathlib.py", "argparse.py", "sitecustomize.py"):
        (repo / shadow).write_text(evil)
    _git(repo, "init", "-q")
    (repo / "leak.py").write_text(f'KEY = "{fakes.github()}"\n')
    _git(repo, "add", "leak.py")
    e = {**os.environ, "ACE_PLAYBOOK_HOME": str(ROOT), "ACE_PYTHON": sys.executable,
         "PYTHONPATH": str(repo)}
    p = subprocess.run([str(ROOT / "hooks" / "pre-commit-scrub.sh")], cwd=repo, env=e,
                       capture_output=True, text=True)
    assert not marker.exists()
    assert p.returncode == 1 and "github_token" in p.stdout


def test_pre_commit_hook_uses_isolated_python():
    text = (ROOT / "hooks" / "pre-commit-scrub.sh").read_text()
    assert "-I -P" in text and "PYTHONSAFEPATH=1" in text and "--root" in text
    assert "PYTHONPATH=" not in text
