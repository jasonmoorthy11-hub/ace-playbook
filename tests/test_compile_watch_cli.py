"""`compile watch scan|status|queue` end to end, in process through `__main__.main` with a tmp config and a fake network (no real fetch)."""

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook.compile import __main__ as cli_main
from ace_playbook.compile.watch import cli, inventory, match, netguard, scan, store

from .watch_support import INJECTION, Resp, fixture

ROOT = Path(__file__).resolve().parent.parent
OLD, NEW = "claude-old-sonnet-20250101", "claude-new-sonnet-20260601"
U = {"depr": "https://example.test/deprecations.md", "npm": "https://example.test/npm", "map": "https://example.test/sitemap.xml",
     "page": "https://docs.example.test/en/hooks.md", "feed": "https://example.test/feed.xml"}
SOURCES = [{"id": "depr", "url": U["depr"], "kind": "deprecations", "tier": 0}, {"id": "npm", "url": U["npm"], "kind": "npm", "tier": 0},
           {"id": "map", "url": U["map"], "kind": "sitemap", "tier": 0}, {"id": "page", "url": U["page"], "kind": "md", "tier": 0},
           {"id": "feed", "url": U["feed"], "kind": "rss", "tier": 1}]
MAP = {"targets": {"settings.model": {"path": "SETTINGS", "pattern": "model"}},
       "docs": {"https://docs.example.test/en/hooks": ["settings.model"]}, "lead_keywords": ["subagent"]}


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.root, self.wd = tmp_path, tmp_path / "watch"
        self.settings = tmp_path / "settings.json"
        self.settings.write_text(f'{{\n  "theme": "dark",\n  "model": "{OLD}"\n}}\n')
        (tmp_path / "sources.json").write_text(json.dumps(SOURCES))
        (tmp_path / "map.json").write_text(json.dumps({**MAP, "targets": {"settings.model": {"path": str(self.settings), "pattern": "model"}}}))
        self.conf = tmp_path / "config.json"
        self.conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "watch_dir": str(self.wd), "chain_key_path": None,
                                         "watch_sources": str(tmp_path / "sources.json"), "watch_inventory_map": str(tmp_path / "map.json")}))
        monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(self.conf))
        self.pages = {U["depr"]: (fixture("deprecations.md"), "text/markdown"), U["npm"]: (fixture("npm_tags.json"), "application/json"),
                      U["map"]: (fixture("sitemap.xml"), "application/xml"), U["page"]: (fixture("doc_page.md"), "text/markdown"),
                      U["feed"]: (fixture("rss_hidden.xml"), "application/rss+xml")}
        self.calls = []

        def urlopen(req, timeout=None):
            self.calls.append(req.full_url)
            body, ctype = self.pages[req.full_url]
            return Resp(body, ctype)

        monkeypatch.setattr(netguard, "open_url", urlopen)
        monkeypatch.setattr(inventory, "claude_version", lambda *a, **k: "9.0.1 (Claude Code)")

    def run(self, capsys, *argv):
        code = cli_main.main(["watch", *argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    def proposals(self):
        return store.statuses(self.wd, "2026-10-21T12:00:00Z")


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def test_a_first_scan_writes_the_expected_proposals_and_a_summary_line(w, capsys):
    code, out, err = w.run(capsys, "scan")
    assert code == 0 and err == ""
    lines = out.splitlines()
    assert sorted(ln.split(":")[0].split()[2] for ln in lines if ln.startswith("new ")) == ["lead", "model_pin", "version"]
    assert lines[-1] == "watch: fetched 5, unchanged 0, dropped 0; new 3, closed 0, promoted 0; open 2, leads 1"
    by = {p["class"]: p for p, _ in w.proposals()}
    assert by["model_pin"]["title"] == f"Replace {OLD} with {NEW} in settings.model" and by["model_pin"]["change"]["diff"].count("\n") == 5
    assert by["version"]["title"] == "Claude Code 9.1.0 available (installed 9.0.1)"
    assert by["lead"]["title"] == "A harmless post about subagents" and by["lead"]["evidence"][0]["tier"] == 1 and by["lead"]["change"]["diff"] is None
    assert not [p for p, _ in w.proposals() if p["class"] == "docs"]                       # the first sight of a page is a baseline


def test_everything_written_is_private_and_inside_the_watch_dir(w, capsys):
    before = {p for p in w.root.rglob("*")}
    w.run(capsys, "scan")
    made = {p for p in w.root.rglob("*")} - before
    assert made and all(w.wd == p or w.wd in p.parents for p in made)
    for p in made:
        assert stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600), p
    assert sorted(p.name for p in w.wd.iterdir()) == ["inventory.json", "proposals", "scan.lock", "seen.json"]
    seen = json.loads((w.wd / "seen.json").read_text())
    assert set(seen["fetched"]) == set(U.values()) and all(len(v["sha256"]) == 64 and v["fetched_at"] for v in seen["fetched"].values())
    inv = json.loads((w.wd / "inventory.json").read_text())
    assert "theme" not in json.dumps(inv) and inv["claude_version"] == "9.0.1 (Claude Code)"


def test_a_rescan_dedupes_and_respects_the_cadence_and_force_refetches(w, capsys):
    w.run(capsys, "scan")
    n = len(w.calls)
    code, out, _ = w.run(capsys, "scan")
    assert code == 0 and len(w.calls) == n and out.splitlines() == [
        "watch: fetched 0, unchanged 0, dropped 0; new 0, closed 0, promoted 0; open 2, leads 1"]
    code, out, _ = w.run(capsys, "scan", "--force")
    assert len(w.calls) == 2 * n and out.splitlines() == ["watch: fetched 0, unchanged 5, dropped 0; new 0, closed 0, promoted 0; open 2, leads 1"]
    assert len(list((w.wd / "proposals").glob("W-*.json"))) == 3 and not (w.wd / "decisions.jsonl").exists()


def test_a_vanished_trigger_closes_the_proposal_by_a_decision_row(w, capsys):
    w.run(capsys, "scan")
    w.settings.write_text('{\n  "model": "claude-other-1"\n}\n')
    code, out, _ = w.run(capsys, "scan", "--force")
    assert out.splitlines()[-1] == "watch: fetched 0, unchanged 5, dropped 0; new 0, closed 1, promoted 0; open 1, leads 1"
    rows = store.read_decisions(w.wd)
    assert [(r["decision"], r["note"]) for r in rows] == [("closed", "trigger vanished")]
    assert {p["class"]: s for p, s in w.proposals()}["model_pin"] == "auto-closed"
    pid = rows[0]["id"]
    assert json.loads((w.wd / "proposals" / f"{pid}.json").read_text())["status"] == "open"          # the file is immutable
    w.run(capsys, "scan", "--force")
    assert len(store.read_decisions(w.wd)) == 1                                                 # not closed twice


def test_docs_proposals_come_from_a_moved_lastmod_or_a_changed_page_hash(w, capsys):
    w.run(capsys, "scan")
    w.pages[U["map"]] = (fixture("sitemap.xml").replace(b"2026-10-20", b"2026-10-21"), "application/xml")
    w.pages[U["page"]] = (fixture("doc_page.md") + b"\nA new paragraph.\n", "text/markdown")
    code, out, _ = w.run(capsys, "scan", "--force")
    docs = [p for p, _ in w.proposals() if p["class"] == "docs"]
    assert len(docs) == 2 and out.splitlines()[-1].startswith("watch: fetched 2, unchanged 3, dropped 0; new 2, closed 0")
    assert sorted(d["evidence"][0]["quote"] for d in docs) == ["Hooks reference", "https://docs.example.test/en/hooks lastmod 2026-10-21"]
    assert all(d["change"]["diff"] is None and d["inventory_hits"] == ["settings.model"] for d in docs)
    w.run(capsys, "scan", "--force")                                                             # an event: it does not vanish on the next scan
    assert all(s == "open" for p, s in w.proposals() if p["class"] == "docs")


def test_a_dropped_source_is_reported_and_the_rest_still_runs(w, capsys):
    w.pages[U["npm"]] = (fixture("npm_tags.json"), "text/html")
    w.pages[U["page"]] = (b"x" * (1024 * 1024 + 1), "text/markdown")
    code, out, _ = w.run(capsys, "scan")
    assert code == 0 and "dropped npm: content-type text/html" in out and "dropped page: too large" in out
    assert out.splitlines()[-1] == "watch: fetched 3, unchanged 0, dropped 2; new 2, closed 0, promoted 0; open 1, leads 1"
    assert "version" not in {p["class"] for p, _ in w.proposals()}                      # the dropped npm source produced nothing


def test_hidden_text_never_reaches_anything_the_scan_wrote(w, capsys):
    w.run(capsys, "scan")
    text = "".join(p.read_text(errors="replace") for p in w.wd.rglob("*") if p.is_file())
    assert INJECTION not in text.lower() and "evil-model" not in text and text.count("harmless post about subagents") >= 1


def test_tier_filter_and_dry_run(w, capsys):
    code, out, _ = w.run(capsys, "scan", "--dry-run")
    assert code == 0 and out.startswith("new ") and "watch (dry run, nothing written): fetched 5" in out
    assert not w.wd.exists()
    code, out, _ = w.run(capsys, "scan", "--tier", "1", "--dry-run")
    assert "fetched 1," in out.splitlines()[-1] and w.calls[-1] == U["feed"] and not w.wd.exists()


def test_the_kill_file_stops_a_scan_before_anything_is_fetched_or_written(w, capsys):
    w.wd.mkdir()
    (w.wd / "DISABLED").write_text("")
    code, out, _ = w.run(capsys, "scan", "--force")
    assert (code, out, w.calls) == (0, "watch: disabled\n", []) and sorted(p.name for p in w.wd.iterdir()) == ["DISABLED"]
    assert w.run(capsys, "status")[1] == "proposals\ndisabled\n"


def test_missing_or_bad_config_is_one_line_and_exit_2(w, capsys, tmp_path):
    full = json.loads(w.conf.read_text())
    for key in ("watch_sources", "watch_inventory_map"):
        w.conf.write_text(json.dumps({k: v for k, v in full.items() if k != key}))
        code, out, err = w.run(capsys, "scan")
        assert (code, out) == (2, "") and err == f"error: {key} is not set or the file is missing (hooks/INSTALL.md, Watch)\n"
    w.conf.write_text(json.dumps({**full, "watch_inventory_map": str(tmp_path / "gone.json")}))
    assert w.run(capsys, "scan")[2].startswith("error: watch_inventory_map is not set or the file is missing")
    w.conf.write_text(json.dumps(full))
    (tmp_path / "sources.json").write_text("{nope")
    code, out, err = w.run(capsys, "scan")
    assert code == 2 and err == "error: watch_sources is not readable JSON\n" and not w.wd.exists()


def test_status_prints_the_digest_block(w, capsys):
    assert w.run(capsys, "status")[1] == "proposals\nnone\n"
    w.run(capsys, "scan")
    lines = w.run(capsys, "status")[1].splitlines()
    assert lines[0] == "proposals" and lines[1].startswith("open 2  leads 1  oldest ") and "last scan " in lines[1]
    assert lines[2:] == [f"- Replace {OLD} with {NEW} in settings.model", "- Claude Code 9.1.0 available (installed 9.0.1)"]


def test_status_as_a_real_subprocess_with_the_module_entry_point(tmp_path):
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "d"), "chain_key_path": None}))
    p = subprocess.run([sys.executable, "-m", "ace_playbook.compile", "--config", str(conf), "watch", "status"], capture_output=True, text=True,
                       cwd=ROOT, timeout=60)
    assert (p.returncode, p.stdout, p.stderr) == (0, "proposals\nnone\n", "") and not (tmp_path / "d").exists()


