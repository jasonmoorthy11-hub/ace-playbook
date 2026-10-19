"""S2.4 health line: the sweep writes health.json, the injector warns, inject --health prints it."""

import io
import json
import os
import stat
import subprocess
import sys

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.inject import build, inert_reason
from ace_playbook.inject import __main__ as hook
from ace_playbook.reflect import ReflectorClient
from ace_playbook.store import DeltaLog
from ace_playbook.sweep import run_sweep

from . import fakes
from .helpers import GLOBAL, add_draft
from .test_hooks import HOOKS, env, make_transcripts  # noqa: F401 - env is a fixture
from .test_reflect import FakeTransport, api

WARN = "- ace-playbook: nightly sweep failed {n} times in a row; see reflect.log"
GOOD = api('{"insights":[],"tags":[]}')


@pytest.fixture
def cfg(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-" + "ant-" + "FAKEFAKEFAKEFAKEFAKEFAKE1234")
    return cfgmod.Config(data_dir=tmp_path / "data", api_key_path=key, holdout_enabled=False,
                         transcripts_root=tmp_path / "projects")


def health(cfg):
    return json.loads((cfg.data_dir / "health.json").read_text())


def sweep_once(cfg, *responses, names=("sa",)):
    """One sweep over fresh transcripts; each response is one fake API reply."""
    make_transcripts(cfg.transcripts_root, list(names))
    client = ReflectorClient(cfg, transport=FakeTransport(*responses), sleep=lambda s: None)
    return run_sweep(cfg, client, max_n=5)


# ------------------------------------------------------------------- sweep
def test_sweep_success_writes_health(cfg):
    out = sweep_once(cfg, GOOD)
    assert out[0]["status"] == "ok"
    h = health(cfg)
    assert set(h) == {"last_run", "ok", "consecutive_failures", "last_error"}
    assert h["ok"] is True and h["consecutive_failures"] == 0 and h["last_error"] == ""
    assert h["last_run"].endswith("Z") and len(h["last_run"]) == 20


def test_health_file_mode_is_0600(cfg):
    sweep_once(cfg, GOOD)
    assert stat.S_IMODE(os.stat(cfg.data_dir / "health.json").st_mode) == 0o600
    (cfg.data_dir / "health.json").chmod(0o644)
    sweep_once(cfg, GOOD, names=("sb",))  # a rewrite restores the mode
    assert stat.S_IMODE(os.stat(cfg.data_dir / "health.json").st_mode) == 0o600


def test_exception_records_scrubbed_truncated_error_and_still_raises(cfg):
    secret = fakes.anthropic()
    make_transcripts(cfg.transcripts_root, ["sa"])
    t = FakeTransport(RuntimeError(f"boom {secret} " + "x" * 400))
    client = ReflectorClient(cfg, transport=t, sleep=lambda s: None)
    with pytest.raises(RuntimeError):  # the sweep's failure behaviour is unchanged
        run_sweep(cfg, client)
    h = health(cfg)
    assert h["ok"] is False and h["consecutive_failures"] == 1
    assert 0 < len(h["last_error"]) <= 200 and h["last_error"].startswith("RuntimeError: boom")
    assert secret not in h["last_error"] and fakes.FILL not in h["last_error"]


def test_failures_increment_then_reset_after_success(cfg):
    for expected in (1, 2):
        make_transcripts(cfg.transcripts_root, [f"f{expected}"])
        client = ReflectorClient(cfg, transport=FakeTransport(RuntimeError("down")), sleep=lambda s: None)
        with pytest.raises(RuntimeError):
            run_sweep(cfg, client)
        assert health(cfg)["consecutive_failures"] == expected
    sweep_once(cfg, GOOD, GOOD, GOOD, names=("good",))  # f1, f2 and good are all pending
    h = health(cfg)
    assert h["ok"] is True and h["consecutive_failures"] == 0 and h["last_error"] == ""


def test_usage_cap_hit_counts_as_failure(cfg):
    body = {"type": "error", "error": {"type": "invalid_request_error",
                                       "message": "You have reached your workspace API usage limits"}}
    out = sweep_once(cfg, (400, json.dumps(body).encode()))
    assert out[0]["status"] == "failed_final"
    h = health(cfg)
    assert h["ok"] is False and h["consecutive_failures"] == 1
    assert "usage limits" in h["last_error"] and len(h["last_error"]) <= 200


def test_no_candidates_is_success_and_dry_run_writes_nothing(cfg):
    assert run_sweep(cfg, dry_run=True) == []
    assert not (cfg.data_dir / "health.json").exists()
    assert run_sweep(cfg) == []
    assert health(cfg)["ok"] is True


def test_health_write_failure_never_changes_the_sweep(cfg):
    (cfg.data_dir / "health.json").mkdir(parents=True)  # os.replace onto a directory fails
    out = sweep_once(cfg, GOOD)
    assert out[0]["status"] == "ok"


def test_sweep_script_exit_code_zero_and_health_written(env):  # noqa: F811
    e, cfg = env
    p = subprocess.run([sys.executable, str(HOOKS / "nightly_sweep.py")], capture_output=True,
                       text=True, env=e)
    assert p.returncode == 0 and json.loads(p.stdout) == []
    assert health(cfg)["ok"] is True


# ---------------------------------------------------------------- injector
def put_health(cfg, **over):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    rec = {"last_run": "2026-10-19T03:00:00Z", "ok": False, "consecutive_failures": 3,
           "last_error": "boom", **over}
    (cfg.data_dir / "health.json").write_text(json.dumps(rec))


def seeded(cfg, n=1):
    log = DeltaLog(cfg.deltas)
    for i in range(n):
        log.append(add_draft(f"Pinned global lesson number {i}", GLOBAL, status="active", pinned=True),
                   ts="2026-01-01T00:00:00Z")


def test_injector_warns_at_three_not_at_two(cfg, tmp_path):
    seeded(cfg)
    put_health(cfg, consecutive_failures=2)
    assert "nightly sweep failed" not in build(cfg, "s1", str(tmp_path)).block
    put_health(cfg, consecutive_failures=3)
    assert WARN.format(n=3) in build(cfg, "s1", str(tmp_path)).block.splitlines()
    put_health(cfg, consecutive_failures=11)
    assert WARN.format(n=11) in build(cfg, "s1", str(tmp_path)).block


def test_warning_line_passes_inert_reason():
    for n in (3, 7, 120):
        assert inert_reason(WARN.format(n=n)) is None


def test_warning_counts_toward_the_cap(cfg, tmp_path):
    seeded(cfg, 3)
    cfg.inject_max_chars = 600
    cfg.max_global = 3
    put_health(cfg)
    inj = build(cfg, "s1", str(tmp_path))
    assert len(inj.block) <= 600 and WARN.format(n=3) in inj.block and inj.dropped_cap


@pytest.mark.parametrize("raw", ["{", "[]", '"x"', "{}", '{"consecutive_failures": "9"}',
                                 '{"consecutive_failures": true}', '{"consecutive_failures": 1e9999}',
                                 '{"consecutive_failures": -5}', '{"consecutive_failures": 3.5}'])
def test_malformed_health_adds_nothing(cfg, tmp_path, raw):
    seeded(cfg)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "health.json").write_text(raw)
    assert "nightly sweep failed" not in build(cfg, "s1", str(tmp_path)).block


