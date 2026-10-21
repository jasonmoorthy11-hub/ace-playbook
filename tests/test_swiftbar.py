"""`hooks/swiftbar/ace.5m.sh`: the SwiftBar menu-bar plugin. It runs in a subprocess against a tmp status.json and a throwaway HOME."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "hooks" / "swiftbar" / "ace.5m.sh"
FMT = "%Y-%m-%dT%H:%M:%SZ"


def stamp(hours: float = 0) -> str:
    return time.strftime(FMT, time.gmtime(time.time() - hours * 3600))


def status(**over) -> dict:
    base = {"generated_at": stamp(1), "queue_total": 0, "watch_open": 0, "watch_leads": 0, "watch_oldest": None, "watch_titles": [],
            "heartbeat": {"last_hook_row": stamp(2), "last_offline_scan": None, "last_nightly_ok": None},
            "measurement": {"chains": {"approvals": "ok", "demotions": "ok", "labels": "ok"}}}
    return {**base, **over}


def plugin(tmp_path, content=None, raw=None, conf=True, script=SCRIPT, **env_over):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    if raw is not None:
        (data / "status.json").write_text(raw)
    elif content is not None:
        (data / "status.json").write_text(json.dumps(content))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"data_dir": str(data)}))
    env = {"HOME": str(tmp_path / "home"), "PATH": os.environ["PATH"], "ACE_PYTHON": sys.executable, **env_over}
    if conf:
        env["ACE_PLAYBOOK_CONFIG"] = str(config)
    p = subprocess.run([str(script)], capture_output=True, text=True, env=env, timeout=20)
    assert p.returncode == 0 and p.stderr == ""
    return p.stdout.splitlines()


def test_ok_when_nothing_is_open_and_the_heartbeat_is_fresh(tmp_path):
    out = plugin(tmp_path, status())
    assert out[0] == "● ok  watch 0  unlabelled 0" and out[1] == "---"
    assert out[2] == "ACE  unlabelled 0  chains ok  heartbeat 2h" and out[3] == "Watch  open 0  leads 0  oldest never"
    assert out[4] == "---" and out[5].startswith("Open panel | href=file://") and out[5].endswith("/data/dashboard.html")


def test_needs_you_for_open_proposals_or_unlabelled_fires_with_up_to_three_titles(tmp_path):
    titles = ["one", "two | with pipe", "three\x1b[2J\nnewline", "four is dropped"]
    out = plugin(tmp_path, status(watch_open=2, watch_leads=1, watch_oldest=stamp(50), watch_titles=titles))
    assert out[0] == "◐ needs you  watch 2  unlabelled 0"
    assert out[3] == "Watch  open 2  leads 1  oldest 2d"
    assert out[4:7] == ["- one", "- two  with pipe", "- three[2Jnewline"] and out[7] == "---"
    out = plugin(tmp_path, status(queue_total=4))
    assert out[0] == "◐ needs you  watch 0  unlabelled 4" and out[2].startswith("ACE  unlabelled 4  chains ok")


def test_item_text_is_cut_and_never_carries_a_pipe_or_a_parameter(tmp_path):
    out = plugin(tmp_path, status(watch_open=1, watch_titles=["x" * 200, "evil | href=https://evil.test bash=/bin/sh", "\x1b\x07", 5, None]))
    assert out[4] == "- " + "x" * 80 and out[5] == "- evil  href=https://evil.test bash=/bin/sh"
    items = [ln for ln in out if ln.startswith("- ")]
    assert len(items) == 2 and not any("|" in ln for ln in out[:out.index("---", 2)])


def test_broken_chains_show_in_the_ace_line(tmp_path):
    s = status()
    s["measurement"]["chains"]["labels"] = "broken:3"
    assert "chains BROKEN" in plugin(tmp_path, s)[2]


def test_stale_when_generated_long_ago_or_missing_or_without_any_fresh_heartbeat(tmp_path):
    assert plugin(tmp_path, status(generated_at=stamp(40)))[0] == "○ stale"
    assert plugin(tmp_path, status(generated_at=stamp(40), watch_open=3))[0] == "○ stale"       # stale wins over needs you
    assert plugin(tmp_path, status(generated_at=None))[0] == "○ stale"
    (tmp_path / "empty").mkdir()
    out = plugin(tmp_path / "empty", None)                                                  # no status.json at all
    assert out[0] == "○ stale" and out[2] == "ACE  no status file"
    old = status(heartbeat={"last_hook_row": stamp(80), "last_offline_scan": stamp(90), "last_nightly_ok": None})
    assert plugin(tmp_path, old)[0] == "○ stale"
    fresh_nightly = status(heartbeat={"last_hook_row": None, "last_offline_scan": None, "last_nightly_ok": stamp(5)})
    assert plugin(tmp_path, fresh_nightly)[0] == "● ok  watch 0  unlabelled 0"


def test_no_status_fallback_for_a_file_that_cannot_be_read(tmp_path):
    for raw, err in (("{nope", "JSONDecodeError"), ("[1]", "ValueError"), ("", "JSONDecodeError")):
        assert plugin(tmp_path, raw=raw) == ["○ ACE: no status", "---", f"error: {err}"]


def test_odd_values_do_not_break_the_menu(tmp_path):
    out = plugin(tmp_path, status(watch_open="x", queue_total=-5, watch_leads=True, watch_titles="not a list", heartbeat=[1], measurement=None))
    assert out[0] == "○ stale" and out[1] == "---"                                           # no usable heartbeat: stale, not an error
    assert plugin(tmp_path, status(watch_titles={"a": 1}, watch_oldest=12))[0] == "● ok  watch 0  unlabelled 0"


def test_python_that_cannot_run_still_prints_the_fallback_and_exits_zero(tmp_path):
    assert plugin(tmp_path, status(), ACE_PYTHON="/nonexistent/python") == ["○ ACE: no status", "---", "error: python3 unavailable"]


def test_review_queue_button_uses_the_repo_of_the_plugin_and_env_overrides(tmp_path):
    link_dir = tmp_path / "plugins"
    link_dir.mkdir()
    link = link_dir / "ace.5m.sh"
    link.symlink_to(SCRIPT)                                                                 # SwiftBar runs a symlink in its plugin folder
    out = plugin(tmp_path, status(), script=link)
    button = out[-1]
    assert button.startswith("Review queue | bash=/usr/bin/env param1=uv param2=run param3=--directory param4=")
    assert f"param4={ROOT} param5=python param6=-m param7=ace_playbook.compile param8=--config param9={tmp_path}/config.json " in button
    assert button.endswith(" param10=watch param11=queue terminal=true")
    spaced = plugin(tmp_path, status(), conf=False, ACE_PLAYBOOK_REPO="/opt/my repo")[-1]
    assert 'param4="/opt/my repo" param5=python param6=-m param7=ace_playbook.compile param8=watch param9=queue terminal=true' in spaced


def test_the_default_config_file_under_home_is_found(tmp_path):
    home = tmp_path / "home" / ".config" / "ace-playbook"
    home.mkdir(parents=True)
    data = tmp_path / "alt data"
    data.mkdir()
    (home / "config.json").write_text(json.dumps({"data_dir": str(data)}))
    (data / "status.json").write_text(json.dumps(status(watch_open=1)))
    p = subprocess.run([str(SCRIPT)], capture_output=True, text=True, timeout=20,
                       env={"HOME": str(tmp_path / "home"), "PATH": os.environ["PATH"], "ACE_PYTHON": sys.executable})
    assert p.stdout.splitlines()[0] == "◐ needs you  watch 1  unlabelled 0" and "alt%20data/dashboard.html" in p.stdout


def test_the_script_is_executable_syntax_clean_and_within_its_budget():
    assert os.access(SCRIPT, os.X_OK) and len(SCRIPT.read_text().splitlines()) <= 80
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert SCRIPT.name == "ace.5m.sh" and SCRIPT.read_text().startswith("#!/bin/bash\n")
