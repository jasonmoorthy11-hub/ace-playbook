"""`hooks/brief.sh` end to end with a FAKE claude (tests/fixtures/brief/fake_claude.py): flags, cleaning, files, modes, ledger, dedupe."""

import json
import os
import stat
import subprocess
import time

import pytest

from . import brief_support as bs
from .test_statusline import ROOT

SCRIPT = ROOT / "hooks" / "brief.sh"
FAKE = ROOT / "tests" / "fixtures" / "brief" / "fake_claude.py"


class World:
    def __init__(self, tmp_path, **cfg):
        self.tmp = tmp_path
        self.fake = tmp_path / "fake"
        self.fake.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        notifier = self.bin / "terminal-notifier"          # first on PATH: a real terminal-notifier must never fire from a test
        notifier.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > {tmp_path}/notified\n')
        notifier.chmod(0o755)
        self.set_answers()
        (tmp_path / "home").mkdir()
        self.conf = bs.config(tmp_path, brief_exclusions=str(bs.exclusions(tmp_path / "ex.json")), **cfg)
        self.dir = tmp_path / "brief"
        self.env = {"HOME": str(tmp_path / "home"), "PATH": f"{self.bin}:{os.environ['PATH']}", "ACE_PLAYBOOK_CONFIG": str(self.conf),
                    "ACE_BRIEF_CLAUDE": str(FAKE), "ACE_FAKE_DIR": str(self.fake), "ANTHROPIC_API_KEY": "FAKE-key", "CLAUDECODE": "1"}

    def set_answers(self, digest=None, brief=None):
        (self.fake / "digest.json").write_text(digest if isinstance(digest, str) else json.dumps(digest or bs.digest()))
        (self.fake / "brief.json").write_text(brief if isinstance(brief, str) else json.dumps(brief or bs.brief()))

    def run(self, *args, **env):
        return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env={**self.env, **env}, timeout=120, cwd=self.tmp)

    def ledger(self):
        path = self.dir / "brief_ledger.jsonl"
        return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []

    def seen(self, name):
        return json.loads((self.fake / name).read_text())


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_the_script_is_executable_and_its_syntax_parses():
    assert os.access(SCRIPT, os.X_OK) and os.access(FAKE, os.X_OK)
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_a_forced_run_writes_page_json_ledger_and_state_with_private_modes(w):
    p = w.run("--force")
    assert (p.returncode, p.stdout, p.stderr) == (0, "digest: calendar 1, mail 2 of 4, issues 1\nbrief: ok, 1 need you\n", "")
    page = (w.dir / "brief.html").read_text()
    assert "<h1>Meetings bookend the day, the middle is yours.</h1>" in page and "Reply to the draft question" in page
    assert json.loads((w.dir / "brief.json").read_text())["day_class"] == "normal"
    state = json.loads((w.dir / "brief_state.json").read_text())
    assert state["needs_you_count"] == 1 and state["last_brief"].endswith("Z")
    rows = w.ledger()
    assert [r["stage"] for r in rows] == ["stage1", "stage2"]
    for r in rows:
        assert r["model"] == "claude-sonnet-5-5" and r["five_hour_before"] == r["five_hour_after"] and r["seven_day_before"] == 0.01
        assert {"ts", "cost_usd", "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens", "duration_ms",
                "num_turns", "tools_called", "five_hour_after", "seven_day_after"} <= set(r)
    assert (rows[0]["five_hour_before"], rows[1]["five_hour_before"]) == (0.08, 0.09)
    assert (rows[0]["tools_called"], rows[1]["tools_called"]) == (["mcp__claude_ai_Gmail__search_threads"], [])
    assert (rows[0]["cost_usd"], rows[1]["cost_usd"], rows[0]["num_turns"], rows[1]["num_turns"]) == (0.2424, 0.012, 3, 1)
    files = [p for p in w.dir.rglob("*") if p.is_file()]
    assert {p.name for p in files} >= {"brief.html", "brief.json", "brief_ledger.jsonl", "brief_state.json", "digest_clean.json"}
    assert all(mode(p) == 0o600 for p in files), [(p.name, oct(mode(p))) for p in files if mode(p) != 0o600]
    assert all(mode(p) == 0o700 for p in w.dir.rglob("*") if p.is_dir()) and mode(w.dir) == 0o700


