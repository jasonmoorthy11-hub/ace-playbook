"""`compile/brief.py`: schemas, the exclusion list applied by code, the dedupe clock, the stream readers, the ledger and the state file."""

import json
import os
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest

from ace_playbook.compile import brief, brief_audit as ba, brief_excl, brief_render

from . import brief_support as bs
from .test_statusline import ROOT


@pytest.fixture
def env(tmp_path, monkeypatch):
    conf = bs.config(tmp_path)
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(conf))
    return tmp_path


def run(capsys, *argv):
    code = brief.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def put(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return str(path)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ------------------------------------------------------------------ schemas
def test_a_valid_digest_and_brief_pass_and_unknown_keys_are_dropped():
    assert brief_render.validate_digest(bs.digest(extra="x"))["mail"][0]["thread_id"] == "t1"
    assert "extra" not in brief_render.validate_digest(bs.digest(extra="x"))
    out = brief_render.validate_brief(bs.brief())
    assert [a["load"] for a in out["acts"]] == ["normal"] * 3                  # an act with no load takes the day class


def test_strings_are_capped_and_stripped_of_control_characters():
    d = bs.digest()
    d["mail"][0]["summary"] = "a\x00b\x1b[31mc‮d\nline\ttab" + "z" * 400
    d["mail"][0]["subject"] = "​hidden\u0085"
    got = brief_render.validate_digest(d)["mail"][0]
    assert len(got["summary"]) == 300 and got["summary"].startswith("ab[31mcd line tab") and "\x00" not in got["summary"]
    assert got["subject"] == "hidden"
    b = brief_render.validate_brief(bs.brief(headline="h" * 500))
    assert len(b["headline"]) == 140


def test_every_url_becomes_null_in_the_brief_because_the_digest_has_no_url_field():
    urls = ("javascript:alert(1)", "http://x.example", "data:text/html,x", "HTTPS://x.example", "https://linear.app/a b", 7, "https://x.example/a",
            "https://linear.app/a?b=1", "https://linear.app?@x", "https://mail.google.com/?q=SECRET", "https://mail.google.com/mail/u/0/#inbox/abc")
    for url in urls:
        item = {**bs.brief()["needs_you"][0], "url": url}
        assert brief_render.validate_brief(bs.brief(needs_you=[item], resolved=[item]))["needs_you"][0]["url"] is None, url
        assert brief_render.validate_brief(bs.brief(needs_you=[item], resolved=[item]))["resolved"][0]["url"] is None, url


@pytest.mark.parametrize("bad", [
    lambda b: b.pop("headline"), lambda b: b.update(day_class="busy"), lambda b: b.update(acts=b["acts"][:2]), lambda b: b.update(headline=7),
    lambda b: b.update(needs_you="none"), lambda b: b["acts"][0].pop("range"), lambda b: b["needs_you"][0].pop("title"), lambda b: b.update(resolved=[1]),
])
def test_schema_failures_name_the_field_path_only(bad):
    b = bs.brief()
    bad(b)
    with pytest.raises(brief_render.SchemaError) as e:
        brief_render.validate_brief(b)
    assert str(e.value).startswith("brief") and "Pat" not in str(e.value)


def test_digest_schema_failures():
    for bad in ({"mail": [{"thread_id": "x"}]}, {"calendar": "x"}, {"issues": [{"id": 1, "title": "", "state": "", "due": None}]}):
        with pytest.raises(brief_render.SchemaError):
            brief_render.validate_digest({**bs.digest(), **bad})
    d = bs.digest()
    d["calendar"][0]["attendees_n"] = True
    with pytest.raises(brief_render.SchemaError, match=r"digest\.calendar\[0\]\.attendees_n"):
        brief_render.validate_digest(d)


# ------------------------------------------------------------------ digest-apply
def test_digest_apply_removes_excluded_threads_by_code_and_keeps_sender_and_subject_only_for_bodies(env, capsys):
    conf = bs.config(env, brief_exclusions=str(bs.exclusions(env / "ex.json")))
    os.environ["ACE_PLAYBOOK_CONFIG"] = str(conf)
    src = put(env, "digest.json", bs.digest())
    code, out, err = run(capsys, "digest-apply", src)
    assert code == 0 and err == "" and out.strip() == "digest: calendar 1, mail 2 of 4, issues 1"
    clean_path = env / "brief" / "digest_clean.json"
    clean = json.loads(clean_path.read_text())
    assert [m["thread_id"] for m in clean["mail"]] == ["t1", "t3"]
    assert clean["mail"][1]["summary"] == "" and clean["mail"][1]["subject"] == "Invoice 12" and "Billing" in clean["mail"][1]["sender"]
    assert "casey@" not in clean_path.read_text() and "HOLD-THIS" not in clean_path.read_text() and "account number" not in clean_path.read_text()
    assert mode(clean_path) == 0o600 and mode(env / "brief") == 0o700


def test_digest_apply_tolerates_a_fence_and_prose_and_writes_to_out(env, capsys):
    src = put(env, "d.json", "Here you go:\n```json\n" + json.dumps(bs.digest()) + "\n```\n")
    out_path = env / "elsewhere.json"
    assert run(capsys, "digest-apply", src, "--out", str(out_path))[0] == 0
    assert len(json.loads(out_path.read_text())["mail"]) == 4 and not (env / "brief" / "digest_clean.json").exists()


@pytest.mark.parametrize("body", ["", "no json at all", "[1,2]", '{"mail": []}', "{" * 5000])
def test_digest_apply_refuses_a_bad_digest_with_exit_2_and_writes_nothing(env, capsys, body):
    code, out, err = run(capsys, "digest-apply", put(env, "d.json", body))
    assert (code, out) == (2, "") and err.startswith("brief: schema:") and not (env / "brief" / "digest_clean.json").exists()


LONG = '{"drop_senders": ["' + "a" * 201 + '"]}'
BAD_EXCLUSIONS = ['{"drop_senders": "x"}', '{"drop_other": []}', '{"drop_subjects": [1]}', "[]", LONG, '{"drop_subjects": [""]}',
                  '{"drop_subjects": [{"ends": "x"}]}', '{"drop_subjects": [{"starts": "x", "contains": "y"}]}', '{"drop_subjects": [{"starts": 1}]}',
                  '{"drop_subjects": [{"starts": ""}]}', '{"drop_subjects": [{}]}', '{"drop_subjects": [null]}',
                  json.dumps({"drop_senders": ["x"] * 501})]


@pytest.mark.parametrize("ex", BAD_EXCLUSIONS)
def test_a_bad_exclusions_file_fails_closed(env, capsys, ex):
    os.environ["ACE_PLAYBOOK_CONFIG"] = str(bs.config(env, brief_exclusions=put(env, "ex.json", ex)))
    code, _, err = run(capsys, "digest-apply", put(env, "d.json", bs.digest()))
    assert code == 2 and "schema: exclusions" in err and not (env / "brief" / "digest_clean.json").exists()


# D-116: the exclusion entries are LITERAL substrings, so no pattern can backtrack; each of these hung a regex engine for more than 5 s
HOSTILE = ["(a|aa)+$", "((a+))+$", "((a)+)+$", "(a{1,})+$", "(a|a)*$", "(a?){30}a{30}", ".*.*.*.*x", "(a+)+$", "^(a*)*b", "(x+x+)+y", "(.*a){3}+",
           "(ab*)*", "(", "[a-", "\\"]
MISS = "a" * 40 + "!"


def mail(tid, sender="Pat <p@example.com>", subject="hello", summary="body text"):
    return {"thread_id": tid, "sender": sender, "subject": subject, "received": "2026-10-20T17:00:00Z", "asked_me": True, "replied": False,
            "summary": summary}


def kept(env, capsys, ex, mails):
    os.environ["ACE_PLAYBOOK_CONFIG"] = str(bs.config(env, brief_exclusions=put(env, "ex.json", ex)))
    start = time.monotonic()
    code, _, err = run(capsys, "digest-apply", put(env, "d.json", bs.digest(mail=mails)))
    assert (code, err) == (0, "") and time.monotonic() - start < 5
    return {m["thread_id"]: m for m in json.loads((env / "brief" / "digest_clean.json").read_text())["mail"]}


@pytest.mark.parametrize("pat", HOSTILE)
def test_a_regex_shaped_entry_is_a_literal_and_finishes_instantly(env, capsys, pat):
    ex = {"drop_senders": [pat], "drop_subjects": [pat]}
    out = kept(env, capsys, ex, [mail("a", sender=f"Pat {pat} x"), mail("b", subject=f"re {pat}"), mail("m1", sender=MISS, subject=MISS)])
    assert list(out) == ["m1"]                                                      # the literal text drops its threads, the look-alike stays
    out = kept(env, capsys, {"drop_bodies_from": [pat]}, [mail("c", sender=f"{pat} bills"), mail("m2", sender=MISS, subject=MISS)])
    assert out["c"]["summary"] == "" and out["m2"]["summary"] == "body text"       # the sender keeps sender and subject only


def test_starts_and_contains_modes_are_literal_and_case_insensitive(env, capsys):
    ex = {"drop_senders": [{"starts": "casey"}], "drop_subjects": [{"contains": "PRIVATE"}, "x.y"]}
    got = kept(env, capsys, ex, [mail("1", sender="CASEY <c@example.org>"), mail("2", sender="not casey <c@example.org>"),
                                  mail("3", subject="a Private matter"), mail("4", subject="xzy"), mail("5", subject="x.y"), mail("6")])
    assert sorted(got) == ["2", "4", "6"]


def test_the_exclusion_code_has_no_nested_quantifier_heuristic_and_compiles_only_escaped_text():
    src = (ROOT / "ace_playbook" / "compile" / "brief_excl.py").read_text() + (ROOT / "ace_playbook" / "compile" / "brief.py").read_text()
    assert "NESTED" not in src and src.count("re.compile(") == 1 and "re.escape(" in src


def child_env(env, monkeypatch, worker):
    """Run the exclusion child with `worker` in place of brief_excl._work, under a config that names an exclusions file."""
    monkeypatch.setenv("BRIEF_TEST_CHILD_PID", str(env / "child.pid"))
    monkeypatch.setattr(brief_excl, "_work", worker)
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(bs.config(env, brief_exclusions=str(bs.exclusions(env / "ex.json")))))


