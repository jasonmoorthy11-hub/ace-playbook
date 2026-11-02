"""The connectors check of the brief (ace_playbook/compile/brief_conn.py) and its use in hooks/brief.sh: an init whose read connectors were not
`connected` (pending, or absent from the list), or a stage 1 with no connector call, is never an `ok` brief from an empty digest."""

import json
import subprocess
import sys
import time

import pytest

from ace_playbook.compile import brief_audit as ba, brief_conn as bc

from . import brief_support as bs
from .test_brief_sh import FAKE, ROOT, World, free, raw_left, start, wait_for  # noqa: F401  (free: the lock is released after a refusal)

@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


ALLOW = ROOT / "hooks" / "brief_allowed_tools.txt"
SLUGS = ["claude_ai_Gmail", "claude_ai_Google_Calendar", "claude_ai_Linear"]
NAMES = {"claude_ai_Gmail": "claude.ai Gmail", "claude_ai_Google_Calendar": "claude.ai Google Calendar", "claude_ai_Linear": "claude.ai Linear"}
MARKER_NAME = "UNIQUE-SERVER-NAME-MARKER"
PLAYWRIGHT = {"name": "plugin:playwright:playwright", "status": "connected", "source": "plugin"}
REFUSED = "brief: connectors pending, run refused"


def srv(slug, status="connected"):
    return {"name": NAMES[slug], "status": status, "source": "claude.ai"}


ALL_UP = [srv(s) for s in SLUGS]


def call(name="mcp__claude_ai_Gmail__search_threads"):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": name, "input": {}}]}}


def write(tmp_path, init_extra, calls=(call(),), junk=False, init=True, name="stream.jsonl"):
    evs = ([{"type": "system", "subtype": "init", "tools": [], **init_extra}] if init else []) + list(calls)
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in evs) + ("\nnot json {\n" if junk else "\n")
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def result(tmp_path, servers, calls=(call(),), **kw):
    return bc.check(write(tmp_path, {"mcp_servers": servers}, calls, **kw), ALLOW)


# ------------------------------------------------------------------ units
def test_the_required_slugs_are_derived_from_the_checked_in_allowlist():
    assert bc.required_slugs(ALLOW) == set(SLUGS)


def test_required_slugs_ignores_toolsearch_and_non_connector_names(tmp_path):
    f = tmp_path / "allow.txt"
    f.write_text("# c\nToolSearch\nmcp__a_b__read\nmcp__a_b__other\nmcp__c__x\nRead\n")
    assert bc.required_slugs(f) == {"a_b", "c"}


def test_slug_replaces_every_character_outside_letters_and_digits():
    assert bc.slug("claude.ai Gmail") == "claude_ai_Gmail"
    assert bc.slug("plugin:playwright:playwright") == "plugin_playwright_playwright"
    assert bc.slug("a-b.c d eé") == "a_b_c_d_e_"


def test_all_connected_and_one_connector_call_is_ready(tmp_path):
    assert result(tmp_path, ALL_UP) == {"servers": 3, "pending": [], "missing": [], "calls": 1, "malformed": 0, "ready": True}


def test_one_pending_connector_is_not_ready_and_is_named_by_slug(tmp_path):
    r = result(tmp_path, [srv("claude_ai_Gmail", "pending"), srv("claude_ai_Google_Calendar"), srv("claude_ai_Linear")])
    assert r["pending"] == ["claude_ai_Gmail"] and r["missing"] == [] and r["ready"] is False and r["calls"] == 1


def test_a_failed_or_odd_status_is_not_connected(tmp_path):
    for status in ("failed", "needs-auth", "Connected", "connected ", ""):
        r = result(tmp_path, [srv("claude_ai_Gmail", status), srv("claude_ai_Google_Calendar"), srv("claude_ai_Linear")])
        assert (r["pending"], r["ready"]) == (["claude_ai_Gmail"], False), status


def test_one_absent_connector_is_missing(tmp_path):
    r = result(tmp_path, [srv("claude_ai_Gmail"), srv("claude_ai_Linear")])
    assert (r["pending"], r["missing"], r["ready"], r["servers"]) == ([], ["claude_ai_Google_Calendar"], False, 2)


