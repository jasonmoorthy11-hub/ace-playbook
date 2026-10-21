"""`compile/watch/match.py`: the per-class rules. Plain code over synthetic inventory snapshots and fixture results; no model, no network."""

import hashlib
import json
import re

import pytest

from ace_playbook.compile.watch import match
from ace_playbook.compile.watch.fetch import EXPECT, fetch

from .watch_support import INJECTION, Opener, Resp, fixture

NOW = "2026-10-21T12:00:00Z"
OLD, NEW = "claude-old-sonnet-20250101", "claude-new-sonnet-20260601"


def result(kind, name, ctype, tier=0, url=None, prev=None, store=None):
    url = url or f"https://example.test/{kind}"
    item = fetch(url, expect=EXPECT[kind], store={} if store is None else store, kind=kind, now=NOW, opener=Opener({url: Resp(fixture(name), ctype)}))
    return {"id": kind, "url": url, "kind": kind, "tier": tier, "sha256": item["sha256"], "prev_sha256": prev, "data": item["data"],
            "quote": item["body"][:400], "heading": item["data"].get("heading", "") if kind == "md" else ""}


def inv(version=None, **targets):
    return {"generated_at": NOW, "claude_version": version, "targets": {k: {"files": files} for k, files in targets.items()}}


SETTINGS = {"~/.claude/settings.json": {"sha256": "1" * 64, "lines": [[3, f'  "model": "{OLD}",'], [9, f'  "fast": "{OLD}"']]}}
DEPR = lambda: result("deprecations", "deprecations.md", "text/markdown", url="https://example.test/deprecations.md")  # noqa: E731


def test_model_pin_generates_the_diff_from_the_template_and_quotes_the_table_row():
    recs = match.candidates(inv(**{"settings.model": SETTINGS}), [DEPR()], {}, {}, NOW)
    assert len(recs) == 1
    r = recs[0]
    assert r["class"] == "model_pin" and r["elevated"] is False and r["status"] == "open" and r["created"] == NOW
    assert r["title"] == f"Replace {OLD} with {NEW} in settings.model" and len(r["title"]) <= 120
    assert r["change"]["target"] == "settings.model" and r["inventory_hits"] == ["settings.model"]
    assert r["change"]["diff"] == ("--- a/~/.claude/settings.json\n+++ b/~/.claude/settings.json\n"
                                   f'@@ -3,1 +3,1 @@\n-  "model": "{OLD}",\n+  "model": "{NEW}",\n'
                                   f'@@ -9,1 +9,1 @@\n-  "fast": "{OLD}"\n+  "fast": "{NEW}"\n')
    assert r["change"]["diff_sha256"] == hashlib.sha256(r["change"]["diff"].encode()).hexdigest()
    ev = r["evidence"][0]
    assert ev["quote"] == f"| March 1, 2027 | [`{OLD}`](https://docs.example.test/models) | `{NEW}` |"          # the history row, which names the successor
    assert ev["tier"] == 0 and ev["url"] == "https://example.test/deprecations.md" and len(ev["sha256"]) == 64 and len(ev["quote"]) <= 400
    assert r["verify"] == f'grep -n "{OLD}" ~/.claude/settings.json'
    assert r["expires"] == "2026-11-20T12:00:00Z" and re.fullmatch(r"W-[0-9a-f]{8}", r["id"]) and match.check(r) is None


def test_the_quote_is_the_table_row_text_verbatim_and_a_substring_of_the_fetched_body():
    body = fetch("https://example.test/d.md", expect=EXPECT["deprecations"], store={}, kind="deprecations", now=NOW,
                 opener=Opener({"https://example.test/d.md": Resp(fixture("deprecations.md"), "text/markdown")}))["body"]
    r = match.candidates(inv(**{"settings.model": SETTINGS}), [DEPR()], {}, {}, NOW)[0]
    assert r["evidence"][0]["quote"] in body.splitlines()


def test_one_proposal_per_model_and_target_key():
    files = {"~/a.md": {"sha256": "2" * 64, "lines": [[1, f"model: {OLD}"]]}}
    recs = match.candidates(inv(**{"settings.model": SETTINGS, "agents.model": files}), [DEPR()], {}, {}, NOW)
    assert sorted(r["change"]["target"] for r in recs) == ["agents.model", "settings.model"] and len({r["id"] for r in recs}) == 2