def test_a_hung_exclusion_child_is_killed_and_the_run_refuses(env, capsys, monkeypatch):
    child_env(env, monkeypatch, bs.work_hang)
    monkeypatch.setattr(brief_excl, "TIMEOUT", 4)                                                # a spawned child needs a moment to start
    start = time.monotonic()
    code, out, err = run(capsys, "digest-apply", put(env, "d.json", bs.digest()))
    assert (code, out) == (2, "") and "schema: exclusions" in err and time.monotonic() - start < 15
    assert not (env / "brief" / "digest_clean.json").exists()
    pid = int((env / "child.pid").read_text())
    assert pid != os.getpid()
    with pytest.raises(ProcessLookupError):                                                     # the child is gone, not left running
        os.kill(pid, 0)


@pytest.mark.parametrize("worker", [bs.work_crash, bs.work_boom, *(partial(bs.work_answer, k) for k in ("dict", "str", "ints", "twice", "alien", "forged"))])
def test_a_failing_or_garbling_exclusion_child_never_lets_the_digest_through(env, capsys, monkeypatch, worker):
    child_env(env, monkeypatch, worker)
    code, out, err = run(capsys, "digest-apply", put(env, "d.json", bs.digest()))
    assert code == 2 and "schema: exclusions" in err and bs.MARKER not in out + err and not (env / "brief" / "digest_clean.json").exists()


