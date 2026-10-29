"""Opus round 2: an import twin never outlives the own scan's diff (expired, auto-closed, forged), the replacement is reported and pinned."""

import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile.watch import cli, records, store, vet

from .test_compile_watch_cli import NEW, World
from .test_compile_watch_cli import w as world  # noqa: F401 - the fixture
from .test_watch_import import World as ImportWorld, make

FMT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def iw(tmp_path, monkeypatch):
    return ImportWorld(tmp_path, monkeypatch)


def stored(w):
    return {p.stem: json.loads(p.read_text()) for p in (w.wd / "proposals").glob("W-*.json")}


def model_pins(w):
    now = datetime.now(timezone.utc).strftime(FMT)
    return [(p, s) for p, s in store.statuses(w.wd, now) if p["class"] == "model_pin"]


def two_worlds(tmp_path, monkeypatch, capsys):
    (tmp_path / "acewatch").mkdir()
    sandbox = World(tmp_path / "acewatch", monkeypatch)
    sandbox.run(capsys, "scan")
    pin = next(r for r in stored(sandbox).values() if r["class"] == "model_pin")
    (tmp_path / "operator").mkdir()
    return pin, World(tmp_path / "operator", monkeypatch)


def plant(mine, rec, **over):
    """A diff-less imported-looking twin written straight into the operator's store (as an import long ago would have)."""
    twin = {**rec, "title": rec["title"] + " (imported)", "change": {**rec["change"], "diff": None, "diff_sha256": None}, **over}
    store.ensure(mine.wd)
    assert store.write_proposal(mine.wd, twin)
    return twin


def live_with_diff(mine):
    return [p for p, s in model_pins(mine) if s in ("open", "queued") and p["change"]["diff"]]


def test_an_expired_copy_is_rejected_as_dates(iw, capsys):
    gone = make(1)
    gone["created"], gone["expires"] = "2026-09-01T00:00:00Z", "2026-09-30T00:00:00Z"
    soon = make(2)
    now = datetime.now(timezone.utc)
    soon["created"], soon["expires"] = (now - timedelta(days=5)).strftime(FMT), (now + timedelta(days=5)).strftime(FMT)
    iw.put(gone)
    iw.put(soon)
    code, out, _ = iw.run(capsys)
    assert out[:2] == ["import: 1 imported, 0 duplicate, 1 rejected", f"rejected {gone['id']}: dates"] and iw.stored() == [soon["id"] + ".json"]


def test_an_expired_diff_less_twin_gives_way_to_the_own_scan(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin, created="2026-09-01T00:00:00Z", expires="2026-09-30T00:00:00Z")
    assert live_with_diff(mine) == []
    code, out, _ = mine.run(capsys, "scan", "--force")
    out = out.splitlines()
    assert out[-1].endswith("; diffed 1") and f"diffed {pin['id']}" in out
    [own] = live_with_diff(mine)
    assert own["id"] == pin["id"] and not own["title"].endswith(" (imported)") and records.check(own) is None


def test_an_auto_closed_diff_less_twin_is_replaced_and_reopened(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin)
    store.append_decision(mine.wd, pin["id"], "closed", "trigger vanished", "2026-10-28T00:00:00Z")
    assert [s for _, s in model_pins(mine)] == ["auto-closed"]
    code, out, _ = mine.run(capsys, "scan", "--force")
    assert f"diffed {pin['id']}" in out
    [own] = live_with_diff(mine)
    assert own["id"] == pin["id"] and own["change"]["diff"].count("\n") == 5


def test_a_forged_successor_under_the_own_id_hides_nothing_on_this_scan_or_the_next(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forged = {**pin, "title": pin["title"].replace(NEW, "claude-evil-9") + " (imported)", "change": {**pin["change"], "diff": None, "diff_sha256": None}}
    assert records.check(forged) is None and forged["id"] == pin["id"] and reconcile_key(forged) != reconcile_key(pin)
    store.ensure(mine.wd)
    store.write_proposal(mine.wd, forged)
    for _ in range(2):
        code, out, _ = mine.run(capsys, "scan", "--force")
        [own] = live_with_diff(mine)
        assert own["id"] == pin["id"] and NEW in own["title"] and "evil" not in own["title"] and len(model_pins(mine)) == 1


def reconcile_key(rec):
    from ace_playbook.compile.watch import reconcile
    return reconcile.key(rec)


def test_a_dismissed_twin_is_a_decision_and_is_not_replaced(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin)
    store.append_decision(mine.wd, pin["id"], "dismissed", "not trusted", "2026-10-28T00:00:00Z")
    code, out, _ = mine.run(capsys, "scan", "--force")
    assert [s for _, s in model_pins(mine)] == ["dismissed"] and not any(ln.startswith("diffed") for ln in out.splitlines())


def test_a_dry_run_scan_reports_the_replacement_and_changes_no_file(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    twin = plant(mine, pin)
    path = mine.wd / "proposals" / (pin["id"] + ".json")
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    code, out, _ = mine.run(capsys, "scan", "--force", "--dry-run")
    out = out.splitlines()
    assert f"diffed {pin['id']}" in out and out[-1].endswith("; diffed 1")
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime and json.loads(before)["title"] == twin["title"]


def test_the_scan_line_without_a_replacement_is_the_pinned_text(world, capsys):
    code, out, _ = world.run(capsys, "scan")
    assert out.splitlines()[-1] == "watch: fetched 5, unchanged 0, dropped 0; new 3, closed 0, promoted 0; open 2, leads 1"


def test_an_accept_pins_the_diff_hash_so_a_replaced_record_reads_stale(world, capsys):
    world.run(capsys, "scan")
    cfg = cfgmod.load()
    answers = iter(["1"] * 5)
    assert cli.queue(cfg, 5, lambda prompt: next(answers), lambda what: True) == 0
    notes = {d["id"]: d["note"] for d in store.read_decisions(world.wd) if d["decision"] == "accepted"}
    recs = stored(world)
    assert notes and all(n == "diff_sha256 " + (recs[i]["change"]["diff_sha256"] or "none") for i, n in notes.items())
    assert any(n != "diff_sha256 none" for n in notes.values()) and "diff_sha256 none" in notes.values()


# ------------------------------------------------------------------ vet.read branches
def test_a_file_swapped_between_the_lstat_and_the_open_is_refused_as_not_a_file(iw, capsys, monkeypatch, tmp_path):
    rec = make(1)
    iw.put(rec)
    spare = tmp_path / "spare"
    spare.write_text(json.dumps(make(2)))
    real = os.lstat
    swapped = []

    def lstat(name, *a, dir_fd=None, **k):
        out = real(name, *a, dir_fd=dir_fd, **k)
        if dir_fd is not None and not swapped:
            swapped.append(True)
            shutil.copyfile(spare, iw.src / "tmp.swap")
            os.replace(iw.src / "tmp.swap", iw.src / (rec["id"] + ".json"))                 # a new inode under the same name
        return out

    monkeypatch.setattr(vet.os, "lstat", lstat)
    code, out, _ = iw.run(capsys)
    assert out[:2] == ["import: 0 imported, 0 duplicate, 1 rejected", f"rejected {rec['id']}: not a file"] and iw.stored() == []

