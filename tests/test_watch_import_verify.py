"""S-3 F3: an imported proposal never carries the `verify` command the copy chose, and `records.check` ties the command to the class, the url and no `-` argument."""

import json
import re
from datetime import datetime

import pytest

from ace_playbook.compile.watch import cli, records, store, vet

from .test_watch_import import NOW
from .test_watch_import import w as world  # noqa: F401 - the fixture

URL = "https://example.test/post/1"
DOCS = "https://docs.example.test/en/hooks"
FORGED = "curl -sI -K/tmp/x | head -1"                                  # curl reads its options (a config file that writes files) from -K
CTX = {"targets": {"feed", "k1", "claude_version", DOCS}, "now": datetime(2026, 10, 21, 13, 0, 0), "docs": {DOCS}, "paths": {"k1": ["~/.claude/s.json"]}}
EV = {"url": URL, "sha256": "a" * 64, "quote": "q", "tier": 0}
GLOBS = ("http://ex.test/[1-9999]", "http://{a,b}.ex.test/")             # curl turns each into many requests unless it runs with -g
RETIRE = "retirement with no successor named: claude-old (k1)"
GREP = 'grep -n "claude-old" -- ~/.claude/s.json'


def rec(cls, verify, target=None, title=None, url=URL, tier=0):
    target = target or {"docs": DOCS, "version": "claude_version", "lead": "feed", "model_pin": "k1"}[cls]
    title = title or {"model_pin": "Replace claude-old with claude-new in k1", "version": "Claude Code 9.1.0 available (installed 9.0.1)"}.get(cls, "A post")
    return records.record(cls, title, {**EV, "url": url, "tier": tier}, [], target, None, verify, NOW)


# ------------------------------------------------------------------ check: the template belongs to the class and the url
@pytest.mark.parametrize("mid,verify", [("-abc", 'grep -n "-abc" a'), ("abc", 'grep -n "abc" -f/etc/passwd'), ("abc", 'grep -n "abc" a -f/etc/passwd'),
                                        ("abc", "grep -n \"abc\" '-f/etc/passwd'"), ("abc", 'grep -n "abc" a --file=x'), ("abc", 'grep -n "abc" -- -f/x'),
                                        ("abc", 'grep -n "abc" -- a -- b'), ("abc", 'grep -n "abc" a -- b')])
def test_check_rejects_a_leading_dash_argument_in_every_grep_slot(mid, verify):
    # the title names `mid`, so only the dash can be what refuses the command: kills "ARG allows a leading dash" and "the grep pattern allows a leading dash"
    assert records.check(rec("lead", verify, "k1", f"retirement with no successor named: {mid} (k1)")) == "verify"


def test_check_accepts_the_grep_forms_without_a_dash_argument():
    for verify in ('grep -n "abc" a b', 'grep -n "abc" -- a b', "grep -n \"abc\" -- 'a b'"):
        assert records.check(rec("lead", verify, "k1", "retirement with no successor named: abc (k1)")) is None, verify


@pytest.mark.parametrize("verify", [FORGED, "curl -sI -- -K/tmp/x | head -1", "curl -sI '-K/tmp/x' | head -1", "curl -sI -- '-K/tmp/x' | head -1",
                                    "curl -gsI -K/tmp/x | head -1", "curl -gsI -- '-K/tmp/x' | head -1"])
def test_check_rejects_a_leading_dash_argument_in_the_curl_slot(verify):
    assert records.check(rec("lead", verify)) == "verify"
    assert records.CURL.fullmatch(verify) is None


def test_sh_refuses_a_word_that_begins_with_a_dash_and_the_builders_return_no_command():
    for word in ("-K/tmp/x", "-x", "--", "-"):
        with pytest.raises(ValueError):                                                    # kills "sh() quotes a leading dash instead of refusing"
            records.sh(word)
    assert records.sh("~/s.json") == "~/s.json" and records.sh("a b") == "'a b'"
    assert records.grep_cmd("abc", ["a", "-x.json"]) == "" and records.curl_cmd("-K/tmp/x") == ""      # an empty command fails check: no traceback, a refusal
    assert records.check(rec("lead", records.curl_cmd("-K/tmp/x"))) == "verify"


