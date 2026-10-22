"""Opus wave-1 review fixes: parsing of the deprecations table, model-id and successor validity, XHTML feeds, inventory numbering, the fetch
edge cases, dedupe, auto-close and reopen, the scan lock and the private writes. No network."""

import argparse
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ace_playbook.compile.watch import cli, fsio, inventory, match, parsers, pin, reconcile, store
from ace_playbook.compile.watch.fetch import EXPECT, fetch

from .test_compile_watch_cli import OLD, U, World
from .watch_support import Opener, Resp, fixture

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-10-21T12:00:00Z"
HDR = "| Model | State | Deprecated | Retires | Replacement |\n|---|---|---|---|---|\n"


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def dep(md: str, url: str = "https://example.test/dep.md", sha: str = "a" * 64) -> dict:
    return {"id": "dep", "url": url, "kind": "deprecations", "tier": 0, "sha256": sha, "prev_sha256": None,
            "data": parsers.parse_deprecations_md(md), "quote": "", "heading": ""}


def inv_of(lines, key="t", path="~/f.json", **extra) -> dict:
    return {"claude_version": None, "targets": {key: {"files": {path: {"sha256": "b" * 64, "lines": lines, **extra}}}}}


def row(mid="claude-p-1", succ="claude-q-1", state="Deprecated", deprecated="2026-01-01", retires="2026-06-01") -> str:
    return HDR + f"| {mid} | {state} | {deprecated} | {retires} | {succ} |\n"


# ------------------------------------------------------------------ the table
def test_an_escaped_pipe_inside_a_cell_does_not_shift_the_columns():
    rows = parsers.parse_deprecations_md(HDR + "| claude-p-1 | Deprecated | Jan \\| 2026 | 2026-06-01 | claude-q-1 |\n")
    assert [(r["state"], r["deprecated"], r["retires"], r["successor"]) for r in rows] == [("Deprecated", "Jan | 2026", "2026-06-01", "claude-q-1")]
    recs = match.model_pin(inv_of([[1, "m claude-p-1"]]), [dep(HDR + "| claude-p-1 | Deprecated | Jan \\| 2026 | 2026-06-01 | claude-q-1 |\n")], NOW)
    assert [r["class"] for r in recs] == ["model_pin"] and "+m claude-q-1" in recs[0]["change"]["diff"]
    last = parsers.parse_deprecations_md("| Model | Notes |\n|---|---|\n| claude-p-1 | a \\| b |\n")
    assert last[0]["model_id"] == "claude-p-1"


@pytest.mark.parametrize("cell", ["default", "sonnet", "model", "opus-4", "N/A", "Claude Sonnet 4.5", "claude", "claude-", "Claude-x-1"])
def test_only_a_claude_dash_id_is_a_model_id(cell):
    assert parsers.parse_deprecations_md(row(cell)) == []
    inv = inv_of([[1, f'"model": "{cell}" # default sonnet opus-4']])
    assert match.model_pin(inv, [dep(row(cell))], NOW) == []


def test_a_model_cell_gives_a_row_per_id_and_not_the_punctuation_around_it():
    ids = [r["model_id"] for r in parsers.parse_deprecations_md(row("claude-a-1, claude-b-2 (alias claude-c.3.)"))]
    assert ids == ["claude-a-1", "claude-b-2", "claude-c.3"]


@pytest.mark.parametrize("succ", ["None.", "Discontinued", "2027-03-01", "claude-q-1.", "claude-", "claude--x", "Claude-Q-1", "claude-a or claude-b",
                                  "claude-q-1 (see below)", "default", "claude:latest", "claude-q-1;", "claude-q$(id)", "-", "N/A"])
def test_a_successor_that_is_not_a_model_id_makes_a_lead_with_no_diff(succ):
    recs = match.model_pin(inv_of([[1, '"model": "claude-p-1"']]), [dep(row(succ=succ))], NOW)
    assert [(r["class"], r["change"]["diff"]) for r in recs] == [("lead", None)], succ