def test_the_real_shaped_playwright_only_init_has_three_missing(tmp_path):
    r = result(tmp_path, [PLAYWRIGHT])
    assert r == {"servers": 1, "pending": [], "missing": SLUGS, "calls": 1, "malformed": 0, "ready": False}


def test_all_connected_with_zero_connector_calls_is_not_ready(tmp_path):
    r = result(tmp_path, ALL_UP, calls=())
    assert (r["calls"], r["pending"], r["missing"], r["ready"]) == (0, [], [], False)


def test_a_non_connector_call_does_not_count(tmp_path):
    for name in ("mcp__plugin_x__y", "ToolSearch", "mcp__claude_ai_Gmail", "mcp__claude_ai_GmailX__read", "mcp__claude_ai_Slack__read", "Bash"):
        r = result(tmp_path, ALL_UP, calls=(call(name),))
        assert (r["calls"], r["ready"]) == (0, False), name


def test_a_name_that_is_only_a_server_prefix_is_not_a_call(tmp_path):
    r = result(tmp_path, ALL_UP, calls=(call("mcp__claude_ai_Gmail__"),))
    assert (r["calls"], r["ready"]) == (0, False)


def test_calls_counts_blocks_of_every_tool_use_kind_and_ignores_a_nonstring_name(tmp_path):
    blocks = [{"type": "mcp_tool_use", "name": "mcp__claude_ai_Linear__list_issues"}, {"type": "server_tool_use", "name": ["mcp__claude_ai_Linear__x"]},
              {"type": "tool_use", "name": 7}, {"type": "text", "text": "mcp__claude_ai_Gmail__x"}]
    r = result(tmp_path, ALL_UP, calls=({"type": "assistant", "message": {"content": blocks}},))
    assert (r["calls"], r["ready"]) == (1, True)


FUZZ = {
    "null": (None, 0, 3, 0), "dict": ({"name": "claude.ai Gmail", "status": "connected"}, 0, 3, 0), "string": ("claude.ai Gmail", 0, 3, 0),
    "int": (7, 0, 3, 0), "empty": ([], 0, 3, 0),
    "list of junk": ([1, "s", None, 2.5, [], [ALL_UP[0]], True], 7, 3, 7),
    "no name": ([{"status": "connected"}], 1, 3, 1), "no status": ([{"name": "claude.ai Gmail"}], 1, 3, 1),
    "status not str": ([{"name": "claude.ai Gmail", "status": True}, {"name": "claude.ai Linear", "status": ["connected"]}], 2, 3, 2),
    "name is a list": ([{"name": ["claude.ai", "Gmail"], "status": "connected"}], 1, 3, 1),
    "name is null": ([{"name": None, "status": "connected"}], 1, 3, 1),
}


@pytest.mark.parametrize("key", sorted(FUZZ))
def test_fuzz_a_malformed_mcp_servers_value_never_raises_and_is_never_ready(tmp_path, key):
    value, size, missing, malformed = FUZZ[key]
    r = result(tmp_path, value)
    assert (r["servers"], len(r["missing"]) + len(r["pending"]), r["malformed"], r["ready"]) == (size, missing, malformed, False)


def test_fuzz_an_init_without_mcp_servers_is_checked_and_not_ready(tmp_path):
    r = bc.check(write(tmp_path, {}), ALLOW)
    assert (r["servers"], r["missing"], r["ready"]) == (0, SLUGS, False)


def test_fuzz_malformed_entries_next_to_good_ones_are_counted_and_the_good_ones_still_count(tmp_path):
    r = result(tmp_path, [*ALL_UP, 5, {"name": 1, "status": "connected"}, "x"])
    assert (r["servers"], r["malformed"], r["ready"]) == (6, 3, True)


def test_fuzz_a_duplicate_with_one_connected_entry_is_ready(tmp_path):
    assert result(tmp_path, [srv("claude_ai_Gmail", "pending"), *ALL_UP])["ready"] is True
    r = result(tmp_path, [srv("claude_ai_Gmail", "pending"), srv("claude_ai_Gmail", "failed"), srv("claude_ai_Google_Calendar"), srv("claude_ai_Linear")])
    assert (r["pending"], r["ready"]) == (["claude_ai_Gmail"], False)


