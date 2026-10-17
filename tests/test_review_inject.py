"""Regression tests for the 2026-10-17 stage-1 review: injector, config and hook inputs."""
import io
import json
import sys
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.inject import build
from ace_playbook.inject import __main__ as hook
from ace_playbook.reflect import ReflectorClient, reflect_transcript
from ace_playbook.store import DeltaLog

from .helpers import GLOBAL, add_draft, seeded, tamper
from .jsonl import user, write
from .test_reflect import FakeTransport, api

ROOT = Path(__file__).resolve().parent.parent
UUID = "3f2b8c1e-" + "9a7d-4e21-" + "b5c3-0d9e8f7a6b5c"
QUOTE = "we always deploy from the release branch only"
BASE = [user("hello, can you fix the deploy please"), user(QUOTE)]


def insight(text="Deploy only from the release branch", quote=QUOTE, **kw):
    base = {"text": text, "kind": "rule", "scope": "project", "applies_when": "deploying",
            "evidence": [{"quote": quote}]}
    base.update(kw)
    return base


@pytest.fixture
def cfg(tmp_path):
    key = tmp_path / "k"
    key.write_text("x")
    return cfgmod.Config(data_dir=tmp_path / "data", api_key_path=key, holdout_enabled=False)


def reflect(cfg, tmp_path, records, insights, *responses, name="s.jsonl", force=False):
    tp = write(tmp_path / name, records)
    body = api(json.dumps({"insights": insights, "tags": []}))
    t = FakeTransport(*(responses or (body,)))
    res = reflect_transcript(cfg, tp, ReflectorClient(cfg, transport=t, sleep=lambda s: None), force=force)
    return res, t


def quarantine_text(cfg):
    return cfg.quarantine.read_text() if cfg.quarantine.exists() else ""




# ---- hash chain at injection
def test_injector_injects_nothing_when_chain_is_broken(cfg, tmp_path):
    seeded(cfg)
    assert build(cfg, "s1", str(tmp_path)).block
    tamper(cfg)
    inj = build(cfg, "s2", str(tmp_path))
    assert inj.block == "" and inj.refused


# ------------------------------------------------------------- ranked mode
def test_ranked_mode_never_injects_provisional(cfg, tmp_path):
    cfg.inject_mode = "ranked"
    log = DeltaLog(cfg.deltas)
    prov = add_draft("Provisional lesson from a fresh session", GLOBAL, status="provisional", pinned=False)
    log.append(prov, ts="2026-01-01T00:00:00Z")
    assert build(cfg, "s1", str(tmp_path)).shown == []


# ------------------------------------------------------------------ config
def test_holdout_is_off_by_default_with_no_public_seed():
    c = cfgmod.Config()
    assert c.holdout_enabled is False and c.holdout_seed == ""


def test_holdout_requires_private_seed_matching_committed_hash():
    import hashlib
    seed = "private-seed-" + "0123456789abcdef"
    cfgmod.Config(holdout_enabled=True, holdout_seed=seed).check_holdout()
    with pytest.raises(ValueError):
        cfgmod.Config(holdout_enabled=True).check_holdout()
    good = hashlib.sha256(seed.encode()).hexdigest()
    cfgmod.Config(holdout_enabled=True, holdout_seed=seed, holdout_seed_sha256=good).check_holdout()
    with pytest.raises(ValueError):
        cfgmod.Config(holdout_enabled=True, holdout_seed=seed, holdout_seed_sha256="0" * 64).check_holdout()


def test_config_clamps_budget_and_rate():
    for bad in ({"inject_max_chars": 7001}, {"inject_max_chars": 10}, {"holdout_rate": 1.5},
                {"holdout_rate": -0.1}):
        with pytest.raises(ValueError):
            cfgmod.from_dict(bad)


def test_injector_budget_is_clamped_even_if_mutated(cfg, tmp_path):
    cfg.inject_max_chars = 50_000
    log = DeltaLog(cfg.deltas)
    for i in range(60):
        log.append(add_draft(f"Lesson {i}: " + "word " * 70, GLOBAL, status="active", pinned=True),
                   ts="2026-01-01T00:00:00Z")
    cfg.max_global = 60
    assert len(build(cfg, "s1", str(tmp_path)).block) <= 7000


# ------------------------------------------------------------- hook inputs
def test_hook_skips_injection_and_ledger_without_session_id(cfg, tmp_path, monkeypatch, capsys):
    seeded(cfg)
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(cfg.data_dir)}))
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(conf))
    for payload in ({"cwd": str(tmp_path)}, {"session_id": "  ", "cwd": str(tmp_path)}):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        assert hook.main(["start"]) == 0
    assert capsys.readouterr().out == "" and not cfg.injections.exists()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"last_assistant_message": "[L-12345678]"})))
    assert hook.main(["cite"]) == 0 and not cfg.citations.exists()


