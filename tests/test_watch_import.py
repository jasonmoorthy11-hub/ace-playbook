"""`compile watch import <dir>`: a reviewed copy of acewatch's proposals moves into the operator's store, validated, deduped, capped."""

import hashlib
import json
import os
import signal
import time
import stat
from pathlib import Path

import pytest

from ace_playbook.compile import __main__ as cli_main
from ace_playbook.compile.watch import carry, reconcile, records, store

NOW = "2026-10-21T12:00:00Z"


def make(n, cls="docs", diff=None, tier=0, title=None):
    ev = {"url": "https://example.test/d", "sha256": f"{n:064x}", "quote": f"row {n}", "tier": tier}
    return records.record(cls, title or f"title {n}", ev, ["k"], f"k{n}", diff, "claude --version", NOW)


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.root, self.wd, self.src = tmp_path, tmp_path / "watch", tmp_path / "copy"
        self.src.mkdir()
        conf = tmp_path / "config.json"
        (tmp_path / "sources.json").write_text(json.dumps([{"id": "feed", "url": "https://example.test/feed.xml", "kind": "rss", "tier": 1}]))
        targets = {f"k{n}": {"path": str(tmp_path / "none"), "pattern": "x"} for n in range(1, 10)}
        (tmp_path / "map.json").write_text(json.dumps({"targets": targets, "docs": {"https://docs.example.test/en/hooks": ["k1"]}}))
        conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "watch_dir": str(self.wd), "chain_key_path": None,
                                    "watch_sources": str(tmp_path / "sources.json"), "watch_inventory_map": str(tmp_path / "map.json")}))
        monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(conf))

    def put(self, rec, name=None, text=None):
        path = self.src / ((name or rec["id"]) + ".json")
        path.write_text(text if text is not None else json.dumps(rec))
        return path

    def run(self, capsys, *argv, src=None):
        code = cli_main.main(["watch", "import", str(src or self.src), *argv])
        out = capsys.readouterr()
        return code, out.out.splitlines(), out.err

    def stored(self):
        return sorted(p.name for p in (self.wd / "proposals").glob("W-*.json")) if (self.wd / "proposals").is_dir() else []


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def test_a_valid_copy_imports_and_shows_in_watch_status(w, capsys):
    rec = make(1)
    w.put(rec)
    code, out, err = w.run(capsys)
    assert (code, err) == (0, "") and out == ["import: 1 imported, 0 duplicate, 0 rejected", "- title 1 (imported)"]
    path = w.wd / "proposals" / (rec["id"] + ".json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and json.loads(path.read_text())["status"] == "open"
    assert cli_main.main(["watch", "status"]) == 0
    assert "open 1" in capsys.readouterr().out
    assert (w.src / (rec["id"] + ".json")).exists()                                          # the copy is never touched


def test_status_is_forced_to_open(w, capsys):
    rec = {**make(1), "status": "queued"}
    w.put(rec)
    w.run(capsys)
    assert json.loads((w.wd / "proposals" / (rec["id"] + ".json")).read_text())["status"] == "open"


def test_a_duplicate_by_id_and_by_key_is_counted_not_written(w, capsys):
    a = make(1)
    w.put(a)
    assert w.run(capsys)[1][0] == "import: 1 imported, 0 duplicate, 0 rejected"
    assert w.run(capsys)[1][0] == "import: 0 imported, 1 duplicate, 0 rejected"             # same id
    keyed = make(3, cls="docs")
    keyed["evidence"][0]["sha256"] = a["evidence"][0]["sha256"]
    keyed["change"]["target"] = a["change"]["target"]
    keyed["id"] = records.make_id("docs", keyed["change"]["target"], keyed["evidence"][0]["sha256"], keyed["evidence"][0]["quote"])
    assert keyed["id"] != a["id"] and reconcile.key(keyed) == reconcile.key(a)
    w.put(keyed)
    code, out, _ = w.run(capsys)
    assert out[0] == "import: 0 imported, 2 duplicate, 0 rejected" and len(w.stored()) == 1


def reasons(out):
    return [ln for ln in out if ln.startswith("rejected ")]


def test_every_bad_file_is_rejected_with_a_reason_and_nothing_is_written(w, capsys, tmp_path):
    good = make(9)
    w.put(good)
    mismatch = make(1)
    w.put(mismatch, name=make(2)["id"])
    badclass = {**make(3), "class": "bogus"}
    w.put(badclass)
    tier1 = make(4, diff="--- a\n+++ b\n", tier=1)
    tier1["change"]["diff_sha256"] = hashlib.sha256(tier1["change"]["diff"].encode()).hexdigest()
    w.put(tier1)
    w.put(None, name=make(5)["id"], text="[1, 2]")
    w.put(None, name=make(6)["id"], text="not json {")
    big = make(7)
    w.put(big, text=json.dumps(big) + " " * 70000)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(make(8)))
    (w.src / (make(8)["id"] + ".json")).symlink_to(outside)
    w.put(None, name="W-notanid", text="{}")
    code, out, err = w.run(capsys)
    assert code == 0 and out[0] == "import: 1 imported, 0 duplicate, 8 rejected"
    got = {ln.split()[1].rstrip(":"): ln.split(": ", 1)[1] for ln in reasons(out)}
    assert got == {make(2)["id"]: "id", badclass["id"]: "shape", tier1["id"]: "tier-1 evidence alone cannot carry a diff",
                   make(5)["id"]: "shape", make(6)["id"]: "json", big["id"]: "size", make(8)["id"]: "symlink", "W-notanid": "name"}
    assert w.stored() == [good["id"] + ".json"]
    assert "row" not in "\n".join(out) and "{" not in "\n".join(out)                          # no file content is echoed


