"""`scrub.engine.try_decode` keeps main's lenient Python 3.12 decode VALUE by explicit emulation, with no lenient stdlib path (3.15 warns).
TABLE is frozen from main's function on 3.12 (the contract); the fuzz test compares against main's exact code where that interpreter runs."""

import base64
import random
import string
import sys
import warnings

import pytest

from ace_playbook.scrub import SECRETS, scan
from ace_playbook.scrub.engine import try_decode

K = "key>>> AKIA" "ZZZZ1111YYYY2222"          # an AWS-key-shaped text, split so the source itself is no finding
TABLE = [
    ('c2VjcmV0Pj4+' 'dmFsdWU/Pz90' 'b2tlbg==', 'secret>>>val' 'ue???token'),
    ('c2VjcmV0Pj4+' 'dmFsdWU/Pz90' 'b2tlbg', 'secret>>>val' 'ue???token'),
    ('c2VjcmV0Pj4-' 'dmFsdWU_Pz90' 'b2tlbg==', 'secret>>>val' 'ue???token'),
    ('c2VjcmV0Pj4-' 'dmFsdWU_Pz90' 'b2tlbg', 'secret>>>val' 'ue???token'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('a2V5Pj4+IEFL' 'SUFaWlpaMTEx' 'MVlZWVkyMjIy' 'Pw==', K + '?'),
    ('a2V5Pj4-IEFL' 'SUFaWlpaMTEx' 'MVlZWVkyMjIy' 'Pw==', K + '?'),
    ('a2V5Pj4-IEFL' 'SUFaWlpaMTEx' 'MVlZWVkyMjIy' 'Pw', K + '?'),
    ('a2V5Pj4+IEFL' 'SUFaWlpaMTEx' 'MVlZWVkyMjIy' 'Pw==_', K + '?'),
    ('a2V5Pj4-IEFL' 'SUFaWlpaMTEx' 'MVlZWVkyMjIy' 'Pw==_', K + '?'),
    ('a2V5Pj4+IEFL' 'SUFaWlpaMTEx' 'MVlZWVkyMjIy' 'Pw_', K + '?\x0f'),
    ('a2V5Pj4-IEFL' 'SUFaWlpaMTEx' 'MVlZWVkyMjIy' 'Pw==-', K + '?'),
    ('-a2V5Pj4-IEF' 'LSUFaWlpaMTE' 'xMVlZWVkyMjI' 'yPw==', None),
    ('_a2V5Pj4+IEF' 'LSUFaWlpaMTE' 'xMVlZWVkyMjI' 'yPw==', K + '?'),
    ('c2VjcmV0Pj4+' '-dmFsdWU/Pz9' '0b2tlbg==', 'secret>>>val' 'ue???token'),
    ('c2VjcmV0Pj_4' '+dmFsdWU/Pz9' '0b2tlbg==', 'secret>>>val' 'ue???token'),
    ('c2VjcmV0Pj4-' '+dmFsdWU_Pz9' '0b2tlbg==', 'secret>>>مՔѽ'),
    ('c2VjcmV0/Pj4' '-dmFsdWU_Pz9' '0b2tlbg==', None),
    ('c2VjcmV0Pj4+' 'dmFsdWU/Pz90' 'b2tlbg==c2Vj' 'cmV0Pj4-dmFs' 'dWU_Pz90b2tl' 'bg==', 'secret>>>val' 'ue???token'),
    ('aGVsbG8g d29' 'ybGQgc2VjcmV' '0IHZhbHVl', 'hello world ' 'secret value'),
    ('aGVsbG8gd29y' ' bGQgc2Vj cm' 'V0IHZhbHVl', 'hello world ' 'secret value'),
    (' bG9uZyBzZWN' 'yZXQgdmFsdWU' 'gd2l0aCBwbGF' 'pbiB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0 ', 'long secret ' 'value with p' 'lain text'),
    ('bG9uZyBz\nZWN' 'yZXQgdmFsdWU' 'gd2l0aCBwbGF' 'pbiB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('bG9uZyBz=ZWN' 'yZXQgdmFsdWU' 'gd2l0aCBwbGF' 'pbiB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('bG9uZyBzZWNy' 'ZXQg==dmFsdW' 'Ugd2l0aCBwbG' 'FpbiB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('bG9u=ZyBzZWN' 'yZXQgdmFsdWU' 'gd2l0aCBwbGF' 'pbiB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('bG=9uZyBzZWN' 'yZXQgdmFsdWU' 'gd2l0aCBwbGF' 'pbiB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('bG9uZy=BzZWN' 'yZXQgdmFsdWU' 'gd2l0aCBwbGF' 'pbiB0ZXh0', 'long secret ' 'value with p' 'lain text'),
    ('c2VjcmV0Pj4-' '=dmFsdWU_Pz9' '0b2tlbg==', 'secret>>>val' 'ue???token'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0A', None),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0AB', 'long secret ' 'value with p' 'lain text\x00'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0ABC', 'long secret ' 'value with p' 'lain text\x00\x10'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh', 'long secret ' 'value with p' 'lain tex'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZX', 'long secret ' 'value with p' 'lain te'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0Z', None),
    ('YSBsb25nIGVu' 'b3VnaCBzZWNy' 'ZXQgdmFsdWUh' 'IQ==', 'a long enoug' 'h secret val' 'ue!!'),
    ('YSBsb25nIGVu' 'b3VnaCBzZWNy' 'ZXQgdmFsdW', 'a long enoug' 'h secret val' 'u'),
    ('YSBsb25nIGVu' 'b3VnaCBzZWNy' 'ZXQgdmFsdWU', 'a long enoug' 'h secret val' 'ue'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0!!!!', 'long secret ' 'value with p' 'lain text'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0==ju' 'nk', 'long secret ' 'value with p' 'lain text'),
    ('bG9uZyBzZWNy' 'ZXQgdmFsdWUg' 'd2l0aCBwbGFp' 'biB0ZXh0=jun' 'k', 'long secret ' 'value with p' 'lain text'),
    ('YWJjZGVmZ2hp' 'amtsbW4==jun' 'k=more', 'abcdefghijklmn'),
    ('YWJjZGVmZ2hp' 'amtsbQ===zzz' 'z', 'abcdefghijklm'),
    ('YWJjZGVmZ2hp' 'amtsbW4=A=A=' '=', 'abcdefghijklmn'),
    ('b!G!9!u!Z!y!' 'B!z!Z!W!N!y!' 'Z!X!Q!g!d!m!' 'F!s!d!W!U!g!' 'd!2!l!0!a!C!' 'B!w!b!G!F!p!' 'b!i!B!0!Z!X!' 'h!0!', 'long secret value with plain text'),
    ('AAAAAAAAAAAA' 'AAAAAAAAAAAA', None),
    ('////////////' '////////', None),
    ('++++++++++++++++', None),
    ('____________' '________', None),
    ('------------' '--------', None),
    ('!!!!', None),
    ('not base64 at all', None),
    ('====', None),
    ('@@@@AAAA', None),
    ('\x00\x01\x02\x03', None),
    ('éééééééé', None),
    ('ébG9uZyBzZWN' 'yZXQgdmFsdWU' 'gd2l0aCBwbGF' 'pbiB0ZXh0', None),
    ('', None),
    ('A', None),
    ('AB', None),
    ('ABC', None),
    ('ABCD', None),
    ('=', None),
    ('=A', None),
    ('A=', None),
    ('a-', None),
    ('_a', None),
    ('AQIDBAUGBwgJ' 'CgsMDQ4PEBES' 'ExQVFhcYGRob' 'HB0eHyAhIiMk' 'JSYn', None),
    ('c2hvcnQ=', None),
    ('U2hvAQIDBHJ0' 'IHRleHQgdGhh' 'dCBpcyBsb25n' 'IGVub3VnaA==', None),
]


def _fuzz_tokens() -> list[str]:
    rnd = random.Random(20261030)
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits + "+/-_= "
    return ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40))) for _ in range(2000)]


