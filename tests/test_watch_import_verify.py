"""S-3 F3: an imported proposal never carries the `verify` command the copy chose, and `records.check` ties the command to the class, the url and no `-` argument."""

import json
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


def rec(cls, verify, target=None, title=None, url=URL, tier=0):
    target = target or {"docs": DOCS, "version": "claude_version", "lead": "feed", "model_pin": "k1"}[cls]
    title = title or {"model_pin": "Replace claude-old with claude-new in k1", "version": "Claude Code 9.1.0 available (installed 9.0.1)"}.get(cls, "A post")
    return records.record(cls, title, {**EV, "url": url, "tier": tier}, [], target, None, verify, NOW)


RETIRE = "retirement with no successor named: claude-old (k1)"


# ------------------------------------------------------------------ check: the template belongs to the class and the url
@pytest.mark.parametrize("mid,verify", [("-abc", 'grep -n "-abc" a'), ("abc", 'grep -n "abc" -f/etc/passwd'), ("abc", 'grep -n "abc" a -f/etc/passwd'),
                                        ("abc", "grep -n \"abc\" '-f/etc/passwd'"), ("abc", 'grep -n "abc" -- a'), ("abc", 'grep -n "abc" a --file=x')])
def test_check_rejects_a_leading_dash_argument_in_every_grep_slot(mid, verify):
    # the title names `mid`, so only the dash can be what refuses the command: kills "ARG allows a leading dash" and "the grep pattern allows a leading dash"
    title = f"retirement with no successor named: {mid} (k1)"
    assert records.check(rec("lead", verify, "k1", title)) == "verify"
    assert records.check(rec("lead", f'grep -n "{mid.lstrip("-")}" a b', "k1", title.replace("-", "", 1) if mid == "-abc" else title)) is None


@pytest.mark.parametrize("verify", [FORGED, "curl -sI -- -K/tmp/x | head -1", "curl -sI '-K/tmp/x' | head -1", "curl -sI -- '-K/tmp/x' | head -1"])
def test_check_rejects_a_leading_dash_argument_in_the_curl_slot(verify):
    assert records.check(rec("lead", verify)) == "verify"
    assert records.CURL.fullmatch(verify) is None


def test_sh_quotes_a_word_that_begins_with_a_dash_and_arg_refuses_it():
    import re
    for word in ("-K/tmp/x", "-x", "--", "-"):
        assert records.sh(word).startswith("'") and not re.fullmatch(records.ARG, records.sh(word))      # kills "sh() leaves a leading dash bare"
    assert records.sh("~/s.json") == "~/s.json" and records.sh("a b") == "'a b'"


def test_check_rejects_a_curl_url_that_is_not_the_records_own_url():
    other = "https://evil.example.test/x"
    assert records.check(rec("lead", f"curl -sI -- {other} | head -1")) == "verify"                    # kills "the curl argument is not tied to the evidence url"
    assert records.check(rec("lead", f"curl -sI {other} | head -1")) == "verify"
    assert records.check(rec("docs", f"curl -sI -- {URL} | head -1")) == "verify"                       # a docs page's command names its target, not the evidence
    assert records.check(rec("docs", f"curl -sI -- {other} | head -1")) == "verify"
    assert records.check(rec("lead", records.curl_cmd(URL))) is None and records.check(rec("docs", records.curl_cmd(DOCS))) is None
    assert records.check(rec("lead", "curl -sI -- ftp://example.test/x | head -1", url="ftp://example.test/x")) == "verify"      # not http(s)


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
    olds = [("lead", f"curl -sI {URL} | head -1", None), ("docs", f"curl -sI {DOCS} | head -1", None), ("version", "claude --version", None),
            ("model_pin", 'grep -n "claude-old" ~/.claude/s.json', None), ("model_pin", "grep -n \"claude-old\" '~/my dir/s.json' b.json", None),
            ("lead", 'grep -n "claude-old" ~/.claude/s.json', RETIRE), ("lead", f"curl -sI 'https://example.test/a b' | head -1", None)]
    for cls, verify, title in olds:
        url = "https://example.test/a b" if "a b" in verify else URL
        assert records.check(rec(cls, verify, "k1" if title else None, title, url=url)) is None, (cls, verify)
    assert records.curl_cmd(URL) == f"curl -sI -- {URL} | head -1" and records.check(rec("lead", records.curl_cmd(URL))) is None      # new ones get `--`


# ------------------------------------------------------------------ vet: verify is rebuilt, never taken from the copy
def forged(cls, **kw):
    return {**rec(cls, FORGED, **kw), "verify": FORGED}


@pytest.mark.parametrize("cls,expect", [("lead", records.curl_cmd(URL)), ("version", "claude --version"), ("docs", records.curl_cmd(DOCS)),
                                        ("model_pin", 'grep -n "claude-old" ~/.claude/s.json')])
def test_vet_stores_the_rebuilt_verify_whatever_the_copy_says(cls, expect):
    got, why = vet.vet(forged(cls), CTX)                                      # vet alone, before `records.check` is run on the copy
    assert (why, got["verify"]) == ("", expect) and got["verify"] != FORGED   # kills "vet keeps rec['verify']"
    assert records.check(got) is None


def test_vet_rebuilds_a_retirement_lead_from_the_operators_own_paths_and_other_leads_from_their_url():
    got, why = vet.vet(forged("lead", target="k1", title=RETIRE), CTX)
    assert why == "" and got["verify"] == 'grep -n "claude-old" ~/.claude/s.json'
    assert vet.vet(forged("lead", target="feed", title=RETIRE), CTX)[0]["verify"] == records.curl_cmd(URL)      # a source id has no paths: the lead's url


def test_vet_rejects_a_record_with_no_safe_rebuild():
    assert vet.vet(forged("docs", target="k1"), CTX) == (None, "verify")                   # a docs page that is not a mapped page
    assert vet.vet(forged("model_pin", target="claude_version"), CTX) == (None, "verify")   # a model_pin whose target is not a mapped file
    assert vet.vet(forged("model_pin", title="Replace -x with y in k1"), CTX) == (None, "verify")     # an id that is an option
    odd = {**CTX, "paths": {"k1": ["-x.json"]}}
    assert vet.vet(forged("model_pin"), odd) == (None, "verify")                             # a path that is an option
    assert vet.vet(forged("lead", url="https://example.test/it's"), CTX) == (None, "verify")  # a url that cannot be quoted as one word


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
    out = put_and_import(world, capsys, old, pin)
    assert out[0] == "import: 2 imported, 0 duplicate, 0 rejected"
    mine = {r["id"]: r for r in store.load_proposals(world.wd)}
    op_path = str(world.root / "none")
    assert mine[old["id"]]["verify"] == records.curl_cmd(URL) and mine[pin["id"]]["verify"] == f'grep -n "claude-old" {op_path}'
    for r, shown in ((old, records.curl_cmd(URL)), (pin, f'grep -n "claude-old" {op_path}')):
        cli._card(mine[r["id"]], 1, 2)
        text = capsys.readouterr().out
        assert f"verify:   {shown}\n" in text and r["verify"] not in text      # kills "vet keeps rec['verify']" at what the operator is shown
    assert all(records.check(r) is None for r in mine.values())


def test_a_docs_copy_is_rebuilt_from_the_mapped_target(world, capsys):
    page = rec("docs", f"curl -sI {DOCS} | head -1")
    out = put_and_import(world, capsys, page)
    assert out[0] == "import: 1 imported, 0 duplicate, 0 rejected"
    assert json.loads((world.wd / "proposals" / (page["id"] + ".json")).read_text())["verify"] == f"curl -sI -- {DOCS} | head -1"
