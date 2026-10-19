import pytest

from ace_playbook.migrate.normalize import normalize

TABLE = [
    ("em dash", "a—b", "a-b"),
    ("en dash", "a–b", "a-b"),
    ("minus sign", "a−b", "a-b"),
    ("right arrow", "a→b", "a to b"),
    ("double right arrow", "a⇒b", "a to b"),
    ("long right arrow", "a⟶b", "a to b"),
    ("left arrow", "a←b", "a from b"),
    ("left single quote", "‘x’", "'x'"),
    ("right single quote", "it’s", "it's"),
    ("left double quote", "“x”", '"x"'),
    ("ellipsis", "wait…", "wait..."),
    ("multiplication sign", "2×3", "2x3"),
    ("greater or equal", "n≥5", "n at least 5"),
    ("less or equal", "n≤5", "n at most 5"),
    ("not equal", "a≠b", "a!=b"),
    ("nbsp", "a b", "a b"),
    ("thin space", "a b", "a b"),
    ("narrow nbsp", "a b", "a b"),
    ("bullet", "• item", "- item"),
    ("middle dot", "a·b", "a-b"),
    ("check mark", "done✓", "doneok"),
    ("heavy check mark", "done✔", "doneok"),
    ("ballot x", "no✗", "nox"),
    ("heavy ballot x", "no✘", "nox"),
    ("emoji dropped", "ship it \U0001F680 now", "ship it now"),
    ("other symbol dropped", "a☃b", "a b"),
    ("emoji only", "\U0001F600\U0001F680", ""),
]


@pytest.mark.parametrize("name,raw,want", TABLE, ids=[t[0] for t in TABLE])
def test_mapping(name, raw, want):
    assert normalize(raw) == want


def test_ascii_unchanged():
    s = "Use python3, not python: `run` it (x) [y] {z} 100% ok!"
    assert normalize(s) == s


def test_multiline_collapses_to_one_line():
    assert normalize("  first line\n\nsecond\tline\r\n  third  ") == "first line second line third"


def test_nfkc_fullwidth_folds():
    assert normalize("Ｕｓｅ ｐｙｔｈｏｎ３") == "Use python3"


def test_result_is_printable_ascii_one_line():
    out = normalize("café — naïve “quote” → \U0001F600\n\x00end")
    assert all(0x20 <= ord(c) <= 0x7E for c in out)
    assert out == out.strip() and "  " not in out