@pytest.mark.parametrize("kind", ["blanked", "same"])
def test_a_child_answer_made_of_input_threads_or_a_blanked_summary_is_accepted(env, capsys, monkeypatch, kind):
    child_env(env, monkeypatch, partial(bs.work_answer, kind))
    code, out, err = run(capsys, "digest-apply", put(env, "d.json", bs.digest()))
    assert code == 0 and (env / "brief" / "digest_clean.json").exists() and err == ""


def test_the_exclusion_child_is_spawned_and_sees_only_its_arguments(env, capsys, monkeypatch):
    child_env(env, monkeypatch, bs.work_spy)
    monkeypatch.setattr(brief_excl, "_PARENT_ONLY", "RAW-IN-THE-PARENT", raising=False)
    assert run(capsys, "digest-apply", put(env, "d.json", bs.digest()))[0] == 0
    assert json.loads((env / "child.pid").read_text()) == {"leak": None, "mail_arg": 4}        # a forked child would have seen the variable
    assert brief_excl.START == "spawn"


def test_a_configured_but_missing_or_unparseable_exclusions_file_fails_closed(env, capsys):
    os.environ["ACE_PLAYBOOK_CONFIG"] = str(bs.config(env, brief_exclusions=str(env / "nowhere.json")))
    assert run(capsys, "digest-apply", put(env, "d.json", bs.digest()))[0] == 1
    os.environ["ACE_PLAYBOOK_CONFIG"] = str(bs.config(env, brief_exclusions=put(env, "bad.json", "{not json")))
    assert run(capsys, "digest-apply", put(env, "d.json", bs.digest()))[0] == 1
    assert not (env / "brief" / "digest_clean.json").exists()


def test_exclusions_match_case_insensitively_on_the_cleaned_fields(env, capsys):
    os.environ["ACE_PLAYBOOK_CONFIG"] = str(bs.config(env, brief_exclusions=str(bs.exclusions(env / "ex.json", drop_senders=[{"starts": "casey"}]))))
    d = bs.digest()
    d["mail"][1]["sender"] = "​CASEY <c@example.org>"                   # a zero-width prefix is stripped before the match
    run(capsys, "digest-apply", put(env, "d.json", d))
    assert [m["thread_id"] for m in json.loads((env / "brief" / "digest_clean.json").read_text())["mail"]] == ["t1", "t3"]


# ------------------------------------------------------------------ due, render, state
def mark(env, delta):
    d = env / "brief"
    d.mkdir(exist_ok=True)
    (d / "brief_state.json").write_text(json.dumps({"last_brief": (datetime.now(timezone.utc) + delta).strftime(brief.FMT), "needs_you_count": 1}))


def test_due_follows_the_state_file_and_dedupe_hours(env, capsys):
    assert run(capsys, "due")[0] == 0                                          # no state: due
    mark(env, timedelta(hours=-1))
    assert run(capsys, "due")[0] == 3 and run(capsys, "due", "--force")[0] == 0
    mark(env, timedelta(hours=-4, minutes=-1))
    assert run(capsys, "due")[0] == 0
    mark(env, timedelta(hours=5))                                              # a clock that ran ahead cannot block the brief for good
    assert run(capsys, "due")[0] == 0
    (env / "brief" / "brief_state.json").write_text("{broken")
    assert run(capsys, "due")[0] == 0
    os.environ["ACE_PLAYBOOK_CONFIG"] = str(bs.config(env, brief_dedupe_hours=0))
    mark(env, timedelta(seconds=-5))
    assert run(capsys, "due")[0] == 0


def test_render_writes_page_and_json_0600_and_prints_the_path(env, capsys):
    code, out, err = run(capsys, "render", put(env, "b.json", "```json\n" + json.dumps(bs.brief()) + "\n```"))
    page = env / "brief" / "brief.html"
    assert (code, err, out.strip()) == (0, "", str(page))
    assert mode(page) == 0o600 and mode(env / "brief" / "brief.json") == 0o600
    assert "<h1>Meetings bookend the day, the middle is yours.</h1>" in page.read_text()
    assert json.loads((env / "brief" / "brief.json").read_text())["acts"][0]["load"] == "normal"


def test_render_refuses_a_bad_brief_and_writes_nothing(env, capsys):
    code, out, err = run(capsys, "render", put(env, "b.json", bs.brief(day_class="busy", headline=bs.MARKER)))
    assert (code, out) == (2, "") and bs.MARKER not in err
    assert not list((env / "brief").glob("brief.*"))