def test_importing_the_cli_entry_loads_no_network_module():
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ace_playbook.compile.__main__; "
            "print(','.join(m for m in ('urllib.request', 'subprocess', 'socket', 'ssl', 'http.client', 'xml.etree.ElementTree', "
            "'ace_playbook.compile.watch.fetch', 'ace_playbook.compile.watch.inventory', 'ace_playbook.compile.watch.scan') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


# ------------------------------------------------------------------ the cap
def cand(n, cls="model_pin"):
    ev = {"url": "https://example.test/d", "sha256": f"{n:064x}", "quote": f"row {n}", "tier": 0}
    return match.record(cls, f"title {n}", ev, ["k"], f"k{n}", None, "claude --version", "2026-10-21T12:00:00Z")


def test_at_most_five_open_and_the_rest_are_queued_then_promoted_in_ranking_order(tmp_path):
    wd = store.ensure(tmp_path / "w")
    now = "2026-10-21T12:00:00Z"
    cands = [cand(n) for n in range(7)] + [cand(20, "lead"), cand(21, "docs")]
    out = scan.reconcile(wd, cands, [], now, dry=False)
    got = {p["id"]: s for p, s in store.statuses(wd, now)}
    assert len(out["new"]) == 9 and sorted(got.values()).count("open") == 6 and sorted(got.values()).count("queued") == 3     # 5 + 1 lead open
    assert store.open_count(store.statuses(wd, now)) == 5
    first = next(p["id"] for p, s in store.statuses(wd, now) if p["class"] == "model_pin" and s == "open")
    store.append_decision(wd, first, "dismissed", "no", now)
    assert len(store.rebalance(wd, now)) == 1 and store.open_count(store.statuses(wd, now)) == 5
    again = scan.reconcile(wd, cands, [], now, dry=False)
    assert again["new"] == []                                                                  # nothing is written twice