def test_a_symlinked_directory_is_refused(w, capsys, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(w.src)
    code, out, err = w.run(capsys, src=link)
    assert code == 2 and out == [] and "directory" in err and not w.wd.exists()


def test_a_missing_directory_is_refused(w, capsys, tmp_path):
    code, out, err = w.run(capsys, src=tmp_path / "nope")
    assert code == 2 and "directory" in err


def test_over_the_cap_the_rest_are_queued(w, capsys):
    recs = [make(n) for n in range(1, 8)]
    for r in recs:
        w.put(r)
    code, out, _ = w.run(capsys)
    assert out[0] == "import: 7 imported, 0 duplicate, 0 rejected"
    rows = store.statuses(w.wd, NOW)
    assert sorted(s for _, s in rows) == ["open"] * 5 + ["queued"] * 2


def test_dry_run_writes_nothing_and_prints_the_same(w, capsys):
    w.put(make(1))
    w.put(make(2))
    code, dry, _ = w.run(capsys, "--dry-run")
    assert code == 0 and dry[0] == "import: 2 imported, 0 duplicate, 0 rejected" and not w.wd.exists()
    code, real, _ = w.run(capsys)
    assert real == dry and len(w.stored()) == 2


def test_disabled_refuses(w, capsys):
    w.put(make(1))
    store.ensure(w.wd)
    (w.wd / store.KILL).write_text("")
    code, out, err = w.run(capsys)
    assert code == 2 and "disabled" in err and w.stored() == []


def test_an_unsafe_watch_dir_is_refused(w, capsys, tmp_path):
    w.put(make(1))
    real = tmp_path / "real"
    real.mkdir()
    w.wd.symlink_to(real)
    code, out, err = w.run(capsys)
    assert code == 2 and "symlink" in err and not list(real.iterdir())


def test_carry_is_small():
    assert len(Path(carry.__file__).read_text().splitlines()) <= 120
    assert os.path.basename(carry.__file__) == "carry.py"


def only(w, rec):
    return json.loads((w.wd / "proposals" / (rec["id"] + ".json")).read_text())


def test_a_diff_is_never_imported_the_record_is_stored_without_it_and_counted(w, capsys):
    diff = "--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-claude-old\n+evil\n"
    rec = make(1, cls="model_pin", diff=diff, title="Replace claude-old with claude-new in k1")
    w.put(rec)
    plain = make(2)
    w.put(plain)
    code, out, _ = w.run(capsys)
    assert code == 0 and out[0] == "import: 2 imported, 0 duplicate, 0 rejected; 1 imported without diff"
    got = only(w, rec)
    assert got["change"]["diff"] is None and got["change"]["diff_sha256"] is None and got["id"] == rec["id"]
    assert records.check(got) is None and got["title"] == "Replace claude-old with claude-new in k1 (imported)"
    assert records.title_model(got["title"]) == ("claude-old", "claude-new")


def test_the_title_of_an_imported_record_is_marked_in_status_and_the_mark_is_not_doubled(w, capsys):
    rec = make(1, title="Docs changed")
    w.put(rec)
    w.run(capsys)
    assert only(w, rec)["title"] == "Docs changed (imported)"
    cli_main.main(["watch", "status"])
    assert "- Docs changed (imported)" in capsys.readouterr().out
    marked = make(2, title="Docs again (imported)")
    w.put(marked)
    w.run(capsys)
    assert only(w, marked)["title"] == "Docs again (imported)"


def test_a_title_too_long_to_carry_the_mark_is_rejected(w, capsys):
    rec = make(1, title="t" * 115)
    w.put(rec)
    code, out, _ = w.run(capsys)
    assert out[:2] == ["import: 0 imported, 0 duplicate, 1 rejected", f"rejected {rec['id']}: title"] and w.stored() == []


def test_stamps_urls_and_targets_are_checked_against_the_operators_own_config(w, capsys):
    def bad(n, **changes):
        rec = make(n)
        for path, value in changes.items():
            node = rec
            *keys, last = path.split(".")
            for k in keys:
                node = node[int(k)] if k.isdigit() else node[k]
            node[int(last) if last.isdigit() else last] = value
        w.put(rec)
        return rec["id"]
    long_window = bad(1, expires="2027-12-31T00:00:00Z")
    odd_stamp = bad(2, created="2026-10-21 12:00:00")
    ftp = bad(3, **{"evidence.0.url": "ftp://example.test/x"})
    nohost = bad(4, **{"evidence.0.url": "https:///x"})
    notstr = bad(5, **{"evidence.0.url": 5})
    code, out, _ = w.run(capsys)
    got = {ln.split()[1].rstrip(":"): ln.split(": ", 1)[1] for ln in reasons(out)}
    assert got[long_window] == "dates" and got[odd_stamp] == "dates" and got[ftp] == "url" and got[nohost] == "url" and got[notstr] in ("url", "shape")
    assert w.stored() == []


def test_an_unknown_target_is_rejected_and_a_known_one_of_each_kind_is_taken(w, capsys):
    foreign = records.record("docs", "t", {"url": "https://example.test/d", "sha256": "1" * 64, "quote": "q", "tier": 0}, [], "k99", None,
                             "claude --version", NOW)
    w.put(foreign)
    ok = [records.record("docs", "page", {"url": "https://example.test/d", "sha256": "2" * 64, "quote": "q", "tier": 0}, [],
                         "https://docs.example.test/en/hooks", None, "claude --version", NOW),
          records.record("version", "Claude Code 9.1.0 available (installed 9.0.1)",
                         {"url": "https://example.test/n", "sha256": "3" * 64, "quote": "q", "tier": 0}, [], "claude_version", None,
                         "claude --version", NOW),
          records.record("lead", "A post", {"url": "https://example.test/p", "sha256": "4" * 64, "quote": "q", "tier": 1}, [], "feed", None,
                         "claude --version", NOW)]
    for rec in ok:
        w.put(rec)
    code, out, _ = w.run(capsys)
    assert out[:2] == ["import: 3 imported, 0 duplicate, 1 rejected", f"rejected {foreign['id']}: target"]


def test_import_needs_the_sources_file_and_the_inventory_map(w, capsys, monkeypatch, tmp_path):
    w.put(make(1))
    conf = tmp_path / "bare.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "watch_dir": str(w.wd), "chain_key_path": None}))
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(conf))
    code, out, err = w.run(capsys)
    assert code == 2 and "watch_sources" in err and not w.wd.exists()


