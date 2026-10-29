"""`compile watch import <dir>`: a reviewed copy of acewatch's proposals moves into the operator's store, validated, deduped, capped."""

import hashlib
import json
import os
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
        conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "watch_dir": str(self.wd), "chain_key_path": None}))
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
    assert (code, err) == (0, "") and out == ["import: 1 imported, 0 duplicate, 0 rejected", "- title 1"]
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


def test_carry_is_small_and_stdlib_only():
    assert len(Path(carry.__file__).read_text().splitlines()) <= 120
    assert os.path.basename(carry.__file__) == "carry.py"