def test_fuzz_two_spellings_of_one_slug_are_the_same_server(tmp_path):
    other = {"name": "claude_ai_Gmail", "status": "connected", "source": "x"}
    assert result(tmp_path, [srv("claude_ai_Gmail", "pending"), other, srv("claude_ai_Google_Calendar"), srv("claude_ai_Linear")])["ready"] is True


def test_fuzz_extra_unknown_servers_are_counted_in_servers_and_ignored(tmp_path):
    r = result(tmp_path, [*ALL_UP, PLAYWRIGHT, {"name": "other", "status": "pending", "source": "x"}])
    assert (r["servers"], r["pending"], r["missing"], r["ready"]) == (5, [], [], True)


def test_fuzz_a_ten_thousand_entry_list(tmp_path):
    junk = [{"name": f"srv {i}", "status": "pending", "source": "x"} for i in range(10_000)]
    r = result(tmp_path, junk + ALL_UP)
    assert (r["servers"], r["ready"]) == (10_003, True)
    r = result(tmp_path, junk)
    assert (r["servers"], r["missing"], r["ready"]) == (10_000, SLUGS, False)


def test_fuzz_a_hundred_thousand_character_name(tmp_path):
    r = result(tmp_path, [{"name": "claude.ai Gmail" + "x" * 100_000, "status": "connected", "source": "x"}, *ALL_UP[1:]])
    assert (r["missing"], r["ready"]) == (["claude_ai_Gmail"], False)
    r = result(tmp_path, [{"name": "x" * 100_000, "status": "connected" * 10_000, "source": "x"}, *ALL_UP])
    assert r["ready"] is True


def test_fuzz_u2028_inside_a_name_does_not_split_the_line(tmp_path):
    path = write(tmp_path, {"mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"}, *ALL_UP[1:]]})
    assert " " in path.read_text(encoding="utf-8")
    r = bc.check(path, ALLOW)
    assert (r["servers"], r["malformed"], r["missing"], r["ready"]) == (3, 0, [], True)


def test_fuzz_junk_lines_are_tolerated_like_events_does(tmp_path):
    path = write(tmp_path, {"mcp_servers": ALL_UP}, junk=True)
    assert path.read_text().endswith("not json {\n") and bc.check(path, ALLOW)["ready"] is True


def test_the_first_init_event_decides(tmp_path):
    second = {"type": "system", "subtype": "init", "mcp_servers": ALL_UP}
    assert bc.check(write(tmp_path, {"mcp_servers": [PLAYWRIGHT]}, calls=(second, call())), ALLOW)["ready"] is False
    first = {"type": "system", "subtype": "init", "mcp_servers": ALL_UP}
    assert bc.check(write(tmp_path, {"mcp_servers": [PLAYWRIGHT]}, calls=(call(), first), name="b.jsonl"), ALLOW)["ready"] is False


def test_no_init_event_is_a_value_error_and_so_is_an_empty_or_non_json_stream(tmp_path):
    with pytest.raises(ValueError):
        bc.check(write(tmp_path, {}, init=False), ALLOW)
    for text in ("", "\n\n", "not json\n", "[1, 2]\n", '{"type": "system", "subtype": "result"}\n'):
        (tmp_path / "s.jsonl").write_text(text)
        with pytest.raises(ValueError):
            bc.check(tmp_path / "s.jsonl", ALLOW)


def test_a_stream_over_the_cap_is_a_value_error_not_a_cut_read(tmp_path, monkeypatch):
    path = write(tmp_path, {"mcp_servers": ALL_UP})
    assert bc.check(path, ALLOW)["ready"] is True
    monkeypatch.setattr(ba, "CAP", path.stat().st_size - 1)
    with pytest.raises(ValueError):
        bc.check(path, ALLOW)
    monkeypatch.setattr(ba, "CAP", path.stat().st_size)
    assert bc.check(path, ALLOW)["ready"] is True