@pytest.mark.parametrize("succ", ["claude-q-1", "claude-sonnet-5-5", "claude-q.2", "claude-3-7-sonnet-20250219", "claude-haiku-4-5-20251001"])
def test_a_successor_that_is_a_model_id_makes_a_diff(succ):
    recs = match.model_pin(inv_of([[1, '"model": "claude-p-1"']]), [dep(row(succ=succ))], NOW)
    assert [r["class"] for r in recs] == ["model_pin"] and f'+"model": "{succ}"' in recs[0]["change"]["diff"]


@pytest.mark.parametrize("line,hit", [("claude-p-1", True), ('"claude-p-1"', True), ("claude-p-1[1m]", True), ("claude-p-1 x", True),
                                      ("claude-p-1@2025", False), ("x@claude-p-1", False), ("claude-p-1.x", False), ("claude-p-1.5", False),
                                      ("claude-p-1_x", False), ("claude-p-1-x", False), ("xclaude-p-1", False), ("claude-p-1.", True),
                                      ("a.claude-p-1", False), ("a-claude-p-1", False)])
def test_the_boundary_around_a_model_id(line, hit):
    recs = match.model_pin(inv_of([[1, line]]), [dep(row())], NOW)
    assert bool(recs) is hit, line


@pytest.mark.parametrize("extra", [{"truncated": True}, {"too_large": True}])
def test_a_truncated_or_skipped_target_never_gets_a_diff(extra):
    recs = match.model_pin(inv_of([[1, '"model": "claude-p-1"']], **extra), [dep(row())], NOW)
    assert [(r["class"], r["change"]["diff"]) for r in recs] == [("lead", None)]
    other = {"claude_version": None, "targets": {"t": {"files": {"~/a": {"sha256": "1" * 64, "lines": [[1, "claude-p-1"]]},
                                                               "~/b": {"sha256": "2" * 64, "lines": [], **extra}}}}}
    assert [r["class"] for r in match.model_pin(other, [dep(row())], NOW)] == ["lead"]               # any file of the target counts


def test_a_line_with_a_replacement_character_gets_no_diff():
    assert [r["class"] for r in match.model_pin(inv_of([[1, "claude-p-1 caf�"]]), [dep(row())], NOW)] == ["lead"]


def test_change_target_and_titles_never_come_from_a_page(tmp_path):
    feed = {"id": "bsky-mirror", "url": "https://example.test/f", "kind": "rss", "tier": 1, "sha256": "c" * 64, "prev_sha256": None, "quote": "",
            "heading": "", "data": [{"id": "https://evil.test/guid?x=1&y=<script>", "title": "About hooks", "link": "https://example.test/p", "updated": "",
                                     "summary": ""}]}
    lead = match.leads([feed], {"lead_keywords": ["hooks"]}, NOW)[0]
    assert lead["change"]["target"] == "bsky-mirror" and "evil" not in json.dumps(lead["change"])
    sm = {"id": "sm", "url": "https://example.test/sitemap.xml", "kind": "sitemap", "tier": 0, "sha256": "d" * 64, "prev_sha256": None, "quote": "",
          "heading": "", "data": {"https://docs.example.test/en/hooks/#frag": "2026-10-25"}}
    mapping = {"docs": {"https://docs.example.test/en/hooks": ["k"]}}
    rec = match.docs({}, [sm], mapping, {"https://docs.example.test/en/hooks/#frag": "2026-10-01"}, NOW)[0]
    assert rec["title"] == "docs page changed: https://docs.example.test/en/hooks" and rec["change"]["target"] == "https://docs.example.test/en/hooks"
    assert rec["verify"] == "curl -sI https://docs.example.test/en/hooks | head -1"


# ------------------------------------------------------------------ XHTML and inline XML in feeds
def test_xhtml_titles_and_content_lose_their_hidden_subtrees():
    e = parsers.parse_atom(fixture("atom_xhtml.xml").decode())[0]
    assert e["title"] == "Real subagents title" and e["summary"] == "Visible words after"
    blob = json.dumps(e)
    for secret in ("HIDDEN", "ignore previous"):
        assert secret not in blob