def test_a_fifo_named_like_a_proposal_is_refused_not_waited_for_and_the_lock_is_released(w, capsys):
    name = make(1)["id"]
    os.mkfifo(w.src / (name + ".json"))
    w.put(make(2))

    def boom(*a):
        raise TimeoutError("the import blocked on a FIFO")

    signal.signal(signal.SIGALRM, boom)
    signal.alarm(10)
    began = time.monotonic()
    try:
        code, out, _ = w.run(capsys)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
    assert code == 0 and time.monotonic() - began < 5 and f"rejected {name}: not a file" in out
    assert out[0] == "import: 1 imported, 0 duplicate, 1 rejected" and fsio_free(w)


def fsio_free(w):
    from ace_playbook.compile.watch import fsio
    fd = fsio.lock(w.wd)
    if fd is not None:
        os.close(fd)
    return fd is not None


def test_a_device_or_a_directory_named_like_a_proposal_is_not_a_file(w, capsys):
    (w.src / (make(1)["id"] + ".json")).mkdir()
    code, out, _ = w.run(capsys)
    assert out[:2] == ["import: 0 imported, 0 duplicate, 1 rejected", f"rejected {make(1)['id']}: not a file"]


@pytest.mark.parametrize("field, value", [
    ("inventory_hits", "k1"), ("inventory_hits", [{"x": 1}, 5]), ("inventory_hits", ["k"] * 51), ("inventory_hits", ["x" * 201]),
    ("inventory_hits", ["bell\x07"]), ("inventory_hits", None)])