def test_state_mark_counts_needs_you_and_status_reports(env, capsys):
    run(capsys, "render", put(env, "b.json", bs.brief()))
    assert run(capsys, "status")[1].splitlines()[:3] == ["last brief: never", "needs you: 0", "ledger rows: 0"]
    code, out, _ = run(capsys, "state-mark")
    state = json.loads((env / "brief" / "brief_state.json").read_text())
    assert (code, out.strip(), state["needs_you_count"]) == (0, "1", 1) and mode(env / "brief" / "brief_state.json") == 0o600
    assert datetime.strptime(state["last_brief"], brief.FMT)
    assert run(capsys, "due")[0] == 3
    lines = run(capsys, "status")[1].splitlines()
    assert lines[0] == f"last brief: {state['last_brief']}" and lines[1:3] == ["needs you: 1", "ledger rows: 0"] and lines[3] == "last stage costs: none"


def test_conf_prints_the_value_and_creates_the_brief_dir_0700(env, capsys):
    assert run(capsys, "conf", "brief_model") == (0, "claude-sonnet-5-5\n", "")
    assert run(capsys, "conf", "brief_max_budget_usd")[1] == "1.0\n"
    assert run(capsys, "conf", "brief_dir")[1] == f"{env / 'brief'}\n" and mode(env / "brief") == 0o700
    assert run(capsys, "conf", "data_dir")[0] == 1 and run(capsys, "conf", "brief_model_stage2")[0] == 0     # only brief_* keys


# ------------------------------------------------------------------ extract and ledger
def test_extract_reads_rate_limits_result_tools_and_text(env, capsys):
    path = put(env, "s.jsonl", bs.stream(result_text='{"a": 1}'))
    first = json.loads(run(capsys, "extract", path, "--what", "rate_limit")[1])
    last = json.loads(run(capsys, "extract", path, "--what", "rate_limit", "--last")[1])
    assert first["rate_limit_info"]["unifiedWindows"]["five_hour"]["utilization"] == 0.08
    assert last["rate_limit_info"]["unifiedWindows"]["five_hour"]["utilization"] == pytest.approx(0.09)
    res = json.loads(run(capsys, "extract", path, "--what", "result")[1])
    assert res["total_cost_usd"] == 0.2424 and res["_tools_called"] == ["mcp__claude_ai_Gmail__search_threads"]
    assert run(capsys, "extract", path, "--what", "text") == (0, '{"a": 1}\n', "")


def test_extract_failures(env, capsys):
    assert run(capsys, "extract", put(env, "e.jsonl", bs.stream(is_error=True)), "--what", "text")[0] == 1
    assert run(capsys, "extract", put(env, "n.jsonl", '{"type": "system"}\n'), "--what", "result")[0] == 1
    assert run(capsys, "extract", put(env, "n.jsonl", '{"type": "system"}\n'), "--what", "rate_limit") == (0, "null\n", "")
    assert run(capsys, "extract", str(env / "missing"), "--what", "text")[0] == 1
    assert run(capsys, "extract", put(env, "s.jsonl", bs.stream()), "--what", "bogus")[0] == 1


def test_ledger_appends_one_0600_row_with_the_window_fields(env, capsys):
    path = put(env, "s.jsonl", bs.stream())
    files = {}
    for name, extra in (("first", []), ("last", ["--last"])):
        files[name] = env / f"{name}.json"
        files[name].write_text(run(capsys, "extract", path, "--what", "rate_limit", *extra)[1])
    files["res"] = env / "res.json"
    files["res"].write_text(run(capsys, "extract", path, "--what", "result")[1])
    assert run(capsys, "ledger", str(files["first"]), str(files["last"]), "stage1", str(files["res"]))[0] == 0
    assert run(capsys, "ledger", "nowhere", "nowhere", "stage2", str(files["res"]))[0] == 0              # missing rate-limit files: nulls
    rows = [json.loads(ln) for ln in (env / "brief" / "brief_ledger.jsonl").read_text().splitlines()]
    assert mode(env / "brief" / "brief_ledger.jsonl") == 0o600 and [r["stage"] for r in rows] == ["stage1", "stage2"]
    r = rows[0]
    assert r["model"] == "claude-sonnet-5-5" and r["cost_usd"] == 0.2424 and r["num_turns"] == 5 and r["duration_ms"] == 20800
    assert (r["input_tokens"], r["cache_creation_input_tokens"], r["cache_read_input_tokens"], r["output_tokens"]) == (7, 51891, 91229, 2570)
    assert (r["five_hour_before"], r["seven_day_before"], r["seven_day_after"]) == (0.08, 0.01, 0.01)
    assert r["five_hour_after"] == pytest.approx(0.09) and r["tools_called"] == ["mcp__claude_ai_Gmail__search_threads"]
    assert datetime.strptime(r["ts"], brief.FMT)
    assert rows[1]["five_hour_before"] is None and rows[1]["seven_day_after"] is None and rows[1]["cost_usd"] == 0.2424
    assert run(capsys, "status")[1].splitlines()[2:] == ["ledger rows: 2", "last stage costs: stage1 $0.2424, stage2 $0.2424"]


def test_ledger_survives_garbage_inputs_and_refuses_a_bad_stage(env, capsys):
    junk = put(env, "j.json", "[1, 2")
    assert run(capsys, "ledger", junk, junk, "stage1", junk)[0] == 0
    row = json.loads((env / "brief" / "brief_ledger.jsonl").read_text())
    assert row["cost_usd"] is None and row["tools_called"] == [] and row["model"] == "unknown"
    assert run(capsys, "ledger", junk, junk, "Stage 1; rm", junk)[0] == 2


