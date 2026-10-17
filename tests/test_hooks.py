import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.inject.ledger import read_jsonl
from ace_playbook.reflect import ReflectorClient
from ace_playbook.store import DeltaLog
from ace_playbook.sweep import find_candidates, prune_queue, run_sweep

from .helpers import GLOBAL, add_draft
from .jsonl import assistant, user, write
from .test_reflect import FakeTransport, api

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"


@pytest.fixture
def env(tmp_path):
    conf = tmp_path / "config.json"
    data = tmp_path / "data"
    conf.write_text(json.dumps({"data_dir": str(data), "holdout_enabled": False}))
    e = {**os.environ, "ACE_PLAYBOOK_CONFIG": str(conf), "ACE_PLAYBOOK_HOME": str(ROOT),
         "ACE_PYTHON": sys.executable}
    return e, cfgmod.load(conf)


def run_hook(name, env, payload):
    t0 = time.perf_counter()
    p = subprocess.run([str(HOOKS / name)], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=30)
    return p, time.perf_counter() - t0


def test_sessionend_only_enqueues_and_is_fast(env, tmp_path):
    e, cfg = env
    p, dt = run_hook("sessionend.sh", e, {"session_id": "s1", "transcript_path": "/x/s1.jsonl",
                                          "reason": "clear"})
    assert p.returncode == 0 and p.stdout == ""
    q = list(read_jsonl(cfg.queue))
    assert q[0]["session_id"] == "s1" and q[0]["transcript_path"] == "/x/s1.jsonl"
    assert not cfg.deltas.exists() and not cfg.reflections.exists()
    assert dt < 1.0, f"SessionEnd budget is 1.5 s; took {dt:.2f}s"


def test_sessionstart_script_emits_context_json(env, tmp_path):
    e, cfg = env
    DeltaLog(cfg.deltas).append(add_draft("Script visible pinned global lesson", GLOBAL,
                                          status="active", pinned=True))
    p, _ = run_hook("sessionstart.sh", e, {"session_id": "s1", "cwd": str(tmp_path), "source": "compact"})
    assert p.returncode == 0
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Script visible pinned global lesson" in ctx and len(ctx) <= 7000


@pytest.mark.parametrize("script", ["sessionstart.sh", "sessionend.sh", "stop_citation_ledger.sh"])
def test_scripts_exit_zero_on_garbage_input(env, script):
    e, _ = env
    p = subprocess.run([str(HOOKS / script)], input="}{ garbage", capture_output=True, text=True, env=e)
    assert p.returncode == 0


def test_stop_script_records_citation(env, tmp_path):
    e, cfg = env
    DeltaLog(cfg.deltas).append(add_draft("Cited via the stop hook script", GLOBAL,
                                          status="active", pinned=True))
    run_hook("sessionstart.sh", e, {"session_id": "s7", "cwd": str(tmp_path)})
    lid = next(read_jsonl(cfg.injections))["shown_ids"][0]
    run_hook("stop_citation_ledger.sh", e, {"session_id": "s7",
                                            "last_assistant_message": f"Because [{lid}] says so"})
    assert next(read_jsonl(cfg.citations))["cited_ids"] == [lid]


# ------------------------------------------------------------------- sweep
def make_transcripts(root, names):
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in names:
        recs = [user(f"message {i} for {n} " + "pad " * 40, sessionId=n) for i in range(8)]
        recs[1] = assistant("ok " * 30)
        p = write(root / f"{n}.jsonl", recs)
        old = time.time() - 7200
        os.utime(p, (old, old))
        paths.append(p)
    return paths


def test_sweep_finds_unreflected_idle_transcripts(env, tmp_path):
    _, cfg = env
    cfg.transcripts_root = tmp_path / "projects"
    a, b, c = make_transcripts(cfg.transcripts_root / "proj", ["sa", "sb", "sc"])
    (cfg.transcripts_root / "proj" / "subagents").mkdir()
    sub = write(cfg.transcripts_root / "proj" / "subagents" / "x.jsonl", [user("x" * 3000)])
    fresh = write(cfg.transcripts_root / "proj" / "fresh.jsonl", [user("y" * 3000)])
    cfg.reflections.parent.mkdir(parents=True, exist_ok=True)
    cfg.reflections.write_text(json.dumps({"session_id": "sb", "status": "ok"}) + "\n")
    names = {cd.path.stem for cd in find_candidates(cfg, idle_minutes=60)}
    assert names == {"sa", "sc"} and sub.stem not in names and fresh.stem not in names


def test_sweep_runs_reflector_and_prunes_queue(env, tmp_path):
    _, cfg = env
    cfg.transcripts_root = tmp_path / "projects"
    cfg.api_key_path = tmp_path / "key"
    cfg.api_key_path.write_text("sk-" + "ant-" + "FAKEFAKEFAKEFAKEFAKEFAKE1234")
    a, b = make_transcripts(cfg.transcripts_root, ["sa", "sb"])
    cfg.queue.parent.mkdir(parents=True, exist_ok=True)
    cfg.queue.write_text(json.dumps({"session_id": "sa", "transcript_path": str(a)}) + "\n"
                         + json.dumps({"session_id": "zz", "transcript_path": str(tmp_path / "gone")}) + "\n")
    t = FakeTransport(api('{"insights":[],"tags":[]}'), api('{"insights":[],"tags":[]}'))
    client = ReflectorClient(cfg, transport=t, sleep=lambda s: None)
    dry = run_sweep(cfg, client, dry_run=True)
    assert {d["path"] for d in dry} == {"sa.jsonl", "sb.jsonl"} and not t.calls
    out = run_sweep(cfg, client, max_n=5)
    assert [o["status"] for o in out] == ["ok", "ok"] and len(t.calls) == 2
    assert run_sweep(cfg, client) == []
    assert [e["session_id"] for e in read_jsonl(cfg.queue)] == ["zz"]
    prune_queue(cfg)


def test_sweep_without_api_key_makes_no_calls_and_logs(env, tmp_path):
    _, cfg = env
    cfg.transcripts_root = tmp_path / "projects"
    make_transcripts(cfg.transcripts_root, ["sa"])
    out = run_sweep(cfg)
    assert out[0]["status"] == "skipped_no_key" and "no-api-key" in cfg.log.read_text()


def test_sweep_script_dry_run(env, tmp_path):
    e, cfg = env
    conf = json.loads(Path(e["ACE_PLAYBOOK_CONFIG"]).read_text())
    conf["transcripts_root"] = str(tmp_path / "projects")
    Path(e["ACE_PLAYBOOK_CONFIG"]).write_text(json.dumps(conf))
    make_transcripts(tmp_path / "projects", ["sa"])
    p = subprocess.run([sys.executable, str(HOOKS / "nightly_sweep.py"), "--dry-run"],
                       capture_output=True, text=True, env=e)
    assert p.returncode == 0 and json.loads(p.stdout)[0]["status"] == "would_reflect"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_pre_commit_script_blocks_staged_secret_and_passes_clean(env, tmp_path):
    from . import fakes
    e, _ = env
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    script = str(HOOKS / "pre-commit-scrub.sh")
    (repo / "ok.txt").write_text("nothing to see here\n")
    _git(repo, "add", "ok.txt")
    assert subprocess.run([script], cwd=repo, env=e, capture_output=True).returncode == 0
    (repo / "leak.py").write_text(f'KEY = "{fakes.github()}"\n')
    _git(repo, "add", "leak.py")
    p = subprocess.run([script], cwd=repo, env=e, capture_output=True, text=True)
    assert p.returncode == 1 and "github_token" in p.stdout and fakes.github() not in p.stdout