def test_a_retirement_without_a_successor_is_a_lead_with_no_diff():
    d = result("deprecations", "deprecations_reordered.md", "text/plain")
    d["data"] = [{**r, "successor": None} for r in d["data"]]
    recs = match.candidates(inv(**{"settings.model": SETTINGS}), [d], {}, {}, NOW)
    assert [(r["class"], r["change"]["diff"], r["change"]["diff_sha256"]) for r in recs] == [("lead", None, None)]
    assert recs[0]["title"] == f"retirement with no successor named: {OLD} (settings.model)" and recs[0]["inventory_hits"] == ["settings.model"]


def test_an_active_model_with_a_tentative_retirement_is_not_retiring_but_a_deprecated_one_without_a_date_is():
    pinned = {"~/s": {"sha256": "9" * 64, "lines": [[1, "claude-future-model-20260401"], [2, "claude-preview-model-20260101"],
                                                        [3, "claude-old-haiku-20240101"], [4, "claude-active-model-20260101"]]}}
    recs = match.candidates(inv(k=pinned), [DEPR()], {}, {}, NOW)
    assert sorted(r["title"] for r in recs) == [
        "retirement with no successor named: claude-old-haiku-20240101 (k)", "retirement with no successor named: claude-preview-model-20260101 (k)"]
    assert all(r["class"] == "lead" and r["change"]["diff"] is None for r in recs)


def test_only_deprecated_models_the_inventory_pins_and_with_boundaries():
    active = {"~/s": {"sha256": "3" * 64, "lines": [[1, "claude-active-model-20260101"]]}}
    prefix = {"~/p": {"sha256": "4" * 64, "lines": [[1, f"{OLD}-extra"], [2, f"x{OLD}"], [3, f"{OLD}.1"]]}}
    assert match.candidates(inv(a=active, p=prefix), [DEPR()], {}, {}, NOW) == []
    end = {"~/e": {"sha256": "5" * 64, "lines": [[1, f'see "{OLD}".']]}}
    assert len(match.candidates(inv(e=end), [DEPR()], {}, {}, NOW)) == 1


def test_an_unsafe_model_id_or_successor_never_reaches_a_diff_or_a_command():
    d = DEPR()
    d["data"] = [{"model_id": OLD, "deprecated": "x", "retires": "y", "successor": 'claude-x"; rm -rf', "row": "r"}]
    recs = match.candidates(inv(**{"settings.model": SETTINGS}), [d], {}, {}, NOW)
    assert [r["class"] for r in recs] == ["lead"] and "rm -rf" not in json.dumps(recs)
    d["data"] = [{"model_id": 'claude-a"; echo hi', "deprecated": "x", "retires": None, "successor": NEW, "row": "r"}]
    assert match.candidates(inv(x={"~/f": {"sha256": "6" * 64, "lines": [[1, 'claude-a"; echo hi']]}}), [d], {}, {}, NOW) == []


def test_a_long_inventory_line_may_be_truncated_so_no_diff_is_written_for_it():
    long = {"~/l": {"sha256": "7" * 64, "lines": [[1, OLD + " " + "z" * 300]]}}
    assert [r["class"] for r in match.candidates(inv(l=long), [DEPR()], {}, {}, NOW)] == ["lead"]


def test_paths_with_spaces_are_quoted_in_the_verify_command():
    r = match.candidates(inv(k={"~/my dir/s.json": {"sha256": "8" * 64, "lines": [[1, OLD]]}}), [DEPR()], {}, {}, NOW)[0]
    assert r["verify"] == f"""grep -n "{OLD}" '~/my dir/s.json'""" and match.check(r) is None