def test_the_module_runs_as_a_script_and_never_echoes_content(tmp_path):
    conf = bs.config(tmp_path)
    bad = tmp_path / "d.json"
    bad.write_text(json.dumps({"mail": bs.MARKER}))
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.brief", "digest-apply", str(bad)], capture_output=True, text=True, cwd=ROOT,
                       env={**os.environ, "ACE_PLAYBOOK_CONFIG": str(conf)}, timeout=30)
    assert p.returncode == 2 and bs.MARKER not in p.stdout + p.stderr
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.brief"], capture_output=True, text=True, cwd=ROOT, timeout=30,
                       env={**os.environ, "ACE_PLAYBOOK_CONFIG": str(conf)})
    assert p.returncode == 2 and "usage" in p.stderr


# ------------------------------------------------------------------ stream splitting and the tool drift audit
def jsonl(events) -> str:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85", "\x1c", "\x1d", "\x1e", "\x0b", "\x0c"])
def test_a_line_separator_inside_a_json_string_neither_blocks_the_result_nor_hides_a_tool_call(env, capsys, sep):
    call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__claude_ai_Gmail__send_message", "input": {"to": f"a{sep}b"}}]}}
    result = {"type": "result", "subtype": "success", "is_error": False, "result": f"x{sep}y", "total_cost_usd": 0.1}
    path = put(env, "u.jsonl", jsonl([call, result]))
    assert run(capsys, "extract", path, "--what", "text") == (0, f"x{sep}y\n", "")
    assert json.loads(run(capsys, "extract", path, "--what", "result")[1])["_tools_called"] == ["mcp__claude_ai_Gmail__send_message"]


ALLOW = ROOT / "hooks" / "brief_allowed_tools.txt"
EXTRA = "mcp__claude_ai_Gmail__a_new_write_tool"


def audit(env, capsys, text):
    code, out, err = run(capsys, "tools-audit", put(env, "s.jsonl", text), "--allow", str(ALLOW))
    ledger = env / "brief" / "brief_ledger.jsonl"
    return code, out, err, [json.loads(ln) for ln in ledger.read_text().splitlines()] if ledger.exists() else []


def summary(out):
    return json.loads(out)


def test_a_clean_stream_passes_the_tool_audit_prints_a_summary_and_writes_no_row(env, capsys):
    init = ["ToolSearch", "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Linear__list_issues"]
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=init, junk=False))
    assert (code, err, rows) == (0, "", []) and out == '{"init": 3, "drift": [], "unknown": 0}\n'


@pytest.mark.parametrize("where", ["init", "call", "both"])
def test_an_mcp_tool_outside_the_allowlist_in_the_init_list_or_a_call_is_drift(env, capsys, where):
    tools = (EXTRA,) if where in ("call", "both") else ()
    init = ("ToolSearch", EXTRA, "mcp__claude_ai_Gmail__search_threads") if where in ("init", "both") else ()
    code, out, err, rows = audit(env, capsys, bs.stream(tools=tools, init_tools=init, junk=False))
    assert (code, err) == (2, "") and summary(out) == {"init": len(init), "drift": ["unknown"], "unknown": 1} and EXTRA not in out
    assert len(rows) == 1 and rows[0]["stage"] == "stage1" and rows[0]["ts"].endswith("Z")
    # the init list is claude's own, so its names may be listed in the ledger; a name only a call shows is counted
    assert (rows[0]["drift"], rows[0]["unknown_tools"]) == ((["unknown"], 1) if where == "call" else ([EXTRA], 0))


def test_a_known_connector_tool_outside_the_allowlist_is_drift_and_named_in_the_summary(env, capsys):
    send = "mcp__claude_ai_Gmail__send_message"
    code, out, err, rows = audit(env, capsys, bs.stream(tools=(send,), init_tools=["ToolSearch"], junk=False))
    assert code == 2 and summary(out) == {"init": 1, "drift": [send], "unknown": 0} and rows[0]["drift"] == [send]


def test_any_built_in_in_the_init_list_or_a_call_is_drift_only_toolsearch_and_the_allowlist_pass(env, capsys):
    code, out, err, rows = audit(env, capsys, bs.stream(tools=("Write",), init_tools=["ToolSearch", "Bash", "Read"], junk=False))
    assert code == 2 and summary(out) == {"init": 3, "drift": ["Bash", "Read", "Write"], "unknown": 0} and rows[0]["drift"] == ["Bash", "Read", "Write"]
    assert audit(env, capsys, bs.stream(tools=("ToolSearch",), init_tools=["ToolSearch"], junk=False))[0] == 0


def test_a_bad_allow_file_or_a_missing_stream_fails_with_no_summary(env, capsys, tmp_path):
    bad = tmp_path / "allow.txt"
    bad.write_text("mcp__x__read # note\n")
    code, out, err = run(capsys, "tools-audit", put(env, "s.jsonl", bs.stream()), "--allow", str(bad))
    assert code == 1 and out == ""
    assert run(capsys, "tools-audit", str(env / "missing.jsonl"), "--allow", str(ALLOW))[0] == 1


NASTY = ["Pat asks whether the draft is final", "mcp__Pat_asks_" + bs.MARKER, "-lead", "a" * 300, "x\ny", "", "mcp__x\u2028y", "mcp__x__\x00"]