def test_a_record_the_stores_own_readers_would_skip_is_rejected_as_shape(w, capsys, field, value):
    rec = make(1)
    rec[field] = value
    w.put(rec)
    code, out, _ = w.run(capsys)
    assert out[:2] == ["import: 0 imported, 0 duplicate, 1 rejected", f"rejected {rec['id']}: shape"] and w.stored() == []


@pytest.mark.parametrize("tier", [True, False, 1.0])
def test_a_tier_that_is_a_bool_or_a_float_is_a_shape(w, capsys, tier):
    rec = make(1)
    rec["evidence"][0]["tier"] = tier
    w.put(rec)
    assert w.run(capsys)[1][:2] == ["import: 0 imported, 0 duplicate, 1 rejected", f"rejected {rec['id']}: shape"]


def test_everything_imported_is_read_back_by_the_stores_own_readers(w, capsys):
    for n in (1, 2, 3):
        w.put(make(n))
    assert w.run(capsys)[1][0].startswith("import: 3 imported")
    props, bad = store.scan_proposals(w.wd)
    assert len(props) == 3 and bad == 0


def test_a_created_stamp_in_the_future_is_a_dates_rejection_and_a_day_ahead_is_tolerated(w, capsys):
    from datetime import datetime, timedelta, timezone
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    far = make(1)
    far["created"], far["expires"] = "2099-01-01T00:00:00Z", "2099-01-30T00:00:00Z"
    soon = make(2)
    soon["created"] = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime(fmt)
    soon["expires"] = (datetime.now(timezone.utc) + timedelta(days=20)).strftime(fmt)
    later = make(3)
    later["created"] = (datetime.now(timezone.utc) + timedelta(days=3)).strftime(fmt)
    later["expires"] = (datetime.now(timezone.utc) + timedelta(days=20)).strftime(fmt)
    for r in (far, soon, later):
        w.put(r)
    code, out, _ = w.run(capsys)
    got = {ln.split()[1].rstrip(":"): ln.split(": ", 1)[1] for ln in reasons(out)}
    assert got == {far["id"]: "dates", later["id"]: "dates"} and w.stored() == [soon["id"] + ".json"]


def test_a_symlink_with_a_dotdot_behind_it_is_refused_and_a_symlinked_ancestor_is_not_a_reason_to_refuse(w, capsys, tmp_path):
    w.put(make(1))
    link = tmp_path / "lnk"
    link.symlink_to(w.src)
    (w.src / "sub").mkdir()
    for src in (str(link) + "/sub/..", str(link) + "/", str(link)):
        code, out, err = w.run(capsys, src=src)
        assert code == 2 and "directory" in err and not w.wd.exists(), src
    parent = tmp_path / "parentlink"
    parent.symlink_to(tmp_path)
    code, out, err = w.run(capsys, src=str(parent / "copy"))
    assert code == 0 and out[0].startswith("import: 1 imported")