def test_no_server_name_from_the_stream_is_in_the_result(tmp_path, capsys):
    servers = [{"name": MARKER_NAME, "status": "pending", "source": MARKER_NAME}, {"name": MARKER_NAME, "status": "connected"},
               {"name": ["x", MARKER_NAME], "status": "connected"}, {"name": MARKER_NAME + " Gmail", "status": MARKER_NAME}, PLAYWRIGHT, *ALL_UP[1:]]
    path = write(tmp_path, {"mcp_servers": servers}, calls=(call(MARKER_NAME), call("mcp__" + MARKER_NAME + "__x")))
    r = bc.check(path, ALLOW)
    assert MARKER_NAME not in json.dumps(r) and r["missing"] == ["claude_ai_Gmail"]
    assert bc.main([str(path), "--allow", str(ALLOW)]) == 2
    out = capsys.readouterr()
    assert MARKER_NAME not in out.out + out.err and "plugin" not in out.out + out.err


# ------------------------------------------------------------------ the command line
def test_main_exits_0_when_ready_with_one_json_line(tmp_path, capsys):
    path = write(tmp_path, {"mcp_servers": ALL_UP})
    assert bc.main([str(path), "--allow", str(ALLOW)]) == 0
    out = capsys.readouterr()
    assert out.err == "" and out.out.count("\n") == 1 and json.loads(out.out)["ready"] is True


def test_main_exits_2_when_not_ready_with_the_json_line(tmp_path, capsys):
    path = write(tmp_path, {"mcp_servers": [srv("claude_ai_Gmail", "pending"), *ALL_UP[1:]]})
    assert bc.main([str(path), "--allow", str(ALLOW)]) == 2
    out = capsys.readouterr()
    assert out.err == "" and json.loads(out.out) == {"servers": 3, "pending": ["claude_ai_Gmail"], "missing": [], "calls": 1, "malformed": 0, "ready": False}


def test_main_exits_1_with_one_stderr_line_on_a_value_or_os_error(tmp_path, capsys):
    bad = write(tmp_path, {}, init=False)
    assert bc.main([str(bad), "--allow", str(ALLOW)]) == 1
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "brief: ValueError\n")
    assert bc.main([str(tmp_path / "nope.jsonl"), "--allow", str(ALLOW)]) == 1
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "brief: FileNotFoundError\n") and str(tmp_path) not in out.err
    assert bc.main([str(bad), "--allow", str(tmp_path / "nope.txt")]) == 1
    assert capsys.readouterr().err == "brief: FileNotFoundError\n"
    (tmp_path / "allow.txt").write_text("mcp__a__b # note\n")
    assert bc.main([str(bad), "--allow", str(tmp_path / "allow.txt")]) == 1
    assert capsys.readouterr().err == "brief: ValueError\n"


def test_main_usage_errors_are_one_line_exit_2(capsys):
    with pytest.raises(SystemExit) as e:
        bc.main([])
    assert e.value.code == 2 and capsys.readouterr().err.startswith("brief: usage: ")


def test_python_dash_m_runs_the_module(tmp_path):
    path = write(tmp_path, {"mcp_servers": [PLAYWRIGHT]})
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.brief_conn", str(path), "--allow", str(ALLOW)], capture_output=True, text=True,
                       cwd=ROOT, timeout=60)
    assert p.returncode == 2 and json.loads(p.stdout)["missing"] == SLUGS and p.stderr == ""


# ------------------------------------------------------------------ hooks/brief.sh end to end
def pending(*slugs):
    return json.dumps([srv(s, "pending") if s in slugs else srv(s) for s in SLUGS])


BOTH = {"ACE_BRIEF_RETRY_SLEEP": "0"}
PENDING_ALL = {**BOTH, "ACE_FAKE_MCP": pending("claude_ai_Gmail", "claude_ai_Google_Calendar")}


def calls_of(w):
    return int((w.fake / "stage1_calls").read_text())


def status_line(w):
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.brief", "status"], capture_output=True, text=True, cwd=ROOT, timeout=60,
                       env={"ACE_PLAYBOOK_CONFIG": str(w.conf), "PATH": w.env["PATH"], "HOME": w.env["HOME"]})
    return next(ln for ln in p.stdout.splitlines() if ln.startswith("last refusal:"))