def test_a_tool_name_outside_the_alphabet_or_unknown_is_counted_and_never_echoed_by_the_audit_the_extract_or_the_ledger(env, capsys):
    calls, init = tuple(NASTY) + ("mcp__claude_ai_Gmail__search_threads",), ["ToolSearch", NASTY[0], NASTY[2]]
    stream = put(env, "n.jsonl", bs.stream(tools=calls, init_tools=init, junk=False))
    code, out, err, rows = audit(env, capsys, open(stream).read())
    assert code == 2 and summary(out) == {"init": 3, "drift": ["unknown"], "unknown": 8} and rows[0]["drift"] == ["unknown"]
    res = json.loads(run(capsys, "extract", stream, "--what", "result")[1])
    assert res["_tools_called"] == ["mcp__claude_ai_Gmail__search_threads"] and res["_unknown_tools"] == len(set(NASTY))
    junk = env / "j.json"
    junk.write_text("[]")
    resf = env / "res.json"
    resf.write_text(json.dumps({**res, "_tools_called": res["_tools_called"] + NASTY[:1] + NASTY[2:]}))      # a doctored file is filtered by NAME too
    assert run(capsys, "ledger", str(junk), str(junk), "stage1", str(resf))[0] == 0
    text = (env / "brief" / "brief_ledger.jsonl").read_text()
    assert bs.MARKER not in text + out + err and "Pat asks" not in text and "-lead" not in text and "a" * 101 not in text
    last = json.loads(text.splitlines()[-1])
    assert last["tools_called"] == ["mcp__claude_ai_Gmail__search_threads"] and last["unknown_tools"] == len(set(NASTY))


def test_the_ledger_row_counts_unknown_tools_as_zero_when_the_result_has_none(env, capsys):
    path = put(env, "s.jsonl", bs.stream())
    resf = env / "res.json"
    resf.write_text(run(capsys, "extract", path, "--what", "result")[1])
    junk = put(env, "j.json", "[]")
    assert run(capsys, "ledger", junk, junk, "stage1", str(resf))[0] == 0
    assert json.loads((env / "brief" / "brief_ledger.jsonl").read_text())["unknown_tools"] == 0


@pytest.mark.parametrize("init", [{"type": "assistant", "message": {"content": []}}, {"type": "system", "subtype": "init"},
                                  {"type": "system", "subtype": "init", "tools": "mcp__x__y"}])
def test_a_stream_without_a_usable_init_tool_list_is_not_audited_so_it_fails(env, capsys, init):
    assert audit(env, capsys, jsonl([init]))[0] == 1


def test_a_garbage_line_makes_the_audit_refuse_and_a_blank_line_does_not(env, capsys):
    clean = bs.stream(init_tools=["ToolSearch"], junk=False)
    code, out, err, rows = audit(env, capsys, clean + "\n   \n")
    assert (code, rows) == (0, [])
    for bad in ("not json\n", "[1, 2]\n", '{"type": \n', "[" * 100_000 + "\n"):         # the last one also overflows the parser's recursion
        code, out, err, rows = audit(env, capsys, clean + bad)
        assert (code, err) == (2, "") and summary(out)["drift"] == ["unparseable"] and rows[-1]["drift"] == ["unparseable"], bad[:10]
        assert rows[-1]["stage"] == "stage1"


def test_an_mcp_tool_that_appears_only_in_a_malformed_line_is_refused_and_reported_by_name(env, capsys):
    hidden = '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name" :  "' + EXTRA + '", "input": {\n'
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=["ToolSearch"], junk=False) + hidden)
    assert (code, err) == (2, "") and rows[-1]["drift"] == ["unknown", "unparseable"] and EXTRA not in out + json.dumps(rows)
    known = hidden.replace(EXTRA, "mcp__claude_ai_Gmail__send_message")                  # a name from the checked-in snapshot may be listed
    assert audit(env, capsys, bs.stream(init_tools=["ToolSearch"], junk=False) + known)[3][-1]["drift"] == ["mcp__claude_ai_Gmail__send_message", "unparseable"]
    allowed = '{"name": "mcp__claude_ai_Gmail__search_threads"\n'                       # an allowed name in a malformed line: still unparseable
    assert audit(env, capsys, bs.stream(init_tools=["ToolSearch"], junk=False) + allowed)[3][-1]["drift"] == ["unparseable"]


