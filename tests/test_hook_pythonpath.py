"""The session hooks drop an inherited PYTHONPATH: an empty or relative element would put the
hook's working directory ahead of the stdlib and import a planted json.py from it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.inject.ledger import read_jsonl
from ace_playbook.store import DeltaLog

from .helpers import GLOBAL, add_draft

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


HOMES = [".", "sub", "./sub", "sub/..", "/nonexistent:", "/x:.", "<ROOT>:", "<ROOT>:sub", "<ROOT>\n", "<NOPKG>", "//nonexistent"]
HOME_IDS = ["dot", "sub", "dot-sub", "sub-dotdot", "abs-colon", "abs-colon-dot", "root-colon", "root-colon-sub", "root-newline",
            "dir-without-package", "double-slash-missing"]
BASH = [None, "/bin/bash"]


def _env_for(home, tmp_path, homedir):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"ACE_PLAYBOOK_HOME": home, "ACE_PYTHON": sys.executable, "HOME": str(homedir)})
    return env


def _run(script, env, cwd, bash):
    argv = [str(HOOKS / script)] if bash is None else [bash, str(HOOKS / script)]
    return subprocess.run(argv, input=json.dumps(PAYLOADS[script]), capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=30)


@pytest.mark.parametrize("bash", BASH, ids=["shebang", "bin-bash"])
@pytest.mark.parametrize("home", HOMES, ids=HOME_IDS)
@pytest.mark.parametrize("script", sorted(PAYLOADS))
def test_an_unusable_home_is_refused(tmp_path, script, home, bash):
    # A relative home, a home holding ':' or a newline, or a directory without the package is not
    # the repo: the hook must do nothing. The cwd and cwd/sub hold planted modules that write a marker.
    cwd = tmp_path / "cwd"
    (cwd / "sub").mkdir(parents=True)
    homedir = tmp_path / "home"
    homedir.mkdir()
    nopkg = tmp_path / "nopkg"
    nopkg.mkdir()
    marker = tmp_path / "marker"
    plant = f"open({str(marker)!r}, 'a').write('imported')\n"
    for d in (cwd, cwd / "sub"):
        (d / "json.py").write_text(plant)
    (cwd / "sitecustomize.py").write_text(plant)
    home = home.replace("<ROOT>", str(ROOT)).replace("<NOPKG>", str(nopkg))
    p = _run(script, _env_for(home, tmp_path, homedir), cwd, bash)
    assert p.returncode == 0
    assert p.stdout == "" and p.stderr == "", f"{script} must do nothing for an unusable home"
    assert not marker.exists(), f"{script} imported a planted module from the working directory"


@pytest.mark.parametrize("bash", BASH, ids=["shebang", "bin-bash"])
def test_the_absolute_repo_home_still_prints_the_block(tmp_path, bash):
    conf = tmp_path / "config.json"
    data = tmp_path / "data"
    conf.write_text(json.dumps({"data_dir": str(data), "holdout_enabled": False}))
    homedir = tmp_path / "home"
    homedir.mkdir()
    cfg = cfgmod.load(conf)
    DeltaLog(cfg.deltas).append(add_draft("Pythonpath visible pinned global lesson", GLOBAL,
                                          status="active", pinned=True))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = _env_for(str(ROOT), tmp_path, homedir)
    env["ACE_PLAYBOOK_CONFIG"] = str(conf)
    p = _run("sessionstart.sh", env, cwd, bash)
    assert p.returncode == 0
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Pythonpath visible pinned global lesson" in ctx