def _legacy(token: str) -> str | None:
    """Main's exact two-pass code (4bafa5a), the Python 3.12 reference."""
    pad = token + "=" * (-len(token) % 4)
    for fn in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = fn(pad)
        except (ValueError, TypeError):
            continue
        text = raw.decode("utf-8", errors="ignore")
        if len(text) >= 12 and sum(c.isprintable() for c in text) / len(text) >= 0.9:
            return text
    return None


def test_the_table_is_large_and_mixes_decodes_with_refusals():
    assert len(TABLE) >= 40 and sum(e is None for _, e in TABLE) >= 10 and sum(e is not None for _, e in TABLE) >= 30


@pytest.mark.parametrize("token, expected", TABLE)
def test_try_decode_matches_the_frozen_python_312_value(token, expected):
    assert try_decode(token) == expected


@pytest.mark.skipif(sys.version_info >= (3, 13), reason="lenient stdlib decode is the 3.12 reference")
def test_try_decode_equals_the_legacy_function_on_2000_seeded_tokens():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bad = [t for t in _fuzz_tokens() if try_decode(t) != _legacy(t)]
    assert bad == []


def test_try_decode_raises_no_warning_and_never_raises_on_the_table_and_the_fuzz_tokens():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for token in [t for t, _ in TABLE] + _fuzz_tokens():
            try_decode(token)


def test_a_base64_wrapped_secret_touching_an_underscore_is_still_reported_as_decoded():
    wrapped = base64.b64encode(("key>>> AKIA" + "ZZZZ1111YYYY2222").encode()).decode() + "_"
    assert "-" not in wrapped and wrapped.endswith("_")
    rules = {h.rule for h in scan(f"payload {wrapped} end", SECRETS)}
    assert "decoded:aws_access_key" in rules, rules
