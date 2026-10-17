import json
import shutil
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.clean import clean_file
from ace_playbook.fixtures.generate import generate
from ace_playbook.inject import inert_reason, injector
from ace_playbook.reflect import ReflectorClient, reflect_transcript
from ace_playbook.scrub import LESSON, SECRETS, scan
from ace_playbook.store import DeltaLog

from .test_reflect import FakeTransport, api

FROZEN = Path(__file__).resolve().parent.parent / "ace_playbook" / "fixtures" / "frozen"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("corpus")
    generate(1, out)
    return out


def test_generator_is_deterministic_and_seed_sensitive(tmp_path, corpus):
    a = generate(1, tmp_path / "a")
    assert a["sha256"] == json.loads((corpus / "MANIFEST.json").read_text())["sha256"]
    assert generate(2, tmp_path / "b")["sha256"] != a["sha256"]


def test_regenerating_over_existing_output_is_idempotent(tmp_path):
    first = generate(1, tmp_path)["sha256"]
    assert generate(1, tmp_path)["sha256"] == first


def test_frozen_copy_matches_seed_1(corpus):
    frozen = json.loads((FROZEN / "MANIFEST.json").read_text())
    fresh = json.loads((corpus / "MANIFEST.json").read_text())
    assert frozen == fresh, "frozen fixtures drifted: rerun the generator with --seed 1 --out <frozen>"
    for rel, digest in frozen["sha256"].items():
        import hashlib
        assert hashlib.sha256((FROZEN / rel).read_bytes()).hexdigest() == digest


def test_store_fixture_is_valid(corpus):
    log = DeltaLog(corpus / "deltas.jsonl")
    assert log.verify() > 40
    state = log.state()
    assert len(state) >= 30 and sum(s["pinned"] for s in state.values()) == 10
    statuses = {s["status"] for s in state.values()}
    assert {"active", "provisional", "superseded", "retired", "compiled"} <= statuses
    scopes = {s["scope_key"] for s in state.values()}
    assert {"acme-widgets", "globex-api", "initech-site", ""} <= scopes


def test_no_real_data_markers(corpus):
    blob = "".join(p.read_text() for p in corpus.rglob("*") if p.is_file())
    for needle in ("think", "samplecloset", "railway.app"):
        assert needle not in blob.lower() or needle == "think" and "thinking" in blob.lower()


def test_planted_secrets_are_found_in_transcripts(corpus):
    text = (corpus / "transcripts" / "session-04-secrets.jsonl").read_text()
    rules = {h.rule for h in scan(text, LESSON)}
    assert {"anthropic_key", "github_token", "railway_token", "email", "user_path"} <= rules
    assert any(r.startswith("decoded:") for r in rules)
    clean_text = (corpus / "transcripts" / "session-01-grounded.jsonl").read_text()
    assert scan(clean_text, SECRETS) == []


def test_candidate_expectations(corpus):
    rows = [json.loads(ln) for ln in (corpus / "planted" / "candidates.jsonl").read_text().splitlines()]
    assert {r["expect"] for r in rows} == {"mint", "inert_reject", "quarantine"}
    for r in rows:
        hits = scan(r["text"], LESSON)
        outcome = "quarantine" if hits else ("inert_reject" if inert_reason(r["text"], "x y z") else "mint")
        assert outcome == r["expect"], (r["why"], outcome)


def test_cleaner_on_fixture_transcripts(corpus):
    noisy = clean_file(corpus / "transcripts" / "session-02-noisy.jsonl")
    assert noisy.signals.hook_blocks == 1 and noisy.signals.user_corrections >= 1
    assert "Boot context" not in noisy.body and "private reasoning" not in noisy.body
    assert "side chatter" not in noisy.body and "[result omitted: 6000 chars]" in noisy.body
    rep = clean_file(corpus / "transcripts" / "session-03-repeated-failure.jsonl")
    assert rep.signals.repeated_failing_commands == [("npm run build", 3)]


def test_injector_on_fixture_store(corpus, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(corpus / "deltas.jsonl", data / "deltas.jsonl")
    cfg = cfgmod.Config(data_dir=data, engagements_path=corpus / "engagements.json", holdout_enabled=False)
    inj = injector.run(cfg, "demo-1", "/work/acme/service")
    state = DeltaLog(cfg.deltas).state()
    assert inj.key == "acme-widgets" and inj.shown and len(inj.block) <= 7000
    for lid in inj.shown:
        s = state[lid]
        assert s["pinned"] and s["status"] == "active" and s["scope_key"] in ("", "acme-widgets")
    assert not inj.excluded


def run_mock_reflection(corpus, tmp_path, name):
    cfg = cfgmod.Config(data_dir=tmp_path / name, api_key_path=tmp_path / "k", holdout_enabled=False)
    (tmp_path / "k").write_text("sk-" + "ant-" + "FAKEFAKEFAKEFAKEFAKEFAKE1234")
    canned = (corpus / "reflector_responses" / f"{name}.json").read_text()
    t = FakeTransport(api(canned))
    res = reflect_transcript(cfg, corpus / "transcripts" / f"{name}.jsonl",
                             ReflectorClient(cfg, transport=t, sleep=lambda s: None))
    return cfg, res, t


def test_mock_reflection_grounded_session(corpus, tmp_path):
    cfg, res, _ = run_mock_reflection(corpus, tmp_path, "session-01-grounded")
    assert res["status"] == "ok" and len(res["minted"]) == 1 and res["rejected"] == ["quote_not_in_transcript"]
    (lesson,) = DeltaLog(cfg.deltas).state().values()
    assert lesson["scope_key"] == "acme-widgets" and lesson["evidence"][0]["origin"] == "user_message"


def test_mock_reflection_blocks_injection_and_secrets(corpus, tmp_path):
    cfg, res, _ = run_mock_reflection(corpus, tmp_path, "session-05-injection")
    assert res["minted"] == [] and res["rejected"] == ["no_user_evidence"]
    assert not cfg.deltas.exists() and cfg.quarantine.exists()
    cfg, res, t = run_mock_reflection(corpus, tmp_path, "session-04-secrets")
    assert res["minted"] == [] and res["rejected"] == ["scrub_hit"] and res["outbound_redactions"] >= 3
    sent = t.calls[0]["body"]["messages"][0]["content"]
    assert not scan(sent, SECRETS) or all(h.rule.startswith("decoded") for h in scan(sent, SECRETS))
    assert "sk-" + "ant-FAKE" not in sent and "sk-" + "ant-FAKE" not in cfg.quarantine.read_text()
