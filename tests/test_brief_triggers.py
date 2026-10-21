"""The wake triggers: the LaunchAgent template and the Hammerspoon file. No Lua interpreter is installed here, so the Lua is checked
only as text (the events it watches, the script it runs, no network call); it was reviewed by eye, never run."""

import plistlib
import re
import shutil
import subprocess

import pytest

from .test_statusline import ROOT

PLIST = ROOT / "hooks" / "launchd" / "com.example.ace-brief.plist"
LUA = ROOT / "hooks" / "hammerspoon" / "ace.lua"


def test_the_launch_agent_template_parses_and_runs_brief_sh_once_at_login():
    data = plistlib.loads(PLIST.read_bytes())
    assert data["RunAtLoad"] is True and "StartInterval" not in data and "StartCalendarInterval" not in data and "KeepAlive" not in data
    assert data["ProgramArguments"] == ["/bin/bash", "__ACE_PLAYBOOK_HOME__/hooks/brief.sh"]
    path = data["EnvironmentVariables"]["PATH"].split(":")
    assert "/opt/homebrew/bin" in path and "__ACE_USER_HOME__/.local/bin" in path
    assert set(re.findall(r"__[A-Z_]+__", str(data))) == {"__ACE_PLAYBOOK_HOME__", "__ACE_CONFIG_FILE__", "__ACE_USER_HOME__", "__ACE_DATA_DIR__"}


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
def test_plutil_lints_the_template():
    p = subprocess.run(["plutil", "-lint", str(PLIST)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def test_the_lua_watches_wake_and_unlock_and_runs_the_brief_script_through_hs_task():
    text = LUA.read_text()
    assert "hs.caffeinate.watcher.systemDidWake" in text and "hs.caffeinate.watcher.screensDidUnlock" in text
    assert 'os.getenv("HOME") .. "/ace-playbook/hooks/brief.sh"' in text and 'pcall(require, "ace_config")' in text
    assert "hs.task.new(" in text and "ace_brief_watcher:start()" in text
    assert not re.search(r"hs\.http|io\.popen|os\.execute|hs\.execute|load\(|loadstring", text)           # one fixed script, no shell string
    assert text.count("(") == text.count(")") and text.count("{") == text.count("}")


def test_nothing_in_the_trigger_files_names_a_home_directory_or_a_real_account():
    for path in (PLIST, LUA):
        assert "/".join(["", "Users", ""]) not in path.read_text()