# ------------------------------------------------------------------ the queue
class Script:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        assert self.answers, f"unexpected prompt {prompt!r}"
        return self.answers.pop(0)


def seeded(tmp_path, n=3):
    cfg = type("C", (), {"watch_dir": tmp_path / "w", "data_dir": tmp_path})()
    wd = store.ensure(cfg.watch_dir)
    for i in range(n):
        p = {**cand(i), "created": f"2026-10-1{i}T00:00:00Z", "expires": "2099-01-01T00:00:00Z", "title": f"title {i}\x1b[2J"}
        p["id"] = f"W-{i:08x}"
        store.write_proposal(wd, p)
    return cfg, wd


def run_queue(cfg, ask, tty=True, limit=50):
    return cli.run(cfg, argparse.Namespace(watch_cmd="queue", limit=limit), ask, lambda what: tty)


def test_the_queue_records_one_decision_per_key_and_a_dismissal_needs_a_note(tmp_path, capsys):
    cfg, wd = seeded(tmp_path)
    ask = Script("1", "2", "", "  ", "not relevant to me", "3")
    assert run_queue(cfg, ask) == 0
    rows = store.read_decisions(wd)
    assert [(r["id"], r["decision"], r["note"]) for r in rows[:3]] == [
        ("W-00000000", "accepted", "diff_sha256 none"), ("W-00000001", "dismissed", "not relevant to me"), ("W-00000002", "later", "")]
    assert ask.prompts.count("dismiss note (required): ") == 3 and "accepted 1, dismissed 1, later 1" in capsys.readouterr().out
    assert {p["id"]: s for p, s in store.statuses(wd, "2026-10-21T00:00:00Z")} == {
        "W-00000000": "accepted", "W-00000001": "dismissed", "W-00000002": "open"}


