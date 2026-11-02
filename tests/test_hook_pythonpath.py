"""The session hooks drop an inherited PYTHONPATH: an empty or relative element would put the
hook's working directory ahead of the stdlib and import a planted json.py from it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
