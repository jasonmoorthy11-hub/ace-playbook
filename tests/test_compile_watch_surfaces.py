"""The watcher's fields in `status.json` and the `proposals` block of the digest (the status line and SwiftBar read the same fields)."""

import json
import stat

from ace_playbook.compile import digest, status
from ace_playbook.compile.watch import match, store

from .test_compile_digest import block
from .test_compile_status import NOW, build, w  # noqa: F401 - w is a fixture

KEYS = {"watch_open", "watch_leads", "watch_oldest", "watch_top", "watch_titles", "watch_disabled", "watch_last_scan"}


def put(w, n, cls, title, created="2026-10-18T00:00:00Z"):
    ev = {"url": "https://example.test/d", "sha256": f"{n:064x}", "quote": f"row {n}", "tier": 0 if cls != "lead" else 1}
    rec = {**match.record(cls, title, ev, ["k"], f"k{n}", None, "claude --version", created), "expires": "2026-12-01T00:00:00Z"}
    store.ensure(store.watch_dir(w.cfg))
    store.write_proposal(store.watch_dir(w.cfg), rec)
    return rec


def test_status_json_carries_the_watch_fields_and_zero_without_a_watch_dir(w):
    s = build(w)
    assert KEYS <= set(s) and (s["watch_open"], s["watch_leads"], s["watch_top"], s["watch_oldest"], s["watch_disabled"]) == (0, 0, None, None, False)
    assert not store.watch_dir(w.cfg).exists()                                          # building the status creates nothing
    status.write_json(w.cfg, s)
    assert KEYS <= set(json.loads(w.cfg.path("status.json").read_text())) and stat.S_IMODE(w.cfg.path("status.json").stat().st_mode) == 0o600


def test_status_json_counts_the_stored_proposals(w):
    put(w, 1, "docs", "docs one", "2026-10-17T00:00:00Z")
    put(w, 2, "model_pin", "pin two")
    put(w, 3, "lead", "lead three")
    s = build(w)
    assert (s["watch_open"], s["watch_leads"], s["watch_top"], s["watch_oldest"]) == (2, 1, "pin two", "2026-10-17T00:00:00Z")
    assert s["watch_titles"] == ["pin two", "docs one"]


def test_the_digest_proposals_block_follows_health_and_reads_none_when_zero(w):
    text = digest.render(build(w), None)
    assert block(text, "proposals") == ["none"]
    lines = text.splitlines()
    assert lines.index("proposals") == lines.index("health") + len(block(text, "health")) + 2


def test_the_digest_proposals_block_lines_and_the_kill_file(w):
    for n in range(5):
        put(w, n, "docs", f"title {n}", f"2026-10-1{n}T00:00:00Z")
    put(w, 9, "lead", "a lead")
    s = build(w, now=NOW)
    lines = block(digest.render(s, None), "proposals")
    assert lines[0] == f"open 5  leads 1  oldest {status.age('2026-10-10T00:00:00Z', NOW)}  last scan never"
    assert lines[1:] == ["- title 0", "- title 1", "- title 2"]
    (store.watch_dir(w.cfg) / "DISABLED").write_text("")
    assert block(digest.render(build(w, now=NOW), None), "proposals") == ["disabled"]


def test_an_older_status_json_without_the_fields_reads_as_none(w):
    s = {k: v for k, v in build(w).items() if k not in KEYS}
    assert block(digest.render(s, None), "proposals") == ["none"]