def test_an_oversized_stream_is_refused_not_truncated(env, capsys, monkeypatch):
    monkeypatch.setattr(ba, "CAP", 1000)
    tail = jsonl([{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": EXTRA, "input": {"pad": "x" * 2000}}]}}])
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=["ToolSearch"], junk=False) + tail)
    assert (code, err) == (2, "") and "unparseable" in rows[-1]["drift"] and "unparseable" in summary(out)["drift"]


def test_a_u2028_in_the_init_event_does_not_hide_an_extra_tool(env, capsys):
    init = {"type": "system", "subtype": "init", "tools": ["x\u2028y", EXTRA], "mcp_servers": []}
    assert audit(env, capsys, jsonl([init]))[0] == 2


def test_timeout_mark_appends_one_row_and_status_ignores_it_for_costs(env, capsys):
    assert run(capsys, "timeout-mark", "stage1")[0] == 0
    assert run(capsys, "timeout-mark", "Stage 1; rm")[0] == 2
    rows = [json.loads(ln) for ln in (env / "brief" / "brief_ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["timeout"] == "stage1" and rows[0]["ts"].endswith("Z")
    assert run(capsys, "status")[1].splitlines()[2:] == ["ledger rows: 1", "last stage costs: none"]


# ------------------------------------------------------------------ wave 7: every tool call block, the raw scan, status, the ledger model
INIT_OK = ["ToolSearch", "mcp__claude_ai_Gmail__search_threads"]
SEND = "mcp__claude_ai_Gmail__send_message"


def call_event(block):
    return {"type": "assistant", "message": {"content": [block]}}


@pytest.mark.parametrize("name", [["mcp__x"], {"a": 1}, 7, 1.5, None, True])
@pytest.mark.parametrize("kind", ["tool_use", "server_tool_use", "mcp_tool_use"])
def test_a_tool_call_block_whose_name_is_not_a_string_makes_the_stream_unparseable(env, capsys, kind, name):
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + jsonl([call_event({"type": kind, "name": name, "input": {}})]))
    assert (code, err) == (2, "") and summary(out)["drift"] == ["unparseable"] and rows[-1]["drift"] == ["unparseable"]


def test_a_tool_call_block_with_no_name_at_all_is_unparseable(env, capsys):
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + jsonl([call_event({"type": "tool_use", "input": {}})]))
    assert code == 2 and summary(out)["drift"] == ["unparseable"] and rows[-1]["drift"] == ["unparseable"]


@pytest.mark.parametrize("kind", ["server_tool_use", "mcp_tool_use"])
def test_server_and_mcp_tool_use_blocks_are_audited_like_tool_use(env, capsys, kind):
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + jsonl([call_event({"type": kind, "name": SEND})]))
    assert code == 2 and summary(out) == {"init": 2, "drift": [SEND], "unknown": 0} and rows[-1]["drift"] == [SEND]
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + jsonl([call_event({"type": kind, "name": INIT_OK[1]})]))
    assert (code, len(rows)) == (0, 1)          # the first run's ledger row only: an allowed name writes none
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + jsonl([call_event({"type": kind, "name": EXTRA})]))
    assert code == 2 and summary(out)["drift"] == ["unknown"] and EXTRA not in out


def test_a_non_string_call_name_counts_as_an_unknown_tool_in_the_extract(env, capsys):
    calls = [call_event({"type": "tool_use", "name": n}) for n in (["x"], None, 3)]
    res = json.loads(run(capsys, "extract", put(env, "s.jsonl", jsonl([{"type": "result", "result": "x"}] + calls)), "--what", "result")[1])
    assert res["_tools_called"] == [] and res["_unknown_tools"] == 3


def test_a_name_key_inside_an_allowed_tools_input_is_data_and_does_not_refuse_the_run(env, capsys):
    block = {"type": "tool_use", "name": INIT_OK[1], "input": {"query": '{"name": "mcp__evil__x"}', "name": "mcp__evil", "n": [{"name": "mcp__evil2"}]}}
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + jsonl([call_event(block)]))
    assert (code, err, rows) == (0, "", []) and summary(out) == {"init": 2, "drift": [], "unknown": 0}
    result = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f'"name": "{SEND}"'}]}}
    assert audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + jsonl([result]))[0] == 0      # a mail body that quotes a tool name


def test_the_same_text_on_a_malformed_line_is_still_refused(env, capsys):
    bad = '{"type": "assistant", "input": {"name": "' + SEND + '"}\n'
    code, out, err, rows = audit(env, capsys, bs.stream(init_tools=INIT_OK, junk=False) + bad)
    assert code == 2 and rows[-1]["drift"] == [SEND, "unparseable"] and summary(out)["drift"] == [SEND, "unparseable"]


@pytest.mark.parametrize("body", ["1", '"x"', "[1]", "null", "true", "[]", "{", "2.5"])
def test_status_treats_a_state_file_that_is_not_an_object_as_empty(env, capsys, body):
    (env / "brief").mkdir()
    (env / "brief" / "brief_state.json").write_text(body)
    code, out, err = run(capsys, "status")
    assert (code, err) == (0, "") and out.splitlines()[:3] == ["last brief: never", "needs you: 0", "ledger rows: 0"]


def test_status_never_creates_the_brief_directory(env, capsys):
    assert not (env / "brief").exists()
    code, out, err = run(capsys, "status")
    assert (code, err) == (0, "") and out.splitlines() == ["last brief: never", "needs you: 0", "ledger rows: 0", "last stage costs: none"]
    assert not (env / "brief").exists()


def ledger_model(env, capsys, models):
    junk = put(env, "j.json", "[]")
    res = put(env, "r.json", {"type": "result", "modelUsage": models})
    assert run(capsys, "ledger", junk, junk, "stage1", res)[0] == 0
    return json.loads((env / "brief" / "brief_ledger.jsonl").read_text().splitlines()[-1])["model"]


def test_the_ledger_model_is_filtered_by_name_and_unknown_when_nothing_is_left(env, capsys):
    secret = f"Pat asks whether the draft is final {bs.MARKER}"
    assert ledger_model(env, capsys, {"claude-sonnet-5-5": {}, "claude-haiku-5-5": {}}) == "claude-haiku-5-5,claude-sonnet-5-5"
    assert ledger_model(env, capsys, {secret: {}, "claude-sonnet-5-5": {}, "-lead": {}, "x\ny": {}, "": {}}) == "claude-sonnet-5-5"
    assert ledger_model(env, capsys, {secret: {}, "a b": {}, "mcp__x y": {}}) == "unknown"
    assert ledger_model(env, capsys, {}) == "unknown" and ledger_model(env, capsys, ["claude-sonnet-5-5"]) == "unknown"
    assert bs.MARKER not in (env / "brief" / "brief_ledger.jsonl").read_text()


# ------------------------------------------------------------------ wave-6 minors
def last_row(env):
    return json.loads((env / "brief" / "brief_ledger.jsonl").read_text().splitlines()[-1])