def test_the_two_stages_get_exactly_the_flags_the_design_names(w):
    assert w.run("--force").returncode == 0
    one, two = w.seen("argv_stage1.json"), w.seen("argv_stage2.json")
    allowed = [ln for ln in (ROOT / "hooks" / "brief_allowed_tools.txt").read_text().splitlines() if ln and not ln.startswith("#")]
    flag = lambda argv, name: argv[argv.index(name) + 1]  # noqa: E731
    assert "-p" in one and flag(one, "--allowedTools") == ",".join(allowed) and flag(one, "--model") == "claude-sonnet-5-5"
    assert flag(one, "--settings") == str(ROOT / "hooks" / "brief-settings.json") and flag(one, "--output-format") == "stream-json"
    assert "--verbose" in one and flag(one, "--max-turns") == "12" and flag(one, "--max-budget-usd") == "1.0"
    assert flag(one, "--tools") == "ToolSearch" and "--strict-mcp-config" not in one          # connectors are account-level: strict would drop them
    assert one[-1].startswith("You are the read-only gatherer") and "--bare" not in one + two
    assert flag(two, "--tools") == "" and "--strict-mcp-config" in two and flag(two, "--max-turns") == "1" and flag(two, "--model") == "claude-sonnet-5-5"
    assert flag(two, "--system-prompt").startswith("You write a calm morning brief") and "--allowedTools" not in two
    for stage in ("stage1", "stage2"):
        e = w.seen(f"env_{stage}.json")
        assert (e["has_api_key"], e["has_claudecode"]) == (False, False)          # env -u ANTHROPIC_API_KEY -u CLAUDECODE
        assert e["repo"] == str(ROOT) and os.path.realpath(e["cwd"]) == os.path.realpath(w.dir / "run" / "cwd")


def test_models_come_from_the_config(tmp_path):
    w = World(tmp_path, brief_model="model-one", brief_model_stage2="model-two", brief_max_budget_usd=0.5)
    assert w.run("--force").returncode == 0
    assert w.seen("argv_stage1.json")[w.seen("argv_stage1.json").index("--model") + 1] == "model-one"
    assert w.seen("argv_stage2.json")[w.seen("argv_stage2.json").index("--model") + 1] == "model-two"
    assert "0.5" in w.seen("argv_stage2.json") and [r["model"] for r in w.ledger()] == ["model-one", "model-two"]


def test_an_excluded_sender_is_cleaned_before_stage_two_sees_the_digest(w):
    assert w.run("--force").returncode == 0
    clean_text = (w.dir / "run" / "digest_clean.json").read_text()
    clean = json.loads(clean_text)
    assert [m["thread_id"] for m in clean["mail"]] == ["t1", "t3"] and clean["mail"][1]["summary"] == ""
    sent = (w.fake / "stdin_stage2.txt").read_text()
    assert sent == clean_text
    for private in ("casey@", "Casey", "should never reach stage two", "HOLD-THIS", "account number in the body"):
        assert private not in sent


def test_no_mail_content_reaches_the_output_and_the_raw_streams_do_not_outlive_the_run(w):
    p = w.run("--force")
    assert bs.MARKER not in p.stdout + p.stderr and "FAKE-MAIL-BODY" not in p.stdout + p.stderr and "Pat" not in p.stdout + p.stderr
    assert sorted(x.name for x in (w.dir / "run").iterdir()) == ["cwd", "digest_clean.json"]
    kept = w.run("--force", ACE_BRIEF_KEEP="1")
    assert kept.returncode == 0 and (w.dir / "run" / "stage1.jsonl").exists()
    assert all(mode(x) == 0o600 for x in (w.dir / "run").iterdir() if x.is_file())


def test_a_second_run_inside_the_dedupe_window_exits_0_and_writes_nothing(w):
    assert w.run("--force").returncode == 0
    state, rows, html = (w.dir / "brief_state.json").read_text(), w.ledger(), (w.dir / "brief.html").read_text()
    (w.fake / "argv_stage1.json").unlink()
    p = w.run()
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")
    assert (w.dir / "brief_state.json").read_text() == state and w.ledger() == rows and (w.dir / "brief.html").read_text() == html
    assert not (w.fake / "argv_stage1.json").exists()                          # claude was not even started
    time.sleep(1.1)
    assert w.run("--force").returncode == 0 and len(w.ledger()) == 4 and (w.dir / "brief_state.json").read_text() != state