def test_unreadable_health_adds_nothing(cfg, tmp_path):
    seeded(cfg)
    (cfg.data_dir / "health.json").mkdir(parents=True, exist_ok=True)  # reading raises IsADirectoryError
    assert "nightly sweep failed" not in build(cfg, "s1", str(tmp_path)).block


def test_warning_never_reads_the_api_key(cfg, tmp_path):
    seeded(cfg)
    put_health(cfg)
    cfg.api_key_path.unlink()
    assert WARN.format(n=3) in build(cfg, "s1", str(tmp_path)).block


# --------------------------------------------------------------------- CLI
def run_health_cli(monkeypatch, cfg, capsys):
    monkeypatch.setattr(hook.cfgmod, "load", lambda *a, **k: cfg)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not read"))
    rc = hook.main(["--health"])
    return rc, capsys.readouterr().out


def test_cli_health_prints_the_file(cfg, monkeypatch, capsys):
    put_health(cfg, consecutive_failures=4)
    rc, out = run_health_cli(monkeypatch, cfg, capsys)
    assert rc == 0 and json.loads(out)["consecutive_failures"] == 4 and "\n  " in out  # pretty


def test_cli_health_without_record(cfg, monkeypatch, capsys):
    rc, out = run_health_cli(monkeypatch, cfg, capsys)
    assert rc == 0 and out == "no health record\n"
    cfg.data_dir.mkdir(parents=True)
    (cfg.data_dir / "health.json").write_text("{")
    assert run_health_cli(monkeypatch, cfg, capsys)[1] == "no health record\n"


def test_cli_health_touches_neither_key_nor_store(cfg, monkeypatch, capsys):
    put_health(cfg)
    cfg.api_key_path.unlink()
    rc, _ = run_health_cli(monkeypatch, cfg, capsys)
    assert rc == 0 and not cfg.deltas.exists() and not cfg.sqlite.exists() and not cfg.injections.exists()