def test_rss_inline_xml_loses_its_hidden_subtrees_and_escaped_html_still_does():
    e = parsers.parse_rss(fixture("rss_inline.xml").decode())[0]
    assert (e["title"], e["summary"]) == ("Notes on subagents", "Visible summary tail")
    assert "HIDDEN" not in json.dumps(parsers.parse_rss(fixture("rss_hidden.xml").decode()))


def test_hidden_feed_text_reaches_no_file_of_the_store(w, capsys):
    w.pages[U["feed"]] = (fixture("rss_inline.xml"), "application/rss+xml")
    w.run(capsys, "scan")
    text = "".join(p.read_text(errors="replace") for p in w.wd.rglob("*") if p.is_file())
    assert "Notes on subagents" in text and "HIDDEN" not in text and "ignore previous" not in text.lower()
    w.pages[U["feed"]] = (fixture("atom_xhtml.xml"), "application/atom+xml")
    assert fetch("https://example.test/a", expect=EXPECT["atom"], store={}, kind="atom", now=NOW,
                 opener=Opener({"https://example.test/a": Resp(fixture("atom_xhtml.xml"), "application/atom+xml")}))["body"].count("HIDDEN") == 0


# ------------------------------------------------------------------ inventory numbering
def test_line_numbers_agree_with_grep_even_with_form_feeds_line_separators_and_carriage_returns(tmp_path):
    f = tmp_path / "f.txt"
    f.write_bytes('a\x0cb\nx y\r\n"model": "claude-p-1"\r\nlast model\x85line\nmodel'.encode())          # no trailing newline
    snap = inventory.build({"targets": {"k": {"path": str(f), "pattern": "model"}}}, NOW, which=lambda n: None)
    lines = snap["targets"]["k"]["files"][str(f)]["lines"]
    assert lines == [[3, '"model": "claude-p-1"\r'], [4, "last model\x85line"], [5, "model"]]
    assert [i for i, ln in enumerate(f.read_bytes().split(b"\n"), 1) if b"model" in ln] == [3, 4, 5]
    recs = match.model_pin(snap, [dep(row())], NOW)
    assert recs and "@@ -3,1 +3,1 @@" in recs[0]["change"]["diff"] and '-"model": "claude-p-1"\r\n' in recs[0]["change"]["diff"]


