"""The text-level pieces of compile/srchash.py: exact-span excision, the drop rule for other rows' fires, the digest's framing."""

import textwrap
from pathlib import Path

import pytest

from ace_playbook.compile import srchash

SRC = textwrap.dedent('''\
    """doc"""
    import posixpath
    LIMIT = 3  # keep

    def _helper(x):
        return x

    @decorate
    @other
    def alpha(event, state=LIMIT):
        """alpha doc"""
        return _helper(event)  # tail

    async def beta(event, state):
        return LIMIT

    def gamma(event, state):
        return alpha(event, state)

    def delta(event, state):
        if event:
            return state

    def advance(event, state):
        return blank_state()

    def blank_state():
        return {}

    class K:
        def alpha(self):
            return 1
    ''')
OTHERS = ("alpha", "beta", "gamma", "delta")


def drop(fire, others=OTHERS, text=SRC):
    return srchash.detector_drop(text, fire, others)


# ------------------------------------------------------------------ excised_text: functions
def test_only_the_body_of_a_dropped_function_is_excised_decorators_and_signature_stay():
    out = srchash.excised_text(SRC, {"alpha"})
    assert "@decorate\n@other\ndef alpha(event, state=LIMIT):\n    ...excised...  # tail\n" in out
    assert "alpha doc" not in out and "return _helper(event)" not in out


def test_an_async_def_and_a_one_line_body_keep_their_header():
    out = srchash.excised_text(SRC, {"beta"})
    assert "async def beta(event, state):\n    ...excised...\n" in out
    assert srchash.excised_text("def f(x=g()): return x  # c\n", {"f"}) == "def f(x=g()): ...excised...  # c\n"


def test_everything_else_is_kept_verbatim_and_a_method_of_the_same_name_stays():
    out = srchash.excised_text(SRC, {"alpha", "beta"})
    for kept in ('"""doc"""', "import posixpath", "LIMIT = 3  # keep", "def gamma(event, state):", "class K:",
                 "def alpha(self):\n        return 1", "def advance(event, state):\n    return blank_state()"):
        assert kept in out


def test_excising_nothing_or_an_absent_name_changes_nothing():
    assert srchash.excised_text(SRC, ()) == SRC and srchash.excised_text(SRC, {"nope"}) == SRC


def test_a_header_side_effect_is_hashed_a_decorator_or_a_default_argument():
    text = "@_b\ndef f(x=AUTOLOADED.add(1)):\n    return 1\n"
    assert srchash.excised_text(text, {"f"}) == "@_b\ndef f(x=AUTOLOADED.add(1)):\n    ...excised...\n"


# ------------------------------------------------------------------ excised_text: the registry literal, by exact span
def test_a_registry_assignment_is_excised_plain_or_annotated_and_other_assigns_stay():
    text = "A = {\n    1: 2,\n}\nB: dict = {\n    3: 4}\nC = 5\n"
    assert srchash.excised_text(text, (), ("A",)) == "A = ...excised...\nB: dict = {\n    3: 4}\nC = 5\n"
    assert srchash.excised_text(text, (), ("B",)) == "A = {\n    1: 2,\n}\nB = ...excised...\nC = 5\n"


def test_statements_sharing_a_line_with_the_registry_stay_hashed():
    text = "X = 1; CATALOGUE = {\n    1: 2,\n}; Y = evil()\n"
    assert srchash.excised_text(text, (), ("CATALOGUE",)) == "X = 1; CATALOGUE = ...excised...; Y = evil()\n"
    text = "CATALOGUE = {1: 2}; shell.git_subcommand = lambda t: 0\n"
    assert srchash.excised_text(text, (), ("CATALOGUE",)) == "CATALOGUE = ...excised...; shell.git_subcommand = lambda t: 0\n"


def test_columns_are_utf8_byte_offsets_so_a_non_ascii_neighbour_is_safe():
    text = 's = "é"; CATALOGUE = {1: 2}; t = "ü"\n'
    assert srchash.excised_text(text, (), ("CATALOGUE",)) == 's = "é"; CATALOGUE = ...excised...; t = "ü"\n'


def test_a_chained_tuple_or_attribute_assignment_is_not_excised():
    for text in ("A = B = 1\n", "A, B = 1, 2\n", "X.A = 2\n"):
        assert srchash.excised_text(text, (), ("A",)) == text