# ------------------------------------------------------------------ version
def test_version_proposal_only_when_npm_is_newer_than_installed():
    npm = result("npm", "npm_tags.json", "application/json", url="https://example.test/npm")
    recs = match.candidates(inv(version="9.0.1 (Claude Code)"), [npm], {}, {}, NOW)
    assert len(recs) == 1
    r = recs[0]
    assert (r["class"], r["change"]["diff"], r["verify"], r["inventory_hits"]) == ("version", None, "claude --version", ["claude_version"])
    assert r["title"] == "Claude Code 9.1.0 available (installed 9.0.1)" and match.check(r) is None
    assert match.candidates(inv(version="9.1.0"), [npm], {}, {}, NOW) == []
    assert match.candidates(inv(version="10.0.0"), [npm], {}, {}, NOW) == []
    assert match.candidates(inv(version=None), [npm], {}, {}, NOW) == []
    assert match.candidates(inv(version="no digits here"), [npm], {}, {}, NOW) == []
    stable_only = {**npm, "data": {"latest": "9.0.1", "stable": "9.0.4"}}
    assert match.candidates(inv(version="9.0.1"), [stable_only], {}, {}, NOW)[0]["title"].startswith("Claude Code 9.0.4 available")


# ------------------------------------------------------------------ docs
DOCS = {"docs": {"https://docs.example.test/en/hooks": ["settings.model"]}, "lead_keywords": []}


def test_a_newer_sitemap_lastmod_for_a_mapped_page_is_a_docs_proposal():
    sm = result("sitemap", "sitemap.xml", "application/xml", url="https://example.test/sitemap.xml")
    prev = {"https://docs.example.test/en/hooks": "2026-10-01", "https://docs.example.test/en/settings": "2026-01-01"}
    recs = match.candidates(inv(), [sm], DOCS, prev, NOW)
    assert len(recs) == 1
    r = recs[0]
    assert (r["class"], r["change"]["diff"], r["inventory_hits"], r["change"]["target"]) == ("docs", None, ["settings.model"],
                                                                                               "https://docs.example.test/en/hooks")
    assert r["verify"] == "curl -sI https://docs.example.test/en/hooks | head -1" and r["evidence"][0]["quote"].endswith("lastmod 2026-10-20")
    assert match.candidates(inv(), [sm], DOCS, {"https://docs.example.test/en/hooks": "2026-10-20"}, NOW) == []     # not newer
    assert match.candidates(inv(), [sm], DOCS, {}, NOW) == []                                                       # first sight is a baseline
    assert match.candidates(inv(), [sm], {"docs": {}}, prev, NOW) == []                                             # an unmapped page is never an item


def test_a_changed_md_hash_for_a_mapped_page_is_a_docs_proposal_quoting_the_heading():
    page = result("md", "doc_page.md", "text/markdown", url="https://docs.example.test/en/hooks.md", prev="a" * 64)
    recs = match.candidates(inv(), [page], DOCS, {}, NOW)
    assert len(recs) == 1 and recs[0]["evidence"][0]["quote"] == "Hooks reference" and recs[0]["class"] == "docs"
    assert match.candidates(inv(), [{**page, "prev_sha256": page["sha256"]}], DOCS, {}, NOW) == []
    assert match.candidates(inv(), [{**page, "prev_sha256": None}], DOCS, {}, NOW) == []
    assert match.candidates(inv(), [{**page, "url": "https://docs.example.test/en/other.md"}], DOCS, {}, NOW) == []
    assert INJECTION not in json.dumps(recs)


# ------------------------------------------------------------------ leads
KW = {"lead_keywords": ["Subagent", "context engineering"], "docs": {}}


def test_a_tier_1_entry_with_a_keyword_is_a_lead_with_no_diff():
    feed = result("rss", "rss_hidden.xml", "application/rss+xml", tier=1)
    recs = match.candidates(inv(), [feed], KW, {}, NOW)
    assert len(recs) == 1
    r = recs[0]
    assert (r["class"], r["change"]["diff"], r["change"]["diff_sha256"], r["inventory_hits"]) == ("lead", None, None, [])
    assert r["title"] == "A harmless post about subagents" and r["evidence"][0]["tier"] == 1 and r["evidence"][0]["quote"] == r["title"]
    assert r["evidence"][0]["url"] == "https://example.test/post/1" and r["verify"] == "curl -sI https://example.test/post/1 | head -1"
    assert INJECTION not in json.dumps(recs) and "evil-model" not in json.dumps(recs)
    assert match.candidates(inv(), [feed], {"lead_keywords": ["nothing"]}, {}, NOW) == []
    assert match.candidates(inv(), [{**feed, "tier": 0}], KW, {}, NOW) == []                       # W1 reads leads from tier-1 feeds only


