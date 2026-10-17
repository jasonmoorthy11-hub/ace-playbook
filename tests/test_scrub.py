import pytest

from ace_playbook.scrub import Hit, LESSON, REPO, SECRETS, QuarantineRequired, quarantine, redact, scan
from ace_playbook.scrub.check import scan_files

from . import fakes


@pytest.mark.parametrize("rule", sorted(fakes.ALL))
def test_each_provider_pattern_is_detected(rule):
    text = f"note: {fakes.ALL[rule]()} end"
    rules = {h.rule for h in scan(text, SECRETS)}
    assert rule in rules, rules


def test_hit_never_exposes_match_in_public_metadata():
    secret = fakes.anthropic()
    hits = scan(f"k={secret}", SECRETS)
    assert hits and secret not in repr(hits) and secret not in str(hits[0].public())


def test_redact_removes_secret_text():
    secret = fakes.github()
    text = f"token {secret} used"
    out = redact(text, scan(text, SECRETS))
    assert secret not in out and "[REDACTED:" in out


def test_redact_merges_overlapping_hits_without_leaking_tail():
    secret = fakes.anthropic()
    text = f"x {secret} y"
    start = text.index(secret)
    hits = [Hit("a", "regex", start, start + 20, secret[:20]),
            Hit("b", "regex", start + 10, start + len(secret), secret[10:])]
    out = redact(text, hits)
    assert out == "x [REDACTED:a] y"
    assert secret[-8:] not in out and secret[10:20] not in out


@pytest.mark.parametrize("depth", [1, 2])
def test_base64_decode_depth(depth):
    enc = fakes.b64("sk-" + "ant-" + "FAKE" + fakes.FILL, depth)
    hits = scan(f"payload {enc}", SECRETS)
    assert any(h.layer == "decoded" and h.rule.startswith("decoded:") for h in hits)


def test_base64_depth_three_is_out_of_scope():
    enc = fakes.b64("sk-" + "ant-" + "FAKE" + fakes.FILL, 3)
    rules = {h.rule for h in scan(f"payload {enc}", SECRETS)}
    assert "decoded:anthropic_key" not in rules


def test_entropy_layer_catches_unknown_provider_token():
    tok = "Zq8Xv2Lm9Rt4Bn7Cw1Ks6Hd3Pg5Yj0Fa"
    assert any(h.layer == "entropy" for h in scan(f"value {tok}", SECRETS))


def test_ordinary_prose_and_identifiers_are_clean():
    text = ("Prefer python3 over python. The function normalize_project_key_for_lookup "
            "returns a lowercase string. Commit abcdef1234567890abcdef1234567890abcdef12.")
    assert scan(text, SECRETS) == []


def test_placeholder_assignment_is_not_a_hit():
    assert scan("export API_KEY=your-key-goes-here-123", SECRETS) == []
    assert scan("TOKEN=$SOME_ENV_VARIABLE_VALUE", SECRETS) == []


def test_pii_lint_flags_email_phone_and_home_path_in_lesson_profile():
    home = "/" + "Users/alice/project"
    text = f"mail bob@corp-client.io or 415-555-0199 in {home}"
    rules = {h.rule for h in scan(text, LESSON)}
    assert {"email", "phone", "user_path"} <= rules
    assert scan(text, SECRETS) == []


def test_safe_placeholders_in_pii_lint():
    assert scan("write to dev@example.com", LESSON) == []
    assert scan("path " + "/home" + "/runner/work", LESSON) == []


def test_long_token_lint_only_in_lesson_profile():
    text = "run id 9f86d081884c7d659a2feaa0 failed"
    assert any(h.rule == "long_hex" for h in scan(text, LESSON))
    assert scan(text, REPO) == []


def test_deny_terms():
    assert [h.rule for h in scan("Acme Corp asked", LESSON, deny_terms=["acme corp"])] == ["deny_term"]


def test_quarantine_stores_masked_text_and_metadata(tmp_path):
    secret = fakes.anthropic()
    text = f"Use {secret} for the build"
    hits = scan(text, LESSON)
    path = tmp_path / "quarantine.jsonl"
    rec = quarantine(path, "lesson", {"text": text}, text, hits, "scrub hit", "s1")
    raw = path.read_text()
    assert secret not in raw and rec["hits"][0]["rule"] == "anthropic_key"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_quarantine_required_message_has_no_secret():
    secret = fakes.jwt()
    err = QuarantineRequired(scan(secret, SECRETS), "text")
    assert secret not in str(err)


def _repo(tmp_path, files):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return list(files)


ALLOW = ("fx/",)


def test_precommit_tolerates_fake_marked_secrets_in_allowlisted_dir(tmp_path):
    files = _repo(tmp_path, {"fx/a.txt": fakes.anthropic(), "src/b.py": "x = 1\n"})
    assert scan_files(tmp_path, files, "pre-commit", ALLOW) == []
    audit = scan_files(tmp_path, files, "audit", ALLOW)
    assert [f.path for f in audit] == ["fx/a.txt"]


def test_precommit_rejects_unmarked_secret_even_in_allowlisted_dir(tmp_path):
    real_looking = "sk-" + "ant-" + "Zq8Xv2Lm9Rt4Bn7Cw1Ks6Hd3Pg5Yj0Fa"
    files = _repo(tmp_path, {"fx/a.txt": real_looking})
    assert scan_files(tmp_path, files, "pre-commit", ALLOW)


def test_precommit_rejects_secret_outside_allowlist(tmp_path):
    files = _repo(tmp_path, {"src/a.py": f'KEY = "{fakes.github()}"'})
    found = scan_files(tmp_path, files, "pre-commit", ALLOW)
    assert found and not found[0].allowlisted
    assert fakes.github() not in found[0].render()
