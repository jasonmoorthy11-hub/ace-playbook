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


def run_brief_of(text):
    return text[text.index("local function run_brief()"):text.index("-- the network is not up")]


def test_the_lua_exports_the_config_file_of_ace_config_to_the_task_environment():
    text = LUA.read_text()
    assert "config_file = local_config.config_file" in text and "usable(local_config.config_file)" in text
    env_table = re.search(r"local env = \{([^}]*)\}", text)
    assert env_table and "HOME = home" in env_table.group(1) and "PATH =" in env_table.group(1)       # the task still gets HOME and PATH
    assert "ACE_PLAYBOOK_CONFIG = config_file" in env_table.group(1)           # unconditional: the run is skipped before it when there is none
    assert re.search(r"setEnvironment\(env\)", text)                                                  # the table with the export is the one passed


def test_the_lua_skips_the_run_with_a_warning_when_ace_config_has_no_config_file():
    text = LUA.read_text()
    run = run_brief_of(text)
    guard = re.search(r"if malformed or not config_file then(.*?)\n  end", run, re.S)
    assert guard and "hs.printf(" in guard.group(1) and "hs.alert.show(" in guard.group(1) and re.search(r"\breturn\b", guard.group(1))
    assert run.index("if malformed or not config_file then") < run.index("hs.task.new(") < run.index("setEnvironment(env)")      # fail closed first
    assert not re.search(r"if config_file then", text) and "default config" in text.split("local home")[0]        # no silent fallback; the header says so
    assert text.count("(") == text.count(")") and text.count("{") == text.count("}")


def test_the_lua_reads_an_empty_or_blank_config_file_as_no_config_file_not_as_a_literal_empty_path():
    """No Lua interpreter here, so the text: a value is used only when `usable` (a string with a non-blank character); anything else leaves
    config_file nil, which the guard in run_brief turns into the skipped run. An empty ACE_PLAYBOOK_CONFIG would be read as the default config."""
    text = LUA.read_text()
    fn = re.search(r"local function usable\(v\)(.*?)\nend", text, re.S)
    assert fn and 'type(v) == "string"' in fn.group(1) and '%S' in fn.group(1)
    assert "if usable(local_config.config_file) then config_file = local_config.config_file end" in text
    assert "ACE_PLAYBOOK_CONFIG = config_file" in text and "ACE_PLAYBOOK_CONFIG = \"\"" not in text


def test_the_lua_fails_closed_with_one_logged_line_on_an_ace_config_that_is_not_a_table_or_has_a_malformed_value():
    text = LUA.read_text()
    head = text[:text.index("-- The task and the timer")]
    assert 'pcall(require, "ace_config")' in head and "local malformed = false" in head
    assert 'type(local_config) ~= "table"' in head                                        # a string, number or boolean returned by the file
    for key in ("brief_script", "config_file"):
        assert re.search(r'local_config\.%s ~= nil and type\(local_config\.%s\) ~= "string"' % (key, key), head)       # present but not a string
    assert head.count("malformed = true") >= 3 and head.count("hs.printf(") >= 3          # one logged line per kind of failure
    assert not re.search(r"\berror\(|\bassert\(", text)                                    # never raised: the load must not crash init.lua
    assert text.count("(") == text.count(")") and text.count("{") == text.count("}")


def test_the_lua_task_environment_names_user_and_logname_so_the_claude_cli_finds_its_login():
    """Under { HOME, PATH, ACE_PLAYBOOK_CONFIG } alone `claude -p` says "Not logged in" (a live wake run failed that way). An unset getenv
    is nil, and a nil field is simply absent from a Lua table, so no key is passed with a nil value."""
    text = LUA.read_text()
    env_table = re.search(r"local env = \{([^}]*)\}", text).group(1)
    assert 'USER = os.getenv("USER")' in env_table and 'LOGNAME = os.getenv("LOGNAME") or os.getenv("USER")' in env_table
    assert "HOME = home" in env_table and "PATH =" in env_table and "ACE_PLAYBOOK_CONFIG = config_file" in env_table
    guide = (ROOT / "hooks" / "INSTALL.md").read_text()
    section = guide[guide.index("## 8. Brief on wake"):guide.index("## What the hooks write")]
    assert "USER" in text.split("local home")[0] and "HOME, USER, LOGNAME, PATH and ACE_PLAYBOOK_CONFIG" in section


def test_the_install_guide_and_the_plist_require_the_same_config_file_for_both_triggers():
    guide = (ROOT / "hooks" / "INSTALL.md").read_text()
    section = guide[guide.index("## 8. Brief on wake"):guide.index("## What the hooks write")]
    assert "config_file" in section and "ACE_PLAYBOOK_CONFIG" in section and "same config file" in section and "no exclusions" in section
    assert "config_file" in PLIST.read_text()