def test_a_registry_bound_twice_at_top_level_raises():
    with pytest.raises(ValueError, match="CATALOGUE"):
        srchash.excised_text("CATALOGUE = {}\nCATALOGUE = {1: 1}\n", (), ("CATALOGUE",))
    with pytest.raises(ValueError):
        srchash.excised_text("CATALOGUE = {}\nCATALOGUE: dict = {}\n", (), ("CATALOGUE",))
    srchash.excised_text("CATALOGUE = {}\nCATALOGUE, X = 1, 2\n", (), ("CATALOGUE",))        # a tuple target stays hashed


# ------------------------------------------------------------------ detector_drop
def test_the_other_rows_fires_are_dropped_and_the_rows_own_is_kept():
    assert drop("beta") == {"alpha", "gamma", "delta"}
    assert drop("beta", others=("alpha", "beta")) == set()                       # gamma names alpha
    assert drop("beta", others=("alpha", "gamma")) == {"alpha", "gamma"}


def test_advance_and_blank_state_are_never_dropped():
    for fire in ("alpha", "delta", "elsewhere"):
        assert not {"advance", "blank_state"} & drop(fire)


def test_a_fire_the_kept_text_still_names_is_kept():
    text = SRC.replace("def delta(event, state):\n    if event:", "def delta(event, state):\n    alpha\n    if event:")
    assert "alpha" not in drop("delta", text=text) and "beta" in drop("delta", text=text)


def test_a_chain_of_callees_is_kept_to_a_fixed_point():
    text = SRC.replace("def delta(event, state):\n    if event:", "def delta(event, state):\n    gamma\n    if event:")
    got = drop("delta", text=text)
    assert "gamma" not in got and "alpha" not in got and "beta" in got        # delta -> gamma -> alpha


def test_a_dropped_fires_header_still_runs_so_what_it_names_is_kept_but_its_body_is_not():
    text = SRC.replace("def gamma(event, state):", "def gamma(event, state=beta):")
    got = drop("delta", text=text)
    assert "beta" not in got and "gamma" in got and "alpha" in got            # alpha: only gamma's body names it


def test_a_fire_missing_from_the_text_drops_the_others():
    assert {"alpha", "beta"} <= drop("elsewhere")


# ------------------------------------------------------------------ digest framing
@pytest.fixture
def texts(monkeypatch):
    """detectors.py replaced by a synthetic text; catalogue.py stays real."""
    box = {"d": SRC}
    real = srchash.module_text
    monkeypatch.setattr(srchash, "module_text", lambda p: box["d"] if p.name == "detectors.py" else real(p))
    return box


def digest(**kw):
    args = dict(name="r", meta={"state": "none"}, fire="alpha", others=("beta",), leaves=("e", "s", "t"))
    return srchash.digest(**{**args, **kw})


def test_the_digest_is_stable_and_hex(texts):
    assert digest() == digest() and len(digest()) == 64


def test_labels_keep_neighbouring_fields_apart(texts):
    assert digest(name="ab", fire="c") != digest(name="a", fire="bc")
    assert digest(leaves=("es", "", "t")) != digest(leaves=("e", "s", "t"))
    assert digest(leaves=("e", "s")) != digest(leaves=("e", "s", "t"))


def test_a_repeated_statement_is_not_deduplicated(texts):
    texts["d"] = SRC + "\nCOUNT = 0\nCOUNT += 1\n"
    once = digest()
    texts["d"] = SRC + "\nCOUNT = 0\nCOUNT += 1\nCOUNT += 1\n"
    assert digest() != once


def test_the_digest_follows_the_edited_text_of_a_kept_function_not_a_dropped_body(texts):
    kept = digest(fire="beta", others=("alpha", "gamma"))
    texts["d"] = SRC.replace("return _helper(event)", "return _helper(event) + 1")        # alpha's body: another row's fire
    assert digest(fire="beta", others=("alpha", "gamma")) == kept
    texts["d"] = SRC.replace("return LIMIT", "return LIMIT + 1")                           # beta: this row's fire
    assert digest(fire="beta", others=("alpha", "gamma")) != kept
    texts["d"] = SRC.replace("return blank_state()", "return blank_state() or {}")         # advance: every row
    assert digest(fire="beta", others=("alpha", "gamma")) != kept


def test_module_text_reads_the_file_once(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a = 1\n")
    assert srchash.module_text(f) == "a = 1\n"
    f.write_text("a = 2\n")
    assert srchash.module_text(Path(f)) == "a = 1\n"