def test_the_queue_is_oldest_first_limit_q_and_escape_free(tmp_path, capsys):
    cfg, wd = seeded(tmp_path)
    assert run_queue(cfg, Script("3", "q"), limit=2) == 0
    out = capsys.readouterr().out
    assert out.index("W-00000000") < out.index("W-00000001") and "W-00000002" not in out and "\x1b" not in out
    assert [r["id"] for r in store.read_decisions(wd)] == ["W-00000000"]
    assert run_queue(cfg, Script(), limit=0) == 2


def test_the_queue_needs_a_terminal_and_stops_on_eof_and_after_three_bad_keys(tmp_path, capsys):
    cfg, wd = seeded(tmp_path)
    assert run_queue(cfg, Script(), tty=False) == 2 and store.read_decisions(wd) == []

    def eof(prompt):
        raise EOFError

    assert run_queue(cfg, eof) == 0 and store.read_decisions(wd) == []
    assert run_queue(cfg, Script("x", "y", "z", "q")) == 0 and store.read_decisions(wd) == []              # three misses skip the card
    capsys.readouterr()


def test_an_empty_queue_and_the_kill_file(tmp_path, capsys):
    cfg = type("C", (), {"watch_dir": tmp_path / "w", "data_dir": tmp_path})()
    assert run_queue(cfg, Script()) == 0 and capsys.readouterr().out == "queue empty\n"
    store.ensure(cfg.watch_dir)
    (cfg.watch_dir / "DISABLED").write_text("")
    assert run_queue(cfg, Script()) == 0 and capsys.readouterr().out == "watch: disabled\n"


def test_the_queue_through_main_refuses_without_a_terminal(w, capsys):
    code, out, err = w.run(capsys, "queue")
    assert code == 2 and "needs an interactive terminal" in err and os.path.exists(w.conf)


# ------------------------------------------------------------------ wave 4: tier on the card, a symlinked proposals dir, a future lastmod
def test_the_queue_card_prints_the_tier_number(tmp_path, capsys):
    cfg, wd = seeded(tmp_path, 1)
    run_queue(cfg, Script("q"))
    assert "evidence: tier 0  https://example.test/d" in capsys.readouterr().out


def test_a_scan_with_a_symlinked_proposals_dir_is_exit_2_with_one_line(w, capsys, tmp_path):
    w.wd.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (w.wd / "proposals").symlink_to(elsewhere)
    code, out, err = w.run(capsys, "scan")
    assert code == 2 and err.startswith("error: ") and len(err.strip().splitlines()) == 1 and str(tmp_path) not in err
    assert list(elsewhere.iterdir()) == [] and w.calls == []


def test_a_scan_with_a_symlinked_watch_dir_is_exit_2_with_one_line(w, capsys, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    w.wd.symlink_to(real)
    code, out, err = w.run(capsys, "scan")
    assert code == 2 and err.startswith("error: ") and len(err.strip().splitlines()) == 1 and str(tmp_path) not in err
    assert list(real.iterdir()) == [] and w.calls == []


def test_a_future_sitemap_lastmod_is_not_remembered(w, capsys):
    w.run(capsys, "scan")
    w.pages[U["map"]] = (fixture("sitemap.xml").replace(b"2026-10-20", b"9999-12-31"), "application/xml")
    w.run(capsys, "scan", "--force")
    seen = store.read_seen(w.wd)
    assert "9999-12-31" not in json.dumps(seen.get("lastmod", {}))
    w.pages[U["map"]] = (fixture("sitemap.xml").replace(b"2026-10-20", b"2026-10-21"), "application/xml")
    w.run(capsys, "scan", "--force")
    assert any(p["class"] == "docs" for p, _ in w.proposals())
