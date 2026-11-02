"""The session hooks drop an inherited PYTHONPATH: an empty or relative element would put the
hook's working directory ahead of the stdlib and import a planted json.py from it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook.inject.ledger import read_jsonl

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"

PAYLOADS = {
    "sessionstart.sh": {"session_id": "s1", "cwd": "/x", "source": "startup"},
    "sessionend.sh": {"session_id": "s1", "transcript_path": "/x/s1.jsonl", "reason": "clear"},
    "stop_citation_ledger.sh": {"session_id": "s1", "last_assistant_message": "nothing cited"},
}
INHERITED = [None, "/nonexistent:", ":/nonexistent", "."]


@pytest.mark.parametrize("inherited", INHERITED, ids=["unset", "trailing-empty", "leading-empty", "dot"])
@pytest.mark.parametrize("script", sorted(PAYLOADS))
def test_hook_never_imports_from_the_working_directory(tmp_path, script, inherited):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "holdout_enabled": False}))
    marker = tmp_path / "marker"
    (cwd / "json.py").write_text(f"open({str(marker)!r}, 'w').write('imported')\n")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"ACE_PLAYBOOK_CONFIG": str(conf), "ACE_PLAYBOOK_HOME": str(ROOT),
                "ACE_PYTHON": sys.executable, "HOME": str(home)})
    if inherited is not None:
        env["PYTHONPATH"] = inherited
    p = subprocess.run([str(HOOKS / script)], input=json.dumps(PAYLOADS[script]), capture_output=True,
                       text=True, env=env, cwd=cwd, timeout=30)
    assert p.returncode == 0
    assert not marker.exists(), f"{script} imported json.py from the working directory"
    if script == "sessionend.sh":
        q = list(read_jsonl(tmp_path / "data" / "queue.jsonl"))
        assert q and q[0]["session_id"] == "s1", "sessionend.sh did not enqueue under this PYTHONPATH"


@pytest.mark.parametrize("home", [".", "sub", "./sub", "sub/.."])
@pytest.mark.parametrize("script", sorted(PAYLOADS))
def test_a_relative_home_is_refused(tmp_path, script, home):
    # A relative home, even made absolute, is the attacker-chosen working directory (or a directory
    # under it): the hook must do nothing. The cwd and cwd/sub hold planted modules that write a marker.
    cwd = tmp_path / "cwd"
    (cwd / "sub").mkdir(parents=True)
    homedir = tmp_path / "home"
    homedir.mkdir()
    marker = tmp_path / "marker"
    plant = f"open({str(marker)!r}, 'a').write('imported')\n"
    for d in (cwd, cwd / "sub"):
        (d / "json.py").write_text(plant)
    (cwd / "sitecustomize.py").write_text(plant)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"ACE_PLAYBOOK_HOME": home, "ACE_PYTHON": sys.executable, "HOME": str(homedir)})
    p = subprocess.run([str(HOOKS / script)], input=json.dumps(PAYLOADS[script]), capture_output=True,
                       text=True, env=env, cwd=cwd, timeout=30)
    assert p.returncode == 0
    assert p.stdout == "" and p.stderr == "", f"{script} must do nothing for a relative home"
    assert not marker.exists(), f"{script} imported a planted module from the working directory"
