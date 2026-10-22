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

from ace_playbook.compile import brief, brief_excl, brief_render

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
    assert row["cost_usd"] is None and row["tools_called"] == [] and row["model"] == ""
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


def test_a_clean_stream_passes_the_tool_audit_and_writes_no_row(env, capsys):
    init = ["ToolSearch", "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Linear__list_issues"]
    assert audit(env, capsys, bs.stream(init_tools=init)) == (0, "", "", [])


@pytest.mark.parametrize("where", ["init", "call", "both"])
def test_an_mcp_tool_outside_the_allowlist_in_the_init_list_or_a_call_is_drift(env, capsys, where):
    tools = (EXTRA,) if where in ("call", "both") else ()
    init = ("ToolSearch", EXTRA, "mcp__claude_ai_Gmail__search_threads") if where in ("init", "both") else ()
    code, out, err, rows = audit(env, capsys, bs.stream(tools=tools, init_tools=init))
    assert (code, out, err) == (2, "", "")          # names go to the ledger only; brief.sh prints the one-line reason
    assert len(rows) == 1 and rows[0]["drift"] == [EXTRA] and rows[0]["stage"] == "stage1" and rows[0]["ts"].endswith("Z")


def test_a_non_mcp_tool_is_not_drift_and_a_bad_allow_file_fails(env, capsys, tmp_path):
    assert audit(env, capsys, bs.stream(tools=("ToolSearch",), init_tools=["Bash", "Read"]))[0] == 0
    bad = tmp_path / "allow.txt"
    bad.write_text("mcp__x__read # note\n")
    code, out, err = run(capsys, "tools-audit", put(env, "s.jsonl", bs.stream()), "--allow", str(bad))
    assert code == 1 and out == ""
    assert run(capsys, "tools-audit", str(env / "missing.jsonl"), "--allow", str(ALLOW))[0] == 1


@pytest.mark.parametrize("init", [{"type": "assistant", "message": {"content": []}}, {"type": "system", "subtype": "init"},
                                  {"type": "system", "subtype": "init", "tools": "mcp__x__y"}])
def test_a_stream_without_a_usable_init_tool_list_is_not_audited_so_it_fails(env, capsys, init):
    assert audit(env, capsys, jsonl([init]))[0] == 1


def test_a_u2028_in_the_init_event_does_not_hide_an_extra_tool(env, capsys):
    init = {"type": "system", "subtype": "init", "tools": ["x\u2028y", EXTRA], "mcp_servers": []}
    assert audit(env, capsys, jsonl([init]))[0] == 2


def test_timeout_mark_appends_one_row_and_status_ignores_it_for_costs(env, capsys):
    assert run(capsys, "timeout-mark", "stage1")[0] == 0
    assert run(capsys, "timeout-mark", "Stage 1; rm")[0] == 2
    rows = [json.loads(ln) for ln in (env / "brief" / "brief_ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["timeout"] == "stage1" and rows[0]["ts"].endswith("Z")
    assert run(capsys, "status")[1].splitlines()[2:] == ["ledger rows: 1", "last stage costs: none"]