def test_a_run_after_the_window_is_due_again(tmp_path):
    w = World(tmp_path, brief_dedupe_hours=0)
    assert w.run().returncode == 0 and w.run().returncode == 0 and len(w.ledger()) == 4


def test_a_fenced_answer_is_tolerated(w):
    p = w.run("--force", ACE_FAKE_FENCE="1")
    assert p.returncode == 0 and (w.dir / "brief.html").exists()


def test_stage_one_failure_stops_the_run_and_still_logs_its_cost(w):
    p = w.run("--force", ACE_FAKE_FAIL="stage1")
    assert p.returncode == 1 and p.stderr == "brief: stage 1 failed (exit 1)\n" and p.stdout == ""
    assert [r["stage"] for r in w.ledger()] == ["stage1"] and not (w.dir / "brief.html").exists() and not (w.dir / "brief_state.json").exists()
    assert not (w.fake / "argv_stage2.json").exists() and not (w.dir / "run.lock").exists()


def test_stage_two_failure_leaves_no_page_and_no_state_so_the_next_wake_retries(w):
    p = w.run("--force", ACE_FAKE_FAIL="stage2")
    assert p.returncode == 1 and p.stderr == "brief: stage 2 failed (exit 1)\n"
    assert [r["stage"] for r in w.ledger()] == ["stage1", "stage2"] and not (w.dir / "brief.html").exists() and not (w.dir / "brief_state.json").exists()
    assert w.run().returncode == 0 and (w.dir / "brief.html").exists()


@pytest.mark.parametrize("digest, reason", [("no json here", "digest refused"), ('{"mail": []}', "digest refused")])
def test_a_digest_that_fails_the_schema_never_reaches_stage_two(w, digest, reason):
    w.set_answers(digest=digest)
    p = w.run("--force")
    assert p.returncode == 1 and p.stderr.strip().splitlines()[-1] == f"brief: {reason}" and not (w.fake / "argv_stage2.json").exists()
    assert not (w.dir / "run" / "digest_clean.json").exists()


def test_a_brief_that_fails_the_schema_writes_no_page_and_no_state(w):
    w.set_answers(brief=json.dumps(bs.brief(day_class="busy")))
    p = w.run("--force")
    assert p.returncode == 1 and p.stderr.strip().splitlines()[-1] == "brief: brief refused" and not (w.dir / "brief.html").exists()
    assert not (w.dir / "brief_state.json").exists()


def test_an_injection_in_the_brief_json_is_escaped_in_the_served_page(w):
    evil = "<script>alert(1)</script><img src=x onerror=alert(1)>"
    item = {"title": evil, "sentence": evil, "source": evil, "url": "javascript:alert(1)"}
    w.set_answers(brief=json.dumps(bs.brief(headline=evil, needs_you=[item])))
    assert w.run("--force").returncode == 0
    page = (w.dir / "brief.html").read_text()
    assert "<script" not in page.lower() and "<img" not in page and "javascript:" not in page.split("<body>")[0] and 'href="javascript' not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_a_missing_config_is_a_one_line_failure(w):
    p = w.run("--force", ACE_PLAYBOOK_CONFIG=str(w.tmp / "nowhere.json"))
    assert (p.returncode, p.stdout, p.stderr.strip().splitlines()[-1]) == (1, "", "brief: config")


def test_a_held_lock_exits_quietly_and_a_stale_one_is_cleared(w):
    (w.dir / "run.lock").mkdir(parents=True)
    p = w.run("--force")
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "") and not w.ledger() and (w.dir / "run.lock").exists()
    old = time.time() - 3600
    os.utime(w.dir / "run.lock", (old, old))
    assert w.run("--force").returncode == 0 and len(w.ledger()) == 2 and not (w.dir / "run.lock").exists()


def test_the_notification_fires_only_when_something_needs_you(w):
    assert w.run("--force").returncode == 0
    assert (w.tmp / "notified").read_text().splitlines() == ["-title", "ACE brief", "-message", "1 need you", "-open", f"file://{w.dir}/brief.html"]
    (w.tmp / "notified").unlink()
    w.set_answers(brief=json.dumps(bs.brief(needs_you=[])))
    p = w.run("--force")
    assert p.stdout.splitlines()[-1] == "brief: ok, 0 need you" and not (w.tmp / "notified").exists()
