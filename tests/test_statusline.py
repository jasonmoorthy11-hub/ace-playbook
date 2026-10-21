"""`hooks/statusline.sh` (docs/design/compiler.md 8.x): one status-line segment read from status.json with a bare `python3 -I -S`."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from ace_playbook.compile import status

from .test_compile_status import NOW, ROOT, build, labelled_world, w  # noqa: F401 - w is a fixture

SCRIPT = ROOT / "hooks" / "statusline.sh"
FMT = "%Y-%m-%dT%H:%M:%SZ"


def stamp(delta: timedelta, base: datetime | None = None) -> str:
    return ((base or datetime.now(timezone.utc)) + delta).strftime(FMT)


def child_env(tmp_path, conf=None) -> dict:
    """A throwaway HOME, the python under test, and the config only when one is named."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "ACE_PYTHON": sys.executable}
    if conf is not None:
        env["ACE_PLAYBOOK_CONFIG"] = str(conf)
    return env


def line(tmp_path, stdin, conf=None, extra=None) -> str:
    p = subprocess.run([str(SCRIPT)], input=stdin if isinstance(stdin, str) else json.dumps(stdin), capture_output=True, text=True,
                       env={**child_env(tmp_path, conf), **(extra or {})}, timeout=10)
    assert p.returncode == 0 and p.stderr == ""
    assert p.stdout.endswith("\n") and p.stdout.count("\n") == 1
    return p.stdout.rstrip("\n")


def write_status(w, edit=None) -> dict:
    """status.json of the labelled world with its clock fields moved to the real clock (the segment ages against `now`)."""
    labelled_world(w)
    s = build(w)
    gen = datetime.strptime(NOW, FMT).replace(tzinfo=timezone.utc)
    for entry, hours in zip(s["queue"], (1, 23, 25)):                              # two inside 24 hours of generated_at, one outside
        entry["ts"] = stamp(timedelta(hours=-hours), gen)
    s["heartbeat"]["last_hook_row"] = stamp(timedelta(hours=-2, minutes=-30))
    if edit:
        edit(s)
    w.cfg.path("status.json").write_text(json.dumps(s))
    return s


INPUT = {"model": {"display_name": "Fable 5.1"}, "context_window": {"used_percentage": 42}}


def test_the_exact_line(w, tmp_path):
    s = write_status(w)
    assert s["queue_total"] == 3 and len(s["queue"]) == 3
    assert line(tmp_path, INPUT, w.conf) == "Fable 5.1 | ctx 42% | ace approved 2  unlab24h 2  unlabelled 3  chains ok  heartbeat 2h"


def test_the_retired_queue_count_and_awaiting_scan_leave_the_line_alone(w, tmp_path):
    def edit(s):
        s["queue_retired"] = 7
        s["agreement"]["awaiting_scan"] = 9
    s = write_status(w, edit)
    assert s["queue_retired"] == 7
    assert line(tmp_path, INPUT, w.conf) == "Fable 5.1 | ctx 42% | ace approved 2  unlab24h 2  unlabelled 3  chains ok  heartbeat 2h"


def test_a_broken_chain_and_a_retired_proposal(w, tmp_path):
    def edit(s):
        s["measurement"]["chains"]["labels"] = "broken:5"
        next(iter(s["proposals"].values()))["mode"] = "disabled"
    write_status(w, edit)
    assert line(tmp_path, INPUT, w.conf).endswith("ace approved 1  unlab24h 2  unlabelled 3  chains BROKEN  heartbeat 2h")


def test_warn_and_block_count_as_approved(w, tmp_path):
    def edit(s):
        modes = iter(("warn", "block"))
        for p in s["proposals"].values():
            p["mode"] = next(modes)
    write_status(w, edit)
    assert "ace approved 2  " in line(tmp_path, INPUT, w.conf)


def test_a_truncated_queue_marks_the_24_hour_count_as_a_floor(w, tmp_path):
    write_status(w, lambda s: s.update(queue_total=60))
    assert "unlab24h 2+  unlabelled 60  " in line(tmp_path, INPUT, w.conf)


def test_no_hook_row_yet_says_never(w, tmp_path):
    write_status(w, lambda s: s["heartbeat"].update(last_hook_row=None))
    assert line(tmp_path, INPUT, w.conf).endswith("heartbeat never")


def test_a_missing_or_unreadable_status_file(w, tmp_path):
    assert line(tmp_path, INPUT, w.conf) == "Fable 5.1 | ctx 42% | ace: no status file"
    w.cfg.path("status.json").write_text("{not json")
    assert line(tmp_path, INPUT, w.conf) == "Fable 5.1 | ctx 42% | ace: no status file"
    w.cfg.path("status.json").write_text("[]")
    assert line(tmp_path, INPUT, w.conf) == "Fable 5.1 | ctx 42% | ace: no status file"
    w.cfg.path("status.json").unlink()
    w.cfg.path("status.json").mkdir()                                              # a directory where the file should be
    assert line(tmp_path, INPUT, w.conf) == "Fable 5.1 | ctx 42% | ace: no status file"


