"""Regression tests for the 2026-10-17 stage-1 review: outbound redaction and span handling."""
import json

from ace_playbook import config as cfgmod
from ace_playbook.reflect import ReflectorClient, reflect_transcript
from ace_playbook.scrub import OUTBOUND, SECRETS, redact, scan

from .jsonl import user, write
from .test_reflect import FakeTransport, api

UUID = "3f2b8c1e-" + "9a7d-4e21-" + "b5c3-0d9e8f7a6b5c"
HEX40 = "9f86d081884c7d65" + "9a2feaa0c55ad015" + "a3bf4f1b"
MIXED = "Ab3dE5gH7j" + "K9mN1pQ2rS"
PW = "db_password=" + "hunter2hunter2xx"
MAIL = "jane.doe" + "@clientcorp.com"


def redacted(text, profile=OUTBOUND):
    return redact(text, scan(text, profile))


def test_outbound_masks_bare_uuid_hex_mixed_token_assignment_email_and_home_path():
    home = "/" + "Users/alicesmith/project"
    text = f"id {UUID} sha {HEX40} pass {MIXED} {PW} mail {MAIL} cwd {home}/x"
    out = redacted(text)
    for secret in (UUID, HEX40, MIXED, "hunter2hunter2xx", MAIL, "alicesmith"):
        assert secret not in out, secret


def test_outbound_leaves_ordinary_prose_alone():
    text = "Please fix the deploy script and run pytest on tests/test_reflect.py"
    assert scan(text, OUTBOUND) == []


def test_secrets_profile_alone_does_not_cover_these():
    assert UUID in redacted(f"key {UUID}", SECRETS)


def test_reflect_request_body_has_none_of_the_probe_secrets(tmp_path):
    key = tmp_path / "k"
    key.write_text("x")
    cfg = cfgmod.Config(data_dir=tmp_path / "data", api_key_path=key, holdout_enabled=False)
    tp = write(tmp_path / "s.jsonl", [
        user(f"hello, the key is {UUID} and sha {HEX40}"),
        user(f"{PW} contact {MAIL} thanks for fixing this deploy"),
    ])
    t = FakeTransport(api(json.dumps({"insights": [], "tags": []})))
    reflect_transcript(cfg, tp, ReflectorClient(cfg, transport=t, sleep=lambda s: None))
    sent = json.dumps(t.calls[0]["body"])
    for secret in (UUID, HEX40, "hunter2hunter2xx", MAIL):
        assert secret not in sent, secret


def test_bearer_charset_covers_slash_plus_tilde():
    tail = "XYZsecretTAIL98765+more~end"
    text = "Authorization: " + "Bearer " + "abcDEF1234567890abcdef/" + tail
    out = redacted(text, SECRETS)
    assert "TAIL98765" not in out and "more~end" not in out


def test_entropy_hit_extending_past_kept_hit_is_not_dropped():
    from ace_playbook.scrub.engine import Hit, _dedupe
    regex = Hit("bearer_token", "regex", 0, 10, "x" * 10)
    entropy = Hit("high_entropy", "entropy", 5, 20, "y" * 15)
    inside = Hit("high_entropy", "entropy", 2, 8, "z" * 6)
    kept = _dedupe([regex, entropy, inside])
    assert entropy in kept and inside not in kept
    text = "0123456789abcdefghij"
    assert redact(text, kept) == "[REDACTED:bearer_token]"