def assert_refused(w, p):
    assert p.returncode == 2 and "brief: ok" not in p.stdout and p.stdout == ""
    first, rest = p.stderr.splitlines()[0], p.stderr.splitlines()[1:]
    assert first == REFUSED and len(rest) == 1 and json.loads(rest[0])["ready"] is False
    assert (w.tmp / "notified").read_text().splitlines() == ["-title", "ACE brief", "-message", REFUSED]
    rows = w.ledger()
    assert [r["stage"] for r in rows] == ["stage1", "stage1", "run"] and rows[-1]["refused"] == "connectors pending"
    assert set(rows[-1]) == {"ts", "stage", "refused"}
    assert calls_of(w) == 2 and not (w.fake / "argv_stage2.json").exists() and not (w.dir / "run" / "digest_clean.json").exists()
    assert raw_left(w) == [] and not (w.dir / "run" / "stage1.jsonl").exists() and not (w.dir / "brief.html").exists() and free(w)
    assert status_line(w) == "last refusal: connectors pending 0m"


def test_pending_on_both_attempts_refuses_after_one_retry(w):
    assert_refused(w, w.run("--force", **PENDING_ALL))


def test_zero_connector_calls_on_both_attempts_refuses_like_pending(w):
    assert_refused(w, w.run("--force", **BOTH, ACE_FAKE_NO_CALLS="1"))


def test_a_connector_missing_from_the_list_on_both_attempts_refuses(w):
    assert_refused(w, w.run("--force", **BOTH, ACE_FAKE_MCP=json.dumps([PLAYWRIGHT])))


def test_the_refusal_prints_only_slugs_never_a_server_name_from_the_stream(w):
    servers = json.dumps([{"name": MARKER_NAME, "status": "pending", "source": "x"}, PLAYWRIGHT])
    p = w.run("--force", **BOTH, ACE_FAKE_MCP=servers)
    assert p.returncode == 2 and MARKER_NAME not in p.stdout + p.stderr + (w.tmp / "notified").read_text() + (w.dir / "brief_ledger.jsonl").read_text()
    assert "playwright" not in p.stderr


def test_pending_then_connected_retries_once_and_the_brief_is_ok(w):
    p = w.run("--force", **BOTH, ACE_FAKE_MCP_FIRST=pending("claude_ai_Gmail"))
    assert (p.returncode, p.stdout, p.stderr) == (0, "digest: calendar 1, mail 2 of 4, issues 1\nbrief: ok, 1 need you\n", "")
    assert calls_of(w) == 2 and [r["stage"] for r in w.ledger()] == ["stage1", "stage1", "stage2"] and (w.dir / "brief.html").exists()


def test_the_default_world_makes_one_attempt_and_the_exact_stdout(w):
    p = w.run("--force")
    assert (p.returncode, p.stdout, p.stderr) == (0, "digest: calendar 1, mail 2 of 4, issues 1\nbrief: ok, 1 need you\n", "")
    assert calls_of(w) == 1 and [r["stage"] for r in w.ledger()] == ["stage1", "stage2"]


def test_zero_calls_then_calls_is_also_one_retry(w):
    p = w.run("--force", **BOTH, ACE_FAKE_NO_CALLS_FIRST="1")
    assert p.returncode == 0 and calls_of(w) == 2 and [r["stage"] for r in w.ledger()] == ["stage1", "stage1", "stage2"]


def test_tool_drift_still_wins_over_pending_servers_and_is_not_retried(w):
    p = w.run("--force", **PENDING_ALL, ACE_FAKE_INIT_TOOLS="ToolSearch,Bash")
    assert p.returncode == 2 and p.stderr.splitlines()[0] == "brief: tool drift, run refused" and REFUSED not in p.stderr
    assert calls_of(w) == 1 and not (w.fake / "argv_stage2.json").exists() and raw_left(w) == []
    assert (w.tmp / "notified").read_text().splitlines()[-1] == "brief: tool drift, run refused"
    assert [r["stage"] for r in w.ledger()] == ["stage1", "stage1"] and "drift" in w.ledger()[1]


def test_a_failing_retry_takes_the_same_path_as_a_failing_first_attempt(w):
    p = w.run("--force", **PENDING_ALL, ACE_FAKE_FAIL_SECOND="stage1")
    assert p.returncode == 1 and p.stderr.strip().splitlines()[-1] == "brief: stage 1 failed (exit 1)" and calls_of(w) == 2
    assert [r["stage"] for r in w.ledger()] == ["stage1", "stage1", "run"] and w.ledger()[-1]["refused"] == "stage 1 failed" and raw_left(w) == []