def test_status_tolerates_a_ledger_stage_that_is_a_list_or_an_object_and_renders_it(env, capsys):
    (env / "brief").mkdir()
    rows = [{"stage": ["a", "b"], "cost_usd": 0.1}, {"stage": {"x": 1}, "cost_usd": 0.2}, {"stage": "stage1", "cost_usd": 0.3}, {"stage": None}]
    (env / "brief" / "brief_ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    code, out, err = run(capsys, "status")
    assert (code, err) == (0, "") and "ledger rows: 4" in out and "stage1 $0.3" in out and "['a', 'b'] $0.1" in out


@pytest.mark.parametrize("v", [float("inf"), float("-inf"), float("nan")])
def test_num_rejects_inf_and_nan_as_missing(v):
    assert brief._num(v) is None and brief._num(1.5) == 1.5 and brief._num(0) == 0 and brief._num(True) is None and brief._num("1") is None


def test_the_ledger_row_treats_an_infinite_or_nan_figure_as_missing(env, capsys):
    junk = put(env, "j.json", "[]")
    res = put(env, "r.json", '{"type": "result", "total_cost_usd": Infinity, "duration_ms": NaN, "num_turns": -Infinity, "usage": {"output_tokens": 5}}')
    assert run(capsys, "ledger", junk, junk, "stage1", res)[0] == 0
    row = last_row(env)
    assert (row["cost_usd"], row["duration_ms"], row["num_turns"], row["output_tokens"]) == (None, None, None, 5)
    assert "Infinity" not in (env / "brief" / "brief_ledger.jsonl").read_text() and "NaN" not in (env / "brief" / "brief_ledger.jsonl").read_text()


def test_the_drift_list_is_sorted_before_the_cut_to_50(env, capsys, monkeypatch):
    names = [f"mcp__tool{i:02d}" for i in range(60)][::-1]
    monkeypatch.setattr(ba, "audit", lambda stream, allow: {"drift": names, "unknown": 0, "summary": {"init": 0, "drift": [], "unknown": 0}})
    assert run(capsys, "tools-audit", "x", "--allow", "y")[0] == 2
    assert last_row(env)["drift"] == sorted(names)[:50]


def test_the_ledger_tool_list_is_sorted_before_the_cut_to_50(env, capsys):
    names = [f"mcp__claude_ai_Gmail__t{i:02d}" for i in range(60)][::-1]
    junk = put(env, "j.json", "[]")
    res = put(env, "r.json", {"type": "result", "_tools_called": names})
    assert run(capsys, "ledger", junk, junk, "stage1", res)[0] == 0
    assert last_row(env)["tools_called"] == sorted(names)[:50]


def test_the_ledger_model_cuts_each_name_before_it_joins_so_a_long_first_name_does_not_hide_the_rest(env, capsys):
    long = "claude-" + "a" * 300
    assert ledger_model(env, capsys, {long: {}, "claude-b": {}}) == f"{long[:40]},claude-b"
    assert ledger_model(env, capsys, {"claude-sonnet-5-5": {}, "claude-haiku-5-5": {}}) == "claude-haiku-5-5,claude-sonnet-5-5"


@pytest.mark.parametrize("argv", [["state-mark", "junk"], ["due", "--bogus"], ["bogus"], [], ["conf"], ["ledger", "a", "b"]])
def test_unknown_or_missing_argv_exits_2_with_a_one_line_usage_error(env, argv):
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.brief", *argv], capture_output=True, text=True, cwd=ROOT, timeout=30,
                       env={**os.environ, "ACE_PLAYBOOK_CONFIG": str(env / "config.json")})
    assert p.returncode == 2 and p.stdout == ""
    assert len(p.stderr.splitlines()) == 1 and p.stderr.startswith("brief: usage: ") and "Traceback" not in p.stderr


def test_help_still_prints_the_full_usage_and_exits_0(env):
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.brief", "-h"], capture_output=True, text=True, cwd=ROOT, timeout=30,
                       env={**os.environ, "ACE_PLAYBOOK_CONFIG": str(env / "config.json")})
    assert p.returncode == 0 and "usage:" in p.stdout and len(p.stdout.splitlines()) > 2


def test_a_huge_integer_is_kept_as_before_and_is_never_an_overflow_traceback(env, capsys):
    """`math.isfinite` on an int beyond float range raises OverflowError: only a float goes through it (finite floats and ints are kept)."""
    big = int("9" * 400)
    junk = put(env, "j.json", "[]")
    window = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": big}}}}
    res = put(env, "r.json", {"type": "result", "total_cost_usd": big, "duration_ms": 7, "usage": {"output_tokens": -big}})
    assert run(capsys, "ledger", put(env, "w.json", window), junk, "stage1", res) == (0, "", "")
    row = last_row(env)
    assert (row["cost_usd"], row["five_hour_before"], row["duration_ms"], row["output_tokens"]) == (big, big, 7, -big)
    assert brief._num(big) == big and brief._num(True) is None and brief._num(2.5) == 2.5 and brief._num(float("inf")) is None


def test_a_usage_error_is_one_line_even_when_an_argument_holds_a_newline(env):
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile.brief", "state-mark", "a\nb", "c\rd"], capture_output=True, text=True, cwd=ROOT,
                       timeout=30, env={**os.environ, "ACE_PLAYBOOK_CONFIG": str(env / "config.json")})
    assert p.returncode == 2 and len(p.stderr.splitlines()) == 1 and p.stderr.startswith("brief: usage: ") and "<verb>" not in p.stderr