def test_check_rejects_a_curl_url_that_is_not_the_records_own_url():
    other = "https://evil.example.test/x"
    assert records.check(rec("lead", f"curl -gsI -- {other} | head -1")) == "verify"                   # kills "the curl argument is not tied to the evidence url"
    assert records.check(rec("lead", f"curl -sI {other} | head -1")) == "verify"
    assert records.check(rec("docs", f"curl -gsI -- {URL} | head -1")) == "verify"                      # a docs page's command names its target, not the evidence
    assert records.check(rec("docs", f"curl -gsI -- {other} | head -1")) == "verify"
    assert records.check(rec("lead", records.curl_cmd(URL))) is None and records.check(rec("docs", records.curl_cmd(DOCS))) is None
    assert records.check(rec("lead", "curl -gsI -- ftp://example.test/x | head -1", url="ftp://example.test/x")) == "verify"      # not http(s)


def test_check_rejects_a_template_that_does_not_belong_to_the_class():
    curl, grep, ver = records.curl_cmd(URL), 'grep -n "claude-old" a', "claude --version"
    for cls, verify, title in (("version", curl, None), ("version", grep, None), ("docs", ver, None), ("docs", grep, None), ("lead", ver, None),
                               ("model_pin", ver, None), ("model_pin", curl, None), ("lead", 'grep -n "other" a', RETIRE),
                               ("lead", grep, None), ("model_pin", 'grep -n "other" a', None),
                               ("docs", grep, RETIRE)):         # a grep whose id the title does not name
        assert records.check(rec(cls, verify, title=title)) == "verify", (cls, verify)      # kills "the template is not tied to the class"
    assert records.check(rec("version", ver)) is None and records.check(rec("model_pin", grep)) is None
    assert records.check(rec("lead", grep, "k1", RETIRE)) is None


def test_records_made_the_old_way_still_check():
    olds = [("lead", f"curl -sI {URL} | head -1", None, URL), ("docs", f"curl -sI {DOCS} | head -1", None, URL), ("version", "claude --version", None, URL),
            ("lead", f"curl -sI -- {URL} | head -1", None, URL), ("docs", f"curl -sI -- {DOCS} | head -1", None, URL),
            ("model_pin", 'grep -n "claude-old" ~/.claude/s.json', None, URL), ("model_pin", "grep -n \"claude-old\" '~/my dir/s.json' b.json", None, URL),
            ("lead", 'grep -n "claude-old" ~/.claude/s.json', RETIRE, URL), ("lead", "curl -sI 'https://example.test/a?x=1&y=2' | head -1", None, "https://example.test/a?x=1&y=2")]
    for cls, verify, title, url in olds:
        assert records.check(rec(cls, verify, "k1" if title else None, title, url=url)) is None, (cls, verify)
    assert records.curl_cmd(URL) == f"curl -gsI -- {URL} | head -1" and records.check(rec("lead", records.curl_cmd(URL))) is None      # new ones get -g and `--`
    assert records.grep_cmd("claude-old", ["~/.claude/s.json"]) == GREP and records.check(rec("model_pin", GREP)) is None


# ------------------------------------------------------------------ curl globbing: [ ] { } in a url
@pytest.mark.parametrize("url", GLOBS)
def test_a_url_with_glob_characters_is_accepted_only_with_globoff(url):
    for old in (f"curl -sI -- '{url}' | head -1", f"curl -sI '{url}' | head -1"):
        assert records.check(rec("lead", old, url=url)) == "verify", old               # kills "the bracket/brace refusal dropped from the old forms"
    assert records.check(rec("lead", records.curl_cmd(url), url=url)) is None
    assert records.curl_cmd(url).startswith("curl -gsI -- ")                           # kills "-g dropped from curl_cmd" (and the match above)


