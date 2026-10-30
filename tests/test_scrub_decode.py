"""`scrub.engine.try_decode`: both base64 alphabets are decoded explicitly and strictly, so Python 3.12-3.15 raise no warning
(3.15 emits a FutureWarning for a `+` handed to the URL-safe decode, and for any non-alphabet byte the lenient decode drops)."""

import base64
import string
import warnings

import pytest

from ace_playbook.scrub.engine import try_decode

PLAIN = "secret>>>value???token"                      # `>>>` encodes to `Pj4+` and `???` to `Pz8/`


def _std(text: str) -> str:
    return base64.b64encode(text.encode()).decode().rstrip("=")


def _url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _quiet(token: str) -> str | None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return try_decode(token)


def test_the_fixtures_use_the_characters_that_differ_between_the_alphabets():
    assert "+" in _std(PLAIN) and "/" in _std(PLAIN) and "-" in _url(PLAIN) and "_" in _url(PLAIN)


def test_a_standard_base64_secret_decodes_without_a_warning():
    assert _quiet(_std(PLAIN)) == PLAIN


def test_a_url_safe_base64_secret_decodes_without_a_warning():
    assert _quiet(_url(PLAIN)) == PLAIN


def test_an_alphanumeric_token_decodes_the_same_in_both_alphabets():
    text = "plain ascii secret value 0123456789"
    token = _std(text)
    assert set(token) <= set(string.ascii_letters + string.digits) and _quiet(token) == text


def test_a_mixed_alphabet_token_raises_no_warning():
    mixed = _std(PLAIN)[:12] + "-" + _std(PLAIN)[12:]
    assert "+" in mixed and "-" in mixed
    assert _quiet(mixed) is None or isinstance(_quiet(mixed), str)


@pytest.mark.parametrize("junk", ["", "!!!!", "not base64 at all", "A", "====", "@@@@AAAA", "\x00\x01\x02\x03", "é" * 8])
def test_junk_is_none_without_a_warning(junk):
    assert _quiet(junk) is None


def test_a_short_or_unprintable_decode_is_still_refused():
    assert _quiet(_std("short")) is None
    assert _quiet(base64.b64encode(bytes(range(1, 40))).decode()) is None
