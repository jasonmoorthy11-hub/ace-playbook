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
    (tmp_path / "home").mkdir(exist_ok=True)                     # the child's HOME: its `~` never reaches the operator's key dir
    e = {**os.environ, "ACE_PLAYBOOK_CONFIG": str(conf), "ACE_PLAYBOOK_HOME": str(ROOT),
         "ACE_PYTHON": sys.executable, "HOME": str(tmp_path / "home")}
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
    assert p.returncode == 0 and json.loads(p.stdout)["sweep"][0]["status"] == "would_reflect"


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


def test_prune_queue_rewrites_0600_even_under_umask_000(env, tmp_path):
    _, cfg = env
    cfg.queue.parent.mkdir(parents=True, exist_ok=True)
    cfg.queue.write_text(json.dumps({"session_id": "zz", "transcript_path": str(tmp_path / "gone")}) + "\n")
    os.chmod(cfg.queue, 0o644)
    old = os.umask(0)
    try:
        prune_queue(cfg)
    finally:
        os.umask(old)
    assert cfg.queue.stat().st_mode & 0o777 == 0o600
    assert [e["session_id"] for e in read_jsonl(cfg.queue)] == ["zz"]


# ------------------------------------------------------------------- shadow.sh (docs/design/compiler.md 7, 9)
SYSTEM_PY = "/usr/bin/python3"
needs_system_py = pytest.mark.skipif(not os.access(SYSTEM_PY, os.X_OK), reason="no /usr/bin/python3")


@pytest.fixture
def shadow_env(tmp_path):
    from . import compile_support as cs
    w = cs.real_world(tmp_path)
    return w, {**os.environ, "ACE_PLAYBOOK_CONFIG": w.conf, "ACE_PLAYBOOK_HOME": str(ROOT), "ACE_PYTHON": sys.executable}


def run_shadow(env, payload, *args, cwd=None, raw=None):
    data = raw if raw is not None else json.dumps(payload)
    return subprocess.run([str(HOOKS / "shadow.sh"), *args], input=data, capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=30)


def rm_payload(command="rm -rf /w/src"):
    from . import compile_support as cs
    return cs.pre_payload(command)


def test_shadow_script_is_the_design_block_verbatim_and_never_execs():
    doc = (ROOT / "docs" / "design" / "compiler.md").read_text()
    head = doc.index("## 7. shadow.py and hooks/shadow.sh")
    block = doc[doc.index("```bash\n", head) + len("```bash\n"):]
    block = block[:block.index("```")]
    script = HOOKS / "shadow.sh"
    text = script.read_text()
    assert text == block and os.access(script, os.X_OK)
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert code[-1] == "exit 0" and not any(ln.split()[0] == "exec" for ln in code)
    assert '[ -n "$ACE_HOME" ] || exit 0' in text and 'cd "$ACE_HOME" || exit 0' in text
    assert "-I -P -c pass" in text and "ace_playbook.compile.shadow import main" in text


def test_shadow_script_evaluates_a_payload_and_prints_nothing_in_observe_mode(shadow_env):
    from . import compile_support as cs
    w, e = shadow_env
    p = run_shadow(e, rm_payload(), "pre")
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")
    assert len(cs.shadow_rows(w.data)) == 1
    p = run_shadow(e, rm_payload("rm -rf /tmp/scratch/x"), "pre")
    assert p.returncode == 0 and len(cs.shadow_rows(w.data)) == 1


@needs_system_py
def test_shadow_script_under_the_system_python_exits_0_and_still_evaluates(shadow_env):
    from . import compile_support as cs
    w, e = shadow_env
    p = run_shadow({**e, "ACE_PYTHON": SYSTEM_PY}, rm_payload(), "pre")
    assert (p.returncode, p.stdout) == (0, "")
    assert len(cs.shadow_rows(w.data)) == 1


def test_shadow_script_with_no_argument_or_a_missing_interpreter_exits_0(shadow_env):
    from . import compile_support as cs
    w, e = shadow_env
    assert run_shadow(e, rm_payload()).returncode == 0
    p = run_shadow({**e, "ACE_PYTHON": "/nonexistent/python3"}, rm_payload(), "pre")
    assert (p.returncode, p.stdout) == (0, "")
    assert cs.shadow_rows(w.data) == []


@pytest.mark.parametrize("module", ["raise RuntimeError('boom')\n",
                                    "def main(argv):\n    print('junk')\n    return 2\n",
                                    "import sys\ndef main(argv):\n    sys.exit(2)\n"])
def test_shadow_script_exits_0_when_the_module_crashes_or_returns_2(tmp_path, module):
    home = tmp_path / "home"
    (home / "ace_playbook" / "compile").mkdir(parents=True)
    (home / "ace_playbook" / "__init__.py").write_text("")
    (home / "ace_playbook" / "compile" / "__init__.py").write_text("")
    (home / "ace_playbook" / "compile" / "shadow.py").write_text(module)
    e = {**os.environ, "ACE_PLAYBOOK_HOME": str(home), "ACE_PYTHON": sys.executable}
    p = run_shadow(e, rm_payload(), "pre")
    assert p.returncode == 0                                   # exit 2 would mean "block" to the harness


@pytest.mark.parametrize("command", [None, "rm -rf /w/src", "rm -rf /tmp/scratch/x", "echo $(id) `id`"])
def test_shadow_script_stdout_is_empty_or_valid_json(shadow_env, command):
    w, e = shadow_env
    p = run_shadow(e, None, "pre", raw="}{ garbage") if command is None else run_shadow(e, rm_payload(command), "pre")
    assert p.returncode == 0 and (p.stdout == "" or isinstance(json.loads(p.stdout), dict))


@pytest.mark.parametrize("interpreter", [sys.executable, pytest.param(SYSTEM_PY, marks=needs_system_py)])
def test_shadow_script_never_imports_modules_planted_in_the_working_directory(shadow_env, tmp_path, interpreter):
    from . import compile_support as cs
    w, e = shadow_env
    cwd = tmp_path / "project"
    (cwd / "ace_playbook" / "compile").mkdir(parents=True)
    marker = tmp_path / "marker"
    trap = f"open({str(marker)!r}, 'a').write('imported')\n"
    (cwd / "json.py").write_text(trap)
    (cwd / "ace_playbook" / "__init__.py").write_text(trap)
    (cwd / "ace_playbook" / "compile" / "shadow.py").write_text(trap + "def main(argv):\n    return 2\n")
    p = run_shadow({**e, "ACE_PYTHON": interpreter}, rm_payload(), "pre", cwd=cwd)
    assert p.returncode == 0 and not marker.exists()
    assert len(cs.shadow_rows(w.data)) == 1                     # the real module ran, not the planted one


def test_shadow_script_does_not_expand_payload_text_in_the_shell(shadow_env, tmp_path):
    w, e = shadow_env
    marker = tmp_path / "pwned"
    p = run_shadow(e, rm_payload(f"rm -rf $(touch {marker}) `touch {marker}`"), "pre")
    assert p.returncode == 0 and p.stdout == "" and p.stderr == "" and not marker.exists()