def test_curl_cmd_always_carries_globoff():
    assert records.curl_cmd(URL) == f"curl -gsI -- {URL} | head -1"                    # kills "-g dropped from curl_cmd"


# ------------------------------------------------------------------ control characters and whitespace in a url or a path
@pytest.mark.parametrize("ch", ["\n", "\r", "\t", "\x00", "\x7f"])
def test_a_control_character_in_a_url_or_a_map_path_is_refused(ch):
    url = f"https://example.test/a{ch}b"
    assert records.check(rec("lead", f"curl -gsI -- '{url}' | head -1", url=url)) == "verify"
    assert vet.vet(rec("lead", FORGED, "feed", url=url), CTX) == (None, "verify")
    path = f"~/a{ch}b.json"
    assert records.check(rec("model_pin", f"grep -n \"claude-old\" -- '{path}'")) == "verify"          # kills "ARG's quoted branch allows control characters"
    assert vet.vet(rec("model_pin", FORGED), {**CTX, "paths": {"k1": [path]}}) == (None, "verify")


def test_a_url_with_whitespace_is_not_http_for_check():
    url = "https://example.test/a b"
    assert records.check(rec("lead", f"curl -gsI -- '{url}' | head -1", url=url)) == "verify"          # kills "_http allows whitespace" (ARG allows a space inside quotes)
    assert records._http(URL) and not records._http(url) and not records._http("https://example.test/a\nb")


# ------------------------------------------------------------------ vet: verify is rebuilt, never taken from the copy
def forged(cls, **kw):
    return {**rec(cls, FORGED, **kw), "verify": FORGED}


@pytest.mark.parametrize("cls,expect", [("lead", records.curl_cmd(URL)), ("version", "claude --version"), ("docs", records.curl_cmd(DOCS)), ("model_pin", GREP)])
def test_vet_stores_the_rebuilt_verify_whatever_the_copy_says(cls, expect):
    got, why = vet.vet(forged(cls), CTX)                                      # vet alone, before `records.check` is run on the copy
    assert (why, got["verify"]) == ("", expect) and got["verify"] != FORGED   # kills "vet keeps rec['verify']"
    assert records.check(got) is None


@pytest.mark.parametrize("url", GLOBS)
def test_vet_stores_the_globoff_command_for_a_lead_whose_url_has_glob_characters(url):
    got, why = vet.vet(forged("lead", url=url), CTX)
    assert why == "" and got["verify"] == f"curl -gsI -- '{url}' | head -1" and records.check(got) is None


def test_vet_rebuilds_a_retirement_lead_from_the_operators_own_paths_and_other_leads_from_their_url():
    got, why = vet.vet(forged("lead", target="k1", title=RETIRE), CTX)
    assert why == "" and got["verify"] == GREP
    assert vet.vet(forged("lead", target="feed", title=RETIRE), CTX)[0]["verify"] == records.curl_cmd(URL)      # a source id has no paths: the lead's url


def test_vet_rejects_a_record_with_no_safe_rebuild():
    assert vet.vet(forged("docs", target="k1"), CTX) == (None, "verify")                   # a docs page that is not a mapped page
    assert vet.vet(forged("model_pin", target="claude_version"), CTX) == (None, "verify")   # a model_pin whose target is not a map key
    assert vet.vet(forged("model_pin", title="Replace -x with y in k1"), CTX) == (None, "verify")     # an id that is an option
    assert vet.vet(forged("model_pin"), {**CTX, "paths": {"k1": ["-x.json"]}}) == (None, "verify")    # a path that is an option: a refusal, not a traceback
    assert vet.vet(forged("lead", url="https://example.test/it's"), CTX) == (None, "verify")  # a url that cannot be quoted as one word
    assert vet.vet(forged("model_pin"), {**CTX, "paths": {"k1": []}}) == (None, "verify")             # a map key whose pattern lists no file: no command
    assert vet.vet(forged("lead", target="k1", title=RETIRE), {**CTX, "paths": {"k1": []}}) == (None, "verify")