def test_the_keyword_may_sit_in_the_summary_and_leads_are_capped():
    feed = result("rss", "rss_ok.xml", "application/xml", tier=1)
    assert [r["title"] for r in match.candidates(inv(), [feed], {"lead_keywords": ["status line bug"]}, {}, NOW)] == ["Release 9.1.0: new hooks events"]
    many = {**feed, "data": [{"id": f"i{n}", "title": f"hook {n}", "link": "", "updated": "", "summary": ""} for n in range(30)]}
    assert len(match.candidates(inv(), [many], {"lead_keywords": ["hook"]}, {}, NOW)) == match.MAX_LEADS


def test_tier_1_evidence_alone_never_yields_a_diff():
    feed = result("rss", "rss_hidden.xml", "application/rss+xml", tier=1)
    good = match.candidates(inv(), [feed], KW, {}, NOW)[0]
    diff = "--- a/x\n+++ b/x\n"
    bad = {**good, "change": {"target": good["change"]["target"], "diff": diff, "diff_sha256": hashlib.sha256(diff.encode()).hexdigest()}}
    assert match.check(bad) == "tier-1 evidence alone cannot carry a diff"
    ok = {**bad, "evidence": [{**good["evidence"][0], "tier": 0}]}
    ok["id"] = match.make_id(ok["class"], ok["change"]["target"], ok["evidence"][0]["sha256"], ok["evidence"][0]["quote"])
    assert match.check({**ok, "class": "model_pin", "id": match.make_id("model_pin", ok["change"]["target"], ok["evidence"][0]["sha256"],
                                                                         ok["evidence"][0]["quote"])}) is None


# ------------------------------------------------------------------ shared rules
def test_ids_are_stable_across_rescans_and_change_with_the_evidence():
    a = match.candidates(inv(**{"settings.model": SETTINGS}), [DEPR()], {}, {}, NOW)
    b = match.candidates(inv(**{"settings.model": SETTINGS}), [DEPR()], {}, {}, "2026-10-22T00:00:00Z")
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert match.make_id("docs", "t", "0" * 64, "q") != match.make_id("docs", "t", "1" * 64, "q") != match.make_id("lead", "t", "0" * 64, "q")
    assert match.make_id("docs", "t", "0" * 64, "q") == "W-" + hashlib.sha256(("docs|t|" + "0" * 64 + "|q").encode()).hexdigest()[:8]


def test_candidates_are_ranked_model_pin_version_docs_lead():
    sm = result("sitemap", "sitemap.xml", "application/xml", url="https://example.test/sitemap.xml")
    prev = {"https://docs.example.test/en/hooks": "2026-10-01"}
    feed = result("rss", "rss_hidden.xml", "application/rss+xml", tier=1)
    npm = result("npm", "npm_tags.json", "application/json")
    got = match.candidates(inv("9.0.1", **{"settings.model": SETTINGS}), [feed, sm, npm, DEPR()],
                           {"docs": DOCS["docs"], "lead_keywords": KW["lead_keywords"]}, prev, NOW)
    assert [r["class"] for r in got] == ["model_pin", "version", "docs", "lead"]


@pytest.mark.parametrize("mutate,why", [
    (lambda r: r.update(extra=1), "shape"), (lambda r: r.update(elevated=True), "shape"), (lambda r: r.update(title="t" * 121), "title"),
    (lambda r: r.update(title="a\x1b[2J"), "title"), (lambda r: r["evidence"][0].update(quote="q" * 401), "evidence"),
    (lambda r: r["evidence"][0].update(sha256="xyz"), "evidence"), (lambda r: r["evidence"][0].update(tier=2), "evidence"),
    (lambda r: r.update(id="W-00000000"), "id"), (lambda r: r.update(verify="rm -rf /"), "verify"),
    (lambda r: r.update(verify='grep -n "x" a; rm -rf b'), "verify"), (lambda r: r.update(status="applied"), "dates"),
    (lambda r: r["change"].update(diff_sha256="0" * 64), "diff hash"), (lambda r: r.update(**{"class": "hook"}), "shape")])
def test_check_rejects_records_outside_the_shape(mutate, why):
    r = match.candidates(inv(**{"settings.model": SETTINGS}), [DEPR()], {}, {}, NOW)[0]
    mutate(r)
    assert match.check(r) == why