def test_a_timed_out_retry_takes_the_same_path_as_a_timed_out_first_attempt(w):
    wrapper = w.tmp / "hang_on_retry"      # the first stage-1 call is the real fake; once its counter file exists the wrapper sleeps instead
    wrapper.write_text(f'#!/bin/bash\n[ -e "$ACE_FAKE_DIR/stage1_calls" ] && exec sleep 30\nexec {FAKE} "$@"\n')
    wrapper.chmod(0o755)
    began = time.monotonic()
    p = w.run("--force", **BOTH, ACE_BRIEF_CLAUDE=str(wrapper), ACE_FAKE_MCP_FIRST=pending("claude_ai_Gmail"), ACE_BRIEF_STAGE_TIMEOUT="2",
              ACE_BRIEF_KILL_GRACE="1")
    assert (p.returncode, p.stdout, p.stderr) == (1, "", "brief: stage 1 timed out\n") and time.monotonic() - began < 30
    assert calls_of(w) == 1 and not (w.fake / "argv_stage2.json").exists()      # the first attempt only: the wrapper never reached the fake again
    assert [(r.get("stage"), r.get("timeout")) for r in w.ledger()] == [("stage1", None), ("stage1", "stage1")]
    assert raw_left(w) == [] and sorted(c.name for c in (w.dir / "run").iterdir()) == ["cwd"] and free(w)


def test_a_check_that_cannot_run_is_connectors_check_failed_and_not_a_pass(w):
    shim = w.tmp / "py-shim"
    shim.write_text(f'#!/bin/bash\n[[ "$*" == *brief_conn* ]] && exit 1\nexec uv run --quiet --project {ROOT} python "$@"\n')
    shim.chmod(0o755)
    p = w.run("--force", ACE_BRIEF_PY=str(shim))
    assert p.returncode == 1 and p.stderr.strip().splitlines()[-1] == "brief: connectors check failed" and p.stdout == ""
    assert w.ledger()[-1]["refused"] == "connectors check failed" and not (w.fake / "argv_stage2.json").exists() and calls_of(w) == 1


def test_the_retry_sleeps_the_configured_seconds_and_a_signal_during_it_stops_the_run_and_purges(w):
    proc = start(w, ACE_FAKE_MCP_FIRST=pending("claude_ai_Gmail"), ACE_BRIEF_RETRY_SLEEP="30")
    wait_for(w.dir / "run" / "res_conn.json")          # the first attempt is over (its check wrote this), the run is in the sleep
    time.sleep(0.5)
    assert proc.poll() is None and not free(w) and calls_of(w) == 1
    proc.terminate()
    proc.communicate(timeout=30)
    assert proc.returncode == 143 and raw_left(w) == [] and free(w) and calls_of(w) == 1


def test_a_short_sleep_value_is_honoured(w):
    t0 = time.time()
    p = w.run("--force", ACE_FAKE_MCP_FIRST=pending("claude_ai_Gmail"), ACE_BRIEF_RETRY_SLEEP="2")
    assert p.returncode == 0 and time.time() - t0 >= 2


@pytest.mark.parametrize("value, want", [("abc", "15"), ("", "15"), ("-1", "15"), ("1.5", "15"), ("1234567", "15"), (" 3", "15"), ("0", "0"),
                                          ("007", "7"), ("12", "12"), ("999999", "999999")])
def test_retry_sleep_is_validated_like_the_other_limits(value, want):
    p = subprocess.run(["/bin/bash", "-c", 'source "$1/hooks/brief_lib.sh"; limits; echo "$RETRY_S"', "x", str(ROOT)], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "ACE_BRIEF_RETRY_SLEEP": value}, timeout=30)
    assert p.returncode == 0 and p.stdout.strip() == want


def test_retry_sleep_defaults_to_fifteen_when_unset():
    p = subprocess.run(["/bin/bash", "-c", 'source "$1/hooks/brief_lib.sh"; limits; echo "$RETRY_S"', "x", str(ROOT)], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin"}, timeout=30)
    assert p.stdout.strip() == "15"


def test_the_reasons_and_the_fake_are_in_step():
    assert "connectors pending" in ba.REASONS and "connectors check failed" in ba.REASONS
    assert ba.refusal_reason("connectors pending") == "connectors pending" and FAKE.exists()