def test_the_map_listing_is_what_inventory_build_keys_its_files_by(tmp_path):
    from ace_playbook.compile.watch import inventory
    (tmp_path / "a.json").write_text("x")
    (tmp_path / "b.json").write_text("x")
    mapping = {"targets": {"k": {"path": str(tmp_path / "*.json"), "pattern": "x"}, "none": {"path": str(tmp_path / "*.nope"), "pattern": "x"}}}
    snap = inventory.build(mapping, NOW, which=lambda n: None)["targets"]
    assert inventory.names(mapping["targets"]["k"]["path"]) == sorted(snap["k"]["files"]) and inventory.names(mapping["targets"]["none"]["path"]) == []


# ------------------------------------------------------------------ the import and the queue card
def put_and_import(w, capsys, *recs):
    for r in recs:
        w.put(r)
    code, out, _ = w.run(capsys)
    assert code == 0
    return out


def test_import_refuses_a_copy_whose_verify_is_the_forged_one_for_lead_and_version(world, capsys):
    lead = {**rec("lead", FORGED, "feed", url="https://example.test/p")}
    version = rec("version", FORGED)
    out = put_and_import(world, capsys, lead, version)
    assert out[0] == "import: 0 imported, 0 duplicate, 2 rejected" and sorted(out[1:]) == sorted(f"rejected {r['id']}: verify" for r in (lead, version))
    assert world.stored() == []


def test_import_stores_and_the_queue_card_shows_the_rebuilt_command_not_the_copys(world, capsys):
    old = rec("lead", f"curl -sI {URL} | head -1", "feed")                                  # the form a copy made by the older code has: it checks
    pin = rec("model_pin", 'grep -n "claude-old" /var/acewatch/.claude/s.json b.json', "k1")  # the sandbox user's own paths
    glob = rec("lead", "curl -gsI -- 'http://ex.test/[1-9999]' | head -1", "k2", url="http://ex.test/[1-9999]")
    out = put_and_import(world, capsys, old, pin, glob)
    assert out[0] == "import: 3 imported, 0 duplicate, 0 rejected"
    mine = {r["id"]: r for r in store.load_proposals(world.wd)}
    grep = f'grep -n "claude-old" -- {world.pins}'
    assert mine[old["id"]]["verify"] == records.curl_cmd(URL) and mine[pin["id"]]["verify"] == grep
    for r, shown in ((old, records.curl_cmd(URL)), (pin, grep), (glob, "curl -gsI -- 'http://ex.test/[1-9999]' | head -1")):
        cli._card(mine[r["id"]], 1, 3)
        text = capsys.readouterr().out
        assert f"verify:   {shown}\n" in text and (r["verify"] not in text or r["verify"] == shown)      # kills "vet keeps rec['verify']" at what the operator is shown
    assert all(records.check(r) is None for r in mine.values())


def test_a_docs_copy_is_rebuilt_from_the_mapped_target(world, capsys):
    page = rec("docs", f"curl -sI {DOCS} | head -1")
    out = put_and_import(world, capsys, page)
    assert out[0] == "import: 1 imported, 0 duplicate, 0 rejected"
    assert json.loads((world.wd / "proposals" / (page["id"] + ".json")).read_text())["verify"] == f"curl -gsI -- {DOCS} | head -1"


def test_the_queue_card_marks_a_command_it_cut(capsys):
    short = rec("model_pin", records.grep_cmd("claude-old", ["a"]))
    cli._card(short, 1, 1)
    assert "(truncated" not in capsys.readouterr().out
    paths = [f"~/dir/file{n:03d}.json" for n in range(40)]
    long = rec("model_pin", records.grep_cmd("claude-old", paths))
    assert len(long["verify"]) > 300
    cli._card(long, 1, 1)
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  verify:"))
    assert line == f"  verify:   {long['verify'][:300]} …(truncated, {len(long['verify'])} chars)"      # kills "the card cuts the command without a mark"
    assert re.search(r"…\(truncated, \d+ chars\)$", line)