def test_the_cap_marks_the_file_truncated(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("model claude-p-1\n" * (inventory.MAX_LINES + 1))
    entry = inventory.build({"targets": {"k": {"path": str(f), "pattern": "model"}}}, NOW, which=lambda n: None)["targets"]["k"]["files"][str(f)]
    assert entry["truncated"] is True and len(entry["lines"]) == inventory.MAX_LINES
    exact = tmp_path / "g.txt"
    exact.write_text("model\n" * inventory.MAX_LINES)
    assert "truncated" not in inventory.build({"targets": {"k": {"path": str(exact), "pattern": "model"}}}, NOW,
                                              which=lambda n: None)["targets"]["k"]["files"][str(exact)]
    snap = inventory.build({"targets": {"k": {"path": str(f), "pattern": "model"}}}, NOW, which=lambda n: None)
    assert [r["class"] for r in match.model_pin(snap, [dep(row())], NOW)] == ["lead"]


# ------------------------------------------------------------------ fetch edge cases
URL = "https://example.test/x"


def get(body, ctype="text/markdown", kind="deprecations", headers=None, store_=None):
    resp = Resp(body, ctype)
    resp.headers.update(headers or {})
    return fetch(URL, expect=EXPECT[kind], store={} if store_ is None else store_, kind=kind, now=NOW, opener=Opener({URL: resp}))


@pytest.mark.parametrize("enc", ["gzip", "br", "deflate", "GZIP", "gzip, br", "zstd"])
def test_a_compressed_body_is_dropped_with_the_reason_encoding(enc):
    assert get(b"# x", "text/markdown", "md", {"Content-Encoding": enc})["dropped"] == "encoding"
    assert "dropped" not in get(b"# x", "text/markdown", "md", {"Content-Encoding": "identity"})
    assert "dropped" not in get(b"# x", "text/markdown", "md", {"Content-Encoding": ""})


def test_a_304_without_a_stored_validator_is_etag_not_redirect():
    from .watch_support import not_modified
    assert fetch(URL, expect=EXPECT["md"], store={}, kind="md", now=NOW, opener=Opener({URL: not_modified(URL)}))["dropped"] == "etag"
    known = {"fetched": {URL: {"sha256": "a" * 64, "fetched_at": "t", "etag": '"v"'}}}
    assert fetch(URL, expect=EXPECT["md"], store=known, kind="md", now=NOW, opener=Opener({URL: not_modified(URL)})) is None


def test_a_deprecations_page_with_no_rows_is_a_failed_fetch_and_is_not_remembered():
    store_ = {}
    for body in (b"Nothing here\n", b"# Model deprecations\n\n| Feature | Notes |\n|---|---|\n| a | b |\n", b"Note: no <script> tag. " + b"x" * 10):
        assert get(body, store_=store_)["dropped"] == "parse"
    assert "fetched" not in store_ or URL not in store_["fetched"]
    assert "dropped" not in get(fixture("deprecations.md"), store_=store_)


def test_markdown_pages_do_not_lose_the_rest_to_a_prose_mention_of_a_raw_text_tag():
    body = b"Note: the console no longer loads a <script> tag for this page.\n\n" + fixture("deprecations.md")
    item = get(body)
    assert "dropped" not in item and {r["model_id"] for r in item["data"]} >= {"claude-old-sonnet-20250101", "claude-old-haiku-20240101"}
    md = get(b"# Title\n\nuse the <style> element\n\nmore text\n", kind="md")
    assert "more text" in md["body"]
    hidden = get(b"# Title\n\n<script>var x = 1;</script>\n<div hidden>SECRET</div>\n<!-- SECRET -->\nvisible\n", kind="md")
    assert "SECRET" not in hidden["body"] and "visible" in hidden["body"]


# ------------------------------------------------------------------ dedupe, auto-close, reopen
def wd_of(tmp_path):
    return store.ensure(tmp_path / "w")


def put_pin(wd, results, inv, now=NOW):
    cands = match.candidates(inv, results, {}, {}, now)
    out = reconcile.reconcile(wd, cands, results, now, False, inv)
    return cands, out


def state_of(wd, now):
    return {p["class"]: s for p, s in store.statuses(wd, now)}


PINNED = inv_of([[1, '"model": "claude-p-1"']])
UNPINNED = inv_of([[1, '"model": "claude-other-1"']])
LATER = "2026-10-22T12:00:00Z"


def test_a_model_proposal_closes_when_the_inventory_no_longer_pins_the_id(tmp_path):
    wd, res = wd_of(tmp_path), [dep(row())]
    put_pin(wd, res, PINNED)
    out = reconcile.reconcile(wd, match.candidates(UNPINNED, res, {}, {}, LATER), res, LATER, False, UNPINNED)
    assert len(out["closed"]) == 1 and state_of(wd, LATER) == {"model_pin": "auto-closed"}


def test_a_model_proposal_closes_when_the_page_says_the_model_is_not_retiring(tmp_path):
    wd = wd_of(tmp_path)
    put_pin(wd, [dep(row())], PINNED)
    active = [dep(row(state="Active", retires="Not sooner than June 1, 2027"))]
    out = reconcile.reconcile(wd, match.candidates(PINNED, active, {}, {}, LATER), active, LATER, False, PINNED)
    assert len(out["closed"]) == 1


@pytest.mark.parametrize("results", [[], [dep("| Model | State |\n|---|---|\n| claude-zzz-9 | Deprecated |\n")]])
def test_nothing_closes_when_the_page_has_no_row_for_the_id_or_was_not_read(tmp_path, results):
    wd = wd_of(tmp_path)
    put_pin(wd, [dep(row())], PINNED)
    out = reconcile.reconcile(wd, match.candidates(PINNED, results, {}, {}, LATER), results, LATER, False, PINNED)
    assert out["closed"] == [] and state_of(wd, LATER) == {"model_pin": "open"}


def test_an_empty_parse_never_closes_through_a_scan(w, capsys):
    w.run(capsys, "scan")
    w.pages[U["depr"]] = (b"Note: no <script> tag here, and no table at all.\n", "text/markdown")
    code, out, _ = w.run(capsys, "scan", "--force")
    assert "dropped depr: parse" in out and "closed 0" in out.splitlines()[-1]
    assert {p["class"]: s for p, s in w.proposals()}["model_pin"] == "open" and store.read_decisions(w.wd) == []


def test_a_script_mention_in_prose_does_not_close_through_a_scan(w, capsys):
    w.run(capsys, "scan")
    w.pages[U["depr"]] = (b"Note: the console no longer loads a <script> tag.\n\n" + fixture("deprecations.md"), "text/markdown")
    code, out, _ = w.run(capsys, "scan", "--force")
    assert "closed 0" in out.splitlines()[-1] and store.read_decisions(w.wd) == []


def test_a_version_proposal_closes_only_when_claude_reports_a_version_and_npm_is_no_longer_newer(tmp_path):
    npm = {"id": "npm", "url": "https://example.test/npm", "kind": "npm", "tier": 0, "sha256": "e" * 64, "prev_sha256": None,
           "data": {"latest": "9.1.0", "stable": "9.0.4"}, "quote": '{"latest":"9.1.0"}', "heading": ""}
    wd = wd_of(tmp_path)
    old, new = {"claude_version": "9.0.1", "targets": {}}, {"claude_version": "9.1.0", "targets": {}}
    put_pin(wd, [npm], old)
    unknown = {"claude_version": None, "targets": {}}
    assert reconcile.reconcile(wd, match.candidates(unknown, [npm], {}, {}, LATER), [npm], LATER, False, unknown)["closed"] == []
    assert reconcile.reconcile(wd, match.candidates(new, [npm], {}, {}, LATER), [npm], LATER, False, new)["closed"] != []


def test_an_expired_or_closed_trigger_that_is_still_true_is_reopened_with_a_new_expiry(tmp_path):
    wd, res = wd_of(tmp_path), [dep(row())]
    cands, _ = put_pin(wd, res, PINNED)
    pid = cands[0]["id"]
    late = "2026-12-15T00:00:00Z"                                                                  # past the 30 days
    assert state_of(wd, late) == {"model_pin": "expired"}
    out = reconcile.reconcile(wd, match.candidates(PINNED, res, {}, {}, late), res, late, False, PINNED)
    assert out["reopened"] == [pid] and out["new"] == [] and state_of(wd, late) == {"model_pin": "open"}
    renewed = [d for d in store.read_decisions(wd) if d["decision"] == "reopened"]
    assert renewed[0]["note"] == "2027-01-14T00:00:00Z" and state_of(wd, "2027-01-13T00:00:00Z") == {"model_pin": "open"}
    assert state_of(wd, "2027-01-15T00:00:00Z") == {"model_pin": "expired"}
    assert len(list((wd / "proposals").glob("W-*.json"))) == 1                                      # the file is untouched
    again = reconcile.reconcile(wd, match.candidates(PINNED, res, {}, {}, late), res, late, False, PINNED)
    assert again["reopened"] == []                                                                  # already open: nothing more


def test_a_closed_proposal_returns_when_its_trigger_returns(tmp_path):
    wd, res = wd_of(tmp_path), [dep(row())]
    put_pin(wd, res, PINNED)
    reconcile.reconcile(wd, match.candidates(UNPINNED, res, {}, {}, LATER), res, LATER, False, UNPINNED)
    assert state_of(wd, LATER) == {"model_pin": "auto-closed"}
    out = reconcile.reconcile(wd, match.candidates(PINNED, res, {}, {}, LATER), res, LATER, False, PINNED)
    assert len(out["reopened"]) == 1 and state_of(wd, LATER) == {"model_pin": "open"}


def test_a_dismissed_proposal_stays_dismissed_even_after_it_would_have_expired(tmp_path):
    wd, res = wd_of(tmp_path), [dep(row())]
    cands, _ = put_pin(wd, res, PINNED)
    store.append_decision(wd, cands[0]["id"], "dismissed", "no thanks", NOW)
    late = "2026-12-15T00:00:00Z"
    out = reconcile.reconcile(wd, match.candidates(PINNED, res, {}, {}, late), res, late, False, PINNED)
    assert out["reopened"] == [] and out["new"] == [] and state_of(wd, late) == {"model_pin": "dismissed"}
    changed = [dep(row(), sha="f" * 64)]                                                           # the page changed elsewhere, the row did not
    out = reconcile.reconcile(wd, match.candidates(PINNED, changed, {}, {}, late), changed, late, False, PINNED)
    assert out["new"] == [] and state_of(wd, late) == {"model_pin": "dismissed"}


def test_a_reopen_waits_for_a_free_slot(tmp_path):
    wd, res = wd_of(tmp_path), [dep(row())]
    put_pin(wd, res, PINNED)
    late = "2026-12-15T00:00:00Z"
    for n in range(store.CAP):
        ev = {"url": "https://example.test/d", "sha256": f"{n:064x}", "quote": f"row {n}", "tier": 0}
        rec = {**match.record("docs", f"t{n}", ev, ["k"], f"k{n}", None, "claude --version", late), "expires": "2027-12-01T00:00:00Z"}
        store.write_proposal(wd, rec)
    out = reconcile.reconcile(wd, match.candidates(PINNED, res, {}, {}, late), res, late, False, PINNED)
    assert out["reopened"] == [] and store.open_count(store.statuses(wd, late)) == store.CAP


def test_docs_proposals_key_on_the_evidence_hash(tmp_path):
    wd = wd_of(tmp_path)

    def docs_cand(sha, quote="Hooks reference"):
        ev = {"url": "https://docs.example.test/en/hooks.md", "sha256": sha, "quote": quote, "tier": 0}
        return match.record("docs", "docs page changed: https://docs.example.test/en/hooks", ev, ["k"], "https://docs.example.test/en/hooks",
                            None, "curl -sI https://docs.example.test/en/hooks | head -1", NOW)

    first = reconcile.reconcile(wd, [docs_cand("1" * 64)], [], NOW, False)
    store.append_decision(wd, first["new"][0]["id"], "dismissed", "seen", NOW)
    second = reconcile.reconcile(wd, [docs_cand("2" * 64)], [], NOW, False)                         # same heading, new hash: a new change
    assert len(second["new"]) == 1 and second["new"][0]["id"] != first["new"][0]["id"]
    third = reconcile.reconcile(wd, [docs_cand("2" * 64), docs_cand("1" * 64)], [], NOW, False)     # both hashes are known now
    assert third["new"] == []


def test_the_docs_scan_flow_proposes_again_after_a_dismissal(w, capsys):
    w.run(capsys, "scan")
    w.pages[U["page"]] = (b"# Hooks reference\n\nv2 change one\n", "text/markdown")
    w.run(capsys, "scan", "--force")
    first = [p for p, _ in w.proposals() if p["class"] == "docs"][0]
    store.append_decision(w.wd, first["id"], "dismissed", "seen it", NOW)
    w.pages[U["page"]] = (b"# Hooks reference\n\nv3 a NEW breaking change\n", "text/markdown")
    _, out, _ = w.run(capsys, "scan", "--force")
    assert "new 1" in out and len([p for p, _ in w.proposals() if p["class"] == "docs"]) == 2


# ------------------------------------------------------------------ the scan lock and the private writes
def test_two_concurrent_scans_one_runs_and_one_says_scan_running(tmp_path):
    (tmp_path / "pin.json").write_text(f'{{"model": "{OLD}"}}\n')
    (tmp_path / "sources.json").write_text(json.dumps([{"id": "depr", "url": U["depr"], "kind": "deprecations", "tier": 0}]))
    (tmp_path / "map.json").write_text(json.dumps({"targets": {"k": {"path": str(tmp_path / "pin.json"), "pattern": "model"}}}))
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "watch_dir": str(tmp_path / "watch"), "chain_key_path": None,
                                "watch_sources": str(tmp_path / "sources.json"), "watch_inventory_map": str(tmp_path / "map.json")}))
    code = textwrap.dedent(f"""
        import sys, time
        sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'tests')!r}]
        from watch_support import Resp, fixture
        from ace_playbook.compile.watch import inventory, netguard
        netguard.RESOLVE = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
        inventory.claude_version = lambda *a, **k: None
        def slow(req, timeout=None):
            time.sleep(1.5)
            return Resp(fixture("deprecations.md"), "text/markdown")
        netguard.open_url = slow
        from ace_playbook.compile import __main__ as m
        raise SystemExit(m.main(["--config", {str(conf)!r}, "watch", "scan", "--force"]))
    """)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    first = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    for _ in range(100):                                                                           # wait until the first holds the lock
        if (tmp_path / "watch" / "scan.lock").exists():
            break
        subprocess.run(["sleep", "0.05"])
    second = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    out1, err1 = first.communicate(timeout=60)
    assert (second.returncode, second.stdout.strip(), second.stderr) == (0, "watch: scan running", "")
    assert first.returncode == 0 and "new 1" in out1.splitlines()[-1] and err1 == ""
    assert len(list((tmp_path / "watch" / "proposals").glob("W-*.json"))) == 1