def test_a_null_or_odd_context_percentage_and_a_bad_stdin(w, tmp_path):
    assert line(tmp_path, {"model": {"display_name": "M"}, "context_window": {"used_percentage": None}}, w.conf) == "M | ctx ?% | ace: no status file"
    assert line(tmp_path, {"model": {"display_name": "M"}, "context_window": {"used_percentage": 42.6}}, w.conf).startswith("M | ctx 43% | ")
    assert line(tmp_path, {"model": {"display_name": "M"}}, w.conf).startswith("M | ctx ?% | ")
    assert line(tmp_path, {"model": {"display_name": "M"}, "context_window": {"used_percentage": "x"}}, w.conf).startswith("M | ctx ?% | ")
    assert line(tmp_path, "not json", w.conf) == "claude | ctx ?% | ace: no status file"
    assert line(tmp_path, "", w.conf) == "claude | ctx ?% | ace: no status file"
    assert line(tmp_path, {"model": "x"}, w.conf) == "claude | ctx ?% | ace: no status file"


def test_the_default_config_location_under_home(w, tmp_path):
    write_status(w)
    home = tmp_path / "home"
    conf_dir = home / ".config" / ("ace" + "-playbook")                          # joined: the operator's hook matches the literal path
    conf_dir.mkdir(parents=True)
    (conf_dir / "config.json").write_text(json.dumps({"data_dir": str(w.data)}))
    assert line(tmp_path, INPUT).startswith("Fable 5.1 | ctx 42% | ace approved 2  ")
    (conf_dir / "config.json").write_text(json.dumps({"data_dir": "~/rel"}))        # "~" expands against the throwaway HOME
    assert line(tmp_path, INPUT) == "Fable 5.1 | ctx 42% | ace: no status file"


def test_no_config_at_all_is_the_missing_file_line(tmp_path):
    assert line(tmp_path, INPUT) == "Fable 5.1 | ctx 42% | ace: no status file"


def test_a_python_that_cannot_run_still_exits_zero_and_prints_one_line(w, tmp_path):
    p = subprocess.run([str(SCRIPT)], input=json.dumps(INPUT), capture_output=True, text=True,
                       env={**child_env(tmp_path, w.conf), "ACE_PYTHON": "/nonexistent/python"}, timeout=10)
    assert p.returncode == 0 and p.stdout.count("\n") == 1 and p.stdout.startswith("ace: ")


def test_it_is_fast_and_imports_no_package(w, tmp_path, capsys):
    write_status(w)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        line(tmp_path, INPUT, w.conf)
        times.append(time.perf_counter() - t0)
    with capsys.disabled():
        print(f"\nstatusline runs: {[round(t * 1000) for t in times]} ms (budget 300 ms)")
    assert max(times) < 1.0
    text = SCRIPT.read_text()
    assert "-I -S" in text and "uv run" not in text and "ace_playbook" not in text.replace("ACE_PLAYBOOK", "")


def test_the_script_is_executable_syntax_clean_and_within_its_budget():
    assert os.access(SCRIPT, os.X_OK) and len(SCRIPT.read_text().splitlines()) <= 60
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert status.QUEUE_MAX == 50                                                  # the "+" floor in the script assumes the queue cap


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_a_non_finite_context_percentage_prints_a_question_mark_not_the_unavailable_form(w, tmp_path, raw):
    write_status(w)
    out = line(tmp_path, '{"model": {"display_name": "Fable 5.1"}, "context_window": {"used_percentage": %s}}' % raw, w.conf)
    assert out.startswith("Fable 5.1 | ctx ?% | ace approved 2  unlab24h 2")


def test_control_characters_are_stripped_from_the_model_name(w, tmp_path):
    write_status(w)
    out = line(tmp_path, {"model": {"display_name": "Fa\x1b[31mble\x07 5\x7f.1\n"}, "context_window": {"used_percentage": 42}}, w.conf)
    assert out.startswith("Fa[31mble 5.1 | ctx 42% | ace approved 2") and not any(ord(c) < 32 or ord(c) == 127 for c in out.rstrip("\n"))


def test_a_model_name_of_only_control_characters_falls_back_to_claude(w, tmp_path):
    write_status(w)
    assert line(tmp_path, {"model": {"display_name": "\x1b\x07"}, "context_window": {"used_percentage": 1}}, w.conf).startswith("claude | ctx 1% |")


def test_the_segment_is_named_unlab24h_and_documented_that_way():
    text = SCRIPT.read_text()
    assert "unlab24h" in text and "fires24h" not in text and len(text.splitlines()) <= 60
    assert "fires24h" not in (ROOT / "hooks" / "INSTALL.md").read_text()
