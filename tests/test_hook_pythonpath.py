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


@pytest.mark.parametrize("home", [".", "sub", "missing"])
@pytest.mark.parametrize("script", sorted(PAYLOADS))
def test_relative_home_is_made_absolute_before_it_becomes_pythonpath(tmp_path, script, home):
    # A relative element would be resolved again against whatever working directory the interpreter
    # (or a child it spawns) has. ACE_PYTHON is a stub that prints the PYTHONPATH it was given.
    # (Planting a json.py inside the named home cannot discriminate: the absolute path of that
    # directory is the operator's own choice and holds it too.)
    cwd = tmp_path / "cwd"
    (cwd / "sub").mkdir(parents=True)
    homedir = tmp_path / "home"
    homedir.mkdir()
    stub = tmp_path / "pystub"
    stub.write_text('#!/bin/bash\nprintf "%s" "$PYTHONPATH"\n')
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"ACE_PLAYBOOK_HOME": home, "ACE_PYTHON": str(stub), "HOME": str(homedir)})
    p = subprocess.run([str(HOOKS / script)], input=json.dumps(PAYLOADS[script]), capture_output=True,
                       text=True, env=env, cwd=cwd, timeout=30)
    assert p.returncode == 0
    if home == "missing":
        assert p.stdout == "" and p.stderr == "", "an unusable home must exit 0 quietly"
    else:
        expected = str((cwd / home).resolve())
        assert p.stdout == expected, f"{script} gave PYTHONPATH={p.stdout!r}, wanted {expected!r}"