def test_a_scan_lock_is_released_when_the_scan_ends(w, capsys):
    w.run(capsys, "scan")
    fd = fsio.lock(w.wd)
    assert fd is not None
    os.close(fd)
    assert "scan running" not in w.run(capsys, "scan", "--force")[1]


def test_a_second_lock_holder_gets_none_and_a_blocking_one_waits(tmp_path):
    first = fsio.lock(tmp_path)
    assert first is not None and fsio.lock(tmp_path) is None
    os.close(first)
    again = fsio.lock(tmp_path, blocking=True)
    assert again is not None
    os.close(again)


def test_private_writes_use_unique_exclusive_temp_names_and_clean_up(tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: seen.append(Path(a).name) or real(a, b))
    for _ in range(3):
        fsio.write_private(tmp_path / "x.json", "{}")
    assert len(set(seen)) == 3 and all(n.startswith(".x.json.") and n.endswith(".tmp") and str(os.getpid()) in n for n in seen)
    assert oct((tmp_path / "x.json").stat().st_mode & 0o777) == "0o600" and sorted(p.name for p in tmp_path.iterdir()) == ["x.json"]
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        fsio.write_private(tmp_path / "y.json", "{}")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.json"]
    (tmp_path / "link").symlink_to(tmp_path / "x.json")
    monkeypatch.setattr(os, "replace", real)
    fsio.write_private(tmp_path / "link", "{}")                                                    # replaces the link, never writes through it
    assert not (tmp_path / "link").is_symlink() and (tmp_path / "x.json").read_text() == "{}"


def test_clean_drops_every_invisible_character_and_the_queue_card_cuts_diff_lines_at_300(tmp_path, capsys):
    assert store.clean("aㅤb⠀c\U000e0041d‎e‪f️g h") == "abcdefgh"
    wd = store.ensure(tmp_path / "w")
    diff = "--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-" + "x" * 350 + "\n+" + "y" * 250 + "\n"
    ev = {"url": "https://example.test/d", "sha256": "1" * 64, "quote": "q", "tier": 0}
    rec = match.record("model_pin", "t", ev, ["k"], "k", diff, 'grep -n "claude-p-1" ~/f', NOW)
    store.write_proposal(wd, {**rec, "expires": "2099-01-01T00:00:00Z"})
    cfg = type("C", (), {"watch_dir": wd, "data_dir": tmp_path})()
    answers = iter(["q"])
    cli.run(cfg, argparse.Namespace(watch_cmd="queue", limit=5), lambda p: next(answers), lambda what: True)
    out = capsys.readouterr().out
    assert "x" * 299 in out and "x" * 301 not in out and "y" * 250 in out
